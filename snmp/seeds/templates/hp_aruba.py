"""
HP ProCurve (legacy) + ArubaOS-CX switching.

ProCurve: hpSwitchCpuStat scalar (11.2.14.11.5.1.9.6.1.0, %); memory from
NETSWITCH hpLocalMemTable (Total=.5 / Alloc(used)=.7 bytes, walked per
slot); temp/fan/PSU are rows of HP-ICF-CHASSIS hpicfSensorTable — status
enum only, no numeric °C (Status=.4: 4=good first; Descr=.7 names the row).
Serial = SEMI-MIB hpHttpMgSerialNumber 11.2.36.1.1.2.9.0 (not .5.1.1).

ArubaOS-CX: no vendor CPU/mem scalar is publicly verified — CPU is
hrProcessorLoad; memory works via hpLocalMemTable on ICF-compatible models
(6100/6000) and is model-dependent elsewhere. Environment via standard
ENTITY-SENSOR entPhySensorValue with RFC 3433 scale/precision (read per
row — the community's hardcoded ×0.001 is a per-model shortcut), named via
entPhysicalName; identity via entPhysical serial/software columns.

Sources: oidref.com hpSwitchCpuStat/hpLocalMemEntry/hpicfSensorEntry,
circitor hpicfSensorStatus enum, Zabbix Aruba-CX community template,
RFC 3433, Airheads CX memory thread.
"""

from snmp.seeds.templates._util import scalar, table, pct_table, ENT_NAME, ENT_DESCR

TEMPLATES = [
    {
        "builtin_key": "builtin:hp-procurve",
        "name": "HP ProCurve",
        "vendor": "HP / Aruba",
        "description": ("CPU %, per-slot memory %, chassis sensor status "
                        "(temp/fan/PSU rows of hpicfSensorTable), serial."),
        "items": [
            scalar("CPU Utilization", "1.3.6.1.4.1.11.2.14.11.5.1.9.6.1.0",
                   unit="%", warn=80, crit=90),      # hpSwitchCpuStat
            pct_table("Memory Used %",
                      "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.7",  # hpLocalMemAllocBytes
                      "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.5",  # hpLocalMemTotalBytes
                      mode="used_total", warn=80, crit=90),
            table("Chassis Sensor Status", "1.3.6.1.4.1.11.2.14.11.1.2.6.1.4",
                  unit="4=good 3=warning 2=bad 5=notPresent 1=unknown",
                  name_oid="1.3.6.1.4.1.11.2.14.11.1.2.6.1.7"),  # hpicfSensorDescr
            scalar("Serial Number", "1.3.6.1.4.1.11.2.36.1.1.2.9.0",
                   unit="string"),                   # hpHttpMgSerialNumber
            scalar("System Description", "1.3.6.1.2.1.1.1.0", unit="string"),
        ],
    },
    {
        "builtin_key": "builtin:aruba-cx",
        "name": "ArubaOS-CX",
        "vendor": "HP / Aruba",
        "description": ("CPU per core (host-resources), entity sensors with "
                        "RFC 3433 auto-scaling (temp/fan/PSU/voltage), sensor "
                        "status, per-slot memory % (ICF-compatible models), "
                        "chassis identity. CX has no verified vendor CPU/mem "
                        "scalar — memory coverage varies by model."),
        "items": [
            table("CPU Core Load", "1.3.6.1.2.1.25.3.3.1.2", unit="%",
                  warn=80, crit=90),                 # hrProcessorLoad
            table("Entity Sensor", "1.3.6.1.2.1.99.1.1.1.4",
                  name_oid=ENT_NAME, name_oid2=ENT_DESCR,
                  scale_oid="1.3.6.1.2.1.99.1.1.1.2",
                  precision_oid="1.3.6.1.2.1.99.1.1.1.3"),  # entPhySensorValue
            table("Sensor Status", "1.3.6.1.2.1.99.1.1.1.5",
                  unit="1=ok 2=unavailable 3=nonoperational",
                  name_oid=ENT_NAME, name_oid2=ENT_DESCR),  # entPhySensorOperStatus
            pct_table("Memory Used % (ICF models)",
                      "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.7",
                      "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.5",
                      mode="used_total", warn=80, crit=90),
            table("Serial Number", "1.3.6.1.2.1.47.1.1.1.1.11", unit="string",
                  name_oid=ENT_NAME, name_oid2=ENT_DESCR),  # entPhysicalSerialNum
            table("Firmware Version", "1.3.6.1.2.1.47.1.1.1.1.10", unit="string",
                  name_oid=ENT_NAME, name_oid2=ENT_DESCR),  # entPhysicalSoftwareRev
        ],
    },
]
