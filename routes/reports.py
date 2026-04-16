"""
routes/reports.py — Reports API.

Endpoints:
  GET    /api/reports/templates                      viewer  list templates
  POST   /api/reports/template                       admin   create template
  GET    /api/reports/template/{id}                  viewer  get template
  PATCH  /api/reports/template/{id}                  admin   update template
  DELETE /api/reports/template/{id}                  admin   delete template

  GET    /api/reports/schedules                      viewer  list schedules
  POST   /api/reports/schedule                       admin   create schedule
  GET    /api/reports/schedule/{id}                  viewer  get schedule
  PATCH  /api/reports/schedule/{id}                  admin   update schedule
  DELETE /api/reports/schedule/{id}                  admin   delete schedule
  POST   /api/reports/schedule/{id}/run              operator trigger schedule now

  GET    /api/reports/history                        viewer  list history
  GET    /api/reports/history/{id}/download          viewer  download PDF
  DELETE /api/reports/history/{id}                   admin   delete history row + PDF

  POST   /api/reports/run                            operator ad-hoc run template_id
  POST   /api/reports/preview                        operator render HTML preview
  POST   /api/reports/test-send                      admin   send test email to caller
"""

import os
import re

from core.logger import log


_RE_TEMPLATES     = re.compile(r"^/api/reports/templates$")
_RE_TEMPLATE      = re.compile(r"^/api/reports/template$")
_RE_TEMPLATE_ID   = re.compile(r"^/api/reports/template/([a-zA-Z0-9_\-]+)$")
_RE_SCHEDULES     = re.compile(r"^/api/reports/schedules$")
_RE_SCHEDULE      = re.compile(r"^/api/reports/schedule$")
_RE_SCHEDULE_ID   = re.compile(r"^/api/reports/schedule/([a-zA-Z0-9_\-]+)$")
_RE_SCHEDULE_RUN  = re.compile(r"^/api/reports/schedule/([a-zA-Z0-9_\-]+)/run$")
_RE_HISTORY       = re.compile(r"^/api/reports/history$")
_RE_HISTORY_ID    = re.compile(r"^/api/reports/history/([a-zA-Z0-9_\-]+)$")
_RE_HISTORY_DL    = re.compile(r"^/api/reports/history/([a-zA-Z0-9_\-]+)/download$")
_RE_RUN_NOW       = re.compile(r"^/api/reports/run$")
_RE_PREVIEW       = re.compile(r"^/api/reports/preview$")
_RE_TEST_SEND     = re.compile(r"^/api/reports/test-send$")


_VALID_KINDS   = {"executive", "technical", "inventory", "custom"}
_VALID_FREQS   = {"daily", "weekly", "monthly", "quarterly"}
_VALID_PERIODS = {"last_7d", "last_30d", "last_90d", "last_month",
                  "last_quarter", "last_year", "month_to_date"}


# ── Validators ────────────────────────────────────────────────────────

def _validate_template(body: dict) -> str:
    name = str(body.get("name", "")).strip()
    if not name:
        return "name is required"
    if len(name) > 200:
        return "name too long (max 200)"
    kind = body.get("kind", "executive")
    if kind not in _VALID_KINDS:
        return f"kind must be one of: {', '.join(sorted(_VALID_KINDS))}"
    return ""


def _validate_schedule(body: dict) -> str:
    if not str(body.get("template_id", "")).strip():
        return "template_id is required"
    if not str(body.get("name", "")).strip():
        return "name is required"
    freq = body.get("freq", "monthly")
    if freq not in _VALID_FREQS:
        return f"freq must be one of: {', '.join(sorted(_VALID_FREQS))}"
    period = body.get("period", "last_month")
    if period not in _VALID_PERIODS and not str(period).startswith("custom:"):
        return f"period must be one of: {', '.join(sorted(_VALID_PERIODS))}"
    t = str(body.get("time_str", "03:00"))
    try:
        hh, mm = t.split(":")
        hh = int(hh); mm = int(mm)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return "time_str must be HH:MM (0–23 : 0–59)"
    except Exception:
        return "time_str must be HH:MM"
    return ""


# ── Handler ───────────────────────────────────────────────────────────

