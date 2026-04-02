"""
db/alert_events.py — Alert event history and dedup persistence helpers.

Tables: alert_events, alert_dedup
"""

import sqlite3
import time

from core.config import DB_PATH
from core.logger import log
from db.backend  import is_pg


def _con():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _row(r) -> dict:
    return dict(r) if r else None


# ── Alert events ──────────────────────────────────────────────────

def db_log_event(rule_id: int, rule_name: str, ctx: dict, state: str = 'active') -> int:
    """
    Log a fired rule event.  If an active event already exists for the same
    rule + device + sensor, increments repeat_count instead of inserting a new
    row.  Returns the event id.
    """
    now = time.time()
    if is_pg():
        from db.pg_pool import pg_conn
        import psycopg2.extras
        try:
            with pg_conn("main") as con:
                cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(
                    "SELECT id, repeat_count FROM alert_events "
                    "WHERE rule_id=%s AND did=%s AND sid=%s AND state='active'",
                    (rule_id, ctx.get('did', ''), ctx.get('sid', ''))
                )
                existing = cur.fetchone()
                if existing and state == 'active':
                    cur.execute(
                        "UPDATE alert_events SET repeat_count=repeat_count+1 WHERE id=%s",
                        (existing['id'],)
                    )
                    eid = existing['id']
                else:
                    cur.execute(
                        """INSERT INTO alert_events
                           (rule_id, rule_name, did, sid, dname, sname,
                            severity, event_type, state, triggered_at, detail)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (
                            rule_id, rule_name,
                            ctx.get('did', ''), ctx.get('sid', ''),
                            ctx.get('dname', ''), ctx.get('sname', ''),
                            ctx.get('severity', ''), ctx.get('event_type', ''),
                            state, now, ctx.get('detail', ''),
                        )
                    )
                    eid = cur.fetchone()['id']
                cur.close()
            return eid
        except Exception as e:
            log.error(f"db_log_event error: {e}")
            return -1
    # SQLite
    con = _con()
    try:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT id, repeat_count FROM alert_events "
            "WHERE rule_id=? AND did=? AND sid=? AND state='active'",
            (rule_id, ctx.get('did', ''), ctx.get('sid', ''))
        ).fetchone()
        if existing and state == 'active':
            con.execute(
                "UPDATE alert_events SET repeat_count=repeat_count+1 WHERE id=?",
                (existing['id'],)
            )
            eid = existing['id']
        else:
            cur = con.execute(
                """INSERT INTO alert_events
                   (rule_id, rule_name, did, sid, dname, sname,
                    severity, event_type, state, triggered_at, detail)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rule_id, rule_name,
                    ctx.get('did', ''), ctx.get('sid', ''),
                    ctx.get('dname', ''), ctx.get('sname', ''),
                    ctx.get('severity', ''), ctx.get('event_type', ''),
                    state, now, ctx.get('detail', ''),
                )
            )
            eid = cur.lastrowid
        con.execute("COMMIT")
        return eid
    except Exception as e:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        log.error(f"db_log_event error: {e}")
        return -1
    finally:
        con.close()


def db_list_events(state: str = None, limit: int = 200, offset: int = 0) -> list:
    """Return events, newest first. Filter by state if provided."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                if state and state != 'all':
                    cur.execute(
                        "SELECT * FROM alert_events WHERE state=%s "
                        "ORDER BY triggered_at DESC LIMIT %s OFFSET %s",
                        (state, limit, offset)
                    )
                else:
                    cur.execute(
                        "SELECT * FROM alert_events "
                        "ORDER BY triggered_at DESC LIMIT %s OFFSET %s",
                        (limit, offset)
                    )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            log.error(f"db_list_events error: {e}")
            return []
    # SQLite
    con = _con()
    try:
        if state and state != 'all':
            rows = con.execute(
                "SELECT * FROM alert_events WHERE state=? "
                "ORDER BY triggered_at DESC LIMIT ? OFFSET ?",
                (state, limit, offset)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM alert_events "
                "ORDER BY triggered_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.error(f"db_list_events error: {e}")
        return []
    finally:
        con.close()


def db_count_active() -> int:
    """Count events in 'active' state (unacknowledged)."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM alert_events WHERE state='active'")
                return cur.fetchone()["cnt"]
        except Exception as e:
            log.error(f"db_count_active error: {e}")
            return 0
    # SQLite
    con = _con()
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM alert_events WHERE state='active'"
        ).fetchone()[0]
        return n
    except Exception as e:
        log.error(f"db_count_active error: {e}")
        return 0
    finally:
        con.close()


