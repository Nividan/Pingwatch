"""
routes/probes.py — Admin-facing distributed-probe management.

    GET    /api/probes                 list probes (+ live status, counts)
    POST   /api/probes                 create probe → one-time enrollment token
    PATCH  /api/probes/<id>            rename / re-describe
    POST   /api/probes/<id>/reenroll   revoke live token, issue new enrollment token
    POST   /api/probes/<id>/revoke     revoke live token, keep the record
    DELETE /api/probes/<id>            delete (requires reassign_to when referenced)
    GET    /api/probes/<id>/package    download pre-configured agent zip

Enrollment tokens are one-time use, expire after PROBE_ENROLL_TTL_S, and are
bound to the probe record (db/probes.py consumes them atomically). The
plaintext is shown exactly once — only its SHA-256 lands in the DB.

The in-memory STATE is the source of truth for device/sensor probe_id
(autosave rewrites the DB from it), so reassignment mutates STATE objects
first and lets persistence catch up; only sites live DB-side.
"""
import hashlib
import json
import re
import secrets
import time

from core.app_state import STATE
from core.logger import log, log_probes
from core.probe_assign import effective_probe, invalidate_site_probe_cache
from db.audit import db_log_audit
from db.probes import (
    db_create_probe, db_get_probe, db_list_probes, db_update_probe,
    db_delete_probe, db_set_enroll_token, db_set_probe_status,
    db_probe_assignment_counts, db_pending_task_counts,
)

_RE_PROBES     = re.compile(r"^/api/probes$")
_RE_PROBE      = re.compile(r"^/api/probes/(pr[0-9a-f]{10})$")
_RE_PROBE_OP   = re.compile(r"^/api/probes/(pr[0-9a-f]{10})/(reenroll|revoke|package)$")
# Managed agent updates (v1.4)
_RE_BUILD       = re.compile(r"^/api/probes/build$")
_RE_CAMPAIGNS   = re.compile(r"^/api/probes/campaigns$")
_RE_CAMPAIGN    = re.compile(r"^/api/probes/campaigns/(\d+)$")
_RE_CAMPAIGN_OP = re.compile(r"^/api/probes/campaigns/(\d+)/(abort)$")
_RE_PROBE_UPD   = re.compile(r"^/api/probes/(pr[0-9a-f]{10})/updates$")
_RE_UPD_REPORT  = re.compile(r"^/api/probes/updates/(\d+)$")

PROBE_ENROLL_TTL_S = 7 * 86400      # one-time enrollment tokens live 7 days
PROBE_OFFLINE_AFTER_S = 35          # last_seen older than this = disconnected


def _new_enroll_token() -> tuple:
    """(plaintext, sha256hex) for a fresh enrollment token."""
    tok = "pwe_" + secrets.token_urlsafe(32)
    return tok, hashlib.sha256(tok.encode()).hexdigest()


def _revoke_probe_token(probe: dict):
    """Revoke the probe's live bearer token (if any) and evict the cache."""
    token_id = probe.get("token_id")
    if not token_id:
        return
    try:
        from db.api_tokens import db_revoke_api_token
        from core.auth import auth_evict_api_token_hash
        h = db_revoke_api_token(int(token_id))
        if h:
            auth_evict_api_token_hash(h)
    except Exception as e:
        log_probes.error(f"probe token revoke failed: {type(e).__name__}: {e}")


def _effective_sensor_counts() -> dict:
    """{probe_id: n} of sensors whose EFFECTIVE probe is that probe."""
    out = {}
    with STATE._lock:
        pairs = [(dev, s) for dev in STATE.devices.values()
                 for s in dev.sensors.values()]
    for dev, s in pairs:
        eff = effective_probe(dev, s)
        if eff:
            out[eff] = out.get(eff, 0) + 1
    return out


def _probe_view(p: dict, sensor_counts: dict, task_counts: dict) -> dict:
    """Public shape for one probe row (never exposes token/enroll hashes)."""
    try:
        caps = json.loads(p.get("capabilities") or "{}")
        if not isinstance(caps, dict):
            caps = {}
    except Exception:
        caps = {}
    last_seen = float(p.get("last_seen") or 0)
    return {
        "probe_id":        p["probe_id"],
        "name":            p.get("name") or "",
        "description":     p.get("description") or "",
        "status":          p.get("status") or "pending",
        "connected":       bool(last_seen and
                                time.time() - last_seen < PROBE_OFFLINE_AFTER_S),
        "last_seen":       last_seen,
        "last_checkin_ip": p.get("last_checkin_ip") or "",
        "agent_version":   p.get("agent_version") or "",
        "protocol_version": int(p.get("protocol_version") or 0),
        "os_info":         p.get("os_info") or "",
        "capabilities":    caps,
        "spool_depth":     int(p.get("spool_depth") or 0),
        "clock_skew_s":    float(p.get("clock_skew_s") or 0),
        "config_version":  int(p.get("config_version") or 1),
        "enroll_pending":  bool(p.get("enroll_token_hash")),
        "enroll_expires":  float(p.get("enroll_expires") or 0) if p.get("enroll_expires") else 0,
        "sensor_count":    sensor_counts.get(p["probe_id"], 0),
        "pending_tasks":   task_counts.get(p["probe_id"], 0),
        "created_at":      float(p.get("created_at") or 0),
        "created_by":      p.get("created_by") or "",
        # Managed updates (v1.4)
        "build_id":        p.get("build_id") or "",
        "supervisor":      bool(int(p.get("supervisor") or 0)),
        "update_state":    p.get("update_state") or "",
        "update_target":   p.get("update_target") or "",
        "update_changed_at": float(p.get("update_changed_at") or 0),
        "update_error":    p.get("update_error") or "",
    }


