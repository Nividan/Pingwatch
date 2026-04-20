"""
db/users.py — User management and app settings persistence.

Refactored to use db.helpers (db_query, db_execute, db_upsert, db_cursor)
which abstracts the SQLite vs PostgreSQL boilerplate. Functions that need
backend-specific behavior (e.g., catching IntegrityError) use db_cursor()
directly with a single try/except branch.
"""

from core.logger import log
from db.backend  import is_pg
from db.helpers  import db_query, db_query_one, db_execute, db_upsert, db_cursor


# ── App settings ─────────────────────────────────────────────────

def db_load_settings() -> dict:
    """Return all app_settings rows as a plain dict (values cast to int where numeric)."""
    rows = db_query("main", "SELECT key, value FROM app_settings")
    result = {}
    for r in rows:
        k, v = r["key"], r["value"]
        try:
            result[k] = int(v)
        except (ValueError, TypeError):
            result[k] = v
    return result


def db_save_settings(d: dict):
    """Upsert a dict of settings into app_settings."""
    for k, v in d.items():
        db_upsert("main", "app_settings", ["key", "value"], (k, str(v)), "key")


# ── User management ───────────────────────────────────────────────

def db_list_users() -> list:
    """Return all users as [{username, role, auth_type, domain, full_name, email, group_id, group_name, totp_enabled}]."""
    rows = db_query("main", """
        SELECT u.username, u.role, u.auth_type, u.domain,
               u.full_name, u.email, u.group_id, u.totp_enabled,
               g.name AS group_name
        FROM users u
        LEFT JOIN user_groups g ON g.id = u.group_id
        ORDER BY u.username
    """)
    return [{"username":     r["username"],
             "role":         r["role"],
             "auth_type":    r["auth_type"] or "local",
             "domain":       r["domain"]    or "",
             "full_name":    r["full_name"] or "",
             "email":        r["email"]     or "",
             "group_id":     r["group_id"],
             "group_name":   r["group_name"] or "",
             "totp_enabled": int(r.get("totp_enabled") or 0) if isinstance(r, dict)
                             else int(r["totp_enabled"] or 0)} for r in rows]


_UNSET = object()


def db_update_profile(username: str, full_name: str, email: str,
                      group_id=_UNSET, role: str = _UNSET,
                      theme_preference: str = _UNSET) -> bool:
    """
    Update user profile fields.
    group_id, role, and theme_preference are only updated when explicitly passed.
    Returns False if user not found.
    """
    sets = ["full_name=?", "email=?"]
    params = [full_name.strip(), email.strip()]
    if group_id is not _UNSET:
        sets.append("group_id=?")
        params.append(group_id)
    if role is not _UNSET:
        sets.append("role=?")
        params.append(role)
    if theme_preference is not _UNSET:
        sets.append("theme_preference=?")
        params.append(theme_preference)
    params.append(username)
    try:
        with db_cursor("main") as cur:
            ph_marker = "%s" if is_pg() else "?"
            query = f"UPDATE users SET {', '.join(sets).replace('?', ph_marker)} WHERE username={ph_marker}"
            cur.execute(query, params)
            return cur.rowcount > 0
    except Exception as e:
        log.error(f"DB update profile error: {e}")
        return False


def db_update_own_profile(username: str, full_name: str, email: str) -> bool:
    """Update only full_name and email for the user (self-service, no role/group change)."""
    return db_update_profile(username, full_name, email)


def db_update_theme(username: str, theme: str) -> bool:
    """Persist the user's preferred UI theme ('dark' or 'light')."""
    if theme not in ("dark", "light"):
        return False
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"UPDATE users SET theme_preference={ph} WHERE username={ph}",
                (theme, username))
            return cur.rowcount > 0
    except Exception as e:
        log.error(f"DB update theme error: {e}")
        return False


def db_add_user(username: str, password: str, role: str = "admin") -> bool:
    """Insert a new user. Returns False if username already exists."""
    from core.auth import _hash_pw
    # Note: catching IntegrityError requires backend-specific exception type,
    # so we use db_cursor() directly with a generic Exception fallback.
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"INSERT INTO users (username, pw_hash, role) VALUES ({ph},{ph},{ph})",
                (username, _hash_pw(password), role)
            )
        return True
    except Exception as e:
        # IntegrityError (duplicate username) or other DB error — log if not duplicate
        msg = str(e).lower()
        if "unique" not in msg and "duplicate" not in msg:
            log.error(f"DB add user error: {e}")
        return False


