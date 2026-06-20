"""
backup/remote_upload.py — Off-box upload of DB backup files to SFTP or SMB.

Called by do_db_backup() after the local backup files are written. Failure
here is non-fatal — it only affects db_backup_remote_last_result; the local
backup still counts as successful.

Destinations (chosen via db_backup_remote_type):
  sftp — paramiko.SFTPClient, reuses the TOFU host-key store from backup/engine.py
  smb  — smbprotocol (import smbclient), lazy-imported so SFTP-only users are
         not blocked when smbprotocol is not installed.
"""

from __future__ import annotations

import io
import os
import posixpath
import socket

from core.logger import log_backup as log
from core.settings import get as _cfg
from db.backups import decrypt_pw

_PROBE_NAME = 'pingwatch-remote-probe.tmp'
_PROBE_BODY = b'pingwatch remote backup probe\n'
_CONNECT_TIMEOUT = 15
_BACKUP_PREFIXES = ('pingwatch-main-', 'pingwatch-logs-')


# ── Public API ──────────────────────────────────────────────────────

def do_remote_upload(local_paths: list) -> tuple:
    """
    Upload one or more local backup files to the configured remote.
    Returns (ok: bool, message: str). Never raises.
    """
    rtype = (_cfg('db_backup_remote_type', '') or '').lower()
    settings = _resolve_settings()
    try:
        if rtype == 'sftp':
            return _upload_sftp(local_paths, settings)
        if rtype == 'smb':
            return _upload_smb(local_paths, settings)
        return False, f"unknown remote type {rtype!r}"
    except Exception as e:
        log.error(f"Remote upload crashed ({rtype}): {e}", exc_info=True)
        return False, "remote upload failed — check server logs"


def test_remote(overrides: dict) -> tuple:
    """
    Connect to the configured remote, write and delete a small probe file.
    Returns (ok: bool, message: str). Called by the UI Test Connection button.

    `overrides` may contain plaintext password / key from the form; empty values
    fall back to the currently stored (Fernet-encrypted) settings.
    """
    rtype = (overrides.get('type') or _cfg('db_backup_remote_type', '') or '').lower()
    settings = _resolve_settings(overrides)
    try:
        if rtype == 'sftp':
            return _test_sftp(settings)
        if rtype == 'smb':
            return _test_smb(settings)
        return False, f"unknown remote type {rtype!r}"
    except Exception as e:
        log.error(f"Remote test crashed ({rtype}): {e}", exc_info=True)
        return False, "connection test failed — check server logs"


# ── Settings resolution ─────────────────────────────────────────────

def _resolve_settings(overrides: dict | None = None) -> dict:
    """
    Build the effective connection settings dict, layering UI overrides on top
    of stored settings. Password / key: empty override means "use stored".
    """
    overrides = overrides or {}
    stored_pw = decrypt_pw(_cfg('db_backup_remote_password_enc', '') or '')
    stored_key = decrypt_pw(_cfg('db_backup_remote_key_enc', '') or '')

    def _pick(ov_key, cfg_key, default=''):
        v = overrides.get(ov_key)
        if v is None or v == '':
            return _cfg(cfg_key, default)
        return v

    return {
        'host':     _pick('host',  'db_backup_remote_host', ''),
        'port':     int(_pick('port', 'db_backup_remote_port', 22) or 22),
        'share':    _pick('share', 'db_backup_remote_share', ''),
        'path':     _pick('path',  'db_backup_remote_path', ''),
        'user':     _pick('user',  'db_backup_remote_user', ''),
        'password': overrides.get('password') or stored_pw,
        'key':      overrides.get('key') or stored_key,
        'keep':     int(_cfg('db_backup_keep', 7) or 7),
    }


# ── SFTP ────────────────────────────────────────────────────────────

def _upload_sftp(local_paths: list, s: dict) -> tuple:
    try:
        import paramiko  # noqa: F401
    except ImportError:
        return False, "paramiko not installed — run setup to add it"
    if not s['host']:
        return False, "remote host is empty"

    transport, err = _sftp_connect(s)
    if err:
        return False, err
    try:
        import paramiko
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_dir = (s['path'] or '.').replace('\\', '/')
        _sftp_ensure_dir(sftp, remote_dir)
        uploaded = []
        for lp in local_paths:
            fname = os.path.basename(lp)
            rp = posixpath.join(remote_dir, fname) if remote_dir != '.' else fname
            sftp.put(lp, rp)
            uploaded.append(fname)
            log.info(f"Remote upload (sftp): {fname} → {s['host']}:{rp}")
        _sftp_prune(sftp, remote_dir, s['keep'])
        sftp.close()
        return True, f"uploaded {len(uploaded)} file(s) via sftp"
    except Exception as e:
        log.error(f"Remote upload (sftp) failed: {e}", exc_info=True)
        return False, "sftp upload failed — check server logs"
    finally:
        try:
            transport.close()
        except Exception:
            pass


