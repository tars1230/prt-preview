# -*- coding: utf-8 -*-
"""semantic_group.py —— 语义分组器（替代/增强 group_step.py）。

与「沿最长轴切空间」不同，本脚本先看懂这是什么，再按零件语义角色建树：

  1. 产品类型推断（尺寸指纹 + 结构证据） → 例如「立式双门柜体」
  2. 几何指纹聚类（bbox 三边 + 体积） → 完全相同的零件聚成一组
  3. 组级角色分类（可解释规则 + 置信度） → 外壳 / 门板 / 层板 / 框架 / 标准件 / 内部件
  4. 外壳(EXTERIOR) / 内部(INTERIOR) 二分，写回 XCAF 装配树
  5. 输出 semantic_map.json，可人工改分组后 --map 回灌（借鉴 SegCAD 的外部标签文件）

用法:
  python semantic_group.py <in.step> <out.step> [--dry] [--map override.json]

--dry  只分类出报告 + 写 semantic_map.json，不生成 STEP（秒级，用于调规则）
"""
import sys, os, json, time, math
from collections import Counter, defaultdict

from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name, TDataStd_NamedData
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorType
from OCP.collections import Sequence_TDF_Label
from OCP.TDF import TDF_Label
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopoDS import TopoDS_Compound
from OCP.BRep import BRep_Builder
from OCP.TopLoc import TopLoc_Location
from OCP.gp import gp_Trsf
from OCP.STEPControl import STEPControl_StepModelType
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB


# 角色定义：key -> (中文名, 属于外壳还是内部, 排序权重)
ROLES = {
    "SHELL":     ("主体外壳",  "A", 10),
    "DOOR":      ("门板组件",  "A", 20),
    "PANEL":     ("侧/顶/底板", "A", 30),
    "FRAME":     ("框架立柱",  "A", 40),
    "SHELF":     ("层板托盘",  "B", 50),
    "REPEAT":    ("同型组件",  "B", 60),
    "HARDWARE":  ("标准件五金", "B", 70),
    "INTERNAL":  ("其它内部件", "B", 80),
    "UNKNOWN":   ("未分类",    "C", 90),
}

# 非柜类模型换中性角色名：不能把「门板 / 层板」硬套到轴、法兰、支架上
GENERIC_LABELS = {
    "SHELL": "主体大件", "DOOR": "竖直大板", "PANEL": "大面板",
    "FRAME": "细长杆件", "SHELF": "水平板件", "REPEAT": "同型组件",
    "HARDWARE": "小型重复件", "INTERNAL": "其它零件", "UNKNOWN": "未分类",
}


def es(s):
    """中文安全的 ExtendedString（OCP 需显式声明 multi-byte UTF-8）"""
    try:
        return TCollection_ExtendedString(s, True)
    except Exception:
        return TCollection_ExtendedString(s)


def bbox(shape):
    b = Bnd_Box()
    BRepBndLib.Add_s(shape, b, True)
    return [b.GetXMin(), b.GetYMin(), b.GetZMin(),
            b.GetXMax(), b.GetYMax(), b.GetZMax()]


def _free_shapes(st):
    """返回 STEP 中全部自由形状，不能只取 labels.Value(1)。

    很多 STEP 是单一 product，但板件、钣金展开件、外部参照或多产品导出
    可能有多个 free shape。只读第一个会悄悄漏件，最终树看起来像「分组成功」
    但数量不完整。
    """
    labels = Sequence_TDF_Label()
    st.GetFreeShapes(labels)
    return [st.GetShape_s(labels.Value(i))
            for i in range(1, labels.Size() + 1)
            if not st.GetShape_s(labels.Value(i)).IsNull()]


def color_of(ct, shape):
    col = Quantity_Color()
    for typ in (XCAFDoc_ColorType.XCAFDoc_ColorSurf,
                XCAFDoc_ColorType.XCAFDoc_ColorGen):
        if ct.GetColor(shape, typ, col):
            try:
                r, g, b = col.Values(Quantity_TOC_RGB)
            except Exception:
                r, g, b = col.Red(), col.Green(), col.Blue()
            return [round(r, 3), round(g, 3), round(b, 3)]
    return None


