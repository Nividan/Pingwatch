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
from core.device_importer import create_devices_batch
from db import _db_enqueue, db_log_audit
from db.ipam import db_list_subnets, db_add_subnet
from core.logger import log
from monitoring.subnet_discovery import start_scan, get_scan, cancel_scan

STATE = app_state.STATE

# NOTE: The sensor field allow-list + per-stype kwarg builder that used to live
# here were extracted to core/device_importer.py so both Discovery and the
# Bulk Import feature share the same validation and defaults. Callers should
# pass device dicts directly to create_devices_batch().


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

        # Add-only semantics — Discovery finds NEW hosts, never updates existing.
        # Delta reconciliation (add_update / replace) is intentionally reserved
        # for the Bulk Import feature's /api/import/apply endpoint.
        result = create_devices_batch(items, default_group="Discovered")
        created = result["created"]
        errors  = result["errors"]

        # Auto-add scanned CIDR to IPAM if it doesn't already exist,
        # then immediately back-populate allocations from the just-added devices.
        _cidr = str(body.get("cidr", "")).strip()
        if _cidr and "/" in _cidr and created:
            try:
                existing = {s["cidr"] for s in db_list_subnets()}
                if _cidr not in existing:
                    from db.ipam import ipam_sync_subnet_add
                    def _create_and_sync(_c=_cidr, _u=user):
                        try:
                            sid = db_add_subnet(_c, "Discovered", _u)
                            ipam_sync_subnet_add(sid, _c)
                        except Exception as _e:
                            log.warning(f"discovery IPAM subnet auto-create failed: {_e}")
                    _db_enqueue(_create_and_sync)
            except Exception:
                pass  # non-critical — don't fail the bulk add

        try:
            db_log_audit(user, h.client_address[0],
                         "subnet_bulk_add",
                         f"created={len(created)} errors={len(errors)}")
        except Exception:
            pass
        h._json(200, {"created": created, "errors": errors})
        return True

    return False
