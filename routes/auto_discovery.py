"""routes/auto_discovery.py — REST endpoints for Auto-Discovery.

Endpoints:
  POST /api/auto-discovery/run-now                      — trigger a tick now
  GET  /api/auto-discovery/status                       — daemon state + stats
  POST /api/auto-discovery/suppressed/<host>/remove     — un-suppress a host
  POST /api/auto-discovery/subnet/<id>/approve-first-scan — override first-scan cap

Daemon control + the subnet `auto_discover` toggle live separately:
  - Global on/off / interval / pause — `POST /api/settings` (see routes/settings.py)
  - Per-subnet auto_discover flag   — `POST /api/ipam/subnet/<id>/auto-discover`
    (see routes/ipam.py)
"""

from urllib.parse import unquote

from core.config import (
    _RE_AD_RUN_NOW, _RE_AD_STATUS,
    _RE_AD_SUPPRESS_REMOVE, _RE_AD_APPROVE_FIRST,
    _RE_AD_ACTIVITY,
)
from core.logger import log
from core.validation import validate_host
from db import db_log_audit, db_get_subnet, db_approve_first_scan
from db.audit import db_get_audit
from monitoring import auto_discovery

# audit_log `action` values worth surfacing in the Auto-Discovery activity pane.
# Keep this list narrow — the pane is feature-scoped, not a generic audit view.
_AUDIT_ACTION_PREFIXES = (
    "auto_discovery_",        # tick, run_now, cap_hit, unsuppress, approve_first_scan
    "ipam_subnet_edit",       # consolidated subnet edit (new)
    "ipam_auto_discover_",    # legacy narrow toggle (back-compat)
)


def handle(h, method, path, body):
    # ── POST /api/auto-discovery/run-now ─────────────────────────
    if _RE_AD_RUN_NOW.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user:
            return True
        triggered = auto_discovery.trigger_run_now()
        try:
            db_log_audit(user, h.client_address[0],
                         "auto_discovery_run_now",
                         "triggered" if triggered else "already_running")
        except Exception:
            pass
        h._json(202 if triggered else 200, {
            "triggered": triggered,
            "already_running": not triggered,
        })
        return True

    # ── GET /api/auto-discovery/status ───────────────────────────
    if _RE_AD_STATUS.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user:
            return True
        h._json(200, auto_discovery.get_last_run_status())
        return True

    # ── GET /api/auto-discovery/activity ─────────────────────────
    # Audit-log-backed "recent activity" feed for the settings UI.
    # Reuses existing audit_log writes; no new storage.
    if _RE_AD_ACTIVITY.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user:
            return True
        try:
            rows = db_get_audit(limit=100,
                                action_prefixes=list(_AUDIT_ACTION_PREFIXES))
        except Exception as e:
            log.warning(f"auto_discovery activity query failed: {e}")
            rows = []
        h._json(200, {"entries": rows})
        return True

    # ── POST /api/auto-discovery/suppressed/<host>/remove ────────
    m = _RE_AD_SUPPRESS_REMOVE.match(path)
    if m and method == "POST":
        user, _ = h._require("admin")
        if not user:
            return True
        host = unquote(m.group(1) or "").strip()
        try:
            host = validate_host(host)
        except ValueError as ve:
            # Never leak raw exception text — use a generic-but-actionable msg.
            log.warning(f"auto_discovery unsuppress rejected invalid host: {ve}")
            h._json(400, {"error": "invalid host"})
            return True
        removed = auto_discovery.unsuppress_host(host)
        try:
            db_log_audit(user, h.client_address[0],
                         "auto_discovery_unsuppress", host)
        except Exception:
            pass
        h._json(200 if removed else 404,
                {"ok": removed, "host": host})
        return True

    # ── POST /api/auto-discovery/subnet/<id>/approve-first-scan ─
    m = _RE_AD_APPROVE_FIRST.match(path)
    if m and method == "POST":
        user, _ = h._require("admin")
        if not user:
            return True
        try:
            sid = int(m.group(1))
        except (TypeError, ValueError):
            h._json(400, {"error": "invalid subnet id"}); return True
        subnet = db_get_subnet(sid)
        if not subnet:
            h._json(404, {"error": "subnet not found"}); return True
        ok = db_approve_first_scan(sid)
        try:
            db_log_audit(user, h.client_address[0],
                         "auto_discovery_approve_first_scan",
                         f"{subnet.get('cidr', sid)}")
        except Exception:
            pass
        h._json(200 if ok else 500, {"ok": ok, "cidr": subnet.get("cidr", "")})
        return True

    return False
