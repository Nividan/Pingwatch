"""
monitoring/syslog_client.py - Non-blocking syslog forwarding client.

Sends PingWatch events (flap_down, flap_recovered, snmp_trap) as RFC 5424
syslog messages to a configured remote server via UDP or TCP.

Design:
- A single daemon queue thread dequeues and sends messages asynchronously.
- The queue is bounded (500 entries); if full, messages are silently dropped
  so the monitor thread is never blocked.
- Settings are re-read on every send - changes take effect without restart.
"""

import datetime
import json
import logging as _logging
import queue
import socket
import threading
import time

from core.logger import log
from core.settings import get as _cfg

# ── Internal queue + worker ────────────────────────────────────────────────
_Q: queue.Queue = queue.Queue(maxsize=500)
_started = False
_start_lock = threading.Lock()

# Connection status tracking (in-memory, resets on restart)
_last_ok_ts: float = 0
_last_err: dict = {'ts': 0.0, 'msg': ''}

# ── Severity maps ─────────────────────────────────────────────────────────
# Syslog facility LOCAL0 = 16; PRI = facility*8 + severity_level
_FACILITY = 16

_SEV_MAP = {
    "critical":  2,   # CRIT
    "down":      4,   # WARNING
    "warning":   4,   # WARNING
    "recovered": 5,   # NOTICE
    "threshold": 6,   # INFO
    "info":      6,   # INFO
}
_SEV_ORDER = {"critical": 0, "warning": 1, "down": 1, "recovered": 2,
              "threshold": 2, "info": 3}


def _event_severity(event_type: str, data: dict) -> str:
    """Derive a severity label from event_type + data fields."""
    if event_type == "flap_down":
        return "down"
    if event_type == "flap_recovered":
        return "recovered"
    if event_type == "snmp_trap":
        return data.get("severity", "warning")
    if event_type in ("threshold_critical",):
        return "critical"
    if event_type in ("threshold_warning",):
        return "warning"
    return "info"


def _above_min(sev: str, min_sev: str) -> bool:
    """Return True if sev is at or above the configured minimum severity."""
    return _SEV_ORDER.get(sev, 99) <= _SEV_ORDER.get(min_sev, 99)


