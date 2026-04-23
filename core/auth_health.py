"""
core/auth_health.py — background health checks for auth backends.

Two phases:
  1. boot_sanity_pass()           — synchronous, fast, no network. Runs once
                                    at startup. Populates status badges so
                                    admins see real state before the first
                                    login. Config + local crypto only.
  2. start_auth_refresh_loop()    — spawns a daemon thread that runs a full
                                    refresh on a configurable interval.
                                    LDAP: real bind. OIDC: discovery refetch.
                                    SAML: cert re-parse. RADIUS: config-only
                                    (skip network to avoid phantom auths).

Interval is read from app_settings.auth_refresh_interval_min (default 60).
A value of 0 disables the loop but keeps the boot pass.

Every non-ok outcome produces at least a WARNING log line so admins can
trace failures without enabling DEBUG.
"""

from __future__ import annotations

import threading
import time
import traceback

import core.settings as _settings
from core.logger import log


# ── Thread lifecycle state ─────────────────────────────────────────

_stop   = threading.Event()   # shutdown signal
_wake   = threading.Event()   # "run now" signal
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


# ── Phase 1: boot sanity pass ──────────────────────────────────────

def boot_sanity_pass() -> dict:
    """Run fast config-only checks on all four auth backends. Target <200ms.

    Writes status via each backend's _record_ok / _record_err so GET /api/settings
    returns populated badges immediately after boot.

    Returns a summary dict for logging: {ldap: bool, radius: bool, saml: bool, oidc: bool}.
    Disabled backends are reported as None.
    """
    t0 = time.time()
    results: dict = {}
    for name, fn in (("ldap",   _check_ldap_config),
                     ("radius", _check_radius_config),
                     ("saml",   _check_saml_config),
                     ("oidc",   _check_oidc_config)):
        try:
            results[name] = fn()
        except Exception as e:
            log.error(f"Auth boot check for {name} crashed: {e}")
            results[name] = False
    # Seed cert-expiry alert state from current cert days so admins get
    # notified immediately after boot if a cert is already near expiry.
    if results.get("saml"):
        try:
            _dispatch_saml_cert_alerts()
        except Exception as e:
            log.warning(f"cert_alert: boot dispatch failed: {e}")
    elapsed_ms = int((time.time() - t0) * 1000)
    enabled = [k for k, v in results.items() if v is not None]
    if enabled:
        log.info(f"Auth boot sanity pass complete ({elapsed_ms}ms): "
                 f"{', '.join(f'{k}={results[k]}' for k in enabled)}")
    return results


def _check_ldap_config() -> bool | None:
    """Returns True if config is valid, False if broken, None if disabled."""
    if not int(_settings.get("ldap_enabled", 0) or 0):
        return None
    from core import ldap_auth
    host = (_settings.get("ldap_server", "") or "").strip()
    bind_dn = (_settings.get("ldap_bind_dn", "") or "").strip()
    if not host:
        msg = "LDAP enabled but ldap_server is empty"
        ldap_auth._record_err(msg)
        log.warning(msg)
        return False
    enc = _settings.get("ldap_bind_pass", "") or ""
    if enc:
        try:
            from db.backups import decrypt_pw
            pw = decrypt_pw(enc)
            if not pw:
                msg = "LDAP bind password decrypted to empty — Fernet key rotated?"
                ldap_auth._record_err(msg)
                log.error(msg)
                return False
        except Exception as e:
            msg = f"LDAP bind password could not be decrypted: {e}"
            ldap_auth._record_err(msg)
            log.error(msg)
            return False
    log.info(f"LDAP config valid at startup (host={host}, bind_dn={bind_dn or '<anonymous>'})")
    return True


def _check_radius_config() -> bool | None:
    if not int(_settings.get("radius_enabled", 0) or 0):
        return None
    from core import radius_auth
    host = (_settings.get("radius_server", "") or "").strip()
    if not host:
        msg = "RADIUS enabled but radius_server is empty"
        radius_auth._record_err(msg)
        log.warning(msg)
        return False
    try:
        port = int(_settings.get("radius_port", 1812) or 1812)
    except (TypeError, ValueError):
        port = -1
    if not (1 <= port <= 65535):
        msg = f"RADIUS port out of range: {port}"
        radius_auth._record_err(msg)
        log.error(msg)
        return False
    enc = _settings.get("radius_secret", "") or ""
    if enc:
        try:
            from db.backups import decrypt_pw
            sec = decrypt_pw(enc)
            if not sec:
                msg = "RADIUS shared secret decrypted to empty — Fernet key rotated?"
                radius_auth._record_err(msg)
                log.error(msg)
                return False
        except Exception as e:
            msg = f"RADIUS shared secret could not be decrypted: {e}"
            radius_auth._record_err(msg)
            log.error(msg)
            return False
    log.info(f"RADIUS config valid at startup (host={host}:{port})")
    return True


