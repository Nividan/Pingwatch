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
        'enabled':     bool(int(_settings.get('ldap_enabled', 0) or 0)),
        'server':      _settings.get('ldap_server', ''),
        'port':        int(_settings.get('ldap_port', 389) or 389),
        'ssl':         int(_settings.get('ldap_ssl', 0) or 0),
        'base_dn':     _settings.get('ldap_base_dn', ''),
        'bind_dn':     _settings.get('ldap_bind_dn', ''),
        'bind_pass':   bind_pass,
        'user_filter': _settings.get('ldap_user_filter', '(sAMAccountName={username})'),
        'timeout':     int(_settings.get('ldap_timeout', 10) or 10),
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

    # ── Step 2: Search for user DN ────────────────────────────────
    try:
        safe_user = _escape(username)
        search_filter = cfg['user_filter'].format(username=safe_user)
        _ldap_dbg(f"LDAP test_auth_user: searching base_dn={cfg['base_dn']!r} "
                  f"filter={search_filter!r}")
        svc.search(cfg['base_dn'], search_filter, attributes=['distinguishedName'])

        if not svc.entries:
            svc.unbind()
            log.warning(f"LDAP test_auth_user: user {username!r} not found — "
                        f"base_dn={cfg['base_dn']!r} filter={search_filter!r}")
            return False, f"User '{username}' not found in directory"

        user_dn = str(svc.entries[0].entry_dn)
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
        return True, f"Authentication successful for {username}"
    except Exception as e:
        log.warning(f"LDAP test_auth_user: user bind FAILED for {username!r} "
                    f"(dn={user_dn!r}): {e}")
        return False, f"Authentication failed: {e}"


def ldap_authenticate(username: str, password: str) -> bool:
    """
    Authenticate username/password against the configured LDAP server.
    Returns True on success, False on any failure.
    Never raises. Never logs the password.
    Only called for users with auth_type='ldap'.
    """
    if not password:
        log.warning(f"LDAP authenticate: called with empty password for {username!r} — rejected")
        return False

    cfg = _get_cfg()

    if not cfg['enabled']:
        log.warning(f"LDAP authenticate: LDAP is disabled — rejecting login for {username!r}")
        return False

    if not cfg['server']:
        log.error(f"LDAP authenticate: no server configured — cannot authenticate {username!r}. "
                  "Configure LDAP server in Settings → Users → LDAP Settings")
        return False

    if not cfg['base_dn']:
        log.error(f"LDAP authenticate: no base DN configured — cannot authenticate {username!r}")
        return False

    if not cfg['bind_dn']:
        log.warning(f"LDAP authenticate: no bind DN configured for {username!r} — "
                    "attempting anonymous service bind")

    _ldap_dbg(f"LDAP authenticate: starting for {username!r} via {cfg['server']}:{cfg['port']}")
    try:
        ok, msg = ldap_test_auth_user(username, password, cfg)
        if ok:
            log.info(f"LDAP authenticate: SUCCESS for {username!r}")
        else:
            log.info(f"LDAP authenticate: FAILED for {username!r} — {msg}")
        return ok
    except Exception as e:
        log.error(f"LDAP authenticate: unexpected error for {username!r}: {e}")
        return False
