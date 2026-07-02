"""
HPE iLO / ProLiant — CPQHLTH-MIB + CPQSTDEQ-MIB (.232).

Condition enums: ok=2 (listed first → healthy), degraded=3, failed=4,
other=1. Temperature objects are cpqHeTemperature* with columns
Locale=.3 / Celsius=.4 / Threshold=.5 / Condition=.6 (whole °C — several
community lists cite wrong cpqHeThermalTemp* names/columns). Fan condition
is column .9 of cpqHeFltTolFanTable (.6 is a state enum, NOT an RPM).
Temperature/fan rows are located by an enum locale, not a string — rows
are named by index; the preview shows live values for identification.

Sources: HPE cpqhlth.mib (SPP / LibreNMS copy), oidref .232.6 tree.
"""

from snmp.seeds.templates._util import scalar, table

_HPE_COND = "2=ok 3=degraded 4=failed 1=other"

TEMPLATES = [{
    "builtin_key": "builtin:hpe-ilo",
    "name": "HPE iLO / ProLiant",
    "vendor": "HPE",
    "description": ("Overall / thermal / power health conditions, "
                    "temperature sensors (whole °C), fan + PSU condition, "
                    "PSU wattage, CPU status. Sensor rows are named by "
                    "index (the MIB locates them by an enum locale)."),
    "items": [
        scalar("Overall Health", "1.3.6.1.4.1.232.6.1.3.0",
               unit=_HPE_COND),                      # cpqHeMibCondition
        scalar("Thermal Condition", "1.3.6.1.4.1.232.6.2.6.1.0",
               unit=_HPE_COND),                      # cpqHeThermalCondition
        scalar("Power Supply Condition (overall)",
               "1.3.6.1.4.1.232.6.2.9.1.0",
               unit=_HPE_COND),                      # cpqHeFltTolPwrSupplyCondition
        table("Temperature", "1.3.6.1.4.1.232.6.2.6.8.1.4",
              unit="°C", warn=60, crit=75),          # cpqHeTemperatureCelsius
        table("Temperature Condition", "1.3.6.1.4.1.232.6.2.6.8.1.6",
              unit=_HPE_COND),                       # cpqHeTemperatureCondition
        table("Fan Condition", "1.3.6.1.4.1.232.6.2.6.7.1.9",
              unit=_HPE_COND),                       # cpqHeFltTolFanCondition
        table("PSU Condition", "1.3.6.1.4.1.232.6.2.9.3.1.4",
              unit=_HPE_COND),                       # cpqHeFltTolPowerSupplyCondition
        table("PSU Power Draw", "1.3.6.1.4.1.232.6.2.9.3.1.7",
              unit="W"),                             # cpqHeFltTolPowerSupplyCapacityUsed
        table("CPU Status", "1.3.6.1.4.1.232.1.2.2.1.1.6",
              unit=_HPE_COND,
              name_oid="1.3.6.1.4.1.232.1.2.2.1.1.3"),  # cpqSeCpuName
        scalar("Serial Number", "1.3.6.1.4.1.232.2.2.2.1.0",
               unit="string"),                       # cpqSiSysSerialNum
    ],
}]
