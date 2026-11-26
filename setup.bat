@echo off
setlocal
cd /d "%~dp0"
title TaskbarHero Stash Viewer - Setup

set "PYEXE="
call :try "%TBH_PYTHON%"
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do call :try "%%i"
call :try "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
call :try "%ProgramFiles%\Python313\python.exe"
call :try "%ProgramFiles%\Python312\python.exe"
call :try "%ProgramFiles%\Python311\python.exe"
call :try "C:\Python313\python.exe"
call :try "C:\Python312\python.exe"
call :try "C:\Python311\python.exe"
call :try "C:\Python310\python.exe"
for /f "delims=" %%i in ('where python.exe 2^>nul') do call :try "%%i"

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
if "%RC%"=="9009" echo  Python at "%PYEXE%" could not be started - install Python 3.10+ from python.org.
if "%RC%"=="2" echo  An error dialog should have appeared as well.
echo.
if not defined TBH_NO_PAUSE pause
endlocal
exit /b %RC%

:done
endlocal
exit /b 0

:nopython
echo.
echo  Python 3.10+ was not found (Windows Store python.exe aliases are ignored).
echo  Install Python from https://www.python.org/downloads/windows/
echo  (check "Add python.exe to PATH" during install), then double-click setup.bat again.
echo  Set TBH_PYTHON to your python.exe path if it lives somewhere unusual.
echo.
>> "setup_log.txt" echo [%DATE% %TIME%] no usable Python found (WindowsApps alias or wrong path skipped)
if not defined TBH_NO_PAUSE pause
endlocal
exit /b 3

:try
rem skip empty paths, Windows Store alias stubs and anything that cannot actually run
if defined PYEXE goto :eof
if "%~1"=="" goto :eof
echo %~1| findstr /i "\WindowsApps\" >nul && goto :eof
if not exist "%~1" goto :eof
"%~1" -c "import sys" >nul 2>nul || goto :eof
set "PYEXE=%~1"
goto :eof
