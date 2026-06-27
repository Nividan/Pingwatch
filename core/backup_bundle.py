"""
core/backup_bundle.py — Full, restorable backup bundles (DBs + secrets) with
optional passphrase encryption.

A *bundle* is the unit that makes a server restorable on a fresh box. The inner
artifact is the same ZIP the import endpoint already understands:

    pingwatch_main.{db|sql}     both DBs (SQLite snapshot / pg_dump per schema)
    pingwatch_logs.{db|sql}
    secrets/backup_enc.key      the Fernet key (without it, every encrypted
                                credential — LDAP/SMTP/SAML/device backups — is
                                dead ciphertext on the new server)
    secrets/certs/...           TLS cert + key (so HTTPS comes up identically)
    secrets/pingwatch.conf      backend/PG connection config
    manifest.json               version, backend, and what secrets are included

Because that inner ZIP now carries the crown jewels in the clear, the bundle is
optionally wrapped in an AEAD container (``PWBK1``) keyed by a passphrase:

    b"PWBK1\\n" | u32 header_len | header_json (AAD) | AES-256-GCM ciphertext

The key is derived with **Argon2id** where the platform's ``cryptography`` is
new enough (>= 44), and falls back to **Scrypt** otherwise — the header records
which, so decryption is deterministic and an air-gapped/old box never hits an
ImportError mid-restore. A leaked ``.pwbk`` is ciphertext-only; the passphrase
must be escrowed by the operator (it is NOT inside the bundle).

This module is the single source of truth for both the manual export route and
the scheduled backup job, so the on-disk format never diverges between them.
"""
from __future__ import annotations

import io
import json
import os
import struct
import subprocess
import tempfile
import time
import zipfile

from core.config import (
    DB_PATH, LOGS_DB_PATH, DATA_ROOT, CERTS_DIR, SECRETS_DIR,
)
from core import app_state

# ── Container constants ──────────────────────────────────────────────────────
MAGIC          = b"PWBK1\n"          # PingWatch backup container, format 1
_KEY_LEN       = 32                  # AES-256
_NONCE_LEN     = 12                  # AES-GCM standard nonce
_CONF_PATH     = os.path.join(DATA_ROOT, "pingwatch.conf")
_BACKUP_KEY    = os.path.join(SECRETS_DIR, "backup_enc.key")

# Inner-zip entry prefixes for the bundled secrets.
_SEC_KEY_ARC   = "secrets/backup_enc.key"
_SEC_CERTS_DIR = "secrets/certs"
_SEC_CONF_ARC  = "secrets/pingwatch.conf"


# ── Raw DB dump helpers (shared with routes/export.py) ───────────────────────

def sqlite_backup_bytes(src_path) -> bytes:
    """Return a WAL-safe binary snapshot of a SQLite database."""
    import sqlite3 as _sq3
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src = _sq3.connect(str(src_path))
        try:
            with _sq3.connect(tmp) as dst:
                src.backup(dst)
        finally:
            src.close()
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def pg_dump_bytes(schema: str) -> bytes:
    """Run pg_dump for one schema and return the SQL dump as bytes."""
    from db.backend import get_config, pg_env as _pg_env
    cfg = get_config()
    fd, tmp = tempfile.mkstemp(suffix=".sql")
    os.close(fd)
    pgpass = None
    try:
        env, pgpass = _pg_env(cfg)
        cmd = [
            'pg_dump',
            '-h', cfg['pg_host'],
            '-p', str(cfg['pg_port']),
            '-U', cfg['pg_user'],
            '-d', cfg['pg_database'],
            '--schema', schema,
            '--no-password',
            '-f', tmp,
        ]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"pg_dump exited {result.returncode}")
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        if pgpass:
            try:
                os.unlink(pgpass)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── Secret collection ────────────────────────────────────────────────────────

def _add_secrets(zf: zipfile.ZipFile) -> dict:
    """Add the Fernet key, TLS certs and pingwatch.conf to the inner zip.

    Returns a manifest fragment describing what was actually included (files
    that don't exist on this box are simply skipped — e.g. a SQLite install
    has no pingwatch.conf, a never-used-TLS box has no certs)."""
    info = {"key": False, "conf": False, "certs": []}

    if os.path.isfile(_BACKUP_KEY):
        with open(_BACKUP_KEY, "rb") as f:
            zf.writestr(_SEC_KEY_ARC, f.read())
        info["key"] = True

    if os.path.isfile(_CONF_PATH):
        with open(_CONF_PATH, "rb") as f:
            zf.writestr(_SEC_CONF_ARC, f.read())
        info["conf"] = True

    if os.path.isdir(CERTS_DIR):
        for dirpath, _dirs, files in os.walk(CERTS_DIR):
            for fn in sorted(files):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, CERTS_DIR).replace(os.sep, "/")
                arc = _SEC_CERTS_DIR + "/" + rel
                with open(full, "rb") as f:
                    zf.writestr(arc, f.read())
                info["certs"].append(rel)
    return info


