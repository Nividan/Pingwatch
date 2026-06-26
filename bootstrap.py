#!/usr/bin/env python3
"""
bootstrap.py — immutable outer supervisor for PingWatch's managed-upgrade layout.

Server-side analogue of agent/supervisor.py. It is the stable root the OS service
starts (systemd ExecStart on Linux; invoked by windows/launcher.pyw on Windows).
It owns: release selection, the at-boot upgrade swap, probation, and rollback —
while the server runtime lives in a swappable releases/<version>/ directory.

Layout (base dir = this file's directory):

    bootstrap.py             <- this file (BASE, immutable; changed only by re-install)
    current.txt              <- pointer: the active release dir name
    upgrade_state.json       <- upgrade state machine (shared with core/upgrade.py)
    update_report.json       <- outcome of the last upgrade, for the UI to upload
    server_health.json       <- health beacon written by the running server
    releases/<version>/      <- server runtime payload (server.py, core/, ...)
    db_snapshots/<id>/       <- pre-upgrade DB snapshot (restored on rollback)
    data/                    <- persistent state (DB, conf, certs, logs, backups)

Why a persistent parent and not a plain systemd ExecStart of server.py: an upgrade
must not have the running server overwrite its own code, and on rollback something
stable must repoint the next launch at the previous release AND restore the DB
snapshot while no server holds the files. The supervisor is that stable thing —
identical model on both platforms (systemd keeps THIS alive on Linux; the launcher
does on Windows).

Failure model covered (mirrors the agent supervisor):
  (a) new release starts but never reports healthy  -> probation deadline -> roll back
  (b) new release crashes during probation          -> child exit -> roll back
  (c) a committed release later crash-loops          -> revert to previous good
A bad release can never dark-out the server: the previous good release + its DB
snapshot are always kept and restored.

In a FLAT checkout (server.py beside this file, no releases/), bootstrap is a
transparent pass-through that runs ./server.py once — so every launcher can route
through bootstrap without changing behavior on existing installs.

Stdlib only. Must NOT import anything from inside a release directory — at the
moment this runs it is still deciding which release to load and may need to roll
one back.
"""

import json
import os
import shutil
import subprocess
import sys
import time

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
RELEASES_DIR  = os.path.join(BASE_DIR, "releases")
DATA_DIR      = os.path.join(BASE_DIR, "data")
SNAPSHOTS_DIR = os.path.join(BASE_DIR, "db_snapshots")
POINTER_PATH  = os.path.join(BASE_DIR, "current.txt")
STATE_PATH    = os.path.join(BASE_DIR, "upgrade_state.json")
REPORT_PATH   = os.path.join(BASE_DIR, "update_report.json")
HEALTH_PATH   = os.path.join(BASE_DIR, "server_health.json")

# Probation tunables (deliberately conservative — the supervisor must never be
# the thing that breaks). Aligned with agent/supervisor.py.
PROBATION_DEFAULT = 120     # s — fallback if the image didn't specify a window
PROBATION_MIN     = 30
PROBATION_MAX     = 900
GOOD_CHECKINS     = 2       # consecutive healthy beacons to commit a new release
CRASH_FAST_SECS   = 60      # a committed release exiting this fast counts as a crash
CRASH_MAX         = 3       # this many fast crashes of a committed release -> revert
RESTART_BACKOFF   = 5       # s between respawns
POLL_SECS         = 2       # health/exit poll cadence
KILL_GRACE_SECS   = 15      # wait after terminate() before kill()


def _log(msg):
    try:
        sys.stderr.write("bootstrap: " + str(msg) + "\n")
        sys.stderr.flush()
    except Exception:
        pass


# ── JSON helpers (atomic) ─────────────────────────────────────────────────────
def _load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {} if default is None else default
    except Exception:
        return {} if default is None else default


def _save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, path)


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except Exception:
        pass


# ── Pointer + release resolution ──────────────────────────────────────────────
def read_pointer():
    try:
        with open(POINTER_PATH, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None
    except Exception as e:
        _log("could not read pointer: %s" % type(e).__name__)
        return None


def write_pointer(name):
    tmp = POINTER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(name).strip() + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, POINTER_PATH)


def release_dir(name):
    return os.path.join(RELEASES_DIR, name or "")


