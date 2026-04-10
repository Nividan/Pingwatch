"""
core/ldap_auth.py — LDAP / Active Directory authentication helpers.

All public functions handle ImportError (ldap3 not installed) and all
exceptions gracefully — they never raise, never log passwords.

ssl setting values:
  0 = plain LDAP (no TLS)
  1 = LDAPS (TLS-wrapped from the start, default port 636)
  2 = StartTLS (upgrade plain connection after connect, port 389)
"""

import core.settings as _settings
from core.logger import log

_SSL_LABELS = {0: 'none', 1: 'LDAPS', 2: 'StartTLS'}


def _ldap_dbg(msg: str) -> None:
    """Emit a debug log only when ldap_debug is enabled in settings."""
    if int(_settings.get('ldap_debug', 0) or 0):
        log.debug(msg)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_cfg() -> dict:
    """Build config dict from runtime settings, decrypting the bind password."""
    encrypted = _settings.get('ldap_bind_pass', '')
    bind_pass = ''
    if encrypted:
        try:
            from db import decrypt_pw
            bind_pass = decrypt_pw(encrypted) or ''
            if not bind_pass:
                log.warning("LDAP: bind password is set but decrypted to empty — "
                            "Fernet key may have changed")
        except Exception as e:
            log.warning(f"LDAP: could not decrypt bind password: {e}")
    return {
        'enabled':        bool(int(_settings.get('ldap_enabled', 0) or 0)),
        'server':         _settings.get('ldap_server', ''),
        'port':           int(_settings.get('ldap_port', 389) or 389),
        'ssl':            int(_settings.get('ldap_ssl', 0) or 0),
        'base_dn':        _settings.get('ldap_base_dn', ''),
        'bind_dn':        _settings.get('ldap_bind_dn', ''),
        'bind_pass':      bind_pass,
        'user_filter':    _settings.get('ldap_user_filter', '(sAMAccountName={username})'),
        'timeout':        int(_settings.get('ldap_timeout', 10) or 10),
        'group_base_dn':  _settings.get('ldap_group_base_dn', ''),
        'group_filter':   _settings.get('ldap_group_filter', '(objectClass=group)'),
        'auto_provision': bool(int(_settings.get('ldap_auto_provision', 0) or 0)),
        'sync_interval':  int(_settings.get('ldap_sync_interval', 60) or 60),
        'nested_groups':  bool(int(_settings.get('ldap_nested_groups', 0) or 0)),
    }


def _escape(value: str) -> str:
    """Escape special characters in LDAP filter values (RFC 4515)."""
    for char, repl in [
        ('\\', '\\5c'), ('*', '\\2a'), ('(', '\\28'), (')', '\\29'), ('\x00', '\\00'),
    ]:
        value = value.replace(char, repl)
    return value


def _build_server(cfg: dict):
    """Create an ldap3 Server object from config."""
    from ldap3 import Server, Tls
    import ssl as _ssl_mod
    ssl_mode = cfg['ssl']
    tls_obj = Tls(validate=_ssl_mod.CERT_OPTIONAL) if ssl_mode in (1, 2) else None
    return Server(
        cfg['server'],
        port=cfg['port'],
        use_ssl=(ssl_mode == 1),
        tls=tls_obj,
        connect_timeout=cfg['timeout'],
    )


def _open_connection(srv, bind_dn: str, bind_pass: str, ssl_mode: int, timeout: int):
    """
    Open and return a bound ldap3 Connection.
    For ssl_mode=2 (StartTLS) the TLS upgrade is applied after connect.
    Raises ldap3 exceptions on failure — caller is responsible for logging.
    """
    from ldap3 import Connection, SIMPLE
    conn = Connection(
        srv,
        user=bind_dn,
        password=bind_pass,
        authentication=SIMPLE,
        receive_timeout=timeout,
        auto_bind=False,
    )
    _dn_label = repr(bind_dn) if bind_dn else "'<anonymous>'"
    _ldap_dbg(f"LDAP _open_connection: opening TCP to {srv.host}:{srv.port} "
              f"(ssl={_SSL_LABELS.get(ssl_mode, ssl_mode)}, bind_dn={_dn_label})")
    conn.open()
    _ldap_dbg(f"LDAP _open_connection: TCP open OK — {srv.host}:{srv.port}")

    if ssl_mode == 2:
        _ldap_dbg(f"LDAP _open_connection: upgrading to TLS (StartTLS) — {srv.host}:{srv.port}")
        conn.start_tls()
        _ldap_dbg(f"LDAP _open_connection: StartTLS upgrade OK — {srv.host}:{srv.port}")

    _ldap_dbg(f"LDAP _open_connection: sending BIND for {_dn_label}")
    conn.bind()
    if not conn.bound:
        raise RuntimeError(
            f"LDAP BIND returned success=False for bind_dn={bind_dn!r} "
            f"(result: {conn.result})"
        )
    _ldap_dbg(f"LDAP _open_connection: BIND OK for {_dn_label}")
    return conn


