"""
routes/devices.py — Device and sensor CRUD + port-scan endpoint.

Handles: /api/devices (GET), /api/device (POST), /api/devices/{did} (GET/PATCH/DELETE),
         /api/devices/{did}/(start|stop) (POST), /api/devices/{did}/scan (POST),
         /api/sensors/{did} (POST), /api/sensors/{did}/{sid} (PATCH/DELETE),
         /api/sensors/{did}/{sid}/(start|stop) (POST),
         /api/sensors/{did}/{sid}/history (GET), /api/sensors/{did}/{sid}/summary (GET),
         /api/devices/{did}/logs (GET/DELETE), /api/sensors/{did}/{sid}/logs (DELETE),
         /api/start (POST), /api/stop (POST).
"""

import re
import re as _re_mod
import threading
import time as _time
from urllib.parse import urlparse, parse_qs

_RE_SENSOR_ANOMALY_RESET = _re_mod.compile(r"^/api/sensors/([^/]+)/([^/]+)/anomaly/reset$")

import core.app_state as app_state
from core.config import (
    _RE_DEVICE, _RE_DEVICE_ACTION, _RE_DEVICE_LOGS, _RE_DEVICE_SIP,
    _RE_SENSOR, _RE_SENSOR_ACTION, _RE_SENSOR_ITEM,
    _RE_SENSOR_HISTORY, _RE_SENSOR_SUMMARY, _RE_DEVICE_SCAN,
    _RE_SENSOR_LOGS, _RE_AVAILABILITY, _RE_DEV_GROUP_MUTE,
    _RE_DEV_GROUPS_MUTED, _RE_DEVICE_ROLE,
)
from db     import (
    _db_enqueue, db_save, db_log_audit,
    db_load_err_logs, db_clear_err_logs, db_clear_sensor_err_logs,
    db_clear_device_traps, db_load_history, db_load_summary, db_load_availability,
    db_resolve_events_by_sensor, db_resolve_flaps_by_sensor,
    db_clear_stage_state_for_sensor,
    db_reset_anomaly_baseline,
)
from db.ipam import ipam_sync_device_add, ipam_sync_device_update, ipam_sync_device_delete, db_set_device_role
from monitoring.network_map import topo_prune_pw_links
from core.logger import log
from monitoring.probes import probe_ping, probe_tcp, probe_http, probe_tls, probe_banner

_vmware_ssl_warned = set()

# ── Port-scan target list ─────────────────────────────────────────
_SCAN_TARGETS = [
    {"name": "Ping",       "stype": "ping",   "port": None,  "tout": 3},
    {"name": "FTP",        "stype": "banner", "port": 21,    "tout": 2},
    {"name": "SSH",        "stype": "banner", "port": 22,    "tout": 2},
    {"name": "SMTP",       "stype": "tcp",    "port": 25,    "tout": 2},
    {"name": "DNS",        "stype": "tcp",    "port": 53,    "tout": 2},
    {"name": "HTTP",       "stype": "http",   "port": 80,    "tout": 4},
    {"name": "HTTPS/TLS",  "stype": "tls",    "port": 443,   "tout": 4},
    {"name": "RDP",        "stype": "tcp",    "port": 3389,  "tout": 2},
    {"name": "MySQL",      "stype": "tcp",    "port": 3306,  "tout": 2},
    {"name": "PostgreSQL", "stype": "tcp",    "port": 5432,  "tout": 2},
    {"name": "Redis",      "stype": "banner", "port": 6379,  "tout": 2},
    {"name": "MongoDB",    "stype": "tcp",    "port": 27017, "tout": 2},
    {"name": "LDAP",       "stype": "tcp",    "port": 389,   "tout": 2},
    {"name": "HTTP-Alt",   "stype": "http",   "port": 8080,  "tout": 4},
    {"name": "HTTPS-Alt",  "stype": "tls",    "port": 8443,  "tout": 4},
]

STATE = app_state.STATE

_SCAN_DEFAULTS_STR = "ping,21,22,25,53,80,443,3389,3306,5432,6379,27017,389,8080,8443"


def _get_scan_targets():
    """Return scan target list from the scan_ports setting, falling back to defaults."""
    import core.settings as _settings
    raw = (_settings.get("scan_ports") or "").strip()
    if not raw:
        return _SCAN_TARGETS

    _known = {t["port"]: t for t in _SCAN_TARGETS}
    _known[None] = _SCAN_TARGETS[0]   # ping entry keyed by None

    targets = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if entry.lower() == "ping":
            targets.append(_known[None])
        else:
            try:
                port = int(entry)
            except ValueError:
                continue
            if port in _known:
                targets.append(_known[port])
            else:
                targets.append({"name": f"Port {port}", "stype": "tcp",
                                 "port": port, "tout": 2})
    return targets or _SCAN_TARGETS


