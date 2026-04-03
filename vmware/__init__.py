"""
vmware/ — VMware vCenter/ESXi VM and host monitoring (optional: requires pyvmomi).
"""
from vmware.client import (vmware_discover_vms, vmware_discover_hosts,  # noqa: F401
                           vmware_probe, VM_METRICS, HOST_METRICS)
