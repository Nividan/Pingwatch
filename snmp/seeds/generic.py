"""
snmp_seeds/generic.py — RFC standard trap definitions.

Covers:
  - RFC 1157 / RFC 1215 generic traps (coldStart, warmStart, linkDown, linkUp,
    authenticationFailure, egpNeighborLoss)
  - SNMPv2-MIB traps (coldStart, warmStart, authenticationFailure, linkDown, linkUp)
  - IF-MIB link traps
"""

CATEGORIES = [
    {"name": "link",     "label": "Link State",      "color": "#f0a500"},
    {"name": "system",   "label": "System",           "color": "#388bfd"},
    {"name": "auth",     "label": "Authentication",   "color": "#f85149"},
    {"name": "hardware", "label": "Hardware",         "color": "#da3633"},
    {"name": "ups",      "label": "UPS / Power",      "color": "#e3b341"},
    {"name": "vpn",      "label": "VPN / Tunnel",     "color": "#bc8cff"},
    {"name": "config",   "label": "Configuration",    "color": "#56d364"},
    {"name": "routing",  "label": "Routing",          "color": "#79c0ff"},
    {"name": "security", "label": "Security",         "color": "#f85149"},
    {"name": "other",    "label": "Other",            "color": "#8b949e"},
]

ENTERPRISE_MAP = [
    {"enterprise_oid": "1.3.6.1.6.3.1.1.5", "vendor": "Generic",  "product_family": "RFC 1215"},
    {"enterprise_oid": "1.3.6.1.2.1.11",    "vendor": "Generic",  "product_family": "SNMPv2-MIB"},
]

TRAP_DEFINITIONS = [
    # ── SNMPv2c standard traps (1.3.6.1.6.3.1.1.5.x) ─────────────────────────
    {
        "trap_oid":           "1.3.6.1.6.3.1.1.5.1",
        "trap_name":          "coldStart",
        "vendor":             "Generic",
        "product_family":     "",
        "severity":           "warning",
        "category":           "system",
        "probable_cause":     "Device rebooted or powered on for the first time.",
        "description":        "A coldStart trap signifies that the SNMP entity has reinitialized itself and its configuration may have changed.",
        "recommended_action": "Verify device configuration is intact. Check if the reboot was expected (maintenance) or unexpected (power loss, crash).",
        "varbind_hints":      {},
        "mib_name":           "SNMPv2-MIB",
    },
    {
        "trap_oid":           "1.3.6.1.6.3.1.1.5.2",
        "trap_name":          "warmStart",
        "vendor":             "Generic",
        "product_family":     "",
        "severity":           "info",
        "category":           "system",
        "probable_cause":     "Device performed a soft reboot without configuration change.",
        "description":        "A warmStart trap signifies that the SNMP entity has reinitialized itself without any change to its configuration.",
        "recommended_action": "Informational. Verify device is operating normally after restart.",
        "varbind_hints":      {},
        "mib_name":           "SNMPv2-MIB",
    },
    {
        "trap_oid":           "1.3.6.1.6.3.1.1.5.3",
        "trap_name":          "linkDown",
        "vendor":             "Generic",
        "product_family":     "",
        "severity":           "critical",
        "category":           "link",
        "probable_cause":     "Network interface transitioned to down state. Possible causes: cable disconnected, remote device powered off, SFP failure, speed/duplex mismatch.",
        "description":        "A linkDown trap signifies that the SNMP entity has detected that the ifOperStatus object for one of its communication links left the up state.",
        "recommended_action": "Check physical cable and port LED. Verify remote device is online. Check interface config for errors or admin-down state.",
        "varbind_hints":      {
            "1.3.6.1.2.1.2.2.1.1":  "ifIndex",
            "1.3.6.1.2.1.2.2.1.2":  "ifDescr",
            "1.3.6.1.2.1.2.2.1.3":  "ifType",
            "1.3.6.1.2.1.2.2.1.8":  "ifOperStatus",
        },
        "mib_name":           "IF-MIB",
    },
    {
        "trap_oid":           "1.3.6.1.6.3.1.1.5.4",
        "trap_name":          "linkUp",
        "vendor":             "Generic",
        "product_family":     "",
        "severity":           "info",
        "category":           "link",
        "probable_cause":     "Network interface recovered and transitioned to up state.",
        "description":        "A linkUp trap signifies that the SNMP entity has detected that the ifOperStatus object for one of its communication links left the down state.",
        "recommended_action": "Informational. Verify traffic is flowing normally on the recovered interface.",
        "varbind_hints":      {
            "1.3.6.1.2.1.2.2.1.1":  "ifIndex",
            "1.3.6.1.2.1.2.2.1.2":  "ifDescr",
            "1.3.6.1.2.1.2.2.1.3":  "ifType",
            "1.3.6.1.2.1.2.2.1.8":  "ifOperStatus",
        },
        "mib_name":           "IF-MIB",
    },
    {
        "trap_oid":           "1.3.6.1.6.3.1.1.5.5",
        "trap_name":          "authenticationFailure",
        "vendor":             "Generic",
        "product_family":     "",
        "severity":           "warning",
        "category":           "auth",
        "probable_cause":     "SNMP request received with incorrect community string. May indicate misconfiguration or a scanning/probing attempt.",
        "description":        "An authenticationFailure trap signifies that the SNMP entity has received a protocol message not properly authenticated.",
        "recommended_action": "Review SNMP community strings. Check source IP for unauthorized scanning. Consider restricting SNMP access by ACL.",
        "varbind_hints":      {},
        "mib_name":           "SNMPv2-MIB",
    },
    {
        "trap_oid":           "1.3.6.1.6.3.1.1.5.6",
        "trap_name":          "egpNeighborLoss",
        "vendor":             "Generic",
        "product_family":     "",
        "severity":           "critical",
        "category":           "routing",
        "probable_cause":     "EGP neighbor relationship dropped. Remote AS is unreachable.",
        "description":        "An egpNeighborLoss trap signifies that an EGP neighbor relationship has been lost.",
        "recommended_action": "Check BGP/EGP peer status. Verify network connectivity to remote AS. Review routing logs.",
        "varbind_hints":      {
            "1.3.6.1.2.1.8.5.1.2": "egpNeighAddr",
        },
        "mib_name":           "RFC1213-MIB",
    },
]
