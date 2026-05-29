"""
auth.py — Password hashing and session management.
"""

from __future__ import annotations

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


# PBKDF2-SHA256 cost. 600k matches the OWASP 2023 minimum; existing hashes
# (stored as "salt:hex") were produced at 200k and are still verifiable, then
# transparently upgraded on next successful login (see _maybe_rehash).
_PBKDF2_ITERATIONS        = 600_000
_PBKDF2_LEGACY_ITERATIONS = 200_000


def _hash_pw(password: str, salt: str = None, iters: int = None) -> str:
    """Return "iters:salt:hex" — new format includes iteration count so future
    cost bumps don't lock anyone out."""
    if salt is None:
        salt = secrets.token_hex(16)
    if iters is None:
        iters = _PBKDF2_ITERATIONS
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iters)
    return f"{iters}:{salt}:{key.hex()}"


def _verify_pw(password: str, stored: str) -> bool:
    """Constant-time verify that handles both legacy ("salt:hex") and new
    ("iters:salt:hex") stored formats."""
    try:
        parts = stored.split(":")
        if len(parts) == 2:
            salt, stored_hex = parts
            expected_hex = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), salt.encode(),
                _PBKDF2_LEGACY_ITERATIONS
            ).hex()
            return secrets.compare_digest(stored_hex, expected_hex)
        if len(parts) == 3:
            iters_s, salt, stored_hex = parts
            iters = int(iters_s)
            expected_hex = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), salt.encode(), iters
            ).hex()
            return secrets.compare_digest(stored_hex, expected_hex)
    except Exception:
        pass
    return False


def _needs_rehash(stored: str) -> bool:
    """True if the stored hash is below the current iteration target."""
    try:
        parts = stored.split(":")
        if len(parts) == 2:
            return True
        if len(parts) == 3:
            return int(parts[0]) < _PBKDF2_ITERATIONS
    except Exception:
        pass
    return False


def _maybe_rehash(username: str, password: str, stored: str) -> None:
    """Transparently upgrade a user's password hash to current iterations
    after a successful verify. Failures here are non-fatal — we just miss the
    upgrade for this session."""
    if not _needs_rehash(stored):
        return
    try:
        new_hash = _hash_pw(password)
        from db.backend import is_pg
        if is_pg():
            from db.pg_pool import pg_cursor
            with pg_cursor("main") as cur:
                cur.execute("UPDATE users SET pw_hash=%s WHERE username=%s",
                            (new_hash, username))
        else:
            con = sqlite3.connect(DB_PATH, timeout=15)
            try:
                con.execute("UPDATE users SET pw_hash=? WHERE username=?",
                            (new_hash, username))
                con.commit()
            finally:
                con.close()
    except Exception as e:
        log.warning(f"Password hash upgrade failed for {username!r}: {e}")


def _strip_domain(username: str) -> str:
    """Normalize domain\\user or user@domain to plain username."""
    if '\\' in username:
        return username.split('\\', 1)[1]
    if '@' in username:
        return username.split('@')[0]
    return username


def _create_session(clean: str, role: str, ip: str = '', user_agent: str = '', device_label: str = ''):
    """Create session token, store in memory + DB. Returns token.

    `ip`, `user_agent`, `device_label` are optional metadata captured at login
    for the Active Sessions UI. Callers from SSO/LDAP/RADIUS paths that don't
    have ready access to the request can omit them — empty strings store
    cleanly and the UI surfaces them as 'Unknown'.
    """
    from db.backend import is_pg
    token   = secrets.token_hex(32)
    now     = time.time()
    expires = now + _settings.get("session_ttl", 86400)
    with _SESSIONS_LOCK:
        _SESSIONS[token] = {"username": clean, "expires": expires, "role": role}

    # Truncate UA to keep table rows reasonable
    _ua  = (user_agent or '')[:512]
    _lbl = (device_label or '')[:128]
    _ip  = (ip or '')[:64]

    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute("DELETE FROM sessions WHERE username=%s", (clean,))
                cur.execute(
                    "INSERT INTO sessions "
                    "(token, username, expires, ip, user_agent, device_label, created_at, last_active) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (_hash_token(token), clean, expires, _ip, _ua, _lbl, now, now)
                )
                cur.execute("DELETE FROM sessions WHERE expires<%s", (now,))
        except Exception as e:
            log.error(f"Session save error: {e}")
    else:
        try:
            con = sqlite3.connect(DB_PATH, timeout=15)
            try:
                con.execute("DELETE FROM sessions WHERE username=?", (clean,))
                con.execute(
                    "INSERT INTO sessions "
                    "(token, username, expires, ip, user_agent, device_label, created_at, last_active) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (_hash_token(token), clean, expires, _ip, _ua, _lbl, now, now)
                )
                con.execute("DELETE FROM sessions WHERE expires<?", (now,))
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


