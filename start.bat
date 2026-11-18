@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
where npm.cmd >nul 2>nul || goto :node
pushd frontend
call npm.cmd ci --prefer-offline --no-audit --no-fund
if errorlevel 1 (popd & goto :fail)
call npm.cmd run build
if errorlevel 1 (popd & goto :fail)
popd
".venv\Scripts\python.exe" -m backend.server --open
if errorlevel 1 goto :fail
exit /b 0
:node
echo Install Node.js 22.12 or newer with npm and retry.
:fail
pause
exit /b 1
