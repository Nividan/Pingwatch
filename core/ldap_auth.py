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


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_cfg() -> dict:
    """Build config dict from runtime settings, decrypting the bind password."""
    encrypted = _settings.get('ldap_bind_pass', '')
    bind_pass = ''
    if encrypted:
        try:
            from db import decrypt_pw
            bind_pass = decrypt_pw(encrypted) or ''
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
    For ssl_mode=2 (StartTLS) the upgrade is applied after connect.
    Raises ldap3 exceptions on failure.
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
    conn.open()
    if ssl_mode == 2:
        conn.start_tls()
    conn.bind()
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
        return False, "ldap3 library not installed — run: pip install ldap3"

    if cfg is None:
        cfg = _get_cfg()
    if not cfg.get('server'):
        return False, "LDAP server address not configured"
    try:
        srv = _build_server(cfg)
        conn = _open_connection(
            srv, cfg['bind_dn'], cfg['bind_pass'], cfg['ssl'], cfg['timeout']
        )
        conn.unbind()
        log.debug(f"LDAP test_connection OK: server={cfg['server']}:{cfg['port']}")
        return True, "Connection successful"
    except Exception as e:
        log.warning(f"LDAP test_connection failed ({cfg.get('server')}): {e}")
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
        return False, "ldap3 library not installed — run: pip install ldap3"

    if not password:
        return False, "Password is required"
    if cfg is None:
        cfg = _get_cfg()
    if not cfg.get('server'):
        return False, "LDAP server address not configured"
    if not cfg.get('base_dn'):
        return False, "Base DN not configured"

    try:
        srv = _build_server(cfg)

        # Step 1: Bind as service account
        svc = _open_connection(
            srv, cfg['bind_dn'], cfg['bind_pass'], cfg['ssl'], cfg['timeout']
        )

        # Step 2: Search for user DN
        safe_user = _escape(username)
        search_filter = cfg['user_filter'].format(username=safe_user)
        svc.search(cfg['base_dn'], search_filter, attributes=['distinguishedName'])

        if not svc.entries:
            svc.unbind()
            log.debug(f"LDAP test_auth_user: user {username!r} not found in {cfg['base_dn']!r}")
            return False, f"User '{username}' not found in directory"

        user_dn = str(svc.entries[0].entry_dn)
        svc.unbind()
        log.debug(f"LDAP test_auth_user: found DN for {username!r}")

        # Step 3: Bind as the user
        user_conn = _open_connection(srv, user_dn, password, cfg['ssl'], cfg['timeout'])
        user_conn.unbind()
        log.debug(f"LDAP test_auth_user: bind succeeded for {username!r}")
        return True, f"Authentication successful for {username}"

    except Exception as e:
        log.warning(f"LDAP test_auth_user for {username!r} failed: {e}")
        return False, str(e)


def ldap_authenticate(username: str, password: str) -> bool:
    """
    Authenticate username/password against the configured LDAP server.
    Returns True on success, False on any failure.
    Never raises. Never logs the password.
    Only called for users with auth_type='ldap'.
    """
    if not password:
        return False
    cfg = _get_cfg()
    if not cfg['enabled']:
        log.warning(f"LDAP auth attempted for {username!r} but LDAP is disabled")
        return False
    if not cfg['server']:
        log.warning(f"LDAP auth attempted for {username!r} but no server configured")
        return False
    try:
        ok, msg = ldap_test_auth_user(username, password, cfg)
        if ok:
            log.debug(f"LDAP authenticate OK: {username!r}")
        else:
            log.debug(f"LDAP authenticate failed: {username!r} — {msg}")
        return ok
    except Exception as e:
        log.error(f"LDAP authenticate error for {username!r}: {e}")
        return False
