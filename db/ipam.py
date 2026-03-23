"""
db/ipam.py — CRUD functions for the IPAM (IP address management) tables.

Tables: ipam_subnets, ip_allocations
All external writes are enqueued through the single-writer queue (_db_enqueue).
The ipam_sync_* functions are called DIRECTLY inside the DB writer thread
(already enqueued by the caller) and must NOT call _db_enqueue themselves.
"""

import ipaddress
import sqlite3
import time

from core.config import DB_PATH
from core.logger import log
from db.core     import _db_enqueue


# ── Subnet CRUD ────────────────────────────────────────────────────────────

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
    except Exception as e:
        log.error(f"IPAM list subnets error: {e}")
        return []
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
            log.debug(f"IPAM get subnet: id={subnet_id} not found")
            return None
        return {"id": row[0], "cidr": row[1], "name": row[2],
                "created_by": row[3], "created_at": row[4]}
    except Exception as e:
        log.error(f"IPAM get subnet error (id={subnet_id}): {e}")
        return None
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
        log.debug(f"IPAM subnet inserted: cidr={cidr!r} id={cur.lastrowid} by {user!r}")
        return cur.lastrowid
    except sqlite3.IntegrityError:
        log.warning(f"IPAM add subnet rejected — duplicate CIDR: {cidr!r} (requested by {user!r})")
        raise ValueError(f"Subnet {cidr!r} already exists")
    except Exception as e:
        log.error(f"IPAM add subnet error (cidr={cidr!r}): {e}")
        raise
    finally:
        con.close()


def db_delete_subnet(subnet_id: int) -> None:
    """Delete a subnet and all its IP allocations (enqueued write)."""
    def _do():
        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            alloc_count = con.execute(
                "SELECT COUNT(*) FROM ip_allocations WHERE subnet_id=?", (subnet_id,)
            ).fetchone()[0]
            con.execute("DELETE FROM ip_allocations WHERE subnet_id=?", (subnet_id,))
            con.execute("DELETE FROM ipam_subnets WHERE id=?", (subnet_id,))
            con.commit()
            log.info(f"IPAM subnet {subnet_id} deleted ({alloc_count} allocation(s) removed)")
        except Exception as e:
            log.error(f"IPAM delete subnet error (id={subnet_id}): {e}")
        finally:
            con.close()
    _db_enqueue(_do)


# ── Allocation CRUD ────────────────────────────────────────────────────────

def db_get_allocations(subnet_id: int) -> dict:
    """
    Return all allocations for a subnet as a dict:
      { ip_str: {name, modified_by, modified_at, device_id} }
    """
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        rows = con.execute(
            """SELECT ip, name, modified_by, modified_at, device_id
               FROM ip_allocations WHERE subnet_id=?""",
            (subnet_id,)
        ).fetchall()
        return {
            r[0]: {
                "name":        r[1],
                "modified_by": r[2],
                "modified_at": r[3],
                "device_id":   r[4] or '',
            }
            for r in rows
        }
    except Exception as e:
        log.error(f"IPAM get allocations error (subnet_id={subnet_id}): {e}")
        return {}
    finally:
        con.close()


def db_upsert_allocation(subnet_id: int, ip: str, name: str, user: str,
                         device_id: str = '') -> None:
    """Set the name for an IP (insert or update). Enqueued write."""
    now = time.time()
    def _do():
        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            con.execute(
                """INSERT INTO ip_allocations
                       (subnet_id, ip, name, modified_by, modified_at, device_id)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(subnet_id, ip) DO UPDATE SET
                     name=excluded.name,
                     modified_by=excluded.modified_by,
                     modified_at=excluded.modified_at,
                     device_id=excluded.device_id""",
                (subnet_id, ip, name, user, now, device_id)
            )
            con.commit()
            log.debug(f"IPAM allocation set: {ip} → {name!r} by {user!r} "
                      f"(subnet={subnet_id}, device_id={device_id!r})")
        except Exception as e:
            log.error(f"IPAM upsert allocation error ({ip} subnet={subnet_id}): {e}")
        finally:
            con.close()
    _db_enqueue(_do)


