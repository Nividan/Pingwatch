"""
db/licenses.py — CRUD + status tracking for per-device license records.

Table: device_licenses (Main DB)
Dual-backend: SQLite + PostgreSQL.
"""
from __future__ import annotations

import sqlite3
import time

from core.config import DB_PATH
from core.logger import log
from db.backend  import is_pg


# ── Read ──────────────────────────────────────────────────────────────────

def db_get_licenses(did: str) -> list:
    """Return all license rows for a device, sorted by expiry date."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "SELECT id, did, license_name, expiry_date, note, "
                    "warn_days, crit_days, last_status, created_at, updated_at "
                    "FROM device_licenses WHERE did=%s ORDER BY expiry_date",
                    (did,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            log.error(f"db_get_licenses error (did={did}): {e}")
            return []

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        rows = con.execute(
            "SELECT id, did, license_name, expiry_date, note, "
            "warn_days, crit_days, last_status, created_at, updated_at "
            "FROM device_licenses WHERE did=? ORDER BY expiry_date",
            (did,)
        ).fetchall()
        return [
            {"id": r[0], "did": r[1], "license_name": r[2], "expiry_date": r[3],
             "note": r[4], "warn_days": r[5], "crit_days": r[6],
             "last_status": r[7], "created_at": r[8], "updated_at": r[9]}
            for r in rows
        ]
    except Exception as e:
        log.error(f"db_get_licenses error (did={did}): {e}")
        return []
    finally:
        con.close()


def db_get_all_licenses() -> list:
    """Return all license rows across all devices (for checker + dashboard)."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "SELECT id, did, license_name, expiry_date, note, "
                    "warn_days, crit_days, last_status, created_at, updated_at "
                    "FROM device_licenses ORDER BY expiry_date"
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            log.error(f"db_get_all_licenses error: {e}")
            return []

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        rows = con.execute(
            "SELECT id, did, license_name, expiry_date, note, "
            "warn_days, crit_days, last_status, created_at, updated_at "
            "FROM device_licenses ORDER BY expiry_date"
        ).fetchall()
        return [
            {"id": r[0], "did": r[1], "license_name": r[2], "expiry_date": r[3],
             "note": r[4], "warn_days": r[5], "crit_days": r[6],
             "last_status": r[7], "created_at": r[8], "updated_at": r[9]}
            for r in rows
        ]
    except Exception as e:
        log.error(f"db_get_all_licenses error: {e}")
        return []
    finally:
        con.close()


# ── Write ─────────────────────────────────────────────────────────────────

def db_add_license(did: str, license_name: str, expiry_date: str,
                   note: str = "", warn_days: int = 30,
                   crit_days: int = 0) -> int | None:
    """Insert a new license record.  Returns the new row id or None on error."""
    now = time.time()
    if is_pg():
        from db.pg_pool import pg_conn
        try:
            with pg_conn('main') as con:
                cur = con.cursor()
                cur.execute(
                    "INSERT INTO device_licenses "
                    "(did, license_name, expiry_date, note, warn_days, crit_days, "
                    " last_status, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'ok',%s,%s) RETURNING id",
                    (did, license_name, expiry_date, note, warn_days, crit_days,
                     now, now)
                )
                row = cur.fetchone()
                return row[0] if row else None  # pg_conn cursor returns tuples
        except Exception as e:
            log.error(f"db_add_license error: {e}")
            return None

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        cur = con.execute(
            "INSERT INTO device_licenses "
            "(did, license_name, expiry_date, note, warn_days, crit_days, "
            " last_status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,'ok',?,?)",
            (did, license_name, expiry_date, note, warn_days, crit_days,
             now, now)
        )
        con.commit()
        return cur.lastrowid
    except Exception as e:
        log.error(f"db_add_license error: {e}")
        return None
    finally:
        con.close()


