"""
auth.py — Password hashing and session management.
"""

import hashlib
import secrets
import sqlite3
import threading
import time


def _hash_token(token: str) -> str:
    """SHA-256 of the session token — stored in DB so a DB leak ≠ valid session."""
    return hashlib.sha256(token.encode()).hexdigest()

from . import settings as _settings
from .config import DB_PATH
from .logger import log

_SESSIONS: dict      = {}   # token -> {username, expires}
_SESSIONS_LOCK       = threading.Lock()


def _hash_pw(password: str, salt: str = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"{salt}:{key.hex()}"


def _verify_pw(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split(":", 1)
        return secrets.compare_digest(stored, _hash_pw(password, salt))
    except Exception:
        return False


def _strip_domain(username: str) -> str:
    """Normalize domain\\user or user@domain to plain username."""
    if '\\' in username:
        return username.split('\\', 1)[1]
    if '@' in username:
        return username.split('@')[0]
    return username


def auth_login(username: str, password: str):
    """Return a session token on success, None on failure."""
    clean = _strip_domain(username)
    try:
        con = sqlite3.connect(DB_PATH)
        try:
            row = con.execute(
                "SELECT pw_hash, role, auth_type FROM users WHERE username=?", (clean,)
            ).fetchone()
        finally:
            con.close()
    except Exception:
        return None
    if not row:
        return None

    pw_hash   = row[0]
    _role     = row[1] or "viewer"
    auth_type = row[2] if len(row) > 2 else 'local'

    if auth_type == 'ldap':
        # Domain user — authenticate against LDAP directory
        try:
            from core.ldap_auth import ldap_authenticate
            if not ldap_authenticate(clean, password):
                return None
        except Exception as e:
            log.error(f"LDAP auth error for {clean!r}: {e}")
            return None
    else:
        # Local user — verify password hash
        if not _verify_pw(password, pw_hash):
            return None

    token   = secrets.token_hex(32)
    expires = time.time() + _settings.get("session_ttl", 86400)
    with _SESSIONS_LOCK:
        _SESSIONS[token] = {"username": clean, "expires": expires, "role": _role}
    try:
        con = sqlite3.connect(DB_PATH)
        try:
            con.execute("DELETE FROM sessions WHERE username=?", (clean,))
            con.execute("INSERT INTO sessions VALUES (?,?,?)", (_hash_token(token), clean, expires))
            con.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            con.commit()
        finally:
            con.close()
    except Exception as e:
        log.error(f"Session save error: {e}")
    return token


def auth_logout(token: str):
    with _SESSIONS_LOCK:
        _SESSIONS.pop(token, None)
    try:
        con = sqlite3.connect(DB_PATH)
        try:
            con.execute("DELETE FROM sessions WHERE token=?", (_hash_token(token),))
            con.commit()
        finally:
            con.close()
    except Exception:
        pass


def auth_revoke_user_sessions(username: str):
    """Invalidate all active sessions for a given user (e.g. after password reset)."""
    with _SESSIONS_LOCK:
        to_remove = [t for t, s in _SESSIONS.items() if s["username"] == username]
        for t in to_remove:
            del _SESSIONS[t]
    try:
        con = sqlite3.connect(DB_PATH)
        try:
            con.execute("DELETE FROM sessions WHERE username=?", (username,))
            con.commit()
        finally:
            con.close()
    except Exception as e:
        log.error(f"Session revoke error: {e}")


def auth_verify_current(username: str, password: str) -> bool:
    """Return True if password matches the stored hash for username."""
    try:
        con = sqlite3.connect(DB_PATH)
        try:
            row = con.execute("SELECT pw_hash FROM users WHERE username=?", (username,)).fetchone()
        finally:
            con.close()
        return bool(row and _verify_pw(password, row[0]))
    except Exception:
        return False


def auth_check(token: str):
    """Return username if session token is valid, else None.

    Falls back to a DB lookup on cache miss so sessions survive server restarts.
    On a successful DB hit the session is re-populated into _SESSIONS so that
    the immediately-following auth_check_role() call finds it in memory.
    """
    if not token:
        return None
    with _SESSIONS_LOCK:
        s = _SESSIONS.get(token)
    if s:
        if s["expires"] < time.time():
            auth_logout(token)
            return None
        return s["username"]
    # Not in memory — may have survived a restart; check the DB.
    try:
        h = _hash_token(token)
        con = sqlite3.connect(DB_PATH)
        try:
            row = con.execute(
                "SELECT s.username, s.expires, u.role "
                "FROM sessions s JOIN users u ON u.username=s.username "
                "WHERE s.token=? AND s.expires>?",
                (h, time.time())
            ).fetchone()
        finally:
            con.close()
        if row:
            username, expires, role = row
            with _SESSIONS_LOCK:
                _SESSIONS[token] = {"username": username, "expires": expires,
                                    "role": role or "viewer"}
            return username
    except Exception as e:
        log.error(f"Session DB lookup error: {e}")
    return None


def auth_check_role(token: str):
    """Return the role of an authenticated session, or None if invalid."""
    if not token:
        return None
    with _SESSIONS_LOCK:
        s = _SESSIONS.get(token)
    if not s or s["expires"] < time.time():
        return None
    return s.get("role", "viewer")
