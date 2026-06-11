"""
db/events.py — Flap log, SNMP trap log, and sensor error log helpers.
"""

import sqlite3

from core.config  import LOGS_DB_PATH
from core.logger  import log
from db.backend   import is_pg
import core.settings as _settings


# ── Error log ────────────────────────────────────────────────────

def db_log_err(did, sid, sname, stype, msg, ts):
    """Append a sensor error entry; keep at most 1 000 per sensor."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "INSERT INTO sensor_err_log (ts,did,sid,sname,stype,msg) VALUES (%s,%s,%s,%s,%s,%s)",
                    (ts, did, sid, sname, stype, msg)
                )
                cur.execute("""
                    DELETE FROM sensor_err_log WHERE did=%s AND sid=%s
                      AND id NOT IN (
                        SELECT id FROM sensor_err_log WHERE did=%s AND sid=%s
                        ORDER BY id DESC LIMIT 1000
                      )""", (did, sid, did, sid))
        except Exception as e:
            log.error(f"DB err log error: {e}")
        return
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
        con.execute(
            "INSERT INTO sensor_err_log (ts,did,sid,sname,stype,msg) VALUES (?,?,?,?,?,?)",
            (ts, did, sid, sname, stype, msg)
        )
        con.execute("""
            DELETE FROM sensor_err_log WHERE did=? AND sid=?
              AND id NOT IN (
                SELECT id FROM sensor_err_log WHERE did=? AND sid=?
                ORDER BY id DESC LIMIT 1000
              )""", (did, sid, did, sid))
        con.commit()
    except Exception as e:
        log.error(f"DB err log error: {e}")
    finally:
        if con:
            con.close()


def db_load_err_logs(did):
    """Return last 200 error entries for a device's sensors, newest first."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "SELECT ts,did,sid,sname,stype,msg FROM sensor_err_log "
                    "WHERE did=%s ORDER BY id DESC LIMIT 200", (did,)
                )
                rows = cur.fetchall()
            return [{"ts": r["ts"], "did": r["did"], "sid": r["sid"],
                     "sname": r["sname"], "stype": r["stype"], "msg": r["msg"], "type": "err"}
                    for r in rows]
        except Exception as e:
            log.error(f"DB load err logs error: {e}")
            return []
    # SQLite
    con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
    try:
        rows = con.execute(
            "SELECT ts,did,sid,sname,stype,msg FROM sensor_err_log "
            "WHERE did=? ORDER BY id DESC LIMIT 200", (did,)
        ).fetchall()
        return [{"ts": r[0], "did": r[1], "sid": r[2],
                 "sname": r[3], "stype": r[4], "msg": r[5], "type": "err"}
                for r in rows]
    except Exception as e:
        log.error(f"DB load err logs error: {e}")
        return []
    finally:
        con.close()


def db_clear_err_logs(did):
    """Delete all sensor error logs for a device."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute("DELETE FROM sensor_err_log WHERE did=%s", (did,))
        except Exception as e:
            log.error(f"DB clear err logs error: {e}")
        return
    # SQLite
    con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
    try:
        con.execute("DELETE FROM sensor_err_log WHERE did=?", (did,))
        con.commit()
    except Exception as e:
        log.error(f"DB clear err logs error: {e}")
    finally:
        con.close()


def db_clear_sensor_err_logs(did, sid):
    """Delete all sensor error logs for a specific sensor."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute("DELETE FROM sensor_err_log WHERE did=%s AND sid=%s", (did, sid))
        except Exception as e:
            log.error(f"DB clear sensor err logs error: {e}")
        return
    # SQLite
    con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
    try:
        con.execute("DELETE FROM sensor_err_log WHERE did=? AND sid=?", (did, sid))
        con.commit()
    except Exception as e:
        log.error(f"DB clear sensor err logs error: {e}")
    finally:
        con.close()


# ── Flap log ─────────────────────────────────────────────────────

