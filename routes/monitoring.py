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


def _mark_sensor_ack(STATE, did, sid, actor):
    """Reflect an event ACK on the live sensor so tiles render the muted
    "acknowledged-down" state. Only sticks while an incident is active —
    ACKing a historical row of a healthy sensor changes nothing. Cleared
    automatically on recovery (core/state.py). The updated sensor dict is
    broadcast so open browsers flip the tile instantly."""
    import time as _t
    with STATE._lock:
        dev = STATE.devices.get(did)
        s = dev.sensors.get(sid) if dev else None
    if not s:
        return
    if not (s._alerted_down or s._threshold_state in ("warn", "crit")):
        return
    s._ack_by = actor or ""
    s._ack_at = _t.time()
    try:
        STATE._broadcast("sensor", s.to_dict())
    except Exception:
        pass


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


# ── SNMP sensor template validation/cleaning ─────────────────────────
_SNMP_TPL_KINDS = ("scalar", "table", "percent")
_SNMP_PCT_MODES = ("used_total", "used_free", "free_total")


def _is_oid(s: str) -> bool:
    """Loose OID check: digits + dots, starts with a digit. Table item bases
    may carry a trailing dot (index appended at discovery time)."""
    s = (s or "").strip()
    if not s or s[0] not in "0123456789":
        return False
    return all(c in "0123456789." for c in s)


def _clean_num(v):
    """Return a number or None (blank item threshold/interval → default)."""
    if v is None or v == "":
        return None
    try:
        return float(v) if "." in str(v) else int(v)
    except (TypeError, ValueError):
        return None


def _snmp_tpl_clean_items(raw) -> list:
    """Sanitize the items list. Drops malformed items rather than failing the
    whole save — the validator has already rejected an empty/entirely-invalid
    list."""
    out = []
    if not isinstance(raw, list):
        return out
    for it in raw[:500]:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "scalar").strip().lower()
        if kind not in _SNMP_TPL_KINDS:
            continue
        oid = str(it.get("oid") or "").strip()
        label = str(it.get("label") or "").strip()[:120]
        if not label or not _is_oid(oid):
            continue
        item = {
            "kind": kind,
            "label": label,
            "oid": oid[:255],
            "unit": str(it.get("unit") or "").strip()[:64],
            "warn": _clean_num(it.get("warn")),
            "crit": _clean_num(it.get("crit")),
            "interval": _clean_num(it.get("interval")),
        }
        # Static value scale divisor (deci-°C → 10, KB→MB → 1024); > 0 only.
        _sc = _clean_num(it.get("scale"))
        if _sc and _sc > 0:
            item["scale"] = _sc
        # Computed-%: partner OID + combination mode. Valid on kind="percent"
        # (scalar pair) and on kind="table" (per-row pair, walked).
        oid2 = str(it.get("oid2") or "").strip()
        mode = str(it.get("percent_mode") or "").strip()
        if kind == "percent":
            if not _is_oid(oid2):
                continue   # a percent item without a valid partner is useless
            item["oid2"] = oid2[:255]
            item["percent_mode"] = mode if mode in _SNMP_PCT_MODES else "used_total"
        if kind == "table":
            if _is_oid(oid2):
                item["oid2"] = oid2[:255]
                item["percent_mode"] = mode if mode in _SNMP_PCT_MODES else "used_total"
            for k in ("name_oid", "name_oid2", "speed_oid", "hc_variant_oid",
                      "scale_oid", "precision_oid"):
                v = str(it.get(k) or "").strip()
                if v:
                    item[k] = v[:255]
            item["speed_auto_threshold"] = bool(it.get("speed_auto_threshold"))
        out.append(item)
    return out


def _snmp_tpl_validate(body: dict) -> str:
    """Return an error string, or "" if the payload is a valid template."""
    name = str(body.get("name") or "").strip()
    if not name:
        return "name is required"
    if len(name) > 120:
        return "name too long (max 120)"
    items = _snmp_tpl_clean_items(body.get("items"))
    if not items:
        return "at least one valid item (label + OID) is required"
    return ""


