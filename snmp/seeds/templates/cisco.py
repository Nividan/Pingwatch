"""
Cisco IOS / IOS-XE / NX-OS.

CPU uses the current cpmCPUTotal*Rev columns (5secRev=6, 1minRev=7,
5minRev=8; the non-Rev 3/4/5 are deprecated and absent on modern IOS-XE).
Memory prefers CISCO-ENHANCED-MEMPOOL (HCUsed=18/HCFree=20, name=3) with the
legacy CISCO-MEMORY-POOL (Used=5/Free=6, name=2) as fallback — both as
walked used/(used+free) percentages. Environment: CISCO-ENVMON tables
(whole °C + state enums; state cols differ per table: temp=6, fan=3, psu=3)
plus CISCO-ENTITY-SENSOR with RFC 3433 auto-scale (value=4, scale=2,
precision=3) named via entPhysicalName. FRU fan/PSU oper status from
CISCO-ENTITY-FRU-CONTROL (entPhysicalIndex-indexed → entPhysicalName join).
NX-OS: no ENVMON/legacy-mempool — cseSysCPU/MemoryUtilization scalars
(CISCO-SYSTEM-EXT) + entity sensors cover it.

Sources: cisco-mibs (github.com/cisco/cisco-mibs) CISCO-PROCESS-MIB,
CISCO-ENHANCED-MEMPOOL-MIB, CISCO-ENTITY-SENSOR-MIB, CISCO-ENVMON-MIB,
CISCO-ENTITY-FRU-CONTROL-MIB, CISCO-SYSTEM-EXT-MIB; oidref.com; Observium/
LibreNMS definitions.
"""

from snmp.seeds.templates._util import scalar, table, pct_table, ENT_NAME, ENT_DESCR

_ENVMON_STATE = ("1=normal 2=warning 3=critical 4=shutdown 5=notPresent "
                 "6=notFunctioning")

TEMPLATES = [{
    "builtin_key": "builtin:cisco",
    "name": "Cisco IOS / IOS-XE / NX-OS",
    "vendor": "Cisco",
    "description": ("CPU (cpmCPUTotal Rev columns, per core), memory pools "
                    "(enhanced + legacy, used %), ENVMON temperature/fan/PSU, "
                    "entity sensors with RFC 3433 auto-scaling, FRU fan/PSU "
                    "status, NX-OS system-ext scalars, chassis identity."),
    "items": [
        # ── CPU (CISCO-PROCESS-MIB cpmCPUTotalTable, per CPU/core) ──
        table("CPU 1-min Avg", "1.3.6.1.4.1.9.9.109.1.1.1.1.7", unit="%",
              warn=80, crit=90),                     # cpmCPUTotal1minRev
        table("CPU 5-min Avg", "1.3.6.1.4.1.9.9.109.1.1.1.1.8", unit="%",
              warn=80, crit=90),                     # cpmCPUTotal5minRev
        # ── NX-OS aggregates (CISCO-SYSTEM-EXT-MIB) ──
        scalar("CPU Utilization (NX-OS)", "1.3.6.1.4.1.9.9.305.1.1.1.0",
               unit="%", warn=80, crit=90),          # cseSysCPUUtilization
        scalar("Memory Utilization (NX-OS)", "1.3.6.1.4.1.9.9.305.1.1.2.0",
               unit="%", warn=85, crit=95),          # cseSysMemoryUtilization
        # ── Memory pools → used/(used+free) % per pool ──
        pct_table("Memory Pool Used %",
                  "1.3.6.1.4.1.9.9.221.1.1.1.1.18",  # cempMemPoolHCUsed
                  "1.3.6.1.4.1.9.9.221.1.1.1.1.20",  # cempMemPoolHCFree
                  mode="used_free",
                  name_oid="1.3.6.1.4.1.9.9.221.1.1.1.1.3",  # cempMemPoolName
                  warn=80, crit=90),
        pct_table("Memory Pool Used % (legacy)",
                  "1.3.6.1.4.1.9.9.48.1.1.1.5",      # ciscoMemoryPoolUsed
                  "1.3.6.1.4.1.9.9.48.1.1.1.6",      # ciscoMemoryPoolFree
                  mode="used_free",
                  name_oid="1.3.6.1.4.1.9.9.48.1.1.1.2",     # ciscoMemoryPoolName
                  warn=80, crit=90),
        # ── Environment — CISCO-ENVMON (whole °C; per-table state cols) ──
        table("Temperature", "1.3.6.1.4.1.9.9.13.1.3.1.3", unit="°C",
              name_oid="1.3.6.1.4.1.9.9.13.1.3.1.2",         # …TempStatusDescr
              warn=60, crit=75),
        table("Temperature State", "1.3.6.1.4.1.9.9.13.1.3.1.6",
              unit=_ENVMON_STATE,
              name_oid="1.3.6.1.4.1.9.9.13.1.3.1.2"),
        table("Fan State", "1.3.6.1.4.1.9.9.13.1.4.1.3",
              unit=_ENVMON_STATE,
              name_oid="1.3.6.1.4.1.9.9.13.1.4.1.2"),        # …FanStatusDescr
        table("Power Supply State", "1.3.6.1.4.1.9.9.13.1.5.1.3",
              unit=_ENVMON_STATE,
              name_oid="1.3.6.1.4.1.9.9.13.1.5.1.2"),        # …SupplyStatusDescr
        # ── Entity sensors (CISCO-ENTITY-SENSOR, RFC 3433 auto-scale) ──
        table("Entity Sensor", "1.3.6.1.4.1.9.9.91.1.1.1.1.4",
              name_oid=ENT_NAME, name_oid2=ENT_DESCR,
              scale_oid="1.3.6.1.4.1.9.9.91.1.1.1.1.2",      # entSensorScale
              precision_oid="1.3.6.1.4.1.9.9.91.1.1.1.1.3"), # entSensorPrecision
        # ── FRU control (entPhysicalIndex-indexed → name joins directly) ──
        table("FRU Power Status", "1.3.6.1.4.1.9.9.117.1.1.2.1.2",
              unit=("2=on 1=offEnvOther 3=offAdmin 4=offDenied 5=offEnvPower "
                    "6=offEnvTemp 7=offEnvFan 8=failed 9=onButFanFail"),
              name_oid=ENT_NAME, name_oid2=ENT_DESCR),       # cefcFRUPowerOperStatus
        table("Fan Tray Status", "1.3.6.1.4.1.9.9.117.1.4.1.1.1",
              unit="2=up 3=down 4=warning 1=unknown",
              name_oid=ENT_NAME, name_oid2=ENT_DESCR),       # cefcFanTrayOperStatus
        # ── Identity (ENTITY-MIB; pick the chassis row in the preview) ──
        table("Serial Number", "1.3.6.1.2.1.47.1.1.1.1.11", unit="string",
              name_oid=ENT_NAME, name_oid2=ENT_DESCR),       # entPhysicalSerialNum
        table("Model Name", "1.3.6.1.2.1.47.1.1.1.1.13", unit="string",
              name_oid=ENT_NAME, name_oid2=ENT_DESCR),       # entPhysicalModelName
    ],
}]