def db_log_flap(flap):
    """Append a flap/recovery event.

    Retention: cleanup trims resolved entries down to `max_flap_entries`
    (default 2000, settable in Settings → Retention). Active and acknowledged
    rows are never evicted — they must stay visible until resolved.
    """
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "INSERT INTO flap_log (ts,did,sid,dname,sname,host,stype,detail,direction,raw_data) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (flap["ts"], flap["did"], flap["sid"], flap.get("dname", ""),
                     flap.get("sname", ""), flap.get("host", ""),
                     flap.get("stype", ""), flap.get("detail", ""),
                     flap.get("direction", "down"), flap.get("raw_data") or None)
                )
                _flap_limit = max(50, int(_settings.get('max_flap_entries', 2000)))
                # LRU trim resolved entries only — never evict active/acknowledged
                # rows (ACK means "keep visible until resolved"; licenses rarely
                # transition so their single crit flap would otherwise get
                # silently purged once 500 newer flaps pile up).
                cur.execute(
                    "DELETE FROM flap_log WHERE ack_state='resolved' AND id NOT IN "
                    "(SELECT id FROM flap_log WHERE ack_state='resolved' "
                    "ORDER BY id DESC LIMIT %s)",
                    (_flap_limit,)
                )
        except Exception as e:
            log.error(f"DB flap log error: {e}")
        return
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
        con.execute(
            "INSERT INTO flap_log (ts,did,sid,dname,sname,host,stype,detail,direction,raw_data) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (flap["ts"], flap["did"], flap["sid"], flap.get("dname", ""),
             flap.get("sname", ""), flap.get("host", ""),
             flap.get("stype", ""), flap.get("detail", ""),
             flap.get("direction", "down"), flap.get("raw_data") or None)
        )
        _flap_limit = max(50, int(_settings.get('max_flap_entries', 2000)))
        # LRU trim resolved entries only — see PG branch above for rationale.
        con.execute(
            "DELETE FROM flap_log WHERE ack_state='resolved' AND id NOT IN "
            "(SELECT id FROM flap_log WHERE ack_state='resolved' "
            "ORDER BY id DESC LIMIT ?)",
            (_flap_limit,)
        )
        con.commit()
    except Exception as e:
        log.error(f"DB flap log error: {e}")
    finally:
        if con:
            con.close()


def db_auto_resolve_flap(did, sid, resolved_ts, directions=("down",)):
    """Resolve ALL unresolved flaps for did+sid matching the given directions.

    Previously this resolved only the most recent entry (ORDER BY id DESC
    LIMIT 1), which left stale entries behind when a sensor escalated
    warn→crit (or crit→warn) without going through "ok" in between — the
    earlier warn/crit entry stayed "active" forever since the next OK only
    resolved the newest one. Now resolves every pending entry matching
    directions so one OK clears the whole chain.

    Duration is computed per-row from each entry's original ts.
    Returns a list of {id, duration} dicts (empty if nothing matched).
    """
    import time as _time
    from datetime import datetime, timezone
    now = _time.time()

    def _parse_ts(s):
        s = s.replace("Z", "+00:00") if s.endswith("Z") else s
        return datetime.fromisoformat(s)

    results = []
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                ph = ",".join(["%s"] * len(directions))
                cur.execute(
                    f"SELECT id, ts FROM flap_log "
                    f"WHERE did=%s AND sid=%s AND direction IN ({ph}) "
                    f"AND (resolved_at IS NULL OR resolved_at=0) "
                    f"AND ack_state != 'resolved' "
                    f"ORDER BY id ASC",
                    (did, sid, *directions)
                )
                rows = cur.fetchall()
                for row in rows:
                    try:
                        dur = max(0, (_parse_ts(resolved_ts) - _parse_ts(row["ts"])).total_seconds())
                    except Exception:
                        dur = 0
                    cur.execute(
                        "UPDATE flap_log SET resolved_at=%s, duration=%s, ack_state='resolved' WHERE id=%s",
                        (now, dur, row["id"])
                    )
                    results.append({"id": row["id"], "duration": dur})
            return results
        except Exception as e:
            log.error(f"db_auto_resolve_flap error: {e}")
            return results
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
        ph = ",".join(["?"] * len(directions))
        rows = con.execute(
            f"SELECT id, ts FROM flap_log "
            f"WHERE did=? AND sid=? AND direction IN ({ph}) "
            f"AND (resolved_at IS NULL OR resolved_at=0) "
            f"AND ack_state != 'resolved' "
            f"ORDER BY id ASC",
            (did, sid, *directions)
        ).fetchall()
        for row in rows:
            try:
                dur = max(0, (_parse_ts(resolved_ts) - _parse_ts(row[1])).total_seconds())
            except Exception:
                dur = 0
            con.execute(
                "UPDATE flap_log SET resolved_at=?, duration=?, ack_state='resolved' WHERE id=?",
                (now, dur, row[0])
            )
            results.append({"id": row[0], "duration": dur})
        con.commit()
        return results
    except Exception as e:
        log.error(f"db_auto_resolve_flap error: {e}")
        return results
    finally:
        if con:
            con.close()


