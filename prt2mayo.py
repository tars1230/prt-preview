"""prt2mayo.py —— UG NX .prt 与通用 CAD 格式转换 + Mayo 看图。稳定版 v4（可移植）

用法：
  拖放/查看         pyw.exe prt2mayo.py "<文件>.prt"              -> 转换(缓存) + 打开 Mayo
  智能分组查看       pyw.exe prt2mayo.py "<文件>.prt" --semantic    -> 语义装配树(外壳/内部/门板…)
  空间分组(旧)       pyw.exe prt2mayo.py "<文件>.prt" --grouped     -> 纯空间分块，fallback
  右键转 STEP       pyw.exe prt2mayo.py "<文件>.prt" --convert-only                    -> STEP(AP214) 放到 prt 旁边
  右键转 IGES       pyw.exe prt2mayo.py "<文件>.prt" --convert-only --to-iges          -> IGES 放到 prt 旁边
  干跑             pyw.exe prt2mayo.py "<文件>.prt" --dry        -> 只打印缓存路径
  自检             pyw.exe prt2mayo.py --doctor                   -> 打印依赖探测结果

稳定化设计：
  1. STEP 用 AP214 为主（保留颜色/属性）、AP203 兜底；IGES 用 igesexport
  2. 许可自愈：仅当 config.json 显式开启 license_autostart 才拉起 lmgrd
  3. 转换失败自动重启许可服务再试一次
  4. 并发保护：缓存目录下 .lock 文件（>10 分钟视为死锁自动接管）
  5. 日志超 1MB 自动截断
  6. 全程无控制台窗口；运行记录写 <缓存目录>/prt2mayo.log
  7. 所有路径来自 prt_config.py（配置 > 环境变量 > 自动探测），零本机硬编码
"""
import os, sys, json, hashlib, socket, subprocess, traceback, time

import prt_config

_CFG     = prt_config.load()
BASE     = _CFG["nx_base"]
UGII     = os.path.join(BASE, "UGII") if BASE else ""
LICDIR   = _CFG["license_dir"]
MAYO     = _CFG["mayo"]
CACHE    = _CFG["cache_dir"]
LOG      = os.path.join(CACHE, "prt2mayo.log")
LOCK     = os.path.join(CACHE, "prt2mayo.lock")
LOCK_STALE_SEC = 600
_LICENSE_AUTOSTART = bool(_CFG.get("license_autostart"))
_LICENSE_SERVER   = _CFG.get("license_server") or "27000@localhost"
# pyw.exe 无控制台；OCP 分组器是 python.exe，显式禁止它弹黑框
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 自动分组器（OCP venv；group_step.py / semantic_group.py 与本脚本同目录）
OCP_PY   = _CFG["ocp_python"]
_HERE    = os.path.dirname(os.path.abspath(__file__))
GROUPER  = os.path.join(_HERE, "group_step.py")       # 空间分组（旧，fallback）
SEMANTIC = os.path.join(_HERE, "semantic_group.py")   # 语义分组（默认）

# 格式注册表：每个格式一条或多条转换链（按顺序尝试）
# chain = (tag, 转换器目录, exe, def, 输出扩展名)
def _build_formats(base):
    """按 NX 安装根动态拼转换链。base 为空时返回空链（调用方给可读提示）。"""
    if not base:
        return {}
    return {
        "step": {
            "chains": [
                ("ap214", os.path.join(base, "STEP214UG"), "step214ug.exe", "ugstep214.def", ".stp"),
                ("ap203", os.path.join(base, "STEP203UG"), "step203ug.exe", "ugstep203.def", ".stp"),
            ],
            "env_dir": {"STEP214UG_DIR": 0, "STEP203UG_DIR": 1},  # 键 -> chains 下标
        },
        "iges": {
            "chains": [
                ("iges", os.path.join(base, "IGES"), "iges.exe", "igesexport.def", ".igs"),
            ],
            "env_dir": {"IGES_DIR": 0},
        },
    }


FORMATS = _build_formats(BASE)


