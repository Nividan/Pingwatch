#!/usr/bin/env python3
"""
PingWatch Agent Supervisor
==========================

The small, stable process the OS service actually runs (systemd ExecStart /
Windows Scheduled Task). It is the *immutable root* of the managed-update
design: it owns process lifecycle, release selection, probation, and rollback,
while the agent runtime (agent.py + probes.py + core/ + vmware/) lives in a
swappable release directory underneath it.

Layout (base dir = this file's directory):
    supervisor.py            ← this file (changed only by manual re-install)
    supervisor_state.json    ← {active_release, previous_release, probation}
    config.json              ← server URL, enroll token, cert pin  ┐ persistent,
    agent_state.json         ← probe_token, probe_id, sensors      │ outside
    spool.jsonl              ← buffered results                    │ releases →
    agent_health.json        ← beacon written by the agent         │ survive a
    update.log               ← supervisor + child stdout/stderr    ┘ swap/rollback
    pending_switch.json      ← agent → supervisor: "stage + probate this build"
    update_report.json       ← supervisor → agent: outcome to upload
    releases/<build_id>/      ← agent runtime payload (self-contained)

Why a persistent parent-monitor (not a per-boot bootstrapper): on Windows the
Scheduled Task does not restart the agent on exit, so nothing but a live parent
can notice "the new build never came up" and revert it. One model serves both
platforms; on Linux systemd keeps THIS supervisor alive (Restart=always).

Failure model covered:
  (a) new build starts but can't re-checkin  → no health beacon in time → revert
  (b) new build won't start / crash-loops     → child exits during probation → revert
A bad build can never dark-out a probe: the previous good release is always kept
and restored, exactly mirroring the server's compile-before-restart deploy.sh.

Stdlib only.
"""

import json
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RELEASES_DIR   = os.path.join(BASE_DIR, "releases")
STATE_PATH     = os.path.join(BASE_DIR, "supervisor_state.json")
HEALTH_PATH    = os.path.join(BASE_DIR, "agent_health.json")
PENDING_SWITCH = os.path.join(BASE_DIR, "pending_switch.json")
UPDATE_REPORT  = os.path.join(BASE_DIR, "update_report.json")
UPDATE_LOG     = os.path.join(BASE_DIR, "update.log")

# Tunables (deliberately conservative; the supervisor must never be the thing
# that breaks).
PROBATION_DEFAULT = 120     # s — fallback if the agent didn't specify a window
PROBATION_MIN     = 30
PROBATION_MAX     = 900
GOOD_CHECKINS     = 2       # consecutive good checkins to commit (see Q3)
CRASH_FAST_SECS   = 60      # a child that exits within this counts as a fast crash
CRASH_MAX         = 3       # this many fast crashes of a committed build → revert
RESTART_BACKOFF   = 5       # s between respawns of a normally-exited child
POLL_SECS         = 2       # health/deadline poll cadence
KILL_GRACE_SECS   = 15      # wait after terminate() before kill()
LOG_CAP_BYTES     = 2_000_000
REPORT_TAIL_BYTES = 64_000