def db_load_flaps():
    """Return flap events, newest first, excluding legacy recovery rows.

    Inclusion policy (regression-fix; matches the DELETE side at L154/L177):
      - ALL active + acknowledged rows are returned regardless of age. ACK
        means "keep visible until resolved", and a chronic problem (e.g. a
        license_crit that the operator acked but never resolved) must not
        silently drop out of the Events tab once the resolved-history cap
        fills with newer noise.
      - Resolved rows capped at `max_flap_entries` (default 2000) for
        history depth — same setting the DELETE-side cleanup uses, so
        load and purge stay aligned.
    """
    # Read the same setting the writer uses for purge cap. Clamp to a sane
    # minimum (50) and a ceiling that protects the browser from rendering
    # tens of thousands of cards (10000 is generous; users wanting deeper
    # history should use the History tab's pagination / CSV export instead).
    _resolved_cap = max(50, min(10000, int(_settings.get('max_flap_entries', 2000))))
    _dir_ok = "direction NOT IN ('recovered','threshold_ok','state_up')"
    _select_cols_pg = (
        "id,ts,did,sid,dname,sname,host,stype,detail,direction,"
        "COALESCE(ack_state,'active') AS ack_state,"
        "COALESCE(ack_by,'') AS ack_by,"
        "COALESCE(ack_at,0) AS ack_at,"
        "COALESCE(resolved_at,0) AS resolved_at,"
        "COALESCE(duration,0) AS duration,"
        "raw_data"
    )
    _select_cols_sl = (
        "id,ts,did,sid,dname,sname,host,stype,detail,direction,"
        "COALESCE(ack_state,'active'),COALESCE(ack_by,''),COALESCE(ack_at,0),"
        "COALESCE(resolved_at,0),COALESCE(duration,0),raw_data"
    )
    # CTE form scopes ORDER BY / LIMIT correctly inside each named query so
    # the resolved cap doesn't accidentally trim the unbounded active+acked
    # set. CTEs supported in PG and SQLite ≥ 3.8.3 (2014).
    _cte_pg = (
        f"WITH active_acked AS ("
        f"  SELECT {_select_cols_pg} FROM flap_log "
        f"  WHERE {_dir_ok} "
        f"    AND COALESCE(ack_state,'active') IN ('active','acknowledged')"
        f"),"
        f" recent_resolved AS ("
        f"  SELECT {_select_cols_pg} FROM flap_log "
        f"  WHERE {_dir_ok} "
        f"    AND COALESCE(ack_state,'active') = 'resolved' "
        f"  ORDER BY id DESC LIMIT {_resolved_cap}"
        f") "
        f"SELECT * FROM active_acked "
        f"UNION ALL "
        f"SELECT * FROM recent_resolved "
        f"ORDER BY id DESC"
    )
    _cte_sl = _cte_pg.replace(_select_cols_pg, _select_cols_sl)

    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(_cte_pg)
                rows = cur.fetchall()
            return [{"id": r["id"], "ts": r["ts"], "did": r["did"], "sid": r["sid"],
                     "dname": r["dname"], "sname": r["sname"], "host": r["host"],
                     "stype": r["stype"], "detail": r["detail"],
                     "direction": r["direction"] or "down",
                     "ack_state": r["ack_state"], "ack_by": r["ack_by"], "ack_at": r["ack_at"],
                     "resolved_at": r["resolved_at"], "duration": r["duration"],
                     "raw_data": r.get("raw_data") or ""}
                    for r in rows]
        except Exception as e:
            log.error(f"DB load flaps error: {e}")
            return []
    # SQLite
    con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
    try:
        rows = con.execute(_cte_sl).fetchall()
        return [{"id": r[0], "ts": r[1], "did": r[2], "sid": r[3],
                 "dname": r[4], "sname": r[5], "host": r[6], "stype": r[7],
                 "detail": r[8], "direction": r[9] or "down",
                 "ack_state": r[10], "ack_by": r[11], "ack_at": r[12],
                 "resolved_at": r[13], "duration": r[14],
                 "raw_data": r[15] or ""}
                for r in rows]
    except Exception as e:
        log.error(f"DB load flaps error: {e}")
        return []
    finally:
        con.close()


