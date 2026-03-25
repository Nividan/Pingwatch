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

from core.config import (
    _RE_BACKUPS, _RE_BACKUP_DEV, _RE_BACKUP_HISTORY,
    _RE_BACKUP_RUN_ID, _RE_BACKUP_TRIGGER,
)
from db import (
    db_log_audit,
    db_get_backup_list, db_get_backup_settings, db_save_backup_settings,
    db_get_backup_history, db_get_backup_run,
    db_delete_backup_run, db_ensure_backup_device,
)
from backup.engine import do_backup
from core.logger import log_backup as log

# ── Rate-limit: prevent spamming backup triggers per device ──────────────────
_last_trigger: dict = {}
_TRIGGER_COOLDOWN = 30  # seconds


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # ── GET /api/backups — list all devices ───────────────────────
    if _RE_BACKUPS.match(path) and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        from core.app_state import STATE
        devices = db_get_backup_list()
        # Merge with live device names/hosts from STATE
        dev_map = {did: d for did, d in STATE.devices.items()}
        for entry in devices:
            live = dev_map.get(entry['did'])
            entry['name']    = live.name  if live else None
            entry['host']    = live.host  if live else ''
            entry['group']   = live.group if live else ''
            entry['orphaned'] = live is None
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
                    'timeout': 30, 'in_schedule': False,
                    'last_run_id': None, 'last_ts': None,
                    'last_success': None, 'last_size': None, 'last_error': None,
                    'run_count': 0,
                })
        # Enabled+scheduled devices first → enabled-only second → alphabetically last
        def _sort_key(e):
            pri = 3 if (e.get('enabled') and e.get('in_schedule')) else \
                  2 if e.get('enabled') else \
                  1 if e.get('username') else 0
            return (-pri, (e.get('name') or '').lower())
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

        def _run_backup(device_id):
            try:
                do_backup(device_id)
            except Exception as e:
                log.error(f"Backup crashed for device {device_id!r}: {e}", exc_info=True)

        threading.Thread(target=_run_backup, args=(did,), daemon=True,
                         name=f"manual-bk-{did}").start()
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
            settings = {
                'did': did, 'enabled': False, 'method': 'ssh', 'port': 22,
                'username': '', 'has_password': False, 'has_enable': False,
                'commands': ['show running-config'], 'paging_cmd': '',
                'timeout': 30, 'in_schedule': False,
            }
        h._json(200, {'settings': settings})
        return True

    # ── PUT /api/backups/<did> — save settings ────────────────────
    if m and method == 'PUT':
        user, _ = h._require('operator')
        if not user: return True
        did = m.group(1)
        # Validate user-supplied fields before persisting
        _method = (body.get('method') or 'ssh').lower()
        if _method not in ('ssh', 'telnet'):
            h._json(400, {'error': f"Unsupported backup method: {_method!r}"}); return True
        try:
            _port = int(body.get('port', 22))
            if not (1 <= _port <= 65535):
                raise ValueError
        except (TypeError, ValueError):
            h._json(400, {'error': 'Port must be an integer between 1 and 65535'}); return True
        try:
            _timeout = int(body.get('timeout', 30))
            if not (1 <= _timeout <= 300):
                raise ValueError
        except (TypeError, ValueError):
            h._json(400, {'error': 'Timeout must be an integer between 1 and 300'}); return True
        db_ensure_backup_device(did)
        db_save_backup_settings(did, body)
        db_log_audit(user, h.client_address[0], 'backup_settings_save', did)
        h._json(200, {'ok': True})
        return True

    return False
