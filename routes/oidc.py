"""
routes/oidc.py — OpenID Connect RP endpoints.

Public (no auth):
  GET  /api/oidc/login             Redirect to IdP authorization endpoint
  GET  /api/oidc/callback          Exchange code, validate token, issue session

Admin:
  GET   /api/oidc/settings
  PATCH /api/oidc/settings
  POST  /api/oidc/discovery/refresh
  POST  /api/oidc/test
"""

from __future__ import annotations

from core.logger import log
import core.settings as _settings


def _send_302(h, location: str, cookies: list[str] | None = None) -> None:
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


def _send_html(h, code: int, html: str) -> None:
    body = html.encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    try:
        h._sec_headers()
    except Exception:
        pass
    h.end_headers()
    h.wfile.write(body)


def _issue_session_cookie(h, username: str, role: str) -> str:
    from core.auth import _create_session
    token = _create_session(username, role)
    tls_active = bool(getattr(h.server, "tls_active", False))
    sec = "; Secure" if tls_active else ""
    return f"session={token}; HttpOnly; Path=/; SameSite=Strict; Max-Age=2592000{sec}"


def _parse_query(path: str) -> dict:
    """Extract ?k=v&... from a path. Returns {k: v}."""
    from urllib.parse import urlparse, parse_qs
    q = urlparse(path).query
    if not q:
        return {}
    parsed = parse_qs(q, keep_blank_values=True)
    return {k: (v[0] if v else "") for k, v in parsed.items()}