def _maybe_resize_executor():
    """Re-evaluate auto worker count after sensor count changes.

    No-op when max_workers_executor is set to a manual value (>= 4).
    Called via _db_enqueue so it runs after the STATE is already saved.
    """
    import concurrent.futures as _cf
    import core.settings as _settings
    _mw_override = int(_settings.get("max_workers_executor", 0) or 0)
    if _mw_override >= 4:
        return  # manual override in effect
    _count = sum(len(d.sensors) for d in STATE.devices.values())
    _mw = max(64, min(512, _count // 4 or 64))
    if STATE._executor._max_workers != _mw:
        STATE._executor = _cf.ThreadPoolExecutor(
            max_workers=_mw, thread_name_prefix='pw-sensor'
        )
        STATE._scheduler._executor = STATE._executor
        log.info(f"Executor auto-resized to {_mw} workers ({_count} sensors)")


_MAX_PARENT_IDS = 8
_MAX_CYCLE_HOPS = 32

# SNMPv3 whitelists — single source of truth for server-side validation.
# Empty string is accepted in every set so that an inherited / cleared value
# round-trips correctly. Mirror of the frontend dropdown options in
# frontend/forms-utils.js (_SNMP_V3_LEVELS / _SNMP_V3_AUTH_PROTOS /
# _SNMP_V3_PRIV_PROTOS). Adding a new protocol means updating both sides.
_V3_LEVELS = frozenset({"", "noAuthNoPriv", "authNoPriv", "authPriv"})
_V3_AUTH   = frozenset({"", "MD5", "SHA", "SHA-224", "SHA-256", "SHA-384", "SHA-512"})
_V3_PRIV   = frozenset({"", "DES", "AES", "AES-192", "AES-256"})
_MAX_PORT_LEN = 32  # Switch interface names are short — Gi1/0/24, Te0/0/1, ether1, etc.


def _normalize_parent_ports(raw, allowed_pids) -> "tuple[bool, dict, str]":
    """Validate `parent_device_ports`: {pid: [{"lport": str, "rport": str}, ...]}.

    A pid may have multiple port pairs (LACP — same device pair connected over
    multiple physical interfaces). The output shape is always a list of pairs
    per pid; the legacy single-object shape `{pid: {lport, rport}}` is accepted
    on input and wrapped, so older clients keep working.

    Entries whose pid isn't in `allowed_pids` are silently dropped (e.g. user
    removed the parent but a stale port entry lingered). Empty/all-blank port
    pairs are dropped too. A pid whose list ends up empty is omitted entirely.

    Returns (ok, cleaned, error). Empty/missing input is valid.
    """
    if raw is None:
        return True, {}, ""
    if not isinstance(raw, dict):
        return False, {}, "parent_device_ports must be an object"
    cleaned: dict = {}
    for pid, val in raw.items():
        if not isinstance(pid, str) or not pid:
            continue
        if pid not in allowed_pids:
            continue  # drop ports for parents the user no longer has
        # Accept both shapes: a single {lport,rport} dict OR a list of dicts.
        entries = val if isinstance(val, list) else [val]
        out_pairs = []
        for entry in entries:
            if not isinstance(entry, dict):
                return False, {}, "parent_device_ports entries must be objects"
            lport = entry.get("lport", "")
            rport = entry.get("rport", "")
            if not isinstance(lport, str) or not isinstance(rport, str):
                return False, {}, "lport/rport must be strings"
            lport = lport.strip()[:_MAX_PORT_LEN]
            rport = rport.strip()[:_MAX_PORT_LEN]
            if not lport and not rport:
                continue  # nothing worth persisting for this pair
            out_pairs.append({"lport": lport, "rport": rport})
        if out_pairs:
            cleaned[pid] = out_pairs
    return True, cleaned, ""


def _normalize_parent_ids(raw) -> "tuple[bool, list[str], str]":
    """Validate + dedup a parent_device_ids input.

    Each ref is either a device id (e.g. "d1") or a group reference of the
    form "group:<group_name>". Group refs let a child point at an entire
    cluster (e.g. a VM hanging off "the ESXi cluster" rather than 10 hosts).

    Returns (ok, cleaned, error). Empty list is valid.
    """
    if raw is None:
        return True, [], ""
    if not isinstance(raw, list):
        return False, [], "parent_device_ids must be a list"
    if len(raw) > _MAX_PARENT_IDS:
        return False, [], f"too many parents (max {_MAX_PARENT_IDS})"
    seen = set()
    cleaned = []
    for p in raw:
        if not isinstance(p, str):
            return False, [], "parent ids must be strings"
        p = p.strip()
        if not p:
            continue
        if p.startswith("group:"):
            gname = p[6:].strip()
            if not gname:
                return False, [], "group reference missing group name"
            if len(gname) > 80:
                return False, [], "group name too long"
            p = "group:" + gname
        else:
            if len(p) > 64:
                return False, [], "parent id too long"
        if p in seen:
            continue
        seen.add(p)
        cleaned.append(p)
    return True, cleaned, ""


def _detect_parent_cycle(STATE, did: str, new_parents: list) -> bool:
    """Return True if assigning new_parents to did would create a cycle.

    Walks up from each candidate parent by BFS. Group refs ("group:<name>")
    expand to every device currently in that group — touching any of them
    detects a cycle. Short-circuits at _MAX_CYCLE_HOPS to bound the worst
    case if the graph is already corrupted.
    """
    if not new_parents:
        return False
    visited = set()
    queue = list(new_parents)
    hops = 0
    while queue and hops < _MAX_CYCLE_HOPS:
        nxt = []
        for ref in queue:
            if ref == did:
                return True
            if ref in visited:
                continue
            visited.add(ref)
            if ref.startswith("group:"):
                gname = ref[6:]
                # Walk every device currently in this group.
                for d in STATE.devices.values():
                    if (d.group or "").strip() == gname:
                        if d.device_id == did:
                            return True
                        nxt.extend(getattr(d, "parent_device_ids", []) or [])
            else:
                parent_dev = STATE.devices.get(ref)
                if not parent_dev:
                    continue
                nxt.extend(getattr(parent_dev, "parent_device_ids", []) or [])
        queue = nxt
        hops += 1
    return False


def _filter_known_parents(STATE, refs: list, exclude_did: str = "") -> list:
    """Drop unknown device refs; keep group refs (group may have no devices yet)."""
    out = []
    for p in refs:
        if p.startswith("group:"):
            out.append(p)
        elif p in STATE.devices and p != exclude_did:
            out.append(p)
    return out


def _valid_probe_ref(v: str) -> bool:
    """Validate a probe_id assignment value: '' (inherit), the literal
    'central' (explicit pin), or an existing probe record."""
    if v in ("", "central"):
        return True
    try:
        from db.probes import db_get_probe
        return db_get_probe(v) is not None
    except Exception:
        return False


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""
    STATE = app_state.STATE  # always current reference

    # ── /api/devices GET ─────────────────────────────────────────
    if path == "/api/devices" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, {"devices": STATE.all_devices()})
        return True

    # ── /api/devices/bulk POST — bulk actions across many devices ─
    if path == "/api/devices/bulk" and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        dids = body.get("device_ids") or []
        action = body.get("action")
        if not isinstance(dids, list) or not dids or len(dids) > 1000:
            h._json(400, {"error": "device_ids required (1-1000)"}); return True
        if action not in ("move", "start", "stop", "delete"):
            h._json(400, {"error": "invalid action"}); return True

        target_group = None
        target_site  = None
        if action == "move":
            target_group = (body.get("group") or "").strip()
            # Site can be moved independently of group, or together with it.
            # Empty string is a valid value meaning "Unsited" (clears the field).
            if "site" in body:
                target_site = str(body.get("site") or "").strip()
                if len(target_site) > 80:
                    h._json(400, {"error": "site too long (max 80 chars)"}); return True
            if not target_group and target_site is None:
                h._json(400, {"error": "group or site required for move"}); return True
            if target_group and len(target_group) > 80:
                h._json(400, {"error": "group too long (max 80 chars)"}); return True

        results = []
        applied = 0
        # Phase 1 — collect valid device references under the lock.
        with STATE._lock:
            valid_dids = []
            for did in dids:
                if not isinstance(did, str):
                    results.append({"did": str(did), "ok": False, "reason": "invalid_id"})
                    continue
                if did not in STATE.devices:
                    results.append({"did": did, "ok": False, "reason": "not_found"})
                    continue
                valid_dids.append(did)

        # Phase 2 — apply the action. Done outside the lock for start/stop/delete
        # because those helpers acquire the lock internally and have broadcast +
        # persistence side effects.
        if action == "move":
            with STATE._lock:
                for did in valid_dids:
                    dev = STATE.devices.get(did)
                    if dev:
                        if target_group:
                            dev.group = target_group
                        if target_site is not None:
                            dev.site = target_site
                        applied += 1
                        results.append({"did": did, "ok": True})
            _db_enqueue(lambda: db_save(STATE))
            # Broadcast once so open tabs refresh grouping without reloading.
            STATE._broadcast("devices_bulk_updated", {
                "action": "move",
                "group":  target_group,
                "site":   target_site,
                "dids":   valid_dids,
            })

        elif action == "start":
            for did in valid_dids:
                try:
                    STATE.start_device(did)
                    applied += 1
                    results.append({"did": did, "ok": True})
                except Exception:
                    log.exception(f"bulk start failed on {did}")
                    results.append({"did": did, "ok": False, "reason": "error"})
            _db_enqueue(lambda: db_save(STATE))

        elif action == "stop":
            for did in valid_dids:
                try:
                    STATE.stop_device(did)
                    applied += 1
                    results.append({"did": did, "ok": True})
                except Exception:
                    log.exception(f"bulk stop failed on {did}")
                    results.append({"did": did, "ok": False, "reason": "error"})
            _db_enqueue(lambda: db_save(STATE))

        elif action == "delete":
            # Replicate the single-device DELETE path's side effects per device.
            for did in valid_dids:
                try:
                    with STATE._lock:
                        dd = STATE.devices.get(did)
                        if not dd:
                            results.append({"did": did, "ok": False, "reason": "not_found"})
                            continue
                        ddname = dd.name or did
                        dd_ext = (getattr(dd, "external_id", None) or "")
                        dd_host = dd.host or ""
                        _sensor_ids = list(dd.sensors.keys())
                    STATE.remove_device(did)
                    if dd_ext.startswith("discovery:") and dd_host:
                        try:
                            from monitoring.auto_discovery import suppress_host
                            suppress_host(dd_host, ddname, user)
                        except Exception as _sup_err:
                            log.warning(f"Auto-Discovery suppress-host hook failed: {_sup_err}")
                    _cap_did = did
                    _db_enqueue(lambda _d=_cap_did: ipam_sync_device_delete(_d))
                    _db_enqueue(lambda _d=_cap_did: topo_prune_pw_links(_d))
                    for _sid in _sensor_ids:
                        _db_enqueue(lambda _d=_cap_did, _s=_sid: db_resolve_events_by_sensor(_d, _s))
                        _db_enqueue(lambda _d=_cap_did, _s=_sid: db_resolve_flaps_by_sensor(_d, _s))
                        _db_enqueue(lambda _d=_cap_did, _s=_sid: db_clear_stage_state_for_sensor(_d, _s))
                    STATE._broadcast("device_deleted", {"did": did})
                    applied += 1
                    results.append({"did": did, "ok": True})
                except Exception:
                    log.exception(f"bulk delete failed on {did}")
                    results.append({"did": did, "ok": False, "reason": "error"})
            _db_enqueue(lambda: db_save(STATE))
            _db_enqueue(_maybe_resize_executor)

        detail = f"{applied} device(s)"
        if target_group:
            detail += f" → {target_group}"
        db_log_audit(user, h.client_address[0], f"bulk_{action}", detail)

        h._json(200, {
            "ok": True,
            "action": action,
            "applied": applied,
            "failed": len(dids) - applied,
            "results": results,
        })
        return True

    # ── /api/start / /api/stop POST ──────────────────────────────
    if path == "/api/start" and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        STATE.start_all()
        db_log_audit(user, h.client_address[0], 'sensors_start')
        h._json(200, {"status": "started"})
        return True

    if path == "/api/stop" and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        STATE.stop_all()
        db_log_audit(user, h.client_address[0], 'sensors_stop')
        h._json(200, {"status": "stopped"})
        return True

    # ── /api/device POST (create) ─────────────────────────────────
    if path == "/api/device" and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        name        = body.get("name", "").strip()
        host        = body.get("host", "").lower().strip()
        group       = body.get("group", "Default Group")
        site        = str(body.get("site", "") or "").strip()
        webhook_url = body.get("webhook_url", "").strip()
        if not name or not host:
            h._json(400, {"error": "name and host required"}); return True
        if len(name) > 255:
            h._json(400, {"error": "name too long (max 255)"}); return True
        if len(host) > 253:
            h._json(400, {"error": "host too long (max 253)"}); return True
        if len(site) > 80:
            h._json(400, {"error": "site too long (max 80 chars)"}); return True
        if webhook_url and len(webhook_url) > 2048:
            h._json(400, {"error": "webhook_url too long (max 2048)"}); return True
        if not h._valid_host(host):
            h._json(400, {"error": "invalid host — use a hostname or IP address"}); return True
        probe_id = str(body.get("probe_id", "") or "").strip()
        if not _valid_probe_ref(probe_id):
            h._json(400, {"error": "unknown probe"}); return True
        snmp_community_default  = body.get("snmp_community_default", "").strip()
        snmp_version_default    = body.get("snmp_version_default", "").strip()
        if snmp_version_default not in ("", "1", "2c", "3"):
            snmp_version_default = ""
        vmware_user_default     = body.get("vmware_user_default", "").strip()
        vmware_password_default = body.get("vmware_password_default", "")
        if vmware_password_default:
            from db.backups import encrypt_pw
            vmware_password_default = encrypt_pw(vmware_password_default)
        snmp_v3_user_default       = body.get("snmp_v3_user_default", "").strip()
        snmp_v3_level_default      = body.get("snmp_v3_level_default", "").strip()
        if snmp_v3_level_default not in _V3_LEVELS: snmp_v3_level_default = ""
        snmp_v3_auth_proto_default = body.get("snmp_v3_auth_proto_default", "").strip()
        if snmp_v3_auth_proto_default not in _V3_AUTH: snmp_v3_auth_proto_default = ""
        snmp_v3_priv_proto_default = body.get("snmp_v3_priv_proto_default", "").strip()
        if snmp_v3_priv_proto_default not in _V3_PRIV: snmp_v3_priv_proto_default = ""
        snmp_v3_context_default    = body.get("snmp_v3_context_default", "").strip()
        _v3_auth_pw_default = body.get("snmp_v3_auth_pass_default", "")
        _v3_priv_pw_default = body.get("snmp_v3_priv_pass_default", "")
        if _v3_auth_pw_default or _v3_priv_pw_default:
            from db.backups import encrypt_pw
            _v3_auth_pw_default = encrypt_pw(_v3_auth_pw_default) if _v3_auth_pw_default else ""
            _v3_priv_pw_default = encrypt_pw(_v3_priv_pw_default) if _v3_priv_pw_default else ""
        # Optional parent linkage on create — validated against existing devices.
        # New device's own did is not yet known here, so self-parent isn't possible.
        parent_ok, parent_ids_clean, parent_err = _normalize_parent_ids(body.get("parent_device_ids"))
        if not parent_ok:
            h._json(400, {"error": parent_err}); return True
        # Per-parent port wiring (Live Map link info). Validated against the
        # *cleaned* parent list so stale pids in the map don't leak through.
        pports_ok, parent_ports_clean, pports_err = _normalize_parent_ports(
            body.get("parent_device_ports"), set(parent_ids_clean)
        )
        if not pports_ok:
            h._json(400, {"error": pports_err}); return True
        did = STATE.add_device(name, host, group, site=site)
        with STATE._lock:
            if did in STATE.devices:
                STATE.devices[did].probe_id = probe_id
                STATE.devices[did].webhook_url = webhook_url
                STATE.devices[did].snmp_community_default  = snmp_community_default
                STATE.devices[did].snmp_version_default    = snmp_version_default
                STATE.devices[did].vmware_user_default     = vmware_user_default
                STATE.devices[did].vmware_password_default = vmware_password_default
                STATE.devices[did].snmp_v3_user_default       = snmp_v3_user_default
                STATE.devices[did].snmp_v3_level_default      = snmp_v3_level_default
                STATE.devices[did].snmp_v3_auth_proto_default = snmp_v3_auth_proto_default
                STATE.devices[did].snmp_v3_priv_proto_default = snmp_v3_priv_proto_default
                STATE.devices[did].snmp_v3_context_default    = snmp_v3_context_default
                if _v3_auth_pw_default:
                    STATE.devices[did].snmp_v3_auth_pass_default = _v3_auth_pw_default
                if _v3_priv_pw_default:
                    STATE.devices[did].snmp_v3_priv_pass_default = _v3_priv_pw_default
                # Drop unknown parent ids (e.g. user pasted a stale id); keep group refs.
                _filtered_parents = _filter_known_parents(
                    STATE, parent_ids_clean, exclude_did=did
                )
                STATE.devices[did].parent_device_ids = _filtered_parents
                # Re-filter port map against the post-filter pid set so entries
                # for dropped parents don't survive.
                STATE.devices[did].parent_device_ports = {
                    k: v for k, v in parent_ports_clean.items() if k in _filtered_parents
                }
        _db_enqueue(lambda: db_save(STATE))
        _db_enqueue(_maybe_resize_executor)
        _did, _name, _host = did, name, host
        _db_enqueue(lambda: ipam_sync_device_add(_did, _name, _host))
        db_log_audit(user, h.client_address[0], 'device_create', name)
        with STATE._lock:
            _new_dev = STATE.devices.get(did)
            _added_payload = _new_dev.to_dict() if _new_dev else None
        if _added_payload:
            STATE._broadcast("device_added", _added_payload)
        h._json(200, {"did": did})
        return True

    # ── /api/devices/{did}/logs GET ──────────────────────────────
    m = _RE_DEVICE_LOGS.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, {"logs": db_load_err_logs(m.group(1))})
        return True

    # ── /api/devices/{did}/logs DELETE ───────────────────────────
    if m and method == "DELETE":
        user, _ = h._require("operator")
        if not user: return True
        did = m.group(1)
        _db_enqueue(lambda: db_clear_err_logs(did))
        with STATE._lock:
            _dev  = STATE.devices.get(did)
            _host = _dev.host if _dev else None
        if _host:
            _h = _host
            _db_enqueue(lambda: db_clear_device_traps(_h))
        db_log_audit(user, h.client_address[0], 'logs_clear', did)
        h._json(200, {"ok": True})
        return True

    # ── /api/devices/{did}/scan POST ─────────────────────────────
    m = _RE_DEVICE_SCAN.match(path)
    if m and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        did = m.group(1)
        dev = STATE.get_device(did)
        if not dev:
            h._json(404, {"error": "not found"}); return True
        host    = dev.host

        # Devices measured from a remote probe are scanned there too —
        # central usually can't even reach them. Long-polls the agent
        # task channel (~10s pickup + ~8s scan), same response shape.
        from core.probe_assign import effective_probe
        pid = effective_probe(dev)
        if pid:
            from db.probes import db_get_probe
            probe = db_get_probe(pid)
            pname = (probe or {}).get("name") or pid
            if not probe or probe.get("status") != "enrolled":
                h._json(409, {"error": f"Probe '{pname}' is not enrolled — "
                              "this device can only be scanned from there"})
                return True
            if _time.time() - float(probe.get("last_seen") or 0) > 35:
                h._json(503, {"error": f"Probe '{pname}' is offline — "
                              "this device can only be scanned from there"})
                return True
            from routes.agent import run_remote_device_scan
            services, err = run_remote_device_scan(
                probe, host, _get_scan_targets(), user)
            if services is None:
                h._json(502, {"error": err or "remote scan failed"})
                return True
            h._json(200, {"did": did, "host": host, "services": services,
                          "via_probe": probe["name"]})
            return True

        results = []
        _lock   = threading.Lock()

        def _scan_one(t):
            stype, port, tout = t["stype"], t["port"], t["tout"]
            try:
                if stype == "ping":
                    r = probe_ping(host, timeout=tout)
                elif stype == "tcp":
                    r = probe_tcp(host, port, timeout=tout)
                elif stype == "http":
                    url = f"http://{host}" if port == 80 else f"http://{host}:{port}"
                    r = probe_http(url, timeout=tout, verify_ssl=False)
                elif stype == "tls":
                    r = probe_tls(host, port, timeout=tout)
                elif stype == "banner":
                    r = probe_banner(host, port, timeout=tout)
                else:
                    return
            except Exception as e:
                r = {"ok": False, "ms": None, "detail": str(e)[:80]}
            if r.get("ok"):
                with _lock:
                    results.append({
                        "stype": stype, "name": t["name"],
                        "port":  port,  "ms":   r.get("ms"),
                        "detail": r.get("detail", ""),
                    })

        targets = _get_scan_targets()
        threads = [threading.Thread(target=_scan_one, args=(t,), daemon=True)
                   for t in targets]
        deadline = _time.monotonic() + 8  # 8s total for all probes
        for th in threads: th.start()
        for th in threads:
            remaining = deadline - _time.monotonic()
            if remaining > 0: th.join(timeout=remaining)
        h._json(200, {"did": did, "host": host, "services": results})
        return True

    # ── /api/devices/{did}/(start|stop) POST ─────────────────────
    m = _RE_DEVICE_ACTION.match(path)
    if m and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        did, action = m.group(1), m.group(2)
        if action == "start": STATE.start_device(did)
        else:                  STATE.stop_device(did)
        h._json(200, {"status": action})
        return True

    # ── /api/devices/{did} GET ────────────────────────────────────
    m = _RE_DEVICE.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        dev = STATE.get_device(m.group(1))
        if dev: h._json(200, dev.to_dict())
        else:   h._json(404, {"error": "not found"})
        return True

    # ── /api/devices/{did} PATCH ──────────────────────────────────
    if m and method == "PATCH":
        user, _ = h._require("operator")
        if not user: return True
        did = m.group(1)
        if "probe_id" in body and not _valid_probe_ref(str(body.get("probe_id") or "").strip()):
            h._json(400, {"error": "unknown probe"}); return True
        with STATE._lock:
            dev = STATE.devices.get(did)
            if not dev:
                h._json(404, {"error": "device not found"}); return True
            _old_host = dev.host
            _old_name = dev.name
            _old_site  = getattr(dev, "site", "")
            _old_probe = getattr(dev, "probe_id", "")
            if "group" in body: dev.group = body["group"]
            if "site" in body:
                _site = str(body.get("site") or "").strip()
                if len(_site) > 80:
                    h._json(400, {"error": "site too long (max 80 chars)"}); return True
                dev.site = _site
            if "probe_id" in body:
                dev.probe_id = str(body.get("probe_id") or "").strip()
            if "name" in body:
                _n = str(body["name"]).strip()
                if len(_n) > 255:
                    h._json(400, {"error": "name too long (max 255)"}); return True
                dev.name = _n
            if "webhook_url" in body:
                _w = body["webhook_url"].strip()
                if _w and len(_w) > 2048:
                    h._json(400, {"error": "webhook_url too long (max 2048)"}); return True
                dev.webhook_url = _w
            if "alerts_muted" in body: dev.alerts_muted = bool(body["alerts_muted"])
            if "snmp_community_default" in body:
                dev.snmp_community_default = str(body["snmp_community_default"]).strip()
            if "snmp_version_default" in body:
                _sv = str(body["snmp_version_default"]).strip()
                if _sv in ("", "1", "2c", "3"):
                    dev.snmp_version_default = _sv
            if "vmware_user_default" in body:
                dev.vmware_user_default = str(body["vmware_user_default"]).strip()
            if "vmware_password_default" in body:
                _vpw = body["vmware_password_default"]
                if _vpw:
                    from db.backups import encrypt_pw
                    dev.vmware_password_default = encrypt_pw(_vpw)
                # empty string = keep existing (don't clear)
            if "snmp_v3_user_default" in body:
                dev.snmp_v3_user_default = str(body["snmp_v3_user_default"]).strip()
            if "snmp_v3_level_default" in body:
                _lv = str(body["snmp_v3_level_default"]).strip()
                if _lv in _V3_LEVELS: dev.snmp_v3_level_default = _lv
            if "snmp_v3_auth_proto_default" in body:
                _ap = str(body["snmp_v3_auth_proto_default"]).strip()
                if _ap in _V3_AUTH: dev.snmp_v3_auth_proto_default = _ap
            if "snmp_v3_priv_proto_default" in body:
                _pp = str(body["snmp_v3_priv_proto_default"]).strip()
                if _pp in _V3_PRIV: dev.snmp_v3_priv_proto_default = _pp
            if "snmp_v3_context_default" in body:
                dev.snmp_v3_context_default = str(body["snmp_v3_context_default"]).strip()
            if "snmp_v3_auth_pass_default" in body:
                _pw = body["snmp_v3_auth_pass_default"]
                if _pw:
                    from db.backups import encrypt_pw
                    dev.snmp_v3_auth_pass_default = encrypt_pw(_pw)
                # empty = keep existing (placeholder-submit from UI)
            if "snmp_v3_priv_pass_default" in body:
                _pw = body["snmp_v3_priv_pass_default"]
                if _pw:
                    from db.backups import encrypt_pw
                    dev.snmp_v3_priv_pass_default = encrypt_pw(_pw)
            if "host" in body:
                h2 = body["host"].strip()
                if len(h2) > 253:
                    h._json(400, {"error": "host too long (max 253)"}); return True
                if not h._valid_host(h2):
                    h._json(400, {"error": "invalid host"}); return True
                from core.orchestration import propagate_device_host
                propagate_device_host(dev, h2)
            if "secondary_ips" in body:
                sips = body["secondary_ips"]
                if not isinstance(sips, list):
                    h._json(400, {"error": "secondary_ips must be a list"}); return True
                if len(sips) > 50:
                    h._json(400, {"error": "too many secondary IPs (max 50)"}); return True
                cleaned = []
                for _sip in sips:
                    _sip = str(_sip).strip().lower()
                    if not _sip or len(_sip) > 253:
                        continue
                    if not h._valid_host(_sip):
                        h._json(400, {"error": f"invalid secondary IP: {_sip}"}); return True
                    if _sip == dev.host.lower():
                        continue  # skip — same as primary host
                    if _sip not in cleaned:
                        cleaned.append(_sip)
                dev.secondary_ips = cleaned
            if "parent_device_ids" in body:
                ok, cleaned_parents, perr = _normalize_parent_ids(body["parent_device_ids"])
                if not ok:
                    h._json(400, {"error": perr}); return True
                if did in cleaned_parents:
                    h._json(400, {"error": "device cannot be its own parent"}); return True
                # Drop unknown device refs (silently — they may have been deleted);
                # keep group refs even if the group is empty (forward-compatible).
                cleaned_parents = _filter_known_parents(STATE, cleaned_parents, exclude_did=did)
                if _detect_parent_cycle(STATE, did, cleaned_parents):
                    h._json(400, {"error": "parent assignment would create a cycle"}); return True
                dev.parent_device_ids = cleaned_parents
                # Keep the port map in sync with the new parent list — entries
                # for removed parents drop out automatically.
                dev.parent_device_ports = {
                    k: v for k, v in (getattr(dev, "parent_device_ports", {}) or {}).items()
                    if k in cleaned_parents
                }
            if "parent_device_ports" in body:
                # Port edits without a corresponding parent list edit. Validate
                # against the current (possibly just-updated above) parent set.
                _allowed = set(getattr(dev, "parent_device_ids", []) or [])
                pp_ok, pp_clean, pp_err = _normalize_parent_ports(
                    body["parent_device_ports"], _allowed
                )
                if not pp_ok:
                    h._json(400, {"error": pp_err}); return True
                dev.parent_device_ports = pp_clean
            _dev_edit_name = dev.name
            _new_host = dev.host
            _new_name = dev.name
            _new_site  = getattr(dev, "site", "")
            _new_probe = getattr(dev, "probe_id", "")
            _assign_sids = list(dev.sensors.keys())
        _db_enqueue(lambda: db_save(STATE))
        # Site or probe changes can move this device's sensors between the
        # central scheduler and a remote probe — re-apply scheduling and let
        # every agent re-pull its config.
        if _old_site != _new_site or _old_probe != _new_probe:
            STATE.apply_probe_assignment([(did, _sid) for _sid in _assign_sids])
            from routes.probes import _bump_all_probe_configs
            _bump_all_probe_configs()
        _d = did
        _db_enqueue(lambda: ipam_sync_device_update(_d, _old_host, _new_host, _new_name))
        db_log_audit(user, h.client_address[0], 'device_edit', _dev_edit_name)
        if "alerts_muted" in body:
            STATE._broadcast("device_status", {"did": did, "status": dev.status})
        with STATE._lock:
            _upd_dev = STATE.devices.get(did)
            _upd_payload = _upd_dev.to_dict() if _upd_dev else None
        if _upd_payload:
            STATE._broadcast("device_updated", _upd_payload)
        h._json(200, {"status": "updated"})
        return True

    # ── /api/device/{did}/secondary-ip POST ────────────────────────
    m_sip = _RE_DEVICE_SIP.match(path)
    if m_sip and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        did = m_sip.group(1)
        ip = str(body.get("ip", "")).strip().lower()
        if not ip or len(ip) > 253:
            h._json(400, {"error": "ip is required"}); return True
        if not h._valid_host(ip):
            h._json(400, {"error": "invalid IP address"}); return True
        with STATE._lock:
            dev = STATE.devices.get(did)
            if not dev:
                h._json(404, {"error": "device not found"}); return True
            if ip == dev.host.lower():
                h._json(400, {"error": "IP is already the primary host"}); return True
            sips = list(getattr(dev, "secondary_ips", []) or [])
            if ip in sips:
                h._json(200, {"status": "already_present", "secondary_ips": sips}); return True
            if len(sips) >= 50:
                h._json(400, {"error": "too many secondary IPs (max 50)"}); return True
            sips.append(ip)
            dev.secondary_ips = sips
            _dev_name = dev.name
        _db_enqueue(lambda: db_save(STATE))
        db_log_audit(user, h.client_address[0], 'device_edit', _dev_name)
        h._json(200, {"status": "added", "secondary_ips": sips})
        return True

    # ── /api/device/{did}/role PUT ─────────────────────────────────
    # Topology role tag for NTM Live auto-links. Whitelist = the full Live
    # Map tier set (mirrors the per-group dropdown) PLUS the legacy IPAM-style
    # aliases ('backbone', 'core', 'gateway') so previously-written values
    # remain settable from automation/scripts without a data migration.
    # Persisted on ip_allocations.kind for the device's host IP (one row per
    # matching subnet). Silent no-op if host isn't a plain IP or no IPAM
    # subnet covers it.
    m_role = _RE_DEVICE_ROLE.match(path)
    if m_role and method == "PUT":
        user, _ = h._require("operator")
        if not user: return True
        did_r = m_role.group(1)
        role  = (body.get("role") or "").strip().lower()
        from monitoring.site_tree import _ROLE_TO_TIER
        if role != "" and role not in _ROLE_TO_TIER:
            h._json(400, {"error": "invalid role"})
            return True
        with STATE._lock:
            devr = STATE.devices.get(did_r)
            if not devr:
                h._json(404, {"error": "device not found"}); return True
            host_r = devr.host
            name_r = devr.name
        # Enqueue the IPAM write — db_set_device_role enqueues internally? No,
        # it opens its own connection. Call inline so we can return rowcount.
        try:
            n = db_set_device_role(did_r, host_r, role)
        except Exception as e:
            log.error(f"PUT /api/device/{did_r}/role failed: {e}")
            h._json(500, {"error": "failed to update role"}); return True
        db_log_audit(user, h.client_address[0], 'device_role',
                     f"{name_r} role={role or 'none'}")
        h._json(200, {"status": "ok", "role": role, "updated": n})
        return True

    # ── /api/devices/{did} DELETE ─────────────────────────────────
    if m and method == "DELETE":
        user, _ = h._require("operator")
        if not user: return True
        ddid = m.group(1)
        with STATE._lock:
            dd     = STATE.devices.get(ddid)
            ddname = dd.name if dd else ddid
            # Capture identity for Auto-Discovery suppression (see below).
            dd_ext = (getattr(dd, "external_id", None) or "") if dd else ""
            dd_host = (dd.host or "") if dd else ""
        # Collect sensor IDs before removing the device (for event cleanup)
        with STATE._lock:
            _sensor_ids = list(dd.sensors.keys()) if dd else []
        STATE.remove_device(ddid)
        # Auto-Discovery: if this device was auto-added (external_id starts
        # with "discovery:"), record its host in the suppressed-list so the
        # next Auto-Discovery tick doesn't resurrect it.
        if dd_ext.startswith("discovery:") and dd_host:
            try:
                from monitoring.auto_discovery import suppress_host
                suppress_host(dd_host, ddname, user)
            except Exception as _sup_err:
                log.warning(f"Auto-Discovery suppress-host hook failed: {_sup_err}")
        _db_enqueue(lambda: db_save(STATE))
        _db_enqueue(_maybe_resize_executor)
        _dd = ddid
        _db_enqueue(lambda: ipam_sync_device_delete(_dd))
        _db_enqueue(lambda: topo_prune_pw_links(_dd))
        # Auto-resolve active events/flaps for all sensors in the deleted device
        for _sid in _sensor_ids:
            _db_enqueue(lambda _d=_dd, _s=_sid: db_resolve_events_by_sensor(_d, _s))
            _db_enqueue(lambda _d=_dd, _s=_sid: db_resolve_flaps_by_sensor(_d, _s))
            _db_enqueue(lambda _d=_dd, _s=_sid: db_clear_stage_state_for_sensor(_d, _s))
        db_log_audit(user, h.client_address[0], 'device_delete', ddname)
        STATE._broadcast("device_deleted", {"did": ddid})
        h._json(200, {"status": "ok"})
        return True

    # ── /api/sensors/{did}/{sid}/history GET ─────────────────────
    m = _RE_SENSOR_HISTORY.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        did, sid = m.group(1), m.group(2)
        _qs = parse_qs(urlparse(h.path).query)
        try:
            minutes = max(1, int(_qs.get("minutes", ["1440"])[0]))
            limit   = max(1, min(10000, int(_qs.get("limit", ["1000"])[0])))
        except (ValueError, TypeError):
            h._json(400, {"error": "invalid query parameter"}); return True
        h._json(200, {"samples": db_load_history(did, sid, minutes, limit)})
        return True

    # ── /api/sensors/{did}/{sid}/summary GET ─────────────────────
    m = _RE_SENSOR_SUMMARY.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        did, sid = m.group(1), m.group(2)
        _qs = parse_qs(urlparse(h.path).query)
        try:
            minutes = max(1, int(_qs.get("minutes", ["1440"])[0]))
        except (ValueError, TypeError):
            h._json(400, {"error": "invalid query parameter"}); return True
        h._json(200, {"summary": db_load_summary(did, sid, minutes)})
        return True

    # ── /api/sensors/{did}/{sid}/logs DELETE ─────────────────────
    m = _RE_SENSOR_LOGS.match(path)
    if m and method == "DELETE":
        user, _ = h._require("operator")
        if not user: return True
        did, sid = m.group(1), m.group(2)
        _db_enqueue(lambda: db_clear_sensor_err_logs(did, sid))
        h._json(200, {"ok": True})
        return True

    # ── /api/sensors/{did}/{sid}/(start|stop) POST ───────────────
    m = _RE_SENSOR_ACTION.match(path)
    if m and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        did, sid, action = m.group(1), m.group(2), m.group(3)
        if action == "start": STATE.start_sensor(did, sid)
        else:                  STATE.stop_sensor(did, sid)
        h._json(200, {"status": action})
        return True

    # ── /api/sensors/{did}/{sid} PATCH ────────────────────────────
    m = _RE_SENSOR_ITEM.match(path)
    if m and method == "PATCH":
        user, _ = h._require("operator")
        if not user: return True
        did, sid = m.group(1), m.group(2)
        kwargs = {}
        for k in ["name", "stype", "host", "url", "interval", "timeout",
                  "verify_ssl", "snmp_community", "snmp_oid", "snmp_version",
                  "dns_query", "dns_record_type", "dns_server",
                  "http_expected_status", "cert_warn_days", "cert_crit_days",
                  "warn_ms", "crit_ms",
                  "loss_warn_pct", "loss_crit_pct",
                  "keyword", "keyword_case", "banner_regex", "alerts_muted",
                  "snmp_unit",
                  "vmware_user", "vmware_vm_id", "vmware_vm_name", "vmware_metric",
                  "vmware_disk_path",
                  "smtp_tls", "smtp_user", "smtp_from", "smtp_rcpt", "smtp_test_level",
                  "ssh_user", "ssh_auth_type", "ssh_test_level",
                  "sftp_user", "sftp_auth_type", "sftp_test_level",
                  "sftp_remote_path", "sftp_expected_sha256",
                  "radius_test_level", "radius_username", "radius_nas_id",
                  "snmp_v3_user", "snmp_v3_level",
                  "snmp_v3_auth_proto", "snmp_v3_priv_proto", "snmp_v3_context",
                  "anomaly_enabled", "anomaly_sensitivity", "anomaly_min_samples",
                  "probe_id"]:
            if k in body: kwargs[k] = body[k]
        if "probe_id" in kwargs:
            kwargs["probe_id"] = str(kwargs["probe_id"] or "").strip()
            if not _valid_probe_ref(kwargs["probe_id"]):
                h._json(400, {"error": "unknown probe"}); return True
        # Normalize anomaly fields to safe ranges
        if "anomaly_enabled" in kwargs:
            kwargs["anomaly_enabled"] = 1 if kwargs["anomaly_enabled"] else 0
        if "anomaly_sensitivity" in kwargs:
            try:
                _s = int(kwargs["anomaly_sensitivity"])
                kwargs["anomaly_sensitivity"] = 1 if _s < 1 else (3 if _s > 3 else _s)
            except (TypeError, ValueError):
                h._json(400, {"error": "anomaly_sensitivity must be 1, 2, or 3"}); return True
        if "anomaly_min_samples" in kwargs:
            try:
                _m = int(kwargs["anomaly_min_samples"])
                kwargs["anomaly_min_samples"] = max(5, min(10000, _m))
            except (TypeError, ValueError):
                h._json(400, {"error": "anomaly_min_samples must be an integer"}); return True
        # HTTPS cert-expiry thresholds (days; 0 = off) — clamp to a sane range
        for _cf in ("cert_warn_days", "cert_crit_days"):
            if _cf in kwargs and kwargs[_cf] not in (None, ""):
                try:
                    kwargs[_cf] = max(0, min(3650, int(kwargs[_cf])))
                except (TypeError, ValueError):
                    h._json(400, {"error": f"{_cf} must be an integer"}); return True
        # VMware password: encrypt if provided, skip if empty (keep existing)
        if body.get("vmware_password"):
            from db.backups import encrypt_pw
            kwargs["vmware_password"] = encrypt_pw(body["vmware_password"])
        # SMTP password: same pattern — encrypt if provided, skip if empty
        if body.get("smtp_password"):
            from db.backups import encrypt_pw
            kwargs["smtp_password"] = encrypt_pw(body["smtp_password"])
        # SSH password + private key: encrypt if provided, skip if empty
        if body.get("ssh_password"):
            from db.backups import encrypt_pw
            kwargs["ssh_password"] = encrypt_pw(body["ssh_password"])
        if body.get("ssh_private_key"):
            from db.backups import encrypt_pw
            kwargs["ssh_private_key"] = encrypt_pw(body["ssh_private_key"])
        # SFTP password + private key: same pattern
        if body.get("sftp_password"):
            from db.backups import encrypt_pw
            kwargs["sftp_password"] = encrypt_pw(body["sftp_password"])
        if body.get("sftp_private_key"):
            from db.backups import encrypt_pw
            kwargs["sftp_private_key"] = encrypt_pw(body["sftp_private_key"])
        # RADIUS shared secret + user password: encrypt if provided, skip if empty
        if body.get("radius_secret"):
            from db.backups import encrypt_pw
            kwargs["radius_secret"] = encrypt_pw(body["radius_secret"])
        if body.get("radius_password"):
            from db.backups import encrypt_pw
            kwargs["radius_password"] = encrypt_pw(body["radius_password"])
        # SNMPv3: validate enum fields, encrypt passphrases.  Empty field in
        # body = keep existing (placeholder-submit pattern from UI).
        if "snmp_v3_level" in kwargs and kwargs["snmp_v3_level"] not in _V3_LEVELS:
            h._json(400, {"error": "invalid snmp_v3_level"}); return True
        if "snmp_v3_auth_proto" in kwargs and kwargs["snmp_v3_auth_proto"] not in _V3_AUTH:
            h._json(400, {"error": "invalid snmp_v3_auth_proto"}); return True
        if "snmp_v3_priv_proto" in kwargs and kwargs["snmp_v3_priv_proto"] not in _V3_PRIV:
            h._json(400, {"error": "invalid snmp_v3_priv_proto"}); return True
        if body.get("snmp_v3_auth_pass"):
            from db.backups import encrypt_pw
            kwargs["snmp_v3_auth_pass"] = encrypt_pw(body["snmp_v3_auth_pass"])
        if body.get("snmp_v3_priv_pass"):
            from db.backups import encrypt_pw
            kwargs["snmp_v3_priv_pass"] = encrypt_pw(body["snmp_v3_priv_pass"])
        # SFTP checksum level: enforce minimum interval (avoids hammering the
        # server with big downloads). Guard fires only when both level + interval
        # are present in the update.
        if kwargs.get("sftp_test_level") == "checksum" and "interval" in kwargs:
            try:
                if int(kwargs["interval"]) < 60:
                    h._json(400, {"error": "checksum level requires interval ≥ 60s"}); return True
            except (TypeError, ValueError):
                h._json(400, {"error": "interval must be an integer"}); return True
        if "port" in body: kwargs["port"] = body["port"]
        if "type" in body: kwargs["stype"] = body["type"]
        try:
            from core.validation import validate_interval, validate_port
            if "interval" in kwargs:
                kwargs["interval"] = validate_interval(kwargs["interval"], 1, 3600)
            if "timeout" in kwargs:
                kwargs["timeout"] = int(kwargs["timeout"])
            if "port" in kwargs and kwargs["port"] not in (None, ""):
                kwargs["port"] = validate_port(kwargs["port"])
        except ValueError as _ve:
            h._json(400, {"error": str(_ve)}); return True
        except (TypeError,):
            h._json(400, {"error": "interval and timeout must be integers"}); return True
        if kwargs.get("banner_regex"):
            if len(kwargs["banner_regex"]) > 200:
                h._json(400, {"error": "banner_regex too long (max 200 chars)"}); return True
            try:
                _bpat = re.compile(kwargs["banner_regex"])
                _sm_r = [None]
                def _sm(_p=_bpat): _sm_r[0] = bool(_p.search("a" * 100))
                _smt = threading.Thread(target=_sm, daemon=True)
                _smt.start(); _smt.join(1.0)
                if _smt.is_alive():
                    h._json(400, {"error": "banner_regex is too complex"}); return True
            except re.error as _re_err:
                h._json(400, {"error": f"Invalid banner_regex: {_re_err}"}); return True
        # Validate timeout against the interval (current or being updated)
        if "timeout" in kwargs:
            with STATE._lock:
                _sensor = STATE.devices.get(did)
                _sensor = _sensor.sensors.get(sid) if _sensor else None
                _current_iv = _sensor.interval if _sensor else 5
            if "interval" in kwargs:
                iv = int(kwargs["interval"])
            else:
                iv = _current_iv
            kwargs["timeout"] = max(1, min(iv, int(kwargs["timeout"])))
        ok = STATE.update_sensor(did, sid, **kwargs)
        if not ok:
            h._json(404, {"error": "sensor not found"}); return True
        # update_sensor's stop/start already re-resolved the scheduling mode
        # for probe_id changes; agents still need a config re-pull.
        if "probe_id" in kwargs:
            from routes.probes import _bump_all_probe_configs
            _bump_all_probe_configs()
        _db_enqueue(lambda: db_save(STATE))
        with STATE._lock:
            _se_dev   = STATE.devices.get(did)
            _se_dname = _se_dev.name if _se_dev else did
            _se_sname = (_se_dev.sensors[sid].name
                         if _se_dev and sid in _se_dev.sensors else sid)
        db_log_audit(user, h.client_address[0], 'sensor_edit', f"{_se_dname}/{_se_sname}")
        if "alerts_muted" in body and _se_dev:
            STATE._broadcast("device_status", {"did": did, "status": _se_dev.status})
        h._json(200, {"status": "updated"})
        return True

    # ── /api/sensors/{did}/{sid}/anomaly/reset POST ───────────────
    mar = _RE_SENSOR_ANOMALY_RESET.match(path) if _RE_SENSOR_ANOMALY_RESET else None
    if mar and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        rdid, rsid = mar.group(1), mar.group(2)
        with STATE._lock:
            rdev = STATE.devices.get(rdid)
            rsen = rdev.sensors.get(rsid) if rdev else None
            if not rsen:
                h._json(404, {"error": "sensor not found"}); return True
            from monitoring.anomaly import reset_baseline as _anom_reset
            _anom_reset(rsen)
            _rdname, _rsname = rdev.name, rsen.name
        _db_enqueue(lambda: db_reset_anomaly_baseline(rdid, rsid))
        db_log_audit(user, h.client_address[0], 'anomaly_baseline_reset', f"{_rdname}/{_rsname}")
        h._json(200, {"ok": True, "baseline_reset": True})
        return True

    # ── /api/sensors/{did}/{sid} DELETE ──────────────────────────
    if m and method == "DELETE":
        user, _ = h._require("operator")
        if not user: return True
        sdid, ssid = m.group(1), m.group(2)
        with STATE._lock:
            sd     = STATE.devices.get(sdid)
            sdname = sd.name if sd else sdid
            ssname = sd.sensors[ssid].name if sd and ssid in sd.sensors else ssid
        STATE.remove_sensor(sdid, ssid)
        _db_enqueue(lambda: db_save(STATE))
        _db_enqueue(_maybe_resize_executor)
        # Auto-resolve active events/flaps for the deleted sensor
        _sd, _ss = sdid, ssid
        _db_enqueue(lambda: db_resolve_events_by_sensor(_sd, _ss))
        _db_enqueue(lambda: db_resolve_flaps_by_sensor(_sd, _ss))
        _db_enqueue(lambda: db_clear_stage_state_for_sensor(_sd, _ss))
        db_log_audit(user, h.client_address[0], 'sensor_delete', f"{sdname}/{ssname}")
        STATE._broadcast("sensor_deleted", {"did": sdid, "sid": ssid})
        h._json(200, {"status": "ok"})
        return True

    # ── /api/sensors/{did} POST (create sensor) ───────────────────
    m = _RE_SENSOR.match(path)
    if m and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        did   = m.group(1)
        name  = body.get("name", "").strip()
        stype = body.get("type", "ping")
        dev   = STATE.get_device(did)
        host  = body.get("host") or (dev.host if dev else None)
        port  = body.get("port")
        url   = (body.get("url") or "").strip() or None
        try:
            iv    = max(1, min(3600, int(body.get("interval", 60))))
            to    = max(1, min(iv, int(body.get("timeout", 10))))
        except (TypeError, ValueError):
            h._json(400, {"error": "interval and timeout must be integers"}); return True
        vssl  = bool(body.get("verify_ssl", True))
        comm  = (body.get("snmp_community") or "").strip()
        if not comm:
            comm = (dev.snmp_community_default if dev and dev.snmp_community_default else "public")
        oid   = body.get("snmp_oid", "1.3.6.1.2.1.1.1.0")
        sver  = body.get("snmp_version", "2c")
        sunit = body.get("snmp_unit", "")
        try:
            xstat = int(body.get("http_expected_status", 0))
            wms   = int(body["warn_ms"])  if body.get("warn_ms")  else None
            cms   = int(body["crit_ms"])  if body.get("crit_ms")  else None
            lwp   = int(body.get("loss_warn_pct", 0) or 0)
            lcp   = int(body.get("loss_crit_pct", 0) or 0)
            cwd   = max(0, min(3650, int(body.get("cert_warn_days", 0) or 0)))
            ccd   = max(0, min(3650, int(body.get("cert_crit_days", 0) or 0)))
        except (TypeError, ValueError):
            h._json(400, {"error": "Numeric fields must be integers"}); return True
        kw    = body.get("keyword", "")
        kwc   = bool(body.get("keyword_case", False))
        bnr   = body.get("banner_regex", "")
        # VMware fields
        vm_user      = body.get("vmware_user", "")
        vm_pw        = body.get("vmware_password", "")
        vm_vmid      = body.get("vmware_vm_id", "")
        vm_vmname    = body.get("vmware_vm_name", "")
        vm_metric    = body.get("vmware_metric", "")
        vm_disk_path = body.get("vmware_disk_path", "")
        _vm_pw_from_device = False
        if stype == "vmware" and dev:
            if not vm_user and dev.vmware_user_default:
                vm_user = dev.vmware_user_default
            if not vm_pw and dev.vmware_password_default:
                vm_pw = dev.vmware_password_default  # already encrypted
                _vm_pw_from_device = True
        if stype == "vmware" and vm_pw and not _vm_pw_from_device:
            from db.backups import encrypt_pw
            vm_pw = encrypt_pw(vm_pw)
        # SMTP fields
        smtp_tls_v        = body.get("smtp_tls", "none")
        smtp_user_v       = body.get("smtp_user", "")
        smtp_pw_v         = body.get("smtp_password", "")
        smtp_from_v       = body.get("smtp_from", "")
        smtp_rcpt_v       = body.get("smtp_rcpt", "")
        smtp_test_level_v = body.get("smtp_test_level", "ehlo")
        if stype == "smtp" and smtp_pw_v:
            from db.backups import encrypt_pw
            smtp_pw_v = encrypt_pw(smtp_pw_v)
        # SSH fields
        ssh_user_v        = body.get("ssh_user", "")
        ssh_pw_v          = body.get("ssh_password", "")
        ssh_key_v         = body.get("ssh_private_key", "")
        ssh_auth_type_v   = body.get("ssh_auth_type", "password")
        ssh_test_level_v  = body.get("ssh_test_level", "banner")
        if stype == "ssh":
            from db.backups import encrypt_pw
            if ssh_pw_v:
                ssh_pw_v = encrypt_pw(ssh_pw_v)
            if ssh_key_v:
                ssh_key_v = encrypt_pw(ssh_key_v)
        # SFTP fields
        sftp_user_v        = body.get("sftp_user", "")
        sftp_pw_v          = body.get("sftp_password", "")
        sftp_key_v         = body.get("sftp_private_key", "")
        sftp_auth_type_v   = body.get("sftp_auth_type", "password")
        sftp_test_level_v  = body.get("sftp_test_level", "open")
        sftp_remote_path_v = body.get("sftp_remote_path", "")
        sftp_expected_v    = body.get("sftp_expected_sha256", "")
        if stype == "sftp":
            from db.backups import encrypt_pw
            if sftp_pw_v:
                sftp_pw_v = encrypt_pw(sftp_pw_v)
            if sftp_key_v:
                sftp_key_v = encrypt_pw(sftp_key_v)
            # Interval floor for checksum level — matches PATCH guard above
            if sftp_test_level_v == "checksum" and iv < 60:
                h._json(400, {"error": "checksum level requires interval ≥ 60s"}); return True
        # RADIUS fields
        radius_secret_v    = body.get("radius_secret", "")
        radius_level_v     = body.get("radius_test_level", "reachable")
        radius_user_v      = body.get("radius_username", "")
        radius_pw_v        = body.get("radius_password", "")
        radius_nas_id_v    = body.get("radius_nas_id", "")
        if stype == "radius":
            from db.backups import encrypt_pw
            if radius_secret_v:
                radius_secret_v = encrypt_pw(radius_secret_v)
            if radius_pw_v:
                radius_pw_v = encrypt_pw(radius_pw_v)
        # SNMPv3 fields — per-sensor override of device defaults.  Blank
        # fields inherit from the device at probe time (see Sensor._resolve_snmp_v3_creds).
        v3_user   = (body.get("snmp_v3_user") or "").strip()
        v3_level  = (body.get("snmp_v3_level") or "").strip()
        if v3_level not in _V3_LEVELS:
            h._json(400, {"error": "invalid snmp_v3_level"}); return True
        v3_aproto = (body.get("snmp_v3_auth_proto") or "").strip()
        if v3_aproto not in _V3_AUTH:
            h._json(400, {"error": "invalid snmp_v3_auth_proto"}); return True
        v3_pproto = (body.get("snmp_v3_priv_proto") or "").strip()
        if v3_pproto not in _V3_PRIV:
            h._json(400, {"error": "invalid snmp_v3_priv_proto"}); return True
        v3_context = (body.get("snmp_v3_context") or "").strip()
        v3_apw = body.get("snmp_v3_auth_pass") or ""
        v3_ppw = body.get("snmp_v3_priv_pass") or ""
        if v3_apw or v3_ppw:
            from db.backups import encrypt_pw
            v3_apw = encrypt_pw(v3_apw) if v3_apw else ""
            v3_ppw = encrypt_pw(v3_ppw) if v3_ppw else ""
        if bnr:
            if len(bnr) > 200:
                h._json(400, {"error": "banner_regex too long (max 200 chars)"}); return True
            try:
                _bpat = re.compile(bnr)
                _sm_r = [None]
                def _sm(_p=_bpat): _sm_r[0] = bool(_p.search("a" * 100))
                _smt = threading.Thread(target=_sm, daemon=True)
                _smt.start(); _smt.join(1.0)
                if _smt.is_alive():
                    h._json(400, {"error": "banner_regex is too complex"}); return True
            except re.error as _re_err:
                h._json(400, {"error": f"Invalid banner_regex: {_re_err}"}); return True
        if stype == "http" and url and not h._valid_url(url):
            h._json(400, {"error": "url must start with http:// or https://"}); return True
        if stype in ("ping", "tcp", "snmp", "dns") and host and not h._valid_host(host):
            h._json(400, {"error": "invalid host"}); return True
        try:
            import core.settings as _settings
            _fa = int(_settings.get("snr_fail_after",    3) or 3)
            _ra = int(_settings.get("snr_recover_after", 2) or 2)
        except (TypeError, ValueError):
            _fa, _ra = 3, 2
        sid = STATE.add_sensor(did, name, stype, host, port, url,
                               iv, to, vssl, comm, oid, sver,
                               fail_after=_fa, recover_after=_ra,
                               warn_ms=wms, crit_ms=cms,
                               loss_warn_pct=lwp, loss_crit_pct=lcp,
                               keyword=kw, keyword_case=kwc, banner_regex=bnr,
                               snmp_unit=sunit,
                               vmware_user=vm_user, vmware_password=vm_pw,
                               vmware_vm_id=vm_vmid, vmware_vm_name=vm_vmname,
                               vmware_metric=vm_metric, vmware_disk_path=vm_disk_path,
                               smtp_tls=smtp_tls_v, smtp_user=smtp_user_v,
                               smtp_password=smtp_pw_v,
                               smtp_from=smtp_from_v, smtp_rcpt=smtp_rcpt_v,
                               smtp_test_level=smtp_test_level_v,
                               ssh_user=ssh_user_v,
                               ssh_password=ssh_pw_v,
                               ssh_private_key=ssh_key_v,
                               ssh_auth_type=ssh_auth_type_v,
                               ssh_test_level=ssh_test_level_v,
                               sftp_user=sftp_user_v,
                               sftp_password=sftp_pw_v,
                               sftp_private_key=sftp_key_v,
                               sftp_auth_type=sftp_auth_type_v,
                               sftp_test_level=sftp_test_level_v,
                               sftp_remote_path=sftp_remote_path_v,
                               sftp_expected_sha256=sftp_expected_v,
                               radius_secret=radius_secret_v,
                               radius_test_level=radius_level_v,
                               radius_username=radius_user_v,
                               radius_password=radius_pw_v,
                               radius_nas_id=radius_nas_id_v,
                               snmp_v3_user=v3_user, snmp_v3_level=v3_level,
                               snmp_v3_auth_proto=v3_aproto,
                               snmp_v3_auth_pass=v3_apw,
                               snmp_v3_priv_proto=v3_pproto,
                               snmp_v3_priv_pass=v3_ppw,
                               snmp_v3_context=v3_context)
        if not sid:
            h._json(404, {"error": "device not found"}); return True
        with STATE._lock:
            dev2 = STATE.devices.get(did)
            if dev2 and sid in dev2.sensors:
                s2 = dev2.sensors[sid]
                s2.dns_query            = body.get("dns_query", "")
                s2.dns_record_type      = body.get("dns_record_type", "A")
                s2.dns_server           = body.get("dns_server", "")
                s2.http_expected_status = xstat
                s2.cert_warn_days       = cwd
                s2.cert_crit_days       = ccd
                # Distributed probes: per-sensor override must land BEFORE the
                # sensor starts so start_sensor resolves the right scheduler.
                _new_probe = str(body.get("probe_id") or "").strip()
                if _new_probe and _valid_probe_ref(_new_probe):
                    s2.probe_id = _new_probe
                # Optional anomaly config on create (UI enables post-creation; API may set here).
                if "anomaly_enabled" in body:
                    s2.anomaly_enabled = 1 if body["anomaly_enabled"] else 0
                else:
                    import core.settings as _anom_st_mod
                    if int(_anom_st_mod.get("anomaly_default_new_sensors", 0) or 0):
                        from monitoring.anomaly import SUPPORTED_STYPES as _ANOM_STYPES
                        if s2.stype in _ANOM_STYPES:
                            s2.anomaly_enabled = 1
                if "anomaly_sensitivity" in body:
                    try:
                        _sv = int(body["anomaly_sensitivity"])
                        s2.anomaly_sensitivity = 1 if _sv < 1 else (3 if _sv > 3 else _sv)
                    except (TypeError, ValueError):
                        pass
                if "anomaly_min_samples" in body:
                    try:
                        s2.anomaly_min_samples = max(5, min(10000, int(body["anomaly_min_samples"])))
                    except (TypeError, ValueError):
                        pass
        _db_enqueue(lambda: db_save(STATE))
        _db_enqueue(_maybe_resize_executor)
        _dev_name = dev.name if dev else did
        db_log_audit(user, h.client_address[0], 'sensor_create', f"{_dev_name}/{name}")
        _h = host or "unknown"
        if stype == "vmware" and not vssl and _h not in _vmware_ssl_warned:
            _vmware_ssl_warned.add(_h)
            log.warning("VMware: sensor on %s has Verify SSL disabled — enable for production use", _h)
        with STATE._lock:
            _sdev = STATE.devices.get(did)
            _spayload = _sdev.sensors[sid].to_dict() if _sdev and sid in _sdev.sensors else None
        if _spayload:
            STATE._broadcast("sensor_added", {"did": did, "sensor": _spayload})
        h._json(200, {"sid": sid})
        return True

    # ── GET /api/availability ─────────────────────────────────────────
    if _RE_AVAILABILITY.match(path) and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        qs = parse_qs(urlparse(h.path).query)
        try:
            minutes = max(1, int(qs.get('minutes', ['1440'])[0]))
        except (ValueError, TypeError):
            h._json(400, {'error': 'bad param'}); return True
        h._json(200, {'availability': db_load_availability(minutes)})
        return True

    # ── GET /api/device-groups/muted ──────────────────────────────────
    # Bulk list of currently-muted group names. Loaded once at page boot
    # so every group header can be decorated without N round-trips.
    if _RE_DEV_GROUPS_MUTED.match(path) and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        import core.settings as _settings
        import json as _json
        raw = _settings.get("muted_groups", "") or ""
        lst: list = []
        if raw:
            try:
                v = _json.loads(raw)
                if isinstance(v, list):
                    lst = [str(g) for g in v if isinstance(g, str)]
            except Exception:
                lst = []
        h._json(200, {"groups": lst})
        return True

    # ── /api/device-group/<name>/mute (GET viewer, POST operator) ─────
    # Group mute = suppress alert dispatch + flap events for every device in
    # the named group. Matches per-device `alerts_muted` semantics; probes
    # still run, device cards still reflect real status. Stored as a JSON
    # list in app_settings.muted_groups so no schema change is required —
    # device groups are just strings on Device, not a separate table.
    m = _RE_DEV_GROUP_MUTE.match(path)
    if m:
        from urllib.parse import unquote
        group_name = unquote(m.group(1) or "").strip()
        if not group_name:
            h._json(400, {"error": "group name required"}); return True

        def _read_muted_list() -> list:
            import core.settings as _settings
            import json as _json
            raw = _settings.get("muted_groups", "") or ""
            if not raw:
                return []
            try:
                v = _json.loads(raw)
                return v if isinstance(v, list) else []
            except Exception:
                return []

        if method == "GET":
            user, _ = h._require("viewer")
            if not user: return True
            h._json(200, {"group": group_name,
                          "muted": group_name in _read_muted_list()})
            return True

        if method == "POST":
            user, _ = h._require("operator")
            if not user: return True
            want = bool(body.get("muted")) if isinstance(body, dict) else False
            lst = _read_muted_list()
            changed = False
            if want and group_name not in lst:
                lst.append(group_name); changed = True
            elif (not want) and group_name in lst:
                lst = [g for g in lst if g != group_name]; changed = True

            if changed:
                import core.settings as _settings
                import json as _json
                from db import db_save_settings
                payload = _json.dumps(lst)
                _settings.load({"muted_groups": payload})
                _db_enqueue(lambda _v=payload: db_save_settings({"muted_groups": _v}))
                try:
                    db_log_audit(user, h.client_address[0],
                                 "device_group_mute",
                                 group_name, "1" if want else "0")
                except Exception:
                    pass
                # Invalidate cached device status for every device in the
                # group so the UI's refresh picks up the mute-driven change.
                with STATE._lock:
                    for _dev in STATE.devices.values():
                        if (_dev.group or "") == group_name:
                            _dev.invalidate_status()
            h._json(200, {"ok": True, "muted": want, "group": group_name})
            return True

    return False
