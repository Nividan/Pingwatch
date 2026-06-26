"""
core/upgrade.py — managed server self-upgrade: image verification, DB snapshots,
upgrade state machine, and the probation watcher.

This module is PAYLOAD (it ships inside a release and runs as part of the
server). It is the rich counterpart to the immutable BASE launcher bootstrap.py:
where bootstrap owns the at-boot pointer swap and rollback file operations (it
runs when no server holds the DB), this module — running inside the live server
— verifies an uploaded image, snapshots the DB, stages the new release, and then
watches the next boot's health to decide commit vs. rollback.

Trust model: an uploaded image is arbitrary code that will run as the server
user, so it must be authentic. Every image carries an Ed25519 signature over its
manifest; the server verifies it against RELEASE_PUBKEY_HEX baked in below. The
matching PRIVATE key lives only on the release build machine (outside the repo)
and signs images via tools/build_image.py. Verification always uses the running
server's baked key, so a malicious image cannot substitute its own.

Stages of the verify chain (all must pass before anything is staged):
    signature  ->  payload sha256  ->  version compatibility  ->  syntax (compileall)

The state machine + probation/rollback live in later sections (Phase 4).
"""

import hashlib
import json
import os
import shutil
import time

from core.config import DATA_ROOT
from core.logger import log

# ── Release signing trust (hybrid: vendor key + operator-registered keys) ─────
# VENDOR_PUBKEY_HEX is the baked-in default authority — the project's own Ed25519
# public key. Its private half signs official releases on the build machine and
# is NEVER in the repo. Rotating it is a two-release migration: ship an image
# (signed by the OLD key) whose code bakes in the NEW key, deploy, then sign with
# the NEW key.
VENDOR_PUBKEY_HEX = "c6defa2275f809cc95856e90a346a9c3bcb1da317e169f53841ba927067041a4"
RELEASE_PUBKEY_HEX = VENDOR_PUBKEY_HEX   # back-compat alias

# An operator who self-hosts can ADD their own trusted public key(s) so they can
# build and install their own images alongside (or instead of) the vendor's.
# These live in the instance's data dir and are managed by tools/trust_key.py —
# adding a key requires FILESYSTEM access on the box, deliberately NOT a web
# action, so a compromised web-admin session cannot widen who may push code.
TRUSTED_KEYS_FILE = os.path.join(DATA_ROOT, "trusted_upgrade_keys.json")


