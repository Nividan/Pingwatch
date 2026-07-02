"""
Dell iDRAC — IDRAC-MIB-SMIv2 (.674.10892.5).

Every *Status object uses ObjectStatusEnum: ok=3 (listed first → healthy),
nonCritical=4, critical=5, nonRecoverable=6. Sensor tables are walked and
named via their LocationName column (.8): temperature readings are
tenths-°C (scale 10), PSU output tenths-W (scale 10 — nameplate rating,
not draw), voltages are mV (scale 1000). Live system wattage is the
amperageProbe row named "System Board Pwr Consumption" — pick it in the
preview rather than pinning an index.

Sources: Dell iDRAC-MIB-SMIv2 (Dell techcenter / LibreNMS copy), oidref
.674.10892.5 tree.
"""

from snmp.seeds.templates._util import scalar, table

_DELL_STATE = "3=ok 4=nonCritical 5=critical 6=nonRecoverable 1=other 2=unknown"

TEMPLATES = [{
    "builtin_key": "builtin:dell-idrac",
    "name": "Dell iDRAC (PowerEdge)",
    "vendor": "Dell",
    "description": ("Global system + storage status, temperature probes "
                    "(tenths auto-divided), fan RPM, PSU output, voltage "
                    "probes, live power draw — all walked and named via "
                    "LocationName."),
    "items": [
        scalar("Global System Status", "1.3.6.1.4.1.674.10892.5.2.1.0",
               unit=_DELL_STATE),                    # globalSystemStatus
        scalar("Global Storage Status", "1.3.6.1.4.1.674.10892.5.2.3.0",
               unit=_DELL_STATE),                    # globalStorageStatus
        table("Temperature", "1.3.6.1.4.1.674.10892.5.4.700.20.1.6",
              unit="°C", scale=10, warn=60, crit=75,
              name_oid="1.3.6.1.4.1.674.10892.5.4.700.20.1.8"),  # LocationName
        table("Fan Speed", "1.3.6.1.4.1.674.10892.5.4.700.12.1.6",
              unit="rpm",
              name_oid="1.3.6.1.4.1.674.10892.5.4.700.12.1.8"),
        table("PSU Output", "1.3.6.1.4.1.674.10892.5.4.600.12.1.6",
              unit="W", scale=10,
              name_oid="1.3.6.1.4.1.674.10892.5.4.600.12.1.8"),
        table("Voltage Probe", "1.3.6.1.4.1.674.10892.5.4.600.20.1.6",
              unit="volts", scale=1000,
              name_oid="1.3.6.1.4.1.674.10892.5.4.600.20.1.8"),
        table("Power / Amperage Probe", "1.3.6.1.4.1.674.10892.5.4.600.30.1.6",
              name_oid="1.3.6.1.4.1.674.10892.5.4.600.30.1.8"),
        scalar("Service Tag", "1.3.6.1.4.1.674.10892.5.1.3.2.0",
               unit="string"),                       # systemServiceTag
        scalar("Model Name", "1.3.6.1.4.1.674.10892.5.1.3.12.0",
               unit="string"),                       # systemModelName
    ],
}]
