"""
routes/livemap.py — Read-only endpoints feeding the Live Map NOC console.

GET /api/livemap/sites              viewer — per-site rollup with metadata
GET /api/livemap/noc/summary        viewer — hero stats + widgets payload
GET /api/livemap/sites/<name>/tree  viewer — drill-in tier tree for a site
"""
from __future__ import annotations

import re
from urllib.parse import unquote

from monitoring.site_rollup import site_summary_list, noc_summary
from monitoring.site_tree   import site_tree


_RE_LM_SITES   = re.compile(r"^/api/livemap/sites/?$")
_RE_LM_SUMMARY = re.compile(r"^/api/livemap/noc/summary/?$")
_RE_LM_TREE    = re.compile(r"^/api/livemap/sites/([^/]+)/tree/?$")


def handle(h, method, path, body):
    if method != "GET":
        return False

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
