"""
core/device_importer.py — shared bulk device creator + delta reconciler.

Used by:
  - routes/discovery.py::bulk_add     (Subnet Discovery "add new hosts")
  - routes/imports.py::apply          (Bulk Device Import from file)

Discovery uses only `create_devices_batch()` — add-only semantics, always
skip existing. Bulk Import uses `reconcile_devices_batch()` on top — delta
semantics (add / update / replace) driven by `devices.external_id`.

Both paths share:
  - sensor field allow-list per stype (defense in depth)
  - host deduplication against live STATE + secondary_ips
  - sensor creation via STATE.add_sensor + start_sensor
  - IPAM sync
  - single end-of-batch db_save

NO HTTP concerns here — callers are responsible for auth, audit, response
serialization. This module is pure domain logic.
"""
from __future__ import annotations


from __future__ import annotations

import core.app_state as app_state
from core.logger import log
from core.validation import validate_host
from db import _db_enqueue, db_save
from db.ipam import ipam_sync_device_add

STATE = app_state.STATE


# ── Sensor field allow-list per stype ──────────────────────────────
# Canonical source of truth. The import parsers and the discovery path
# all validate incoming sensor specs against this whitelist. Unknown
# fields are silently dropped (defense in depth against an attacker
# crafting a file with `{"pw_hash": "__saml__"}` etc.).

ALLOWED_SENSOR_FIELDS = {
    "ping":    set(),
    "tcp":     {"port"},
    "http":    {"port", "url", "verify_ssl", "http_expected_status",
                "keyword", "keyword_case"},
    "http_keyword": {"port", "url", "verify_ssl", "keyword", "keyword_case"},
    "tls":     {"port"},
    "snmp":    {"port", "snmp_community", "snmp_version", "snmp_oid", "snmp_unit"},
    "banner":  {"port", "banner_regex"},
    "dns":     {"port", "dns_query", "dns_record_type", "dns_server"},
    "vmware":  {"vmware_user", "vmware_password", "vmware_vm_id",
                "vmware_vm_name", "vmware_metric"},
    "smtp":    {"port", "smtp_tls", "smtp_user", "smtp_password",
                "smtp_from", "smtp_rcpt", "smtp_test_level"},
    "ssh":     {"port", "ssh_user", "ssh_password", "ssh_private_key",
                "ssh_auth_type", "ssh_test_level"},
    "sftp":    {"port", "sftp_user", "sftp_password", "sftp_private_key",
                "sftp_auth_type", "sftp_test_level", "sftp_remote_path",
                "sftp_expected_sha256"},
    "radius":  {"port", "radius_secret", "radius_test_level",
                "radius_username", "radius_password", "radius_nas_id"},
}

# Sensor types we'll never accept from an import file — these need
# specialized flows (SNMP trap rules, etc.) and don't fit the generic
# "device-and-sensors" shape.
_REJECTED_STYPES = set()

# Full catalog — used by parsers to validate `stype` values.
VALID_STYPES = set(ALLOWED_SENSOR_FIELDS.keys())


def build_sensor_kwargs(spec: dict) -> tuple[dict, str]:
    """Filter incoming sensor spec to fields valid for its stype.

    Returns `(kwargs_dict_ready_for_add_sensor, stype)`. Unknown fields
    are silently dropped. Unknown stype falls back to 'ping'.
    """
    stype = str(spec.get("stype", "ping")).strip().lower()
    if stype not in ALLOWED_SENSOR_FIELDS:
        stype = "ping"
    allowed = ALLOWED_SENSOR_FIELDS[stype]
    out = {}
    for key in allowed:
        if key in spec and spec[key] not in (None, ""):
            out[key] = spec[key]
    # Per-stype defaults — matches the single-device add flow.
    if stype == "snmp":
        out.setdefault("snmp_community", "public")
        out.setdefault("snmp_version",   "2c")
        out.setdefault("snmp_oid",       "1.3.6.1.2.1.1.3.0")   # sysUpTime
    if stype == "smtp":
        out.setdefault("smtp_test_level", "ehlo")
        out.setdefault("smtp_tls",        "starttls")
    if stype == "ssh":
        out.setdefault("ssh_test_level", "banner")
        out.setdefault("ssh_auth_type",  "password")
    if stype == "sftp":
        out.setdefault("sftp_test_level", "open")
        out.setdefault("sftp_auth_type",  "password")
    if stype == "radius":
        out.setdefault("radius_test_level", "reachable")
    return out, stype


