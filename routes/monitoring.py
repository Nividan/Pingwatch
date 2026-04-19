"""
routes/monitoring.py — Real-time event stream, flap/trap queries, SNMP helpers.

Handles: /events (SSE), /api/flaps, /api/traps,
         /api/snmp/catalog, /api/snmp/interfaces,
         /api/vmware/vms, /api/vmware/metrics.
"""

import datetime
import queue
import sqlite3
import time

import core.app_state as app_state
from core.config import DB_PATH, LOGS_DB_PATH
from db import db_load_flaps, db_load_traps, db_ack_flap, db_resolve_flap, \
               db_sample_buffer_stats
from db.backend import is_pg
from core.logger import log


def _get_flap_sensor(flap_id):
    """Return (did, sid) for a flap_log entry, or None."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute("SELECT did, sid FROM flap_log WHERE id=%s", (flap_id,))
                row = cur.fetchone()
            return (row["did"], row["sid"]) if row else None
        except Exception:
            return None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
        try:
            row = con.execute("SELECT did, sid FROM flap_log WHERE id=?", (flap_id,)).fetchone()
            return (row[0], row[1]) if row else None
        finally:
            con.close()
    except Exception:
        return None


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

    # ── /api/stats/sample-buffer GET ──────────────────────────────
    # Operator-visible health signal for the probe-sample buffer. When the
    # writer queue stalls, the oldest buffered row is dropped; this endpoint
    # makes those drops observable without grep'ing logs.
    if path == "/api/stats/sample-buffer" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, db_sample_buffer_stats())
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
        if is_pg():
            from db.pg_pool import pg_cursor
            try:
                with pg_cursor("logs") as cur:
                    result = {}
                    for label, secs in periods.items():
                        cutoff = datetime.datetime.utcfromtimestamp(now - secs).strftime("%Y-%m-%dT%H:%M:%SZ")
                        cur.execute(
                            "SELECT direction, COUNT(*) AS cnt FROM flap_log WHERE ts >= %s GROUP BY direction",
                            (cutoff,)
                        )
                        counts = {r["direction"]: r["cnt"] for r in cur.fetchall()}
                        cur.execute(
                            "SELECT COUNT(*) AS cnt FROM snmp_traps WHERE ts >= %s", (cutoff,)
                        )
                        trap_row = cur.fetchone()
                        result[label] = {
                            "down":      counts.get("down", 0),
                            "recovered": counts.get("recovered", 0),
                            "threshold": counts.get("threshold_crit", 0) + counts.get("threshold_warn", 0) + counts.get("threshold_ok", 0),
                            "trap":      trap_row["cnt"] if trap_row else 0,
                        }
                h._json(200, {"summary": result})
            except Exception as e:
                h._error(500, "Failed to load events summary", e, context="events_summary_pg")
            return True
        # SQLite
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
            h._error(500, "Failed to load events summary", e, context="events_summary_sqlite")
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
        if is_pg():
            from db.pg_pool import pg_cursor
            try:
                with pg_cursor("logs") as cur:
                    cur.execute(
                        "SELECT ts, direction, dname, sname FROM flap_log "
                        "WHERE ts >= %s AND direction IN ('down','threshold_crit') ORDER BY ts",
                        (cutoff,)
                    )
                    for r in cur.fetchall():
                        try:
                            dt = datetime.datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ")
                            epoch = int((dt - datetime.datetime(1970, 1, 1)).total_seconds())
                        except Exception:
                            continue
                        label = " / ".join(x for x in [r["dname"], r["sname"]] if x)
                        events.append({
                            "ts":    epoch,
                            "type":  "outage" if r["direction"] == "down" else "alert",
                            "label": label,
                        })
            except Exception as e:
                log.error(f"health/trend events error: {e}")
        else:
            # SQLite
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
        try:
            _port = int(body.get("port") or 161)
        except (TypeError, ValueError):
            h._json(400, {"error": "port must be an integer"}); return True
        _ver  = (body.get("version")   or "2c").strip()
        if not _host:
            h._json(400, {"error": "host required"}); return True
        if not (1 <= _port <= 65535):
            h._json(400, {"error": "port must be 1-65535"}); return True
        if _ver not in ("1", "2c", "3"):
            h._json(400, {"error": "version must be 1, 2c, or 3"}); return True
        _ifaces = snmpwalk_interfaces(_host, _comm, _port, timeout=10, version=_ver)
        if _ifaces is None:
            h._json(503, {"error": "snmpwalk not found — install net-snmp"}); return True
        h._json(200, {"interfaces": _ifaces})
        return True

    # ── /api/snmp/reenrich POST (admin) ───────────────────────────
    # Walks historical snmp_traps rows with empty trap_name and looks them up
    # in trap_definitions (populated from MIBs at startup). Useful after adding
    # new MIB files — existing rows aren't re-enriched at receive time.
    if path == "/api/snmp/reenrich" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        from db.trap_defs import db_lookup_trap
        from db import db_log_audit
        scanned = 0
        updated = 0
        try:
            if is_pg():
                from db.pg_pool import pg_cursor, pg_conn
                with pg_cursor("logs") as cur:
                    cur.execute(
                        "SELECT id, trap_oid FROM snmp_traps "
                        "WHERE (trap_name IS NULL OR trap_name='') "
                        "AND trap_oid IS NOT NULL AND trap_oid <> ''"
                    )
                    rows = [(r["id"], r["trap_oid"]) for r in cur.fetchall()]
                scanned = len(rows)
                if rows:
                    with pg_conn("logs") as con:
                        cur = con.cursor()
                        for rid, oid in rows:
                            defn = db_lookup_trap(oid)
                            if not defn or not defn.get("trap_name"):
                                continue
                            cur.execute(
                                "UPDATE snmp_traps SET trap_name=%s, vendor=%s, "
                                "severity=%s, category=%s, probable_cause=%s, "
                                "recommended_action=%s WHERE id=%s",
                                (
                                    defn.get("trap_name", ""),
                                    defn.get("vendor", "") or "Unknown",
                                    defn.get("severity", "info") or "info",
                                    defn.get("category", ""),
                                    (defn.get("probable_cause", "") or "")[:500],
                                    (defn.get("recommended_action", "") or "")[:500],
                                    rid,
                                )
                            )
                            updated += 1
            else:
                con = None
                try:
                    con = sqlite3.connect(LOGS_DB_PATH, timeout=30)
                    rows = con.execute(
                        "SELECT id, trap_oid FROM snmp_traps "
                        "WHERE (trap_name IS NULL OR trap_name='') "
                        "AND trap_oid IS NOT NULL AND trap_oid <> ''"
                    ).fetchall()
                    scanned = len(rows)
                    for rid, oid in rows:
                        defn = db_lookup_trap(oid)
                        if not defn or not defn.get("trap_name"):
                            continue
                        con.execute(
                            "UPDATE snmp_traps SET trap_name=?, vendor=?, "
                            "severity=?, category=?, probable_cause=?, "
                            "recommended_action=? WHERE id=?",
                            (
                                defn.get("trap_name", ""),
                                defn.get("vendor", "") or "Unknown",
                                defn.get("severity", "info") or "info",
                                defn.get("category", ""),
                                (defn.get("probable_cause", "") or "")[:500],
                                (defn.get("recommended_action", "") or "")[:500],
                                rid,
                            )
                        )
                        updated += 1
                    con.commit()
                finally:
                    if con: con.close()
        except Exception as e:
            log.error(f"snmp reenrich error: {e}")
            h._json(500, {"error": "re-enrichment failed — see server log"})
            return True
        db_log_audit(user, h.client_address[0], "snmp_reenrich", "",
                     f"scanned={scanned} updated={updated}")
        log.info(f"SNMP re-enrichment: scanned={scanned}, updated={updated} (by {user})")
        h._json(200, {"scanned": scanned, "updated": updated})
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
        if ok:
            # Propagate ACK to matching alert events
            _flap_sensor = _get_flap_sensor(flap_id)
            if _flap_sensor:
                from db.alert_events import db_ack_events_by_sensor
                db_ack_events_by_sensor(_flap_sensor[0], _flap_sensor[1], actor)
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
        if ok:
            # Propagate resolve to matching alert events
            _flap_sensor = _get_flap_sensor(flap_id)
            if _flap_sensor:
                from db.alert_events import db_resolve_events_by_sensor
                db_resolve_events_by_sensor(_flap_sensor[0], _flap_sensor[1])
        h._json(200, {"ok": ok})
        return True

    # ── /api/vmware/metrics GET ─────────────────────────────────
    if path == "/api/vmware/metrics" and method == "GET":
        if not h._auth(): return True
        from vmware.client import VM_METRICS
        h._json(200, {"metrics": [
            {"v": m["v"], "l": m["l"], "unit": m["unit"], "group": m["group"]}
            for m in VM_METRICS
        ]})
        return True

    # ── /api/vmware/vms POST ─────────────────────────────────────
    if path == "/api/vmware/vms" and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        _host = (body.get("host") or "").strip()
        _user = (body.get("username") or "").strip()
        _pw   = body.get("password") or ""
        _vssl = body.get("verify_ssl", False)
        try:
            _port = int(body.get("port") or 443)
        except (TypeError, ValueError):
            h._json(400, {"error": "port must be an integer"}); return True
        if not _host:
            h._json(400, {"error": "host required"}); return True
        if not _user:
            h._json(400, {"error": "username required"}); return True
        # Fall back to device-level stored password
        if not _pw:
            _did = body.get("did", "")
            if _did:
                _dev = STATE.devices.get(_did)
                if _dev and _dev.vmware_password_default:
                    from db.backups import decrypt_pw
                    _pw = decrypt_pw(_dev.vmware_password_default)
        if not _pw:
            h._json(400, {"error": "password required"}); return True
        try:
            from vmware import vmware_discover_vms
            _vms = vmware_discover_vms(_host, _user, _pw, port=_port, verify_ssl=_vssl)
        except RuntimeError as e:
            h._json(503, {"error": str(e)}); return True
        except PermissionError:
            h._json(401, {"error": "Authentication failed"}); return True
        except ConnectionError as e:
            h._json(502, {"error": str(e)}); return True
        except Exception as e:
            log.error(f"VMware discovery error: {e}")
            h._json(500, {"error": "VMware connection failed"}); return True
        h._json(200, {"vms": _vms})
        return True

    # ── /api/vmware/host-metrics GET ────────────────────────────────
    if path == "/api/vmware/host-metrics" and method == "GET":
        if not h._auth(): return True
        from vmware.client import HOST_METRICS
        h._json(200, {"metrics": [
            {"v": m["v"], "l": m["l"], "unit": m["unit"], "group": m["group"]}
            for m in HOST_METRICS
        ]})
        return True

    # ── /api/vmware/datastore-metrics GET ───────────────────────────
    if path == "/api/vmware/datastore-metrics" and method == "GET":
        if not h._auth(): return True
        from vmware.client import DATASTORE_METRICS
        h._json(200, {"metrics": [
            {"v": m["v"], "l": m["l"], "unit": m["unit"], "group": m["group"]}
            for m in DATASTORE_METRICS
        ]})
        return True

    # ── /api/vmware/datastores POST ─────────────────────────────────
    if path == "/api/vmware/datastores" and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        _host = (body.get("host") or "").strip()
        _user = (body.get("username") or "").strip()
        _pw   = body.get("password") or ""
        _vssl = body.get("verify_ssl", False)
        try:
            _port = int(body.get("port") or 443)
        except (TypeError, ValueError):
            h._json(400, {"error": "port must be an integer"}); return True
        if not _host:
            h._json(400, {"error": "host required"}); return True
        if not _user:
            h._json(400, {"error": "username required"}); return True
        if not _pw:
            _did = body.get("did", "")
            if _did:
                _dev = STATE.devices.get(_did)
                if _dev and _dev.vmware_password_default:
                    from db.backups import decrypt_pw
                    _pw = decrypt_pw(_dev.vmware_password_default)
        if not _pw:
            h._json(400, {"error": "password required"}); return True
        try:
            from vmware import vmware_discover_datastores
            _ds = vmware_discover_datastores(_host, _user, _pw, port=_port, verify_ssl=_vssl)
        except RuntimeError as e:
            h._json(503, {"error": str(e)}); return True
        except PermissionError:
            h._json(401, {"error": "Authentication failed"}); return True
        except ConnectionError as e:
            h._json(502, {"error": str(e)}); return True
        except Exception as e:
            log.error(f"VMware datastore discovery error: {e}")
            h._json(500, {"error": "VMware connection failed"}); return True
        h._json(200, {"datastores": _ds})
        return True

    # ── /api/vmware/hosts POST ──────────────────────────────────────
    if path == "/api/vmware/hosts" and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        _host = (body.get("host") or "").strip()
        _user = (body.get("username") or "").strip()
        _pw   = body.get("password") or ""
        _vssl = body.get("verify_ssl", False)
        try:
            _port = int(body.get("port") or 443)
        except (TypeError, ValueError):
            h._json(400, {"error": "port must be an integer"}); return True
        if not _host:
            h._json(400, {"error": "host required"}); return True
        if not _user:
            h._json(400, {"error": "username required"}); return True
        if not _pw:
            _did = body.get("did", "")
            if _did:
                _dev = STATE.devices.get(_did)
                if _dev and _dev.vmware_password_default:
                    from db.backups import decrypt_pw
                    _pw = decrypt_pw(_dev.vmware_password_default)
        if not _pw:
            h._json(400, {"error": "password required"}); return True
        try:
            from vmware import vmware_discover_hosts
            _hosts = vmware_discover_hosts(_host, _user, _pw, port=_port, verify_ssl=_vssl)
        except RuntimeError as e:
            h._json(503, {"error": str(e)}); return True
        except PermissionError:
            h._json(401, {"error": "Authentication failed"}); return True
        except ConnectionError as e:
            h._json(502, {"error": str(e)}); return True
        except Exception as e:
            log.error(f"VMware host discovery error: {e}")
            h._json(500, {"error": "VMware connection failed"}); return True
        h._json(200, {"hosts": _hosts})
        return True

    return False
