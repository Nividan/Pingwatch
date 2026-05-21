"""
db/sites.py — Sites metadata (Live Map NOC console).

The `sites` table is a sidecar — distinct site names still come from
`devices.site` and `ipam_subnets.site`. Rows here store only presentation
metadata (kind, pinned flag, display_name, sort_order) so the new Live Map
can colour the sidebar mosaic and sites-by-type widget consistently.

Rows are created lazily by the Live Map rollup when it sees a site name
that has no metadata yet — fresh installs need no seeding.
"""
from __future__ import annotations

import time

from core.logger import log
from db.backend  import is_pg
from db.helpers  import db_query, db_query_one, db_cursor


KNOWN_KINDS = ("internet", "hq", "dc", "lab", "pop", "edge", "office")


def _now() -> int:
    return int(time.time())


def _normalize_kind(kind: str) -> str:
    k = (kind or "").strip().lower()
    return k if k in KNOWN_KINDS else "lab"


def db_list_sites() -> list:
    """Return all metadata rows ordered by sort_order then name."""
    rows = db_query("main", """
        SELECT name, kind, pinned, display_name, sort_order, created_ts, updated_ts
        FROM sites
        ORDER BY sort_order, name
    """)
    return [{
        "name":         r["name"],
        "kind":         r["kind"] or "lab",
        "pinned":       int(r["pinned"] or 0),
        "display_name": r["display_name"] or "",
        "sort_order":   int(r["sort_order"] or 0),
        "created_ts":   int(r["created_ts"] or 0),
        "updated_ts":   int(r["updated_ts"] or 0),
    } for r in rows]


def db_get_site_meta(name: str):
    """Return a single metadata row by name, or None."""
    if not name:
        return None
    row = db_query_one("main",
        "SELECT name, kind, pinned, display_name, sort_order, created_ts, updated_ts "
        "FROM sites WHERE name=?", (name,))
    if not row:
        return None
    return {
        "name":         row["name"],
        "kind":         row["kind"] or "lab",
        "pinned":       int(row["pinned"] or 0),
        "display_name": row["display_name"] or "",
        "sort_order":   int(row["sort_order"] or 0),
        "created_ts":   int(row["created_ts"] or 0),
        "updated_ts":   int(row["updated_ts"] or 0),
    }


def db_upsert_site_meta(name: str, *, kind: str = "lab",
                        pinned: int = 0, display_name: str = "",
                        sort_order: int = 0) -> bool:
    """Insert or update a metadata row. Returns True on success."""
    n = (name or "").strip()
    if not n:
        return False
    kind = _normalize_kind(kind)
    pinned = 1 if int(pinned or 0) else 0
    display_name = (display_name or "").strip()
    sort_order = int(sort_order or 0)
    now = _now()
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            if is_pg():
                cur.execute(
                    f"INSERT INTO sites (name, kind, pinned, display_name, sort_order, created_ts, updated_ts) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph}) "
                    f"ON CONFLICT (name) DO UPDATE SET "
                    f"kind=EXCLUDED.kind, pinned=EXCLUDED.pinned, "
                    f"display_name=EXCLUDED.display_name, sort_order=EXCLUDED.sort_order, "
                    f"updated_ts=EXCLUDED.updated_ts",
                    (n, kind, pinned, display_name, sort_order, now, now)
                )
            else:
                # SQLite: use INSERT OR REPLACE but preserve created_ts when row exists.
                existing = cur.execute(
                    "SELECT created_ts FROM sites WHERE name=?", (n,)
                ).fetchone()
                created_ts = existing[0] if existing else now
                cur.execute(
                    "INSERT OR REPLACE INTO sites "
                    "(name, kind, pinned, display_name, sort_order, created_ts, updated_ts) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (n, kind, pinned, display_name, sort_order, created_ts, now)
                )
        return True
    except Exception as e:
        log.error(f"db_upsert_site_meta error: {type(e).__name__}: {e}")
        return False


def db_ensure_site_meta(name: str, *, kind: str = "lab", pinned: int = 0) -> None:
    """If `name` has no metadata row, insert one with the given defaults.
    No-op if a row already exists. Used by the Live Map rollup to populate
    metadata lazily for sites that appear in devices.site but not here yet."""
    n = (name or "").strip()
    if not n:
        return
    existing = db_query_one("main", "SELECT 1 FROM sites WHERE name=?", (n,))
    if existing:
        return
    db_upsert_site_meta(n, kind=kind, pinned=pinned)


def db_rename_site_meta(old_name: str, new_name: str,
                        also_rename_devices: bool = False) -> bool:
    """Rename a metadata row. Optionally bulk-update devices.site too.
    Returns True on success, False if old_name not found or new_name exists."""
    old = (old_name or "").strip()
    new = (new_name or "").strip()
    if not old or not new or old == new:
        return False
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            # Check old exists
            row = cur.execute(f"SELECT 1 FROM sites WHERE name={ph}", (old,)).fetchone()
            if not row:
                return False
            # Check new doesn't exist
            row = cur.execute(f"SELECT 1 FROM sites WHERE name={ph}", (new,)).fetchone()
            if row:
                return False
            cur.execute(f"UPDATE sites SET name={ph}, updated_ts={ph} WHERE name={ph}",
                        (new, _now(), old))
            if also_rename_devices:
                cur.execute(f"UPDATE devices SET site={ph} WHERE site={ph}", (new, old))
        return True
    except Exception as e:
        log.error(f"db_rename_site_meta error: {type(e).__name__}: {e}")
        return False


def db_delete_site_meta(name: str) -> bool:
    """Delete a metadata row. Devices keep their site string.
    Returns True if deleted, False if not found or on error."""
    n = (name or "").strip()
    if not n:
        return False
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(f"DELETE FROM sites WHERE name={ph}", (n,))
            return cur.rowcount > 0
    except Exception as e:
        log.error(f"db_delete_site_meta error: {type(e).__name__}: {e}")
        return False


def db_distinct_site_names() -> list:
    """UNION of distinct site names from devices and ipam_subnets.
    Returns a sorted list of strings (case-insensitive sort, non-empty only)."""
    names = set()
    try:
        for r in db_query("main", "SELECT DISTINCT site FROM devices WHERE site <> ''"):
            v = (r["site"] or "").strip()
            if v:
                names.add(v)
    except Exception as e:
        log.warning(f"db_distinct_site_names devices query failed: {e}")
    try:
        for r in db_query("main", "SELECT DISTINCT site FROM ipam_subnets WHERE site <> ''"):
            v = (r["site"] or "").strip()
            if v:
                names.add(v)
    except Exception as e:
        log.warning(f"db_distinct_site_names ipam query failed: {e}")
    return sorted(names, key=str.lower)