# ── Lookup helpers (STATE-backed) ──────────────────────────────────

def find_device_by_external_id(ext_id: str):
    """Return the live Device with matching external_id, or None.
    Takes STATE._lock briefly; safe to call from request threads.
    """
    if not ext_id:
        return None
    ext_id = ext_id.strip()
    with STATE._lock:
        for d in STATE.devices.values():
            if (getattr(d, "external_id", None) or "").strip() == ext_id:
                return d
    return None


def find_device_by_host(host: str):
    """Return the live Device whose primary host (lowercased) matches,
    or None. Does NOT match secondary_ips — that's duplicate-detection,
    not identity. A device with host=10.0.0.1 and secondary_ips=[10.0.0.2]
    matches on host only.
    """
    if not host:
        return None
    host = host.lower().strip()
    with STATE._lock:
        for d in STATE.devices.values():
            if (getattr(d, "host", "") or "").lower().strip() == host:
                return d
    return None


def _snapshot_existing_hosts() -> set:
    """Return the set of lowercased hosts + secondary_ips currently monitored.
    Used for duplicate rejection in add-only flows.
    """
    hosts = set()
    with STATE._lock:
        for d in STATE.devices.values():
            h = (d.host or "").lower()
            if h:
                hosts.add(h)
            for sip in getattr(d, "secondary_ips", []) or []:
                if sip:
                    hosts.add(sip.lower())
    return hosts


# ── Add-only path — Discovery + Import "add_only" mode ─────────────

def create_devices_batch(devices: list, default_group: str = "Imported") -> dict:
    """Create new devices + sensors from a normalized list.

    Add-only semantics: any device whose host matches an existing one
    (primary or secondary_ips) is skipped with `error="already monitored"`.
    Callers that want delta behavior (update existing) should use
    `reconcile_devices_batch()` instead.

    Each entry in `devices` is a dict with keys:
      name (required), host (required), group, external_id (optional),
      snmp_community_default, snmp_version_default, webhook_url (optional),
      sensors: [ {name, stype, <per-type fields>}, ... ]

    Returns: {created: [{did, host, name, sensors}],
              errors: [{index, host, error}]}
    """
    if not isinstance(devices, list):
        return {"created": [], "errors": [{"index": 0, "host": "",
                                            "error": "devices must be a list"}]}

    existing_hosts = _snapshot_existing_hosts()
    created: list = []
    errors:  list = []

    for idx, item in enumerate(devices):
        try:
            if not isinstance(item, dict):
                errors.append({"index": idx, "host": "", "error": "invalid item"})
                continue
            name  = str(item.get("name", "")).strip()[:255]
            host  = str(item.get("host", "")).lower().strip()[:253]
            group = (str(item.get("group", "")).strip() or default_group)[:255]
            site  = str(item.get("site", "") or "").strip()[:80]

            if not name or not host:
                errors.append({"index": idx, "host": host,
                               "error": "name and host required"})
                continue
            try:
                host = validate_host(host)
            except ValueError as ve:
                errors.append({"index": idx, "host": host, "error": str(ve)})
                continue
            if host in existing_hosts:
                errors.append({"index": idx, "host": host,
                               "error": "already monitored"})
                continue

            did = STATE.add_device(name, host, group, site=site)
            if not did:
                errors.append({"index": idx, "host": host, "error": "create failed"})
                continue
            existing_hosts.add(host)

            # Apply device-level fields the add_device constructor doesn't set.
            with STATE._lock:
                dev = STATE.devices.get(did)
                if dev:
                    ext_id = (item.get("external_id") or "").strip() or None
                    if ext_id:
                        dev.external_id = ext_id
                    for fld in ("webhook_url", "snmp_community_default",
                                "snmp_version_default"):
                        v = item.get(fld)
                        if v not in (None, ""):
                            setattr(dev, fld, str(v))
                    # Auto-Discovery breadcrumb — set only when the caller
                    # passed both fields. Manual / Bulk-Import leave them at
                    # their Device() defaults (0.0 / "").
                    d_at = item.get("discovered_at")
                    if d_at:
                        try:
                            dev.discovered_at = float(d_at)
                        except (TypeError, ValueError):
                            pass
                    d_cidr = item.get("discovered_from_cidr")
                    if d_cidr:
                        dev.discovered_from_cidr = str(d_cidr)[:64]

            # Sensors
            sensor_sids = _create_sensors_for_device(did, item.get("sensors") or [])

            created.append({"did": did, "host": host, "name": name,
                            "sensors": sensor_sids})

            _did, _name, _host = did, name, host
            _db_enqueue(
                lambda d=_did, n=_name, ho=_host: ipam_sync_device_add(d, n, ho)
            )
        except Exception as e:
            log.error(f"device_importer create_devices_batch: create failed: {e}")
            errors.append({
                "index": idx,
                "host": str(item.get("host", "")) if isinstance(item, dict) else "",
                "error": "create failed"
            })

    _db_enqueue(lambda: db_save(STATE))
    return {"created": created, "errors": errors}


