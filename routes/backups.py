"""
routes/backups.py — Device configuration backup API endpoints.

GET    /api/backups                    → list all devices with latest backup metadata
GET    /api/backups/<did>              → get backup settings (no plaintext passwords)
PUT    /api/backups/<did>              → save backup settings
GET    /api/backups/<did>/history      → list backup run metadata
GET    /api/backups/run/<id>           → full run with config text
POST   /api/backups/<did>/run          → trigger immediate backup (async)
DELETE /api/backups/run/<id>           → delete a backup run (admin)
"""

import threading
import time as _time

from config import (
    _RE_BACKUPS, _RE_BACKUP_DEV, _RE_BACKUP_HISTORY,
    _RE_BACKUP_RUN_ID, _RE_BACKUP_TRIGGER,
)
from db import (
    db_log_audit,
    db_get_backup_list, db_get_backup_settings, db_save_backup_settings,
    db_get_backup_history, db_get_backup_run,
    db_save_backup_run, db_delete_backup_run, db_ensure_backup_device,
)
from logger import log

# ── Rate-limit: prevent spamming backup triggers per device ──────────────────
_last_trigger: dict = {}
_TRIGGER_COOLDOWN = 30  # seconds


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # ── GET /api/backups — list all devices ───────────────────────
    if _RE_BACKUPS.match(path) and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        from app_state import STATE
        devices = db_get_backup_list()
        # Merge with live device names/hosts from STATE
        dev_map = {did: d for did, d in STATE.devices.items()}
        for entry in devices:
            live = dev_map.get(entry['did'])
            entry['name']  = live.name  if live else entry['did']
            entry['host']  = live.host  if live else ''
            entry['group'] = live.group if live else ''
        # Also include devices not yet configured (enabled=False placeholder)
        configured = {e['did'] for e in devices}
        for did, d in dev_map.items():
            if did not in configured:
                devices.append({
                    'did': did, 'name': d.name,
                    'host': d.host, 'group': d.group,
                    'enabled': False, 'method': 'ssh', 'port': 22,
                    'username': '', 'has_password': False, 'has_enable': False,
                    'commands': ['show running-config'], 'paging_cmd': '',
                    'timeout': 30, 'schedule': '',
                    'last_run_id': None, 'last_ts': None,
                    'last_success': None, 'last_size': None, 'last_error': None,
                    'run_count': 0,
                })
        # Enabled devices first → has-credentials second → alphabetically last
        def _sort_key(e):
            configured = 2 if e.get('enabled') else (1 if e.get('username') else 0)
            return (-configured, e.get('name', '').lower())
        devices.sort(key=_sort_key)
        h._json(200, {'devices': devices})
        return True

    # ── GET /api/backups/<did>/history ────────────────────────────
    m = _RE_BACKUP_HISTORY.match(path)
    if m and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        did = m.group(1)
        h._json(200, {'history': db_get_backup_history(did)})
        return True

    # ── POST /api/backups/<did>/run — trigger backup ───────────────
    m = _RE_BACKUP_TRIGGER.match(path)
    if m and method == 'POST':
        user, _ = h._require('operator')
        if not user: return True
        did = m.group(1)
        now = _time.time()
        if now - _last_trigger.get(did, 0) < _TRIGGER_COOLDOWN:
            h._json(429, {'error': 'Backup triggered too recently, please wait 30 s'})
            return True
        _last_trigger[did] = now
        db_log_audit(user, h.client_address[0], 'backup_run', did)
        log.info(f"Backup: manual trigger for device {did!r} by {user!r}")
        threading.Thread(target=_do_backup, args=(did,), daemon=True).start()
        h._json(202, {'ok': True, 'started': True})
        return True

    # ── GET /api/backups/run/<id> — full run with config ──────────
    m = _RE_BACKUP_RUN_ID.match(path)
    if m and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        run = db_get_backup_run(int(m.group(1)))
        if not run:
            h._json(404, {'error': 'Run not found'}); return True
        h._json(200, {'run': run})
        return True

    # ── DELETE /api/backups/run/<id> ──────────────────────────────
    if m and method == 'DELETE':
        user, _ = h._require('admin')
        if not user: return True
        db_delete_backup_run(int(m.group(1)))
        db_log_audit(user, h.client_address[0], 'backup_delete_run', m.group(1))
        h._json(200, {'ok': True})
        return True

    # ── GET /api/backups/<did> — settings ─────────────────────────
    m = _RE_BACKUP_DEV.match(path)
    if m and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        did = m.group(1)
        settings = db_get_backup_settings(did)
        if not settings:
            # Return defaults if not yet configured
            settings = {
                'did': did, 'enabled': False, 'method': 'ssh', 'port': 22,
                'username': '', 'has_password': False, 'has_enable': False,
                'commands': ['show running-config'], 'paging_cmd': '',
                'timeout': 30, 'schedule': '',
            }
        h._json(200, {'settings': settings})
        return True

    # ── PUT /api/backups/<did> — save settings ────────────────────
    if m and method == 'PUT':
        user, _ = h._require('operator')
        if not user: return True
        did = m.group(1)
        db_ensure_backup_device(did)
        db_save_backup_settings(did, body)
        db_log_audit(user, h.client_address[0], 'backup_settings_save', did)
        h._json(200, {'ok': True})
        return True

    return False


# ── Async backup execution ────────────────────────────────────────────

def _do_backup(did: str):
    """Run in a background thread. Fetches settings, runs backup, saves result."""
    try:
        from app_state import STATE
        import app_state as _as

        device = STATE.devices.get(did)
        if not device:
            log.warning(f"Backup: device {did!r} not found in state")
            return

        # with_secrets=True so the engine receives password_enc / enable_enc
        settings_row = db_get_backup_settings(did, with_secrets=True)
        if not settings_row:
            log.warning(f"Backup: no settings for device {did!r}")
            return

        from backup_engine import run_backup
        result = run_backup(device, settings_row)
        new_id = db_save_backup_run(did, result)

        status = 'success' if result['success'] else 'failed'
        log.info(
            f"Backup: {status} for {device.name} "
            f"({device.host}) — {result.get('size_bytes', 0)} bytes"
        )
        if not result['success']:
            log.warning(f"Backup error for {did!r}: {result.get('error_msg','')}")

        # Push SSE event so the frontend table updates in real time
        try:
            _as.STATE._broadcast('backup_complete', {
                'did':     did,
                'run_id':  new_id,
                'success': result['success'],
                'ts':      result['ts'],
                'size':    result.get('size_bytes', 0),
                'error':   result.get('error_msg', ''),
            })
        except Exception as e:
            log.warning(f"Backup SSE push failed: {e}")

    except Exception as e:
        log.error(f"Backup thread error for {did!r}: {e}")
