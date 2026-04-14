"""
routes/licenses.py — Device license tracking API endpoints.

GET    /api/device/<did>/licenses       → list licenses for device (viewer)
POST   /api/device/<did>/licenses       → add license  (operator)
PATCH  /api/license/<id>                → update license (operator)
DELETE /api/license/<id>                → delete license (operator)
GET    /api/licenses                    → all licenses (viewer)
GET    /api/licenses/summary            → counts by status (viewer)
POST   /api/licenses/check              → trigger manual expiration check (admin)
"""

from core.config import (
    _RE_DEVICE_LICENSES,
    _RE_LICENSE_ITEM,
    _RE_LICENSES_ALL,
    _RE_LICENSES_SUMMARY,
    _RE_LICENSES_CHECK,
)
from db import (
    db_get_licenses,
    db_get_all_licenses,
    db_add_license,
    db_update_license,
    db_delete_license,
    db_license_summary,
    db_log_audit,
)


def handle(h, method, path, body) -> bool:

    # ── GET /api/device/<did>/licenses ─────────────────────────────
    m = _RE_DEVICE_LICENSES.match(path)
    if m and method == 'GET':
        auth = h._auth()
        if not auth:
            return True
        did = m.group(1)
        rows = db_get_licenses(did)
        h._json(200, {"licenses": rows})
        return True

    # ── POST /api/device/<did>/licenses ────────────────────────────
    if m and method == 'POST':
        auth = h._require('operator')
        if not auth:
            return True
        username = auth[0]
        did = m.group(1)
        name = (body.get("license_name") or "").strip()
        expiry = (body.get("expiry_date") or "").strip()
        if not name or not expiry:
            h._json(400, {"error": "license_name and expiry_date are required"})
            return True
        note = (body.get("note") or "").strip()
        warn_days = int(body.get("warn_days", 30))
        crit_days = int(body.get("crit_days", 0))
        lic_id = db_add_license(did, name, expiry, note, warn_days, crit_days)
        if lic_id is None:
            h._json(500, {"error": "Failed to add license"})
            return True
        db_log_audit(username, "license_add",
                     f"Added license '{name}' (exp {expiry}) to device {did}")
        # Run an immediate status check for this license
        try:
            from monitoring.license_checker import check_license_expirations
            check_license_expirations()
        except Exception:
            pass
        h._json(201, {"id": lic_id, "licenses": db_get_licenses(did)})
        return True

    # ── PATCH /api/license/<id> ────────────────────────────────────
    m2 = _RE_LICENSE_ITEM.match(path)
    if m2 and method == 'PATCH':
        auth = h._require('operator')
        if not auth:
            return True
        username = auth[0]
        lic_id = int(m2.group(1))
        name = (body.get("license_name") or "").strip()
        expiry = (body.get("expiry_date") or "").strip()
        if not name or not expiry:
            h._json(400, {"error": "license_name and expiry_date are required"})
            return True
        note = (body.get("note") or "").strip()
        warn_days = int(body.get("warn_days", 30))
        crit_days = int(body.get("crit_days", 0))
        ok = db_update_license(lic_id, name, expiry, note, warn_days, crit_days)
        if not ok:
            h._json(404, {"error": "License not found"})
            return True
        db_log_audit(username, "license_update",
                     f"Updated license {lic_id}: '{name}' exp {expiry}")
        # Re-check after update (may trigger recovery if date was extended)
        try:
            from monitoring.license_checker import check_license_expirations
            check_license_expirations()
        except Exception:
            pass
        h._json(200, {"ok": True})
        return True

    # ── DELETE /api/license/<id> ───────────────────────────────────
    if m2 and method == 'DELETE':
        auth = h._require('operator')
        if not auth:
            return True
        username = auth[0]
        lic_id = int(m2.group(1))
        ok = db_delete_license(lic_id)
        if not ok:
            h._json(404, {"error": "License not found"})
            return True
        db_log_audit(username, "license_delete",
                     f"Deleted license {lic_id}")
        h._json(200, {"ok": True})
        return True

    # ── GET /api/licenses ──────────────────────────────────────────
    if _RE_LICENSES_ALL.match(path) and method == 'GET':
        auth = h._auth()
        if not auth:
            return True
        rows = db_get_all_licenses()
        h._json(200, {"licenses": rows})
        return True

    # ── GET /api/licenses/summary ──────────────────────────────────
    if _RE_LICENSES_SUMMARY.match(path) and method == 'GET':
        auth = h._auth()
        if not auth:
            return True
        h._json(200, db_license_summary())
        return True

    # ── POST /api/licenses/check ───────────────────────────────────
    if _RE_LICENSES_CHECK.match(path) and method == 'POST':
        auth = h._require('admin')
        if not auth:
            return True
        try:
            from monitoring.license_checker import check_license_expirations
            check_license_expirations()
        except Exception as e:
            h._json(500, {"error": "Check failed"})
            return True
        h._json(200, {"ok": True})
        return True

    return False
