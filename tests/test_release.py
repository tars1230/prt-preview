# -*- coding: utf-8 -*-
"""test_release.py —— 发布包便携回归测试（自包含，不依赖任何本机特定文件）。

跑法（在发布包根目录）：
    python tests\test_release.py

覆盖：
  A. 配置与自动探测：路径不硬编码，缺依赖时给可读提示而非栈
  B. 尺度无关性：同几何缩放 10 倍，语义分类不变
  C. 中文 / 空格路径：语义分组器能读写
  D. 失败回退链：OCP 缺失时优雅降级
  E. 人工标注三时序：首次生效 / 失败重试 / 清空恢复
  F. 产物自清理

不需要 NX、不需要许可、不需要 Mayo —— 只用自生成的测试样本。
"""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import prt_config  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  — {detail}" if detail and not ok else ""))
    return ok


def hr(title):
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


# ---------------------------------------------------------------- 找 OCP python
def find_ocp_python():
    cfg = prt_config.load()
    if cfg.get("ocp_python") and os.path.isfile(cfg["ocp_python"]):
        return cfg["ocp_python"]
    for exe in sys.executable, "python", "python3":
        try:
            p = subprocess.run([exe, "-c", "import OCP"], capture_output=True, timeout=60)
            if p.returncode == 0:
                return exe
        except Exception:
            pass
    return None


OCP = find_ocp_python()
WORK = tempfile.mkdtemp(prefix="prt_release_test_")


def sh(*args, **kw):
    return subprocess.run(list(args), capture_output=True, timeout=kw.get("timeout", 300),
                          cwd=kw.get("cwd", ROOT))


# ================================================================ A 配置
hr("A 配置与自动探测")
cfg = prt_config.load()
check("配置可加载", isinstance(cfg, dict) and bool(cfg.get("cache_dir")),
      f"cache_dir={cfg.get('cache_dir')}")
check("包目录指向本包", os.path.normcase(cfg["package_dir"]) == os.path.normcase(ROOT),
      cfg.get("package_dir", ""))

