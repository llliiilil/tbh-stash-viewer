@echo off
rem Troubleshooting launcher: runs with a console so exceptions are visible.
rem Use this first when clicking start.bat seems to do nothing.
setlocal
cd /d "%~dp0"

set "PY="
if defined TBH_PYTHON if exist "%TBH_PYTHON%" set "PY=%TBH_PYTHON%"
if not defined PY (
    for /f "delims=" %%i in ('py -3 -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'python.exe'))" 2^>nul') do set "PY=%%i"
)
if defined PY if not exist "%PY%" set "PY="
if not defined PY (
    for /f "delims=" %%i in ('where python.exe 2^>nul') do if not defined PY set "PY=%%i"
)
if not defined PY (
    echo python.exe not found. Install Python 3.10+ first.
    pause
    exit /b 1
)

"%PY%" -X utf8 stash_viewer.py
echo.
echo Exit code %ERRORLEVEL%. Traceback is also appended to launch_log.txt
pause
endlocal
