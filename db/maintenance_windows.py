"""
db/maintenance_windows.py — Maintenance window CRUD helpers.

Table: maintenance_windows
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


def _to_dict(r) -> dict:
    d = dict(r)
    d['recurring'] = bool(d.get('recurring', 0))
    return d


# ── CRUD ──────────────────────────────────────────────────────────

def db_list_windows() -> list:
    con = _con()
    try:
        rows = con.execute(
            "SELECT * FROM maintenance_windows ORDER BY start_ts"
        ).fetchall()
        return [_to_dict(r) for r in rows]
    except Exception as e:
        log.error(f"db_list_windows error: {e}")
        return []
    finally:
        con.close()


def db_get_window(window_id: int) -> dict:
    con = _con()
    try:
        row = con.execute(
            "SELECT * FROM maintenance_windows WHERE id=?", (window_id,)
        ).fetchone()
        return _to_dict(row) if row else None
    except Exception as e:
        log.error(f"db_get_window error: {e}")
        return None
    finally:
        con.close()


def db_create_window(data: dict, created_by: str = '') -> int:
    now = time.time()
    con = _con()
    try:
        cur = con.execute(
            """INSERT INTO maintenance_windows
               (name, scope_type, scope_value, start_ts, end_ts,
                recurring, recur_days, recur_start, recur_end,
                created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data['name'],
                data.get('scope_type', 'all'),
                data.get('scope_value', ''),
                float(data['start_ts']),
                float(data['end_ts']),
                1 if data.get('recurring') else 0,
                data.get('recur_days', ''),
                data.get('recur_start', ''),
                data.get('recur_end', ''),
                created_by,
                now,
            )
        )
        con.commit()
        return cur.lastrowid
    except Exception as e:
        log.error(f"db_create_window error: {e}")
        return -1
    finally:
        con.close()


def db_update_window(window_id: int, data: dict) -> bool:
    con = _con()
    try:
        con.execute(
            """UPDATE maintenance_windows SET
               name=?, scope_type=?, scope_value=?,
               start_ts=?, end_ts=?, recurring=?,
               recur_days=?, recur_start=?, recur_end=?
               WHERE id=?""",
            (
                data['name'],
                data.get('scope_type', 'all'),
                data.get('scope_value', ''),
                float(data['start_ts']),
                float(data['end_ts']),
                1 if data.get('recurring') else 0,
                data.get('recur_days', ''),
                data.get('recur_start', ''),
                data.get('recur_end', ''),
                window_id,
            )
        )
        con.commit()
        return True
    except Exception as e:
        log.error(f"db_update_window error: {e}")
        return False
    finally:
        con.close()


def db_delete_window(window_id: int) -> bool:
    con = _con()
    try:
        con.execute("DELETE FROM maintenance_windows WHERE id=?", (window_id,))
        con.commit()
        return True
    except Exception as e:
        log.error(f"db_delete_window error: {e}")
        return False
    finally:
        con.close()


def db_active_windows() -> list:
    """Return windows that are currently active (used by alert engine)."""
    now = time.time()
    con = _con()
    try:
        rows = con.execute(
            "SELECT * FROM maintenance_windows WHERE start_ts<=? AND end_ts>=?",
            (now, now)
        ).fetchall()
        return [_to_dict(r) for r in rows]
    except Exception as e:
        log.error(f"db_active_windows error: {e}")
        return []
    finally:
        con.close()