def trusted_pubkeys():
    """Every public key whose signature this server will accept on an image: the
    baked vendor key plus any operator keys registered in TRUSTED_KEYS_FILE.
    De-duplicated, lower-cased; malformed entries are ignored."""
    keys = []
    if VENDOR_PUBKEY_HEX:
        keys.append(VENDOR_PUBKEY_HEX.strip().lower())
    try:
        with open(TRUSTED_KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        entries = data if isinstance(data, list) else (data.get("keys") or [])
        for e in entries:
            hx = (e.get("pubkey") if isinstance(e, dict) else e) or ""
            hx = str(hx).strip().lower()
            if len(hx) == 64:
                keys.append(hx)
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("trusted_upgrade_keys.json unreadable: %s", type(e).__name__)
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out

# Oldest app_version this build will accept an upgrade *from*. Guards against
# skipping a required intermediate migration. Bump when a release drops support
# for upgrading directly from very old schemas.
MIN_UPGRADE_FROM = "1.4"

MANIFEST_NAME = "manifest.json"
SIG_NAME      = "manifest.sig"
PAYLOAD_DIR   = "payload"

# BASE-layout paths (the persistent state root is <base>/data, so BASE is its
# parent). PW_BASE_DIR is exported by bootstrap.py / launcher.pyw; fall back to
# the parent of DATA_ROOT, which holds under the managed layout.
BASE_DIR      = os.environ.get("PW_BASE_DIR") or os.path.dirname(DATA_ROOT)
RELEASES_DIR  = os.path.join(BASE_DIR, "releases")
SNAPSHOTS_DIR = os.path.join(BASE_DIR, "db_snapshots")
STATE_PATH    = os.path.join(BASE_DIR, "upgrade_state.json")
POINTER_PATH  = os.path.join(BASE_DIR, "current.txt")
HEALTH_PATH   = os.path.join(BASE_DIR, "server_health.json")
REPORT_PATH   = os.path.join(BASE_DIR, "update_report.json")


class ImageError(Exception):
    """Image failed verification or staging. Messages are deliberately CURATED
    (no paths, SQL, or stack detail) so the route may return them to the admin —
    they are the actionable reason an upload was rejected. Generic exceptions
    (which may leak internals) still go through h._error, never str(e)."""


# ── Deterministic payload digest (shared by builder and verifier) ─────────────
def payload_digest(payload_root):
    """SHA-256 over every file under ``payload_root``, order-independent and
    metadata-independent: files are sorted by POSIX-relative path and each
    contributes ``relpath \\0 content \\0``. __pycache__ is ignored. This is the
    single source of truth for both tools/build_image.py (manifest field) and
    verification, so the two cannot drift."""
    h = hashlib.sha256()
    entries = []
    for dirpath, dirnames, filenames in os.walk(payload_root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, payload_root).replace(os.sep, "/")
            entries.append((rel, full))
    for rel, full in sorted(entries):
        h.update(rel.encode("utf-8") + b"\0")
        with open(full, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()


# ── Signature ────────────────────────────────────────────────────────────────
def sign_manifest(manifest_bytes, private_key_hex):
    """Return the hex Ed25519 signature of ``manifest_bytes``. Used by the
    builder; kept here so signing and verifying share one implementation."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex.strip()))
    return key.sign(manifest_bytes).hex()


def _verify_signature(manifest_bytes, sig_hex):
    """Accept the image if its signature verifies against ANY trusted key
    (vendor or operator-registered). Reject if none match."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    try:
        sig = bytes.fromhex(sig_hex.strip())
    except ValueError:
        raise ImageError("malformed signature")
    candidates = trusted_pubkeys()
    if not candidates:
        raise ImageError("no trusted signing keys configured")
    for hexk in candidates:
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(hexk)).verify(sig, manifest_bytes)
            return   # a trusted key verified it
        except InvalidSignature:
            continue
        except Exception:
            continue   # malformed trusted entry — try the next
    raise ImageError("signature is not from any trusted key")


# ── Version compatibility ─────────────────────────────────────────────────────
def _ver_tuple(v):
    """Loose dotted-version parse: '1.5' -> (1,5); the build hash after '+' is
    ignored. Non-numeric parts degrade to 0 so a bad value can't crash the gate."""
    head = str(v or "").split("+", 1)[0]
    out = []
    for part in head.split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out) or (0,)


def _check_compat(manifest, running_app_version):
    target_app = manifest.get("app_version") or ""
    min_from = manifest.get("min_upgrade_from") or MIN_UPGRADE_FROM
    if _ver_tuple(running_app_version) < _ver_tuple(min_from):
        raise ImageError(
            "image requires upgrading from >= %s, but this server is %s"
            % (min_from, running_app_version))
    if not target_app:
        raise ImageError("image manifest missing app_version")


# ── Full image verification (on an already-extracted staging dir) ─────────────
def verify_staged_image(staging_dir, running_app_version):
    """Verify an extracted image directory (manifest.json + manifest.sig +
    payload/). Returns the parsed manifest on success; raises ImageError on any
    failure. Order: signature -> payload sha256 -> version compatibility. The
    syntax (compileall) gate is applied by the caller against the payload it is
    about to stage, mirroring deploy.sh / the systemd ExecStartPre."""
    man_path = os.path.join(staging_dir, MANIFEST_NAME)
    sig_path = os.path.join(staging_dir, SIG_NAME)
    payload  = os.path.join(staging_dir, PAYLOAD_DIR)
    if not (os.path.isfile(man_path) and os.path.isfile(sig_path) and os.path.isdir(payload)):
        raise ImageError("image missing manifest.json, manifest.sig, or payload/")

    with open(man_path, "rb") as f:
        manifest_bytes = f.read()
    with open(sig_path, "r", encoding="utf-8") as f:
        sig_hex = f.read().strip()

    # 1) signature over the exact manifest bytes
    _verify_signature(manifest_bytes, sig_hex)

    try:
        manifest = json.loads(manifest_bytes)
    except Exception:
        raise ImageError("manifest.json is not valid JSON")

    # 2) payload integrity (covers tamper/corruption AND any added file, since
    #    the digest re-walks the whole payload tree)
    want = str(manifest.get("payload_sha256") or "").lower()
    got = payload_digest(payload)
    if not want or got != want:
        raise ImageError("payload checksum mismatch")

    # 3) version compatibility
    _check_compat(manifest, running_app_version)

    return manifest


