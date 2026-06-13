"""
monitoring/site_rollup.py — Live Map roll-ups.

Aggregates in-memory device/sensor state (STATE.devices) and DB-backed
alert / flap counts into the payloads the new Live Map NOC console
consumes. Every function here is read-only.
"""
from __future__ import annotations

import datetime
import time
from collections import defaultdict

from core.app_state import STATE
from core.logger    import log
from db             import (
    db_list_sites, db_get_site_meta, db_ensure_site_meta,
    db_count_active_flaps, db_load_flaps,
)
from db.helpers     import db_query


def _iso_utc(epoch_sec: int) -> str:
    """Format an epoch second as the ISO string flap_log.ts uses."""
    return datetime.datetime.fromtimestamp(epoch_sec, datetime.timezone.utc) \
                            .strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_ts(s) -> int:
    """Parse an ISO-Z timestamp (or pass-through epoch number) into epoch seconds.
    Returns 0 on failure so callers don't blow up on legacy or malformed rows."""
    if s is None:
        return 0
    if isinstance(s, (int, float)):
        return int(s)
    try:
        # The format we store is "%Y-%m-%dT%H:%M:%SZ"
        return int(datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
                     .replace(tzinfo=datetime.timezone.utc).timestamp())
    except Exception:
        try:
            return int(float(s))   # fallback for old numeric-string rows
        except Exception:
            return 0


# ── Constants ───────────────────────────────────────────────────

UNSITED_NAME = "Unsited"          # synthetic bucket for devices with no site

KIND_PIN_DEFAULTS = {
    # Lower-cased site name → kind; used the first time a site is rolled-up.
    "internet": ("internet", 1),
    "off-site": ("internet", 1),
    "offsite":  ("internet", 1),
}


# ── Internal helpers ────────────────────────────────────────────

def _site_of(device) -> str:
    """Effective site for a device, falling back to the Unsited bucket."""
    s = (getattr(device, "site", "") or "").strip()
    return s if s else UNSITED_NAME


def _device_status(device) -> str:
    """Return 'up' | 'warn' | 'down' | 'pause' | 'unknown' for a device."""
    try:
        return device.status or "unknown"
    except Exception:
        return "unknown"


def _meta_for(name: str, *, kind_hint: str = "lab", pinned_hint: int = 0) -> dict:
    """Return metadata for a site name, auto-creating the row if missing."""
    # Hint override for well-known names
    low = name.lower()
    if low in KIND_PIN_DEFAULTS:
        kind_hint, pinned_hint = KIND_PIN_DEFAULTS[low]
    db_ensure_site_meta(name, kind=kind_hint, pinned=pinned_hint)
    meta = db_get_site_meta(name)
    if not meta:
        # Defensive — should never happen because we just ensured it
        return {"name": name, "kind": kind_hint, "pinned": pinned_hint,
                "display_name": "", "sort_order": 0,
                "created_ts": 0, "updated_ts": 0}
    return meta


def _active_alerts_by_did() -> dict:
    """Return {did: count} of active alert_events grouped by device id."""
    try:
        rows = db_query(
            "main",
            "SELECT did, COUNT(*) AS c FROM alert_events WHERE state='active' GROUP BY did"
        )
        return {r["did"]: int(r["c"] or 0) for r in rows}
    except Exception as e:
        log.warning(f"site_rollup active_alerts query failed: {e}")
        return {}


def _active_alerts_by_state() -> dict:
    """Return {'down': N, 'warn': N, 'ack': N, 'active': N}."""
    out = {"down": 0, "warn": 0, "ack": 0, "active": 0}
    try:
        rows = db_query(
            "main",
            "SELECT severity, state, COUNT(*) AS c "
            "FROM alert_events WHERE state IN ('active','acknowledged') "
            "GROUP BY severity, state"
        )
        for r in rows:
            sev   = (r["severity"] or "").lower()
            st    = (r["state"]    or "").lower()
            count = int(r["c"] or 0)
            if st == "acknowledged":
                out["ack"] += count
            else:
                out["active"] += count
                if sev in ("crit", "critical", "down", "error"):
                    out["down"] += count
                elif sev in ("warn", "warning"):
                    out["warn"] += count
    except Exception as e:
        log.warning(f"site_rollup alert state query failed: {e}")
    return out


# ── Public API ──────────────────────────────────────────────────

