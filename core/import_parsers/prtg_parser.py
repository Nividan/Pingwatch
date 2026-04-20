"""core/import_parsers/prtg_parser.py — PRTG config XML import.

Parses a `.prtg` / `.odt` export (they're both XML). PRTG's exported
schema varies by version, so this parser is deliberately tolerant:

  - Walks the entire tree for `<node>` / `<device>` elements regardless
    of depth (PRTG nests devices inside `<group>` wrappers arbitrarily).
  - Pulls `name`, `host`, and id from either attributes OR child elements
    (different PRTG versions use different conventions).
  - Reads each device's `<sensor>` children; looks up `<sensortype>`
    (also tried as a `type=` attr) in prtg_sensor_map.

Uses `defusedxml.ElementTree` to block XXE / billion-laughs attacks.
"""

from __future__ import annotations

try:
    from defusedxml import ElementTree as ET
except ImportError:  # pragma: no cover — defused is in requirements
    import xml.etree.ElementTree as ET  # type: ignore

from core.import_parsers.prtg_sensor_map import map_prtg_sensor, _norm_prtg_type

# Element tags that represent a "device" in various PRTG export flavors.
_DEVICE_TAGS = {"device", "node"}
_SENSOR_TAGS = {"sensor"}

# Candidate field names PRTG uses for the primary hostname/IP.
_HOST_FIELDS = ("host", "ipaddress", "ip", "address", "hostv4", "targethost")
# Candidate field names PRTG uses for the device display name.
_NAME_FIELDS = ("name", "displayname", "devicename")
# Candidate field names for sensor type.
_STYPE_FIELDS = ("sensortype", "type", "kind", "sensorkind")
# Candidate field names for sensor display name.
_SNAME_FIELDS = ("name", "sensorname", "displayname")


def parse_prtg_xml(text: str) -> dict:
    """Parse a PRTG XML export. Returns canonical parse result.

    Never raises — malformed XML becomes a single row-0 error.
    """
    text = (text or "").strip()
    if not text:
        return _empty_result("empty file")

    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return _empty_result(f"XML parse failed: {e}")
    except Exception as e:
        return _empty_result(f"XML parse failed: {e}")

    devices: list = []
    errors:  list = []
    sensors_total = 0
    sensors_mapped = 0
    skip_buckets: dict[str, dict] = {}   # normalized_type → {count, reason}

    seen_devices: set = set()   # id-dedup within a single file
    device_idx = 0

    for elem in _iter_all(root):
        tag = _local_tag(elem.tag)
        if tag not in _DEVICE_TAGS:
            continue
        # Heuristic: real devices always have a host-ish field. Group
        # container nodes also tag as <node> but don't — skip those.
        host = _pick_field(elem, _HOST_FIELDS)
        if not host:
            continue
        name = _pick_field(elem, _NAME_FIELDS) or host
        dev_id = elem.get("id") or _child_text(elem, "id") or _child_text(elem, "objid") or ""
        dedup_key = f"{dev_id}|{host.lower()}"
        if dedup_key in seen_devices:
            continue
        seen_devices.add(dedup_key)

        sensors_out: list = []
        for s_elem in _iter_all(elem):
            if _local_tag(s_elem.tag) not in _SENSOR_TAGS:
                continue
            raw_type = _pick_field(s_elem, _STYPE_FIELDS)
            s_name   = _pick_field(s_elem, _SNAME_FIELDS) or raw_type or "Sensor"
            if not raw_type:
                sensors_total += 1
                _bucket(skip_buckets, "(missing type)",
                        "sensor element had no <sensortype> or type attr")
                continue
            sensors_total += 1

            # Build an attr-dict from both elem attributes AND simple child
            # text values — PRTG puts host/port/url in either spot depending
            # on version.
            attrs = _gather_attrs(s_elem)
            spec, skip_reason = map_prtg_sensor(raw_type, attrs)
            if spec is None:
                _bucket(skip_buckets, raw_type, skip_reason or "unmapped type")
                continue
            spec["name"] = s_name
            sensors_out.append(spec)
            sensors_mapped += 1

        ext_id = f"prtg:{dev_id}" if dev_id else f"prtg:{host.lower()}"
        dev: dict = {
            "external_id": ext_id,
            "name":        name,
            "host":        host,
            "group":       "",    # PRTG group tree not mapped in v1
            "sensors":     sensors_out,
        }
        devices.append(dev)
        device_idx += 1

    mapping_report = {
        "sensors_total":   sensors_total,
        "sensors_mapped":  sensors_mapped,
        "sensors_skipped": [
            {"source_type": k, "count": b["count"], "reason": b["reason"]}
            for k, b in sorted(skip_buckets.items(),
                               key=lambda kv: -kv[1]["count"])
        ],
    }
    if not devices and not errors:
        errors.append({"row": 0,
                       "reason": "no <device> elements with a host field found"})
    return {
        "devices":        devices,
        "errors":         errors,
        "mapping_report": mapping_report,
    }


# ── Helpers ────────────────────────────────────────────────────────

def _iter_all(elem):
    """Iterate all descendants (not including `elem` itself)."""
    for child in elem:
        yield child
        yield from _iter_all(child)


def _local_tag(tag: str) -> str:
    """Strip XML namespace from a tag. `{ns}device` → `device`."""
    if not tag:
        return ""
    if tag.startswith("{"):
        return tag.split("}", 1)[1].lower()
    return tag.lower()


def _pick_field(elem, field_names) -> str:
    """Try each field name as (1) an attribute and (2) a direct child.
    Returns the first non-empty stripped value, or ''.
    """
    for name in field_names:
        v = elem.get(name)
        if v:
            v = v.strip()
            if v:
                return v
    for child in elem:
        tag = _local_tag(child.tag)
        if tag in field_names:
            v = (child.text or "").strip()
            if v:
                return v
    return ""


def _child_text(elem, tag: str) -> str:
    for child in elem:
        if _local_tag(child.tag) == tag.lower():
            return (child.text or "").strip()
    return ""


def _gather_attrs(elem) -> dict:
    """Merge element attributes + direct-child text into one namespace.

    PRTG XML puts a sensor's port sometimes as `<sensor targetport="80">`
    and sometimes as `<sensor><targetport>80</targetport></sensor>`.
    Normalizing both shapes to one dict simplifies the attr_map logic in
    `prtg_sensor_map.map_prtg_sensor()`.
    """
    out: dict = {}
    for k, v in (elem.attrib or {}).items():
        if v is None:
            continue
        out[k.lower()] = v.strip() if isinstance(v, str) else v
    for child in elem:
        tag = _local_tag(child.tag)
        txt = (child.text or "").strip() if child.text else ""
        if txt and tag not in out:
            out[tag] = txt
    return out


def _bucket(d: dict, source_type: str, reason: str) -> None:
    """Increment a skip-reason counter for the mapping report."""
    key = _norm_prtg_type(source_type) or source_type
    b = d.get(key)
    if b:
        b["count"] += 1
    else:
        d[key] = {"count": 1, "reason": reason, "source_type": source_type}


def _empty_result(reason: str) -> dict:
    return {
        "devices": [],
        "errors":  [{"row": 0, "reason": reason}],
        "mapping_report": {
            "sensors_total":   0,
            "sensors_mapped":  0,
            "sensors_skipped": [],
        },
    }