def release_runnable(name):
    return bool(name) and os.path.isfile(os.path.join(release_dir(name), "server.py"))


def discover_fallback():
    """Newest runnable release on disk — last resort when the pointer names a
    release that no longer exists."""
    try:
        cands = [(os.path.getmtime(release_dir(n)), n)
                 for n in os.listdir(RELEASES_DIR) if release_runnable(n)]
        cands.sort(reverse=True)
        return cands[0][1] if cands else None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def resolve_active():
    name = read_pointer()
    if release_runnable(name):
        return name
    fb = discover_fallback()
    if fb and fb != name:
        _log("pointer %r not runnable — falling back to %s" % (name, fb))
        try:
            write_pointer(fb)
        except Exception:
            pass
    return fb


def is_managed():
    return bool(read_pointer()) or discover_fallback() is not None


def resolve_code_root():
    """(code_root, data_dir) for an in-process launcher (windows/launcher.pyw).
    Managed -> (active release dir, BASE/data). Flat -> (BASE, None)."""
    if is_managed():
        active = resolve_active()
        return (release_dir(active), DATA_DIR) if active else (None, None)
    return BASE_DIR, None


def _compile_ok(path):
    """Syntactic gate on the code about to launch — same guard as deploy.sh /
    the systemd ExecStartPre, but targeting exactly this release."""
    try:
        import compileall
        return bool(compileall.compile_dir(path, quiet=1, maxlevels=20))
    except Exception as e:
        _log("compile check error on %s: %s" % (path, type(e).__name__))
        return False


# ── DB snapshot restore (rollback) ────────────────────────────────────────────
def _restore_snapshot(snapshot_id, backend):
    """Restore the pre-upgrade DB snapshot so a rolled-back (older) server runs
    against a schema it understands. Runs only here, with NO server holding the
    DB. SQLite is an atomic file swap; PostgreSQL replays a pg_dump via psql."""
    snap = os.path.join(SNAPSHOTS_DIR, snapshot_id or "")
    if not snapshot_id or not os.path.isdir(snap):
        _log("rollback: no snapshot %r to restore — leaving DB as-is" % snapshot_id)
        return
    if backend == "postgresql":
        _restore_pg(snap)
    else:
        _restore_sqlite(snap)


def _restore_sqlite(snap):
    pairs = [("main.sqlite", "pingwatch.db"), ("logs.sqlite", "pingwatch_logs.db")]
    for src_name, dst_name in pairs:
        src = os.path.join(snap, src_name)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(DATA_DIR, dst_name)
        # Drop stale WAL/SHM so the restored file is authoritative (mirrors the
        # DB-import apply path in server.py).
        for sfx in ("-wal", "-shm"):
            _remove(dst + sfx)
        try:
            shutil.copy2(src, dst)
            _log("rollback: restored %s" % dst_name)
        except Exception as e:
            _log("rollback: FAILED to restore %s: %s" % (dst_name, type(e).__name__))


def _restore_pg(snap):
    """Restore main (and logs, best-effort) schemas from pg_dump files via psql,
    using credentials from data/pingwatch.conf. main is restored first and is the
    one rollback correctness depends on; logs is high-volume and additive, so a
    logs restore failure is logged but not fatal."""
    conf = _load_json(os.path.join(DATA_DIR, "pingwatch.conf"), {})
    host = conf.get("pg_host", "localhost"); port = str(conf.get("pg_port", 5432))
    user = conf.get("pg_user", "pingwatch"); db = conf.get("pg_database", "pingwatch")
    pw   = conf.get("pg_password", "")
    env = dict(os.environ)
    if pw:
        env["PGPASSWORD"] = pw
    for schema, fname, fatal in (("main", "main.sql", True), ("logs", "logs.sql", False)):
        path = os.path.join(snap, fname)
        if not os.path.isfile(path):
            continue
        try:
            drop = subprocess.run(
                ["psql", "-h", host, "-p", port, "-U", user, "-d", db, "-v", "ON_ERROR_STOP=1",
                 "-c", "DROP SCHEMA IF EXISTS %s CASCADE; CREATE SCHEMA %s;" % (schema, schema)],
                env=env, capture_output=True, text=True)
            load = subprocess.run(
                ["psql", "-h", host, "-p", port, "-U", user, "-d", db, "-v", "ON_ERROR_STOP=1", "-f", path],
                env=env, capture_output=True, text=True)
            if drop.returncode or load.returncode:
                raise RuntimeError((load.stderr or drop.stderr or "psql failed").strip()[:200])
            _log("rollback: restored PG schema %s" % schema)
        except Exception as e:
            lvl = "FAILED (fatal)" if fatal else "failed (non-fatal)"
            _log("rollback: PG schema %s restore %s: %s" % (schema, lvl, type(e).__name__))