def extract(src):
    """读 STEP，逐 solid 提特征。返回 (solids, records, st_src, ct_src, doc)"""
    t0 = time.time()
    doc = TDocStd_Document(es("src"))
    rdr = STEPCAFControl_Reader()
    rdr.SetNameMode(True)
    rdr.SetColorMode(True)
    rdr.ReadFile(src)
    rdr.Transfer(doc)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    ct = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    roots = _free_shapes(st)
    if not roots:
        raise ValueError("STEP 中没有可读取的自由形状（文件损坏或格式不符）")
    print(f"[read] {time.time()-t0:.1f}s free_shapes={len(roots)}", flush=True)

    # 不能只读取第一个 free shape：多产品 STEP、钣金展开件和外部参照
    # 经常把多个独立产品放在同一个文件中。
    solids = []
    for root in roots:
        ex = TopExp_Explorer(root, TopAbs_ShapeEnum.TopAbs_SOLID)
        while ex.More():
            solids.append(ex.Current())
            ex.Next()

    # 纯壳体/面模型没有 SOLID，但仍然是有效的 CAD 几何。先收集 SHELL，
    # 让后续走低置信度的几何分组，而不是误报「0 个实体」。
    representation = "SOLID"
    if not solids:
        representation = "SHELL"
        for root in roots:
            ex = TopExp_Explorer(root, TopAbs_ShapeEnum.TopAbs_SHELL)
            while ex.More():
                solids.append(ex.Current())
                ex.Next()
    # 某些曲面导出只有 FACE，没有 SHELL；仍保留为可查看对象。
    if not solids:
        representation = "FACE"
        for root in roots:
            ex = TopExp_Explorer(root, TopAbs_ShapeEnum.TopAbs_FACE)
            while ex.More():
                solids.append(ex.Current())
                ex.Next()

    recs = []
    vp, sp = GProp_GProps(), GProp_GProps()
    for i, s in enumerate(solids):
        bb = bbox(s)
        dim = [bb[3]-bb[0], bb[4]-bb[1], bb[5]-bb[2]]
        try:
            BRepGProp.VolumeProperties_s(s, vp); vol = vp.Mass()
        except Exception:
            vol = 0.0
        try:
            BRepGProp.SurfaceProperties_s(s, sp); area = sp.Mass()
        except Exception:
            area = 0.0
        col = color_of(ct, s)
        if col is None:
            fex = TopExp_Explorer(s, TopAbs_ShapeEnum.TopAbs_FACE)
            nf = 0
            while fex.More() and nf < 40 and col is None:
                col = color_of(ct, fex.Current()); fex.Next(); nf += 1
        dsort = sorted(dim)
        # ⚠️ 禁止 round(v, 2) 这类「绝对精度」：STEP 数值可能是米(0.09)/英寸，
        # 两位小数会把小尺寸抹成 0，导致指纹坍缩与除零。一律保留原始 float，
        # 精度控制交给下游按尺度自适应量化。thin 是无量纲比值，可安全取整。
        recs.append({
            "i": i, "bb": [float(v) for v in bb],
            "dim": [float(v) for v in dim],
            "ctr": [float((bb[k]+bb[k+3])/2) for k in range(3)],
            "vol": float(vol), "area": float(area),
            "thin": round(dsort[0]/dsort[2], 6) if dsort[2] > 0 else 0,
            "color": col,
        })
    # 无实体体积的壳体/面模型，用面积与包围盒体积构造一个仅用于排序和
    # 相对阈值的 proxy mass；真实 Volume 仍保留为 0，避免伪造测量数据。
    if recs:
        model_s = max((max(r["dim"]) for r in recs), default=1.0) or 1.0
        for r in recs:
            box_vol = max(math.prod(max(v, 0.0) for v in r["dim"]), 1e-30)
            if r["vol"] > 1e-30:
                r["mass"] = r["vol"]
            else:
                r["mass"] = max(r["area"] * max(model_s * 1e-3, min(r["dim"]) * 0.25),
                                 box_vol * 1e-3, 1e-30)
            # 仅按 bbox 会把「同外形但有孔/空腔」的零件错误合并。
            # 这两个粗特征只用于指纹分组，不作为测量结果对外展示。
            r["fill"] = min(max(r["vol"] / box_vol, 0.0), 1.0)
            box_area = max(2.0 * (r["dim"][0] * r["dim"][1]
                                  + r["dim"][0] * r["dim"][2]
                                  + r["dim"][1] * r["dim"][2]), 1e-30)
            r["area_density"] = min(max(r["area"] / box_area, 0.0), 20.0)
            r["representation"] = representation
    print(f"[features] {len(solids)} {representation.lower()}s, {time.time()-t0:.1f}s", flush=True)
    return solids, recs, st, ct, doc


