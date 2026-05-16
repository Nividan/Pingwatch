"""
routes/maintenance_windows.py — Maintenance window API endpoints.

GET    /api/alert/windows      viewer  — list all windows
POST   /api/alert/window       admin   — create window
GET    /api/alert/window/{id}  viewer  — get single window
PATCH  /api/alert/window/{id}  admin   — update window
DELETE /api/alert/window/{id}  admin   — delete window
"""
from __future__ import annotations

from core.config import _RE_ALERT_WINDOWS, _RE_ALERT_WINDOW
from db.maintenance_windows import (
    db_list_windows, db_get_window,
    db_create_window, db_update_window, db_delete_window,
)
from db import db_log_audit

_VALID_SCOPES = {"all", "site", "group", "device"}


def _validate(body: dict) -> str | None:
    name = str(body.get("name", "")).strip()
    if not name:
        return "name is required"
    if len(name) > 200:
        return "name too long (max 200)"
    if body.get("scope_type", "all") not in _VALID_SCOPES:
        return f"scope_type must be one of: {', '.join(_VALID_SCOPES)}"
    try:
        start = float(body.get("start_ts", 0))
        end   = float(body.get("end_ts", 0))
    except (TypeError, ValueError):
        return "start_ts and end_ts must be Unix timestamps"
    if end <= start:
        return "end_ts must be after start_ts"
    return None


def _clean(body: dict) -> dict:
    return {
        "name":        str(body.get("name", "")).strip(),
        "scope_type":  str(body.get("scope_type", "all")),
        "scope_value": str(body.get("scope_value", "")).strip(),
        "start_ts":    float(body.get("start_ts", 0)),
        "end_ts":      float(body.get("end_ts", 0)),
        "recurring":   bool(body.get("recurring", False)),
        "recur_days":  str(body.get("recur_days", "")).strip(),
        "recur_start": str(body.get("recur_start", "")).strip(),
        "recur_end":   str(body.get("recur_end", "")).strip(),
        # Default new windows to enabled. PATCH callers that omit the
        # field implicitly leave it enabled (same as legacy behavior).
        "enabled":     bool(body.get("enabled", True)),
    }


def handle(h, method, path, body):
    """Return True if this module handled the request."""

    # GET /api/alert/windows
    if _RE_ALERT_WINDOWS.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, {"windows": db_list_windows()})
        return True

    # POST /api/alert/window
    if _RE_ALERT_WINDOWS.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        err = _validate(body)
        if err:
            h._json(400, {"error": err}); return True
        data = _clean(body)
        wid  = db_create_window(data, created_by=user)
        if wid < 0:
            h._json(500, {"error": "failed to create window"}); return True
        db_log_audit(user, h.client_address[0], 'maint_window_create', data["name"])
        h._json(200, {"id": wid, "window": db_get_window(wid)})
        return True

    m = _RE_ALERT_WINDOW.match(path)

    # GET /api/alert/window/{id}
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        w = db_get_window(int(m.group(1)))
        if not w:
            h._json(404, {"error": "not found"}); return True
        h._json(200, {"window": w})
        return True

    # PATCH /api/alert/window/{id}
    if m and method == "PATCH":
        user, _ = h._require("admin")
        if not user: return True
        wid = int(m.group(1))
        if not db_get_window(wid):
            h._json(404, {"error": "not found"}); return True
        err = _validate(body)
        if err:
            h._json(400, {"error": err}); return True
        data = _clean(body)
        ok   = db_update_window(wid, data)
        if not ok:
            h._json(500, {"error": "update failed"}); return True
        db_log_audit(user, h.client_address[0], 'maint_window_update', data["name"])
        h._json(200, {"window": db_get_window(wid)})
        return True

    # DELETE /api/alert/window/{id}
    if m and method == "DELETE":
        user, _ = h._require("admin")
        if not user: return True
        wid = int(m.group(1))
        w   = db_get_window(wid)
        if not w:
            h._json(404, {"error": "not found"}); return True
        db_delete_window(wid)
        db_log_audit(user, h.client_address[0], 'maint_window_delete', w["name"])
        h._json(200, {"ok": True})
        return True

    return False
