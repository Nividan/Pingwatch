"""
db/groups.py — User group CRUD and email resolution.
"""

import sqlite3

from core.config import DB_PATH
from core.logger import log


def db_list_groups() -> list:
    """Return [{id, name, description, member_count}] ordered by name."""
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute("""
            SELECT g.id, g.name, g.description,
                   COUNT(u.username) AS member_count
            FROM user_groups g
            LEFT JOIN users u ON u.group_id = g.id
            GROUP BY g.id
            ORDER BY g.name
        """).fetchall()
        return [{"id": r[0], "name": r[1], "description": r[2] or "",
                 "member_count": r[3]} for r in rows]
    except Exception as e:
        log.error(f"db_list_groups error: {e}")
        return []
    finally:
        con.close()


def db_create_group(name: str, description: str = "") -> int:
    """Insert group. Returns new id, -2 on duplicate name, -1 on error."""
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.execute(
            "INSERT INTO user_groups (name, description) VALUES (?,?)",
            (name.strip(), description.strip())
        )
        con.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return -2  # duplicate name
    except Exception as e:
        log.error(f"db_create_group error: {e}")
        return -1
    finally:
        con.close()


def db_update_group(group_id: int, name: str, description: str = "") -> bool:
    """Update group name/description. Returns False if not found."""
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.execute(
            "UPDATE user_groups SET name=?, description=? WHERE id=?",
            (name.strip(), description.strip(), group_id)
        )
        con.commit()
        return cur.rowcount > 0
    except sqlite3.IntegrityError:
        return False  # duplicate name
    except Exception as e:
        log.error(f"db_update_group error: {e}")
        return False
    finally:
        con.close()


def db_delete_group(group_id: int) -> bool:
    """Delete group; sets group_id=NULL for all members. Returns False if not found."""
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT id FROM user_groups WHERE id=?", (group_id,)
        ).fetchone()
        if not row:
            return False
        con.execute("UPDATE users SET group_id=NULL WHERE group_id=?", (group_id,))
        con.execute("DELETE FROM user_groups WHERE id=?", (group_id,))
        con.commit()
        return True
    except Exception as e:
        log.error(f"db_delete_group error: {e}")
        return False
    finally:
        con.close()


def db_update_group_members(group_id: int, usernames: list) -> bool:
    """
    Replace group membership:
    - Set group_id=group_id WHERE username IN usernames
    - Set group_id=NULL WHERE group_id=group_id AND username NOT IN usernames
    Returns True on success.
    """
    con = sqlite3.connect(DB_PATH)
    try:
        if not con.execute(
            "SELECT id FROM user_groups WHERE id=?", (group_id,)
        ).fetchone():
            return False
        # Clear current members of this group
        con.execute("UPDATE users SET group_id=NULL WHERE group_id=?", (group_id,))
        # Assign selected members
        if usernames:
            placeholders = ",".join("?" * len(usernames))
            con.execute(
                f"UPDATE users SET group_id=? WHERE username IN ({placeholders})",
                [group_id] + list(usernames)
            )
        con.commit()
        return True
    except Exception as e:
        log.error(f"db_update_group_members error: {e}")
        return False
    finally:
        con.close()


def db_resolve_group_emails(group_id: int) -> list:
    """Return list of non-empty email addresses for all users in this group."""
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT email FROM users WHERE group_id=? AND email IS NOT NULL AND email != ''",
            (group_id,)
        ).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        log.error(f"db_resolve_group_emails error: {e}")
        return []
    finally:
        con.close()
