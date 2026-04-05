"""
routes/auth.py — Authentication and user-management endpoints.

Handles: /api/login, /api/logout, /api/me, /api/me/password,
         /api/users (GET/POST), /api/users/{u} (DELETE),
         /api/users/{u}/password (PATCH),
         /api/me/profile (PATCH), /api/users/{u}/profile (PATCH).
"""

import re
import sqlite3
import threading
import time

from core.auth   import (auth_check, auth_check_role, auth_login, auth_logout,
                         auth_revoke_user_sessions, auth_verify_current)
from core.config import (_RE_USER, _RE_USER_PW, _RE_ME_PW,
                         _RE_ME_PROFILE, _RE_USER_PROFILE)
from core.config import DB_PATH
from db          import (db_log_audit, db_list_users, db_add_user, db_add_ldap_user,
                         db_delete_user, db_set_password, db_get_user_auth_type,
                         db_update_profile, db_update_own_profile)
from db.backend  import is_pg
from core.logger    import log
from core.app_state import tls_active
import core.settings as _settings

_RE_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _get_user_profile(username: str) -> dict:
    """Return {full_name, email} for username."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "SELECT full_name, email FROM users WHERE username=%s", (username,)
                )
                row = cur.fetchone()
            return {"full_name": row["full_name"] or "", "email": row["email"] or ""} if row else {}
        except Exception:
            return {}
    # SQLite
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT full_name, email FROM users WHERE username=?", (username,)
        ).fetchone()
        return {"full_name": row[0] or "", "email": row[1] or ""} if row else {}
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

    # ── /api/me ───────────────────────────────────────────────────
    if path == "/api/me" and method == "GET":
        token = h._get_token()
        user  = auth_check(token)
        if user:
            role    = auth_check_role(token) or "viewer"
            profile = _get_user_profile(user)
            h._json(200, {"username": user, "role": role,
                          "full_name":   profile.get("full_name", ""),
                          "email":       profile.get("email", ""),
                          "session_ttl": int(_settings.get("session_ttl", 86400))})
        else:
            h._json(401, {"error": "unauthorized"})
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
        log.info(f"User created: {username} (role={new_role}, auth_type={auth_type})")
        db_log_audit(user, h.client_address[0], 'user_create', username,
                     f"role={new_role},auth_type={auth_type}")
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
        log.info(f"Password reset for user: {username}")
        db_log_audit(user, h.client_address[0], 'pass_reset', username)
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
        log.info(f"User {me} changed their own password")
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
                h._json(429, {"error": f"Too many failed attempts. Try again in {_fail_window} s."})
                return True
        username = body.get("username", "").strip()
        password = body.get("password", "")
        token    = auth_login(username, password)
        if not token:
            with _FAIL_LOCK:
                _FAIL_LOG.setdefault(ip, []).append(time.time())
            db_log_audit(username, ip, 'login_fail', username)
            log.warning(f"Login FAILED: {username!r} from {ip}")
            h._json(401, {"error": "Invalid username or password"})
            return True
        with _FAIL_LOCK:
            _FAIL_LOG.pop(ip, None)
        db_log_audit(username, ip, 'login_ok', username)
        log.info(f"Login OK: {username!r} from {ip}")
        role = auth_check_role(token) or "viewer"
        _sec = "; Secure" if tls_active else ""
        h._send_with_cookie(
            200, {"ok": True, "username": username, "role": role,
                  "session_ttl": int(_settings.get("session_ttl", 86400))},
            f"session={token}; HttpOnly; Path=/; SameSite=Strict; Max-Age=2592000{_sec}"
        )
        return True

    # ── /api/logout POST ──────────────────────────────────────────
    if path == "/api/logout" and method == "POST":
        token     = h._get_token()
        me_logout = auth_check(token) or "anonymous"
        if token: auth_logout(token)
        db_log_audit(me_logout, h.client_address[0], 'logout', me_logout)
        _sec = "; Secure" if tls_active else ""
        h._send_with_cookie(200, {"ok": True}, f"session=; HttpOnly; Path=/; Max-Age=0{_sec}")
        return True

    return False