# ── Inner bundle (plain ZIP) ─────────────────────────────────────────────────

def build_inner_zip() -> bytes:
    """Assemble the full bundle ZIP (both DBs + secrets + manifest).

    Identical DB-entry layout to the historical export bundle so the existing
    importer keeps working; the ``secrets/`` entries and manifest ``secrets``
    block are additive."""
    from db.backend import is_pg

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if is_pg():
            main_data = pg_dump_bytes('main')
            logs_data = pg_dump_bytes('logs')
            zf.writestr("pingwatch_main.sql", main_data)
            if logs_data:
                zf.writestr("pingwatch_logs.sql", logs_data)
            manifest = {
                "version":     1,
                "app_version": app_state.APP_VERSION,
                "backend":     "postgresql",
                "created_at":  time.strftime("%Y-%m-%dT%H:%M:%S"),
                "has_main":    True,
                "has_logs":    bool(logs_data),
            }
        else:
            import sqlite3
            main_data = sqlite_backup_bytes(DB_PATH)
            logs_data = sqlite_backup_bytes(LOGS_DB_PATH) if os.path.exists(LOGS_DB_PATH) else b""
            zf.writestr("pingwatch_main.db", main_data)
            if logs_data:
                zf.writestr("pingwatch_logs.db", logs_data)

            # Schema versions (best-effort; default to 1)
            sv_main = 1
            try:
                _mc = sqlite3.connect(DB_PATH)
                sv_main = (_mc.execute(
                    "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
                ).fetchone() or (1,))[0]
                _mc.close()
            except Exception:
                sv_main = 1
            sv_logs = 1
            if logs_data:
                try:
                    _lc = sqlite3.connect(LOGS_DB_PATH)
                    sv_logs = (_lc.execute(
                        "SELECT version FROM logs_schema_version ORDER BY version DESC LIMIT 1"
                    ).fetchone() or (1,))[0]
                    _lc.close()
                except Exception:
                    sv_logs = 1
            manifest = {
                "version":     1,
                "app_version": app_state.APP_VERSION,
                "backend":     "sqlite",
                "created_at":  time.strftime("%Y-%m-%dT%H:%M:%S"),
                "schema_main": sv_main,
                "schema_logs": sv_logs,
                "has_main":    True,
                "has_logs":    bool(logs_data),
            }

        manifest["secrets"] = _add_secrets(zf)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2).encode())

    return buf.getvalue()


# ── KDF abstraction (Argon2id preferred, Scrypt fallback) ────────────────────

def _argon2id_available() -> bool:
    try:
        from cryptography.hazmat.primitives.kdf.argon2 import Argon2id  # noqa: F401
        return True
    except Exception:
        return False


def _new_kdf_params() -> dict:
    """Choose the strongest KDF this platform's cryptography supports."""
    salt = os.urandom(16).hex()
    if _argon2id_available():
        # ~64 MiB, t=3, p=4 — interactive-but-stout defaults.
        return {"kdf": "argon2id", "salt": salt, "length": _KEY_LEN,
                "iterations": 3, "lanes": 4, "memory_cost": 65536}
    # Scrypt N=2**15 (~32 MiB) — available since cryptography 2.x.
    return {"kdf": "scrypt", "salt": salt, "length": _KEY_LEN,
            "n": 32768, "r": 8, "p": 1}


def _derive_key(passphrase: str, params: dict) -> bytes:
    salt = bytes.fromhex(params["salt"])
    length = int(params.get("length", _KEY_LEN))
    kind = params.get("kdf")
    if kind == "argon2id":
        try:
            from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
        except Exception:
            raise RuntimeError(
                "This bundle was encrypted with Argon2id, which needs "
                "cryptography>=44. Upgrade the 'cryptography' package on this "
                "server to restore it, or re-export with a Scrypt-capable build.")
        kdf = Argon2id(
            salt=salt, length=length,
            iterations=int(params["iterations"]),
            lanes=int(params["lanes"]),
            memory_cost=int(params["memory_cost"]),
        )
        return kdf.derive(passphrase.encode("utf-8"))
    if kind == "scrypt":
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        kdf = Scrypt(salt=salt, length=length,
                     n=int(params["n"]), r=int(params["r"]), p=int(params["p"]))
        return kdf.derive(passphrase.encode("utf-8"))
    raise RuntimeError(f"Unknown KDF in bundle header: {kind!r}")


# ── AEAD container ───────────────────────────────────────────────────────────

def is_encrypted(blob: bytes) -> bool:
    return blob[:len(MAGIC)] == MAGIC


