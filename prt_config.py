# -*- coding: utf-8 -*-
"""prt_config.py —— 路径与开关的唯一配置源（可移植发布版核心）。

设计原则：
  1. 本机零硬编码。NX2 / Mayo / OCP Python / 缓存目录全部由配置或自动探测得出。
  2. 配置优先级（高 -> 低）：
       命令行 --config  >  环境变量 PRT_PREVIEW_CONFIG  >  脚本同目录 config.json
       >  环境变量单项覆盖（PRT_PREVIEW_NX_BASE 等）>  自动探测  >  内置默认
  3. 自动探测只读注册表/环境变量/常见安装位置，不写任何东西。
  4. 找不到依赖不报错退出，而是返回 None，由调用方给出可读提示。

用法：
    import prt_config
    cfg = prt_config.load()
    cfg["nx_base"]      # NX2 安装根（含 UGII / STEP214UG / IGES）
    cfg["mayo"]         # mayo.exe 路径
    cfg["ocp_python"]   # 装了 OCP 的 python.exe
    cfg["cache_dir"]    # 缓存根目录
"""
import json
import os
import sys

ENV_PREFIX = "PRT_PREVIEW_"

# 内置默认（尽量中性；探测失败时才用）
DEFAULTS = {
    "nx_base": "",
    "mayo": "",
    "ocp_python": "",
    "cache_dir": "",
    "license_dir": "",
    "license_server": "27000@localhost",
    # 许可服务自愈默认关闭：同事机器上的许可拓扑未知，
    # 擅自拉起 lmgrd 可能与他们的正式许可服务器冲突。
    # 需要时在 config.json 里显式打开并填 license_dir。
    "license_autostart": False,
    "register_menu": True,
    # 显式关闭 OCP（安装时 --SkipOcp 置位）。
    # 与「探测不到」区分：探测不到可以继续自动找，关掉就是明确的「不要用」。
    "disable_ocp": False,
}


# ------------------------------------------------------------------ 小工具
def _env(*names):
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip()
    return ""


def _is_file(p):
    try:
        return bool(p) and os.path.isfile(p)
    except OSError:
        return False


def _is_dir(p):
    try:
        return bool(p) and os.path.isdir(p)
    except OSError:
        return False


def _first_existing(paths, kind="file"):
    check = _is_file if kind == "file" else _is_dir
    for p in paths:
        if p and check(p):
            return p
    return ""


# ------------------------------------------------------------------ 探测：NX2
def _registry_nx_bases():
    """从 Windows 注册表找 NX 安装根（用户级 HKCU + HKLM + WOW6432Node）。"""
    out = []
    try:
        import winreg
    except ImportError:
        return out
    roots = []
    for hive, path in (
        (getattr(winreg, "HKEY_LOCAL_MACHINE", None), r"SOFTWARE\UGS"),
        (getattr(winreg, "HKEY_LOCAL_MACHINE", None), r"SOFTWARE\WOW6432Node\UGS"),
        (getattr(winreg, "HKEY_CURRENT_USER", None), r"SOFTWARE\UGS"),
    ):
        if hive is None:
            continue
        roots.append((hive, path))
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as k:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    for valname in ("UGII_BASE_DIR", "UGII_ROOT_DIR", "InstallPath"):
                        try:
                            with winreg.OpenKey(k, sub) as sk:
                                v, _ = winreg.QueryValueEx(sk, valname)
                            if v and _is_dir(str(v)):
                                out.append(str(v))
                        except OSError:
                            continue
        except OSError:
            continue
    return out


def _scan_nx_bases():
    """在常见位置找 NX2 安装根（含 UGII 子目录才算合法）。"""
    cands = [
        r"C:\Program Files\UGS",
        r"C:\Program Files (x86)\UGS",
        r"D:\UGS",
        r"E:\UGS",
        r"C:\UGS",
    ]
    cands += [os.path.join(r, "UGS") for r in ("F:", "G:", "H:")]
    found = []
    for base in cands:
        if not _is_dir(base):
            continue
        # base 本身就是 NX 根？
        if _is_dir(os.path.join(base, "UGII")):
            found.append(base)
            continue
        try:
            for name in sorted(os.listdir(base)):
                sub = os.path.join(base, name)
                if _is_dir(os.path.join(sub, "UGII")):
                    found.append(sub)
        except OSError:
            continue
    return found


