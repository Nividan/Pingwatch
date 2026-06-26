#!/usr/bin/env python3
"""
tools/build_image.py — build a signed PingWatch upgrade image.

Produces ``pingwatch-image-<version>.zip`` containing:

    manifest.json     version, app_version, payload_sha256, min_upgrade_from, created_at
    manifest.sig      detached Ed25519 signature (hex) over manifest.json's bytes
    payload/...       the full source tree, minus runtime state and VCS/build junk

The image is the unit an admin uploads in the UI to upgrade an air-gapped or
no-git server. The matching server verifies the signature against its baked-in
public key (core/upgrade.RELEASE_PUBKEY_HEX) before running any of this code, so
the signing PRIVATE key must stay off the repo and only on the build machine.

Run on the BUILD machine (has the source + the private key), NOT on the server:

    PW_RELEASE_SIGNING_KEY=<hex>  python tools/build_image.py [--out DIR]
    python tools/build_image.py --key /path/to/release_ed25519.key [--out DIR]

Stdlib + cryptography (already a project dependency). The payload digest and the
signature use core/upgrade.py helpers so the builder and verifier never diverge.
"""

import argparse
import ast
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from core import upgrade as up          # payload_digest, sign_manifest, MIN_UPGRADE_FROM
from core import app_state

# Reproducible zip timestamp (DOS epoch) so the archive bytes are stable.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# Runtime state and VCS/build junk that must NOT ship in an image. Mirrors the
# convert_to_managed.py classification and the release-packaging exclusion list.
_EXCLUDE_NAMES = {
    ".git", ".github", ".claude", "venv", "__pycache__",
    "releases", "data", "db_snapshots", "current.txt", "upgrade_state.json",
    "pingwatch.conf", "certs", "logs", "ssh_known_hosts.txt",
    "CLAUDE.md", "MIGRATION_NOTES.md", ".gitignore",
}
_EXCLUDE_DB_PREFIXES = ("pingwatch.db", "pingwatch_logs.db")
_EXCLUDE_EXTS = (".pyc", ".pyo", ".pem", ".key", ".crt")
# backup/ is mixed: ship its code, never its data subdirs.
_BACKUP_DATA_SUBDIRS = {os.path.join("backup", "configs"), os.path.join("backup", "database")}


def _excluded(rel):
    """True if the repo-relative path should be left out of the payload."""
    parts = rel.replace("\\", "/").split("/")
    top = parts[0]
    if top in _EXCLUDE_NAMES:
        return True
    base = parts[-1]
    if any(base.startswith(p) for p in _EXCLUDE_DB_PREFIXES):
        return True
    if os.path.splitext(base)[1] in _EXCLUDE_EXTS:
        return True
    norm = rel.replace("\\", "/")
    if any(norm == d.replace("\\", "/") or norm.startswith(d.replace("\\", "/") + "/")
           for d in _BACKUP_DATA_SUBDIRS):
        return True
    return False


