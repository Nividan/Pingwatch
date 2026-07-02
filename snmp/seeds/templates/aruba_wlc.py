"""
Aruba wireless — Mobility Controller (WLSX-SYSTEMEXT/WLSX-SWITCH) and
Instant / Virtual Controller (AI-AP-MIB).

Controller: CPU is a per-processor TABLE (sysExtProcessorLoad .13.1.3 —
there is no wlsxSysExtCpuUsedPercent scalar); memory is KB used/size
(.15.1.3/.15.1.2, computed %); flash is MB (.14.1.4/.14.1.3). Fan/PSU use
ArubaActiveState (1=active first). The internal-temperature object is a
DisplayString ("36 C") — not numerically thresholdable, so it is omitted.
AP/station totals are under .2.2.1.1.3.x (not .2.2.1.3.x).

Instant: everything hangs off aiAccessPointTable (indexed by AP MAC, named
via aiAPName): status 1=up/2=down, CPU %, memory bytes free/total
(computed %). Radio utilization/noise from aiRadioTable; client SNR from
aiClientTable named via aiClientName.

Sources: LibreNMS WLSX-SYSTEMEXT/WLSX-SWITCH/AI-AP-MIB raw MIBs, oidref
wlsxSwitchTotalNumAccessPoints, Aruba Instant MIB Reference Guide.
"""

from snmp.seeds.templates._util import scalar, table, pct_table

TEMPLATES = [
    {
        "builtin_key": "builtin:aruba-controller",
        "name": "Aruba Mobility Controller",
        "vendor": "HP / Aruba",
        "description": ("Per-processor CPU, memory %, flash %, fan + PSU "
                        "state, connected APs and associated stations. "
                        "(Controller temperature is a text string in the MIB "
                        "— not thresholdable, omitted.)"),
        "items": [
            table("Processor Load", "1.3.6.1.4.1.14823.2.2.1.2.1.13.1.3",
                  unit="%", warn=80, crit=90,
                  name_oid="1.3.6.1.4.1.14823.2.2.1.2.1.13.1.2"),  # sysExtProcessorDescr
            pct_table("Memory Used %",
                      "1.3.6.1.4.1.14823.2.2.1.2.1.15.1.3",  # sysExtMemoryUsed (KB)
                      "1.3.6.1.4.1.14823.2.2.1.2.1.15.1.2",  # sysExtMemorySize (KB)
                      mode="used_total", warn=85, crit=95),
            pct_table("Flash Used %",
                      "1.3.6.1.4.1.14823.2.2.1.2.1.14.1.4",  # sysExtStorageUsed (MB)
                      "1.3.6.1.4.1.14823.2.2.1.2.1.14.1.3",  # sysExtStorageSize (MB)
                      mode="used_total",
                      name_oid="1.3.6.1.4.1.14823.2.2.1.2.1.14.1.5",  # sysExtStorageName
                      warn=85, crit=95),
            table("Fan Status", "1.3.6.1.4.1.14823.2.2.1.2.1.17.1.2",
                  unit="1=active 2=inactive"),       # sysExtFanStatus
            table("PSU Status", "1.3.6.1.4.1.14823.2.2.1.2.1.18.1.2",
                  unit="1=active 2=inactive"),       # sysExtPowerSupplyStatus
            scalar("Access Points Connected",
                   "1.3.6.1.4.1.14823.2.2.1.1.3.1.0",
                   unit="count"),                    # wlsxSwitchTotalNumAccessPoints
            scalar("Stations Associated",
                   "1.3.6.1.4.1.14823.2.2.1.1.3.2.0",
                   unit="count"),                    # wlsxSwitchTotalNumStationsAssociated
        ],
    },
    {
        "builtin_key": "builtin:aruba-instant",
        "name": "Aruba Instant (Virtual Controller)",
        "vendor": "HP / Aruba",
        "description": ("Per-AP up/down, CPU %, memory %, uptime; per-radio "
                        "channel utilization + noise floor; per-client SNR. "
                        "All tables indexed by MAC — walked + named."),
        "items": [
            table("AP Status", "1.3.6.1.4.1.14823.2.3.3.1.2.1.1.11",
                  unit="1=up 2=down",
                  name_oid="1.3.6.1.4.1.14823.2.3.3.1.2.1.1.2"),   # aiAPName
            table("AP CPU Utilization", "1.3.6.1.4.1.14823.2.3.3.1.2.1.1.7",
                  unit="%", warn=80, crit=90,
                  name_oid="1.3.6.1.4.1.14823.2.3.3.1.2.1.1.2"),
            pct_table("AP Memory Used %",
                      "1.3.6.1.4.1.14823.2.3.3.1.2.1.1.8",   # aiAPMemoryFree (bytes)
                      "1.3.6.1.4.1.14823.2.3.3.1.2.1.1.10",  # aiAPTotalMemory (bytes)
                      mode="free_total",
                      name_oid="1.3.6.1.4.1.14823.2.3.3.1.2.1.1.2",
                      warn=85, crit=95),
            table("AP Uptime", "1.3.6.1.4.1.14823.2.3.3.1.2.1.1.9",
                  unit="1/100 sec",
                  name_oid="1.3.6.1.4.1.14823.2.3.3.1.2.1.1.2"),   # aiAPUptime
            table("Radio Channel Utilization (64s)",
                  "1.3.6.1.4.1.14823.2.3.3.1.2.2.1.8",
                  unit="%", warn=60, crit=80),       # aiRadioUtilization64
            table("Radio Noise Floor", "1.3.6.1.4.1.14823.2.3.3.1.2.2.1.6",
                  unit="dbm"),                       # aiRadioNoiseFloor
            table("Client SNR", "1.3.6.1.4.1.14823.2.3.3.1.2.4.1.7",
                  unit="dB",
                  name_oid="1.3.6.1.4.1.14823.2.3.3.1.2.4.1.5"),   # aiClientName
            scalar("Virtual Controller Name",
                   "1.3.6.1.4.1.14823.2.3.3.1.1.2.0", unit="string"),
        ],
    },
]