def auth_login(username: str, password: str, ip: str = '', user_agent: str = '', device_label: str = ''):
    """Return a session token on success, None on failure.

    `ip` / `user_agent` / `device_label` are optional and get plumbed through
    to _create_session for the Active Sessions UI.
    """
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
            con = sqlite3.connect(DB_PATH, timeout=15)
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
            _maybe_rehash(clean, password, pw_hash)

        return _create_session(clean, _role, ip=ip, user_agent=user_agent, device_label=device_label)

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

    return _create_session(clean, role, ip=ip, user_agent=user_agent, device_label=device_label)


# ── RADIUS login flow ───────────────────────────────────────────────
# Two-phase: Phase 1 takes (username, password) and may return an Access-Challenge
# prompt. Phase 2 takes the challenge_id + user response. On full success we apply
# attribute-based role mapping, auto-provision if needed, then return a session.

_RADIUS_LOGIN_CTX: dict = {}     # challenge_id → {"username", "created_ts"}
_RADIUS_CTX_LOCK = threading.Lock()
_RADIUS_CTX_TTL = 120


def _prune_radius_ctx_locked() -> None:
    import time as _t
    now = _t.time()
    for k in [k for k, v in _RADIUS_LOGIN_CTX.items()
              if now - v.get("created_ts", 0) > _RADIUS_CTX_TTL]:
        _RADIUS_LOGIN_CTX.pop(k, None)


def _radius_resolve_role(attrs: dict) -> tuple:
    """Apply first-match attribute → group mapping. Returns (group_id|None, role_str).

    Fallback order when no attribute mapping matches:
      1. radius_default_group_id + its group's default_role (if set)
      2. radius_default_role with no group
    """
    from db import db_get_radius_mapped_groups
    import core.settings as _s
    from core.radius_auth import _radius_dbg

    try:
        mapped = db_get_radius_mapped_groups() or []
    except Exception:
        mapped = []

    attr_count = sum(len(v or []) for v in (attrs or {}).values())
    _radius_dbg(f"RADIUS resolve: user has {attr_count} attribute value(s), "
                f"{len(mapped)} RADIUS-mapped group(s) to check")
    for g in mapped:
        _radius_dbg(f"RADIUS match:   mapping: {g['name']!r} → "
                    f"{g['radius_attribute']}={g['radius_value']!r} (role={g['default_role']})")

    for name, values in (attrs or {}).items():
        name_s = str(name)
        for v in (values or []):
            v_s = str(v)
            for g in mapped:
                if g["radius_attribute"] == name_s and g["radius_value"] == v_s:
                    _radius_dbg(f"RADIUS match: direct match → attr {name_s}={v_s!r} "
                                f"-> group {g['name']!r} role={g['default_role']!r}")
                    return g["id"], g["default_role"]
            _radius_dbg(f"RADIUS match: no mapping matched attr {name_s}={v_s!r}")

    # Default group fallback
    try:
        default_gid = int(_s.get("radius_default_group_id", 0) or 0)
    except (ValueError, TypeError):
        default_gid = 0
    if default_gid:
        try:
            from db import db_list_groups
            for g in db_list_groups():
                if g["id"] == default_gid:
                    _radius_dbg(f"RADIUS resolve: no attribute match — falling back "
                                f"to default group {g['name']!r} role={g.get('default_role') or 'viewer'!r}")
                    return default_gid, (g.get("default_role") or "viewer")
        except Exception:
            pass

    role = _s.get("radius_default_role", "viewer") or "viewer"
    _radius_dbg(f"RADIUS resolve: no attribute match, no default group — using default role {role!r}")
    return None, role


