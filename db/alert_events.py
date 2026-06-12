"""
db/alert_events.py — Alert event history persistence helpers.

Table: alert_events  (rotational, capped retention)

Used by the PRTG-style alert profile engine. Each row is one fire (or repeat)
of a profile stage. The Events view in the UI reads from this table.
"""

import sqlite3
import time

from core.config import DB_PATH
from core.logger import log
from db.backend  import is_pg

# Column list for alert_events — keep in sync with CREATE TABLE in
# db/core.py and db/pg_schema.py. Decouples query code from schema order.
_AE_COLS = (
    "id, profile_id, stage_id, profile_name, did, sid, dname, sname, "
    "severity, event_type, state, triggered_at, resolved_at, ack_by, "
    "ack_at, detail, repeat_count, suppress_reason"
)


def _con():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _row(r) -> dict:
    return dict(r) if r else None


# ── Alert events ──────────────────────────────────────────────────

def db_log_event(profile_id: int, stage_id: int, profile_name: str,
                 ctx: dict, state: str = 'active',
                 suppress_reason: str = '') -> int:
    """
    Log a fired stage event. If an active OR acknowledged event already exists
    for the same device + sensor, updates that row in place (bump repeat_count,
    refresh detail/stage/profile, escalate severity but never downgrade).
    Otherwise inserts a new row. Returns the event id.

    Dedup matches on (did, sid) alone — profile_id is NOT part of the key, so
    swapping a sensor's profile mid-incident updates the same row instead of
    creating a new one. This gives "one row per failure session per sensor".
    """
    now        = time.time()
    did        = ctx.get('did', '')
    sid        = ctx.get('sid', '')
    dname      = ctx.get('dname', '')
    sname      = ctx.get('sname', '')
    severity   = ctx.get('severity', '')
    event_type = ctx.get('event_type', '')
    detail     = ctx.get('detail', '')

    if is_pg():
        from db.pg_pool import pg_conn
        import psycopg2.extras
        try:
            with pg_conn("main") as con:
                cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(
                    "SELECT id, severity FROM alert_events "
                    "WHERE did=%s AND sid=%s AND state IN ('active','acknowledged') "
                    "ORDER BY id DESC LIMIT 1",
                    (did, sid)
                )
                existing = cur.fetchone()
                if existing and state == 'active':
                    # Escalate severity (never downgrade). If current row is already
                    # 'critical', keep it; otherwise adopt the incoming severity.
                    # triggered_at is preserved — it anchors to the original fire
                    # time so the events-tab matcher can correlate the flap with
                    # this event row across the whole incident (stages 2/3 would
                    # otherwise drift it outside the correlation window).
                    cur.execute(
                        """UPDATE alert_events SET
                               repeat_count = repeat_count + 1,
                               stage_id     = %s,
                               profile_id   = %s,
                               profile_name = %s,
                               severity     = CASE WHEN severity = 'critical' THEN severity ELSE %s END,
                               event_type   = CASE WHEN severity = 'critical' THEN event_type ELSE %s END,
                               detail       = %s
                           WHERE id = %s""",
                        (stage_id, profile_id, profile_name,
                         severity, event_type, detail, existing['id'])
                    )
                    eid = existing['id']
                else:
                    cur.execute(
                        """INSERT INTO alert_events
                           (profile_id, stage_id, profile_name, did, sid, dname, sname,
                            severity, event_type, state, triggered_at, detail, suppress_reason)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (
                            profile_id, stage_id, profile_name,
                            did, sid, dname, sname,
                            severity, event_type, state, now, detail, suppress_reason,
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
            "SELECT id, severity, event_type FROM alert_events "
            "WHERE did=? AND sid=? AND state IN ('active','acknowledged') "
            "ORDER BY id DESC LIMIT 1",
            (did, sid)
        ).fetchone()
        if existing and state == 'active':
            # Escalate severity but never downgrade (critical stays critical
            # until the whole session recovers).
            if existing['severity'] == 'critical':
                new_sev  = 'critical'
                new_etyp = existing['event_type']
            else:
                new_sev  = severity
                new_etyp = event_type
            con.execute(
                """UPDATE alert_events SET
                       repeat_count = repeat_count + 1,
                       stage_id     = ?,
                       profile_id   = ?,
                       profile_name = ?,
                       severity     = ?,
                       event_type   = ?,
                       detail       = ?
                   WHERE id = ?""",
                (stage_id, profile_id, profile_name,
                 new_sev, new_etyp, detail, existing['id'])
            )
            eid = existing['id']
        else:
            cur = con.execute(
                """INSERT INTO alert_events
                   (profile_id, stage_id, profile_name, did, sid, dname, sname,
                    severity, event_type, state, triggered_at, detail, suppress_reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    profile_id, stage_id, profile_name,
                    did, sid, dname, sname,
                    severity, event_type, state, now, detail, suppress_reason,
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
                        f"SELECT {_AE_COLS} FROM alert_events WHERE state=%s "
                        "ORDER BY triggered_at DESC LIMIT %s OFFSET %s",
                        (state, limit, offset)
                    )
                else:
                    cur.execute(
                        f"SELECT {_AE_COLS} FROM alert_events "
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
                f"SELECT {_AE_COLS} FROM alert_events WHERE state=? "
                "ORDER BY triggered_at DESC LIMIT ? OFFSET ?",
                (state, limit, offset)
            ).fetchall()
        else:
            rows = con.execute(
                f"SELECT {_AE_COLS} FROM alert_events "
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
                cur.execute(f"SELECT {_AE_COLS} FROM alert_events WHERE id=%s", (event_id,))
                row = cur.fetchone()
            return dict(row) if row else None
        except Exception as e:
            log.error(f"db_get_event error: {e}")
            return None
    # SQLite
    con = _con()
    try:
        row = con.execute(
            f"SELECT {_AE_COLS} FROM alert_events WHERE id=?", (event_id,)
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


def db_auto_resolve_event(profile_id: int, did: str, sid: str) -> bool:
    """Auto-resolve active alert_event for profile+device+sensor on recovery."""
    now = time.time()
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "UPDATE alert_events SET state='resolved', resolved_at=%s "
                    "WHERE profile_id=%s AND did=%s AND sid=%s AND state IN ('active','acknowledged')",
                    (now, profile_id, did, sid)
                )
                return cur.rowcount > 0
        except Exception as e:
            log.error(f"db_auto_resolve_event error: {e}")
            return False
    # SQLite
    con = _con()
    try:
        con.execute(
            "UPDATE alert_events SET state='resolved', resolved_at=? "
            "WHERE profile_id=? AND did=? AND sid=? AND state IN ('active','acknowledged')",
            (now, profile_id, did, sid)
        )
        con.commit()
        return True
    except Exception as e:
        log.error(f"db_auto_resolve_event error: {e}")
        return False
    finally:
        con.close()


def db_ack_events_by_sensor(did: str, sid: str, actor: str = "") -> None:
    """ACK all active alert events for a device+sensor pair."""
    now = time.time()
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "UPDATE alert_events SET state='acknowledged', ack_by=%s, ack_at=%s "
                    "WHERE did=%s AND sid=%s AND state='active'",
                    (actor, now, did, sid)
                )
        except Exception as e:
            log.error(f"db_ack_events_by_sensor error: {e}")
        return
    con = _con()
    try:
        con.execute(
            "UPDATE alert_events SET state='acknowledged', ack_by=?, ack_at=? "
            "WHERE did=? AND sid=? AND state='active'",
            (actor, now, did, sid)
        )
        con.commit()
    except Exception as e:
        log.error(f"db_ack_events_by_sensor error: {e}")
    finally:
        con.close()


def db_resolve_events_by_sensor(did: str, sid: str) -> None:
    """Resolve all active/acknowledged alert events for a device+sensor pair."""
    now = time.time()
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "UPDATE alert_events SET state='resolved', resolved_at=%s "
                    "WHERE did=%s AND sid=%s AND state IN ('active','acknowledged')",
                    (now, did, sid)
                )
        except Exception as e:
            log.error(f"db_resolve_events_by_sensor error: {e}")
        return
    con = _con()
    try:
        con.execute(
            "UPDATE alert_events SET state='resolved', resolved_at=? "
            "WHERE did=? AND sid=? AND state IN ('active','acknowledged')",
            (now, did, sid)
        )
        con.commit()
    except Exception as e:
        log.error(f"db_resolve_events_by_sensor error: {e}")
    finally:
        con.close()


