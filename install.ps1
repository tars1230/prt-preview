<#
.SYNOPSIS
    PRT-Preview 一键安装（Windows / PowerShell 5.1+）。

.DESCRIPTION
    1. 复制程序到安装目录（默认 %LOCALAPPDATA%\PRT-Preview）
    2. 建立 Python 虚拟环境并安装 cadquery-ocp（语义分组依赖）
    3. 写 config.json
    4. 注册 .prt 右键菜单
    5. 跑一次自检

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1
    powershell -ExecutionPolicy Bypass -File install.ps1 -InstallDir "D:\Tools\PRT-Preview" -NxBase "D:\UGS\NX2.0"
    powershell -ExecutionPolicy Bypass -File install.ps1 -SkipOcp      # 不装 OCP（无智能分组）
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$NxBase = "",
    [string]$Mayo = "",
    [string]$LicenseServer = "",
    [switch]$SkipOcp,
    [switch]$SkipMenu
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Src = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $InstallDir) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "PRT-Preview"
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  PRT-Preview 安装程序" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "源目录  : $Src"
Write-Host "安装到  : $InstallDir"
Write-Host ""

# ---------------------------------------------------------------- 1. 找 Python
function Find-Python {
    $cands = @()
    foreach ($n in @("python", "python3")) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) { $cands += $c.Source }
    }
    $py = Get-Command "py" -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $v = & $py.Source -3 -c "import sys;print(sys.executable)" 2>$null
            if ($v) { $cands += $v.Trim() }
        } catch { }
    }
    foreach ($p in $cands) {
        if (Test-Path $p) {
            try {
                $ver = & $p -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
                if ($ver -and [version]($ver.Trim()) -ge [version]"3.9") {
                    return @{ Exe = $p; Ver = $ver.Trim() }
                }
            } catch { }
        }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host "[X] 未找到 Python 3.9+。" -ForegroundColor Red
    Write-Host "    请先安装 Python：https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "    安装时务必勾选 'Add python.exe to PATH'。" -ForegroundColor Yellow
    exit 1
}
Write-Host "[1/5] Python: $($py.Exe) (Python $($py.Ver))" -ForegroundColor Green

# ---------------------------------------------------------------- 2. 复制文件
Write-Host "[2/5] 复制程序文件..." -ForegroundColor Green
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$files = @(
    "prt2mayo.py", "prt_config.py", "semantic_group.py", "group_step.py",
    "reg_menu.py", "gen_test_part.py", "config.example.json",
    "README.md", "LICENSE", "requirements.txt",
    "uninstall.ps1", "prt-preview.bat"
)
# 注意：不复制 install.ps1 自身 —— 正在被执行的脚本无法覆盖自己，
# 在 $ErrorActionPreference='Stop' 下会静默中断整个复制循环。
$copied = 0
foreach ($f in $files) {
    # ⚠️ 变量名不能叫 $src —— PowerShell 变量名不区分大小写，
    # 会覆盖外层的 $Src（源码目录），导致第二轮起路径全错、只复制 1 个文件。
    $srcFile = Join-Path $Src $f
    if (-not (Test-Path $srcFile)) { continue }
    try {
        Copy-Item $srcFile (Join-Path $InstallDir $f) -Force
        $copied++
    } catch {
        Write-Host "      [!] 复制失败 $f : $($_.Exception.Message)" -ForegroundColor Yellow
    }
}
# 测试脚本单独放 tests/
if (Test-Path (Join-Path $Src "tests")) {
    try {
        Copy-Item (Join-Path $Src "tests") $InstallDir -Recurse -Force
        $copied++
    } catch { Write-Host "      [!] 复制 tests 失败" -ForegroundColor Yellow }
}
if (Test-Path (Join-Path $Src "docs")) {
    try {
        Copy-Item (Join-Path $Src "docs") $InstallDir -Recurse -Force
        $copied++
    } catch { Write-Host "      [!] 复制 docs 失败" -ForegroundColor Yellow }
}
Write-Host "      已复制 $copied 项到 $InstallDir" -ForegroundColor Gray