# ── tiny logging (own line discipline; child output is teed into the same
#    file so a crash traceback lands next to the supervisor's narration) ──
def _log(msg):
    line = time.strftime("%Y-%m-%dT%H:%M:%S") + " supervisor: " + str(msg) + "\n"
    try:
        _rotate_log()
        with open(UPDATE_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    try:
        sys.stderr.write(line)
    except Exception:
        pass


def _rotate_log():
    try:
        if os.path.getsize(UPDATE_LOG) > LOG_CAP_BYTES:
            # Keep the tail; drop the head. Simple, cross-platform, no extra files.
            with open(UPDATE_LOG, "rb") as f:
                f.seek(-LOG_CAP_BYTES // 2, os.SEEK_END)
                tail = f.read()
            with open(UPDATE_LOG, "wb") as f:
                f.write(b"...[log truncated]...\n" + tail)
    except Exception:
        pass


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, path)   # atomic on POSIX and Windows


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _read_log_tail():
    try:
        with open(UPDATE_LOG, "rb") as f:
            try:
                f.seek(-REPORT_TAIL_BYTES, os.SEEK_END)
            except OSError:
                f.seek(0)
            return f.read().decode("utf-8", "replace")
    except Exception:
        return ""


# ── State helpers ─────────────────────────────────────────────────
def _load_state():
    st = _load_json(STATE_PATH, {})
    st.setdefault("active_release", None)
    st.setdefault("previous_release", None)
    st.setdefault("probation", None)   # {target, previous, deadline, started, campaign_id, attempt_id}
    return st


def _release_dir(build_id):
    return os.path.join(RELEASES_DIR, build_id or "")


def _release_runnable(build_id):
    return bool(build_id) and os.path.isfile(os.path.join(_release_dir(build_id), "agent.py"))


def _discover_fallback_release():
    """Newest runnable release dir on disk — last resort when state names a
    release that no longer exists (corrupt/partial extract)."""
    try:
        cands = []
        for name in os.listdir(RELEASES_DIR):
            if _release_runnable(name):
                cands.append((os.path.getmtime(_release_dir(name)), name))
        cands.sort(reverse=True)
        return cands[0][1] if cands else None
    except Exception:
        return None


def _prune_releases(keep):
    keep = {b for b in keep if b}
    try:
        for name in os.listdir(RELEASES_DIR):
            if name in keep:
                continue
            path = _release_dir(name)
            if os.path.isdir(path):
                _rmtree(path)
                _log("pruned old release %s" % name)
    except Exception:
        pass


def _rmtree(path):
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _write_report(outcome, st, reason="", target=None):
    """Leave an update report for the running agent to upload on its next
    checkin (POST /api/agent/update-report). The agent deletes it once acked."""
    prob = st.get("probation") or {}
    rep = {
        "outcome":     outcome,                       # 'success' | 'rolled_back'
        "from_build":  prob.get("previous"),
        "to_build":    st.get("active_release"),
        "target_build": target or prob.get("target"),
        "campaign_id": prob.get("campaign_id"),
        "attempt_id":  prob.get("attempt_id"),
        "ts":          time.time(),
        "reason":      reason,
        # Full log tail only matters on failure; success stays a one-liner (Q8).
        "log":         _read_log_tail() if outcome != "success" else "",
    }
    try:
        _save_json_atomic(UPDATE_REPORT, rep)
    except Exception as e:
        _log("could not write update_report: %s" % type(e).__name__)


# ── Child process management ──────────────────────────────────────
def _spawn(build_id):
    """Launch the agent runtime from releases/<build_id>/, teeing its stdout +
    stderr into update.log so a crash-on-start traceback is captured (case b)."""
    agent_py = os.path.join(_release_dir(build_id), "agent.py")
    _rotate_log()
    logf = open(UPDATE_LOG, "a", encoding="utf-8")
    logf.write("%s supervisor: spawning agent build=%s\n"
               % (time.strftime("%Y-%m-%dT%H:%M:%S"), build_id))
    logf.flush()
    kw = {}
    if os.name == "nt":
        # No console window for the child (matches the pythonw Scheduled Task).
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [sys.executable, agent_py, "--data-dir", BASE_DIR, "--build-id", build_id],
        stdout=logf, stderr=subprocess.STDOUT, cwd=_release_dir(build_id), **kw,
    )
    return proc, logf


def _stop_child(proc):
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    deadline = time.time() + KILL_GRACE_SECS
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.3)
    try:
        proc.kill()
    except Exception:
        pass


def _health():
    return _load_json(HEALTH_PATH, {}) or {}


# ── Probation transitions ─────────────────────────────────────────
def _begin_switch(st, ps):
    """Agent staged a new release and asked to switch to it. Make it active,
    remember the current good release as the rollback target, start probation."""
    target = ps.get("target_release")
    window = ps.get("probation_window") or PROBATION_DEFAULT
    window = max(PROBATION_MIN, min(PROBATION_MAX, int(window)))
    prev_good = st.get("active_release")
    st["probation"] = {
        "target":      target,
        "previous":    prev_good,
        "deadline":    time.time() + window,
        "started":     time.time(),
        "campaign_id": ps.get("campaign_id"),
        "attempt_id":  ps.get("attempt_id"),
    }
    st["active_release"] = target
    _save_json_atomic(STATE_PATH, st)
    # Record the build id inside the release so the agent can self-identify even
    # without the --build-id arg.
    try:
        with open(os.path.join(_release_dir(target), "BUILD_ID"), "w",
                  encoding="utf-8") as f:
            f.write(str(target) + "\n")
    except Exception:
        pass
    _remove(HEALTH_PATH)   # don't let a prior build's beacon satisfy probation
    _log("switch: probating build %s (was %s), window %ss"
         % (target, prev_good, window))


def _commit(st):
    prob = st.get("probation") or {}
    st["previous_release"] = prob.get("previous")
    target = prob.get("target")
    st["probation"] = None
    _save_json_atomic(STATE_PATH, st)
    _write_report("success", st, reason="committed", target=target)
    _prune_releases({st.get("active_release"), st.get("previous_release")})
    _log("commit: build %s healthy (%d consecutive checkins) — committed"
         % (target, GOOD_CHECKINS))


def _rollback(st, reason):
    prob = st.get("probation") or {}
    target = prob.get("target")
    prev = prob.get("previous")
    _write_report("rolled_back", st, reason=reason, target=target)  # capture log BEFORE flipping active
    st["active_release"] = prev if _release_runnable(prev) else (
        _discover_fallback_release() or prev)
    st["probation"] = None
    _save_json_atomic(STATE_PATH, st)
    _remove(HEALTH_PATH)
    _log("ROLLBACK: build %s -> %s (%s)" % (target, st["active_release"], reason))


