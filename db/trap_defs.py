"""
db/trap_defs.py — DB access layer for SNMP trap intelligence tables.

Tables managed here:
  - trap_definitions    : known trap OID → name/severity/description
  - enterprise_oid_map  : enterprise OID prefix → vendor/product
  - trap_categories     : category name → display label/color
"""

import json
import sqlite3

from core.config import DB_PATH
from core.logger import log


# ── Trap definitions ──────────────────────────────────────────────────────────

def db_lookup_trap(trap_oid: str) -> dict | None:
    """Exact OID lookup in trap_definitions. Returns definition dict or None."""
    try:
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            "SELECT trap_name,vendor,product_family,severity,category,"
            "probable_cause,description,recommended_action,varbind_hints,mib_name "
            "FROM trap_definitions WHERE trap_oid=?",
            (trap_oid,)
        ).fetchone()
        con.close()
        if not row:
            return None
        return {
            "trap_name":          row[0],
            "vendor":             row[1],
            "product_family":     row[2],
            "severity":           row[3],
            "category":           row[4],
            "probable_cause":     row[5],
            "description":        row[6],
            "recommended_action": row[7],
            "varbind_hints":      json.loads(row[8] or "{}"),
            "mib_name":           row[9],
        }
    except Exception as e:
        log.error(f"db_lookup_trap error: {e}")
        return None


def db_seed_definitions(rows: list):
    """Insert trap definitions with INSERT OR IGNORE (idempotent)."""
    if not rows:
        return
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        con.executemany(
            "INSERT OR IGNORE INTO trap_definitions "
            "(trap_oid,trap_name,vendor,product_family,severity,category,"
            "probable_cause,description,recommended_action,varbind_hints,mib_name,source) "
            "VALUES (:trap_oid,:trap_name,:vendor,:product_family,:severity,:category,"
            ":probable_cause,:description,:recommended_action,:varbind_hints,:mib_name,:source)",
            [_prep_def(r) for r in rows]
        )
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"db_seed_definitions error: {e}")


def _prep_def(r: dict) -> dict:
    d = dict(r)
    if isinstance(d.get("varbind_hints"), dict):
        d["varbind_hints"] = json.dumps(d["varbind_hints"])
    d.setdefault("vendor", "")
    d.setdefault("product_family", "")
    d.setdefault("severity", "info")
    d.setdefault("category", "")
    d.setdefault("probable_cause", "")
    d.setdefault("description", "")
    d.setdefault("recommended_action", "")
    d.setdefault("varbind_hints", "{}")
    d.setdefault("mib_name", "")
    d.setdefault("source", "builtin")
    return d


def db_get_trap_vendors() -> list:
    """Return distinct vendor names from trap_definitions."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT DISTINCT vendor FROM trap_definitions WHERE vendor!='' ORDER BY vendor"
        ).fetchall()
        con.close()
        return [r[0] for r in rows]
    except Exception as e:
        log.error(f"db_get_trap_vendors error: {e}")
        return []


# ── Enterprise OID map ────────────────────────────────────────────────────────

def db_lookup_enterprise(enterprise_oid: str) -> dict | None:
    """
    Look up vendor by enterprise OID prefix.
    Tries exact match first, then walks up the OID tree to find a prefix match.
    e.g. '1.3.6.1.4.1.12356.101.4.5' → matches '1.3.6.1.4.1.12356' (Fortinet)
    """
    try:
        con = sqlite3.connect(DB_PATH)
        # Try progressively shorter prefixes (walk up the tree)
        parts = enterprise_oid.split(".")
        for length in range(len(parts), 5, -1):
            prefix = ".".join(parts[:length])
            row = con.execute(
                "SELECT vendor,product_family FROM enterprise_oid_map WHERE enterprise_oid=?",
                (prefix,)
            ).fetchone()
            if row:
                con.close()
                return {"vendor": row[0], "product_family": row[1]}
        con.close()
        return None
    except Exception as e:
        log.error(f"db_lookup_enterprise error: {e}")
        return None


def db_seed_enterprise_map(rows: list):
    """Insert enterprise OID mappings with INSERT OR IGNORE (idempotent)."""
    if not rows:
        return
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        con.executemany(
            "INSERT OR IGNORE INTO enterprise_oid_map "
            "(enterprise_oid,vendor,product_family,notes) "
            "VALUES (:enterprise_oid,:vendor,:product_family,:notes)",
            [_prep_ent(r) for r in rows]
        )
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"db_seed_enterprise_map error: {e}")


def _prep_ent(r: dict) -> dict:
    d = dict(r)
    d.setdefault("product_family", "")
    d.setdefault("notes", "")
    return d


# ── Trap categories ───────────────────────────────────────────────────────────

def db_get_trap_categories() -> list:
    """Return all trap categories as list of {name, label, color}."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT name,label,color FROM trap_categories ORDER BY name"
        ).fetchall()
        con.close()
        return [{"name": r[0], "label": r[1], "color": r[2]} for r in rows]
    except Exception as e:
        log.error(f"db_get_trap_categories error: {e}")
        return []


def db_seed_categories(rows: list):
    """Insert trap categories with INSERT OR IGNORE (idempotent)."""
    if not rows:
        return
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        con.executemany(
            "INSERT OR IGNORE INTO trap_categories (name,label,color) "
            "VALUES (:name,:label,:color)",
            rows
        )
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"db_seed_categories error: {e}")
