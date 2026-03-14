"""
routes/monitoring.py — Real-time event stream, flap/trap queries, SNMP helpers.

Handles: /events (SSE), /api/flaps, /api/traps,
         /api/snmp/catalog, /api/snmp/interfaces.
"""

import queue

import app_state
from db import db_load_flaps, db_load_traps
from logger import log


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""
    STATE = app_state.STATE

    # ── /events SSE ───────────────────────────────────────────────
    if path == "/events" and method == "GET":
        if not h._auth(): return True
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.send_header("Cache-Control", "no-cache")
        h.send_header("Connection", "keep-alive")
        h.send_header("Access-Control-Allow-Origin", "*")
        h.end_headers()
        q = STATE.subscribe()
        try:
            h.wfile.write(b": connected\n\n")
            h.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    h.wfile.write(msg.encode("utf-8"))
                    h.wfile.flush()
                except queue.Empty:
                    h.wfile.write(b": heartbeat\n\n")
                    h.wfile.flush()
        except Exception:
            pass
        finally:
            STATE.unsubscribe(q)
        return True

    # ── /api/flaps GET ────────────────────────────────────────────
    if path == "/api/flaps" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, {"flaps": db_load_flaps()})
        return True

    # ── /api/traps GET ────────────────────────────────────────────
    if path == "/api/traps" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, {"traps": db_load_traps()})
        return True

    # ── /api/snmp/catalog GET ─────────────────────────────────────
    if path == "/api/snmp/catalog" and method == "GET":
        if not h._auth(): return True
        from snmp_catalog import SNMP_CATALOG
        h._json(200, {"catalog": SNMP_CATALOG})
        return True

    # ── /api/snmp/interfaces POST ─────────────────────────────────
    if path == "/api/snmp/interfaces" and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        from probes import snmpwalk_interfaces
        _host = (body.get("host")      or "").strip()
        _comm = (body.get("community") or "public").strip()
        _port = int(body.get("port")   or 161)
        _ver  = (body.get("version")   or "2c").strip()
        if not _host:
            h._json(400, {"error": "host required"}); return True
        _ifaces = snmpwalk_interfaces(_host, _comm, _port, timeout=10, version=_ver)
        if _ifaces is None:
            h._json(503, {"error": "snmpwalk not found — install net-snmp"}); return True
        h._json(200, {"interfaces": _ifaces})
        return True

    return False
