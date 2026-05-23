"""
db/ipam.py — CRUD functions for the IPAM (IP address management) tables.

Tables: ipam_subnets, ip_allocations
All external writes are enqueued through the single-writer queue (_db_enqueue).
The ipam_sync_* functions are called DIRECTLY inside the DB writer thread
(already enqueued by the caller) and must NOT call _db_enqueue themselves.
"""
from __future__ import annotations

import ipaddress
import sqlite3
import time

from core.config import DB_PATH
from core.logger import log
from db.backend  import is_pg
from db.core     import _db_enqueue


# ── Subnet CRUD ────────────────────────────────────────────────────────────

_SUBNET_COLS = ("id, cidr, name, created_by, created_at, "
                "COALESCE(auto_discover,0)       AS auto_discover, "
                "COALESCE(first_scan_approved,0) AS first_scan_approved, "
                "last_auto_scan_ts, "
                "COALESCE(dns_server,'')         AS dns_server, "
                "COALESCE(site,'')               AS site, "
                "COALESCE(vlan,0)                AS vlan")


def _fmt_ts(v) -> str:
    """Normalize last_auto_scan_ts to a JSON-safe string.

    PG returns datetime; SQLite returns str (our writer uses strftime). Either
    way, downstream code just shows this in the UI — a single format is fine.
    """
    if v is None or v == "":
        return ""
    try:
        return v.strftime("%Y-%m-%d %H:%M:%S")   # datetime → str
    except AttributeError:
        return str(v)                             # already a str


def _row_to_subnet_pg(r) -> dict:
    return {"id": r["id"], "cidr": r["cidr"], "name": r["name"],
            "created_by": r["created_by"], "created_at": r["created_at"],
            "auto_discover":       int(r.get("auto_discover") or 0),
            "first_scan_approved": int(r.get("first_scan_approved") or 0),
            "last_auto_scan_ts":   _fmt_ts(r.get("last_auto_scan_ts")),
            "dns_server":          (r.get("dns_server") or ""),
            "site":                (r.get("site") or ""),
            "vlan":                int(r.get("vlan") or 0)}


def _row_to_subnet_sqlite(r) -> dict:
    return {"id": r[0], "cidr": r[1], "name": r[2],
            "created_by": r[3], "created_at": r[4],
            "auto_discover":       int(r[5] or 0),
            "first_scan_approved": int(r[6] or 0),
            "last_auto_scan_ts":   _fmt_ts(r[7]),
            "dns_server":          (r[8] or "") if len(r) > 8 else "",
            "site":                (r[9] or "") if len(r) > 9 else "",
            "vlan":                int(r[10] or 0) if len(r) > 10 else 0}


def db_list_subnets() -> list:
    """Return all subnets ordered by CIDR. Includes auto-discover fields."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    f"SELECT {_SUBNET_COLS} FROM ipam_subnets ORDER BY cidr"
                )
                return [_row_to_subnet_pg(r) for r in cur.fetchall()]
        except Exception as e:
            log.error(f"IPAM list subnets error: {e}")
            return []

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        rows = con.execute(
            f"SELECT {_SUBNET_COLS} FROM ipam_subnets ORDER BY cidr"
        ).fetchall()
        return [_row_to_subnet_sqlite(r) for r in rows]
    except Exception as e:
        log.error(f"IPAM list subnets error: {e}")
        return []
    finally:
        con.close()


def db_get_subnet(subnet_id: int) -> dict | None:
    """Return a single subnet row or None."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    f"SELECT {_SUBNET_COLS} FROM ipam_subnets WHERE id=%s",
                    (subnet_id,)
                )
                row = cur.fetchone()
            if not row:
                log.debug(f"IPAM get subnet: id={subnet_id} not found")
                return None
            return _row_to_subnet_pg(row)
        except Exception as e:
            log.error(f"IPAM get subnet error (id={subnet_id}): {e}")
            return None

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        row = con.execute(
            f"SELECT {_SUBNET_COLS} FROM ipam_subnets WHERE id=?",
            (subnet_id,)
        ).fetchone()
        if not row:
            log.debug(f"IPAM get subnet: id={subnet_id} not found")
            return None
        return _row_to_subnet_sqlite(row)
    except Exception as e:
        log.error(f"IPAM get subnet error (id={subnet_id}): {e}")
        return None
    finally:
        con.close()