def _create_sensors_for_device(did: str, sensor_specs: list) -> list:
    """Create + start each sensor for the given device. Returns list of sids
    successfully created. Failures logged, not raised."""
    sids: list = []
    for s in sensor_specs or []:
        if not isinstance(s, dict):
            continue
        s_name = str(s.get("name") or "Sensor").strip()[:255] or "Sensor"
        kwargs, s_stype = build_sensor_kwargs(s)
        try:
            sid = STATE.add_sensor(did, s_name, s_stype, **kwargs)
            if sid is not None:
                try:
                    STATE.start_sensor(did, sid)
                except Exception:
                    pass
                sids.append(sid)
        except Exception as e:
            log.warning(f"device_importer: sensor create failed "
                        f"(did={did} stype={s_stype}): {e}")
    return sids


# ── Delta path — Import "add_update" / "replace" modes ─────────────

_VALID_MODES = ("add_only", "add_update", "replace")

# Device fields the importer considers "safe to overwrite from the file".
# Excludes things like `did` (identity) and `secondary_ips` (derived).
_DEVICE_UPDATABLE_FIELDS = (
    "name", "host", "group", "webhook_url",
    "snmp_community_default", "snmp_version_default",
)


def preview_match(devices: list) -> list:
    """Annotate each item with match_status / match_did / match_diff.

    Called by /api/import/parse to populate the preview table. Does NOT
    mutate state. External_id match wins over host match.

    Returns the *same* list, each dict mutated in place with added fields:
      - match_status: "new" | "update"
      - match_did:    did of matched device, or None
      - match_diff:   {field: [old, new]} for updated fields (empty if no changes)

    Caller should also call `find_orphans(devices)` to get the full orphan list.
    """
    for item in devices:
        if not isinstance(item, dict):
            continue
        existing = None
        ext_id = (item.get("external_id") or "").strip()
        if ext_id:
            existing = find_device_by_external_id(ext_id)
        if existing is None:
            existing = find_device_by_host(str(item.get("host", "")).lower().strip())

        if existing is None:
            item["match_status"] = "new"
            item["match_did"]    = None
            item["match_diff"]   = {}
            continue

        # Compute diff — what would change if we applied the update
        diff: dict = {}
        new_name  = str(item.get("name", "")).strip()
        new_host  = str(item.get("host", "")).lower().strip()
        new_group = str(item.get("group", "")).strip()
        if new_name  and new_name  != existing.name:  diff["name"]  = [existing.name,  new_name]
        if new_host  and new_host  != (existing.host or "").lower(): diff["host"] = [existing.host, new_host]
        if new_group and new_group != existing.group: diff["group"] = [existing.group, new_group]
        for fld in ("webhook_url", "snmp_community_default", "snmp_version_default"):
            v = item.get(fld)
            if v not in (None, ""):
                cur = getattr(existing, fld, "") or ""
                if str(v) != cur:
                    diff[fld] = [cur, str(v)]
        # Sensor-level diff — count additions only, no "modified" granularity
        # in the preview (keeps the UI readable; full diff is in apply).
        new_sensor_names = {
            str(s.get("name") or "").strip().lower()
            for s in (item.get("sensors") or []) if isinstance(s, dict)
        }
        cur_sensor_names = {
            (s.name or "").strip().lower()
            for s in existing.sensors.values()
        }
        to_add = new_sensor_names - cur_sensor_names
        if to_add:
            diff["sensors_added"] = sorted(to_add)

        item["match_status"] = "update"
        item["match_did"]    = existing.device_id
        item["match_diff"]   = diff
    return devices


