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
            'ldap_debug':         int(_settings.get('ldap_debug', 0) or 0),
        })
        return True

    # ── PATCH /api/ldap/settings ──────────────────────────────────
    if path == '/api/ldap/settings' and method == 'PATCH':
        user, _ = h._require('admin')
        if not user: return True
        save = {}

        if 'ldap_enabled' in body:
            save['ldap_enabled'] = '1' if body['ldap_enabled'] else '0'

        if 'ldap_debug' in body:
            save['ldap_debug'] = '1' if body['ldap_debug'] else '0'

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
            # Log what changed at a meaningful level
            if 'ldap_enabled' in save:
                state = 'enabled' if save['ldap_enabled'] == '1' else 'disabled'
                log.info(f"LDAP authentication {state} by {user!r}")
            if 'ldap_server' in save:
                log.info(f"LDAP server changed to {save['ldap_server']!r} by {user!r}")
            if 'ldap_port' in save or 'ldap_ssl' in save:
                ssl_labels = {'0': 'none', '1': 'LDAPS', '2': 'StartTLS'}
                ssl_val = save.get('ldap_ssl', str(_settings.get('ldap_ssl', 0)))
                log.info(f"LDAP connection parameters updated by {user!r}: "
                         f"port={save.get('ldap_port', _settings.get('ldap_port', 389))} "
                         f"ssl={ssl_labels.get(ssl_val, ssl_val)}")
            if 'ldap_bind_dn' in save:
                log.info(f"LDAP bind DN changed to {save['ldap_bind_dn']!r} by {user!r}")
            if 'ldap_bind_pass' in save:
                log.info(f"LDAP bind password updated by {user!r}")
            if 'ldap_base_dn' in save:
                log.info(f"LDAP base DN changed to {save['ldap_base_dn']!r} by {user!r}")
            if 'ldap_user_filter' in save:
                log.info(f"LDAP user filter changed to {save['ldap_user_filter']!r} by {user!r}")
            _settings.load(save)
            _db_enqueue(lambda s=save: db_save_settings(s))
            db_log_audit(user, h.client_address[0], 'ldap_settings_update', '')
        else:
            log.debug(f"LDAP settings PATCH by {user!r}: no recognised fields — no-op")

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
            log.info(f"LDAP test_connection initiated by {user!r}: "
                     f"server={cfg['server']}:{cfg['port']}")
            ok, msg = ldap_test_connection(cfg)
            if ok:
                log.info(f"LDAP test_connection result: OK "
                         f"(initiated by {user!r}, server={cfg['server']}:{cfg['port']})")
            else:
                log.warning(f"LDAP test_connection result: FAILED "
                            f"(initiated by {user!r}, server={cfg['server']}:{cfg['port']}): {msg}")
        except Exception as e:
            log.error(f"LDAP test_connection route error (initiated by {user!r}): {e}")
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
            log.warning(f"LDAP test_auth: missing username or password "
                        f"(initiated by {user!r})")
            h._json(400, {'error': 'username and password required'}); return True
        log.info(f"LDAP test_auth initiated by {user!r}: testing {test_user!r}")
        try:
            from core.ldap_auth import ldap_test_auth_user
            ok, msg = ldap_test_auth_user(test_user, test_pass)
            if ok:
                log.info(f"LDAP test_auth result: OK for {test_user!r} "
                         f"(initiated by {user!r})")
            else:
                log.warning(f"LDAP test_auth result: FAILED for {test_user!r} "
                            f"(initiated by {user!r}): {msg}")
        except Exception as e:
            log.error(f"LDAP test_auth route error for {test_user!r} "
                      f"(initiated by {user!r}): {e}")
            ok, msg = False, str(e)
        h._json(200, {'ok': ok, 'message': msg})
        return True

    return False
