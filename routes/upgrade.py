"""
routes/upgrade.py — admin endpoints for the managed server self-upgrade.

    POST /api/upgrade/image      upload a signed image (octet-stream zip); verify,
                                 snapshot the DB, stage the new release, restart
    GET  /api/upgrade/status     current managed state + last upgrade outcome
    POST /api/upgrade/rollback   revert to the previous release + DB snapshot

All endpoints require the admin role and are audit-logged. The image upload reads
its own (potentially large) body, so server.py dispatches it BEFORE _body() —
mirroring the DB-import route. Verification + staging live in core/upgrade.py; this
module is the thin HTTP layer.
"""

import secrets
import threading
import time

import core.app_state as app_state
from core import upgrade as up
from core.logger import log
from db.audit import db_log_audit

_MAX_IMAGE = 512 * 1024 * 1024   # 512 MB — an image is code-only (a few MB); generous cap


def _restart_soon(delay=1.5):
    """Let the HTTP response flush, then hand control to the bootstrap supervisor
    (it applies the staged swap / rollback on respawn)."""
    def _go():
        time.sleep(delay)
        up.request_restart()
    threading.Thread(target=_go, name="upgrade-restart", daemon=True).start()


def handle(h, method, path, body):
    if path == "/api/upgrade/status" and method == "GET":
        user, _ = h._require("admin")
        if not user:
            return True
        h._json(200, {
            "ok": True,
            "managed": up.is_managed(),
            "current": up.current_release(),
            "state": up.load_state(),
            "last_outcome": up.read_report(),
        })
        return True

    if path == "/api/upgrade/image" and method == "POST":
        user, _ = h._require("admin")
        if not user:
            return True
        if not up.is_managed():
            h._json(409, {"error": "server is not in the managed (releases/) layout; "
                                   "run tools/convert_to_managed.py first"})
            return True
        try:
            n = int(h.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            h._json(400, {"error": "missing request body"})
            return True
        if n > _MAX_IMAGE:
            h._json(413, {"error": "image too large"})
            return True
        from routes.export import _read_body_spooled
        try:
            zip_bytes = _read_body_spooled(h, n)
        except Exception as e:
            h._error(500, "upload failed", e, context="upgrade_upload")
            return True
        if zip_bytes[:4] != b"PK\x03\x04":
            h._json(400, {"error": "not a zip image"})
            return True

        upgrade_id = "u-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        try:
            manifest = up.stage_image(zip_bytes, upgrade_id, app_state.APP_VERSION)
        except up.ImageError as e:
            # Curated, admin-safe reason (see ImageError docstring).
            log.warning("upgrade image rejected: %s", e)
            h._json(400, {"error": str(e)})
            return True
        except Exception as e:
            h._error(500, "staging failed", e, context="upgrade_stage")
            return True

        version = up.manifest_version(manifest)
        db_log_audit(user, h.client_address[0], "server_upgrade_staged", version,
                     f"from={up.current_release()} to={version} id={upgrade_id} "
                     f"size={len(zip_bytes)}")
        log.info("upgrade staged by %s: %s -> %s", user, up.current_release(), version)
        h._json(200, {"ok": True, "version": version, "restarting": True})
        _restart_soon()
        return True

    if path == "/api/upgrade/rollback" and method == "POST":
        user, _ = h._require("admin")
        if not user:
            return True
        st = up.load_state()
        if not st.get("previous") or not st.get("db_snapshot"):
            h._json(409, {"error": "no rollback target available"})
            return True
        st["phase"] = "rollback_requested"
        up.save_state(st)
        db_log_audit(user, h.client_address[0], "server_upgrade_rollback",
                     st.get("previous") or "", f"to={st.get('previous')}")
        log.info("upgrade rollback requested by %s -> %s", user, st.get("previous"))
        h._json(200, {"ok": True, "restarting": True})
        _restart_soon()
        return True

    return False
