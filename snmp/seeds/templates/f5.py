"""
F5 BIG-IP — F5-BIGIP-SYSTEM-MIB (.3375.2).

Two CPU domains: host/system CPU per core via sysMultiHostCpuUsageRatio5s
(.7.5.2.1.19, already 0–100; composite host/cpu/slot index → walked), and
TMM data-plane cycles (not a ready percentage — omitted; would need delta
math). Two memory pools measured separately: TMM (sysStatMemoryUsed/Total,
bytes) and whole-system host memory (sysGlobalHostMemUsed/Total, bytes).
Chassis temp/fan/PSU tables are walked; fan/PSU enums are INVERTED vs
intuition (good=1, bad=0 — legend leads with 1). sysStatClientCurConns is
declared Counter64 but is semantically a current gauge — unit "count" keeps
it classified as a gauge, never rate-converted.

Sources: oidref.com sysMultiHostCpuUsageRatio5s/sysStat/sysChassis*,
Observium F5-BIGIP-SYSTEM-MIB, DataDog f5-big-ip profile.
"""

from snmp.seeds.templates._util import scalar, percent, table

TEMPLATES = [{
    "builtin_key": "builtin:f5-bigip",
    "name": "F5 BIG-IP",
    "vendor": "F5",
    "description": ("Per-core host CPU %, TMM + host memory %, chassis "
                    "temperature/fan/PSU, current client connections, "
                    "product version + serial."),
    "items": [
        # ── CPU (per core; index = host.cpu[.slot] composite) ──
        table("CPU Core Usage (5s)", "1.3.6.1.4.1.3375.2.1.7.5.2.1.19",
              unit="%", warn=80, crit=90),           # sysMultiHostCpuUsageRatio5s
        table("CPU Core Usage (1m)", "1.3.6.1.4.1.3375.2.1.7.5.2.1.27",
              unit="%", warn=80, crit=90),           # sysMultiHostCpuUsageRatio1m
        # ── Memory (bytes → computed %) ──
        percent("TMM Memory Used %",
                "1.3.6.1.4.1.3375.2.1.1.2.1.45.0",   # sysStatMemoryUsed
                "1.3.6.1.4.1.3375.2.1.1.2.1.44.0",   # sysStatMemoryTotal
                mode="used_total", warn=80, crit=90),
        percent("Host Memory Used %",
                "1.3.6.1.4.1.3375.2.1.1.2.20.3.0",   # sysGlobalHostMemUsed
                "1.3.6.1.4.1.3375.2.1.1.2.20.2.0",   # sysGlobalHostMemTotal
                mode="used_total", warn=85, crit=95),
        # ── Chassis environment ──
        table("Chassis Temperature", "1.3.6.1.4.1.3375.2.1.3.2.3.2.1.2",
              unit="°C", warn=55, crit=70),          # sysChassisTempTemperature
        table("Fan Status", "1.3.6.1.4.1.3375.2.1.3.2.1.2.1.2",
              unit="1=good 0=bad 2=notPresent"),     # sysChassisFanStatus
        table("Power Supply Status", "1.3.6.1.4.1.3375.2.1.3.2.2.2.1.2",
              unit="1=good 0=bad 2=notPresent"),     # sysChassisPowerSupplyStatus
        # ── Traffic / connections ──
        scalar("Client Connections (current)",
               "1.3.6.1.4.1.3375.2.1.1.2.1.8.0",
               unit="count"),                        # sysStatClientCurConns
        # ── Identity ──
        scalar("Product Version", "1.3.6.1.4.1.3375.2.1.4.2.0",
               unit="string"),                       # sysProductVersion
        scalar("Chassis Serial", "1.3.6.1.4.1.3375.2.1.3.3.3.0",
               unit="string"),                       # sysGeneralChassisSerialNum
    ],
}]
