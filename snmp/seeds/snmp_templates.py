"""
snmp/seeds/snmp_templates.py — Built-in SNMP sensor templates.

Seeded idempotently at startup (source='builtin', keyed by builtin_key). Scalar
templates are generated from snmp.catalog.SNMP_CATALOG so there's a single
source of OID truth; the Interfaces template mirrors the interface metrics the
"Discover Interfaces" flow uses (table items with per-row naming, speed-based
auto-thresholds, and 64-bit counter variants).
"""

import re

from snmp.catalog import SNMP_CATALOG

# Shared interface naming/speed OIDs (index appended at discovery time where the
# base ends in a dot).
_IF_NAME  = "1.3.6.1.2.1.31.1.1.1.1"   # ifName (ifXTable)
_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"      # ifDescr (fallback name)
_IF_SPEED = "1.3.6.1.2.1.2.2.1.5."     # ifSpeed (base)


def _table(label, oid, unit, **extra):
    it = {"kind": "table", "label": label, "oid": oid, "unit": unit,
          "warn": None, "crit": None, "interval": None,
          "name_oid": _IF_NAME, "name_oid2": _IF_DESCR,
          "speed_auto_threshold": False}
    it.update(extra)
    return it


# ── Interfaces (folds the Discover Interfaces flow into a template) ──
_INTERFACES = {
    "builtin_key": "builtin:interfaces",
    "name": "Interfaces (ifTable)",
    "vendor": "Standard",
    "description": ("Per-interface traffic, errors, discards and status. Traffic "
                    "items prefer 64-bit counters and derive warn/crit from link "
                    "speed."),
    "items": [
        _table("In Traffic",   "1.3.6.1.2.1.2.2.1.10.", "bytes",
               speed_auto_threshold=True, speed_oid=_IF_SPEED,
               hc_variant_oid="1.3.6.1.2.1.31.1.1.1.6."),
        _table("Out Traffic",  "1.3.6.1.2.1.2.2.1.16.", "bytes",
               speed_auto_threshold=True, speed_oid=_IF_SPEED,
               hc_variant_oid="1.3.6.1.2.1.31.1.1.1.10."),
        _table("In Errors",    "1.3.6.1.2.1.2.2.1.14.", "errors"),
        _table("Out Errors",   "1.3.6.1.2.1.2.2.1.20.", "errors"),
        _table("In Discards",  "1.3.6.1.2.1.2.2.1.13.", "packets"),
        _table("Out Discards", "1.3.6.1.2.1.2.2.1.19.", "packets"),
        _table("Oper Status",  "1.3.6.1.2.1.2.2.1.8.",  "1=up 2=down"),
        _table("Admin Status", "1.3.6.1.2.1.2.2.1.7.",  "1=up 2=down"),
    ],
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40] or "vendor"


def _catalog_templates() -> list:
    """One scalar template per SNMP_CATALOG vendor group."""
    out = []
    for group in SNMP_CATALOG:
        vendor = (group.get("vendor") or "").strip()
        items = []
        for e in (group.get("oids") or []):
            oid = (e.get("oid") or "").strip()
            label = (e.get("label") or "").strip()
            if not oid or not label:
                continue
            items.append({"kind": "scalar", "label": label, "oid": oid,
                          "unit": (e.get("unit") or "").strip(),
                          "warn": None, "crit": None, "interval": None})
        if not items:
            continue
        out.append({
            "builtin_key": f"builtin:catalog:{_slug(vendor)}",
            "name": vendor or "Catalog",
            "vendor": vendor,
            "description": "Built-in scalar OIDs from the SNMP catalog.",
            "items": items,
        })
    return out


SNMP_SENSOR_TEMPLATES = [_INTERFACES] + _catalog_templates()