def _radius_lookup_user(clean: str):
    """Return {auth_type, role, group_id} or None if user row doesn't exist."""
    from db.backend import is_pg
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "SELECT auth_type, role, group_id FROM users WHERE username=%s",
                    (clean,)
                )
                row = cur.fetchone()
            if not row:
                return None
            return {"auth_type": row["auth_type"] or "local",
                    "role": row["role"] or "viewer",
                    "group_id": row["group_id"]}
        except Exception:
            return None
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        try:
            row = con.execute(
                "SELECT auth_type, role, group_id FROM users WHERE username=?",
                (clean,)
            ).fetchone()
        finally:
            con.close()
    except Exception:
        return None
    if not row:
        return None
    return {"auth_type": row[0] or "local",
            "role": row[1] or "viewer",
            "group_id": row[2]}


def _radius_update_user(clean: str, role: str, group_id):
    """Sync a RADIUS user's role + group from fresh attribute mapping."""
    from db.backend import is_pg
    try:
        if is_pg():
            from db.pg_pool import pg_cursor
            with pg_cursor("main") as cur:
                cur.execute(
                    "UPDATE users SET role=%s, group_id=%s WHERE username=%s AND auth_type='radius'",
                    (role, group_id, clean)
                )
        else:
            con = sqlite3.connect(DB_PATH, timeout=15)
            try:
                con.execute(
                    "UPDATE users SET role=?, group_id=? WHERE username=? AND auth_type='radius'",
                    (role, group_id, clean)
                )
                con.commit()
            finally:
                con.close()
    except Exception as e:
        log.error(f"RADIUS role sync failed for {clean!r}: {e}")


def _radius_post_auth(clean: str, attrs: dict, challenge_used: bool) -> dict:
    """Shared tail: resolve role, auto-provision if needed, create session.
    Returns {"status": "accept", ...} or {"status": "reject", ...}."""
    import core.settings as _s

    group_id, role = _radius_resolve_role(attrs)
    existing = _radius_lookup_user(clean)

    if existing is None:
        # Unknown user — require auto-provision
        if not int(_s.get("radius_auto_provision", 0) or 0):
            log.info(f"RADIUS: rejected unknown user {clean!r} (auto-provision disabled)")
            return {"status": "reject", "reason": "user not provisioned"}
        from db import db_add_radius_user
        ok = db_add_radius_user(clean, role=role, group_id=group_id)
        if not ok:
            # Race with another request — re-query
            existing = _radius_lookup_user(clean)
            if existing is None:
                return {"status": "reject", "reason": "provisioning failed"}
        else:
            log.info(f"RADIUS: auto-provisioned {clean!r} role={role!r} group_id={group_id}")
            try:
                from db import db_log_audit
                db_log_audit("system", "radius_auto_provision",
                             "radius_user_provisioned",
                             f"{clean} role={role}")
            except Exception:
                pass
    else:
        if existing["auth_type"] != "radius":
            # Existing local/LDAP user with same name — refuse to override
            log.warning(f"RADIUS: user {clean!r} exists with auth_type={existing['auth_type']!r} — refused")
            return {"status": "reject", "reason": "user type conflict"}
        # Sync role/group if attributes indicate a change
        if existing["role"] != role or existing["group_id"] != group_id:
            _radius_update_user(clean, role, group_id)

    token = _create_session(clean, role)
    return {"status": "accept",
            "username": clean,
            "role": role,
            "token": token,
            "challenge_used": bool(challenge_used)}


def radius_login_phase1(username: str, password: str) -> dict:
    """Initial RADIUS login. Returns one of:
      {"status": "accept",    "username", "role", "token", "challenge_used": False}
      {"status": "challenge", "challenge_id", "prompt"}
      {"status": "reject",    "reason"}
    """
    from core.radius_auth import radius_authenticate
    clean = _strip_domain(username)
    try:
        res = radius_authenticate(clean, password)
    except Exception as e:
        log.error(f"RADIUS auth error for {clean!r}: {e}")
        return {"status": "reject", "reason": "server error"}

    if res is None:
        return {"status": "reject", "reason": "authentication failed"}

    if res.get("ok"):
        return _radius_post_auth(clean, res.get("attrs") or {}, challenge_used=False)

    # Access-Challenge path
    ch = res.get("challenge") or {}
    cid = ch.get("id")
    if not cid:
        return {"status": "reject", "reason": "invalid challenge response"}
    with _RADIUS_CTX_LOCK:
        _prune_radius_ctx_locked()
        _RADIUS_LOGIN_CTX[cid] = {"username": clean, "created_ts": time.time()}
    return {"status": "challenge", "challenge_id": cid, "prompt": ch.get("prompt", "")}


