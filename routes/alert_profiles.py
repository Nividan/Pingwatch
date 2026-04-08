"""
routes/alert_profiles.py — Alert Profile API endpoints (PRTG-style).

Profiles
    GET    /api/alert/profiles                   viewer    — list with stages
    POST   /api/alert/profiles                   admin     — create
    POST   /api/alert/profile                    admin     — alias for create
    GET    /api/alert/profile/{id}               viewer    — single
    PATCH  /api/alert/profile/{id}               admin     — update (replaces stages)
    DELETE /api/alert/profile/{id}               admin     — delete (cascades to stages)
    POST   /api/alert/profile/{id}/toggle        operator  — enable/disable
    POST   /api/alert/profile/{id}/test          admin     — fire all stages with fake ctx

Action templates
    GET    /api/alert/action-templates           viewer    — list
    POST   /api/alert/action-templates           admin     — create
    POST   /api/alert/action-template            admin     — alias for create
    GET    /api/alert/action-template/{id}       viewer    — single
    PATCH  /api/alert/action-template/{id}       admin     — update
    DELETE /api/alert/action-template/{id}       admin     — delete (fails if referenced)
"""

from core.config import (
    _RE_ALERT_PROFILES, _RE_ALERT_PROFILE_NEW,
    _RE_ALERT_PROFILE,  _RE_ALERT_PROFILE_ACT,
    _RE_ALERT_TEMPLATES, _RE_ALERT_TEMPLATE_NEW, _RE_ALERT_TEMPLATE,
)
from db import db_log_audit
from db.alert_profiles import (
    db_list_profiles, db_get_profile, db_save_profile, db_delete_profile,
    db_set_profile_enabled,
    db_list_action_templates, db_get_action_template,
    db_save_action_template, db_delete_action_template,
)
from core.logger import log


# ── Validation ───────────────────────────────────────────────────

_VALID_SCOPES   = {"global", "group", "device", "sensor"}
_VALID_TRIGGERS = {"down", "warning", "down_recovered", "warning_recovered"}
_VALID_ATYPES   = {"email", "webhook", "syslog", "browser"}


def _validate_profile(body: dict) -> str | None:
    """Return an error string or None if valid."""
    name = str(body.get("name", "")).strip()
    if not name:
        return "name is required"
    if len(name) > 200:
        return "name too long (max 200)"

    scope_type = str(body.get("scope_type", "")).strip()
    if scope_type not in _VALID_SCOPES:
        return f"scope_type must be one of: {', '.join(sorted(_VALID_SCOPES))}"
    scope_value = str(body.get("scope_value", "") or "").strip()
    if scope_type != "global" and not scope_value:
        return "scope_value is required for non-global scopes"

    stages = body.get("stages")
    if stages is not None and not isinstance(stages, list):
        return "stages must be a list"
    for i, s in enumerate(stages or []):
        if not isinstance(s, dict):
            return f"stage {i}: must be an object"
        trig = s.get("trigger_state")
        if trig not in _VALID_TRIGGERS:
            return f"stage {i}: trigger_state must be one of: {', '.join(sorted(_VALID_TRIGGERS))}"
        try:
            d = int(s.get("delay_s") or 0)
            if d < 0:
                return f"stage {i}: delay_s must be >= 0"
        except (TypeError, ValueError):
            return f"stage {i}: delay_s must be an integer"
        try:
            r = int(s.get("repeat_min") or 0)
            if r < 0:
                return f"stage {i}: repeat_min must be >= 0"
        except (TypeError, ValueError):
            return f"stage {i}: repeat_min must be an integer"
        action_ids = s.get("action_ids") or []
        if not isinstance(action_ids, list):
            return f"stage {i}: action_ids must be a list"
        for j, aid in enumerate(action_ids):
            try:
                aid = int(aid)
            except (TypeError, ValueError):
                return f"stage {i}: action_ids[{j}] must be an integer"
            if not db_get_action_template(aid):
                return f"stage {i}: action template {aid} not found"
    return None


def _clean_profile_body(body: dict) -> dict:
    scope_type = str(body.get("scope_type", "global")).strip()
    return {
        "name":        str(body.get("name", "")).strip(),
        "scope_type":  scope_type,
        "scope_value": "" if scope_type == "global"
                       else str(body.get("scope_value", "") or "").strip(),
        "enabled":     bool(body.get("enabled", True)),
        "stages":      body.get("stages") or [],
    }