# ── Generic multi-field update (used by PATCH /api/ipam/subnets/<id>) ──

# Keep this whitelist tight — the PATCH route passes user input straight in.
_SUBNET_UPDATABLE_FIELDS = {
    "name":                ("TEXT",    80),
    "site":                ("TEXT",    40),
    "auto_discover":       ("INT",     None),
    "first_scan_approved": ("INT",     None),
    "dns_server":          ("TEXT",    255),
    # VLAN (v1.0+): valid IEEE 802.1Q range is 1..4094. 0 = untagged / no VLAN.
    # INT_RANGE clamps out-of-range to 0 rather than silently dropping the field
    # so the user gets a stable result ("invalid -> untagged" is recoverable;
    # silent drop leaves the previous VLAN in place which is surprising).
    "vlan":                ("INT_RANGE", (0, 4094)),
}


def db_update_subnet(subnet_id: int, fields: dict) -> bool:
    """Update an arbitrary subset of subnet fields in one statement.

    Unknown keys are silently dropped (defense in depth). Returns True on
    success — caller validates field values before calling.
    """
    clean: dict = {}
    for k, v in (fields or {}).items():
        spec = _SUBNET_UPDATABLE_FIELDS.get(k)
        if not spec:
            continue
        kind, maxlen = spec
        if kind == "INT":
            try:
                clean[k] = 1 if int(v) else 0
            except (TypeError, ValueError):
                continue
        elif kind == "INT_RANGE":
            lo, hi = maxlen  # spec[1] holds (lo, hi)
            try:
                iv = int(v)
            except (TypeError, ValueError):
                continue
            clean[k] = iv if (lo <= iv <= hi) else 0
        else:
            s = "" if v is None else str(v).strip()
            if maxlen:
                s = s[:maxlen]
            clean[k] = s
    if not clean:
        return True   # no-op — nothing to update

    set_cols = list(clean.keys())
    values   = [clean[k] for k in set_cols]

    if is_pg():
        from db.pg_pool import pg_cursor
        placeholders = ", ".join(f"{c}=%s" for c in set_cols)
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    f"UPDATE ipam_subnets SET {placeholders} WHERE id=%s",
                    (*values, subnet_id)
                )
            return True
        except Exception as e:
            log.error(f"IPAM update_subnet error (id={subnet_id}): {e}")
            return False

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        placeholders = ", ".join(f"{c}=?" for c in set_cols)
        con.execute(
            f"UPDATE ipam_subnets SET {placeholders} WHERE id=?",
            (*values, subnet_id)
        )
        con.commit()
        return True
    except Exception as e:
        log.error(f"IPAM update_subnet error (id={subnet_id}): {e}")
        return False
    finally:
        con.close()


# ── Auto-Discovery helpers (added v0.9.3) ────────────────────────────

def db_set_auto_discover(subnet_id: int, enabled: bool) -> bool:
    """Toggle the `auto_discover` flag on a subnet. Returns True on success.
    Writes synchronously (no queue) so the caller can confirm immediately.
    """
    flag = 1 if enabled else 0
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "UPDATE ipam_subnets SET auto_discover=%s WHERE id=%s",
                    (flag, subnet_id)
                )
            return True
        except Exception as e:
            log.error(f"IPAM set auto_discover error (id={subnet_id}): {e}")
            return False

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        con.execute(
            "UPDATE ipam_subnets SET auto_discover=? WHERE id=?",
            (flag, subnet_id)
        )
        con.commit()
        return True
    except Exception as e:
        log.error(f"IPAM set auto_discover error (id={subnet_id}): {e}")
        return False
    finally:
        con.close()


