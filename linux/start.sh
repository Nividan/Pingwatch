#!/usr/bin/env bash
# ============================================================
#  PingWatch — launcher for Linux / macOS
#
#  Usage:
#    bash start.sh                   — foreground, console visible
#    bash start.sh --setup           — re-run setup wizard
#    bash start.sh --check           — re-check required packages (no wizard)
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
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
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
    sed "s|/opt/pingwatch|$PROJECT_ROOT|g" "$SERVICE_SRC" > "$SERVICE_DST"
    # Replace python path with the actual python3 on this system
    PYPATH="$(command -v python3)"
    sed -i "s|/usr/bin/python3|$PYPATH|g" "$SERVICE_DST"
    # Set User/Group so the service runs as the file owner, not root
    # (CapabilityBoundingSet=CAP_NET_BIND_SERVICE would strip root's DAC_OVERRIDE)
    sed -i "s|^# User=pingwatch|User=$ACTUAL_USER|" "$SERVICE_DST"
    sed -i "s|^# Group=pingwatch|Group=$ACTUAL_GROUP|" "$SERVICE_DST"

    # ── Polkit rule: let $ACTUAL_USER manage pingwatch.service without password
    # Without this, every `systemctl start/stop/restart pingwatch` triggers a
    # polkit auth prompt for the pingwatch admin user — fine for occasional ops,
    # painful for the typical pull-and-restart workflow. Scope is narrow: only
    # this one unit, only this one user.
    POLKIT_DIR="/etc/polkit-1/rules.d"
    POLKIT_RULE="$POLKIT_DIR/49-pingwatch.rules"
    if [ -d "$POLKIT_DIR" ]; then
        cat > "$POLKIT_RULE" <<EOF
// PingWatch — auto-installed by start.sh --install-service
// Allows '$ACTUAL_USER' to start/stop/restart pingwatch.service without a
// polkit password prompt. Remove with: sudo bash start.sh --uninstall-service
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") == "pingwatch.service" &&
        subject.user == "$ACTUAL_USER") {
        return polkit.Result.YES;
    }
});
EOF
        chmod 0644 "$POLKIT_RULE"
        # polkit picks up rule changes automatically on most distros, but
        # restarting the daemon guarantees the rule is live before the next
        # systemctl call. Tolerate the absence of polkit (unusual on systemd
        # systems but possible on stripped-down containers).
        systemctl restart polkit 2>/dev/null \
            || systemctl restart polkitd 2>/dev/null \
            || true
        echo "[OK]  Polkit rule installed: $ACTUAL_USER can manage pingwatch.service without password"
    else
        echo "[WARN] $POLKIT_DIR not found — skipping polkit rule"
        echo "       (you'll be prompted for password on systemctl start/stop/restart)"
    fi

    systemctl daemon-reload
    systemctl enable pingwatch
    systemctl start  pingwatch
    echo "[OK]  PingWatch service installed and started."
    echo "      Check status:  systemctl status pingwatch"
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
    # Remove the polkit rule that --install-service may have created
    if [ -f /etc/polkit-1/rules.d/49-pingwatch.rules ]; then
        rm -f /etc/polkit-1/rules.d/49-pingwatch.rules
        systemctl restart polkit 2>/dev/null \
            || systemctl restart polkitd 2>/dev/null \
            || true
        echo "[OK]  Polkit rule removed."
    fi
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

# ── Package health-check (--check) ─────────────────────────
if [ "${1:-}" = "--check" ]; then
    echo "[CHECK] Checking required packages..."
    "$PYTHON" - <<'PYEOF'
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else '.')

_PACKAGES = [
    ("cryptography", "TLS certificate generation & encryption", True),
    ("paramiko",     "SSH device backups",                      False),
    ("smbclient",    "SMB / CIFS remote DB backup upload",      False),
    ("pyrad",        "RADIUS authentication",                   False),
    ("saml2",        "SAML 2.0 SSO",                            False),
    ("authlib",      "OpenID Connect SSO",                      False),
    ("pystray",      "system tray icon",                        False),
    ("PIL",          "image support (tray icon)",               False),
    ("ldap3",        "LDAP / Active Directory authentication",  False),
    ("pyotp",        "two-factor authentication (TOTP)",        False),
    ("qrcode",       "QR code rendering for 2FA enrolment",     False),
    ("jinja2",       "report HTML template rendering",          False),
    ("matplotlib",   "report charts (PNG rendering)",           False),
    ("weasyprint",   "PDF report generation (HTML->PDF)",       False),
    ("tkinter",      "status window GUI",                       False),
]
all_ok = True
for mod, desc, required in _PACKAGES:
    try:
        __import__(mod)
        print(f"  [OK]   {mod} — {desc}")
    except ImportError:
        tag = "[ERROR]" if required else "[WARN] "
        print(f"  {tag} {mod} is NOT installed — {desc}")
        if required:
            all_ok = False
if all_ok:
    print("\n  All required packages present.")
else:
    print("\n  Some required packages are missing.")
    print("  Run: bash start.sh --setup   to reinstall them.")
    sys.exit(1)
PYEOF
    exit $?
fi

# ── First-run / forced setup ────────────────────────────────
CONF="$PROJECT_ROOT/pingwatch.conf"
if [ ! -f "$CONF" ] || [ "${1:-}" = "--setup" ]; then
    echo "[SETUP] Running setup wizard..."
    # Try GUI wizard first (tkinter); fall back to CLI if no display or no tkinter
    if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        "$PYTHON" -c "from gui_setup import run_wizard; exit(0 if run_wizard() else 1)" 2>/dev/null && WIZARD_OK=1 || WIZARD_OK=0
    else
        WIZARD_OK=0
    fi
    if [ "$WIZARD_OK" = "0" ]; then
        "$PYTHON" "$PROJECT_ROOT/setup_wizard.py" "$@"
    fi
    # If the wizard restarted the systemd service, don't launch a second instance
    if command -v systemctl &>/dev/null && systemctl is-active --quiet pingwatch 2>/dev/null; then
        exit 0
    fi
fi

# ── Root / privileged port warning ─────────────────────────
if [ "$EUID" -ne 0 ] && [ "${PINGWATCH_NO_ROOT_WARN:-}" != "1" ]; then
    "$PYTHON" -c "
import sys, os
sys.path.insert(0, '$PROJECT_ROOT')
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
cd "$PROJECT_ROOT"
exec "$PYTHON" "$PROJECT_ROOT/server.py"
