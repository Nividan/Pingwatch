@echo off
cd /d "%~dp0.."

:: ── 0. Elevate to admin (required to bind SNMP trap port 162) ─────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges ^(needed for SNMP trap port 162^)...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    exit /b
)

:: ── 1. Python check ───────────────────────────────────────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found in PATH.
    echo         Download Python 3.8+ from https://www.python.org/downloads/
    echo         Make sure to tick "Add Python to PATH" during installation.
    pause
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python 3.8 or newer is required.
    for /f "delims=" %%V in ('python --version 2^>^&1') do echo         Found: %%V
    pause
    exit /b 1
)

:: ── 2. First-run detection ────────────────────────────────────────────────────
set RUN_WIZARD=0

if not exist "pingwatch.conf" set RUN_WIZARD=1
if "%1"=="--setup"            set RUN_WIZARD=1

if %RUN_WIZARD% equ 1 (
    echo.
    python setup_wizard.py %*
    if %errorlevel% neq 0 (
        echo.
        echo [ERROR] Setup wizard failed. Fix the error above, then run start.bat again.
        pause
        exit /b 1
    )
    echo.
)

:: ── 3. Kill any existing PingWatch process on ports 7070 / 8443 ───────────────
powershell -NoProfile -Command "foreach($port in @(7070,8443)){$c=Get-NetTCPConnection -LocalPort $port -State Listen -EA SilentlyContinue; if($c){Write-Host ('[!] Port '+$port+' in use. Stopping PingWatch...'); $c|ForEach-Object{$p=Get-Process -Id $_.OwningProcess -EA SilentlyContinue; if($p -and $p.Name -match 'python'){Write-Host ('  Stopping PID '+$p.Id+' ('+$p.Name+')...'); Stop-Process -Id $p.Id -Force}}}}; Start-Sleep 1"
echo.

:: ── 4. Start server ───────────────────────────────────────────────────────────
echo Starting PingWatch...
python server.py
if %errorlevel% neq 0 (
    echo.
    echo PingWatch failed to start. See error above.
    pause
)
