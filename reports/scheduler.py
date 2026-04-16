"""
reports/scheduler.py — Background thread that fires scheduled reports.

Mirrors the pattern in backup/scheduler.py: wake every 30 s, inspect enabled
schedules, decide which ones must fire now (based on their freq / time_str /
day_of_* fields). On fire, delegate to reports.runner.run_schedule().
"""

import datetime
import threading
import time

from core.logger import log


def _matches_schedule(sch: dict, now_dt: datetime.datetime) -> bool:
    """Return True if the schedule's cadence + time of day matches 'now'."""
    try:
        h, m = map(int, (sch.get("time_str") or "03:00").split(":"))
    except Exception:
        log.warning(f"reports.scheduler bad time_str: {sch.get('time_str')!r}")
        return False

    if now_dt.hour != h or now_dt.minute != m:
        return False

    freq = (sch.get("freq") or "monthly").lower()

    if freq == "daily":
        return True

    if freq == "weekly":
        days_str = sch.get("day_of_week") or "1"
        try:
            days = {int(d) for d in str(days_str).split(",") if d.strip()}
        except Exception:
            return False
        return (now_dt.weekday() + 1) in days   # 1=Mon … 7=Sun

    if freq == "monthly":
        try:
            dom = int(sch.get("day_of_month") or 1)
        except Exception:
            dom = 1
        return now_dt.day == dom

    if freq == "quarterly":
        # Fire on day_of_month of the first month of each quarter
        if now_dt.month not in (1, 4, 7, 10):
            return False
        try:
            dom = int(sch.get("day_of_month") or 1)
        except Exception:
            dom = 1
        return now_dt.day == dom

    return False


def _scheduler_loop():
    from db import db_list_report_schedules, db_record_schedule_run
    from reports.runner import run_schedule

    log.info("Report scheduler started")
    last_fired: dict = {}    # schedule_id -> datetime of last fire (for 90 s dedupe)

    while True:
        try:
            time.sleep(30)
            now = datetime.datetime.now()
            schedules = db_list_report_schedules() or []
            for sch in schedules:
                if not sch.get("enabled"):
                    continue
                sid = sch["id"]
                if not _matches_schedule(sch, now):
                    continue
                prev = last_fired.get(sid)
                if prev and (now - prev).total_seconds() < 90:
                    continue
                last_fired[sid] = now
                log.info(f"Report scheduler firing schedule {sid} ({sch.get('name')!r})")

                def _fire(_sch):
                    try:
                        run_schedule(_sch)
                        db_record_schedule_run(_sch["id"], time.time())
                    except Exception as e:
                        log.error(f"Scheduled report crashed ({_sch.get('id')}): {e}",
                                  exc_info=True)

                t = threading.Thread(target=_fire, args=(sch,),
                                     daemon=True, name=f"rep-sched-{sid}")
                t.start()
                # Stagger multiple schedules landing in the same minute
                time.sleep(2)

        except Exception as e:
            log.error(f"Report scheduler error: {e}")


def start_scheduler():
    """Start the background report scheduler thread (call once at boot)."""
    t = threading.Thread(target=_scheduler_loop, daemon=True,
                         name="report-scheduler")
    t.start()
