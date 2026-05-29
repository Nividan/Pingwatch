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
from __future__ import annotations

import base64
import csv as _csv
import io as _io
import ipaddress as _ipaddr

from core.config import (
    _RE_IMPORT_PARSE, _RE_IMPORT_APPLY, _RE_IMPORT_SW_INSPECT,
    _RE_IMPORT_SUB_TPL, _RE_IMPORT_SUB_PREVIEW, _RE_IMPORT_SUB_APPLY,
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


_SUBNET_CSV_HEADERS = ["cidr", "name", "site", "vlan"]
_SUBNET_CSV_TEMPLATE = (
    "cidr,name,site,vlan\n"
    "10.0.0.0/24,Office LAN,HQ,10\n"
    "10.0.1.0/24,Lab Servers,BSLAB,20\n"
    "192.168.50.0/24,Guest WiFi,HQ,\n"
    "172.16.0.0/22,Management,DC1,99\n"
)


def _parse_subnet_csv(text: str) -> list:
    """Parse a CSV/TSV/paste of subnets into a list of rows annotated with
    per-row validation results. Never raises — every parse error becomes a
    row with `_ok=False, _msg="..."` so the UI can show the user exactly
    which lines need fixing.

    Accepted columns (case-insensitive, in any order): cidr, name, site, vlan.
    The header row is optional — if the first row contains "cidr" we treat
    it as headers; otherwise we assume positional order cidr,name,site,vlan.
    """
    out = []
    if not (text or "").strip():
        return out
    # Try comma first; fall back to tab/semicolon for Excel-pasted regions.
    sample = text[:1024]
    delim = ","
    if sample.count("\t") > sample.count(","):
        delim = "\t"
    elif sample.count(";") > sample.count(","):
        delim = ";"
    reader = _csv.reader(_io.StringIO(text), delimiter=delim)
    header = None
    seen_cidrs = set()  # detect duplicates within the same file
    for raw in reader:
        if not raw or all(not (c or "").strip() for c in raw):
            continue
        row = [(c or "").strip() for c in raw]
        # Detect header by looking for "cidr" in any cell of the first row
        if header is None and any(c.lower() == "cidr" for c in row):
            header = [c.lower() for c in row]
            continue
        if header is None:
            # No header — assume positional order
            header = _SUBNET_CSV_HEADERS[:]
        # Build {field: value} from this row
        cells = {h: (row[i] if i < len(row) else "") for i, h in enumerate(header)}
        cidr = cells.get("cidr", "")
        name = cells.get("name", "")
        site = cells.get("site", "")
        vlan_raw = cells.get("vlan", "")
        # VLAN coercion — bad values silently become 0 (untagged) rather than
        # failing the row, matching the single-add endpoint's behaviour.
        try:
            vlan = int(vlan_raw) if vlan_raw else 0
        except (TypeError, ValueError):
            vlan = 0
        if not (0 <= vlan <= 4094):
            vlan = 0
        # CIDR validation + canonicalisation
        ok, msg, canonical = True, "", cidr
        if not cidr:
            ok, msg = False, "missing CIDR"
        else:
            try:
                net = _ipaddr.ip_network(cidr, strict=False)
                canonical = str(net)
                if canonical in seen_cidrs:
                    ok, msg = False, "duplicate CIDR in this file"
                else:
                    seen_cidrs.add(canonical)
            except Exception:
                ok, msg = False, f"invalid CIDR: {cidr!r}"
        # Name length cap to match the Add Subnet modal
        if len(name) > 100:
            name = name[:100]
        if len(site) > 80:
            site = site[:80]
        out.append({
            "cidr": canonical, "name": name, "site": site, "vlan": vlan,
            "_ok": ok, "_msg": msg,
        })
    return out


def _apply_subnet_rows(rows: list, user: str) -> "tuple[int, list]":
    """Create each valid row via the same db_add_subnet path the Add Subnet
    modal uses. Returns (created_count, errors[]) so the UI can report exactly
    which CIDRs failed and why. Skips rows that failed parse validation."""
    from db.ipam import db_add_subnet, ipam_sync_subnet_add
    from db.core import _db_enqueue
    created = 0
    errors  = []
    for r in rows:
        if not r.get("_ok"):
            errors.append({"cidr": r.get("cidr", ""), "error": r.get("_msg", "validation failed")})
            continue
        cidr = r.get("cidr", "")
        try:
            new_id = db_add_subnet(
                cidr,
                r.get("name") or "",
                user,
                site=r.get("site") or "",
                vlan=int(r.get("vlan") or 0),
            )
            # Mirror the single-add path: queue the sync so IPAM picks up
            # any devices that already live in this CIDR.
            _db_enqueue(lambda _sid=new_id, _c=cidr: ipam_sync_subnet_add(_sid, _c))
            created += 1
        except ValueError as e:
            # Curated message from db_add_subnet (e.g. "Subnet '...' already exists")
            errors.append({"cidr": cidr, "error": (e.args[0] if e.args else "duplicate")})
        except Exception as e:
            log.error(f"bulk subnet import: cidr={cidr!r} — {type(e).__name__}: {e}")
            errors.append({"cidr": cidr, "error": "internal error (see server log)"})
    return created, errors


def handle(h, method, path, body):
    # ── GET /api/import/subnets/template ─────────────────────────
    # Returns a CSV template with a header row + 4 example rows. Viewer
    # role is enough — the template is the same for everyone and has no
    # tenant-specific data.
    if method == "GET" and _RE_IMPORT_SUB_TPL.match(path):
        user, _ = h._require("viewer")
        if not user:
            return True
        data = _SUBNET_CSV_TEMPLATE.encode("utf-8")
        try:
            h.send_response(200)
            h.send_header("Content-Type", "text/csv; charset=utf-8")
            h.send_header(
                "Content-Disposition",
                'attachment; filename="pingwatch-subnets-template.csv"',
            )
            h.send_header("Content-Length", str(len(data)))
            h.end_headers()
            h.wfile.write(data)
        except Exception as e:
            log.warning(f"subnet template download write failed: {e}")
        return True

    if method != "POST":
        return False

    # ── POST /api/import/subnets/preview ─────────────────────────
    # Parse-only. No DB writes; the response feeds the modal's preview
    # table so the user can see which rows are valid before committing.
    # Each row gets an `_exists` flag (set when the canonical CIDR is
    # already in ipam_subnets) so the UI can default-uncheck duplicates
    # and label them "already exists" instead of letting them fail at
    # apply-time. Defense in depth — the apply path also catches this.
    if _RE_IMPORT_SUB_PREVIEW.match(path):
        user, _ = h._require("operator")
        if not user:
            return True
        text = (body.get("text") or "")
        if len(text.encode("utf-8")) > _max_payload_bytes():
            h._json(413, {"error": "import payload too large"}); return True
        rows = _parse_subnet_csv(text)
        if len(rows) > _MAX_DEVICES_PARSED:
            h._json(413, {
                "error": f"too many rows ({len(rows)}); split the file into "
                         f"chunks of ≤ {_MAX_DEVICES_PARSED}"
            }); return True
        # Fetch existing CIDRs in one query and tag matching parsed rows.
        try:
            from db.ipam import db_list_subnets
            existing = {(s.get("cidr") or "").strip() for s in (db_list_subnets() or [])}
        except Exception as e:
            log.warning(f"subnet preview: could not load existing CIDRs: {e}")
            existing = set()
        already = 0
        for r in rows:
            if r.get("_ok") and r.get("cidr") in existing:
                r["_exists"] = True
                already += 1
        h._json(200, {
            "rows":     rows,
            "total":    len(rows),
            "valid":    sum(1 for r in rows if r.get("_ok")),
            "existing": already,
        })
        return True

    # ── POST /api/import/subnets/apply ───────────────────────────
    # Actually creates the subnets. The body should be the same `rows`
    # array the preview endpoint returned (client-side toggles let users
    # exclude rows before committing), but we re-validate each row server-
    # side so a hand-crafted payload can't bypass validation.
    if _RE_IMPORT_SUB_APPLY.match(path):
        user, _ = h._require("operator")
        if not user:
            return True
        rows = body.get("rows") or []
        if not isinstance(rows, list):
            h._json(400, {"error": "rows must be a list"}); return True
        if len(rows) > _MAX_DEVICES_APPLIED:
            h._json(413, {
                "error": f"too many rows ({len(rows)}); apply in chunks of "
                         f"≤ {_MAX_DEVICES_APPLIED}"
            }); return True
        # Server-side revalidation: never trust the client's _ok flag. Re-
        # check every row's CIDR and VLAN, and detect duplicates within the
        # apply batch even if the preview marked them valid (e.g. user
        # un-deselected a row that conflicted with another).
        revalidated = []
        seen_cidrs  = set()
        for r in rows:
            if not isinstance(r, dict):
                continue
            cidr_in = (r.get("cidr") or "").strip()
            name    = (r.get("name") or "").strip()[:100]
            site    = (r.get("site") or "").strip()[:80]
            try:
                vlan = int(r.get("vlan") or 0)
            except (TypeError, ValueError):
                vlan = 0
            if not (0 <= vlan <= 4094):
                vlan = 0
            ok, msg, canonical = True, "", cidr_in
            if not cidr_in:
                ok, msg = False, "missing CIDR"
            else:
                try:
                    canonical = str(_ipaddr.ip_network(cidr_in, strict=False))
                    if canonical in seen_cidrs:
                        ok, msg = False, "duplicate CIDR in this batch"
                    else:
                        seen_cidrs.add(canonical)
                except Exception:
                    ok, msg = False, f"invalid CIDR: {cidr_in!r}"
            revalidated.append({
                "cidr": canonical, "name": name, "site": site, "vlan": vlan,
                "_ok": ok, "_msg": msg,
            })
        created, errors = _apply_subnet_rows(revalidated, user)
        db_log_audit(
            user, h.client_address[0], "ipam_subnet_import",
            f"created={created} errors={len(errors)}"
        )
        h._json(200, {"created": created, "errors": errors})
        return True

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