# ── Child process management ──────────────────────────────────────────────────
def _spawn(name):
    rel = release_dir(name)
    env = dict(os.environ)
    env["PW_DATA_DIR"] = DATA_DIR
    env["PW_BASE_DIR"] = BASE_DIR
    env["PW_RELEASE"]  = name
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _log("spawning release %s" % name)
    return subprocess.Popen([sys.executable, os.path.join(rel, "server.py")],
                            cwd=rel, env=env, **kw)


def _stop(proc):
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


def _clamp_window(w):
    try:
        return max(PROBATION_MIN, min(PROBATION_MAX, int(w)))
    except (TypeError, ValueError):
        return PROBATION_DEFAULT


# ── Upgrade state transitions ─────────────────────────────────────────────────
def _begin_probation(st, active):
    """A new release was staged (phase=staged by the server). Make it active,
    remember the current good release as the rollback target, start probation."""
    target = st.get("target")
    window = _clamp_window(st.get("probation_window") or PROBATION_DEFAULT)
    st["previous"] = active
    st["phase"] = "probation"
    st["probation_deadline"] = time.time() + window
    st["probation_window"] = window
    write_pointer(target)
    _save_json(STATE_PATH, st)
    _remove(HEALTH_PATH)   # a prior release's beacon must not satisfy probation
    _log("switch: probating %s (was %s), window %ss" % (target, active, window))


def _write_report(outcome, st, reason=""):
    rep = {
        "outcome": outcome,
        "from": st.get("previous"),
        "to": st.get("target"),
        "upgrade_id": st.get("upgrade_id"),
        "reason": reason,
        "ts": time.time(),
    }
    try:
        _save_json(REPORT_PATH, rep)
    except Exception:
        pass


def _commit(st):
    target = st.get("target")
    _write_report("committed", st, reason="probation passed")
    prev = st.get("previous")
    st["phase"] = "idle"
    _save_json(STATE_PATH, st)
    # Keep the active + previous releases; keep ONLY this upgrade's DB snapshot
    # (the crash-loop rollback target). Prune everything older.
    _prune_releases({target, prev})
    _prune_snapshots({st.get("db_snapshot")})
    _log("commit: %s healthy (%d good beacons) — committed" % (target, GOOD_CHECKINS))


def _rollback(st, reason):
    target = st.get("target")
    prev = st.get("previous")
    _write_report("rolled_back", st, reason=reason)
    _restore_snapshot(st.get("db_snapshot"), st.get("db_backend"))
    if release_runnable(prev):
        write_pointer(prev)
    else:
        fb = discover_fallback()
        if fb:
            write_pointer(fb)
    st["phase"] = "idle"
    _save_json(STATE_PATH, st)
    _remove(HEALTH_PATH)
    _log("ROLLBACK: %s -> %s (%s)" % (target, read_pointer(), reason))


def _prune_releases(keep):
    keep = {k for k in keep if k}
    try:
        for name in os.listdir(RELEASES_DIR):
            if name not in keep and os.path.isdir(release_dir(name)):
                shutil.rmtree(release_dir(name), ignore_errors=True)
                _log("pruned old release %s" % name)
    except Exception:
        pass


def _prune_snapshots(keep):
    """Delete DB snapshots except the ones in `keep` (the committed upgrade's
    snapshot is retained as the crash-loop rollback target). PG logs snapshots
    can be hundreds of MB, so leaving them around leaks disk fast."""
    keep = {k for k in keep if k}
    try:
        for name in os.listdir(SNAPSHOTS_DIR):
            path = os.path.join(SNAPSHOTS_DIR, name)
            if name not in keep and os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
                _log("pruned old DB snapshot %s" % name)
    except FileNotFoundError:
        pass
    except Exception:
        pass


