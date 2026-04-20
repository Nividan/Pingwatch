"""core/import_parsers/csv_parser.py — simple CSV import.

Minimum viable header: `name,host,group,sensors`. Sensors is a
comma-separated list of stypes in a single (usually quoted) cell:

    name,host,group,sensors
    router1,10.0.0.1,Core,"ping,snmp"
    web01,10.0.1.5,Web,"ping,http,tls"

Extended columns recognized when present (all optional). These set a
default value for any sensor of the matching stype on that row:

    snmp_community, snmp_version, snmp_oid
    http_port,      http_url
    tls_port
    tcp_port
    dns_query,      dns_record_type, dns_server
    smtp_port
    ssh_port
    sftp_port
    radius_port,    radius_secret

Additional device-level columns (all optional):
    external_id, webhook_url, snmp_community_default, snmp_version_default

Headers are case-insensitive. Spaces, hyphens, and dots in the header
are normalized to underscores (`"SNMP Community"` → `snmp_community`).
BOM-prefixed files (Excel exports) and trailing newlines are handled.
"""

from __future__ import annotations

import csv
import io

from core.device_importer import VALID_STYPES

REQUIRED_COLUMNS = ("name", "host")

# Per-stype "apply this row column as a sensor field" map.
# Keys are normalized CSV header names; value is (stype, sensor_field).
_EXTENDED_COLUMN_MAP = {
    # SNMP
    "snmp_community":    ("snmp", "snmp_community"),
    "snmp_version":      ("snmp", "snmp_version"),
    "snmp_oid":          ("snmp", "snmp_oid"),
    # HTTP
    "http_port":         ("http", "port"),
    "http_url":          ("http", "url"),
    # TLS
    "tls_port":          ("tls",  "port"),
    # TCP
    "tcp_port":          ("tcp",  "port"),
    # DNS
    "dns_query":         ("dns",  "dns_query"),
    "dns_record_type":   ("dns",  "dns_record_type"),
    "dns_server":        ("dns",  "dns_server"),
    # SMTP / SSH / SFTP
    "smtp_port":         ("smtp", "port"),
    "ssh_port":          ("ssh",  "port"),
    "sftp_port":         ("sftp", "port"),
    # RADIUS
    "radius_port":       ("radius", "port"),
    "radius_secret":     ("radius", "radius_secret"),
}

# Device-level columns (not sensor-level).
_DEVICE_LEVEL_COLUMNS = {
    "external_id", "webhook_url",
    "snmp_community_default", "snmp_version_default",
}


def parse_csv(text: str) -> dict:
    """Parse a CSV import file. Returns canonical parse result.

    Never raises — malformed input becomes a single row-0 error.
    """
    text = text or ""
    # Strip UTF-8 BOM if present (Excel-exported CSVs commonly have one).
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.strip():
        return _empty_result("empty file")

    # csv.DictReader handles quoted commas, escaped quotes, trailing \r\n.
    try:
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            return _empty_result("no header row")
        # Normalize headers in place so DictReader returns normalized keys.
        reader.fieldnames = [_norm_header(h) for h in reader.fieldnames]
    except Exception as e:
        return _empty_result(f"CSV parse failed: {e}")

    headers = reader.fieldnames
    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        return _empty_result(
            f"missing required column(s): {', '.join(missing)}"
        )

    devices: list = []
    errors:  list = []
    sensors_total   = 0
    sensors_dropped = 0

    try:
        rows = list(reader)
    except Exception as e:
        return _empty_result(f"CSV parse failed mid-stream: {e}")

    for idx, raw in enumerate(rows):
        # Skip entirely-empty lines (trailing newline, blank separator rows).
        if not raw or all(not (v or "").strip() for v in raw.values() if v is not None):
            continue

        name = _s(raw.get("name"))
        host = _s(raw.get("host"))
        if not name:
            errors.append({"row": idx, "reason": "missing required field: name"})
            continue
        if not host:
            errors.append({"row": idx, "reason": "missing required field: host"})
            continue

        # Parse sensors column — comma-separated list of stypes.
        sensor_tokens = [t.strip().lower() for t
                         in _s(raw.get("sensors")).split(",")
                         if t.strip()]
        sensors: list = []
        for s_idx, stype in enumerate(sensor_tokens):
            sensors_total += 1
            if stype not in VALID_STYPES:
                errors.append({"row": idx,
                               "reason": f"sensor[{s_idx}]: unknown stype {stype!r}"})
                sensors_dropped += 1
                continue
            sensor = {"name": stype.upper(), "stype": stype}
            # Apply extended-column defaults for sensors of matching stype.
            for col, (matches_stype, field) in _EXTENDED_COLUMN_MAP.items():
                if matches_stype != stype:
                    continue
                v = _s(raw.get(col))
                if v:
                    sensor[field] = v
            sensors.append(sensor)

        ext_id = _s(raw.get("external_id")) or f"csv:{idx + 1}"
        dev: dict = {
            "external_id": ext_id,
            "name":  name,
            "host":  host,
            "group": _s(raw.get("group")),
            "sensors": sensors,
        }
        for fld in ("webhook_url", "snmp_community_default",
                    "snmp_version_default"):
            v = _s(raw.get(fld))
            if v:
                dev[fld] = v
        devices.append(dev)

    mapping_report = {
        "sensors_total":   sensors_total,
        "sensors_mapped":  sensors_total - sensors_dropped,
        "sensors_skipped": [],
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


def _norm_header(h: str) -> str:
    """Normalize a CSV header: lowercase, strip, spaces/dots/hyphens → underscore."""
    if h is None:
        return ""
    h = h.strip().lower()
    for ch in (" ", "-", "."):
        h = h.replace(ch, "_")
    while "__" in h:
        h = h.replace("__", "_")
    return h.strip("_")


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    return str(v).strip()


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