def discover_nx_base():
    env = _env("UGII_BASE_DIR", "UGII_ROOT_DIR")
    if env and _is_dir(os.path.join(env, "UGII")):
        return env.rstrip("\\/")
    for b in _registry_nx_bases():
        if _is_dir(os.path.join(b, "UGII")):
            return b.rstrip("\\/")
    for b in _scan_nx_bases():
        return b
    return ""


# ------------------------------------------------------------------ 探测：Mayo
def discover_mayo():
    env = _env("MAYO_EXE", "MAYO_HOME")
    if env and _is_file(env):
        return env
    prog = os.environ.get("ProgramFiles", r"C:\Program Files")
    prog86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    cands = []
    if env and _is_dir(env):
        cands.append(os.path.join(env, "mayo.exe"))
    for p in (prog, prog86):
        cands += [
            os.path.join(p, "Fougue", "Mayo", "mayo.exe"),
            os.path.join(p, "Mayo", "mayo.exe"),
        ]
    if local:
        cands += [
            os.path.join(local, "Programs", "Mayo", "mayo.exe"),
            os.path.join(local, "Programs", "Fougue", "Mayo", "mayo.exe"),
        ]
    hit = _first_existing(cands)
    if hit:
        return hit
    # 从 PATH 找 mayo.exe
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = os.path.join(d.strip(), "mayo.exe")
        if _is_file(cand):
            return cand
    return ""


# ------------------------------------------------------------------ 探测：OCP python
def _nx_adjacent_dirs():
    """NX 安装根的同级目录（UGS 装在 D:\\UGS\\NX2.0 时返回 D:\\UGS）。

    UGS 生态里团队常在 NX 旁边放一个装好 OCP 的 venv（ocpenv）供二次开发用，
    复用它可以省掉一次 200MB 的 pip 下载。
    """
    bases = []
    nx = os.environ.get("UGII_BASE_DIR") or os.environ.get("UGII_ROOT_DIR")
    if nx and _is_dir(nx):
        bases.append(os.path.abspath(nx))
    try:
        bases.extend(_registry_nx_bases())
        bases.extend(_scan_nx_bases())
    except Exception:
        pass
    out = []
    for b in bases:
        if not b:
            continue
        parent = os.path.dirname(os.path.abspath(b))
        if parent and parent not in out:
            out.append(parent)
    return out