def radius_login_phase2(challenge_id: str, response: str) -> dict:
    """Continue a RADIUS challenge. Same return shape as phase1 (but accept sets challenge_used=True)."""
    from core.radius_auth import radius_continue_challenge
    with _RADIUS_CTX_LOCK:
        _prune_radius_ctx_locked()
        ctx = _RADIUS_LOGIN_CTX.get(challenge_id)
    if ctx is None:
        return {"status": "reject", "reason": "challenge expired or invalid"}

    try:
        res = radius_continue_challenge(challenge_id, response)
    except Exception as e:
        log.error(f"RADIUS challenge continuation error: {e}")
        with _RADIUS_CTX_LOCK:
            _RADIUS_LOGIN_CTX.pop(challenge_id, None)
        return {"status": "reject", "reason": "server error"}

    if res is None:
        with _RADIUS_CTX_LOCK:
            _RADIUS_LOGIN_CTX.pop(challenge_id, None)
        return {"status": "reject", "reason": "authentication failed"}

    if res.get("ok"):
        with _RADIUS_CTX_LOCK:
            _RADIUS_LOGIN_CTX.pop(challenge_id, None)
        return _radius_post_auth(ctx["username"], res.get("attrs") or {},
                                 challenge_used=True)

    # Multi-step challenge — carry the user context forward under the new id
    ch = res.get("challenge") or {}
    new_cid = ch.get("id")
    if not new_cid:
        with _RADIUS_CTX_LOCK:
            _RADIUS_LOGIN_CTX.pop(challenge_id, None)
        return {"status": "reject", "reason": "invalid challenge response"}
    with _RADIUS_CTX_LOCK:
        _RADIUS_LOGIN_CTX[new_cid] = {"username": ctx["username"], "created_ts": time.time()}
        _RADIUS_LOGIN_CTX.pop(challenge_id, None)
    return {"status": "challenge", "challenge_id": new_cid, "prompt": ch.get("prompt", "")}


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
            con = sqlite3.connect(DB_PATH, timeout=15)
            try:
                con.execute("DELETE FROM sessions WHERE token=?", (_hash_token(token),))
                con.commit()
            finally:
                con.close()
        except Exception:
            pass


def auth_list_user_sessions(username: str) -> list:
    """Return all active session rows for a user, sorted by most-recent activity.

    Each row: {id, ip, user_agent, device_label, created_at, last_active, expires}.
    `id` is the SHA-256 token-hash (the same value stored as the row's primary key)
    so the frontend can pass it back to DELETE /api/me/sessions/{id} for revoke.
    """
    from db.backend import is_pg
    now = time.time()
    rows = []
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "SELECT token, ip, user_agent, device_label, created_at, last_active, expires "
                    "FROM sessions WHERE username=%s AND expires>%s "
                    "ORDER BY COALESCE(last_active, created_at, 0) DESC",
                    (username, now)
                )
                for r in cur.fetchall():
                    rows.append({
                        "id":           r["token"],
                        "ip":           r["ip"] or "",
                        "user_agent":   r["user_agent"] or "",
                        "device_label": r["device_label"] or "",
                        "created_at":   r["created_at"] or 0,
                        "last_active":  r["last_active"] or 0,
                        "expires":      r["expires"],
                    })
        except Exception as e:
            log.error(f"Session list error: {e}")
    else:
        try:
            con = sqlite3.connect(DB_PATH, timeout=15)
            try:
                cur = con.execute(
                    "SELECT token, ip, user_agent, device_label, created_at, last_active, expires "
                    "FROM sessions WHERE username=? AND expires>? "
                    "ORDER BY COALESCE(last_active, created_at, 0) DESC",
                    (username, now)
                )
                for r in cur.fetchall():
                    rows.append({
                        "id":           r[0],
                        "ip":           r[1] or "",
                        "user_agent":   r[2] or "",
                        "device_label": r[3] or "",
                        "created_at":   r[4] or 0,
                        "last_active":  r[5] or 0,
                        "expires":      r[6],
                    })
            finally:
                con.close()
        except Exception as e:
            log.error(f"Session list error: {e}")
    return rows