def db_resolve_all_active() -> int:
    """Resolve all active/acknowledged alert events.  Returns count resolved."""
    now = time.time()
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "UPDATE alert_events SET state='resolved', resolved_at=%s "
                    "WHERE state IN ('active','acknowledged')", (now,)
                )
                return cur.rowcount
        except Exception as e:
            log.error(f"db_resolve_all_active error: {e}")
            return 0
    # SQLite
    con = _con()
    try:
        cur = con.execute(
            "UPDATE alert_events SET state='resolved', resolved_at=? "
            "WHERE state IN ('active','acknowledged')", (now,)
        )
        con.commit()
        return cur.rowcount
    except Exception as e:
        log.error(f"db_resolve_all_active error: {e}")
        return 0
    finally:
        con.close()


# ── Active / ACK suppression checks ─────────────────────────────

def db_has_active_event(did: str, sid: str) -> bool:
    """Return True if any active or acknowledged event exists for this device+sensor.

    Used by the alert engine to detect mid-incident state transitions (e.g.
    warn→crit session-key change) and suppress duplicate dispatches.
    """
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "SELECT 1 FROM alert_events "
                    "WHERE did=%s AND sid=%s AND state IN ('active','acknowledged') LIMIT 1",
                    (did, sid)
                )
                return cur.fetchone() is not None
        except Exception as e:
            log.error(f"db_has_active_event error: {e}")
            return False
    # SQLite
    con = _con()
    try:
        row = con.execute(
            "SELECT 1 FROM alert_events "
            "WHERE did=? AND sid=? AND state IN ('active','acknowledged') LIMIT 1",
            (did, sid)
        ).fetchone()
        return row is not None
    except Exception as e:
        log.error(f"db_has_active_event error: {e}")
        return False
    finally:
        con.close()