def db_update_license(lic_id: int, license_name: str, expiry_date: str,
                      note: str, warn_days: int, crit_days: int) -> bool:
    """Update an existing license record.  Returns True on success."""
    now = time.time()
    if is_pg():
        from db.pg_pool import pg_conn
        try:
            with pg_conn('main') as con:
                cur = con.cursor()
                cur.execute(
                    "UPDATE device_licenses SET license_name=%s, expiry_date=%s, "
                    "note=%s, warn_days=%s, crit_days=%s, updated_at=%s "
                    "WHERE id=%s",
                    (license_name, expiry_date, note, warn_days, crit_days, now,
                     lic_id)
                )
                return cur.rowcount > 0
        except Exception as e:
            log.error(f"db_update_license error (id={lic_id}): {e}")
            return False

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        cur = con.execute(
            "UPDATE device_licenses SET license_name=?, expiry_date=?, "
            "note=?, warn_days=?, crit_days=?, updated_at=? "
            "WHERE id=?",
            (license_name, expiry_date, note, warn_days, crit_days, now, lic_id)
        )
        con.commit()
        return cur.rowcount > 0
    except Exception as e:
        log.error(f"db_update_license error (id={lic_id}): {e}")
        return False
    finally:
        con.close()


def db_delete_license(lic_id: int) -> bool:
    """Delete a license record.  Returns True on success."""
    if is_pg():
        from db.pg_pool import pg_conn
        try:
            with pg_conn('main') as con:
                cur = con.cursor()
                cur.execute("DELETE FROM device_licenses WHERE id=%s", (lic_id,))
                return cur.rowcount > 0
        except Exception as e:
            log.error(f"db_delete_license error (id={lic_id}): {e}")
            return False

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        cur = con.execute("DELETE FROM device_licenses WHERE id=?", (lic_id,))
        con.commit()
        return cur.rowcount > 0
    except Exception as e:
        log.error(f"db_delete_license error (id={lic_id}): {e}")
        return False
    finally:
        con.close()


def db_delete_device_licenses(did: str) -> int:
    """Delete all licenses for a device (called on device deletion).  Returns count."""
    if is_pg():
        from db.pg_pool import pg_conn
        try:
            with pg_conn('main') as con:
                cur = con.cursor()
                cur.execute("DELETE FROM device_licenses WHERE did=%s", (did,))
                return cur.rowcount
        except Exception as e:
            log.error(f"db_delete_device_licenses error (did={did}): {e}")
            return 0

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        cur = con.execute("DELETE FROM device_licenses WHERE did=?", (did,))
        con.commit()
        return cur.rowcount
    except Exception as e:
        log.error(f"db_delete_device_licenses error (did={did}): {e}")
        return 0
    finally:
        con.close()


# ── Internal: status update (called by license checker) ──────────────────

def db_update_license_status(lic_id: int, new_status: str) -> bool:
    """Update last_status for a license.  Called by the expiration checker."""
    now = time.time()
    if is_pg():
        from db.pg_pool import pg_conn
        try:
            with pg_conn('main') as con:
                cur = con.cursor()
                cur.execute(
                    "UPDATE device_licenses SET last_status=%s, updated_at=%s WHERE id=%s",
                    (new_status, now, lic_id)
                )
                return cur.rowcount > 0
        except Exception as e:
            log.error(f"db_update_license_status error (id={lic_id}): {e}")
            return False

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        con.execute(
            "UPDATE device_licenses SET last_status=?, updated_at=? WHERE id=?",
            (new_status, now, lic_id)
        )
        con.commit()
        return True
    except Exception as e:
        log.error(f"db_update_license_status error (id={lic_id}): {e}")
        return False
    finally:
        con.close()


# ── Summary (for dashboard widget / badge counts) ────────────────────────

def db_license_summary() -> dict:
    """Return counts: {ok: N, warn: N, crit: N, total: N}."""
    result = {"ok": 0, "warn": 0, "crit": 0, "total": 0}
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "SELECT last_status, COUNT(*) AS cnt FROM device_licenses GROUP BY last_status"
                )
                for r in cur.fetchall():
                    result[r["last_status"]] = r["cnt"]
                    result["total"] += r["cnt"]
        except Exception as e:
            log.error(f"db_license_summary error: {e}")
        return result

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        rows = con.execute(
            "SELECT last_status, COUNT(*) FROM device_licenses GROUP BY last_status"
        ).fetchall()
        for r in rows:
            result[r[0]] = r[1]
            result["total"] += r[1]
    except Exception as e:
        log.error(f"db_license_summary error: {e}")
    finally:
        con.close()
    return result
