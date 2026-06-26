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

import gzip
import logging
import os
import re
import shutil
import sys
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

from core.config import DATA_ROOT

_LOG_DIR = os.path.join(DATA_ROOT, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_PATH = os.path.join(_LOG_DIR, "pingwatch.log")

_fmt = logging.Formatter(
    "%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

log = logging.getLogger("pingwatch")
log.setLevel(logging.DEBUG)

# ── Rotating file handler (10 MB × 14 backups) — INFO by default ──────────
_fh = RotatingFileHandler(_LOG_PATH, maxBytes=10_000_000, backupCount=14, encoding="utf-8")
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

# ── Audit logger → logs/audit/pingwatchaudit.log ──────────────────────────
# Compliance-oriented retention: weekly rotation, ~1 year of history. Audit is
# low-volume enough that size-based rotation would give unpredictable
# coverage; time-based rotation guarantees calendar coverage. Weekly (rather
# than daily) rotation keeps the rollover count low — ~52 files/year instead
# of ~365 — which is why retention is expressed in days but applied as weeks.
#
# The weekly rollovers live in their own logs/audit/ subfolder (so they don't
# bury the handful of active logs in logs/) and are gzip-compressed on
# rotation (a year of audit is ~1 MB raw, far less compressed). The active
# file stays plain text so the log viewer / support bundle can tail it.
_AUDIT_DIR = os.path.join(_LOG_DIR, "audit")
os.makedirs(_AUDIT_DIR, exist_ok=True)
_AUDIT_PATH = os.path.join(_AUDIT_DIR, "pingwatchaudit.log")

# backupCount pruning must still recognise the gzipped rollovers. We pin the
# un-anchored, lookaround form of the date matcher (the modern stdlib default):
#   - Python 3.9+ calls ``extMatch.search(full_filename)`` in the namer branch,
#     which an ``^…$``-anchored pattern would never satisfy (the name starts
#     with "pingwatchaudit.log."), so the .gz backups would leak forever.
#   - Python 3.8 calls ``extMatch.match(suffix)`` on the already-stripped
#     suffix ("2026-06-26.gz"); ``match`` only anchors at the start, so the
#     trailing ".gz" is fine and the file is still pruned.
# One pattern, correct on every supported version.
_AUDIT_EXTMATCH = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", re.ASCII)


def _gz_namer(name):
    """Rolled audit file gets a .gz suffix (the rotator does the compressing)."""
    return name + ".gz"


def _gz_rotator(source, dest):
    """Compress the just-closed daily file into dest, then drop the original."""
    with open(source, "rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(source)


def _apply_gz_rotation(handler):
    handler.namer = _gz_namer
    handler.rotator = _gz_rotator
    handler.extMatch = _AUDIT_EXTMATCH
    return handler


def _audit_weeks(days):
    """Convert a 'days of history' retention into a weekly backup count.

    The audit log rotates weekly, so a configured retention of N days keeps
    ~N/7 rollovers. 365 → 52 (~1 year). Always at least one backup.
    """
    try:
        d = int(days)
    except (TypeError, ValueError):
        d = 365
    return max(1, round(d / 7))


def _migrate_audit_logs():
    """One-time: relocate legacy logs/pingwatchaudit.log* into logs/audit/.

    Runs before the handler opens its file so the active log keeps its history.
    Dated backups are gzipped on the way in; the active file is moved as-is so
    the new handler appends to it. Idempotent and best-effort — a partially
    converted folder (already-moved or already-gzipped files) is left alone.
    """
    try:
        names = os.listdir(_LOG_DIR)
    except OSError:
        return
    for fn in names:
        if not fn.startswith("pingwatchaudit.log"):
            continue
        src = os.path.join(_LOG_DIR, fn)
        if not os.path.isfile(src):
            continue
        try:
            if fn == "pingwatchaudit.log":
                if not os.path.exists(_AUDIT_PATH):
                    shutil.move(src, _AUDIT_PATH)
            elif fn.endswith(".gz"):
                dst = os.path.join(_AUDIT_DIR, fn)
                if not os.path.exists(dst):
                    shutil.move(src, dst)
            else:
                dst = os.path.join(_AUDIT_DIR, fn + ".gz")
                if not os.path.exists(dst):
                    with open(src, "rb") as f_in, gzip.open(dst, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                os.remove(src)
        except OSError:
            pass


_migrate_audit_logs()

log_audit = logging.getLogger("pingwatch.audit")
log_audit.setLevel(logging.INFO)
log_audit.propagate = False     # don't bubble up to the main pingwatch logger
_ah = TimedRotatingFileHandler(
    _AUDIT_PATH, when="W0", interval=1,
    backupCount=_audit_weeks(365), encoding="utf-8"
)
_ah.setFormatter(_fmt)
_apply_gz_rotation(_ah)
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

# ── Probes logger → logs/pingwatchprobes.log (v1.3) ───────────────────────
# Dedicated stream for distributed-probe connectivity and lifecycle:
# enrollments, checkin transport problems, rejected results, offline/online
# transitions, task dispatch — everything an operator needs when a branch
# agent misbehaves, without digging through the main application log.
log_probes = logging.getLogger("pingwatch.probes")
log_probes.setLevel(logging.DEBUG)
log_probes.propagate = False    # keep probe messages out of pingwatch.log
_prh = RotatingFileHandler(
    os.path.join(_LOG_DIR, "pingwatchprobes.log"),
    maxBytes=5_000_000, backupCount=5, encoding="utf-8"
)
_prh.setFormatter(_fmt)
_prh.setLevel(logging.INFO)
log_probes.addHandler(_prh)

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
    _prh.setLevel(lvl)
    log_buffer.setLevel(lvl)
    if _ch is not None:
        _ch.setLevel(lvl)
    if changed:
        log.info(f"Debug mode {'enabled' if enabled else 'disabled'}")


# ── Runtime handler swap from app_settings ───────────────────────────────
def reconfigure_from_settings():
    """Re-instantiate file handlers using user-configured sizes/counts.

    Called once by server.py after core.settings.load() has populated the
    in-memory cache from app_settings. Values out of range fall back to the
    import-time defaults. No-op when the configured values match what's
    already running, so normal startups don't churn file handles.
    """
    global _fh, _sh, _ah, _bkh, _prh
    try:
        import core.settings as _settings_mod
    except Exception:
        return

    def _int(key, default, lo, hi):
        try:
            v = int(_settings_mod.get(key, default) or default)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    def _swap_size(logger_obj, old_handler, path, max_mb, backups):
        new_h = RotatingFileHandler(
            path, maxBytes=max_mb * 1_000_000,
            backupCount=backups, encoding="utf-8"
        )
        new_h.setFormatter(_fmt)
        new_h.setLevel(old_handler.level)
        logger_obj.addHandler(new_h)
        try:
            logger_obj.removeHandler(old_handler)
            old_handler.close()
        except Exception:
            pass
        return new_h

    def _swap_time(logger_obj, old_handler, path, days):
        new_h = TimedRotatingFileHandler(
            path, when="W0", interval=1,
            backupCount=_audit_weeks(days), encoding="utf-8"
        )
        new_h.setFormatter(_fmt)
        new_h.setLevel(old_handler.level)
        _apply_gz_rotation(new_h)   # keep subfolder gzip behaviour after a swap
        logger_obj.addHandler(new_h)
        try:
            logger_obj.removeHandler(old_handler)
            old_handler.close()
        except Exception:
            pass
        return new_h

    # Bounds match the validators in routes/settings.py
    main_mb  = _int("log_main_max_mb",    10,  1, 500)
    main_bk  = _int("log_main_backups",   14,  1, 100)
    sens_mb  = _int("log_sensors_max_mb", 20,  1, 500)
    sens_bk  = _int("log_sensors_backups", 5,  1, 100)
    audit_dy = _int("log_audit_days",    365,  7, 3650)
    bkup_mb  = _int("log_backup_max_mb",   5,  1, 500)
    bkup_bk  = _int("log_backup_backups",  5,  1, 100)
    prb_mb   = _int("log_probes_max_mb",   5,  1, 500)
    prb_bk   = _int("log_probes_backups",  5,  1, 100)

    if _fh.maxBytes != main_mb * 1_000_000 or _fh.backupCount != main_bk:
        _fh = _swap_size(log, _fh, _LOG_PATH, main_mb, main_bk)
    if _sh.maxBytes != sens_mb * 1_000_000 or _sh.backupCount != sens_bk:
        _sh = _swap_size(log_sensors, _sh,
                         os.path.join(_LOG_DIR, "pingwatchsensors.log"),
                         sens_mb, sens_bk)
    if _ah.backupCount != _audit_weeks(audit_dy):
        _ah = _swap_time(log_audit, _ah, _AUDIT_PATH, audit_dy)
    if _bkh.maxBytes != bkup_mb * 1_000_000 or _bkh.backupCount != bkup_bk:
        _bkh = _swap_size(log_backup, _bkh,
                          os.path.join(_LOG_DIR, "pingwatchbackup.log"),
                          bkup_mb, bkup_bk)
    if _prh.maxBytes != prb_mb * 1_000_000 or _prh.backupCount != prb_bk:
        _prh = _swap_size(log_probes, _prh,
                          os.path.join(_LOG_DIR, "pingwatchprobes.log"),
                          prb_mb, prb_bk)


# ── Public map consumed by the log-viewer API (/api/logs/{key}) ───────────
LOG_FILES = {
    'app':     _LOG_PATH,
    'sensors': os.path.join(_LOG_DIR, 'pingwatchsensors.log'),
    'audit':   _AUDIT_PATH,
    'backup':  os.path.join(_LOG_DIR, 'pingwatchbackup.log'),
    'probes':  os.path.join(_LOG_DIR, 'pingwatchprobes.log'),
}
