@echo off
rem PingWatch Remote Probe Agent — Windows installer.
rem Registers a Scheduled Task that starts the agent at boot as SYSTEM
rem (no console window) and starts it now. Run from an elevated prompt.

setlocal
set "DIR=%~dp0"
set "DIR=%DIR:~0,-1%"

net session >nul 2>&1
if errorlevel 1 (
    echo This installer must run as Administrator.
    pause
    exit /b 1
)

where pythonw >nul 2>&1
if errorlevel 1 (
    echo Python 3.8+ with pythonw.exe is required but was not found in PATH.
    pause
    exit /b 1
)
for /f "delims=" %%P in ('where pythonw') do set "PYW=%%P" & goto :gotpy
:gotpy

where snmpget >nul 2>&1
if errorlevel 1 (
    echo NOTE: snmpget.exe not found - SNMP sensors assigned to this probe
    echo       will fail. Install net-snmp if you need SNMP from this site.
)

schtasks /Query /TN "PingWatchAgent" >nul 2>&1
if not errorlevel 1 schtasks /Delete /TN "PingWatchAgent" /F >nul

schtasks /Create /TN "PingWatchAgent" /SC ONSTART /RU SYSTEM /RL HIGHEST ^
    /TR "\"%PYW%\" \"%DIR%\agent.py\"" /F
if errorlevel 1 (
    echo Failed to create the scheduled task.
    pause
    exit /b 1
)
schtasks /Run /TN "PingWatchAgent" >nul

echo.
echo PingWatch agent installed (Scheduled Task "PingWatchAgent") and started.
echo   logs: %DIR%\agent.log
echo   stop: schtasks /End /TN PingWatchAgent
pause