def db_ack_flap(flap_id, actor=""):
    """Set ack_state='acknowledged' on a flap entry."""
    import time as _time
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "UPDATE flap_log SET ack_state='acknowledged', ack_by=%s, ack_at=%s WHERE id=%s",
                    (actor, _time.time(), flap_id)
                )
                return cur.rowcount > 0
        except Exception as e:
            log.error(f"DB ack flap error: {e}")
            return False
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
        con.execute(
            "UPDATE flap_log SET ack_state='acknowledged', ack_by=?, ack_at=? WHERE id=?",
            (actor, _time.time(), flap_id)
        )
        con.commit()
        return con.execute("SELECT changes()").fetchone()[0] > 0
    except Exception as e:
        log.error(f"DB ack flap error: {e}")
        return False
    finally:
        if con: con.close()


def db_resolve_flap(flap_id):
    """Set ack_state='resolved' on a flap entry."""
    import time as _time
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "UPDATE flap_log SET ack_state='resolved', ack_at=%s WHERE id=%s",
                    (_time.time(), flap_id)
                )
                return cur.rowcount > 0
        except Exception as e:
            log.error(f"DB resolve flap error: {e}")
            return False
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
        con.execute(
            "UPDATE flap_log SET ack_state='resolved', ack_at=? WHERE id=?",
            (_time.time(), flap_id)
        )
        con.commit()
        return con.execute("SELECT changes()").fetchone()[0] > 0
    except Exception as e:
        log.error(f"DB resolve flap error: {e}")
        return False
    finally:
        if con: con.close()


def db_ack_flaps_by_sensor(did, sid, actor=""):
    """ACK all active flaps for a device+sensor pair."""
    import time as _time
    now = _time.time()
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "UPDATE flap_log SET ack_state='acknowledged', ack_by=%s, ack_at=%s "
                    "WHERE did=%s AND sid=%s AND COALESCE(ack_state,'active')='active'",
                    (actor, now, did, sid)
                )
        except Exception as e:
            log.error(f"db_ack_flaps_by_sensor error: {e}")
        return
    con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
    try:
        con.execute(
            "UPDATE flap_log SET ack_state='acknowledged', ack_by=?, ack_at=? "
            "WHERE did=? AND sid=? AND COALESCE(ack_state,'active')='active'",
            (actor, now, did, sid)
        )
        con.commit()
    except Exception as e:
        log.error(f"db_ack_flaps_by_sensor error: {e}")
    finally:
        con.close()


