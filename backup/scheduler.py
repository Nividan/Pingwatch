"""
backup/scheduler.py — Global backup scheduler background thread.

Reads global schedule settings from app_settings every 30 seconds,
and triggers do_backup() for all devices with in_schedule=True when
the configured time and day conditions are met.
"""

import datetime
import threading
import time

from core.logger import log_backup as log


_LAST_TS_FMT = '%Y-%m-%d_%H-%M-%S'


def _should_fire(last_fired, freq: str, time_str: str, days_str: str) -> bool:
    """
    Catch-up semantics: fire if today's scheduled slot has passed and we haven't
    fired since that slot. This survives restarts, thread stalls, and missed
    minute windows — any poll after the slot on a scheduled day triggers once.
    """
    now = datetime.datetime.now()
    try:
        h, m = map(int, time_str.split(':'))
    except Exception:
        log.warning(f"Backup scheduler: invalid time_str {time_str!r}")
        return False

    if freq == 'weekly':
        # days_str: "1,2,3,4,5,6,7"  Mon=1 … Sun=7
        try:
            days = {int(d) for d in days_str.split(',') if d.strip()}
        except Exception:
            log.warning(f"Backup scheduler: invalid days_str {days_str!r}")
            return False
        today = now.weekday() + 1  # weekday() returns 0=Mon; we use 1=Mon…7=Sun
        if today not in days:
            return False

    slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if now < slot:
        return False

    # Fire if we haven't fired since today's scheduled slot
    if last_fired is None or last_fired < slot:
        return True

    return False


def _load_last_ts(key: str):
    """Load a persisted '%Y-%m-%d_%H-%M-%S' timestamp from settings; None if absent/invalid."""
    try:
        from core.settings import get as _cfg
        ts = str(_cfg(key, '') or '')
        if not ts:
            return None
        return datetime.datetime.strptime(ts, _LAST_TS_FMT)
    except Exception:
        return None


def _save_last_ts(key: str, dt: datetime.datetime) -> None:
    """Persist a last-fired timestamp to settings (best-effort, non-blocking).

    Failures are logged at WARNING (not DEBUG) — a silent save miss here
    means the next reboot replays the last scheduled slot, which is hard
    to diagnose without the log line. The two callers tolerate failure,
    so we don't re-raise.
    """
    try:
        from core.settings import load as _sl
        from db import _db_enqueue, db_save_settings
        data = {key: dt.strftime(_LAST_TS_FMT)}
        _sl(data)
        _db_enqueue(lambda d=data: db_save_settings(d))
    except Exception as e:
        log.warning(f"Backup scheduler: could not persist {key}: {e}")


_stop = threading.Event()


def _scheduler_loop():
    from core.settings import get as _cfg
    from db.backups import db_get_backup_list
    from .engine import do_backup

    # Restore last-fired timestamps so catch-up survives restarts
    last_fired    = _load_last_ts('backup_sched_last_ts')
    last_db_fired = _load_last_ts('db_backup_last_ts')
    log.info(
        f"Backup scheduler started — last_fired={last_fired.strftime(_LAST_TS_FMT) if last_fired else 'never'}, "
        f"last_db_fired={last_db_fired.strftime(_LAST_TS_FMT) if last_db_fired else 'never'}"
    )
    _poll = 0  # poll counter — used to emit a periodic heartbeat

    while not _stop.is_set():
        try:
            # Interruptible sleep so shutdown short-circuits within seconds
            # instead of letting the loop wake up after pg_close_pool() and
            # spam 'NoneType has no attribute getconn' errors.
            if _stop.wait(30):
                break
            _poll += 1

            # ── Device config backup ──────────────────────────────────────
            enabled  = int(_cfg('backup_sched_enabled', 0))
            freq     = str(_cfg('backup_sched_freq',  'daily'))
            time_str = str(_cfg('backup_sched_time',  '02:00'))
            days_str = str(_cfg('backup_sched_days',  '1,2,3,4,5,6,7'))

            # ── Database backup settings ──────────────────────────────────
            db_en   = int(_cfg('db_backup_enabled', 0) or 0)
            db_freq = str(_cfg('db_backup_freq',  'daily'))
            db_time = str(_cfg('db_backup_time',  '03:00'))
            db_days = str(_cfg('db_backup_days',  '1,2,3,4,5,6,7'))

            # Heartbeat every ~30 min so we can confirm the scheduler is alive
            if _poll % 60 == 0:
                log.debug(
                    f"Scheduler heartbeat — cfg_enabled={enabled} cfg_freq={freq!r} "
                    f"cfg_time={time_str!r} "
                    f"db_enabled={db_en} db_freq={db_freq!r} db_time={db_time!r} "
                    f"last_fired={last_fired.strftime('%H:%M:%S') if last_fired else 'never'} "
                    f"last_db_fired={last_db_fired.strftime('%H:%M:%S') if last_db_fired else 'never'}"
                )

            # ── Fire device config backups ────────────────────────────────
            if enabled and _should_fire(last_fired, freq, time_str, days_str):
                _first_save = (last_fired is None)
                last_fired = datetime.datetime.now()
                _save_last_ts('backup_sched_last_ts', last_fired)
                if _first_save:
                    log.info(f"Backup scheduler: first device-backup timestamp recorded "
                             f"({last_fired.strftime(_LAST_TS_FMT)}) — "
                             f"previous value was missing; restarts will now show this date")

                devices = db_get_backup_list()
                scheduled = [d for d in devices
                             if d.get('in_schedule') and d.get('enabled')]
                if not scheduled:
                    log.info("Backup scheduler: no devices with 'Add to schedule' enabled")
                else:
                    log.info(f"Backup scheduler firing — {len(scheduled)} device(s)")

                    def _run_backup(device_id):
                        try:
                            do_backup(device_id)
                        except Exception as e:
                            log.error(f"Scheduled backup crashed for device {device_id!r}: {e}", exc_info=True)

                    for dev in scheduled:
                        if _stop.is_set():
                            break
                        t = threading.Thread(
                            target=_run_backup,
                            args=(dev['did'],),
                            daemon=True,
                            name=f"sched-bk-{dev['did']}",
                        )
                        t.start()
                        if _stop.wait(1):   # 1-second stagger, interruptible
                            break

            # ── Fire database backup ──────────────────────────────────────
            if db_en and _should_fire(last_db_fired, db_freq, db_time, db_days):
                _first_db_save = (last_db_fired is None)
                last_db_fired = datetime.datetime.now()
                _save_last_ts('db_backup_last_ts', last_db_fired)
                if _first_db_save:
                    log.info(f"Backup scheduler: first DB-backup timestamp recorded "
                             f"({last_db_fired.strftime(_LAST_TS_FMT)}) — "
                             f"previous value was missing; restarts will now show this date")
                log.info("Backup scheduler: firing database backup")
                from .db_backup import do_db_backup
                threading.Thread(target=do_db_backup, daemon=True, name='sched-db-bk').start()

        except Exception as e:
            log.error(f"Backup scheduler error: {e}")
    log.info("Backup scheduler stopped")


def start_scheduler():
    """Start the background scheduler thread. Call once from server.py at startup."""
    t = threading.Thread(target=_scheduler_loop, daemon=True, name='backup-scheduler')
    t.start()


def stop_scheduler() -> None:
    """Signal the backup scheduler loop to exit (call at shutdown before pg_close_pool)."""
    _stop.set()
