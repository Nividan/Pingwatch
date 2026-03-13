"""
db/users.py — User management and app settings persistence.
"""

import sqlite3

from config import DB_PATH
from logger import log


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
    """Return all users as [{username, role}]."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT username, role FROM users ORDER BY username").fetchall()
        con.close()
        return [{"username": r[0], "role": r[1]} for r in rows]
    except Exception as e:
        log.error(f"DB list users error: {e}")
        return []


def db_add_user(username: str, password: str, role: str = "admin") -> bool:
    """Insert a new user. Returns False if username already exists."""
    from auth import _hash_pw
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("INSERT INTO users VALUES (?,?,?)", (username, _hash_pw(password), role))
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


def db_set_password(username: str, password: str):
    """Update the password hash for an existing user."""
    from auth import _hash_pw
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("UPDATE users SET pw_hash=? WHERE username=?",
                    (_hash_pw(password), username))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"DB set password error: {e}")
