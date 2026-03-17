"""
snmp_catalog.py — Built-in SNMP OID catalog for well-known vendors.

Each entry: {"label": str, "oid": str, "unit": str}
unit is shown next to the label so the user knows what the value means.
"""

SNMP_CATALOG = [
    {
        "vendor": "Standard (MIB-II / RFC 1213)",
        "oids": [
            {"label": "System Description",      "oid": "1.3.6.1.2.1.1.1.0",  "unit": "string"},
            {"label": "System Uptime",            "oid": "1.3.6.1.2.1.1.3.0",  "unit": "1/100 sec"},
            {"label": "System Name (hostname)",   "oid": "1.3.6.1.2.1.1.5.0",  "unit": "string"},
            {"label": "System Contact",           "oid": "1.3.6.1.2.1.1.4.0",  "unit": "string"},
            {"label": "System Location",          "oid": "1.3.6.1.2.1.1.6.0",  "unit": "string"},
            {"label": "Interface Count",          "oid": "1.3.6.1.2.1.2.1.0",  "unit": "count"},
            {"label": "IF In Octets (port 1)",    "oid": "1.3.6.1.2.1.2.2.1.10.1", "unit": "bytes"},
            {"label": "IF Out Octets (port 1)",   "oid": "1.3.6.1.2.1.2.2.1.16.1", "unit": "bytes"},
            {"label": "IF In Errors (port 1)",    "oid": "1.3.6.1.2.1.2.2.1.14.1", "unit": "errors"},
            {"label": "IF Out Errors (port 1)",   "oid": "1.3.6.1.2.1.2.2.1.20.1", "unit": "errors"},
            {"label": "IF Operational Status (port 1)", "oid": "1.3.6.1.2.1.2.2.1.8.1", "unit": "1=up 2=down"},
            {"label": "TCP Active Opens",         "oid": "1.3.6.1.2.1.6.5.0",  "unit": "count"},
            {"label": "TCP Established Conns",    "oid": "1.3.6.1.2.1.6.9.0",  "unit": "count"},
            {"label": "IP In Receives",           "oid": "1.3.6.1.2.1.4.3.0",  "unit": "packets"},
            {"label": "IP Out Requests",          "oid": "1.3.6.1.2.1.4.10.0", "unit": "packets"},
        ],
    },
    {
        "vendor": "Host Resources (hrMIB)",
        "oids": [
            {"label": "HR System Uptime",         "oid": "1.3.6.1.2.1.25.1.1.0",     "unit": "1/100 sec"},
            {"label": "HR CPU Load (CPU 1)",      "oid": "1.3.6.1.2.1.25.3.3.1.2.1", "unit": "%"},
            {"label": "HR Physical Memory Size",  "oid": "1.3.6.1.2.1.25.2.2.0",     "unit": "KB"},
            {"label": "HR Storage Used (RAM)",    "oid": "1.3.6.1.2.1.25.2.3.1.6.1", "unit": "allocation units"},
            {"label": "HR Storage Size (RAM)",    "oid": "1.3.6.1.2.1.25.2.3.1.5.1", "unit": "allocation units"},
            {"label": "HR Storage Alloc Unit",    "oid": "1.3.6.1.2.1.25.2.3.1.4.1", "unit": "bytes/unit"},
        ],
    },
    {
        "vendor": "Cisco — IOS / IOS-XE / NX-OS",
        "oids": [
            # CPU
            {"label": "CPU 1-min Avg (new, core 1)",  "oid": "1.3.6.1.4.1.9.9.109.1.1.1.1.3.1",  "unit": "%"},
            {"label": "CPU 5-min Avg (new, core 1)",  "oid": "1.3.6.1.4.1.9.9.109.1.1.1.1.7.1",  "unit": "%"},
            {"label": "CPU 5-min Avg (IOS legacy)",   "oid": "1.3.6.1.4.1.9.2.1.56.0",            "unit": "%"},
            # Memory
            {"label": "Memory Pool Used (processor)", "oid": "1.3.6.1.4.1.9.9.48.1.1.1.6.1",      "unit": "bytes"},
            {"label": "Memory Pool Free (processor)", "oid": "1.3.6.1.4.1.9.9.48.1.1.1.7.1",      "unit": "bytes"},
            {"label": "Memory Free (IOS legacy)",     "oid": "1.3.6.1.4.1.9.2.1.8.0",             "unit": "bytes"},
            # Environment
            {"label": "Inlet Temperature (sensor 1)", "oid": "1.3.6.1.4.1.9.9.13.1.3.1.3.1",      "unit": "°C"},
            {"label": "Fan State (fan 1)",            "oid": "1.3.6.1.4.1.9.9.13.1.4.1.3.1",      "unit": "1=normal"},
            {"label": "Power Supply State (PSU 1)",   "oid": "1.3.6.1.4.1.9.9.13.1.5.1.3.1",      "unit": "1=normal"},
            # BGP / routing
            {"label": "BGP Established Peers",        "oid": "1.3.6.1.2.1.15.3.0",                "unit": "count"},
            {"label": "OSPF Neighbors",               "oid": "1.3.6.1.2.1.14.10.1.6.0.0.0.0.0",  "unit": "state"},
            # ASA / Firewall
            {"label": "ASA Active Connections",       "oid": "1.3.6.1.4.1.9.9.147.1.2.2.2.1.5.40.6", "unit": "count"},
            {"label": "ASA Connection Rate",          "oid": "1.3.6.1.4.1.9.9.147.1.2.2.2.1.5.40.1", "unit": "conn/sec"},
            # Chassis
            {"label": "Chassis Serial Number",        "oid": "1.3.6.1.4.1.9.3.6.3.0",             "unit": "string"},
            {"label": "IOS Software Version",         "oid": "1.3.6.1.4.1.9.2.1.73.0",            "unit": "string"},
            # Interfaces — standard ifTable (use ⊕ Discover for full list)
            {"label": "IF GigE0/0 — Oper Status",    "oid": "1.3.6.1.2.1.2.2.1.8.1",             "unit": "1=up 2=down"},
            {"label": "IF GigE0/0 — In Octets",      "oid": "1.3.6.1.2.1.2.2.1.10.1",            "unit": "bytes"},
            {"label": "IF GigE0/0 — Out Octets",     "oid": "1.3.6.1.2.1.2.2.1.16.1",            "unit": "bytes"},
            {"label": "IF GigE0/0 — In Errors",      "oid": "1.3.6.1.2.1.2.2.1.14.1",            "unit": "errors"},
            {"label": "IF GigE0/0 — Out Errors",     "oid": "1.3.6.1.2.1.2.2.1.20.1",            "unit": "errors"},
            {"label": "IF GigE0/1 — Oper Status",    "oid": "1.3.6.1.2.1.2.2.1.8.2",             "unit": "1=up 2=down"},
            {"label": "IF GigE0/1 — In Octets",      "oid": "1.3.6.1.2.1.2.2.1.10.2",            "unit": "bytes"},
            {"label": "IF GigE0/1 — Out Octets",     "oid": "1.3.6.1.2.1.2.2.1.16.2",            "unit": "bytes"},
            # Cisco extended interface stats (cieIfExtensionMIB)
            {"label": "IF 1 — Input Queue Drops",    "oid": "1.3.6.1.4.1.9.9.276.1.1.2.1.3.1",   "unit": "packets"},
            {"label": "IF 1 — Output Queue Drops",   "oid": "1.3.6.1.4.1.9.9.276.1.1.2.1.4.1",   "unit": "packets"},
        ],
    },
    {
        "vendor": "Fortinet — FortiGate",
        "oids": [
            # System resources
            {"label": "CPU Usage",                    "oid": "1.3.6.1.4.1.12356.101.4.1.3.0",     "unit": "%"},
            {"label": "Memory Usage",                 "oid": "1.3.6.1.4.1.12356.101.4.1.4.0",     "unit": "%"},
            {"label": "Memory Capacity",              "oid": "1.3.6.1.4.1.12356.101.4.1.5.0",     "unit": "MB"},
            {"label": "Disk Usage",                   "oid": "1.3.6.1.4.1.12356.101.4.1.6.0",     "unit": "%"},
            {"label": "System Uptime",                "oid": "1.3.6.1.4.1.12356.101.4.1.8.0",     "unit": "seconds"},
            # Sessions
            {"label": "Active Sessions",              "oid": "1.3.6.1.4.1.12356.101.4.1.9.0",     "unit": "count"},
            {"label": "Session Rate (new/sec)",       "oid": "1.3.6.1.4.1.12356.101.4.1.10.0",    "unit": "sess/sec"},
            {"label": "Failed Auth Count",            "oid": "1.3.6.1.4.1.12356.101.4.1.11.0",    "unit": "count"},
            # VPN — IPSec
            {"label": "IPSec Active Tunnels",         "oid": "1.3.6.1.4.1.12356.101.12.2.2.1.3.1","unit": "count"},
            {"label": "IPSec Tunnels Established",    "oid": "1.3.6.1.4.1.12356.101.12.2.2.1.4.1","unit": "count"},
            # VPN — SSL
            {"label": "SSL-VPN Logged-in Users",      "oid": "1.3.6.1.4.1.12356.101.3.2.1.0",     "unit": "count"},
            {"label": "SSL-VPN Active Tunnels",       "oid": "1.3.6.1.4.1.12356.101.3.2.4.0",     "unit": "count"},
            # HA
            {"label": "HA System Mode",               "oid": "1.3.6.1.4.1.12356.101.13.1.1.0",    "unit": "1=standalone 2=a-a 3=a-p"},
            {"label": "HA Cluster Member Count",      "oid": "1.3.6.1.4.1.12356.101.13.1.7.0",    "unit": "count"},
            # IPS
            {"label": "IPS Intrusions Detected",      "oid": "1.3.6.1.4.1.12356.101.9.2.1.0",     "unit": "count"},
            {"label": "IPS Intrusions Blocked",       "oid": "1.3.6.1.4.1.12356.101.9.2.2.0",     "unit": "count"},
            # Firmware
            {"label": "Firmware Version",             "oid": "1.3.6.1.4.1.12356.101.4.1.1.0",     "unit": "string"},
            {"label": "Serial Number",                "oid": "1.3.6.1.4.1.12356.101.4.1.2.0",     "unit": "string"},
            # Interfaces — FortiGate-specific fgIntf MIB (use ⊕ Discover for full list)
            {"label": "IF 1 — Name (fgIntf)",        "oid": "1.3.6.1.4.1.12356.101.7.2.1.1.1",   "unit": "string"},
            {"label": "IF 1 — IP Address",           "oid": "1.3.6.1.4.1.12356.101.7.2.1.2.1",   "unit": "string"},
            {"label": "IF 1 — Rx Packets",           "oid": "1.3.6.1.4.1.12356.101.7.2.1.5.1",   "unit": "packets"},
            {"label": "IF 1 — Tx Packets",           "oid": "1.3.6.1.4.1.12356.101.7.2.1.6.1",   "unit": "packets"},
            {"label": "IF 1 — Rx Bytes",             "oid": "1.3.6.1.4.1.12356.101.7.2.1.7.1",   "unit": "bytes"},
            {"label": "IF 1 — Tx Bytes",             "oid": "1.3.6.1.4.1.12356.101.7.2.1.8.1",   "unit": "bytes"},
            {"label": "IF 1 — Oper Status (ifTable)","oid": "1.3.6.1.2.1.2.2.1.8.1",             "unit": "1=up 2=down"},
            {"label": "IF 2 — Oper Status (ifTable)","oid": "1.3.6.1.2.1.2.2.1.8.2",             "unit": "1=up 2=down"},
            {"label": "IF 3 — Oper Status (ifTable)","oid": "1.3.6.1.2.1.2.2.1.8.3",             "unit": "1=up 2=down"},
        ],
    },
    {
        "vendor": "Juniper — JunOS (EX/MX/SRX/QFX)",
        "oids": [
            # Routing Engine
            {"label": "RE CPU Utilization",           "oid": "1.3.6.1.4.1.2636.3.1.13.1.11.9.1.0.0", "unit": "%"},
            {"label": "RE Memory Buffer Utilization", "oid": "1.3.6.1.4.1.2636.3.1.13.1.9.9.1.0.0",  "unit": "%"},
            {"label": "RE Temperature",               "oid": "1.3.6.1.4.1.2636.3.1.13.1.8.9.1.0.0",  "unit": "°C"},
            {"label": "RE Uptime",                    "oid": "1.3.6.1.4.1.2636.3.1.13.1.2.9.1.0.0",  "unit": "seconds"},
            {"label": "RE Memory DRAM Size",          "oid": "1.3.6.1.4.1.2636.3.1.13.1.7.9.1.0.0",  "unit": "MB"},
            # Chassis
            {"label": "Chassis Description",          "oid": "1.3.6.1.4.1.2636.3.1.2.0",             "unit": "string"},
            {"label": "Chassis Serial Number",        "oid": "1.3.6.1.4.1.2636.3.1.3.0",             "unit": "string"},
            # Alarms
            {"label": "Yellow Alarm Count",           "oid": "1.3.6.1.4.1.2636.3.4.2.2.1.0",         "unit": "count"},
            {"label": "Red Alarm Count",              "oid": "1.3.6.1.4.1.2636.3.4.2.3.1.0",         "unit": "count"},
            # BGP (standard)
            {"label": "BGP Established Peers",        "oid": "1.3.6.1.2.1.15.3.0",                   "unit": "count"},
            # Firewall (SRX)
            {"label": "SRX Active Sessions",          "oid": "1.3.6.1.4.1.2636.3.39.1.12.1.1.1.2.0", "unit": "count"},
            {"label": "SRX Active Sessions IPv6",     "oid": "1.3.6.1.4.1.2636.3.39.1.12.1.1.1.3.0", "unit": "count"},
            # Interfaces — Juniper uses standard ifTable; interface indices vary by model
            # Use ⊕ Discover Interfaces to find actual indices for your device
            {"label": "IF 1 — Oper Status (ifTable)", "oid": "1.3.6.1.2.1.2.2.1.8.1",              "unit": "1=up 2=down"},
            {"label": "IF 1 — In Octets (32-bit)",    "oid": "1.3.6.1.2.1.2.2.1.10.1",             "unit": "bytes"},
            {"label": "IF 1 — Out Octets (32-bit)",   "oid": "1.3.6.1.2.1.2.2.1.16.1",             "unit": "bytes"},
            {"label": "IF 1 — HC In Octets (64-bit)", "oid": "1.3.6.1.2.1.31.1.1.1.6.1",           "unit": "bytes"},
            {"label": "IF 1 — HC Out Octets (64-bit)","oid": "1.3.6.1.2.1.31.1.1.1.10.1",          "unit": "bytes"},
            {"label": "IF 1 — In Errors",             "oid": "1.3.6.1.2.1.2.2.1.14.1",             "unit": "errors"},
            {"label": "IF 2 — Oper Status",           "oid": "1.3.6.1.2.1.2.2.1.8.2",              "unit": "1=up 2=down"},
            {"label": "IF 2 — In Octets",             "oid": "1.3.6.1.2.1.2.2.1.10.2",             "unit": "bytes"},
        ],
    },
    {
        "vendor": "Palo Alto — PAN-OS",
        "oids": [
            {"label": "Software Version",             "oid": "1.3.6.1.4.1.25461.2.1.2.1.1.0",  "unit": "string"},
            {"label": "Serial Number",                "oid": "1.3.6.1.4.1.25461.2.1.2.1.3.0",  "unit": "string"},
            {"label": "Active Sessions",              "oid": "1.3.6.1.4.1.25461.2.1.2.1.7.0",  "unit": "count"},
            {"label": "Max Sessions",                 "oid": "1.3.6.1.4.1.25461.2.1.2.1.8.0",  "unit": "count"},
            {"label": "Session Utilization",          "oid": "1.3.6.1.4.1.25461.2.1.2.1.9.0",  "unit": "%"},
            {"label": "CPU Util — Management Plane",  "oid": "1.3.6.1.4.1.25461.2.1.2.1.10.0", "unit": "%"},
            {"label": "CPU Util — Data Plane",        "oid": "1.3.6.1.4.1.25461.2.1.2.1.11.0", "unit": "%"},
            {"label": "GlobalProtect Active Tunnels", "oid": "1.3.6.1.4.1.25461.2.1.2.5.1.3.0","unit": "count"},
            {"label": "HA State",                     "oid": "1.3.6.1.4.1.25461.2.1.2.1.13.0", "unit": "string"},
        ],
    },
    {
        "vendor": "HP / Aruba — ProCurve / ArubaOS-CX",
        "oids": [
            {"label": "Switch CPU Utilization",       "oid": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.5.1", "unit": "%"},
            {"label": "Total Memory",                 "oid": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.2.1", "unit": "bytes"},
            {"label": "Free Memory",                  "oid": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.1.1.1.4.1", "unit": "bytes"},
            {"label": "Switch Model (hpSwitchHwId)",  "oid": "1.3.6.1.4.1.11.2.14.11.5.1.1.5.0",         "unit": "string"},
            {"label": "Active Ports Up",              "oid": "1.3.6.1.4.1.11.2.14.11.5.1.1.2.6.1.2.1",   "unit": "count"},
            # Interfaces — HP uses standard ifTable (use ⊕ Discover for actual port mapping)
            {"label": "Port 1 — Oper Status",        "oid": "1.3.6.1.2.1.2.2.1.8.1",  "unit": "1=up 2=down"},
            {"label": "Port 1 — In Octets",          "oid": "1.3.6.1.2.1.2.2.1.10.1", "unit": "bytes"},
            {"label": "Port 1 — Out Octets",         "oid": "1.3.6.1.2.1.2.2.1.16.1", "unit": "bytes"},
            {"label": "Port 2 — Oper Status",        "oid": "1.3.6.1.2.1.2.2.1.8.2",  "unit": "1=up 2=down"},
            {"label": "Port 2 — In Octets",          "oid": "1.3.6.1.2.1.2.2.1.10.2", "unit": "bytes"},
            {"label": "Port 2 — Out Octets",         "oid": "1.3.6.1.2.1.2.2.1.16.2", "unit": "bytes"},
        ],
    },
    {
        "vendor": "MikroTik — RouterOS",
        "oids": [
            {"label": "CPU Load",                     "oid": "1.3.6.1.4.1.14988.1.1.3.14.0",  "unit": "%"},
            {"label": "CPU Frequency",                "oid": "1.3.6.1.4.1.14988.1.1.3.1.0",   "unit": "MHz"},
            {"label": "Memory Used",                  "oid": "1.3.6.1.4.1.14988.1.1.3.15.0",  "unit": "bytes"},
            {"label": "Memory Total",                 "oid": "1.3.6.1.4.1.14988.1.1.3.16.0",  "unit": "bytes"},
            {"label": "Active Users",                 "oid": "1.3.6.1.4.1.14988.1.1.5.3.0",   "unit": "count"},
            {"label": "System Temperature",           "oid": "1.3.6.1.4.1.14988.1.1.3.11.0",  "unit": "°C"},
            {"label": "Voltage",                      "oid": "1.3.6.1.4.1.14988.1.1.3.8.0",   "unit": "mV"},
            {"label": "Firmware Version",             "oid": "1.3.6.1.4.1.14988.1.1.4.4.0",   "unit": "string"},
            {"label": "Board Name",                   "oid": "1.3.6.1.4.1.14988.1.1.7.4.0",   "unit": "string"},
            # Interfaces — MikroTik uses standard ifTable (use ⊕ Discover for actual names)
            {"label": "IF 1 — Oper Status",          "oid": "1.3.6.1.2.1.2.2.1.8.1",  "unit": "1=up 2=down"},
            {"label": "IF 1 — In Octets",            "oid": "1.3.6.1.2.1.2.2.1.10.1", "unit": "bytes"},
            {"label": "IF 1 — Out Octets",           "oid": "1.3.6.1.2.1.2.2.1.16.1", "unit": "bytes"},
            {"label": "IF 2 — Oper Status",          "oid": "1.3.6.1.2.1.2.2.1.8.2",  "unit": "1=up 2=down"},
            {"label": "IF 2 — In Octets",            "oid": "1.3.6.1.2.1.2.2.1.10.2", "unit": "bytes"},
            {"label": "IF 2 — Out Octets",           "oid": "1.3.6.1.2.1.2.2.1.16.2", "unit": "bytes"},
        ],
    },
    {
        "vendor": "Linux / Net-SNMP",
        "oids": [
            {"label": "CPU User Time",                "oid": "1.3.6.1.4.1.2021.11.9.0",   "unit": "%"},
            {"label": "CPU System Time",              "oid": "1.3.6.1.4.1.2021.11.10.0",  "unit": "%"},
            {"label": "CPU Idle Time",                "oid": "1.3.6.1.4.1.2021.11.11.0",  "unit": "%"},
            {"label": "Total RAM (physical)",         "oid": "1.3.6.1.4.1.2021.4.5.0",    "unit": "KB"},
            {"label": "RAM Available",                "oid": "1.3.6.1.4.1.2021.4.11.0",   "unit": "KB"},
            {"label": "RAM Used",                     "oid": "1.3.6.1.4.1.2021.4.6.0",    "unit": "KB"},
            {"label": "Total Swap",                   "oid": "1.3.6.1.4.1.2021.4.3.0",    "unit": "KB"},
            {"label": "Swap Available",               "oid": "1.3.6.1.4.1.2021.4.4.0",    "unit": "KB"},
            {"label": "1-min Load Average ×100",      "oid": "1.3.6.1.4.1.2021.10.1.5.1", "unit": "×100"},
            {"label": "5-min Load Average ×100",      "oid": "1.3.6.1.4.1.2021.10.1.5.2", "unit": "×100"},
            {"label": "Disk Space Used (disk 1)",     "oid": "1.3.6.1.4.1.2021.9.1.9.1",  "unit": "%"},
            {"label": "Disk Error Flag (disk 1)",     "oid": "1.3.6.1.4.1.2021.9.1.100.1","unit": "0=ok"},
        ],
    },
    {
        "vendor": "Windows — SNMP Service",
        "oids": [
            {"label": "Total Physical Memory",        "oid": "1.3.6.1.2.1.25.2.2.0",             "unit": "KB"},
            {"label": "CPU Load (processor 1)",       "oid": "1.3.6.1.2.1.25.3.3.1.2.1",         "unit": "%"},
            {"label": "Logged-in Users",              "oid": "1.3.6.1.2.1.25.1.5.0",             "unit": "count"},
            {"label": "Running Processes",            "oid": "1.3.6.1.2.1.25.1.6.0",             "unit": "count"},
            {"label": "System Uptime (hrMIB)",        "oid": "1.3.6.1.2.1.25.1.1.0",             "unit": "1/100 sec"},
            {"label": "TCP Connections Established",  "oid": "1.3.6.1.2.1.6.9.0",               "unit": "count"},
        ],
    },
    {
        "vendor": "UPS (APC / Eaton — RFC 1628)",
        "oids": [
            {"label": "Battery Status",               "oid": "1.3.6.1.2.1.33.1.2.1.0",   "unit": "1=unknown 2=normal 3=low 4=depleted"},
            {"label": "Battery Charge",               "oid": "1.3.6.1.2.1.33.1.2.4.0",   "unit": "%"},
            {"label": "Battery Runtime Remaining",    "oid": "1.3.6.1.2.1.33.1.2.3.0",   "unit": "minutes"},
            {"label": "Input Voltage",                "oid": "1.3.6.1.2.1.33.1.3.3.1.3.1","unit": "VAC×10"},
            {"label": "Output Voltage",               "oid": "1.3.6.1.2.1.33.1.4.4.1.2.1","unit": "VAC×10"},
            {"label": "Output Load",                  "oid": "1.3.6.1.2.1.33.1.4.4.1.5.1","unit": "%"},
            {"label": "UPS Power Status",             "oid": "1.3.6.1.2.1.33.1.4.1.0",   "unit": "1=other 2=none 3=normal 4=bypass 5=battery"},
            # APC-specific (OID 1.3.6.1.4.1.318)
            {"label": "APC Battery Temperature",      "oid": "1.3.6.1.4.1.318.1.1.1.2.2.2.0", "unit": "°C"},
            {"label": "APC Output Current",           "oid": "1.3.6.1.4.1.318.1.1.1.4.2.4.0", "unit": "A×10"},
        ],
    },
    {
        "vendor": "VMware — vSphere (SNMP)",
        "oids": [
            {"label": "Hypervisor Version",           "oid": "1.3.6.1.4.1.6876.1.1.0",  "unit": "string"},
            {"label": "Product Name",                 "oid": "1.3.6.1.4.1.6876.1.2.0",  "unit": "string"},
            {"label": "CPU Speed (MHz)",               "oid": "1.3.6.1.4.1.6876.3.1.1.3.1", "unit": "MHz"},
            {"label": "Total Memory",                 "oid": "1.3.6.1.4.1.6876.3.2.1.5.1", "unit": "MB"},
            {"label": "VM Running Count",             "oid": "1.3.6.1.4.1.6876.2.1.0",  "unit": "count"},
        ],
    },
]
