"""
monitoring/license_checker.py — Periodic license expiration check.

Called from autosave_loop every ~6 hours.  Compares each license's
expiry_date against today, fires flap events on state transitions
(ok↔warn↔crit), and broadcasts via SSE.
"""
from __future__ import annotations

import datetime
import time

from core.logger import log


def check_license_expirations() -> None:
    """Check all license records and fire events on state changes."""
    from core.app_state import STATE
    from db.licenses import db_get_all_licenses, db_update_license_status
    from db.events   import db_log_flap, db_auto_resolve_flap

    licenses = db_get_all_licenses()
    if not licenses:
        return

    today = datetime.date.today()
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for lic in licenses:
        try:
            expiry = datetime.date.fromisoformat(lic["expiry_date"])
        except (ValueError, TypeError):
            continue

        days_left = (expiry - today).days

        # Determine new status
        crit_days = int(lic.get("crit_days") or 0)
        warn_days = int(lic.get("warn_days") or 30)
        new_status = "ok"
        if days_left <= crit_days:
            new_status = "crit"
        elif days_left <= warn_days:
            new_status = "warn"

        old_status = lic.get("last_status") or "ok"
        if new_status == old_status:
            continue

        # State changed — update DB
        db_update_license_status(lic["id"], new_status)

        # Resolve device info
        did = lic["did"]
        dev = STATE.devices.get(did)
        dname = dev.name if dev else did
        host  = dev.host if dev else ""
        sid   = f"lic_{lic['id']}"

        if new_status == "ok":
            # Recovery
            direction = "license_ok"
            detail = (
                f"License '{lic['license_name']}' renewed — "
                f"expires {lic['expiry_date']} ({days_left} days)"
            )
            log.info(f"LICENSE OK: {dname}/{lic['license_name']} — "
                     f"expires {lic['expiry_date']} ({days_left} days)")
            # Auto-resolve any active flap for this license
            db_auto_resolve_flap(did, sid, ts,
                                 directions=("license_warn", "license_crit"))
        elif new_status == "crit":
            direction = "license_crit"
            if days_left < 0:
                detail = (
                    f"License '{lic['license_name']}' EXPIRED "
                    f"{-days_left} day{'s' if -days_left != 1 else ''} ago "
                    f"({lic['expiry_date']})"
                )
            else:
                detail = (
                    f"License '{lic['license_name']}' expires "
                    f"{lic['expiry_date']} ({days_left} day{'s' if days_left != 1 else ''} left)"
                )
            log.error(f"LICENSE CRIT: {dname}/{lic['license_name']} — {detail}")
        else:
            direction = "license_warn"
            detail = (
                f"License '{lic['license_name']}' expiring soon — "
                f"{lic['expiry_date']} ({days_left} day{'s' if days_left != 1 else ''} left)"
            )
            log.warning(f"LICENSE WARN: {dname}/{lic['license_name']} — {detail}")

        # Log flap event (shows in Events tab)
        if new_status != "ok":
            flap = {
                "ts":        ts,
                "did":       did,
                "sid":       sid,
                "dname":     dname,
                "sname":     lic["license_name"],
                "host":      host,
                "stype":     "license",
                "detail":    detail,
                "direction": direction,
            }
            db_log_flap(flap)

        # SSE broadcast for real-time UI update
        STATE._broadcast("license_status", {
            "did":          did,
            "license_id":   lic["id"],
            "license_name": lic["license_name"],
            "status":       new_status,
            "days_left":    days_left,
            "detail":       detail,
        })