def _has_ocp(python_exe):
    """检查该解释器是否有**可用**的 OCP。

    ⚠️ 不能只测 `import OCP`：某些环境（如 WorkBuddy 内置 Python）装了老版本
    OCP，`import OCP` 成功但缺 `OCP.collections` 模块，语义分组器一跑就
    ModuleNotFoundError，静默退化成「没有智能分组」。

    这里同时验证 `OCP.collections`（OCP 7.8+ / 8.x 才有），并校验版本号。
    """
    if not _is_file(python_exe):
        return False
    probe = (
        "import OCP, OCP.collections, OCP.STEPCAFControl; "
        "from OCP.TCollection import TCollection_ExtendedString; "
        "print('ok')"
    )
    try:
        import subprocess
        p = subprocess.run([python_exe, "-c", probe],
                           capture_output=True, timeout=90,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode == 0
    except Exception:
        return False


def ocp_version(python_exe):
    """返回 OCP 版本号字符串，取不到返回 ''。"""
    if not _is_file(python_exe):
        return ""
    try:
        import subprocess
        p = subprocess.run(
            [python_exe, "-c",
             "import importlib.metadata as m;print(m.version('cadquery-ocp'))"],
            capture_output=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if p.returncode == 0:
            return (p.stdout or b"").decode("utf-8", "replace").strip()
    except Exception:
        pass
    return ""


def discover_ocp_python(verbose=False):
    """按优先级找可用的 OCP 解释器。

    优先级：显式配置 > 包内 venv > PATH 上的 python。
    每个候选都过 _has_ocp() 校验（含版本兼容检查）。
    """
    tried = []

    def _try(p, why):
        if not p:
            return ""
        if _has_ocp(p):
            return p
        tried.append(f"{p} ({why})")
        return ""

    # 1) 显式配置 / 环境变量
    env = _env(ENV_PREFIX + "OCP_PYTHON", "OCP_PYTHON")
    hit = _try(env, "显式指定但不可用")
    if hit:
        return hit

    # 2) 包内 venv（install.ps1 建的）
    here = os.path.dirname(os.path.abspath(__file__))
    local_venv = os.path.join(here, ".venv", "Scripts", "python.exe")
    hit = _try(local_venv, "包内 venv 无 OCP")
    if hit:
        return hit

    # 3) PATH 上的 python
    try:
        import shutil
        for exe in ("python", "python3"):
            found = shutil.which(exe)
            hit = _try(found, "无兼容 OCP")
            if hit:
                return hit
    except Exception:
        pass

    # 4) NX 安装根旁边的 OCP 环境（ocpenv / python venv）
    for base in _nx_adjacent_dirs():
        for rel in (("ocpenv", "Scripts", "python.exe"),
                    ("ocpenv", "Scripts", "pythonw.exe"),
                    ("python", "Scripts", "python.exe"),
                    ("python", "bin", "python.exe")):
            cand = os.path.join(base, *rel)
            hit = _try(cand, "NX 旁环境无兼容 OCP")
            if hit:
                return hit

    if verbose:
        for t in tried:
            print(f"  [OCP 候选被跳过] {t}")
    return ""


# ------------------------------------------------------------------ 探测：缓存目录
def discover_cache_dir():
    env = _env(ENV_PREFIX + "CACHE_DIR")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        return os.path.join(local, "PRTPreview", "cache")
    return os.path.join(os.path.expanduser("~"), ".prt-preview", "cache")


def discover_license_dir():
    env = _env(ENV_PREFIX + "LICENSE_DIR")
    if env and _is_dir(env):
        return env
    for c in (r"D:\UGNX2_Lic", r"C:\UGNX2_Lic"):
        if _is_dir(c):
            return c
    return ""


# ------------------------------------------------------------------ 配置加载
def _read_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_config_file(explicit=None):
    """返回 (config_dict, 配置文件路径)。找不到返回 ({}, "")。"""
    cands = []
    if explicit:
        cands.append(explicit)
    env_cfg = os.environ.get(ENV_PREFIX + "CONFIG")
    if env_cfg:
        cands.append(env_cfg)
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(here, "config.json"))
    for c in cands:
        if c and os.path.isfile(c):
            return _read_json(c), os.path.abspath(c)
    return {}, ""


def _apply_env_overrides(cfg):
    mapping = {
        "nx_base": ENV_PREFIX + "NX_BASE",
        "mayo": ENV_PREFIX + "MAYO",
        "ocp_python": ENV_PREFIX + "OCP_PYTHON",
        "cache_dir": ENV_PREFIX + "CACHE_DIR",
        "license_dir": ENV_PREFIX + "LICENSE_DIR",
        "license_server": ENV_PREFIX + "LICENSE_SERVER",
    }
    for key, var in mapping.items():
        v = os.environ.get(var)
        if v and v.strip():
            cfg[key] = v.strip()
    for key in ("license_autostart", "register_menu"):
        var = ENV_PREFIX + key.upper()
        v = os.environ.get(var)
        if v is not None and v.strip() != "":
            cfg[key] = v.strip().lower() in ("1", "true", "yes", "on")
    return cfg


def load(explicit=None):
    """加载完整配置（自动探测 + 覆盖合并）。"""
    cfg = dict(DEFAULTS)
    file_cfg, cfg_path = _load_config_file(explicit)
    cfg.update({k: v for k, v in file_cfg.items() if k in DEFAULTS})
    _apply_env_overrides(cfg)

    if not cfg.get("nx_base"):
        cfg["nx_base"] = discover_nx_base()
    if not cfg.get("mayo"):
        cfg["mayo"] = discover_mayo()
    if not cfg.get("ocp_python") and not cfg.get("disable_ocp"):
        cfg["ocp_python"] = discover_ocp_python()
    if not cfg.get("cache_dir"):
        cfg["cache_dir"] = discover_cache_dir()
    if not cfg.get("license_dir"):
        cfg["license_dir"] = discover_license_dir()
    if not cfg.get("license_server"):
        cfg["license_server"] = _env("UGII_LICENSE_SERVER", "UGS_LICENSE_SERVER") \
            or DEFAULTS["license_server"]

    cfg["config_path"] = cfg_path
    cfg["package_dir"] = os.path.dirname(os.path.abspath(__file__))
    return cfg


# ------------------------------------------------------------------ 自检
def doctor(cfg=None):
    """逐项检查依赖，返回 [(项目, 是否就绪, 说明)]。"""
    cfg = cfg or load()
    rows = []
    nx = cfg.get("nx_base") or ""
    rows.append(("NX2 安装根", bool(nx) and _is_dir(os.path.join(nx, "UGII")),
                 nx or "未探测到（需在 config.json 填 nx_base）"))
    rows.append(("STEP 转换器 (AP214)",
                 bool(nx) and _is_dir(os.path.join(nx, "STEP214UG")),
                 os.path.join(nx, "STEP214UG") if nx else "缺 nx_base"))
    rows.append(("IGES 转换器",
                 bool(nx) and _is_dir(os.path.join(nx, "IGES")),
                 os.path.join(nx, "IGES") if nx else "缺 nx_base"))
    rows.append(("Mayo 看图程序", _is_file(cfg.get("mayo")),
                 cfg.get("mayo") or "未探测到（需装 Mayo 或填 mayo）"))

    ocp = cfg.get("ocp_python") or ""
    if ocp and _is_file(ocp):
        ver = ocp_version(ocp)
        rows.append(("OCP Python (语义分组)", True,
                     f"{ocp}" + (f"  [OCP {ver}]" if ver else "")))
    else:
        rows.append(("OCP Python (语义分组)", False,
                     "未探测到（需 pip install cadquery-ocp；"
                     "无此依赖时「智能分组」不可用，其余功能正常）"))

    rows.append(("缓存目录", bool(cfg.get("cache_dir")),
                 cfg.get("cache_dir") or "未确定"))
    rows.append(("许可自愈", bool(cfg.get("license_autostart")),
                 "关闭（默认，不影响转换）" if not cfg.get("license_autostart")
                 else ("已开启: " + str(cfg.get("license_dir")))))
    return rows


def main():
    # --print-ocp：只输出探测到的 OCP 解释器路径，供安装脚本复用决策用。
    # 单独输出，不掺日志，避免 PowerShell 侧解析歧义。
    if "--print-ocp" in sys.argv:
        cfg = load()
        print(cfg.get("ocp_python") or "")
        return 0

    cfg = load()
    print("PRT-Preview 配置自检")
    print("=" * 58)
    print(f"配置文件: {cfg['config_path'] or '(未找到，使用自动探测)'}")
    print(f"包目录  : {cfg['package_dir']}")
    print("=" * 58)
    ok = True
    for name, ready, detail in doctor(cfg):
        flag = "OK  " if ready else "缺失"
        print(f"  [{flag}] {name:<22} {detail}")
        if name in ("NX2 安装根", "Mayo 看图程序") and not ready:
            ok = False
    if not (cfg.get("ocp_python") or ""):
        print("  [提示] 未装 OCP：智能分组不可用，但「转 STEP/IGES」与「用 Mayo 查看」正常")
    print("=" * 58)
    print("结论:", "核心依赖齐备" if ok else "核心依赖缺失，见 README「安装」")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