def _check_saml_config() -> bool | None:
    if not int(_settings.get("saml_enabled", 0) or 0):
        return None
    from core import saml_auth
    try:
        import signxml  # noqa: F401
    except ImportError:
        msg = "SAML enabled but signxml is not installed — pip install signxml"
        saml_auth._record_err(msg)
        log.error(msg)
        return False

    idp_cert = (_settings.get("saml_idp_cert_pem", "") or "").strip()
    sp_cert  = (_settings.get("saml_sp_cert_pem", "") or "").strip()
    from core.tls import parse_cert_info

    if idp_cert:
        try:
            info = parse_cert_info(idp_cert)
            days = int(info.get("days_left", 0))
            if days <= 0:
                msg = f"SAML IdP cert has expired (valid until {info.get('not_after', '?')})"
                saml_auth._record_err(msg)
                log.error(msg)
                return False
            if days < 30:
                msg = (f"SAML IdP cert expires in {days} days "
                       f"(valid until {info.get('not_after', '?')})")
                saml_auth._record_warn(msg)
                log.warning(msg)
        except Exception as e:
            msg = f"SAML IdP cert could not be parsed: {e}"
            saml_auth._record_err(msg)
            log.error(msg)
            return False
    if sp_cert:
        try:
            info = parse_cert_info(sp_cert)
            days = int(info.get("days_left", 0))
            if days <= 0:
                msg = "SAML SP cert has expired — re-generate from Settings"
                saml_auth._record_err(msg)
                log.error(msg)
                return False
            if days < 30:
                msg = (f"SAML SP cert expires in {days} days "
                       f"(valid until {info.get('not_after', '?')})")
                saml_auth._record_warn(msg)
                log.warning(msg)
        except Exception as e:
            msg = f"SAML SP cert could not be parsed: {e}"
            saml_auth._record_err(msg)
            log.error(msg)
            return False

    enc_key = _settings.get("saml_sp_key_pem_enc", "") or ""
    if enc_key:
        try:
            from db.backups import decrypt_pw
            key = decrypt_pw(enc_key)
            if not key or "BEGIN" not in key:
                msg = "SAML SP private key decrypted but does not look like a PEM"
                saml_auth._record_err(msg)
                log.error(msg)
                return False
        except Exception as e:
            msg = f"SAML SP private key could not be decrypted: {e}"
            saml_auth._record_err(msg)
            log.error(msg)
            return False
    log.info("SAML config valid at startup")
    return True


def _check_oidc_config() -> bool | None:
    if not int(_settings.get("oidc_enabled", 0) or 0):
        return None
    from core import oidc_auth
    try:
        import authlib  # noqa: F401
    except ImportError:
        msg = "OIDC enabled but authlib is not installed — pip install authlib"
        oidc_auth._record_err(msg)
        log.error(msg)
        return False

    issuer = (_settings.get("oidc_issuer_url", "") or "").strip()
    if not issuer.lower().startswith(("https://", "http://")):
        msg = f"OIDC issuer_url malformed (must start with https://): {issuer!r}"
        oidc_auth._record_err(msg)
        log.warning(msg)
        return False

    enc = _settings.get("oidc_client_secret_enc", "") or ""
    if enc:
        try:
            from db.backups import decrypt_pw
            sec = decrypt_pw(enc)
            if not sec:
                msg = "OIDC client secret decrypted to empty — Fernet key rotated?"
                oidc_auth._record_err(msg)
                log.error(msg)
                return False
        except Exception as e:
            msg = f"OIDC client secret could not be decrypted: {e}"
            oidc_auth._record_err(msg)
            log.error(msg)
            return False

    cache = _settings.get("oidc_discovery_cache", "") or ""
    if cache:
        try:
            import json
            json.loads(cache)
        except Exception as e:
            log.warning(f"OIDC cached discovery doc is not valid JSON, will refetch: {e}")
    log.info("OIDC config valid at startup")
    return True


# ── Phase 2: hourly refresh loop ───────────────────────────────────

def start_auth_refresh_loop() -> None:
    """Launch the background refresh thread. Idempotent."""
    global _thread
    with _thread_lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _wake.clear()
        _thread = threading.Thread(target=_refresh_loop,
                                   name="auth-refresh",
                                   daemon=True)
        _thread.start()
        log.info("Auth refresh loop started")


def stop_auth_refresh_loop(timeout: float = 5.0) -> None:
    """Signal the loop to exit and wait up to `timeout` seconds."""
    global _thread
    _stop.set()
    _wake.set()
    with _thread_lock:
        t = _thread
    if t and t.is_alive():
        t.join(timeout=timeout)
    log.info("Auth refresh loop stopped")


def trigger_run_now() -> None:
    """External signal — skip the current wait and run an iteration now."""
    _wake.set()


