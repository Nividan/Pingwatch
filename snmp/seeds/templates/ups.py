"""
UPS — RFC 1628 UPS-MIB (.33.1) + APC PowerNet (.318).

RFC 1628: battery status enum (normal=2 first — leaving it fires a state
event, which is the operative low-battery alert since runtime/charge are
low-direction metrics our warn/crit can't invert). Output load % is a table
(one row per output line). upsBatteryVoltage is 0.1 V DC (÷10); input/output
line voltages are whole RMS volts; battery temperature is whole °C.

APC PowerNet: upsAdvBatteryRunTimeRemaining is TimeTicks → scale 6000
converts to minutes. Battery-packs count is .2.2.5 (.2.2.4 is the
replace-battery indicator). Values are whole units (no tenths).

Sources: RFC 1628, oidref.com upsBattery/upsOutput, APC PowerNet MIB
(upsBasic/upsAdv groups), Observium definitions.
"""

from snmp.seeds.templates._util import scalar, table

TEMPLATES = [
    {
        "builtin_key": "builtin:ups-rfc1628",
        "name": "UPS (RFC 1628 standard)",
        "vendor": "UPS",
        "description": ("Standard UPS-MIB: battery status/charge/runtime, "
                        "seconds on battery, output source, per-line output "
                        "load % and voltages, battery voltage + temperature."),
        "items": [
            scalar("Battery Status", "1.3.6.1.2.1.33.1.2.1.0",
                   unit="2=batteryNormal 3=batteryLow 4=batteryDepleted 1=unknown"),
            scalar("Charge Remaining", "1.3.6.1.2.1.33.1.2.4.0", unit="%"),
            scalar("Runtime Remaining", "1.3.6.1.2.1.33.1.2.3.0",
                   unit="minutes"),                  # upsEstimatedMinutesRemaining
            scalar("Seconds on Battery", "1.3.6.1.2.1.33.1.2.2.0",
                   unit="seconds"),                  # upsSecondsOnBattery
            scalar("Battery Voltage", "1.3.6.1.2.1.33.1.2.5.0",
                   unit="volts", scale=10),          # 0.1 V DC
            scalar("Battery Temperature", "1.3.6.1.2.1.33.1.2.7.0",
                   unit="°C", warn=40, crit=50),
            scalar("Output Source", "1.3.6.1.2.1.33.1.4.1.0",
                   unit=("3=normal 5=onBattery 4=bypass 6=booster 7=reducer "
                         "2=none 1=other")),
            table("Output Load", "1.3.6.1.2.1.33.1.4.4.1.5", unit="%",
                  warn=80, crit=90),                 # upsOutputPercentLoad (per line)
            table("Output Voltage", "1.3.6.1.2.1.33.1.4.4.1.2",
                  unit="volts"),                     # RMS volts (whole)
            table("Input Voltage", "1.3.6.1.2.1.33.1.3.3.1.3",
                  unit="volts"),
            scalar("UPS Model", "1.3.6.1.2.1.33.1.1.2.0", unit="string"),
        ],
    },
    {
        "builtin_key": "builtin:ups-apc",
        "name": "APC UPS (PowerNet)",
        "vendor": "UPS",
        "description": ("APC PowerNet MIB: battery status/capacity, runtime "
                        "(auto-converted to minutes), output load/status, "
                        "battery temperature, replace-battery indicator."),
        "items": [
            scalar("Battery Status", "1.3.6.1.4.1.318.1.1.1.2.1.1.0",
                   unit="2=batteryNormal 3=batteryLow 1=unknown"),
            scalar("Battery Capacity", "1.3.6.1.4.1.318.1.1.1.2.2.1.0",
                   unit="%"),                        # upsAdvBatteryCapacity
            scalar("Runtime Remaining", "1.3.6.1.4.1.318.1.1.1.2.2.3.0",
                   unit="minutes", scale=6000),      # TimeTicks → minutes
            scalar("Battery Temperature", "1.3.6.1.4.1.318.1.1.1.2.2.2.0",
                   unit="°C", warn=40, crit=50),     # upsAdvBatteryTemperature
            scalar("Replace Battery", "1.3.6.1.4.1.318.1.1.1.2.2.4.0",
                   unit="1=ok 2=replaceBattery"),    # upsAdvBatteryReplaceIndicator
            scalar("Output Load", "1.3.6.1.4.1.318.1.1.1.4.2.3.0",
                   unit="%", warn=80, crit=90),      # upsAdvOutputLoad
            scalar("Output Status", "1.3.6.1.4.1.318.1.1.1.4.1.1.0",
                   unit=("2=onLine 3=onBattery 4=onSmartBoost 12=onSmartTrim "
                         "7=off 9=switchedBypass 6=softwareBypass 1=unknown")),
            scalar("Output Voltage", "1.3.6.1.4.1.318.1.1.1.4.2.1.0",
                   unit="volts"),                    # upsAdvOutputVoltage
            scalar("Model", "1.3.6.1.4.1.318.1.1.1.1.1.1.0", unit="string"),
        ],
    },
]
