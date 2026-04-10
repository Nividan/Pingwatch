"""
db/users.py — User management and app settings persistence.

Refactored to use db.helpers (db_query, db_execute, db_upsert, db_cursor)
which abstracts the SQLite vs PostgreSQL boilerplate. Functions that need
backend-specific behavior (e.g., catching IntegrityError) use db_cursor()
directly with a single try/except branch.
"""

from core.logger import log
from db.backend  import is_pg
from db.helpers  import db_query, db_execute, db_upsert, db_cursor


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
    """Return all users as [{username, role, auth_type, domain, full_name, email, group_id, group_name}]."""
    rows = db_query("main", """
        SELECT u.username, u.role, u.auth_type, u.domain,
               u.full_name, u.email, u.group_id, g.name AS group_name
        FROM users u
        LEFT JOIN user_groups g ON g.id = u.group_id
        ORDER BY u.username
    """)
    return [{"username":  r["username"],
             "role":       r["role"],
             "auth_type":  r["auth_type"] or "local",
             "domain":     r["domain"]    or "",
             "full_name":  r["full_name"] or "",
             "email":      r["email"]     or "",
             "group_id":   r["group_id"],
             "group_name": r["group_name"] or ""} for r in rows]


_UNSET = object()


def db_update_profile(username: str, full_name: str, email: str,
                      group_id=_UNSET, role: str = _UNSET) -> bool:
    """
    Update user profile fields.
    group_id and role are only updated when explicitly passed (admin path).
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
    """Delete a user. Returns False if not found."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
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


def db_get_user_auth_type(username: str) -> str:
    """Return 'local' or 'ldap' for username, defaulting to 'local' if not found."""
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


# ── Dashboard widget layout ───────────────────────────────────────

def db_get_dashboard(username: str) -> str:
    """Return the stored widgets JSON string for this user (default '[]')."""
    rows = db_query("main",
                    "SELECT widgets FROM dashboard_widgets WHERE username=?",
                    (username,))
    return rows[0]["widgets"] if rows else "[]"


def db_save_dashboard(username: str, widgets_json: str):
    """Upsert the widgets JSON string for this user."""
    db_upsert("main", "dashboard_widgets",
              ["username", "widgets"],
              (username, widgets_json),
              "username")
