"""routes/imports.py — Bulk Device Import endpoints.

  POST /api/import/parse        { format, text?, b64?, filename?, column_map? }
                                → 200 {devices, errors, mapping_report,
                                        orphan_count}
  POST /api/import/sw/inspect   { text?, b64?, filename }
                                → 200 {headers, sample_rows, detected_format}
  POST /api/import/apply        { devices, mode }
                                → 200 {created, updated, deleted, skipped, errors}

Auth: all endpoints require operator+ (matches /api/discovery/bulk-add).

Body shape:
  - JSON / CSV / PRTG / Zabbix uploads: send `{format, text}`. Text
    uploads keep the body small + human-readable in audit logs.
  - SolarWinds XLSX: send `{format: "solarwinds", b64, filename, column_map}`
    because XLSX is binary. b64 is std base64 of the file bytes.

The /parse endpoint mutates no state — it only renders a preview. Apply
is the only state-changing call; it goes through `reconcile_devices_batch`
which uses the same dedup + sensor-create path as Subnet Discovery.
"""

import base64

from core.config import (
    _RE_IMPORT_PARSE, _RE_IMPORT_APPLY, _RE_IMPORT_SW_INSPECT,
)
from core.import_parsers import PARSERS
from core.import_parsers.solarwinds_parser import (
    inspect_solarwinds, parse_solarwinds,
)
from core.device_importer import (
    preview_match, find_orphans, reconcile_devices_batch,
)
from core.logger import log
from db import db_log_audit

_VALID_FORMATS = {"json", "csv", "prtg", "zabbix", "solarwinds"}
_VALID_MODES   = {"add_only", "add_update", "replace"}

# Soft cap on the request body. The HTTP layer already enforces a
# global limit, but parsers can be slow on huge inputs — keep imports
# under a reasonable ceiling so the UI stays responsive.
# Fallback used only when settings cache isn't loaded; real cap is
# `import_max_payload_mb` (bounded 1..100).
_MAX_PAYLOAD_BYTES_DEFAULT = 8 * 1024 * 1024
_MAX_DEVICES_PARSED  = 5000                # past this we hint "split the file"
_MAX_DEVICES_APPLIED = 1000                # per /apply call


def _max_payload_bytes() -> int:
    try:
        import core.settings as _s
        mb = max(1, min(100, int(_s.get("import_max_payload_mb", 8) or 8)))
        return mb * 1024 * 1024
    except Exception:
        return _MAX_PAYLOAD_BYTES_DEFAULT


