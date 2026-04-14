@echo off
cd /d "%~dp0.."

:: Check Python is installed
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found in PATH.
    echo         Download Python 3.8+ from https://www.python.org/downloads/
    echo         Make sure to tick "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: Prefer pythonw (no console window); fall back to python
where pythonw >nul 2>&1 && (pythonw windows\launcher.pyw %* & exit /b)
python windows\launcher.pyw %*
if %errorlevel% neq 0 pause
