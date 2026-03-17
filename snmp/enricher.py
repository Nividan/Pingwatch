"""
snmp/enricher.py — SNMP trap enrichment pipeline.

Takes a raw parsed trap dict and enriches it with:
  - vendor / product family identification
  - trap name resolution
  - severity mapping
  - category classification
  - probable cause / description / recommended action
  - structured raw_varbinds (JSON)
"""

import json

from db.trap_defs import db_lookup_trap, db_lookup_enterprise
from .vendor import identify_vendor


def enrich_trap(raw: dict) -> dict:
    """
    Enrich a raw trap event dict.

    Input keys (from snmp/receiver):
      ts, src_ip, dname, community, trap_oid, detail, varbinds (list), _direction

    Returns a new dict with all snmp_traps columns populated.
    The original keys are preserved; enrichment fields are added/overwritten.
    enriched=1 means a matching definition was found; 0 means unknown trap.
    """
    trap_oid = raw.get("trap_oid", "")
    varbinds = raw.get("varbinds", [])

    # Serialize raw varbinds to JSON
    raw_varbinds_json = json.dumps(varbinds) if varbinds else "[]"

    # Step 1: exact trap OID lookup
    defn = db_lookup_trap(trap_oid) if trap_oid else None

    if defn:
        enriched = {
            **raw,
            "vendor":             defn["vendor"],
            "product_family":     defn["product_family"],
            "trap_name":          defn["trap_name"],
            "severity":           defn["severity"],
            "category":           defn["category"],
            "probable_cause":     defn["probable_cause"],
            "recommended_action": defn["recommended_action"],
            "description":        defn.get("description", ""),
            "varbind_hints":      defn.get("varbind_hints", {}),
            "raw_varbinds":       raw_varbinds_json,
            "enriched":           1,
        }
        return enriched

    # Step 2: no definition found — identify vendor from enterprise OID
    vendor_info = identify_vendor(trap_oid, db_lookup_fn=db_lookup_enterprise)

    return {
        **raw,
        "vendor":             vendor_info.get("vendor", "Unknown"),
        "product_family":     vendor_info.get("product_family", ""),
        "trap_name":          "",        # unknown
        "severity":           "info",
        "category":           "",
        "probable_cause":     "",
        "recommended_action": "",
        "description":        "",
        "varbind_hints":      {},
        "raw_varbinds":       raw_varbinds_json,
        "enriched":           0,
    }
