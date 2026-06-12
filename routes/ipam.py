"""
routes/ipam.py — IPAM (IP address management) API endpoints.

GET    /api/ipam/subnets                              → list all subnets (viewer)
POST   /api/ipam/subnets                              → add subnet  {cidr, name}  (operator)
DELETE /api/ipam/subnets/<id>                         → remove subnet + allocations (operator)
GET    /api/ipam/subnets/<id>/ips                     → get all allocations for subnet (viewer)
POST   /api/ipam/subnets/<id>/dns/refresh             → trigger background DNS resolution (operator)
POST   /api/ipam/subnets/<id>/scan                    → kick off active-host scan, auto-populates
                                                         ip_allocations with kind='discovered' (operator)
GET    /api/ipam/subnets/<id>/scan/<scan_id>          → poll scan progress (viewer)
POST   /api/ipam/subnets/<id>/scan/<scan_id>/cancel   → cancel running scan (operator)
PUT    /api/ipam/ips/<subnet_id>/<ip>                 → set IP name {name} (operator)
"""

import ipaddress
import threading

from core.config import (
    _RE_IPAM_SUBNETS,
    _RE_IPAM_SUBNET,
    _RE_IPAM_SUBNET_IPS,
    _RE_IPAM_SUBNET_DNS,
    _RE_IPAM_SUBNET_SCAN,
    _RE_IPAM_SUBNET_SCAN_POLL,
    _RE_IPAM_SUBNET_SCAN_CANCEL,
    _RE_IPAM_SEARCH,
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
    db_set_subnet_last_scan,
    db_get_allocations,
    db_upsert_allocation,
    db_clear_allocation,
    db_mark_allocations_stale,
    apply_subnet_scan_results,
    db_search_allocations,
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


# ── Active-host scan (auto-populate allocations) ───────────────────────────
# Tracks at most one scan per subnet at a time. Calling /scan again while a
# scan is running returns the existing scan_id instead of starting a duplicate.
_active_scans       = {}   # subnet_id → scan_id of an in-progress active-host scan
_active_scans_mutex = threading.Lock()


def _scan_apply_worker(subnet_id: int, scan_id: str, user: str):
    """Background thread: polls the discovery scan and, when it finishes,
    upserts the alive IPs into ip_allocations with kind='discovered'.

    Dedup rules — runs against the *current* allocations at apply time so a
    concurrent manual edit isn't overwritten:
      • allocation has a device_id           → skip (already monitored)
      • allocation.kind in {gateway,reserved,
        conflict,switch,backbone,core}       → skip (user-tagged)
      • allocation.modified_by is a real user
        and the name is non-empty            → skip (manual entry)
      • everything else                      → upsert kind='discovered',
                                                name=hostname or existing name
    """
    from monitoring.subnet_discovery import get_scan
    poll_interval = 1.0
    deadline_s = 30 * 60  # hard cap so a stuck scan can't leak the thread
    started = _now_monotonic()
    try:
        while True:
            if _now_monotonic() - started > deadline_s:
                log.warning(f"IPAM scan apply: deadline exceeded for subnet={subnet_id} scan={scan_id}")
                return
            st = get_scan(scan_id)
            if not st:
                # Scan was purged from the registry before we could read it.
                log.warning(f"IPAM scan apply: scan {scan_id} vanished before completion")
                return
            state = st.get('state')
            if state == 'running':
                import time as _t
                _t.sleep(poll_interval)
                continue
            if state in ('done', 'cancelled', 'error'):
                if state != 'done':
                    log.info(f"IPAM scan apply: scan {scan_id} ended state={state}; nothing to apply")
                    return
                results = st.get('results') or []
                _apply_scan_results(subnet_id, results, user)
                return
            # Unknown state — bail without applying.
            log.warning(f"IPAM scan apply: scan {scan_id} unknown state={state!r}")
            return
    finally:
        with _active_scans_mutex:
            # Only clear if we still own the slot; a later scan may have replaced
            # us if the registry was racy.
            if _active_scans.get(subnet_id) == scan_id:
                _active_scans.pop(subnet_id, None)


def _apply_scan_results(subnet_id: int, results: list, user: str) -> None:
    """Thin shim around the shared db.ipam.apply_subnet_scan_results — logs the
    per-call stats so the manual-scan flow shows up as a single audit-style
    line in the server log. The actual upsert + stale logic lives in db.ipam
    so monitoring/auto_discovery.py can share it without a route dependency."""
    stats = apply_subnet_scan_results(subnet_id, results, user)
    log.info(f"IPAM scan apply: subnet={subnet_id} "
             f"alive={stats['alive']} upserted={stats['upserted']} "
             f"skipped={stats['skipped']} staled={stats['staled']} by={user!r}")


def _now_monotonic() -> float:
    import time as _t
    return _t.monotonic()


def _ipam_status_label(kind: str, name: str) -> str:
    """Human-readable allocation status, mirroring the IPAM grid badge."""
    return {
        'gateway':    'Gateway',
        'reserved':   'Reserved',
        'conflict':   'Conflict',
        'stale':      'Stale',
        'discovered': 'Discovered',
    }.get((kind or '').lower(), 'Used' if (name or '').strip() else 'Free')


def _build_ipam_csv():
    """Assemble the IPAM export CSV — every allocation across every subnet.

    Returns (utf8_bytes_with_bom, filename, row_count). Columns:
    Site, Subnet, IP Address, Name, DNS, Status, Licenses, Modified By,
    Last Modified. UTF-8 BOM so Excel renders non-ASCII names/sites.
    """
    import csv, io, datetime
    from db.helpers import db_query

    rows = [dict(r) for r in db_query(
        'main',
        "SELECT s.site AS site, s.cidr AS cidr, a.ip AS ip, "
        "COALESCE(a.name,'') AS name, COALESCE(a.dns_name,'') AS dns_name, "
        "COALESCE(a.kind,'') AS kind, COALESCE(a.modified_by,'') AS modified_by, "
        "COALESCE(a.modified_at,0) AS modified_at, "
        "COALESCE(a.device_id,'') AS device_id "
        "FROM ip_allocations a JOIN ipam_subnets s ON a.subnet_id = s.id"
    )]

    # device_id → ["LicName (expiry; status)", …]. Best-effort: the table may
    # be absent on a very old DB — an empty Licenses column is fine then.
    lic_map = {}
    try:
        for _r in db_query(
                'main',
                "SELECT did, COALESCE(license_name,'') AS license_name, "
                "COALESCE(expiry_date,'') AS expiry_date, "
                "COALESCE(last_status,'') AS last_status "
                "FROM device_licenses ORDER BY did, expiry_date"):
            r = dict(_r)
            label = (r['license_name'] or '').strip()
            if not label:
                continue
            extra = '; '.join(p for p in (str(r['expiry_date'] or '').strip(),
                                          str(r['last_status'] or '').strip()) if p)
            lic_map.setdefault(r['did'], []).append(
                f"{label} ({extra})" if extra else label)
    except Exception as e:
        log.warning(f"IPAM export: licenses unavailable: {e}")

    def _ip_key(ip):
        try:
            return (0, int(ipaddress.ip_address(ip)))
        except ValueError:
            return (1, 0)
    rows.sort(key=lambda r: ((r['site'] or '').lower(),
                             (r['cidr'] or ''), _ip_key(r['ip'])))

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(['Site', 'Subnet', 'IP Address', 'Name', 'DNS', 'Status',
                'Licenses', 'Modified By', 'Last Modified'])
    for r in rows:
        ma = r.get('modified_at') or 0
        try:
            last_mod = (datetime.datetime.utcfromtimestamp(float(ma))
                        .strftime('%Y-%m-%d %H:%M:%S') if ma else '')
        except (TypeError, ValueError, OSError):
            last_mod = ''
        w.writerow([
            r['site'] or '', r['cidr'] or '', r['ip'] or '',
            r['name'] or '', r['dns_name'] or '',
            _ipam_status_label(r['kind'], r['name']),
            '; '.join(lic_map.get(r['device_id'], [])),
            r['modified_by'] or '', last_mod,
        ])
    data = (chr(0xFEFF) + buf.getvalue()).encode('utf-8')   # BOM for Excel
    fname = f"pingwatch-ipam-{datetime.date.today().isoformat()}.csv"
    return data, fname, len(rows)


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""
    # ── GET /api/sites ───────────────────────────────────────────
    # Returns sorted UNION of distinct, non-empty site names from
    # ipam_subnets, devices, AND the sites metadata table (Live Map sites
    # added via /api/sites/meta should show up in the device-editor /
    # group-editor / alert-profile-editor autocomplete even when no device
    # has been assigned to them yet).
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
        try:
            for r in db_query('main', "SELECT name FROM sites"):
                v = (r['name'] or '').strip()
                if v: sites.add(v)
        except Exception as e:
            log.warning(f"/api/sites sites query failed: {e}")
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

    # ── GET /api/ipam/export → CSV of every allocation, all subnets ──
    if path == '/api/ipam/export' and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        try:
            data, fname, n = _build_ipam_csv()
        except Exception as e:
            h._error(500, 'IPAM export failed', e, context='ipam_export')
            return True
        db_log_audit(user, h.client_address[0], 'ipam_export', f'{n} allocations')
        h.send_response(200)
        h.send_header('Content-Type', 'text/csv; charset=utf-8')
        h.send_header('Content-Disposition', f'attachment; filename="{fname}"')
        h.send_header('Content-Length', str(len(data)))
        h.send_header('Cache-Control', 'no-store')
        h.end_headers()
        h.wfile.write(data)
        return True

    # ── GET /api/ipam/search?q=… ──────────────────────────────────
    # Backs the Ctrl+K palette's IP suggestions. Matches the query against IP
    # prefix, name (substring), and dns_name (substring) across every subnet.
    # Capped at 50 results — palette UI only renders ~10–20 anyway.
    if _RE_IPAM_SEARCH.match(path) and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(h.path).query)
        q = (qs.get('q', [''])[0] or '').strip()[:80]
        if not q:
            h._json(200, {'results': []})
            return True
        results = db_search_allocations(q, limit=50)
        h._json(200, {'results': results})
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
        # VLAN ID (optional). IEEE 802.1Q valid range is 1..4094; 0 = untagged.
        # Out-of-range / non-int falls back to 0 silently so the subnet still
        # gets created — frontend already clamps in the input, so this is just
        # defense-in-depth for API consumers.
        try:
            vlan_id = int(body.get('vlan') or 0)
        except (TypeError, ValueError):
            vlan_id = 0
        if not (0 <= vlan_id <= 4094):
            vlan_id = 0
        try:
            new_id = db_add_subnet(canonical, name, user, site=site, vlan=vlan_id)
        except ValueError as e:
            # ValueError from db_add_subnet carries a curated user-facing
            # message (e.g. "Subnet '10.0.0.0/24' already exists"). Pull the
            # arg directly rather than str(e) so we never accidentally surface
            # an unrelated exception that bubbles through this path.
            msg = e.args[0] if e.args else "subnet already exists"
            h._json(409, {'error': msg}); return True
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

        if 'vlan' in body:
            try:
                new_vlan = int(body.get('vlan') or 0)
            except (TypeError, ValueError):
                new_vlan = 0
            if not (0 <= new_vlan <= 4094):
                new_vlan = 0
            if new_vlan != int(sub.get('vlan') or 0):
                updates['vlan'] = new_vlan
                audit_parts.append(f"vlan={new_vlan or 'untagged'}")

        if 'auto_discover' in body:
            new_ad = 1 if body.get('auto_discover') else 0
            if new_ad != int(sub.get('auto_discover') or 0):
                updates['auto_discover'] = new_ad
                audit_parts.append(f"auto_discover={bool(new_ad)}")

        if 'auto_host_scan' in body:
            new_ahs = 1 if body.get('auto_host_scan') else 0
            if new_ahs != int(sub.get('auto_host_scan') or 0):
                updates['auto_host_scan'] = new_ahs
                audit_parts.append(f"auto_host_scan={bool(new_ahs)}")

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

    # ── POST /api/ipam/subnets/<id>/scan ──────────────────────────
    # Kicks off an active-host scan for the subnet and registers an applier
    # thread that writes alive IPs into ip_allocations with kind='discovered'.
    m = _RE_IPAM_SUBNET_SCAN.match(path)
    if m and method == 'POST':
        user, _ = h._require('operator')
        if not user: return True
        subnet_id = int(m.group(1))
        sub = db_get_subnet(subnet_id)
        if not sub:
            h._json(404, {'error': 'Subnet not found'}); return True
        with _active_scans_mutex:
            existing = _active_scans.get(subnet_id)
            if existing:
                # Return the in-flight scan_id so the UI can pick up polling
                # instead of failing with a 409.
                h._json(200, {'ok': True, 'scan_id': existing, 'already_running': True})
                return True
        # Distributed probes: a subnet whose site is bound to a probe scans
        # FROM that probe — central usually can't reach branch ranges. The
        # registry entry looks identical to a local scan, so the poll
        # endpoint and the apply worker below need no changes.
        from core.probe_assign import site_probe
        _probe_id = site_probe((sub.get('site') or '').strip())
        if _probe_id:
            from db.probes import db_get_probe, db_create_task
            _probe = db_get_probe(_probe_id)
            if not _probe or _probe.get('status') != 'enrolled':
                h._json(409, {'error': "this subnet's site is bound to a probe "
                                       "that is not enrolled — scan unavailable"})
                return True
            from monitoring.subnet_discovery import (register_remote_scan,
                                                     bind_remote_scan_task,
                                                     complete_remote_scan)
            scan_id, err = register_remote_scan(sub['cidr'], 'ping', _probe_id)
            if not scan_id:
                h._json(400, {'error': err or 'scan start failed'}); return True
            import json as _json
            _tid = db_create_task(_probe_id, 'ipam_scan',
                                  _json.dumps({'scan_id': scan_id,
                                               'cidr': sub['cidr'],
                                               'mode': 'ping',
                                               'subnet_id': subnet_id}),
                                  user)
            if not _tid:
                complete_remote_scan(scan_id, [], error='failed to queue task')
                h._json(500, {'error': 'failed to queue scan task'}); return True
            bind_remote_scan_task(scan_id, _tid)
        else:
            # Reuse the discovery scanner with skip_monitored=False so already-
            # monitored IPs still get refreshed in the allocation grid. Mode is
            # 'ping' (cheaper, no port-scan enrichment) — IPAM only needs the
            # ip + hostname; sensor-suggestion data isn't displayed in the grid.
            from monitoring.subnet_discovery import start_scan
            scan_id, err = start_scan(sub['cidr'], skip_monitored=False, mode='ping')
            if not scan_id:
                log.warning(f"IPAM scan start failed: subnet={subnet_id} cidr={sub['cidr']!r} err={err!r}")
                h._json(400, {'error': err or 'scan start failed'}); return True
        with _active_scans_mutex:
            _active_scans[subnet_id] = scan_id
        t = threading.Thread(
            target=_scan_apply_worker,
            args=(subnet_id, scan_id, user),
            daemon=True,
            name=f"pw-ipam-scan-apply-{subnet_id}",
        )
        t.start()
        db_log_audit(user, h.client_address[0], 'ipam_subnet_scan',
                     f"{sub['cidr']} scan_id={scan_id}")
        log.info(f"IPAM scan started: subnet={subnet_id} cidr={sub['cidr']!r} "
                 f"scan_id={scan_id} by={user!r}")
        h._json(202, {'ok': True, 'scan_id': scan_id})
        return True

    # ── GET /api/ipam/subnets/<id>/scan/<scan_id> ─────────────────
    m = _RE_IPAM_SUBNET_SCAN_POLL.match(path)
    if m and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        subnet_id = int(m.group(1))
        scan_id   = m.group(2)
        from monitoring.subnet_discovery import get_scan
        st = get_scan(scan_id)
        if not st:
            h._json(404, {'error': 'scan not found'}); return True
        # Return a slim view — we don't surface the result rows (the apply
        # thread writes them straight to the DB). Frontend just needs progress
        # + a "done" signal to trigger an allocation reload.
        h._json(200, {
            'scan_id':     st.get('scan_id'),
            'state':       st.get('state'),
            'phase':       st.get('phase'),
            'progress':    st.get('progress'),
            'started_at':  st.get('started_at'),
            'finished_at': st.get('finished_at'),
            'error':       st.get('error') or '',
        })
        return True

    # ── POST /api/ipam/subnets/<id>/scan/<scan_id>/cancel ────────
    m = _RE_IPAM_SUBNET_SCAN_CANCEL.match(path)
    if m and method == 'POST':
        user, _ = h._require('operator')
        if not user: return True
        subnet_id = int(m.group(1))
        scan_id   = m.group(2)
        from monitoring.subnet_discovery import cancel_scan, get_scan
        ok = cancel_scan(scan_id)
        if ok:
            # Remote scans: flip the agent task too so the probe aborts the
            # sweep (relayed via the checkin response's cancelled_tasks).
            _st = get_scan(scan_id)
            if _st and _st.get('task_id'):
                from db.probes import db_set_task_state
                db_set_task_state(int(_st['task_id']), 'cancelled')
            log.info(f"IPAM scan cancelled: subnet={subnet_id} scan_id={scan_id} by={user!r}")
        h._json(200, {'ok': bool(ok)})
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
