# -*- coding: utf-8 -*-
"""analyze_probe.py —— 离线分析 probe_result.json，验证语义判据有效性。
不加载 STEP，纯 JSON 统计，秒级。
"""
import json, sys, math
from collections import Counter, defaultdict

p = sys.argv[1] if len(sys.argv) > 1 else "probe_result.json"
d = json.load(open(p, encoding="utf-8"))
R = d["records"]
G = d["global_bbox"]
GD = d["global_dim"]
print(f"全局包络 mm: {[round(v,1) for v in GD]}  solids={len(R)}")

# ---- 1. 几何指纹组 ----
sig = defaultdict(list)
for r in R:
    k = (tuple(round(v) for v in sorted(r["dim"])), round(r["vol"] / 100) * 100)
    sig[k].append(r)
groups = sorted(sig.values(), key=lambda g: -len(g))
sizes = Counter(len(g) for g in groups)
print(f"\n几何指纹组数: {len(groups)}  (单件组 {sizes[1]} 个, "
      f"重复组 {len(groups)-sizes[1]} 个)")
print("组大小分布:", sorted(sizes.items(), reverse=True)[:12])
multi = [g for g in groups if len(g) >= 3]
print(f">=3 件的重复组: {len(multi)} 组, 覆盖 {sum(len(g) for g in multi)} 件 "
      f"({sum(len(g) for g in multi)/len(R)*100:.0f}%)")

# 同组是否同色 / 同主色
same_color = sum(1 for g in multi if len({tuple(x["color"]) if x["color"] else None for x in g}) == 1)
print(f"重复组内颜色完全一致: {same_color}/{len(multi)} 组 "
      f"→ 颜色跟随零件，可作辅助判据")

# ---- 2. 薄板轴判据 ----
def thin_axis(r):
    return min(range(3), key=lambda k: r["dim"][k])

axc = Counter("XYZ"[thin_axis(r)] for r in R)
print(f"\n最薄轴分布: {axc}")
horiz = [r for r in R if thin_axis(r) == 2 and r["dim"][2] < 120
         and r["dim"][0] > 250 and r["dim"][1] > 250]
print(f"水平大薄板 (层板候选): {len(horiz)} 件")
print("  dim 众数:", Counter(tuple(round(v) for v in r["dim"]) for r in horiz).most_common(8))
print("  Z 中心分层:", [round(v) for v in sorted({round(r["ctr"][2]/10)*10 for r in horiz})][:40])

vert = [r for r in R if thin_axis(r) in (0, 1) and r["dim"][thin_axis(r)] < 60
        and max(r["dim"]) > 1000]
print(f"\n垂直大薄板 (门板/侧板候选): {len(vert)} 件")
for r in sorted(vert, key=lambda x: -x["vol"])[:14]:
    print(f"  #{r['i']:<4} dim={r['dim']} thin={'XYZ'[thin_axis(r)]} "
          f"vol={r['vol']:>12.0f} col={r['color']}")

longbar = [r for r in R if min(r["dim"]) < 80 and max(r["dim"]) > 800]
print(f"\n细长件 (框架/立柱候选): {len(longbar)} 件")
print("  dim 众数:", Counter(tuple(round(v) for v in sorted(r["dim"])) for r in longbar).most_common(8))

# ---- 3. 位置分布：看是否有明显分层 ----
print("\nZ 中心直方图 (100mm 一档):")
zbin = Counter(int(r["ctr"][2] // 100) * 100 for r in R)
for z in sorted(zbin):
    print(f"  {z:>5}-{z+100:<5}: {'#'*min(50, zbin[z])} {zbin[z]}")

print("\nX 中心直方图 (100mm 一档):")
xbin = Counter(int(r["ctr"][0] // 100) * 100 for r in R)
for x in sorted(xbin):
    print(f"  {x:>5}-{x+100:<5}: {'#'*min(50, xbin[x])} {xbin[x]}")

# ---- 4. 外壳候选：体积/面积 top ----
print("\n体积 Top 8 的占比:")
tot = sum(r["vol"] for r in R)
for r in sorted(R, key=lambda x: -x["vol"])[:8]:
    print(f"  #{r['i']:<4} {r['vol']/tot*100:5.1f}%  dim={r['dim']} col={r['color']}")
print(f"全部体积 = {tot/1e9:.3f} m^3, 全局包络体积 = {GD[0]*GD[1]*GD[2]/1e9:.3f} m^3")

# ---- 5. 颜色 × 语义交叉：主色组内的几何形态 ----
print("\nTop 8 颜色组内形态 (最大件 dim / 薄板数 / 小件数):")
cc = Counter(tuple(r["color"]) if r["color"] else None for r in R)
for c, n in cc.most_common(8):
    g = [r for r in R if (tuple(r["color"]) if r["color"] else None) == c]
    big = max(g, key=lambda x: x["vol"])
    thin_n = sum(1 for r in g if r["thin"] < 0.1)
    small_n = sum(1 for r in g if r["vol"] < 10000)
    print(f"  {str(c):26s} {n:3d} 件 | 最大dim={big['dim']} "
          f"| 薄板={thin_n} 小件={small_n}")