def log(msg):
    os.makedirs(CACHE, exist_ok=True)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 1_000_000:
            with open(LOG, "r", encoding="utf-8", errors="replace") as f:
                f.seek(max(0, os.path.getsize(LOG) - 200_000))
                tail = f.read()
            with open(LOG, "w", encoding="utf-8") as f:
                f.write("[截断] ...以下为最近日志...\n" + tail)
    except Exception:
        pass
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def alert(msg):
    """pythonw 无控制台，出错时用系统弹窗告知（仅失败时用）。"""
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, msg, "PRT 转换", 0x10 | 0x40000)


# ---------------------------------------------------------------- 并发锁
class Lock:
    def __init__(self):
        self.acquired = False

    def __enter__(self):
        os.makedirs(CACHE, exist_ok=True)
        deadline = time.time() + 120
        while True:
            try:
                if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) > LOCK_STALE_SEC:
                    os.remove(LOCK)  # 死锁残留，接管
                fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.acquired = True
                return self
            except FileExistsError:
                if time.time() > deadline:
                    log("锁等待超时(120s)，强制接管")
                    try:
                        os.remove(LOCK)
                    except OSError:
                        pass
                    deadline = time.time() + 120  # 再给一轮
                time.sleep(1)

    def __exit__(self, *a):
        if self.acquired:
            try:
                os.remove(LOCK)
            except OSError:
                pass


# ---------------------------------------------------------------- 许可自愈
def _license_host_port():
    """从 license_server 解析 (host, port)，默认 127.0.0.1:27000。"""
    s = _LICENSE_SERVER or "27000@localhost"
    port, _, host = s.partition("@")
    return (host.strip() or "localhost", int(port.strip() or 27000))


def port_license_open(timeout=1.0):
    host, port = _license_host_port()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def uglmd_up():
    """lmstat 确认 vendor daemon UP（端口通不代表签出可用）。"""
    if not LICDIR:
        return port_license_open()
    lmutil = os.path.join(LICDIR, "lmutil.exe")
    if not os.path.isfile(lmutil):
        return port_license_open()
    try:
        p = subprocess.run([lmutil, "lmstat", "-c", _LICENSE_SERVER],
                           capture_output=True, timeout=20,
                           creationflags=_NO_WINDOW)
        out = (p.stdout or b"").decode("utf-8", errors="replace")
        return "uglmd: UP" in out
    except Exception:
        return False


def ensure_license():
    """许可不可用时拉起 lmgrd 并等待 uglmd UP。

    ⚠️ 默认关闭（license_autostart=false）。同事机器的许可拓扑未知，
    擅自拉起本地 lmgrd 可能与其正式许可服务器冲突，所以必须显式开启。
    """
    if uglmd_up():
        return True
    if not _LICENSE_AUTOSTART:
        log("许可服务未响应，且 license_autostart 未开启 —— 跳过自愈"
            "（如需自动拉起，请在 config.json 打开 license_autostart）")
        return False
    lmgrd = os.path.join(LICDIR, "lmgrd.exe")
    lic = os.path.join(LICDIR, "ugnx2.lic")
    if not (os.path.isfile(lmgrd) and os.path.isfile(lic)):
        log("自愈失败: 找不到 " + lmgrd + " 或 " + lic)
        return False
    log("许可不可用，静默拉起 lmgrd ...")
    DETACHED = 0x00000008
    # DETACHED_PROCESS 本身就不分配控制台（CREATE_NO_WINDOW 在此组合下会被
    # Windows 忽略）；保留 _NO_WINDOW 只为表达意图，并对旧系统边界情况兜底。
    subprocess.Popen(
        [lmgrd, "-c", lic, "-l", os.path.join(LICDIR, "auto.log")],
        cwd=LICDIR, creationflags=DETACHED | _NO_WINDOW, close_fds=True)
    deadline = time.time() + 60
    while time.time() < deadline:
        time.sleep(3)
        if uglmd_up():
            log("自愈成功: uglmd UP")
            return True
    log("自愈失败: 60s 内 uglmd 未 UP")
    return False


