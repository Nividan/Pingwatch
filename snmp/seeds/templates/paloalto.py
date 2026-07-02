"""
Palo Alto PAN-OS — PAN-COMMON-MIB (.25461.2.1.2) + HOST-RESOURCES +
ENTITY-SENSOR.

PAN-COMMON has NO native CPU/memory/session-rate objects: CPU comes from
hrProcessorLoad (mgmt + dataplane cores as separate rows) and memory from
hrStorageTable (used/size % per row). Sessions ARE native and pre-scaled
(panSessionUtilization 0–100). Environment sensors are standard
ENTITY-SENSOR-MIB (entPhySensorValue + RFC 3433 scale/precision), named via
entPhysicalDescr. HA state/mode are STRINGS ("active"/"passive" — passive is
normal on the standby). Site-to-site IPsec tunnel state is not exposed via
SNMP at all — GlobalProtect gateway tunnel counts are.

Sources: oidref.com panSys/panSession, LibreNMS PAN-COMMON-MIB raw MIB,
Palo Alto KB (hardware sensors via entity MIBs), RFC 3433.
"""

from snmp.seeds.templates._util import (scalar, table, pct_table,
                                        ENT_NAME, ENT_DESCR, HR_STORAGE_DESCR)

TEMPLATES = [{
    "builtin_key": "builtin:paloalto",
    "name": "Palo Alto PAN-OS",
    "vendor": "Palo Alto",
    "description": ("Session table utilization + counts (native), CPU via "
                    "host-resources per core, memory via hrStorage %, "
                    "GlobalProtect gateway load, entity sensors (auto-"
                    "scaled), HA state, software version + serial."),
    "items": [
        # ── Sessions (native, pre-scaled) ──
        scalar("Session Utilization", "1.3.6.1.4.1.25461.2.1.2.3.1.0",
               unit="%", warn=80, crit=90),          # panSessionUtilization
        scalar("Active Sessions", "1.3.6.1.4.1.25461.2.1.2.3.3.0",
               unit="count"),                        # panSessionActive
        scalar("Max Sessions", "1.3.6.1.4.1.25461.2.1.2.3.2.0",
               unit="count"),                        # panSessionMax
        scalar("Active TCP Sessions", "1.3.6.1.4.1.25461.2.1.2.3.4.0",
               unit="count"),                        # panSessionActiveTcp
        scalar("Active UDP Sessions", "1.3.6.1.4.1.25461.2.1.2.3.5.0",
               unit="count"),                        # panSessionActiveUdp
        scalar("SSL-Proxy Utilization", "1.3.6.1.4.1.25461.2.1.2.3.8.0",
               unit="%", warn=80, crit=90),          # panSessionSslProxyUtilization
        # ── CPU / memory (host-resources — nothing native in PAN-COMMON) ──
        table("CPU Core Load", "1.3.6.1.2.1.25.3.3.1.2", unit="%",
              warn=80, crit=90),                     # hrProcessorLoad
        pct_table("Memory / Storage Used %",
                  "1.3.6.1.2.1.25.2.3.1.6",          # hrStorageUsed
                  "1.3.6.1.2.1.25.2.3.1.5",          # hrStorageSize
                  mode="used_total", name_oid=HR_STORAGE_DESCR,
                  warn=85, crit=95),
        # ── GlobalProtect gateway ──
        scalar("GP Gateway Utilization", "1.3.6.1.4.1.25461.2.1.2.5.1.1.0",
               unit="%", warn=80, crit=90),          # panGPGWUtilizationPct
        scalar("GP Active Tunnels", "1.3.6.1.4.1.25461.2.1.2.5.1.3.0",
               unit="count"),                        # panGPGWUtilizationActiveTunnels
        # ── Environment (standard ENTITY-SENSOR, RFC 3433 auto-scale) ──
        table("Entity Sensor", "1.3.6.1.2.1.99.1.1.1.4",
              name_oid=ENT_DESCR, name_oid2=ENT_NAME,
              scale_oid="1.3.6.1.2.1.99.1.1.1.2",    # entPhySensorScale
              precision_oid="1.3.6.1.2.1.99.1.1.1.3"),  # entPhySensorPrecision
        table("Sensor Status", "1.3.6.1.2.1.99.1.1.1.5",
              unit="1=ok 2=unavailable 3=nonoperational",
              name_oid=ENT_DESCR, name_oid2=ENT_NAME),  # entPhySensorOperStatus
        # ── HA / identity (strings) ──
        scalar("HA State", "1.3.6.1.4.1.25461.2.1.2.1.11.0", unit="string"),
        scalar("HA Mode", "1.3.6.1.4.1.25461.2.1.2.1.13.0", unit="string"),
        scalar("Software Version", "1.3.6.1.4.1.25461.2.1.2.1.1.0",
               unit="string"),                       # panSysSwVersion
        scalar("Serial Number", "1.3.6.1.4.1.25461.2.1.2.1.3.0",
               unit="string"),                       # panSysSerialNumber
        scalar("Threat Content Version", "1.3.6.1.4.1.25461.2.1.2.1.9.0",
               unit="string"),                       # panSysThreatVersion
    ],
}]