def site_summary_list() -> list:
    """Return one row per site with rollup counts + metadata.

    Output: [
      {name, kind, pinned, display_name,
       devices, up, warn, down, alerts}
    ]
    Sites without any device are still included if they have metadata.
    Special "Unsited" bucket present iff at least one device has no site.
    """
    devices = list(STATE.devices.values())
    alerts_by_did = _active_alerts_by_did()

    # Bucket devices by site
    by_site = defaultdict(lambda: {"devices": 0, "up": 0, "warn": 0, "down": 0,
                                   "alerts": 0, "_has_devices": False})
    for d in devices:
        name = _site_of(d)
        bucket = by_site[name]
        bucket["devices"] += 1
        bucket["_has_devices"] = True
        st = _device_status(d)
        if   st == "up":   bucket["up"]   += 1
        elif st == "warn": bucket["warn"] += 1
        elif st == "down": bucket["down"] += 1
        bucket["alerts"] += alerts_by_did.get(d.device_id, 0)

    # Also include metadata-only sites (rows in `sites` with no devices today)
    meta_rows = {m["name"]: m for m in db_list_sites()}
    for name in meta_rows:
        if name not in by_site:
            by_site[name]  # initializes default zero counters

    result = []
    for name in sorted(by_site.keys(), key=str.lower):
        b = by_site[name]
        # Skip the Unsited bucket if it has no devices
        if name == UNSITED_NAME and not b["_has_devices"]:
            continue
        meta = meta_rows.get(name) or _meta_for(name)
        result.append({
            "name":         name,
            "kind":         meta.get("kind") or "lab",
            "pinned":       int(meta.get("pinned") or 0),
            "display_name": meta.get("display_name") or "",
            "devices":      b["devices"],
            "up":           b["up"],
            "warn":         b["warn"],
            "down":         b["down"],
            "alerts":       b["alerts"],
        })
    return result


def _uptime_24h() -> float:
    """Rough 24h availability = 1 - (active_down_seconds / device_seconds).
    Uses flap_log: each unresolved 'down' contributes (now-ts) seconds; each
    resolved 'down' contributes (resolved_at-ts). Returns a float 0..1,
    defaulting to 1.0 when no devices.

    flap_log.ts is TEXT (ISO 'YYYY-MM-DDTHH:MM:SSZ'); resolved_at is REAL/
    DOUBLE PRECISION epoch seconds. We compare ts as TEXT to keep both PG
    and SQLite happy.
    """
    devices = list(STATE.devices.values())
    if not devices:
        return 1.0
    window = 86400
    now = int(time.time())
    cutoff = now - window
    cutoff_iso = _iso_utc(cutoff)
    try:
        rows = db_query(
            "logs",
            "SELECT ts, COALESCE(resolved_at,0) AS rts, direction "
            "FROM flap_log WHERE ts >= ? AND direction IN ('down','recovered')",
            (cutoff_iso,)
        )
    except Exception as e:
        log.debug(f"_uptime_24h query failed: {e}")
        return 1.0
    down_sec = 0
    for r in rows:
        if (r["direction"] or "").lower() != "down":
            continue
        start = max(_parse_iso_ts(r["ts"]), cutoff)
        rts = int(float(r["rts"] or 0))
        end = rts if rts > 0 else now
        if end > start:
            down_sec += (end - start)
    total_sec = len(devices) * window
    if total_sec <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - (down_sec / total_sec)))


def _flaps_24h() -> int:
    """Count of 'down' flap events in the last 24h."""
    cutoff_iso = _iso_utc(int(time.time()) - 86400)
    try:
        row = db_query(
            "logs",
            "SELECT COUNT(*) AS c FROM flap_log WHERE ts >= ? AND direction='down'",
            (cutoff_iso,)
        )
        return int(row[0]["c"]) if row else 0
    except Exception as e:
        log.debug(f"_flaps_24h query failed: {e}")
        return 0


def _incidents_24h() -> int:
    """Count of unique devices that experienced a down in the last 24h."""
    cutoff_iso = _iso_utc(int(time.time()) - 86400)
    try:
        row = db_query(
            "logs",
            "SELECT COUNT(DISTINCT did) AS c FROM flap_log "
            "WHERE ts >= ? AND direction='down'",
            (cutoff_iso,)
        )
        return int(row[0]["c"]) if row else 0
    except Exception as e:
        log.debug(f"_incidents_24h query failed: {e}")
        return 0