def estimate_vertical_axis(recs, GD):
    """估计模型的竖直轴，避免把任意朝向的产品都当成 Z-up。

    STEP 不保证导出坐标系的 Z 轴就是产品竖直方向。对 X/Y/Z 三个候选轴，
    统计「水平大薄板」和「竖直大薄板」证据；只有证据明显时才旋转语义判断，
    对近似立方体/证据不足的零件保持传统 Z 轴，避免凭空改变结果。
    """
    if not recs or len(GD) != 3:
        return 2
    scores = []
    S = max(GD) or 1.0
    for a in range(3):
        h = [k for k in range(3) if k != a]
        vspan = GD[a]
        h0, h1 = GD[h[0]], GD[h[1]]
        score = 3.0 if vspan > 1.25 * max(h0, h1) else 0.0
        shelves = 0
        panels = 0
        for r in recs:
            d = r["dim"]
            if (d[a] < 0.08 * S and d[h[0]] > 0.25 * h0
                    and d[h[1]] > 0.25 * h1):
                shelves += 1
            thin_h = min(d[h[0]], d[h[1]])
            if (thin_h < 0.04 * S and d[a] > 0.6 * max(vspan, 1e-30)
                    and max(d[h[0]], d[h[1]]) > 0.15 * max(h0, h1)):
                panels += 1
        score += shelves * 2.0 + panels
        scores.append((score, a, shelves, panels))
    scores.sort(reverse=True)
    best = scores[0]
    # 证据不足或并列时维持历史 Z-up 行为。
    if best[0] < 3.0 or (len(scores) > 1 and best[0] - scores[1][0] < 1.0):
        return 2
    return best[1]


def infer_product(recs):
    """阶段一：这是什么。所有判据都是**相对比例**，不含绝对毫米数，
    这样 1950mm 的方舱和 195mm 的小设备会得到同一结论。
    返回 (类型名, 包络尺寸, 证据列表)
    """
    if not recs:
        return "空模型", [1.0, 1.0, 1.0], ["没有可分组的实体"]
    g0 = [min(r["bb"][k] for r in recs) for k in range(3)]
    g1 = [max(r["bb"][k+3] for r in recs) for k in range(3)]
    GD = [g1[k]-g0[k] for k in range(3)]
    S = max(GD)
    if S <= 0 or not all(math.isfinite(v) for v in GD):
        return "退化模型", GD, ["包围盒无效（零尺寸或非有限值）"]

    vertical_axis = estimate_vertical_axis(recs, GD)
    for r in recs:
        r["vertical_axis"] = vertical_axis
    haxes = [k for k in range(3) if k != vertical_axis]
    W, D, H = GD[haxes[0]], GD[haxes[1]], GD[vertical_axis]
    span = max(W, D) or S
    ev = [f"竖直轴: {'XYZ'[vertical_axis]}"]
    horiz = [r for r in recs
             if r["dim"][vertical_axis] < 0.08 * S
             and r["dim"][haxes[0]] > 0.25 * W
             and r["dim"][haxes[1]] > 0.25 * D]
    pairs = sum(1 for g in fingerprint_groups(recs, S).values()
                if len(g) == 2 and max(g[0]["dim"]) > 0.5 * S)
    tall = H > 1.4 * max(W, D)
    flat = min(GD) < 0.15 * S
    ev.insert(0, f"整体尺寸 {W:.4g}×{D:.4g}×{H:.4g} 模型单位（高/宽比 {H/span:.2f}）")
    ev.append(f"水平大薄板 {len(horiz)} 件（多层内部层架特征）")
    ev.append(f"成对镜像大件 {pairs} 组（双门/双壳特征）")
    if tall and len(horiz) >= 6:
        return ("立式双门柜体 / 方舱" if pairs >= 2 else "立式多层柜体"), GD, ev
    if len(horiz) >= 6:
        return "多层箱体 / 机架", GD, ev
    if flat:
        return "板类 / 薄壁结构", GD, ev
    if W > 1.5 * H:
        return "卧式箱体 / 底座", GD, ev
    return "通用装配体", GD, ev


