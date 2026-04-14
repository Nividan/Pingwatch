"""
logger.py — Central logging configuration for PingWatch.

Writes to both the console and a rotating log file (pingwatch.log).
All modules import `log` from here instead of using print().

Logging level policy
--------------------
ERROR     Operation failed, needs attention (DB write failure, TLS cert
          load error, port bind failure).
WARNING   Unexpected but non-fatal; investigate when convenient (LDAP user
          matched no group, webhook delivery failed, rate-limit triggered).
INFO      Significant operational events visible in normal production
          (login success/failure, alert fired, service start/stop, DB
          loaded N devices, debug mode toggled).
DEBUG     Diagnostic detail, only visible when debug mode is enabled (LDAP
          connection steps, memberOf list, alert engine decision path,
          sample flush counts).

Never log passwords, tokens, session IDs, or secrets at any level.
Never use log.debug() in per-probe tight loops — use log_sensors for
sensor-specific events.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_DIR = os.path.join(_ROOT, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_PATH = os.path.join(_LOG_DIR, "pingwatch.log")

_fmt = logging.Formatter(
    "%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

log = logging.getLogger("pingwatch")
log.setLevel(logging.DEBUG)

# ── Rotating file handler (10 MB × 5 backups) — INFO by default ───────────
_fh = RotatingFileHandler(_LOG_PATH, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
_fh.setFormatter(_fmt)
_fh.setLevel(logging.INFO)
log.addHandler(_fh)

# ── Console handler — INFO and above only (skipped without a console) ──────
_ch = None
if sys.stderr is not None:
    _ch = logging.StreamHandler(sys.stderr)
    _ch.setFormatter(_fmt)
    _ch.setLevel(logging.INFO)
    log.addHandler(_ch)

# ── Sensor state logger → logs/pingwatchsensors.log ──────────────────────
log_sensors = logging.getLogger("pingwatch.sensors")
log_sensors.setLevel(logging.INFO)
log_sensors.propagate = False   # don't bubble up to the main pingwatch logger
_sh = RotatingFileHandler(
    os.path.join(_LOG_DIR, "pingwatchsensors.log"),
    maxBytes=20_000_000, backupCount=5, encoding="utf-8"
)
_sh.setFormatter(_fmt)
log_sensors.addHandler(_sh)

# ── Audit logger → logs/pingwatchaudit.log ────────────────────────────────
log_audit = logging.getLogger("pingwatch.audit")
log_audit.setLevel(logging.INFO)
log_audit.propagate = False     # don't bubble up to the main pingwatch logger
_ah = RotatingFileHandler(
    os.path.join(_LOG_DIR, "pingwatchaudit.log"),
    maxBytes=10_000_000, backupCount=10, encoding="utf-8"
)
_ah.setFormatter(_fmt)
log_audit.addHandler(_ah)

# ── Backup logger → logs/pingwatchbackup.log ──────────────────────────────
log_backup = logging.getLogger("pingwatch.backup")
log_backup.setLevel(logging.DEBUG)
log_backup.propagate = False    # keep backup messages out of pingwatch.log
_bkh = RotatingFileHandler(
    os.path.join(_LOG_DIR, "pingwatchbackup.log"),
    maxBytes=5_000_000, backupCount=5, encoding="utf-8"
)
_bkh.setFormatter(_fmt)
_bkh.setLevel(logging.INFO)
log_backup.addHandler(_bkh)
# propagate=False ensures no logger bleeds into pingwatch.log or log_buffer.

# ── In-memory ring buffer (consumed by the status GUI) ────────────────────
import collections

class _MemoryHandler(logging.Handler):
    def __init__(self, maxlines=500):
        super().__init__()
        self.setFormatter(_fmt)
        self.lines = collections.deque(maxlen=maxlines)
    def emit(self, record):
        try: self.lines.append(self.format(record))
        except Exception: pass

log_buffer = _MemoryHandler()
log.addHandler(log_buffer)

# ── Badge counter (drives the "New Log Entries" status-bar badge) ────────
_badge_total = 0

class _BadgeHandler(logging.Handler):
    """Counts WARNING+ records and pushes SSE updates to the frontend."""
    def __init__(self):
        super().__init__(level=logging.WARNING)
    def emit(self, record):
        global _badge_total
        _badge_total += 1
        try:
            from core.app_state import STATE
            if hasattr(STATE, '_broadcast'):
                STATE._broadcast("log_badge", {"total": _badge_total})
        except Exception:
            pass

log.addHandler(_BadgeHandler())

def get_badge_total() -> int:
    return _badge_total

# ── Debug mode toggle ─────────────────────────────────────────────────────
def set_debug_mode(enabled: bool):
    """Switch file + console handlers between DEBUG and INFO level.

    Called at startup from server.py and at runtime from settings PATCH.
    Sensor and audit loggers are unaffected (always INFO).
    Only logs when the level actually changes (suppresses spurious startup/save noise).
    """
    lvl = logging.DEBUG if enabled else logging.INFO
    changed = (_fh.level != lvl)
    _fh.setLevel(lvl)
    _bkh.setLevel(lvl)
    log_buffer.setLevel(lvl)
    if _ch is not None:
        _ch.setLevel(lvl)
    if changed:
        log.info(f"Debug mode {'enabled' if enabled else 'disabled'}")


# ── Public map consumed by the log-viewer API (/api/logs/{key}) ───────────
LOG_FILES = {
    'app':     _LOG_PATH,
    'sensors': os.path.join(_LOG_DIR, 'pingwatchsensors.log'),
    'audit':   os.path.join(_LOG_DIR, 'pingwatchaudit.log'),
    'backup':  os.path.join(_LOG_DIR, 'pingwatchbackup.log'),
}
