"""
Item constructors shared by the authored template modules.

Field semantics (must stay in sync with routes/monitoring._snmp_tpl_clean_items
and monitoring.probes.snmp_discover_template):
  kind="scalar"  — one snmpget; oid is a full instance OID (usually ….0).
  kind="percent" — two snmpgets combined per mode:
                     used_total → 100·A/B        (A=used,  B=total)
                     used_free  → 100·A/(A+B)    (A=used,  B=free)
                     free_total → 100·(B−A)/B    (A=free,  B=total)
  kind="table"   — oid is a column base, walked; one sensor per row, named by
                   walking name_oid → name_oid2 → index suffix. A table item
                   may also carry oid2+percent_mode (per-row computed %),
                   scale (static divisor), or scale_oid+precision_oid
                   (RFC 3433 entity-sensor auto-scale per row).

Enum units ("2=up 1=down"): the FIRST pair listed is the healthy state —
author fault-first enums accordingly ("0=ok 1=alarm" leads with 0).
"""

# Standard name columns reused across vendors
ENT_NAME  = "1.3.6.1.2.1.47.1.1.1.1.7"    # entPhysicalName  (ENTITY-MIB)
ENT_DESCR = "1.3.6.1.2.1.47.1.1.1.1.2"    # entPhysicalDescr (ENTITY-MIB)
HR_STORAGE_DESCR = "1.3.6.1.2.1.25.2.3.1.3"  # hrStorageDescr


def scalar(label, oid, unit="", warn=None, crit=None, scale=None):
    it = {"kind": "scalar", "label": label, "oid": oid, "unit": unit,
          "warn": warn, "crit": crit, "interval": None}
    if scale:
        it["scale"] = scale
    return it


def percent(label, oid_a, oid_b, mode="used_total", warn=80, crit=90):
    """Scalar computed-%: A=oid_a, B=oid_b combined per mode."""
    return {"kind": "percent", "label": label, "oid": oid_a, "oid2": oid_b,
            "percent_mode": mode, "unit": "%",
            "warn": warn, "crit": crit, "interval": None}


def table(label, oid, unit="", name_oid="", name_oid2="", warn=None, crit=None,
          scale=None, scale_oid="", precision_oid="", oid2="",
          percent_mode="", speed_auto_threshold=False, speed_oid="",
          hc_variant_oid=""):
    it = {"kind": "table", "label": label, "oid": oid, "unit": unit,
          "warn": warn, "crit": crit, "interval": None}
    if name_oid:
        it["name_oid"] = name_oid
    if name_oid2:
        it["name_oid2"] = name_oid2
    if scale:
        it["scale"] = scale
    if scale_oid:
        it["scale_oid"] = scale_oid
    if precision_oid:
        it["precision_oid"] = precision_oid
    if oid2:
        it["oid2"] = oid2
        it["percent_mode"] = percent_mode or "used_total"
    if speed_auto_threshold:
        it["speed_auto_threshold"] = True
    if speed_oid:
        it["speed_oid"] = speed_oid
    if hc_variant_oid:
        it["hc_variant_oid"] = hc_variant_oid
    return it


def pct_table(label, oid_a, oid_b, mode="used_total", name_oid="",
              name_oid2="", warn=80, crit=90):
    """Table computed-%: walk column A + partner column B, % per row."""
    return table(label, oid_a, unit="%", name_oid=name_oid,
                 name_oid2=name_oid2, warn=warn, crit=crit,
                 oid2=oid_b, percent_mode=mode)