def db_resolve_flaps_by_sensor(did, sid):
    """Resolve all active/acknowledged flaps for a device+sensor pair."""
    import time as _time
    now = _time.time()
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "UPDATE flap_log SET ack_state='resolved', ack_at=%s "
                    "WHERE did=%s AND sid=%s AND COALESCE(ack_state,'active') IN ('active','acknowledged')",
                    (now, did, sid)
                )
        except Exception as e:
            log.error(f"db_resolve_flaps_by_sensor error: {e}")
        return
    con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
    try:
        con.execute(
            "UPDATE flap_log SET ack_state='resolved', ack_at=? "
            "WHERE did=? AND sid=? AND COALESCE(ack_state,'active') IN ('active','acknowledged')",
            (now, did, sid)
        )
        con.commit()
    except Exception as e:
        log.error(f"db_resolve_flaps_by_sensor error: {e}")
    finally:
        con.close()


def db_has_open_flap(did, sid, directions=("down",)) -> bool:
    """Return True if at least one flap_log row for did+sid in `directions`
    is still active or acknowledged (i.e. not resolved).

    Used by long-lived monitors (license_checker, etc.) to decide whether
    the underlying problem already has an open event in the Events tab —
    so we can re-create the flap when the original was resolved by the
    user but the condition still exists.
    """
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                ph = ",".join(["%s"] * len(directions))
                cur.execute(
                    f"SELECT 1 FROM flap_log "
                    f"WHERE did=%s AND sid=%s AND direction IN ({ph}) "
                    f"AND COALESCE(ack_state,'active') IN ('active','acknowledged') "
                    f"LIMIT 1",
                    (did, sid, *directions)
                )
                return cur.fetchone() is not None
        except Exception as e:
            log.error(f"db_has_open_flap error: {e}")
            return False
    con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
    try:
        ph = ",".join(["?"] * len(directions))
        row = con.execute(
            f"SELECT 1 FROM flap_log "
            f"WHERE did=? AND sid=? AND direction IN ({ph}) "
            f"AND COALESCE(ack_state,'active') IN ('active','acknowledged') "
            f"LIMIT 1",
            (did, sid, *directions)
        ).fetchone()
        return row is not None
    except Exception as e:
        log.error(f"db_has_open_flap error: {e}")
        return False
    finally:
        con.close()


def db_count_active_flaps() -> int:
    """Count flap_log entries with ack_state in ('active','acknowledged')."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM flap_log "
                    "WHERE COALESCE(ack_state,'active') IN ('active','acknowledged')"
                )
                return cur.fetchone()["cnt"]
        except Exception as e:
            log.error(f"db_count_active_flaps error: {e}")
            return 0
    con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM flap_log "
            "WHERE COALESCE(ack_state,'active') IN ('active','acknowledged')"
        ).fetchone()[0]
        return n
    except Exception as e:
        log.error(f"db_count_active_flaps error: {e}")
        return 0
    finally:
        con.close()


_FLAP_SEVERITY_SQL = (
    "SELECT "
    "SUM(CASE WHEN COALESCE(ack_state,'active')='active' "
    "         AND direction IN ('down','threshold_crit','license_crit','state_down','reboot') "
    "    THEN 1 ELSE 0 END) AS crit_count, "
    "SUM(CASE WHEN COALESCE(ack_state,'active')='active' "
    "         AND direction IN ('threshold_warn','license_warn','state_change') "
    "    THEN 1 ELSE 0 END) AS warn_count, "
    "SUM(CASE WHEN COALESCE(ack_state,'active')='acknowledged' "
    "    THEN 1 ELSE 0 END) AS ack_count "
    "FROM flap_log "
    "WHERE COALESCE(ack_state,'active') IN ('active','acknowledged')"
)

_ZERO_SEVERITY = {"crit": 0, "warn": 0, "ack": 0}


def db_count_active_flaps_by_severity() -> dict:
    """Return active flap counts broken down by severity and ack state."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(_FLAP_SEVERITY_SQL)
                row = cur.fetchone()
                return {
                    "crit": row["crit_count"] or 0,
                    "warn": row["warn_count"] or 0,
                    "ack":  row["ack_count"] or 0,
                }
        except Exception as e:
            log.error(f"db_count_active_flaps_by_severity error: {e}")
            return dict(_ZERO_SEVERITY)
    con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
    try:
        row = con.execute(_FLAP_SEVERITY_SQL).fetchone()
        return {
            "crit": row[0] or 0,
            "warn": row[1] or 0,
            "ack":  row[2] or 0,
        }
    except Exception as e:
        log.error(f"db_count_active_flaps_by_severity error: {e}")
        return dict(_ZERO_SEVERITY)
    finally:
        con.close()