def auth_revoke_session_by_id(username: str, session_id: str) -> bool:
    """Revoke one session by its token-hash. Returns True if a row was deleted.

    Username is enforced server-side: a user can never revoke another user's
    session via this path (returns False if the row exists but doesn't match).
    """
    from db.backend import is_pg
    deleted = False
    # Drop from in-memory cache
    with _SESSIONS_LOCK:
        for tok, sess in list(_SESSIONS.items()):
            if _hash_token(tok) == session_id and sess.get("username") == username:
                del _SESSIONS[tok]
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "DELETE FROM sessions WHERE token=%s AND username=%s",
                    (session_id, username)
                )
                deleted = (cur.rowcount or 0) > 0
        except Exception as e:
            log.error(f"Session revoke error: {e}")
    else:
        try:
            con = sqlite3.connect(DB_PATH, timeout=15)
            try:
                cur = con.execute(
                    "DELETE FROM sessions WHERE token=? AND username=?",
                    (session_id, username)
                )
                deleted = (cur.rowcount or 0) > 0
                con.commit()
            finally:
                con.close()
        except Exception as e:
            log.error(f"Session revoke error: {e}")
    return deleted


def auth_revoke_other_user_sessions(username: str, current_token: str) -> int:
    """Revoke all sessions for `username` EXCEPT the one identified by current_token.

    Returns the number of rows deleted. Used by 'Sign out all other sessions'.
    """
    from db.backend import is_pg
    keep_hash = _hash_token(current_token) if current_token else ""
    n = 0
    # Drop from in-memory cache
    with _SESSIONS_LOCK:
        for tok in list(_SESSIONS.keys()):
            sess = _SESSIONS.get(tok)
            if not sess or sess.get("username") != username:
                continue
            if _hash_token(tok) == keep_hash:
                continue
            del _SESSIONS[tok]
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "DELETE FROM sessions WHERE username=%s AND token<>%s",
                    (username, keep_hash)
                )
                n = cur.rowcount or 0
        except Exception as e:
            log.error(f"Session revoke-others error: {e}")
    else:
        try:
            con = sqlite3.connect(DB_PATH, timeout=15)
            try:
                cur = con.execute(
                    "DELETE FROM sessions WHERE username=? AND token<>?",
                    (username, keep_hash)
                )
                n = cur.rowcount or 0
                con.commit()
            finally:
                con.close()
        except Exception as e:
            log.error(f"Session revoke-others error: {e}")
    return n


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
            con = sqlite3.connect(DB_PATH, timeout=15)
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
            con = sqlite3.connect(DB_PATH, timeout=15)
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
            con = sqlite3.connect(DB_PATH, timeout=15)
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


# ── API token auth (Bearer; scripts / CI / Terraform) ────────────
#
# Kept separate from _SESSIONS because tokens have different invalidation
# semantics: a fixed `expires_at` (or never), no sliding TTL on use, and
# revocation by row update. The cache TTL caps revoke-propagation latency
# without forcing a DB read on every request.
_API_TOKENS: dict = {}    # raw_token -> {username, role, scope, id, expires_at, token_hash, cached_at}
_API_TOKENS_LOCK = threading.Lock()
_API_TOKEN_CACHE_TTL = 300   # seconds — bounds how long a revoke takes to take effect


def auth_check_api_token(token: str):
    """Validate a Bearer API token. Returns {username, role, scope, id} or None.

    Cache → DB-by-hash. On a DB hit the result is cached for
    ``_API_TOKEN_CACHE_TTL`` and ``last_used_at`` is touched (fire-and-forget).
    Revoked or expired tokens never populate the cache.
    """
    from db.api_tokens import db_get_api_token_by_hash, db_touch_api_token_last_used
    if not token or not token.startswith("pw_"):
        return None
    now = time.time()
    with _API_TOKENS_LOCK:
        c = _API_TOKENS.get(token)
    if c and (now - c["cached_at"]) < _API_TOKEN_CACHE_TTL:
        # Token's own expires_at is authoritative even on a cache hit.
        if c.get("expires_at") is not None and c["expires_at"] < now:
            with _API_TOKENS_LOCK:
                _API_TOKENS.pop(token, None)
            return None
        return {k: c[k] for k in ("username", "role", "scope", "id")}
    # Cache miss or stale — go to the DB.
    h = _hash_token(token)
    try:
        row = db_get_api_token_by_hash(h)
    except Exception as e:
        log.error(f"API token lookup error: {type(e).__name__}: {e}")
        return None
    if not row:
        with _API_TOKENS_LOCK:
            _API_TOKENS.pop(token, None)
        return None
    entry = {"username": row["username"], "role": row["role"] or "viewer",
             "scope": row["scope"], "id": row["id"],
             "expires_at": row["expires_at"], "token_hash": h,
             "cached_at": now}
    with _API_TOKENS_LOCK:
        _API_TOKENS[token] = entry
    # Slide last_used_at forward — fire-and-forget so a write failure can
    # never block authentication.
    try:
        db_touch_api_token_last_used(row["id"])
    except Exception:
        pass
    return {k: entry[k] for k in ("username", "role", "scope", "id")}


