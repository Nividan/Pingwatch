"""
routes/ipam.py — IPAM (IP address management) API endpoints.

GET    /api/ipam/subnets                    → list all subnets (viewer)
POST   /api/ipam/subnets                    → add subnet  {cidr, name}  (operator)
DELETE /api/ipam/subnets/<id>               → remove subnet + allocations (operator)
GET    /api/ipam/subnets/<id>/ips           → get all allocations for subnet (viewer)
POST   /api/ipam/subnets/<id>/dns/refresh   → trigger background DNS resolution (operator)
PUT    /api/ipam/ips/<subnet_id>/<ip>       → set IP name {name} (operator)
"""

import ipaddress
import threading

from core.config import (
    _RE_IPAM_SUBNETS,
    _RE_IPAM_SUBNET,
    _RE_IPAM_SUBNET_IPS,
    _RE_IPAM_SUBNET_DNS,
    _RE_IPAM_IP,
    _RE_IPAM_AD_TOGGLE,
    _RE_TOPOLOGY_ROLES,
)
from core.logger import log
from core.validation import validate_host
from db import (
    _db_enqueue,
    db_log_audit,
    db_list_subnets,
    db_get_subnet,
    db_add_subnet,
    db_rename_subnet,
    db_delete_subnet,
    db_set_auto_discover,
    db_update_subnet,
    db_approve_first_scan,
    db_get_allocations,
    db_upsert_allocation,
    db_clear_allocation,
    db_get_device_roles,
)
from db.ipam import ipam_sync_subnet_add

# Largest subnet allowed: /9 = 8,388,606 hosts is already impractical;
# cap at /9 to prevent accidents (prefix must be >= 9).
_MIN_PREFIX = 9

# ── DNS resolution helpers ─────────────────────────────────────────────────

_dns_refresh_lock       = {}   # subnet_id → True when refresh is running
_dns_refresh_lock_mutex = threading.Lock()


def _resolve_dns(ip_str, timeout=2):
    """Reverse DNS lookup. Returns hostname string or '' on any failure.

    Uses a thread + Future.result(timeout) instead of socket.setdefaulttimeout()
    to avoid mutating the process-global socket timeout.
    """
    import socket
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(socket.gethostbyaddr, ip_str)
            return fut.result(timeout=timeout)[0]
    except Exception:
        return ''