def db_resolve_all_flaps() -> int:
    """Resolve all active/acknowledged flaps.  Returns count resolved."""
    import time as _time
    now = _time.time()
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "UPDATE flap_log SET ack_state='resolved', ack_at=%s "
                    "WHERE ack_state IN ('active','acknowledged')", (now,)
                )
                return cur.rowcount
        except Exception as e:
            log.error(f"db_resolve_all_flaps error: {e}")
            return 0
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
        cur = con.execute(
            "UPDATE flap_log SET ack_state='resolved', ack_at=? "
            "WHERE ack_state IN ('active','acknowledged')", (now,)
        )
        con.commit()
        return cur.rowcount
    except Exception as e:
        log.error(f"db_resolve_all_flaps error: {e}")
        return 0
    finally:
        if con: con.close()


def db_load_unresolved_flap_state(state):
    """Re-hydrate sensor runtime state (_alerted_down, _threshold_state,
    _down_since_ts, _threshold_triggered_ts) from any unresolved flap_log rows.

    Without this, every restart re-fires brand-new 'down'/'threshold_warn'/
    'threshold_crit' flap rows for sensors that were already in those states
    pre-restart — the previously-ACKed rows stay in the DB but a fresh active
    row pops up alongside, looking to the user like the ACK was lost.

    Direction precedence per sensor: 'threshold_crit' beats 'threshold_warn' /
    'anomaly_warn' (matches the warn↔crit escalation logic in state.py). 'down'
    is independent of threshold state.
    """
    from datetime import datetime
    _DOWN_DIRS = ("down",)
    _CRIT_DIRS = ("threshold_crit",)
    _WARN_DIRS = ("threshold_warn", "anomaly_warn")
    _ALL_DIRS  = _DOWN_DIRS + _CRIT_DIRS + _WARN_DIRS

    def _parse_iso(s):
        try:
            if not s: return None
            t = s.replace("Z", "+00:00") if s.endswith("Z") else s
            return datetime.fromisoformat(t).timestamp()
        except Exception:
            return None

    log.info("db_load_unresolved_flap_state: scanning flap_log for unresolved entries...")
    rows = []
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                ph = ",".join(["%s"] * len(_ALL_DIRS))
                cur.execute(
                    f"SELECT did, sid, direction, MIN(ts) AS first_ts FROM flap_log "
                    f"WHERE direction IN ({ph}) "
                    f"AND COALESCE(ack_state,'active') IN ('active','acknowledged') "
                    f"GROUP BY did, sid, direction",
                    _ALL_DIRS
                )
                rows = [(r["did"], r["sid"], r["direction"], r["first_ts"]) for r in cur.fetchall()]
        except Exception as e:
            log.error(f"db_load_unresolved_flap_state error: {e}")
            return
    else:
        con = None
        try:
            con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
            ph = ",".join(["?"] * len(_ALL_DIRS))
            rows = con.execute(
                f"SELECT did, sid, direction, MIN(ts) FROM flap_log "
                f"WHERE direction IN ({ph}) "
                f"AND COALESCE(ack_state,'active') IN ('active','acknowledged') "
                f"GROUP BY did, sid, direction",
                _ALL_DIRS
            ).fetchall()
        except Exception as e:
            log.error(f"db_load_unresolved_flap_state error: {e}")
            return
        finally:
            if con: con.close()
    log.info(f"db_load_unresolved_flap_state: found {len(rows)} unresolved flap_log row(s)")

    # Aggregate per (did, sid): note which categories matched + earliest ts each.
    # ts may be unparseable; track presence-of-row separately from the epoch.
    by_sensor = {}
    for did, sid, direction, first_ts in rows:
        slot = by_sensor.setdefault((did, sid), {
            "down": False, "down_ts": None,
            "warn": False, "warn_ts": None,
            "crit": False, "crit_ts": None,
        })
        ts_epoch = _parse_iso(first_ts)
        def _earlier(prev, new):
            if new is None: return prev
            if prev is None: return new
            return min(prev, new)
        if direction in _DOWN_DIRS:
            slot["down"] = True
            slot["down_ts"] = _earlier(slot["down_ts"], ts_epoch)
        elif direction in _CRIT_DIRS:
            slot["crit"] = True
            slot["crit_ts"] = _earlier(slot["crit_ts"], ts_epoch)
        elif direction in _WARN_DIRS:
            slot["warn"] = True
            slot["warn_ts"] = _earlier(slot["warn_ts"], ts_epoch)

    restored = 0
    skipped_no_device = 0
    skipped_no_sensor = 0
    for (did, sid), slot in by_sensor.items():
        dev = state.devices.get(did)
        if not dev:
            skipped_no_device += 1
            log.warning(f"flap_log references missing device did={did} sid={sid} — skipping restore")
            continue
        s = dev.sensors.get(sid)
        if not s:
            skipped_no_sensor += 1
            log.warning(f"flap_log references missing sensor did={did} sid={sid} (device exists) — skipping restore")
            continue
        flags = []
        if slot["down"]:
            s._alerted_down = True
            if slot["down_ts"]:
                s._down_since_ts = slot["down_ts"]
            flags.append("down")
        # Threshold: crit takes precedence over warn (matches escalation logic).
        if slot["crit"]:
            s._threshold_state = "crit"
            if slot["crit_ts"]:
                s._threshold_triggered_ts = slot["crit_ts"]
            flags.append("crit")
        elif slot["warn"]:
            s._threshold_state = "warn"
            if slot["warn_ts"]:
                s._threshold_triggered_ts = slot["warn_ts"]
            flags.append("warn")
        if flags:
            restored += 1
            log.info(f"Restored flap state for {dev.name}/{s.name} (did={did} sid={sid}): {','.join(flags)}")
    log.info(
        f"db_load_unresolved_flap_state: restored {restored} sensor(s); "
        f"skipped {skipped_no_device} missing device(s), {skipped_no_sensor} missing sensor(s)"
    )


