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


def _is_scheduled_day(sch: dict, d: datetime.date) -> bool:
    """Return True if `d` is an eligible day for this schedule's cadence."""
    freq = (sch.get("freq") or "monthly").lower()
    if freq == "daily":
        return True
    if freq == "weekly":
        try:
            days = {int(x) for x in str(sch.get("day_of_week") or "1").split(",") if x.strip()}
        except Exception:
            return False
        return (d.weekday() + 1) in days   # 1=Mon … 7=Sun
    try:
        dom = int(sch.get("day_of_month") or 1)
    except Exception:
        dom = 1
    # Clamp to the month length so day 29/30/31 still fires in shorter months
    # (a "31st" schedule otherwise silently skipped Feb/Apr/Jun/Sep/Nov).
    import calendar
    dom = min(dom, calendar.monthrange(d.year, d.month)[1])
    if freq == "monthly":
        return d.day == dom
    if freq == "quarterly":
        return d.month in (1, 4, 7, 10) and d.day == dom
    return False


def _matches_schedule(sch: dict, now_dt: datetime.datetime) -> bool:
    """Catch-up semantics: fire if the most recent eligible slot is in the past
    and we have not already fired since that slot.

    Exact-minute matching (the previous approach) silently skipped a run on
    DST spring-forward (the 02:xx slot never occurs), when the 30 s poll was
    busy through the target minute, or across a restart spanning the minute.
    Comparing against the persisted last_run_ts makes any poll after the slot
    fire exactly once.
    """
    try:
        h, m = map(int, (sch.get("time_str") or "03:00").split(":"))
    except Exception:
        log.warning(f"reports.scheduler bad time_str: {sch.get('time_str')!r}")
        return False

    # Find today's slot; if today isn't an eligible day or its slot hasn't
    # arrived yet, walk back to the most recent eligible slot (handles a
    # weekend/overnight outage spanning the scheduled day).
    slot = None
    for back in range(0, 400):   # generous bound (covers quarterly + slack)
        day = (now_dt - datetime.timedelta(days=back)).date()
        if _is_scheduled_day(sch, day):
            cand = datetime.datetime(day.year, day.month, day.day, h, m)
            if cand <= now_dt:
                slot = cand
                break
    if slot is None:
        return False

    last_run = float(sch.get("last_run_ts") or 0)
    return last_run < slot.timestamp()


def _prune_history_once() -> int:
    """Delete history entries (and their PDFs) older than retention policy.

    Controlled by setting 'report_retention_days' (int, default 365).
    Set to 0 to disable pruning entirely.
    Returns the number of rows deleted.
    """
    import os
    from core.settings import get as _cfg
    from db import db_list_report_history, db_delete_report_history
    try:
        days = int(_cfg("report_retention_days", 365) or 365)
    except Exception:
        days = 365
    if days <= 0:
        return 0
    cutoff = time.time() - days * 86400
    # Fetch a generous batch — the scheduler runs hourly, keeping churn bounded.
    rows = db_list_report_history(2000) or []
    n = 0
    for r in rows:
        gen = r.get("generated_at") or 0
        if gen >= cutoff:
            continue
        pdf_path = r.get("pdf_path") or ""
        if pdf_path and os.path.isfile(pdf_path):
            try: os.remove(pdf_path)
            except Exception as e: log.debug(f"report prune: remove {pdf_path} failed: {e}")
        if db_delete_report_history(r["id"]):
            n += 1
    if n:
        log.info(f"Report retention: pruned {n} history row(s) older than {days} days")
    return n


_stop = threading.Event()


def _scheduler_loop():
    from db import db_list_report_schedules, db_record_schedule_run
    from reports.runner import run_schedule

    log.info("Report scheduler started")
    last_fired: dict = {}       # schedule_id -> datetime of last fire (for 90 s dedupe)
    last_prune_ts = 0.0

    while not _stop.is_set():
        try:
            # Interruptible sleep so shutdown doesn't have to wait up to 30 s
            # before the scheduler notices and exits — preventing a crash on
            # the next db_list_report_schedules() once pg_close_pool() has run.
            if _stop.wait(30):
                break
            now = datetime.datetime.now()

            # Retention sweep once per hour — cheap, bounded query
            if time.time() - last_prune_ts >= 3600:
                try:
                    _prune_history_once()
                except Exception as e:
                    log.warning(f"Report prune failed: {e}")
                last_prune_ts = time.time()

            schedules = db_list_report_schedules() or []
            for sch in schedules:
                if _stop.is_set():
                    break
                if not sch.get("enabled"):
                    continue
                sid = sch["id"]
                if not _matches_schedule(sch, now):
                    continue
                prev = last_fired.get(sid)
                if prev and (now - prev).total_seconds() < 90:
                    continue
                last_fired[sid] = now
                # Record the run NOW (not after run_schedule completes): the
                # catch-up check keys on last_run_ts, so a report that takes
                # longer than one poll interval would otherwise re-fire on the
                # next poll. Stamp it before dispatch to make firing idempotent.
                try:
                    db_record_schedule_run(sid, now.timestamp())
                except Exception as e:
                    log.warning(f"reports.scheduler: record run {sid} failed: {e}")
                log.info(f"Report scheduler firing schedule {sid} ({sch.get('name')!r})")

                def _fire(_sch):
                    try:
                        run_schedule(_sch)
                    except Exception as e:
                        log.error(f"Scheduled report crashed ({_sch.get('id')}): {e}",
                                  exc_info=True)

                t = threading.Thread(target=_fire, args=(sch,),
                                     daemon=True, name=f"rep-sched-{sid}")
                t.start()
                # Stagger multiple schedules landing in the same minute
                if _stop.wait(2):
                    break

        except Exception as e:
            log.error(f"Report scheduler error: {e}")
    log.info("Report scheduler stopped")


def start_scheduler():
    """Start the background report scheduler thread (call once at boot)."""
    t = threading.Thread(target=_scheduler_loop, daemon=True,
                         name="report-scheduler")
    t.start()


def stop_scheduler() -> None:
    """Signal the report scheduler loop to exit (call at shutdown before pg_close_pool)."""
    _stop.set()
