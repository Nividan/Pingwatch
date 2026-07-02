"""
db/snmp_sensor_templates.py — CRUD for per-vendor SNMP sensor templates.

A template is a named, per-vendor bundle of SNMP "items" (scalar OIDs and
per-index table OIDs) stored as a JSON array in `items_json`. Built-in
templates ship with the app (source='builtin', keyed by builtin_key for
idempotent re-seeding); users can add/clone/edit their own (source='user').

Mirrors the JSON-column pattern in db/reports.py and the idempotent seeding
pattern in db/trap_defs.py. All queries go through the backend-agnostic
helpers in db/helpers.py (write '?' placeholders — rewritten to %s for PG).
"""

import hashlib
import json
import time
import uuid

from core.logger import log
from db.backend  import is_pg
from db.helpers  import db_query, db_query_one, db_execute, db_cursor


def _new_id() -> str:
    return f"snmptpl_{uuid.uuid4().hex[:12]}"


def _row(r) -> dict:
    """Normalize a DB row → dict, inflating items_json into a list."""
    if not r:
        return None
    d = dict(r)
    v = d.get("items_json")
    if isinstance(v, str):
        try:
            d["items"] = json.loads(v) if v else []
        except Exception:
            d["items"] = []
    else:
        d["items"] = v or []
    d.pop("items_json", None)
    d["enabled"] = bool(d.get("enabled", 1))
    return d


# ── Read ─────────────────────────────────────────────────────────────

def db_list_snmp_templates() -> list:
    rows = db_query("main",
                    "SELECT * FROM snmp_sensor_templates ORDER BY vendor, name")
    return [_row(r) for r in rows]


def db_get_snmp_template(template_id: str) -> dict:
    row = db_query_one("main",
                       "SELECT * FROM snmp_sensor_templates WHERE id=?",
                       (template_id,))
    return _row(row)


# ── Write ────────────────────────────────────────────────────────────

def db_create_snmp_template(data: dict, created_by: str = "") -> str:
    """Create a user template. Always source='user' (builtins arrive via
    db_seed_snmp_templates). Returns the new id, or "" on failure."""
    tid = _new_id()
    now = time.time()
    items = data.get("items")
    items_str = items if isinstance(items, str) else json.dumps(items or [])
    _vals = (
        tid,
        data.get("name", ""),
        data.get("vendor", ""),
        data.get("description", ""),
        items_str,
        "user",   # source
        "",       # builtin_key — user rows never collide with seeds
        1 if data.get("enabled", True) else 0,
        created_by,
        now,
        now,
    )
    ok = db_execute(
        "main",
        """INSERT INTO snmp_sensor_templates
           (id, name, vendor, description, items_json, source, builtin_key,
            enabled, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        _vals,
    )
    return tid if ok else ""


def db_update_snmp_template(template_id: str, data: dict) -> bool:
    """Update name/vendor/description/items/enabled. Never changes `source`
    or `builtin_key` — an edited built-in stays a built-in. Marks the row
    user_modified so shipped updates to that built-in never clobber the
    user's edits (Reset = delete + re-seed restores the shipped default)."""
    items = data.get("items")
    items_str = items if isinstance(items, str) else json.dumps(items or [])
    _vals = (
        data.get("name", ""),
        data.get("vendor", ""),
        data.get("description", ""),
        items_str,
        1 if data.get("enabled", True) else 0,
        time.time(),
        template_id,
    )
    return db_execute(
        "main",
        """UPDATE snmp_sensor_templates
           SET name=?, vendor=?, description=?, items_json=?, enabled=?,
               updated_at=?, user_modified=1
           WHERE id=?""",
        _vals,
    )


def db_delete_snmp_template(template_id: str) -> bool:
    return db_execute("main",
                      "DELETE FROM snmp_sensor_templates WHERE id=?",
                      (template_id,))


# ── Seeding (versioned refresh, built-ins only) ──────────────────────

def _tpl_content_hash(name, vendor, description, items_str) -> str:
    """Deterministic content version for a shipped built-in — changes when
    any shipped field changes, so unedited installs pick up corrections."""
    basis = "|".join((str(name or ""), str(vendor or ""),
                      str(description or ""), str(items_str or "")))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def db_seed_snmp_templates(rows: list, prune: bool = True) -> None:
    """Seed built-in templates, keyed on builtin_key, with version refresh:
      - key not present                                → INSERT
      - present, un-edited, shipped content changed    → UPDATE in place
      - present, user-edited (user_modified=1)         → left untouched
      - un-edited built-in whose key is gone from the
        shipped set                                    → DELETE (pruned)
    User edits always win; Reset (delete + re-seed) restores the shipped
    default. Each row: {name, vendor, description, items(list), builtin_key}.

    `prune` must stay True for the startup seed, which is handed the FULL
    shipped catalogue and legitimately removes built-ins dropped from it.
    A single-key "Reset to default" (routes/monitoring.py) passes prune=False:
    it hands only the one template being reset, so pruning would delete every
    OTHER un-edited built-in whose key isn't in that one-element set."""
    shipped = {}
    for r in (rows or []):
        bkey = (r.get("builtin_key") or "").strip()
        if not bkey:
            continue  # a built-in must carry a stable key
        items = r.get("items")
        items_str = items if isinstance(items, str) else json.dumps(items or [])
        shipped[bkey] = (r.get("name", ""), r.get("vendor", ""),
                         r.get("description", ""), items_str)
    now = time.time()
    try:
        existing = {}
        for er in db_query("main",
                           "SELECT id, builtin_key, builtin_version, user_modified "
                           "FROM snmp_sensor_templates "
                           "WHERE source='builtin' AND builtin_key <> ''"):
            d = dict(er)
            existing[d.get("builtin_key")] = d
        for bkey, (name, vendor, desc, items_str) in shipped.items():
            ver = _tpl_content_hash(name, vendor, desc, items_str)
            cur = existing.get(bkey)
            if cur is None:
                db_execute(
                    "main",
                    """INSERT INTO snmp_sensor_templates
                       (id, name, vendor, description, items_json, source,
                        builtin_key, enabled, created_by, created_at,
                        updated_at, builtin_version, user_modified)
                       VALUES (?, ?, ?, ?, ?, 'builtin', ?, 1, 'system', ?, ?, ?, 0)""",
                    (_new_id(), name, vendor, desc, items_str, bkey, now, now, ver))
            elif (not int(cur.get("user_modified") or 0)
                    and (cur.get("builtin_version") or "") != ver):
                db_execute(
                    "main",
                    """UPDATE snmp_sensor_templates
                       SET name=?, vendor=?, description=?, items_json=?,
                           builtin_version=?, updated_at=?
                       WHERE id=?""",
                    (name, vendor, desc, items_str, ver, now, cur.get("id")))
        if prune:
            for bkey, cur in existing.items():
                if bkey not in shipped and not int(cur.get("user_modified") or 0):
                    db_execute("main",
                               "DELETE FROM snmp_sensor_templates WHERE id=?",
                               (cur.get("id"),))
    except Exception as e:
        log.error(f"db_seed_snmp_templates error: {e}")
