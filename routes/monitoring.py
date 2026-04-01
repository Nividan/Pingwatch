"""
routes/monitoring.py — Real-time event stream, flap/trap queries, SNMP helpers.

Handles: /events (SSE), /api/flaps, /api/traps,
         /api/snmp/catalog, /api/snmp/interfaces.
"""

import datetime
import queue
import sqlite3
import time

import core.app_state as app_state
from core.config import DB_PATH, LOGS_DB_PATH
from db import db_load_flaps, db_load_traps, db_ack_flap, db_resolve_flap
from core.logger import log


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
        from urllib.parse import parse_qs, urlparse as _urlparse
        qs = parse_qs(_urlparse(h.path).query)
        vendor   = qs.get("vendor",   [None])[0]
        category = qs.get("category", [None])[0]
        severity = qs.get("severity", [None])[0]
        h._json(200, {"traps": db_load_traps(
            vendor=vendor, category=category, severity=severity
        )})
        return True

    # ── /api/traps/vendors GET ────────────────────────────────────
    if path == "/api/traps/vendors" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from db.trap_defs import db_get_trap_vendors
        h._json(200, {"vendors": db_get_trap_vendors()})
        return True

    # ── /api/traps/categories GET ─────────────────────────────────
    if path == "/api/traps/categories" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from db.trap_defs import db_get_trap_categories
        h._json(200, {"categories": db_get_trap_categories()})
        return True

    # ── /api/events/summary GET ───────────────────────────────────
    if path == "/api/events/summary" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        now = time.time()
        periods = {"1h": 3600, "24h": 86400, "7d": 604800}
        con = None
        try:
            con = sqlite3.connect(LOGS_DB_PATH)
            result = {}
            for label, secs in periods.items():
                cutoff = datetime.datetime.utcfromtimestamp(now - secs).strftime("%Y-%m-%dT%H:%M:%SZ")
                rows = con.execute(
                    "SELECT direction, COUNT(*) FROM flap_log WHERE ts >= ? GROUP BY direction",
                    (cutoff,)
                ).fetchall()
                counts = {r[0]: r[1] for r in rows}
                trap_row = con.execute(
                    "SELECT COUNT(*) FROM snmp_traps WHERE ts >= ?", (cutoff,)
                ).fetchone()
                result[label] = {
                    "down":      counts.get("down", 0),
                    "recovered": counts.get("recovered", 0),
                    "threshold": counts.get("threshold_crit", 0) + counts.get("threshold_warn", 0) + counts.get("threshold_ok", 0),
                    "trap":      trap_row[0] if trap_row else 0,
                }
            h._json(200, {"summary": result})
        except Exception as e:
            log.error(f"Events summary error: {e}")
            h._json(500, {"error": str(e)})
        finally:
            if con: con.close()
        return True

    # ── /api/health/trend GET ────────────────────────────────────
    if path == "/api/health/trend" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from urllib.parse import parse_qs, urlparse as _urlparse
        from db import db_load_availability
        qs          = parse_qs(_urlparse(h.path).query)
        range_param = qs.get("range", ["24h"])[0]
        minutes     = {"1h": 60, "6h": 360, "24h": 1440}.get(range_param, 1440)
        pts         = db_load_availability(minutes)
        cutoff      = datetime.datetime.utcfromtimestamp(
            time.time() - minutes * 60).strftime("%Y-%m-%dT%H:%M:%SZ")
        events = []
        con = None
        try:
            con = sqlite3.connect(LOGS_DB_PATH)
            rows = con.execute(
                "SELECT ts, direction, dname, sname FROM flap_log "
                "WHERE ts >= ? AND direction IN ('down','threshold_crit') ORDER BY ts",
                (cutoff,)
            ).fetchall()
            for ts_str, direction, dname, sname in rows:
                try:
                    dt = datetime.datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
                    epoch = int((dt - datetime.datetime(1970, 1, 1)).total_seconds())
                except Exception:
                    continue
                label = " / ".join(x for x in [dname, sname] if x)
                events.append({
                    "ts":    epoch,
                    "type":  "outage" if direction == "down" else "alert",
                    "label": label,
                })
        except Exception as e:
            log.error(f"health/trend events error: {e}")
        finally:
            if con: con.close()
        h._json(200, {"points": pts, "events": events, "range": range_param})
        return True

    # ── /api/snmp/catalog GET ─────────────────────────────────────
    if path == "/api/snmp/catalog" and method == "GET":
        if not h._auth(): return True
        from snmp.catalog import SNMP_CATALOG
        h._json(200, {"catalog": SNMP_CATALOG})
        return True

    # ── /api/snmp/interfaces POST ─────────────────────────────────
    if path == "/api/snmp/interfaces" and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        from monitoring.probes import snmpwalk_interfaces
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

    # ── /api/flaps/<id>/ack POST ──────────────────────────────────
    if method == "POST" and path.startswith("/api/flaps/") and path.endswith("/ack"):
        user, _ = h._require("operator")
        if not user: return True
        try:
            flap_id = int(path.split("/")[3])
        except (IndexError, ValueError):
            h._json(400, {"error": "invalid id"}); return True
        actor = user or ""
        ok = db_ack_flap(flap_id, actor)
        h._json(200, {"ok": ok})
        return True

    # ── /api/flaps/<id>/resolve POST ──────────────────────────────
    if method == "POST" and path.startswith("/api/flaps/") and path.endswith("/resolve"):
        user, _ = h._require("operator")
        if not user: return True
        try:
            flap_id = int(path.split("/")[3])
        except (IndexError, ValueError):
            h._json(400, {"error": "invalid id"}); return True
        ok = db_resolve_flap(flap_id)
        h._json(200, {"ok": ok})
        return True

    return False
