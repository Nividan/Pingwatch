"""Shim for core.ssl_trust — the agent has no custom CA store.

probes.py calls these to honor server-side trusted CAs for HTTPS/TLS
sensors; on the agent the system trust store is used as-is.
"""


def apply_trusted_cas(ctx):
    return ctx


def get_trusted_ca_pem() -> str:
    return ""
