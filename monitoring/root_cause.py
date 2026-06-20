"""
monitoring/root_cause.py — Dependency-graph root-cause correlation (RCA).

When upstream infrastructure fails (a core switch, a firewall, an ISP link),
every device behind it goes down at the same moment. Today each of those is an
independent flap/alert with no hint they share one cause. This module correlates
the live down-set (STATE.devices) against the parent dependency graph
(devices.parent_device_ids, with the pw_group_parents fallback) to:

  * name the single ROOT device behind each cluster of downs (active_incidents),
  * tell the alert engine which device alerts are downstream symptoms that
    should be suppressed while their root is down (suppressed_root_for),
  * reconstruct past incidents from flap_log for the historical view
    (historical_incidents).

Everything here is read-only over in-memory state + flap_log. It reuses the
parent-resolution / tier / status helpers the Live Map already relies on, so RCA
sees exactly the same topology the operator does.

Redundancy-aware: a down device is only "explained by upstream" when EVERY one
of its resolved parents is also down. A dual-homed device that still has one live
uplink is treated as a genuine local fault (its own root), never suppressed.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict

import core.settings as _settings
from core.app_state import STATE
from core.logger    import log

from monitoring.site_rollup import _device_status, _site_of, _iso_utc, _parse_iso_ts
from monitoring.site_tree   import (
    _resolve_parents, _load_group_parent_overrides,
    _load_group_tier_overrides, _load_device_role_overrides,
    infer_tier,
)

# Upstream-walk hop cap — guards against a mis-wired parent cycle dragging the
# walk into an infinite loop. Far above any real topology depth.
_MAX_WALK = 64

# Tiers that represent shared infrastructure: a failure here plausibly takes a
# whole subtree with it, so a root in one of these earns a confidence boost.
_INFRA_TIERS = {"isp", "wan_switch", "firewall", "core_switch", "switch",
                "ap", "chassis"}

# Memoization — the alert engine calls suppressed_root_for() once per firing
# sensor, potentially from several probe threads at once. Rebuild the attribution
# at most once per _MEMO_TTL seconds so an outage storm doesn't recompute the
# graph N times. Probe intervals are tens of seconds, so a few seconds of lag in
# lifting/applying suppression is immaterial.
_MEMO_TTL = 5.0
_memo_lock = threading.Lock()
_memo: dict = {"ts": 0.0, "incidents": [], "suppress": {}}


# ── Graph helpers ───────────────────────────────────────────────

def _device_parents_map(by_id: dict, group_parents: dict) -> dict:
    """{did: [parent_did, ...]} keeping only parents that are real monitored
    devices. Group refs ('group:<name>') and parents we don't monitor are
    dropped — we can only reason about, and only blame, devices we can see the
    status of."""
    pmap = {}
    for did, d in by_id.items():
        ps = _resolve_parents(d, group_parents)
        pmap[did] = [p for p in ps
                     if isinstance(p, str) and not p.startswith("group:") and p in by_id]
    return pmap


def _device_down_since(d) -> float | None:
    """Earliest wall-clock time any of this device's sensors went down, from the
    in-memory _down_since_ts the probe loop maintains. None when nothing is
    currently down (no DB hit)."""
    ts = None
    for s in d.sensors.values():
        t = getattr(s, "_down_since_ts", None)
        if t and (ts is None or t < ts):
            ts = t
    return ts


def _dev_summary(d, status: str, tier: str) -> dict:
    ds = _device_down_since(d)
    return {
        "did":        d.device_id,
        "name":       d.name,
        "host":       d.host,
        "group":      d.group or "",
        "site":       _site_of(d),
        "tier":       tier,
        "status":     status,
        "down_since": int(ds) if ds else None,
    }


# ── Core attribution ────────────────────────────────────────────

def _compute_full() -> tuple[list, dict]:
    """Build (incidents, suppress_map) from current STATE + the parent graph.

    incidents    — list of incident dicts (root + impacted + evidence), sorted
                   most-impactful first.
    suppress_map — {did: root_summary} for every device that is explained by a
                   down upstream (all parents down → topmost down ancestor).
    """
    devs  = list(STATE.devices.values())
    by_id = {d.device_id: d for d in devs}
    if not by_id:
        return [], {}

    group_parents = _load_group_parent_overrides()
    parents = _device_parents_map(by_id, group_parents)
    status  = {did: _device_status(by_id[did]) for did in by_id}
    down    = {did for did, st in status.items() if st == "down"}

    # Tier classification — load the override maps once (cheap DB reads).
    try:
        g_over = _load_group_tier_overrides()
        r_over = _load_device_role_overrides()
    except Exception:
        g_over, r_over = {}, {}
    _tier_cache: dict = {}

    def tier_of(did):
        t = _tier_cache.get(did)
        if t is None:
            t = infer_tier(by_id[did], g_over, r_over)
            _tier_cache[did] = t
        return t

    def all_parents_down(did) -> bool:
        ps = parents.get(did, [])
        return bool(ps) and all(p in down for p in ps)

    def find_root(did) -> str:
        """Topmost down ancestor reachable while every step is explained by
        upstream. Cycle-guarded; deterministic (follows the lexicographically
        smallest down parent at each diamond)."""
        seen, cur, hops = set(), did, 0
        while hops < _MAX_WALK and cur not in seen:
            seen.add(cur)
            if not all_parents_down(cur):
                return cur
            dps = sorted(p for p in parents[cur] if p in down)
            if not dps:
                return cur
            cur = dps[0]
            hops += 1
        return cur

    # Attribute every down device to its root, then cluster by root.
    clusters: dict = defaultdict(list)
    for did in down:
        clusters[find_root(did)].append(did)

    # Suppression: any device whose parents are all down is a downstream symptom
    # of the topmost down ancestor — independent of whether it's "down" itself
    # yet (a single failing sensor still counts as collateral of the outage).
    suppress_map: dict = {}
    root_summary_cache: dict = {}

    def root_summary(root_did) -> dict:
        s = root_summary_cache.get(root_did)
        if s is None:
            rd = by_id[root_did]
            s = _dev_summary(rd, status[root_did], tier_of(root_did))
            root_summary_cache[root_did] = s
        return s

    for did in by_id:
        if all_parents_down(did):
            root = find_root(did)
            if root != did and root in down:
                suppress_map[did] = root_summary(root)

    # Optional evidence: link/interface-down traps near each root host.
    trap_hosts = _recent_trap_hosts()

    incidents = []
    for root_did, members in clusters.items():
        rd = by_id[root_did]
        r_tier = tier_of(root_did)
        root_sum = _dev_summary(rd, status[root_did], r_tier)
        impacted = sorted(
            (_dev_summary(by_id[m], status[m], tier_of(m)) for m in members if m != root_did),
            key=lambda x: (x["tier"], x["name"].lower()),
        )

        reasons = []
        if r_tier in _INFRA_TIERS:
            reasons.append(f"root is {r_tier.replace('_', ' ')} (shared infrastructure)")
        # "Root went down first": root's down_since is at/earlier than every
        # impacted device's down_since.
        r_ds = root_sum["down_since"]
        if impacted and r_ds:
            child_ds = [c["down_since"] for c in impacted if c["down_since"]]
            if child_ds and r_ds <= min(child_ds):
                reasons.append("root went down first")
        if (rd.host or "") in trap_hosts or (rd.name or "") in trap_hosts:
            reasons.append("link-down trap from root")

        impacted_count = len(impacted)
        if impacted_count == 0:
            confidence = "low"            # lone down device, no correlation
        elif r_tier in _INFRA_TIERS and len(reasons) >= 2:
            confidence = "high"
        else:
            confidence = "medium"

        incidents.append({
            "root":           root_sum,
            "impacted":       impacted,
            "impacted_count": impacted_count,
            "confidence":     confidence,
            "reasons":        reasons,
            "site":           root_sum["site"],
        })

    # Most impactful first; tie-break newest-down first.
    incidents.sort(key=lambda i: (i["impacted_count"],
                                  -(i["root"]["down_since"] or 0)),
                   reverse=True)
    return incidents, suppress_map


def _recent_trap_hosts() -> set:
    """Set of src_ip / dname that emitted a warning/critical SNMP trap within
    the correlation window. Used only as confidence evidence, so failures here
    degrade silently to 'no trap evidence'."""
    try:
        window = int(_settings.get("rca_correlation_window_s", 120) or 120)
    except (ValueError, TypeError):
        window = 120
    cutoff_iso = _iso_utc(int(time.time()) - max(30, window))
    try:
        from db.helpers import db_query
        rows = db_query(
            "logs",
            "SELECT src_ip, dname FROM snmp_traps "
            "WHERE ts >= ? AND severity IN ('warning','critical')",
            (cutoff_iso,),
        )
    except Exception as e:
        log.debug(f"root_cause trap evidence query failed: {e}")
        return set()
    hosts = set()
    for r in rows:
        if r.get("src_ip"):
            hosts.add(r["src_ip"])
        if r.get("dname"):
            hosts.add(r["dname"])
    return hosts


# ── Memo + public API ───────────────────────────────────────────

def _refresh(force: bool = False) -> dict:
    now = time.time()
    with _memo_lock:
        if not force and _memo["ts"] > 0 and (now - _memo["ts"]) < _MEMO_TTL:
            return _memo
        try:
            incidents, suppress = _compute_full()
            _memo["incidents"] = incidents
            _memo["suppress"]  = suppress
            _memo["ts"]        = now
        except Exception as e:
            log.warning(f"root_cause: incident computation failed: {e}")
        return _memo


def active_incidents() -> dict:
    """Live root-cause incidents for the endpoint / widget / map / events view.

    {
      "incidents":        [ {root, impacted, impacted_count, confidence,
                             reasons, site}, ... ],   # sorted, most-impactful first
      "correlated_count": N,        # incidents with >=1 impacted device
      "suppress_enabled": bool,
      "generated_at":     epoch,
    }
    """
    memo = _refresh()
    incidents = memo["incidents"]
    return {
        "incidents":        incidents,
        "correlated_count": sum(1 for i in incidents if i["impacted_count"] > 0),
        "suppress_enabled": bool(int(_settings.get("rca_suppress_downstream", 1) or 0)),
        "generated_at":     int(memo["ts"] or time.time()),
    }


def suppressed_root_for(did: str) -> dict | None:
    """Return the root-device summary this device is a downstream symptom of, or
    None. Used by the alert engine to suppress symptom alerts. Returns None when
    the master toggle is off, so the caller need not check separately."""
    try:
        if not int(_settings.get("rca_suppress_downstream", 1) or 0):
            return None
    except (ValueError, TypeError):
        return None
    return _refresh()["suppress"].get(did)


def invalidate() -> None:
    """Drop the memo so the next read recomputes immediately (e.g. after the
    suppression toggle changes)."""
    with _memo_lock:
        _memo["ts"] = 0.0


# ── Historical reconstruction (flap_log) ────────────────────────

def historical_incidents(window_s: int = 86400, max_incidents: int = 100) -> dict:
    """Reconstruct past incidents from flap_log over the last `window_s`.

    Clusters 'down' flaps whose devices share a root in the CURRENT parent graph
    and that overlap within the correlation window, then attributes each cluster
    to its root. Because PingWatch keeps no historical topology snapshots, the
    attribution uses today's parent graph — accurate while topology is stable.
    The caller surfaces that caveat in the UI.
    """
    try:
        window_s = max(300, min(int(window_s), 90 * 86400))
    except (ValueError, TypeError):
        window_s = 86400
    try:
        corr = int(_settings.get("rca_correlation_window_s", 120) or 120)
    except (ValueError, TypeError):
        corr = 120

    now = int(time.time())
    cutoff_iso = _iso_utc(now - window_s)
    try:
        from db.helpers import db_query
        rows = db_query(
            "logs",
            "SELECT ts, did, dname, COALESCE(resolved_at,0) AS rts "
            "FROM flap_log WHERE ts >= ? AND direction='down' "
            "ORDER BY ts ASC",
            (cutoff_iso,),
        )
    except Exception as e:
        log.warning(f"root_cause: historical query failed: {e}")
        return {"incidents": [], "window_s": window_s, "topology_caveat": True}

    by_id = {d.device_id: d for d in STATE.devices.values()}
    group_parents = _load_group_parent_overrides()
    parents = _device_parents_map(by_id, group_parents) if by_id else {}

    # Each flap → (start, end, did, dname).
    flaps = []
    for r in rows:
        start = _parse_iso_ts(r.get("ts"))
        if not start:
            continue
        rts = int(float(r.get("rts") or 0))
        end = rts if rts > 0 else now
        flaps.append({"start": start, "end": end,
                      "did": r.get("did") or "", "dname": r.get("dname") or ""})

    # Group flaps by the root they resolve to in the current graph. With no live
    # status to walk, a flap's "root" is the highest ancestor that ALSO flapped
    # in an overlapping window — approximated by: walk up the parent chain while
    # the parent has an overlapping down flap.
    down_windows: dict = defaultdict(list)   # did → [(start,end), ...]
    for f in flaps:
        down_windows[f["did"]].append((f["start"], f["end"]))

    def was_down_at(did, t0, t1) -> bool:
        for (s, e) in down_windows.get(did, ()):
            if s <= t1 + corr and e + corr >= t0:
                return True
        return False

    def hist_root(did, t0, t1) -> str:
        seen, cur, hops = set(), did, 0
        while hops < _MAX_WALK and cur not in seen:
            seen.add(cur)
            ps = parents.get(cur, [])
            up = [p for p in ps if was_down_at(p, t0, t1)]
            if not (ps and len(up) == len(ps)):   # not all parents down → root
                return cur
            cur = sorted(up)[0]
            hops += 1
        return cur

    clusters: dict = defaultdict(lambda: {"members": {}, "start": None, "end": None})
    for f in flaps:
        root = hist_root(f["did"], f["start"], f["end"])
        c = clusters[root]
        c["members"].setdefault(f["did"], f["dname"])
        c["start"] = f["start"] if c["start"] is None else min(c["start"], f["start"])
        c["end"]   = f["end"]   if c["end"]   is None else max(c["end"],   f["end"])

    out = []
    for root_did, c in clusters.items():
        members = c["members"]
        impacted = max(0, len(members) - 1)
        rd = by_id.get(root_did)
        out.append({
            "root": {
                "did":   root_did,
                "name":  (members.get(root_did) or (rd.name if rd else root_did)),
                "host":  (rd.host if rd else ""),
            },
            "impacted_count": impacted,
            "impacted":       [{"did": d, "name": n}
                               for d, n in sorted(members.items(), key=lambda kv: (kv[1] or "").lower())
                               if d != root_did],
            "started_at":  c["start"],
            "ended_at":    c["end"] if c["end"] < now else None,
            "duration_s":  (c["end"] - c["start"]) if c["start"] else 0,
        })

    out.sort(key=lambda i: (i["impacted_count"], i["started_at"] or 0), reverse=True)
    return {
        "incidents":       out[:max_incidents],
        "window_s":        window_s,
        "topology_caveat": True,
    }