def find_orphans(devices: list) -> list:
    """Return devices in STATE that aren't referenced by the import list.

    A device is orphan if neither its external_id nor its host matches any
    entry in `devices`. Used by `replace` mode to report what would be deleted.
    """
    ext_ids  = {(d.get("external_id") or "").strip()
                for d in devices if isinstance(d, dict) and d.get("external_id")}
    hosts    = {str(d.get("host", "")).lower().strip()
                for d in devices if isinstance(d, dict) and d.get("host")}
    orphans: list = []
    with STATE._lock:
        for dev in STATE.devices.values():
            dev_ext  = (getattr(dev, "external_id", None) or "").strip()
            dev_host = (dev.host or "").lower().strip()
            if dev_ext and dev_ext in ext_ids:
                continue
            if dev_host in hosts:
                continue
            orphans.append({"did": dev.device_id, "host": dev.host,
                            "name": dev.name})
    return orphans


def reconcile_devices_batch(devices: list, mode: str = "add_update",
                            default_group: str = "Imported") -> dict:
    """Delta-aware batch creator for bulk device import.

    Mode semantics:
      - add_only:   create new, skip existing   (same as create_devices_batch)
      - add_update: create new, update matched  (default)
      - replace:    create new, update matched, DELETE orphans

    For `add_update` / `replace`, a device is matched if either:
      1. `external_id` in the file matches a live device's external_id, OR
      2. `host` matches a live device's primary host.

    Update behavior — conservative:
      - Device-level: overwrite name/host/group/snmp_*_default/webhook_url
        if the file value is non-empty. Never blanks existing values.
      - Sensors: merge by name. File-only → add. Name match → update. DB-only
        → kept (not deleted). Admins preserve hand-added sensors.

    Returns: {created, updated, deleted, skipped, errors}
    """
    if mode not in _VALID_MODES:
        mode = "add_update"

    # Add-only short-circuit — reuse the existing path
    if mode == "add_only":
        res = create_devices_batch(devices, default_group=default_group)
        return {"created": res["created"], "updated": [], "deleted": [],
                "skipped": [],
                "errors": res["errors"]}

    created: list = []
    updated: list = []
    deleted: list = []
    skipped: list = []
    errors:  list = []

    # Match-first pass (avoids repeated iteration over STATE.devices).
    preview_match(devices)

    for idx, item in enumerate(devices):
        try:
            if not isinstance(item, dict):
                errors.append({"index": idx, "host": "", "error": "invalid item"})
                continue
            name  = str(item.get("name", "")).strip()[:255]
            host  = str(item.get("host", "")).lower().strip()[:253]
            group = (str(item.get("group", "")).strip() or default_group)[:255]
            ext_id = (item.get("external_id") or "").strip() or None

            if not name or not host:
                errors.append({"index": idx, "host": host,
                               "error": "name and host required"})
                continue
            try:
                host = validate_host(host)
            except ValueError as ve:
                errors.append({"index": idx, "host": host, "error": str(ve)})
                continue

            match_did = item.get("match_did")
            if match_did:
                # UPDATE path — device exists
                changed = _update_device_inplace(
                    match_did, name, host, group, ext_id, item)
                if changed is None:
                    errors.append({"index": idx, "host": host,
                                   "error": "update failed"})
                    continue
                # Merge sensors
                sensor_change = _merge_sensors_for_device(
                    match_did, item.get("sensors") or [])
                updated.append({
                    "did": match_did,
                    "host": host,
                    "name": name,
                    "changed_fields": changed,
                    "sensors_added": sensor_change.get("added", []),
                    "sensors_updated": sensor_change.get("updated", []),
                })
            else:
                # NEW path — create device + sensors
                did = STATE.add_device(name, host, group)
                if not did:
                    errors.append({"index": idx, "host": host,
                                   "error": "create failed"})
                    continue
                # Apply device-level extras (including external_id)
                with STATE._lock:
                    dev = STATE.devices.get(did)
                    if dev:
                        if ext_id:
                            dev.external_id = ext_id
                        for fld in ("webhook_url", "snmp_community_default",
                                    "snmp_version_default"):
                            v = item.get(fld)
                            if v not in (None, ""):
                                setattr(dev, fld, str(v))
                sensor_sids = _create_sensors_for_device(
                    did, item.get("sensors") or [])
                created.append({"did": did, "host": host, "name": name,
                                "sensors": sensor_sids})
                _did, _name, _host = did, name, host
                _db_enqueue(
                    lambda d=_did, n=_name, ho=_host: ipam_sync_device_add(d, n, ho)
                )
        except Exception as e:
            log.error(f"device_importer reconcile: entry failed: {e}")
            errors.append({
                "index": idx,
                "host": str(item.get("host", "")) if isinstance(item, dict) else "",
                "error": "create failed"
            })

    # Orphan handling — only in replace mode.
    if mode == "replace":
        orphans = find_orphans(devices)
        for o in orphans:
            try:
                if STATE.delete_device(o["did"]):
                    deleted.append(o)
                else:
                    skipped.append({"host": o["host"],
                                    "reason": "delete failed"})
            except Exception as e:
                log.error(f"device_importer reconcile: delete failed for "
                          f"{o['did']}: {e}")
                skipped.append({"host": o["host"], "reason": "delete crashed"})

    _db_enqueue(lambda: db_save(STATE))
    return {"created": created, "updated": updated, "deleted": deleted,
            "skipped": skipped, "errors": errors}


