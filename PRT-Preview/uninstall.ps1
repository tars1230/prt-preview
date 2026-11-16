<#
.SYNOPSIS
    PRT-Preview 卸载。

.DESCRIPTION
    1. 注销 .prt 右键菜单
    2. 删除安装目录
    3. 可选清理缓存

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File uninstall.ps1
    powershell -ExecutionPolicy Bypass -File uninstall.ps1 -KeepCache
    powershell -ExecutionPolicy Bypass -File uninstall.ps1 -PurgeCache
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [switch]$KeepCache,
    [switch]$PurgeCache
)

$ErrorActionPreference = "Stop"

if (-not $InstallDir) {
    # 默认用「脚本自身所在目录」—— uninstall.ps1 就躺在安装目录里，
    # 这样无论装到哪都能正确定位；脚本不在安装目录时再退回默认路径。
    $selfDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    if ((Test-Path (Join-Path $selfDir "prt2mayo.py"))) {
        $InstallDir = $selfDir
    } else {
        $InstallDir = Join-Path $env:LOCALAPPDATA "PRT-Preview"
    }
}

Write-Host ""
Write-Host "PRT-Preview 卸载" -ForegroundColor Cyan
Write-Host "安装目录: $InstallDir"
Write-Host ""

# ---------------------------------------------------------------- 1. 注销菜单
$regPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path $regPy)) {
    $c = Get-Command python -ErrorAction SilentlyContinue
    if ($c) { $regPy = $c.Source }
}
$regScript = Join-Path $InstallDir "reg_menu.py"
if ((Test-Path $regPy) -and (Test-Path $regScript)) {
    Write-Host "[1/3] 注销右键菜单..." -ForegroundColor Green
    Push-Location $InstallDir
    try { & $regPy $regScript --uninstall } catch { }
    Pop-Location
} else {
    Write-Host "[1/3] 跳过（注册脚本已不存在）" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 2. 缓存
$cacheDir = Join-Path $env:LOCALAPPDATA "PRTPreview\cache"
if ($PurgeCache) {
    Write-Host "[2/3] 清理缓存: $cacheDir" -ForegroundColor Green
    if (Test-Path $cacheDir) {
        Remove-Item $cacheDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "      已删除" -ForegroundColor Gray
    }
} elseif ($KeepCache) {
    Write-Host "[2/3] 保留缓存: $cacheDir" -ForegroundColor Gray
} else {
    Write-Host "[2/3] 保留缓存: $cacheDir（加 -PurgeCache 可一并删除）" -ForegroundColor Gray
}

# ---------------------------------------------------------------- 3. 删除目录
if (Test-Path $InstallDir) {
    Write-Host "[3/3] 删除安装目录..." -ForegroundColor Green
    # 先确保没有进程占用
    Get-Process -Name "mayo", "python", "pythonw" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$InstallDir*" } |
        ForEach-Object { Write-Host "      结束占用进程: $($_.Name)"; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }

    # ⚠️ 正在被执行的 uninstall.ps1 会锁住自己，Remove-Item 必然失败。
    # 策略：先删掉除自身外的所有内容，把自身移到临时目录，
    # 再由一个独立进程在父脚本退出、锁释放后删掉那个空目录。
    Push-Location $env:TEMP
    try {
        Get-ChildItem $InstallDir -Force | Where-Object { $_.Name -ne "uninstall.ps1" } |
            ForEach-Object {
                Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
            }

        $selfName = "uninstall.ps1"
        $selfSrc = Join-Path $InstallDir $selfName
        $staged = Join-Path $env:TEMP ("prt_uninstall_{0}.ps1" -f [guid]::NewGuid().ToString("N"))
        if (Test-Path $selfSrc) {
            Move-Item $selfSrc $staged -Force -ErrorAction SilentlyContinue
        }

        # 独立进程：等父脚本退出（锁释放）后删空目录
        $deleter = "Start-Sleep -Seconds 3; Remove-Item -LiteralPath '$InstallDir' -Recurse -Force -ErrorAction SilentlyContinue; Remove-Item -LiteralPath '$staged' -Force -ErrorAction SilentlyContinue"
        Start-Process -FilePath "powershell.exe" `
            -ArgumentList "-NoProfile", "-WindowStyle", "Hidden", "-Command", $deleter `
            -WindowStyle Hidden
        Write-Host "      删除任务已派发（3 秒后执行）" -ForegroundColor Gray
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[3/3] 安装目录不存在，跳过" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "卸载完成。缓存如需彻底清除： Remove-Item `"$cacheDir`" -Recurse -Force" -ForegroundColor Green
Write-Host ""
