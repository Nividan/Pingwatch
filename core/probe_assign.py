"""
core/probe_assign.py — effective-probe resolution for distributed probes.

Assignment cascade (first non-empty wins):

    sensor.probe_id  →  device.probe_id  →  site binding  →  '' (central)

The literal value 'central' is an explicit pin: it short-circuits the
cascade at that level and resolves to '' so a sensor/device on a
probe-bound device/site can opt back into central probing.

effective_probe() sits on the probe hot path (every scheduler tick checks
it), so the site→probe tier is served from an in-memory cache instead of
a DB query. Writers to sites.probe_id and probe delete/reassign must call
invalidate_site_probe_cache().
"""
from __future__ import annotations

import threading

CENTRAL = "central"   # explicit-pin sentinel stored in probe_id columns

_site_map: dict | None = None
_site_map_lock = threading.Lock()


def _get_site_map() -> dict:
    global _site_map
    m = _site_map
    if m is not None:
        return m
    with _site_map_lock:
        if _site_map is None:
            try:
                from db.probes import db_site_probe_map
                _site_map = db_site_probe_map()
            except Exception:
                # DB not ready yet (early startup) — serve empty, retry next call
                return {}
        return _site_map


def invalidate_site_probe_cache():
    """Drop the cached site→probe map; next lookup reloads from the DB."""
    global _site_map
    with _site_map_lock:
        _site_map = None


def site_probe(site_name: str) -> str:
    """Probe bound to a site name ('' when unbound/unknown)."""
    if not site_name:
        return ""
    pid = _get_site_map().get(site_name, "")
    return "" if pid == CENTRAL else pid


def effective_probe(dev, sensor=None) -> str:
    """Resolve which probe (probe_id) measures this sensor/device.

    Returns '' for central probing. Pass sensor=None to resolve at the
    device level (device → site tiers only).
    """
    if sensor is not None:
        pid = getattr(sensor, "probe_id", "") or ""
        if pid:
            return "" if pid == CENTRAL else pid
    if dev is not None:
        pid = getattr(dev, "probe_id", "") or ""
        if pid:
            return "" if pid == CENTRAL else pid
        return site_probe(getattr(dev, "site", "") or "")
    return ""
