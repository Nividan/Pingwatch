"""
routes/sites.py — Site metadata CRUD for the Live Map.

GET    /api/sites/meta              viewer    — list metadata rows
POST   /api/sites/meta              operator  — create {name, kind, pinned?, display_name?}
PUT    /api/sites/meta/<name>       operator  — update {kind?, pinned?, display_name?, new_name?, also_rename?}
DELETE /api/sites/meta/<name>       operator  — delete metadata only (devices keep site string)

The free-text `devices.site` column is the source of truth for which
sites exist; this sidecar stores presentation metadata (kind, pinned,
display name). Deleting metadata does not remove the site itself.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse, parse_qs

from db import (
    db_log_audit,
    db_list_sites, db_get_site_meta, db_upsert_site_meta,
    db_rename_site_meta, db_delete_site_meta, db_distinct_site_names,
    db_site_usage, KNOWN_KINDS,
)


_RE_SITE_META_COLL  = re.compile(r"^/api/sites/meta/?$")
_RE_SITE_META_ITEM  = re.compile(r"^/api/sites/meta/([^/]+)/?$")
_RE_SITE_META_USAGE = re.compile(r"^/api/sites/meta/([^/]+)/usage/?$")


def _validate_kind(kind) -> bool:
    return (kind or "").strip().lower() in KNOWN_KINDS


def _name_from_path(path: str) -> str:
    # Strip query string first so we don't carry "?cascade=1" into the name
    base = path.split("?", 1)[0]
    m = _RE_SITE_META_ITEM.match(base)
    if not m:
        m = _RE_SITE_META_USAGE.match(base)
    if not m:
        return ""
    return unquote(m.group(1)).strip()


def _query_flag(path: str, key: str) -> bool:
    q = urlparse(path).query
    if not q:
        return False
    vals = parse_qs(q).get(key, [])
    return bool(vals) and vals[0] not in ("0", "false", "")


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # Strip query string for path matching (so /api/sites/meta/X?cascade=1 still matches)
    base_path = path.split("?", 1)[0]

    # GET /api/sites/meta/<name>/usage — counts for delete-confirm UI
    if _RE_SITE_META_USAGE.match(base_path) and method == "GET":
        user, _ = h._require("viewer")
        if not user:
            return True
        name = _name_from_path(base_path)
        if not name:
            h._json(400, {"error": "invalid name"}); return True
        h._json(200, db_site_usage(name))
        return True

    # GET /api/sites/meta — list metadata + the set of known site names.
    if _RE_SITE_META_COLL.match(base_path) and method == "GET":
        user, _ = h._require("viewer")
        if not user:
            return True
        meta = db_list_sites()
        all_names = db_distinct_site_names()
        meta_by_name = {m["name"]: m for m in meta}
        # Merge: every distinct site name appears in the response, with a
        # default-metadata placeholder if no row exists yet.
        merged = []
        for name in all_names:
            m = meta_by_name.get(name)
            if m:
                merged.append(m)
            else:
                merged.append({
                    "name": name, "kind": "lab", "pinned": 0,
                    "display_name": "", "sort_order": 0,
                    "created_ts": 0, "updated_ts": 0,
                })
        # Include metadata-only sites that aren't in the distinct list yet
        for m in meta:
            if m["name"] not in all_names:
                merged.append(m)
        h._json(200, {"sites": merged, "kinds": list(KNOWN_KINDS)})
        return True

    # POST /api/sites/meta — create
    if _RE_SITE_META_COLL.match(base_path) and method == "POST":
        user, _ = h._require("operator")
        if not user:
            return True
        name = (body.get("name") or "").strip()
        if not name:
            h._json(400, {"error": "name is required"}); return True
        if len(name) > 100:
            h._json(400, {"error": "name too long (max 100)"}); return True
        kind = (body.get("kind") or "lab").strip().lower()
        if not _validate_kind(kind):
            h._json(400, {"error": f"kind must be one of {list(KNOWN_KINDS)}"}); return True
        if db_get_site_meta(name):
            h._json(409, {"error": "site already exists"}); return True
        ok = db_upsert_site_meta(
            name,
            kind=kind,
            pinned=int(body.get("pinned") or 0),
            display_name=(body.get("display_name") or "").strip(),
            sort_order=int(body.get("sort_order") or 0),
        )
        if not ok:
            h._json(500, {"error": "failed to create site"}); return True
        db_log_audit(user, h.client_address[0], "site_create", f"{name} kind={kind}")
        h._json(200, {"ok": True, "site": db_get_site_meta(name)})
        return True

    # PUT /api/sites/meta/<name> — update / rename
    if _RE_SITE_META_ITEM.match(base_path) and method == "PUT":
        user, _ = h._require("operator")
        if not user:
            return True
        name = _name_from_path(path)
        if not name:
            h._json(400, {"error": "invalid name"}); return True
        existing = db_get_site_meta(name)
        # If no metadata yet but devices have this site, treat as upsert-from-blank.
        if not existing:
            distinct = db_distinct_site_names()
            if name not in distinct:
                h._json(404, {"error": "site not found"}); return True

        new_name = (body.get("new_name") or "").strip()
        also_rename = bool(body.get("also_rename"))

        kind = (body.get("kind") or (existing or {}).get("kind") or "lab").strip().lower()
        if not _validate_kind(kind):
            h._json(400, {"error": f"kind must be one of {list(KNOWN_KINDS)}"}); return True

        target_name = new_name or name

        # Apply rename first if needed
        if new_name and new_name != name:
            if not existing:
                # Bootstrap a metadata row so the rename path has something to move
                db_upsert_site_meta(name, kind=kind, pinned=int(body.get("pinned") or 0))
            ok = db_rename_site_meta(name, new_name, also_rename_devices=also_rename)
            if not ok:
                h._json(409, {"error": "rename failed (target may already exist)"}); return True
            db_log_audit(user, h.client_address[0], "site_rename",
                         f"{name} -> {new_name} also_rename={also_rename}")

        ok = db_upsert_site_meta(
            target_name,
            kind=kind,
            pinned=int(body.get("pinned") or (existing or {}).get("pinned") or 0),
            display_name=(body.get("display_name") if "display_name" in body
                          else (existing or {}).get("display_name", "")),
            sort_order=int(body.get("sort_order") if "sort_order" in body
                           else (existing or {}).get("sort_order", 0)),
        )
        if not ok:
            h._json(500, {"error": "failed to update site"}); return True
        db_log_audit(user, h.client_address[0], "site_update",
                     f"{target_name} kind={kind}")
        h._json(200, {"ok": True, "site": db_get_site_meta(target_name)})
        return True

    # DELETE /api/sites/meta/<name>?cascade=1
    if _RE_SITE_META_ITEM.match(base_path) and method == "DELETE":
        user, _ = h._require("operator")
        if not user:
            return True
        name = _name_from_path(base_path)
        if not name:
            h._json(400, {"error": "invalid name"}); return True
        cascade = _query_flag(path, "cascade")
        ok = db_delete_site_meta(name, cascade=cascade)
        # 200 even if nothing changed — the user's intent is "have no
        # metadata / no assignments for this site"; either way that's true now.
        db_log_audit(user, h.client_address[0], "site_delete",
                     f"{name} cascade={cascade} ok={ok}")
        h._json(200, {"ok": True, "deleted": ok, "cascade": cascade})
        return True

    return False
