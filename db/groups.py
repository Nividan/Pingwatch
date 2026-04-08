"""
db/groups.py — User group CRUD and email resolution.
"""

from core.logger import log
from db.backend  import is_pg
from db.helpers  import db_query, db_execute, db_cursor


def db_list_groups() -> list:
    """Return [{id, name, description, member_count}] ordered by name."""
    rows = db_query("main", """
        SELECT g.id, g.name, g.description,
               COUNT(u.username) AS member_count
        FROM user_groups g
        LEFT JOIN users u ON u.group_id = g.id
        GROUP BY g.id
        ORDER BY g.name
    """)
    return [{"id":           r["id"],
             "name":         r["name"],
             "description":  r["description"] or "",
             "member_count": r["member_count"]} for r in rows]


def db_create_group(name: str, description: str = "") -> int:
    """Insert group. Returns new id, -2 on duplicate name, -1 on error."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            if is_pg():
                cur.execute(
                    f"INSERT INTO user_groups (name, description) VALUES ({ph},{ph}) RETURNING id",
                    (name.strip(), description.strip())
                )
                return cur.fetchone()["id"]
            else:
                cur.execute(
                    f"INSERT INTO user_groups (name, description) VALUES ({ph},{ph})",
                    (name.strip(), description.strip())
                )
                return cur.lastrowid
    except Exception as e:
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            return -2
        log.error(f"db_create_group error: {e}")
        return -1


def db_update_group(group_id: int, name: str, description: str = "") -> bool:
    """Update group name/description. Returns False if not found or on duplicate name."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"UPDATE user_groups SET name={ph}, description={ph} WHERE id={ph}",
                (name.strip(), description.strip(), group_id)
            )
            return cur.rowcount > 0
    except Exception as e:
        msg = str(e).lower()
        if "unique" not in msg and "duplicate" not in msg:
            log.error(f"db_update_group error: {e}")
        return False


def db_delete_group(group_id: int) -> bool:
    """Delete group; sets group_id=NULL for all members. Returns False if not found."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(f"SELECT id FROM user_groups WHERE id={ph}", (group_id,))
            if not cur.fetchone():
                return False
            cur.execute(f"UPDATE users SET group_id=NULL WHERE group_id={ph}", (group_id,))
            cur.execute(f"DELETE FROM user_groups WHERE id={ph}", (group_id,))
        return True
    except Exception as e:
        log.error(f"db_delete_group error: {e}")
        return False


def db_update_group_members(group_id: int, usernames: list) -> bool:
    """
    Replace group membership:
    - Set group_id=group_id WHERE username IN usernames
    - Set group_id=NULL WHERE group_id=group_id AND username NOT IN usernames
    Returns True on success.
    """
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(f"SELECT id FROM user_groups WHERE id={ph}", (group_id,))
            if not cur.fetchone():
                return False
            cur.execute(f"UPDATE users SET group_id=NULL WHERE group_id={ph}", (group_id,))
            if usernames:
                placeholders = ",".join([ph] * len(usernames))
                cur.execute(
                    f"UPDATE users SET group_id={ph} WHERE username IN ({placeholders})",
                    [group_id] + list(usernames)
                )
        return True
    except Exception as e:
        log.error(f"db_update_group_members error: {e}")
        return False


def db_resolve_group_emails(group_id: int) -> list:
    """Return list of non-empty email addresses for all users in this group."""
    rows = db_query("main",
                    "SELECT email FROM users WHERE group_id=? AND email IS NOT NULL AND email != ''",
                    (group_id,))
    return [r["email"] for r in rows]
