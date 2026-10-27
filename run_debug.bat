@echo off
chcp 65001 >nul
rem Debug start: keeps the console and shows live output.
setlocal
cd /d "%~dp0"
call "%~dp0_python.bat"
if errorlevel 1 (
  echo Python 3.8+ not found. See run.bat for details.
  pause
  exit /b 1
)
"%PY%" launch.py %*
pause