def _refresh_loop() -> None:
    """Thread body. First iteration runs immediately; subsequent iterations
    wait on the configurable interval or an explicit wake event.
    """
    # Tiny delay on first iteration so the HTTP listener is up by the time
    # we start logging refresh results — keeps boot log readable.
    _wait_any(2.0)

    while not _stop.is_set():
        try:
            _run_one_iteration()
        except Exception as e:
            log.error(f"Auth refresh loop crashed: {e}\n{traceback.format_exc()}")

        interval_min = _get_interval_min()
        if interval_min <= 0:
            # Disabled — poll flags every 5 min so admin re-enable takes effect.
            _wait_any(300.0)
        else:
            _wait_any(float(interval_min * 60))
        _wake.clear()


def _get_interval_min() -> int:
    """Read the configured interval, clamp to the allow-list."""
    try:
        v = int(_settings.get("auth_refresh_interval_min", 60) or 60)
    except (TypeError, ValueError):
        v = 60
    allowed = (0, 15, 30, 60, 240, 720)
    return v if v in allowed else 60


def _wait_any(timeout: float) -> None:
    """Wait for either _stop or _wake, up to timeout seconds."""
    end = time.time() + timeout
    while not _stop.is_set() and not _wake.is_set():
        remaining = end - time.time()
        if remaining <= 0:
            return
        # Event.wait with short poll lets both events get picked up quickly.
        if _stop.wait(timeout=min(remaining, 1.0)):
            return
        if _wake.is_set():
            return


def _run_one_iteration() -> None:
    """Run all four backend refreshes serially. Each one is isolated so one
    failure doesn't prevent the others from running."""
    log.debug("Auth refresh pass starting")
    t0 = time.time()
    for name, fn in (("ldap",   _refresh_ldap),
                     ("radius", _refresh_radius),
                     ("saml",   _refresh_saml),
                     ("oidc",   _refresh_oidc)):
        try:
            fn()
        except Exception as e:
            log.error(f"Auth refresh for {name} crashed: {e}\n{traceback.format_exc()}")
    log.debug(f"Auth refresh pass complete ({int((time.time() - t0) * 1000)}ms)")


def _refresh_ldap() -> None:
    if not int(_settings.get("ldap_enabled", 0) or 0):
        return
    from core.ldap_auth import ldap_test_connection, _record_ok, _record_err
    t0 = time.time()
    ok, msg = ldap_test_connection()
    elapsed_ms = int((time.time() - t0) * 1000)
    if ok:
        _record_ok()
        log.debug(f"LDAP refresh: bind OK ({elapsed_ms}ms)")
    else:
        _record_err(msg)
        log.warning(f"LDAP refresh: bind failed — {msg}")


def _refresh_radius() -> None:
    # Config-only by design (no network probe — phantom auth events in the
    # RADIUS server logs are intrusive for a 1-per-hour poll). Matches SAML's
    # refresh semantic: green badge means "local config is valid".
    if not int(_settings.get("radius_enabled", 0) or 0):
        return
    from core import radius_auth
    if _check_radius_config() is True:
        radius_auth._record_ok()
        log.debug("RADIUS refresh: config valid")


def _refresh_saml() -> None:
    # Cert re-parse + expiry check — same as boot sanity, but status is not
    # flipped to 'ok' by the check alone. A successful refresh that finds
    # every cert valid does record_ok to keep the badge green.
    if not int(_settings.get("saml_enabled", 0) or 0):
        return
    from core import saml_auth
    ok = _check_saml_config()
    if ok is True:
        saml_auth._record_ok()
        log.debug("SAML refresh: certs valid")
    _dispatch_saml_cert_alerts()


def _dispatch_saml_cert_alerts() -> None:
    """Re-parse SAML certs and feed day counts to cert_alert_checker so that
    threshold crossings emit alert events. Safe to call even when the config
    check failed — alerts are independent of overall ok/err state."""
    from monitoring.cert_alert_checker import check_cert
    from core.tls import parse_cert_info
    for side, pem_key in (("idp", "saml_idp_cert_pem"),
                          ("sp",  "saml_sp_cert_pem")):
        pem = (_settings.get(pem_key, "") or "").strip()
        if not pem:
            continue
        try:
            info = parse_cert_info(pem)
            check_cert(("saml", side),
                       int(info.get("days_left", 0)),
                       info.get("not_after", "?"),
                       f"SAML {side.upper()} cert")
        except Exception as e:
            log.warning(f"cert_alert: failed to re-parse SAML {side} cert: {e}")


def _refresh_oidc() -> None:
    if not int(_settings.get("oidc_enabled", 0) or 0):
        return
    from core import oidc_auth
    # First: config sanity (decrypt check, issuer URL, cached JSON).
    if _check_oidc_config() is False:
        return
    # Second: refetch discovery + JWKS. This is the whole reason OIDC has
    # a refresh loop — JWKS keys rotate silently.
    try:
        info = oidc_auth.oidc_refresh_discovery()
        jwks_keys = len((info or {}).get("jwks", {}).get("keys", []) or [])
        oidc_auth._record_ok()
        log.info(f"OIDC discovery refreshed ({jwks_keys} JWKS keys)")
    except Exception as e:
        oidc_auth._record_err(f"discovery refresh failed: {e}")
        log.error(f"OIDC discovery refresh failed: {e}")