# ── Public API ────────────────────────────────────────────────────────────────

def ldap_test_connection(cfg: dict | None = None) -> tuple:
    """
    Test that a service-account bind succeeds.
    Returns (ok: bool, message: str).
    Safe to call when ldap3 is not installed.
    """
    try:
        from ldap3 import Server  # noqa: F401 — just checking import
    except ImportError:
        log.warning("LDAP test_connection: ldap3 library not installed")
        return False, "ldap3 library not installed — run: pip install ldap3"

    if cfg is None:
        cfg = _get_cfg()

    if not cfg.get('server'):
        log.warning("LDAP test_connection: no server configured — cannot test")
        return False, "LDAP server address not configured"

    ssl_label = _SSL_LABELS.get(cfg['ssl'], cfg['ssl'])
    _dn_label = repr(cfg['bind_dn']) if cfg['bind_dn'] else "'<anonymous>'"
    _ldap_dbg(f"LDAP test_connection: attempting {cfg['server']}:{cfg['port']} "
              f"ssl={ssl_label} bind_dn={_dn_label} timeout={cfg['timeout']}s")
    try:
        srv = _build_server(cfg)
        conn = _open_connection(
            srv, cfg['bind_dn'], cfg['bind_pass'], cfg['ssl'], cfg['timeout']
        )
        conn.unbind()
        log.info(f"LDAP test_connection: OK — {cfg['server']}:{cfg['port']} "
                 f"ssl={ssl_label}")
        return True, "Connection successful"
    except Exception as e:
        log.warning(f"LDAP test_connection: FAILED — {cfg['server']}:{cfg['port']} "
                    f"ssl={ssl_label}: {e}")
        return False, str(e)


