"""
routes/livemap.py — Read-only endpoints feeding the Live Map NOC console.

GET /api/livemap/sites              viewer — per-site rollup with metadata
GET /api/livemap/noc/summary        viewer — hero stats + widgets payload
GET /api/livemap/sites/<name>/tree  viewer — drill-in tier tree for a site
GET /api/incidents                  viewer — live root-cause incidents (RCA)
GET /api/incidents/history          viewer — reconstructed past incidents
"""
from __future__ import annotations

import re
from urllib.parse import unquote, parse_qs, urlparse

from monitoring.site_rollup import site_summary_list, noc_summary
from monitoring.site_tree   import site_tree


_RE_LM_SITES   = re.compile(r"^/api/livemap/sites/?$")
_RE_LM_SUMMARY = re.compile(r"^/api/livemap/noc/summary/?$")
_RE_LM_TREE    = re.compile(r"^/api/livemap/sites/([^/]+)/tree/?$")
_RE_INCIDENTS  = re.compile(r"^/api/incidents/?$")
_RE_INC_HIST   = re.compile(r"^/api/incidents/history/?$")

# Window aliases for /api/incidents/history?window=…
_HIST_WINDOWS = {"1h": 3600, "6h": 21600, "24h": 86400,
                 "7d": 604800, "30d": 2592000}


def handle(h, method, path, body):
    if method != "GET":
        return False

    if _RE_INCIDENTS.match(path):
        user, _ = h._require("viewer")
        if not user:
            return True
        try:
            from monitoring.root_cause import active_incidents
            h._json(200, active_incidents())
        except Exception:
            from core.logger import log
            log.exception("active_incidents failed")
            h._json(500, {"error": "Failed to compute incidents"})
        return True

    if _RE_INC_HIST.match(path):
        user, _ = h._require("viewer")
        if not user:
            return True
        qs = parse_qs(urlparse(h.path).query)
        win = (qs.get("window", ["24h"])[0] or "24h").strip()
        window_s = _HIST_WINDOWS.get(win)
        if window_s is None:
            try:
                window_s = int(win)
            except (ValueError, TypeError):
                window_s = 86400
        try:
            from monitoring.root_cause import historical_incidents
            h._json(200, historical_incidents(window_s))
        except Exception:
            from core.logger import log
            log.exception("historical_incidents failed")
            h._json(500, {"error": "Failed to compute incident history"})
        return True

    if _RE_LM_SITES.match(path):
        user, _ = h._require("viewer")
        if not user:
            return True
        h._json(200, {"sites": site_summary_list()})
        return True

    if _RE_LM_SUMMARY.match(path):
        user, _ = h._require("viewer")
        if not user:
            return True
        h._json(200, noc_summary())
        return True

    m = _RE_LM_TREE.match(path)
    if m:
        user, _ = h._require("viewer")
        if not user:
            return True
        name = unquote(m.group(1)).strip()
        if not name:
            h._json(400, {"error": "site name required"}); return True
        h._json(200, site_tree(name))
        return True

    return False
