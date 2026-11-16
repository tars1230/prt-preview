# -*- coding: utf-8 -*-
"""gen_test_part.py —— 生成一个与柜体完全无关的法兰/轴装配 STEP，
用于验证语义分组器换模型后是否仍能建出可用装配树。14 个 solids，约 90mm。
"""
import sys, math
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2
from OCP.TopoDS import TopoDS_Compound
from OCP.BRep import BRep_Builder
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.STEPControl import STEPControl_Writer, STEPControl_StepModelType


def cyl(x, y, z, r, h):
    return BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1)), r, h).Shape()


def box(x, y, z, dx, dy, dz):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), dx, dy, dz).Shape()


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "_test_flange.stp"
    k = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0   # 尺度：0.001 ≈ 米单位数值
    C = lambda x, y, z, r, h: cyl(x*k, y*k, z*k, r*k, h*k)
    B = lambda x, y, z, a, b, c: box(x*k, y*k, z*k, a*k, b*k, c*k)
    sh = []
    if "--no-solid" in sys.argv:
        # 只有面、没有 solid 的退化 STEP，测空 solid 分支
        bx = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 10*k, 10*k, 10*k).Shape()
        ex = TopExp_Explorer(bx, TopAbs_ShapeEnum.TopAbs_FACE)
        sh = [ex.Current()]
    elif "--single" in sys.argv:
        sh = [C(0, 0, 0, 45, 12)]
    else:
        sh.append(C(0, 0, 0, 45, 12))               # 法兰盘主体
        sh.append(C(0, 0, 12, 8, 70))               # 中心轴
        for i in range(6):                          # 6 个同型螺栓
            a = 2 * math.pi * i / 6
            sh.append(C(35 * math.cos(a), 35 * math.sin(a), 12, 3, 20))
        for i in range(4):                          # 4 个同型支架
            a = 2 * math.pi * i / 4 + math.pi / 4
            sh.append(B(25 * math.cos(a) - 10, 25 * math.sin(a) - 7.5,
                        0, 20, 15, 8))
        sh.append(B(-30, -30, 82, 60, 60, 3))      # 顶部薄板
        sh.append(B(-30, -30, -6, 60, 60, 3))      # 底部薄板

    comp = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(comp)
    for s in sh:
        b.Add(comp, s)

    w = STEPControl_Writer()
    w.Transfer(comp, STEPControl_StepModelType.STEPControl_AsIs)
    st = w.Write(out)
    print(f"wrote {len(sh)} solids -> {out}  status={st}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
