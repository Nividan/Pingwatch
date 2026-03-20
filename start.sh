#!/usr/bin/env bash
# ============================================================
#  PingWatch — launcher for Linux / macOS
#
#  Usage:
#    bash start.sh                   — foreground, console visible
#    bash start.sh --setup           — re-run setup wizard
#    sudo bash start.sh --install-service   — install as systemd service
#    sudo bash start.sh --uninstall-service — remove systemd service
#
#  Service management (after --install-service):
#    sudo systemctl start   pingwatch
#    sudo systemctl stop    pingwatch
#    sudo systemctl restart pingwatch
#    sudo systemctl status  pingwatch
#    journalctl -u pingwatch -f        (live logs)
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

# ── Service install / uninstall ─────────────────────────────
if [ "${1:-}" = "--install-service" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo "[ERROR] --install-service requires root: sudo bash start.sh --install-service"
        exit 1
    fi
    SERVICE_SRC="$SCRIPT_DIR/pingwatch.service"
    SERVICE_DST="/etc/systemd/system/pingwatch.service"
    if [ ! -f "$SERVICE_SRC" ]; then
        echo "[ERROR] pingwatch.service not found in $SCRIPT_DIR"
        exit 1
    fi
    # Determine the actual user who invoked sudo (fall back to current user)
    ACTUAL_USER="${SUDO_USER:-$(whoami)}"
    ACTUAL_GROUP="$(id -gn "$ACTUAL_USER" 2>/dev/null || echo "$ACTUAL_USER")"
    # Patch WorkingDirectory and ExecStart to the actual install path
    sed "s|/opt/pingwatch|$SCRIPT_DIR|g" "$SERVICE_SRC" > "$SERVICE_DST"
    # Replace python path with the actual python3 on this system
    PYPATH="$(command -v python3)"
    sed -i "s|/usr/bin/python3|$PYPATH|g" "$SERVICE_DST"
    # Set User/Group so the service runs as the file owner, not root
    # (CapabilityBoundingSet=CAP_NET_BIND_SERVICE would strip root's DAC_OVERRIDE)
    sed -i "s|^# User=pingwatch|User=$ACTUAL_USER|" "$SERVICE_DST"
    sed -i "s|^# Group=pingwatch|Group=$ACTUAL_GROUP|" "$SERVICE_DST"
    systemctl daemon-reload
    systemctl enable pingwatch
    systemctl start  pingwatch
    echo "[OK]  PingWatch service installed and started."
    echo "      Check status:  sudo systemctl status pingwatch"
    echo "      Live logs:     journalctl -u pingwatch -f"
    exit 0
fi

if [ "${1:-}" = "--uninstall-service" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo "[ERROR] --uninstall-service requires root: sudo bash start.sh --uninstall-service"
        exit 1
    fi
    systemctl stop    pingwatch 2>/dev/null || true
    systemctl disable pingwatch 2>/dev/null || true
    rm -f /etc/systemd/system/pingwatch.service
    systemctl daemon-reload
    echo "[OK]  PingWatch service removed."
    exit 0
fi

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

# ── Root / privileged port warning ─────────────────────────
if [ "$EUID" -ne 0 ] && [ "${PINGWATCH_NO_ROOT_WARN:-}" != "1" ]; then
    "$PYTHON" -c "
import sys, os
sys.path.insert(0, '$SCRIPT_DIR')
try:
    from db.users import db_load_settings
    s = db_load_settings()
    ports = {'HTTP': int(s.get('http_port', 7070))}
    if int(s.get('tls_enabled', 0)):
        ports['HTTPS'] = int(s.get('tls_port', 8443))
    ports['SNMP'] = int(s.get('snmp_port', 162))
    priv = {name: p for name, p in ports.items() if p < 1024}
    if priv:
        port_list = ', '.join(f'{name} {p}' for name, p in priv.items())
        print()
        print('[WARN] Not running as root.')
        print(f'       Privileged ports configured (requires root): {port_list}')
        print('       Options:')
        print('         1) sudo bash start.sh')
        print('         2) Change ports >= 1024 in Settings -> Networking')
        if 'SNMP' in priv:
            print('         3) iptables redirect (SNMP only):')
            print('              sudo iptables -t nat -A PREROUTING -p udp --dport 162 -j REDIRECT --to-ports 1162')
        print()
except Exception:
    pass
" 2>/dev/null
fi

# ── Launch server ───────────────────────────────────────────
cd "$SCRIPT_DIR"
exec "$PYTHON" "$SCRIPT_DIR/server.py"