def handle(h, method, path, body):
    if method != "POST":
        return False

    # ── /api/import/sw/inspect ────────────────────────────────────
    if _RE_IMPORT_SW_INSPECT.match(path):
        user, _ = h._require("operator")
        if not user:
            return True
        payload, fmt_hint, err = _decode_payload(body, default_filename="export.csv")
        if err:
            h._json(400, {"error": err}); return True
        result = inspect_solarwinds(payload, body.get("filename") or "")
        if result.get("error"):
            h._json(400, {"error": result["error"]}); return True
        h._json(200, {
            "headers":         result["headers"],
            "sample_rows":     result["sample_rows"],
            "detected_format": result["detected_format"],
        })
        return True

    # ── /api/import/parse ─────────────────────────────────────────
    if _RE_IMPORT_PARSE.match(path):
        user, _ = h._require("operator")
        if not user:
            return True
        fmt = str(body.get("format", "")).strip().lower()
        if fmt not in _VALID_FORMATS:
            h._json(400, {"error": f"format must be one of {sorted(_VALID_FORMATS)}"})
            return True

        payload, _hint, err = _decode_payload(body, default_filename="upload")
        if err:
            h._json(400, {"error": err}); return True

        try:
            if fmt == "solarwinds":
                cmap = body.get("column_map") or {}
                if not isinstance(cmap, dict):
                    h._json(400, {"error": "column_map must be an object"})
                    return True
                filename = str(body.get("filename") or "")
                # Format hint: prefer client-provided, fall back to filename
                # detection inside the parser.
                fmt_arg = (str(body.get("sw_format") or "") or None)
                result = parse_solarwinds(payload, cmap,
                                          fmt=fmt_arg, filename=filename)
            else:
                parser = PARSERS[fmt]
                # All non-SW parsers take str text.
                if isinstance(payload, (bytes, bytearray)):
                    try:
                        payload = payload.decode("utf-8")
                    except UnicodeDecodeError:
                        payload = payload.decode("latin-1", errors="replace")
                result = parser(payload)
        except Exception as e:
            log.error(f"import parse failed (format={fmt}): {e}")
            h._json(400, {"error": "parser failed"}); return True

        devices = result.get("devices") or []
        if len(devices) > _MAX_DEVICES_PARSED:
            h._json(400, {"error": f"too many devices ({len(devices)}); "
                                    f"split into files of {_MAX_DEVICES_PARSED} or fewer"})
            return True

        # Annotate with match_status / match_did / match_diff for the preview UI.
        try:
            preview_match(devices)
            orphans = find_orphans(devices)
        except Exception as e:
            log.warning(f"import preview match failed: {e}")
            orphans = []

        h._json(200, {
            "devices":        devices,
            "errors":         result.get("errors") or [],
            "mapping_report": result.get("mapping_report") or {},
            "orphan_count":   len(orphans),
            "orphans":        orphans[:50],   # cap — full list only relevant in apply
        })
        return True

    # ── /api/import/apply ─────────────────────────────────────────
    if _RE_IMPORT_APPLY.match(path):
        user, _ = h._require("operator")
        if not user:
            return True

        items = body.get("devices") or []
        if not isinstance(items, list) or not items:
            h._json(400, {"error": "devices list required"}); return True
        if len(items) > _MAX_DEVICES_APPLIED:
            h._json(400, {"error": f"too many devices (max {_MAX_DEVICES_APPLIED} per call)"})
            return True

        mode = str(body.get("mode", "add_update")).strip().lower()
        if mode not in _VALID_MODES:
            h._json(400, {"error": f"mode must be one of {sorted(_VALID_MODES)}"})
            return True

        fmt_for_audit = str(body.get("format", "import")).strip().lower()[:32]
        default_group = str(body.get("default_group", "Imported")).strip()[:255] or "Imported"

        try:
            result = reconcile_devices_batch(items, mode=mode,
                                              default_group=default_group)
        except Exception as e:
            log.error(f"import apply failed (mode={mode}): {e}")
            h._json(500, {"error": "import failed"}); return True

        try:
            db_log_audit(user, h.client_address[0], "device_import",
                         f"format={fmt_for_audit} mode={mode} "
                         f"created={len(result.get('created') or [])} "
                         f"updated={len(result.get('updated') or [])} "
                         f"deleted={len(result.get('deleted') or [])} "
                         f"errors={len(result.get('errors') or [])}")
        except Exception:
            pass

        h._json(200, result)
        return True

    return False


# ── Helpers ────────────────────────────────────────────────────────

def _decode_payload(body, default_filename: str = "upload"
                    ) -> tuple[object, str, str | None]:
    """Extract upload payload from a request body.

    Body may carry text directly (`text`) or base64-encoded bytes (`b64`)
    for binary uploads (XLSX). Returns `(payload, filename, error)`.

    `payload` is `str` for text uploads, `bytes` for b64 uploads.
    `error` is None on success, or a human-readable string on failure.
    """
    text = body.get("text")
    b64  = body.get("b64")
    filename = str(body.get("filename") or default_filename)

    _cap = _max_payload_bytes()
    _cap_mb = _cap // (1024 * 1024)
    if isinstance(text, str) and text:
        if len(text.encode("utf-8", errors="ignore")) > _cap:
            return None, filename, f"payload too large (max {_cap_mb} MB)"
        return text, filename, None

    if isinstance(b64, str) and b64:
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception:
            return None, filename, "b64 decode failed"
        if len(raw) > _cap:
            return None, filename, f"payload too large (max {_cap_mb} MB)"
        return raw, filename, None

    return None, filename, "request body needs 'text' or 'b64'"
