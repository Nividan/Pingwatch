"""
db/probes.py — CRUD for distributed probes (remote agents) + agent task queue.

A probe row is the server-side registry entry for one remote agent:
enrollment state, liveness (last_seen), reported version/OS/capabilities,
and the config_version counter agents compare on every checkin.

agent_tasks is the on-demand work queue (IPAM scans, discovery sweeps).
Task *results* never land in the DB — they stream into the in-memory
_SCANS registry (monitoring/subnet_discovery.py) exactly like local scans.

Backend-agnostic via db.helpers — one module serves SQLite and PostgreSQL.
"""
from __future__ import annotations

import time
import uuid

from core.logger import log
from db.backend  import is_pg
from db.helpers  import db_query, db_query_one, db_execute, db_cursor


# ── Probe CRUD ───────────────────────────────────────────────────

def db_create_probe(name: str, description: str, created_by: str) -> dict | None:
    """Insert a new probe in 'pending' state. Returns the row dict or None."""
    probe_id = "pr" + uuid.uuid4().hex[:10]
    now = time.time()
    ok = db_execute("main",
        "INSERT INTO probes (probe_id, name, description, status, created_at, created_by) "
        "VALUES (?,?,?,?,?,?)",
        (probe_id, name, description, "pending", now, created_by))
    if not ok:
        return None
    return db_get_probe(probe_id)


def db_get_probe(probe_id: str) -> dict | None:
    row = db_query_one("main", "SELECT * FROM probes WHERE probe_id = ?", (probe_id,))
    return dict(row) if row else None


def db_list_probes() -> list:
    rows = db_query("main", "SELECT * FROM probes ORDER BY name")
    return [dict(r) for r in rows]


def db_update_probe(probe_id: str, name: str, description: str) -> bool:
    return db_execute("main",
        "UPDATE probes SET name = ?, description = ? WHERE probe_id = ?",
        (name, description, probe_id))


def db_delete_probe(probe_id: str) -> bool:
    db_execute("main", "DELETE FROM agent_tasks WHERE probe_id = ?", (probe_id,))
    return db_execute("main", "DELETE FROM probes WHERE probe_id = ?", (probe_id,))


# ── Enrollment token lifecycle ───────────────────────────────────

def db_set_enroll_token(probe_id: str, token_hash: str, expires: float,
                        status: str = "pending") -> bool:
    """Arm a fresh one-time enrollment token (re-enroll resets status too)."""
    return db_execute("main",
        "UPDATE probes SET enroll_token_hash = ?, enroll_expires = ?, status = ? "
        "WHERE probe_id = ?",
        (token_hash, expires, status, probe_id))


def db_consume_enroll_token(token_hash: str) -> dict | None:
    """Atomically consume a one-time enrollment token.

    Returns the probe row when the token matched, is unexpired, and THIS
    call was the one that cleared it (single use under concurrency).
    Returns None otherwise.
    """
    row = db_query_one("main",
        "SELECT * FROM probes WHERE enroll_token_hash = ?", (token_hash,))
    if not row:
        return None
    probe = dict(row)
    if probe.get("status") == "revoked":
        return None
    exp = probe.get("enroll_expires")
    if exp is None or float(exp) < time.time():
        return None
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"UPDATE probes SET enroll_token_hash = NULL, enroll_expires = NULL "
                f"WHERE probe_id = {ph} AND enroll_token_hash = {ph}",
                (probe["probe_id"], token_hash))
            if cur.rowcount != 1:
                return None   # lost the race — someone else consumed it
    except Exception as e:
        log.error(f"db_consume_enroll_token failed: {type(e).__name__}: {e}")
        return None
    return probe


def db_set_probe_enrolled(probe_id: str, token_id: int) -> bool:
    return db_execute("main",
        "UPDATE probes SET status = 'enrolled', token_id = ? WHERE probe_id = ?",
        (token_id, probe_id))


def db_set_probe_status(probe_id: str, status: str) -> bool:
    return db_execute("main",
        "UPDATE probes SET status = ? WHERE probe_id = ?", (status, probe_id))


# ── Checkin / liveness ───────────────────────────────────────────