# ── Main supervise loop ───────────────────────────────────────────
def supervise():
    st = _load_state()
    # Recover an active release if state points at a missing one.
    if not _release_runnable(st.get("active_release")):
        fb = _discover_fallback_release()
        if fb:
            _log("active release %s missing — falling back to %s"
                 % (st.get("active_release"), fb))
            st["active_release"] = fb
            st["probation"] = None
            _save_json_atomic(STATE_PATH, st)

    fast_crashes = 0
    while True:
        active = st.get("active_release")
        if not _release_runnable(active):
            _log("no runnable release on disk — retrying in %ss" % RESTART_BACKOFF)
            time.sleep(RESTART_BACKOFF)
            st = _load_state()
            continue

        # A pending switch left over from a prior boot (agent staged + exited
        # before we processed it) — apply it before spawning.
        ps = _load_json(PENDING_SWITCH, None)
        if ps and ps.get("target_release") and _release_runnable(ps["target_release"]):
            _remove(PENDING_SWITCH)
            _begin_switch(st, ps)
            active = st["active_release"]

        try:
            proc, logf = _spawn(active)
        except Exception as e:
            _log("spawn failed for %s: %s — backing off" % (active, type(e).__name__))
            time.sleep(RESTART_BACKOFF)
            st = _load_state()
            continue
        started = time.time()

        # Monitor the child until it exits or probation resolves.
        rolled = False
        while True:
            ret = proc.poll()
            if ret is not None:
                break
            prob = st.get("probation")
            if prob and prob.get("target") == active:
                h = _health()
                if (h.get("build_id") == active
                        and int(h.get("consecutive_good") or 0) >= GOOD_CHECKINS):
                    _commit(st)
                elif time.time() > float(prob.get("deadline") or 0):
                    _log("probation deadline passed for %s without %d good "
                         "checkins — rolling back" % (active, GOOD_CHECKINS))
                    _stop_child(proc)
                    _rollback(st, "probation timeout (no healthy checkin)")
                    rolled = True
                    break
            time.sleep(POLL_SECS)

        try:
            logf.close()
        except Exception:
            pass
        ran_for = time.time() - started

        if rolled:
            fast_crashes = 0
            continue   # respawn previous (now active)

        # Child exited on its own. Did the agent ask to switch (graceful update
        # handoff)?
        ps = _load_json(PENDING_SWITCH, None)
        if ps and ps.get("target_release"):
            _remove(PENDING_SWITCH)
            if _release_runnable(ps["target_release"]):
                _begin_switch(st, ps)
                fast_crashes = 0
                continue
            _log("pending switch named a non-runnable release %s — ignoring"
                 % ps.get("target_release"))

        # Child died while on probation (crash on start / mid-probation = case b).
        prob = st.get("probation")
        if prob and prob.get("target") == active:
            _rollback(st, "agent exited during probation (code=%s)" % ret)
            fast_crashes = 0
            continue

        # A committed release exited/crashed. Guard against a release that was
        # healthy at commit but later crash-loops: after CRASH_MAX fast crashes,
        # fall back to the previous good release if we have one.
        if ran_for < CRASH_FAST_SECS:
            fast_crashes += 1
            _log("agent %s exited after %.0fs (code=%s); fast-crash %d/%d"
                 % (active, ran_for, ret, fast_crashes, CRASH_MAX))
            prev = st.get("previous_release")
            if fast_crashes >= CRASH_MAX and _release_runnable(prev) and prev != active:
                _write_report("rolled_back",
                              {"active_release": prev,
                               "probation": {"target": active, "previous": prev}},
                              reason="committed release crash-looping")
                st["active_release"] = prev
                _save_json_atomic(STATE_PATH, st)
                _remove(HEALTH_PATH)
                _log("CRASH-LOOP ROLLBACK: %s -> %s" % (active, prev))
                fast_crashes = 0
            else:
                time.sleep(RESTART_BACKOFF)
        else:
            fast_crashes = 0   # ran long enough — a normal restart, not a loop
        # respawn
        st = _load_state()


def main():
    _log("PingWatch supervisor starting (base=%s, python=%s)"
         % (BASE_DIR, sys.executable))
    while True:
        try:
            supervise()
        except KeyboardInterrupt:
            _log("supervisor stopping (Ctrl+C)")
            return
        except Exception as e:
            # The supervisor must never die from a transient error — log and
            # restart the loop. (systemd would relaunch us on Linux, but the
            # Windows Task would not, so we self-heal.)
            _log("supervise loop crashed: %s: %s — restarting in %ss"
                 % (type(e).__name__, e, RESTART_BACKOFF))
            time.sleep(RESTART_BACKOFF)


if __name__ == "__main__":
    main()