def ldap_test_auth_user(username: str, password: str,
                         cfg: dict | None = None) -> tuple:
    """
    Test full authentication flow for a user:
      1. Bind as service account
      2. Search for user DN using configured filter
      3. Bind as the user with provided password
    Returns (ok: bool, message: str).
    Never logs the password.
    """
    try:
        from ldap3 import Server  # noqa: F401
    except ImportError:
        log.warning("LDAP test_auth_user: ldap3 library not installed")
        return False, "ldap3 library not installed — run: pip install ldap3"

    if not password:
        log.warning(f"LDAP test_auth_user: called with empty password for {username!r}")
        return False, "Password is required"

    if cfg is None:
        cfg = _get_cfg()

    if not cfg.get('server'):
        log.warning(f"LDAP test_auth_user: no server configured "
                    f"(user={username!r})")
        return False, "LDAP server address not configured"
    if not cfg.get('base_dn'):
        log.warning(f"LDAP test_auth_user: no base DN configured "
                    f"(user={username!r}, server={cfg['server']})")
        return False, "Base DN not configured"

    ssl_label = _SSL_LABELS.get(cfg['ssl'], cfg['ssl'])
    _ldap_dbg(f"LDAP test_auth_user: starting auth for {username!r} — "
              f"server={cfg['server']}:{cfg['port']} ssl={ssl_label} "
              f"base_dn={cfg['base_dn']!r}")

    # ── Step 1: Bind as service account ──────────────────────────
    try:
        srv = _build_server(cfg)
        svc = _open_connection(
            srv, cfg['bind_dn'], cfg['bind_pass'], cfg['ssl'], cfg['timeout']
        )
    except Exception as e:
        log.warning(f"LDAP test_auth_user: service-account bind FAILED "
                    f"(bind_dn={cfg['bind_dn']!r}, server={cfg['server']}:{cfg['port']}): {e}")
        return False, f"Service account bind failed: {e}"

    # ── Step 2: Search for user DN + attributes ────────────────────
    user_attrs = {}
    try:
        safe_user = _escape(username)
        search_filter = cfg['user_filter'].format(username=safe_user)
        _ldap_dbg(f"LDAP test_auth_user: searching base_dn={cfg['base_dn']!r} "
                  f"filter={search_filter!r}")
        svc.search(cfg['base_dn'], search_filter,
                   attributes=['distinguishedName', 'displayName', 'mail', 'memberOf'])

        if not svc.entries:
            svc.unbind()
            log.warning(f"LDAP test_auth_user: user {username!r} not found — "
                        f"base_dn={cfg['base_dn']!r} filter={search_filter!r}")
            return False, f"User '{username}' not found in directory"

        entry = svc.entries[0]
        user_dn = str(entry.entry_dn)
        # Extract optional attributes
        user_attrs['dn'] = user_dn
        user_attrs['display_name'] = str(entry.displayName) if hasattr(entry, 'displayName') and entry.displayName.value else ''
        user_attrs['email'] = str(entry.mail) if hasattr(entry, 'mail') and entry.mail.value else ''
        member_of_raw = entry.memberOf.values if hasattr(entry, 'memberOf') and entry.memberOf.value else []
        user_attrs['member_of'] = [str(m) for m in member_of_raw]

        result_count = len(svc.entries)
        svc.unbind()
        if result_count > 1:
            log.warning(f"LDAP test_auth_user: search for {username!r} returned "
                        f"{result_count} entries — using first: {user_dn!r}")
        else:
            _ldap_dbg(f"LDAP test_auth_user: found DN for {username!r}: {user_dn!r}")
    except Exception as e:
        try: svc.unbind()
        except Exception: pass
        log.warning(f"LDAP test_auth_user: user search FAILED "
                    f"(user={username!r}, base_dn={cfg['base_dn']!r}): {e}")
        return False, f"Directory search failed: {e}"

    # ── Step 3: Bind as the user ──────────────────────────────────
    try:
        user_conn = _open_connection(srv, user_dn, password, cfg['ssl'], cfg['timeout'])
        user_conn.unbind()
        _ldap_dbg(f"LDAP test_auth_user: user bind OK for {username!r} ({user_dn!r})")
        return True, f"Authentication successful for {username}", user_attrs
    except Exception as e:
        log.warning(f"LDAP test_auth_user: user bind FAILED for {username!r} "
                    f"(dn={user_dn!r}): {e}")
        return False, f"Authentication failed: {e}", {}


def ldap_authenticate(username: str, password: str):
    """
    Authenticate username/password against the configured LDAP server.
    Returns a dict on success (truthy):
        {"ok": True, "display_name": str, "email": str, "member_of": [str]}
    Returns None on failure (falsy).
    Never raises. Never logs the password.
    Only called for users with auth_type='ldap'.
    """
    if not password:
        log.warning(f"LDAP authenticate: called with empty password for {username!r} — rejected")
        return None

    cfg = _get_cfg()

    if not cfg['enabled']:
        log.warning(f"LDAP authenticate: LDAP is disabled — rejecting login for {username!r}")
        return None

    if not cfg['server']:
        log.error(f"LDAP authenticate: no server configured — cannot authenticate {username!r}. "
                  "Configure LDAP server in Settings → Users → LDAP Settings")
        return None

    if not cfg['base_dn']:
        log.error(f"LDAP authenticate: no base DN configured — cannot authenticate {username!r}")
        return None

    if not cfg['bind_dn']:
        log.warning(f"LDAP authenticate: no bind DN configured for {username!r} — "
                    "attempting anonymous service bind")

    _ldap_dbg(f"LDAP authenticate: starting for {username!r} via {cfg['server']}:{cfg['port']}")
    try:
        result = ldap_test_auth_user(username, password, cfg)
        # ldap_test_auth_user returns (ok, msg, attrs) — attrs may be absent for old callers
        ok  = result[0]
        msg = result[1]
        attrs = result[2] if len(result) > 2 else {}
        if ok:
            log.info(f"LDAP authenticate: SUCCESS for {username!r}")
            return {
                "ok":           True,
                "display_name": attrs.get("display_name", ""),
                "email":        attrs.get("email", ""),
                "member_of":    attrs.get("member_of", []),
                "dn":           attrs.get("dn", ""),
            }
        else:
            log.info(f"LDAP authenticate: FAILED for {username!r} — {msg}")
            return None
    except Exception as e:
        log.error(f"LDAP authenticate: unexpected error for {username!r}: {e}")
        return None


