@echo off
chcp 65001 >nul
rem Rebuild the name table after changing mods (adds/removes mods).
setlocal
cd /d "%~dp0"
call "%~dp0_python.bat"
if errorlevel 1 (
  echo Python 3.8+ not found. See run.bat for details.
  pause
  exit /b 1
)
"%PY%" launch.py --build
pause
