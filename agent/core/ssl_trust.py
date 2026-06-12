"""Shim for core.ssl_trust — branch-side trusted CAs from the bundled ca.pem.

The agent package ships ca.pem (the server's Settings → TLS → Trusted CA
certificates plus the server's own cert). probes.py calls these helpers for
HTTPS/TLS/SMTP sensors, so sensors probed FROM this branch verify internal
endpoints against the same private CAs central does. Missing ca.pem = no-op
(system trust store only).
"""
import os

_CA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ca.pem")


def get_trusted_ca_pem() -> str:
    try:
        with open(_CA_PATH, "r", encoding="utf-8") as f:
            return f.read() or ""
    except OSError:
        return ""


def apply_trusted_cas(ctx):
    blob = get_trusted_ca_pem()
    if blob:
        try:
            ctx.load_verify_locations(cadata=blob)
        except Exception:
            pass
    return ctx