def fingerprint_groups(recs, S=None):
    """几何指纹：bbox 三边按**尺度自适应量化**，体积仅作 ±25% 容差二级拆分。

    q = S/2000 让量化精度恒为整体尺度的 1/2000：mm 级文件约 1mm（与旧版
    round(v) 行为一致），米级文件约 0.05mm 等效，不会把 0.09/0.03 全抹成 0。
    体积容差也相对化（旧版 +100 是绝对量，米级单位下等于把所有件并成一组）。
    """
    if S is None:
        S = max((max(r["dim"]) for r in recs), default=0.0) or 1.0
    q = max(S / 2000.0, 1e-12)
    tiny = max((S ** 3) * 1e-12, 1e-30)
    by_dim = defaultdict(list)
    for r in recs:
        dims = tuple(round(v / q) for v in sorted(r["dim"]))
        # 2% 的填充率、0.2 的表面积密度量化能区分同 bbox 的实心件/开孔件，
        # 同时对 STEP 数值噪声保持容忍。没有这些字段时兼容旧 map/测试数据。
        fill_bin = round(r.get("fill", 0.0) / 0.02)
        area_bin = round(r.get("area_density", 0.0) / 0.2)
        by_dim[dims + (fill_bin, area_bin)].append(r)
    grp = {}
    for k, g in by_dim.items():
        g = sorted(g, key=lambda x: x["vol"])
        cur, base, sub = [], None, 0
        for r in g:
            if base is None or r["vol"] <= base * 1.25 + tiny:
                if base is None:
                    base = r["vol"]
                cur.append(r)
            else:
                grp[f"{k[0]}_{k[1]}_{k[2]}_{k[3]}_{k[4]}|{sub}"] = cur
                sub += 1
                cur, base = [r], r["vol"]
        grp[f"{k[0]}_{k[1]}_{k[2]}_{k[3]}_{k[4]}|{sub}"] = cur
    return grp