def _recent_alerts(limit: int = 8) -> list:
    """Return last N alert / flap events for the recent-alerts feed.
    Drawn from the flap_log (high-volume, already retention-capped)."""
    try:
        flaps = db_load_flaps(limit=limit)
    except Exception as e:
        log.debug(f"_recent_alerts failed: {e}")
        return []
    out = []
    for f in flaps or []:
        # Map flap_log row → feed row. flap_log.ts is stored as ISO TEXT;
        # convert to epoch int so the frontend's time-ago renderer works.
        direction = (f.get("direction") or "").lower()
        severity  = "down" if direction in ("down",) else (
                    "warn" if direction in ("threshold_warn", "threshold_crit") else "ok")
        out.append({
            "ts":        _parse_iso_ts(f.get("ts")),
            "severity":  severity,
            "direction": direction,
            "did":       f.get("did") or "",
            "sid":       f.get("sid") or "",
            "dname":     f.get("dname") or "",
            "sname":     f.get("sname") or "",
            "host":      f.get("host") or "",
            "site":      _site_for_did(f.get("did") or ""),
        })
    return out


def _site_for_did(did: str) -> str:
    if not did:
        return ""
    d = STATE.devices.get(did)
    if not d:
        return ""
    return _site_of(d)


def _off_site_block() -> list:
    """Internet reachability checks — devices whose site is 'OFF-Site' /
    'Internet' / 'Off-Site' (case-insensitive)."""
    targets = []
    for d in STATE.devices.values():
        site_low = (d.site or "").lower()
        if site_low in ("off-site", "offsite", "internet"):
            targets.append(d)
    out = []
    for d in targets:
        st = _device_status(d)
        # Pick a primary sensor's last_ms for latency display
        latency = None
        for s in d.sensors.values():
            if s.alive and s.last_ms is not None:
                latency = int(s.last_ms)
                break
        out.append({
            "did":    d.device_id,
            "name":   d.name,
            "host":   d.host,
            "status": st,
            "latency_ms": latency,
        })
    return out


def noc_summary() -> dict:
    """Top-level summary used by the M1a hero stats + sidebars/widgets."""
    sites = site_summary_list()

    s_up = s_warn = s_down = 0
    d_total = d_up = d_warn = d_down = 0
    by_kind = defaultdict(lambda: {"total": 0, "up": 0, "warn": 0, "down": 0, "devices": 0})
    for s in sites:
        # Worst-status rollup for this site
        if   s["down"]: s_down += 1
        elif s["warn"]: s_warn += 1
        else:           s_up   += 1
        d_total += s["devices"]
        d_up    += s["up"]
        d_warn  += s["warn"]
        d_down  += s["down"]
        k = s["kind"] or "lab"
        bk = by_kind[k]
        bk["total"]   += 1
        bk["devices"] += s["devices"]
        if   s["down"]: bk["down"] += 1
        elif s["warn"]: bk["warn"] += 1
        else:           bk["up"]   += 1

    alerts = _active_alerts_by_state()

    # Top problem sites: sort by alerts desc then by down desc
    problems = sorted(
        [s for s in sites if s["alerts"] > 0 or s["down"] > 0 or s["warn"] > 0],
        key=lambda s: (s["alerts"], s["down"], s["warn"]),
        reverse=True,
    )[:6]

    return {
        "sites":     {"up": s_up, "warn": s_warn, "down": s_down, "total": len(sites)},
        "devices":   {"up": d_up, "warn": d_warn, "down": d_down, "total": d_total},
        "alerts":    {"active": alerts["active"], "down": alerts["down"],
                      "warn":   alerts["warn"],   "ack":  alerts["ack"]},
        "uptime_24h":    _uptime_24h(),
        "flaps_24h":     _flaps_24h(),
        "incidents_24h": _incidents_24h(),
        "by_kind":       dict(by_kind),
        "top_problems":  [{"name": s["name"], "kind": s["kind"],
                           "devices": s["devices"], "up": s["up"],
                           "warn": s["warn"], "down": s["down"],
                           "alerts": s["alerts"]} for s in problems],
        "recent_alerts": _recent_alerts(8),
        "off_site":      _off_site_block(),
    }