def db_get_event(event_id: int) -> dict:
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute("SELECT * FROM alert_events WHERE id=%s", (event_id,))
                row = cur.fetchone()
            return dict(row) if row else None
        except Exception as e:
            log.error(f"db_get_event error: {e}")
            return None
    # SQLite
    con = _con()
    try:
        row = con.execute(
            "SELECT * FROM alert_events WHERE id=?", (event_id,)
        ).fetchone()
        return _row(row)
    except Exception as e:
        log.error(f"db_get_event error: {e}")
        return None
    finally:
        con.close()


def db_ack_event(event_id: int, actor: str) -> bool:
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "UPDATE alert_events SET state='acknowledged', ack_by=%s, ack_at=%s WHERE id=%s",
                    (actor, time.time(), event_id)
                )
            return True
        except Exception as e:
            log.error(f"db_ack_event error: {e}")
            return False
    # SQLite
    con = _con()
    try:
        con.execute(
            "UPDATE alert_events SET state='acknowledged', ack_by=?, ack_at=? WHERE id=?",
            (actor, time.time(), event_id)
        )
        con.commit()
        return True
    except Exception as e:
        log.error(f"db_ack_event error: {e}")
        return False
    finally:
        con.close()


def db_resolve_event(event_id: int) -> bool:
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "UPDATE alert_events SET state='resolved', resolved_at=%s WHERE id=%s",
                    (time.time(), event_id)
                )
            return True
        except Exception as e:
            log.error(f"db_resolve_event error: {e}")
            return False
    # SQLite
    con = _con()
    try:
        con.execute(
            "UPDATE alert_events SET state='resolved', resolved_at=? WHERE id=?",
            (time.time(), event_id)
        )
        con.commit()
        return True
    except Exception as e:
        log.error(f"db_resolve_event error: {e}")
        return False
    finally:
        con.close()


# ── ACK suppression check ────────────────────────────────────────

def db_has_acked_event(rule_id: int, did: str, sid: str) -> bool:
    """Return True if an acknowledged (not resolved) event exists for this rule+device+sensor."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "SELECT 1 FROM alert_events "
                    "WHERE rule_id=%s AND did=%s AND sid=%s AND state='acknowledged' LIMIT 1",
                    (rule_id, did, sid)
                )
                return cur.fetchone() is not None
        except Exception as e:
            log.error(f"db_has_acked_event error: {e}")
            return False
    # SQLite
    con = _con()
    try:
        row = con.execute(
            "SELECT 1 FROM alert_events "
            "WHERE rule_id=? AND did=? AND sid=? AND state='acknowledged' LIMIT 1",
            (rule_id, did, sid)
        ).fetchone()
        return row is not None
    except Exception as e:
        log.error(f"db_has_acked_event error: {e}")
        return False
    finally:
        con.close()


# ── Dedup / cooldown persistence ──────────────────────────────────

def db_get_dedup(sig: str) -> dict:
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "SELECT sig, last_fired, fire_count FROM alert_dedup WHERE sig=%s", (sig,)
                )
                row = cur.fetchone()
            return dict(row) if row else None
        except Exception as e:
            log.error(f"db_get_dedup error: {e}")
            return None
    # SQLite
    con = _con()
    try:
        row = con.execute(
            "SELECT sig, last_fired, fire_count FROM alert_dedup WHERE sig=?", (sig,)
        ).fetchone()
        return _row(row)
    except Exception as e:
        log.error(f"db_get_dedup error: {e}")
        return None
    finally:
        con.close()


def db_upsert_dedup(sig: str, now: float) -> int:
    """Upsert dedup record. Returns updated fire_count."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    """INSERT INTO alert_dedup (sig, last_fired, fire_count) VALUES (%s,%s,1)
                       ON CONFLICT(sig) DO UPDATE SET
                         last_fired=EXCLUDED.last_fired,
                         fire_count=alert_dedup.fire_count+1""",
                    (sig, now)
                )
                cur.execute(
                    "SELECT fire_count FROM alert_dedup WHERE sig=%s", (sig,)
                )
                return cur.fetchone()["fire_count"]
        except Exception as e:
            log.error(f"db_upsert_dedup error: {e}")
            return 1
    # SQLite
    con = _con()
    try:
        con.execute(
            """INSERT INTO alert_dedup (sig, last_fired, fire_count) VALUES (?,?,1)
               ON CONFLICT(sig) DO UPDATE SET
                 last_fired=excluded.last_fired,
                 fire_count=fire_count+1""",
            (sig, now)
        )
        con.commit()
        count = con.execute(
            "SELECT fire_count FROM alert_dedup WHERE sig=?", (sig,)
        ).fetchone()[0]
        return count
    except Exception as e:
        log.error(f"db_upsert_dedup error: {e}")
        return 1
    finally:
        con.close()
