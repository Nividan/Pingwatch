"""
core/agent_package.py — builds the self-contained remote-agent zip.

build_agent_package() assembles, in memory, everything a branch host needs:

    pingwatch-agent/
      agent.py                  (from agent/)
      probes.py                 (verbatim copy of monitoring/probes.py)
      core/…                    (shim package from agent/core/ +
                                 verbatim core/radius_auth.py)
      install.sh / install.bat / pingwatch-agent.service / README.md
      requirements-optional.txt
      config.json               (generated: server URL, one-time enrollment
                                 token, server cert SHA-256 pin)

probes.py and radius_auth.py are copied from the canonical sources at
download time, so a freshly downloaded package always matches the running
server. Deployed agents report their version each checkin; the Probes page
shows an "update available" badge when they drift.
"""
import hashlib
import io
import json
import os
import zipfile

from core.logger import log

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AGENT_DIR = os.path.join(_REPO_ROOT, "agent")

# Files copied verbatim from elsewhere in the repo into the package root /
# package core/ — the reason the agent/core shims exist at all.
_EXTRA_FILES = [
    (os.path.join(_REPO_ROOT, "monitoring", "probes.py"), "probes.py"),
    (os.path.join(_REPO_ROOT, "core", "radius_auth.py"), "core/radius_auth.py"),
]


def _server_cert_fingerprint() -> str:
    """SHA-256 hex of the active TLS certificate's DER form ('' when TLS is
    off or the cert can't be parsed). The agent pins this fingerprint."""
    try:
        import ssl as _ssl
        import core.settings as _settings
        if str(_settings.get("tls_enabled", "1")) != "1":
            return ""
        cert_pem = _settings.get("tls_cert_pem", "") or ""
        if not cert_pem:
            from core.config import CERTS_DIR
            _p = os.path.join(str(CERTS_DIR), "cert.pem")
            if os.path.exists(_p):
                with open(_p, "r", encoding="utf-8") as f:
                    cert_pem = f.read()
        if not cert_pem:
            return ""
        der = _ssl.PEM_cert_to_DER_cert(cert_pem)
        return hashlib.sha256(der).hexdigest()
    except Exception as e:
        log.warning(f"agent package: cert fingerprint unavailable: {e}")
        return ""


def _guess_server_url(host_header: str) -> str:
    """Best-effort server URL for config.json from the request Host header.
    The admin can edit config.json before deploying if this guess is wrong
    (e.g. downloading via an internal name the branch can't resolve)."""
    import core.settings as _settings
    host = (host_header or "").strip()
    tls_on = str(_settings.get("tls_enabled", "1")) == "1"
    scheme = "https" if tls_on else "http"
    if not host:
        import core.app_state as app_state
        port = (_settings.get("tls_port", "8443") if tls_on
                else app_state.effective_port)
        host = f"localhost:{port}"
    return f"{scheme}://{host}"


def _behind_reverse_proxy(headers) -> bool:
    """True when the download request carries reverse-proxy markers.

    Behind a proxy, the TLS certificate agents will see belongs to the
    proxy, not to PingWatch — pinning PingWatch's own cert would brick
    every install with a fingerprint mismatch. CA validation (empty pin)
    is the correct mode there, since proxies front publicly-trusted certs.
    """
    if headers is None:
        return False
    for k in ("X-Forwarded-For", "X-Forwarded-Proto", "X-Forwarded-Host",
              "X-Real-IP", "Forwarded"):
        try:
            if headers.get(k):
                return True
        except Exception:
            return False
    return False


def build_agent_package(probe: dict, enrollment_token: str,
                        host_header: str = "",
                        request_headers=None) -> bytes:
    """Assemble the zip in memory and return its bytes."""
    if _behind_reverse_proxy(request_headers):
        pin = ""
        log.info("agent package: reverse proxy detected (X-Forwarded-* headers) "
                 "— omitting cert pin; the agent will use CA validation")
    else:
        pin = _server_cert_fingerprint()
    cfg = {
        "server_url":         _guess_server_url(host_header),
        "enrollment_token":   enrollment_token,
        "server_cert_sha256": pin,
        "probe_id":           probe["probe_id"],
        "probe_name":         probe.get("name") or "",
        "checkin_interval":   10,
        "spool_max":          50000,
        "protocol_version":   1,
    }
    buf = io.BytesIO()
    prefix = "pingwatch-agent/"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Everything under agent/ (shims, installers, README, agent.py)
        for root, _dirs, files in os.walk(_AGENT_DIR):
            for name in files:
                if name.endswith((".pyc", ".pyo")) or name == "__pycache__":
                    continue
                full = os.path.join(root, name)
                rel = os.path.relpath(full, _AGENT_DIR).replace(os.sep, "/")
                zf.write(full, prefix + rel)
        # Canonical copies (probes.py, radius_auth.py)
        for src, arc in _EXTRA_FILES:
            zf.write(src, prefix + arc)
        # Generated config — the one-time token lives only in this download
        zf.writestr(prefix + "config.json", json.dumps(cfg, indent=2) + "\n")
    return buf.getvalue()
