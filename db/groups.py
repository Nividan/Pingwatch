"""
db/groups.py — User group CRUD and email resolution.
"""

from core.logger import log
from db.backend  import is_pg
from db.helpers  import db_query, db_execute, db_cursor


def db_list_groups() -> list:
    """Return [{id, name, description, member_count, ldap_dn, radius_attribute,
    radius_value, saml_group_value, oidc_group_value, default_role}] ordered by name."""
    rows = db_query("main", """
        SELECT g.id, g.name, g.description, g.ldap_dn,
               g.radius_attribute, g.radius_value,
               COALESCE(g.saml_group_value,'') AS saml_group_value,
               COALESCE(g.oidc_group_value,'') AS oidc_group_value,
               g.default_role,
               COUNT(u.username) AS member_count
        FROM user_groups g
        LEFT JOIN users u ON u.group_id = g.id
        GROUP BY g.id
        ORDER BY g.name
    """)
    return [{"id":               r["id"],
             "name":             r["name"],
             "description":      r["description"] or "",
             "ldap_dn":          r["ldap_dn"] or "",
             "radius_attribute": r["radius_attribute"] or "",
             "radius_value":     r["radius_value"] or "",
             "saml_group_value": r["saml_group_value"] or "",
             "oidc_group_value": r["oidc_group_value"] or "",
             "default_role":     r["default_role"] or "viewer",
             "member_count":     r["member_count"]} for r in rows]


def db_create_group(name: str, description: str = "",
                    ldap_dn: str = "", default_role: str = "viewer",
                    radius_attribute: str = "", radius_value: str = "",
                    saml_group_value: str = "", oidc_group_value: str = "") -> int:
    """Insert group. Returns new id, -2 on duplicate name, -1 on error."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            params = (name.strip(), description.strip(), ldap_dn.strip(),
                      radius_attribute.strip(), radius_value.strip(),
                      saml_group_value.strip(), oidc_group_value.strip(),
                      default_role)
            if is_pg():
                cur.execute(
                    f"INSERT INTO user_groups (name, description, ldap_dn, "
                    f"radius_attribute, radius_value, "
                    f"saml_group_value, oidc_group_value, default_role) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph}) RETURNING id",
                    params
                )
                return cur.fetchone()["id"]
            else:
                cur.execute(
                    f"INSERT INTO user_groups (name, description, ldap_dn, "
                    f"radius_attribute, radius_value, "
                    f"saml_group_value, oidc_group_value, default_role) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                    params
                )
                return cur.lastrowid
    except Exception as e:
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            return -2
        log.error(f"db_create_group error: {e}")
        return -1


_UG_UNSET = object()

def db_update_group(group_id: int, name: str, description: str = "",
                    default_role=_UG_UNSET,
                    radius_attribute=_UG_UNSET,
                    radius_value=_UG_UNSET,
                    saml_group_value=_UG_UNSET,
                    oidc_group_value=_UG_UNSET) -> bool:
    """Update group name/description and optionally default_role / RADIUS / SAML / OIDC mapping.
    Returns False if not found or on duplicate name."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            sets = [f"name={ph}", f"description={ph}"]
            params = [name.strip(), description.strip()]
            if default_role is not _UG_UNSET:
                sets.append(f"default_role={ph}")
                params.append(default_role)
            if radius_attribute is not _UG_UNSET:
                sets.append(f"radius_attribute={ph}")
                params.append(str(radius_attribute).strip())
            if radius_value is not _UG_UNSET:
                sets.append(f"radius_value={ph}")
                params.append(str(radius_value).strip())
            if saml_group_value is not _UG_UNSET:
                sets.append(f"saml_group_value={ph}")
                params.append(str(saml_group_value).strip())
            if oidc_group_value is not _UG_UNSET:
                sets.append(f"oidc_group_value={ph}")
                params.append(str(oidc_group_value).strip())
            params.append(group_id)
            cur.execute(
                f"UPDATE user_groups SET {', '.join(sets)} WHERE id={ph}",
                params
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


def db_get_ldap_mapped_groups() -> list:
    """Return [{id, name, ldap_dn, default_role}] for groups with a non-empty ldap_dn."""
    rows = db_query("main",
                    "SELECT id, name, ldap_dn, default_role FROM user_groups "
                    "WHERE ldap_dn IS NOT NULL AND ldap_dn != ''")
    return [{"id":           r["id"],
             "name":         r["name"],
             "ldap_dn":      r["ldap_dn"],
             "default_role": r["default_role"] or "viewer"} for r in rows]


def db_find_group_by_radius(attribute: str, value: str) -> dict | None:
    """Find a group whose (radius_attribute, radius_value) matches. First match wins.
    Returns {id, name, default_role} or None."""
    if not attribute or not value:
        return None
    rows = db_query(
        "main",
        "SELECT id, name, default_role FROM user_groups "
        "WHERE radius_attribute=? AND radius_value=? LIMIT 1",
        (attribute, value)
    )
    if not rows:
        return None
    r = rows[0]
    return {"id": r["id"], "name": r["name"],
            "default_role": r["default_role"] or "viewer"}


def db_get_radius_mapped_groups() -> list:
    """Return [{id, name, radius_attribute, radius_value, default_role}] for RADIUS-mapped groups."""
    rows = db_query("main",
                    "SELECT id, name, radius_attribute, radius_value, default_role "
                    "FROM user_groups "
                    "WHERE radius_attribute IS NOT NULL AND radius_attribute != ''")
    return [{"id":               r["id"],
             "name":             r["name"],
             "radius_attribute": r["radius_attribute"],
             "radius_value":     r["radius_value"] or "",
             "default_role":     r["default_role"] or "viewer"} for r in rows]


def db_get_saml_mapped_groups() -> list:
    """Return [{id, name, saml_group_value, default_role}] for SAML-mapped groups."""
    rows = db_query("main",
                    "SELECT id, name, saml_group_value, default_role FROM user_groups "
                    "WHERE saml_group_value IS NOT NULL AND saml_group_value != ''")
    return [{"id":               r["id"],
             "name":             r["name"],
             "saml_group_value": r["saml_group_value"],
             "default_role":     r["default_role"] or "viewer"} for r in rows]


def db_get_oidc_mapped_groups() -> list:
    """Return [{id, name, oidc_group_value, default_role}] for OIDC-mapped groups."""
    rows = db_query("main",
                    "SELECT id, name, oidc_group_value, default_role FROM user_groups "
                    "WHERE oidc_group_value IS NOT NULL AND oidc_group_value != ''")
    return [{"id":               r["id"],
             "name":             r["name"],
             "oidc_group_value": r["oidc_group_value"],
             "default_role":     r["default_role"] or "viewer"} for r in rows]