def _test_sftp(s: dict) -> tuple:
    try:
        import paramiko  # noqa: F401
    except ImportError:
        return False, "paramiko not installed — run setup to add it"
    if not s['host']:
        return False, "remote host is empty"

    transport, err = _sftp_connect(s)
    if err:
        return False, err
    try:
        import paramiko
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_dir = (s['path'] or '.').replace('\\', '/')
        _sftp_ensure_dir(sftp, remote_dir)
        probe = posixpath.join(remote_dir, _PROBE_NAME) if remote_dir != '.' else _PROBE_NAME
        with sftp.file(probe, 'wb') as f:
            f.write(_PROBE_BODY)
        sftp.remove(probe)
        sftp.close()
        return True, f"connected to {s['host']}:{s['port']} as {s['user']} — probe write OK"
    except Exception as e:
        log.warning(f"Remote test (sftp) failed: {e}")
        return False, _sftp_sanitize_err(e)
    finally:
        try:
            transport.close()
        except Exception:
            pass


def _sftp_connect(s: dict):
    """Open + authenticate a paramiko.Transport. Returns (transport, err_or_None)."""
    import paramiko
    from .engine import _verify_host_key
    # Explicit socket connect with timeout — paramiko.Transport((host, port))
    # otherwise blocks on the OS-default TCP timeout (minutes) for an
    # unreachable backup target, ignoring _CONNECT_TIMEOUT.
    try:
        _sock = socket.create_connection((s['host'], s['port']),
                                         timeout=_CONNECT_TIMEOUT)
    except Exception as e:
        return None, f"connection failed: {e}"
    try:
        t = paramiko.Transport(_sock)
        t.banner_timeout = _CONNECT_TIMEOUT
        t.start_client(timeout=_CONNECT_TIMEOUT)
    except Exception as e:
        try: _sock.close()
        except Exception: pass
        return None, f"connection failed: {e}"

    err = _verify_host_key(t, s['host'], s['port'])
    if err:
        try:
            t.close()
        except Exception:
            pass
        return None, err

    # Prefer key auth if a key was provided, otherwise fall back to password
    authed = False
    last_err = 'authentication failed'
    if s['key']:
        pkey, perr = _sftp_load_key(s['key'], s['password'])
        if pkey is not None:
            try:
                t.auth_publickey(s['user'], pkey)
                authed = True
            except Exception as e:
                last_err = f"key auth failed: {e}"
        else:
            last_err = perr or 'invalid private key'
    if not authed and s['password']:
        try:
            t.auth_password(s['user'], s['password'])
            authed = True
        except Exception as e:
            last_err = f"password auth failed: {e}"

    if not authed:
        try:
            t.close()
        except Exception:
            pass
        log.warning(f"SFTP auth failed for {s['user']}@{s['host']}: {last_err}")
        return None, "authentication failed"
    return t, None


def _sftp_load_key(key_text: str, passphrase: str):
    """Try RSA, Ed25519, ECDSA in order. Returns (pkey, err_or_None)."""
    import paramiko
    errors = []
    for loader in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            buf = io.StringIO(key_text)
            return loader.from_private_key(buf, password=passphrase or None), None
        except Exception as e:
            errors.append(f"{loader.__name__}: {e}")
            continue
    return None, "unrecognised private key format"


def _sftp_ensure_dir(sftp, path: str) -> None:
    """mkdir -p equivalent. Silent on pre-existing dirs."""
    if not path or path == '.':
        return
    parts = [p for p in path.split('/') if p]
    acc = '' if not path.startswith('/') else '/'
    for p in parts:
        acc = p if acc == '' else (acc + '/' + p if acc != '/' else '/' + p)
        try:
            sftp.stat(acc)
        except IOError:
            try:
                sftp.mkdir(acc)
            except Exception as e:
                log.warning(f"SFTP: could not mkdir {acc}: {e}")
                return


def _sftp_prune(sftp, remote_dir: str, keep: int) -> None:
    try:
        entries = sftp.listdir(remote_dir or '.')
    except Exception as e:
        log.warning(f"SFTP: retention listdir failed for {remote_dir!r}: {e}")
        return
    for prefix in _BACKUP_PREFIXES:
        matching = sorted(f for f in entries if f.startswith(prefix))
        to_delete = matching[:-keep] if len(matching) > keep else []
        for fname in to_delete:
            rp = posixpath.join(remote_dir, fname) if remote_dir and remote_dir != '.' else fname
            try:
                sftp.remove(rp)
                log.info(f"Remote retention (sftp): deleted {rp}")
            except Exception as e:
                log.warning(f"SFTP: could not delete {rp}: {e}")


def _sftp_sanitize_err(e: Exception) -> str:
    msg = str(e).lower()
    if 'auth' in msg:
        return "authentication failed"
    if 'host key' in msg or 'mitm' in msg:
        return "host key verification failed — see server logs"
    if 'timed out' in msg or 'timeout' in msg:
        return "connection timed out"
    if 'refused' in msg:
        return "connection refused"
    return "connection failed — check server logs"


