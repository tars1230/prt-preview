# -*- coding: utf-8 -*-
"""group_step.py —— 扁平 STEP 自动空间分组器。

读入单体扁平 STEP（如 464 个 solid 堆在一个 product 里），按空间递归剖分
（沿最长轴按中位数二分），生成多级装配结构写回 STEP AP214。
Mayo/其他 OCC 系查看器的模型树即出现可整体隐藏的组件层级。

用法: python group_step.py <输入.step> <输出.step> [每组上限=32] [最大深度=3]
"""
import sys, time
from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.collections import Sequence_TDF_Label
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.TopoDS import TopoDS_Compound
from OCP.BRep import BRep_Builder
from OCP.TopLoc import TopLoc_Location
from OCP.gp import gp_Trsf
from OCP.STEPControl import STEPControl_StepModelType
from OCP.Quantity import Quantity_Color
from OCP.XCAFDoc import XCAFDoc_ColorType


def es(s):
    """中文安全的 ExtendedString。默认构造写出的中文读回是乱码（如 59ä»¶），
    必须显式按 UTF-8 多字节解析（与 semantic_group.py 保持一致）。"""
    try:
        return TCollection_ExtendedString(s, True)
    except Exception:
        return TCollection_ExtendedString(s)


def bbox(solid):
    b = Bnd_Box()
    BRepBndLib.Add_s(solid, b, True)
    return (b.GetXMin(), b.GetYMin(), b.GetZMin(),
            b.GetXMax(), b.GetYMax(), b.GetZMax())


def split_group(items, bbs, max_size, max_depth, depth=0):
    """items: [(idx, solid)]；返回树: ('leaf', items) 或 ('node', [(name_hint, subtree)...])"""
    if len(items) <= max_size or depth >= max_depth:
        return ("leaf", items)
    # 最长轴
    x0 = min(bbs[i][0] for i, _ in items)
    y0 = min(bbs[i][1] for i, _ in items)
    z0 = min(bbs[i][2] for i, _ in items)
    x1 = max(bbs[i][3] for i, _ in items)
    y1 = max(bbs[i][4] for i, _ in items)
    z1 = max(bbs[i][5] for i, _ in items)
    dims = (x1 - x0, y1 - y0, z1 - z0)
    ax = dims.index(max(dims))
    centers = sorted(((bbs[i][ax] + bbs[i][ax + 3]) / 2.0, i) for i, _ in items)
    mid = len(centers) // 2
    # 找一个不重合的切分点
    while mid < len(centers) - 1 and abs(centers[mid][0] - centers[mid + 1][0]) < 1e-9:
        mid += 1
    if mid >= len(centers):
        mid = len(centers) // 2
        while mid > 0 and abs(centers[mid][0] - centers[mid - 1][0]) < 1e-9:
            mid -= 1
        if mid == 0:
            return ("leaf", items)  # 无法再分（全部重叠在同一位置）
    left_ids = {i for _, i in centers[:mid]}
    L = [(i, s) for i, s in items if i in left_ids]
    R = [(i, s) for i, s in items if i not in left_ids]
    a = split_group(L, bbs, max_size, max_depth, depth + 1)
    b = split_group(R, bbs, max_size, max_depth, depth + 1)
    return ("node", [a, b])


def build_label(st, tree, bbs, prefix, counter):
    kind, payload = tree
    if kind == "leaf":
        comp = TopoDS_Compound()
        b = BRep_Builder()
        b.MakeCompound(comp)
        for _, s in payload:
            b.Add(comp, s)
        lab = st.AddShape(comp, False)
        n = len(payload)
        TDataStd_Name.Set_s(lab, es(f"{prefix} ({n}件)"))
        return lab
    lab = st.NewShape()  # 纯装配节点（无自身几何）
    TDataStd_Name.Set_s(lab, es(prefix))
    for k, sub in enumerate(payload, 1):
        child = build_label(st, sub, bbs, f"{prefix}.{k}", counter)
        st.AddComponent(lab, child, TopLoc_Location(gp_Trsf()))
    return lab


def main():
    src = sys.argv[1]
    dst = sys.argv[2]
    max_size = int(sys.argv[3]) if len(sys.argv) > 3 else 32
    max_depth = int(sys.argv[4]) if len(sys.argv) > 4 else 3
    t0 = time.time()

    doc = TDocStd_Document(TCollection_ExtendedString("src"))
    rdr = STEPCAFControl_Reader()
    rdr.SetNameMode(True)
    rdr.SetColorMode(True)
    rdr.ReadFile(src)
    rdr.Transfer(doc)
    st_src = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    ct_src = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    labels = Sequence_TDF_Label()
    st_src.GetFreeShapes(labels)
    roots = [st_src.GetShape_s(labels.Value(i))
             for i in range(1, labels.Size() + 1)
             if not st_src.GetShape_s(labels.Value(i)).IsNull()]
    if not roots:
        print("没有自由形状")
        return 1

    # 多 free-shape STEP 不能只取第一个，否则会静默漏掉独立产品。
    solids = []
    for root_shape in roots:
        ex = TopExp_Explorer(root_shape, TopAbs_ShapeEnum.TopAbs_SOLID)
        while ex.More():
            solids.append(ex.Current())
            ex.Next()
    print(f"free_shapes: {len(roots)}, solids: {len(solids)}")
    if len(solids) < 8:
        print("实体太少，无需分组")
        return 0

    bbs = [bbox(s) for s in solids]
    items = list(enumerate(solids))
    tree = split_group(items, bbs, max_size, max_depth)

    # 统计叶组
    def leaves(t):
        k, p = t
        if k == "leaf":
            return [len(p)]
        out = []
        for s in p:
            out += leaves(s)
        return out
    ls = sorted(leaves(tree), reverse=True)
    print(f"分组: {len(ls)} 个叶组, 大小前10: {ls[:10]}, 耗时 {time.time()-t0:.1f}s")

    # 建新文档
    ndoc = TDocStd_Document(TCollection_ExtendedString("new"))
    st = XCAFDoc_DocumentTool.ShapeTool_s(ndoc.Main())
    root_label = st.NewShape()
    TDataStd_Name.Set_s(root_label, es("ASSEMBLY"))
    counter = [0]
    for k, sub in enumerate(tree[1], 1):
        child = build_label(st, sub, bbs, f"GRP-{k:02d}", counter)
        st.AddComponent(root_label, child, TopLoc_Location(gp_Trsf()))
    st.UpdateAssemblies()

    # 颜色转移（solid 级；源里查得到的才设）
    col = Quantity_Color()
    n_col = 0
    ct_new = XCAFDoc_DocumentTool.ColorTool_s(ndoc.Main())
    for idx, s in items:
        found = ct_src.GetColor(s, XCAFDoc_ColorType.XCAFDoc_ColorSurf, col)
        if not found:
            found = ct_src.GetColor(s, XCAFDoc_ColorType.XCAFDoc_ColorGen, col)
        if found:
            ct_new.SetColor(s, col, XCAFDoc_ColorType.XCAFDoc_ColorSurf)
            n_col += 1
    print(f"颜色转移: {n_col}/{len(items)}")

    w = STEPCAFControl_Writer()
    w.SetColorMode(True)
    w.SetNameMode(True)
    w.Transfer(ndoc, STEPControl_StepModelType.STEPControl_AsIs)
    st_status = w.Write(dst)
    print(f"write: {st_status} -> {dst}, 总耗时 {time.time()-t0:.1f}s")
    return 0 if str(st_status).endswith("RetDone") else 1


if __name__ == "__main__":
    sys.exit(main())
