"""
auth.py — Password hashing and session management.
test commit test branch
"""

import hashlib
import secrets
import sqlite3
import threading
import time

import settings as _settings
from config import DB_PATH
from logger import log

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


def auth_login(username: str, password: str):
    """Return a session token on success, None on failure."""
    try:
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            "SELECT pw_hash FROM users WHERE username=?", (username,)
        ).fetchone()
        con.close()
    except Exception:
        return None
    if not row or not _verify_pw(password, row[0]):
        return None
    token   = secrets.token_hex(32)
    expires = time.time() + _settings.get("session_ttl", 86400)
    # Load role for this user at login time (cached in session)
    _role = "viewer"
    try:
        _rc = sqlite3.connect(DB_PATH)
        _rr = _rc.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        _rc.close()
        if _rr:
            _role = _rr[0]
    except Exception:
        pass
    with _SESSIONS_LOCK:
        _SESSIONS[token] = {"username": username, "expires": expires, "role": _role}
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("INSERT INTO sessions VALUES (?,?,?)", (token, username, expires))
        con.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"Session save error: {e}")
    return token


def auth_logout(token: str):
    with _SESSIONS_LOCK:
        _SESSIONS.pop(token, None)
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM sessions WHERE token=?", (token,))
        con.commit()
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
        con.execute("DELETE FROM sessions WHERE username=?", (username,))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"Session revoke error: {e}")


def auth_verify_current(username: str, password: str) -> bool:
    """Return True if password matches the stored hash for username."""
    try:
        con = sqlite3.connect(DB_PATH)
        row = con.execute("SELECT pw_hash FROM users WHERE username=?", (username,)).fetchone()
        con.close()
        return bool(row and _verify_pw(password, row[0]))
    except Exception:
        return False


def auth_check(token: str):
    """Return username if session token is valid, else None."""
    if not token:
        return None
    with _SESSIONS_LOCK:
        s = _SESSIONS.get(token)
    if not s:
        return None
    if s["expires"] < time.time():
        auth_logout(token)
        return None
    return s["username"]


def auth_check_role(token: str):
    """Return the role of an authenticated session, or None if invalid."""
    if not token:
        return None
    with _SESSIONS_LOCK:
        s = _SESSIONS.get(token)
    if not s or s["expires"] < time.time():
        return None
    return s.get("role", "viewer")
