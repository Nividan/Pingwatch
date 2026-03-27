"""
snmp/vendor.py — Enterprise OID → vendor/product identification helpers.

Used by snmp/enricher to identify the vendor from a trap OID before
doing a full definition lookup.
"""
from __future__ import annotations


# Standard enterprise OID prefix (1.3.6.1.4.1.<enterprise>)
_ENTERPRISE_PREFIX = "1.3.6.1.4.1."

# Well-known enterprise numbers (fallback if DB not yet seeded)
_BUILTIN_ENTERPRISE = {
    "1.3.6.1.4.1.9":     {"vendor": "Cisco",    "product_family": "IOS/NX-OS"},
    "1.3.6.1.4.1.11":    {"vendor": "HPE",       "product_family": "ProCurve/Aruba"},
    "1.3.6.1.4.1.12356": {"vendor": "Fortinet",  "product_family": "FortiGate"},
    "1.3.6.1.4.1.2636":  {"vendor": "Juniper",   "product_family": "JunOS"},
    "1.3.6.1.4.1.318":   {"vendor": "APC",       "product_family": "UPS/PowerNet"},
    "1.3.6.1.4.1.4413":  {"vendor": "Ubiquiti",  "product_family": "UniFi/EdgeOS"},
    "1.3.6.1.4.1.14988": {"vendor": "MikroTik",  "product_family": "RouterOS"},
    "1.3.6.1.4.1.6876":  {"vendor": "VMware",    "product_family": "vSphere"},
    "1.3.6.1.4.1.25506": {"vendor": "HPE",       "product_family": "Comware"},
    "1.3.6.1.4.1.3955":  {"vendor": "Palo Alto", "product_family": "PAN-OS"},
    "1.3.6.1.4.1.8072":  {"vendor": "Net-SNMP",  "product_family": "Linux/Net-SNMP"},
}

# Generic RFC trap OID prefixes (not under enterprise arc)
_GENERIC_PREFIXES = {
    "1.3.6.1.6.3.1.1.5": {"vendor": "Generic", "product_family": "RFC 1215"},
    "1.3.6.1.2.1.11":    {"vendor": "Generic", "product_family": "SNMPv2-MIB"},
}


def extract_enterprise_oid(trap_oid: str) -> str | None:
    """
    Extract the enterprise OID prefix from a trap OID.
    For 1.3.6.1.4.1.12356.101.x.y returns '1.3.6.1.4.1.12356'.
    Returns None if trap_oid is not under the enterprise arc.
    """
    if not trap_oid:
        return None
    if trap_oid.startswith(_ENTERPRISE_PREFIX):
        # enterprise number is the 7th arc (index 6)
        parts = trap_oid.split(".")
        if len(parts) > 6:
            return ".".join(parts[:7])   # e.g. 1.3.6.1.4.1.12356
    return None


def identify_vendor(trap_oid: str, db_lookup_fn=None) -> dict:
    """
    Identify vendor/product_family from a trap OID.

    Strategy:
      1. Check RFC generic prefixes
      2. Try DB lookup (enterprise_oid_map) if db_lookup_fn provided
      3. Fall back to built-in enterprise map
      4. Return {"vendor": "Unknown", "product_family": ""}

    db_lookup_fn: callable(enterprise_oid) -> dict|None  (from db.trap_defs)
    """
    if not trap_oid:
        return {"vendor": "Unknown", "product_family": ""}

    # Check generic RFC prefixes first
    for prefix, info in _GENERIC_PREFIXES.items():
        if trap_oid.startswith(prefix):
            return dict(info)

    # Extract enterprise OID
    ent_oid = extract_enterprise_oid(trap_oid)
    if not ent_oid:
        return {"vendor": "Unknown", "product_family": ""}

    # Try DB lookup (walks up the tree internally)
    if db_lookup_fn:
        try:
            result = db_lookup_fn(trap_oid)  # passes full OID; db walks up
            if result:
                return result
        except Exception:
            pass

    # Fall back to built-in map
    result = _BUILTIN_ENTERPRISE.get(ent_oid)
    if result:
        return dict(result)

    # Try shorter prefixes in built-in map (e.g. sub-enterprise)
    parts = ent_oid.split(".")
    for length in range(len(parts) - 1, 6, -1):
        prefix = ".".join(parts[:length])
        result = _BUILTIN_ENTERPRISE.get(prefix)
        if result:
            return dict(result)

    return {"vendor": "Unknown", "product_family": ""}