def auth_evict_api_token_hash(token_hash: str):
    """Drop cached entries for a revoked token (matched by hash). Called
    right after db_revoke_api_token so the next request misses the cache
    and goes straight to the DB, which now reports revoked_at."""
    if not token_hash:
        return
    with _API_TOKENS_LOCK:
        victims = [t for t, e in _API_TOKENS.items()
                   if e.get("token_hash") == token_hash]
        for t in victims:
            _API_TOKENS.pop(t, None)


# ── TOTP (RFC 6238 — Google Authenticator compatible) ────────────

# Pending TOTP challenges keyed by short-lived id. Issued on POST /api/login when
# the user has TOTP enabled; consumed by POST /api/login/totp.
_TOTP_CHALLENGES: dict = {}        # cid -> {username, role, expires}
_TOTP_CHALLENGE_LOCK         = threading.Lock()
_TOTP_CHALLENGE_TTL_SEC      = 300   # 5 minutes


def totp_available() -> bool:
    """Return True if the optional pyotp dependency is importable."""
    try:
        import pyotp  # noqa: F401
        return True
    except Exception:
        return False


def totp_generate_secret() -> str:
    import pyotp
    return pyotp.random_base32()


def totp_provisioning_uri(username: str, secret: str, issuer: str = "PingWatch") -> str:
    import pyotp
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def totp_qr_data_url(uri: str) -> str:
    """Render ``uri`` as a QR code SVG, return a base64 data URL (``data:image/svg+xml;base64,...``).

    SVG is used (not PNG) so the ``qrcode`` lib works without Pillow — qrcode 8.x
    no longer pulls Pillow as a transitive dep. Returns an empty string if the
    optional ``qrcode`` dep is not installed.
    """
    try:
        import io, base64
        import qrcode
        import qrcode.image.svg
        # SvgPathImage merges all modules into one <path>, avoiding subpixel gaps
        # that SvgImage (one <rect> per module) produces when the browser downscales.
        # border=4 is the QR-spec quiet zone — some scanners (e.g. Ente Auth) require it.
        img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=4)
        buf = io.BytesIO()
        img.save(buf)
        return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        log.warning(f"totp_qr: failed to render QR code ({type(e).__name__}: {e})")
        return ""


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


def parse_user_agent_label(ua: str) -> str:
    """Derive a short human-readable label from a User-Agent string.

    Examples:
      "Mozilla/5.0 (Windows NT 10.0; Win64) ... Chrome/120.0 ..."  → "Chrome on Windows"
      "Mozilla/5.0 (Macintosh; ...) ... Safari/537.36"             → "Safari on macOS"
      "Mozilla/5.0 (X11; Linux ...) ... Firefox/121.0"             → "Firefox on Linux"
    No external library — simple substring checks are good enough.
    """
    if not ua:
        return "Unknown browser"
    ua_l = ua.lower()

    # Browser
    if "edg/" in ua_l or "edge/" in ua_l:
        browser = "Edge"
    elif "opr/" in ua_l or "opera" in ua_l:
        browser = "Opera"
    elif "chrome/" in ua_l or "chromium/" in ua_l:
        browser = "Chrome"
    elif "firefox/" in ua_l:
        browser = "Firefox"
    elif "safari/" in ua_l:
        browser = "Safari"
    else:
        browser = "Browser"

    # OS
    if "windows" in ua_l:
        os_name = "Windows"
    elif "macintosh" in ua_l or "mac os" in ua_l:
        os_name = "macOS"
    elif "iphone" in ua_l or "ipad" in ua_l:
        os_name = "iOS"
    elif "android" in ua_l:
        os_name = "Android"
    elif "linux" in ua_l:
        os_name = "Linux"
    else:
        os_name = "Unknown OS"

    return f"{browser} on {os_name}"