def handle(h, method, path, body) -> bool:

    # Normalize path — strip query string for matching
    from urllib.parse import urlparse
    clean_path = urlparse(path).path if "?" in path else path

    # ══════════════════════════════════════════════════════════════
    # PUBLIC: authorization redirect
    # ══════════════════════════════════════════════════════════════
    if clean_path == "/api/oidc/login" and method == "GET":
        from core.oidc_auth import oidc_build_auth_url
        try:
            ar = oidc_build_auth_url()
        except Exception as e:
            log.error(f"oidc_build_auth_url error: {e}")
            _send_html(h, 503,
                       "<h1>OIDC unavailable</h1><p>Contact administrator.</p>")
            return True
        if not ar.get("ok"):
            _send_html(h, 503,
                       f"<h1>OIDC not configured</h1><p>{ar.get('message','')}</p>")
            return True
        _send_302(h, ar["url"])
        return True

    # ══════════════════════════════════════════════════════════════
    # PUBLIC: callback — exchange code → tokens → session
    # ══════════════════════════════════════════════════════════════
    if clean_path == "/api/oidc/callback" and method == "GET":
        q = _parse_query(path)
        # Check for error returned by IdP
        if q.get("error"):
            err = q.get("error_description") or q.get("error", "unknown")
            _send_html(h, 401,
                       f"<h1>Sign-in failed</h1><p>IdP returned error: {err}. "
                       '<a href="/">Return to PingWatch</a>.</p>')
            return True
        code  = q.get("code", "")
        state = q.get("state", "")
        if not code or not state:
            _send_html(h, 400,
                       "<h1>Invalid callback</h1><p>Missing code or state parameter. "
                       '<a href="/">Return to PingWatch</a>.</p>')
            return True

        from core.oidc_auth import oidc_exchange_code, _get_cfg
        from core.sso_common import sso_provision_or_sync, sanitize_username

        try:
            parsed = oidc_exchange_code(code, state)
        except Exception as e:
            log.error(f"OIDC exchange error: {e}")
            _send_html(h, 400,
                       "<h1>Sign-in failed</h1>"
                       '<p>Could not validate OIDC response. '
                       '<a href="/">Return to PingWatch</a>.</p>')
            return True

        if not parsed.get("ok"):
            msg = parsed.get("message", "validation failed")
            log.warning(f"OIDC callback rejected: {msg}")
            _send_html(h, 401,
                       f"<h1>Sign-in rejected</h1><p>{msg}. "
                       '<a href="/">Return to PingWatch</a>.</p>')
            return True

        cfg = _get_cfg()
        username = sanitize_username(parsed["username"])
        result = sso_provision_or_sync(
            external_id=parsed["external_id"],
            username_hint=username,
            email=parsed.get("email", ""),
            display_name=parsed.get("display_name", ""),
            groups=parsed.get("groups", []),
            auth_type="oidc",
            default_role=cfg["default_role"],
            allow_unmapped=cfg["allow_unmapped"],
        )
        if result is None:
            _send_html(h, 403,
                       "<h1>Access denied</h1>"
                       "<p>You are not authorized to access PingWatch. "
                       'Contact your administrator. <a href="/">Return</a>.</p>')
            return True

        resolved_username, role = result

        # TOTP gate
        try:
            from db import db_get_totp
            totp_row = db_get_totp(resolved_username)
            if totp_row and int(totp_row.get("enabled", 0) or 0):
                import secrets as _sec
                import time as _t
                pending = _sec.token_urlsafe(24)
                from core.oidc_auth import _state_put
                _state_put(f"sso-totp-{pending}",
                           {"username": resolved_username, "role": role, "ts": _t.time()})
                _send_302(h, f"/?sso_totp={pending}")
                return True
        except Exception as e:
            log.warning(f"OIDC TOTP gate check failed: {e}")

        cookie = _issue_session_cookie(h, resolved_username, role)
        log.info(f"OIDC login: {resolved_username!r} role={role}")
        _send_302(h, "/", cookies=[cookie])
        return True

    # ══════════════════════════════════════════════════════════════
    # ADMIN: read config (secrets redacted)
    # ══════════════════════════════════════════════════════════════
    if clean_path == "/api/oidc/settings" and method == "GET":
        user, _ = h._require("admin")
        if not user: return True
        import json as _json
        discovery_raw = _settings.get("oidc_discovery_cache", "") or ""
        discovery = {}
        if discovery_raw:
            try:
                discovery = _json.loads(discovery_raw)
            except (ValueError, TypeError):
                discovery = {}
        resp = {
            "oidc_enabled":               int(_settings.get("oidc_enabled", 0) or 0),
            "oidc_issuer_url":            _settings.get("oidc_issuer_url", ""),
            "oidc_client_id":             _settings.get("oidc_client_id", ""),
            "oidc_redirect_uri":          _settings.get("oidc_redirect_uri", ""),
            "oidc_scopes":                _settings.get("oidc_scopes", "openid profile email groups"),
            "oidc_claim_username":        _settings.get("oidc_claim_username", "preferred_username"),
            "oidc_claim_email":           _settings.get("oidc_claim_email", "email"),
            "oidc_claim_display_name":    _settings.get("oidc_claim_display_name", "name"),
            "oidc_claim_groups":          _settings.get("oidc_claim_groups", "groups"),
            "oidc_auto_provision":        int(_settings.get("oidc_auto_provision", 1) or 0),
            "oidc_default_role":          _settings.get("oidc_default_role", "viewer"),
            "oidc_allow_unmapped":        int(_settings.get("oidc_allow_unmapped", 1) or 0),
            "oidc_display_name":          _settings.get("oidc_display_name", "Single Sign-On"),
            "oidc_discovery_fetched_ts":  int(_settings.get("oidc_discovery_fetched_ts", 0) or 0),
            # Redacted
            "oidc_client_secret_set":     bool(_settings.get("oidc_client_secret_enc", "")),
            # Useful display fields from discovery
            "discovery_issuer":           discovery.get("issuer", ""),
            "discovery_auth_endpoint":    discovery.get("authorization_endpoint", ""),
            "discovery_token_endpoint":   discovery.get("token_endpoint", ""),
            "discovery_userinfo_endpoint":discovery.get("userinfo_endpoint", ""),
            "discovery_jwks_uri":         discovery.get("jwks_uri", ""),
            "discovery_end_session_endpoint": discovery.get("end_session_endpoint", ""),
        }
        h._json(200, resp)
        return True

    # ══════════════════════════════════════════════════════════════
    # ADMIN: update config
    # ══════════════════════════════════════════════════════════════
    if clean_path == "/api/oidc/settings" and method == "PATCH":
        user, _ = h._require("admin")
        if not user: return True
        from db import db_save_settings, db_log_audit
        from db.backups import encrypt_pw

        save: dict = {}
        _bool_keys = {"oidc_enabled", "oidc_auto_provision", "oidc_allow_unmapped"}
        _str_keys  = {"oidc_issuer_url", "oidc_client_id", "oidc_redirect_uri",
                      "oidc_scopes", "oidc_claim_username", "oidc_claim_email",
                      "oidc_claim_display_name", "oidc_claim_groups",
                      "oidc_default_role", "oidc_display_name"}

        for k, v in (body or {}).items():
            if k in _bool_keys:
                save[k] = "1" if (v in (1, "1", True, "true", "on")) else "0"
            elif k in _str_keys:
                save[k] = str(v).strip()
            elif k == "oidc_client_secret":
                # Encrypt only if non-empty; empty means "keep existing"
                raw = (v or "").strip()
                if raw:
                    save["oidc_client_secret_enc"] = encrypt_pw(raw)

        if not save:
            h._json(400, {"error": "no valid fields to update"}); return True

        _settings.load(save)
        from db.core import _db_enqueue
        _db_enqueue(lambda _s=dict(save): db_save_settings(_s))

        # If issuer changed, kick off a discovery refresh async
        if "oidc_issuer_url" in save and save["oidc_issuer_url"]:
            try:
                import threading as _t
                from core.oidc_auth import oidc_refresh_discovery
                _t.Thread(target=oidc_refresh_discovery, daemon=True).start()
            except Exception as e:
                log.warning(f"OIDC auto-discovery refresh failed: {e}")

        try:
            db_log_audit(user, h.client_address[0] if h.client_address else "",
                         "oidc_settings_update", "", ",".join(save.keys()))
        except Exception:
            pass
        h._json(200, {"ok": True, "updated": list(save.keys())})
        return True

    # ══════════════════════════════════════════════════════════════
    # ADMIN: refresh discovery
    # ══════════════════════════════════════════════════════════════
    if clean_path == "/api/oidc/discovery/refresh" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        from core.oidc_auth import oidc_refresh_discovery
        try:
            result = oidc_refresh_discovery()
        except Exception as e:
            log.error(f"oidc_refresh_discovery error: {e}")
            h._json(500, {"error": "discovery refresh failed"}); return True
        if not result.get("ok"):
            h._json(400, {"error": result.get("message", "discovery refresh failed")}); return True
        h._json(200, result)
        return True

    # ══════════════════════════════════════════════════════════════
    # ADMIN: test configuration
    # ══════════════════════════════════════════════════════════════
    if clean_path == "/api/oidc/test" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        from core.oidc_auth import oidc_test_config
        try:
            ok, msg, detail = oidc_test_config()
        except Exception as e:
            log.error(f"oidc_test_config error: {e}")
            h._json(500, {"error": "test failed"}); return True
        h._json(200, {"ok": ok, "message": msg, "detail": detail})
        return True

    return False