# ── Group discovery & sync ───────────────────────────────────────────────────

def ldap_search_groups(query: str = '', cfg: dict | None = None) -> tuple:
    """
    Browse/search LDAP directory for groups.
    Returns (True, [{dn, cn, description, member_count}]) or (False, error_msg).
    """
    try:
        from ldap3 import Server, SUBTREE  # noqa: F401
    except ImportError:
        return False, "ldap3 library not installed"

    if cfg is None:
        cfg = _get_cfg()
    if not cfg.get('server'):
        return False, "LDAP server not configured"

    group_base = cfg.get('group_base_dn') or cfg.get('base_dn', '')
    if not group_base:
        return False, "No base DN configured for group search"

    group_filter = cfg.get('group_filter', '(objectClass=group)')
    if query:
        safe_q = _escape(query)
        group_filter = f"(&{group_filter}(cn=*{safe_q}*))"

    _ldap_dbg(f"LDAP search_groups: base={group_base!r} filter={group_filter!r}")
    try:
        srv = _build_server(cfg)
        conn = _open_connection(srv, cfg['bind_dn'], cfg['bind_pass'],
                                cfg['ssl'], cfg['timeout'])
    except Exception as e:
        log.warning(f"LDAP search_groups: service bind failed: {e}")
        return False, f"Service account bind failed: {e}"

    try:
        conn.search(group_base, group_filter, search_scope=SUBTREE,
                    attributes=['cn', 'distinguishedName', 'description', 'member'],
                    paged_size=500)
        groups = []
        for entry in conn.entries:
            dn = str(entry.entry_dn)
            cn = str(entry.cn) if hasattr(entry, 'cn') and entry.cn.value else ''
            desc = str(entry.description) if hasattr(entry, 'description') and entry.description.value else ''
            members = entry.member.values if hasattr(entry, 'member') and entry.member.value else []
            groups.append({
                "dn":           dn,
                "cn":           cn,
                "description":  desc,
                "member_count": len(members),
            })
        conn.unbind()
        _ldap_dbg(f"LDAP search_groups: found {len(groups)} groups")
        return True, groups
    except Exception as e:
        try: conn.unbind()
        except Exception: pass
        log.warning(f"LDAP search_groups: search failed: {e}")
        return False, f"Group search failed: {e}"


def ldap_get_user_info(username: str, cfg: dict | None = None) -> tuple:
    """
    Fetch user attributes from LDAP (service-account bind, no password needed).
    Returns (True, {dn, display_name, email, member_of: [dn_list]}) or (False, error_msg).
    """
    try:
        from ldap3 import Server  # noqa: F401
    except ImportError:
        return False, "ldap3 library not installed"

    if cfg is None:
        cfg = _get_cfg()
    if not cfg.get('server') or not cfg.get('base_dn'):
        return False, "LDAP server or base DN not configured"

    try:
        srv = _build_server(cfg)
        conn = _open_connection(srv, cfg['bind_dn'], cfg['bind_pass'],
                                cfg['ssl'], cfg['timeout'])
    except Exception as e:
        log.warning(f"LDAP get_user_info: service bind failed: {e}")
        return False, f"Service account bind failed: {e}"

    try:
        safe_user = _escape(username)
        search_filter = cfg['user_filter'].format(username=safe_user)
        _ldap_dbg(f"LDAP get_user_info: searching base_dn={cfg['base_dn']!r} "
                  f"filter={search_filter!r}")
        conn.search(cfg['base_dn'], search_filter,
                    attributes=['distinguishedName', 'displayName', 'mail', 'memberOf'])
        if not conn.entries:
            conn.unbind()
            _ldap_dbg(f"LDAP get_user_info: user {username!r} not found")
            return False, f"User '{username}' not found"
        entry = conn.entries[0]
        dn = str(entry.entry_dn)
        display_name = str(entry.displayName) if hasattr(entry, 'displayName') and entry.displayName.value else ''
        email = str(entry.mail) if hasattr(entry, 'mail') and entry.mail.value else ''
        member_of = [str(m) for m in (entry.memberOf.values if hasattr(entry, 'memberOf') and entry.memberOf.value else [])]
        conn.unbind()
        _ldap_dbg(f"LDAP get_user_info: {username!r} → dn={dn!r} "
                  f"display_name={display_name!r} memberOf={len(member_of)} groups")
        return True, {"dn": dn, "display_name": display_name, "email": email, "member_of": member_of}
    except Exception as e:
        try: conn.unbind()
        except Exception: pass
        log.warning(f"LDAP get_user_info: failed for {username!r}: {e}")
        return False, f"User lookup failed: {e}"