def db_delete_user(username: str) -> bool:
    """Delete a user and their dashboards. Returns False if not found."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(f"DELETE FROM dashboards WHERE username={ph}", (username,))
            cur.execute(f"DELETE FROM users WHERE username={ph}", (username,))
            return cur.rowcount > 0
    except Exception as e:
        log.error(f"DB delete user error: {e}")
        return False


def db_add_ldap_user(username: str, domain: str, role: str = 'viewer',
                     full_name: str = '', email: str = '',
                     group_id: int | None = None) -> bool:
    """Insert a domain/LDAP user (no local password). Returns False if username exists."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"INSERT INTO users (username, pw_hash, role, auth_type, domain, "
                f"full_name, email, group_id) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (username, '__ldap__', role, 'ldap', domain,
                 full_name, email, group_id)
            )
        return True
    except Exception as e:
        msg = str(e).lower()
        if "unique" not in msg and "duplicate" not in msg:
            log.error(f"DB add LDAP user error: {e}")
        return False


def db_add_radius_user(username: str, domain: str = '', role: str = 'viewer',
                       full_name: str = '', email: str = '',
                       group_id: int | None = None) -> bool:
    """Insert a RADIUS-authenticated user (no local password). Returns False if username exists."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"INSERT INTO users (username, pw_hash, role, auth_type, domain, "
                f"full_name, email, group_id) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (username, '__radius__', role, 'radius', domain,
                 full_name, email, group_id)
            )
        return True
    except Exception as e:
        msg = str(e).lower()
        if "unique" not in msg and "duplicate" not in msg:
            log.error(f"DB add RADIUS user error: {e}")
        return False


def db_add_sso_user(username: str, auth_type: str, external_id: str,
                    role: str = 'viewer', full_name: str = '', email: str = '',
                    group_id: int | None = None) -> bool:
    """Insert a SAML or OIDC-authenticated user (JIT provisioning, no local password).

    auth_type: 'saml' or 'oidc'
    external_id: federated subject — format 'saml|<entity>|<nameid>' or 'oidc|<issuer>|<sub>'.
    Returns False if username or external_id already exists.
    """
    if auth_type not in ('saml', 'oidc'):
        log.error(f"db_add_sso_user: invalid auth_type {auth_type!r}")
        return False
    sentinel = '__saml__' if auth_type == 'saml' else '__oidc__'
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"INSERT INTO users (username, pw_hash, role, auth_type, "
                f"full_name, email, group_id, external_id) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (username, sentinel, role, auth_type,
                 full_name, email, group_id, external_id)
            )
        return True
    except Exception as e:
        msg = str(e).lower()
        if "unique" not in msg and "duplicate" not in msg:
            log.error(f"DB add SSO user error: {e}")
        return False


def db_get_user_by_external_id(external_id: str) -> dict | None:
    """Look up a user by their federated external_id. Returns row dict or None."""
    if not external_id:
        return None
    rows = db_query("main",
                    "SELECT username, role, auth_type, full_name, email, group_id "
                    "FROM users WHERE external_id=?",
                    (external_id,))
    if not rows:
        return None
    r = rows[0]
    return {"username":   r["username"],
            "role":       r["role"],
            "auth_type":  r["auth_type"] or "local",
            "full_name":  r["full_name"] or "",
            "email":      r["email"]     or "",
            "group_id":   r["group_id"]}


def db_update_external_id(username: str, external_id: str) -> bool:
    """Set or clear external_id on an existing user. Used when admins pre-create an SSO shell."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"UPDATE users SET external_id={ph} WHERE username={ph}",
                (external_id or None, username))
            return cur.rowcount > 0
    except Exception as e:
        log.error(f"DB update external_id error: {e}")
        return False


def db_get_user_auth_type(username: str) -> str:
    """Return 'local', 'ldap', 'radius', 'saml', or 'oidc' for username, defaulting to 'local'."""
    rows = db_query("main", "SELECT auth_type FROM users WHERE username=?", (username,))
    if not rows:
        return 'local'
    return rows[0]["auth_type"] or 'local'


def db_set_password(username: str, password: str):
    """Update the password hash for an existing user."""
    from core.auth import _hash_pw
    db_execute("main",
               "UPDATE users SET pw_hash=? WHERE username=?",
               (_hash_pw(password), username))


# ── Dashboard management (multi-dashboard) ───────────────────────

MAX_DASHBOARDS = 10


