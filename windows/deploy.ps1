# ============================================================
#  PingWatch - safe update for Windows
#
#  Usage (from anywhere):
#    powershell -ExecutionPolicy Bypass -File windows\deploy.ps1
#
#  Windows counterpart of linux/deploy.sh. Pulls the latest code, byte-compiles
#  every source file, and only then relaunches. If the pulled code has a syntax
#  error the relaunch is SKIPPED and the currently-running instance keeps
#  serving the previous version - a typo never takes the server down.
#
#  Note: relaunch runs windows\start.bat, whose launcher frees the HTTP/HTTPS
#  ports by stopping the old instance, then starts the new code. That is a hard
#  restart (no graceful drain); up to ~5s of buffered samples may be lost.
# ============================================================
$ErrorActionPreference = 'Stop'

# Project root = parent of the windows\ folder this script lives in.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Locate a Python interpreter (py launcher preferred, then python).
$py = $null
$pyCmd = Get-Command py -ErrorAction SilentlyContinue
if ($pyCmd) {
    $py = $pyCmd.Source
} else {
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pyCmd) { $py = $pyCmd.Source }
}
if ($null -eq $py) {
    Write-Host '[deploy] ERROR - python not found on PATH' -ForegroundColor Red
    exit 1
}

Write-Host "[deploy] git pull (fast-forward only) in $root"
git pull --ff-only
if ($LASTEXITCODE -ne 0) {
    Write-Host '[deploy] ABORT - git pull failed; nothing changed' -ForegroundColor Red
    exit 1
}

Write-Host "[deploy] syntax check: compileall"
& $py -m compileall -q -x venv $root
if ($LASTEXITCODE -ne 0) {
    Write-Host '[deploy] ABORT - syntax error in pulled code.' -ForegroundColor Red
    Write-Host '[deploy] Not relaunched; the previous version is still running.' -ForegroundColor Red
    exit 1
}

Write-Host '[deploy] relaunching (start.bat stops the old instance via port cleanup)'
Start-Process -FilePath (Join-Path $root 'windows\start.bat') -WorkingDirectory $root
Write-Host '[deploy] OK - relaunch triggered. Verify at https://localhost:8443'
