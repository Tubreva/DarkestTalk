@echo off
rem Normal start: hand off to pythonw and close this console at once.
rem Logs: out\dd_scribe.log and out\overlay.log
setlocal
cd /d "%~dp0"
call "%~dp0_python.bat"
if errorlevel 1 goto nopython
start "" "%PYW%" launch.py %*
exit /b

:nopython
echo.
echo  Python 3.8+ not found.
echo  Please install Python from https://www.python.org/downloads/
echo  (tick "Add Python to PATH" during install), then run this file again.
echo.
echo  Already installed in a custom place? Put its full path in python_path.txt
echo  next to this file, one line, for example:
echo      D://Python39//python.exe
echo.
pause
exit /b 1
