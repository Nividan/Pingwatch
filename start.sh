#!/usr/bin/env bash
# ============================================================
#  PingWatch — launcher for Linux / macOS
#  Usage:
#    bash start.sh            — normal start (runs setup on first launch)
#    bash start.sh --setup    — force re-run the setup wizard
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

# ── Python version check ───────────────────────────────────
if ! "$PYTHON" -c "import sys; assert sys.version_info >= (3,8)" 2>/dev/null; then
    echo "[ERROR] Python 3.8 or newer is required."
    echo "        Install it with:"
    echo "          Debian/Ubuntu: sudo apt-get install python3"
    echo "          macOS:         brew install python@3.11"
    exit 1
fi

# ── First-run / forced setup ────────────────────────────────
DB="$SCRIPT_DIR/pingwatch.db"
if [ ! -f "$DB" ] || [ "${1:-}" = "--setup" ]; then
    echo "[SETUP] Running first-run setup wizard..."
    "$PYTHON" "$SCRIPT_DIR/setup_wizard.py"
fi

# ── Root / SNMP port warning ────────────────────────────────
if [ "$EUID" -ne 0 ] && [ "${PINGWATCH_NO_ROOT_WARN:-}" != "1" ]; then
    echo ""
    echo "[WARN] Not running as root."
    echo "       SNMP traps on port 162 will fail (privileged port)."
    echo "       Options:"
    echo "         1) sudo bash start.sh"
    echo "         2) Set SNMP port to 1162 in Settings → Networking"
    echo "         3) iptables redirect:  sudo iptables -t nat -A PREROUTING \\"
    echo "                -p udp --dport 162 -j REDIRECT --to-ports 1162"
    echo ""
fi

# ── Launch server ───────────────────────────────────────────
cd "$SCRIPT_DIR"
exec "$PYTHON" "$SCRIPT_DIR/server.py"
