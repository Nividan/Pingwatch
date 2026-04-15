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


def _create_session(clean: str, role: str):
    """Create session token, store in memory + DB. Returns token."""
    from db.backend import is_pg
    token   = secrets.token_hex(32)
    expires = time.time() + _settings.get("session_ttl", 86400)
    with _SESSIONS_LOCK:
        _SESSIONS[token] = {"username": clean, "expires": expires, "role": role}

    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute("DELETE FROM sessions WHERE username=%s", (clean,))
                cur.execute("INSERT INTO sessions VALUES (%s,%s,%s)",
                            (_hash_token(token), clean, expires))
                cur.execute("DELETE FROM sessions WHERE expires<%s", (time.time(),))
        except Exception as e:
            log.error(f"Session save error: {e}")
    else:
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


def _ldap_login_sync(clean: str, ldap_result: dict, current_role: str,
                     current_group_id) -> str | None:
    """
    Login-time sync for existing LDAP users:
    - Check group membership against imported LDAP groups
    - If user not in any imported group → reject login (auto-disable)
    - If user's group/role changed → update
    - Sync display_name from LDAP
    Returns the (possibly updated) role, or None to reject login.
    """
    from core.ldap_auth import _match_user_to_groups, _get_cfg
    from db.groups import db_get_ldap_mapped_groups
    from db.users  import db_update_profile

    cfg = _get_cfg()
    mapped_groups = db_get_ldap_mapped_groups()

    log.debug(f"LDAP login sync: {clean!r} — {len(mapped_groups)} mapped groups, "
              f"memberOf={len(ldap_result.get('member_of', []))} entries")
    if log.isEnabledFor(10):  # DEBUG
        for dn in ldap_result.get('member_of', []):
            log.debug(f"LDAP login sync:   memberOf: {dn}")

    # If no LDAP groups are imported, skip sync — allow login with existing role
    if not mapped_groups:
        # Still sync display_name if available
        display_name = ldap_result.get('display_name', '')
        if display_name:
            try:
                # Preserve existing email — don't overwrite with empty string
                from routes.auth import _get_user_profile
                profile = _get_user_profile(clean)
                existing_email = profile.get('email', '')
                db_update_profile(clean, display_name, existing_email,
                                  group_id=current_group_id, role=current_role)
            except Exception:
                pass
        return current_role

    member_of = ldap_result.get('member_of', [])
    user_dn   = ldap_result.get('dn', '')
    best = _match_user_to_groups(member_of, user_dn, mapped_groups, cfg)

    if best is None:
        # User not in any imported group → disable
        log.warning(f"LDAP login sync: {clean!r} not in any imported LDAP group — "
                    "rejecting login")
        try:
            from db.helpers import db_cursor
            from db.backend import is_pg as _is_pg
            ph = "%s" if _is_pg() else "?"
            with db_cursor("main") as cur:
                cur.execute(f"UPDATE users SET group_id=NULL WHERE username={ph}",
                            (clean,))
        except Exception as e:
            log.error(f"LDAP login sync: failed to clear group for {clean!r}: {e}")
        try:
            from db import db_log_audit
            db_log_audit("system", "ldap_login", "ldap_user_disabled", clean)
        except Exception:
            pass
        return None  # reject login

    # User is in a group — update if changed
    new_role = best['default_role']
    display_name = ldap_result.get('display_name', '')
    log.debug(f"LDAP login sync: {clean!r} matched group={best['name']!r} "
              f"role={new_role!r} display_name={display_name!r}")
    try:
        # Always update to keep in sync; db_update_profile is a no-op if nothing changed
        # Preserve existing email — don't overwrite with empty string
        from routes.auth import _get_user_profile
        profile = _get_user_profile(clean)
        existing_email = profile.get('email', '')
        db_update_profile(clean, display_name or '', existing_email,
                          group_id=best['id'], role=new_role)
    except Exception as e:
        log.error(f"LDAP login sync: profile update failed for {clean!r}: {e}")

    return new_role


