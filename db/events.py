"""
db/events.py — Flap log, SNMP trap log, and sensor error log helpers.
"""

import sqlite3

from config  import DB_PATH
from logger  import log
import settings as _settings


# ── Error log ────────────────────────────────────────────────────

def db_log_err(did, sid, sname, stype, msg, ts):
    """Append a sensor error entry; keep at most 1 000 per sensor."""
    con = None
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
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
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT ts,did,sid,sname,stype,msg FROM sensor_err_log "
            "WHERE did=? ORDER BY id DESC LIMIT 200", (did,)
        ).fetchall()
        con.close()
        return [{"ts": r[0], "did": r[1], "sid": r[2],
                 "sname": r[3], "stype": r[4], "msg": r[5], "type": "err"}
                for r in rows]
    except Exception as e:
        log.error(f"DB load err logs error: {e}")
        return []


def db_clear_err_logs(did):
    """Delete all sensor error logs for a device."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM sensor_err_log WHERE did=?", (did,))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"DB clear err logs error: {e}")


def db_clear_sensor_err_logs(did, sid):
    """Delete all sensor error logs for a specific sensor."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM sensor_err_log WHERE did=? AND sid=?", (did, sid))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"DB clear sensor err logs error: {e}")


# ── Flap log ─────────────────────────────────────────────────────

def db_log_flap(flap):
    """Append a flap/recovery event; keep at most 500 total."""
    con = None
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
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
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT ts,did,sid,dname,sname,host,stype,detail,direction "
            "FROM flap_log ORDER BY id DESC LIMIT 500"
        ).fetchall()
        con.close()
        return [{"ts": r[0], "did": r[1], "sid": r[2], "dname": r[3],
                 "sname": r[4], "host": r[5], "stype": r[6], "detail": r[7],
                 "direction": r[8] or "down"}
                for r in rows]
    except Exception as e:
        log.error(f"DB load flaps error: {e}")
        return []


# ── SNMP trap log ────────────────────────────────────────────────

def db_log_trap(t):
    """Append one SNMP trap; keep at most 500."""
    con = None
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        con.execute(
            "INSERT INTO snmp_traps (ts,src_ip,dname,community,trap_oid,detail) "
            "VALUES (?,?,?,?,?,?)",
            (t.get("ts", ""), t.get("src_ip", ""), t.get("dname", ""),
             t.get("community", ""), t.get("trap_oid", ""), t.get("detail", ""))
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


def db_load_traps():
    """Return last 500 SNMP traps, newest first."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT ts,src_ip,dname,community,trap_oid,detail "
            "FROM snmp_traps ORDER BY id DESC LIMIT 500"
        ).fetchall()
        con.close()
        return [{"ts": r[0], "src_ip": r[1], "dname": r[2],
                 "community": r[3], "trap_oid": r[4], "detail": r[5],
                 "_direction": "trap"}
                for r in rows]
    except Exception as e:
        log.error(f"DB load traps error: {e}")
        return []


def db_clear_device_traps(src_ip):
    """Delete all SNMP traps from a device (matched by src_ip / host)."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM snmp_traps WHERE src_ip=?", (src_ip,))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"DB clear device traps error: {e}")
