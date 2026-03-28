"""
routes/alert_rules.py — Alert Rules Engine API endpoints.

GET  /api/alert/rules            viewer  — list all rules
POST /api/alert/rule             admin   — create rule
GET  /api/alert/rule/{id}        viewer  — get single rule
PATCH /api/alert/rule/{id}       admin   — update rule
DELETE /api/alert/rule/{id}      admin   — delete rule
POST /api/alert/rule/{id}/toggle operator — enable/disable
POST /api/alert/rule/{id}/test   admin   — test-fire all actions
"""

from core.config import (
    _RE_ALERT_RULES, _RE_ALERT_RULE_NEW,
    _RE_ALERT_RULE, _RE_ALERT_RULE_ACT,
)
from db import _db_enqueue, db_log_audit
from db.alert_rules import (
    db_list_rules, db_get_rule, db_create_rule,
    db_update_rule, db_delete_rule, db_set_rule_enabled,
    db_reorder_rules,
)
from core.logger import log


# ── Validation ────────────────────────────────────────────────────

_VALID_FIELDS = {
    "event_type", "sensor_type", "device_group",
    "threshold_state", "direction", "loss_pct", "severity",
}
_VALID_OPS    = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "contains"}
_VALID_SEVS   = {"info", "warning", "critical"}
_VALID_LOGICS = {"AND", "OR"}
_VALID_ATYPES = {"email", "webhook", "syslog", "browser"}


def _validate_rule(body: dict) -> str | None:
    """Return an error string or None if valid."""
    name = str(body.get("name", "")).strip()
    if not name:
        return "name is required"
    if len(name) > 200:
        return "name too long (max 200)"
    sev = body.get("severity", "warning")
    if sev not in _VALID_SEVS:
        return f"severity must be one of: {', '.join(_VALID_SEVS)}"
    logic = str(body.get("condition_logic", "AND")).upper()
    if logic not in _VALID_LOGICS:
        return "condition_logic must be AND or OR"
    try:
        cooldown = int(body.get("cooldown_s", 300))
        if cooldown < 0:
            return "cooldown_s must be >= 0"
    except (TypeError, ValueError):
        return "cooldown_s must be an integer"

    for cond in body.get("conditions", []):
        if cond.get("field") not in _VALID_FIELDS:
            return f"unknown condition field '{cond.get('field')}'"
        if cond.get("op") not in _VALID_OPS:
            return f"unknown condition operator '{cond.get('op')}'"

    for action in body.get("actions", []):
        if action.get("atype") not in _VALID_ATYPES:
            return f"unknown action type '{action.get('atype')}'"
        if action.get("atype") == "email":
            cfg = action.get("config", {})
            if not isinstance(cfg, dict):
                return "email action config must be an object"
            if not str(cfg.get("to", "")).strip():
                return "email action requires at least one recipient in 'to'"

    return None


def _clean_body(body: dict) -> dict:
    """Normalise and sanitise a rule body before write."""
    return {
        "name":            str(body.get("name", "")).strip(),
        "enabled":         bool(body.get("enabled", True)),
        "severity":        str(body.get("severity", "warning")),
        "condition_logic": str(body.get("condition_logic", "AND")).upper(),
        "cooldown_s":      int(body.get("cooldown_s", 300)),
        "sort_order":      int(body.get("sort_order", 0)),
        "conditions":      body.get("conditions", []),
        "actions":         body.get("actions", []),
    }


# ── Route handler ─────────────────────────────────────────────────

