@echo off
setlocal
cd /d "%~dp0"
title Voice Dashboard
echo [1/3] Checking Python. First launch may take a minute.
if not exist ".venv\Scripts\python.exe" (
  py -3 -c "import sys; sys.exit(not sys.version_info >= (3, 11))" >nul 2>nul || goto :python
  echo Creating the Python environment...
  py -3 -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -c "import tornado" >nul 2>nul
if errorlevel 1 (
  echo [2/3] Installing Python dependencies. Internet access is needed.
  ".venv\Scripts\python.exe" -m pip --disable-pip-version-check install --timeout 15 --retries 1 -r requirements.txt || goto :fail
) else (
  echo [2/3] Python dependencies are ready.
)
if exist "frontend\dist\index.html" goto :serve
where npm.cmd >nul 2>nul || goto :node
pushd frontend
call npm.cmd ci --prefer-offline --no-audit --no-fund
if errorlevel 1 (popd & goto :fail)
call npm.cmd run build
if errorlevel 1 (popd & goto :fail)
popd
:serve
if not defined VOICE_PORT set "VOICE_PORT=8765"
echo [3/3] Starting Dashboard on the local computer
echo Keep this window open while using the Dashboard.
if /i "%~1"=="--no-open" goto :serve_no_browser
".venv\Scripts\python.exe" -m backend.server --port "%VOICE_PORT%" --open
goto :server_done
:serve_no_browser
".venv\Scripts\python.exe" -m backend.server --port "%VOICE_PORT%"
:server_done
if errorlevel 1 goto :fail
exit /b 0
:python
echo Python 3.11 or later with the py launcher is required.
echo Download: https://www.python.org/downloads/windows/
goto :fail
:node
echo Install Node.js 22.12 or newer with npm and retry.
:fail
echo Startup stopped. The reason is shown above.
pause
exit /b 1