# Status-blob columns an agent may update at checkin. Whitelist keeps the
# UPDATE builder injection-proof — keys outside this set are ignored.
_CHECKIN_FIELDS = ("agent_version", "protocol_version", "os_info",
                   "capabilities", "spool_depth", "clock_skew_s",
                   "offline_alerted",
                   # Managed updates (v1.4): reported running build + whether
                   # the probe runs under the supervisor (update-capable).
                   "build_id", "supervisor")

def db_probe_checkin(probe_id: str, ip: str, fields: dict | None = None) -> bool:
    """Update last_seen/last_checkin_ip plus any whitelisted status fields."""
    sets   = ["last_seen = ?", "last_checkin_ip = ?"]
    params = [time.time(), ip]
    for k, v in (fields or {}).items():
        if k in _CHECKIN_FIELDS:
            sets.append(f"{k} = ?")
            params.append(v)
    params.append(probe_id)
    return db_execute("main",
        f"UPDATE probes SET {', '.join(sets)} WHERE probe_id = ?", tuple(params))


def db_bump_config_version(probe_id: str) -> int:
    """Increment config_version; returns the new value (0 on failure)."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"UPDATE probes SET config_version = config_version + 1 "
                f"WHERE probe_id = {ph}", (probe_id,))
            cur.execute(
                f"SELECT config_version FROM probes WHERE probe_id = {ph}",
                (probe_id,))
            row = cur.fetchone()
            return int(row["config_version"]) if row else 0
    except Exception as e:
        log.error(f"db_bump_config_version failed: {type(e).__name__}: {e}")
        return 0


# ── Assignment queries ───────────────────────────────────────────

def db_probe_assignment_counts(probe_id: str) -> dict:
    """How many devices / sensors / sites reference this probe directly."""
    out = {}
    for key, table in (("devices", "devices"), ("sensors", "sensors"),
                       ("sites", "sites")):
        row = db_query_one("main",
            f"SELECT COUNT(*) AS n FROM {table} WHERE probe_id = ?", (probe_id,))
        out[key] = int(row["n"]) if row else 0
    return out


def db_site_probe_map() -> dict:
    """{site_name: probe_id} for every site with a probe binding."""
    rows = db_query("main",
        "SELECT name, probe_id FROM sites WHERE probe_id IS NOT NULL AND probe_id != ''")
    return {r["name"]: r["probe_id"] for r in rows}


# ── Agent task queue ─────────────────────────────────────────────

def db_create_task(probe_id: str, task_type: str, payload_json: str,
                   created_by: str) -> int | None:
    """Insert a pending task. Returns the new task id or None."""
    now = time.time()
    try:
        with db_cursor("main") as cur:
            if is_pg():
                cur.execute(
                    "INSERT INTO agent_tasks (probe_id, task_type, payload, state, "
                    "created_by, created_at) VALUES (%s,%s,%s,'pending',%s,%s) "
                    "RETURNING id",
                    (probe_id, task_type, payload_json, created_by, now))
                row = cur.fetchone()
                return row["id"] if row else None
            cur.execute(
                "INSERT INTO agent_tasks (probe_id, task_type, payload, state, "
                "created_by, created_at) VALUES (?,?,?,'pending',?,?)",
                (probe_id, task_type, payload_json, created_by, now))
            return cur.lastrowid
    except Exception as e:
        log.error(f"db_create_task failed: {type(e).__name__}: {e}")
        return None


def db_get_task(task_id: int) -> dict | None:
    row = db_query_one("main", "SELECT * FROM agent_tasks WHERE id = ?", (task_id,))
    return dict(row) if row else None


def db_dispatch_pending_tasks(probe_id: str) -> list:
    """Flip this probe's pending tasks to 'dispatched' and return them.

    Called from the checkin handler; the per-probe ingest lock in
    routes/agent.py serializes callers, so read-then-update is safe.
    """
    rows = db_query("main",
        "SELECT * FROM agent_tasks WHERE probe_id = ? AND state = 'pending' "
        "ORDER BY id", (probe_id,))
    tasks = [dict(r) for r in rows]
    if tasks:
        now = time.time()
        for t in tasks:
            db_execute("main",
                "UPDATE agent_tasks SET state = 'dispatched', dispatched_at = ? "
                "WHERE id = ? AND state = 'pending'", (now, t["id"]))
            t["state"] = "dispatched"
    return tasks


_TASK_STATES = ("pending", "dispatched", "running", "done", "error",
                "cancelled", "expired")

def db_set_task_state(task_id: int, state: str, error: str = "",
                      progress: str | None = None) -> bool:
    """Advance a task's lifecycle state; stamps finished_at on terminal states."""
    if state not in _TASK_STATES:
        return False
    sets   = ["state = ?"]
    params = [state]
    if error:
        sets.append("error = ?")
        params.append(error[:512])
    if progress is not None:
        sets.append("progress = ?")
        params.append(progress)
    if state in ("done", "error", "cancelled", "expired"):
        sets.append("finished_at = ?")
        params.append(time.time())
    params.append(task_id)
    return db_execute("main",
        f"UPDATE agent_tasks SET {', '.join(sets)} WHERE id = ?", tuple(params))


