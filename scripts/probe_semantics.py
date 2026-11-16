# -*- coding: utf-8 -*-
"""probe_semantics.py —— 语义分组判据验证原型（只读，不改任何交付文件）。

对扁平 STEP 的每个 solid 提取可解释几何/外观特征，输出 JSON + 统计，
用来判断哪些判据真能区分「外壳 / 门板 / 层板 / 内部 / 五金」。

用法: python probe_semantics.py <输入.step> [输出.json]
"""
import sys, os, json, time, math
from collections import Counter, defaultdict

from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorType
from OCP.collections import Sequence_TDF_Label
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_SurfaceType
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.TopoDS import TopoDS


def bbox(shape):
    b = Bnd_Box()
    BRepBndLib.Add_s(shape, b, True)
    return [b.GetXMin(), b.GetYMin(), b.GetZMin(),
            b.GetXMax(), b.GetYMax(), b.GetZMax()]


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


def face_signature(solid, max_faces=400):
    """返回 (总面数, 平面数, 最大平面面积, 最大平面法向, 主法向轴)"""
    n_face = 0
    n_plane = 0
    planes = []
    ex = TopExp_Explorer(solid, TopAbs_ShapeEnum.TopAbs_FACE)
    area_prop = GProp_GProps()
    while ex.More() and n_face < max_faces:
        f = TopoDS.Face(ex.Current())
        n_face += 1
        try:
            ad = BRepAdaptor_Surface(f)
            if ad.GetType() == GeomAbs_SurfaceType.GeomAbs_Plane:
                n_plane += 1
                BRepGProp.SurfaceProperties_s(f, area_prop)
                a = area_prop.Mass()
                d = ad.Plane().Axis().Direction()
                planes.append((a, (abs(d.X()), abs(d.Y()), abs(d.Z()))))
        except Exception:
            pass
        ex.Next()
    if not planes:
        return n_face, n_plane, 0.0, None
    planes.sort(reverse=True, key=lambda x: x[0])
    a, d = planes[0]
    axis = "XYZ"[d.index(max(d))]
    return n_face, n_plane, round(a, 2), axis


