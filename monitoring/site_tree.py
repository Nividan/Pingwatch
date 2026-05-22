"""
monitoring/site_tree.py — Tier inference + cluster grouping per site.

The Live Map drill-in view (M1b) shows a fixed tier hierarchy:
    FIREWALL  →  SWITCHES  →  HYPERVISORS  →  VM CLUSTERS
                                            ↘ IPMI (OOB)

PingWatch doesn't store tier information today, so this module infers
the tier of each device using its sensor types + name. Within a tier,
devices group into clusters by `devices.grp` so a card represents a
real PingWatch group.
"""
from __future__ import annotations

import re
from collections import defaultdict

from core.app_state import STATE
from core.logger    import log
from monitoring.site_rollup import _site_of, _device_status, _active_alerts_by_did


TIER_FIREWALL   = "firewall"
TIER_SWITCH     = "switch"
TIER_HYPERVISOR = "hypervisor"
TIER_VM         = "vm"
TIER_IPMI       = "ipmi"
TIER_OTHER      = "other"

# Tier keys an Edit Group dropdown may persist. "auto" / "" both mean
# "fall back to the regex inference below" — the override is opt-in.
_VALID_TIERS = {
    TIER_FIREWALL, TIER_SWITCH, TIER_HYPERVISOR, TIER_VM, TIER_IPMI, TIER_OTHER,
}


def _load_group_tier_overrides() -> dict:
    """Load `topo_settings.pw_group_tiers` — a `{groupName: tierKey}` map.
    Returns {} if the setting doesn't exist or anything goes wrong; the
    regex fallback in infer_tier() handles every group either way."""
    try:
        from monitoring.network_map import topo_get_setting
        row = topo_get_setting("pw_group_tiers")
        if not row:
            return {}
        val = row.get("value") if isinstance(row, dict) else None
        if not isinstance(val, dict):
            return {}
        # Normalize: drop entries pointing at unknown tiers so a stale
        # entry never silently classifies devices into a non-existent tier.
        return {k: v for k, v in val.items() if v in _VALID_TIERS}
    except Exception as e:
        log.debug(f"site_tree: tier overrides unavailable: {e}")
        return {}


def _load_group_parent_overrides() -> dict:
    """Load `topo_settings.pw_group_parents` — `{groupName: [did, did, ...]}`.

    Provides a group-level default for parent linking. Per-device
    `parent_device_ids` overrides this when set (non-empty). Returns {}
    on any failure — the live map just renders without group fallbacks.
    """
    try:
        from monitoring.network_map import topo_get_setting
        row = topo_get_setting("pw_group_parents")
        if not row:
            return {}
        val = row.get("value") if isinstance(row, dict) else None
        if not isinstance(val, dict):
            return {}
        out = {}
        for k, v in val.items():
            if isinstance(v, list):
                cleaned = [p for p in v if isinstance(p, str) and p]
                if cleaned:
                    out[k] = cleaned
        return out
    except Exception as e:
        log.debug(f"site_tree: group parent overrides unavailable: {e}")
        return {}


def _resolve_parents(device, group_parents: dict) -> list:
    """Return the effective parent device IDs for `device`.

    Resolution order:
      1. `device.parent_device_ids` if non-empty (manual override).
      2. `pw_group_parents[device.group]` if defined.
      3. Empty list (orphan/root).
    """
    own = list(getattr(device, "parent_device_ids", []) or [])
    if own:
        return own
    g = (getattr(device, "group", "") or "").strip()
    if g and g in group_parents:
        return list(group_parents[g])
    return []

# Order matters — first match wins. IPMI / firewall / switch use narrower rules
# (they have distinctive vendor markers); VM and hypervisor get broader.
_TIER_RULES = [
    (TIER_IPMI,       re.compile(r"\b(ipmi|idrac|ilo|drac|oob|bmc|cimc)\b", re.I)),
    (TIER_FIREWALL,   re.compile(r"\b(fortigate|fortinet|palo[\s\-]?alto|sonicwall|"
                                 r"checkpoint|firewall|fw\d|asa\d|edgewall|pfsense|"
                                 r"opnsense|untangle|fw-)\b", re.I)),
    (TIER_SWITCH,     re.compile(r"\b(switch|sw\d|sw-|tor-|ex[-\s]?\d+|n[57]k|"
                                 r"catalyst|nexus|junos|mikrotik|aruba|cisco-sw|"
                                 r"l3|l2|router|rtr-)\b", re.I)),
    (TIER_VM,         re.compile(r"\b(vm-|-vm\b|vms?\b|cluster-vm|"
                                 r"guest|tenant)\b", re.I)),
    (TIER_HYPERVISOR, re.compile(r"\b(esxi?|hyperv|kvm|proxmox|vmware|xenserver|"
                                 r"blade|bladecenter|esx-|hypervisor|host\d)\b", re.I)),
]


