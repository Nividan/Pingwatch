"""
Fortinet FortiGate — FORTINET-FORTIGATE-MIB (.12356.101) + FORTINET-CORE.

fgSystemInfo columns verified live + against the MIB: CPU=.4.1.3, Mem=.4.1.4,
MemCapacity=.4.1.5 (KB), Disk=.4.1.6/.7 (MB), SesCount=.4.1.8,
LowMem=.4.1.9, SesRate1=.4.1.11, Ses6Count=.4.1.15. Serial is
FORTINET-CORE fnSysSerial (.12356.100.1.1.1.0 — .100, not .101). Hardware
sensors (fgHwSensorTable) report a STRING value, so health is the alarm
boolean (0=ok first → healthy). IPsec per-tunnel status walks fgVpnTunTable
(down=1/up=2 → legend leads with 2). HA member stats walk fgHaStatsTable
named by hostname.

Sources: oidref.com fgSystemInfo/fgVpnTun/fgVpnSslStats/fgHaStats/fgHwSensor,
DataDog fortinet-fortigate profile, live FortiGate v7.6 verification.
"""

from snmp.seeds.templates._util import scalar, percent, table

TEMPLATES = [{
    "builtin_key": "builtin:fortinet",
    "name": "Fortinet FortiGate",
    "vendor": "Fortinet",
    "description": ("CPU/memory/low-memory %, disk %, sessions + setup rate, "
                    "hardware sensor alarms, IPsec tunnel status, SSL-VPN "
                    "users, HA member health, firmware + serial."),
    "items": [
        # ── System resources (native % gauges) ──
        scalar("CPU Usage", "1.3.6.1.4.1.12356.101.4.1.3.0", unit="%",
               warn=80, crit=90),                    # fgSysCpuUsage
        scalar("Memory Usage", "1.3.6.1.4.1.12356.101.4.1.4.0", unit="%",
               warn=80, crit=90),                    # fgSysMemUsage
        scalar("Low Memory Usage", "1.3.6.1.4.1.12356.101.4.1.9.0", unit="%",
               warn=80, crit=90),                    # fgSysLowMemUsage (conserve-mode)
        percent("Disk Used %",
                "1.3.6.1.4.1.12356.101.4.1.6.0",     # fgSysDiskUsage (MB)
                "1.3.6.1.4.1.12356.101.4.1.7.0",     # fgSysDiskCapacity (MB)
                mode="used_total", warn=85, crit=95),
        # ── Sessions ──
        scalar("Active Sessions", "1.3.6.1.4.1.12356.101.4.1.8.0",
               unit="count"),                        # fgSysSesCount
        scalar("Session Setup Rate (1-min)", "1.3.6.1.4.1.12356.101.4.1.11.0",
               unit="sess/sec"),                     # fgSysSesRate1
        scalar("IPv6 Sessions", "1.3.6.1.4.1.12356.101.4.1.15.0",
               unit="count"),                        # fgSysSes6Count
        # ── Hardware sensors (value is a string → alarm bool is the signal) ──
        table("HW Sensor Alarm", "1.3.6.1.4.1.12356.101.4.3.2.1.4",
              unit="0=ok 1=alarm",
              name_oid="1.3.6.1.4.1.12356.101.4.3.2.1.2"),   # fgHwSensorEntName
        # ── VPN ──
        scalar("IPsec Tunnels Up", "1.3.6.1.4.1.12356.101.12.1.1.0",
               unit="count"),                        # fgVpnTunnelUpCount
        table("IPsec Tunnel Status", "1.3.6.1.4.1.12356.101.12.2.2.1.20",
              unit="2=up 1=down",
              name_oid="1.3.6.1.4.1.12356.101.12.2.2.1.2",   # fgVpnTunEntPhase1Name
              name_oid2="1.3.6.1.4.1.12356.101.12.2.2.1.3"), # fgVpnTunEntPhase2Name
        table("SSL-VPN Logged-in Users", "1.3.6.1.4.1.12356.101.12.2.3.1.2",
              unit="count"),                         # fgVpnSslStatsLoginUsers (per VDOM)
        table("SSL-VPN Active Tunnels", "1.3.6.1.4.1.12356.101.12.2.3.1.6",
              unit="count"),                         # fgVpnSslStatsActiveTunnels
        # ── HA cluster members (rows appear only when clustered) ──
        table("HA Member CPU", "1.3.6.1.4.1.12356.101.13.2.1.1.3", unit="%",
              name_oid="1.3.6.1.4.1.12356.101.13.2.1.1.11",  # fgHaStatsHostname
              warn=80, crit=90),
        table("HA Member Memory", "1.3.6.1.4.1.12356.101.13.2.1.1.4", unit="%",
              name_oid="1.3.6.1.4.1.12356.101.13.2.1.1.11",
              warn=80, crit=90),
        table("HA Sync Status", "1.3.6.1.4.1.12356.101.13.2.1.1.12",
              unit="1=synchronized 0=notSynchronized",
              name_oid="1.3.6.1.4.1.12356.101.13.2.1.1.11"),
        # ── Identity ──
        scalar("Firmware Version", "1.3.6.1.4.1.12356.101.4.1.1.0",
               unit="string"),                       # fgSysVersion
        scalar("Serial Number", "1.3.6.1.4.1.12356.100.1.1.1.0",
               unit="string"),                       # fnSysSerial (FORTINET-CORE)
    ],
}]
