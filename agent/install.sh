#!/usr/bin/env bash
# PingWatch Remote Probe Agent — Linux installer.
# Copies this folder to /opt/pingwatch-agent (unless already running from
# there), installs + starts the systemd service.
set -euo pipefail

TARGET="/opt/pingwatch-agent"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo bash install.sh" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required but not found" >&2
    exit 1
fi
PYVER=$(python3 -c 'import sys; print("%d%02d" % sys.version_info[:2])')
if [[ "$PYVER" -lt 308 ]]; then
    echo "Python 3.8+ required (found $(python3 -V))" >&2
    exit 1
fi

if [[ "$SRC" != "$TARGET" ]]; then
    echo "Installing to $TARGET ..."
    mkdir -p "$TARGET"
    cp -r "$SRC/." "$TARGET/"
fi
chmod 600 "$TARGET/config.json" 2>/dev/null || true

# Optional deps note
if ! command -v snmpget >/dev/null 2>&1; then
    echo "NOTE: 'snmpget' not found — SNMP sensors assigned to this probe"
    echo "      will fail. Install net-snmp (apt: snmp / dnf: net-snmp-utils)."
fi

UNIT_SRC="$TARGET/pingwatch-agent.service"
UNIT_DST="/etc/systemd/system/pingwatch-agent.service"
sed -e "s|WorkingDirectory=.*|WorkingDirectory=$TARGET|" \
    -e "s|ExecStart=.*|ExecStart=$(command -v python3) $TARGET/agent.py|" \
    "$UNIT_SRC" > "$UNIT_DST"
systemctl daemon-reload
systemctl enable pingwatch-agent.service
systemctl restart pingwatch-agent.service

echo ""
echo "PingWatch agent installed and started."
echo "  status : systemctl status pingwatch-agent"
echo "  logs   : journalctl -u pingwatch-agent -f   (or $TARGET/agent.log)"
