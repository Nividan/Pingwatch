"""
backup/engine.py — Device configuration backup execution.

Supports SSH (via paramiko) and Telnet.
Each run_backup() call returns a result dict consumed by db_save_backup_run().
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import threading
import time

# Strip ANSI/VT100 escape sequences from SSH shell output.
# Covers: CSI sequences (ESC [ ... x), 2-char ESC sequences, and BEL/CR.
_ANSI_RE = re.compile(
    r'\x1b(?:\[[0-9;?]*[ -/]*[@-~]|[@-_][0-9;]*[a-zA-Z]?|[()][0-9A-Za-z])|'
    r'\x07|\r'
)

from core.logger import log_backup as log
from db.backups import decrypt_pw
from core.config import DB_PATH

# ── SSH known-host fingerprint store (TOFU — Trust On First Use) ──────────
# Keys are stored as  "host:port -> key_type:base64_fingerprint" in a simple
# text file next to the database.  On first connect the key is accepted and
# persisted; on subsequent connects a mismatch is treated as a hard error.

_KNOWN_HOSTS_PATH = os.path.join(os.path.dirname(DB_PATH), "ssh_known_hosts.txt")
_kh_lock = threading.RLock()   # RLock so _verify_host_key can hold it across the load-check-save sequence


def _load_known_hosts() -> dict:
    """Return {host_port_str: (key_type, hex_fingerprint)}."""
    out: dict = {}
    try:
        if not os.path.isfile(_KNOWN_HOSTS_PATH):
            return out
        with open(_KNOWN_HOSTS_PATH, 'r') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) == 3:
                    out[parts[0]] = (parts[1], parts[2])
    except Exception as e:
        log.warning(f"Could not read SSH known_hosts file: {e}")
    return out


def _save_known_host(hostport: str, key_type: str, fingerprint: str) -> None:
    with _kh_lock:
        with open(_KNOWN_HOSTS_PATH, 'a') as fh:
            fh.write(f"{hostport}\t{key_type}\t{fingerprint}\n")


def _verify_host_key(transport: 'paramiko.Transport', host: str, port: int) -> str | None:
    """
    Verify remote host key using TOFU.
    Returns None on success, or an error string on failure.
    """
    import base64
    remote_key = transport.get_remote_server_key()
    key_type = remote_key.get_name()
    fingerprint = hashlib.sha256(remote_key.asbytes()).hexdigest()
    hostport = f"{host}:{port}"

    with _kh_lock:
        known = _load_known_hosts()
        if hostport not in known:
            # First time seeing this host — trust and record (atomic: no TOCTOU window)
            _save_known_host(hostport, key_type, fingerprint)
            log.info(
                f"SSH backup: trusting new host key for {hostport} "
                f"({key_type} SHA256:{fingerprint[:16]}…) — saved to known_hosts"
            )
            return None
        stored_type, stored_fp = known[hostport]

    if stored_fp != fingerprint or stored_type != key_type:
        return (
            f"HOST KEY MISMATCH for {hostport}: expected {stored_type} "
            f"SHA256:{stored_fp[:16]}… but got {key_type} SHA256:{fingerprint[:16]}… "
            f"— possible MITM. To reset, remove the entry from {_KNOWN_HOSTS_PATH}"
        )
    return None

# Optional dependency check
try:
    import paramiko
    _PARAMIKO_OK = True
except ImportError:
    _PARAMIKO_OK = False
    log.warning("paramiko not installed — SSH backups disabled. Run: pip install paramiko")

try:
    import telnetlib
    _TELNET_OK = True
except ImportError:
    _TELNET_OK = False


def run_backup(device: dict, settings: dict) -> dict:
    """
    Execute backup for one device. Returns result dict:
        {success, config, error_msg, method, size_bytes, sha256, ts}

    'device' comes from app STATE (has 'host', 'name', 'device_id').
    'settings' comes from db_get_backup_settings (already decrypted fields NOT here — use db.backups.decrypt_pw).
    """
    method = (settings.get('method') or 'ssh').lower()
    log.debug(f"Backup: starting {method} backup for {device.name} ({device.host})")
    if method == 'ssh':
        return _ssh_backup(device, settings)
    elif method == 'telnet':
        return _telnet_backup(device, settings)
    else:
        return _fail(method, f"Unsupported backup method: {method}")


# ── SSH ──────────────────────────────────────────────────────────────

def _ssh_backup(device: dict, settings: dict) -> dict:
    if not _PARAMIKO_OK:
        return _fail('ssh', 'paramiko is not installed — run: pip install paramiko')

    host     = device.host
    port     = int(settings.get('port', 22))
    username = settings.get('username', '')
    password = decrypt_pw(settings.get('password_enc', ''))
    enable   = decrypt_pw(settings.get('enable_enc', ''))
    timeout  = int(settings.get('timeout', 30))
    paging   = (settings.get('paging_cmd') or '').strip()
    commands = _parse_commands(settings.get('commands', '["show running-config"]'))

    # ── Build a raw transport so we can probe auth methods and try both ──
    # Old network devices (JUNOS 12.x, Cisco IOS, etc.) often reject the
    # modern KEX/cipher set that paramiko prefers.  Widen the allowed set
    # to include the legacy algorithms those devices advertise.
    _transport_factory = paramiko.Transport((host, port))
    _transport_factory.banner_timeout = timeout
    try:
        _transport_factory.start_client(timeout=timeout)
    except Exception as _conn_err:
        return _fail('ssh', f'SSH connection failed: {_conn_err}')

    # ── Host key verification (TOFU) ──────────────────────────────────
    _hk_err = _verify_host_key(_transport_factory, host, port)
    if _hk_err:
        try:
            _transport_factory.close()
        except Exception:
            pass
        return _fail('ssh', _hk_err)

    # ── Discover which auth methods the server actually accepts ───────
    _allowed: list = []
    try:
        _transport_factory.auth_none(username)
    except paramiko.BadAuthenticationType as _bat:
        _allowed = list(_bat.allowed_types)
    except Exception:
        pass
    log.debug(
        f"Backup SSH: {host} allowed auth methods: {_allowed or '(unknown)'} | "
        f"user={username!r} | pw_len={len(password)}"
    )

    # ── Attempt auth in order: password → keyboard-interactive ────────
    _authed = False
    _last_auth_err = 'Authentication failed'

    # 1. Direct password auth
    if not _authed and ('password' in _allowed or not _allowed):
        try:
            _transport_factory.auth_password(username, password)
            _authed = True
            log.debug(f"Backup SSH: password auth succeeded for {host}")
        except paramiko.AuthenticationException as _e:
            _last_auth_err = str(_e)
            log.debug(f"Backup SSH: password auth failed for {host} — {_e}")
        except Exception as _e:
            _last_auth_err = str(_e)

    # 2. Keyboard-interactive (required by JUNOS, some IOS-XE, FortiGate, etc.)
    if not _authed and ('keyboard-interactive' in _allowed or not _allowed):
        try:
            _transport_factory.auth_interactive_dumb(
                username,
                lambda title, instructions, fields: [password] * len(fields),
            )
            _authed = True
            log.debug(f"Backup SSH: keyboard-interactive auth succeeded for {host}")
        except paramiko.AuthenticationException as _e:
            _last_auth_err = str(_e)
            log.debug(f"Backup SSH: keyboard-interactive auth failed for {host} — {_e}")
        except Exception as _e:
            _last_auth_err = str(_e)

    if not _authed:
        try:
            _transport_factory.close()
        except Exception:
            pass
        return _fail(
            'ssh',
            f'Authentication failed for user {username!r} '
            f'(tried: password, keyboard-interactive; '
            f'server allowed: {_allowed or "unknown"}). '
            f'Last error: {_last_auth_err}',
        )

    # ── Wire authenticated transport into an SSHClient ────────────────
    # Host key was already verified above via _verify_host_key(); RejectPolicy
    # is set here so any unexpected re-connect attempt is denied rather than silently accepted.
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client._transport = _transport_factory

    try:
        # term='dumb' tells the remote device not to send ANSI escape codes,
        # cursor-positioning sequences, or tab-stop initialisation.
        shell = client.invoke_shell(term='dumb', width=512, height=5000)
        shell.settimeout(timeout)
        _drain(shell, timeout)      # clear banner / MOTD

        # Long-command idle window: up to 1/3 of timeout (min 8 s).
        # Network devices (JUNOS, etc.) can pause several seconds after
        # receiving a command before the first line of output appears.
        _cmd_idle = max(8.0, timeout / 3.0)

        # Disable paging (e.g. "terminal length 0" for Cisco,
        # "set cli screen-length 0" for JUNOS)
        if paging:
            shell.send(paging + '\n')
            _drain(shell, timeout, idle_secs=3.0)

        # Enter enable mode if an enable password is set
        if enable:
            shell.send('enable\n')
            time.sleep(0.4)
            shell.send(enable + '\n')
            _drain(shell, timeout, idle_secs=3.0)

        # Run each command and collect output
        parts = []
        for cmd in commands:
            cmd = cmd.strip()
            if not cmd:
                continue
            shell.send(cmd + '\n')
            # Use the long idle window so slow devices have time to start
            # generating output before we give up
            out = _drain(shell, timeout, idle_secs=_cmd_idle)
            parts.append(out)

        client.close()
        config = '\n'.join(parts)
        return _ok('ssh', config)

    except Exception as e:
        try:
            client.close()
        except Exception:
            pass
        msg = str(e)
        log.warning(f"Backup SSH failed for {host}: {msg}")
        return _fail('ssh', msg)


def _drain(shell, timeout: int = 15,
           end_markers: tuple = ('#', '>', '$ ', ']', '%'),
           idle_secs: float = 1.5) -> str:
    """
    Read from an interactive SSH shell until a CLI prompt is detected or
    the timeout expires. Returns accumulated output as a string.

    idle_secs: how long to wait with no new data before giving up.
    Keeping this at 1.5 s prevents cutting off large config outputs from
    slow devices (JUNOS, Cisco) that send data in bursts with brief pauses.
    """
    buf      = ''
    deadline = time.time() + timeout
    idle_end = time.time() + 3.0   # allow 3 s for the first data chunk

    while time.time() < deadline:
        if shell.recv_ready():
            # Large buffer so we capture multi-KB chunks in one read
            chunk = shell.recv(65536).decode('utf-8', errors='replace')
            buf  += chunk
            idle_end = time.time() + idle_secs   # reset idle window on each chunk
            stripped = buf.rstrip('\r\n ')
            if any(stripped.endswith(m) for m in end_markers):
                break
        else:
            if time.time() > idle_end:
                break    # no new data within the idle window — assume done
            time.sleep(0.05)

    # Strip ANSI/VT100 escape sequences that leak through even on dumb terminals
    return _ANSI_RE.sub('', buf)


# ── Telnet ───────────────────────────────────────────────────────────

def _telnet_backup(device: dict, settings: dict) -> dict:
    if not _TELNET_OK:
        return _fail('telnet', 'telnetlib not available in this Python environment')

    import telnetlib as _tl

    host     = device.host
    port     = int(settings.get('port', 23))
    username = settings.get('username', '')
    password = decrypt_pw(settings.get('password_enc', ''))
    timeout  = int(settings.get('timeout', 30))
    paging   = (settings.get('paging_cmd') or '').strip()
    commands = _parse_commands(settings.get('commands', '["show running-config"]'))

    try:
        tn = _tl.Telnet(host, port, timeout)
        data = tn.read_until(b'Username:', timeout)
        if b'Username:' not in data:
            tn.close()
            return _fail('telnet', 'No username prompt received from device')
        tn.write(username.encode() + b'\n')
        data = tn.read_until(b'Password:', timeout)
        if b'Password:' not in data:
            tn.close()
            return _fail('telnet', 'No password prompt received from device')
        tn.write(password.encode() + b'\n')
        time.sleep(1.0)
        tn.read_very_eager()   # drain banner

        if paging:
            tn.write(paging.encode() + b'\n')
            time.sleep(0.5)
            tn.read_very_eager()

        parts = []
        for cmd in commands:
            cmd = cmd.strip()
            if not cmd:
                continue
            tn.write(cmd.encode() + b'\n')
            time.sleep(1.5)
            out = tn.read_very_eager().decode('utf-8', errors='replace')
            parts.append(out)

        tn.close()
        config = '\n'.join(parts)
        return _ok('telnet', config)

    except Exception as e:
        msg = str(e)
        log.warning(f"Backup Telnet failed for {host}: {msg}")
        return _fail('telnet', msg)


# ── Helpers ───────────────────────────────────────────────────────────

def _ok(method: str, config: str) -> dict:
    encoded = config.encode('utf-8', errors='replace')
    return {
        'success':    True,
        'config':     config,
        'error_msg':  '',
        'method':     method,
        'size_bytes': len(encoded),
        'sha256':     hashlib.sha256(encoded).hexdigest(),
        'ts':         datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def _fail(method: str, error_msg: str) -> dict:
    return {
        'success':    False,
        'config':     '',
        'error_msg':  error_msg,
        'method':     method,
        'size_bytes': 0,
        'sha256':     '',
        'ts':         datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def _parse_commands(raw) -> list:
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else [str(v)]
    except Exception:
        return [raw] if raw else ['show running-config']


# ── Shared execution helper (used by manual API and scheduler) ───────

def do_backup(did: str):
    """
    Execute backup for one device and persist the result.
    Called by both the manual API trigger and the background scheduler.
    """
    import core.app_state as _as
    from db.backups import (db_get_backup_settings, db_save_backup_run,
                            db_write_config_file)

    device = _as.STATE.devices.get(did)
    if not device:
        log.warning(f"Backup: device '{did}' not found in state — skipping")
        return

    settings = db_get_backup_settings(did, with_secrets=True)
    if not settings or not settings.get('enabled'):
        log.info(f"Backup: skipping '{did}' — not enabled")
        return

    result = run_backup(device, settings)
    run_id = db_save_backup_run(did, result)

    if result.get('success') and result.get('config'):
        db_write_config_file(did, device.name, result['ts'], result['config'])

    status = 'success' if result.get('success') else 'failed'
    log.info(f"Backup: {status} for {device.name} ({device.host}) — "
             f"{result.get('size_bytes', 0)} bytes")

    _as.STATE._broadcast('backup_complete', {
        'did':        did,
        'run_id':     run_id,
        'success':    result.get('success'),
        'ts':         result.get('ts'),
        'size_bytes': result.get('size_bytes', 0),
        'error_msg':  result.get('error_msg', ''),
    })