def _validate_template(body: dict) -> str | None:
    name = str(body.get("name", "")).strip()
    if not name:
        return "name is required"
    if len(name) > 200:
        return "name too long (max 200)"
    atype = body.get("atype")
    if atype not in _VALID_ATYPES:
        return f"atype must be one of: {', '.join(sorted(_VALID_ATYPES))}"
    cfg = body.get("config")
    if cfg is not None and not isinstance(cfg, dict):
        return "config must be an object"
    if atype == "email":
        c = cfg or {}
        has_users  = bool(c.get("to_users"))
        has_groups = bool(c.get("to_groups"))
        has_emails = bool(str(c.get("to_emails", "") or c.get("to", "")).strip())
        if not (has_users or has_groups or has_emails):
            return "email template requires at least one recipient (to_users / to_groups / to_emails)"
    if atype == "webhook":
        if not str((cfg or {}).get("url", "") or "").strip():
            return "webhook template requires a url"
    return None


def _clean_template_body(body: dict) -> dict:
    return {
        "name":   str(body.get("name", "")).strip(),
        "atype":  body.get("atype"),
        "config": body.get("config") or {},
    }


# ── Cache invalidation ───────────────────────────────────────────

def _invalidate():
    """Force every sensor to re-resolve its alert profile on next probe."""
    try:
        from core.app_state import STATE
        STATE._profile_cache_ver = getattr(STATE, "_profile_cache_ver", 0) + 1
    except Exception as e:
        log.warning(f"alert_profiles: cache invalidate failed: {e}")


# ── Test fire ────────────────────────────────────────────────────

