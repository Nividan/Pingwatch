"""
logger.py — Central logging configuration for PingWatch.

Writes to both the console and a rotating log file (pingwatch.log).
All modules import `log` from here instead of using print().
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

# ── Rotating file handler (1 MB × 3 backups) — INFO by default ────────────
_fh = RotatingFileHandler(_LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
_fh.setFormatter(_fmt)
_fh.setLevel(logging.INFO)
log.addHandler(_fh)

# ── Console handler — INFO and above only (skipped without a console) ──────
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
    maxBytes=2_000_000, backupCount=3, encoding="utf-8"
)
_sh.setFormatter(_fmt)
log_sensors.addHandler(_sh)

# ── Audit logger → logs/pingwatchaudit.log ────────────────────────────────
log_audit = logging.getLogger("pingwatch.audit")
log_audit.setLevel(logging.INFO)
log_audit.propagate = False     # don't bubble up to the main pingwatch logger
_ah = RotatingFileHandler(
    os.path.join(_LOG_DIR, "pingwatchaudit.log"),
    maxBytes=5_000_000, backupCount=5, encoding="utf-8"
)
_ah.setFormatter(_fmt)
log_audit.addHandler(_ah)

# ── Backup logger → logs/pingwatchbackup.log ──────────────────────────────
log_backup = logging.getLogger("pingwatch.backup")
log_backup.setLevel(logging.DEBUG)
log_backup.propagate = False    # keep backup messages out of pingwatch.log
_bkh = RotatingFileHandler(
    os.path.join(_LOG_DIR, "pingwatchbackup.log"),
    maxBytes=2_000_000, backupCount=3, encoding="utf-8"
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

# ── Debug mode toggle ─────────────────────────────────────────────────────
def set_debug_mode(enabled: bool):
    """Switch file handlers between DEBUG and INFO level.

    Called at startup from server.py and at runtime from settings PATCH.
    Sensor and audit loggers are unaffected (always INFO).
    """
    lvl = logging.DEBUG if enabled else logging.INFO
    _fh.setLevel(lvl)
    _bkh.setLevel(lvl)
    log_buffer.setLevel(lvl)


# ── Public map consumed by the log-viewer API (/api/logs/{key}) ───────────
LOG_FILES = {
    'app':     _LOG_PATH,
    'sensors': os.path.join(_LOG_DIR, 'pingwatchsensors.log'),
    'audit':   os.path.join(_LOG_DIR, 'pingwatchaudit.log'),
    'backup':  os.path.join(_LOG_DIR, 'pingwatchbackup.log'),
}
