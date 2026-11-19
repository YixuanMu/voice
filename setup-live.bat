@echo off
setlocal
cd /d "%~dp0"
title Voice Dashboard - Live Setup
echo [1/3] Checking Python. First launch may take a minute.
if not exist ".venv\Scripts\python.exe" (
  py -3 -c "import sys; sys.exit(not sys.version_info >= (3, 11))" >nul 2>nul || goto :python
  echo Creating the Python environment...
  py -3 -m venv .venv || goto :fail
)
echo [2/3] Checking the Tongdaxin SDK...
".venv\Scripts\python.exe" -c "from tdxaidata import tqs" >nul 2>nul
if errorlevel 1 (
  echo Installing the SDK. Internet access is needed.
  ".venv\Scripts\python.exe" -m pip --disable-pip-version-check install --timeout 15 --retries 1 tdxaidata -i https://pypi.tuna.tsinghua.edu.cn/simple || goto :fail
)
echo [3/3] Paste the Key in the pop-up window. Verification checks one quote.
".venv\Scripts\python.exe" -m scripts.configure_live
if errorlevel 2 exit /b 0
if errorlevel 1 goto :fail
set "VOICE_SOURCE="
call "%~dp0start.bat"
exit /b %errorlevel%
:python
echo Python 3.11 or later with the py launcher is required.
echo Download: https://www.python.org/downloads/windows/
:fail
echo Setup stopped. Check the message above.
pause
exit /b 1
