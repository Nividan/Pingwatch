"""
snmp/seeds/snmp_templates.py — Built-in SNMP sensor templates.

Seeded with versioned refresh at startup (source='builtin', keyed by
builtin_key; unedited built-ins are updated in place when the shipped
content changes, user-edited ones are never touched — see
db/snmp_sensor_templates.db_seed_snmp_templates).

The vendor templates are authored, MIB-verified modules under
snmp/seeds/templates/ (walked table items with per-row naming, computed
percentages, static + RFC 3433 auto scaling, enum legends with the healthy
state listed first). They replace the earlier fixed-index templates
generated from snmp/catalog.py — the catalog remains only as the manual
Add-Sensor OID picker and a scan-aggregation source. The Interfaces
template mirrors the interface metrics the "Discover Interfaces" flow uses
(speed-based auto-thresholds, 64-bit counter variants).
"""

from snmp.seeds.templates import (host_generic, cisco, fortinet, juniper,
                                  paloalto, mikrotik, hp_aruba, f5, ups,
                                  vmware, meraki, aruba_wlc, ubiquiti,
                                  dell_idrac, hpe_ilo)

# Shared interface naming/speed OIDs (index appended at discovery time where
# the base ends in a dot).
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


SNMP_SENSOR_TEMPLATES = (
    [_INTERFACES]
    + host_generic.TEMPLATES
    + cisco.TEMPLATES
    + fortinet.TEMPLATES
    + juniper.TEMPLATES
    + paloalto.TEMPLATES
    + mikrotik.TEMPLATES
    + hp_aruba.TEMPLATES
    + f5.TEMPLATES
    + ups.TEMPLATES
    + vmware.TEMPLATES
    + meraki.TEMPLATES
    + aruba_wlc.TEMPLATES
    + ubiquiti.TEMPLATES
    + dell_idrac.TEMPLATES
    + hpe_ilo.TEMPLATES
)
