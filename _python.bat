@echo off
rem Locate a Python interpreter. Sets PY (console) and PYW (no console).
rem Order: python_path.txt -> PATH -> LocalAppData -> ProgramFiles -> C:/Python3*
rem Exit code 9 means "no python found".
set "PY="
set "PYW="
if exist "%~dp0python_path.txt" set /p PY=<"%~dp0python_path.txt"
if defined PY if exist "%PY%" goto have
for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if defined PY if exist "%PY%" goto have
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%~D\python.exe" set "PY=%%~D\python.exe"
if defined PY if exist "%PY%" goto have
for /d %%D in ("%PROGRAMFILES%\Python3*") do if exist "%%~D\python.exe" set "PY=%%~D\python.exe"
if defined PY if exist "%PY%" goto have
for /d %%D in ("C://Python3*") do if exist "%%~D\python.exe" set "PY=%%~D\python.exe"
if defined PY if exist "%PY%" goto have
exit /b 9
:have
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"
exit /b 0