def _fire_test_profile(profile: dict, actor: str):
    """Synchronously dispatch every stage in a profile with a fake ctx."""
    import datetime
    import threading

    from monitoring.alert_dispatchers import dispatch

    def _run():
        for stage in profile.get("stages") or []:
            ctx = {
                "did":        "test",
                "sid":        "test",
                "dname":      "Test Device",
                "sname":      "Test Sensor",
                "host":       "192.0.2.1",
                "stype":      "ping",
                "grp":        "Test Group",
                "state":      "",
                "ms":         None,
                "loss_pct":   0,
                "detail":     f"[TEST] Profile '{profile['name']}' "
                              f"stage {stage['id']} test fired by {actor}",
                "ts":         datetime.datetime.now(datetime.timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"),
                "severity":   "info",
                "event_type": "test",
            }
            for aid in (stage.get("action_ids") or []):
                tpl = db_get_action_template(aid)
                if not tpl:
                    continue
                try:
                    dispatch(tpl["atype"], tpl["config"], ctx)
                except Exception as e:
                    log.error(f"alert_profile test dispatch error: {e}")

    threading.Thread(target=_run, daemon=True).start()


# ── Route handler ────────────────────────────────────────────────

def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # ── Action templates ────────────────────────────────────────

    # GET /api/alert/action-templates
    if _RE_ALERT_TEMPLATES.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, {"templates": db_list_action_templates()})
        return True

    # POST /api/alert/action-templates  (create)
    if _RE_ALERT_TEMPLATES.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        err = _validate_template(body)
        if err:
            h._json(400, {"error": err}); return True
        data = _clean_template_body(body)
        tpl_id = db_save_action_template(data)
        if tpl_id < 0:
            h._json(500, {"error": "failed to create action template"}); return True
        db_log_audit(user, h.client_address[0],
                     'alert_template_create', data["name"])
        h._json(200, {"id": tpl_id, "template": db_get_action_template(tpl_id)})
        return True

    # POST /api/alert/action-template  (alias for create)
    if _RE_ALERT_TEMPLATE_NEW.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        err = _validate_template(body)
        if err:
            h._json(400, {"error": err}); return True
        data = _clean_template_body(body)
        tpl_id = db_save_action_template(data)
        if tpl_id < 0:
            h._json(500, {"error": "failed to create action template"}); return True
        db_log_audit(user, h.client_address[0],
                     'alert_template_create', data["name"])
        h._json(200, {"id": tpl_id, "template": db_get_action_template(tpl_id)})
        return True

    # /api/alert/action-template/{id}
    mt = _RE_ALERT_TEMPLATE.match(path)
    if mt:
        tpl_id = int(mt.group(1))

        if method == "GET":
            user, _ = h._require("viewer")
            if not user: return True
            tpl = db_get_action_template(tpl_id)
            if not tpl:
                h._json(404, {"error": "not found"}); return True
            h._json(200, {"template": tpl})
            return True

        if method == "PATCH":
            user, _ = h._require("admin")
            if not user: return True
            existing = db_get_action_template(tpl_id)
            if not existing:
                h._json(404, {"error": "not found"}); return True
            err = _validate_template(body)
            if err:
                h._json(400, {"error": err}); return True
            data = _clean_template_body(body)
            new_id = db_save_action_template(data, tpl_id=tpl_id)
            if new_id < 0:
                h._json(500, {"error": "update failed"}); return True
            db_log_audit(user, h.client_address[0],
                         'alert_template_update', data["name"])
            h._json(200, {"template": db_get_action_template(tpl_id)})
            return True

        if method == "DELETE":
            user, _ = h._require("admin")
            if not user: return True
            tpl = db_get_action_template(tpl_id)
            if not tpl:
                h._json(404, {"error": "not found"}); return True
            ok = db_delete_action_template(tpl_id)
            if not ok:
                h._json(409, {"error": "template still referenced by one or more stages"})
                return True
            db_log_audit(user, h.client_address[0],
                         'alert_template_delete', tpl["name"])
            h._json(200, {"ok": True})
            return True

    # ── Profiles ────────────────────────────────────────────────

    # GET /api/alert/profiles
    if _RE_ALERT_PROFILES.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, {"profiles": db_list_profiles()})
        return True

    # POST /api/alert/profiles  (create)
    if _RE_ALERT_PROFILES.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        err = _validate_profile(body)
        if err:
            h._json(400, {"error": err}); return True
        data = _clean_profile_body(body)
        pid = db_save_profile(data)
        if pid < 0:
            h._json(500, {"error": "failed to create profile"}); return True
        _invalidate()
        db_log_audit(user, h.client_address[0],
                     'alert_profile_create', data["name"])
        h._json(200, {"id": pid, "profile": db_get_profile(pid)})
        return True

    # POST /api/alert/profile  (alias for create)
    if _RE_ALERT_PROFILE_NEW.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        err = _validate_profile(body)
        if err:
            h._json(400, {"error": err}); return True
        data = _clean_profile_body(body)
        pid = db_save_profile(data)
        if pid < 0:
            h._json(500, {"error": "failed to create profile"}); return True
        _invalidate()
        db_log_audit(user, h.client_address[0],
                     'alert_profile_create', data["name"])
        h._json(200, {"id": pid, "profile": db_get_profile(pid)})
        return True

    # /api/alert/profile/{id}
    m = _RE_ALERT_PROFILE.match(path)
    if m:
        pid = int(m.group(1))

        if method == "GET":
            user, _ = h._require("viewer")
            if not user: return True
            prof = db_get_profile(pid)
            if not prof:
                h._json(404, {"error": "not found"}); return True
            h._json(200, {"profile": prof})
            return True

        if method == "PATCH":
            user, _ = h._require("admin")
            if not user: return True
            if not db_get_profile(pid):
                h._json(404, {"error": "not found"}); return True
            err = _validate_profile(body)
            if err:
                h._json(400, {"error": err}); return True
            data = _clean_profile_body(body)
            new_id = db_save_profile(data, profile_id=pid)
            if new_id < 0:
                h._json(500, {"error": "update failed"}); return True
            _invalidate()
            db_log_audit(user, h.client_address[0],
                         'alert_profile_update', data["name"])
            h._json(200, {"profile": db_get_profile(pid)})
            return True

        if method == "DELETE":
            user, _ = h._require("admin")
            if not user: return True
            prof = db_get_profile(pid)
            if not prof:
                h._json(404, {"error": "not found"}); return True
            db_delete_profile(pid)
            _invalidate()
            db_log_audit(user, h.client_address[0],
                         'alert_profile_delete', prof["name"])
            h._json(200, {"ok": True})
            return True

    # /api/alert/profile/{id}/(toggle|test)
    m2 = _RE_ALERT_PROFILE_ACT.match(path)
    if m2 and method == "POST":
        pid    = int(m2.group(1))
        action = m2.group(2)

        if action == "toggle":
            user, _ = h._require("operator")
            if not user: return True
            prof = db_get_profile(pid)
            if not prof:
                h._json(404, {"error": "not found"}); return True
            new_state = not prof["enabled"]
            db_set_profile_enabled(pid, new_state)
            _invalidate()
            db_log_audit(user, h.client_address[0],
                         'alert_profile_enable' if new_state else 'alert_profile_disable',
                         prof["name"])
            h._json(200, {"enabled": new_state})
            return True

        if action == "test":
            user, _ = h._require("admin")
            if not user: return True
            prof = db_get_profile(pid)
            if not prof:
                h._json(404, {"error": "not found"}); return True
            _fire_test_profile(prof, user)
            db_log_audit(user, h.client_address[0],
                         'alert_profile_test', prof["name"])
            h._json(200, {"ok": True,
                          "msg": f"Test dispatched for profile '{prof['name']}'"})
            return True

    return False
