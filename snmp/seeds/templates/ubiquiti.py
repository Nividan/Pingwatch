"""
Ubiquiti — UniFi APs (UBNT-UniFi-MIB + FROGFOOT), airMAX/airOS (UBNT-AirMAX
+ UCD), EdgeOS routers/switches (UCD/HOST-RESOURCES + FASTPATH).

UniFi: the UniFi MIB has no CPU/memory — those come from the bundled
FROGFOOT-RESOURCES MIB (loadValue is a PERCENTAGE per the MIB text, walked
with loadDescr names; memory is KB). Radio/VAP tables walked + named
(unifiRadioName / unifiVapEssId).

airMAX: link quality from ubntWlStatTable (signal dBm — most firmware
returns negative values; CCQ %), per-station detail from ubntStaTable
(named), airtime from ubntAirMaxTable (percent ×10 → scale 10). CPU/RAM
via UCD (airOS is Linux).

EdgeOS: UCD memory (KB) + hrProcessorLoad; load averages ×100. EdgeSwitch
temperature/fan live under FASTPATH boxServices — subtree varies by
firmware (.1.1 vs .1.19), so both variants are included and discovery keeps
whichever answers. EdgeRouter exposes no temperature via stock SNMP.

Sources: LibreNMS UBNT-UniFi-MIB/UBNT-AirMAX-MIB, FROGFOOT-RESOURCES-MIB,
net-snmp UCD-SNMP-MIB, EdgeSwitch-BOXSERVICES-PRIVATE-MIB (mibbrowser +
Observium copies disagree on the subtree — both shipped).
"""

from snmp.seeds.templates._util import scalar, percent, table, pct_table