def encrypt_container(inner_zip: bytes, passphrase: str) -> bytes:
    """Wrap the inner ZIP in a PWBK1 AES-256-GCM container."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not passphrase:
        raise ValueError("encrypt_container requires a non-empty passphrase")
    params = _new_kdf_params()
    nonce = os.urandom(_NONCE_LEN)
    header = {
        "cipher":      "AES-256-GCM",
        "nonce":       nonce.hex(),
        "app_version": app_state.APP_VERSION,
        "created_at":  time.strftime("%Y-%m-%dT%H:%M:%S"),
        **params,
    }
    header_bytes = json.dumps(header, sort_keys=True).encode("utf-8")
    key = _derive_key(passphrase, params)
    ct = AESGCM(key).encrypt(nonce, inner_zip, header_bytes)   # header is AAD
    out = io.BytesIO()
    out.write(MAGIC)
    out.write(struct.pack(">I", len(header_bytes)))
    out.write(header_bytes)
    out.write(ct)
    return out.getvalue()


def decrypt_container(blob: bytes, passphrase: str) -> bytes:
    """Reverse encrypt_container(); returns the inner ZIP bytes.

    Raises ValueError on a bad passphrase / tampered bundle, RuntimeError if the
    bundle's KDF is unavailable on this platform."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not is_encrypted(blob):
        raise ValueError("Not a PingWatch encrypted bundle")
    off = len(MAGIC)
    (hlen,) = struct.unpack(">I", blob[off:off + 4])
    off += 4
    header_bytes = blob[off:off + hlen]
    off += hlen
    ct = blob[off:]
    try:
        header = json.loads(header_bytes)
    except Exception:
        raise ValueError("Corrupt bundle header")
    nonce = bytes.fromhex(header["nonce"])
    key = _derive_key(passphrase, header)
    try:
        return AESGCM(key).decrypt(nonce, ct, header_bytes)
    except Exception:
        # InvalidTag etc. — almost always a wrong passphrase.
        raise ValueError("Decryption failed — wrong passphrase or corrupt bundle")


# ── Public build entry point ─────────────────────────────────────────────────

def build_bundle(passphrase: str | None):
    """Build a full bundle. Returns (data: bytes, filename, encrypted: bool).

    When ``passphrase`` is set, the bundle is a ``.pwbk`` AEAD container;
    otherwise a plain ``.zip`` whose ``secrets/`` entries are in the clear."""
    inner = build_inner_zip()
    ver = app_state.APP_VERSION
    ts = time.strftime("%Y%m%d-%H%M%S")
    if passphrase:
        data = encrypt_container(inner, passphrase)
        return data, f"pingwatch-bundle-v{ver}-{ts}.pwbk", True
    return inner, f"pingwatch-bundle-v{ver}-{ts}.zip", False


# ── Secret restore (used by the importer on a fresh server) ──────────────────

def restore_secrets_from_zip(inner_zip: bytes) -> list:
    """Install bundled secrets onto THIS server, conservatively.

    The Fernet key and pingwatch.conf are NEVER overwritten if one already
    exists (clobbering the live key would brick local ciphertext; clobbering
    the conf could repoint a working backend). TLS cert files are written only
    when absent. Returns a list of human-readable actions for the audit log."""
    actions = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(inner_zip), "r")
    except Exception:
        return actions
    names = set(zf.namelist())

    # 1. Fernet key — only if this box has none yet.
    if _SEC_KEY_ARC in names:
        if os.path.isfile(_BACKUP_KEY):
            actions.append("key: kept existing (not overwritten)")
        else:
            try:
                os.makedirs(SECRETS_DIR, mode=0o700, exist_ok=True)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_BINARY"):
                    flags |= os.O_BINARY
                fd = os.open(_BACKUP_KEY, flags, 0o600)
                try:
                    os.write(fd, zf.read(_SEC_KEY_ARC))
                finally:
                    os.close(fd)
                try:
                    os.chmod(_BACKUP_KEY, 0o600)
                except Exception:
                    pass
                actions.append("key: installed")
            except FileExistsError:
                actions.append("key: kept existing (race)")
            except Exception as e:
                actions.append(f"key: FAILED ({e})")

    # 2. pingwatch.conf — only if absent.
    if _SEC_CONF_ARC in names:
        if os.path.exists(_CONF_PATH):
            actions.append("conf: kept existing (not overwritten)")
        else:
            try:
                with open(_CONF_PATH, "wb") as f:
                    f.write(zf.read(_SEC_CONF_ARC))
                actions.append("conf: installed")
            except Exception as e:
                actions.append(f"conf: FAILED ({e})")

    # 3. TLS certs — write only files that don't already exist.
    cert_names = [n for n in names if n.startswith(_SEC_CERTS_DIR + "/")]
    if cert_names:
        written = 0
        for n in cert_names:
            rel = n[len(_SEC_CERTS_DIR) + 1:]
            if not rel:
                continue
            dst = os.path.join(CERTS_DIR, rel.replace("/", os.sep))
            if os.path.exists(dst):
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(zf.read(n))
                try:
                    os.chmod(dst, 0o600)
                except Exception:
                    pass
                written += 1
            except Exception as e:
                actions.append(f"cert {rel}: FAILED ({e})")
        actions.append(f"certs: installed {written}/{len(cert_names)} (existing kept)")

    return actions
