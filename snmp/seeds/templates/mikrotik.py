"""
MikroTik RouterOS — MIKROTIK-MIB mtxrHealth (.14988.1.1.3) + HOST-RESOURCES.

mtxrHealth columns verified (several community lists have these wrong):
Voltage=.8 (deci-V ÷10), ActiveFan=.9, Temperature=.10 (deci-°C ÷10),
ProcessorTemperature=.11 (÷10), Power=.12 (W), Current=.13 (mA),
PowerSupplyState=.15 / BackupPowerSupplyState=.16 (1=ok 2=fail),
FanSpeed1=.17 / FanSpeed2=.18 (rpm). mtxrHlFanState does NOT exist.
CPU load is HOST-RESOURCES hrProcessorLoad (no mtxr CPU-load leaf); memory
is hrStorage (walk the descr to find "main memory" vs disk rows). Wireless
registration signal from mtxrWlRtabStrength (dBm, MAC+ifIndex composite
index — walked).

Sources: LibreNMS MIKROTIK-MIB raw file, mibbrowser.online, oidref.com
mtxrHlVoltage/.8 + mtxrHlTemperature/.10 (deci scaling via DISPLAY-HINT d-1).
"""

from snmp.seeds.templates._util import (scalar, table, pct_table,
                                        HR_STORAGE_DESCR)

TEMPLATES = [{
    "builtin_key": "builtin:mikrotik",
    "name": "MikroTik RouterOS",
    "vendor": "MikroTik",
    "description": ("CPU per core (host-resources), memory/disk % "
                    "(hrStorage), board + CPU temperature (deci-°C auto-"
                    "divided), voltage, PSU state, fan speeds, wireless "
                    "client signal, RouterOS version + serial."),
    "items": [
        # ── CPU / memory (standard MIBs — RouterOS has no vendor CPU leaf) ──
        table("CPU Core Load", "1.3.6.1.2.1.25.3.3.1.2", unit="%",
              warn=80, crit=90),                     # hrProcessorLoad
        pct_table("Memory / Disk Used %",
                  "1.3.6.1.2.1.25.2.3.1.6",          # hrStorageUsed
                  "1.3.6.1.2.1.25.2.3.1.5",          # hrStorageSize
                  mode="used_total", name_oid=HR_STORAGE_DESCR,
                  warn=80, crit=90),
        # ── Health (mtxrHealth; deci-units divided by the scale engine) ──
        scalar("Board Temperature", "1.3.6.1.4.1.14988.1.1.3.10.0",
               unit="°C", scale=10, warn=55, crit=70),   # mtxrHlTemperature
        scalar("CPU Temperature", "1.3.6.1.4.1.14988.1.1.3.11.0",
               unit="°C", scale=10, warn=70, crit=85),   # mtxrHlProcessorTemperature
        scalar("Input Voltage", "1.3.6.1.4.1.14988.1.1.3.8.0",
               unit="volts", scale=10),                  # mtxrHlVoltage (deci-V)
        scalar("Power Consumption", "1.3.6.1.4.1.14988.1.1.3.12.0",
               unit="W"),                                # mtxrHlPower
        scalar("Current Draw", "1.3.6.1.4.1.14988.1.1.3.13.0",
               unit="mA"),                               # mtxrHlCurrent
        scalar("Power Supply State", "1.3.6.1.4.1.14988.1.1.3.15.0",
               unit="1=ok 2=fail"),                      # mtxrHlPowerSupplyState
        scalar("Backup PSU State", "1.3.6.1.4.1.14988.1.1.3.16.0",
               unit="1=ok 2=fail"),                      # mtxrHlBackupPowerSupplyState
        scalar("Fan 1 Speed", "1.3.6.1.4.1.14988.1.1.3.17.0", unit="rpm"),
        scalar("Fan 2 Speed", "1.3.6.1.4.1.14988.1.1.3.18.0", unit="rpm"),
        # ── Wireless (legacy wireless package; index = client MAC+ifIndex) ──
        table("Wireless Client Signal", "1.3.6.1.4.1.14988.1.1.1.2.1.3",
              unit="dbm"),                               # mtxrWlRtabStrength
        # ── Identity ──
        scalar("RouterOS Version", "1.3.6.1.4.1.14988.1.1.4.4.0",
               unit="string"),                           # mtxrLicVersion
        scalar("Serial Number", "1.3.6.1.4.1.14988.1.1.7.3.0",
               unit="string"),                           # mtxrSerialNumber
    ],
}]
