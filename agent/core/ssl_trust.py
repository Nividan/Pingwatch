"""Shim for core.ssl_trust — branch-side trusted CAs from the bundled ca.pem.

The agent package ships ca.pem (the server's Settings → TLS → Trusted CA
certificates plus the server's own cert). probes.py calls these helpers for
HTTPS/TLS/SMTP sensors, so sensors probed FROM this branch verify internal
endpoints against the same private CAs central does. Missing ca.pem = no-op
(system trust store only).
"""
import os


def _ca_path() -> str:
    # ca.pem lives in the agent's persistent base dir. Under the supervisor
    # that's the --data-dir (exported as PW_AGENT_DATA_DIR by agent.py), NOT
    # this shim's swappable release dir. A flat/standalone install has them the
    # same, so the module-relative path is the fallback. Resolved at call time
    # so it's correct regardless of import order.
    base = os.environ.get("PW_AGENT_DATA_DIR") or \
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "ca.pem")


def get_trusted_ca_pem() -> str:
    try:
        with open(_ca_path(), "r", encoding="utf-8") as f:
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