def ldap_check_nested_membership(user_dn: str, group_dn: str,
                                  cfg: dict | None = None) -> bool:
    """
    Check recursive AD group membership using LDAP_MATCHING_RULE_IN_CHAIN.
    Returns True if user_dn is a member of group_dn at any nesting depth.
    AD-specific — does not work with OpenLDAP.
    """
    try:
        from ldap3 import Server, BASE  # noqa: F401
    except ImportError:
        return False

    if cfg is None:
        cfg = _get_cfg()
    if not cfg.get('server'):
        return False

    try:
        srv = _build_server(cfg)
        conn = _open_connection(srv, cfg['bind_dn'], cfg['bind_pass'],
                                cfg['ssl'], cfg['timeout'])
    except Exception:
        return False

    try:
        safe_dn = _escape(user_dn)
        # OID 1.2.840.113556.1.4.1941 = LDAP_MATCHING_RULE_IN_CHAIN (recursive)
        nested_filter = f"(member:1.2.840.113556.1.4.1941:={safe_dn})"
        _ldap_dbg(f"LDAP nested check: user_dn={user_dn!r} group_dn={group_dn!r}")
        conn.search(group_dn, nested_filter, search_scope=BASE,
                    attributes=['distinguishedName'])
        found = len(conn.entries) > 0
        conn.unbind()
        _ldap_dbg(f"LDAP nested check: result={'MEMBER' if found else 'NOT MEMBER'}")
        return found
    except Exception as e:
        _ldap_dbg(f"LDAP nested check: error — {e}")
        try: conn.unbind()
        except Exception: pass
        return False


def _match_user_to_groups(member_of: list, user_dn: str,
                          mapped_groups: list, cfg: dict | None = None) -> dict | None:
    """
    Given a user's memberOf list and the DB's LDAP-mapped groups, find the best
    matching group (highest role).  Returns the group dict or None.
    If nested_groups is enabled and direct match fails, tries recursive check.
    """
    _ROLE_RANK = {'viewer': 0, 'operator': 1, 'admin': 2}

    # Normalise memberOf DNs to lowercase for case-insensitive comparison
    member_of_lower = {m.lower() for m in member_of}
    _ldap_dbg(f"LDAP match_user_to_groups: user has {len(member_of)} memberOf entries, "
              f"checking against {len(mapped_groups)} imported groups")

    best = None
    for g in mapped_groups:
        if g['ldap_dn'].lower() in member_of_lower:
            _ldap_dbg(f"LDAP match: direct match → group={g['name']!r} role={g['default_role']}")
            if best is None or _ROLE_RANK.get(g['default_role'], 0) > _ROLE_RANK.get(best['default_role'], 0):
                best = g

    # If no direct match and nested groups enabled, try recursive
    if best is None and cfg and cfg.get('nested_groups') and user_dn:
        _ldap_dbg("LDAP match: no direct match — trying nested group check")
        for g in mapped_groups:
            if ldap_check_nested_membership(user_dn, g['ldap_dn'], cfg):
                _ldap_dbg(f"LDAP match: nested match → group={g['name']!r} role={g['default_role']}")
                if best is None or _ROLE_RANK.get(g['default_role'], 0) > _ROLE_RANK.get(best['default_role'], 0):
                    best = g

    if best:
        _ldap_dbg(f"LDAP match: best group={best['name']!r} role={best['default_role']}")
    else:
        _ldap_dbg("LDAP match: no matching group found")
    return best


