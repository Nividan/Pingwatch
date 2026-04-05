"""
routes/tls.py — TLS/HTTPS certificate management API endpoints.

GET    /api/tls            → cert metadata + TLS settings (no private key)
PATCH  /api/tls            → update tls_enabled, tls_port, http_redirect
POST   /api/tls/upload     → upload and validate a new cert+key pair (PEM)
POST   /api/tls/upload-pfx → upload a PFX/PKCS#12 bundle (base64)
POST   /api/tls/generate   → generate a new self-signed certificate
POST   /api/tls/csr        → generate a CSR + private key
GET    /api/tls/csr        → retrieve last generated CSR PEM
"""

import base64

import core.settings as _settings
import core.app_state as app_state

from core.config import (_RE_TLS, _RE_TLS_UPLOAD, _RE_TLS_GENERATE,
                         _RE_TLS_UPLOAD_PFX, _RE_TLS_CSR, _RE_TLS_INSTALL,
                         TLS_PORT_DEFAULT)
from db          import _db_enqueue, db_log_audit, db_save_settings
from core.logger import log


# ── Shared helper: build the cert-info dict ───────────────────────────────────

def _cert_info() -> dict:
    """Return parsed cert metadata from the current DB cert, or {}."""
    from core.tls import parse_cert_info
    cert_pem = _settings.get("tls_cert_pem", "")
    info = parse_cert_info(cert_pem) if cert_pem else {}
    if info:
        info["source"] = _settings.get("tls_cert_source", "")
    return info


def _tls_response() -> dict:
    return {
        "tls_enabled":   int(_settings.get("tls_enabled",  0)),
        "tls_port":      int(_settings.get("tls_port",     TLS_PORT_DEFAULT)),
        "http_redirect": int(_settings.get("http_redirect", 0)),
        "tls_active":    getattr(app_state, "tls_active", False),
        "cert":          _cert_info(),
        "csr_pending":   _settings.get("tls_cert_source", "") == "csr_pending",
        "csr_pem":       _settings.get("tls_csr_pem", "") if _settings.get("tls_cert_source", "") == "csr_pending" else "",
    }


# ── Route handler ─────────────────────────────────────────────────────────────

