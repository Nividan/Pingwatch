"""
routes/auth.py — Authentication and user-management endpoints.

Handles: /api/login, /api/logout, /api/me, /api/me/password,
         /api/users (GET/POST), /api/users/{u} (DELETE),
         /api/users/{u}/password (PATCH),
         /api/me/profile (PATCH), /api/users/{u}/profile (PATCH).
"""

import hashlib
import re
import secrets
import sqlite3
import threading
import time

from core.auth   import (auth_check, auth_check_role, auth_login, auth_logout,
                         auth_revoke_user_sessions, auth_verify_current)
from core.config import (_RE_USER, _RE_USER_PW, _RE_ME_PW,
                         _RE_ME_PROFILE, _RE_USER_PROFILE, _RE_READY)
from core.config import DB_PATH
from core.logger import log
from db          import (db_log_audit, db_list_users, db_add_user, db_add_ldap_user,
                         db_delete_user, db_set_password, db_get_user_auth_type,
                         db_update_profile, db_update_own_profile, db_update_theme)
from db.backend  import is_pg
from core.app_state import tls_active
import core.app_state as app_state
import core.settings as _settings

_RE_EMAIL         = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_RE_TRUSTED_DEV   = re.compile(r'^/api/me/trusted-devices/(\d+)$')
_TRUSTED_COOKIE   = "pw_trusted"


def _get_cookie(h, name: str) -> str:
    """Extract a named cookie value from the request headers, or ''."""
    for part in h.headers.get("Cookie", "").split(";"):
        p = part.strip()
        prefix = name + "="
        if p.startswith(prefix):
            return p[len(prefix):]
    return ""


def _trusted_cookie_header(token: str, max_age: int, tls: bool) -> str:
    """Build a Set-Cookie header value for pw_trusted."""
    _sec = "; Secure" if tls else ""
    return (f"{_TRUSTED_COOKIE}={token}; HttpOnly; Path=/; "
            f"SameSite=Strict; Max-Age={max_age}{_sec}")


def _clear_trusted_cookie(tls: bool) -> str:
    """Build a Set-Cookie header to clear pw_trusted."""
    _sec = "; Secure" if tls else ""
    return f"{_TRUSTED_COOKIE}=; HttpOnly; Path=/; Max-Age=0{_sec}"


