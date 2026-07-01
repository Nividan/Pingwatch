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
    or `builtin_key` — an edited built-in stays a built-in (its edits are
    preserved across re-seeds; use delete + re-seed to reset to default)."""
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
           SET name=?, vendor=?, description=?, items_json=?, enabled=?, updated_at=?
           WHERE id=?""",
        _vals,
    )


def db_delete_snmp_template(template_id: str) -> bool:
    return db_execute("main",
                      "DELETE FROM snmp_sensor_templates WHERE id=?",
                      (template_id,))


# ── Seeding (idempotent, built-ins only) ─────────────────────────────

def db_seed_snmp_templates(rows: list) -> None:
    """Insert built-in templates, keyed on builtin_key. Idempotent: an
    existing key is left untouched (ON CONFLICT DO NOTHING / INSERT OR
    IGNORE), so user edits to a built-in survive re-seeding on every start.
    Each row: {name, vendor, description, items(list), builtin_key}."""
    if not rows:
        return
    now = time.time()
    prepped = []
    for r in rows:
        bkey = (r.get("builtin_key") or "").strip()
        if not bkey:
            continue  # a built-in must carry a stable key
        items = r.get("items")
        items_str = items if isinstance(items, str) else json.dumps(items or [])
        prepped.append((
            _new_id(), r.get("name", ""), r.get("vendor", ""),
            r.get("description", ""), items_str, "builtin", bkey,
            1, "system", now, now,
        ))
    if not prepped:
        return
    cols = ("id, name, vendor, description, items_json, source, builtin_key, "
            "enabled, created_by, created_at, updated_at")
    try:
        if is_pg():
            with db_cursor("main") as cur:
                import psycopg2.extras
                psycopg2.extras.execute_values(
                    cur,
                    f"INSERT INTO snmp_sensor_templates ({cols}) VALUES %s "
                    f"ON CONFLICT (builtin_key) WHERE builtin_key <> '' DO NOTHING",
                    prepped,
                )
        else:
            with db_cursor("main") as cur:
                cur.executemany(
                    f"INSERT OR IGNORE INTO snmp_sensor_templates ({cols}) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    prepped,
                )
    except Exception as e:
        log.error(f"db_seed_snmp_templates error: {e}")
