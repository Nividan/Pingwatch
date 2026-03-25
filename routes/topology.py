"""
routes/topology.py — Network topology map (NTM) CRUD endpoints.

Handles: /api/pages, /api/nodes, /api/links, /api/groups (GET/POST),
         /api/pages/{id}, /api/nodes/{id}, /api/links/{id}, /api/groups/{id} (PUT/DELETE),
         /api/settings/{key} (GET/PATCH/PUT).
"""

import re

from db import db_log_audit
from monitoring.network_map import (
    topo_get_pages, topo_insert_page, topo_update_page, topo_delete_page,
    topo_get_nodes, topo_insert_node, topo_update_node, topo_delete_node,
    topo_get_links, topo_insert_link, topo_update_link, topo_delete_link,
    topo_get_groups, topo_insert_group, topo_update_group, topo_delete_group,
    topo_get_setting, topo_upsert_setting,
)

# ── Route regexes (local to this module) ─────────────────────────
_RE_TOPO_PAGE    = re.compile(r'^/api/pages/(\d+)$')
_RE_TOPO_NODE    = re.compile(r'^/api/nodes/(\d+)$')
_RE_TOPO_LINK    = re.compile(r'^/api/links/(\d+)$')
_RE_TOPO_GROUP   = re.compile(r'^/api/groups/(\d+)$')
_RE_TOPO_SETTING = re.compile(r'^/api/settings/([^/]+)$')


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # ── /api/pages GET ────────────────────────────────────────────
    if path == '/api/pages' and method == 'GET':
        if not h._auth(): return True
        h._json(200, topo_get_pages())
        return True

    # ── /api/nodes GET ────────────────────────────────────────────
    if path == '/api/nodes' and method == 'GET':
        if not h._auth(): return True
        from urllib.parse import urlparse, parse_qs
        _qs     = urlparse(h.path).query
        _page_id = None
        if _qs:
            _pv = parse_qs(_qs).get('page')
            if _pv:
                try: _page_id = int(_pv[0])
                except ValueError: pass
        h._json(200, topo_get_nodes(_page_id))
        return True

    # ── /api/links GET ────────────────────────────────────────────
    if path == '/api/links' and method == 'GET':
        if not h._auth(): return True
        from urllib.parse import urlparse, parse_qs
        _qs     = urlparse(h.path).query
        _page_id = None
        if _qs:
            _pv = parse_qs(_qs).get('page')
            if _pv:
                try: _page_id = int(_pv[0])
                except ValueError: pass
        h._json(200, topo_get_links(_page_id))
        return True

    # ── /api/groups GET ───────────────────────────────────────────
    if path == '/api/groups' and method == 'GET':
        if not h._auth(): return True
        from urllib.parse import urlparse, parse_qs
        _qs     = urlparse(h.path).query
        _page_id = None
        if _qs:
            _pv = parse_qs(_qs).get('page')
            if _pv:
                try: _page_id = int(_pv[0])
                except ValueError: pass
        h._json(200, topo_get_groups(_page_id))
        return True

    # ── /api/settings/{key} GET ───────────────────────────────────
    m = _RE_TOPO_SETTING.match(path)
    if m and method == 'GET':
        if not h._auth(): return True
        row = topo_get_setting(m.group(1))
        h._json(200, row) if row else h._json(404, {'error': 'not found'})
        return True

    # ── /api/pages POST ───────────────────────────────────────────
    if path == '/api/pages' and method == 'POST':
        user, _ = h._require("operator")
        if not user: return True
        if not body.get('name'):
            h._json(400, {'error': 'name required'}); return True
        _pg = topo_insert_page(body['name'])
        db_log_audit(user, h.client_address[0], 'ntm_page_create', body['name'])
        h._json(201, _pg)
        return True

    # ── /api/nodes POST ───────────────────────────────────────────
    if path == '/api/nodes' and method == 'POST':
        user, _ = h._require("operator")
        if not user: return True
        if not body.get('name') or not body.get('type'):
            h._json(400, {'error': 'name and type required'}); return True
        node = topo_insert_node(
            body['name'], body['type'],
            body.get('x', 200), body.get('y', 200),
            body.get('properties', {}), body.get('page_id', 1),
        )
        db_log_audit(user, h.client_address[0], 'ntm_node_create', body['name'])
        h._json(201, node)
        return True

    # ── /api/links POST ───────────────────────────────────────────
    if path == '/api/links' and method == 'POST':
        user, _ = h._require("operator")
        if not user: return True
        link = topo_insert_link(
            body['source_id'], body['target_id'],
            body.get('label', ''), body.get('link_type', 'trunk'),
            body.get('page_id', 1),
        )
        db_log_audit(user, h.client_address[0], 'ntm_link_create', f"{body['source_id']}\u2192{body['target_id']}")
        h._json(201, link)
        return True

    # ── /api/groups POST ──────────────────────────────────────────
    if path == '/api/groups' and method == 'POST':
        user, _ = h._require("operator")
        if not user: return True
        if not body.get('name'):
            h._json(400, {'error': 'name required'}); return True
        grp = topo_insert_group(
            body['name'], body.get('color', '#00d4ff'),
            body.get('x', 100), body.get('y', 100),
            body.get('w', 300), body.get('h', 200),
            body.get('page_id', 1),
        )
        db_log_audit(user, h.client_address[0], 'ntm_group_create', body['name'])
        h._json(201, grp)
        return True

    # ── PUT topology items ────────────────────────────────────────
    if method == 'PUT':
        m = _RE_TOPO_PAGE.match(path)
        if m:
            user, _ = h._require("operator")
            if not user: return True
            page = topo_update_page(int(m.group(1)), body.get('name', ''))
            if page:
                db_log_audit(user, h.client_address[0], 'ntm_page_update', body.get('name', ''))
                h._json(200, page)
            else:
                h._json(404, {'error': 'not found'})
            return True

        m = _RE_TOPO_NODE.match(path)
        if m:
            user, _ = h._require("operator")
            if not user: return True
            node = topo_update_node(
                int(m.group(1)), body.get('name'), body.get('type'),
                body.get('x'), body.get('y'), body.get('properties'),
            )
            if node:
                db_log_audit(user, h.client_address[0], 'ntm_node_update', str(m.group(1)))
                h._json(200, node)
            else:
                h._json(404, {'error': 'not found'})
            return True

        m = _RE_TOPO_LINK.match(path)
        if m:
            user, _ = h._require("operator")
            if not user: return True
            src = body.get('source_id')
            tgt = body.get('target_id')
            link = topo_update_link(
                int(m.group(1)), body.get('label', ''), body.get('link_type', 'trunk'),
                source_id=int(src) if src is not None else None,
                target_id=int(tgt) if tgt is not None else None,
            )
            if link:
                db_log_audit(user, h.client_address[0], 'ntm_link_update', str(m.group(1)))
                h._json(200, link)
            else:
                h._json(404, {'error': 'not found'})
            return True

        m = _RE_TOPO_GROUP.match(path)
        if m:
            user, _ = h._require("operator")
            if not user: return True
            grp = topo_update_group(
                int(m.group(1)), body.get('name'), body.get('color'),
                body.get('x'), body.get('y'), body.get('w'), body.get('h'),
            )
            if grp:
                db_log_audit(user, h.client_address[0], 'ntm_group_update', str(m.group(1)))
                h._json(200, grp)
            else:
                h._json(404, {'error': 'not found'})
            return True

        m = _RE_TOPO_SETTING.match(path)
        if m:
            user, _ = h._require("operator")
            if not user: return True
            topo_upsert_setting(m.group(1), body.get('value'))
            h._json(200, {'key': m.group(1), 'value': body.get('value')})
            return True

    # ── PATCH /api/settings/{key} ─────────────────────────────────
    if method == 'PATCH':
        m = _RE_TOPO_SETTING.match(path)
        if m:
            user, _ = h._require("operator")
            if not user: return True
            topo_upsert_setting(m.group(1), body.get('value'))
            h._json(200, {'key': m.group(1), 'value': body.get('value')})
            return True

    # ── DELETE topology items ─────────────────────────────────────
    if method == 'DELETE':
        m = _RE_TOPO_PAGE.match(path)
        if m:
            user, _ = h._require("operator")
            if not user: return True
            _pg_id   = int(m.group(1))
            _pg_name = next(
                (pg['name'] for pg in topo_get_pages() if pg['id'] == _pg_id),
                str(_pg_id)
            )
            topo_delete_page(_pg_id)
            db_log_audit(user, h.client_address[0], 'ntm_page_delete', _pg_name)
            h._json(200, {'ok': True})
            return True

        m = _RE_TOPO_NODE.match(path)
        if m:
            user, _ = h._require("operator")
            if not user: return True
            _node_id = int(m.group(1))
            topo_delete_node(_node_id)
            db_log_audit(user, h.client_address[0], 'ntm_node_delete', str(_node_id))
            h._json(200, {'ok': True})
            return True

        m = _RE_TOPO_LINK.match(path)
        if m:
            user, _ = h._require("operator")
            if not user: return True
            _link_id = int(m.group(1))
            topo_delete_link(_link_id)
            db_log_audit(user, h.client_address[0], 'ntm_link_delete', str(_link_id))
            h._json(200, {'ok': True})
            return True

        m = _RE_TOPO_GROUP.match(path)
        if m:
            user, _ = h._require("operator")
            if not user: return True
            _grp_id = int(m.group(1))
            topo_delete_group(_grp_id)
            db_log_audit(user, h.client_address[0], 'ntm_group_delete', str(_grp_id))
            h._json(200, {'ok': True})
            return True

    return False
