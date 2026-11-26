@echo off
rem Launch the viewer without a console window.
rem Python lookup order: TBH_PYTHON -> py launcher -> common install dirs -> pythonw on PATH
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%stash_viewer.py" (
    echo stash_viewer.py not found. Run this file from the program folder.
    pause
    exit /b 1
)

set "PY="
if defined TBH_PYTHON if exist "%TBH_PYTHON%" set "PY=%TBH_PYTHON%"
if not defined PY (
    for /f "delims=" %%i in ('py -3 -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PY=%%i"
)
if defined PY if not exist "%PY%" set "PY="
if not defined PY (
    for %%d in ("%LOCALAPPDATA%\Programs\Python\Python313" "%LOCALAPPDATA%\Programs\Python\Python312" "%LOCALAPPDATA%\Programs\Python\Python311" "%LOCALAPPDATA%\Programs\Python\Python310" "C:\Python313" "C:\Python312" "C:\Python311") do (
        if not defined PY if exist "%%~d\pythonw.exe" set "PY=%%~d\pythonw.exe"
    )
)
if not defined PY (
    for /f "delims=" %%i in ('where pythonw.exe 2^>nul') do if not defined PY set "PY=%%i"
)
if not defined PY (
    echo pythonw.exe not found.
    echo Install Python 3.10+ with "Add python.exe to PATH", or set TBH_PYTHON to the full pythonw.exe path.
    pause
    exit /b 1
)

rem Dependency check: pythonw hides tracebacks, so verify imports with python.exe first.
set "PYC=%PY%"
set "PYC=%PYC:pythonw.exe=python.exe%"
"%PYC%" -X utf8 -c "import tkinter, PIL, Crypto, httpx" 2>nul
if errorlevel 1 (
    echo Missing dependencies: tkinter / Pillow / pycryptodome / httpx.
    echo Run:  "%PYC%" -m pip install -r "%ROOT%requirements.txt"
    pause
    exit /b 1
)

start "" "%PY%" -X utf8 "%ROOT%stash_viewer.py"
endlocal