# ── SMB ─────────────────────────────────────────────────────────────

def _upload_smb(local_paths: list, s: dict) -> tuple:
    try:
        import smbclient  # type: ignore
    except ImportError:
        return False, "smbprotocol not installed — run setup to add it"
    if not s['host'] or not s['share']:
        return False, "remote host and share are required for SMB"

    try:
        smbclient.register_session(
            s['host'],
            username=s['user'],
            password=s['password'],
            connection_timeout=_CONNECT_TIMEOUT,
        )
    except Exception as e:
        log.warning(f"SMB register_session failed for {s['host']}: {e}")
        return False, _smb_sanitize_err(e)

    try:
        base = _smb_build_path(s['host'], s['share'], s['path'])
        _smb_ensure_dir(smbclient, base)
        uploaded = []
        for lp in local_paths:
            fname = os.path.basename(lp)
            dest = base + '\\' + fname
            with open(lp, 'rb') as src, smbclient.open_file(dest, mode='wb') as dst:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    dst.write(chunk)
            uploaded.append(fname)
            log.info(f"Remote upload (smb): {fname} → {dest}")
        _smb_prune(smbclient, base, s['keep'])
        return True, f"uploaded {len(uploaded)} file(s) via smb"
    except Exception as e:
        log.error(f"Remote upload (smb) failed: {e}", exc_info=True)
        return False, _smb_sanitize_err(e)
    finally:
        try:
            smbclient.delete_session(s['host'])
        except Exception:
            pass


def _test_smb(s: dict) -> tuple:
    try:
        import smbclient  # type: ignore
    except ImportError:
        return False, "smbprotocol not installed — run setup to add it"
    if not s['host'] or not s['share']:
        return False, "remote host and share are required for SMB"
    try:
        smbclient.register_session(
            s['host'],
            username=s['user'],
            password=s['password'],
            connection_timeout=_CONNECT_TIMEOUT,
        )
    except Exception as e:
        log.warning(f"SMB register_session failed for {s['host']}: {e}")
        return False, _smb_sanitize_err(e)
    try:
        base = _smb_build_path(s['host'], s['share'], s['path'])
        _smb_ensure_dir(smbclient, base)
        probe = base + '\\' + _PROBE_NAME
        with smbclient.open_file(probe, mode='wb') as f:
            f.write(_PROBE_BODY)
        smbclient.remove(probe)
        return True, f"connected to \\\\{s['host']}\\{s['share']} as {s['user']} — probe write OK"
    except Exception as e:
        log.warning(f"Remote test (smb) failed: {e}")
        return False, _smb_sanitize_err(e)
    finally:
        try:
            smbclient.delete_session(s['host'])
        except Exception:
            pass


def _smb_build_path(host: str, share: str, subpath: str) -> str:
    """Return UNC path \\\\host\\share\\sub\\dirs (backslashes, no trailing)."""
    sub = (subpath or '').strip().replace('/', '\\').strip('\\')
    base = f"\\\\{host}\\{share}"
    return base + ('\\' + sub if sub else '')


def _smb_ensure_dir(smbclient, unc_path: str) -> None:
    """mkdir -p for an SMB UNC path. Silent on pre-existing dirs."""
    parts = unc_path.split('\\')
    # parts = ['', '', host, share, 'sub1', 'sub2'] → skip first 4
    if len(parts) <= 4:
        return
    prefix = '\\\\' + parts[2] + '\\' + parts[3]
    acc = prefix
    for segment in parts[4:]:
        if not segment:
            continue
        acc = acc + '\\' + segment
        try:
            smbclient.stat(acc)
        except Exception:
            try:
                smbclient.mkdir(acc)
            except Exception as e:
                log.warning(f"SMB: could not mkdir {acc}: {e}")
                return


def _smb_prune(smbclient, unc_dir: str, keep: int) -> None:
    try:
        entries = smbclient.listdir(unc_dir)
    except Exception as e:
        log.warning(f"SMB: retention listdir failed for {unc_dir}: {e}")
        return
    for prefix in _BACKUP_PREFIXES:
        matching = sorted(f for f in entries if f.startswith(prefix))
        to_delete = matching[:-keep] if len(matching) > keep else []
        for fname in to_delete:
            rp = unc_dir + '\\' + fname
            try:
                smbclient.remove(rp)
                log.info(f"Remote retention (smb): deleted {rp}")
            except Exception as e:
                log.warning(f"SMB: could not delete {rp}: {e}")


def _smb_sanitize_err(e: Exception) -> str:
    msg = str(e).lower()
    if 'logon' in msg or 'auth' in msg or 'access_denied' in msg or 'sts_logon' in msg:
        return "authentication failed"
    if 'bad_network_name' in msg or 'not found' in msg:
        return "share not found"
    if 'timed out' in msg or 'timeout' in msg:
        return "connection timed out"
    if 'refused' in msg or 'unreachable' in msg:
        return "connection refused"
    return "smb connection failed — check server logs"