def ldap_sync_groups() -> dict:
    """
    Background sync: check all LDAP users in DB against their LDAP group memberships.
    - Users no longer in any imported group → group_id=NULL (disabled)
    - Users whose group/role changed → updated
    - display_name synced from LDAP
    Returns {"updated": N, "disabled": N, "errors": N}.
    """
    from db.groups import db_get_ldap_mapped_groups
    from db.users  import db_list_users
    from db.helpers import db_cursor
    from db.backend import is_pg

    stats = {"updated": 0, "disabled": 0, "errors": 0}

    cfg = _get_cfg()
    if not cfg['enabled'] or not cfg['server'] or not cfg['base_dn']:
        return stats

    mapped_groups = db_get_ldap_mapped_groups()
    if not mapped_groups:
        return stats

    # Get all LDAP users from DB
    all_users = db_list_users()
    ldap_users = [u for u in all_users if u.get('auth_type') == 'ldap']
    if not ldap_users:
        return stats

    _ldap_dbg(f"LDAP sync: checking {len(ldap_users)} LDAP users against "
              f"{len(mapped_groups)} mapped groups")

    for user in ldap_users:
        username = user['username']
        try:
            ok, info = ldap_get_user_info(username, cfg)
            if not ok:
                _ldap_dbg(f"LDAP sync: could not fetch info for {username!r}: {info}")
                stats["errors"] += 1
                continue

            best = _match_user_to_groups(
                info['member_of'], info['dn'], mapped_groups, cfg
            )

            ph = "%s" if is_pg() else "?"
            if best is None:
                # User not in any imported group → disable
                if user.get('group_id') is not None:
                    try:
                        with db_cursor("main") as cur:
                            cur.execute(
                                f"UPDATE users SET group_id=NULL WHERE username={ph}",
                                (username,)
                            )
                        stats["disabled"] += 1
                        log.info(f"LDAP sync: disabled {username!r} — "
                                 "no longer in any imported LDAP group")
                        try:
                            from db import db_log_audit
                            db_log_audit("system", "ldap_sync", "ldap_sync_disabled",
                                         username)
                        except Exception:
                            pass
                    except Exception as e:
                        log.error(f"LDAP sync: failed to disable {username!r}: {e}")
                        stats["errors"] += 1
            else:
                # User is in a group — update if changed
                changed = False
                new_group_id = best['id']
                new_role = best['default_role']
                new_display = info.get('display_name', '')

                sets = []
                params = []
                if user.get('group_id') != new_group_id:
                    sets.append(f"group_id={ph}")
                    params.append(new_group_id)
                    changed = True
                if user.get('role') != new_role:
                    sets.append(f"role={ph}")
                    params.append(new_role)
                    changed = True
                if new_display and user.get('full_name', '') != new_display:
                    sets.append(f"full_name={ph}")
                    params.append(new_display)
                    changed = True

                if changed:
                    params.append(username)
                    try:
                        with db_cursor("main") as cur:
                            cur.execute(
                                f"UPDATE users SET {', '.join(sets)} WHERE username={ph}",
                                params
                            )
                        stats["updated"] += 1
                        _ldap_dbg(f"LDAP sync: updated {username!r} → "
                                  f"group={best['name']!r} role={new_role}")
                    except Exception as e:
                        log.error(f"LDAP sync: failed to update {username!r}: {e}")
                        stats["errors"] += 1
        except Exception as e:
            log.error(f"LDAP sync: unexpected error for {username!r}: {e}")
            stats["errors"] += 1

    log.info(f"LDAP sync complete: {stats}")
    try:
        from db import db_log_audit
        db_log_audit("system", "ldap_sync", "ldap_sync_complete",
                     f"updated={stats['updated']} disabled={stats['disabled']} "
                     f"errors={stats['errors']}")
    except Exception:
        pass
    return stats


def ldap_sync_loop():
    """Background thread: runs ldap_sync_groups() on the configured interval."""
    import time
    _ldap_dbg("LDAP sync loop: thread started")
    while True:
        try:
            cfg = _get_cfg()
            interval = cfg.get('sync_interval', 60)
            if not cfg['enabled'] or interval <= 0:
                time.sleep(60)
                continue
            _ldap_dbg(f"LDAP sync loop: sleeping {interval} minutes until next sync")
            time.sleep(interval * 60)
            _ldap_dbg("LDAP sync loop: waking up — starting sync")
            ldap_sync_groups()
        except Exception as e:
            log.error(f"LDAP sync loop error: {e}")
            time.sleep(300)