def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # ── GET /api/tls ──────────────────────────────────────────────
    if _RE_TLS.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, _tls_response())
        return True

    # ── PATCH /api/tls ────────────────────────────────────────────
    if _RE_TLS.match(path) and method == "PATCH":
        user, _ = h._require("admin")
        if not user: return True

        updates = {}

        if "tls_enabled" in body:
            updates["tls_enabled"] = "1" if body["tls_enabled"] else "0"

        if "tls_port" in body:
            try:
                p = int(body["tls_port"])
                if not (1 <= p <= 65535):
                    raise ValueError
            except (TypeError, ValueError):
                h._json(400, {"error": "tls_port must be an integer between 1 and 65535"})
                return True
            updates["tls_port"] = str(p)

        if "http_redirect" in body:
            updates["http_redirect"] = "1" if body["http_redirect"] else "0"

        if updates:
            _settings.load(updates)
            _db_enqueue(lambda _u=dict(updates): db_save_settings(_u))
            db_log_audit(user, h.client_address[0], "tls_settings_update", "",
                         str(list(updates.keys())))

        h._json(200, {"ok": True, "restart_required": True})
        return True

    # ── POST /api/tls/upload ──────────────────────────────────────
    if _RE_TLS_UPLOAD.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True

        cert_pem = (body.get("cert_pem") or "").strip()
        key_pem  = (body.get("key_pem")  or "").strip()

        # Support file-upload path: cert_b64 (may be DER or PEM bytes)
        cert_b64 = (body.get("cert_b64") or "").strip()
        if cert_b64 and not cert_pem:
            from core.tls import load_der_cert
            try:
                cert_pem = load_der_cert(base64.b64decode(cert_b64))
            except Exception as e:
                h._json(400, {"error": f"Invalid certificate file: {e}"})
                return True

        if not cert_pem or not key_pem:
            h._json(400, {"error": "cert_pem and key_pem are required"})
            return True

        from core.tls import (validate_cert_key_pair, parse_cert_info,
                               canonicalize_cert_pem, canonicalize_key_pem)
        from db.backups import encrypt_pw

        try:
            cert_pem = canonicalize_cert_pem(cert_pem)
            key_pem  = canonicalize_key_pem(key_pem)
        except ValueError as e:
            h._json(400, {"error": str(e)})
            return True

        err = validate_cert_key_pair(cert_pem, key_pem)
        if err:
            h._json(400, {"error": err})
            return True

        key_enc = encrypt_pw(key_pem)
        updates = {
            "tls_cert_pem":    cert_pem,
            "tls_key_pem_enc": key_enc,
            "tls_cert_source": "uploaded",
        }
        _settings.load(updates)
        _db_enqueue(lambda _u=dict(updates): db_save_settings(_u))
        info = parse_cert_info(cert_pem)
        db_log_audit(user, h.client_address[0], "tls_cert_upload", "",
                     f"subject={info.get('subject','?')} expires={info.get('not_after','?')}")
        info["source"] = "uploaded"
        h._json(200, {"ok": True, "cert": info, "restart_required": True})
        return True

    # ── POST /api/tls/generate ────────────────────────────────────
    if _RE_TLS_GENERATE.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True

        from core.tls import generate_self_signed_cert, parse_cert_info
        from db.backups import encrypt_pw
        import socket as _sock

        org_name   = (body.get("org_name") or _settings.get("org_name") or "PingWatch").strip()
        hostname   = (body.get("hostname") or _settings.get("tls_cn") or _sock.gethostname() or "localhost").strip()
        extra_sans = [s.strip() for s in (body.get("extra_sans") or []) if str(s).strip()]
        try:
            cert_pem, key_pem = generate_self_signed_cert(org_name, hostname, extra_sans=extra_sans)
        except Exception as e:
            log.error(f"TLS cert generation failed: {e}", exc_info=True)
            h._json(500, {"error": f"Certificate generation failed: {e}"})
            return True

        key_enc = encrypt_pw(key_pem)
        updates = {
            "tls_cert_pem":    cert_pem,
            "tls_key_pem_enc": key_enc,
            "tls_cert_source": "generated",
            "tls_cn":          hostname,
        }
        _settings.load(updates)
        _db_enqueue(lambda _u=dict(updates): db_save_settings(_u))
        info = parse_cert_info(cert_pem)
        db_log_audit(user, h.client_address[0], "tls_cert_generate", "",
                     f"subject={info.get('subject','?')} expires={info.get('not_after','?')}")
        info["source"] = "generated"
        h._json(200, {"ok": True, "cert": info, "restart_required": True})
        return True

    # ── POST /api/tls/upload-pfx ─────────────────────────────────
    if _RE_TLS_UPLOAD_PFX.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True

        pfx_b64  = (body.get("pfx_b64") or "").strip()
        password = body.get("password") or ""

        if not pfx_b64:
            h._json(400, {"error": "PFX file data is required"})
            return True

        try:
            pfx_bytes = base64.b64decode(pfx_b64)
        except Exception:
            h._json(400, {"error": "Invalid base64 encoding"})
            return True

        from core.tls import (parse_pfx, validate_cert_key_pair, parse_cert_info,
                               canonicalize_cert_pem, canonicalize_key_pem)
        from db.backups import encrypt_pw

        try:
            cert_pem, key_pem = parse_pfx(pfx_bytes, password)
            cert_pem = canonicalize_cert_pem(cert_pem)
            key_pem  = canonicalize_key_pem(key_pem)
        except ValueError as e:
            h._json(400, {"error": str(e)})
            return True

        err = validate_cert_key_pair(cert_pem, key_pem)
        if err:
            h._json(400, {"error": err})
            return True

        key_enc = encrypt_pw(key_pem)
        updates = {
            "tls_cert_pem":    cert_pem,
            "tls_key_pem_enc": key_enc,
            "tls_cert_source": "uploaded",
        }
        _settings.load(updates)
        _db_enqueue(lambda _u=dict(updates): db_save_settings(_u))
        info = parse_cert_info(cert_pem)
        db_log_audit(user, h.client_address[0], "tls_cert_upload_pfx", "",
                     f"subject={info.get('subject','?')} expires={info.get('not_after','?')}")
        info["source"] = "uploaded"
        h._json(200, {"ok": True, "cert": info, "restart_required": True})
        return True

    # ── GET /api/tls/csr ─────────────────────────────────────────
    if _RE_TLS_CSR.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user: return True

        csr_pem = _settings.get("tls_csr_pem", "")
        if not csr_pem:
            h._json(404, {"error": "No CSR has been generated yet"})
            return True

        h._json(200, {"csr_pem": csr_pem})
        return True

    # ── POST /api/tls/csr ────────────────────────────────────────
    if _RE_TLS_CSR.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True

        from core.tls import generate_csr
        from db.backups import encrypt_pw
        import socket as _sock

        hostname   = (body.get("hostname") or _settings.get("tls_cn") or _sock.gethostname() or "localhost").strip()
        org_name   = (body.get("org_name") or "").strip()
        key_size   = int(body.get("key_size") or 2048)
        extra_sans = [s.strip() for s in (body.get("extra_sans") or []) if str(s).strip()]

        try:
            csr_pem, key_pem = generate_csr(
                hostname=hostname, org_name=org_name,
                key_size=key_size, extra_sans=extra_sans,
            )
        except Exception as e:
            log.error(f"CSR generation failed: {e}", exc_info=True)
            h._json(500, {"error": "CSR generation failed"})
            return True

        key_enc = encrypt_pw(key_pem)
        updates = {
            "tls_csr_pem":     csr_pem,
            "tls_key_pem_enc": key_enc,
            "tls_cert_source": "csr_pending",
        }
        _settings.load(updates)
        _db_enqueue(lambda _u=dict(updates): db_save_settings(_u))
        db_log_audit(user, h.client_address[0], "tls_csr_generate", "",
                     f"CN={hostname} key_size={key_size}")

        h._json(200, {"ok": True, "csr_pem": csr_pem})
        return True

    # ── POST /api/tls/install-signed ─────────────────────────────
    # Install a CA-signed certificate when a CSR key is already stored.
    # Accepts cert_pem (PEM text) or cert_b64 (base64-encoded DER/PEM file).
    # The private key is read from tls_key_pem_enc and validated to match.
    if _RE_TLS_INSTALL.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True

        from core.tls import (load_der_cert, validate_cert_key_pair, parse_cert_info,
                               canonicalize_cert_pem, canonicalize_key_pem)
        from db.backups import decrypt_pw, encrypt_pw

        # Resolve certificate PEM
        cert_pem = (body.get("cert_pem") or "").strip()
        cert_b64 = (body.get("cert_b64") or "").strip()
        if cert_b64 and not cert_pem:
            try:
                cert_pem = load_der_cert(base64.b64decode(cert_b64))
            except Exception as e:
                h._json(400, {"error": f"Invalid certificate file: {e}"})
                return True

        if not cert_pem:
            h._json(400, {"error": "cert_pem or cert_b64 is required"})
            return True

        # Canonicalise the cert so it's always clean PEM when stored
        try:
            cert_pem = canonicalize_cert_pem(cert_pem)
        except ValueError as e:
            h._json(400, {"error": str(e)})
            return True

        # Retrieve stored private key (generated during CSR)
        key_enc = _settings.get("tls_key_pem_enc", "")
        if not key_enc:
            h._json(400, {"error": "No stored private key found. Generate a new CSR first."})
            return True
        key_pem = decrypt_pw(key_enc)
        if not key_pem:
            h._json(400, {"error": "Stored private key could not be decrypted."})
            return True

        # Also canonicalise the key before re-storing (cleans any legacy artefacts)
        try:
            key_pem = canonicalize_key_pem(key_pem)
        except ValueError as e:
            h._json(400, {"error": f"Stored private key is invalid: {e}"})
            return True

        # Validate certificate matches the stored key
        err = validate_cert_key_pair(cert_pem, key_pem)
        if err:
            h._json(400, {"error": err})
            return True

        key_enc = encrypt_pw(key_pem)
        updates = {
            "tls_cert_pem":    cert_pem,
            "tls_key_pem_enc": key_enc,  # re-store canonicalised key
            "tls_cert_source": "uploaded",
            "tls_csr_pem":     "",   # clear the pending CSR
        }
        _settings.load(updates)
        _db_enqueue(lambda _u=dict(updates): db_save_settings(_u))
        info = parse_cert_info(cert_pem)
        db_log_audit(user, h.client_address[0], "tls_cert_install_signed", "",
                     f"subject={info.get('subject','?')} expires={info.get('not_after','?')}")
        info["source"] = "uploaded"
        h._json(200, {"ok": True, "cert": info, "restart_required": True})
        return True

    return False
