"""
reports/runner.py — Orchestrate a full report run: data → render → persist → deliver.

Called from:
  - reports.scheduler (background cron-style firing)
  - routes.reports    (ad-hoc "Run Now" and schedule-by-id trigger)
"""

import datetime
import hashlib
import os
import time

from core.config   import REPORTS_DIR
from core.logger   import log
from reports       import data as _data
from reports       import engine as _engine
from reports       import csv_export as _csv_export
from reports.delivery import (
    _resolve_recipients, _render_subject_body, send_report_email,
)


def _build_report_id(tpl: dict, ctx: dict) -> str:
    """Short, human-friendly deterministic ID for this report instance.

    Derived from template id + period bounds + generated_at, so two PDFs
    rendered from the same template for the same period at different times
    get different IDs. 12 hex chars = 48 bits of entropy — plenty for
    unique reference purposes.
    """
    seed = (
        f"{tpl.get('id','')}|{ctx['period']['start_ts']}|"
        f"{ctx['period']['end_ts']}|{ctx['meta']['generated_at']}"
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12].upper()


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
    pdfa_mode = str(cfg.get("pdfa_mode") or "").strip()

    t0 = time.time()
    ctx = _data.build_report_context(
        kind=kind, period=period, filters=filters, config=cfg,
    )
    # Report ID must be set BEFORE render_pdf so the footer can print it.
    ctx["meta"]["report_id"]  = _build_report_id(template, ctx)
    ctx["meta"]["pdfa_mode"]  = pdfa_mode
    pdf = _engine.render_pdf(kind, ctx, pdfa_mode=pdfa_mode)
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

    cfg = tpl.get("config_json") or {}
    if isinstance(cfg, str):
        import json
        try: cfg = json.loads(cfg)
        except Exception: cfg = {}
    want_csv = bool(cfg.get("include_csv", False))

    ts = time.time()
    stem = _safe_filename(f"{tpl['name']}_{datetime.datetime.fromtimestamp(ts).strftime('%Y%m%d_%H%M%S')}")
    pdf_path = csv_path = ""
    csv_bytes_len = 0
    write_err = ""

    pdf_sha256 = hashlib.sha256(pdf).hexdigest()
    report_id  = ctx["meta"].get("report_id", "")

    if out_dir:
        pdf_path = os.path.join(out_dir, f"{stem}.pdf")
        try:
            with open(pdf_path, "wb") as f:
                f.write(pdf)
        except Exception as e:
            log.error(f"reports.runner write PDF failed at {pdf_path!r}: {e}")
            write_err = f"write failed: {e}"
            pdf_path = ""
        if want_csv:
            try:
                csv_bytes = _csv_export.build_csv_sidecar(ctx)
                csv_path = os.path.join(out_dir, f"{stem}.csv")
                with open(csv_path, "wb") as f:
                    f.write(csv_bytes)
                csv_bytes_len = len(csv_bytes)
            except Exception as e:
                log.error(f"reports.runner write CSV failed: {e}")
                csv_path = ""
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
        "pdf_sha256":    pdf_sha256,
        "csv_path":      csv_path,
        "csv_bytes":     csv_bytes_len,
        "report_id":     report_id,
        "delivery_status": "local_only" if pdf_path else "render_only",
        "render_ms":     ms,
        "error":         write_err,
        "triggered_by":  triggered_by,
    })
    return {"id": hid, "pdf_path": pdf_path, "pdf_bytes": len(pdf),
            "csv_path": csv_path, "csv_bytes": csv_bytes_len,
            "pdf_sha256": pdf_sha256, "report_id": report_id,
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

    cfg = tpl.get("config_json") or {}
    if isinstance(cfg, str):
        import json
        try: cfg = json.loads(cfg)
        except Exception: cfg = {}
    want_csv = bool(cfg.get("include_csv", False))

    ts = time.time()
    stem = _safe_filename(
        f"{tpl['name']}_{datetime.datetime.fromtimestamp(ts).strftime('%Y%m%d_%H%M%S')}"
    )
    pdf_sha256 = hashlib.sha256(pdf).hexdigest()
    report_id  = ctx["meta"].get("report_id", "")
    pdf_path = csv_path = ""
    csv_bytes = b""
    csv_bytes_len = 0

    if out_dir:
        pdf_path = os.path.join(out_dir, f"{stem}.pdf")
        try:
            with open(pdf_path, "wb") as f:
                f.write(pdf)
        except Exception as e:
            log.error(f"reports.runner write PDF failed at {pdf_path!r}: {e}")
            pdf_path = ""
        if want_csv:
            try:
                csv_bytes = _csv_export.build_csv_sidecar(ctx)
                csv_path = os.path.join(out_dir, f"{stem}.csv")
                with open(csv_path, "wb") as f:
                    f.write(csv_bytes)
                csv_bytes_len = len(csv_bytes)
            except Exception as e:
                log.error(f"reports.runner write CSV failed: {e}")
                csv_path = ""
                csv_bytes = b""

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
        "pdf_sha256":    pdf_sha256,
        "csv_path":      csv_path,
        "csv_bytes":     csv_bytes_len,
        "report_id":     report_id,
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
    ok, err = send_report_email(
        recipients, subject, body, pdf,
        pdf_filename=os.path.basename(pdf_path) or "report.pdf",
        csv_bytes=csv_bytes if csv_bytes else None,
        csv_filename=os.path.basename(csv_path) if csv_path else None,
    )
    db_update_report_history_delivery(hid, "sent" if ok else "failed", err)
    return {"ok": ok, "history_id": hid, "sent": len(recipients) if ok else 0, "error": err}
