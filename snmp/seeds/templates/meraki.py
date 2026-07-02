"""
Cisco Meraki — MERAKI-CLOUD-CONTROLLER-MIB (.29671).

IMPORTANT LIMITATION: this MIB is served only by the Dashboard cloud SNMP
endpoint (snmp.meraki.com, org-wide) and is an inventory + reachability +
interface-counter list ONLY. There is no CPU, memory, temperature, fan or
PSU anywhere in it — do not expect health metrics. Point the device at the
cloud poller host. devTable is indexed by device MAC (composite numeric
suffix) and named via devName; devInterfaceTable by MAC+ifIndex, named via
devInterfaceName. Interface byte counters are Counter32 (wrap-guarded rate).

Sources: mibbrowser.online MERAKI-CLOUD-CONTROLLER-MIB, oidref devStatus,
kmalinich raw MIB copy.
"""

from snmp.seeds.templates._util import table

_DEV_NAME = "1.3.6.1.4.1.29671.1.1.4.1.2"     # devName
_IF_NAME  = "1.3.6.1.4.1.29671.1.1.5.1.3"     # devInterfaceName

TEMPLATES = [{
    "builtin_key": "builtin:meraki-cloud",
    "name": "Cisco Meraki (Cloud SNMP)",
    "vendor": "Cisco Meraki",
    "description": ("Dashboard cloud SNMP (snmp.meraki.com): per-device "
                    "online/offline status + client counts across the whole "
                    "organization, per-interface traffic. The cloud MIB has "
                    "no CPU/memory/temperature — this is inventory + "
                    "reachability monitoring."),
    "items": [
        table("Device Status", "1.3.6.1.4.1.29671.1.1.4.1.3",
              unit="1=online 0=offline",
              name_oid=_DEV_NAME),                   # devStatus
        table("Client Count", "1.3.6.1.4.1.29671.1.1.4.1.5",
              unit="count", name_oid=_DEV_NAME),     # devClientCount
        table("Interface Sent Bytes", "1.3.6.1.4.1.29671.1.1.5.1.6",
              unit="bytes", name_oid=_IF_NAME),      # devInterfaceSentBytes (C32)
        table("Interface Recv Bytes", "1.3.6.1.4.1.29671.1.1.5.1.7",
              unit="bytes", name_oid=_IF_NAME),      # devInterfaceRecvBytes (C32)
        table("Interface Sent Packets", "1.3.6.1.4.1.29671.1.1.5.1.4",
              unit="packets", name_oid=_IF_NAME),
        table("Interface Recv Packets", "1.3.6.1.4.1.29671.1.1.5.1.5",
              unit="packets", name_oid=_IF_NAME),
    ],
}]