def _reassign_in_memory(old_pid: str, new_target: str) -> list:
    """Repoint every in-memory device/sensor that references old_pid.

    Returns the list of (did, sid) pairs whose effective probe may have
    changed (for apply_probe_assignment). new_target is a probe_id or the
    literal 'central' (explicit pin — never silently re-cascade to another
    probe through a site binding).
    """
    affected = []
    with STATE._lock:
        for dev in STATE.devices.values():
            dev_hit = (getattr(dev, "probe_id", "") == old_pid)
            if dev_hit:
                dev.probe_id = new_target
            for s in dev.sensors.values():
                if getattr(s, "probe_id", "") == old_pid:
                    s.probe_id = new_target
                    affected.append((dev.device_id, s.sensor_id))
                elif dev_hit:
                    affected.append((dev.device_id, s.sensor_id))
    return affected


def _sensors_under_probe(pid: str) -> list:
    """All (did, sid) whose effective probe is pid (incl. site-tier hits)."""
    out = []
    with STATE._lock:
        pairs = [(dev, s) for dev in STATE.devices.values()
                 for s in dev.sensors.values()]
    for dev, s in pairs:
        if effective_probe(dev, s) == pid:
            out.append((dev.device_id, s.sensor_id))
    return out


def handle(h, method, path, body) -> bool:
    # ── Managed agent updates (v1.4) ──────────────────────────────
    # GET /api/probes/build — the server's current agent build id (target +
    # drift detection for the UI).
    if _RE_BUILD.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        try:
            from core.agent_package import compute_build_id
            h._json(200, {"build_id": compute_build_id()})
        except Exception as e:
            h._error(500, "build id unavailable", e, context="probe_build")
        return True

    # GET /api/probes/campaigns — list rollout campaigns + per-state counts.
    if _RE_CAMPAIGNS.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from db.probes import db_list_campaigns, db_campaign_probe_counts
        out = []
        for c in db_list_campaigns(100):
            c = dict(c)
            c["counts"] = db_campaign_probe_counts(c["id"])
            out.append(c)
        h._json(200, {"campaigns": out})
        return True

    # POST /api/probes/campaigns — create + launch a staged rollout (admin).
    if _RE_CAMPAIGNS.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        probe_ids = body.get("probe_ids")
        if not isinstance(probe_ids, list) or not probe_ids:
            h._json(400, {"error": "probe_ids required"}); return True
        # db_get_probe is already imported at module level — re-importing it
        # here would make it a function-local for ALL of handle(), breaking
        # every other handler that uses it (UnboundLocalError).
        from db.probes import db_create_campaign, db_add_campaign_probes
        valid, skipped = [], []
        for pid in probe_ids[:1000]:
            pr = db_get_probe(pid) if isinstance(pid, str) else None
            if pr and int(pr.get("supervisor") or 0) and pr.get("status") == "enrolled":
                valid.append(pid)
            else:
                skipped.append(pid)
        if not valid:
            h._json(400, {"error": "no supervisor-capable probes selected"})
            return True
        # Build the target package ONCE and pin its hash for the whole campaign.
        try:
            from core.agent_package import build_release_payload
            _data, build_id, sha = build_release_payload()
        except Exception as e:
            h._error(500, "package build failed", e, context="campaign_build")
            return True
        name      = str(body.get("name") or f"Update to {build_id}")[:120]
        canary    = max(1, min(int(body.get("canary") or 1), len(valid)))
        batch     = max(1, min(int(body.get("batch_size") or 5), 500))
        halt      = bool(body.get("halt_on_fail", True))
        window    = max(300, min(int(body.get("window_secs") or 86400), 30 * 86400))
        probation = max(30, min(int(body.get("probation_secs") or 120), 900))
        cid = db_create_campaign(name, build_id, sha, canary, batch, halt,
                                 window, probation,
                                 str(body.get("note") or "")[:500], user)
        if not cid:
            h._json(500, {"error": "failed to create campaign"}); return True
        db_add_campaign_probes(cid, valid)
        db_log_audit(user, h.client_address[0], "update_campaign_create", name,
                     f"build={build_id} probes={len(valid)} skipped={len(skipped)}")
        log_probes.info(f"update campaign {cid} by {user}: build {build_id}, "
                        f"{len(valid)} probe(s), {len(skipped)} skipped")
        h._json(200, {"ok": True, "campaign_id": cid, "build_id": build_id,
                      "selected": len(valid), "skipped": skipped})
        return True

    # GET /api/probes/campaigns/<id> — detail + per-probe states.
    m = _RE_CAMPAIGN.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from db.probes import db_get_campaign, db_list_campaign_probes
        c = db_get_campaign(int(m.group(1)))
        if not c:
            h._json(404, {"error": "not found"}); return True
        c = dict(c)
        c["probes"] = db_list_campaign_probes(c["id"])
        h._json(200, c)
        return True

    # POST /api/probes/campaigns/<id>/abort — stop new dispatch (admin).
    m = _RE_CAMPAIGN_OP.match(path)
    if m and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        from db.probes import db_get_campaign, db_set_campaign_state
        c = db_get_campaign(int(m.group(1)))
        if not c:
            h._json(404, {"error": "not found"}); return True
        if c.get("state") == "running":
            db_set_campaign_state(int(m.group(1)), "aborted")
            db_log_audit(user, h.client_address[0], "update_campaign_abort",
                         str(c.get("name") or ""))
        # In-flight probes finish their own probation/rollback; we just stop
        # dispatching further updates.
        h._json(200, {"ok": True})
        return True

    # GET /api/probes/<id>/updates — per-probe update history.
    m = _RE_PROBE_UPD.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from db.probes import db_list_update_reports
        h._json(200, {"updates": db_list_update_reports(m.group(1), 30)})
        return True

    # GET /api/probes/updates/<report_id> — one report incl. full captured log.
    m = _RE_UPD_REPORT.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        from db.probes import db_get_update_report
        rep = db_get_update_report(int(m.group(1)))
        if not rep:
            h._json(404, {"error": "not found"}); return True
        h._json(200, rep)
        return True

    # ── GET /api/probes ───────────────────────────────────────────
    if _RE_PROBES.match(path) and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        sensor_counts = _effective_sensor_counts()
        task_counts   = db_pending_task_counts()
        h._json(200, {"probes": [_probe_view(p, sensor_counts, task_counts)
                                 for p in db_list_probes()]})
        return True

    # ── POST /api/probes ──────────────────────────────────────────
    if _RE_PROBES.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        name = str(body.get("name") or "").strip()[:64]
        desc = str(body.get("description") or "").strip()[:256]
        if not name:
            h._json(400, {"error": "name required"}); return True
        if any(p["name"].lower() == name.lower() for p in db_list_probes()):
            h._json(400, {"error": "a probe with this name already exists"})
            return True
        probe = db_create_probe(name, desc, user)
        if not probe:
            h._json(500, {"error": "failed to create probe"}); return True
        tok, tok_hash = _new_enroll_token()
        db_set_enroll_token(probe["probe_id"], tok_hash,
                            time.time() + PROBE_ENROLL_TTL_S)
        db_log_audit(user, h.client_address[0], "probe_create",
                     name, f"probe_id={probe['probe_id']}")
        h._json(200, {"probe_id": probe["probe_id"], "name": name,
                      "enrollment_token": tok,
                      "expires_at": time.time() + PROBE_ENROLL_TTL_S})
        return True

    # ── PATCH /api/probes/<id> ────────────────────────────────────
    m = _RE_PROBE.match(path)
    if m and method == "PATCH":
        user, _ = h._require("admin")
        if not user: return True
        probe = db_get_probe(m.group(1))
        if not probe:
            h._json(404, {"error": "probe not found"}); return True
        name = str(body.get("name") or probe["name"]).strip()[:64]
        desc = str(body.get("description") if body.get("description") is not None
                   else probe.get("description") or "").strip()[:256]
        if not name:
            h._json(400, {"error": "name required"}); return True
        db_update_probe(probe["probe_id"], name, desc)
        db_log_audit(user, h.client_address[0], "probe_update", name,
                     f"probe_id={probe['probe_id']}")
        h._json(200, {"ok": True})
        return True

    # ── DELETE /api/probes/<id> ───────────────────────────────────
    if m and method == "DELETE":
        user, _ = h._require("admin")
        if not user: return True
        pid = m.group(1)
        probe = db_get_probe(pid)
        if not probe:
            h._json(404, {"error": "probe not found"}); return True
        counts = db_probe_assignment_counts(pid)
        referenced = any(counts.values())
        # do_DELETE dispatches with an empty body — the reassign target rides
        # the query string: DELETE /api/probes/<id>?reassign_to=central|<pid>
        from urllib.parse import urlparse, parse_qs
        _q = parse_qs(urlparse(h.path).query)
        reassign_to = (_q.get("reassign_to") or [""])[0].strip() \
            or str(body.get("reassign_to") or "").strip()
        if referenced and not reassign_to:
            h._json(409, {"error": "probe has assignments — choose a reassign target",
                          "assignments": counts})
            return True
        if referenced:
            if reassign_to != "central":
                target = db_get_probe(reassign_to)
                if not target:
                    h._json(400, {"error": "invalid reassign target"}); return True
            # In-memory devices/sensors are authoritative; sites live DB-side.
            affected = _reassign_in_memory(pid, reassign_to)
            from db.helpers import db_execute
            db_execute("main", "UPDATE sites SET probe_id = ? WHERE probe_id = ?",
                       ("" if reassign_to == "central" else reassign_to, pid))
            invalidate_site_probe_cache()
            STATE.apply_probe_assignment(affected)
            _bump_all_probe_configs()
        _revoke_probe_token(probe)
        db_delete_probe(pid)
        invalidate_site_probe_cache()
        db_log_audit(user, h.client_address[0], "probe_delete", probe["name"],
                     f"probe_id={pid} reassign_to={reassign_to or '-'} "
                     f"devices={counts['devices']} sensors={counts['sensors']} "
                     f"sites={counts['sites']}")
        STATE._broadcast("probe_status", {"probe_id": pid, "deleted": True})
        h._json(200, {"ok": True, "reassigned": counts if referenced else {}})
        return True

    # ── POST /api/probes/<id>/(reenroll|revoke) + GET package ─────
    m = _RE_PROBE_OP.match(path)
    if m:
        pid, op = m.group(1), m.group(2)
        user, _ = h._require("admin")
        if not user: return True
        probe = db_get_probe(pid)
        if not probe:
            h._json(404, {"error": "probe not found"}); return True

        if op == "reenroll" and method == "POST":
            _revoke_probe_token(probe)
            tok, tok_hash = _new_enroll_token()
            db_set_enroll_token(pid, tok_hash, time.time() + PROBE_ENROLL_TTL_S,
                                status="pending")
            db_log_audit(user, h.client_address[0], "probe_reenroll",
                         probe["name"], f"probe_id={pid}")
            STATE._broadcast("probe_status", {"probe_id": pid, "connected": False,
                                              "status": "pending"})
            h._json(200, {"ok": True, "enrollment_token": tok,
                          "expires_at": time.time() + PROBE_ENROLL_TTL_S})
            return True

        if op == "revoke" and method == "POST":
            _revoke_probe_token(probe)
            db_set_probe_status(pid, "revoked")
            db_log_audit(user, h.client_address[0], "probe_revoke",
                         probe["name"], f"probe_id={pid}")
            STATE._broadcast("probe_status", {"probe_id": pid, "connected": False,
                                              "status": "revoked"})
            h._json(200, {"ok": True})
            return True

        if op == "package" and method == "GET":
            if not probe.get("enroll_token_hash"):
                h._json(409, {"error": "no active enrollment token — use Re-enroll first"})
                return True
            # The hash in the DB can't be reversed; the package needs the
            # plaintext. Issue a FRESH token here so the downloaded zip is
            # always enrollable (invalidates any previously shown token).
            tok, tok_hash = _new_enroll_token()
            db_set_enroll_token(pid, tok_hash, time.time() + PROBE_ENROLL_TTL_S,
                                status=probe.get("status") or "pending")
            try:
                from core.agent_package import build_agent_package
                data = build_agent_package(probe, tok, h.headers.get("Host", ""),
                                           request_headers=h.headers)
            except Exception as e:
                h._error(500, "package build failed", e, context="probe_package")
                return True
            db_log_audit(user, h.client_address[0], "probe_package_download",
                         probe["name"], f"probe_id={pid}")
            fname = re.sub(r"[^A-Za-z0-9._-]", "_", f"pingwatch-agent-{probe['name']}.zip")
            h.send_response(200)
            h.send_header("Content-Type", "application/zip")
            h.send_header("Content-Length", str(len(data)))
            h.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            h.send_header("Cache-Control", "no-store")   # zip embeds the token
            h._sec_headers()
            h.end_headers()
            h.wfile.write(data)
            return True

    return False


def _bump_all_probe_configs():
    """Bump every probe's config_version — used after assignment mutations
    where computing the precise old/new probe set isn't worth the risk of
    missing one. Agents each re-pull config once (cheap, tens of probes)."""
    try:
        from db.helpers import db_execute
        db_execute("main", "UPDATE probes SET config_version = config_version + 1")
    except Exception as e:
        log_probes.error(f"probe config bump failed: {type(e).__name__}: {e}")
