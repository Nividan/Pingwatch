"""
db/alert_events.py — Alert event history and dedup persistence helpers.

Tables: alert_events, alert_dedup
"""

import sqlite3
import time

from core.config import DB_PATH
from core.logger import log


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

    Uses BEGIN IMMEDIATE to prevent TOCTOU races under concurrent dispatch.
    """
    now = time.time()
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
                    rule_id,
                    rule_name,
                    ctx.get('did', ''),
                    ctx.get('sid', ''),
                    ctx.get('dname', ''),
                    ctx.get('sname', ''),
                    ctx.get('severity', ''),
                    ctx.get('event_type', ''),
                    state,
                    now,
                    ctx.get('detail', ''),
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


# ── Dedup / cooldown persistence ──────────────────────────────────

def db_get_dedup(sig: str) -> dict:
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