def _get_user_profile(username: str) -> dict:
    """Return {full_name, email, theme_preference} for username."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "SELECT full_name, email, theme_preference "
                    "FROM users WHERE username=%s", (username,)
                )
                row = cur.fetchone()
            if not row:
                return {}
            return {"full_name":        row["full_name"] or "",
                    "email":            row["email"]     or "",
                    "theme_preference": row["theme_preference"] or "dark"}
        except Exception:
            return {}
    # SQLite
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT full_name, email, theme_preference "
            "FROM users WHERE username=?", (username,)
        ).fetchone()
        if not row:
            return {}
        return {"full_name":        row[0] or "",
                "email":            row[1] or "",
                "theme_preference": row[2] or "dark"}
    except Exception:
        return {}
    finally:
        con.close()

# ── Login rate-limiting state ─────────────────────────────────────
_FAIL_LOCK   = threading.Lock()
_FAIL_LOG: dict = {}   # ip → [timestamp, ...]
_FAIL_WINDOW = 60
_FAIL_MAX    = 5

# ── Admin password-reset rate-limiting (per-target) ──────────────
_PW_RESET_LOCK = threading.Lock()
_PW_RESET_LOG: dict = {}   # username → last_reset_timestamp
_PW_RESET_COOLDOWN  = 10   # seconds between resets for the same user


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # ── /api/ready ────────────────────────────────────────────────
    # Unauthenticated readiness probe — polled by the frontend splash
    # while db_load() is still running after a restart. Returns a simple
    # boolean so the client knows when it's safe to call /api/devices etc.
    # No sensitive data leaked (just a flag + version string).
    if _RE_READY.match(path) and method == "GET":
        h._json(200, {
            "ready":   bool(app_state.ready),
            "version": app_state.APP_VERSION,
        })
        return True

    # ── /api/me ───────────────────────────────────────────────────
    if path == "/api/me" and method == "GET":
        token = h._get_token()
        user  = auth_check(token)
        if user:
            role    = auth_check_role(token) or "viewer"
            profile = _get_user_profile(user)
            try:
                from db.users import db_get_totp
                _totp_enabled = int(db_get_totp(user).get("enabled", 0))
            except Exception:
                _totp_enabled = 0
            h._json(200, {"username": user, "role": role,
                          "full_name":        profile.get("full_name", ""),
                          "email":            profile.get("email", ""),
                          "theme_preference": profile.get("theme_preference", "dark"),
                          "totp_enabled":     _totp_enabled,
                          "session_ttl": int(_settings.get("session_ttl", 86400))})
        else:
            h._json(401, {"error": "unauthorized"})
        return True

    # ── /api/me/theme PATCH ───────────────────────────────────────
    # Lightweight endpoint for theme-only updates (avoids a full profile payload).
    if path == "/api/me/theme" and method == "PATCH":
        me = auth_check(h._get_token())
        if not me:
            h._json(401, {"error": "unauthorized"}); return True
        theme = str(body.get("theme", "")).strip().lower()
        if theme not in ("dark", "light"):
            h._json(400, {"error": "invalid theme"}); return True
        if not db_update_theme(me, theme):
            h._json(500, {"error": "update failed"}); return True
        h._json(200, {"ok": True, "theme": theme})
        return True

    # ── /api/me/profile PATCH ─────────────────────────────────────
    if _RE_ME_PROFILE.match(path) and method == "PATCH":
        me = auth_check(h._get_token())
        if not me:
            h._json(401, {"error": "unauthorized"}); return True
        full_name = str(body.get("full_name", "")).strip()[:200]
        email     = str(body.get("email", "")).strip()[:200]
        if email and not _RE_EMAIL.match(email):
            h._json(400, {"error": "invalid email address"}); return True
        db_update_own_profile(me, full_name, email)
        # Optional theme update alongside profile edit
        theme = str(body.get("theme_preference", "")).strip().lower()
        if theme in ("dark", "light"):
            db_update_theme(me, theme)
        db_log_audit(me, h.client_address[0], 'profile_update', me)
        h._json(200, {"ok": True})
        return True

    # ── /api/users/{u}/profile PATCH ─────────────────────────────
    mp = _RE_USER_PROFILE.match(path)
    if mp and method == "PATCH":
        caller_token = h._get_token()
        caller       = auth_check(caller_token)
        if not caller:
            h._json(401, {"error": "unauthorized"}); return True
        caller_role = auth_check_role(caller_token) or "viewer"
        target = mp.group(1)
        if caller_role != "admin" and caller != target:
            h._json(403, {"error": "forbidden"}); return True
        full_name = str(body.get("full_name", "")).strip()[:200]
        email     = str(body.get("email", "")).strip()[:200]
        if email and not _RE_EMAIL.match(email):
            h._json(400, {"error": "invalid email address"}); return True
        if caller_role == "admin":
            # Admin can also set group_id and role
            group_id  = body.get("group_id")  # None means "no group"
            new_role  = body.get("role", "").strip()
            if group_id is not None:
                try:
                    group_id = int(group_id) if group_id != "" else None
                except (TypeError, ValueError):
                    group_id = None
            if new_role and new_role not in ("viewer", "operator", "admin"):
                h._json(400, {"error": "invalid role"}); return True
            from db.users import _UNSET
            role_arg = new_role if new_role else _UNSET
            gid_arg  = group_id if "group_id" in body else _UNSET
            db_update_profile(target, full_name, email, gid_arg, role_arg)
        else:
            db_update_own_profile(target, full_name, email)
        db_log_audit(caller, h.client_address[0], 'profile_update', target)
        h._json(200, {"ok": True})
        return True

    # ── /api/users GET ────────────────────────────────────────────
    if path == "/api/users" and method == "GET":
        user, _ = h._require("admin")
        if not user: return True
        h._json(200, {"users": db_list_users()})
        return True

    # ── /api/users POST ───────────────────────────────────────────
    if path == "/api/users" and method == "POST":
        user, role = h._require("admin")
        if not user: return True
        username  = body.get("username", "").strip()
        password  = body.get("password", "")
        new_role  = body.get("role", "admin")
        auth_type = body.get("auth_type", "local")
        domain    = (body.get("domain") or "").strip()[:100]
        if new_role not in ("viewer", "operator", "admin"):
            new_role = "admin"
        if auth_type not in ("local", "ldap"):
            auth_type = "local"
        if not username:
            h._json(400, {"error": "username is required"})
            return True
        if auth_type == "ldap":
            ok = db_add_ldap_user(username, domain, new_role)
        else:
            if not password:
                h._json(400, {"error": "username and password required"})
                return True
            ok = db_add_user(username, password, new_role)
        if not ok:
            h._json(409, {"error": "username already exists"})
            return True
        # Optionally assign to a group at creation time
        _gid = body.get("group_id")
        if _gid is not None:
            try:
                _gid = int(_gid) if _gid != "" else None
            except (ValueError, TypeError):
                _gid = None
            db_update_profile(username, '', '', group_id=_gid)
        db_log_audit(user, h.client_address[0], 'user_create', username,
                     f"role={new_role},auth_type={auth_type}")
        log.debug(f"User created: {username!r} role={new_role!r} auth_type={auth_type!r}")
        h._json(200, {"ok": True})
        return True

    # ── /api/users/{u} DELETE ─────────────────────────────────────
    m = _RE_USER.match(path)
    if m and method == "DELETE":
        me, _ = h._require("admin")
        if not me: return True
        username = m.group(1)
        if username == me:
            h._json(400, {"error": "cannot delete your own account"})
            return True
        users = db_list_users()
        if len(users) <= 1:
            h._json(400, {"error": "cannot delete the last user"})
            return True
        ok = db_delete_user(username)
        if not ok:
            h._json(404, {"error": "user not found"})
            return True
        db_log_audit(me, h.client_address[0], 'user_delete', username)
        h._json(200, {"ok": True})
        return True

    # ── /api/users/{u}/password PATCH ─────────────────────────────
    m = _RE_USER_PW.match(path)
    if m and method == "PATCH":
        user, _ = h._require("admin")
        if not user: return True
        username = m.group(1)
        password = body.get("password", "")
        if not password:
            h._json(400, {"error": "password required"})
            return True
        if len(password) < 8:
            h._json(400, {"error": "Password must be at least 8 characters"})
            return True
        with _PW_RESET_LOCK:
            last = _PW_RESET_LOG.get(username, 0)
            if time.time() - last < _PW_RESET_COOLDOWN:
                h._json(429, {"error": "Password was just reset for this user. Try again shortly."})
                return True
        db_set_password(username, password)
        with _PW_RESET_LOCK:
            _PW_RESET_LOG[username] = time.time()
        auth_revoke_user_sessions(username)
        from db.users import db_revoke_trusted_devices as _revoke_td
        _revoke_td(username)
        db_log_audit(user, h.client_address[0], 'pass_reset', username)
        log.debug(f"Password reset: {username!r} by {user!r}")
        h._json(200, {"ok": True})
        return True

    # ── /api/me/password PATCH ────────────────────────────────────
    if _RE_ME_PW.match(path) and method == "PATCH":
        me = auth_check(h._get_token())
        if not me:
            h._json(401, {"error": "unauthorized"})
            return True
        if db_get_user_auth_type(me) == 'ldap':
            h._json(400, {"error": "LDAP users cannot change their password here. Contact your LDAP administrator."})
            return True
        current = body.get("current_password", "")
        new_pw  = body.get("password", "")
        if not current or not new_pw:
            h._json(400, {"error": "current_password and password required"})
            return True
        if len(new_pw) < 8:
            h._json(400, {"error": "Password must be at least 8 characters"})
            return True
        if not auth_verify_current(me, current):
            h._json(400, {"error": "Current password is incorrect"})
            return True
        db_set_password(me, new_pw)
        from db.users import db_revoke_trusted_devices as _revoke_td
        _revoke_td(me)
        db_log_audit(me, h.client_address[0], 'pass_change', me)
        h._json(200, {"ok": True})
        return True

    # ── /api/login POST ───────────────────────────────────────────
    if path == "/api/login" and method == "POST":
        ip = h.client_address[0]
        with _FAIL_LOCK:
            now = time.time()
            _fail_window = int(_settings.get("login_fail_window", _FAIL_WINDOW))
            _fail_max    = int(_settings.get("login_fail_max",    _FAIL_MAX))
            _FAIL_LOG[ip] = [t for t in _FAIL_LOG.get(ip, []) if now - t < _fail_window]
            # Prune stale entries from other IPs so the dict doesn't grow unbounded
            # (exclude current ip — its entry was just rebuilt and may be empty)
            for _old_ip in [k for k, v in _FAIL_LOG.items() if not v and k != ip]:
                del _FAIL_LOG[_old_ip]
            if len(_FAIL_LOG[ip]) >= _fail_max:
                log.warning(f"Login rate-limited: {ip} ({_fail_max} attempts in {_fail_window}s)")
                h._json(429, {"error": f"Too many failed attempts. Try again in {_fail_window} s."})
                return True
        username = body.get("username", "").strip()
        password = body.get("password", "")
        token    = auth_login(username, password)
        if not token:
            with _FAIL_LOCK:
                _FAIL_LOG.setdefault(ip, []).append(time.time())
            db_log_audit(username, ip, 'login_fail', username)
            h._json(401, {"error": "Invalid username or password"})
            return True
        with _FAIL_LOCK:
            _FAIL_LOG.pop(ip, None)
        role = auth_check_role(token) or "viewer"

        # ── 2FA gate: if user has TOTP enabled, check trusted-device cookie
        # first — if valid, skip the TOTP challenge entirely.
        from core.auth import auth_logout as _logout
        from core.auth import totp_create_challenge, totp_available
        from db.users import db_get_totp
        clean_user = username.split('\\', 1)[1] if '\\' in username else (
                     username.split('@')[0] if '@' in username else username)
        try:
            totp = db_get_totp(clean_user)
        except Exception:
            totp = {"enabled": 0}
        if totp.get("enabled"):
            # Check for a valid trusted-device cookie
            _trusted_raw = _get_cookie(h, _TRUSTED_COOKIE)
            if _trusted_raw:
                from db.users import db_lookup_trusted_device, db_touch_trusted_device
                _tok_hash = hashlib.sha256(_trusted_raw.encode()).hexdigest()
                _td_row = db_lookup_trusted_device(clean_user, _tok_hash)
                if _td_row:
                    # Trusted device — skip TOTP, issue full session
                    db_touch_trusted_device(_td_row["id"], ip)
                    db_log_audit(clean_user, ip, 'login_ok_trusted_device', clean_user)
                    _sec = "; Secure" if tls_active else ""
                    remember_hours = int(_settings.get("totp_remember_hours", 9))
                    _new_max_age = remember_hours * 3600
                    h._send_with_cookies(
                        200,
                        {"ok": True, "username": username, "role": role,
                         "session_ttl": int(_settings.get("session_ttl", 86400))},
                        [
                            f"session={token}; HttpOnly; Path=/; SameSite=Strict; Max-Age=2592000{_sec}",
                            _trusted_cookie_header(_trusted_raw, _new_max_age, bool(tls_active)),
                        ]
                    )
                    return True

            if not totp_available():
                # User has 2FA enrolled but pyotp is no longer installed.
                # Fail safe: refuse the login (better than silently bypassing 2FA).
                _logout(token)
                log.error(f"User {clean_user!r} has 2FA enabled but pyotp is not installed — login refused")
                h._json(503, {"error": "2FA required but server is missing pyotp. Contact administrator."})
                return True
            _logout(token)   # discard the just-created session
            cid = totp_create_challenge(clean_user, role)
            db_log_audit(clean_user, ip, 'login_totp_challenge', clean_user)
            _remember_max = int(_settings.get("totp_remember_hours", 9))
            h._json(200, {"totp_required": True, "challenge_id": cid,
                          "remember_hours_max": _remember_max})
            return True

        db_log_audit(username, ip, 'login_ok', username)
        _sec = "; Secure" if tls_active else ""
        h._send_with_cookie(
            200, {"ok": True, "username": username, "role": role,
                  "session_ttl": int(_settings.get("session_ttl", 86400))},
            f"session={token}; HttpOnly; Path=/; SameSite=Strict; Max-Age=2592000{_sec}"
        )
        return True

    # ── /api/login/totp POST ──────────────────────────────────────
    # Second-factor verification step. Body: {challenge_id, code}.
    # `code` may be a 6-digit TOTP code or a recovery code.
    if path == "/api/login/totp" and method == "POST":
        from core.auth import totp_available
        if not totp_available():
            h._json(503, {"error": "2FA support not installed on server (pyotp missing)"})
            return True
        from core.auth import (
            totp_resolve_challenge, totp_consume_challenge, totp_verify,
            totp_consume_recovery, _create_session,
        )
        from db.users import db_get_totp, db_set_totp
        ip = h.client_address[0]
        cid  = (body.get("challenge_id") or "").strip()
        code = (body.get("code") or "").strip()
        challenge = totp_resolve_challenge(cid)
        if not challenge:
            h._json(401, {"error": "Challenge expired or invalid"})
            return True
        # Rate-limit failed second-factor attempts on the same IP
        with _FAIL_LOCK:
            now = time.time()
            _fail_window = int(_settings.get("login_fail_window", _FAIL_WINDOW))
            _fail_max    = int(_settings.get("login_fail_max",    _FAIL_MAX))
            _FAIL_LOG[ip] = [t for t in _FAIL_LOG.get(ip, []) if now - t < _fail_window]
            if len(_FAIL_LOG[ip]) >= _fail_max:
                h._json(429, {"error": f"Too many failed attempts. Try again in {_fail_window} s."})
                return True
        username = challenge["username"]
        role     = challenge["role"]
        totp     = db_get_totp(username)
        ok = False
        if totp_verify(totp.get("secret", ""), code):
            ok = True
        else:
            consumed, new_json = totp_consume_recovery(totp.get("recovery_json", ""), code)
            if consumed:
                ok = True
                # Persist the recovery-code list with this code removed
                try:
                    db_set_totp(username, totp.get("secret", ""), 1, new_json)
                except Exception:
                    pass
                db_log_audit(username, ip, 'totp_recovery_used', username)
        if not ok:
            with _FAIL_LOCK:
                _FAIL_LOG.setdefault(ip, []).append(time.time())
            db_log_audit(username, ip, 'totp_failed', username)
            h._json(401, {"error": "Invalid 2FA code"})
            return True
        # Success — issue the real session
        totp_consume_challenge(cid)
        with _FAIL_LOCK:
            _FAIL_LOG.pop(ip, None)
        token = _create_session(username, role)
        db_log_audit(username, ip, 'login_ok', username)
        _sec = "; Secure" if tls_active else ""
        _session_cookie = (f"session={token}; HttpOnly; Path=/; "
                           f"SameSite=Strict; Max-Age=2592000{_sec}")

        # ── Trusted device: remember this device? ─────────────────
        # Duration is admin-controlled — Settings → Security →
        # totp_remember_hours. The login screen only exposes a
        # checkbox; any client-supplied `remember_hours` is ignored.
        remember = bool(body.get("remember", False))
        if remember:
            from db.users import db_add_trusted_device
            from core.auth import parse_user_agent_label
            remember_hours = int(_settings.get("totp_remember_hours", 9))
            if remember_hours > 0:
                _raw_token = secrets.token_urlsafe(32)
                _tok_hash  = hashlib.sha256(_raw_token.encode()).hexdigest()
                _ua_label  = parse_user_agent_label(
                    h.headers.get("User-Agent", ""))
                db_add_trusted_device(
                    username, _tok_hash, _ua_label, remember_hours,
                    ip, h.headers.get("User-Agent", "")
                )
                db_log_audit(username, ip, 'trusted_device_added', username,
                             f"label={_ua_label!r} hours={remember_hours}")
                _td_cookie = _trusted_cookie_header(
                    _raw_token, remember_hours * 3600, bool(tls_active))
                h._send_with_cookies(
                    200,
                    {"ok": True, "username": username, "role": role,
                     "session_ttl": int(_settings.get("session_ttl", 86400))},
                    [_session_cookie, _td_cookie]
                )
                return True

        h._send_with_cookie(
            200, {"ok": True, "username": username, "role": role,
                  "session_ttl": int(_settings.get("session_ttl", 86400))},
            _session_cookie
        )
        return True

    # ── /api/me/totp/setup POST — issue secret + provisioning URI ─
    if path == "/api/me/totp/setup" and method == "POST":
        me = h._auth()
        if not me: return True
        from core.auth import totp_available
        if not totp_available():
            h._json(503, {"error": "2FA support not installed on server (pyotp missing)"})
            return True
        from core.auth import totp_generate_secret, totp_provisioning_uri, totp_qr_data_url
        from db.users import db_get_totp, db_set_totp
        existing = db_get_totp(me)
        if existing.get("enabled"):
            h._json(409, {"error": "2FA is already enabled. Disable it first."})
            return True
        secret = totp_generate_secret()
        # Persist secret with enabled=0 (pending verification)
        db_set_totp(me, secret, 0, "")
        uri = totp_provisioning_uri(me, secret)
        qr = totp_qr_data_url(uri)
        h._json(200, {"secret": secret, "provisioning_uri": uri, "qr_img": qr})
        return True

    # ── /api/me/totp/verify POST — confirm enrolment ──────────────
    if path == "/api/me/totp/verify" and method == "POST":
        me = h._auth()
        if not me: return True
        from core.auth import totp_available
        if not totp_available():
            h._json(503, {"error": "2FA support not installed on server (pyotp missing)"})
            return True
        from core.auth import totp_verify, totp_generate_recovery_codes
        from db.users import db_get_totp, db_set_totp
        code = (body.get("code") or "").strip()
        totp = db_get_totp(me)
        if not totp.get("secret"):
            h._json(400, {"error": "Run /api/me/totp/setup first"})
            return True
        if not totp_verify(totp["secret"], code):
            h._json(401, {"error": "Invalid 2FA code"})
            return True
        plain, hashed_json = totp_generate_recovery_codes(10)
        db_set_totp(me, totp["secret"], 1, hashed_json)
        db_log_audit(me, h.client_address[0], 'totp_enabled', me)
        h._json(200, {"ok": True, "recovery_codes": plain})
        return True

    # ── /api/me/totp/disable POST — turn off 2FA ──────────────────
    if path == "/api/me/totp/disable" and method == "POST":
        me = h._auth()
        if not me: return True
        from core.auth import totp_available
        if not totp_available():
            h._json(503, {"error": "2FA support not installed on server (pyotp missing)"})
            return True
        from core.auth import auth_verify_current, totp_verify
        from db.users import db_get_totp, db_clear_totp
        password = body.get("password", "")
        code     = (body.get("code") or "").strip()
        if not auth_verify_current(me, password):
            h._json(401, {"error": "Invalid password"})
            return True
        totp = db_get_totp(me)
        if totp.get("enabled") and not totp_verify(totp.get("secret", ""), code):
            h._json(401, {"error": "Invalid 2FA code"})
            return True
        db_clear_totp(me)
        from db.users import db_revoke_trusted_devices as _revoke_td
        _revoke_td(me)
        db_log_audit(me, h.client_address[0], 'totp_disabled', me)
        # Clear the trusted-device cookie (devices no longer valid) but keep the session
        h._send_with_cookies(200, {"ok": True}, [
            _clear_trusted_cookie(bool(tls_active)),
        ])
        return True

    # ── /api/users/{name}/totp/reset POST (admin) — clear a user's 2FA ──
    if method == "POST" and path.startswith("/api/users/") and path.endswith("/totp/reset"):
        admin, _ = h._require("admin")
        if not admin: return True
        from db.users import db_clear_totp
        target = path[len("/api/users/"):-len("/totp/reset")]
        if not target:
            h._json(400, {"error": "username required"})
            return True
        try:
            db_clear_totp(target)
            from db.users import db_revoke_trusted_devices as _revoke_td
            _revoke_td(target)
            db_log_audit(admin, h.client_address[0], 'totp_admin_reset', target)
            h._json(200, {"ok": True})
        except Exception as e:
            log.error(f"totp_admin_reset error: {e}")
            h._json(500, {"error": "Reset failed"})
        return True

    # ── /api/me/trusted-devices GET — list own trusted devices ────
    if path == "/api/me/trusted-devices" and method == "GET":
        me = auth_check(h._get_token())
        if not me:
            h._json(401, {"error": "unauthorized"}); return True
        from db.users import db_list_trusted_devices
        devices = db_list_trusted_devices(me)
        # Tag the current device (if any)
        _current_hash = ""
        _trusted_raw = _get_cookie(h, _TRUSTED_COOKIE)
        if _trusted_raw:
            _current_hash = hashlib.sha256(_trusted_raw.encode()).hexdigest()
        current_id = None
        if _current_hash:
            from db.users import db_lookup_trusted_device
            _row = db_lookup_trusted_device(me, _current_hash)
            if _row:
                current_id = _row["id"]
        for d in devices:
            d["current"] = (d["id"] == current_id)
        h._json(200, {"devices": devices})
        return True

    # ── /api/me/trusted-devices DELETE — revoke all ────────────────
    if path == "/api/me/trusted-devices" and method == "DELETE":
        me = auth_check(h._get_token())
        if not me:
            h._json(401, {"error": "unauthorized"}); return True
        from db.users import db_revoke_trusted_devices
        n = db_revoke_trusted_devices(me)
        db_log_audit(me, h.client_address[0], 'trusted_devices_revoke_all', me,
                     f"count={n}")
        _sec = "; Secure" if tls_active else ""
        h._send_with_cookies(200, {"ok": True, "revoked": n}, [
            _clear_trusted_cookie(bool(tls_active)),
        ])
        return True

    # ── /api/me/trusted-devices/{id} DELETE — revoke one device ───
    _td_match = _RE_TRUSTED_DEV.match(path)
    if _td_match and method == "DELETE":
        me = auth_check(h._get_token())
        if not me:
            h._json(401, {"error": "unauthorized"}); return True
        dev_id = int(_td_match.group(1))
        from db.users import db_revoke_trusted_device, db_lookup_trusted_device
        # Check if this is the current device — if so, clear the cookie too
        _trusted_raw = _get_cookie(h, _TRUSTED_COOKIE)
        _is_current = False
        if _trusted_raw:
            _current_hash = hashlib.sha256(_trusted_raw.encode()).hexdigest()
            _row = db_lookup_trusted_device(me, _current_hash)
            if _row and _row["id"] == dev_id:
                _is_current = True
        ok = db_revoke_trusted_device(me, dev_id)
        if not ok:
            h._json(404, {"error": "Device not found"}); return True
        db_log_audit(me, h.client_address[0], 'trusted_device_revoked', me,
                     f"id={dev_id}")
        if _is_current:
            _sec = "; Secure" if tls_active else ""
            h._send_with_cookies(200, {"ok": True}, [
                _clear_trusted_cookie(bool(tls_active)),
            ])
        else:
            h._json(200, {"ok": True})
        return True

    # ── /api/logout POST ──────────────────────────────────────────
    if path == "/api/logout" and method == "POST":
        token     = h._get_token()
        me_logout = auth_check(token) or "anonymous"
        if token: auth_logout(token)
        db_log_audit(me_logout, h.client_address[0], 'logout', me_logout)
        _sec = "; Secure" if tls_active else ""
        h._send_with_cookie(200, {"ok": True},
            f"session=; HttpOnly; Path=/; SameSite=Strict; Max-Age=0{_sec}")
        return True

    return False