def _snmp_tpl_clean(body: dict) -> dict:
    return {
        "name":        str(body.get("name") or "").strip()[:120],
        "vendor":      str(body.get("vendor") or "").strip()[:80],
        "description": str(body.get("description") or "").strip()[:500],
        "items":       _snmp_tpl_clean_items(body.get("items")),
        "enabled":     bool(body.get("enabled", True)),
    }


def _snmp_tpl_audit(h, user, action, detail):
    try:
        from db import db_log_audit
        db_log_audit(user, h.client_address[0], action, str(detail)[:200])
    except Exception:
        pass


# ── Shared SNMP discovery plumbing (interfaces + template discover + scan) ──

def _snmp_v3_creds(body, dev):
    """Build the v3 creds dict from request fields, falling back to the device's
    stored defaults for any blank field. Returns (creds, err)."""
    _V3_LEVELS = {"noAuthNoPriv", "authNoPriv", "authPriv"}
    _V3_AUTH   = {"", "MD5", "SHA", "SHA-224", "SHA-256", "SHA-384", "SHA-512"}
    _V3_PRIV   = {"", "DES", "AES", "AES-192", "AES-256"}
    level = (body.get("snmp_v3_level") or "noAuthNoPriv").strip()
    if level not in _V3_LEVELS:
        return None, "invalid snmp_v3_level"
    aproto = (body.get("snmp_v3_auth_proto") or "").strip()
    if aproto not in _V3_AUTH:
        return None, "invalid snmp_v3_auth_proto"
    pproto = (body.get("snmp_v3_priv_proto") or "").strip()
    if pproto not in _V3_PRIV:
        return None, "invalid snmp_v3_priv_proto"
    apw = body.get("snmp_v3_auth_pass") or ""
    ppw = body.get("snmp_v3_priv_pass") or ""
    from db.backups import decrypt_pw
    g = lambda a: (getattr(dev, a, "") if dev else "")
    return {
        "user":       (body.get("snmp_v3_user") or "").strip() or g("snmp_v3_user_default"),
        "level":      level,
        "auth_proto": aproto or g("snmp_v3_auth_proto_default"),
        "auth_pass":  apw if apw else (decrypt_pw(g("snmp_v3_auth_pass_default")) if dev else ""),
        "priv_proto": pproto or g("snmp_v3_priv_proto_default"),
        "priv_pass":  ppw if ppw else (decrypt_pw(g("snmp_v3_priv_pass_default")) if dev else ""),
        "context":    (body.get("snmp_v3_context") or "").strip() or g("snmp_v3_context_default"),
    }, None


def _snmp_conn_params(body):
    """Parse + validate host/community/port/version/v3_creds from a discovery
    request. Resolves the device (by `did`) for probe routing + v3 defaults.
    Returns (params, err) — err is a user-facing string or None."""
    host = (body.get("host") or "").strip()
    comm = (body.get("community") or "public").strip()
    try:
        port = int(body.get("port") or 161)
    except (TypeError, ValueError):
        return None, "port must be an integer"
    ver = (body.get("version") or "2c").strip()
    if not host:
        return None, "host required"
    if not (1 <= port <= 65535):
        return None, "port must be 1-65535"
    if ver not in ("1", "2c", "3"):
        return None, "version must be 1, 2c, or 3"
    did = (body.get("did") or "").strip()
    dev = None
    if did:
        from core.app_state import STATE
        dev = STATE.devices.get(did) if STATE else None
    v3 = None
    if ver == "3":
        v3, verr = _snmp_v3_creds(body, dev)
        if verr:
            return None, verr
    return {"host": host, "community": comm, "port": port, "version": ver,
            "v3_creds": v3, "device": dev, "did": did}, None