def classify(recs, GD, override=None, ptype=""):
    """阶段二/三：组级角色分类 + 外壳/内部二分。

    全部阈值相对化（S = 最长边, env_vol = 包络体积），与文件大小/单位无关。
    ptype 决定用柜类语义名还是中性角色名。
    """
    tot = sum(r.get("mass", r["vol"]) for r in recs) or 1.0
    S = max(GD) or 1.0
    env_vol = max(GD[0] * GD[1] * GD[2], S ** 3 * 1e-12, 1e-30)
    vol_small = 5e-5 * env_vol          # 原绝对阈值 100000mm³ 的相对等价
    vertical_axis = recs[0].get("vertical_axis", 2) if recs else 2
    haxes = [k for k in range(3) if k != vertical_axis]
    GDv = [GD[haxes[0]], GD[haxes[1]], GD[vertical_axis]]
    is_cabinet = ("柜" in (ptype or "")) or ("方舱" in (ptype or ""))
    labels = ({k: v[0] for k, v in ROLES.items()}
              if is_cabinet else GENERIC_LABELS)
    groups = fingerprint_groups(recs, S)
    # 组按体积降序 → 大件优先判定
    ordered = sorted(groups.items(), key=lambda kv: -max(x["vol"] for x in kv[1]))

    for key, g in ordered:
        rep = max(g, key=lambda x: x["vol"])
        n = len(g)
        raw_dim = rep["dim"]
        # 把估计的竖直轴映射到局部 [宽,深,高]，后续规则不再硬编码 Z-up。
        dim = [raw_dim[haxes[0]], raw_dim[haxes[1]], raw_dim[vertical_axis]]
        dsort = sorted(dim)
        t_ax = min(range(3), key=lambda k: dim[k])
        big = dsort[2]
        role, conf, why = "INTERNAL", 0.5, "默认内部件"

        # 1) 主体大件：体积占比 > 12% 且尺寸覆盖最长轴 80% 以上
        mass = rep.get("mass", rep["vol"])
        if mass / tot > 0.12 and big > 0.8 * S:
            role, conf, why = "SHELL", 0.95, "体积/面积占比>12%且覆盖主体尺度"
        # 2) 竖直大薄板（柜类=门板）：相对最长轴判定，与单位无关
        elif (t_ax in (0, 1) and dim[t_ax] < 0.04 * S
              and dim[2] > 0.6 * GDv[2] and dsort[1] > 0.15 * S):
            role, conf, why = "DOOR", (0.9 if n >= 2 else 0.72), \
                ("竖直大薄板+成对镜像" if n >= 2 else "竖直大薄板")
        # 3) 大面板：尺寸接近主体
        elif (rep["thin"] < 0.08 and dsort[1] > 0.5 * min(GDv[0], GDv[1])
              and dsort[2] > 0.5 * S):
            role, conf, why = "PANEL", 0.8, "大面积薄板且尺寸接近主体"
        # 4) 水平板件（柜类=层板）
        elif (t_ax == 2 and dim[2] < 0.08 * S
              and dim[0] > 0.25 * GDv[0] and dim[1] > 0.25 * GDv[1]):
            role, conf, why = "SHELF", 0.9, "水平大薄板+相对竖直轴分层"
        # 5) 细长杆件
        elif dsort[0] < 0.05 * S and dsort[2] > 0.4 * S:
            role, conf, why = "FRAME", 0.8, "细长杆件/立柱"
        # 6) 小型重复件：阈值 = 包络体积的 5e-5（壳体用 proxy mass）
        elif n >= 3 and mass < vol_small:
            role, conf, why = "HARDWARE", 0.85, f"同型件×{n}且小体积"
        # 7) 同型组件：重复但不小
        elif n >= 3:
            role, conf, why = "REPEAT", 0.7, f"同型件×{n}"
        elif mass / tot > 0.005:
            role, conf, why = "INTERNAL", 0.6, "非重复中大件"

        if rep.get("representation") == "SHELL":
            conf *= 0.7
            why += "（壳体几何，置信度降级）"
        for r in g:
            r["role"], r["conf"], r["why"] = role, conf, why
            r["label"] = labels[role]
            r["sig"] = key
            r["sig_n"] = n

    if override:
        cnt = 0
        for r in recs:
            k = str(r["i"])
            if k in override and override[k] in ROLES:
                r["role"] = override[k]
                r["label"] = labels[override[k]]
                r["why"] = "人工标注 (semantic_map.json)"
                r["conf"] = 1.0
                cnt += 1
        print(f"[override] 应用人工标注 {cnt} 件", flush=True)
    return groups


def make_compound(solids, ids):
    comp = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(comp)
    for i in ids:
        b.Add(comp, solids[i])
    return comp


def set_uda(label, values):
    """把基础尺寸卡片写入 XCAF NamedData。

    Mayo 官方 XCaf_DocumentTreeNodePropertiesProvider 会自动读取 NamedData，
    在选中树节点的 MetaData 分组显示；无需改 Mayo、无需插件、无需盲点坐标。
    """
    if label.IsNull() or not values:
        return
    nd = TDataStd_NamedData.Set_s(label)
    for key, value in values.items():
        nd.setString(es(str(key)), es(str(value)))