def auth_login(username: str, password: str):
    """Return a session token on success, None on failure."""
    from db.backend import is_pg
    clean = _strip_domain(username)

    # ── Look up user in DB ────────────────────────────────────────
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "SELECT pw_hash, role, auth_type, group_id FROM users WHERE username=%s",
                    (clean,)
                )
                row = cur.fetchone()
        except Exception:
            return None
    else:
        try:
            con = sqlite3.connect(DB_PATH)
            try:
                row = con.execute(
                    "SELECT pw_hash, role, auth_type, group_id FROM users WHERE username=?",
                    (clean,)
                ).fetchone()
            finally:
                con.close()
        except Exception:
            return None

    # ── User exists → authenticate ────────────────────────────────
    if row:
        if is_pg():
            pw_hash   = row["pw_hash"]
            _role     = row["role"] or "viewer"
            auth_type = row["auth_type"] or "local"
            group_id  = row["group_id"]
        else:
            pw_hash   = row[0]
            _role     = row[1] or "viewer"
            auth_type = row[2] if len(row) > 2 else 'local'
            group_id  = row[3] if len(row) > 3 else None

        if auth_type == 'ldap':
            try:
                from core.ldap_auth import ldap_authenticate
                ldap_result = ldap_authenticate(clean, password)
                if not ldap_result:
                    return None
            except Exception as e:
                log.error(f"LDAP auth error for {clean!r}: {e}")
                return None

            # Login-time sync: refresh group/role/display_name from LDAP
            synced_role = _ldap_login_sync(clean, ldap_result, _role, group_id)
            if synced_role is None:
                return None  # auto-disabled — not in any imported group
            _role = synced_role
        else:
            if not _verify_pw(password, pw_hash):
                return None

        return _create_session(clean, _role)

    # ── User NOT found → try LDAP auto-provision ──────────────────
    try:
        import core.settings as _ap_settings
        ldap_enabled = bool(int(_ap_settings.get('ldap_enabled', 0) or 0))
        auto_provision = bool(int(_ap_settings.get('ldap_auto_provision', 0) or 0))
    except Exception:
        ldap_enabled = False
        auto_provision = False

    if not ldap_enabled or not auto_provision:
        log.debug(f"LDAP auto-provision: disabled (ldap_enabled={ldap_enabled}, "
                  f"auto_provision={auto_provision}) — unknown user {clean!r} rejected without LDAP query")
        return None

    log.debug(f"LDAP auto-provision: attempting for unknown user {clean!r}")

    # Attempt LDAP authentication for the unknown user
    try:
        from core.ldap_auth import ldap_authenticate, _match_user_to_groups, _get_cfg
        ldap_result = ldap_authenticate(clean, password)
        if not ldap_result:
            log.debug(f"LDAP auto-provision: auth failed for {clean!r}")
            return None
    except Exception as e:
        log.error(f"LDAP auto-provision auth error for {clean!r}: {e}")
        return None

    log.debug(f"LDAP auto-provision: auth OK for {clean!r}, checking group membership "
              f"(memberOf={len(ldap_result.get('member_of', []))} entries)")

    # Check group membership
    from db.groups import db_get_ldap_mapped_groups
    cfg = _get_cfg()
    mapped_groups = db_get_ldap_mapped_groups()
    if not mapped_groups:
        log.info(f"LDAP auto-provision: no imported groups — cannot provision {clean!r}")
        return None

    member_of = ldap_result.get('member_of', [])
    user_dn   = ldap_result.get('dn', '')
    best = _match_user_to_groups(member_of, user_dn, mapped_groups, cfg)

    if best is None:
        log.info(f"LDAP auto-provision: {clean!r} not in any imported group — "
                 "login rejected")
        return None

    # Auto-create the user
    domain = _ap_settings.get('ldap_domain', '') or ''
    display_name = ldap_result.get('display_name', '')
    email = ldap_result.get('email', '')
    role = best['default_role']

    from db.users import db_add_ldap_user
    ok = db_add_ldap_user(
        clean, domain, role=role,
        full_name=display_name, email=email, group_id=best['id']
    )

    if not ok:
        # Might be a race condition (another request created the user).
        # Re-query and try normal login.
        log.warning(f"LDAP auto-provision: INSERT failed for {clean!r} — "
                    "possible race condition, retrying normal login")
        return auth_login(username, password)

    log.info(f"LDAP auto-provision: created {clean!r} with role={role!r} "
             f"group={best['name']!r}")
    try:
        from db import db_log_audit
        db_log_audit("system", "ldap_auto_provision", "ldap_auto_provision",
                     f"{clean} role={role} group={best['name']}")
    except Exception:
        pass

    return _create_session(clean, role)


