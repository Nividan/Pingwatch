"""
monitoring/update_orchestrator.py — staged-rollout engine for managed agent
updates (Option B).

A campaign targets a set of probes with a pinned build (build_id +
package_sha256) and a policy: canary count, batch size, halt-on-failure,
validity window, probation window. campaign_tick() — driven by the probe
watchdog sweep — advances every running campaign one step:

  • canary first: dispatch up to `canary` probes; don't open the batch phase
    until that many have COMMITTED (succeeded). A canary that rolls back trips
    auto-halt before any wider rollout.
  • then batches: keep up to `batch_size` updates in flight at once.
  • auto-halt: any probe reaching rolled_back or failed_offline halts the whole
    campaign (when halt_on_fail) — a bad build trips on the canary and the rest
    of the fleet is never touched.
  • offline-deferred: a selected probe that's offline isn't dispatched; it waits
    queued and gets picked up when it checks back in, until the validity window
    expires (then → expired).
  • failed_offline: a dispatched probe that never re-checks-in within the
    deadline is declared dark (needs hands-on) — distinct from rolled_back
    (self-healed onto the old build).

Per-probe outcomes themselves come from the agent via /api/agent/update-report
(success / rolled_back); this engine only schedules waves and reacts to the
recorded campaign_probes states.
"""
import json
import time

from core.logger import log_probes
from db.probes import (
    db_list_campaigns, db_set_campaign_state, db_list_campaign_probes,
    db_set_campaign_probe_state, db_get_probe, db_create_task,
    db_set_probe_update_state,
)

# A probe is "online" if it checked in within this window (matches the rest of
# the probe-liveness code).
_ONLINE_S = 60


def _online(probe) -> bool:
    return probe is not None and (time.time() - float(probe.get("last_seen") or 0)) <= _ONLINE_S


def campaign_tick():
    """Advance all running campaigns. Cheap + idempotent when nothing runs."""
    try:
        running = [c for c in db_list_campaigns(200) if c.get("state") == "running"]
    except Exception as e:
        log_probes.debug(f"campaign_tick list failed: {type(e).__name__}: {e}")
        return
    for c in running:
        try:
            _advance(c)
        except Exception as e:
            log_probes.error(f"campaign {c.get('id')} tick failed: "
                             f"{type(e).__name__}: {e}")


def _advance(c):
    cid       = int(c["id"])
    canary_n  = max(1, int(c.get("canary") or 1))
    batch     = max(1, int(c.get("batch_size") or 5))
    halt      = bool(c.get("halt_on_fail"))
    window    = int(c.get("window_secs") or 86400)
    probation = int(c.get("probation_secs") or 120)
    started   = float(c.get("started_at") or c.get("created_at") or time.time())
    now       = time.time()

    probes = db_list_campaign_probes(cid)
    by_state = {}
    for p in probes:
        by_state.setdefault(p["state"], []).append(p)

    # 1. Auto-halt on any terminal failure.
    if halt and (by_state.get("rolled_back") or by_state.get("failed_offline")):
        n = len(by_state.get("rolled_back", [])) + len(by_state.get("failed_offline", []))
        db_set_campaign_state(cid, "halted")
        log_probes.warning(f"campaign {cid} HALTED — {n} probe(s) failed")
        return

    # 2. failed_offline: dispatched but silent past the deadline (download +
    #    restart + 2× probation gives ample slack before declaring it dark).
    deadline_slack = probation * 2 + 120
    transitioned = False
    for p in by_state.get("dispatched", []):
        if now <= float(p.get("started_at") or now) + deadline_slack:
            continue
        pr = db_get_probe(p["probe_id"])
        if not _online(pr):
            db_set_campaign_probe_state(cid, p["probe_id"], "failed_offline",
                error="no checkin after update within deadline", finished=True)
            db_set_probe_update_state(p["probe_id"], "failed_offline")
            log_probes.warning(f"campaign {cid} probe {p['probe_id']} → failed_offline")
            transitioned = True
    if transitioned:
        if halt:
            db_set_campaign_state(cid, "halted")
            return
        probes = db_list_campaign_probes(cid)
        by_state = {}
        for p in probes:
            by_state.setdefault(p["state"], []).append(p)

    queued     = by_state.get("queued", [])
    dispatched = by_state.get("dispatched", [])
    succeeded  = by_state.get("succeeded", [])

    # 3. Expire queued probes that sat out the whole validity window (offline).
    if now - started > window and queued:
        for p in queued:
            db_set_campaign_probe_state(cid, p["probe_id"], "expired",
                error="probe offline for entire campaign window", finished=True)
        queued = []

    # 4. Done when nothing is queued or in flight.
    if not queued and not dispatched:
        db_set_campaign_state(cid, "done", finished=True)
        log_probes.info(f"campaign {cid} complete "
                        f"({len(succeeded)} updated)")
        return

    # 5. Wave budget: stay in the canary phase until `canary` probes have
    #    committed, then open up to `batch_size` concurrent.
    in_flight = len(dispatched)
    if len(succeeded) < canary_n:
        budget = canary_n - in_flight - len(succeeded)
    else:
        budget = batch - in_flight
    if budget <= 0:
        return

    # 6. Dispatch to online + supervisor-capable queued probes (offline ones
    #    wait their turn — they're picked up when they check back in).
    sent = 0
    for p in queued:
        if sent >= budget:
            break
        pr = db_get_probe(p["probe_id"])
        if not _online(pr) or not int((pr or {}).get("supervisor") or 0):
            continue
        attempt = f"c{cid}-{int(now)}-{p['probe_id']}"
        payload = json.dumps({
            "build_id":         c["target_build"],
            "package_sha256":   c.get("package_sha256") or "",
            "probation_window": probation,
            "campaign_id":      cid,
            "attempt_id":       attempt,
        })
        tid = db_create_task(p["probe_id"], "agent_update", payload,
                             c.get("created_by") or "")
        if tid:
            db_set_campaign_probe_state(cid, p["probe_id"], "dispatched",
                                        attempt_id=attempt, started=True)
            db_set_probe_update_state(p["probe_id"], "queued",
                                      target=c["target_build"], campaign_id=cid,
                                      attempt_id=attempt)
            sent += 1
    if sent:
        phase = "canary" if len(succeeded) < canary_n else "batch"
        log_probes.info(f"campaign {cid} dispatched {sent} update(s) [{phase}]")
