"""
ssl_trust.py — Augment outbound SSLContexts with user-uploaded trusted CAs.

PingWatch's SSL-capable sensors (HTTPS, HTTP-keyword, TLS, SMTP, VMware) trust
the OS CA store by default. This module lets admins upload private/corporate
root CAs (managed via /api/tls/ca-certs) so sensors can verify internal
endpoints without disabling SSL verification.

The combined PEM blob is cached in memory and rebuilt on demand. Mutations
through the API call invalidate_cache() so probes pick up changes without a
service restart.
"""

import json
import ssl
import threading

import core.settings as _settings


_lock = threading.Lock()
_cached_pem_blob: "str | None" = None  # concatenated PEMs, "" means "checked, none configured"


def _load_blob() -> "str | None":
    raw = _settings.get("trusted_ca_certs", "")
    if not raw:
        return None
    try:
        entries = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(entries, list):
        return None
    pems = [str(e.get("pem", "")).strip() for e in entries if isinstance(e, dict) and e.get("pem")]
    return ("\n".join(pems) + "\n") if pems else None


def get_trusted_ca_pem() -> "str | None":
    """Return concatenated PEM of all user-uploaded trusted CAs, or None."""
    global _cached_pem_blob
    with _lock:
        if _cached_pem_blob is None:
            _cached_pem_blob = _load_blob() or ""
        return _cached_pem_blob or None


def invalidate_cache() -> None:
    """Force the next get_trusted_ca_pem() call to re-read from settings."""
    global _cached_pem_blob
    with _lock:
        _cached_pem_blob = None


def apply_trusted_cas(ctx: ssl.SSLContext) -> None:
    """Add user-uploaded trusted CAs to an SSLContext. No-op if none configured.

    Always call AFTER the context already trusts what it normally would (e.g.,
    after ssl.create_default_context() or ctx.load_default_certs()), so user
    CAs add to — never replace — the default trust store.
    """
    blob = get_trusted_ca_pem()
    if blob:
        ctx.load_verify_locations(cadata=blob)