# ── Supervise loop (managed layout) ───────────────────────────────────────────
def supervise():
    fast_crashes = 0
    while True:
        st = _load_json(STATE_PATH, {})
        active = resolve_active()
        if not active:
            _log("no runnable release under %s — retry in %ss" % (RELEASES_DIR, RESTART_BACKOFF))
            time.sleep(RESTART_BACKOFF)
            continue

        # Admin asked to revert (manual rollback): restore previous release + DB
        # snapshot before launching anything.
        if st.get("phase") == "rollback_requested":
            _rollback(st, "manual rollback requested")
            continue

        # Apply a staged upgrade before launching: switch + arm probation.
        if st.get("phase") == "staged" and release_runnable(st.get("target")):
            _begin_probation(st, active)
            st = _load_json(STATE_PATH, {})
            active = st.get("target")

        # Syntax-gate the release we are about to run; a broken one rolls back
        # (if on probation) or falls back to a runnable release.
        if not _compile_ok(release_dir(active)):
            _log("release %s failed the syntax gate" % active)
            if st.get("phase") == "probation" and st.get("target") == active:
                _rollback(st, "syntax error in staged release")
                continue
            fb = discover_fallback()
            if fb and fb != active:
                write_pointer(fb)
            time.sleep(RESTART_BACKOFF)
            continue

        proc = _spawn(active)
        started = time.time()
        rolled = False

        while True:
            ret = proc.poll()
            if ret is not None:
                break
            st = _load_json(STATE_PATH, {})
            if st.get("phase") == "probation" and st.get("target") == active:
                h = _health()
                if (h.get("version") == active
                        and int(h.get("consecutive_good") or 0) >= GOOD_CHECKINS):
                    _commit(st)
                elif time.time() > float(st.get("probation_deadline") or 0):
                    _log("probation deadline passed for %s — rolling back" % active)
                    _stop(proc)
                    _rollback(st, "probation timeout (no healthy beacon)")
                    rolled = True
                    break
            time.sleep(POLL_SECS)

        ran_for = time.time() - started
        if rolled:
            fast_crashes = 0
            continue

        # Child exited on its own.
        st = _load_json(STATE_PATH, {})
        if st.get("phase") == "probation" and st.get("target") == active:
            _rollback(st, "server exited during probation (code=%s)" % ret)
            fast_crashes = 0
            continue

        # A committed release exited. Guard against a crash-loop.
        if ran_for < CRASH_FAST_SECS:
            fast_crashes += 1
            _log("release %s exited after %.0fs (code=%s); fast-crash %d/%d"
                 % (active, ran_for, ret, fast_crashes, CRASH_MAX))
            prev = st.get("previous")
            if fast_crashes >= CRASH_MAX and release_runnable(prev) and prev != active:
                _write_report("rolled_back", st, reason="committed release crash-looping")
                _restore_snapshot(st.get("db_snapshot"), st.get("db_backend"))
                write_pointer(prev)
                _remove(HEALTH_PATH)
                _log("CRASH-LOOP ROLLBACK: %s -> %s" % (active, prev))
                fast_crashes = 0
            else:
                time.sleep(RESTART_BACKOFF)
        else:
            fast_crashes = 0


def exec_flat():
    """Flat checkout: run the sibling server.py directly, leaving PW_DATA_DIR
    unset so core.config keeps its legacy in-tree data paths. No supervise loop —
    a flat install does not take managed upgrades."""
    server_py = os.path.join(BASE_DIR, "server.py")
    if not os.path.isfile(server_py):
        _log("flat layout but no %s — cannot start" % server_py)
        return 1
    _log("flat layout — launching %s" % server_py)
    cmd = [sys.executable, server_py]
    if os.name == "nt":
        return subprocess.Popen(cmd, cwd=BASE_DIR).wait()
    os.execv(sys.executable, cmd)   # never returns


def main():
    _log("PingWatch bootstrap starting (base=%s)" % BASE_DIR)
    if not is_managed():
        return exec_flat() or 0
    while True:
        try:
            supervise()
        except KeyboardInterrupt:
            _log("bootstrap stopping (Ctrl+C)")
            return 0
        except Exception as e:
            _log("supervise loop crashed: %s: %s — restarting in %ss"
                 % (type(e).__name__, e, RESTART_BACKOFF))
            time.sleep(RESTART_BACKOFF)


if __name__ == "__main__":
    sys.exit(main())
