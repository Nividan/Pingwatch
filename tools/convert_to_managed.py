#!/usr/bin/env python3
"""
tools/convert_to_managed.py — convert a flat PingWatch install into the
base + releases/<version>/ + data/ layout that the managed-upgrade system
(bootstrap.py) expects.

Flat install (today):
    <base>/server.py core/ routes/ db/ ... pingwatch.db pingwatch.conf certs/ logs/ backup/

Managed layout (after conversion):
    <base>/bootstrap.py                         (stays — immutable outer launcher)
    <base>/linux/  <base>/windows/              (launch scaffolding stays in base)
    <base>/current.txt                          (-> <version>)
    <base>/releases/<version>/server.py core/ ... backup/*.py   (the code payload)
    <base>/data/pingwatch.db pingwatch.conf certs/ logs/ backup/configs backup/database

Safety:
  * DRY-RUN by default — prints the plan; pass --apply to actually move files.
  * Refuses if the base is already managed (idempotent / re-run safe).
  * Uses os.replace (atomic within a filesystem); aborts before any move if the
    target release/data dirs already contain conflicting entries.
  * Never deletes a source — only moves. A failure leaves the half-done move
    visible rather than silently dropping data.

Cross-platform (Windows + Linux): pure os/shutil, os.replace for moves.
Stdlib only. Safe to run while the service is STOPPED (do not convert a live
install — the DB files may be open).

Usage:
    python tools/convert_to_managed.py [BASE_DIR] [--apply]
    (BASE_DIR defaults to the parent of this tools/ directory.)
"""

import os
import re
import shutil
import sys

# ── Classification ────────────────────────────────────────────────────────────
# Persistent state — moved into <base>/data/. Prefixes catch the -wal/-shm/
# .pending_import sidecar files SQLite/imports create alongside the DB.
DATA_FILE_PREFIXES = ("pingwatch.db", "pingwatch_logs.db")
# ssh_known_hosts.txt is runtime state (the SSH TOFU host-key store, resolved by
# backup/engine.py as dirname(DB_PATH)/ssh_known_hosts.txt = DATA_ROOT). It must
# live in data/ so accumulated host keys survive a release swap.
DATA_FILES         = ("pingwatch.conf", "ssh_known_hosts.txt")
DATA_TOP_DIRS      = ("certs", "logs")
# backup/ is MIXED in a flat tree: code (engine.py, db_backup.py, scheduler.py)
# plus data (configs/, database/). These two subdirs are data; the rest is code.
BACKUP_DATA_SUBDIRS = ("configs", "database")

# Base scaffolding and managed-layout dirs that must NOT move into the release
# payload. linux/ and windows/ hold the launch scripts the OS service invokes
# from BASE; bootstrap.py is the immutable outer launcher.
BASE_KEEP = {
    "bootstrap.py", "linux", "windows",
    "releases", "data", "current.txt", "db_snapshots", "upgrade_state.json",
    "venv", ".git", ".github", "__pycache__", ".gitignore",
}


def detect_version(base):
    """Release dir name from APP_VERSION in core/app_state.py (regex parse — no
    import, so this works without the package on sys.path). Falls back to
    'unknown'."""
    p = os.path.join(base, "core", "app_state.py")
    try:
        with open(p, "r", encoding="utf-8") as f:
            txt = f.read()
        m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', txt, re.MULTILINE)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return "unknown"


def is_managed(base):
    """True if base already has the releases/ layout (a pointer or any release)."""
    rel = os.path.join(base, "releases")
    if os.path.exists(os.path.join(base, "current.txt")):
        return True
    return os.path.isdir(rel) and any(os.scandir(rel)) if os.path.isdir(rel) else False


def _is_data_file(name):
    if name in DATA_FILES:
        return True
    return any(name == p or name.startswith(p) for p in DATA_FILE_PREFIXES)


def plan_moves(base, version):
    """Compute (src_abs, dst_abs, kind) moves without touching disk.

    kind is 'data' or 'code'. backup/ is split: its data subdirs go to data/,
    the directory itself (code) goes to the release."""
    rel_dir  = os.path.join(base, "releases", version)
    data_dir = os.path.join(base, "data")
    moves = []
    for name in sorted(os.listdir(base)):
        if name in BASE_KEEP:
            continue
        src = os.path.join(base, name)
        if name == "backup" and os.path.isdir(src):
            # Split the mixed backup/ dir: data subdirs out first, code remainder
            # to the release.
            for sub in BACKUP_DATA_SUBDIRS:
                s = os.path.join(src, sub)
                if os.path.exists(s):
                    moves.append((s, os.path.join(data_dir, "backup", sub), "data"))
            moves.append((src, os.path.join(rel_dir, "backup"), "code"))
            continue
        if _is_data_file(name) or name in DATA_TOP_DIRS:
            moves.append((src, os.path.join(data_dir, name), "data"))
        else:
            moves.append((src, os.path.join(rel_dir, name), "code"))
    return moves, rel_dir, data_dir


def _move(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src, dst)


