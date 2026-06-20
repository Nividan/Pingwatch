#!/usr/bin/env bash
# ============================================================
#  PingWatch — safe production deploy
#
#  Usage (on the server):
#    bash linux/deploy.sh
#
#  Pulls the latest code, byte-compiles every source file, and only then
#  restarts the service. A `git pull` that brings in a SyntaxError followed by
#  a blind `systemctl restart` sends the unit into a systemd crash loop
#  (Restart=on-failure). This compiles first, so a bad pull leaves the
#  currently-running instance untouched and reports the error instead.
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${PYTHON:-python3}"
cd "$PROJECT_ROOT"

echo "[deploy] git pull (fast-forward only) in $PROJECT_ROOT"
git pull --ff-only

echo "[deploy] syntax check: $PYTHON -m compileall"
if ! "$PYTHON" -m compileall -q -x 'venv' "$PROJECT_ROOT"; then
    echo "[deploy] ABORT — syntax error in pulled code." >&2
    echo "[deploy] Service NOT restarted; it is still running the previous version." >&2
    exit 1
fi

echo "[deploy] restarting pingwatch.service"
sudo systemctl restart pingwatch.service

# Give startup a moment, then confirm it actually came up.
sleep 4
if systemctl is-active --quiet pingwatch.service; then
    echo "[deploy] OK — pingwatch.service is active"
else
    echo "[deploy] WARNING — service is not active after restart." >&2
    echo "[deploy] Check: journalctl -u pingwatch -n 50 --no-pager" >&2
    exit 1
fi