def infer_tier(device, group_overrides: dict | None = None) -> str:
    """Best-effort tier classification for a device.

    Priority:
      1. Per-group override from `topo_settings.pw_group_tiers` (set via the
         Edit Group modal). Lets admins force a misclassified group into the
         right tier without changing device names.
      2. Regex inference over name + host + group string.
      3. Fallback to TIER_HYPERVISOR so generic servers still surface as
         cluster cards in the drill-in instead of disappearing into OTHER.
    """
    g = (device.group or "").strip()
    if group_overrides and g:
        ov = group_overrides.get(g)
        if ov in _VALID_TIERS:
            return ov
    name = (device.name or "")
    host = (device.host or "")
    blob = f"{name} {host} {g}"
    for tier, rx in _TIER_RULES:
        if rx.search(blob):
            return tier
    return TIER_HYPERVISOR


def _device_card(d, alerts_by_did: dict, group_parents: dict | None = None) -> dict:
    """Build a minimal device card payload."""
    parents = _resolve_parents(d, group_parents or {})
    return {
        "did":               d.device_id,
        "name":              d.name,
        "host":              d.host,
        "group":             d.group or "",
        "status":            _device_status(d),
        "alerts":            int(alerts_by_did.get(d.device_id, 0)),
        "parent_device_ids": parents,
    }


def _cluster_card(name: str, devs: list, alerts_by_did: dict,
                  group_parents: dict | None = None) -> dict:
    """Roll a list of devices into a cluster card with mini dot-grid data.

    Cluster parents = union of resolved parents across every member. If
    members disagree (mixed parents), all listed parents are included and
    the frontend draws a line per parent — visually the cluster fans out.
    """
    up = warn = down = 0
    cells = []
    alerts = 0
    parents = []
    parents_seen = set()
    mixed_parents = False
    member_parent_sigs = set()
    for d in devs:
        st = _device_status(d)
        if   st == "up":   up   += 1
        elif st == "warn": warn += 1
        elif st == "down": down += 1
        d_parents = _resolve_parents(d, group_parents or {})
        cells.append({
            "did":               d.device_id,
            "name":              d.name,
            "status":            st,
            "parent_device_ids": d_parents,
        })
        alerts += int(alerts_by_did.get(d.device_id, 0))
        for p in d_parents:
            if p not in parents_seen:
                parents_seen.add(p)
                parents.append(p)
        member_parent_sigs.add(tuple(d_parents))
    if len(member_parent_sigs) > 1:
        mixed_parents = True
    # Worst-status colour for the border
    if   down: status = "down"
    elif warn: status = "warn"
    elif up:   status = "up"
    else:      status = "unknown"
    return {
        "name":              name,
        "count":             len(devs),
        "status":            status,
        "up":                up,
        "warn":              warn,
        "down":              down,
        "alerts":            alerts,
        "cells":             cells,
        "parent_device_ids": parents,
        "mixed_parents":     mixed_parents,
    }