def _snmp_all_known_items():
    """Aggregate every known SNMP item for the device-scan flow: all templates'
    items (built-in + user) ∪ the SNMP_CATALOG scalars, deduped by OID and tagged
    with a vendor `group` for grouped display. Deduping matters — many templates
    share sysUpTime / ifTable bases, and probing each once keeps the scan cheap."""
    seen = set()
    items = []
    try:
        from db import db_list_snmp_templates
        for tpl in db_list_snmp_templates():
            grp = tpl.get("vendor") or tpl.get("name") or ""
            for it in (tpl.get("items") or []):
                oid = (it.get("oid") or "").strip()
                if not oid or oid in seen:
                    continue
                seen.add(oid)
                d = dict(it)
                d["group"] = grp
                items.append(d)
    except Exception as e:
        log.error(f"snmp scan aggregate (templates): {e}")
    try:
        from snmp.catalog import SNMP_CATALOG
        for group in SNMP_CATALOG:
            grp = group.get("vendor") or ""
            for e in (group.get("oids") or []):
                oid = (e.get("oid") or "").strip()
                if not oid or oid in seen:
                    continue
                seen.add(oid)
                items.append({"kind": "scalar", "label": e.get("label") or oid,
                              "oid": oid, "unit": e.get("unit") or "", "group": grp})
    except Exception as e:
        log.error(f"snmp scan aggregate (catalog): {e}")
    return items