def manifest_version(manifest):
    """The release directory name for this image (e.g. '1.6+ab12cd34ef00')."""
    return str(manifest.get("version") or "").strip()


# ── Managed-layout detection ──────────────────────────────────────────────────
def is_managed():
    """True when the server is running under the releases/<version>/ layout (the
    upgrade route requires it — there is nowhere to stage a release otherwise)."""
    return bool(os.environ.get("PW_RELEASE")) and os.path.isdir(RELEASES_DIR)


def current_release():
    return os.environ.get("PW_RELEASE") or ""


# ── Upgrade state machine I/O (shared contract with bootstrap.py) ─────────────
def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, STATE_PATH)


def read_report():
    """Outcome of the last upgrade, written by bootstrap.py. Used by the status
    endpoint so the UI can show committed / rolled_back + reason."""
    try:
        with open(os.path.join(BASE_DIR, "update_report.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ── DB snapshot (pre-upgrade) ─────────────────────────────────────────────────
def _sqlite_snapshot(src_path, dest_path):
    """WAL-safe SQLite copy into a fresh snapshot file. Connections are closed
    BEFORE returning so the file is not held open — unlike backup/db_backup.py's
    helper (which os.replace()s a still-open temp, fine on Linux but fails on
    Windows). The snapshot dir is freshly created and unread, so writing the
    destination directly is safe and avoids the locked-replace entirely."""
    import sqlite3
    src = sqlite3.connect(src_path, timeout=30)
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return os.path.getsize(dest_path)


def _snapshot_logs_enabled():
    """The logs schema (sensor_samples, traps, …) is high-volume and its
    migrations are additive, so rollback treats a logs restore as best-effort.
    By default we therefore do NOT snapshot it — a PG logs dump can be hundreds
    of MB and add tens of seconds to EVERY upgrade. Set PW_UPGRADE_SNAPSHOT_LOGS=1
    to include it (e.g. before a release with a breaking logs-schema change)."""
    return os.environ.get("PW_UPGRADE_SNAPSHOT_LOGS", "").strip().lower() in ("1", "true", "yes")


def create_snapshot(upgrade_id):
    """Snapshot the DB(s) into db_snapshots/<upgrade_id>/ so a rollback can undo
    the new release's forward migration. Returns (snapshot_id, backend). PG reuses
    the pg_dump helper from backup/db_backup.py (a subprocess, no lock issue).
    The main schema is always snapshotted; logs only when explicitly enabled."""
    snap = os.path.join(SNAPSHOTS_DIR, upgrade_id)
    os.makedirs(snap, exist_ok=True)
    want_logs = _snapshot_logs_enabled()
    from db.backend import is_pg
    if is_pg():
        from db.backend import load_config
        from backup.db_backup import _backup_pg_schema
        cfg = load_config()
        _backup_pg_schema(cfg, "main", os.path.join(snap, "main.sql"), "snapshot main", log)
        if want_logs:
            try:
                _backup_pg_schema(cfg, "logs", os.path.join(snap, "logs.sql"), "snapshot logs", log)
            except Exception as e:
                log.warning("upgrade snapshot: logs schema skipped (%s)", type(e).__name__)
        backend = "postgresql"
    else:
        from core.config import DB_PATH, LOGS_DB_PATH
        _sqlite_snapshot(DB_PATH, os.path.join(snap, "main.sqlite"))
        if want_logs and os.path.exists(LOGS_DB_PATH):
            _sqlite_snapshot(LOGS_DB_PATH, os.path.join(snap, "logs.sqlite"))
        backend = "sqlite"
    with open(os.path.join(snap, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"backend": backend, "upgrade_id": upgrade_id}, f)
    log.info("upgrade snapshot created: %s (%s)", upgrade_id, backend)
    return upgrade_id, backend


# ── Safe extraction (zip-slip guarded) ────────────────────────────────────────
def extract_zip(zip_bytes, dest_dir):
    """Extract a zip from memory into dest_dir, rejecting any entry that would
    escape it (zip-slip). Mirrors the agent's _extract_release guard."""
    import io
    import zipfile
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.abspath(dest_dir)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for m in zf.namelist():
            target = os.path.abspath(os.path.join(dest_dir, m))
            if target != base and not target.startswith(base + os.sep):
                raise ImageError("unsafe path in image: %s" % m)
        zf.extractall(dest_dir)


def _compile_payload(payload_dir):
    import compileall
    if not compileall.compile_dir(payload_dir, quiet=1, maxlevels=20):
        raise ImageError("staged release failed the syntax check")


def _free_bytes(path):
    try:
        return shutil.disk_usage(path).free
    except Exception:
        return None


def stage_image(zip_bytes, upgrade_id, running_app_version):
    """Verify an uploaded image, stage its payload as releases/<version>/, snapshot
    the DB, and arm the upgrade state machine (phase=staged). Returns the manifest.
    Raises ImageError on any failure. Does NOT restart — the caller triggers that.

    The on-disk commit order matters for crash safety: payload is assembled in a
    temp dir and atomically moved into releases/ before the DB snapshot and the
    state write, and the state file (which bootstrap acts on) is written LAST."""
    if not is_managed():
        raise ImageError("server is not in the managed (releases/) layout; "
                         "convert it before uploading an image")
    os.makedirs(RELEASES_DIR, exist_ok=True)
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

    # Rough free-space guard: need room for payload (~unzipped) + a DB snapshot.
    free = _free_bytes(BASE_DIR)
    if free is not None and free < 3 * len(zip_bytes) + (64 << 20):
        raise ImageError("not enough free disk space to stage the image safely")

    staging = os.path.join(RELEASES_DIR, ".staging-" + upgrade_id)
    if os.path.isdir(staging):
        shutil.rmtree(staging, ignore_errors=True)
    try:
        extract_zip(zip_bytes, staging)
        manifest = verify_staged_image(staging, running_app_version)
        version = manifest_version(manifest)
        if not version:
            raise ImageError("manifest missing version")
        payload = os.path.join(staging, PAYLOAD_DIR)
        _compile_payload(payload)

        if version == current_release():
            raise ImageError("this image's version is already running")

        rel_dir = os.path.join(RELEASES_DIR, version)
        if os.path.isdir(rel_dir):
            shutil.rmtree(rel_dir, ignore_errors=True)
        os.replace(payload, rel_dir)            # atomic publish of the new release
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    # Snapshot AFTER the release is safely on disk, state written LAST so a crash
    # before this point leaves an un-armed (ignored) staging dir, not a half-swap.
    snap_id, backend = create_snapshot(upgrade_id)
    st = {
        "phase": "staged",
        "target": version,
        "previous": current_release(),
        "upgrade_id": upgrade_id,
        "db_snapshot": snap_id,
        "db_backend": backend,
        "expected_version": manifest.get("app_version"),
        "probation_window": int(manifest.get("probation_window") or 120),
        "staged_at": time.time(),
    }
    save_state(st)
    log.info("upgrade staged: %s -> %s (snapshot %s)", current_release(), version, snap_id)
    return manifest


# ── Health beacon (probation signal read by bootstrap.py) ─────────────────────
def _write_health(consecutive_good):
    try:
        tmp = HEALTH_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": current_release(), "ready": True,
                       "consecutive_good": consecutive_good, "ts": time.time()}, f)
        os.replace(tmp, HEALTH_PATH)
    except Exception:
        pass


