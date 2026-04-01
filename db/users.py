"""
db/users.py — User management and app settings persistence.
"""

import sqlite3

from core.config import DB_PATH
from core.logger import log


# ── App settings ─────────────────────────────────────────────────

def db_load_settings() -> dict:
    """Return all app_settings rows as a plain dict (values cast to int where numeric)."""
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute("SELECT key, value FROM app_settings").fetchall()
        result = {}
        for k, v in rows:
            try:
                result[k] = int(v)
            except (ValueError, TypeError):
                result[k] = v
        return result
    except Exception as e:
        log.error(f"DB load settings error: {e}")
        return {}
    finally:
        con.close()


def db_save_settings(d: dict):
    """Upsert a dict of settings into app_settings."""
    con = sqlite3.connect(DB_PATH)
    try:
        for k, v in d.items():
            con.execute("INSERT OR REPLACE INTO app_settings VALUES (?,?)", (k, str(v)))
        con.commit()
    except Exception as e:
        log.error(f"DB save settings error: {e}")
    finally:
        con.close()


# ── User management ───────────────────────────────────────────────

def db_list_users() -> list:
    """Return all users as [{username, role, auth_type, domain, full_name, email, group_id, group_name}]."""
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute("""
            SELECT u.username, u.role, u.auth_type, u.domain,
                   u.full_name, u.email, u.group_id, g.name AS group_name
            FROM users u
            LEFT JOIN user_groups g ON g.id = u.group_id
            ORDER BY u.username
        """).fetchall()
        return [{"username": r[0], "role": r[1],
                 "auth_type": r[2] or "local", "domain": r[3] or "",
                 "full_name": r[4] or "", "email": r[5] or "",
                 "group_id": r[6], "group_name": r[7] or ""} for r in rows]
    except Exception as e:
        log.error(f"DB list users error: {e}")
        return []
    finally:
        con.close()


_UNSET = object()


def db_update_profile(username: str, full_name: str, email: str,
                      group_id=_UNSET, role: str = _UNSET) -> bool:
    """
    Update user profile fields.
    group_id and role are only updated when explicitly passed (admin path).
    Returns False if user not found.
    """
    con = sqlite3.connect(DB_PATH)
    try:
        sets = ["full_name=?", "email=?"]
        params = [full_name.strip(), email.strip()]
        if group_id is not _UNSET:
            sets.append("group_id=?")
            params.append(group_id)
        if role is not _UNSET:
            sets.append("role=?")
            params.append(role)
        params.append(username)
        cur = con.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE username=?", params
        )
        con.commit()
        return cur.rowcount > 0
    except Exception as e:
        log.error(f"DB update profile error: {e}")
        return False
    finally:
        con.close()


def db_update_own_profile(username: str, full_name: str, email: str) -> bool:
    """Update only full_name and email for the user (self-service, no role/group change)."""
    return db_update_profile(username, full_name, email)


def db_add_user(username: str, password: str, role: str = "admin") -> bool:
    """Insert a new user. Returns False if username already exists."""
    from core.auth import _hash_pw
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT INTO users (username, pw_hash, role) VALUES (?,?,?)",
            (username, _hash_pw(password), role)
        )
        con.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        log.error(f"DB add user error: {e}")
        return False
    finally:
        con.close()


def db_delete_user(username: str) -> bool:
    """Delete a user. Returns False if not found."""
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.execute("DELETE FROM users WHERE username=?", (username,))
        con.commit()
        return cur.rowcount > 0
    except Exception as e:
        log.error(f"DB delete user error: {e}")
        return False
    finally:
        con.close()


def db_add_ldap_user(username: str, domain: str, role: str = 'viewer') -> bool:
    """Insert a domain/LDAP user (no local password). Returns False if username exists."""
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT INTO users (username, pw_hash, role, auth_type, domain) VALUES (?,?,?,?,?)",
            (username, '__ldap__', role, 'ldap', domain)
        )
        con.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        log.error(f"DB add LDAP user error: {e}")
        return False
    finally:
        con.close()


def db_get_user_auth_type(username: str) -> str:
    """Return 'local' or 'ldap' for username, defaulting to 'local' if not found."""
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT auth_type FROM users WHERE username=?", (username,)
        ).fetchone()
        return (row[0] or 'local') if row else 'local'
    except Exception:
        return 'local'
    finally:
        con.close()


def db_set_password(username: str, password: str):
    """Update the password hash for an existing user."""
    from core.auth import _hash_pw
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("UPDATE users SET pw_hash=? WHERE username=?",
                    (_hash_pw(password), username))
        con.commit()
    except Exception as e:
        log.error(f"DB set password error: {e}")
    finally:
        con.close()


# ── Dashboard widget layout ───────────────────────────────────────

def db_get_dashboard(username: str) -> str:
    """Return the stored widgets JSON string for this user (default '[]')."""
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT widgets FROM dashboard_widgets WHERE username=?", (username,)
        ).fetchone()
        return row[0] if row else "[]"
    except Exception as e:
        log.error(f"DB get dashboard error: {e}")
        return "[]"
    finally:
        con.close()


def db_save_dashboard(username: str, widgets_json: str):
    """Upsert the widgets JSON string for this user."""
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT OR REPLACE INTO dashboard_widgets (username, widgets) VALUES (?,?)",
            (username, widgets_json)
        )
        con.commit()
    except Exception as e:
        log.error(f"DB save dashboard error: {e}")
    finally:
        con.close()
