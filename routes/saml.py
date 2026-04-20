"""
routes/saml.py — SAML 2.0 SP endpoints.

Public endpoints (no auth required):
  GET  /api/saml/login              Redirect to IdP SSO URL (auto-POST form)
  POST /api/saml/acs                Assertion Consumer Service — consumes SAMLResponse
  GET  /api/saml/metadata           SP metadata XML (for IdP admins)

Admin endpoints:
  GET  /api/saml/settings           Read config (secrets redacted)
  PATCH /api/saml/settings          Partial update
  POST /api/saml/metadata/import    Fetch/parse IdP metadata
  POST /api/saml/sp_cert/generate   Generate SP signing cert
  POST /api/saml/test               Dry-run validation
"""

from __future__ import annotations

import time

from core.logger import log
import core.settings as _settings


def _send_302(h, location: str, cookies: list[str] | None = None) -> None:
    """Redirect helper — optionally sets Set-Cookie headers before the 302."""
    h.send_response(302)
    h.send_header("Location", location)
    h.send_header("Content-Length", "0")
    for c in (cookies or []):
        h.send_header("Set-Cookie", c)
    try:
        h._sec_headers()
    except Exception:
        pass
    h.end_headers()


def _send_html(h, code: int, html: str, cookies: list[str] | None = None) -> None:
    body = html.encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    for c in (cookies or []):
        h.send_header("Set-Cookie", c)
    try:
        h._sec_headers()
    except Exception:
        pass
    h.end_headers()
    h.wfile.write(body)


def _send_xml(h, code: int, xml_bytes: bytes) -> None:
    h.send_response(code)
    h.send_header("Content-Type", "application/samlmetadata+xml; charset=utf-8")
    h.send_header("Content-Length", str(len(xml_bytes)))
    h.send_header("Content-Disposition", 'attachment; filename="pingwatch-sp-metadata.xml"')
    try:
        h._sec_headers()
    except Exception:
        pass
    h.end_headers()
    h.wfile.write(xml_bytes)


def _issue_session_cookie(h, username: str, role: str) -> str:
    """Create a session and return the Set-Cookie header value."""
    from core.auth import _create_session
    token = _create_session(username, role)
    tls_active = bool(getattr(h.server, "tls_active", False))
    sec = "; Secure" if tls_active else ""
    return f"session={token}; HttpOnly; Path=/; SameSite=Strict; Max-Age=2592000{sec}"


