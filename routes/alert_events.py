"""
routes/alert_events.py — Alert event history API endpoints.

GET  /api/alert/events         viewer   — list events (state filter + pagination)
GET  /api/alert/events/active  viewer   — count of active events + first page
GET  /api/alert/event/{id}     viewer   — single event
POST /api/alert/event/{id}/ack operator — acknowledge
POST /api/alert/event/{id}/resolve operator — resolve
"""

from core.config import (
    _RE_ALERT_EVENTS, _RE_ALERT_EVENTS_ACTIVE,
    _RE_ALERT_EVENT, _RE_ALERT_EVENT_ACT,
)
from db.alert_events import (
    db_list_events, db_count_active, db_get_event,
    db_ack_event, db_resolve_event,
)
from db import db_log_audit
from urllib.parse import urlparse, parse_qs


def handle(h, method, path, body):
    """Return True if this module handled the request."""

    # GET /api/alert/events/active  (must check before /events)
    if _RE_ALERT_EVENTS_ACTIVE.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        count  = db_count_active()
        events = db_list_events(state='active', limit=50)
        h._json(200, {"count": count, "events": events})
        return True

    # GET /api/alert/events
    if _RE_ALERT_EVENTS.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        qs     = parse_qs(urlparse(h.path).query)
        state  = qs.get("state",  ["all"])[0]
        limit  = min(int(qs.get("limit",  ["200"])[0]), 500)
        offset = int(qs.get("offset", ["0"])[0])
        events = db_list_events(state=state, limit=limit, offset=offset)
        total  = db_count_active()
        h._json(200, {"events": events, "active_count": total})
        return True

    # GET /api/alert/event/{id}
    m = _RE_ALERT_EVENT.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        evt = db_get_event(int(m.group(1)))
        if not evt:
            h._json(404, {"error": "not found"}); return True
        h._json(200, {"event": evt})
        return True

    # POST /api/alert/event/{id}/ack|resolve
    m2 = _RE_ALERT_EVENT_ACT.match(path)
    if m2 and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        event_id = int(m2.group(1))
        action   = m2.group(2)
        evt = db_get_event(event_id)
        if not evt:
            h._json(404, {"error": "not found"}); return True

        if action == "ack":
            ok = db_ack_event(event_id, user)
            if ok:
                db_log_audit(user, h.client_address[0], 'alert_event_ack',
                             f"event {event_id} rule '{evt.get('rule_name','')}'")
            h._json(200 if ok else 500, {"ok": ok})
        else:  # resolve
            ok = db_resolve_event(event_id)
            if ok:
                db_log_audit(user, h.client_address[0], 'alert_event_resolve',
                             f"event {event_id} rule '{evt.get('rule_name','')}'")
            h._json(200 if ok else 500, {"ok": ok})
        return True

    return False