TEMPLATES = [
    {
        "builtin_key": "builtin:ubnt-unifi",
        "name": "Ubiquiti UniFi AP",
        "vendor": "Ubiquiti",
        "description": ("Per-radio channel utilization + interference, "
                        "per-SSID clients + CCQ, CPU % and memory % via the "
                        "bundled FROGFOOT resources MIB, model + firmware."),
        "items": [
            table("Radio Channel Utilization",
                  "1.3.6.1.4.1.41112.1.6.1.1.1.6",   # unifiRadioCuTotal
                  unit="%", warn=75, crit=90,
                  name_oid="1.3.6.1.4.1.41112.1.6.1.1.1.2"),   # unifiRadioName
            table("Radio Other-BSS Airtime",
                  "1.3.6.1.4.1.41112.1.6.1.1.1.9",   # unifiRadioOtherBss
                  unit="%", warn=40,
                  name_oid="1.3.6.1.4.1.41112.1.6.1.1.1.2"),
            table("SSID Clients", "1.3.6.1.4.1.41112.1.6.1.2.1.8",
                  unit="count",
                  name_oid="1.3.6.1.4.1.41112.1.6.1.2.1.6"),   # unifiVapEssId
            table("SSID CCQ", "1.3.6.1.4.1.41112.1.6.1.2.1.3",
                  unit="%",
                  name_oid="1.3.6.1.4.1.41112.1.6.1.2.1.6"),   # unifiVapCcq
            table("CPU Load", "1.3.6.1.4.1.10002.1.1.1.4.2.1.3",
                  unit="%", warn=75, crit=90,
                  name_oid="1.3.6.1.4.1.10002.1.1.1.4.2.1.2"), # FROGFOOT loadDescr
            percent("Memory Used %",
                    "1.3.6.1.4.1.10002.1.1.1.1.2.0",  # memFree (KB)
                    "1.3.6.1.4.1.10002.1.1.1.1.1.0",  # memTotal (KB)
                    mode="free_total", warn=85, crit=95),
            scalar("Model", "1.3.6.1.4.1.41112.1.6.3.3.0", unit="string"),
            scalar("Firmware", "1.3.6.1.4.1.41112.1.6.3.6.0", unit="string"),
        ],
    },
    {
        "builtin_key": "builtin:ubnt-airmax",
        "name": "Ubiquiti airMAX / airOS",
        "vendor": "Ubiquiti",
        "description": ("Wireless link quality (signal dBm / CCQ / noise "
                        "floor), airMAX quality + capacity, per-station "
                        "signal + latency, CPU/memory via UCD, radio power. "
                        "Signal sign varies by firmware — verify on-device."),
        "items": [
            table("Link Signal", "1.3.6.1.4.1.41112.1.4.5.1.5",
                  unit="dbm",
                  name_oid="1.3.6.1.4.1.41112.1.4.5.1.2"),     # ubntWlStatSsid
            table("Link CCQ", "1.3.6.1.4.1.41112.1.4.5.1.7", unit="%",
                  name_oid="1.3.6.1.4.1.41112.1.4.5.1.2"),
            table("Noise Floor", "1.3.6.1.4.1.41112.1.4.5.1.8",
                  unit="dbm",
                  name_oid="1.3.6.1.4.1.41112.1.4.5.1.2"),
            table("airMAX Quality", "1.3.6.1.4.1.41112.1.4.6.1.3", unit="%"),
            table("airMAX Capacity", "1.3.6.1.4.1.41112.1.4.6.1.4", unit="%"),
            table("airMAX Airtime", "1.3.6.1.4.1.41112.1.4.6.1.7",
                  unit="%", scale=10),               # percent × 10
            table("Station Signal", "1.3.6.1.4.1.41112.1.4.7.1.3",
                  unit="dbm",
                  name_oid="1.3.6.1.4.1.41112.1.4.7.1.2"),     # ubntStaName
            table("Station TX Latency", "1.3.6.1.4.1.41112.1.4.7.1.21",
                  unit="ms", warn=30,
                  name_oid="1.3.6.1.4.1.41112.1.4.7.1.2"),
            percent("Memory Used % (UCD)",
                    "1.3.6.1.4.1.2021.4.6.0",         # memAvailReal (KB)
                    "1.3.6.1.4.1.2021.4.5.0",         # memTotalReal (KB)
                    mode="free_total", warn=85, crit=95),
            scalar("Radio TX Power", "1.3.6.1.4.1.41112.1.4.1.1.6.1",
                   unit="dbm"),                      # ubntRadioTxPower (radio 1)
            scalar("Device Temperature", "1.3.6.1.4.1.41112.1.4.8.4.0",
                   unit="°C", warn=60, crit=75),     # ubntHostTemperature (0 on many models)
        ],
    },
    {
        "builtin_key": "builtin:ubnt-edge",
        "name": "Ubiquiti EdgeOS (EdgeRouter / EdgeSwitch)",
        "vendor": "Ubiquiti",
        "description": ("CPU per core + load averages, memory % (UCD), "
                        "EdgeSwitch temperature/fan (FASTPATH boxServices — "
                        "both firmware subtrees probed). EdgeRouter exposes "
                        "no temperature via stock SNMP."),
        "items": [
            table("CPU Core Load", "1.3.6.1.2.1.25.3.3.1.2", unit="%",
                  warn=80, crit=90),                 # hrProcessorLoad
            percent("Memory Used %",
                    "1.3.6.1.4.1.2021.4.6.0",         # memAvailReal (KB)
                    "1.3.6.1.4.1.2021.4.5.0",         # memTotalReal (KB)
                    mode="free_total", warn=85, crit=95),
            table("Load Average", "1.3.6.1.4.1.2021.10.1.5",
                  name_oid="1.3.6.1.4.1.2021.10.1.2",
                  scale=100),                        # laLoadInt = load × 100
            # EdgeSwitch FASTPATH boxServices — firmware places the agent
            # node at .1.1 or .1.19; discovery keeps whichever answers.
            table("Temperature Sensor", "1.3.6.1.4.1.4413.1.1.43.1.8.1.5",
                  unit="°C", warn=60, crit=75),      # boxServicesTempSensorTemperature
            table("Temperature Sensor (alt subtree)",
                  "1.3.6.1.4.1.4413.1.19.43.1.8.1.5",
                  unit="°C", warn=60, crit=75),
            table("Fan State", "1.3.6.1.4.1.4413.1.1.43.1.6.1.3",
                  unit="1=operational 2=failed 0=notpresent"),
            table("Fan State (alt subtree)", "1.3.6.1.4.1.4413.1.19.43.1.6.1.3",
                  unit="1=operational 2=failed 0=notpresent"),
        ],
    },
]