def db_pending_task_counts() -> dict:
    """{probe_id: n} of tasks not yet in a terminal state (for the Probes UI)."""
    rows = db_query("main",
        "SELECT probe_id, COUNT(*) AS n FROM agent_tasks "
        "WHERE state IN ('pending','dispatched','running') GROUP BY probe_id")
    return {r["probe_id"]: int(r["n"]) for r in rows}


def db_expire_stale_tasks(max_age_s: float = 3600) -> int:
    """Expire tasks stuck in pending/dispatched longer than max_age_s.
    Returns the number of tasks expired (watchdog calls this periodically)."""
    cutoff = time.time() - max_age_s
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"UPDATE agent_tasks SET state = 'expired', finished_at = {ph} "
                f"WHERE state IN ('pending','dispatched') AND created_at < {ph}",
                (time.time(), cutoff))
            return cur.rowcount or 0
    except Exception as e:
        log.error(f"db_expire_stale_tasks failed: {type(e).__name__}: {e}")
        return 0


# ── Managed agent updates (v1.4) ─────────────────────────────────
# Per-probe update lifecycle, the audit log of update attempts, and the
# campaign orchestration tables. State strings:
#   queued → downloading → staged → restarting → verifying →
#   succeeded | rolled_back | failed_offline

def db_set_probe_update_state(probe_id, state, target=None, campaign_id=None,
                              attempt_id=None, error=""):
    """Update a probe's in-flight update lifecycle fields."""
    sets   = ["update_state = ?", "update_changed_at = ?"]
    params = [state, time.time()]
    if target is not None:
        sets.append("update_target = ?");      params.append(target)
    if campaign_id is not None:
        sets.append("update_campaign_id = ?"); params.append(campaign_id)
    if attempt_id is not None:
        sets.append("update_attempt_id = ?");  params.append(attempt_id)
    sets.append("update_error = ?");           params.append(str(error or "")[:500])
    params.append(probe_id)
    return db_execute("main",
        f"UPDATE probes SET {', '.join(sets)} WHERE probe_id = ?", tuple(params))


def db_record_update_report(probe_id, rep) -> bool:
    """Persist an agent-reported update outcome — a success one-liner or a
    rollback with its captured log tail (capped). rep keys: outcome,
    from_build, to_build, target_build, reason, log, campaign_id, attempt_id."""
    return db_execute("main",
        "INSERT INTO agent_update_reports "
        "(probe_id, campaign_id, attempt_id, outcome, from_build, to_build, "
        " target_build, reason, log, ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (probe_id, rep.get("campaign_id"),
         str(rep.get("attempt_id") or "")[:64],
         str(rep.get("outcome") or "")[:32],
         str(rep.get("from_build") or "")[:64],
         str(rep.get("to_build") or "")[:64],
         str(rep.get("target_build") or "")[:64],
         str(rep.get("reason") or "")[:500],
         str(rep.get("log") or "")[:65536],
         float(rep.get("ts") or time.time())))


def db_list_update_reports(probe_id, limit: int = 20) -> list:
    rows = db_query("main",
        "SELECT id, campaign_id, attempt_id, outcome, from_build, to_build, "
        "target_build, reason, ts FROM agent_update_reports "
        "WHERE probe_id = ? ORDER BY ts DESC LIMIT ?", (probe_id, int(limit)))
    return [dict(r) for r in rows]


