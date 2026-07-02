"""
snmp/seeds/templates — authored, MIB-verified built-in SNMP sensor templates.

One module per vendor family, each exporting TEMPLATES (a list of template
dicts consumed by snmp/seeds/snmp_templates.py). Every OID is verified
against the vendor MIB (oidref.com / mibbrowser.online / Observium MIB
browser / LibreNMS MIB files / vendor docs) — sources are cited in each
module header. Replaces the retired fixed-index catalog-generated templates.
"""