# ── SNMP trap log ────────────────────────────────────────────────

def db_log_trap(t):
    """Append one SNMP trap (with enrichment fields); keep at most max_trap_entries."""
    _vals = (
        t.get("ts", ""),          t.get("src_ip", ""),
        t.get("dname", ""),       t.get("community", ""),
        t.get("trap_oid", ""),    t.get("detail", ""),
        t.get("vendor", ""),      t.get("product_family", ""),
        t.get("trap_name", ""),   t.get("severity", "info"),
        t.get("category", ""),    t.get("probable_cause", ""),
        t.get("recommended_action", ""),
        t.get("raw_varbinds", "[]"),
        int(t.get("enriched", 0)),
        t.get("enterprise_oid", ""),
        int(t.get("generic_trap_type", -1)),
        t.get("enriched_varbinds", "[]"),
    )
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "INSERT INTO snmp_traps "
                    "(ts,src_ip,dname,community,trap_oid,detail,"
                    " vendor,product_family,trap_name,severity,category,"
                    " probable_cause,recommended_action,raw_varbinds,enriched,"
                    " enterprise_oid,generic_trap_type,enriched_varbinds) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    _vals
                )
                _trap_limit = max(50, int(_settings.get('max_trap_entries', 2000)))
                cur.execute(
                    "DELETE FROM snmp_traps WHERE id NOT IN "
                    "(SELECT id FROM snmp_traps ORDER BY id DESC LIMIT %s)",
                    (_trap_limit,)
                )
        except Exception as e:
            log.error(f"DB trap log error: {e}")
        return
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
        con.execute(
            "INSERT INTO snmp_traps "
            "(ts,src_ip,dname,community,trap_oid,detail,"
            " vendor,product_family,trap_name,severity,category,"
            " probable_cause,recommended_action,raw_varbinds,enriched,"
            " enterprise_oid,generic_trap_type,enriched_varbinds) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _vals
        )
        _trap_limit = max(50, int(_settings.get('max_trap_entries', 2000)))
        con.execute(
            "DELETE FROM snmp_traps WHERE id NOT IN "
            "(SELECT id FROM snmp_traps ORDER BY id DESC LIMIT ?)",
            (_trap_limit,)
        )
        con.commit()
    except Exception as e:
        log.error(f"DB trap log error: {e}")
    finally:
        if con:
            con.close()


