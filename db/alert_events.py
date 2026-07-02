"""
db/alert_events.py — Alert event history persistence helpers.

Table: alert_events  (rotational, capped retention)

Used by the PRTG-style alert profile engine. Each row is one fire (or repeat)
of a profile stage. The Events view in the UI reads from this table.
"""
from __future__ import annotations  # PEP 604 'list | None' must stay lazy on py3.8/3.9

import sqlite3
import time

from core.config import DB_PATH
from core.logger import log
from db.backend  import is_pg
from db.helpers  import db_query, db_query_one

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

# Flap directions that represent an open "something is wrong" incident —
# mirrors what the alert engine fires on. 'recovered'/'threshold_ok' rows
# also sit unresolved-looking in flap_log but are not incidents.
_INCIDENT_DIRECTIONS = ('down', 'threshold_crit', 'threshold_warn',
                        'anomaly_warn', 'state_down', 'state_change')


def _flap_ack_info(did: str, sid: str):
    """(ack_by, ack_at) when the sensor's current unresolved flap incident is
    acknowledged, else (None, None).

    A fired stage can postdate the operator's ACK: the flap is ACKed during
    the stage's delay window (or the engine re-fires after a restart), and
    the flap-level ACK propagation in routes/monitoring.py only reached
    event rows that existed at ACK time. A freshly inserted event must
    inherit that ACK or the Events row shows '● active' while the flap
    detail modal correctly says Acknowledged.
    """
    try:
        from db.helpers import db_query_one
        ph = ",".join("?" * len(_INCIDENT_DIRECTIONS))
        row = db_query_one("logs",
            f"SELECT ack_state, ack_by, ack_at FROM flap_log "
            f"WHERE did = ? AND sid = ? "
            f"AND (resolved_at IS NULL OR resolved_at = 0) "
            f"AND ack_state != 'resolved' "
            f"AND direction IN ({ph}) "
            f"ORDER BY id DESC LIMIT 1",
            (did, sid, *_INCIDENT_DIRECTIONS))
        if row and (row["ack_state"] or "active") == "acknowledged":
            return row["ack_by"] or "", float(row["ack_at"] or 0)
    except Exception as e:
        log.debug(f"_flap_ack_info lookup failed: {e}")
    return None, None


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

    # ACK inheritance — resolved BEFORE the main-DB transaction opens (the
    # lookup hits the logs DB; nesting that inside the transaction would
    # nest PG pool checkouts). Used only on the INSERT path: the dedup
    # UPDATE path never touches state, so an acknowledged row stays
    # acknowledged across repeat fires either way.
    ins_state, ack_by_v, ack_at_v = state, '', 0
    if state == 'active':
        _ab, _aa = _flap_ack_info(did, sid)
        if _ab is not None:
            ins_state, ack_by_v, ack_at_v = 'acknowledged', _ab, _aa

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
                            severity, event_type, state, triggered_at, detail, suppress_reason,
                            ack_by, ack_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (
                            profile_id, stage_id, profile_name,
                            did, sid, dname, sname,
                            severity, event_type, ins_state, now, detail, suppress_reason,
                            ack_by_v, ack_at_v,
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
                    severity, event_type, state, triggered_at, detail, suppress_reason,
                    ack_by, ack_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    profile_id, stage_id, profile_name,
                    did, sid, dname, sname,
                    severity, event_type, ins_state, now, detail, suppress_reason,
                    ack_by_v, ack_at_v,
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
    """Acknowledge an OPEN event. Returns True only if a row transitioned.

    The `state IN ('active','acknowledged')` guard is essential: without it,
    acking a RESOLVED event flips it back to 'acknowledged' — it re-surfaces in
    unresolved views AND arms db_has_acked_event, whose ack-gate then silences
    every dispatch of that sensor's NEXT incident. Suppressed rows are likewise
    not ackable.
    """
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "UPDATE alert_events SET state='acknowledged', ack_by=%s, ack_at=%s "
                    "WHERE id=%s AND state IN ('active','acknowledged')",
                    (actor, time.time(), event_id)
                )
                return cur.rowcount > 0
        except Exception as e:
            log.error(f"db_ack_event error: {e}")
            return False
    # SQLite
    con = _con()
    try:
        cur = con.execute(
            "UPDATE alert_events SET state='acknowledged', ack_by=?, ack_at=? "
            "WHERE id=? AND state IN ('active','acknowledged')",
            (actor, time.time(), event_id)
        )
        con.commit()
        return cur.rowcount > 0
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
    """Delete terminal alert events older than `retain_days`.

    The table header promised "rotational, capped retention" but nothing ever
    pruned it — events accumulated forever in the main DB of a 24/7 service.
    Prunes both RESOLVED rows (by resolved_at) and SUPPRESSED rows (terminal,
    resolved_at stays 0 → pruned by triggered_at; a fleet with nightly recurring
    maintenance windows writes these continuously). Active/acknowledged rows are
    always kept (open incidents). Returns rows deleted; called hourly.
    """
    cutoff = time.time() - max(1, int(retain_days)) * 86400
    try:
        if is_pg():
            from db.pg_pool import pg_cursor
            with pg_cursor("main") as cur:
                cur.execute(
                    "DELETE FROM alert_events WHERE "
                    "(state='resolved' AND resolved_at>0 AND resolved_at < %s) "
                    "OR (state='suppressed' AND triggered_at>0 AND triggered_at < %s)",
                    (cutoff, cutoff)
                )
                n = cur.rowcount or 0
        else:
            con = _con()
            try:
                cur = con.execute(
                    "DELETE FROM alert_events WHERE "
                    "(state='resolved' AND resolved_at>0 AND resolved_at < ?) "
                    "OR (state='suppressed' AND triggered_at>0 AND triggered_at < ?)",
                    (cutoff, cutoff)
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


# ── Aggregated stats (server-side; backs the MCP get_alert_stats tool) ──
# Group-by dimension → real alert_events column. site/group are NOT columns
# (only `did` is) and are resolved by the caller via live STATE.
_STAT_DIM_COL = {
    "device":         "did",
    "sensor":         "sid",
    "severity":       "severity",
    "state":          "state",
    "event_type":     "event_type",
    "suppress_reason": "suppress_reason",
}
_MTTR_SAMPLE_CAP = 20000   # bound the percentile fetch


def _percentiles(durations: list) -> dict:
    """avg / p50 / p95 over a list of numeric durations (seconds)."""
    n = len(durations)
    if not n:
        return {"avg": None, "p50": None, "p95": None, "n_resolved": 0}
    s = sorted(durations)
    def _pct(p):
        idx = min(n - 1, max(0, int(round((p / 100.0) * (n - 1)))))
        return round(s[idx], 1)
    return {"avg": round(sum(s) / n, 1), "p50": _pct(50), "p95": _pct(95),
            "n_resolved": n}


def db_alert_stats(since: float, until: float, *, device_id: str = None,
                   severity: str = None, state: str = None,
                   include_suppressed: bool = True,
                   group_by: list | None = None) -> dict:
    """Aggregate alert_events over [since, until). All counting happens in
    SQL (GROUP BY) — no event rows cross the wire except a capped sample of
    resolved-event durations for the MTTR percentiles (computed in Python so
    SQLite, which lacks percentile_cont, behaves identically to PG).

    `group_by` accepts keys from _STAT_DIM_COL; unknown keys are ignored.
    site/group are handled by the caller (not columns here).
    """
    where  = ["triggered_at >= ?", "triggered_at < ?"]
    params = [since, until]
    if device_id:
        where.append("did = ?");      params.append(device_id)
    if severity:
        where.append("severity = ?"); params.append(severity)
    if state:
        where.append("state = ?");    params.append(state)
    if not include_suppressed:
        where.append("state != 'suppressed'")
    W = " AND ".join(where)
    P = tuple(params)

    def _count_by(col):
        rows = db_query("main",
            f"SELECT {col} AS k, COUNT(*) AS c FROM alert_events "
            f"WHERE {W} GROUP BY {col}", P)
        return {(r["k"] if r["k"] not in (None, "") else "(none)"): int(r["c"])
                for r in rows}

    total_row = db_query_one("main",
        f"SELECT COUNT(*) AS c FROM alert_events WHERE {W}", P)
    total = int(total_row["c"]) if total_row else 0

    by_state    = _count_by("state")
    by_severity = _count_by("severity")

    supp_rows = db_query("main",
        f"SELECT suppress_reason AS k, COUNT(*) AS c FROM alert_events "
        f"WHERE {W} AND state='suppressed' GROUP BY suppress_reason", P)
    suppressed_by_reason = {(r["k"] or "(none)"): int(r["c"]) for r in supp_rows}

    # MTTR over resolved, non-suppressed events. Fetch only the duration
    # column, capped, and compute percentiles in Python.
    dur_rows = db_query("main",
        f"SELECT (resolved_at - triggered_at) AS d FROM alert_events "
        f"WHERE {W} AND state='resolved' AND resolved_at > 0 "
        f"AND triggered_at > 0 ORDER BY triggered_at DESC LIMIT ?",
        P + (_MTTR_SAMPLE_CAP,))
    durations = [float(r["d"]) for r in dur_rows
                 if r["d"] is not None and float(r["d"]) >= 0]
    mttr = _percentiles(durations)
    mttr["capped"] = len(dur_rows) >= _MTTR_SAMPLE_CAP

    # Buckets shaped by group_by (table-backed dims only).
    buckets = []
    dims = [d for d in (group_by or []) if d in _STAT_DIM_COL]
    if dims:
        cols = [_STAT_DIM_COL[d] for d in dims]
        sel_extra = ""
        if dims == ["device"]:
            sel_extra = ", MAX(dname) AS dname"
        elif dims == ["sensor"]:
            sel_extra = ", MAX(sname) AS sname"
        col_list = ", ".join(cols)
        rows = db_query("main",
            f"SELECT {col_list}{sel_extra}, COUNT(*) AS c FROM alert_events "
            f"WHERE {W} GROUP BY {col_list} ORDER BY COUNT(*) DESC LIMIT 200", P)
        for r in rows:
            r = dict(r)
            key = "|".join(str(r.get(_STAT_DIM_COL[d]) or "") for d in dims)
            b = {"key": key, "count": int(r["c"])}
            if "did" in cols:
                b["did"] = r.get("did")
            if r.get("dname"):
                b["name"] = r["dname"]
            if r.get("sname"):
                b["sname"] = r["sname"]
            buckets.append(b)

    return {
        "total":                total,
        "by_state":             by_state,
        "by_severity":          by_severity,
        "suppressed_by_reason": suppressed_by_reason,
        "mttr_seconds":         mttr,
        "buckets":              buckets,
    }
