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


TIER_ISP         = "isp"
TIER_WAN_SWITCH  = "wan_switch"
TIER_FIREWALL    = "firewall"
TIER_CORE_SWITCH = "core_switch"
TIER_SWITCH      = "switch"          # access switch (legacy key, renamed label)
TIER_CHASSIS     = "chassis"
TIER_HYPERVISOR  = "hypervisor"
TIER_VM          = "vm"
TIER_IPMI        = "ipmi"
TIER_OTHER       = "other"

# Tier keys an Edit Group dropdown may persist. "auto" / "" both mean
# "fall back to the regex inference below" — the override is opt-in.
_VALID_TIERS = {
    TIER_ISP, TIER_WAN_SWITCH, TIER_FIREWALL, TIER_CORE_SWITCH, TIER_SWITCH,
    TIER_CHASSIS, TIER_HYPERVISOR, TIER_VM, TIER_IPMI, TIER_OTHER,
}

# Per-device Topology Role (ip_allocations.kind, set in the Edit Device modal)
# → live-map tier. This is an explicit, user-set signal so it outranks both
# the per-group override and the regex inference. Backbone (aggregation) and
# core both render in the single CORE SWITCH row; gateway is the edge/firewall
# row. The remaining roles ('reserved'/'conflict'/'discovered') aren't tier
# signals and are intentionally absent — they fall through to the regex.
_ROLE_TO_TIER = {
    "core":     TIER_CORE_SWITCH,
    "backbone": TIER_CORE_SWITCH,
    "switch":   TIER_SWITCH,
    "gateway":  TIER_FIREWALL,
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


def _load_device_role_overrides() -> dict:
    """Load per-device Topology Role tags — `{device_id: roleKey}` sourced from
    ip_allocations.kind (set via the Edit Device modal). Kept only when the role
    maps to a live-map tier via _ROLE_TO_TIER; everything else is dropped so a
    non-tier kind never reaches infer_tier(). Returns {} on any failure — the
    regex fallback in infer_tier() then handles every device either way."""
    try:
        from db import db_get_device_roles
        roles = db_get_device_roles() or {}
        return {did: r for did, r in roles.items() if r in _ROLE_TO_TIER}
    except Exception as e:
        log.debug(f"site_tree: device role overrides unavailable: {e}")
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

# Order matters — first match wins. Specific vendor markers (IPMI, firewall,
# ISP, WAN, core) come BEFORE the generic switch rule so an N7K isn't mis-
# bucketed as access. Chassis precedes hypervisor so a BladeCenter slots
# into chassis, not into the generic hypervisor bucket.
_TIER_RULES = [
    (TIER_IPMI,        re.compile(r"\b(ipmi|idrac|ilo|drac|oob|bmc|cimc)\b", re.I)),
    (TIER_FIREWALL,    re.compile(r"\b(fortigate|fortinet|palo[\s\-]?alto|sonicwall|"
                                  r"checkpoint|firewall|fw\d|asa\d|edgewall|pfsense|"
                                  r"opnsense|untangle|fw-)\b", re.I)),
    # ISP demarc — physical link to the carrier. Distinct enough that any
    # "isp" prefix counts; word boundary at start, separator at end so
    # "isp_modem_01" matches but "ispybox" doesn't.
    (TIER_ISP,         re.compile(r"\bisp\b|\bisp[-_\d]|"
                                  r"\bstarlink\b|\bwan[-_\s]link\b|"
                                  r"\b(?:fiber|cable)[-_\s]?isp\b|"
                                  r"\bcarrier[-_\s]?(?:cpe|demarc)\b", re.I)),
    # WAN switch / edge router — sits between the ISP CPE and the firewall.
    (TIER_WAN_SWITCH,  re.compile(r"\b(wan[-_\s]?(?:sw|switch|router|gw)|"
                                  r"edge[-_\s]?(?:router|sw|switch)|"
                                  r"isp[-_\s]?sw|border[-_\s]?(?:sw|router))\b", re.I)),
    # Core / aggregation. N7K / N9K, ASR, spine switches, Catalyst 6500/9500.
    # Allows space / hyphen / underscore between vendor name and model number.
    (TIER_CORE_SWITCH, re.compile(r"\b(core[-_\s]?(?:sw|switch|router)|core\d|"
                                  r"aggregation|agg[-_\s]?(?:sw|switch)|backbone[-_\s]?(?:sw|switch)|"
                                  r"l3[-_\s]?(?:sw|switch)|spine[-_\s]?(?:sw|switch)|spine\d|"
                                  r"n[79]k|nexus[-_\s]?[79]\d{3}|asr\d|"
                                  r"cat(?:alyst)?[-_\s]?[69]\d{3})\b", re.I)),
    # Generic / access switch — TOR, Nexus 5K, EX2200, Catalyst 2/3/4xxx.
    (TIER_SWITCH,      re.compile(r"\b(switch|sw\d|sw-|tor-|ex[-\s]?\d+|n5k|"
                                  r"catalyst|nexus|junos|mikrotik|aruba|cisco-sw|"
                                  r"l2|access[-_\s]?(?:sw|switch)|router|rtr-)\b", re.I)),
    (TIER_CHASSIS,     re.compile(r"\b(bladecenter|chassis|enclosure|c[-\s]?class|"
                                  r"c7000|c3000|ucs[-\s]?\d|ucs-fi|m1000e|"
                                  r"oa\d|onboard[-\s]?admin)\b", re.I)),
    (TIER_VM,          re.compile(r"\b(vm-|-vm\b|vms?\b|cluster-vm|"
                                  r"guest|tenant)\b", re.I)),
    (TIER_HYPERVISOR,  re.compile(r"\b(esxi?|hyperv|kvm|proxmox|vmware|xenserver|"
                                  r"blade|esx-|hypervisor|host\d)\b", re.I)),
]


def infer_tier(device, group_overrides: dict | None = None,
               role_overrides: dict | None = None) -> str:
    """Best-effort tier classification for a device.

    Priority:
      1. Per-device Topology Role (`ip_allocations.kind`, set in the Edit Device
         modal). The most specific, explicitly user-set signal, so it wins over
         everything below — including a per-group override.
      2. Per-group override from `topo_settings.pw_group_tiers` (set via the
         Edit Group modal). Lets admins force a misclassified group into the
         right tier without changing device names.
      3. Regex inference over name + host + group string.
      4. Fallback to TIER_HYPERVISOR so generic servers still surface as
         cluster cards in the drill-in instead of disappearing into OTHER.
    """
    if role_overrides:
        role = role_overrides.get(getattr(device, "device_id", ""))
        tier = _ROLE_TO_TIER.get(role)
        if tier:
            return tier
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
        # {pid: {"lport","rport"}} — wiring info attached to this device's
        # outgoing links. Group parents are never keyed here.
        "parent_device_ports": dict(getattr(d, "parent_device_ports", {}) or {}),
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
            "host":              d.host,
            "status":            st,
            "parent_device_ids": d_parents,
            "parent_device_ports": dict(getattr(d, "parent_device_ports", {}) or {}),
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


# ─── Crossing-reduction ordering ───────────────────────────────────────────
# The Live Map draws a connection line from every card to its parent. Cards are
# laid out left→right inside their tier row, so a child placed far from its
# parent forces a long line that crosses its neighbours. We reduce crossings by
# ordering each tier with the classic layered-graph median (barycenter)
# heuristic: a card's position is pulled toward the median position of the cards
# it connects to in the adjacent tiers. The result is a pure function of the
# parent graph + names (never live status), so card order stays stable across
# status updates — only an actual topology change reshuffles anything.

# Vertical order of the tiers for the sweep. IPMI renders trailing in the
# hypervisor row, but it parents to switches, so it sits as its own layer here
# and gets ordered by its switch parents. `other` is excluded — it renders
# outside the connection canvas, so its order never affects crossings.
_SWEEP_ORDER = [
    TIER_ISP, TIER_WAN_SWITCH, TIER_FIREWALL, TIER_CORE_SWITCH, TIER_SWITCH,
    TIER_CHASSIS, TIER_HYPERVISOR, TIER_IPMI, TIER_VM,
]


def _median(vals: list):
    """Median of a list of floats, or None when empty."""
    s = sorted(vals)
    n = len(s)
    if not n:
        return None
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _card_id(card: dict) -> str:
    """Stable identity: a device card's did, or 'group:<name>' for a cluster."""
    if "did" in card:
        return card["did"]
    return "group:" + (card.get("name") or "")


def _card_sort_name(card: dict) -> str:
    return (card.get("name") or "").lower()


def _order_site_tiers(tiers: dict, by_parent: dict) -> None:
    """Reorder each tier's card list (in place) to minimize connection-line
    crossings. `tiers` maps every _SWEEP_ORDER key to its card list."""

    # Deterministic seed so the sweep — and any tier the graph can't order —
    # is reproducible run to run.
    for key in _SWEEP_ORDER:
        tiers[key].sort(key=_card_sort_name)

    def _pos_index():
        """Map did / group-name → normalized slot center in [0,1] for the
        CURRENT order. Cluster members all resolve to their cluster's slot."""
        did_pos, grp_pos = {}, {}
        for key in _SWEEP_ORDER:
            cards = tiers[key]
            n = len(cards) or 1
            for i, card in enumerate(cards):
                norm = (i + 0.5) / n
                if "did" in card:
                    did_pos[card["did"]] = norm
                else:
                    grp_pos[card.get("name", "")] = norm
                    for cell in card.get("cells", []):
                        did_pos[cell["did"]] = norm
        return did_pos, grp_pos

    def _resolve(pid, did_pos, grp_pos):
        if isinstance(pid, str) and pid.startswith("group:"):
            return grp_pos.get(pid[6:])
        return did_pos.get(pid)

    def _child_keys(card):
        """The parent-refs that point AT this card (so by_parent[...] yields
        its children)."""
        if "did" in card:
            return [card["did"]]
        keys = ["group:" + (card.get("name") or "")]
        for cell in card.get("cells", []):
            keys.append(cell["did"])
        return keys

    def _reorder(cards, key_fn):
        n = len(cards) or 1
        decorated = []
        for i, card in enumerate(cards):
            k = key_fn(card)
            if k is None:                    # no resolvable neighbour → hold slot
                k = (i + 0.5) / n
            decorated.append((k, _card_sort_name(card), card))
        decorated.sort(key=lambda t: (t[0], t[1]))
        cards[:] = [c for _, _, c in decorated]

    def _down_pass():
        # Order each tier by the median position of its PARENTS (above). Rebuild
        # the index per tier so lower tiers see the new order set this pass.
        for key in _SWEEP_ORDER:
            did_pos, grp_pos = _pos_index()
            def parent_key(card, _dp=did_pos, _gp=grp_pos):
                pos = [p for p in (_resolve(pid, _dp, _gp)
                                   for pid in card.get("parent_device_ids", []))
                       if p is not None]
                return _median(pos)
            _reorder(tiers[key], parent_key)

    def _up_pass():
        # Order each tier by the median position of its CHILDREN (below).
        for key in reversed(_SWEEP_ORDER):
            did_pos, grp_pos = _pos_index()
            def child_key(card, _dp=did_pos, _gp=grp_pos):
                pos = []
                for ck in _child_keys(card):
                    for ref in by_parent.get(ck, []):
                        p = (_gp.get(ref.get("name")) if ref.get("kind") == "cluster"
                             else _dp.get(ref.get("did")))
                        if p is not None:
                            pos.append(p)
                return _median(pos)
            _reorder(tiers[key], child_key)

    # Alternating sweeps converge the layout; end on a down-pass so children
    # finish aligned under their parents (the dominant visual).
    _up_pass()
    _down_pass()
    _up_pass()
    _down_pass()

    # Same-row adjacency: pull intra-tier linked cards (e.g. an access switch
    # uplinking to a sibling switch) next to each other so the side-to-side
    # line stays a short hop instead of arcing across the row. Runs last so the
    # barycenter sweep can't undo it.
    for key in _SWEEP_ORDER:
        cards = tiers[key]
        if len(cards) < 3:
            continue
        ids = {_card_id(c): c for c in cards}
        adj = defaultdict(set)
        for c in cards:
            cid = _card_id(c)
            for pid in c.get("parent_device_ids", []):
                tgt = None
                if isinstance(pid, str) and pid.startswith("group:"):
                    g = "group:" + pid[6:]
                    if g in ids:
                        tgt = g
                elif pid in ids:
                    tgt = pid
                if tgt and tgt != cid:
                    adj[cid].add(tgt)
                    adj[tgt].add(cid)
        if not adj:
            continue
        placed, out = set(), []
        for c in cards:
            cid = _card_id(c)
            if cid in placed:
                continue
            # Gather this card's connected component, then emit its members
            # contiguously in their current (barycenter) order.
            comp, stack = set(), [cid]
            while stack:
                x = stack.pop()
                if x in comp:
                    continue
                comp.add(x)
                stack.extend(adj.get(x, ()))
            for cc in cards:
                ccid = _card_id(cc)
                if ccid in comp and ccid not in placed:
                    out.append(cc)
                    placed.add(ccid)
        cards[:] = out


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
    # Load once per drill-in. Per-device role wins over the group override,
    # which in turn wins over the regex.
    group_overrides = _load_group_tier_overrides()
    role_overrides  = _load_device_role_overrides()
    group_parents   = _load_group_parent_overrides()

    # Bucket every device in this site by tier
    by_tier_devices = {
        TIER_ISP:         [],
        TIER_WAN_SWITCH:  [],
        TIER_FIREWALL:    [],
        TIER_CORE_SWITCH: [],
        TIER_SWITCH:      [],
        TIER_CHASSIS:     [],
        TIER_HYPERVISOR:  [],
        TIER_VM:          [],
        TIER_IPMI:        [],
        TIER_OTHER:       [],
    }
    for d in STATE.devices.values():
        if _site_of(d) != site_name:
            continue
        tier = infer_tier(d, group_overrides, role_overrides)
        by_tier_devices[tier].append(d)

    # ISP / WAN / Firewall / Core / Access render as individual device cards.
    isp_cards     = [_device_card(d, alerts_by_did, group_parents)
                     for d in by_tier_devices[TIER_ISP]]
    wan_cards     = [_device_card(d, alerts_by_did, group_parents)
                     for d in by_tier_devices[TIER_WAN_SWITCH]]
    firewalls     = [_device_card(d, alerts_by_did, group_parents)
                     for d in by_tier_devices[TIER_FIREWALL]]
    core_switches = [_device_card(d, alerts_by_did, group_parents)
                     for d in by_tier_devices[TIER_CORE_SWITCH]]
    switches      = [_device_card(d, alerts_by_did, group_parents)
                     for d in by_tier_devices[TIER_SWITCH]]

    # Chassis / VMs / IPMI render as cluster cards grouped by devices.grp.
    # Hypervisors do too. Chassis is its own row between switches + hypervisors.
    def _by_group(devs):
        grouped = defaultdict(list)
        for d in devs:
            grouped[(d.group or "Default Group")].append(d)
        return grouped

    chs_groups   = _by_group(by_tier_devices[TIER_CHASSIS])
    hyp_groups   = _by_group(by_tier_devices[TIER_HYPERVISOR])
    vm_groups    = _by_group(by_tier_devices[TIER_VM])
    ipmi_groups  = _by_group(by_tier_devices[TIER_IPMI])

    chassis     = [_cluster_card(gname, devs, alerts_by_did, group_parents)
                   for gname, devs in sorted(chs_groups.items(),  key=lambda kv: kv[0].lower())]
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

    for ic in isp_cards:
        _push_ref(ic["parent_device_ids"],
                  {"kind": "device", "tier": TIER_ISP, "did": ic["did"]})
    for wc in wan_cards:
        _push_ref(wc["parent_device_ids"],
                  {"kind": "device", "tier": TIER_WAN_SWITCH, "did": wc["did"]})
    for fc in firewalls:
        _push_ref(fc["parent_device_ids"],
                  {"kind": "device", "tier": TIER_FIREWALL, "did": fc["did"]})
    for cc in core_switches:
        _push_ref(cc["parent_device_ids"],
                  {"kind": "device", "tier": TIER_CORE_SWITCH, "did": cc["did"]})
    for sc in switches:
        _push_ref(sc["parent_device_ids"],
                  {"kind": "device", "tier": TIER_SWITCH, "did": sc["did"]})
    for cc in chassis:
        _push_ref(cc["parent_device_ids"],
                  {"kind": "cluster", "tier": TIER_CHASSIS, "name": cc["name"]})
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
    # Group refs ("group:<name>") are excluded from device-id validation —
    # they resolve against cluster cards in the frontend, not the device map.
    cross_site_parents = {pid for pid in by_parent
                          if not pid.startswith("group:")
                          and pid not in site_dids and pid in STATE.devices}
    missing_parents = {pid for pid in by_parent
                       if not pid.startswith("group:")
                       and pid not in STATE.devices}

    # Reorder tiers in place to minimize connection-line crossings. by_parent
    # is keyed by parent ref and is unaffected by card order, so it's safe to
    # read here and still return as-is.
    _order_site_tiers(
        {
            TIER_ISP:         isp_cards,
            TIER_WAN_SWITCH:  wan_cards,
            TIER_FIREWALL:    firewalls,
            TIER_CORE_SWITCH: core_switches,
            TIER_SWITCH:      switches,
            TIER_CHASSIS:     chassis,
            TIER_HYPERVISOR:  hypervisors,
            TIER_IPMI:        ipmi,
            TIER_VM:          vm_clusters,
        },
        by_parent,
    )

    return {
        "site": {
            "name":    site_name,
            "devices": len(all_devs),
            "up":      up,
            "warn":    warn,
            "down":    down,
            "alerts":  alerts,
        },
        "isp":           isp_cards,
        "wan_switches":  wan_cards,
        "firewalls":     firewalls,
        "core_switches": core_switches,
        "switches":      switches,
        "chassis":       chassis,
        "hypervisors":   hypervisors,
        "vm_clusters":   vm_clusters,
        "ipmi":          ipmi,
        "other":         other,
        "by_parent":          dict(by_parent),
        "orphans":            orphans,
        "cross_site_parents": sorted(cross_site_parents),
        "missing_parents":    sorted(missing_parents),
    }
