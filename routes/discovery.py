"""routes/discovery.py — Subnet discovery scan endpoints + bulk device add.

Endpoints:
  POST   /api/discovery/scan              { cidr, skip_monitored, mode }  → 202 {scan_id}
  GET    /api/discovery/scan/<scan_id>                                    → progress + results
  DELETE /api/discovery/scan/<scan_id>                                    → cancel
  POST   /api/discovery/bulk-add          { devices: [...] }              → {created, errors}
"""
import core.app_state as app_state
from core.config import (
    _RE_DISCOVERY_SCAN, _RE_DISCOVERY_STATUS, _RE_DISCOVERY_BULK_ADD,
)
from db import _db_enqueue, db_save, db_log_audit
from db.ipam import ipam_sync_device_add
from core.logger import log
from monitoring.subnet_discovery import start_scan, get_scan, cancel_scan

STATE = app_state.STATE

# ── Sensor field whitelist per stype (defense in depth) ────────────
_ALLOWED_SENSOR_FIELDS = {
    "ping":   set(),
    "tcp":    {"port"},
    "http":   {"port", "url", "verify_ssl"},
    "tls":    {"port"},
    "snmp":   {"port", "snmp_community", "snmp_version", "snmp_oid"},
    "banner": {"port", "banner_regex"},
    "dns":    {"port"},
}


def _build_sensor_kwargs(spec: dict) -> dict:
    """Filter incoming sensor spec to fields valid for its stype.

    Returns a kwargs dict ready to pass to STATE.add_sensor().
    Unknown fields are silently dropped.
    """
    stype = str(spec.get("stype", "ping")).strip().lower()
    if stype not in _ALLOWED_SENSOR_FIELDS:
        stype = "ping"
    allowed = _ALLOWED_SENSOR_FIELDS[stype]
    out = {}
    for key in allowed:
        if key in spec and spec[key] not in (None, ""):
            out[key] = spec[key]
    # Defaults that match the single-device add flow
    if stype == "snmp":
        out.setdefault("snmp_community", "public")
        out.setdefault("snmp_version", "2c")
        out.setdefault("snmp_oid", "1.3.6.1.2.1.1.3.0")  # sysUpTime
    if stype == "http" and "url" in out:
        # add_sensor takes `url=`, not http_url
        pass
    return out, stype


def handle(h, method, path, body):
    # ── POST /api/discovery/scan ───────────────────────────────────
    if _RE_DISCOVERY_SCAN.match(path):
        if method == "POST":
            user, role = h._require("operator")
            if not user:
                return True
            cidr = str(body.get("cidr", "")).strip()
            if not cidr:
                h._json(400, {"error": "cidr required"}); return True
            if len(cidr) > 64:
                h._json(400, {"error": "cidr too long"}); return True
            skip = bool(body.get("skip_monitored", True))
            mode = str(body.get("mode", "full")).strip().lower()
            if mode not in ("full", "ping"):
                h._json(400, {"error": "mode must be 'full' or 'ping'"}); return True

            scan_id, err = start_scan(cidr, skip, mode)
            if err:
                h._json(400, {"error": err}); return True
            try:
                db_log_audit(user, h.client_address[0],
                             "subnet_scan_start", f"{cidr} mode={mode}")
            except Exception:
                pass
            h._json(202, {"scan_id": scan_id, "cidr": cidr, "mode": mode})
            return True

    # ── /api/discovery/scan/<id>  GET=poll  DELETE=cancel ──────────
    m = _RE_DISCOVERY_STATUS.match(path)
    if m:
        scan_id = m.group(1)
        if method == "GET":
            user, _ = h._require("viewer")
            if not user:
                return True
            st = get_scan(scan_id)
            if not st:
                h._json(404, {"error": "scan not found"}); return True
            # Drop internal flags
            resp = {k: v for k, v in st.items() if k != "cancel"}
            h._json(200, resp)
            return True
        if method == "DELETE":
            user, _ = h._require("operator")
            if not user:
                return True
            ok = cancel_scan(scan_id)
            try:
                db_log_audit(user, h.client_address[0],
                             "subnet_scan_cancel", scan_id)
            except Exception:
                pass
            h._json(200 if ok else 404, {"ok": ok})
            return True

    # ── POST /api/discovery/bulk-add ───────────────────────────────
    if _RE_DISCOVERY_BULK_ADD.match(path) and method == "POST":
        user, role = h._require("operator")
        if not user:
            return True

        items = body.get("devices") or []
        if not isinstance(items, list) or not items:
            h._json(400, {"error": "devices list required"}); return True
        if len(items) > 500:
            h._json(400, {"error": "too many devices (max 500 per call)"}); return True

        # Snapshot existing hosts + secondary IPs (lowercased) for dedup.
        with STATE._lock:
            existing_hosts = {
                (d.host or "").lower()
                for d in STATE.devices.values() if getattr(d, "host", "")
            }
            for d in STATE.devices.values():
                for sip in getattr(d, "secondary_ips", []) or []:
                    if sip:
                        existing_hosts.add(sip.lower())

        created, errors = [], []
        for idx, item in enumerate(items):
            try:
                if not isinstance(item, dict):
                    errors.append({"index": idx, "host": "", "error": "invalid item"})
                    continue
                name  = str(item.get("name", "")).strip()[:255]
                host  = str(item.get("host", "")).lower().strip()[:253]
                group = str(item.get("group", "Discovered")).strip() or "Discovered"

                if not name or not host:
                    errors.append({"index": idx, "host": host,
                                   "error": "name and host required"})
                    continue
                if not h._valid_host(host):
                    errors.append({"index": idx, "host": host,
                                   "error": "invalid host"})
                    continue
                if host in existing_hosts:
                    errors.append({"index": idx, "host": host,
                                   "error": "already monitored"})
                    continue

                did = STATE.add_device(name, host, group)
                if not did:
                    errors.append({"index": idx, "host": host,
                                   "error": "create failed"})
                    continue
                existing_hosts.add(host)

                # Create requested sensors
                sensor_sids = []
                for s in (item.get("sensors") or []):
                    if not isinstance(s, dict):
                        continue
                    s_name = str(s.get("name", "Sensor")).strip()[:255] or "Sensor"
                    kwargs, s_stype = _build_sensor_kwargs(s)
                    try:
                        sid = STATE.add_sensor(did, s_name, s_stype, **kwargs)
                        if sid is not None:
                            try:
                                STATE.start_sensor(did, sid)
                            except Exception:
                                pass
                            sensor_sids.append(sid)
                    except Exception as e:
                        log.warning(
                            f"discovery bulk-add: sensor create failed "
                            f"({host} {s_stype}): {e}"
                        )

                created.append({"did": did, "host": host, "name": name,
                                "sensors": sensor_sids})

                _did, _name, _host = did, name, host
                _db_enqueue(
                    lambda d=_did, n=_name, ho=_host: ipam_sync_device_add(d, n, ho)
                )
            except Exception as e:
                log.error(f"discovery bulk-add: device create failed: {e}")
                errors.append({"index": idx,
                               "host": str(item.get("host", "")) if isinstance(item, dict) else "",
                               "error": "create failed"})

        # Single persist after the whole batch
        _db_enqueue(lambda: db_save(STATE))
        try:
            db_log_audit(user, h.client_address[0],
                         "subnet_bulk_add",
                         f"created={len(created)} errors={len(errors)}")
        except Exception:
            pass
        h._json(200, {"created": created, "errors": errors})
        return True

    return False
