@echo off
rem ==========================================================
rem  Water network pressure monitoring demo - double-click me
rem  (Korean guide: see README.md)
rem ==========================================================
cd /d "%~dp0"
set PYTHONUTF8=1

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 launcher.py
) else (
    python launcher.py
)

if errorlevel 1 (
    echo.
    echo [ERROR] Python 3.11+ is required: https://www.python.org/downloads/
    echo         Check "Add python.exe to PATH" when installing.
    pause
)