def handle(h, method, path, body) -> bool:

    # ══════════════════════════════════════════════════════════════
    # PUBLIC: SAML AuthnRequest — redirect to IdP
    # ══════════════════════════════════════════════════════════════
    if path == "/api/saml/login" and method == "GET":
        from core.saml_auth import saml_build_authn_request
        try:
            import saml2  # noqa: F401  — import check
        except ImportError:
            pass  # we implement auth-request build without pysaml2; safe
        try:
            ar = saml_build_authn_request()
        except Exception as e:
            log.error(f"saml_build_authn_request error: {e}")
            _send_html(h, 503,
                       "<h1>SAML unavailable</h1><p>Contact administrator.</p>")
            return True
        if not ar.get("ok"):
            _send_html(h, 503,
                       f"<h1>SAML not configured</h1><p>{ar.get('message','')}</p>")
            return True
        _send_html(h, 200, ar["html"])
        return True

    # ══════════════════════════════════════════════════════════════
    # PUBLIC: Assertion Consumer Service (ACS)
    # ══════════════════════════════════════════════════════════════
    if path == "/api/saml/acs" and method == "POST":
        from core.saml_auth import saml_parse_response, _get_cfg
        from core.sso_common import sso_provision_or_sync, sanitize_username

        saml_response = body.get("SAMLResponse", "") or ""
        relay_state   = body.get("RelayState", "")   or ""
        if not saml_response:
            _send_html(h, 400,
                       "<h1>Invalid SAML response</h1>"
                       "<p>Missing SAMLResponse parameter. Return to "
                       '<a href="/">PingWatch</a>.</p>')
            return True

        try:
            parsed = saml_parse_response(saml_response, relay_state)
        except Exception as e:
            log.error(f"SAML ACS parse error: {e}")
            _send_html(h, 400,
                       "<h1>Sign-in failed</h1>"
                       '<p>SAML response could not be validated. '
                       'Return to <a href="/">PingWatch</a>.</p>')
            return True

        if not parsed.get("ok"):
            msg = parsed.get("message", "validation failed")
            log.warning(f"SAML ACS rejected: {msg}")
            _send_html(h, 401,
                       f"<h1>Sign-in rejected</h1>"
                       f"<p>{msg}. Return to <a href=\"/\">PingWatch</a>.</p>")
            return True

        cfg = _get_cfg()
        username = sanitize_username(parsed["username"])
        result = sso_provision_or_sync(
            external_id=parsed["external_id"],
            username_hint=username,
            email=parsed.get("email", ""),
            display_name=parsed.get("display_name", ""),
            groups=parsed.get("groups", []),
            auth_type="saml",
            default_role=cfg["default_role"],
            allow_unmapped=cfg["allow_unmapped"],
        )
        if result is None:
            _send_html(h, 403,
                       "<h1>Access denied</h1>"
                       "<p>You are not authorized to access PingWatch. "
                       'Contact your administrator. Return to <a href="/">PingWatch</a>.</p>')
            return True

        resolved_username, role = result

        # TOTP gate — if user has 2FA enabled, redirect to TOTP prompt instead
        try:
            from db import db_get_totp
            totp_row = db_get_totp(resolved_username)
            if totp_row and int(totp_row.get("enabled", 0) or 0):
                # Store pending login in relay store, redirect to TOTP page
                import secrets as _sec
                import time as _t
                pending = _sec.token_urlsafe(24)
                from core.saml_auth import _relay_put
                _relay_put(f"sso-totp-{pending}",
                           {"username": resolved_username, "role": role, "ts": _t.time()})
                _send_302(h, f"/?sso_totp={pending}")
                return True
        except Exception as e:
            log.warning(f"SAML TOTP gate check failed: {e}")

        cookie = _issue_session_cookie(h, resolved_username, role)
        log.info(f"SAML login: {resolved_username!r} role={role}")
        _send_302(h, "/", cookies=[cookie])
        return True

    # ══════════════════════════════════════════════════════════════
    # PUBLIC: SP metadata (for IdP admins)
    # ══════════════════════════════════════════════════════════════
    if path == "/api/saml/metadata" and method == "GET":
        from core.saml_auth import saml_sp_metadata_xml
        try:
            xml_bytes = saml_sp_metadata_xml()
        except Exception as e:
            log.error(f"saml_sp_metadata_xml error: {e}")
            h._json(500, {"error": "metadata unavailable"})
            return True
        _send_xml(h, 200, xml_bytes)
        return True

    # ══════════════════════════════════════════════════════════════
    # ADMIN: read config (secrets redacted)
    # ══════════════════════════════════════════════════════════════
    if path == "/api/saml/settings" and method == "GET":
        user, _ = h._require("admin")
        if not user: return True
        from core.saml_auth import saml_sp_cert_info, saml_idp_cert_info
        resp = {
            "saml_enabled":                    int(_settings.get("saml_enabled", 0) or 0),
            "saml_sp_entity_id":               _settings.get("saml_sp_entity_id", ""),
            "saml_sp_acs_url":                 _settings.get("saml_sp_acs_url", ""),
            "saml_idp_entity_id":              _settings.get("saml_idp_entity_id", ""),
            "saml_idp_sso_url":                _settings.get("saml_idp_sso_url", ""),
            "saml_idp_slo_url":                _settings.get("saml_idp_slo_url", ""),
            "saml_metadata_source":            _settings.get("saml_metadata_source", ""),
            "saml_metadata_url":               _settings.get("saml_metadata_url", ""),
            "saml_sign_authn_requests":        int(_settings.get("saml_sign_authn_requests", 1) or 0),
            "saml_want_assertions_signed":     int(_settings.get("saml_want_assertions_signed", 1) or 0),
            "saml_want_assertions_encrypted":  int(_settings.get("saml_want_assertions_encrypted", 0) or 0),
            "saml_attr_username":              _settings.get("saml_attr_username", "NameID"),
            "saml_attr_email":                 _settings.get("saml_attr_email", "mail"),
            "saml_attr_display_name":          _settings.get("saml_attr_display_name", "displayName"),
            "saml_attr_groups":                _settings.get("saml_attr_groups", "memberOf"),
            "saml_auto_provision":             int(_settings.get("saml_auto_provision", 1) or 0),
            "saml_default_role":               _settings.get("saml_default_role", "viewer"),
            "saml_allow_unmapped":             int(_settings.get("saml_allow_unmapped", 1) or 0),
            "saml_display_name":               _settings.get("saml_display_name", "Single Sign-On"),
            # Redacted / derived
            "saml_sp_cert_info":               saml_sp_cert_info(),
            "saml_idp_cert_info":              saml_idp_cert_info(),
            "saml_sp_key_pem_set":             bool(_settings.get("saml_sp_key_pem_enc", "")),
            "saml_idp_cert_pem_set":           bool(_settings.get("saml_idp_cert_pem", "")),
        }
        h._json(200, resp)
        return True

    # ══════════════════════════════════════════════════════════════
    # ADMIN: update config
    # ══════════════════════════════════════════════════════════════
    if path == "/api/saml/settings" and method == "PATCH":
        user, _ = h._require("admin")
        if not user: return True
        from db import db_save_settings, db_log_audit

        save: dict = {}
        _bool_keys = {"saml_enabled", "saml_sign_authn_requests",
                      "saml_want_assertions_signed", "saml_want_assertions_encrypted",
                      "saml_auto_provision", "saml_allow_unmapped"}
        _str_keys  = {"saml_sp_entity_id", "saml_sp_acs_url",
                      "saml_attr_username", "saml_attr_email",
                      "saml_attr_display_name", "saml_attr_groups",
                      "saml_default_role", "saml_display_name",
                      "saml_idp_entity_id", "saml_idp_sso_url",
                      "saml_idp_slo_url", "saml_idp_cert_pem"}

        for k, v in (body or {}).items():
            if k in _bool_keys:
                save[k] = "1" if (v in (1, "1", True, "true", "on")) else "0"
            elif k in _str_keys:
                save[k] = str(v).strip()

        if not save:
            h._json(400, {"error": "no valid fields to update"}); return True

        _settings.load(save)
        from db.core import _db_enqueue
        _db_enqueue(lambda _s=dict(save): db_save_settings(_s))
        try:
            db_log_audit(user, h.client_address[0] if h.client_address else "",
                         "saml_settings_update", "", ",".join(save.keys()))
        except Exception:
            pass
        h._json(200, {"ok": True, "updated": list(save.keys())})
        return True

    # ══════════════════════════════════════════════════════════════
    # ADMIN: IdP metadata import (URL / XML paste)
    # ══════════════════════════════════════════════════════════════
    if path == "/api/saml/metadata/import" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        from core.saml_auth import saml_import_metadata
        source = (body.get("source") or "").strip()
        url    = (body.get("url") or "").strip()
        text   = body.get("xml") or ""
        if source not in ("url", "xml", "file"):
            h._json(400, {"error": "source must be 'url' or 'xml'"}); return True
        try:
            result = saml_import_metadata(source, text=text, url=url)
        except Exception as e:
            log.error(f"saml_import_metadata error: {e}")
            h._json(500, {"error": "metadata import failed"}); return True
        if not result.get("ok"):
            h._json(400, {"error": result.get("message", "import failed")}); return True
        h._json(200, {
            "ok": True,
            "entity_id":    result.get("entity_id", ""),
            "sso_url":      result.get("sso_url", ""),
            "slo_url":      result.get("slo_url", ""),
            "idp_cert_set": bool(result.get("idp_cert_pem")),
            "message":      result.get("message", ""),
        })
        return True

    # ══════════════════════════════════════════════════════════════
    # ADMIN: SP signing cert generation
    # ══════════════════════════════════════════════════════════════
    if path == "/api/saml/sp_cert/generate" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        from core.saml_auth import saml_generate_sp_cert, saml_sp_cert_info
        try:
            cn = (_settings.get("saml_sp_entity_id", "") or "").strip()
            saml_generate_sp_cert(common_name=cn)
        except Exception as e:
            log.error(f"saml_generate_sp_cert error: {e}")
            h._json(500, {"error": "cert generation failed"}); return True
        h._json(200, {"ok": True, "cert_info": saml_sp_cert_info()})
        return True

    # ══════════════════════════════════════════════════════════════
    # ADMIN: test configuration
    # ══════════════════════════════════════════════════════════════
    if path == "/api/saml/test" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        from core.saml_auth import saml_test_config
        try:
            ok, msg, detail = saml_test_config()
        except Exception as e:
            log.error(f"saml_test_config error: {e}")
            h._json(500, {"error": "test failed"}); return True
        h._json(200, {"ok": ok, "message": msg, "detail": detail})
        return True

    return False
