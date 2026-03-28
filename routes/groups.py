"""
routes/groups.py — User group management API endpoints.

GET    /api/groups              viewer  — list all groups with member_count
POST   /api/group               admin   — create group {name, description}
PATCH  /api/group/{id}          admin   — update group {name?, description?}
DELETE /api/group/{id}          admin   — delete group
PUT    /api/group/{id}/members  admin   — replace member list {usernames: [...]}
"""

from core.config import (
    _RE_GROUPS, _RE_GROUP, _RE_GROUP_ITEM, _RE_GROUP_MEMBERS,
)
from db import db_log_audit
from db.groups import (
    db_list_groups, db_create_group, db_update_group,
    db_delete_group, db_update_group_members,
)


def _validate_name(name) -> str | None:
    name = str(name or "").strip()
    if not name:
        return "name is required"
    if len(name) > 100:
        return "name too long (max 100)"
    return None


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # GET /api/groups
    if _RE_GROUPS.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, {"groups": db_list_groups()})
        return True

    # POST /api/group  (create)
    if _RE_GROUP.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        err = _validate_name(body.get("name"))
        if err:
            h._json(400, {"error": err}); return True
        desc = str(body.get("description", "")).strip()
        if len(desc) > 500:
            h._json(400, {"error": "description too long (max 500)"}); return True
        gid = db_create_group(body["name"], desc)
        if gid == -2:
            h._json(409, {"error": "a group with that name already exists"}); return True
        if gid < 0:
            h._json(500, {"error": "failed to create group"}); return True
        db_log_audit(user, h.client_address[0], 'group_create', body["name"])
        h._json(200, {"id": gid, "groups": db_list_groups()})
        return True

    m = _RE_GROUP_ITEM.match(path)

    # PATCH /api/group/{id}  (update)
    if m and method == "PATCH":
        user, _ = h._require("admin")
        if not user: return True
        gid = int(m.group(1))
        err = _validate_name(body.get("name"))
        if err:
            h._json(400, {"error": err}); return True
        desc = str(body.get("description", "")).strip()
        if len(desc) > 500:
            h._json(400, {"error": "description too long (max 500)"}); return True
        ok = db_update_group(gid, body["name"], desc)
        if not ok:
            h._json(404, {"error": "group not found"}); return True
        db_log_audit(user, h.client_address[0], 'group_update', body["name"])
        h._json(200, {"groups": db_list_groups()})
        return True

    # DELETE /api/group/{id}
    if m and method == "DELETE":
        user, _ = h._require("admin")
        if not user: return True
        gid = int(m.group(1))
        ok = db_delete_group(gid)
        if not ok:
            h._json(404, {"error": "group not found"}); return True
        db_log_audit(user, h.client_address[0], 'group_delete', str(gid))
        h._json(200, {"groups": db_list_groups()})
        return True

    # PUT /api/group/{id}/members
    m2 = _RE_GROUP_MEMBERS.match(path)
    if m2 and method == "PUT":
        user, _ = h._require("admin")
        if not user: return True
        gid = int(m2.group(1))
        usernames = body.get("usernames", [])
        if not isinstance(usernames, list):
            h._json(400, {"error": "usernames must be a list"}); return True
        ok = db_update_group_members(gid, usernames)
        if not ok:
            h._json(404, {"error": "group not found"}); return True
        db_log_audit(user, h.client_address[0], 'group_members_update', str(gid))
        h._json(200, {"ok": True})
        return True

    return False
