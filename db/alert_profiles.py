"""
db/alert_profiles.py — CRUD helpers for the PRTG-style alert profile system.

Tables:
    alert_profiles          one profile per scope (global / group / device / sensor)
    alert_action_templates  reusable named action ("Email admin", "Slack #ops")
    alert_profile_stages    ordered escalation stages within a profile
    alert_profile_state     per-stage fire history (survives restart)

Helpers wrap the dual-backend pattern via db.helpers (auto-rewrites '?' → '%s'
for PostgreSQL). Writes are normally invoked from route handlers; the engine
also calls db_record_stage_fire / db_clear_stage_state_for_sensor on the
probe-loop hot path.
"""

import json
import time

from core.logger import log
from db.backend  import is_pg
from db.helpers  import db_query, db_query_one, db_execute, db_cursor


# ── Row → dict converters ────────────────────────────────────────

def _profile_row(row) -> dict:
    if not row:
        return {}
    r = dict(row) if not isinstance(row, dict) else row
    return {
        "id":          r["id"],
        "name":        r["name"],
        "scope_type":  r["scope_type"],
        "scope_value": r.get("scope_value") or "",
        "enabled":     bool(r["enabled"]),
        "created_at":  r.get("created_at") or 0,
        "updated_at":  r.get("updated_at") or 0,
        "stages":      [],
    }


def _stage_row(row) -> dict:
    r = dict(row) if not isinstance(row, dict) else row
    return {
        "id":            r["id"],
        "profile_id":    r["profile_id"],
        "trigger_state": r["trigger_state"],
        "delay_s":       int(r["delay_s"] or 0),
        "repeat_min":    int(r["repeat_min"] or 0),
        "action_id":     r["action_id"],
        "sort_order":    int(r["sort_order"] or 0),
    }


def _template_row(row) -> dict:
    r = dict(row) if not isinstance(row, dict) else row
    cfg = {}
    try:
        cfg = json.loads(r.get("config") or "{}")
    except Exception:
        cfg = {}
    return {
        "id":         r["id"],
        "name":       r["name"],
        "atype":      r["atype"],
        "config":     cfg,
        "created_at": r.get("created_at") or 0,
    }


# ── Profile reads ────────────────────────────────────────────────

def db_list_profiles() -> list:
    """Return every profile with its stages, in scope-then-name order."""
    rows = db_query(
        "main",
        "SELECT * FROM alert_profiles ORDER BY scope_type, scope_value, name"
    )
    profiles = [_profile_row(r) for r in rows]
    if not profiles:
        return profiles
    ids = [p["id"] for p in profiles]
    try:
        with db_cursor("main") as cur:
            if is_pg():
                cur.execute(
                    "SELECT * FROM alert_profile_stages WHERE profile_id = ANY(%s) "
                    "ORDER BY profile_id, sort_order, id",
                    (ids,)
                )
            else:
                ph = ",".join("?" * len(ids))
                cur.execute(
                    f"SELECT * FROM alert_profile_stages WHERE profile_id IN ({ph}) "
                    f"ORDER BY profile_id, sort_order, id",
                    ids
                )
            by_pid = {}
            for r in cur.fetchall():
                by_pid.setdefault(r["profile_id"], []).append(_stage_row(r))
        for p in profiles:
            p["stages"] = by_pid.get(p["id"], [])
    except Exception as e:
        log.error(f"db_list_profiles stage fetch failed: {e}")
    return profiles


def db_get_profile(profile_id: int) -> dict | None:
    row = db_query_one("main", "SELECT * FROM alert_profiles WHERE id=?", (profile_id,))
    if not row:
        return None
    p = _profile_row(row)
    stages = db_query(
        "main",
        "SELECT * FROM alert_profile_stages WHERE profile_id=? "
        "ORDER BY sort_order, id",
        (profile_id,)
    )
    p["stages"] = [_stage_row(s) for s in stages]
    return p


def db_get_profile_for_scope(scope_type: str, scope_value: str = "") -> dict | None:
    """Look up a profile by its scope key. Returns None if no match."""
    if scope_type == "global":
        row = db_query_one(
            "main",
            "SELECT * FROM alert_profiles WHERE scope_type='global' LIMIT 1"
        )
    else:
        row = db_query_one(
            "main",
            "SELECT * FROM alert_profiles WHERE scope_type=? AND scope_value=?",
            (scope_type, scope_value or "")
        )
    if not row:
        return None
    return db_get_profile(row["id"])


# ── Profile writes ───────────────────────────────────────────────