def convert(base, apply=False):
    base = os.path.abspath(base)
    if is_managed(base):
        print("[convert] already managed (releases/ or current.txt present) — nothing to do.")
        return 0
    version = detect_version(base)
    if version == "unknown":
        print("[convert] ERROR: could not read APP_VERSION from core/app_state.py — "
              "is this a PingWatch install root?", file=sys.stderr)
        return 1
    moves, rel_dir, data_dir = plan_moves(base, version)

    print(f"[convert] base    = {base}")
    print(f"[convert] version = {version}")
    print(f"[convert] release = {rel_dir}")
    print(f"[convert] data    = {data_dir}")
    print(f"[convert] {len(moves)} move(s):")
    for src, dst, kind in moves:
        print(f"    [{kind:4}] {os.path.relpath(src, base)}  ->  {os.path.relpath(dst, base)}")

    # Pre-flight: refuse if any destination already exists (avoid clobbering).
    conflicts = [dst for _, dst, _ in moves if os.path.exists(dst)]
    if conflicts:
        print("[convert] ERROR: destination(s) already exist — aborting:", file=sys.stderr)
        for c in conflicts:
            print("    " + c, file=sys.stderr)
        return 1

    if not apply:
        print("\n[convert] DRY RUN — no files moved. Re-run with --apply to perform the conversion.")
        print("[convert] IMPORTANT: stop the PingWatch service and take a DB backup first.")
        return 0

    os.makedirs(rel_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)
    for src, dst, _kind in moves:
        _move(src, dst)
    # Pointer last: until current.txt exists bootstrap treats the base as flat,
    # so a crash mid-move never leaves a half-managed install that won't boot.
    with open(os.path.join(base, "current.txt"), "w", encoding="utf-8") as f:
        f.write(version + "\n")
    print(f"\n[convert] OK — converted to managed layout, active release {version}.")
    print("[convert] Start the service; bootstrap.py will launch the release.")
    return 0


def revert(base, apply=False):
    """Undo a managed conversion: move the active release's code and all of
    data/ back to the base, returning a flat install. The systemd unit can keep
    pointing at bootstrap.py — it passes through to ./server.py in a flat layout
    — so no unit change is needed. Refuses if not managed; dry-run by default."""
    base = os.path.abspath(base)
    if not is_managed(base):
        print("[revert] not a managed install (no current.txt / releases/) — nothing to do.")
        return 0
    try:
        with open(os.path.join(base, "current.txt"), "r", encoding="utf-8") as f:
            current = f.read().strip()
    except Exception:
        current = ""
    rel_dir  = os.path.join(base, "releases", current)
    data_dir = os.path.join(base, "data")
    if not current or not os.path.isdir(rel_dir):
        print("[revert] ERROR: current.txt does not name a present release — aborting.",
              file=sys.stderr)
        return 1

    moves = []
    # Code: the active release's top-level entries go back to the base. (Code
    # entries move BEFORE data files so a directory like backup/ is recreated by
    # the code move, then its data subdirs merge back into it.)
    for name in sorted(os.listdir(rel_dir)):
        moves.append((os.path.join(rel_dir, name), os.path.join(base, name), "code"))
    # Data: file-granular so dirs (e.g. backup/) merge into the restored code.
    for dirpath, _dn, files in os.walk(data_dir):
        for fn in sorted(files):
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, data_dir)
            moves.append((src, os.path.join(base, rel), "data"))

    print(f"[revert] base    = {base}")
    print(f"[revert] release = {rel_dir}")
    print(f"[revert] {len(moves)} move(s) back to a flat layout:")
    for src, dst, kind in moves:
        print(f"    [{kind:4}] {os.path.relpath(src, base)}  ->  {os.path.relpath(dst, base)}")

    conflicts = [dst for _, dst, _ in moves if os.path.exists(dst)]
    if conflicts:
        print("[revert] ERROR: destination(s) already exist — aborting:", file=sys.stderr)
        for c in conflicts:
            print("    " + c, file=sys.stderr)
        return 1

    if not apply:
        print("\n[revert] DRY RUN — no files moved. Re-run with --apply to perform the revert.")
        print("[revert] IMPORTANT: stop the PingWatch service first.")
        return 0

    for src, dst, _kind in moves:
        _move(src, dst)
    # Remove managed-layout artifacts (keep bootstrap.py / linux/ / windows/).
    for name in ("current.txt", "upgrade_state.json", "server_health.json", "update_report.json"):
        try:
            os.remove(os.path.join(base, name))
        except OSError:
            pass
    for d in ("releases", "data", "db_snapshots"):
        shutil.rmtree(os.path.join(base, d), ignore_errors=True)
    print("\n[revert] OK — reverted to a flat layout. Restart the service "
          "(bootstrap.py passes through to ./server.py).")
    return 0


def _find_base(explicit):
    """Resolve the install base. Explicit arg wins. Otherwise walk up from this
    script looking for bootstrap.py / current.txt — so the tool works whether it
    lives at <base>/tools/ (flat) or <base>/releases/<ver>/tools/ (managed)."""
    if explicit:
        return explicit
    d = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    probe = d
    for _ in range(4):
        if (os.path.exists(os.path.join(probe, "bootstrap.py"))
                or os.path.exists(os.path.join(probe, "current.txt"))):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return d


def main(argv):
    flags = {"--apply", "--revert-managed"}
    args = [a for a in argv if a not in flags]
    apply = "--apply" in argv
    do_revert = "--revert-managed" in argv
    base = _find_base(args[0] if args else None)
    return revert(base, apply=apply) if do_revert else convert(base, apply=apply)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