# ---------------------------------------------------------------- 3. 虚拟环境 + OCP
$VenvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not $SkipOcp) {
    Write-Host "[3/5] 建立虚拟环境并安装 cadquery-ocp（较大，约 200MB）..." -ForegroundColor Green
    if (-not (Test-Path $VenvPy)) {
        & $py.Exe -m venv (Join-Path $InstallDir ".venv")
    }
    $pipArgs = @("-m", "pip", "install", "--upgrade", "pip")
    & $VenvPy @pipArgs
    # 走代理（国内直连 PyPI 慢）
    $proxy = $env:HTTP_PROXY
    if (-not $proxy) { $proxy = $env:HTTPS_PROXY }
    if ($proxy) {
        Write-Host "      使用代理: $proxy" -ForegroundColor Gray
        & $VenvPy -m pip install --proxy $proxy "cadquery-ocp>=8.0,<9.0" numpy
    } else {
        & $VenvPy -m pip install "cadquery-ocp>=8.0,<9.0" numpy
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[!] cadquery-ocp 安装失败。" -ForegroundColor Yellow
        Write-Host "    可稍后手动执行：" -ForegroundColor Yellow
        Write-Host "    $VenvPy -m pip install cadquery-ocp numpy" -ForegroundColor Yellow
        Write-Host "    （未装 OCP 时，智能分组不可用，其余功能正常）" -ForegroundColor Yellow
    } else {
        # 验证 OCP 是否**真的可用**（不只 import OCP，还要 OCP.collections）
        & $VenvPy -c "import OCP, OCP.collections; print('OCP OK')"
        if ($LASTEXITCODE -eq 0) {
            Write-Host "      OCP 安装完成且可用" -ForegroundColor Green
        } else {
            Write-Host "[!] OCP 已安装但不可用（缺 OCP.collections，版本过旧）。" -ForegroundColor Yellow
            Write-Host "    升级： $VenvPy -m pip install --upgrade cadquery-ocp" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "[3/5] 跳过 OCP 安装（-SkipOcp）" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 4. 配置
Write-Host "[4/5] 生成 config.json..." -ForegroundColor Green
$cfgPath = Join-Path $InstallDir "config.json"
$cfg = [ordered]@{
    nx_base            = $NxBase
    mayo               = $Mayo
    ocp_python         = $(if ($SkipOcp) { "" } else { $VenvPy })
    cache_dir          = ""
    license_server     = $(if ($LicenseServer) { $LicenseServer } else { "27000@localhost" })
    license_dir        = ""
    license_autostart  = $false
    register_menu      = $(-not $SkipMenu)
}
$cfg | ConvertTo-Json -Depth 5 | Set-Content -Path $cfgPath -Encoding UTF8
Write-Host "      $cfgPath" -ForegroundColor Gray

# ---------------------------------------------------------------- 5. 右键菜单
if (-not $SkipMenu) {
    Write-Host "[5/5] 注册 .prt 右键菜单..." -ForegroundColor Green
    $regPy = $VenvPy
    if (-not (Test-Path $regPy)) { $regPy = $py.Exe }
    # 右键菜单要用 pythonw（无控制台窗口）
    $regPyw = Join-Path (Split-Path -Parent $regPy) "pythonw.exe"
    if (-not (Test-Path $regPyw)) {
        $regPyw = Join-Path (Split-Path -Parent $py.Exe) "pythonw.exe"
    }
    if (-not (Test-Path $regPyw)) { $regPyw = $py.Exe }
    Push-Location $InstallDir
    try {
        & $regPy (Join-Path $InstallDir "reg_menu.py") --python $regPyw
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[5/5] 跳过右键菜单注册（-SkipMenu）" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 自检
Write-Host ""
Write-Host "运行自检..." -ForegroundColor Cyan
$checkPy = $VenvPy
if (-not (Test-Path $checkPy)) { $checkPy = $py.Exe }
Push-Location $InstallDir
try {
    & $checkPy (Join-Path $InstallDir "prt_config.py")
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  安装完成" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host "安装目录 : $InstallDir"
Write-Host "配置文件 : $cfgPath"
Write-Host "查看器   : $(Join-Path $InstallDir 'prt-preview.bat')"
Write-Host ""
Write-Host "下一步：" -ForegroundColor Cyan
Write-Host "  1) 若自检显示缺 nx_base，编辑 config.json 填入 NX 安装根"
Write-Host "  2) 重启资源管理器（或按 F5）即可在 .prt 上右键看到菜单"
Write-Host "  3) 卸载：powershell -ExecutionPolicy Bypass -File `"$InstallDir\uninstall.ps1`""
Write-Host ""
