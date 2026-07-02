"""
Juniper JunOS — JUNIPER-MIB jnxOperatingTable / jnxFruTable / jnxBox.

jnxOperatingTable (.2636.3.1.13.1) columns verified: Descr=5, State=6,
Temp=7 (whole °C), CPU=8 (%), Buffer=11 (%), 1MinLoadAvg=20. The row index
is a 4-component composite (ContentsIndex.L1.L2.L3) that differs per
chassis/model — rows MUST be walked and are named via jnxOperatingDescr
("Routing Engine", "Power Supply 0", "Fan Tray 1"…). Fans and PSUs are rows
of this same table: health = jnxOperatingState (down(6) = PSU failed;
runningAtFullSpeed(5) is normal for fans). FRU view: jnxFruTable (.15.1)
Name=5, State=8 (online(6) healthy). JunOS version lives in sysDescr, not
in JUNIPER-MIB.

Sources: oidref.com jnxOperatingEntry/jnxFruEntry, Juniper TechLibrary
"Interpreting the Enterprise-Specific Chassis MIBs", mibbrowser JUNIPER-MIB;
alarm counters live-verified on an EX virtual chassis.
"""

from snmp.seeds.templates._util import scalar, table

_JNX_DESCR = "1.3.6.1.4.1.2636.3.1.13.1.5"   # jnxOperatingDescr (name column)

TEMPLATES = [{
    "builtin_key": "builtin:juniper",
    "name": "Juniper JunOS (EX/MX/SRX/QFX)",
    "vendor": "Juniper",
    "description": ("jnxOperatingTable health per component (RE, FPC, fans, "
                    "PSUs — walked composite index, named via Descr): CPU %, "
                    "buffer/memory %, temperature, state, load average; FRU "
                    "state; chassis alarms + identity."),
    "items": [
        # ── jnxOperatingTable — one row per hardware component. The MIB
        #    defines 0 = "unavailable or inapplicable" for these gauges
        #    (fans report CPU 0%, PSUs report 0 °C…) → skip_zero keeps the
        #    preview to rows that actually measure something. ──
        table("CPU", "1.3.6.1.4.1.2636.3.1.13.1.8", unit="%",
              name_oid=_JNX_DESCR, warn=75, crit=90,
              skip_zero=True),                               # jnxOperatingCPU
        table("Buffer Utilization", "1.3.6.1.4.1.2636.3.1.13.1.11", unit="%",
              name_oid=_JNX_DESCR, warn=80, crit=90,
              skip_zero=True),                               # jnxOperatingBuffer
        table("Temperature", "1.3.6.1.4.1.2636.3.1.13.1.7", unit="°C",
              name_oid=_JNX_DESCR, warn=60, crit=75,
              skip_zero=True),                               # jnxOperatingTemp
        table("Component State", "1.3.6.1.4.1.2636.3.1.13.1.6",
              unit=("2=running 3=ready 5=runningAtFullSpeed 7=standby "
                    "1=unknown 4=reset 6=down"),
              name_oid=_JNX_DESCR),                          # jnxOperatingState
        table("Load Average (1-min)", "1.3.6.1.4.1.2636.3.1.13.1.20",
              name_oid=_JNX_DESCR, skip_zero=True),          # jnxOperating1MinLoadAvg
        # ── FRU state (jnxFruTable) ──
        table("FRU State", "1.3.6.1.4.1.2636.3.1.15.1.8",
              unit=("6=online 4=ready 10=standby 3=present 9=diagnostic "
                    "2=empty 5=announceOnline 7=announceOffline 8=offline "
                    "1=unknown"),
              name_oid="1.3.6.1.4.1.2636.3.1.15.1.5"),       # jnxFruName
        # ── Chassis alarms (live-verified) ──
        scalar("Yellow Alarm Count", "1.3.6.1.4.1.2636.3.4.2.2.1.0",
               unit="count", warn=1),
        scalar("Red Alarm Count", "1.3.6.1.4.1.2636.3.4.2.3.1.0",
               unit="count", crit=1),
        # ── Identity ──
        scalar("Chassis Description", "1.3.6.1.4.1.2636.3.1.2.0",
               unit="string"),                               # jnxBoxDescr
        scalar("Serial Number", "1.3.6.1.4.1.2636.3.1.3.0",
               unit="string"),                               # jnxBoxSerialNo
    ],
}]
