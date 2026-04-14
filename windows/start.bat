@echo off
cd /d "%~dp0.."
where pythonw >nul 2>&1 && (pythonw windows\launcher.pyw %* & exit /b)
python windows\launcher.pyw %*