def restart_license():
    if not _LICENSE_AUTOSTART:
        return False
    subprocess.run(["taskkill", "/F", "/IM", "lmgrd.exe", "/T"], capture_output=True,
                   creationflags=_NO_WINDOW)
    subprocess.run(["taskkill", "/F", "/IM", "uglmd.exe", "/T"], capture_output=True,
                   creationflags=_NO_WINDOW)
    time.sleep(2)
    return ensure_license()


# ---------------------------------------------------------------- 转换
def cache_path(prt, tag, ext):
    key = hashlib.md5(os.path.abspath(prt).encode("utf-8")).hexdigest()[:8]
    stem = os.path.splitext(os.path.basename(prt))[0]
    return os.path.join(CACHE, f"{stem}_{key}_{tag}{ext}")


def env_for_convert(fmt, chain):
    tag, stepdir, exe, deffile, ext = chain
    env = os.environ.copy()
    env.update({
        "UGII_BASE_DIR": BASE,
        "UGII_ROOT_DIR": UGII + "\\",
        "UGII_LICENSE_FILE": _LICENSE_SERVER,
        "UGS_LICENSE_SERVER": _LICENSE_SERVER,
        "ROSE_DB": stepdir + "\\",   # 所有转换器统一：必须带尾部反斜杠
        "ROSE": stepdir + "\\",
        "UGII_DEX_JRE": os.path.join(BASE, "XLATORUI", "dex"),
        "UGII_DEX_JAR": os.path.join(BASE, "XLATORUI", "dex", "lib"),
    })
    # 各转换器自己的 <NAME>_DIR 变量（同样必须尾部反斜杠）
    for var, idx in FORMATS[fmt]["env_dir"].items():
        env[var] = FORMATS[fmt]["chains"][idx][1] + "\\"
    env["PATH"] = UGII + ";" + stepdir + ";" + env.get("PATH", "")
    return env


def sanitize_input(prt):
    """NX2 转换器是 2002 年 ANSI 程序，实测拒绝非 ASCII 路径（Invalid file name）。
    非 ASCII 时复制到 ASCII 临时路径再转（实测空格无害，无需处理）。"""
    p = os.path.abspath(prt)
    try:
        p.encode("ascii")
        return p
    except UnicodeEncodeError:
        pass
    tmpdir = os.path.join(CACHE, "tmp")
    os.makedirs(tmpdir, exist_ok=True)
    key = hashlib.md5(p.encode("utf-8")).hexdigest()[:8]
    tmp = os.path.join(tmpdir, f"in_{key}.prt")
    import shutil
    shutil.copy2(p, tmp)
    log(f"非 ASCII 路径，复制到临时路径转换: {tmp}")
    return tmp


def _run_chain(fmt, chain, prt, out):
    tag, stepdir, exe, deffile, ext = chain
    env = env_for_convert(fmt, chain)
    src = sanitize_input(prt)
    tmp_log = out + ".log"
    args = [os.path.join(stepdir, exe), src,
            "o=" + out, "l=" + tmp_log, "d=" + os.path.join(stepdir, deffile)]
    try:
        log(f"转换开始[{tag}]: {' '.join(args)}")
        p = subprocess.run(args, cwd=stepdir, env=env, capture_output=True, timeout=600,
                           creationflags=_NO_WINDOW)
    finally:
        # 清理临时副本（缓存 key 按原始路径算，下次需要会重新复制）
        if src != prt and os.path.exists(src):
            try:
                os.remove(src)
            except OSError:
                pass
    rc = p.returncode
    so = (p.stdout or b"").decode("utf-8", errors="replace")
    se = (p.stderr or b"").decode("utf-8", errors="replace")
    log(f"转换器[{tag}] rc={rc}")
    if so.strip():
        log(f"stdout[{tag}] 尾部: " + so[-500:].replace("\n", " | "))
    if se.strip():
        log(f"stderr[{tag}]: " + se[-500:].replace("\n", " | "))
    if not os.path.exists(out) and os.path.exists(os.path.splitext(out)[0] + ".step"):
        os.replace(os.path.splitext(out)[0] + ".step", out)
    ok = rc == 0 and os.path.exists(out) and os.path.getsize(out) > 1024
    if ok:
        log(f"转换完成[{tag}] {os.path.getsize(out)} bytes -> {out}")
    return ok


