"""
vmware/ — VMware vCenter/ESXi VM monitoring (optional: requires pyvmomi).
"""
from vmware.client import vmware_discover_vms, vmware_probe, VM_METRICS  # noqa: F401
