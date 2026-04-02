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
    con = sqlite3.connect(LOGS_DB_PATH)
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
    con = sqlite3.connect(LOGS_DB_PATH)
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
    con = sqlite3.connect(LOGS_DB_PATH)
    try:
        con.execute("DELETE FROM sensor_err_log WHERE did=? AND sid=?", (did, sid))
        con.commit()
    except Exception as e:
        log.error(f"DB clear sensor err logs error: {e}")
    finally:
        con.close()


# ── Flap log ─────────────────────────────────────────────────────

def db_log_flap(flap):
    """Append a flap/recovery event; keep at most 500 total."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "INSERT INTO flap_log (ts,did,sid,dname,sname,host,stype,detail,direction) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (flap["ts"], flap["did"], flap["sid"], flap.get("dname", ""),
                     flap.get("sname", ""), flap.get("host", ""),
                     flap.get("stype", ""), flap.get("detail", ""),
                     flap.get("direction", "down"))
                )
                _flap_limit = max(50, int(_settings.get('max_flap_entries', 500)))
                cur.execute(
                    "DELETE FROM flap_log WHERE id NOT IN "
                    "(SELECT id FROM flap_log ORDER BY id DESC LIMIT %s)",
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
            "INSERT INTO flap_log (ts,did,sid,dname,sname,host,stype,detail,direction) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (flap["ts"], flap["did"], flap["sid"], flap.get("dname", ""),
             flap.get("sname", ""), flap.get("host", ""),
             flap.get("stype", ""), flap.get("detail", ""),
             flap.get("direction", "down"))
        )
        _flap_limit = max(50, int(_settings.get('max_flap_entries', 500)))
        con.execute(
            "DELETE FROM flap_log WHERE id NOT IN "
            "(SELECT id FROM flap_log ORDER BY id DESC LIMIT ?)",
            (_flap_limit,)
        )
        con.commit()
    except Exception as e:
        log.error(f"DB flap log error: {e}")
    finally:
        if con:
            con.close()


def db_load_flaps():
    """Return last 500 flap/recovery events, newest first."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "SELECT id,ts,did,sid,dname,sname,host,stype,detail,direction,"
                    "COALESCE(ack_state,'active') AS ack_state,"
                    "COALESCE(ack_by,'') AS ack_by,"
                    "COALESCE(ack_at,0) AS ack_at "
                    "FROM flap_log ORDER BY id DESC LIMIT 500"
                )
                rows = cur.fetchall()
            return [{"id": r["id"], "ts": r["ts"], "did": r["did"], "sid": r["sid"],
                     "dname": r["dname"], "sname": r["sname"], "host": r["host"],
                     "stype": r["stype"], "detail": r["detail"],
                     "direction": r["direction"] or "down",
                     "ack_state": r["ack_state"], "ack_by": r["ack_by"], "ack_at": r["ack_at"]}
                    for r in rows]
        except Exception as e:
            log.error(f"DB load flaps error: {e}")
            return []
    # SQLite
    con = sqlite3.connect(LOGS_DB_PATH)
    try:
        rows = con.execute(
            "SELECT id,ts,did,sid,dname,sname,host,stype,detail,direction,"
            "COALESCE(ack_state,'active'),COALESCE(ack_by,''),COALESCE(ack_at,0) "
            "FROM flap_log ORDER BY id DESC LIMIT 500"
        ).fetchall()
        return [{"id": r[0], "ts": r[1], "did": r[2], "sid": r[3],
                 "dname": r[4], "sname": r[5], "host": r[6], "stype": r[7],
                 "detail": r[8], "direction": r[9] or "down",
                 "ack_state": r[10], "ack_by": r[11], "ack_at": r[12]}
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
                _trap_limit = max(50, int(_settings.get('max_trap_entries', 500)))
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
        _trap_limit = max(50, int(_settings.get('max_trap_entries', 500)))
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
    con = sqlite3.connect(LOGS_DB_PATH)
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
    con = sqlite3.connect(LOGS_DB_PATH)
    try:
        con.execute("DELETE FROM snmp_traps WHERE src_ip=?", (src_ip,))
        con.commit()
    except Exception as e:
        log.error(f"DB clear device traps error: {e}")
    finally:
        con.close()
