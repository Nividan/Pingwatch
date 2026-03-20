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


def _should_fire(last_fired, freq: str, time_str: str, days_str: str) -> bool:
    """
    Return True if:
    - Current HH:MM matches time_str
    - For weekly: current weekday is in days_str
    - We haven't fired within the last 90 seconds (prevents double-fire)
    """
    now = datetime.datetime.now()
    try:
        h, m = map(int, time_str.split(':'))
    except Exception:
        log.warning(f"Backup scheduler: invalid time_str {time_str!r}")
        return False

    if now.hour != h or now.minute != m:
        return False

    # Time window matched — log so we know the scheduler reached this point
    log.debug(f"Backup scheduler: time matched {time_str!r}, checking other conditions")

    # Avoid double-fire within the same minute
    if last_fired and (now - last_fired).total_seconds() < 90:
        log.debug("Backup scheduler: suppressed (fired within last 90 s)")
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
            log.debug(f"Backup scheduler: today={today} not in scheduled days={days}")
            return False

    return True


def _scheduler_loop():
    from core.settings import get as _cfg
    from db.backups import db_get_backup_list
    from .engine import do_backup

    log.info("Backup scheduler started")
    last_fired = None
    _poll = 0  # poll counter — used to emit a periodic heartbeat

    while True:
        try:
            time.sleep(30)   # check every 30 seconds
            _poll += 1

            enabled  = int(_cfg('backup_sched_enabled', 0))
            freq     = str(_cfg('backup_sched_freq',  'daily'))
            time_str = str(_cfg('backup_sched_time',  '02:00'))
            days_str = str(_cfg('backup_sched_days',  '1,2,3,4,5,6,7'))

            # Heartbeat every ~30 min so we can confirm the scheduler is alive
            if _poll % 60 == 0:
                log.debug(
                    f"Scheduler heartbeat — enabled={enabled} freq={freq!r} "
                    f"time={time_str!r} days={days_str!r} "
                    f"last_fired={last_fired.strftime('%H:%M:%S') if last_fired else 'never'}"
                )

            if not enabled:
                continue

            if not _should_fire(last_fired, freq, time_str, days_str):
                continue

            last_fired = datetime.datetime.now()

            devices = db_get_backup_list()
            scheduled = [d for d in devices
                         if d.get('in_schedule') and d.get('enabled')]
            if not scheduled:
                log.info("Backup scheduler: no devices with 'Add to schedule' enabled")
                continue

            log.info(f"Backup scheduler firing — {len(scheduled)} device(s)")

            def _run_backup(device_id):
                try:
                    do_backup(device_id)
                except Exception as e:
                    log.error(f"Scheduled backup crashed for device {device_id!r}: {e}", exc_info=True)

            for dev in scheduled:
                t = threading.Thread(
                    target=_run_backup,
                    args=(dev['did'],),
                    daemon=True,
                    name=f"sched-bk-{dev['did']}",
                )
                t.start()
                time.sleep(1)   # 1-second stagger to avoid connection storms

        except Exception as e:
            log.error(f"Backup scheduler error: {e}")


def start_scheduler():
    """Start the background scheduler thread. Call once from server.py at startup."""
    t = threading.Thread(target=_scheduler_loop, daemon=True, name='backup-scheduler')
    t.start()