def get_output_for(prt, fmt):
    """返回指定格式的成品路径（缓存优先）。失败返回 None。"""
    if fmt not in FORMATS or not FORMATS[fmt]["chains"]:
        log(f"没有可用的 {fmt.upper()} 转换链（nx_base 未配置？）")
        return None
    chains = FORMATS[fmt]["chains"]
    first = chains[0]
    out = cache_path(prt, first[0], first[4])
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(prt):
        log("缓存命中: " + out)
        return out
    ensure_license()  # 失败也不拦，给转换器一次机会
    for attempt in (1, 2):
        if attempt == 2:
            if not restart_license():
                break
        for chain in chains:
            out = cache_path(prt, chain[0], chain[4])
            try:
                if _run_chain(fmt, chain, prt, out):
                    return out
            except subprocess.TimeoutExpired:
                log(f"转换超时(600s) [{chain[0]}]: {prt}")
            except Exception:
                log(f"转换异常 [{chain[0]}]:\n" + traceback.format_exc())
        log(f"第 {attempt} 次尝试[{fmt}]全部链失败: {prt}")
    return None


def deliver(prt, cache_file):
    """把成品复制到 prt 同目录（若目标不存在或比 prt 旧）。返回目标路径或 None。"""
    stem = os.path.splitext(os.path.basename(prt))[0]
    ext = os.path.splitext(cache_file)[1]
    target = os.path.join(os.path.dirname(os.path.abspath(prt)), stem + ext)
    try:
        if os.path.exists(target) and os.path.getmtime(target) > os.path.getmtime(prt):
            log("目标已存在且比 prt 新，不覆盖: " + target)
            return target
        import shutil
        shutil.copy2(cache_file, target)
        log("已交付: " + target)
        return target
    except Exception:
        log("交付失败(目标目录?): " + target + "\n" + traceback.format_exc())
        return None


def _fresh(out, *deps):
    """缓存是否有效：必须比 STEP 源文件和分组器脚本本身都新。

    ⚠️ 关键：分组规则一改，旧缓存必须自动失效重建，否则用户永远看到旧树，
    还以为「改了没生效」。deps 里必须带上分组器 .py 的 mtime。
    """
    if not (os.path.isfile(out) and os.path.getsize(out) > 1024):
        return False
    om = os.path.getmtime(out)
    for d in deps:
        if os.path.isfile(d) and om < os.path.getmtime(d):
            return False
    return True


def ensure_grouped(step_path):
    """把扁平 STEP 按空间递归剖分为多级装配（Mayo 树可整体隐藏组件）。
    输出 {stem}_{key}_grp.stp；OCP 慢 (~1-2 分钟)，走缓存。"""
    key = hashlib.md5(step_path.lower().encode("utf-8")).hexdigest()[:8]
    stem = os.path.splitext(os.path.basename(step_path))[0]
    out = os.path.join(CACHE, f"{stem}_{key}_grp.stp")
    if _fresh(out, step_path, GROUPER):
        log("分组缓存命中: " + out)
        return out
    if not os.path.isfile(OCP_PY):
        log("找不到 OCP python: " + OCP_PY)
        return None
    log(f"自动分组开始: {step_path} -> {out}")
    try:
        p = subprocess.run([OCP_PY, GROUPER, step_path, out],
                           capture_output=True, timeout=1800,
                           creationflags=_NO_WINDOW)
        so = (p.stdout or b"").decode("utf-8", errors="replace")
        log("分组器: " + so[-300:].replace("\n", " | "))
        ok = os.path.exists(out) and os.path.getsize(out) > 1024
        log(f"分组完成 ok={ok}")
        return out if ok else None
    except Exception:
        log("分组异常:\n" + traceback.format_exc())
        return None


