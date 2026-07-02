"""
Generic Host — HOST-RESOURCES-MIB (RFC 2790) + UCD-SNMP-MIB.

Covers Linux/Net-SNMP, Windows (the native MS SNMP service implements
Host-Resources), ESXi, RouterOS, PAN-OS and any other host-resources agent.
Storage Used % walks hrStorageTable per row (RAM + every filesystem), so
allocation units cancel out of the percentage.

Sources: RFC 2790 (HOST-RESOURCES-MIB), net-snmp UCD-SNMP-MIB
(1.3.6.1.4.1.2021), oidref.com — dskTable 2021.9.1 (dskPath=.2,
dskPercent=.9), laTable 2021.10.1 (laNames=.2, laLoadInt=.5, load×100),
memory 2021.4 (KB).
"""

from snmp.seeds.templates._util import (scalar, percent, table, pct_table,
                                        HR_STORAGE_DESCR)

TEMPLATES = [{
    "builtin_key": "builtin:host",
    "name": "Generic Host (CPU / Memory / Storage)",
    "vendor": "Standard",
    "description": ("HOST-RESOURCES + UCD-SNMP host health: per-core CPU, "
                    "memory %, per-filesystem storage %, load average, "
                    "swap, uptime. Works on Linux, Windows, ESXi and any "
                    "net-snmp agent."),
    "items": [
        # ── Host-Resources (universal) ──
        table("CPU Core Load", "1.3.6.1.2.1.25.3.3.1.2", unit="%",
              warn=80, crit=90),                     # hrProcessorLoad, per core
        pct_table("Storage Used %",
                  "1.3.6.1.2.1.25.2.3.1.6",          # hrStorageUsed
                  "1.3.6.1.2.1.25.2.3.1.5",          # hrStorageSize
                  mode="used_total", name_oid=HR_STORAGE_DESCR,
                  warn=80, crit=90),                 # RAM + each filesystem
        scalar("System Uptime", "1.3.6.1.2.1.25.1.1.0", unit="1/100 sec"),
        # ── UCD (Linux/Net-SNMP richer detail) ──
        percent("Memory Used % (UCD)",
                "1.3.6.1.4.1.2021.4.6.0",            # memAvailReal (free)
                "1.3.6.1.4.1.2021.4.5.0",            # memTotalReal
                mode="free_total", warn=85, crit=95),
        percent("Swap Used % (UCD)",
                "1.3.6.1.4.1.2021.4.4.0",            # memAvailSwap
                "1.3.6.1.4.1.2021.4.3.0",            # memTotalSwap
                mode="free_total", warn=50, crit=80),
        table("Disk Used % (UCD)", "1.3.6.1.4.1.2021.9.1.9", unit="%",
              name_oid="1.3.6.1.4.1.2021.9.1.2",     # dskPath
              warn=80, crit=90),                     # dskPercent
        table("Load Average", "1.3.6.1.4.1.2021.10.1.5",
              name_oid="1.3.6.1.4.1.2021.10.1.2",    # laNames (Load-1/5/15)
              scale=100),                            # laLoadInt = load × 100
        # ── Identity ──
        scalar("System Name", "1.3.6.1.2.1.1.5.0", unit="string"),
        scalar("System Description", "1.3.6.1.2.1.1.1.0", unit="string"),
    ],
}]