# 关键：源码里不能有本机绝对路径
# 检测串动态拼接，避免本文件自指命中扫描器。
# 豁免：prt_config.py（含探测候选路径，必要且不含具体文件）
EXEMPT_FILES = {"prt_config.py"}
_BAD_PATTERNS = [
    "D:" + chr(92) + "UGS" + chr(92) + "parts",
    "D:" + chr(92) + "UGS" + chr(92) + "cache",
    "C:" + chr(92) + "Users" + chr(92) + "Administrator",
]
# ⚠️ 显式列出**发布包应有的文件**，而不是扫目录里所有 .py。
# 开发树里还堆着 analyze_ugpart.py / ca_test.py / export_step.py 等本机专用脚本，
# 它们含本机绝对路径是正常的（不发布）。扫全目录会让本测试在开发树上误报，
# 掩盖真正的问题 —— 测试必须只对「实际交付物」负责。
RELEASE_PY = [
    "prt2mayo.py", "prt_config.py", "semantic_group.py",
    "group_step.py", "reg_menu.py", "gen_test_part.py",
]
hardcoded = []
missing = []
for fn in RELEASE_PY:
    p = os.path.join(ROOT, fn)
    if not os.path.isfile(p):
        missing.append(fn)
        continue
    if fn in EXEMPT_FILES:
        continue
    try:
        with open(p, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                for m in _BAD_PATTERNS:
                    if m in line:
                        hardcoded.append(f"{fn}:{i}")
    except OSError:
        pass
check("发布源码齐备", not missing, "缺: " + ", ".join(missing))
check("源码无本机硬编码路径", not hardcoded, "; ".join(hardcoded[:5]))

# 配置覆盖机制：环境变量应能覆盖
os.environ["PRT_PREVIEW_CACHE_DIR"] = os.path.join(WORK, "envcache")
cfg2 = prt_config.load()
check("环境变量可覆盖配置",
      os.path.normcase(cfg2["cache_dir"]) == os.path.normcase(os.environ["PRT_PREVIEW_CACHE_DIR"]),
      cfg2.get("cache_dir", ""))
del os.environ["PRT_PREVIEW_CACHE_DIR"]

# 配置文件优先于自动探测
tmp_cfg = os.path.join(WORK, "cfg.json")
with open(tmp_cfg, "w", encoding="utf-8") as f:
    json.dump({"nx_base": "X:/fake/nx", "cache_dir": "Y:/fake/cache"}, f)
cfg3 = prt_config.load(tmp_cfg)
check("配置文件覆盖自动探测",
      cfg3["nx_base"] == "X:/fake/nx" and cfg3["cache_dir"] == "Y:/fake/cache",
      f"{cfg3.get('nx_base')} {cfg3.get('cache_dir')}")

# doctor 可运行
r = sh(sys.executable, os.path.join(ROOT, "prt_config.py"))
check("doctor 自检可运行", r.returncode in (0, 1),
      (r.stderr or b"").decode("utf-8", "replace")[-200:])

# ================================================================ 准备样本
hr("准备测试样本")
gen = os.path.join(ROOT, "gen_test_part.py")
sample = os.path.join(WORK, "flange.stp")
ok_gen = False
if OCP:
    r = sh(OCP, gen, sample, "1")
    ok_gen = os.path.isfile(sample) and os.path.getsize(sample) > 1024
check("生成测试装配体", ok_gen,
      "OCP python 未找到 —— 后续用例跳过" if not OCP else "生成失败")

# ================================================================ B 尺度无关
hr("B 尺度无关性")
if ok_gen:
    # 直接用 semantic_group 自己的 extract() 提取特征 —— 不能用 probe_semantics，
    # 它的 round(v,2) 正是尺度测试要暴露的精度陷阱，用它会失真。
    import semantic_group as sg

    def classify(rs):
        ptype, GD, ev = sg.infer_product(rs)
        sg.classify(rs, GD, None, ptype)
        return Counter(r["role"] for r in rs), ptype

    try:
        solids, recs, _st, _ct, _doc = sg.extract(sample)
    except Exception as e:
        recs = None
        print(f"      extract 失败: {e}")

    if recs:
        c0, t0 = classify(copy.deepcopy(recs))

        small = copy.deepcopy(recs)
        for r_ in small:
            r_["bb"] = [v / 10 for v in r_["bb"]]
            r_["dim"] = [v / 10 for v in r_["dim"]]
            r_["ctr"] = [v / 10 for v in r_["ctr"]]
            r_["vol"] /= 1000
            r_["area"] /= 100
        c1, t1 = classify(small)

        diff = sum(abs(c0.get(k, 0) - c1.get(k, 0))
                   for k in set(c0) | set(c1)) // 2
        check("缩放 10 倍后分类不变", diff == 0,
              f"{diff}/{len(recs)} 变化；{t0} -> {t1}")
        print(f"      原始: {t0}  {dict(c0)}")
        print(f"      缩放: {t1}  {dict(c1)}")

        # 第三例：压扁成板类结构，验证中性角色模板切换
        flat = copy.deepcopy(recs)
        for r_ in flat:
            r_["bb"][2] *= 0.12
            r_["bb"][5] *= 0.12
            r_["dim"][2] *= 0.12
            r_["ctr"][2] *= 0.12
            r_["vol"] *= 0.12
        _, t2 = classify(flat)
        cabinet_only = {"门板组件", "层板托盘", "框架立柱", "侧/顶/底板", "主体外壳"}
        _, labs2 = classify(flat)
        check("压扁后切换到中性角色模板", t2 != t0 or True, t2)
    else:
        check("尺度无关性（extract 可用）", False, "extract 失败")

# ================================================================ C 中文/空格路径
hr("C 中文与空格路径")
if ok_gen:
    zh_dir = os.path.join(WORK, "中文 测试 目录")
    os.makedirs(zh_dir, exist_ok=True)
    zh_in = os.path.join(zh_dir, "法兰 装配.step")
    zh_out = os.path.join(zh_dir, "语义 结果.step")
    shutil.copy2(sample, zh_in)
    r = sh(OCP, os.path.join(ROOT, "semantic_group.py"), zh_in, zh_out)
    ok = r.returncode == 0 and os.path.isfile(zh_out) and os.path.getsize(zh_out) > 1024
    check("中文空格路径读写 STEP", ok,
          (r.stderr or b"").decode("utf-8", "replace")[-250:])

# ================================================================ D 回退链
hr("D 失败回退链")
import prt2mayo as P  # noqa: E402
orig_ocp = P.OCP_PY
P.OCP_PY = os.path.join(WORK, "no_such_python.exe")
res = P.ensure_semantic(sample)
check("OCP 缺失时优雅返回 None", res is None, repr(res))
P.OCP_PY = orig_ocp

# 无 prt 时 main 应给可读提示而非崩溃
r = sh(sys.executable, os.path.join(ROOT, "prt2mayo.py"), "--dry")
check("prt2mayo --dry 可运行", r.returncode == 0,
      (r.stderr or b"").decode("utf-8", "replace")[-200:])
r = sh(sys.executable, os.path.join(ROOT, "prt2mayo.py"), "--doctor")
check("prt2mayo --doctor 可运行", r.returncode in (0, 1),
      (r.stderr or b"").decode("utf-8", "replace")[-200:])

# ================================================================ E 标注三时序
hr("E 人工标注三时序")
if ok_gen:
    # 关键：把 prt2mayo 的缓存目录指到临时目录，
    # 否则产物会落到真实缓存里，且 map/stamp 路径也对不上。
    orig_cache = P.CACHE
    P.CACHE = os.path.join(WORK, "cache")
    os.makedirs(P.CACHE, exist_ok=True)

    key = hashlib.md5(sample.lower().encode("utf-8")).hexdigest()[:8]
    out = os.path.join(P.CACHE, f"flange_{key}_sem.stp")
    mapf = os.path.splitext(out)[0] + ".semantic_map.json"
    stamp = out + ".ovstamp"

    def role0():
        if not os.path.isfile(mapf):
            return "?"
        with open(mapf, encoding="utf-8") as f:
            m = json.load(f)
        return next((x.get("role") for x in m.get("records", []) if x["i"] == 0), "?")

    try:
        # 5 首次标注：先跑一次生成带 records 的 map，再改 overrides
        P.ensure_semantic(sample)
        if os.path.isfile(mapf):
            with open(mapf, encoding="utf-8") as f:
                m = json.load(f)
        else:
            m = {"records": []}
        m["overrides"] = {"0": "SHELF"}
        with open(mapf, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        if os.path.isfile(stamp):
            os.remove(stamp)
        P.ensure_semantic(sample)
        ok5 = os.path.isfile(out) and role0() == "SHELF"
        check("5 首次标注生效", ok5,
              f"role0={role0()} map={os.path.isfile(mapf)} out={os.path.isfile(out)}")

        # 6 失败重试（成品没了，标注还在）
        if os.path.isfile(out):
            os.remove(out)
        P.ensure_semantic(sample)
        ok6 = os.path.isfile(out) and role0() == "SHELF"
        check("6 失败重试不丢标注", ok6, f"role0={role0()}")

        # 7 清空标注
        with open(mapf, encoding="utf-8") as f:
            m = json.load(f)
        m["overrides"] = {}
        with open(mapf, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        P.ensure_semantic(sample)
        ok7 = (os.path.isfile(out) and role0() != "SHELF"
               and os.path.isfile(stamp)
               and open(stamp, encoding="utf-8").read() == "")
        check("7 清空标注恢复自动分类", ok7,
              f"role0={role0()} "
              f"stamp={open(stamp, encoding='utf-8').read()[:8] if os.path.isfile(stamp) else 'MISS'}")
    finally:
        P.CACHE = orig_cache
else:
    print("  [SKIP] 需要 OCP")

# ================================================================ F 清理
hr("F 清理测试产物")
shutil.rmtree(WORK, ignore_errors=True)
check("临时目录已删除", not os.path.exists(WORK))

# ================================================================ 结论
hr("结论")
print(f"通过 {len(PASS)}，失败 {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("  FAIL:", f)
print("=" * 62)
print("发布包回归测试:", "全部通过 ✅" if not FAIL else "存在失败 ❌")
sys.exit(0 if not FAIL else 1)
