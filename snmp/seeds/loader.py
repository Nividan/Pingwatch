"""
snmp/seeds/loader.py — Idempotent seed loader for all built-in trap packs.

Called once at server startup (after db_init). Uses INSERT OR IGNORE so
re-runs are safe and existing user-modified records are not overwritten.
"""

from db.trap_defs import (
    db_seed_definitions,
    db_seed_enterprise_map,
    db_seed_categories,
)
from core.logger import log


def load_all_seeds():
    """Load all built-in seed data into the database (idempotent)."""
    try:
        from . import generic, fortinet, cisco, apc, juniper

        # Categories (from generic — shared across all vendors)
        db_seed_categories(generic.CATEGORIES)

        # Enterprise OID maps
        for mod in (generic, fortinet, cisco, apc, juniper):
            if hasattr(mod, "ENTERPRISE_MAP"):
                db_seed_enterprise_map(mod.ENTERPRISE_MAP)

        # Trap definitions
        for mod in (generic, fortinet, cisco, apc, juniper):
            if hasattr(mod, "TRAP_DEFINITIONS"):
                db_seed_definitions(mod.TRAP_DEFINITIONS)

        total = sum(
            len(m.TRAP_DEFINITIONS)
            for m in (generic, fortinet, cisco, apc, juniper)
            if hasattr(m, "TRAP_DEFINITIONS")
        )
        log.info(f"SNMP seeds loaded: {total} trap definitions across 5 vendors")

    except Exception as e:
        log.error(f"SNMP seed load error: {e}")

    # Built-in SNMP sensor templates (per-vendor OID bundles + Interfaces).
    try:
        from db import db_seed_snmp_templates
        from . import snmp_templates
        db_seed_snmp_templates(snmp_templates.SNMP_SENSOR_TEMPLATES)
        log.info(f"SNMP sensor templates seeded: "
                 f"{len(snmp_templates.SNMP_SENSOR_TEMPLATES)} built-ins")
    except Exception as e:
        log.error(f"SNMP template seed error: {e}")

    # Load any user-supplied MIB files from snmp/mibs/
    try:
        import os
        from core.config import _ROOT
        from snmp.mib_loader import load_mibs_from_dir
        mibs_dir = os.path.join(_ROOT, "snmp", "mibs")
        if os.path.isdir(mibs_dir):
            n = load_mibs_from_dir(mibs_dir)
            if n:
                log.info(f"MIB loader: {n} trap definitions loaded from snmp/mibs/")
    except Exception as e:
        log.warning(f"MIB loader error (non-fatal): {e}")