def _format_rfc5424(pri: int, hostname: str, msg: str, app_name: str = 'PingWatch') -> bytes:
    """Format an RFC 5424 syslog message."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"<{pri}>1 {ts} {hostname} {app_name} - - - {msg}"
    return line.encode("utf-8", errors="replace")


def _build_message(event_type: str, data: dict) -> str:
    """Build a human-readable syslog message body."""
    if event_type == "flap_down":
        return (f"[DOWN] {data.get('dname', data.get('host', '?'))}/"
                f"{data.get('sname', '?')} ({data.get('host', '?')}) "
                f"- {data.get('detail', '')}")
    if event_type == "flap_recovered":
        return (f"[RECOVERED] {data.get('dname', data.get('host', '?'))}/"
                f"{data.get('sname', '?')} ({data.get('host', '?')})")
    if event_type == "snmp_trap":
        vendor = data.get("vendor", "")
        trap   = data.get("trap_name") or data.get("trap_oid", "")
        src    = data.get("dname") or data.get("src_ip", "?")
        return (f"[TRAP] {src} {vendor+' ' if vendor else ''}{trap} "
                f"- {data.get('detail', '')}")
    return f"[{event_type.upper()}] {data.get('detail', '')}"


def _send_one(payload: bytes, host: str, port: int, proto: str):
    """Send a single syslog datagram. Raises on error."""
    if proto == "tcp":
        with socket.create_connection((host, port), timeout=3) as s:
            s.sendall(payload + b"\n")
    else:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(3)
            s.sendto(payload, (host, port))


def _worker_loop():
    """Daemon thread - dequeues and sends syslog messages."""
    global _last_ok_ts, _last_err
    while True:
        try:
            payload, host, port, proto = _Q.get(timeout=5)
        except queue.Empty:
            continue
        try:
            _send_one(payload, host, port, proto)
            _last_ok_ts = time.time()
        except Exception as e:
            _last_err = {'ts': time.time(), 'msg': str(e)[:200]}
            log.warning(f"Syslog send failed ({host}:{port}/{proto}): {e}")
        finally:
            _Q.task_done()


def _ensure_started():
    global _started
    if _started:
        return
    with _start_lock:
        if not _started:
            t = threading.Thread(target=_worker_loop, daemon=True, name="syslog-worker")
            t.start()
            _started = True


def _reload() -> dict:
    """Return current syslog settings from the live settings cache."""
    return {
        "enabled":      str(_cfg("syslog_enabled", "0")).strip() == "1",
        "host":         str(_cfg("syslog_host",    "")).strip(),
        "port":         int(_cfg("syslog_port",    514) or 514),
        "proto":        str(_cfg("syslog_proto",   "udp")).strip().lower(),
        "min_severity": str(_cfg("syslog_min_severity", "warning")).strip(),
    }


# ── Public API ────────────────────────────────────────────────────────────

def syslog_send(event_type: str, data: dict) -> None:
    """
    Enqueue a syslog message for the given event, if forwarding is enabled
    and the event meets the minimum severity threshold.

    Non-blocking. Called from MonitorState._broadcast().
    """
    cfg = _reload()
    if not cfg["enabled"] or not cfg["host"]:
        return

    sev = _event_severity(event_type, data)
    if not _above_min(sev, cfg["min_severity"]):
        return

    try:
        _ensure_started()
        pri     = _FACILITY * 8 + _SEV_MAP.get(sev, 6)
        hostname = socket.gethostname()
        msg      = _build_message(event_type, data)
        payload  = _format_rfc5424(pri, hostname, msg)
        _Q.put_nowait((payload, cfg["host"], cfg["port"], cfg["proto"]))
    except queue.Full:
        pass   # drop silently - never block the caller
    except Exception:
        pass   # never let syslog errors propagate


def send_test_syslog() -> tuple:
    """
    Send a test syslog message using current settings.
    Returns (ok: bool, message: str).
    """
    global _last_ok_ts, _last_err
    cfg = _reload()
    if not cfg["host"]:
        return False, "Syslog host is not configured."
    try:
        pri      = _FACILITY * 8 + 6   # INFO
        hostname = socket.gethostname()
        payload  = _format_rfc5424(pri, hostname,
                                   "PingWatch test message - syslog forwarding is working.")
        _send_one(payload, cfg["host"], cfg["port"], cfg["proto"])
        _last_ok_ts = time.time()
        return True, f"Test message sent to {cfg['host']}:{cfg['port']}/{cfg['proto'].upper()}"
    except Exception as e:
        _last_err = {'ts': time.time(), 'msg': str(e)[:200]}
        return False, str(e)


def get_syslog_status() -> dict:
    """Return connection status dict for the Settings API."""
    enabled = str(_cfg('syslog_enabled', '0')).strip() == '1'
    host    = str(_cfg('syslog_host', '')).strip()
    if not enabled or not host:
        state = 'unconfigured'
    elif _last_err['ts'] and (not _last_ok_ts or _last_err['ts'] > _last_ok_ts):
        state = 'error'
    elif _last_ok_ts:
        state = 'ok'
    else:
        state = 'configured'   # enabled+host set but nothing sent yet
    return {
        'state':        state,
        'last_ok_ts':   _last_ok_ts or None,
        'last_err_ts':  _last_err['ts'] or None,
        'last_err_msg': _last_err['msg'],
    }


# ── Application log forwarding ────────────────────────────────────────────

class SyslogAppLogHandler(_logging.Handler):
    """Forwards Python log records to the configured syslog server.

    Attached once at startup to the app/audit/backup loggers.
    All settings are read on every emit — no restart needed for changes.
    Uses facility LOCAL1 (17) to distinguish from sensor alert events (LOCAL0).
    """

    _FACILITY = 17  # LOCAL1
    _PY_TO_SEV = {
        _logging.DEBUG:    7,  # DEBUG
        _logging.INFO:     6,  # INFO
        _logging.WARNING:  4,  # WARNING
        _logging.ERROR:    3,  # ERR
        _logging.CRITICAL: 2,  # CRIT
    }
    _LEVEL_MAP = {
        'debug':   _logging.DEBUG,
        'info':    _logging.INFO,
        'warning': _logging.WARNING,
        'error':   _logging.ERROR,
    }

    def __init__(self, source_key: str):
        super().__init__()
        self.source_key = source_key  # 'app', 'audit', or 'backup'

    def emit(self, record: _logging.LogRecord) -> None:
        try:
            if not int(_cfg('syslog_app_logs', 0) or 0):
                return
            if not int(_cfg('syslog_enabled', 0) or 0):
                return
            host = str(_cfg('syslog_host', '')).strip()
            if not host:
                return
            # Source filter
            try:
                sources = json.loads(
                    _cfg('syslog_app_log_sources', '["app","audit","backup"]') or
                    '["app","audit","backup"]'
                )
            except Exception:
                sources = ['app', 'audit', 'backup']
            if self.source_key not in sources:
                return
            # Level filter
            min_level_str = str(_cfg('syslog_app_log_level', 'info') or 'info').lower()
            min_level = self._LEVEL_MAP.get(min_level_str, _logging.INFO)
            if record.levelno < min_level:
                return
            port  = int(_cfg('syslog_port', 514) or 514)
            proto = str(_cfg('syslog_proto', 'udp')).strip().lower()
            _ensure_started()
            sev     = self._PY_TO_SEV.get(record.levelno, 6)
            pri     = self._FACILITY * 8 + sev
            hostname = socket.gethostname()
            msg     = f"[{record.levelname}] {self.format(record)}"
            payload = _format_rfc5424(pri, hostname, msg,
                                      app_name=f'pingwatch-{self.source_key}')
            _Q.put_nowait((payload, host, port, proto))
        except Exception:
            pass  # logging handlers must never raise


def _attach_app_log_handlers() -> None:
    """Attach SyslogAppLogHandler to each app logger. Called once at startup."""
    from core.logger import log as _al, log_audit as _aul, log_backup as _abl
    for lgr, key in [(_al, 'app'), (_aul, 'audit'), (_abl, 'backup')]:
        if not any(isinstance(h, SyslogAppLogHandler) for h in lgr.handlers):
            lgr.addHandler(SyslogAppLogHandler(key))