def db_get_update_report(report_id) -> dict | None:
    row = db_query_one("main",
        "SELECT * FROM agent_update_reports WHERE id = ?", (int(report_id),))
    return dict(row) if row else None


def db_create_campaign(name, target_build, package_sha256, canary, batch_size,
                       halt_on_fail, window_secs, probation_secs, note,
                       created_by):
    """Create a rollout campaign. Returns the new id or None."""
    now = time.time()
    cols = ("name, target_build, package_sha256, canary, batch_size, "
            "halt_on_fail, window_secs, probation_secs, state, note, "
            "created_by, created_at, started_at")
    vals = (name, target_build, package_sha256, int(canary), int(batch_size),
            1 if halt_on_fail else 0, int(window_secs), int(probation_secs),
            "running", note, created_by, now, now)
    placeholders = ",".join(["?"] * 13)
    try:
        with db_cursor("main") as cur:
            q = f"INSERT INTO update_campaigns ({cols}) VALUES ({placeholders})"
            if is_pg():
                cur.execute(q.replace("?", "%s") + " RETURNING id", vals)
                return int(cur.fetchone()["id"])
            cur.execute(q, vals)
            return int(cur.lastrowid)
    except Exception as e:
        log.error(f"db_create_campaign failed: {type(e).__name__}: {e}")
        return None


def db_get_campaign(cid) -> dict | None:
    row = db_query_one("main", "SELECT * FROM update_campaigns WHERE id = ?",
                       (int(cid),))
    return dict(row) if row else None


def db_list_campaigns(limit: int = 50) -> list:
    rows = db_query("main",
        "SELECT * FROM update_campaigns ORDER BY created_at DESC LIMIT ?",
        (int(limit),))
    return [dict(r) for r in rows]


def db_set_campaign_state(cid, state, finished: bool = False) -> bool:
    if finished:
        return db_execute("main",
            "UPDATE update_campaigns SET state = ?, finished_at = ? WHERE id = ?",
            (state, time.time(), int(cid)))
    return db_execute("main",
        "UPDATE update_campaigns SET state = ? WHERE id = ?", (state, int(cid)))


def db_add_campaign_probes(cid, probe_ids) -> bool:
    now = time.time()
    ok = True
    for pid in probe_ids:
        ok = db_execute("main",
            "INSERT INTO campaign_probes (campaign_id, probe_id, state, queued_at) "
            "VALUES (?,?,?,?)", (int(cid), pid, "queued", now)) and ok
    return ok


def db_list_campaign_probes(cid) -> list:
    rows = db_query("main",
        "SELECT * FROM campaign_probes WHERE campaign_id = ? ORDER BY id",
        (int(cid),))
    return [dict(r) for r in rows]


def db_campaign_probe_counts(cid) -> dict:
    rows = db_query("main",
        "SELECT state, COUNT(*) AS n FROM campaign_probes "
        "WHERE campaign_id = ? GROUP BY state", (int(cid),))
    return {r["state"]: int(r["n"]) for r in rows}


def db_campaign_probes_in_state(cid, state) -> list:
    rows = db_query("main",
        "SELECT * FROM campaign_probes WHERE campaign_id = ? AND state = ? "
        "ORDER BY id", (int(cid), state))
    return [dict(r) for r in rows]


def db_set_campaign_probe_state(cid, probe_id, state, attempt_id=None,
                                wave=None, error=None, started: bool = False,
                                finished: bool = False) -> bool:
    sets   = ["state = ?"]
    params = [state]
    if attempt_id is not None:
        sets.append("attempt_id = ?"); params.append(attempt_id)
    if wave is not None:
        sets.append("wave = ?");       params.append(int(wave))
    if error is not None:
        sets.append("error = ?");      params.append(str(error)[:500])
    if started:
        sets.append("started_at = ?"); params.append(time.time())
    if finished:
        sets.append("finished_at = ?"); params.append(time.time())
    params.extend([int(cid), probe_id])
    return db_execute("main",
        f"UPDATE campaign_probes SET {', '.join(sets)} "
        "WHERE campaign_id = ? AND probe_id = ?", tuple(params))
