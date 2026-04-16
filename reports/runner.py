"""
reports/runner.py — Orchestrate a full report run: data → render → persist → deliver.

Called from:
  - reports.scheduler (background cron-style firing)
  - routes.reports    (ad-hoc "Run Now" and schedule-by-id trigger)
"""

import datetime
import os
import time

from core.config   import REPORTS_DIR
from core.logger   import log
from reports       import data as _data
from reports       import engine as _engine
from reports.delivery import (
    _resolve_recipients, _render_subject_body, send_report_email,
)


def _safe_filename(stem: str) -> str:
    keep = []
    for ch in stem:
        if ch.isalnum() or ch in "-_.":
            keep.append(ch)
        elif ch.isspace():
            keep.append("_")
    return "".join(keep)[:80] or "report"


def _ensure_dir() -> str:
    """Ensure REPORTS_DIR exists and is writable. Returns the usable path, or ''.

    Tries the primary location; on failure falls back to a per-user temp dir so
    runs don't silently lose their artifact on a read-only checkout.
    """
    candidates = [REPORTS_DIR]
    try:
        import tempfile
        candidates.append(os.path.join(tempfile.gettempdir(), "pingwatch_reports"))
    except Exception:
        pass

    last_err = None
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            # Probe write permission with a temp file
            probe = os.path.join(d, ".write_probe")
            with open(probe, "wb") as f:
                f.write(b"x")
            os.remove(probe)
            if d != REPORTS_DIR:
                log.warning(f"reports.runner: using fallback dir {d!r} "
                            f"(primary {REPORTS_DIR!r} is not writable)")
            return d
        except Exception as e:
            last_err = e
            continue
    log.error(f"reports.runner: no writable reports dir; last error: {last_err}")
    return ""


def render_from_template(template: dict,
                         period_override: str = None,
                         triggered_by: str = "") -> tuple:
    """
    Render a report from a template dict (as returned by db_get_report_template).

    Returns (pdf_bytes, ctx, rendered_ms).
    """
    cfg = template.get("config_json") or {}
    if isinstance(cfg, str):
        # Safety: if persisted as string despite _row() inflating, parse
        import json
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = {}
    cfg = dict(cfg)
    if triggered_by:
        cfg["triggered_by"] = triggered_by

    kind   = template.get("kind") or "executive"
    period = period_override or cfg.get("period") or "last_month"
    filters = cfg.get("filters") or {}

    t0 = time.time()
    ctx = _data.build_report_context(
        kind=kind, period=period, filters=filters, config=cfg,
    )
    pdf = _engine.render_pdf(kind, ctx)
    ms = int((time.time() - t0) * 1000)
    return pdf, ctx, ms


def run_template_now(template_id: str, triggered_by: str = "") -> dict:
    """
    Ad-hoc render of a template. Saves PDF + history row, but does NOT email.
    Returns the history row dict.
    """
    from db import db_get_report_template, db_add_report_history

    tpl = db_get_report_template(template_id)
    if not tpl:
        raise ValueError(f"template {template_id!r} not found")

    out_dir = _ensure_dir()
    pdf, ctx, ms = render_from_template(tpl, triggered_by=triggered_by)

    ts = time.time()
    stem = _safe_filename(f"{tpl['name']}_{datetime.datetime.fromtimestamp(ts).strftime('%Y%m%d_%H%M%S')}")
    pdf_path = ""
    write_err = ""
    if out_dir:
        pdf_path = os.path.join(out_dir, f"{stem}.pdf")
        try:
            with open(pdf_path, "wb") as f:
                f.write(pdf)
        except Exception as e:
            log.error(f"reports.runner write PDF failed at {pdf_path!r}: {e}")
            write_err = f"write failed: {e}"
            pdf_path = ""
    else:
        write_err = "no writable reports directory"

    hid = db_add_report_history({
        "template_id":   tpl["id"],
        "template_name": tpl["name"],
        "schedule_id":   "",
        "kind":          tpl.get("kind", ""),
        "generated_at":  ts,
        "period_start":  ctx["period"]["start_ts"],
        "period_end":    ctx["period"]["end_ts"],
        "pdf_path":      pdf_path,
        "pdf_bytes":     len(pdf),
        "delivery_status": "local_only" if pdf_path else "render_only",
        "render_ms":     ms,
        "error":         write_err,
        "triggered_by":  triggered_by,
    })
    return {"id": hid, "pdf_path": pdf_path, "pdf_bytes": len(pdf),
            "error": write_err}


def run_schedule(sch: dict) -> dict:
    """
    Render + persist + email a scheduled report.
    Returns a summary dict suitable for logging / history.
    """
    from db import (
        db_get_report_template, db_add_report_history,
        db_update_report_history_delivery,
    )

    tpl = db_get_report_template(sch["template_id"])
    if not tpl:
        log.warning(f"reports.runner: schedule {sch.get('id')} references missing template")
        return {"ok": False, "error": "template not found"}

    out_dir = _ensure_dir()

    try:
        pdf, ctx, ms = render_from_template(
            tpl,
            period_override=sch.get("period"),
            triggered_by=f"schedule:{sch.get('name') or sch.get('id')}"
        )
    except Exception as e:
        log.error(f"reports.runner render failed: {e}", exc_info=True)
        db_add_report_history({
            "template_id":   tpl["id"],
            "template_name": tpl["name"],
            "schedule_id":   sch["id"],
            "kind":          tpl.get("kind", ""),
            "generated_at":  time.time(),
            "delivery_status": "failed",
            "error":         f"render: {e}",
            "triggered_by":  "scheduler",
        })
        return {"ok": False, "error": "render failed"}

    ts = time.time()
    stem = _safe_filename(
        f"{tpl['name']}_{datetime.datetime.fromtimestamp(ts).strftime('%Y%m%d_%H%M%S')}"
    )
    pdf_path = ""
    if out_dir:
        pdf_path = os.path.join(out_dir, f"{stem}.pdf")
        try:
            with open(pdf_path, "wb") as f:
                f.write(pdf)
        except Exception as e:
            log.error(f"reports.runner write PDF failed at {pdf_path!r}: {e}")
            pdf_path = ""

    recipients = _resolve_recipients(sch)

    hid = db_add_report_history({
        "template_id":   tpl["id"],
        "template_name": tpl["name"],
        "schedule_id":   sch["id"],
        "kind":          tpl.get("kind", ""),
        "generated_at":  ts,
        "period_start":  ctx["period"]["start_ts"],
        "period_end":    ctx["period"]["end_ts"],
        "pdf_path":      pdf_path,
        "pdf_bytes":     len(pdf),
        "delivery_status": "pending",
        "recipients_json": recipients,
        "render_ms":     ms,
        "triggered_by":  "scheduler",
    })

    if not recipients:
        db_update_report_history_delivery(hid, "skipped", "no recipients")
        log.info(f"reports.runner: no recipients for schedule {sch.get('id')}, PDF saved to {pdf_path}")
        return {"ok": True, "history_id": hid, "sent": 0}

    subject, body = _render_subject_body(sch, ctx, len(pdf))
    ok, err = send_report_email(recipients, subject, body, pdf,
                                pdf_filename=os.path.basename(pdf_path) or "report.pdf")
    db_update_report_history_delivery(hid, "sent" if ok else "failed", err)
    return {"ok": ok, "history_id": hid, "sent": len(recipients) if ok else 0, "error": err}
