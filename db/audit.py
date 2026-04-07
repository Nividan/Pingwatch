"""
db/audit.py — Audit log write and query helpers.
"""

import sqlite3
import time

from core.config import DB_PATH
from core.logger import log, log_audit
from db.backend  import is_pg
from db.core import _db_enqueue


def db_log_audit(actor: str, ip: str, action: str, target: str = '', detail: str = ''):
    """Append one audit entry; trim to last 2000 rows."""
    _t = f" -> {target}" if target else ""
    _d = f" | {detail}" if detail else ""
    log_audit.info(f"{actor} [{ip}] {action}{_t}{_d}")
    def _write():
        if is_pg():
            from db.pg_pool import pg_cursor
            with pg_cursor("main") as cur:
                cur.execute(
                    "INSERT INTO audit_log(ts,actor,ip,action,target,detail) VALUES(%s,%s,%s,%s,%s,%s)",
                    (time.time(), actor, ip, action, target, detail)
                )
                cur.execute("DELETE FROM audit_log WHERE id NOT IN "
                            "(SELECT id FROM audit_log ORDER BY ts DESC LIMIT 2000)")
        else:
            con = sqlite3.connect(DB_PATH)
            try:
                con.execute(
                    "INSERT INTO audit_log(ts,actor,ip,action,target,detail) VALUES(?,?,?,?,?,?)",
                    (time.time(), actor, ip, action, target, detail)
                )
                con.execute("DELETE FROM audit_log WHERE id NOT IN "
                            "(SELECT id FROM audit_log ORDER BY ts DESC LIMIT 2000)")
                con.commit()
            finally:
                con.close()
    _db_enqueue(_write)


def db_get_audit(limit: int = 200) -> list:
    """Return newest-first audit entries."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "SELECT ts,actor,ip,action,target,detail FROM audit_log "
                    "ORDER BY ts DESC LIMIT %s", (limit,)
                )
                rows = cur.fetchall()
            return [{"ts": r["ts"], "actor": r["actor"], "ip": r["ip"],
                     "action": r["action"], "target": r["target"], "detail": r["detail"]} for r in rows]
        except Exception:
            return []
    # SQLite
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT ts,actor,ip,action,target,detail FROM audit_log "
            "ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"ts": r[0], "actor": r[1], "ip": r[2],
                 "action": r[3], "target": r[4], "detail": r[5]} for r in rows]
    except Exception:
        return []
    finally:
        con.close()