def _stage_payload(dst_payload):
    """Copy the included source tree into dst_payload, returning the file count."""
    n = 0
    for dirpath, dirnames, filenames in os.walk(_REPO_ROOT):
        dirnames[:] = [d for d in dirnames
                       if not _excluded(os.path.relpath(os.path.join(dirpath, d), _REPO_ROOT))]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, _REPO_ROOT)
            if _excluded(rel):
                continue
            out = os.path.join(dst_payload, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(full, out)
            n += 1
    return n


def _git(args):
    """Run a git command in the repo root; return stripped stdout or None."""
    try:
        out = subprocess.run(["git", "-C", _REPO_ROOT] + args,
                             capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except Exception:
        return None


def _build_identity(app_ver, digest):
    """Human build version string + git metadata for the manifest.

    Convention: ``<app>.<build>`` where the build number is the git commit count
    (monotonic, unique per commit), e.g. ``1.5.930``. The exact commit hash is
    kept in the manifest (``git_commit``) for traceability, not in the name. An
    uncommitted (dirty) tree appends ``.dev<hash6>`` — a content tiebreaker so
    repeated dirty builds don't collide, and a visible "not from a clean commit"
    flag. Falls back to the payload-hash id when git isn't available.

    Returns (version, meta) where meta carries build/git_commit/git_dirty.
    """
    gitcount = _git(["rev-list", "--count", "HEAD"])
    short    = _git(["rev-parse", "--short=7", "HEAD"])
    if not gitcount:
        return "%s+%s" % (app_ver, digest[:12]), \
               {"build": None, "git_commit": short, "git_dirty": None}
    dirty = bool(_git(["status", "--porcelain"]))
    version = "%s.%s" % (app_ver, gitcount)
    if dirty:
        version += ".dev%s" % digest[:6]
    return version, {"build": int(gitcount), "git_commit": short, "git_dirty": dirty}


def _load_key(args):
    if args.key:
        with open(args.key, "r", encoding="utf-8") as f:
            return f.read().strip()
    env = os.environ.get("PW_RELEASE_SIGNING_KEY")
    if env:
        return env.strip()
    sys.exit("[build-image] ERROR: no signing key. Pass --key FILE or set "
             "PW_RELEASE_SIGNING_KEY (hex).")


def _zip_dir(zf, root, arc_prefix):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            arc = arc_prefix + "/" + os.path.relpath(full, root).replace(os.sep, "/")
            zi = zipfile.ZipInfo(arc, date_time=_ZIP_EPOCH)
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(full, "rb") as f:
                zf.writestr(zi, f.read())


def _check_py39_annotations(payload_root):
    """Refuse to ship code that crashes on the supported Python floor (3.8/3.9).

    PEP 604 'X | Y' annotations are evaluated at import on <3.10 UNLESS the
    module has `from __future__ import annotations`. Such a module imports fine
    on the 3.12 build/prod box but hard-crashes a 3.9 server at startup — a
    class of break that has shipped twice. Scan the staged payload with the AST
    (compileall can't catch it — it's import-time, not syntax) and abort the
    build if any module uses a union annotation without the future-import."""
    offenders = []
    for dirpath, _dirs, files in os.walk(payload_root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, payload_root)
            try:
                tree = ast.parse(open(full, encoding="utf-8").read())
            except Exception:
                continue  # syntax errors are caught by the server's compileall gate
            has_future = any(
                isinstance(n, ast.ImportFrom) and n.module == "__future__"
                and any(a.name == "annotations" for a in n.names)
                for n in ast.walk(tree))
            if has_future:
                continue

            def _ann_union(node):
                return any(isinstance(s, ast.BinOp) and isinstance(s.op, ast.BitOr)
                           for s in ast.walk(node))
            lines = set()
            for n in ast.walk(tree):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    anns = [a.annotation for a in
                            (n.args.posonlyargs + n.args.args + n.args.kwonlyargs)
                            if a.annotation]
                    for extra in (n.args.vararg, n.args.kwarg):
                        if extra and extra.annotation:
                            anns.append(extra.annotation)
                    if n.returns:
                        anns.append(n.returns)
                    for a in anns:
                        if _ann_union(a):
                            lines.add(a.lineno)
                elif isinstance(n, ast.AnnAssign) and n.annotation and _ann_union(n.annotation):
                    lines.add(n.annotation.lineno)
            if lines:
                offenders.append("%s (lines %s)" % (rel, ",".join(map(str, sorted(lines)))))
    if offenders:
        sys.exit("[build-image] ABORT — PEP 604 'X | Y' annotations without "
                 "`from __future__ import annotations` (crashes on Python 3.8/3.9):\n  "
                 + "\n  ".join(offenders))


def build(out_dir, key_hex, created_at, min_from=None):
    staging = tempfile.mkdtemp(prefix="pwimg_")
    try:
        payload = os.path.join(staging, up.PAYLOAD_DIR)
        os.makedirs(payload)
        count = _stage_payload(payload)
        _check_py39_annotations(payload)   # fail the build before signing if 3.9-incompatible
        digest = up.payload_digest(payload)
        app_ver = app_state.APP_VERSION
        version, gitmeta = _build_identity(app_ver, digest)

        manifest = {
            "schema": 1,
            "version": version,
            "app_version": app_ver,
            "payload_sha256": digest,
            "min_upgrade_from": (min_from or up.MIN_UPGRADE_FROM),
            "created_at": created_at,
            "files": count,
            "build": gitmeta["build"],
            "git_commit": gitmeta["git_commit"],
            "git_dirty": gitmeta["git_dirty"],
        }
        # Canonical bytes: sort_keys so the signed bytes are reproducible.
        manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
        sig_hex = up.sign_manifest(manifest_bytes, key_hex)

        os.makedirs(out_dir, exist_ok=True)
        out_zip = os.path.join(out_dir, "pingwatch-image-%s.zip" % version)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(zipfile.ZipInfo(up.MANIFEST_NAME, date_time=_ZIP_EPOCH), manifest_bytes)
            zf.writestr(zipfile.ZipInfo(up.SIG_NAME, date_time=_ZIP_EPOCH), sig_hex + "\n")
            _zip_dir(zf, payload, up.PAYLOAD_DIR)
        with open(out_zip, "wb") as f:
            f.write(buf.getvalue())
        return out_zip, version, digest, count, gitmeta
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main(argv):
    ap = argparse.ArgumentParser(description="Build a signed PingWatch upgrade image.")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(_REPO_ROOT), "pingwatch-images"),
                    help="output directory (default: a sibling 'pingwatch-images' dir, outside the repo)")
    ap.add_argument("--key", help="path to the Ed25519 private key file (hex)")
    ap.add_argument("--created-at", default=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    help="manifest timestamp (default: now, UTC)")
    ap.add_argument("--min-from", default=up.MIN_UPGRADE_FROM,
                    help="minimum app_version a server may upgrade FROM to install this image "
                         "(the upgrade-path floor; default: %s)" % up.MIN_UPGRADE_FROM)
    args = ap.parse_args(argv)

    key_hex = _load_key(args)
    out_zip, version, digest, count, gitmeta = build(
        args.out, key_hex, args.created_at, min_from=args.min_from)
    print("[build-image] version      : %s" % version)
    if gitmeta.get("build") is not None:
        _commit = ("g" + gitmeta["git_commit"]) if gitmeta.get("git_commit") else "(no commit)"
        print("[build-image] build / commit: %s / %s%s" % (
            gitmeta["build"], _commit,
            "  (DIRTY — uncommitted changes)" if gitmeta["git_dirty"] else ""))
    else:
        print("[build-image] build / commit: (git unavailable — using payload-hash id)")
    print("[build-image] upgrade from : >= %s" % args.min_from)
    print("[build-image] files        : %d" % count)
    print("[build-image] sha256       : %s" % digest)
    print("[build-image] written      : %s" % out_zip)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