def add_named(st, parent, child, text, uda=None):
    """AddComponent 同时给「形状定义 label」和「组件实例 label」命名/写属性。

    XCAF 的 GetComponents 返回实例 label，默认名是 =>[0:1:1:2]；
    Mayo/OCC 一般读定义名，但双保险可让任何查看器都显示中文语义名。
    """
    TDataStd_Name.Set_s(child, es(text))
    if uda:
        set_uda(child, uda)
    inst = st.AddComponent(parent, child, TopLoc_Location(gp_Trsf()))
    if not inst.IsNull():
        TDataStd_Name.Set_s(inst, es(text))
        if uda:
            set_uda(inst, uda)
    return inst


def dump_tree(st, root, max_depth=3):
    """写盘前在内存里核对装配树，避免 STEP 往返后才发现中间层被压平。"""
    def nm(lab):
        a = TDataStd_Name()
        if lab.FindAttribute(TDataStd_Name.GetID_s(), a):
            return a.Get().ToExtString()
        return "?"
    def walk(lab, d):
        if d > max_depth:
            return
        kids = Sequence_TDF_Label()
        st.GetComponents_s(lab, kids)
        print("   " * d + f"[{nm(lab)}] -> {kids.Size()} 组件")
        for i in range(1, min(kids.Size(), 4) + 1):
            inst = kids.Value(i)
            ref = TDF_Label()
            walk(ref if st.GetReferredShape_s(inst, ref) else inst, d + 1)
        if kids.Size() > 4:
            print("   " * (d + 1) + f"... 另 {kids.Size()-4} 个")
    walk(root, 0)


