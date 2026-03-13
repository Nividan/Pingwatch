@echo off
cd /d "%~dp0"

:: ── 0. Elevate to admin (required to bind SNMP trap port 162) ─────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges ^(needed for SNMP trap port 162^)...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: ── 1. Python check ────────────────────────────────────────────────────────
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

:: ── 2. tkinter check (stdlib — ships with standard Python installer) ───────
python -c "import tkinter" 2>nul
if %errorlevel% neq 0 (
    echo [!] tkinter is not available in your Python installation.
    echo     Re-install Python from python.org and make sure
    echo     "tcl/tk and IDLE" is checked in Optional Features.
    echo     ^(The status window GUI will not open without tkinter^)
    echo.
)

:: ── 3. Ensure optional tray packages are installed ─────────────────────────
python -c "import pystray; from PIL import Image" 2>nul
if %errorlevel% neq 0 (
    echo Installing required packages ^(pystray, Pillow^)...
    python -m pip install pystray Pillow
    echo.
)

:: ── 4. Ensure net-snmp (snmpget) is available ──────────────────────────────
where snmpget >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] snmpget not found. Attempting to install net-snmp via Chocolatey...
    where choco >nul 2>&1
    if %errorlevel% equ 0 (
        choco install net-snmp -y
        echo.
    ) else (
        where winget >nul 2>&1
        if %errorlevel% equ 0 (
            echo Trying winget...
            winget install net-snmp --silent
            echo.
        ) else (
            echo [!] Could not auto-install net-snmp. Install it manually:
            echo     https://sourceforge.net/projects/net-snmp/files/net-snmp%%20binaries/
            echo     Download the .exe, run it, tick "Add to PATH", restart CMD.
            echo     ^(SNMP sensors will not work until snmpget is in PATH^)
            echo.
        )
    )
)

:: ── 5. Desktop shortcut (ask once) ─────────────────────────────────────────
powershell -NoProfile -Command "if(Test-Path([Environment]::GetFolderPath('Desktop')+'\PingWatch.lnk')){exit 0}else{exit 1}" >nul 2>&1
if %errorlevel% equ 0 goto :skip_sc
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; $r=[System.Windows.MessageBox]::Show('Create a desktop shortcut for PingWatch?','PingWatch Setup','YesNo','Question'); if($r -eq 'Yes'){exit 0}else{exit 1}" >nul 2>&1
if %errorlevel% neq 0 goto :skip_sc
if %errorlevel% equ 0 (
    for /f "delims=" %%E in ('python -c "import sys; print(sys.executable)"') do (
        (
            echo $ws = New-Object -ComObject WScript.Shell
            echo $d  = [Environment]::GetFolderPath('Desktop'^)
            echo $sc = $ws.CreateShortcut("$d\PingWatch.lnk"^)
            echo $sc.TargetPath       = '%~dp0pingwatch.pyw'
            echo $sc.WorkingDirectory = '%~dp0'
            echo $sc.IconLocation     = '%%E,0'
            echo $sc.Description      = 'PingWatch Network Monitor'
            echo $sc.Save(^)
        ) > "%TEMP%\_pw_sc.ps1"
    )
    if exist "%TEMP%\_pw_sc.ps1" (
        powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\_pw_sc.ps1"
        del "%TEMP%\_pw_sc.ps1" 2>nul
        echo [OK] Desktop shortcut "PingWatch" created.
        echo.
    )
)
:skip_sc

:: ── 6. Firewall rules ───────────────────────────────────────────────────────
:: Dashboard (TCP 7070)
netsh advfirewall firewall show rule name="PingWatch Dashboard" >nul 2>&1
if %errorlevel% neq 0 (
    netsh advfirewall firewall add rule name="PingWatch Dashboard" ^
        dir=in action=allow protocol=TCP localport=7070 >nul
    echo [OK] Firewall rule added for dashboard port ^(TCP 7070^).
    echo.
)

:: SNMP trap ports — only if net-snmp is installed
where snmpget >nul 2>&1
if %errorlevel% equ 0 (
    netsh advfirewall firewall show rule name="PingWatch SNMP Traps" >nul 2>&1
    if %errorlevel% neq 0 (
        netsh advfirewall firewall add rule name="PingWatch SNMP Traps" ^
            dir=in action=allow protocol=UDP localport=162,1162,2162 >nul
        echo [OK] Firewall rule added for SNMP trap ports ^(UDP 162/1162/2162^).
        echo.
    )
)

:: ── 7. Kill any existing PingWatch process on port 7070 ────────────────────
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 7070 -State Listen -EA SilentlyContinue; if($c){Write-Host '[!] Port 7070 in use. Stopping PingWatch...'; $c|ForEach-Object{$p=Get-Process -Id $_.OwningProcess -EA SilentlyContinue; if($p -and $p.Name -match 'python'){Write-Host ('  Stopping PID '+$p.Id+' ('+$p.Name+')...'); Stop-Process -Id $p.Id -Force}}; Start-Sleep 1}"
echo.

:: ── 8. Start server ─────────────────────────────────────────────────────────
echo Starting PingWatch...
python server.py
if %errorlevel% neq 0 (
    echo.
    echo PingWatch failed to start. See error above.
    pause
)