def db_save_profile(data: dict, profile_id: int | None = None) -> int:
    """Insert or update a profile (replacing its stages atomically).

    Returns the profile id, or -1 on error.
    """
    now = time.time()
    name        = (data.get("name") or "").strip() or "Unnamed"
    scope_type  = data.get("scope_type") or "global"
    scope_value = (data.get("scope_value") or "") if scope_type != "global" else ""
    enabled     = 1 if data.get("enabled", True) else 0
    stages      = data.get("stages") or []

    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            if profile_id:
                cur.execute(
                    f"UPDATE alert_profiles SET name={ph}, scope_type={ph}, "
                    f"scope_value={ph}, enabled={ph}, updated_at={ph} WHERE id={ph}",
                    (name, scope_type, scope_value, enabled, now, profile_id)
                )
                cur.execute(
                    f"DELETE FROM alert_profile_stages WHERE profile_id={ph}",
                    (profile_id,)
                )
                pid = profile_id
            else:
                if is_pg():
                    cur.execute(
                        "INSERT INTO alert_profiles (name, scope_type, scope_value, "
                        "enabled, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s) "
                        "RETURNING id",
                        (name, scope_type, scope_value, enabled, now, now)
                    )
                    pid = cur.fetchone()["id"]
                else:
                    cur.execute(
                        "INSERT INTO alert_profiles (name, scope_type, scope_value, "
                        "enabled, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                        (name, scope_type, scope_value, enabled, now, now)
                    )
                    pid = cur.lastrowid
            _write_stages(cur, pid, stages, is_pg())
        return pid
    except Exception as e:
        log.error(f"db_save_profile error: {e}")
        return -1


def db_delete_profile(profile_id: int) -> bool:
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(f"DELETE FROM alert_profile_stages WHERE profile_id={ph}",
                        (profile_id,))
            cur.execute(f"DELETE FROM alert_profiles WHERE id={ph}", (profile_id,))
        return True
    except Exception as e:
        log.error(f"db_delete_profile error: {e}")
        return False


def db_set_profile_enabled(profile_id: int, enabled: bool) -> bool:
    return db_execute(
        "main",
        "UPDATE alert_profiles SET enabled=?, updated_at=? WHERE id=?",
        (1 if enabled else 0, time.time(), profile_id)
    )


def _write_stages(cur, profile_id: int, stages: list, pg: bool):
    """Bulk-insert stages for a profile inside an active cursor."""
    ph = "%s" if pg else "?"
    for i, s in enumerate(stages):
        trig = s.get("trigger_state") or "down"
        if trig not in ("down", "warning", "down_recovered", "warning_recovered"):
            continue
        action_id = s.get("action_id")
        if not action_id:
            # Stage with no action is meaningless — skip
            continue
        cur.execute(
            f"INSERT INTO alert_profile_stages (profile_id, trigger_state, delay_s, "
            f"repeat_min, action_id, sort_order) VALUES ({ph},{ph},{ph},{ph},{ph},{ph})",
            (profile_id, trig, int(s.get("delay_s") or 0),
             int(s.get("repeat_min") or 0), int(action_id), i)
        )


# ── Action template CRUD ─────────────────────────────────────────

def db_list_action_templates() -> list:
    rows = db_query("main", "SELECT * FROM alert_action_templates ORDER BY name")
    return [_template_row(r) for r in rows]


def db_get_action_template(tpl_id: int) -> dict | None:
    row = db_query_one("main", "SELECT * FROM alert_action_templates WHERE id=?", (tpl_id,))
    return _template_row(row) if row else None


def db_save_action_template(data: dict, tpl_id: int | None = None) -> int:
    """Insert or update an action template. Returns id or -1 on error."""
    name  = (data.get("name") or "").strip() or "Unnamed"
    atype = data.get("atype") or "email"
    if atype not in ("email", "webhook", "syslog", "browser"):
        log.error(f"db_save_action_template: invalid atype={atype}")
        return -1
    cfg = data.get("config") or {}
    if not isinstance(cfg, str):
        cfg = json.dumps(cfg)
    now = time.time()
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            if tpl_id:
                cur.execute(
                    f"UPDATE alert_action_templates SET name={ph}, atype={ph}, "
                    f"config={ph} WHERE id={ph}",
                    (name, atype, cfg, tpl_id)
                )
                return tpl_id
            if is_pg():
                cur.execute(
                    "INSERT INTO alert_action_templates (name, atype, config, "
                    "created_at) VALUES (%s,%s,%s,%s) RETURNING id",
                    (name, atype, cfg, now)
                )
                return cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO alert_action_templates (name, atype, config, created_at) "
                "VALUES (?,?,?,?)",
                (name, atype, cfg, now)
            )
            return cur.lastrowid
    except Exception as e:
        log.error(f"db_save_action_template error: {e}")
        return -1