def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # GET /api/alert/rules
    if _RE_ALERT_RULES.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, {"rules": db_list_rules()})
        return True

    # POST /api/alert/rules (reorder)
    if _RE_ALERT_RULES.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        id_list = body.get("order", [])
        if not isinstance(id_list, list):
            h._json(400, {"error": "order must be a list of rule ids"}); return True
        _db_enqueue(lambda _l=id_list: db_reorder_rules(_l))
        h._json(200, {"ok": True})
        return True

    # POST /api/alert/rule (create)
    if _RE_ALERT_RULE_NEW.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        err = _validate_rule(body)
        if err:
            h._json(400, {"error": err}); return True
        data = _clean_body(body)
        rule_id = db_create_rule(data)
        if rule_id < 0:
            h._json(500, {"error": "failed to create rule"}); return True
        _invalidate()
        db_log_audit(user, h.client_address[0], 'alert_rule_create', data["name"])
        h._json(200, {"id": rule_id, "rule": db_get_rule(rule_id)})
        return True

    # GET /api/alert/rule/{id}
    m = _RE_ALERT_RULE.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        rule = db_get_rule(int(m.group(1)))
        if not rule:
            h._json(404, {"error": "not found"}); return True
        h._json(200, {"rule": rule})
        return True

    # PATCH /api/alert/rule/{id}
    if m and method == "PATCH":
        user, _ = h._require("admin")
        if not user: return True
        rule_id = int(m.group(1))
        if not db_get_rule(rule_id):
            h._json(404, {"error": "not found"}); return True
        err = _validate_rule(body)
        if err:
            h._json(400, {"error": err}); return True
        data = _clean_body(body)
        ok = db_update_rule(rule_id, data)
        if not ok:
            h._json(500, {"error": "update failed"}); return True
        _invalidate()
        db_log_audit(user, h.client_address[0], 'alert_rule_update', data["name"])
        h._json(200, {"rule": db_get_rule(rule_id)})
        return True

    # DELETE /api/alert/rule/{id}
    if m and method == "DELETE":
        user, _ = h._require("admin")
        if not user: return True
        rule_id = int(m.group(1))
        rule = db_get_rule(rule_id)
        if not rule:
            h._json(404, {"error": "not found"}); return True
        db_delete_rule(rule_id)
        _invalidate()
        db_log_audit(user, h.client_address[0], 'alert_rule_delete', rule["name"])
        h._json(200, {"ok": True})
        return True

    # POST /api/alert/rule/{id}/toggle  or  /test
    m2 = _RE_ALERT_RULE_ACT.match(path)
    if m2 and method == "POST":
        rule_id = int(m2.group(1))
        action  = m2.group(2)

        if action == "toggle":
            user, _ = h._require("operator")
            if not user: return True
            rule = db_get_rule(rule_id)
            if not rule:
                h._json(404, {"error": "not found"}); return True
            new_state = not rule["enabled"]
            db_set_rule_enabled(rule_id, new_state)
            _invalidate()
            db_log_audit(user, h.client_address[0],
                         'alert_rule_enable' if new_state else 'alert_rule_disable',
                         rule["name"])
            h._json(200, {"enabled": new_state})
            return True

        if action == "test":
            user, _ = h._require("admin")
            if not user: return True
            rule = db_get_rule(rule_id)
            if not rule:
                h._json(404, {"error": "not found"}); return True
            _fire_test(rule, user)
            h._json(200, {"ok": True, "msg": f"Test dispatched for rule '{rule['name']}'"})
            return True

    return False


# ── Helpers ───────────────────────────────────────────────────────

def _invalidate():
    """Force the engine to reload rules on next evaluation."""
    try:
        from monitoring.alert_engine import invalidate_rules_cache
        invalidate_rules_cache()
    except Exception:
        pass


def _fire_test(rule: dict, actor: str):
    """Dispatch test actions for a rule synchronously (in a daemon thread)."""
    import threading
    ctx = {
        "event_type":      "flap_down",
        "severity":        rule.get("severity", "warning"),
        "did":             "test",
        "sid":             "test",
        "dname":           "Test Device",
        "sname":           "Test Sensor",
        "stype":           "ping",
        "host":            "192.0.2.1",
        "grp":             "Test Group",
        "direction":       "down",
        "state":           "",
        "ts":              "",
        "detail":          f"[TEST] Rule '{rule['name']}' test fired by {actor}",
        "loss_pct":        0,
        "ms":              None,
    }
    def _run():
        from monitoring.alert_engine import _dispatch
        try:
            _dispatch(rule, ctx)
        except Exception as e:
            log.error(f"alert_rule test dispatch error: {e}")
    threading.Thread(target=_run, daemon=True).start()