def main():
    src = sys.argv[1]
    out_json = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "probe_result.json")
    t0 = time.time()

    doc = TDocStd_Document(TCollection_ExtendedString("probe"))
    rdr = STEPCAFControl_Reader()
    rdr.SetNameMode(True)
    rdr.SetColorMode(True)
    rdr.ReadFile(src)
    rdr.Transfer(doc)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    ct = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    labels = Sequence_TDF_Label()
    st.GetFreeShapes(labels)
    root = st.GetShape_s(labels.Value(1))
    print(f"[read] {time.time()-t0:.1f}s", flush=True)

    solids = []
    ex = TopExp_Explorer(root, TopAbs_ShapeEnum.TopAbs_SOLID)
    while ex.More():
        solids.append(ex.Current())
        ex.Next()
    print(f"solids: {len(solids)}", flush=True)

    FAST = "--fast" in sys.argv
    recs = []
    vp, sp = GProp_GProps(), GProp_GProps()
    for i, s in enumerate(solids):
        bb = bbox(s)
        dims = [bb[3]-bb[0], bb[4]-bb[1], bb[5]-bb[2]]
        try:
            BRepGProp.VolumeProperties_s(s, vp)
            vol = vp.Mass()
        except Exception:
            vol = 0.0
        try:
            BRepGProp.SurfaceProperties_s(s, sp)
            area = sp.Mass()
        except Exception:
            area = 0.0
        if FAST:
            n_face, n_plane, max_plane_area, axis = 0, 0, 0.0, None
        else:
            n_face, n_plane, max_plane_area, axis = face_signature(s)
        col = color_of(ct, s)
        if col is None:
            # 颜色常挂在 face 上而不是 solid 上，下钻取第一个有效色
            fex = TopExp_Explorer(s, TopAbs_ShapeEnum.TopAbs_FACE)
            nf = 0
            while fex.More() and nf < 40 and col is None:
                col = color_of(ct, fex.Current())
                fex.Next()
                nf += 1
        dsort = sorted(dims)
        rec = {
            "i": i,
            "bb": [round(v, 2) for v in bb],
            "dim": [round(v, 2) for v in dims],
            "ctr": [round((bb[k]+bb[k+3])/2, 2) for k in range(3)],
            "vol": round(vol, 2),
            "area": round(area, 2),
            "thin": round(dsort[0]/dsort[2], 5) if dsort[2] > 0 else 0,
            "mid": round(dsort[1], 2),
            "n_face": n_face, "n_plane": n_plane,
            "max_plane_area": max_plane_area, "axis": axis,
            "color": col,
        }
        recs.append(rec)
        if (i+1) % 50 == 0:
            print(f"  {i+1}/{len(solids)} {time.time()-t0:.1f}s", flush=True)

    gx0 = min(r["bb"][0] for r in recs); gy0 = min(r["bb"][1] for r in recs)
    gz0 = min(r["bb"][2] for r in recs); gx1 = max(r["bb"][3] for r in recs)
    gy1 = max(r["bb"][4] for r in recs); gz1 = max(r["bb"][5] for r in recs)
    gdim = [gx1-gx0, gy1-gy0, gz1-gz0]
    # 贴边度：bbox 面到整体包络面的距离；<2mm 视为贴边
    tol = 2.0
    for r in recs:
        bb = r["bb"]
        r["edge"] = [axis for k, axis in enumerate("XYZ")
                     if abs(bb[k]- (gx0,gy0,gz0)[k]) < tol
                     or abs(bb[k+3]-(gx1,gy1,gz1)[k]) < tol]
        r["edge_n"] = len(r["edge"])
        r["vol_frac"] = round(r["vol"] / max(1.0, gdim[0]*gdim[1]*gdim[2]) * 100, 5)

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"global_bbox": [gx0,gy0,gz0,gx1,gy1,gz1],
                   "global_dim": [round(v,2) for v in gdim],
                   "records": recs}, f, ensure_ascii=False)
    print(f"\n=== 全局包络 mm: {[round(v,1) for v in gdim]} ===")
    print(f"=== 写出 {out_json} ({time.time()-t0:.1f}s) ===\n")

    # ---- 统计 ----
    print("--- 颜色分布 (top 20) ---")
    cc = Counter(tuple(r["color"]) if r["color"] else None for r in recs)
    for c, n in cc.most_common(20):
        print(f"  {str(c):28s} {n:4d}")

    print("\n--- 薄板比 thin = 最小边/最长边 ---")
    buckets = [(0,0.01),(0.01,0.03),(0.03,0.1),(0.1,0.3),(0.3,0.6),(0.6,1.01)]
    for lo, hi in buckets:
        n = sum(1 for r in recs if lo <= r["thin"] < hi)
        print(f"  [{lo:.2f},{hi:.2f}) : {n:4d}")

    print("\n--- 体积分位 (mm^3) ---")
    vs = sorted(r["vol"] for r in recs)
    for q in (0, .1, .25, .5, .75, .9, .95, .99, 1):
        v = vs[min(len(vs) - 1, int(q * (len(vs) - 1)))]
        print(f"  q{q:<5}: {v:>14.1f}")

    print("\n--- 主平面法向轴 ---")
    print(" ", Counter(r["axis"] for r in recs))
    print("--- 贴边数分布 ---")
    print(" ", Counter(r["edge_n"] for r in recs))

    print("\n--- 体积 Top15 (可能是外壳大板) ---")
    for r in sorted(recs, key=lambda x: -x["vol"])[:15]:
        print(f"  #{r['i']:<4} vol={r['vol']:>13.1f} dim={r['dim']} "
              f"thin={r['thin']:.3f} edge={''.join(r['edge']):4s} "
              f"axis={r['axis']} col={r['color']}")

    print("\n--- 面积 Top15 (可能是大面积面板) ---")
    for r in sorted(recs, key=lambda x: -x["area"])[:15]:
        print(f"  #{r['i']:<4} area={r['area']:>12.1f} dim={r['dim']} "
              f"thin={r['thin']:.3f} edge={''.join(r['edge']):4s} col={r['color']}")

    print("\n--- 重复几何签名 (dim 取整 + vol 取整, top 15 组) ---")
    sig = defaultdict(list)
    for r in recs:
        k = (tuple(round(v) for v in sorted(r["dim"])), round(r["vol"]))
        sig[k].append(r["i"])
    for k, ids in sorted(sig.items(), key=lambda x: -len(x[1]))[:15]:
        print(f"  {len(ids):3d} 件  dim={k[0]} vol={k[1]}  ids={ids[:8]}")


if __name__ == "__main__":
    sys.exit(main())
