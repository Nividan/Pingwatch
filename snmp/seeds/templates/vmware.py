"""
VMware ESXi — VMWARE-SYSTEM/RESOURCES MIBs (.6876) + HOST-RESOURCES.

ESXi SNMP is deliberately shallow: identity + per-core CPU (host-resources)
+ coarse memory work; datastore capacity, VM performance and detailed HW
sensors are API-only (CIM is removed in ESXi 9). The vmw memory objects are
KB. There are no vmwHostMemSize/vmwHostCpuLoad objects — the correct names
are vmwMemSize/vmwMemAvail/vmwNumCPUs. For deeper coverage use PingWatch's
native vmware sensor type; this template is for SNMP-only monitoring.

Sources: VMware KB (SNMP support matrix 7.x/8.x), oidref.com vmware tree,
RFC 2790.
"""

from snmp.seeds.templates._util import scalar, table, pct_table, HR_STORAGE_DESCR

TEMPLATES = [{
    "builtin_key": "builtin:vmware-esxi",
    "name": "VMware ESXi (SNMP)",
    "vendor": "VMware",
    "description": ("SNMP-only ESXi health: per-core CPU (host-resources), "
                    "memory/storage %, host memory + CPU inventory. Use the "
                    "native vmware sensor type for datastores + per-VM "
                    "metrics — ESXi SNMP does not expose them."),
    "items": [
        table("CPU Core Load", "1.3.6.1.2.1.25.3.3.1.2", unit="%",
              warn=80, crit=90),                     # hrProcessorLoad
        pct_table("Memory / Storage Used %",
                  "1.3.6.1.2.1.25.2.3.1.6",          # hrStorageUsed
                  "1.3.6.1.2.1.25.2.3.1.5",          # hrStorageSize
                  mode="used_total", name_oid=HR_STORAGE_DESCR,
                  warn=85, crit=95),
        scalar("Host Memory Size", "1.3.6.1.4.1.6876.3.2.1.0",
               unit="MB", scale=1024),               # vmwMemSize (KB → MB)
        scalar("Host Memory Available", "1.3.6.1.4.1.6876.3.2.3.0",
               unit="MB", scale=1024),               # vmwMemAvail (KB → MB)
        scalar("CPU Count", "1.3.6.1.4.1.6876.3.1.1.0",
               unit="count"),                        # vmwNumCPUs
        scalar("System Uptime", "1.3.6.1.2.1.25.1.1.0", unit="1/100 sec"),
        scalar("System Description", "1.3.6.1.2.1.1.1.0", unit="string"),
    ],
}]