def _snmp_route(params):
    """Decide where a discovery walk runs for the resolved device. Discovery on
    a probe-bound device MUST run on the probe (central usually can't reach it),
    never falling back to a local walk. Returns one of:
        ("local", None)          — run on central
        ("probe", probe_dict)    — run on the device's enrolled probe
        ("error", (status, msg)) — probe unusable; caller returns the error."""
    dev = params.get("device")
    if not dev:
        return ("local", None)
    from core.probe_assign import effective_probe
    pid = effective_probe(dev)
    if not pid:
        return ("local", None)
    from db.probes import db_get_probe
    probe = db_get_probe(pid)
    pname = (probe or {}).get("name") or pid
    if not probe or probe.get("status") != "enrolled":
        return ("error", (409, f"Probe '{pname}' is not enrolled — "
                          "this device can only be discovered from there"))
    if time.time() - float(probe.get("last_seen") or 0) > 35:
        return ("error", (503, f"Probe '{pname}' is offline — "
                          "this device can only be discovered from there"))
    return ("probe", probe)


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
                    if msg is None:
                        # Evicted by the broadcaster (slow client / cap / sweep).
                        # Close the stream so EventSource reconnects instead of
                        # heartbeating a queue nobody fans out to anymore.
                        break
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
        # hour_ts -> set of (did, sid) with any down / threshold_crit event
        # in that hour. Used below to dip the trend line proportional to how
        # many distinct sensors were unhealthy, since the raw sample-based
        # pct from db_load_availability is dominated by the huge denominator
        # of healthy samples and treats threshold breaches as ok (the probe
        # succeeded, the value just crossed a line).
        hour_affected = {}
        def _bucket(epoch):
            return (epoch // 3600) * 3600
        if is_pg():
            from db.pg_pool import pg_cursor
            try:
                with pg_cursor("logs") as cur:
                    cur.execute(
                        "SELECT ts, direction, dname, sname, did, sid FROM flap_log "
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
                        hour_affected.setdefault(_bucket(epoch), set()).add(
                            (r["did"] or "", r["sid"] or "")
                        )
            except Exception as e:
                log.error(f"health/trend events error: {e}")
        else:
            # SQLite
            con = None
            try:
                con = sqlite3.connect(LOGS_DB_PATH)
                rows = con.execute(
                    "SELECT ts, direction, dname, sname, did, sid FROM flap_log "
                    "WHERE ts >= ? AND direction IN ('down','threshold_crit') ORDER BY ts",
                    (cutoff,)
                ).fetchall()
                for ts_str, direction, dname, sname, did, sid in rows:
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
                    hour_affected.setdefault(_bucket(epoch), set()).add(
                        (did or "", sid or "")
                    )
            except Exception as e:
                log.error(f"health/trend events error: {e}")
            finally:
                if con: con.close()

        # Sensor-impact penalty: for each hour bucket with any flap, the pct
        # becomes min(sample_pct, sensor_pct). sensor_pct treats each affected
        # sensor as a 1/total reduction in fleet health — so 4 sensors out of
        # 88 going down or breaching CRIT for an hour shows as ~95.5%, not
        # the 99.8% the sample-aggregate would yield.
        if pts and hour_affected:
            total_active = 0
            try:
                with STATE._lock:
                    for dev in STATE.devices.values():
                        for s in dev.sensors.values():
                            if getattr(s, "running", True):
                                total_active += 1
            except Exception:
                total_active = 0
            if total_active > 0:
                for p in pts:
                    affected = len(hour_affected.get(int(p["ts"]), ()))
                    if affected:
                        sensor_pct = round(100.0 * (1.0 - affected / total_active), 1)
                        if sensor_pct < p["pct"]:
                            p["pct"] = max(0.0, sensor_pct)

        h._json(200, {"points": pts, "events": events, "range": range_param})
        return True

    # ── /api/snmp/catalog GET ─────────────────────────────────────
    if path == "/api/snmp/catalog" and method == "GET":
        if not h._auth(): return True
        from snmp.catalog import SNMP_CATALOG
        h._json(200, {"catalog": SNMP_CATALOG})
        return True

    # ── /api/snmp/templates — SNMP sensor template CRUD ───────────
    # GET: list (viewer — feeds the Add Sensor picker + Settings tab).
    # POST/PATCH/DELETE: admin (config management).
    if path == "/api/snmp/templates" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from db import db_list_snmp_templates
        h._json(200, {"templates": db_list_snmp_templates()})
        return True

    if path == "/api/snmp/templates" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        err = _snmp_tpl_validate(body)
        if err:
            h._json(400, {"error": err}); return True
        from db import db_create_snmp_template, db_get_snmp_template
        tid = db_create_snmp_template(_snmp_tpl_clean(body), created_by=user)
        if not tid:
            h._json(500, {"error": "failed to create template"}); return True
        _snmp_tpl_audit(h, user, "snmp_template_create", body.get("name", ""))
        h._json(201, {"id": tid, "template": db_get_snmp_template(tid)})
        return True

    if path.startswith("/api/snmp/templates/") and method in ("PATCH", "DELETE"):
        tid = path[len("/api/snmp/templates/"):]
        if not tid or "/" in tid:
            h._json(404, {"error": "not found"}); return True
        user, _ = h._require("admin")
        if not user: return True
        from db import (db_get_snmp_template, db_update_snmp_template,
                        db_delete_snmp_template)
        existing = db_get_snmp_template(tid)
        if not existing:
            h._json(404, {"error": "template not found"}); return True
        if method == "DELETE":
            db_delete_snmp_template(tid)
            _snmp_tpl_audit(h, user, "snmp_template_delete", existing.get("name", ""))
            # Deleting a built-in = "Reset to default": re-seed the shipped
            # version immediately so it's back without a server restart.
            restored = None
            bkey = (existing.get("builtin_key") or "").strip()
            if existing.get("source") == "builtin" and bkey:
                try:
                    from db import db_seed_snmp_templates
                    from snmp.seeds.snmp_templates import SNMP_SENSOR_TEMPLATES
                    shipped = [t for t in SNMP_SENSOR_TEMPLATES
                               if (t.get("builtin_key") or "") == bkey]
                    if shipped:
                        db_seed_snmp_templates(shipped)
                        restored = True
                except Exception as e:
                    log.error(f"snmp template reset re-seed failed: {e}")
            h._json(200, {"ok": True, "restored": bool(restored)}); return True
        err = _snmp_tpl_validate(body)
        if err:
            h._json(400, {"error": err}); return True
        if not db_update_snmp_template(tid, _snmp_tpl_clean(body)):
            h._json(500, {"error": "failed to update template"}); return True
        _snmp_tpl_audit(h, user, "snmp_template_update", body.get("name", ""))
        h._json(200, {"template": db_get_snmp_template(tid)}); return True

    # ── /api/snmp/interfaces POST ─────────────────────────────────
    # Discover a device's interfaces. Routes to the device's remote probe when
    # it's probe-bound (central usually can't reach a branch device).
    if path == "/api/snmp/interfaces" and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        params, perr = _snmp_conn_params(body)
        if perr:
            h._json(400, {"error": perr}); return True
        mode, val = _snmp_route(params)
        if mode == "error":
            h._json(val[0], {"error": val[1]}); return True
        if mode == "probe":
            from routes.agent import run_remote_snmp_interfaces
            _ifaces, _err = run_remote_snmp_interfaces(
                val, params["host"], params["community"], params["port"],
                params["version"], params["v3_creds"], user)
            if _ifaces is None:
                h._json(502, {"error": _err or "remote discovery failed"}); return True
            h._json(200, {"interfaces": _ifaces, "via_probe": val["name"]}); return True
        from monitoring.probes import snmpwalk_interfaces
        _ifaces = snmpwalk_interfaces(params["host"], params["community"],
                                      params["port"], timeout=10,
                                      version=params["version"], v3_creds=params["v3_creds"])
        if _ifaces is None:
            h._json(503, {"error": "snmpwalk not found — install net-snmp"}); return True
        h._json(200, {"interfaces": _ifaces})
        return True

    # ── /api/snmp/discover POST — discover a device against a template ─
    # Body: {template_id, host, community, port, version, did, snmp_v3_*}.
    # Returns only the template metrics that responded, as candidate rows.
    if path == "/api/snmp/discover" and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        tpl_id = (body.get("template_id") or "").strip()
        if not tpl_id:
            h._json(400, {"error": "template_id required"}); return True
        from db import db_get_snmp_template
        tpl = db_get_snmp_template(tpl_id)
        if not tpl:
            h._json(404, {"error": "template not found"}); return True
        items = tpl.get("items") or []
        if not items:
            h._json(400, {"error": "template has no items"}); return True
        params, perr = _snmp_conn_params(body)
        if perr:
            h._json(400, {"error": perr}); return True
        mode, val = _snmp_route(params)
        if mode == "error":
            h._json(val[0], {"error": val[1]}); return True
        if mode == "probe":
            from routes.agent import run_remote_snmp_discover
            cands, _err = run_remote_snmp_discover(
                val, params["host"], items, params["community"], params["port"],
                params["version"], params["v3_creds"], user)
            if cands is None:
                h._json(502, {"error": _err or "remote discovery failed"}); return True
            h._json(200, {"candidates": cands, "via_probe": val["name"]}); return True
        from monitoring.probes import snmp_discover_template
        cands = snmp_discover_template(params["host"], items, params["community"],
                                       params["port"], timeout=10,
                                       version=params["version"], v3_creds=params["v3_creds"])
        if cands is None:
            h._json(503, {"error": "snmpwalk not found — install net-snmp"}); return True
        h._json(200, {"candidates": cands})
        return True

    # ── /api/snmp/scan POST — probe a device against ALL known OIDs ────
    # Authoring aid: discovers every catalog/template metric the device supports
    # so the user can save the responders as a new template. Uses a short per-OID
    # timeout + higher waiter budget since the item set is large.
    if path == "/api/snmp/scan" and method == "POST":
        user, _ = h._require("admin")   # authoring tool → admin, like template CRUD
        if not user: return True
        params, perr = _snmp_conn_params(body)
        if perr:
            h._json(400, {"error": perr}); return True
        items = _snmp_all_known_items()
        if not items:
            h._json(200, {"candidates": []}); return True
        mode, val = _snmp_route(params)
        if mode == "error":
            h._json(val[0], {"error": val[1]}); return True
        if mode == "probe":
            from routes.agent import run_remote_snmp_discover
            cands, _err = run_remote_snmp_discover(
                val, params["host"], items, params["community"], params["port"],
                params["version"], params["v3_creds"], user,
                timeout=50.0, op_timeout=3)
            if cands is None:
                h._json(502, {"error": _err or "remote scan failed"}); return True
            h._json(200, {"candidates": cands, "via_probe": val["name"]}); return True
        from monitoring.probes import snmp_discover_template
        cands = snmp_discover_template(params["host"], items, params["community"],
                                       params["port"], timeout=3,
                                       version=params["version"], v3_creds=params["v3_creds"])
        if cands is None:
            h._json(503, {"error": "snmpwalk not found — install net-snmp"}); return True
        h._json(200, {"candidates": cands})
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
                # Reflect on the live sensor → "acknowledged-down" tile state
                _mark_sensor_ack(STATE, _flap_sensor[0], _flap_sensor[1], actor)
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
            log.warning(f"VMware discover RuntimeError for {_host}: {e}")
            h._json(503, {"error": "VMware connection failed"}); return True
        except PermissionError:
            h._json(401, {"error": "Authentication failed"}); return True
        except ConnectionError as e:
            log.warning(f"VMware discover ConnectionError for {_host}: {e}")
            h._json(502, {"error": "Cannot connect to vCenter"}); return True
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
