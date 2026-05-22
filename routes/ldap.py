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
            'ldap_enabled':          int(_settings.get('ldap_enabled', 0) or 0),
            'ldap_server':           _settings.get('ldap_server', ''),
            'ldap_port':             int(_settings.get('ldap_port', 389) or 389),
            'ldap_ssl':              int(_settings.get('ldap_ssl', 0) or 0),
            'ldap_base_dn':          _settings.get('ldap_base_dn', ''),
            'ldap_bind_dn':          _settings.get('ldap_bind_dn', ''),
            'ldap_bind_pass_set':    bool(_settings.get('ldap_bind_pass', '')),
            'ldap_user_filter':      _settings.get('ldap_user_filter', '(sAMAccountName={username})'),
            'ldap_domain':           _settings.get('ldap_domain', ''),
            'ldap_timeout':          int(_settings.get('ldap_timeout', 10) or 10),
            'ldap_debug':            int(_settings.get('ldap_debug', 0) or 0),
            'ldap_group_base_dn':    _settings.get('ldap_group_base_dn', ''),
            'ldap_group_filter':     _settings.get('ldap_group_filter', '(objectClass=group)'),
            'ldap_auto_provision':   int(_settings.get('ldap_auto_provision', 0) or 0),
            'ldap_sync_interval':    int(_settings.get('ldap_sync_interval', 60) or 60),
            'ldap_nested_groups':    int(_settings.get('ldap_nested_groups', 0) or 0),
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
                  'ldap_user_filter', 'ldap_domain',
                  'ldap_group_base_dn', 'ldap_group_filter'):
            if k in body:
                save[k] = str(body[k]).strip()

        if 'ldap_auto_provision' in body:
            save['ldap_auto_provision'] = '1' if body['ldap_auto_provision'] else '0'
        if 'ldap_nested_groups' in body:
            save['ldap_nested_groups'] = '1' if body['ldap_nested_groups'] else '0'

        if 'ldap_sync_interval' in body:
            try:
                save['ldap_sync_interval'] = str(max(0, int(body['ldap_sync_interval'])))
            except (ValueError, TypeError):
                h._json(400, {'error': 'ldap_sync_interval must be an integer'}); return True

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
            db_log_audit(user, h.client_address[0], 'ldap_settings_update', '',
                         ','.join(save.keys()))
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
            # Surface the exception type only — the full message is in the
            # server log. Admins can correlate via the timestamp.
            ok, msg = False, f"unexpected {type(e).__name__}; check server log"
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
            result = ldap_test_auth_user(test_user, test_pass)
            ok, msg = result[0], result[1]
            if ok:
                log.info(f"LDAP test_auth result: OK for {test_user!r} "
                         f"(initiated by {user!r})")
            else:
                log.warning(f"LDAP test_auth result: FAILED for {test_user!r} "
                            f"(initiated by {user!r}): {msg}")
        except Exception as e:
            log.error(f"LDAP test_auth route error for {test_user!r} "
                      f"(initiated by {user!r}): {e}")
            ok, msg = False, f"unexpected {type(e).__name__}; check server log"
        h._json(200, {'ok': ok, 'message': msg})
        return True

    # ── POST /api/ldap/search_groups ─────────────────────────────
    if path == '/api/ldap/search_groups' and method == 'POST':
        user, _ = h._require('admin')
        if not user: return True
        query = (body.get('query') or '').strip()
        log.info(f"LDAP search_groups initiated by {user!r}: query={query!r}")
        try:
            from core.ldap_auth import ldap_search_groups
            ok, result = ldap_search_groups(query)
            if ok:
                h._json(200, {'ok': True, 'groups': result})
            else:
                h._json(200, {'ok': False, 'message': result})
        except Exception as e:
            log.error(f"LDAP search_groups route error: {e}")
            h._json(200, {'ok': False, 'message': 'Search failed'})
        return True

    # ── POST /api/ldap/test_user_groups ──────────────────────────
    if path == '/api/ldap/test_user_groups' and method == 'POST':
        user, _ = h._require('admin')
        if not user: return True
        test_user = (body.get('username') or '').strip()
        if not test_user:
            h._json(400, {'error': 'username is required'}); return True
        log.info(f"LDAP test_user_groups initiated by {user!r}: testing {test_user!r}")
        try:
            from core.ldap_auth import ldap_get_user_info
            ok, result = ldap_get_user_info(test_user)
            if ok:
                h._json(200, {
                    'ok': True,
                    'display_name': result.get('display_name', ''),
                    'email':        result.get('email', ''),
                    'groups':       result.get('member_of', []),
                })
            else:
                h._json(200, {'ok': False, 'message': result})
        except Exception as e:
            log.error(f"LDAP test_user_groups route error: {e}")
            h._json(200, {'ok': False, 'message': 'Lookup failed'})
        return True

    return False
