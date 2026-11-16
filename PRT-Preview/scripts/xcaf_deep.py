# -*- coding: utf-8 -*-
"""xcaf_deep.py —— 正确解引用 XCAF 组件实例，打印真实装配树。

GetComponents_s 返回的是「组件实例 label」（默认名 =>[...]），
必须 GetReferredShape_s 才能拿到形状定义 label 及其名字/子组件。
"""
import sys
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.collections import Sequence_TDF_Label


def name(lab):
    a = TDataStd_Name()
    if lab.FindAttribute(TDataStd_Name.GetID_s(), a):
        return a.Get().ToExtString()
    return "?"


def inspect(path, limit=200):
    print("=" * 20, path, flush=True)
    doc = TDocStd_Document(TCollection_ExtendedString("d"))
    rdr = STEPCAFControl_Reader()
    rdr.SetNameMode(True)
    print("ReadFile:", rdr.ReadFile(path), flush=True)
    rdr.Transfer(doc)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    frees = Sequence_TDF_Label()
    st.GetFreeShapes(frees)
    print("free shapes:", frees.Size(), flush=True)
    seen = [0]

    def walk(lab, d, is_ref=False):
        if seen[0] > limit or d > 5:
            return
        kids = Sequence_TDF_Label()
        st.GetComponents_s(lab, kids)
        inst_nm = name(lab)
        print("  " * d + f"[{inst_nm}] comp={kids.Size()}", flush=True)
        seen[0] += 1
        for i in range(1, kids.Size() + 1):
            c = kids.Value(i)
            ref = TDF_Label()
            if st.GetReferredShape_s(c, ref):
                print("  " * (d + 1) + f"inst='{name(c)}' -> def='{name(ref)}'", flush=True)
                walk(ref, d + 2)
            else:
                walk(c, d + 1)

    for i in range(1, frees.Size() + 1):
        walk(frees.Value(i), 0)


for p in sys.argv[1:]:
    inspect(p)