def ensure_semantic(step_path):
    """语义分组：先认出产品类型，再按 外壳/门板/层板/框架/标准件 建装配树。
    输出 {stem}_{key}_sem.stp + 同名 .semantic_map.json。
    map 里的 overrides 可人工改分组，改完自动重建（不会被重跑冲掉）。
    OCP 约 70s，走缓存；失败返回 None 由调用方回退空间分组。"""
    key = hashlib.md5(step_path.lower().encode("utf-8")).hexdigest()[:8]
    stem = os.path.splitext(os.path.basename(step_path))[0]
    out = os.path.join(CACHE, f"{stem}_{key}_sem.stp")
    mapf = os.path.splitext(out)[0] + ".semantic_map.json"
    # 人工标注用「内容签名」判定是否重建，不靠 map 文件 mtime：
    # 重跑时 map 会被重写，mtime 永远比 out 新，用 mtime 会每次都白跑 70s；
    # 且分组失败时 out 不更新，用 mtime 会陷入无限重试。
    stamp = out + ".ovstamp"
    # 语义分组器直接写目标文件时，进程被杀/磁盘满会留下半个 STEP，
    # 下次被误当成缓存。改用同目录临时文件，成功后原子替换，旧缓存不会被破坏。
    tmp_out = out + f".partial_{os.getpid()}.stp"
    tmp_map = os.path.splitext(tmp_out)[0] + ".semantic_map.json"
    for stale in (tmp_out, tmp_map):
        try:
            if os.path.exists(stale):
                os.remove(stale)
        except OSError:
            pass
    args = [OCP_PY, SEMANTIC, step_path, tmp_out]
    ov_sig = ""
    if os.path.isfile(mapf):
        try:
            with open(mapf, encoding="utf-8") as f:
                ov = json.load(f).get("overrides") or {}
            if ov:
                ov_sig = hashlib.md5(json.dumps(
                    ov, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        except Exception:
            pass
    old_sig = ""
    if os.path.isfile(stamp):
        try:
            with open(stamp, encoding="utf-8") as f:
                old_sig = f.read().strip()
        except Exception:
            pass
    # 签名变化（含清空标注）→ 重建；曾有标注但成品不存在（如上次失败）→ 带 --map 重试。
    # 只比 ov_sig != old_sig 有两个坑：失败重试会丢 --map（标注被静默忽略）、
    # 用户清空 overrides 后旧树还带着已撤销的标注。
    force = (ov_sig != old_sig) or (bool(ov_sig) and not os.path.isfile(out))
    if force and ov_sig:
        args += ["--map", mapf]
        log(f"应用人工标注重建语义树 (sig={ov_sig[:8]})")
    elif force and not ov_sig:
        log("人工标注已清空，重建无标注语义树")

    if not force and _fresh(out, step_path, SEMANTIC):
        log("语义分组缓存命中: " + out)
        return out
    if not os.path.isfile(OCP_PY):
        log("找不到 OCP python: " + OCP_PY)
        return None
    log(f"语义分组开始: {step_path} -> {out}")
    try:
        p = subprocess.run(args, capture_output=True, timeout=1800,
                           creationflags=_NO_WINDOW)
        so = (p.stdout or b"").decode("utf-8", errors="replace")
        se = (p.stderr or b"").decode("utf-8", errors="replace")
        log("语义分组器: " + so[-500:].replace("\n", " | "))
        if se.strip():
            log("语义分组器 stderr: " + se[-300:].replace("\n", " | "))
        ok = (p.returncode == 0 and os.path.exists(tmp_out)
              and os.path.getsize(tmp_out) > 1024)
        if ok:
            try:
                # map 文件与临时 STEP 一起提交；旧成品保持可用直到这一刻。
                if os.path.exists(tmp_map):
                    os.replace(tmp_map, mapf)
                os.replace(tmp_out, out)
                # 成功/失败、有标注/清空标注都写 stamp（空签名也写）：
                # 否则清空标注后 old_sig 永远非空，每次打开都误判「签名又变了」白跑一次重建
                with open(stamp, "w", encoding="utf-8") as f:
                    f.write(ov_sig)
            except OSError:
                ok = False
        if not ok:
            for stale in (tmp_out, tmp_map):
                try:
                    if os.path.exists(stale):
                        os.remove(stale)
                except OSError:
                    pass
        log(f"语义分组完成 ok={ok} rc={p.returncode}")
        return out if ok else None
    except Exception:
        for stale in (tmp_out, tmp_map):
            try:
                if os.path.exists(stale):
                    os.remove(stale)
            except OSError:
                pass
        log("语义分组异常:\n" + traceback.format_exc())
        return None


# ---------------------------------------------------------------- 主流程
def main():
    argv = sys.argv[1:]
    if any(a.lower() in ("/doctor", "--doctor") for a in argv):
        return prt_config.main()

    dry = any(a.lower() in ("/dry", "--dry") for a in argv)
    convert_only = any(a.lower() in ("/convert-only", "--convert-only") for a in argv)
    to_iges = any(a.lower() in ("/to-iges", "--to-iges") for a in argv)
    grouped = any(a.lower() in ("/grouped", "--grouped") for a in argv)
    want_semantic = any(a.lower() in ("/semantic", "--semantic") for a in argv)
    fmt = "iges" if to_iges else "step"
    semantic = want_semantic and fmt == "step"   # 语义分组只吃 STEP
    if want_semantic and not semantic:
        log("IGES 输出，跳过语义分组（语义分组仅支持 STEP）")
    prts = [a for a in argv if a.lower().endswith(".prt")]

    try:
        os.makedirs(CACHE, exist_ok=True)
    except OSError as e:
        alert("缓存目录不可写：\n" + CACHE + "\n\n" + str(e))
        return 4

    if dry:
        for p in prts:
            print(f"prt = {p}")
            for tag, _, _, _, ext in FORMATS.get(fmt, {}).get("chains", []):
                c = cache_path(p, tag, ext)
                print(f"  cache[{tag}] = {c}  exists={os.path.isfile(c)}"
                      + (f"  size={os.path.getsize(c)}" if os.path.isfile(c) else ""))
        return 0

    if not prts:
        alert("把 .prt 文件拖到本快捷方式上即可。\n\n"
              "首次转换约需 1 分钟，之后走缓存秒开。")
        return 1

    if not FORMATS.get(fmt, {}).get("chains"):
        alert("未找到 NX 转换器。\n\n"
              "请在 config.json 填写 nx_base（NX 安装根，需含 UGII 子目录），"
              "或设置环境变量 PRT_PREVIEW_NX_BASE。\n\n"
              "运行 prt2mayo.py --doctor 可查看探测结果。")
        return 5

    rc_all = 0
    with Lock():
        for prt in prts:
            if not os.path.isfile(prt):
                log("文件不存在: " + prt)
                alert("文件不存在：\n" + prt)
                rc_all = 1
                continue
            out = get_output_for(prt, fmt)
            if out is None:
                alert(f"转换失败({fmt.upper()})：\n{prt}\n\n详情见 {LOG}")
                rc_all = 2
                continue
            if convert_only and (want_semantic or grouped):
                log("静默转换忽略分组参数，交付原始扁平文件（CAM 兼容）")
            if semantic and not convert_only:
                sem = ensure_semantic(out)
                if sem is not None:
                    out = sem
                else:
                    log("语义分组失败，回退空间分组")
                    grp = ensure_grouped(out)
                    if grp is not None:
                        out = grp
                    else:
                        log("空间分组也失败，回退扁平文件")
            elif grouped and not convert_only:
                grp = ensure_grouped(out)
                if grp is not None:
                    out = grp
                else:
                    log("分组失败，回退扁平文件")
            if convert_only:
                deliver(prt, out)
                log(f"静默转换完成[{fmt}]: {out}")
            else:
                if not os.path.isfile(MAYO):
                    log("找不到 Mayo: " + str(MAYO))
                    alert("找不到 Mayo 看图程序。\n\n"
                          "请安装 Mayo，或在 config.json 填写 mayo 路径。\n\n"
                          "你也可以先用「转为 STEP」把文件转出来再自行打开。")
                    rc_all = 3
                    continue
                subprocess.Popen([MAYO, out])
                log("已启动 Mayo: " + out)
    return rc_all


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("异常:\n" + traceback.format_exc())
        alert("运行出错，详情见 " + LOG)
        sys.exit(3)