def start_health_beacon(interval=3.0):
    """Background thread that publishes a health beacon bootstrap polls during
    probation. Only meaningful under the managed layout; a no-op otherwise."""
    if not is_managed():
        return
    import threading

    def _loop():
        good = 0
        import core.app_state as app_state
        while True:
            try:
                if getattr(app_state, "ready", False):
                    good += 1
                    _write_health(good)
                else:
                    good = 0
            except Exception:
                good = 0
            time.sleep(interval)

    t = threading.Thread(target=_loop, name="upgrade-health-beacon", daemon=True)
    t.start()


# ── Managed restart (hand back to the bootstrap supervisor) ───────────────────
def request_restart():
    """Exit so the bootstrap supervisor respawns us — it then applies a staged
    swap (phase=staged -> probation) or simply relaunches. Flushes the sample
    buffer first; a hard exit is acceptable (a managed swap loses at most the
    in-memory sample window, same as the DB-import restart)."""
    try:
        from db import db_flush_samples
        db_flush_samples()
    except Exception:
        pass
    try:
        import core.app_state as _as
        if getattr(_as, "tray_icon", None) is not None:
            _as.tray_icon.stop()
    except Exception:
        pass
    log.info("upgrade: exiting for bootstrap to apply the staged release")
    os._exit(0)
