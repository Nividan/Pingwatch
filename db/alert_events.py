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
    """
    now = time.time()
    try:
        con = _con()
        # Check for existing active event with same rule+did+sid
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
            con.commit()
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
            con.commit()
            eid = cur.lastrowid
        con.close()
        return eid
    except Exception as e:
        log.error(f"db_log_event error: {e}")
        return -1


def db_list_events(state: str = None, limit: int = 200, offset: int = 0) -> list:
    """Return events, newest first. Filter by state if provided."""
    try:
        con = _con()
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
        con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.error(f"db_list_events error: {e}")
        return []


def db_count_active() -> int:
    """Count events in 'active' state (unacknowledged)."""
    try:
        con = _con()
        n = con.execute(
            "SELECT COUNT(*) FROM alert_events WHERE state='active'"
        ).fetchone()[0]
        con.close()
        return n
    except Exception as e:
        log.error(f"db_count_active error: {e}")
        return 0


def db_get_event(event_id: int) -> dict:
    try:
        con = _con()
        row = con.execute(
            "SELECT * FROM alert_events WHERE id=?", (event_id,)
        ).fetchone()
        con.close()
        return _row(row)
    except Exception as e:
        log.error(f"db_get_event error: {e}")
        return None


def db_ack_event(event_id: int, actor: str) -> bool:
    try:
        con = _con()
        con.execute(
            "UPDATE alert_events SET state='acknowledged', ack_by=?, ack_at=? WHERE id=?",
            (actor, time.time(), event_id)
        )
        con.commit()
        con.close()
        return True
    except Exception as e:
        log.error(f"db_ack_event error: {e}")
        return False


def db_resolve_event(event_id: int) -> bool:
    try:
        con = _con()
        con.execute(
            "UPDATE alert_events SET state='resolved', resolved_at=? WHERE id=?",
            (time.time(), event_id)
        )
        con.commit()
        con.close()
        return True
    except Exception as e:
        log.error(f"db_resolve_event error: {e}")
        return False


# ── Dedup / cooldown persistence ──────────────────────────────────

def db_get_dedup(sig: str) -> dict:
    try:
        con = _con()
        row = con.execute(
            "SELECT sig, last_fired, fire_count FROM alert_dedup WHERE sig=?", (sig,)
        ).fetchone()
        con.close()
        return _row(row)
    except Exception as e:
        log.error(f"db_get_dedup error: {e}")
        return None


def db_upsert_dedup(sig: str, now: float) -> int:
    """Upsert dedup record. Returns updated fire_count."""
    try:
        con = _con()
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
        con.close()
        return count
    except Exception as e:
        log.error(f"db_upsert_dedup error: {e}")
        return 1
