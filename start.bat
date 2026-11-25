@echo off
rem 手动启动查看器（无控制台窗口）。pythonw 路径可用环境变量 TBH_PYTHON 覆盖。
setlocal
set "ROOT=%~dp0"
if defined TBH_PYTHON (
    set "PY=%TBH_PYTHON%"
) else (
    set "PY=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
    if not exist "%PY%" set "PY=pythonw.exe"
)
start "" "%PY%" -X utf8 "%ROOT%stash_viewer.py"
endlocal
