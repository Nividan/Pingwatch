"""
db/ipam.py — CRUD functions for the IPAM (IP address management) tables.

Tables: ipam_subnets, ip_allocations
All writes are enqueued through the single-writer queue (_db_enqueue).
"""

import sqlite3
import time

from core.config import DB_PATH
from core.logger import log
from db.core     import _db_enqueue


def db_list_subnets() -> list:
    """Return all subnets ordered by CIDR."""
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        rows = con.execute(
            "SELECT id, cidr, name, created_by, created_at FROM ipam_subnets ORDER BY cidr"
        ).fetchall()
        return [
            {"id": r[0], "cidr": r[1], "name": r[2],
             "created_by": r[3], "created_at": r[4]}
            for r in rows
        ]
    finally:
        con.close()


def db_get_subnet(subnet_id: int) -> dict | None:
    """Return a single subnet row or None."""
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        row = con.execute(
            "SELECT id, cidr, name, created_by, created_at FROM ipam_subnets WHERE id=?",
            (subnet_id,)
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "cidr": row[1], "name": row[2],
                "created_by": row[3], "created_at": row[4]}
    finally:
        con.close()


def db_add_subnet(cidr: str, name: str, user: str) -> int:
    """
    Insert a new subnet. Returns the new row id.
    Raises ValueError on duplicate CIDR.
    """
    now = time.time()
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        cur = con.execute(
            "INSERT INTO ipam_subnets (cidr, name, created_by, created_at) VALUES (?,?,?,?)",
            (cidr, name, user, now)
        )
        con.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"Subnet {cidr!r} already exists")
    finally:
        con.close()


def db_delete_subnet(subnet_id: int) -> None:
    """Delete a subnet and all its IP allocations (enqueued write)."""
    def _do():
        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            con.execute("DELETE FROM ip_allocations WHERE subnet_id=?", (subnet_id,))
            con.execute("DELETE FROM ipam_subnets WHERE id=?", (subnet_id,))
            con.commit()
        except Exception as e:
            log.error(f"IPAM delete subnet error: {e}")
        finally:
            con.close()
    _db_enqueue(_do)


def db_get_allocations(subnet_id: int) -> dict:
    """
    Return all non-empty allocations for a subnet as a dict:
      { ip_str: {name, modified_by, modified_at} }
    """
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        rows = con.execute(
            "SELECT ip, name, modified_by, modified_at FROM ip_allocations WHERE subnet_id=?",
            (subnet_id,)
        ).fetchall()
        return {
            r[0]: {"name": r[1], "modified_by": r[2], "modified_at": r[3]}
            for r in rows
        }
    finally:
        con.close()


def db_upsert_allocation(subnet_id: int, ip: str, name: str, user: str) -> None:
    """Set the name for an IP (insert or update). Enqueued write."""
    now = time.time()
    def _do():
        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            con.execute(
                """INSERT INTO ip_allocations (subnet_id, ip, name, modified_by, modified_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(subnet_id, ip) DO UPDATE SET
                     name=excluded.name,
                     modified_by=excluded.modified_by,
                     modified_at=excluded.modified_at""",
                (subnet_id, ip, name, user, now)
            )
            con.commit()
        except Exception as e:
            log.error(f"IPAM upsert allocation error: {e}")
        finally:
            con.close()
    _db_enqueue(_do)


def db_clear_allocation(subnet_id: int, ip: str) -> None:
    """Remove a specific IP allocation. Enqueued write."""
    def _do():
        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            con.execute(
                "DELETE FROM ip_allocations WHERE subnet_id=? AND ip=?",
                (subnet_id, ip)
            )
            con.commit()
        except Exception as e:
            log.error(f"IPAM clear allocation error: {e}")
        finally:
            con.close()
    _db_enqueue(_do)
