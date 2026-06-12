#!/usr/bin/env bash
# PingWatch Remote Probe Agent — Linux installer.
# Copies this folder to /opt/pingwatch-agent (unless already running from
# there), offers to install optional sensor capabilities, then installs +
# starts the systemd service.
#
# Flags (for unattended installs; without them an interactive terminal is
# asked, a non-interactive run just warns like before):
#   --with-snmp      install net-snmp (snmpget) for SNMP sensors
#   --with-ssh       install paramiko for SSH/SFTP sensors
#   --all-optional   both of the above
#   --no-optional    never prompt, never install optional packages
#
# Re-running the installer is safe (idempotent copy + service restart) —
# e.g. `sudo bash install.sh --with-ssh` later, when SSH sensors get
# assigned to this probe.
set -euo pipefail

TARGET="/opt/pingwatch-agent"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WITH_SNMP=""; WITH_SSH=""; NO_OPTIONAL=""
for arg in "$@"; do
    case "$arg" in
        --with-snmp)    WITH_SNMP=1 ;;
        --with-ssh)     WITH_SSH=1 ;;
        --all-optional) WITH_SNMP=1; WITH_SSH=1 ;;
        --no-optional)  NO_OPTIONAL=1 ;;
        *) echo "Unknown flag: $arg" >&2; exit 1 ;;
    esac
done

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

# ── Optional sensor capabilities ──────────────────────────────────
# The agent's core sensor types are stdlib-only. SNMP needs the snmpget
# binary; SSH/SFTP need paramiko. Offer them here — the person running the
# installer is the right one to decide — but never block the install on
# them: a failed/declined extra just means those sensor types report
# "capability missing" until added (re-run with --with-… anytime).

_pkg_mgr() {
    command -v apt-get >/dev/null 2>&1 && { echo apt; return; }
    command -v dnf     >/dev/null 2>&1 && { echo dnf; return; }
    command -v yum     >/dev/null 2>&1 && { echo yum; return; }
    command -v zypper  >/dev/null 2>&1 && { echo zypper; return; }
    echo ""
}

_ask() {  # yes → 0. Auto-no when --no-optional or stdin isn't a terminal.
    [[ -n "$NO_OPTIONAL" ]] && return 1
    [[ -t 0 ]] || return 1
    local _a
    read -r -p "$1 [y/N] " _a
    [[ "$_a" =~ ^[Yy] ]]
}

_install_snmp() {
    case "$(_pkg_mgr)" in
        apt)    apt-get install -y snmp && return 0 ;;
        dnf)    dnf install -y net-snmp-utils && return 0 ;;
        yum)    yum install -y net-snmp-utils && return 0 ;;
        zypper) zypper --non-interactive install net-snmp && return 0 ;;
        *)      echo "  ! No supported package manager found." ;;
    esac
    return 1
}

_install_paramiko() {
    # Distro package first — keeps PEP 668 (externally-managed) systems
    # clean. pip is the fallback; --break-system-packages is the last
    # resort and acceptable on a dedicated monitoring host.
    case "$(_pkg_mgr)" in
        apt)    apt-get install -y python3-paramiko && return 0 ;;
        dnf)    dnf install -y python3-paramiko && return 0 ;;
        yum)    yum install -y python3-paramiko && return 0 ;;
        zypper) zypper --non-interactive install python3-paramiko && return 0 ;;
    esac
    echo "  Distro package unavailable — trying pip…"
    python3 -m pip install paramiko 2>/dev/null && return 0
    python3 -m pip install --break-system-packages paramiko && return 0
    return 1
}

if command -v snmpget >/dev/null 2>&1; then
    echo "snmpget found — SNMP sensors supported."
else
    if [[ -n "$WITH_SNMP" ]] || _ask "Install net-snmp (snmpget) so this probe can run SNMP sensors?"; then
        _install_snmp || echo "  ! net-snmp install failed — SNMP sensors will report 'capability missing'."
    else
        echo "NOTE: 'snmpget' not found — SNMP sensors assigned to this probe"
        echo "      will fail. Re-run later: sudo bash install.sh --with-snmp"
    fi
fi

if python3 -c "import paramiko" >/dev/null 2>&1; then
    echo "paramiko found — SSH/SFTP sensors supported."
else
    if [[ -n "$WITH_SSH" ]] || _ask "Install paramiko so this probe can run SSH/SFTP sensors?"; then
        _install_paramiko || echo "  ! paramiko install failed — SSH/SFTP sensors will report 'capability missing'."
    else
        echo "NOTE: paramiko not found — SSH/SFTP sensors assigned to this probe"
        echo "      will fail. Re-run later: sudo bash install.sh --with-ssh"
    fi
fi
# (VMware sensors additionally need pyvmomi — niche enough that it stays
#  manual: pip3 install pyvmomi. See requirements-optional.txt.)

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
echo "  boot   : enabled — starts automatically at boot, auto-restarts on crash"
echo "           (opt out: systemctl disable pingwatch-agent)"
echo "  status : systemctl status pingwatch-agent"
echo "  logs   : journalctl -u pingwatch-agent -f   (or $TARGET/agent.log)"