def site_tree(site_name: str) -> dict:
    """Compute the tier tree for a single site.

    Returns:
      {
        firewalls:   [device card, ...],     # always rendered as devices
        switches:    [device card, ...],     # always rendered as devices
        hypervisors: [cluster card, ...],    # one card per group
        vm_clusters: [cluster card, ...],    # one card per group
        ipmi:        [cluster card, ...],    # one card per group (often 1)
        other:       [device card, ...],     # unmatched devices
        site:        {name, devices, up, warn, down, alerts}
      }
    """
    site_name = (site_name or "").strip()
    alerts_by_did = _active_alerts_by_did()
    # Load once per drill-in. Admin Edit-Group overrides win over the regex.
    group_overrides = _load_group_tier_overrides()
    group_parents   = _load_group_parent_overrides()

    # Bucket every device in this site by tier
    by_tier_devices = {
        TIER_FIREWALL:   [],
        TIER_SWITCH:     [],
        TIER_HYPERVISOR: [],
        TIER_VM:         [],
        TIER_IPMI:       [],
        TIER_OTHER:      [],
    }
    for d in STATE.devices.values():
        if _site_of(d) != site_name:
            continue
        tier = infer_tier(d, group_overrides)
        by_tier_devices[tier].append(d)

    # Firewalls + switches render as individual device cards
    firewalls = [_device_card(d, alerts_by_did, group_parents)
                 for d in by_tier_devices[TIER_FIREWALL]]
    switches  = [_device_card(d, alerts_by_did, group_parents)
                 for d in by_tier_devices[TIER_SWITCH]]

    # Hypervisors / VMs / IPMI render as cluster cards grouped by devices.grp
    def _by_group(devs):
        grouped = defaultdict(list)
        for d in devs:
            grouped[(d.group or "Default Group")].append(d)
        return grouped

    hyp_groups   = _by_group(by_tier_devices[TIER_HYPERVISOR])
    vm_groups    = _by_group(by_tier_devices[TIER_VM])
    ipmi_groups  = _by_group(by_tier_devices[TIER_IPMI])

    hypervisors = [_cluster_card(gname, devs, alerts_by_did, group_parents)
                   for gname, devs in sorted(hyp_groups.items(),  key=lambda kv: kv[0].lower())]
    vm_clusters = [_cluster_card(gname, devs, alerts_by_did, group_parents)
                   for gname, devs in sorted(vm_groups.items(),   key=lambda kv: kv[0].lower())]
    ipmi        = [_cluster_card(gname, devs, alerts_by_did, group_parents)
                   for gname, devs in sorted(ipmi_groups.items(), key=lambda kv: kv[0].lower())]

    other = [_device_card(d, alerts_by_did, group_parents)
             for d in by_tier_devices[TIER_OTHER]]

    # Site summary
    all_devs = [d for devs in by_tier_devices.values() for d in devs]
    up = warn = down = alerts = 0
    for d in all_devs:
        st = _device_status(d)
        if   st == "up":   up   += 1
        elif st == "warn": warn += 1
        elif st == "down": down += 1
        alerts += int(alerts_by_did.get(d.device_id, 0))

    # Build by_parent map. Keys: parent device id (string). Values: list of
    # child references — each ref is { kind: 'device'|'cluster', tier, ...id }.
    # Frontend uses this to draw SVG connection lines + flag cross-site /
    # missing parents.
    by_parent: dict = defaultdict(list)
    orphans: list = []
    site_dids = {d.device_id for d in all_devs}

    def _push_ref(parents, ref):
        if not parents:
            orphans.append(ref)
            return
        for pid in parents:
            by_parent[pid].append(ref)

    for fc in firewalls:
        _push_ref(fc["parent_device_ids"],
                  {"kind": "device", "tier": TIER_FIREWALL, "did": fc["did"]})
    for sc in switches:
        _push_ref(sc["parent_device_ids"],
                  {"kind": "device", "tier": TIER_SWITCH, "did": sc["did"]})
    for hc in hypervisors:
        _push_ref(hc["parent_device_ids"],
                  {"kind": "cluster", "tier": TIER_HYPERVISOR, "name": hc["name"]})
    for vc in vm_clusters:
        _push_ref(vc["parent_device_ids"],
                  {"kind": "cluster", "tier": TIER_VM, "name": vc["name"]})
    for ic in ipmi:
        _push_ref(ic["parent_device_ids"],
                  {"kind": "cluster", "tier": TIER_IPMI, "name": ic["name"]})
    for oc in other:
        _push_ref(oc["parent_device_ids"],
                  {"kind": "device", "tier": TIER_OTHER, "did": oc["did"]})

    # Cross-site parents — keep them in by_parent so the frontend can decide
    # how to render. Mark with a flag so cards can show a tiny badge.
    cross_site_parents = {pid for pid in by_parent
                          if pid not in site_dids and pid in STATE.devices}
    missing_parents = {pid for pid in by_parent
                       if pid not in STATE.devices}

    return {
        "site": {
            "name":    site_name,
            "devices": len(all_devs),
            "up":      up,
            "warn":    warn,
            "down":    down,
            "alerts":  alerts,
        },
        "firewalls":   firewalls,
        "switches":    switches,
        "hypervisors": hypervisors,
        "vm_clusters": vm_clusters,
        "ipmi":        ipmi,
        "other":       other,
        "by_parent":          dict(by_parent),
        "orphans":            orphans,
        "cross_site_parents": sorted(cross_site_parents),
        "missing_parents":    sorted(missing_parents),
    }