def db_list_dashboards(username: str) -> list:
    """Return [{id, name, sort_order}] for the user.  No widget payloads."""
    rows = db_query("main",
                    "SELECT id, name, sort_order FROM dashboards "
                    "WHERE username=? ORDER BY sort_order, id",
                    (username,))
    return [{"id": r["id"], "name": r["name"], "sort_order": r["sort_order"]}
            for r in rows]


def db_get_dashboard(username: str, dashboard_id: int = None) -> dict | None:
    """Return {id, name, widgets} for a specific dashboard, or None."""
    row = db_query("main",
                   "SELECT id, name, widgets FROM dashboards "
                   "WHERE id=? AND username=?",
                   (dashboard_id, username))
    if not row:
        return None
    return {"id": row[0]["id"], "name": row[0]["name"],
            "widgets": row[0]["widgets"]}


def db_create_dashboard(username: str, name: str,
                        widgets_json: str = "[]") -> dict | None:
    """Create a new dashboard.  Returns {id, name} or None if at limit."""
    rows = db_query("main",
                    "SELECT COUNT(*) AS cnt FROM dashboards WHERE username=?",
                    (username,))
    if rows and int(rows[0]["cnt"]) >= MAX_DASHBOARDS:
        return None
    # Compute next sort_order
    so_rows = db_query("main",
                       "SELECT COALESCE(MAX(sort_order),0)+1 AS nxt "
                       "FROM dashboards WHERE username=?",
                       (username,))
    nxt = int(so_rows[0]["nxt"]) if so_rows else 0
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor("main") as cur:
            cur.execute(
                "INSERT INTO dashboards (username, name, sort_order, widgets) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (username, name, nxt, widgets_json))
            new_id = cur.fetchone()["id"]
        return {"id": new_id, "name": name}
    with db_cursor("main") as cur:
        cur.execute(
            "INSERT INTO dashboards (username, name, sort_order, widgets) "
            "VALUES (?, ?, ?, ?)",
            (username, name, nxt, widgets_json))
        return {"id": cur.lastrowid, "name": name}


def db_rename_dashboard(username: str, dashboard_id: int, name: str) -> bool:
    """Rename a dashboard.  Returns True on success."""
    return db_execute("main",
                      "UPDATE dashboards SET name=? WHERE id=? AND username=?",
                      (name, dashboard_id, username))


def db_delete_dashboard(username: str, dashboard_id: int) -> bool:
    """Delete a dashboard.  Returns True on success."""
    return db_execute("main",
                      "DELETE FROM dashboards WHERE id=? AND username=?",
                      (dashboard_id, username))


def db_save_dashboard(username: str, dashboard_id: int = None,
                      widgets_json: str = "[]"):
    """Update widget layout for a specific dashboard."""
    db_execute("main",
               "UPDATE dashboards SET widgets=? WHERE id=? AND username=?",
               (widgets_json, dashboard_id, username))


def db_reorder_dashboards(username: str, ordered_ids: list):
    """Set sort_order based on position in the ordered_ids list."""
    with db_cursor("main") as cur:
        ph = "%s" if is_pg() else "?"
        for i, did in enumerate(ordered_ids):
            cur.execute(
                f"UPDATE dashboards SET sort_order={ph} "
                f"WHERE id={ph} AND username={ph}",
                (i, did, username))


# ── TOTP (2FA) ───────────────────────────────────────────────────

def db_get_totp(username: str) -> dict:
    """Return {secret, enabled, recovery_json} for a user. Empty values if not set."""
    r = db_query_one("main",
            "SELECT totp_secret, totp_enabled, totp_recovery FROM users WHERE username=?",
            (username,))
    if not r:
        return {"secret": "", "enabled": 0, "recovery_json": ""}
    d = dict(r) if not isinstance(r, dict) else r
    return {
        "secret":        d.get("totp_secret") or "",
        "enabled":       int(d.get("totp_enabled") or 0),
        "recovery_json": d.get("totp_recovery") or "",
    }


def db_set_totp(username: str, secret: str, enabled: int, recovery_json: str = "") -> bool:
    """Upsert TOTP state for a user."""
    return db_execute("main",
                      "UPDATE users SET totp_secret=?, totp_enabled=?, totp_recovery=? "
                      "WHERE username=?",
                      (secret, int(bool(enabled)), recovery_json, username))


def db_clear_totp(username: str) -> bool:
    """Disable TOTP and clear the secret + recovery codes."""
    return db_set_totp(username, "", 0, "")


# ── Trusted devices (Remember 2FA) ──────────────────────────────

def db_get_remember_hours(username: str) -> int:
    """Return the user's max trusted-device duration in hours (default 9)."""
    r = db_query_one("main", "SELECT totp_remember_hours FROM users WHERE username=?",
                     (username,))
    if not r:
        return 9
    v = r["totp_remember_hours"] if isinstance(r, dict) else r[0]
    try:
        return max(0, int(v or 9))
    except (TypeError, ValueError):
        return 9


def db_set_remember_hours(username: str, hours: int) -> bool:
    """Persist the user's preferred trusted-device duration (0–720 h)."""
    hours = max(0, min(720, int(hours)))
    return db_execute("main",
                      "UPDATE users SET totp_remember_hours=? WHERE username=?",
                      (hours, username))


def db_add_trusted_device(username: str, token_hash: str, device_label: str,
                           hours: int, ip: str, ua: str):
    """Insert a trusted-device row and return its id (int) or None on error."""
    import time as _t
    now = _t.time()
    expires = now + hours * 3600
    try:
        if is_pg():
            from db.pg_pool import pg_cursor
            with pg_cursor("main") as cur:
                cur.execute(
                    "INSERT INTO trusted_devices "
                    "(username, token_hash, device_label, created_at, expires_at, "
                    "last_used_at, ip, user_agent) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (username, token_hash, device_label, now, expires, now, ip, ua)
                )
                row = cur.fetchone()
                return row["id"] if row else None
        with db_cursor("main") as cur:
            cur.execute(
                "INSERT INTO trusted_devices "
                "(username, token_hash, device_label, created_at, expires_at, "
                "last_used_at, ip, user_agent) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (username, token_hash, device_label, now, expires, now, ip, ua)
            )
            return cur.lastrowid
    except Exception as e:
        log.error(f"DB add trusted device error: {e}")
        return None


def db_lookup_trusted_device(username: str, token_hash: str):
    """Return the trusted-device row if valid (not expired), else None."""
    import time as _t
    r = db_query_one(
        "main",
        "SELECT id, username, device_label, created_at, expires_at, "
        "last_used_at, ip, user_agent "
        "FROM trusted_devices "
        "WHERE token_hash=? AND username=? AND expires_at>?",
        (token_hash, username, _t.time())
    )
    if not r:
        return None
    return dict(r) if not isinstance(r, dict) else r


def db_touch_trusted_device(device_id: int, ip: str) -> bool:
    """Update last_used_at and ip for a trusted device."""
    import time as _t
    return db_execute("main",
                      "UPDATE trusted_devices SET last_used_at=?, ip=? WHERE id=?",
                      (_t.time(), ip, device_id))


def db_list_trusted_devices(username: str) -> list:
    """Return [{id, device_label, created_at, expires_at, last_used_at, ip}] for the user."""
    rows = db_query("main",
                    "SELECT id, device_label, created_at, expires_at, last_used_at, ip "
                    "FROM trusted_devices WHERE username=? ORDER BY created_at DESC",
                    (username,))
    return [{"id":           r["id"] if isinstance(r, dict) else r[0],
             "device_label": (r["device_label"] if isinstance(r, dict) else r[1]) or "",
             "created_at":   r["created_at"]   if isinstance(r, dict) else r[2],
             "expires_at":   r["expires_at"]   if isinstance(r, dict) else r[3],
             "last_used_at": r["last_used_at"] if isinstance(r, dict) else r[4],
             "ip":           (r["ip"]          if isinstance(r, dict) else r[5]) or ""}
            for r in rows]


def db_revoke_trusted_device(username: str, device_id: int) -> bool:
    """Delete a single trusted-device row (only if it belongs to username)."""
    return db_execute("main",
                      "DELETE FROM trusted_devices WHERE id=? AND username=?",
                      (device_id, username))


def db_revoke_trusted_devices(username: str) -> int:
    """Delete ALL trusted-device rows for a user. Returns number deleted."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"DELETE FROM trusted_devices WHERE username={ph}", (username,)
            )
            return cur.rowcount
    except Exception as e:
        log.error(f"DB revoke trusted devices error: {e}")
        return 0


def db_sweep_expired_trusted_devices() -> int:
    """Delete expired trusted-device rows. Returns number deleted."""
    import time as _t
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"DELETE FROM trusted_devices WHERE expires_at<{ph}", (_t.time(),)
            )
            return cur.rowcount
    except Exception as e:
        log.error(f"DB sweep trusted devices error: {e}")
        return 0