def db_approve_first_scan(subnet_id: int) -> bool:
    """Set `first_scan_approved=1` on a subnet so next scan skips the cap.
    One-shot — subsequent scans have no cap anyway.
    """
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "UPDATE ipam_subnets SET first_scan_approved=1 WHERE id=%s",
                    (subnet_id,)
                )
            return True
        except Exception as e:
            log.error(f"IPAM approve first_scan error (id={subnet_id}): {e}")
            return False

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        con.execute(
            "UPDATE ipam_subnets SET first_scan_approved=1 WHERE id=?",
            (subnet_id,)
        )
        con.commit()
        return True
    except Exception as e:
        log.error(f"IPAM approve first_scan error (id={subnet_id}): {e}")
        return False
    finally:
        con.close()


def db_set_subnet_last_scan(subnet_id: int, ts: str) -> bool:
    """Record the last auto-scan timestamp on a subnet. `ts` is an ISO-like
    string; PG will coerce to TIMESTAMP, SQLite stores as TEXT.
    """
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "UPDATE ipam_subnets SET last_auto_scan_ts=%s WHERE id=%s",
                    (ts, subnet_id)
                )
            return True
        except Exception as e:
            log.error(f"IPAM set last_auto_scan_ts error (id={subnet_id}): {e}")
            return False

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        con.execute(
            "UPDATE ipam_subnets SET last_auto_scan_ts=? WHERE id=?",
            (ts, subnet_id)
        )
        con.commit()
        return True
    except Exception as e:
        log.error(f"IPAM set last_auto_scan_ts error (id={subnet_id}): {e}")
        return False
    finally:
        con.close()


