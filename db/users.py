"""
db/users.py — User management and app settings persistence.
"""

import sqlite3

from core.config import DB_PATH
from core.logger import log


# ── App settings ─────────────────────────────────────────────────

def db_load_settings() -> dict:
    """Return all app_settings rows as a plain dict (values cast to int where numeric)."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT key, value FROM app_settings").fetchall()
        con.close()
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


def db_save_settings(d: dict):
    """Upsert a dict of settings into app_settings."""
    try:
        con = sqlite3.connect(DB_PATH)
        for k, v in d.items():
            con.execute("INSERT OR REPLACE INTO app_settings VALUES (?,?)", (k, str(v)))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"DB save settings error: {e}")


# ── User management ───────────────────────────────────────────────

def db_list_users() -> list:
    """Return all users as [{username, role, auth_type, domain}]."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT username, role, auth_type, domain FROM users ORDER BY username"
        ).fetchall()
        con.close()
        return [{"username": r[0], "role": r[1],
                 "auth_type": r[2] or "local", "domain": r[3] or ""} for r in rows]
    except Exception as e:
        log.error(f"DB list users error: {e}")
        return []


def db_add_user(username: str, password: str, role: str = "admin") -> bool:
    """Insert a new user. Returns False if username already exists."""
    from core.auth import _hash_pw
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT INTO users (username, pw_hash, role) VALUES (?,?,?)",
            (username, _hash_pw(password), role)
        )
        con.commit()
        con.close()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        log.error(f"DB add user error: {e}")
        return False


def db_delete_user(username: str) -> bool:
    """Delete a user. Returns False if not found."""
    try:
        con = sqlite3.connect(DB_PATH)
        cur = con.execute("DELETE FROM users WHERE username=?", (username,))
        con.commit()
        con.close()
        return cur.rowcount > 0
    except Exception as e:
        log.error(f"DB delete user error: {e}")
        return False


def db_add_ldap_user(username: str, domain: str, role: str = 'viewer') -> bool:
    """Insert a domain/LDAP user (no local password). Returns False if username exists."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT INTO users (username, pw_hash, role, auth_type, domain) VALUES (?,?,?,?,?)",
            (username, '__ldap__', role, 'ldap', domain)
        )
        con.commit()
        con.close()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        log.error(f"DB add LDAP user error: {e}")
        return False


def db_get_user_auth_type(username: str) -> str:
    """Return 'local' or 'ldap' for username, defaulting to 'local' if not found."""
    try:
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            "SELECT auth_type FROM users WHERE username=?", (username,)
        ).fetchone()
        con.close()
        return (row[0] or 'local') if row else 'local'
    except Exception:
        return 'local'


def db_set_password(username: str, password: str):
    """Update the password hash for an existing user."""
    from core.auth import _hash_pw
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("UPDATE users SET pw_hash=? WHERE username=?",
                    (_hash_pw(password), username))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"DB set password error: {e}")


# ── Dashboard widget layout ───────────────────────────────────────

def db_get_dashboard(username: str) -> str:
    """Return the stored widgets JSON string for this user (default '[]')."""
    try:
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            "SELECT widgets FROM dashboard_widgets WHERE username=?", (username,)
        ).fetchone()
        con.close()
        return row[0] if row else "[]"
    except Exception as e:
        log.error(f"DB get dashboard error: {e}")
        return "[]"


def db_save_dashboard(username: str, widgets_json: str):
    """Upsert the widgets JSON string for this user."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT OR REPLACE INTO dashboard_widgets (username, widgets) VALUES (?,?)",
            (username, widgets_json)
        )
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"DB save dashboard error: {e}")
