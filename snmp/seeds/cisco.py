"""
snmp_seeds/cisco.py — Cisco IOS/IOS-XE/NX-OS trap definitions.

Enterprise OID: 1.3.6.1.4.1.9
MIBs: CISCO-SYSLOG-MIB, CISCO-HSRP-MIB, CISCO-BGP4-MIB, CISCO-ENVMON-MIB
"""

ENTERPRISE_MAP = [
    {"enterprise_oid": "1.3.6.1.4.1.9",       "vendor": "Cisco", "product_family": "IOS/NX-OS"},
    {"enterprise_oid": "1.3.6.1.4.1.9.1",     "vendor": "Cisco", "product_family": "Routers"},
    {"enterprise_oid": "1.3.6.1.4.1.9.5",     "vendor": "Cisco", "product_family": "Catalyst Switches"},
    {"enterprise_oid": "1.3.6.1.4.1.9.12",    "vendor": "Cisco", "product_family": "IOS"},
]

TRAP_DEFINITIONS = [
    {
        "trap_oid":           "1.3.6.1.4.1.9.9.41.2.0.1",
        "trap_name":          "ciscoSyslogMIBNotification",
        "vendor":             "Cisco",
        "product_family":     "IOS/NX-OS",
        "severity":           "warning",
        "category":           "system",
        "probable_cause":     "Cisco IOS generated a syslog message at or above the configured notification threshold.",
        "description":        "A Cisco syslog message notification trap.",
        "recommended_action": "Check syslog message text for details. Review device logs.",
        "varbind_hints":      {
            "1.3.6.1.4.1.9.9.41.1.2.3.1.2": "clogHistFacility",
            "1.3.6.1.4.1.9.9.41.1.2.3.1.3": "clogHistSeverity",
            "1.3.6.1.4.1.9.9.41.1.2.3.1.5": "clogHistMsgText",
        },
        "mib_name":           "CISCO-SYSLOG-MIB",
    },
    {
        "trap_oid":           "1.3.6.1.4.1.9.9.106.2.0.1",
        "trap_name":          "cHsrpStateChange",
        "vendor":             "Cisco",
        "product_family":     "IOS/NX-OS",
        "severity":           "warning",
        "category":           "routing",
        "probable_cause":     "HSRP state machine transitioned. A router may have become active/standby/init.",
        "description":        "Cisco HSRP group state change notification.",
        "recommended_action": "Verify active router is correct. Check standby router health. Review HSRP priority and preempt settings.",
        "varbind_hints":      {
            "1.3.6.1.4.1.9.9.106.1.2.1.1.15": "cHsrpGrpActiveRouter",
            "1.3.6.1.4.1.9.9.106.1.2.1.1.16": "cHsrpGrpStandbyRouter",
        },
        "mib_name":           "CISCO-HSRP-MIB",
    },
    {
        "trap_oid":           "1.3.6.1.4.1.9.9.187.0.0.1",
        "trap_name":          "cbgpBackwardTransition",
        "vendor":             "Cisco",
        "product_family":     "IOS/NX-OS",
        "severity":           "critical",
        "category":           "routing",
        "probable_cause":     "BGP peer transitioned to a less established state (e.g., Established → Idle). Possible causes: peer reset, authentication failure, hold-timer expiry.",
        "description":        "Cisco BGP backward transition — peer session dropped.",
        "recommended_action": "Check BGP peer status. Verify MD5 authentication keys. Check network connectivity to peer. Review BGP logs.",
        "varbind_hints":      {
            "1.3.6.1.2.1.15.3.1.7":  "bgpPeerRemoteAddr",
            "1.3.6.1.2.1.15.3.1.2":  "bgpPeerState",
        },
        "mib_name":           "CISCO-BGP4-MIB",
    },
    {
        "trap_oid":           "1.3.6.1.4.1.9.9.13.3.0.1",
        "trap_name":          "ciscoEnvMonVoltageNotification",
        "vendor":             "Cisco",
        "product_family":     "IOS/NX-OS",
        "severity":           "critical",
        "category":           "hardware",
        "probable_cause":     "Cisco device detected a voltage reading outside of normal range.",
        "description":        "A voltage sensor on the Cisco device has exceeded its threshold.",
        "recommended_action": "Check device power supply. Measure voltage on affected rail. Schedule hardware inspection.",
        "varbind_hints":      {
            "1.3.6.1.4.1.9.9.13.1.2.1.3": "ciscoEnvMonVoltageStatusDescr",
            "1.3.6.1.4.1.9.9.13.1.2.1.6": "ciscoEnvMonVoltageState",
        },
        "mib_name":           "CISCO-ENVMON-MIB",
    },
    {
        "trap_oid":           "1.3.6.1.4.1.9.9.13.3.0.2",
        "trap_name":          "ciscoEnvMonTemperatureNotification",
        "vendor":             "Cisco",
        "product_family":     "IOS/NX-OS",
        "severity":           "critical",
        "category":           "hardware",
        "probable_cause":     "Cisco device temperature sensor exceeded threshold. Possible causes: blocked airflow, fan failure, high ambient temperature.",
        "description":        "A temperature sensor on the Cisco device has exceeded its threshold.",
        "recommended_action": "Check device airflow and fan status. Verify rack cooling. Reduce load or move device to cooler environment.",
        "varbind_hints":      {
            "1.3.6.1.4.1.9.9.13.1.3.1.3": "ciscoEnvMonTemperatureStatusDescr",
            "1.3.6.1.4.1.9.9.13.1.3.1.6": "ciscoEnvMonTemperatureState",
        },
        "mib_name":           "CISCO-ENVMON-MIB",
    },
    {
        "trap_oid":           "1.3.6.1.4.1.9.9.13.3.0.3",
        "trap_name":          "ciscoEnvMonFanNotification",
        "vendor":             "Cisco",
        "product_family":     "IOS/NX-OS",
        "severity":           "critical",
        "category":           "hardware",
        "probable_cause":     "Cisco device fan failure or abnormal fan speed detected.",
        "description":        "A fan sensor on the Cisco device has failed or is operating outside normal range.",
        "recommended_action": "Replace failed fan tray. Monitor device temperature. Schedule urgent hardware maintenance.",
        "varbind_hints":      {
            "1.3.6.1.4.1.9.9.13.1.4.1.3": "ciscoEnvMonFanStatusDescr",
            "1.3.6.1.4.1.9.9.13.1.4.1.4": "ciscoEnvMonFanState",
        },
        "mib_name":           "CISCO-ENVMON-MIB",
    },
    {
        "trap_oid":           "1.3.6.1.4.1.9.9.13.3.0.4",
        "trap_name":          "ciscoEnvMonSupplyNotification",
        "vendor":             "Cisco",
        "product_family":     "IOS/NX-OS",
        "severity":           "critical",
        "category":           "hardware",
        "probable_cause":     "Cisco device power supply failure detected.",
        "description":        "A power supply on the Cisco device has failed.",
        "recommended_action": "Replace failed power supply immediately. Verify redundant PSU is carrying the load. Schedule emergency maintenance.",
        "varbind_hints":      {
            "1.3.6.1.4.1.9.9.13.1.5.1.3": "ciscoEnvMonSupplyStatusDescr",
            "1.3.6.1.4.1.9.9.13.1.5.1.4": "ciscoEnvMonSupplyState",
        },
        "mib_name":           "CISCO-ENVMON-MIB",
    },
]
