"""
db/reports.py — Reports CRUD (templates, schedules, generated history).

Tables:
  report_templates  — report definitions (what's in the report)
  report_schedules  — when a template runs + who receives it
  report_history    — catalogue of generated PDFs
"""

import json
import time
import uuid

from core.logger import log
from db.backend  import is_pg
from db.helpers  import db_query, db_query_one, db_execute, db_cursor


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _row(r) -> dict:
    if not r:
        return None
    d = dict(r)
    # Inflate config_json / recipients_json so callers get dicts, not strings
    for k in ("config_json", "recipients_json"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = json.loads(v) if v else ({} if k == "config_json" else [])
            except Exception:
                d[k] = {} if k == "config_json" else []
    d["enabled"] = bool(d.get("enabled", 0)) if "enabled" in d else d.get("enabled")
    return d


# ── Templates ────────────────────────────────────────────────────────

def db_list_report_templates() -> list:
    rows = db_query("main",
                    "SELECT * FROM report_templates ORDER BY name")
    return [_row(r) for r in rows]


def db_get_report_template(template_id: str) -> dict:
    row = db_query_one("main",
                       "SELECT * FROM report_templates WHERE id=?",
                       (template_id,))
    return _row(row)


def db_create_report_template(data: dict, created_by: str = "") -> str:
    tid = _new_id("tpl")
    now = time.time()
    cfg = data.get("config_json") or data.get("config") or {}
    cfg_str = json.dumps(cfg) if not isinstance(cfg, str) else cfg
    _vals = (
        tid,
        data["name"],
        data.get("kind", "custom"),
        data.get("description", ""),
        cfg_str,
        created_by,
        now,
        now,
    )
    ok = db_execute(
        "main",
        """INSERT INTO report_templates
           (id, name, kind, description, config_json, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        _vals,
    )
    return tid if ok else ""


def db_update_report_template(template_id: str, data: dict) -> bool:
    cfg = data.get("config_json") or data.get("config") or {}
    cfg_str = json.dumps(cfg) if not isinstance(cfg, str) else cfg
    _vals = (
        data.get("name", ""),
        data.get("kind", "custom"),
        data.get("description", ""),
        cfg_str,
        time.time(),
        template_id,
    )
    return db_execute(
        "main",
        """UPDATE report_templates
           SET name=?, kind=?, description=?, config_json=?, updated_at=?
           WHERE id=?""",
        _vals,
    )


def db_delete_report_template(template_id: str) -> bool:
    # Cascade: delete any schedules that reference this template
    db_execute("main",
               "DELETE FROM report_schedules WHERE template_id=?",
               (template_id,))
    return db_execute("main",
                      "DELETE FROM report_templates WHERE id=?",
                      (template_id,))


# ── Schedules ────────────────────────────────────────────────────────

def db_list_report_schedules() -> list:
    rows = db_query("main",
                    "SELECT * FROM report_schedules ORDER BY name")
    return [_row(r) for r in rows]


def db_get_report_schedule(schedule_id: str) -> dict:
    row = db_query_one("main",
                       "SELECT * FROM report_schedules WHERE id=?",
                       (schedule_id,))
    return _row(row)


def db_list_schedules_for_template(template_id: str) -> list:
    rows = db_query("main",
                    "SELECT * FROM report_schedules WHERE template_id=? ORDER BY name",
                    (template_id,))
    return [_row(r) for r in rows]


def db_create_report_schedule(data: dict, created_by: str = "") -> str:
    sid = _new_id("sch")
    now = time.time()
    rcpt = data.get("recipient_emails") or []
    rcpt_str = json.dumps(rcpt) if not isinstance(rcpt, str) else rcpt
    _vals = (
        sid,
        data["template_id"],
        data.get("name", ""),
        data.get("freq", "monthly"),
        data.get("time_str", "03:00"),
        data.get("day_of_week", "1"),
        int(data.get("day_of_month", 1)),
        data.get("period", "last_month"),
        data.get("timezone", ""),
        int(data.get("recipient_group", 0) or 0),
        rcpt_str,
        data.get("subject_tpl", ""),
        data.get("body_tpl", ""),
        1 if data.get("enabled", True) else 0,
        0.0,
        0.0,
        created_by,
        now,
        now,
    )
    ok = db_execute(
        "main",
        """INSERT INTO report_schedules
           (id, template_id, name, freq, time_str, day_of_week, day_of_month,
            period, timezone, recipient_group, recipient_emails,
            subject_tpl, body_tpl, enabled,
            last_run_ts, next_run_ts, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        _vals,
    )
    return sid if ok else ""


def db_update_report_schedule(schedule_id: str, data: dict) -> bool:
    rcpt = data.get("recipient_emails") or []
    rcpt_str = json.dumps(rcpt) if not isinstance(rcpt, str) else rcpt
    _vals = (
        data.get("name", ""),
        data.get("freq", "monthly"),
        data.get("time_str", "03:00"),
        data.get("day_of_week", "1"),
        int(data.get("day_of_month", 1)),
        data.get("period", "last_month"),
        data.get("timezone", ""),
        int(data.get("recipient_group", 0) or 0),
        rcpt_str,
        data.get("subject_tpl", ""),
        data.get("body_tpl", ""),
        1 if data.get("enabled", True) else 0,
        time.time(),
        schedule_id,
    )
    return db_execute(
        "main",
        """UPDATE report_schedules SET
           name=?, freq=?, time_str=?, day_of_week=?, day_of_month=?,
           period=?, timezone=?, recipient_group=?, recipient_emails=?,
           subject_tpl=?, body_tpl=?, enabled=?, updated_at=?
           WHERE id=?""",
        _vals,
    )


def db_set_schedule_enabled(schedule_id: str, enabled: bool) -> bool:
    return db_execute("main",
                      "UPDATE report_schedules SET enabled=? WHERE id=?",
                      (1 if enabled else 0, schedule_id))


def db_record_schedule_run(schedule_id: str, ran_at: float) -> bool:
    return db_execute("main",
                      "UPDATE report_schedules SET last_run_ts=? WHERE id=?",
                      (float(ran_at), schedule_id))


def db_delete_report_schedule(schedule_id: str) -> bool:
    return db_execute("main",
                      "DELETE FROM report_schedules WHERE id=?",
                      (schedule_id,))


# ── History ──────────────────────────────────────────────────────────

def db_list_report_history(limit: int = 100) -> list:
    rows = db_query(
        "main",
        "SELECT * FROM report_history ORDER BY generated_at DESC LIMIT ?",
        (int(limit),),
    )
    return [_row(r) for r in rows]


def db_get_report_history(history_id: str) -> dict:
    row = db_query_one("main",
                       "SELECT * FROM report_history WHERE id=?",
                       (history_id,))
    return _row(row)


def db_add_report_history(data: dict) -> str:
    hid = _new_id("rh")
    rcpt = data.get("recipients_json") or data.get("recipients") or []
    rcpt_str = json.dumps(rcpt) if not isinstance(rcpt, str) else rcpt
    _vals = (
        hid,
        data.get("template_id", ""),
        data.get("template_name", ""),
        data.get("schedule_id", ""),
        data.get("kind", ""),
        float(data.get("generated_at", time.time())),
        float(data.get("period_start", 0)),
        float(data.get("period_end", 0)),
        data.get("pdf_path", ""),
        int(data.get("pdf_bytes", 0)),
        data.get("pdf_sha256", ""),
        data.get("csv_path", ""),
        int(data.get("csv_bytes", 0)),
        data.get("report_id", ""),
        data.get("delivery_status", ""),
        rcpt_str,
        int(data.get("render_ms", 0)),
        (data.get("error") or "")[:500],
        data.get("triggered_by", ""),
    )
    ok = db_execute(
        "main",
        """INSERT INTO report_history
           (id, template_id, template_name, schedule_id, kind,
            generated_at, period_start, period_end,
            pdf_path, pdf_bytes, pdf_sha256,
            csv_path, csv_bytes, report_id,
            delivery_status, recipients_json,
            render_ms, error, triggered_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        _vals,
    )
    return hid if ok else ""


def db_update_report_history_delivery(history_id: str,
                                      status: str,
                                      error: str = "") -> bool:
    return db_execute(
        "main",
        "UPDATE report_history SET delivery_status=?, error=? WHERE id=?",
        (status, (error or "")[:500], history_id),
    )


def db_delete_report_history(history_id: str) -> bool:
    return db_execute("main",
                      "DELETE FROM report_history WHERE id=?",
                      (history_id,))


def db_prune_report_history(older_than_ts: float) -> int:
    """Delete history rows older than the given epoch. Returns count deleted."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"DELETE FROM report_history WHERE generated_at < {ph}",
                (float(older_than_ts),),
            )
            return cur.rowcount or 0
    except Exception as e:
        log.error(f"db_prune_report_history error: {e}")
        return 0
