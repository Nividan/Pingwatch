"""
db/maintenance_windows.py — Maintenance window CRUD helpers.

Table: maintenance_windows
"""

import time

from core.logger import log
from db.backend  import is_pg
from db.helpers  import db_query, db_query_one, db_execute, db_cursor


def _to_dict(r) -> dict:
    """Normalize a row to a plain dict and coerce flags to bool."""
    if not r:
        return None
    d = dict(r)
    d['recurring'] = bool(d.get('recurring', 0))
    # Legacy rows (pre-enabled column) default to enabled=True
    d['enabled']   = bool(d.get('enabled', 1) if d.get('enabled') is not None else 1)
    return d


# ── CRUD ──────────────────────────────────────────────────────────

def db_list_windows() -> list:
    rows = db_query("main", "SELECT * FROM maintenance_windows ORDER BY start_ts")
    return [_to_dict(r) for r in rows]


def db_get_window(window_id: int) -> dict:
    row = db_query_one("main",
                       "SELECT * FROM maintenance_windows WHERE id=?",
                       (window_id,))
    return _to_dict(row)


def db_create_window(data: dict, created_by: str = '') -> int:
    now = time.time()
    _vals = (
        data['name'],
        data.get('scope_type', 'all'),
        data.get('scope_value', ''),
        float(data['start_ts']),
        float(data['end_ts']),
        1 if data.get('recurring') else 0,
        data.get('recur_days', ''),
        data.get('recur_start', ''),
        data.get('recur_end', ''),
        1 if data.get('enabled', True) else 0,
        created_by,
        now,
    )
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            placeholders = ",".join([ph] * 12)
            if is_pg():
                cur.execute(
                    f"""INSERT INTO maintenance_windows
                       (name, scope_type, scope_value, start_ts, end_ts,
                        recurring, recur_days, recur_start, recur_end,
                        enabled, created_by, created_at)
                       VALUES ({placeholders}) RETURNING id""",
                    _vals
                )
                return cur.fetchone()["id"]
            else:
                cur.execute(
                    f"""INSERT INTO maintenance_windows
                       (name, scope_type, scope_value, start_ts, end_ts,
                        recurring, recur_days, recur_start, recur_end,
                        enabled, created_by, created_at)
                       VALUES ({placeholders})""",
                    _vals
                )
                return cur.lastrowid
    except Exception as e:
        log.error(f"db_create_window error: {e}")
        return -1


def db_update_window(window_id: int, data: dict) -> bool:
    _vals = (
        data['name'],
        data.get('scope_type', 'all'),
        data.get('scope_value', ''),
        float(data['start_ts']),
        float(data['end_ts']),
        1 if data.get('recurring') else 0,
        data.get('recur_days', ''),
        data.get('recur_start', ''),
        data.get('recur_end', ''),
        1 if data.get('enabled', True) else 0,
        window_id,
    )
    return db_execute("main",
        """UPDATE maintenance_windows SET
           name=?, scope_type=?, scope_value=?,
           start_ts=?, end_ts=?, recurring=?,
           recur_days=?, recur_start=?, recur_end=?,
           enabled=?
           WHERE id=?""",
        _vals)


def db_set_window_enabled(window_id: int, enabled: bool) -> bool:
    """Single-field toggle — used by the row toggle in the UI."""
    return db_execute("main",
        "UPDATE maintenance_windows SET enabled=? WHERE id=?",
        (1 if enabled else 0, window_id))


def db_delete_window(window_id: int) -> bool:
    return db_execute("main", "DELETE FROM maintenance_windows WHERE id=?", (window_id,))


def db_active_windows() -> list:
    """Return enabled windows that are currently active (used by alert engine + auto-discovery).

    Disabled windows never suppress notifications or scans, regardless of their schedule.
    """
    now = time.time()
    rows = db_query("main",
                    "SELECT * FROM maintenance_windows "
                    "WHERE start_ts<=? AND end_ts>=? "
                    "AND (enabled IS NULL OR enabled<>0)",
                    (now, now))
    return [_to_dict(r) for r in rows]