def db_add_subnet(cidr: str, name: str, user: str, site: str = '', vlan: int = 0) -> int:
    """
    Insert a new subnet. Returns the new row id.
    Raises ValueError on duplicate CIDR.

    `site` is an optional free-form site/zone tag for sidebar grouping.
    `vlan` is an optional 802.1Q VLAN ID (1..4094; 0 = untagged).
    """
    now = time.time()
    try:
        vlan_i = int(vlan or 0)
    except (TypeError, ValueError):
        vlan_i = 0
    if not (0 <= vlan_i <= 4094):
        vlan_i = 0
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "INSERT INTO ipam_subnets (cidr, name, site, vlan, created_by, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                    (cidr, name, site, vlan_i, user, now)
                )
                new_id = cur.fetchone()["id"]
            log.debug(f"IPAM subnet inserted: cidr={cidr!r} id={new_id} vlan={vlan_i} by {user!r}")
            return new_id
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                log.warning(f"IPAM add subnet rejected — duplicate CIDR: {cidr!r}")
                raise ValueError(f"Subnet {cidr!r} already exists")
            log.error(f"IPAM add subnet error (cidr={cidr!r}): {e}")
            raise

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        cur = con.execute(
            "INSERT INTO ipam_subnets (cidr, name, site, vlan, created_by, created_at) VALUES (?,?,?,?,?,?)",
            (cidr, name, site, vlan_i, user, now)
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


def db_rename_subnet(subnet_id: int, name: str) -> None:
    """Rename a subnet's label. Enqueued write."""
    now = time.time()
    def _do():
        if is_pg():
            from db.pg_pool import pg_cursor
            try:
                with pg_cursor('main') as cur:
                    cur.execute(
                        "UPDATE ipam_subnets SET name=%s WHERE id=%s",
                        (name, subnet_id)
                    )
                log.debug(f"IPAM subnet {subnet_id} renamed to {name!r}")
            except Exception as e:
                log.error(f"IPAM rename subnet error (id={subnet_id}): {e}")
            return
        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            con.execute("UPDATE ipam_subnets SET name=? WHERE id=?", (name, subnet_id))
            con.commit()
            log.debug(f"IPAM subnet {subnet_id} renamed to {name!r}")
        except Exception as e:
            log.error(f"IPAM rename subnet error (id={subnet_id}): {e}")
        finally:
            con.close()
    _db_enqueue(_do)


def db_delete_subnet(subnet_id: int) -> None:
    """Delete a subnet and all its IP allocations (enqueued write)."""
    def _do():
        if is_pg():
            from db.pg_pool import pg_cursor
            try:
                with pg_cursor('main') as cur:
                    cur.execute(
                        "SELECT COUNT(*) AS cnt FROM ip_allocations WHERE subnet_id=%s",
                        (subnet_id,)
                    )
                    alloc_count = cur.fetchone()["cnt"]
                    cur.execute("DELETE FROM ip_allocations WHERE subnet_id=%s", (subnet_id,))
                    cur.execute("DELETE FROM ipam_subnets WHERE id=%s", (subnet_id,))
                log.info(f"IPAM subnet {subnet_id} deleted ({alloc_count} allocation(s) removed)")
            except Exception as e:
                log.error(f"IPAM delete subnet error (id={subnet_id}): {e}")
            return

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
      { ip_str: {name, modified_by, modified_at, device_id, dns_name, dns_resolved_at} }
    """
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    """SELECT ip, name, modified_by, modified_at, device_id, dns_name, dns_resolved_at,
                              COALESCE(kind,'') AS kind
                       FROM ip_allocations WHERE subnet_id=%s""",
                    (subnet_id,)
                )
                return {
                    r["ip"]: {
                        "name":            r["name"],
                        "modified_by":     r["modified_by"],
                        "modified_at":     r["modified_at"],
                        "device_id":       r["device_id"] or '',
                        "dns_name":        r["dns_name"] or '',
                        "dns_resolved_at": r["dns_resolved_at"] or 0,
                        "kind":            r.get("kind") or '',
                    }
                    for r in cur.fetchall()
                }
        except Exception as e:
            log.error(f"IPAM get allocations error (subnet_id={subnet_id}): {e}")
            return {}

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        rows = con.execute(
            """SELECT ip, name, modified_by, modified_at, device_id, dns_name, dns_resolved_at,
                      COALESCE(kind,'') AS kind
               FROM ip_allocations WHERE subnet_id=?""",
            (subnet_id,)
        ).fetchall()
        return {
            r[0]: {
                "name":            r[1],
                "modified_by":     r[2],
                "modified_at":     r[3],
                "device_id":       r[4] or '',
                "dns_name":        r[5] or '',
                "dns_resolved_at": r[6] or 0,
                "kind":            (r[7] if len(r) > 7 else '') or '',
            }
            for r in rows
        }
    except Exception as e:
        log.error(f"IPAM get allocations error (subnet_id={subnet_id}): {e}")
        return {}
    finally:
        con.close()


def db_update_dns(subnet_id: int, ip: str, dns_name: str) -> None:
    """Update cached DNS hostname for a single IP allocation (direct write, no enqueue)."""
    import time as _t
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "UPDATE ip_allocations SET dns_name=%s, dns_resolved_at=%s "
                    "WHERE subnet_id=%s AND ip=%s",
                    (dns_name, _t.time(), subnet_id, ip)
                )
        except Exception as e:
            log.error(f"IPAM update DNS error ({ip} subnet={subnet_id}): {e}")
        return

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        con.execute(
            "UPDATE ip_allocations SET dns_name=?, dns_resolved_at=? "
            "WHERE subnet_id=? AND ip=?",
            (dns_name, _t.time(), subnet_id, ip)
        )
        con.commit()
    except Exception as e:
        log.error(f"IPAM update DNS error ({ip} subnet={subnet_id}): {e}")
    finally:
        con.close()


def db_upsert_allocation(subnet_id: int, ip: str, name: str, user: str,
                         device_id: str = '', kind=None) -> None:
    """Set the name (and optionally the kind tag) for an IP. Enqueued write.

    `kind` semantics:
      - None (default)  → leave the existing kind untouched on UPDATE
      - '' or 'gateway'/'reserved'/'conflict'/etc → overwrite the kind
    This lets device-sync paths set just the name without clobbering a
    user-applied gateway/reserved tag.
    """
    now = time.time()
    set_kind = kind is not None
    kind_val = str(kind)[:24] if set_kind else ''
    kind_log = repr(kind_val) if set_kind else 'unchanged'
    def _do():
        if is_pg():
            from db.pg_pool import pg_cursor
            try:
                with pg_cursor('main') as cur:
                    if set_kind:
                        cur.execute(
                            """INSERT INTO ip_allocations
                                   (subnet_id, ip, name, modified_by, modified_at, device_id, kind)
                               VALUES (%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT(subnet_id, ip) DO UPDATE SET
                                 name=EXCLUDED.name,
                                 modified_by=EXCLUDED.modified_by,
                                 modified_at=EXCLUDED.modified_at,
                                 device_id=EXCLUDED.device_id,
                                 kind=EXCLUDED.kind""",
                            (subnet_id, ip, name, user, now, device_id, kind_val)
                        )
                    else:
                        cur.execute(
                            """INSERT INTO ip_allocations
                                   (subnet_id, ip, name, modified_by, modified_at, device_id)
                               VALUES (%s,%s,%s,%s,%s,%s)
                               ON CONFLICT(subnet_id, ip) DO UPDATE SET
                                 name=EXCLUDED.name,
                                 modified_by=EXCLUDED.modified_by,
                                 modified_at=EXCLUDED.modified_at,
                                 device_id=EXCLUDED.device_id""",
                            (subnet_id, ip, name, user, now, device_id)
                        )
                log.debug(f"IPAM allocation set: {ip} → {name!r} by {user!r} "
                          f"(subnet={subnet_id}, device_id={device_id!r}, kind={kind_log})")
            except Exception as e:
                log.error(f"IPAM upsert allocation error ({ip} subnet={subnet_id}): {e}")
            return

        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            if set_kind:
                con.execute(
                    """INSERT INTO ip_allocations
                           (subnet_id, ip, name, modified_by, modified_at, device_id, kind)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(subnet_id, ip) DO UPDATE SET
                         name=excluded.name,
                         modified_by=excluded.modified_by,
                         modified_at=excluded.modified_at,
                         device_id=excluded.device_id,
                         kind=excluded.kind""",
                    (subnet_id, ip, name, user, now, device_id, kind_val)
                )
            else:
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
                      f"(subnet={subnet_id}, device_id={device_id!r}, kind={kind_log})")
        except Exception as e:
            log.error(f"IPAM upsert allocation error ({ip} subnet={subnet_id}): {e}")
        finally:
            con.close()
    _db_enqueue(_do)


def db_mark_allocations_stale(subnet_id: int, ips: list) -> None:
    """Flip a batch of discovered allocations to kind='stale' without bumping
    modified_at — preserving the timestamp as "last time this IP was seen
    alive." Only affects rows whose current kind is 'discovered' or already
    'stale'; rows with user-set tags (gateway/reserved/...) or a device_id
    are skipped at the SQL level so we never clobber human metadata.

    `ips` is a list of IP strings (str). No-op on empty list. Enqueued write.
    """
    if not ips:
        return
    def _do():
        if is_pg():
            from db.pg_pool import pg_cursor
            try:
                with pg_cursor('main') as cur:
                    cur.execute(
                        "UPDATE ip_allocations SET kind='stale' "
                        "WHERE subnet_id=%s "
                        "  AND ip = ANY(%s) "
                        "  AND COALESCE(device_id,'')='' "
                        "  AND COALESCE(kind,'') IN ('discovered','')",
                        (subnet_id, list(ips))
                    )
                    log.debug(f"IPAM mark stale: subnet={subnet_id} affected={cur.rowcount} "
                              f"requested={len(ips)}")
            except Exception as e:
                log.error(f"IPAM mark stale error (subnet={subnet_id}): {e}")
            return

        con = sqlite3.connect(DB_PATH, timeout=10)
        try:
            # SQLite has no ANY(); chunk the IN-list so a /16 sweep doesn't
            # exceed the variable limit (default 999).
            CHUNK = 500
            affected = 0
            for i in range(0, len(ips), CHUNK):
                chunk = ips[i:i+CHUNK]
                ph = ",".join("?" * len(chunk))
                cur = con.execute(
                    f"UPDATE ip_allocations SET kind='stale' "
                    f"WHERE subnet_id=? "
                    f"  AND ip IN ({ph}) "
                    f"  AND COALESCE(device_id,'')='' "
                    f"  AND COALESCE(kind,'') IN ('discovered','')",
                    (subnet_id, *chunk)
                )
                affected += cur.rowcount
            con.commit()
            log.debug(f"IPAM mark stale: subnet={subnet_id} affected={affected} "
                      f"requested={len(ips)}")
        except Exception as e:
            log.error(f"IPAM mark stale error (subnet={subnet_id}): {e}")
        finally:
            con.close()
    _db_enqueue(_do)


def db_clear_allocation(subnet_id: int, ip: str) -> None:
    """Remove a specific IP allocation. Enqueued write."""
    def _do():
        if is_pg():
            from db.pg_pool import pg_cursor
            try:
                with pg_cursor('main') as cur:
                    cur.execute(
                        "DELETE FROM ip_allocations WHERE subnet_id=%s AND ip=%s",
                        (subnet_id, ip)
                    )
                    if cur.rowcount:
                        log.debug(f"IPAM allocation cleared: {ip} (subnet={subnet_id})")
                    else:
                        log.debug(f"IPAM clear: {ip} had no allocation to remove (subnet={subnet_id})")
            except Exception as e:
                log.error(f"IPAM clear allocation error ({ip} subnet={subnet_id}): {e}")
            return

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


# ── Topology role tagging (switch / backbone / core / gateway) ─────────────
# Role tagging reuses the existing ip_allocations.kind column. The NTM Live
# map reads these tags to render auto-links along the standard 3/4-tier
# enterprise topology: subnet members fan out to the subnet's switch
# (access), switch uplinks to a backbone (aggregation), backbone uplinks
# to a core (central L3), core uplinks to the gateway (edge/FW). Tiers
# that aren't tagged in a site are skipped via fallback to the next-up
# tier. Cross-site connectivity meshes at the core level when present,
# otherwise at the backbone level.

_ROLE_KINDS = ('switch', 'backbone', 'core', 'gateway')


def db_set_device_role(did: str, host: str, role: str) -> int:
    """
    Set/clear the topology role for a device. Returns the number of IPAM
    allocation rows updated. Enqueued write.

    `role` must be one of: '', 'switch', 'backbone', 'core', 'gateway'.
    Other values are rejected (returns 0 without write). Silently no-ops if
    host isn't a plain IP, or no IPAM subnet matches the IP.
    """
    role = (role or '').strip().lower()
    if role not in ('', 'switch', 'backbone', 'core', 'gateway'):
        log.warning(f"db_set_device_role: invalid role {role!r} for {did}")
        return 0
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        log.debug(f"db_set_device_role: host {host!r} for {did} not a plain IP — skipping")
        return 0
    now = time.time()
    ip_str = str(ip_obj)

    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                matched = _ipam_subnets_for_ip(None, ip_obj)
                if not matched:
                    return 0
                total = 0
                for sid, _cidr in matched:
                    cur.execute(
                        """UPDATE ip_allocations
                              SET kind=%s, modified_at=%s
                            WHERE subnet_id=%s AND ip=%s
                              AND (device_id=%s OR device_id='')""",
                        (role, now, sid, ip_str, did)
                    )
                    total += cur.rowcount or 0
                return total
        except Exception as e:
            log.error(f"db_set_device_role error (did={did}, host={host!r}, role={role!r}): {e}")
            return 0

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        matched = _ipam_subnets_for_ip(con, ip_obj)
        if not matched:
            return 0
        total = 0
        for sid, _cidr in matched:
            cur = con.execute(
                """UPDATE ip_allocations
                      SET kind=?, modified_at=?
                    WHERE subnet_id=? AND ip=?
                      AND (device_id=? OR device_id='')""",
                (role, now, sid, ip_str, did)
            )
            total += cur.rowcount or 0
        con.commit()
        return total
    except Exception as e:
        log.error(f"db_set_device_role error (did={did}, host={host!r}, role={role!r}): {e}")
        return 0
    finally:
        con.close()


def db_get_device_roles() -> dict:
    """
    Return {device_id: kind} for every allocation tagged with a topology
    role (switch / backbone / core / gateway) that has a device_id. Used by
    the NTM Live map auto-link renderer and by the device editor to populate
    the Role dropdown.
    """
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    """SELECT device_id, kind FROM ip_allocations
                       WHERE kind IN ('switch','backbone','core','gateway')
                         AND device_id <> ''"""
                )
                return {r["device_id"]: r["kind"] for r in cur.fetchall()}
        except Exception as e:
            log.error(f"db_get_device_roles error: {e}")
            return {}

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        rows = con.execute(
            """SELECT device_id, kind FROM ip_allocations
               WHERE kind IN ('switch','backbone','core','gateway')
                 AND device_id <> ''"""
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception as e:
        log.error(f"db_get_device_roles error: {e}")
        return {}
    finally:
        con.close()


# ── Device-sync helpers (called INSIDE the DB writer thread) ───────────────
# These functions must NOT call _db_enqueue — they run directly in the
# single-writer thread and open/close their own connection.

def _ipam_upsert_direct(con, subnet_id: int, ip: str,
                         name: str, device_id: str) -> None:
    """Upsert an allocation directly on an open connection (no enqueue).
    In PG mode `con` is unused — a fresh pg_cursor is opened instead."""
    now = time.time()
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute(
                """INSERT INTO ip_allocations
                       (subnet_id, ip, name, modified_by, modified_at, device_id)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(subnet_id, ip) DO UPDATE SET
                     name=EXCLUDED.name,
                     modified_by=EXCLUDED.modified_by,
                     modified_at=EXCLUDED.modified_at,
                     device_id=EXCLUDED.device_id
                   WHERE ip_allocations.device_id = EXCLUDED.device_id
                      OR ip_allocations.device_id = ''""",
                (subnet_id, ip, name, 'system', now, device_id)
            )
        return

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


def _ipam_subnets_for_ip(con, ip_obj: ipaddress.IPv4Address) -> list:
    """Return list of (subnet_id, cidr) rows whose network contains ip_obj.
    In PG mode `con` is unused — a fresh pg_cursor is opened instead."""
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute("SELECT id, cidr FROM ipam_subnets")
            rows = [(r["id"], r["cidr"]) for r in cur.fetchall()]
    else:
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

    if is_pg():
        try:
            matched = _ipam_subnets_for_ip(None, ip_obj)
            if not matched:
                log.debug(f"IPAM sync device add: {did} ({name!r}) ip={ip_obj} — no matching subnet")
            else:
                for sid, cidr in matched:
                    _ipam_upsert_direct(None, sid, str(ip_obj), name, did)
                    log.debug(f"IPAM sync device add: {did} ({name!r}) → {ip_obj} in subnet {cidr} (id={sid})")
        except Exception as e:
            log.error(f"IPAM sync device add error (did={did}, host={host!r}): {e}")
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
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            host_changed = old_host != new_host
            if host_changed:
                log.debug(f"IPAM sync device update: {did} host changed {old_host!r} → {new_host!r}")
                try:
                    old_ip = str(ipaddress.ip_address(old_host))
                    with pg_cursor('main') as cur:
                        cur.execute(
                            "DELETE FROM ip_allocations WHERE ip=%s AND device_id=%s",
                            (old_ip, did)
                        )
                        if cur.rowcount:
                            log.debug(f"IPAM sync device update: released old allocation {old_ip} for {did}")
                except ValueError:
                    log.debug(f"IPAM sync device update: old host {old_host!r} for {did} is a hostname — no allocation to release")
            else:
                log.debug(f"IPAM sync device update: {did} name change to {new_name!r} (host unchanged)")

            try:
                new_ip_obj = ipaddress.ip_address(new_host)
            except ValueError:
                log.debug(f"IPAM sync device update: new host {new_host!r} for {did} is a hostname — no new allocation")
                return

            matched = _ipam_subnets_for_ip(None, new_ip_obj)
            if not matched:
                log.debug(f"IPAM sync device update: {did} ip={new_ip_obj} — no matching subnet")
            else:
                for sid, cidr in matched:
                    _ipam_upsert_direct(None, sid, str(new_ip_obj), new_name, did)
                    log.debug(f"IPAM sync device update: {did} → {new_ip_obj} in subnet {cidr} (id={sid})")

            if not host_changed:
                now = time.time()
                with pg_cursor('main') as cur:
                    cur.execute(
                        "UPDATE ip_allocations SET name=%s, modified_at=%s WHERE device_id=%s AND ip=%s",
                        (new_name, now, did, str(new_ip_obj))
                    )
                    if cur.rowcount:
                        log.debug(f"IPAM sync device update: name updated for {new_ip_obj} → {new_name!r}")
        except Exception as e:
            log.error(f"IPAM sync device update error (did={did}, old={old_host!r}, new={new_host!r}): {e}")
        return

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        host_changed = old_host != new_host
        if host_changed:
            log.debug(f"IPAM sync device update: {did} host changed {old_host!r} → {new_host!r}")
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
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute("DELETE FROM ip_allocations WHERE device_id=%s", (did,))
                if cur.rowcount:
                    log.info(f"IPAM sync device delete: removed {cur.rowcount} allocation(s) for device {did}")
                else:
                    log.debug(f"IPAM sync device delete: device {did} had no IPAM allocations")
        except Exception as e:
            log.error(f"IPAM sync device delete error (did={did}): {e}")
        return

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

    populated = 0
    skipped_hostname = 0

    if is_pg():
        try:
            with STATE._lock:
                devices = [(d.device_id, d.name, d.host) for d in STATE.devices.values()]
            log.debug(f"IPAM sync subnet add: scanning {len(devices)} device(s) for {cidr}")
            for did, name, host in devices:
                try:
                    ip_obj = ipaddress.ip_address(host)
                except ValueError:
                    skipped_hostname += 1
                    continue
                if ip_obj in net:
                    _ipam_upsert_direct(None, subnet_id, str(ip_obj), name, did)
                    log.debug(f"IPAM sync subnet add: auto-populated {ip_obj} → {name!r} (device={did})")
                    populated += 1
            log.info(f"IPAM sync subnet add: {cidr} — {populated} device(s) auto-populated, "
                     f"{skipped_hostname} hostname device(s) skipped")
        except Exception as e:
            log.error(f"IPAM sync subnet add error (subnet_id={subnet_id}, cidr={cidr!r}): {e}")
        return

    con = sqlite3.connect(DB_PATH, timeout=10)
    try:
        with STATE._lock:
            devices = [(d.device_id, d.name, d.host) for d in STATE.devices.values()]
        log.debug(f"IPAM sync subnet add: scanning {len(devices)} device(s) for {cidr}")
        for did, name, host in devices:
            try:
                ip_obj = ipaddress.ip_address(host)
            except ValueError:
                skipped_hostname += 1
                continue
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