def db_clear_allocation(subnet_id: int, ip: str) -> None:
    """Remove a specific IP allocation. Enqueued write."""
    def _do():
        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            cur = con.execute(
                "DELETE FROM ip_allocations WHERE subnet_id=? AND ip=?",
                (subnet_id, ip)
            )
            con.commit()
            if cur.rowcount:
                log.debug(f"IPAM allocation cleared: {ip} (subnet={subnet_id})")
            else:
                log.debug(f"IPAM clear: {ip} had no allocation to remove (subnet={subnet_id})")
        except Exception as e:
            log.error(f"IPAM clear allocation error ({ip} subnet={subnet_id}): {e}")
        finally:
            con.close()
    _db_enqueue(_do)


# ── Device-sync helpers (called INSIDE the DB writer thread) ───────────────
# These functions must NOT call _db_enqueue — they run directly in the
# single-writer thread and open/close their own connection.

def _ipam_upsert_direct(con: sqlite3.Connection, subnet_id: int, ip: str,
                         name: str, device_id: str) -> None:
    """Upsert an allocation directly on an open connection (no enqueue)."""
    now = time.time()
    con.execute(
        """INSERT INTO ip_allocations
               (subnet_id, ip, name, modified_by, modified_at, device_id)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(subnet_id, ip) DO UPDATE SET
             name=excluded.name,
             modified_by=excluded.modified_by,
             modified_at=excluded.modified_at,
             device_id=excluded.device_id
           WHERE ip_allocations.device_id = excluded.device_id
              OR ip_allocations.device_id = ''""",
        (subnet_id, ip, name, 'system', now, device_id)
    )


def _ipam_subnets_for_ip(con: sqlite3.Connection,
                          ip_obj: ipaddress.IPv4Address) -> list:
    """Return list of (subnet_id, cidr) rows whose network contains ip_obj."""
    rows = con.execute("SELECT id, cidr FROM ipam_subnets").fetchall()
    matches = []
    for sid, cidr in rows:
        try:
            if ip_obj in ipaddress.ip_network(cidr, strict=False):
                matches.append((sid, cidr))
        except ValueError:
            log.warning(f"IPAM: stored subnet has invalid CIDR {cidr!r} (id={sid}) — skipping")
    return matches


def ipam_sync_device_add(did: str, name: str, host: str) -> None:
    """
    Auto-populate IPAM when a device is created.
    Called inside the DB writer thread — do NOT enqueue this function.
    Skips silently if host is a hostname (not a plain IP).
    Only writes to entries that are either unclaimed (device_id='') or
    already owned by this device (device_id=did).
    """
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        log.debug(f"IPAM sync device add: {did} ({name!r}) host={host!r} is a hostname — skipping")
        return
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        matched = _ipam_subnets_for_ip(con, ip_obj)
        if not matched:
            log.debug(f"IPAM sync device add: {did} ({name!r}) ip={ip_obj} — no matching subnet")
        else:
            for sid, cidr in matched:
                _ipam_upsert_direct(con, sid, str(ip_obj), name, did)
                log.debug(f"IPAM sync device add: {did} ({name!r}) → {ip_obj} in subnet {cidr} (id={sid})")
        con.commit()
    except Exception as e:
        log.error(f"IPAM sync device add error (did={did}, host={host!r}): {e}")
    finally:
        con.close()


