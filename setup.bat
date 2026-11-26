@echo off
setlocal
cd /d "%~dp0"
title TaskbarHero Stash Viewer - Setup

if defined TBH_PYTHON if exist "%TBH_PYTHON%" set "PYEXE=%TBH_PYTHON%"
if not defined PYEXE for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if not defined PYEXE for %%D in (
  "%LOCALAPPDATA%\Programs\Python\Python313"
  "%LOCALAPPDATA%\Programs\Python\Python312"
  "%LOCALAPPDATA%\Programs\Python\Python311"
  "%LOCALAPPDATA%\Programs\Python\Python310"
  "C:\Program Files\Python313"
  "C:\Program Files\Python312"
  "C:\Program Files\Python311"
  "C:\Python313"
  "C:\Python312"
  "C:\Python311"
  "C:\Python310"
) do if not defined PYEXE if exist "%%~D\python.exe" set "PYEXE=%%~D\python.exe"
if not defined PYEXE for /f "delims=" %%i in ('where python.exe 2^>nul') do if not defined PYEXE set "PYEXE=%%i"

if not defined PYEXE goto nopython

echo.
echo  Found Python: %PYEXE%
echo  Preparing assets. This may take a few minutes on first run...
echo  Log file: setup_log.txt
echo.
"%PYEXE%" -X utf8 "tools\setup_all.py" %*
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" goto done
echo.
echo  Setup failed (exit %RC%). Details are in setup_log.txt.
echo  An error dialog should have appeared as well.
echo.
if not defined TBH_NO_PAUSE pause
endlocal
exit /b %RC%

:done
endlocal
exit /b 0

:nopython
echo.
echo  Python 3.10+ was not found.
echo  Install Python from https://www.python.org/downloads/windows/
echo  (check "Add python.exe to PATH" during install), then double-click setup.bat again.
echo.
if not defined TBH_NO_PAUSE pause
endlocal
exit /b 3