def auth_logout(token: str):
    from db.backend import is_pg
    with _SESSIONS_LOCK:
        _SESSIONS.pop(token, None)

    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute("DELETE FROM sessions WHERE token=%s", (_hash_token(token),))
        except Exception:
            pass
    else:
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
    from db.backend import is_pg
    with _SESSIONS_LOCK:
        to_remove = [t for t, s in _SESSIONS.items() if s["username"] == username]
        for t in to_remove:
            del _SESSIONS[t]

    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute("DELETE FROM sessions WHERE username=%s", (username,))
        except Exception as e:
            log.error(f"Session revoke error: {e}")
    else:
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
    from db.backend import is_pg
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute("SELECT pw_hash FROM users WHERE username=%s", (username,))
                row = cur.fetchone()
            return bool(row and _verify_pw(password, row["pw_hash"]))
        except Exception:
            return False
    else:
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
    from db.backend import is_pg
    if not token:
        return None
    with _SESSIONS_LOCK:
        s = _SESSIONS.get(token)
    if s:
        if s["expires"] < time.time():
            auth_logout(token)
            return None
        # Slide the expiry forward on each valid check (idle timeout semantics)
        ttl = _settings.get("session_ttl", 86400)
        with _SESSIONS_LOCK:
            if token in _SESSIONS:
                _SESSIONS[token]["expires"] = time.time() + ttl
        return s["username"]
    # Not in memory — may have survived a restart; check the DB.
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            h = _hash_token(token)
            with pg_cursor("main") as cur:
                cur.execute(
                    "SELECT s.username, s.expires, u.role "
                    "FROM sessions s JOIN users u ON u.username=s.username "
                    "WHERE s.token=%s AND s.expires>%s",
                    (h, time.time())
                )
                row = cur.fetchone()
            if row:
                with _SESSIONS_LOCK:
                    _SESSIONS[token] = {
                        "username": row["username"],
                        "expires": row["expires"],
                        "role": row["role"] or "viewer",
                    }
                return row["username"]
        except Exception as e:
            log.error(f"Session DB lookup error: {e}")
    else:
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


# ── TOTP (RFC 6238 — Google Authenticator compatible) ────────────

# Pending TOTP challenges keyed by short-lived id. Issued on POST /api/login when
# the user has TOTP enabled; consumed by POST /api/login/totp.
_TOTP_CHALLENGES: dict = {}        # cid -> {username, role, expires}
_TOTP_CHALLENGE_LOCK         = threading.Lock()
_TOTP_CHALLENGE_TTL_SEC      = 300   # 5 minutes


def totp_generate_secret() -> str:
    import pyotp
    return pyotp.random_base32()


def totp_provisioning_uri(username: str, secret: str, issuer: str = "PingWatch") -> str:
    import pyotp
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def totp_verify(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    try:
        import pyotp
        return pyotp.TOTP(secret).verify(str(code).strip(), valid_window=1)
    except Exception:
        return False


def totp_generate_recovery_codes(n: int = 10) -> tuple:
    """Return (plaintext_list, hashed_list_json_string).

    Plaintext is shown to the user once (download/print). The hashed list is
    persisted; consumption removes the hash from the JSON array.
    """
    import json
    plain  = ["-".join(secrets.token_hex(2) for _ in range(2)) for _ in range(n)]  # e.g. abcd-1234
    hashed = [hashlib.sha256(c.encode()).hexdigest() for c in plain]
    return plain, json.dumps(hashed)


def totp_consume_recovery(stored_json: str, code: str) -> tuple:
    """If `code` matches one of the hashed codes in `stored_json`, return
    (True, new_json_with_code_removed). Otherwise return (False, stored_json)."""
    import json
    if not stored_json or not code:
        return False, stored_json
    try:
        hashed_list = json.loads(stored_json)
    except Exception:
        return False, stored_json
    code_hash = hashlib.sha256(str(code).strip().encode()).hexdigest()
    if code_hash in hashed_list:
        hashed_list.remove(code_hash)
        return True, json.dumps(hashed_list)
    return False, stored_json


def totp_create_challenge(username: str, role: str) -> str:
    """Issue a short-lived challenge id for the second-factor step."""
    cid = secrets.token_urlsafe(24)
    with _TOTP_CHALLENGE_LOCK:
        # Opportunistic GC of expired entries
        now = time.time()
        for k in [k for k, v in _TOTP_CHALLENGES.items() if v["expires"] < now]:
            _TOTP_CHALLENGES.pop(k, None)
        _TOTP_CHALLENGES[cid] = {
            "username": username,
            "role":     role,
            "expires":  now + _TOTP_CHALLENGE_TTL_SEC,
        }
    return cid


def totp_resolve_challenge(cid: str) -> dict:
    """Return {username, role} for a valid challenge, or None. Does NOT consume."""
    with _TOTP_CHALLENGE_LOCK:
        entry = _TOTP_CHALLENGES.get(cid)
        if not entry or entry["expires"] < time.time():
            return None
        return {"username": entry["username"], "role": entry["role"]}


def totp_consume_challenge(cid: str) -> None:
    with _TOTP_CHALLENGE_LOCK:
        _TOTP_CHALLENGES.pop(cid, None)