def ipam_sync_device_update(did: str, old_host: str, new_host: str,
                             new_name: str) -> None:
    """
    Sync IPAM when a device is renamed or its host IP changes.
    Called inside the DB writer thread — do NOT enqueue this function.
    """
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        host_changed = old_host != new_host
        if host_changed:
            log.debug(f"IPAM sync device update: {did} host changed {old_host!r} → {new_host!r}")
            # Remove old IP allocation (only if this device owns it)
            try:
                old_ip = str(ipaddress.ip_address(old_host))
                cur = con.execute(
                    "DELETE FROM ip_allocations WHERE ip=? AND device_id=?",
                    (old_ip, did)
                )
                if cur.rowcount:
                    log.debug(f"IPAM sync device update: released old allocation {old_ip} for {did}")
            except ValueError:
                log.debug(f"IPAM sync device update: old host {old_host!r} for {did} is a hostname — no allocation to release")
        else:
            log.debug(f"IPAM sync device update: {did} name change to {new_name!r} (host unchanged)")

        # Populate new host
        try:
            new_ip_obj = ipaddress.ip_address(new_host)
        except ValueError:
            log.debug(f"IPAM sync device update: new host {new_host!r} for {did} is a hostname — no new allocation")
            con.commit()
            return

        matched = _ipam_subnets_for_ip(con, new_ip_obj)
        if not matched:
            log.debug(f"IPAM sync device update: {did} ip={new_ip_obj} — no matching subnet")
        else:
            for sid, cidr in matched:
                _ipam_upsert_direct(con, sid, str(new_ip_obj), new_name, did)
                log.debug(f"IPAM sync device update: {did} → {new_ip_obj} in subnet {cidr} (id={sid})")

        # If only name changed (same host), update existing device-owned entries
        if not host_changed:
            now = time.time()
            cur = con.execute(
                """UPDATE ip_allocations
                   SET name=?, modified_at=?
                   WHERE device_id=? AND ip=?""",
                (new_name, now, did, str(new_ip_obj))
            )
            if cur.rowcount:
                log.debug(f"IPAM sync device update: name updated for {new_ip_obj} → {new_name!r}")

        con.commit()
    except Exception as e:
        log.error(f"IPAM sync device update error (did={did}, old={old_host!r}, new={new_host!r}): {e}")
    finally:
        con.close()


def ipam_sync_device_delete(did: str) -> None:
    """
    Remove all device-owned IPAM entries when a device is deleted.
    Called inside the DB writer thread — do NOT enqueue this function.
    """
    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        cur = con.execute("DELETE FROM ip_allocations WHERE device_id=?", (did,))
        con.commit()
        if cur.rowcount:
            log.info(f"IPAM sync device delete: removed {cur.rowcount} allocation(s) for device {did}")
        else:
            log.debug(f"IPAM sync device delete: device {did} had no IPAM allocations")
    except Exception as e:
        log.error(f"IPAM sync device delete error (did={did}): {e}")
    finally:
        con.close()


def ipam_sync_subnet_add(subnet_id: int, cidr: str) -> None:
    """
    Auto-populate a newly-added subnet from all live devices.
    Called inside the DB writer thread — do NOT enqueue this function.
    """
    # Import STATE here to avoid circular import at module load time
    try:
        from core.app_state import STATE
    except Exception as e:
        log.error(f"IPAM sync subnet add: could not import STATE — {e}")
        return
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        log.error(f"IPAM sync subnet add: invalid CIDR {cidr!r} (subnet_id={subnet_id})")
        return

    con = sqlite3.connect(DB_PATH, timeout=10)
    populated = 0
    skipped_hostname = 0
    try:
        with STATE._lock:
            devices = [(d.device_id, d.name, d.host)
                       for d in STATE.devices.values()]
        log.debug(f"IPAM sync subnet add: scanning {len(devices)} device(s) for {cidr}")
        for did, name, host in devices:
            try:
                ip_obj = ipaddress.ip_address(host)
            except ValueError:
                skipped_hostname += 1
                continue  # hostname — skip
            if ip_obj in net:
                _ipam_upsert_direct(con, subnet_id, str(ip_obj), name, did)
                log.debug(f"IPAM sync subnet add: auto-populated {ip_obj} → {name!r} (device={did})")
                populated += 1
        con.commit()
        log.info(f"IPAM sync subnet add: {cidr} — {populated} device(s) auto-populated, "
                 f"{skipped_hostname} hostname device(s) skipped")
    except Exception as e:
        log.error(f"IPAM sync subnet add error (subnet_id={subnet_id}, cidr={cidr!r}): {e}")
    finally:
        con.close()