def db_has_acked_event(profile_id: int, did: str, sid: str) -> bool:
    """Return True if an acknowledged (not resolved) event exists for this profile+device+sensor."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "SELECT 1 FROM alert_events "
                    "WHERE profile_id=%s AND did=%s AND sid=%s AND state='acknowledged' LIMIT 1",
                    (profile_id, did, sid)
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
            "WHERE profile_id=? AND did=? AND sid=? AND state='acknowledged' LIMIT 1",
            (profile_id, did, sid)
        ).fetchone()
        return row is not None
    except Exception as e:
        log.error(f"db_has_acked_event error: {e}")
        return False
    finally:
        con.close()


def db_clean_alert_events(retain_days: int = 90) -> int:
    """Delete RESOLVED alert events older than `retain_days`.

    The table header promised "rotational, capped retention" but nothing ever
    pruned it — resolved events accumulated forever in the main DB of a 24/7
    service. Active/acknowledged rows are always kept (they're open incidents).
    Returns rows deleted. Called from the hourly autosave cleanup.
    """
    cutoff = time.time() - max(1, int(retain_days)) * 86400
    try:
        if is_pg():
            from db.pg_pool import pg_cursor
            with pg_cursor("main") as cur:
                cur.execute(
                    "DELETE FROM alert_events "
                    "WHERE state='resolved' AND resolved_at>0 AND resolved_at < %s",
                    (cutoff,)
                )
                n = cur.rowcount or 0
        else:
            con = _con()
            try:
                cur = con.execute(
                    "DELETE FROM alert_events "
                    "WHERE state='resolved' AND resolved_at>0 AND resolved_at < ?",
                    (cutoff,)
                )
                n = cur.rowcount or 0
                con.commit()
            finally:
                con.close()
        if n:
            log.info(f"Alert events retention: pruned {n} resolved event(s) "
                     f"older than {retain_days}d")
        return n
    except Exception as e:
        log.error(f"db_clean_alert_events error: {e}")
        return 0
