"""
backup_scheduler.py — Global backup scheduler background thread.

Reads global schedule settings from app_settings every 30 seconds,
and triggers do_backup() for all devices with in_schedule=True when
the configured time and day conditions are met.
"""

import datetime
import threading
import time

from logger import log_backup as log


def _should_fire(last_fired: datetime.datetime | None,
                 freq: str, time_str: str, days_str: str) -> bool:
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
        return False

    if now.hour != h or now.minute != m:
        return False

    # Avoid double-fire within the same minute
    if last_fired and (now - last_fired).total_seconds() < 90:
        return False

    if freq == 'weekly':
        # days_str: "1,2,3,4,5,6,7"  Mon=1 … Sun=7
        try:
            days = {int(d) for d in days_str.split(',') if d.strip()}
        except Exception:
            return False
        if (now.weekday() + 1) not in days:   # weekday() returns 0=Mon
            return False

    return True


def _scheduler_loop():
    from settings import get as _cfg
    from db.backups import db_get_backup_list
    from backup_engine import do_backup

    log.info("Backup scheduler started")
    last_fired: datetime.datetime | None = None

    while True:
        time.sleep(30)   # check every 30 seconds
        try:
            if not int(_cfg('backup_sched_enabled', 0)):
                continue

            freq     = str(_cfg('backup_sched_freq',  'daily'))
            time_str = str(_cfg('backup_sched_time',  '02:00'))
            days_str = str(_cfg('backup_sched_days',  '1,2,3,4,5,6,7'))

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