def build_tree(st, solids, recs, groups, title):
    """阶段四：写 XCAF 树（3 层，与已验证可行的 group_step.py 同构）。

    根 → 语义角色组（带 A 外壳 / B 内部 / C 未分类 前缀）→ 同型件叶子。
    早期 4 层版本（根→层→角色→叶子）写 STEP 往返后中间层被压平，
    读回只剩 2 个无名组件实例，故压到 3 层：小白也少点一次展开。
    """
    root = st.NewShape()
    TDataStd_Name.Set_s(root, es(title))

    bucket = defaultdict(list)
    for r in recs:
        bucket[r["role"]].append(r)

    total_nodes = 0
    for role, items in sorted(bucket.items(), key=lambda x: ROLES[x[0]][2]):
        cn = items[0].get("label", ROLES[role][0])
        layer = ROLES[role][1]
        rnode = st.NewShape()
        TDataStd_Name.Set_s(rnode, es(f"{layer} {cn} ({len(items)}件)"))

        subs = defaultdict(list)
        for r in items:
            subs[r["sig"]].append(r)
        subs_sorted = sorted(subs.items(), key=lambda kv: -len(kv[1]))

        if len(subs_sorted) <= 40:
            for sig, g in subs_sorted:
                lab = st.AddShape(make_compound(solids, [r["i"] for r in g]), False)
                dvals = [max(r["bb"][k+3] for r in g) - min(r["bb"][k] for r in g)
                         for k in range(3)]
                d = "×".join(f"{v:.4g}" for v in sorted(dvals))
                nm = f"{cn} ×{len(g)} [{d}]"
                uda = {
                    "Role": role, "Layer": {"A": "EXTERIOR", "B": "INTERIOR", "C": "UNKNOWN"}.get(layer, layer),
                    "EntityCount": len(g), "Size_XYZ": "x".join(f"{v:.4g}" for v in dvals),
                    "Volume": f"{sum(r['vol'] for r in g):.6g}",
                    "SurfaceArea": f"{sum(r['area'] for r in g):.6g}",
                    "Rule": role + "_GEOMETRY", "Confidence": f"{g[0]['conf']:.0%}",
                }
                add_named(st, rnode, lab, nm, uda)
                total_nodes += 1
        else:
            for sig, g in subs_sorted[:39]:
                lab = st.AddShape(make_compound(solids, [r["i"] for r in g]), False)
                dvals = [max(r["bb"][k+3] for r in g) - min(r["bb"][k] for r in g)
                         for k in range(3)]
                d = "×".join(f"{v:.4g}" for v in sorted(dvals))
                nm = f"{cn} ×{len(g)} [{d}]"
                uda = {
                    "Role": role, "Layer": {"A": "EXTERIOR", "B": "INTERIOR", "C": "UNKNOWN"}.get(layer, layer),
                    "EntityCount": len(g), "Size_XYZ": "x".join(f"{v:.4g}" for v in dvals),
                    "Volume": f"{sum(r['vol'] for r in g):.6g}",
                    "SurfaceArea": f"{sum(r['area'] for r in g):.6g}",
                    "Rule": role + "_GEOMETRY", "Confidence": f"{g[0]['conf']:.0%}",
                }
                add_named(st, rnode, lab, nm, uda)
                total_nodes += 1
            rest = [r for _, g in subs_sorted[39:] for r in g]
            # 分桶宽度按整体尺度的 1/10，不能写死 200mm（米级单位会全落一桶）。
            # 使用产品识别阶段选出的竖直轴，而不是硬编码全局 Z。
            S = max((max(r["dim"]) for r in recs), default=0.0) or 1.0
            vertical_axis = recs[0].get("vertical_axis", 2) if recs else 2
            zb = S / 10.0
            zs = defaultdict(list)
            for r in rest:
                # 用识别出的竖直轴做分层；任意朝向 STEP 不再强行按全局 Z 切。
                zs[int(r["ctr"][vertical_axis] // zb)].append(r)
            for zi, g in sorted(zs.items()):
                lab = st.AddShape(make_compound(solids, [r["i"] for r in g]), False)
                nm = f"{cn} Z层{zi} ({len(g)}件)"
                dvals = [max(r["bb"][k+3] for r in g) - min(r["bb"][k] for r in g)
                         for k in range(3)]
                uda = {
                    "Role": role, "Layer": {"A": "EXTERIOR", "B": "INTERIOR", "C": "UNKNOWN"}.get(layer, layer),
                    "EntityCount": len(g), "Size_XYZ": "x".join(f"{v:.4g}" for v in dvals),
                    "Volume": f"{sum(r['vol'] for r in g):.6g}",
                    "SurfaceArea": f"{sum(r['area'] for r in g):.6g}",
                    "Rule": "Z_BAND", "Confidence": "70%",
                }
                add_named(st, rnode, lab, nm, uda)
                total_nodes += 1
        add_named(st, root, rnode, f"{layer} {cn} ({len(items)}件)")
        total_nodes += 1
    st.UpdateAssemblies()
    print("[tree-memory]", flush=True)
    dump_tree(st, root)
    return root, total_nodes


def main():
    if len(sys.argv) < 3:
        print(__doc__); return 2
    src, dst = sys.argv[1], sys.argv[2]
    DRY = "--dry" in sys.argv
    map_path = None
    if "--map" in sys.argv:
        mi = sys.argv.index("--map") + 1
        if mi < len(sys.argv) and not sys.argv[mi].startswith("--"):
            map_path = sys.argv[mi]
        else:
            print("[warn] --map 后缺少文件名，已忽略", flush=True)
    t0 = time.time()

    try:
        solids, recs, st_src, ct_src, doc = extract(src)
    except Exception as e:
        print(f"[error] STEP 读取失败: {type(e).__name__}: {e}", flush=True)
        return 1
    if not solids:
        print("[warn] 0 个实体，跳过语义分组（不会生成半成品）", flush=True)
        return 1
    ptype, GD, ev = infer_product(recs)
    if ptype in ("空模型", "退化模型"):
        print(f"[warn] {ptype}，跳过语义分组", flush=True)
        return 1
    print(f"\n=== 这是什么: {ptype} ===")
    for e in ev:
        print("   ·", e)

    override = None
    if map_path and os.path.exists(map_path):
        try:
            with open(map_path, encoding="utf-8") as f:
                override = json.load(f).get("overrides") or {}
        except Exception as e:
            print(f"[warn] 标注文件不可读，已忽略: {e}", flush=True)
    groups = classify(recs, GD, override, ptype)

    # 报告
    print("\n=== 语义分类结果 ===")
    cnt = Counter(r["role"] for r in recs)
    for role, n in sorted(cnt.items(), key=lambda x: ROLES[x[0]][2]):
        sample = next((r for r in recs if r["role"] == role), None)
        cn = sample.get("label", ROLES[role][0]) if sample else ROLES[role][0]
        layer = ROLES[role][1]
        why = Counter(r["why"] for r in recs if r["role"] == role).most_common(1)[0][0]
        print(f"  [{layer}] {cn:<10s} {n:4d} 件  ({why})")
    print(f"  几何指纹组 {len(groups)} 个, ≥3件重复组覆盖 "
          f"{sum(len(g) for g in groups.values() if len(g)>=3)} 件")

    # 外部标签文件（借鉴 SegCAD：分组可人工编辑，改完 --map 回灌）
    out_map = os.path.splitext(dst)[0] + ".semantic_map.json"
    # 人工标注不能被重跑冲掉
    existing_ov = {}
    if os.path.exists(out_map):
        try:
            with open(out_map, encoding="utf-8") as f:
                existing_ov = json.load(f).get("overrides") or {}
        except Exception:
            pass
    # --map 可能指向旧的正式 map，而本次输出写入临时路径；
    # 必须把传入标注带到新 map，否则原子提交后标注签名会丢失，
    # 下一次「成品不存在重试」就会回到自动分类。
    if override:
        existing_ov = override
    payload = {
        "source": os.path.basename(src),
        "product_type": ptype,
        "global_dim": [float(v) for v in GD],   # 单位是 STEP 自己的单位，不假设 mm
        "vertical_axis": recs[0].get("vertical_axis", 2),
        "representation": recs[0].get("representation", "SOLID"),
        "unit_agnostic": True,                  # 所有判据均为相对比例
        "roles": {k: v[0] for k, v in ROLES.items()},
        "labels_used": "cabinet" if "柜" in ptype or "方舱" in ptype else "generic",
        "overrides": existing_ov,  # {"<solid index>": "ROLE_KEY"} 手工改这里，重跑不会丢
        "records": [{k: r[k] for k in
                     ("i", "dim", "ctr", "vol", "mass", "color", "role", "label",
                      "conf", "why", "sig_n", "vertical_axis", "representation")
                     if k in r}
                    for r in recs],
    }
    with open(out_map, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"\n[map] {out_map}")

    if DRY:
        print(f"--dry 结束，未写 STEP。总耗时 {time.time()-t0:.1f}s")
        return 0

    ndoc = TDocStd_Document(es("sem"))
    st = XCAFDoc_DocumentTool.ShapeTool_s(ndoc.Main())
    title = f"{os.path.splitext(os.path.basename(src))[0]} · {ptype}"
    root, nn = build_tree(st, solids, recs, groups, title)
    print(f"[tree] {nn} 个节点, {time.time()-t0:.1f}s", flush=True)

    # 颜色：按原 solid 转移（XCAF 会跟着 shape 走）
    ct_new = XCAFDoc_DocumentTool.ColorTool_s(ndoc.Main())
    col = Quantity_Color()
    n_col = 0
    for s in solids:
        found = ct_src.GetColor(s, XCAFDoc_ColorType.XCAFDoc_ColorSurf, col)
        if not found:
            found = ct_src.GetColor(s, XCAFDoc_ColorType.XCAFDoc_ColorGen, col)
        if found:
            ct_new.SetColor(s, col, XCAFDoc_ColorType.XCAFDoc_ColorSurf)
            n_col += 1
    print(f"[color] {n_col}/{len(solids)}", flush=True)

    w = STEPCAFControl_Writer()
    w.SetColorMode(True)
    w.SetNameMode(True)
    w.Transfer(ndoc, STEPControl_StepModelType.STEPControl_AsIs)
    status = w.Write(dst)
    print(f"[write] {status} -> {dst}, 总耗时 {time.time()-t0:.1f}s")
    return 0 if str(status).endswith("RetDone") else 1


if __name__ == "__main__":
    sys.exit(main())
