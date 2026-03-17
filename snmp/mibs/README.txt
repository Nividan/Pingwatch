PingWatch — SNMP MIB Files
==========================

Drop vendor MIB files into the appropriate subfolder here.
PingWatch will parse them at startup and load trap definitions
into the database automatically.

Folder layout
-------------
  snmp/mibs/
    fortinet/    <- Fortinet FortiGate MIBs
    cisco/       <- Cisco IOS / NX-OS / ASA MIBs
    juniper/     <- Juniper JunOS MIBs
    apc/         <- APC/Schneider PowerNet MIBs
    <vendor>/    <- Any other vendor (directory name used as vendor hint)

Supported file extensions:  .mib  .txt  .my

Where to get MIB files
-----------------------
  Fortinet:   support.fortinet.com → download FortiGate MIB files
              (FORTINET-CORE-MIB.mib + FORTINET-FORTIGATE-MIB.mib)

  Cisco:      cisco.com/c/en/us/support/docs/ip/simple-network-management-protocol-snmp/

  Juniper:    juniper.net/documentation/software/junos/ → SNMP MIB Explorer

  APC:        apc.com → search "PowerNet MIB"

Notes
-----
- MIB files are parsed once per startup and results are cached in the database.
- Re-dropping files and restarting will re-process them (INSERT OR IGNORE keeps
  existing custom definitions intact).
- The built-in Python seed files (snmp/seeds/*.py) always load first; MIB files
  add to or extend those definitions.
- If a MIB file cannot be parsed it is skipped with a warning in the log.