def _dns_refresh_worker(subnet_id, ip_list):
    """Background thread: resolves PTR for every IP and caches results in DB."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from db.ipam import db_update_dns
    try:
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(_resolve_dns, ip): ip for ip in ip_list}
            for fut in as_completed(futures):
                ip = futures[fut]
                try:
                    dns = fut.result()
                    db_update_dns(subnet_id, ip, dns)
                except Exception:
                    pass
    finally:
        with _dns_refresh_lock_mutex:
            _dns_refresh_lock.pop(subnet_id, None)


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""
    # ── GET /api/sites ───────────────────────────────────────────
    # Returns sorted UNION of distinct, non-empty site names from both
    # ipam_subnets and devices. Used by autocomplete (device editor, alert
    # profile editor, maintenance window editor). Lives in ipam.py because
    # IPAM was the original home of the `site` concept; devices joined later.
    if path == '/api/sites' and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        from db.helpers import db_query
        sites = set()
        try:
            for r in db_query('main', "SELECT DISTINCT site FROM ipam_subnets WHERE site <> ''"):
                v = (r['site'] or '').strip()
                if v: sites.add(v)
        except Exception as e:
            log.warning(f"/api/sites ipam_subnets query failed: {e}")
        try:
            for r in db_query('main', "SELECT DISTINCT site FROM devices WHERE site <> ''"):
                v = (r['site'] or '').strip()
                if v: sites.add(v)
        except Exception as e:
            log.warning(f"/api/sites devices query failed: {e}")
        h._json(200, {'sites': sorted(sites, key=str.lower)})
        return True

    # ── GET /api/topology/roles ──────────────────────────────────
    # Returns {device_id: role} where role is 'switch' | 'gateway' | 'backbone'.
    # Powers the NTM Live auto-link renderer (subnet → switch → gateway →
    # backbone) and the device editor's Role dropdown. Source of truth is
    # ip_allocations.kind; we expose only role-relevant kinds.
    if _RE_TOPOLOGY_ROLES.match(path) and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        try:
            h._json(200, {'roles': db_get_device_roles()})
        except Exception as e:
            log.warning(f"/api/topology/roles failed: {e}")
            h._json(500, {'error': 'failed to load roles'})
        return True

    # ── GET /api/ipam/subnets ─────────────────────────────────────
    if _RE_IPAM_SUBNETS.match(path) and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        h._json(200, {'subnets': db_list_subnets()})
        return True

    # ── POST /api/ipam/subnets ────────────────────────────────────
    if _RE_IPAM_SUBNETS.match(path) and method == 'POST':
        user, _ = h._require('operator')
        if not user: return True
        cidr = (body.get('cidr') or '').strip()
        name = (body.get('name') or '').strip()[:80]
        site = (body.get('site') or '').strip()[:40]
        if not cidr:
            log.warning(f"IPAM add subnet rejected: empty CIDR (user={user!r})")
            h._json(400, {'error': 'cidr is required'}); return True
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            log.warning(f"IPAM add subnet rejected: invalid CIDR {cidr!r} (user={user!r})")
            h._json(400, {'error': f'Invalid CIDR: {cidr!r}'}); return True
        if net.prefixlen < _MIN_PREFIX:
            log.warning(f"IPAM add subnet rejected: prefix too large /{net.prefixlen} "
                        f"for {cidr!r} (user={user!r})")
            h._json(400, {
                'error': f'Prefix /{net.prefixlen} is too large (minimum /{_MIN_PREFIX})'
            }); return True
        # Normalise to network address (e.g. 192.168.1.5/24 → 192.168.1.0/24)
        canonical = str(net)
        try:
            new_id = db_add_subnet(canonical, name, user, site=site)
        except ValueError as e:
            h._json(409, {'error': str(e)}); return True
        _sid, _cidr = new_id, canonical
        _db_enqueue(lambda: ipam_sync_subnet_add(_sid, _cidr))
        db_log_audit(user, h.client_address[0], 'ipam_subnet_add', canonical)
        h._json(201, {'ok': True, 'id': new_id, 'cidr': canonical})
        return True

    # ── POST /api/ipam/subnet/<id>/auto-discover ─────────────────
    # Legacy narrow endpoint; kept for back-compat. New UI uses the
    # consolidated PATCH below.
    m = _RE_IPAM_AD_TOGGLE.match(path)
    if m and method == 'POST':
        user, _ = h._require('operator')
        if not user: return True
        try:
            subnet_id = int(m.group(1))
        except (TypeError, ValueError):
            h._json(400, {'error': 'invalid subnet id'}); return True
        sub = db_get_subnet(subnet_id)
        if not sub:
            h._json(404, {'error': 'Subnet not found'}); return True
        enabled = bool(body.get('enabled'))
        if not db_set_auto_discover(subnet_id, enabled):
            h._json(500, {'error': 'update failed'}); return True
        db_log_audit(user, h.client_address[0],
                     'ipam_auto_discover_toggle',
                     f"{sub.get('cidr', '')} → {enabled}")
        h._json(200, {'ok': True, 'enabled': enabled})
        return True

    # ── PATCH /api/ipam/subnets/<id> — multi-field edit ───────────
    # Accepts any subset of: name, site, auto_discover, dns_server. The
    # editor modal builds a single payload with only changed fields.
    m = _RE_IPAM_SUBNET.match(path)
    if m and method == 'PATCH':
        user, _ = h._require('operator')
        if not user: return True
        subnet_id = int(m.group(1))
        sub = db_get_subnet(subnet_id)
        if not sub:
            h._json(404, {'error': 'Subnet not found'}); return True

        updates: dict = {}
        audit_parts: list = []

        if 'name' in body:
            new_name = (body.get('name') or '').strip()[:80]
            if new_name != (sub.get('name') or ''):
                updates['name'] = new_name
                audit_parts.append(f"name={new_name!r}")

        if 'site' in body:
            new_site = (body.get('site') or '').strip()[:40]
            if new_site != (sub.get('site') or ''):
                updates['site'] = new_site
                audit_parts.append(f"site={new_site!r}")

        if 'auto_discover' in body:
            new_ad = 1 if body.get('auto_discover') else 0
            if new_ad != int(sub.get('auto_discover') or 0):
                updates['auto_discover'] = new_ad
                audit_parts.append(f"auto_discover={bool(new_ad)}")

        if 'dns_server' in body:
            raw = (body.get('dns_server') or '').strip()
            # Allow empty (use system resolver) or a bare IP / hostname; reject
            # anything else so typos become a 400 instead of a broken scan.
            if raw:
                try:
                    validate_host(raw)
                except ValueError as ve:
                    log.warning(f"IPAM dns_server rejected: {ve}")
                    h._json(400, {'error': 'dns_server must be a valid IP or hostname'}); return True
            if raw != (sub.get('dns_server') or ''):
                updates['dns_server'] = raw
                audit_parts.append(f"dns_server={raw or '<system>'}")

        if updates:
            if not db_update_subnet(subnet_id, updates):
                h._json(500, {'error': 'update failed'}); return True
            db_log_audit(user, h.client_address[0], 'ipam_subnet_edit',
                         f"{sub['cidr']} — {'; '.join(audit_parts)}")

        if body.get('approve_first_scan'):
            db_approve_first_scan(subnet_id)
            db_log_audit(user, h.client_address[0], 'auto_discovery_approve_first_scan',
                         sub.get('cidr', ''))

        h._json(200, {'ok': True, 'updated': list(updates.keys())})
        return True

    # ── DELETE /api/ipam/subnets/<id> ─────────────────────────────
    m = _RE_IPAM_SUBNET.match(path)
    if m and method == 'DELETE':
        user, _ = h._require('operator')
        if not user: return True
        subnet_id = int(m.group(1))
        sub = db_get_subnet(subnet_id)
        if not sub:
            log.warning(f"IPAM delete subnet: id={subnet_id} not found (user={user!r})")
            h._json(404, {'error': 'Subnet not found'}); return True
        db_delete_subnet(subnet_id)
        db_log_audit(user, h.client_address[0], 'ipam_subnet_delete', sub['cidr'])
        h._json(200, {'ok': True})
        return True

    # ── GET /api/ipam/subnets/<id>/ips ───────────────────────────
    m = _RE_IPAM_SUBNET_IPS.match(path)
    if m and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        subnet_id = int(m.group(1))
        sub = db_get_subnet(subnet_id)
        if not sub:
            log.warning(f"IPAM get IPs: subnet id={subnet_id} not found (user={user!r})")
            h._json(404, {'error': 'Subnet not found'}); return True
        allocs = db_get_allocations(subnet_id)
        h._json(200, {'subnet': sub, 'allocations': allocs})
        return True

    # ── POST /api/ipam/subnets/<id>/dns/refresh ───────────────
    m = _RE_IPAM_SUBNET_DNS.match(path)
    if m and method == 'POST':
        user, _ = h._require('operator')
        if not user: return True
        subnet_id = int(m.group(1))
        sub = db_get_subnet(subnet_id)
        if not sub:
            h._json(404, {'error': 'Subnet not found'}); return True
        with _dns_refresh_lock_mutex:
            if _dns_refresh_lock.get(subnet_id):
                h._json(409, {'error': 'refresh already in progress'}); return True
            _dns_refresh_lock[subnet_id] = True
        # Build host IP list from CIDR
        try:
            net = ipaddress.ip_network(sub['cidr'], strict=False)
        except ValueError:
            with _dns_refresh_lock_mutex:
                _dns_refresh_lock.pop(subnet_id, None)
            h._json(400, {'error': 'Invalid subnet CIDR'}); return True
        prefix = net.prefixlen
        if prefix == 32:
            ip_list = [str(net.network_address)]
        elif prefix == 31:
            ip_list = [str(net.network_address), str(net.broadcast_address)]
        else:
            ip_list = [str(ip) for ip in net.hosts()]
        t = threading.Thread(
            target=_dns_refresh_worker, args=(subnet_id, ip_list), daemon=True
        )
        t.start()
        log.info(f"IPAM DNS refresh started: subnet {sub['cidr']!r} ({len(ip_list)} IPs) by {user!r}")
        h._json(202, {'ok': True, 'total': len(ip_list)})
        return True

    # ── PUT /api/ipam/ips/<subnet_id>/<ip> ───────────────────────
    m = _RE_IPAM_IP.match(path)
    if m and method == 'PUT':
        user, _ = h._require('operator')
        if not user: return True
        subnet_id = int(m.group(1))
        ip_str    = m.group(2)
        name      = (body.get('name') or '').strip()[:120]
        # Optional `kind` — '' (default), 'gateway', 'switch', 'backbone',
        # 'core', 'reserved', 'conflict'. Anything not in the whitelist falls
        # back to ''. The first four anchor NTM Live auto-link rendering with
        # tier model: switch (access) -> backbone (aggregation) -> core
        # (central L3) -> gateway (edge/FW); cross-site mesh at core level.
        _KIND_OK  = {'', 'gateway', 'switch', 'backbone', 'core', 'reserved', 'conflict'}
        kind_raw  = (body.get('kind') or '').strip().lower()
        kind      = kind_raw if kind_raw in _KIND_OK else ''
        # Only pass kind to the upsert if the client included the key, so a
        # name-only PUT doesn't wipe an existing gateway/reserved tag.
        kind_arg  = kind if 'kind' in body else None

        # Validate subnet exists and IP belongs to it
        sub = db_get_subnet(subnet_id)
        if not sub:
            log.warning(f"IPAM assign IP: subnet id={subnet_id} not found (user={user!r}, ip={ip_str!r})")
            h._json(404, {'error': 'Subnet not found'}); return True
        try:
            ip_obj  = ipaddress.ip_address(ip_str)
            net_obj = ipaddress.ip_network(sub['cidr'], strict=False)
            if ip_obj not in net_obj:
                raise ValueError
        except ValueError:
            log.warning(f"IPAM assign IP rejected: {ip_str!r} not in subnet {sub['cidr']!r} (user={user!r})")
            h._json(400, {'error': f'{ip_str!r} is not in subnet {sub["cidr"]!r}'}); return True

        if name or kind:
            log.debug(f"IPAM: {user!r} assigned {ip_str} → {name!r} kind={kind!r} (subnet={sub['cidr']!r})")
            db_upsert_allocation(subnet_id, ip_str, name, user, device_id='', kind=kind_arg)
        else:
            log.debug(f"IPAM: {user!r} cleared {ip_str} (subnet={sub['cidr']!r})")
            db_clear_allocation(subnet_id, ip_str)
        h._json(200, {'ok': True})
        return True

    return False