def db_delete_action_template(tpl_id: int) -> bool:
    """Delete a template. Fails if any stage still references it."""
    used = db_query_one(
        "main",
        "SELECT COUNT(*) AS n FROM alert_profile_stages WHERE action_id=?",
        (tpl_id,)
    )
    n = (used.get("n") if isinstance(used, dict) else used[0]) if used else 0
    if n:
        log.warning(f"db_delete_action_template: template {tpl_id} still used by {n} stage(s)")
        return False
    return db_execute("main", "DELETE FROM alert_action_templates WHERE id=?", (tpl_id,))


# ── Per-stage fire-history (engine hot path) ─────────────────────

def _sig(stage_id: int, did: str, sid: str) -> str:
    return f"{stage_id}:{did}:{sid}"


def db_get_stage_state(stage_id: int, did: str, sid: str) -> dict | None:
    row = db_query_one(
        "main",
        "SELECT * FROM alert_profile_state WHERE sig=?",
        (_sig(stage_id, did, sid),)
    )
    if not row:
        return None
    r = dict(row) if not isinstance(row, dict) else row
    return {
        "sig":            r["sig"],
        "first_fire_ts":  float(r.get("first_fire_ts") or 0),
        "last_fire_ts":   float(r.get("last_fire_ts") or 0),
        "fire_count":     int(r.get("fire_count") or 0),
        "active_session": r.get("active_session") or "",
    }


def db_record_stage_fire(stage_id: int, did: str, sid: str, session: str) -> None:
    """Record that a stage just fired. Upserts the state row."""
    now = time.time()
    sig = _sig(stage_id, did, sid)
    try:
        with db_cursor("main") as cur:
            if is_pg():
                cur.execute(
                    "INSERT INTO alert_profile_state (sig, first_fire_ts, last_fire_ts, "
                    "fire_count, active_session) VALUES (%s,%s,%s,1,%s) "
                    "ON CONFLICT (sig) DO UPDATE SET "
                    "  last_fire_ts=EXCLUDED.last_fire_ts, "
                    "  fire_count=alert_profile_state.fire_count+1, "
                    "  active_session=EXCLUDED.active_session, "
                    "  first_fire_ts=CASE WHEN alert_profile_state.active_session=EXCLUDED.active_session "
                    "                     THEN alert_profile_state.first_fire_ts "
                    "                     ELSE EXCLUDED.first_fire_ts END",
                    (sig, now, now, session)
                )
            else:
                existing = cur.execute(
                    "SELECT first_fire_ts, fire_count, active_session "
                    "FROM alert_profile_state WHERE sig=?",
                    (sig,)
                ).fetchone()
                if existing:
                    same_sess = (existing["active_session"] == session)
                    cur.execute(
                        "UPDATE alert_profile_state SET last_fire_ts=?, "
                        "fire_count=?, first_fire_ts=?, active_session=? WHERE sig=?",
                        (now,
                         (existing["fire_count"] + 1) if same_sess else 1,
                         existing["first_fire_ts"] if same_sess else now,
                         session, sig)
                    )
                else:
                    cur.execute(
                        "INSERT INTO alert_profile_state (sig, first_fire_ts, "
                        "last_fire_ts, fire_count, active_session) VALUES (?,?,?,?,?)",
                        (sig, now, now, 1, session)
                    )
    except Exception as e:
        log.error(f"db_record_stage_fire error: {e}")


def db_clear_stage_state_for_sensor(did: str, sid: str) -> None:
    """Wipe all stage fire history for a (did,sid) — called after a recovery fires."""
    try:
        db_execute(
            "main",
            "DELETE FROM alert_profile_state WHERE sig LIKE ?",
            (f"%:{did}:{sid}",)
        )
    except Exception as e:
        log.error(f"db_clear_stage_state_for_sensor error: {e}")


def db_list_active_stage_sessions_for_sensor(did: str, sid: str) -> list:
    """Return all stage_ids that have ever fired for this sensor (any session)."""
    rows = db_query(
        "main",
        "SELECT sig, active_session FROM alert_profile_state WHERE sig LIKE ?",
        (f"%:{did}:{sid}",)
    )
    out = []
    for r in rows:
        try:
            stage_id = int(str(r["sig"]).split(":", 1)[0])
            out.append({"stage_id": stage_id,
                        "active_session": r.get("active_session") or ""})
        except Exception:
            continue
    return out
