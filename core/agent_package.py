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

# Files copied verbatim from elsewhere in the repo into the package's release
# payload — the reason the agent/core shims exist at all. vmware/ rides along
# so VMware sensors only need `pip install pyvmomi` on the branch host (the
# installers offer it). These are PAYLOAD: they extract into releases/<id>/.
_EXTRA_FILES = [
    (os.path.join(_REPO_ROOT, "monitoring", "probes.py"), "probes.py"),
    (os.path.join(_REPO_ROOT, "core", "radius_auth.py"), "core/radius_auth.py"),
    (os.path.join(_REPO_ROOT, "vmware", "__init__.py"), "vmware/__init__.py"),
    (os.path.join(_REPO_ROOT, "vmware", "client.py"), "vmware/client.py"),
]

# Managed-update layout (v1.4+): the package splits into two parts.
#   • PAYLOAD — the swappable agent runtime (agent.py + core/ shims + the
#     verbatim copies above). It extracts into releases/<build_id>/ and is what
#     a remote update replaces. Identified by build_id (version + content hash).
#   • BASE scaffolding — the stable supervisor + installers + generated config.
#     The supervisor manages releases but is itself only changed by re-install.
# These top-level names in agent/ are BASE, never payload:
_BASE_ONLY = {
    "supervisor.py", "install.sh", "install.bat",
    "pingwatch-agent.service", "README.md", "requirements-optional.txt",
}
# OS-specific BASE installers. A per-platform download ships only its own set;
# build_id is unaffected because these are BASE (never part of the payload hash),
# so Windows and Linux packages share one build_id and one managed-update path.
_OS_INSTALLERS = {
    "linux":   {"install.sh", "pingwatch-agent.service"},
    "windows": {"install.bat"},
}
# Fixed zip timestamp so an unchanged payload always hashes/zips identically.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _iter_payload_files():
    """Yield (abs_src, arcname) for every file in the swappable agent runtime
    (extracts into releases/<build_id>/). Excludes BASE scaffolding."""
    for root, _dirs, files in os.walk(_AGENT_DIR):
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, _AGENT_DIR).replace(os.sep, "/")
            top = rel.split("/", 1)[0]
            if rel in _BASE_ONLY or top in _BASE_ONLY:
                continue
            yield full, rel
    for src, arc in _EXTRA_FILES:
        yield src, arc


def _payload_files_sorted():
    # Deduplicate by arcname (a verbatim copy could shadow a walked file) and
    # sort so the content hash and zip layout are deterministic.
    by_arc = {}
    for full, arc in _iter_payload_files():
        by_arc[arc] = full
    return sorted(by_arc.items(), key=lambda kv: kv[0])


def compute_build_id() -> str:
    """Stable identity of the current agent payload: APP_VERSION + a short
    content hash over the sorted payload files. Changes whenever any payload
    file changes — even within the same version — so drift and update targets
    are precise, not just version-string deep."""
    import core.app_state as app_state
    h = hashlib.sha256()
    for arc, full in _payload_files_sorted():
        h.update(arc.encode("utf-8") + b"\0")
        with open(full, "rb") as f:
            h.update(f.read())
        h.update(b"\0")
    return f"{app_state.APP_VERSION}+{h.hexdigest()[:12]}"


def build_release_payload() -> tuple:
    """Zip of just the agent runtime payload — extracts into releases/<id>/.
    Served by GET /api/agent/package for remote (managed) updates.

    Returns (zip_bytes, build_id, package_sha256). package_sha256 is the hash
    of the exact bytes returned; a campaign pins it and the agent verifies the
    download against it before staging."""
    build_id = compute_build_id()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc, full in _payload_files_sorted():
            zi = zipfile.ZipInfo(arc, date_time=_ZIP_EPOCH)
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(full, "rb") as f:
                zf.writestr(zi, f.read())
    data = buf.getvalue()
    return data, build_id, hashlib.sha256(data).hexdigest()


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
                        request_headers=None,
                        platform: str = "") -> bytes:
    """Assemble the zip in memory and return its bytes.

    platform "" → both installers (back-compat); "windows" / "linux" → only
    that OS's installers (the other platform's are omitted)."""
    platform = (platform or "").lower()
    if platform in _OS_INSTALLERS:
        _excl_installers = set().union(
            *(files for plat, files in _OS_INSTALLERS.items() if plat != platform))
    else:
        _excl_installers = set()
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
    build_id = compute_build_id()
    buf = io.BytesIO()
    prefix = "pingwatch-agent/"
    rel_prefix = f"{prefix}releases/{build_id}/"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # agent/ contents: BASE scaffolding (supervisor, installers, service,
        # README, reqs) stays at the package root; the agent runtime (payload)
        # goes under releases/<build_id>/ so the supervisor can swap it.
        for root, _dirs, files in os.walk(_AGENT_DIR):
            for name in files:
                if name.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(root, name)
                rel = os.path.relpath(full, _AGENT_DIR).replace(os.sep, "/")
                top = rel.split("/", 1)[0]
                if rel in _BASE_ONLY or top in _BASE_ONLY:
                    if rel in _excl_installers:
                        continue   # other platform's installer — skip
                    zf.write(full, prefix + rel)
                else:
                    zf.write(full, rel_prefix + rel)
        # Canonical copies (probes.py, radius_auth.py, vmware/) — payload.
        for src, arc in _EXTRA_FILES:
            zf.write(src, rel_prefix + arc)
        # Marker so the running agent can report exactly which build it is.
        zf.writestr(rel_prefix + "BUILD_ID", build_id + "\n")
        # CA bundle — server verification AND outbound sensor TLS (the
        # agent's ssl_trust shim reads the same file, so HTTPS/TLS sensors
        # probed from the branch trust internal CAs exactly like central).
        # Base dir — shared across releases.
        if ca_bundle:
            zf.writestr(prefix + "ca.pem", ca_bundle)
        # Generated config — the one-time token lives only in this download
        zf.writestr(prefix + "config.json", json.dumps(cfg, indent=2) + "\n")
        # Seed supervisor state: this release is active, nothing to roll back to.
        sup_state = {"active_release": build_id, "previous_release": None,
                     "probation": None}
        zf.writestr(prefix + "supervisor_state.json",
                    json.dumps(sup_state, indent=2) + "\n")
    return buf.getvalue()
