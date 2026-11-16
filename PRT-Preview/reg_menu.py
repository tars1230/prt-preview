# -*- coding: utf-8 -*-
"""reg_menu.py —— 注册 / 注销 .prt 右键菜单（用户级 HKCU，不动 ugs_router 关联）。

用法：
    python reg_menu.py            注册
    python reg_menu.py --uninstall 注销
    python reg_menu.py --show      查看当前注册状态

菜单项：
   - 转为 STEP (AP214)     静默转换，输出到 prt 同目录（CAM 可直接用）
   - 转为 IGES              静默转换，老 CAM 兼容
   - 用 Mayo 查看            原始扁平模型
   - 智能分组查看            语义装配树：外壳 / 门板 / 层板 / 框架 / 标准件

⚠️ 只写 HKCU\\Software\\Classes\\SystemFileAssociations\\.prt\\shell，
   不改 .prt 的默认打开方式，不动 UG 自己的路由（ugs_router）。
"""
import os
import sys
import winreg

import prt_config

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = r"Software\Classes\SystemFileAssociations\.prt\shell"
SCRIPT = os.path.join(HERE, "prt2mayo.py")


def _pythonw():
    """挑一个能用的 pythonw.exe（无控制台窗口）。

    优先级：
      1. 包内 venv 的 pythonw（install.ps1 建的，最可靠）
      2. 当前解释器同目录的 pythonw（用 python 装的那套）
      3. PATH 上的 pythonw
      4. 兜底 sys.executable

    ⚠️ 不能只看 PATH —— 机器上可能存在多个 Python（IDE 内置的、
    长期不用已损坏的、版本过旧的），挑错会导致右键菜单点了没反应。
    """
    import shutil
    here = os.path.dirname(os.path.abspath(__file__))

    cands = [
        os.path.join(here, ".venv", "Scripts", "pythonw.exe"),
        os.path.join(os.path.dirname(sys.executable), "pythonw.exe"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    for name in ("pythonw", "python"):
        p = shutil.which(name)
        if p:
            return p
    return sys.executable


def _mayo_icon(cfg):
    m = cfg.get("mayo") or ""
    return (m + ",0") if m else ""


def build_verbs(cfg, python_exe=None):
    pyw = python_exe or _pythonw()
    icon = _mayo_icon(cfg)
    return {
        "Prt2Step": {
            "label": "转为 STEP (AP214)",
            "icon": icon,
            "cmd": f'"{pyw}" "{SCRIPT}" "%1" --convert-only',
        },
        "Prt2Iges": {
            "label": "转为 IGES (老CAM兼容)",
            "icon": icon,
            "cmd": f'"{pyw}" "{SCRIPT}" "%1" --convert-only --to-iges',
        },
        "PrtMayoView": {
            "label": "用 Mayo 查看",
            "icon": icon,
            "cmd": f'"{pyw}" "{SCRIPT}" "%1"',
        },
        "PrtGroupedView": {
            "label": "智能分组查看 (外壳/内部一键隐藏)",
            "icon": icon,
            "cmd": f'"{pyw}" "{SCRIPT}" "%1" --semantic',
        },
    }


def register(verbs):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as root:
        for name, v in verbs.items():
            k = winreg.CreateKey(root, name)
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, v["label"])  # 默认值=菜单文字
            if v["icon"]:
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, v["icon"])
            with winreg.CreateKey(k, "command") as c:
                winreg.SetValueEx(c, None, 0, winreg.REG_SZ, v["cmd"])
            print("registered:", name, "->", v["cmd"])


def unregister(verbs):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_ALL_ACCESS) as root:
            for name in verbs:
                try:
                    winreg.DeleteKey(root, name + r"\command")
                except OSError:
                    pass
                try:
                    winreg.DeleteKey(root, name)
                    print("removed:", name)
                except OSError:
                    print("not found:", name)
    except FileNotFoundError:
        print("nothing to remove")


def show():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i); i += 1
                except OSError:
                    break
                with winreg.OpenKey(root, sub + r"\command") as c:
                    label = ""
                    with winreg.OpenKey(root, sub) as sk:
                        label = winreg.QueryValueEx(sk, None)[0]
                    print(f"  {label}\n     {winreg.QueryValueEx(c, None)[0]}")
    except FileNotFoundError:
        print("  (未注册)")


def main():
    cfg = prt_config.load()
    args = sys.argv[1:]
    mode = args[0].lower() if args else ""

    # --python <path>：由 install.ps1 指定确定可用的解释器
    python_exe = None
    if "--python" in args:
        i = args.index("--python")
        if i + 1 < len(args):
            python_exe = args[i + 1]

    verbs = build_verbs(cfg, python_exe)

    if mode in ("--uninstall", "/uninstall", "-u"):
        unregister(verbs)
        return 0
    if mode in ("--show", "/show"):
        show()
        return 0

    print("PRT-Preview 右键菜单注册")
    print("=" * 58)
    print("脚本  :", SCRIPT)
    print("解释器:", python_exe or _pythonw())
    print("Mayo  :", cfg.get("mayo") or "(未找到 —— 仅转 STEP/IGES 可用)")
    print("=" * 58)
    register(verbs)
    print("=" * 58)
    show()
    print("提示：资源管理器需重启或按 F5 才能看到新菜单。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
