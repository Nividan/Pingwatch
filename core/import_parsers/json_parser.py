"""core/import_parsers/json_parser.py — PingWatch native JSON import.

Round-trip-stable format. Same shape the (future) JSON export will produce.

Input shape:

    {
      "format_version": 1,
      "devices": [
        {
          "external_id": "json:router1",           # optional; defaults to json:<name>
          "name":        "router1",                 # required
          "host":        "10.0.0.1",                # required
          "group":       "Core",                    # optional
          "snmp_community_default": "public",       # optional
          "snmp_version_default":   "2c",           # optional
          "webhook_url":            "",             # optional
          "sensors": [
            {"name": "ICMP",   "stype": "ping"},
            {"name": "Uptime", "stype": "snmp",
             "snmp_oid": "1.3.6.1.2.1.1.3.0",
             "snmp_community": "public"}
          ]
        }
      ]
    }

Validation:
  - `format_version` must be 1 (reject 2+ so future schema changes are
    explicit rather than silently reinterpreting unfamiliar files).
  - Each device needs non-empty `name` + `host`.
  - Each sensor must declare an `stype` from the 13-type catalog
    (`VALID_STYPES` in core.device_importer). Unknown stypes are dropped
    from that device's sensor list with a row error naming the index.

Per-sensor field validation (e.g. "snmp_oid must be a string") happens
downstream in `build_sensor_kwargs()` — unknown fields are silently
dropped there as defense in depth. This parser's job is structural.
"""

from __future__ import annotations

import json

from core.device_importer import VALID_STYPES

SUPPORTED_FORMAT_VERSIONS = (1,)


def parse_json(text: str) -> dict:
    """Parse a PingWatch JSON import file.

    Returns canonical parse result (see core/import_parsers/__init__.py).
    Never raises — malformed input becomes a single row-0 error.
    """
    text = (text or "").strip()
    if not text:
        return _empty_result("empty file")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return _empty_result(f"JSON parse failed: {e.msg} (line {e.lineno}, col {e.colno})")
    except Exception:
        return _empty_result("JSON parse failed")

    if not isinstance(data, dict):
        return _empty_result("top-level value must be an object")

    fv = data.get("format_version")
    if fv not in SUPPORTED_FORMAT_VERSIONS:
        return _empty_result(
            f"unsupported format_version: {fv!r} (expected one of {list(SUPPORTED_FORMAT_VERSIONS)})"
        )

    raw_devices = data.get("devices")
    if not isinstance(raw_devices, list):
        return _empty_result("'devices' must be a list")

    devices: list = []
    errors:  list = []
    sensors_total   = 0
    sensors_dropped = 0

    for idx, raw in enumerate(raw_devices):
        if not isinstance(raw, dict):
            errors.append({"row": idx, "reason": "device entry must be an object"})
            continue

        name = _s(raw.get("name"))
        host = _s(raw.get("host"))
        if not name:
            errors.append({"row": idx, "reason": "missing required field: name"})
            continue
        if not host:
            errors.append({"row": idx, "reason": "missing required field: host"})
            continue

        sensors, s_total, s_dropped = _parse_sensors(raw.get("sensors"), row_idx=idx, errors=errors)
        sensors_total   += s_total
        sensors_dropped += s_dropped

        ext_id = _s(raw.get("external_id")) or f"json:{name}"

        dev: dict = {
            "external_id": ext_id,
            "name":  name,
            "host":  host,
            "group": _s(raw.get("group")),
            "sensors": sensors,
        }
        for fld in ("snmp_community_default", "snmp_version_default", "webhook_url"):
            v = _s(raw.get(fld))
            if v:
                dev[fld] = v
        devices.append(dev)

    mapping_report = {
        "sensors_total":   sensors_total,
        "sensors_mapped":  sensors_total - sensors_dropped,
        "sensors_skipped": [],  # populated below if any
    }
    if sensors_dropped:
        mapping_report["sensors_skipped"].append({
            "source_type": "unknown stype",
            "count":       sensors_dropped,
            "reason":      "sensor stype not in PingWatch catalog",
        })

    return {
        "devices":        devices,
        "errors":         errors,
        "mapping_report": mapping_report,
    }


def _parse_sensors(raw_sensors, row_idx: int, errors: list) -> tuple[list, int, int]:
    """Validate + normalize a device's sensor list.

    Returns `(kept_sensors, total_seen, total_dropped)`. Adds row-level
    errors for each dropped sensor so the review UI can show exactly
    which ones were skipped and why.
    """
    if raw_sensors is None:
        return [], 0, 0
    if not isinstance(raw_sensors, list):
        errors.append({"row": row_idx, "reason": "'sensors' must be a list"})
        return [], 0, 0

    kept:    list = []
    total:   int  = 0
    dropped: int  = 0
    for s_idx, s in enumerate(raw_sensors):
        total += 1
        if not isinstance(s, dict):
            errors.append({"row": row_idx,
                           "reason": f"sensor[{s_idx}]: must be an object"})
            dropped += 1
            continue
        stype = _s(s.get("stype")).lower()
        if not stype:
            errors.append({"row": row_idx,
                           "reason": f"sensor[{s_idx}]: missing stype"})
            dropped += 1
            continue
        if stype not in VALID_STYPES:
            errors.append({"row": row_idx,
                           "reason": f"sensor[{s_idx}]: unknown stype {stype!r}"})
            dropped += 1
            continue

        # Pass through — build_sensor_kwargs() downstream filters fields
        # against ALLOWED_SENSOR_FIELDS, so we don't need to whitelist here.
        clean = dict(s)
        clean["stype"] = stype
        if not _s(clean.get("name")):
            clean["name"] = stype.upper()
        kept.append(clean)
    return kept, total, dropped


def _s(v) -> str:
    """Coerce to stripped string. None / non-str → ''."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    # Numbers are occasionally seen in JSON for ports etc. — don't crash,
    # but we only stringify simple scalars. Lists/dicts → ''.
    if isinstance(v, (int, float, bool)):
        return str(v).strip()
    return ""


def _empty_result(reason: str) -> dict:
    """Return a canonical result with a single row-0 error and no devices."""
    return {
        "devices": [],
        "errors":  [{"row": 0, "reason": reason}],
        "mapping_report": {
            "sensors_total":   0,
            "sensors_mapped":  0,
            "sensors_skipped": [],
        },
    }