def handle(h, method, path, body):
    """Return True if this module handled the request."""

    # ── Templates ────────────────────────────────────────────────────
    if _RE_TEMPLATES.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from db import db_list_report_templates
        h._json(200, {"templates": db_list_report_templates()})
        return True

    if _RE_TEMPLATE.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        err = _validate_template(body)
        if err:
            h._json(400, {"error": err}); return True
        from db import db_create_report_template, db_get_report_template, db_log_audit
        tid = db_create_report_template(body, created_by=user)
        if not tid:
            h._json(500, {"error": "failed to create template"}); return True
        db_log_audit(user, h.client_address[0], "report_template_create", body.get("name", ""))
        h._json(200, {"template": db_get_report_template(tid)})
        return True

    m = _RE_TEMPLATE_ID.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from db import db_get_report_template
        t = db_get_report_template(m.group(1))
        if not t:
            h._json(404, {"error": "not found"}); return True
        h._json(200, {"template": t})
        return True

    if m and method == "PATCH":
        user, _ = h._require("admin")
        if not user: return True
        err = _validate_template(body)
        if err:
            h._json(400, {"error": err}); return True
        from db import db_update_report_template, db_get_report_template, db_log_audit
        if not db_get_report_template(m.group(1)):
            h._json(404, {"error": "not found"}); return True
        if not db_update_report_template(m.group(1), body):
            h._json(500, {"error": "update failed"}); return True
        db_log_audit(user, h.client_address[0], "report_template_update", body.get("name", ""))
        h._json(200, {"template": db_get_report_template(m.group(1))})
        return True

    if m and method == "DELETE":
        user, _ = h._require("admin")
        if not user: return True
        from db import db_get_report_template, db_delete_report_template, db_log_audit
        t = db_get_report_template(m.group(1))
        if not t:
            h._json(404, {"error": "not found"}); return True
        db_delete_report_template(m.group(1))
        db_log_audit(user, h.client_address[0], "report_template_delete", t.get("name", ""))
        h._json(200, {"ok": True})
        return True

    # ── Schedules ────────────────────────────────────────────────────
    if _RE_SCHEDULES.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from db import db_list_report_schedules
        h._json(200, {"schedules": db_list_report_schedules()})
        return True

    if _RE_SCHEDULE.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        err = _validate_schedule(body)
        if err:
            h._json(400, {"error": err}); return True
        from db import db_create_report_schedule, db_get_report_schedule, db_log_audit
        sid = db_create_report_schedule(body, created_by=user)
        if not sid:
            h._json(500, {"error": "failed to create schedule"}); return True
        db_log_audit(user, h.client_address[0], "report_schedule_create", body.get("name", ""))
        h._json(200, {"schedule": db_get_report_schedule(sid)})
        return True

    m = _RE_SCHEDULE_ID.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from db import db_get_report_schedule
        s = db_get_report_schedule(m.group(1))
        if not s:
            h._json(404, {"error": "not found"}); return True
        h._json(200, {"schedule": s})
        return True

    if m and method == "PATCH":
        user, _ = h._require("admin")
        if not user: return True
        err = _validate_schedule(body)
        if err:
            h._json(400, {"error": err}); return True
        from db import db_update_report_schedule, db_get_report_schedule, db_log_audit
        if not db_get_report_schedule(m.group(1)):
            h._json(404, {"error": "not found"}); return True
        if not db_update_report_schedule(m.group(1), body):
            h._json(500, {"error": "update failed"}); return True
        db_log_audit(user, h.client_address[0], "report_schedule_update", body.get("name", ""))
        h._json(200, {"schedule": db_get_report_schedule(m.group(1))})
        return True

    if m and method == "DELETE":
        user, _ = h._require("admin")
        if not user: return True
        from db import db_get_report_schedule, db_delete_report_schedule, db_log_audit
        s = db_get_report_schedule(m.group(1))
        if not s:
            h._json(404, {"error": "not found"}); return True
        db_delete_report_schedule(m.group(1))
        db_log_audit(user, h.client_address[0], "report_schedule_delete", s.get("name", ""))
        h._json(200, {"ok": True})
        return True

    m = _RE_SCHEDULE_RUN.match(path)
    if m and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        from db import db_get_report_schedule, db_log_audit
        sch = db_get_report_schedule(m.group(1))
        if not sch:
            h._json(404, {"error": "not found"}); return True
        try:
            from reports.runner import run_schedule
            result = run_schedule(sch)
            db_log_audit(user, h.client_address[0], "report_run_schedule", sch.get("name", ""))
            h._json(200, result); return True
        except Exception as e:
            log.error(f"reports.run_schedule error: {e}", exc_info=True)
            h._json(500, {"error": "run failed"}); return True

    # ── History ──────────────────────────────────────────────────────
    if _RE_HISTORY.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from db import db_list_report_history
        h._json(200, {"history": db_list_report_history(200)})
        return True

    m = _RE_HISTORY_DL.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from db import db_get_report_history
        row = db_get_report_history(m.group(1))
        if not row:
            h._json(404, {"error": "not found"}); return True
        pdf_path = row.get("pdf_path") or ""
        if not pdf_path or not os.path.isfile(pdf_path):
            h._json(404, {"error": "pdf missing"}); return True
        try:
            with open(pdf_path, "rb") as f:
                data = f.read()
            fname = os.path.basename(pdf_path) or "report.pdf"
            h.send_response(200)
            h.send_header("Content-Type", "application/pdf")
            h.send_header("Content-Length", str(len(data)))
            h.send_header("Content-Disposition",
                          f'attachment; filename="{fname}"')
            h.end_headers()
            h.wfile.write(data)
            return True
        except Exception as e:
            log.error(f"reports.history download error: {e}")
            h._json(500, {"error": "read failed"}); return True

    m = _RE_HISTORY_ID.match(path)
    if m and method == "DELETE":
        user, _ = h._require("admin")
        if not user: return True
        from db import db_get_report_history, db_delete_report_history, db_log_audit
        row = db_get_report_history(m.group(1))
        if not row:
            h._json(404, {"error": "not found"}); return True
        # Delete the PDF file too (best-effort)
        pp = row.get("pdf_path") or ""
        if pp and os.path.isfile(pp):
            try: os.remove(pp)
            except Exception as e: log.warning(f"reports.history pdf delete failed: {e}")
        db_delete_report_history(m.group(1))
        db_log_audit(user, h.client_address[0], "report_history_delete", row.get("template_name", ""))
        h._json(200, {"ok": True})
        return True

    # ── Ad-hoc: run + preview + test-send ────────────────────────────
    if _RE_RUN_NOW.match(path) and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        tid = str(body.get("template_id", "")).strip()
        if not tid:
            h._json(400, {"error": "template_id required"}); return True
        try:
            from reports.runner import run_template_now
            result = run_template_now(tid, triggered_by=user)
            from db import db_log_audit
            db_log_audit(user, h.client_address[0], "report_run_now", tid)
            h._json(200, result); return True
        except ValueError as e:
            h._json(404, {"error": str(e)}); return True
        except Exception as e:
            log.error(f"reports.run_now error: {e}", exc_info=True)
            h._json(500, {"error": "run failed"}); return True

    if _RE_PREVIEW.match(path) and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        tid = str(body.get("template_id", "")).strip()
        period_override = body.get("period") or None
        if not tid:
            h._json(400, {"error": "template_id required"}); return True
        try:
            from db import db_get_report_template
            tpl = db_get_report_template(tid)
            if not tpl:
                h._json(404, {"error": "template not found"}); return True
            from reports import data as _data, engine as _engine
            cfg = tpl.get("config_json") or {}
            if isinstance(cfg, str):
                import json
                try: cfg = json.loads(cfg)
                except Exception: cfg = {}
            ctx = _data.build_report_context(
                kind=tpl.get("kind", "executive"),
                period=period_override or cfg.get("period") or "last_month",
                filters=cfg.get("filters") or {},
                config=cfg,
            )
            html = _engine.render_html(tpl.get("kind", "executive"), ctx)
            # Return HTML directly (not JSON) so the browser can render it in a new tab
            data = html.encode("utf-8")
            h.send_response(200)
            h.send_header("Content-Type", "text/html; charset=utf-8")
            h.send_header("Content-Length", str(len(data)))
            h.send_header("Cache-Control", "no-cache, must-revalidate")
            h.end_headers()
            h.wfile.write(data)
            return True
        except Exception as e:
            log.error(f"reports.preview error: {e}", exc_info=True)
            h._json(500, {"error": "preview failed"}); return True

    if _RE_TEST_SEND.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        tid = str(body.get("template_id", "")).strip()
        to  = str(body.get("to", "")).strip()
        if not (tid and to):
            h._json(400, {"error": "template_id and to required"}); return True
        try:
            from db import db_get_report_template
            tpl = db_get_report_template(tid)
            if not tpl:
                h._json(404, {"error": "template not found"}); return True
            from reports.runner   import render_from_template
            from reports.delivery import _render_subject_body, send_report_email
            pdf, ctx, _ms = render_from_template(tpl, triggered_by=f"test:{user}")
            # Build a tiny fake schedule so subject/body rendering reuses the helper
            _fake_sch = {
                "subject_tpl": "[TEST] {company} — {title}",
                "body_tpl":    "This is a test report.\n\nPeriod: {period}\nUptime: {uptime}",
            }
            subject, body_txt = _render_subject_body(_fake_sch, ctx, len(pdf))
            ok, err = send_report_email([to], subject, body_txt, pdf,
                                        pdf_filename="test-report.pdf")
            from db import db_log_audit
            db_log_audit(user, h.client_address[0], "report_test_send", to)
            if ok:
                h._json(200, {"ok": True, "bytes": len(pdf)})
            else:
                h._json(500, {"error": err or "send failed"})
            return True
        except Exception as e:
            log.error(f"reports.test_send error: {e}", exc_info=True)
            h._json(500, {"error": "test send failed"}); return True

    return False
