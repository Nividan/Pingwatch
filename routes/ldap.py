"""
routes/ldap.py — LDAP / Active Directory configuration and test endpoints.

GET   /api/ldap/settings        → get LDAP config (admin)
PATCH /api/ldap/settings        → save LDAP config (admin)
POST  /api/ldap/test_connection → test LDAP server connectivity (admin)
POST  /api/ldap/test_auth       → test user authentication against LDAP (admin)

The bind password is never returned by GET — only bind_pass_set (bool).
"""

import core.settings as _settings
from db import _db_enqueue, db_log_audit, db_save_settings
from core.logger import log


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # ── GET /api/ldap/settings ────────────────────────────────────
    if path == '/api/ldap/settings' and method == 'GET':
        user, _ = h._require('admin')
        if not user: return True
        h._json(200, {
            'ldap_enabled':       int(_settings.get('ldap_enabled', 0) or 0),
            'ldap_server':        _settings.get('ldap_server', ''),
            'ldap_port':          int(_settings.get('ldap_port', 389) or 389),
            'ldap_ssl':           int(_settings.get('ldap_ssl', 0) or 0),
            'ldap_base_dn':       _settings.get('ldap_base_dn', ''),
            'ldap_bind_dn':       _settings.get('ldap_bind_dn', ''),
            'ldap_bind_pass_set': bool(_settings.get('ldap_bind_pass', '')),
            'ldap_user_filter':   _settings.get('ldap_user_filter', '(sAMAccountName={username})'),
            'ldap_domain':        _settings.get('ldap_domain', ''),
            'ldap_timeout':       int(_settings.get('ldap_timeout', 10) or 10),
        })
        return True

    # ── PATCH /api/ldap/settings ──────────────────────────────────
    if path == '/api/ldap/settings' and method == 'PATCH':
        user, _ = h._require('admin')
        if not user: return True
        save = {}

        if 'ldap_enabled' in body:
            save['ldap_enabled'] = '1' if body['ldap_enabled'] else '0'

        for k in ('ldap_server', 'ldap_base_dn', 'ldap_bind_dn',
                  'ldap_user_filter', 'ldap_domain'):
            if k in body:
                save[k] = str(body[k]).strip()

        if 'ldap_port' in body:
            try:
                save['ldap_port'] = str(max(1, min(65535, int(body['ldap_port']))))
            except (ValueError, TypeError):
                h._json(400, {'error': 'ldap_port must be an integer'}); return True

        if 'ldap_ssl' in body:
            try:
                v = int(body['ldap_ssl'])
                if v not in (0, 1, 2):
                    raise ValueError
                save['ldap_ssl'] = str(v)
            except (ValueError, TypeError):
                h._json(400, {'error': 'ldap_ssl must be 0 (none), 1 (LDAPS), or 2 (StartTLS)'}); return True

        if 'ldap_timeout' in body:
            try:
                save['ldap_timeout'] = str(max(1, int(body['ldap_timeout'])))
            except (ValueError, TypeError):
                h._json(400, {'error': 'ldap_timeout must be an integer'}); return True

        if body.get('ldap_bind_pass'):
            try:
                from db import encrypt_pw
                save['ldap_bind_pass'] = encrypt_pw(body['ldap_bind_pass'])
            except Exception as e:
                log.error(f"LDAP bind password encrypt error: {e}")
                h._json(500, {'error': 'Failed to encrypt bind password'}); return True

        if save:
            _settings.load(save)
            _db_enqueue(lambda s=save: db_save_settings(s))
            db_log_audit(user, h.client_address[0], 'ldap_settings_update', '')
            log.info(f"LDAP settings updated by {user!r}")

        h._json(200, {'ok': True})
        return True

    # ── POST /api/ldap/test_connection ────────────────────────────
    if path == '/api/ldap/test_connection' and method == 'POST':
        user, _ = h._require('admin')
        if not user: return True
        try:
            from core.ldap_auth import ldap_test_connection, _get_cfg
            cfg = _get_cfg()
            # Allow body overrides so admin can test before saving
            if body.get('ldap_server'):
                cfg['server'] = str(body['ldap_server']).strip()
            if body.get('ldap_port'):
                try: cfg['port'] = int(body['ldap_port'])
                except (ValueError, TypeError): pass
            if body.get('ldap_ssl') is not None:
                try: cfg['ssl'] = int(body['ldap_ssl'])
                except (ValueError, TypeError): pass
            if body.get('ldap_bind_dn'):
                cfg['bind_dn'] = str(body['ldap_bind_dn']).strip()
            if body.get('ldap_bind_pass'):
                cfg['bind_pass'] = str(body['ldap_bind_pass'])
            if body.get('ldap_timeout'):
                try: cfg['timeout'] = int(body['ldap_timeout'])
                except (ValueError, TypeError): pass
            ok, msg = ldap_test_connection(cfg)
        except Exception as e:
            log.error(f"LDAP test_connection route error: {e}")
            ok, msg = False, str(e)
        h._json(200, {'ok': ok, 'message': msg})
        return True

    # ── POST /api/ldap/test_auth ──────────────────────────────────
    if path == '/api/ldap/test_auth' and method == 'POST':
        user, _ = h._require('admin')
        if not user: return True
        test_user = (body.get('username') or '').strip()
        test_pass = body.get('password', '')
        if not test_user or not test_pass:
            h._json(400, {'error': 'username and password required'}); return True
        try:
            from core.ldap_auth import ldap_test_auth_user
            ok, msg = ldap_test_auth_user(test_user, test_pass)
        except Exception as e:
            log.error(f"LDAP test_auth route error: {e}")
            ok, msg = False, str(e)
        h._json(200, {'ok': ok, 'message': msg})
        return True

    return False
