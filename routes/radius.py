"""
routes/radius.py — RADIUS configuration and test endpoints.

GET   /api/radius/settings         → get RADIUS config (admin)
PATCH /api/radius/settings         → save RADIUS config (admin)
POST  /api/radius/test_connection  → probe the server (admin)
POST  /api/radius/test_auth        → full auth flow, exposes returned attrs (admin)
POST  /api/radius/test_auth_challenge → follow-up challenge response for test_auth (admin)
GET   /api/radius/attribute_mappings  → list groups that are RADIUS-mapped (admin)

Shared secrets are never returned by GET — only `radius_secret_set` / `radius_secret2_set` sentinels.
"""

import core.settings as _settings
from db import _db_enqueue, db_log_audit, db_save_settings
from core.logger import log


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # ── GET /api/radius/settings ──────────────────────────────────
    if path == '/api/radius/settings' and method == 'GET':
        user, _ = h._require('admin')
        if not user: return True
        h._json(200, {
            'radius_enabled':         int(_settings.get('radius_enabled', 0) or 0),
            'radius_server':          _settings.get('radius_server', ''),
            'radius_port':            int(_settings.get('radius_port', 1812) or 1812),
            'radius_secret_set':      bool(_settings.get('radius_secret_enc', '')),
            'radius_server2':         _settings.get('radius_server2', ''),
            'radius_port2':           int(_settings.get('radius_port2', 1812) or 1812),
            'radius_secret2_set':     bool(_settings.get('radius_secret2_enc', '')),
            'radius_timeout':         int(_settings.get('radius_timeout', 5) or 5),
            'radius_retries':         int(_settings.get('radius_retries', 3) or 3),
            'radius_nas_identifier':  _settings.get('radius_nas_identifier', 'pingwatch'),
            'radius_realm_prefix':    _settings.get('radius_realm_prefix', ''),
            'radius_realm_suffix':    _settings.get('radius_realm_suffix', ''),
            'radius_auto_provision':  int(_settings.get('radius_auto_provision', 0) or 0),
            'radius_default_role':    _settings.get('radius_default_role', 'viewer'),
            'radius_debug':           int(_settings.get('radius_debug', 0) or 0),
        })
        return True

    # ── PATCH /api/radius/settings ────────────────────────────────
    if path == '/api/radius/settings' and method == 'PATCH':
        user, _ = h._require('admin')
        if not user: return True
        save = {}

        if 'radius_enabled' in body:
            save['radius_enabled'] = '1' if body['radius_enabled'] else '0'
        if 'radius_auto_provision' in body:
            save['radius_auto_provision'] = '1' if body['radius_auto_provision'] else '0'
        if 'radius_debug' in body:
            save['radius_debug'] = '1' if body['radius_debug'] else '0'

        for k in ('radius_server', 'radius_server2',
                  'radius_nas_identifier', 'radius_realm_prefix',
                  'radius_realm_suffix'):
            if k in body:
                save[k] = str(body[k]).strip()

        if 'radius_default_role' in body:
            v = str(body['radius_default_role']).strip().lower()
            if v not in ('viewer', 'operator', 'admin'):
                h._json(400, {'error': 'radius_default_role must be viewer, operator, or admin'})
                return True
            save['radius_default_role'] = v

        for pk in ('radius_port', 'radius_port2'):
            if pk in body:
                try:
                    v = int(body[pk])
                    if v < 1 or v > 65535:
                        raise ValueError
                    save[pk] = str(v)
                except (ValueError, TypeError):
                    h._json(400, {'error': f'{pk} must be 1\u201365535'})
                    return True

        if 'radius_timeout' in body:
            try:
                save['radius_timeout'] = str(max(1, min(60, int(body['radius_timeout']))))
            except (ValueError, TypeError):
                h._json(400, {'error': 'radius_timeout must be an integer (1\u201360)'})
                return True

        if 'radius_retries' in body:
            try:
                save['radius_retries'] = str(max(1, min(10, int(body['radius_retries']))))
            except (ValueError, TypeError):
                h._json(400, {'error': 'radius_retries must be an integer (1\u201310)'})
                return True

        # Shared secrets: empty = keep; non-empty = Fernet-encrypt + replace
        for secret_key, enc_key in (('radius_secret',  'radius_secret_enc'),
                                    ('radius_secret2', 'radius_secret2_enc')):
            raw = body.get(secret_key)
            if raw:
                try:
                    from db import encrypt_pw
                    save[enc_key] = encrypt_pw(str(raw))
                except Exception as e:
                    log.error(f"RADIUS secret encrypt error: {e}")
                    h._json(500, {'error': 'Failed to encrypt shared secret'}); return True

        if save:
            _settings.load(save)
            _db_enqueue(lambda s=save: db_save_settings(s))
            db_log_audit(user, h.client_address[0], 'radius_settings_update', '',
                         ','.join(save.keys()))
        else:
            log.debug(f"RADIUS settings PATCH by {user!r}: no recognised fields \u2014 no-op")

        h._json(200, {'ok': True})
        return True

    # ── POST /api/radius/test_connection ──────────────────────────
    if path == '/api/radius/test_connection' and method == 'POST':
        user, _ = h._require('admin')
        if not user: return True
        try:
            from core.radius_auth import radius_test_connection
            overrides = {}
            for src, dst in (('radius_server',  'server'),
                             ('radius_server2', 'server2'),
                             ('radius_nas_identifier', 'nas_identifier'),
                             ('radius_realm_prefix', 'realm_prefix'),
                             ('radius_realm_suffix', 'realm_suffix')):
                if src in body:
                    overrides[dst] = str(body[src]).strip()
            for src, dst in (('radius_port',    'port'),
                             ('radius_port2',   'port2'),
                             ('radius_timeout', 'timeout'),
                             ('radius_retries', 'retries')):
                if src in body:
                    try: overrides[dst] = int(body[src])
                    except (ValueError, TypeError): pass
            if body.get('radius_secret'):
                overrides['secret'] = str(body['radius_secret'])
            if body.get('radius_secret2'):
                overrides['secret2'] = str(body['radius_secret2'])
            ok, msg = radius_test_connection(overrides)
            h._json(200, {'ok': ok, 'message': msg})
        except Exception as e:
            log.error(f"RADIUS test_connection crashed: {e}")
            h._json(500, {'ok': False, 'message': 'test failed \u2014 check server logs'})
        return True

    # ── POST /api/radius/test_auth ────────────────────────────────
    if path == '/api/radius/test_auth' and method == 'POST':
        user, _ = h._require('admin')
        if not user: return True
        try:
            from core.radius_auth import radius_test_auth
            username = str(body.get('username', '') or '').strip()
            password = str(body.get('password', '') or '')
            if not username or not password:
                h._json(400, {'error': 'username and password are required'}); return True
            result = radius_test_auth(username, password)
            h._json(200, result)
        except Exception as e:
            log.error(f"RADIUS test_auth crashed: {e}")
            h._json(500, {'ok': False, 'message': 'test failed \u2014 check server logs'})
        return True

    # ── POST /api/radius/test_auth_challenge ──────────────────────
    if path == '/api/radius/test_auth_challenge' and method == 'POST':
        user, _ = h._require('admin')
        if not user: return True
        try:
            from core.radius_auth import radius_continue_challenge
            cid = str(body.get('challenge_id', '') or '').strip()
            response = str(body.get('response', '') or '')
            if not cid or not response:
                h._json(400, {'error': 'challenge_id and response are required'}); return True
            res = radius_continue_challenge(cid, response)
            if res is None:
                h._json(200, {'ok': False, 'message': 'authentication rejected or challenge expired'})
                return True
            if res['ok']:
                h._json(200, {'ok': True, 'attrs': res['attrs'], 'message': 'authentication succeeded'})
                return True
            h._json(200, {'ok': False,
                          'challenge': res['challenge'],
                          'message': 'server returned another Access-Challenge'})
        except Exception as e:
            log.error(f"RADIUS test_auth_challenge crashed: {e}")
            h._json(500, {'ok': False, 'message': 'test failed \u2014 check server logs'})
        return True

    # ── GET /api/radius/attribute_mappings ────────────────────────
    if path == '/api/radius/attribute_mappings' and method == 'GET':
        user, _ = h._require('admin')
        if not user: return True
        try:
            from db import db_get_radius_mapped_groups, db_list_groups
            mapped = db_get_radius_mapped_groups()
            mapped_ids = {m['id'] for m in mapped}
            all_groups = db_list_groups()
            unmapped = [{'id': g['id'], 'name': g['name'],
                         'default_role': g['default_role']}
                        for g in all_groups if g['id'] not in mapped_ids]
            h._json(200, {'mappings': mapped, 'available_groups': unmapped})
        except Exception as e:
            log.error(f"RADIUS attribute_mappings list failed: {e}")
            h._json(500, {'error': 'failed to load mappings'})
        return True

    # ── POST /api/radius/attribute_mappings ───────────────────────
    # Set/clear RADIUS mapping on an existing group.
    # Body: {group_id, attribute, value}. Empty attribute clears the mapping.
    if path == '/api/radius/attribute_mappings' and method == 'POST':
        user, _ = h._require('admin')
        if not user: return True
        try:
            group_id = body.get('group_id')
            if group_id is None:
                h._json(400, {'error': 'group_id is required'}); return True
            try:
                group_id = int(group_id)
            except (ValueError, TypeError):
                h._json(400, {'error': 'group_id must be an integer'}); return True
            attribute = str(body.get('attribute', '') or '').strip()
            value = str(body.get('value', '') or '').strip()

            from db import db_list_groups, db_update_group
            groups = [g for g in db_list_groups() if g['id'] == group_id]
            if not groups:
                h._json(404, {'error': 'group not found'}); return True
            g = groups[0]
            ok = db_update_group(group_id, g['name'], g['description'],
                                 radius_attribute=attribute,
                                 radius_value=value)
            if not ok:
                h._json(500, {'error': 'failed to save mapping'}); return True
            db_log_audit(user, h.client_address[0], 'radius_mapping_update', '',
                         f"group_id={group_id} attr={attribute} value={value}")
            h._json(200, {'ok': True})
        except Exception as e:
            log.error(f"RADIUS mapping save crashed: {e}")
            h._json(500, {'error': 'save failed \u2014 check server logs'})
        return True

    return False