def db_load_traps(limit=500, vendor=None, category=None, severity=None):
    """Return SNMP traps newest first with optional filters."""
    _cols = (
        "ts,src_ip,dname,community,trap_oid,detail,"
        "vendor,product_family,trap_name,severity,category,"
        "probable_cause,recommended_action,raw_varbinds,enriched,"
        "enterprise_oid,generic_trap_type,enriched_varbinds"
    )
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            where, params = [], []
            if vendor:
                where.append("vendor=%s");   params.append(vendor)
            if category:
                where.append("category=%s"); params.append(category)
            if severity:
                where.append("severity=%s"); params.append(severity)
            sql = (
                f"SELECT {_cols} FROM snmp_traps"
                + (" WHERE " + " AND ".join(where) if where else "")
                + " ORDER BY id DESC LIMIT %s"
            )
            params.append(limit)
            with pg_cursor("logs") as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
            return [{
                "ts": r["ts"], "src_ip": r["src_ip"], "dname": r["dname"],
                "community": r["community"], "trap_oid": r["trap_oid"],
                "detail": r["detail"], "vendor": r["vendor"],
                "product_family": r["product_family"], "trap_name": r["trap_name"],
                "severity": r["severity"], "category": r["category"],
                "probable_cause": r["probable_cause"],
                "recommended_action": r["recommended_action"],
                "raw_varbinds": r["raw_varbinds"],
                "enriched": r["enriched"],
                "enterprise_oid": r["enterprise_oid"] or "",
                "generic_trap_type": r["generic_trap_type"] if r["generic_trap_type"] is not None else -1,
                "enriched_varbinds": r["enriched_varbinds"] or "[]",
                "_direction": "trap",
            } for r in rows]
        except Exception as e:
            log.error(f"DB load traps error: {e}")
            return []
    # SQLite
    con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
    try:
        where, params = [], []
        if vendor:
            where.append("vendor=?");   params.append(vendor)
        if category:
            where.append("category=?"); params.append(category)
        if severity:
            where.append("severity=?"); params.append(severity)
        sql = (
            f"SELECT {_cols} FROM snmp_traps"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY id DESC LIMIT ?"
        )
        params.append(limit)
        rows = con.execute(sql, params).fetchall()
        return [{
            "ts": r[0], "src_ip": r[1], "dname": r[2], "community": r[3],
            "trap_oid": r[4], "detail": r[5],
            "vendor": r[6], "product_family": r[7], "trap_name": r[8],
            "severity": r[9], "category": r[10], "probable_cause": r[11],
            "recommended_action": r[12], "raw_varbinds": r[13],
            "enriched": r[14], "enterprise_oid": r[15] or "",
            "generic_trap_type": r[16] if r[16] is not None else -1,
            "enriched_varbinds": r[17] or "[]",
            "_direction": "trap",
        } for r in rows]
    except Exception as e:
        log.error(f"DB load traps error: {e}")
        return []
    finally:
        con.close()


def db_clear_device_traps(src_ip):
    """Delete all SNMP traps from a device (matched by src_ip / host)."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute("DELETE FROM snmp_traps WHERE src_ip=%s", (src_ip,))
        except Exception as e:
            log.error(f"DB clear device traps error: {e}")
        return
    # SQLite
    con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
    try:
        con.execute("DELETE FROM snmp_traps WHERE src_ip=?", (src_ip,))
        con.commit()
    except Exception as e:
        log.error(f"DB clear device traps error: {e}")
    finally:
        con.close()
