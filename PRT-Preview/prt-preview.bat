@echo off
REM PRT-Preview 拖拽查看器 —— 把 .prt 文件拖到本文件上即可
REM 无控制台窗口（走 pythonw.exe）

setlocal
set "HERE=%~dp0"

REM 优先用包内 venv 的 pythonw
set "PYW=%HERE%.venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
    for %%I in (pythonw.exe) do set "PYW=%%~$PATH:I"
)
if not exist "%PYW%" (
    if exist "%SystemRoot%\pyw.exe" set "PYW=%SystemRoot%\pyw.exe"
)
if not exist "%PYW%" (
    echo 未找到 pythonw.exe，请先运行 install.ps1
    pause
    exit /b 1
)

if "%~1"=="" (
    echo 请把 .prt 文件拖到本文件上。
    pause
    exit /b 1
)

"%PYW%" "%HERE%prt2mayo.py" %*
exit /b %ERRORLEVEL%
