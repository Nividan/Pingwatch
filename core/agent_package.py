"""
core/agent_package.py — builds the self-contained remote-agent zip.

build_agent_package() assembles, in memory, everything a branch host needs:

    pingwatch-agent/
      agent.py                  (from agent/)
      probes.py                 (verbatim copy of monitoring/probes.py)
      core/…                    (shim package from agent/core/ +
                                 verbatim core/radius_auth.py)
      vmware/…                  (verbatim copy — VMware sensors need only
                                 pyvmomi on the branch host)
      install.sh / install.bat / pingwatch-agent.service / README.md
      requirements-optional.txt
      config.json               (generated: server URL, one-time enrollment
                                 token, server cert SHA-256 pin)

probes.py, radius_auth.py, and vmware/ are copied from the canonical
sources at download time, so a freshly downloaded package always matches
the running server. Deployed agents report their version each checkin; the Probes page
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
# package core/ — the reason the agent/core shims exist at all. vmware/
# rides along so VMware sensors only need `pip install pyvmomi` on the
# branch host (the installers offer it).
_EXTRA_FILES = [
    (os.path.join(_REPO_ROOT, "monitoring", "probes.py"), "probes.py"),
    (os.path.join(_REPO_ROOT, "core", "radius_auth.py"), "core/radius_auth.py"),
    (os.path.join(_REPO_ROOT, "vmware", "__init__.py"), "vmware/__init__.py"),
    (os.path.join(_REPO_ROOT, "vmware", "client.py"), "vmware/client.py"),
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


def _agent_ca_bundle() -> tuple:
    """(pem_bundle, has_custom_ca) for the agent's ca.pem.

    The bundle holds the admin-uploaded trusted CAs (Settings → TLS →
    Trusted CA certificates — the same store sensors use to verify internal
    endpoints) plus PingWatch's own active certificate, so the agent
    validates the server whether it terminates TLS itself (self-signed) or
    sits behind a private-CA-issued front. Loaded ADDITIVELY on the agent —
    system CAs keep working for publicly-trusted proxies.
    """
    parts = []
    has_custom_ca = False
    try:
        from core.ssl_trust import get_trusted_ca_pem
        blob = get_trusted_ca_pem()
        if blob and blob.strip():
            parts.append(blob.strip())
            has_custom_ca = True
    except Exception as e:
        log.warning(f"agent package: trusted-CA store unavailable: {e}")
    try:
        import core.settings as _settings
        cert_pem = _settings.get("tls_cert_pem", "") or ""
        if not cert_pem:
            from core.config import CERTS_DIR
            _p = os.path.join(str(CERTS_DIR), "cert.pem")
            if os.path.exists(_p):
                with open(_p, "r", encoding="utf-8") as f:
                    cert_pem = f.read()
        if cert_pem.strip():
            parts.append(cert_pem.strip())
    except Exception as e:
        log.warning(f"agent package: own cert unavailable for ca bundle: {e}")
    return (("\n".join(parts) + "\n") if parts else ""), has_custom_ca


def build_agent_package(probe: dict, enrollment_token: str,
                        host_header: str = "",
                        request_headers=None) -> bytes:
    """Assemble the zip in memory and return its bytes."""
    ca_bundle, has_custom_ca = _agent_ca_bundle()
    proxied = _behind_reverse_proxy(request_headers)
    # Pin only in the plain direct self-signed case (it also covers IP-based
    # server_urls that a SAN check would reject). A proxy or an uploaded
    # private CA signals custom TLS in front — CA validation via the bundled
    # ca.pem (+ system store) is the mode that survives renewals there.
    if proxied or has_custom_ca:
        pin = ""
        log.info("agent package: %s — omitting cert pin; agent will use CA "
                 "validation via bundled ca.pem + system store",
                 "reverse proxy detected (X-Forwarded-* headers)" if proxied
                 else "trusted CAs are configured")
    else:
        pin = _server_cert_fingerprint()
    cfg = {
        "server_url":         _guess_server_url(host_header),
        "enrollment_token":   enrollment_token,
        "server_cert_sha256": pin,
        "server_ca_file":     "ca.pem" if ca_bundle else "",
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
        # CA bundle — server verification AND outbound sensor TLS (the
        # agent's ssl_trust shim reads the same file, so HTTPS/TLS sensors
        # probed from the branch trust internal CAs exactly like central).
        if ca_bundle:
            zf.writestr(prefix + "ca.pem", ca_bundle)
        # Generated config — the one-time token lives only in this download
        zf.writestr(prefix + "config.json", json.dumps(cfg, indent=2) + "\n")
    return buf.getvalue()