def _update_device_inplace(did: str, name: str, host: str, group: str,
                           ext_id: str | None, item: dict) -> list | None:
    """Apply file values to an existing Device. Returns list of changed fields,
    or None on failure. Only overwrites when the incoming value is non-empty.
    """
    with STATE._lock:
        dev = STATE.devices.get(did)
        if dev is None:
            return None
        changed: list = []
        if name and name != dev.name:
            dev.name = name
            changed.append("name")
        if host and host != (dev.host or "").lower():
            dev.host = host
            changed.append("host")
        if group and group != dev.group:
            dev.group = group
            changed.append("group")
        if ext_id:
            cur_ext = getattr(dev, "external_id", None) or ""
            if not cur_ext:
                dev.external_id = ext_id
                changed.append("external_id")
            elif cur_ext != ext_id:
                # Defensive — don't overwrite existing external_id silently.
                # The match logic should have prevented this; log and skip.
                log.warning(f"device_importer: refusing to overwrite "
                            f"external_id on {did}: {cur_ext!r} → {ext_id!r}")
        for fld in ("webhook_url", "snmp_community_default",
                    "snmp_version_default"):
            v = item.get(fld)
            if v not in (None, ""):
                cur = getattr(dev, fld, "") or ""
                if str(v) != cur:
                    setattr(dev, fld, str(v))
                    changed.append(fld)
        return changed


def _merge_sensors_for_device(did: str, sensor_specs: list) -> dict:
    """Merge sensors by name. File sensor + matching DB sensor → update fields.
    File sensor with no match → add. DB sensor not in file → keep (never delete
    in add_update / replace mode sensor merge — see plan for rationale).
    Returns {added: [names], updated: [names]}.
    """
    added:   list = []
    updated: list = []
    with STATE._lock:
        dev = STATE.devices.get(did)
        if dev is None:
            return {"added": added, "updated": updated}
        by_name = {(s.name or "").strip().lower(): s
                   for s in dev.sensors.values()}
    for s in sensor_specs or []:
        if not isinstance(s, dict):
            continue
        s_name_raw = str(s.get("name") or "").strip()[:255]
        if not s_name_raw:
            continue
        key = s_name_raw.lower()
        kwargs, stype = build_sensor_kwargs(s)
        if key in by_name:
            # UPDATE — overwrite per-type fields in place
            sensor = by_name[key]
            try:
                STATE.update_sensor(did, sensor.sensor_id, **kwargs)
                updated.append(s_name_raw)
            except Exception as e:
                log.warning(f"device_importer: sensor update failed "
                            f"({did}/{sensor.sensor_id}): {e}")
        else:
            # ADD — new sensor on existing device
            try:
                sid = STATE.add_sensor(did, s_name_raw, stype, **kwargs)
                if sid is not None:
                    try:
                        STATE.start_sensor(did, sid)
                    except Exception:
                        pass
                    added.append(s_name_raw)
            except Exception as e:
                log.warning(f"device_importer: sensor create failed "
                            f"(merge, did={did} stype={stype}): {e}")
    return {"added": added, "updated": updated}
