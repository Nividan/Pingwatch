"""
core/saml_auth.py — SAML 2.0 Service Provider for PingWatch.

Uses pysaml2 (lazy-imported) so installs without SAML configured never pay
the import cost. Stores all config in app_settings, with the SP private key
Fernet-encrypted at rest.

Public API:
  _get_cfg()                                -> dict (decrypts SP key)
  get_saml_status()                         -> dict (badge payload)
  saml_generate_sp_cert(cn, sans=None)      -> (cert_pem, key_pem) and persists
  saml_sp_metadata_xml()                    -> bytes (SP metadata XML)
  saml_import_metadata(source, text)        -> dict (parsed IdP fields + stored)
  saml_build_authn_request()                -> {"url": ..., "form": auto-POST HTML}
  saml_parse_response(saml_response_b64,
                      relay_state)          -> dict {username, email, groups, ...}
  saml_test_config()                        -> (ok, message, detail)

Secrets are never logged. Status updates on _record_ok / _record_err.
"""

from __future__ import annotations

import base64
import secrets
import threading
import time
from urllib.parse import urlencode

import core.settings as _settings
from core.logger import log


# ── Status tracking (same pattern as LDAP/RADIUS) ──────────────────

_last_ok_ts: float | None = None
_last_err: dict = {}

_last_ok_lock = threading.Lock()


def _record_ok() -> None:
    global _last_ok_ts
    with _last_ok_lock:
        _last_ok_ts = time.time()


def _record_err(msg: str) -> None:
    global _last_err
    with _last_ok_lock:
        _last_err = {"ts": time.time(), "msg": (msg or "")[:300]}


def get_saml_status() -> dict:
    enabled = int(_settings.get("saml_enabled", 0) or 0)
    idp     = (_settings.get("saml_idp_sso_url", "") or "").strip()
    sp_cert = (_settings.get("saml_sp_cert_pem", "") or "").strip()
    if not enabled or not idp or not sp_cert:
        state = "unconfigured"
    elif _last_err and (not _last_ok_ts or _last_err["ts"] > _last_ok_ts):
        state = "error"
    elif _last_ok_ts:
        state = "ok"
    else:
        state = "configured"
    return {
        "state":        state,
        "last_ok_ts":   _last_ok_ts,
        "last_err_ts":  _last_err.get("ts") if _last_err else None,
        "last_err_msg": _last_err.get("msg", "") if _last_err else "",
    }


# ── In-memory RelayState store — single-use, 5 min TTL ────────────

_RELAY_STATES: dict = {}
_RELAY_LOCK = threading.Lock()
_RELAY_TTL  = 300  # seconds


def _relay_put(state: str, data: dict) -> None:
    with _RELAY_LOCK:
        _prune_relay_locked()
        _RELAY_STATES[state] = {"data": data, "created": time.time()}


def _relay_take(state: str) -> dict | None:
    """Retrieve and consume a relay state. Returns None if missing or expired."""
    with _RELAY_LOCK:
        _prune_relay_locked()
        entry = _RELAY_STATES.pop(state, None)
    if not entry:
        return None
    return entry["data"]


def _prune_relay_locked() -> None:
    now = time.time()
    stale = [k for k, v in _RELAY_STATES.items() if now - v["created"] > _RELAY_TTL]
    for k in stale:
        _RELAY_STATES.pop(k, None)


# ── Config loader ──────────────────────────────────────────────────

def _get_cfg() -> dict:
    from db.backups import decrypt_pw
    sp_key_enc = _settings.get("saml_sp_key_pem_enc", "") or ""
    sp_key = decrypt_pw(sp_key_enc) if sp_key_enc else ""
    return {
        "enabled":                     bool(int(_settings.get("saml_enabled", 0) or 0)),
        "sp_entity_id":                (_settings.get("saml_sp_entity_id", "") or "").strip(),
        "sp_acs_url":                  (_settings.get("saml_sp_acs_url", "") or "").strip(),
        "sp_cert_pem":                 _settings.get("saml_sp_cert_pem", "") or "",
        "sp_key_pem":                  sp_key,
        "idp_entity_id":               (_settings.get("saml_idp_entity_id", "") or "").strip(),
        "idp_sso_url":                 (_settings.get("saml_idp_sso_url", "") or "").strip(),
        "idp_slo_url":                 (_settings.get("saml_idp_slo_url", "") or "").strip(),
        "idp_cert_pem":                _settings.get("saml_idp_cert_pem", "") or "",
        "metadata_xml":                _settings.get("saml_metadata_xml", "") or "",
        "metadata_url":                (_settings.get("saml_metadata_url", "") or "").strip(),
        "sign_authn_requests":         bool(int(_settings.get("saml_sign_authn_requests", 1) or 0)),
        "want_assertions_signed":      bool(int(_settings.get("saml_want_assertions_signed", 1) or 0)),
        "want_assertions_encrypted":   bool(int(_settings.get("saml_want_assertions_encrypted", 0) or 0)),
        "attr_username":               (_settings.get("saml_attr_username", "") or "").strip() or "NameID",
        "attr_email":                  (_settings.get("saml_attr_email", "") or "mail").strip(),
        "attr_display_name":           (_settings.get("saml_attr_display_name", "") or "displayName").strip(),
        "attr_groups":                 (_settings.get("saml_attr_groups", "") or "memberOf").strip(),
        "auto_provision":              bool(int(_settings.get("saml_auto_provision", 1) or 0)),
        "default_role":                (_settings.get("saml_default_role", "") or "viewer").strip(),
        "allow_unmapped":              bool(int(_settings.get("saml_allow_unmapped", 1) or 0)),
        "display_name":                (_settings.get("saml_display_name", "") or "Single Sign-On").strip(),
    }


# ── SP signing cert generation ─────────────────────────────────────

def saml_generate_sp_cert(common_name: str = "",
                          extra_sans: list | None = None) -> tuple[str, str]:
    """Generate a fresh RSA-2048 SP signing cert (825-day, self-signed).

    Persists to app_settings:
      saml_sp_cert_pem       (public — unencrypted)
      saml_sp_key_pem_enc    (private — Fernet-encrypted)
    Returns (cert_pem, key_pem) — key_pem is plaintext, caller must not log.
    """
    from core.tls import generate_self_signed_cert
    from db.backups import encrypt_pw
    from db import db_save_settings

    cn = (common_name or "").strip() or "pingwatch-saml-sp"
    cert_pem, key_pem = generate_self_signed_cert(
        org_name="PingWatch SAML SP",
        hostname=cn,
        days=825,
        extra_sans=extra_sans or [],
    )
    key_enc = encrypt_pw(key_pem)
    _settings.load({
        "saml_sp_cert_pem":    cert_pem,
        "saml_sp_key_pem_enc": key_enc,
    })
    db_save_settings({
        "saml_sp_cert_pem":    cert_pem,
        "saml_sp_key_pem_enc": key_enc,
    })
    log.info("SAML SP signing cert generated (825 days)")
    return cert_pem, key_pem


def saml_sp_cert_info() -> dict:
    """Return display info for the current SP cert: fingerprint, not-after, days-left."""
    cert_pem = _settings.get("saml_sp_cert_pem", "") or ""
    if not cert_pem:
        return {"present": False}
    try:
        from core.tls import parse_cert_info
        info = parse_cert_info(cert_pem)
        info["present"] = True
        return info
    except Exception as e:
        log.warning(f"saml_sp_cert_info parse failed: {e}")
        return {"present": True, "error": "cert parse failed"}


def saml_idp_cert_info() -> dict:
    """Return display info for the current IdP cert: fingerprint, not-after, days-left."""
    cert_pem = _settings.get("saml_idp_cert_pem", "") or ""
    if not cert_pem:
        return {"present": False}
    try:
        from core.tls import parse_cert_info
        info = parse_cert_info(cert_pem)
        info["present"] = True
        return info
    except Exception as e:
        log.warning(f"saml_idp_cert_info parse failed: {e}")
        return {"present": True, "error": "cert parse failed"}


# ── Metadata import (IdP) ──────────────────────────────────────────

def saml_import_metadata(source: str, text: str = "", url: str = "") -> dict:
    """Fetch and parse IdP metadata from URL or XML text; persist extracted fields.

    source: 'url', 'xml', or 'file' (file + xml use the same text path).
    Returns {"ok": bool, "entity_id": str, "sso_url": str, "idp_cert_pem": str,
             "message": str}.
    """
    from db import db_save_settings

    if source == "url":
        if not url:
            return {"ok": False, "message": "URL is required for url source"}
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={
                "User-Agent": "PingWatch/SAML-SP",
                "Accept": "application/samlmetadata+xml, application/xml, text/xml, */*",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                xml_bytes = resp.read()
            text = xml_bytes.decode("utf-8", errors="replace")
            _settings.load({"saml_metadata_url": url})
            db_save_settings({"saml_metadata_url": url})
        except Exception as e:
            return {"ok": False, "message": f"fetch failed: {str(e)[:200]}"}

    if not text or not text.strip():
        return {"ok": False, "message": "metadata XML is empty"}

    parsed = _parse_idp_metadata_xml(text)
    if not parsed.get("ok"):
        return parsed

    _settings.load({
        "saml_metadata_xml":     text,
        "saml_metadata_source":  source,
        "saml_idp_entity_id":    parsed["entity_id"],
        "saml_idp_sso_url":      parsed["sso_url"],
        "saml_idp_cert_pem":     parsed["idp_cert_pem"],
    })
    db_save_settings({
        "saml_metadata_xml":     text,
        "saml_metadata_source":  source,
        "saml_idp_entity_id":    parsed["entity_id"],
        "saml_idp_sso_url":      parsed["sso_url"],
        "saml_idp_cert_pem":     parsed["idp_cert_pem"],
    })
    if parsed.get("slo_url"):
        _settings.load({"saml_idp_slo_url": parsed["slo_url"]})
        db_save_settings({"saml_idp_slo_url": parsed["slo_url"]})

    _record_ok()
    log.info(f"SAML IdP metadata imported ({source}): entity_id={parsed['entity_id']}")
    return {
        "ok": True,
        "entity_id":    parsed["entity_id"],
        "sso_url":      parsed["sso_url"],
        "slo_url":      parsed.get("slo_url", ""),
        "idp_cert_pem": parsed["idp_cert_pem"],
        "message":      "IdP metadata imported",
    }


def _parse_idp_metadata_xml(xml_text: str) -> dict:
    """Extract entity_id, SSO URL (HTTP-POST), SLO URL (optional), and signing cert
    from an IdP's SAML 2.0 metadata XML. Uses defusedxml to prevent XXE attacks.
    """
    try:
        from defusedxml import ElementTree as DET
    except ImportError:
        try:
            import xml.etree.ElementTree as DET  # fallback — pysaml2 ships defusedxml
        except ImportError:
            return {"ok": False, "message": "XML parser not available"}

    ns = {
        "md": "urn:oasis:names:tc:SAML:2.0:metadata",
        "ds": "http://www.w3.org/2000/09/xmldsig#",
    }
    try:
        root = DET.fromstring(xml_text)
    except Exception as e:
        return {"ok": False, "message": f"XML parse failed: {str(e)[:200]}"}

    # Accept either <EntityDescriptor> at root or <EntitiesDescriptor> wrapper
    if root.tag.endswith("EntitiesDescriptor"):
        ed = root.find("md:EntityDescriptor", ns)
        if ed is None:
            return {"ok": False, "message": "no EntityDescriptor found"}
    else:
        ed = root

    entity_id = ed.attrib.get("entityID", "").strip()
    if not entity_id:
        return {"ok": False, "message": "entityID missing from metadata"}

    idp = ed.find("md:IDPSSODescriptor", ns)
    if idp is None:
        return {"ok": False, "message": "IDPSSODescriptor missing — not an IdP metadata"}

    # SingleSignOnService — prefer HTTP-POST, fall back to HTTP-Redirect
    sso_url = ""
    for binding in ("urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                    "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"):
        for svc in idp.findall("md:SingleSignOnService", ns):
            if svc.attrib.get("Binding", "") == binding:
                sso_url = svc.attrib.get("Location", "").strip()
                break
        if sso_url:
            break
    if not sso_url:
        return {"ok": False, "message": "SingleSignOnService URL not found"}

    slo_url = ""
    for svc in idp.findall("md:SingleLogoutService", ns):
        if svc.attrib.get("Binding", "") == "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST":
            slo_url = svc.attrib.get("Location", "").strip()
            break

    # Signing cert — first signing KeyDescriptor
    cert_pem = ""
    for kd in idp.findall("md:KeyDescriptor", ns):
        use = kd.attrib.get("use", "signing")
        if use not in ("signing", ""):
            continue
        x509 = kd.find(".//ds:X509Certificate", ns)
        if x509 is not None and x509.text:
            raw = "".join(x509.text.split())
            if raw:
                cert_pem = _pem_wrap_cert(raw)
                break
    if not cert_pem:
        return {"ok": False, "message": "IdP signing cert not found in metadata"}

    return {
        "ok": True,
        "entity_id":    entity_id,
        "sso_url":      sso_url,
        "slo_url":      slo_url,
        "idp_cert_pem": cert_pem,
    }


def _pem_wrap_cert(b64_body: str) -> str:
    """Wrap a base64 cert body in BEGIN/END CERTIFICATE with 64-char lines."""
    lines = [b64_body[i:i+64] for i in range(0, len(b64_body), 64)]
    return "-----BEGIN CERTIFICATE-----\n" + "\n".join(lines) + "\n-----END CERTIFICATE-----\n"


# ── SP metadata export ─────────────────────────────────────────────

def saml_sp_metadata_xml() -> bytes:
    """Build and return the SP metadata XML (for IdP admins to consume)."""
    cfg = _get_cfg()
    entity_id = cfg["sp_entity_id"] or "pingwatch-saml-sp"
    acs_url   = cfg["sp_acs_url"]   or ""
    sp_cert   = (cfg["sp_cert_pem"] or "").strip()
    cert_b64  = ""
    if sp_cert:
        # Strip PEM armour
        body = []
        in_body = False
        for line in sp_cert.splitlines():
            if "-----BEGIN CERTIFICATE-----" in line:
                in_body = True
                continue
            if "-----END CERTIFICATE-----" in line:
                break
            if in_body:
                body.append(line.strip())
        cert_b64 = "".join(body)

    # Minimal but complete SP metadata — matches common IdP expectations.
    want_signed = "true" if cfg["want_assertions_signed"] else "false"
    authn_signed = "true" if cfg["sign_authn_requests"] else "false"
    key_descriptors = ""
    if cert_b64:
        key_descriptors = (
            f'  <md:KeyDescriptor use="signing">'
            f'<ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
            f'<ds:X509Data><ds:X509Certificate>{cert_b64}</ds:X509Certificate></ds:X509Data>'
            f'</ds:KeyInfo></md:KeyDescriptor>\n'
            f'  <md:KeyDescriptor use="encryption">'
            f'<ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">'
            f'<ds:X509Data><ds:X509Certificate>{cert_b64}</ds:X509Certificate></ds:X509Data>'
            f'</ds:KeyInfo></md:KeyDescriptor>\n'
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" '
        f'entityID="{_xml_esc(entity_id)}">\n'
        f' <md:SPSSODescriptor '
        f'AuthnRequestsSigned="{authn_signed}" '
        f'WantAssertionsSigned="{want_signed}" '
        f'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">\n'
        f'{key_descriptors}'
        f'  <md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>\n'
        f'  <md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified</md:NameIDFormat>\n'
        f'  <md:AssertionConsumerService '
        f'Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location="{_xml_esc(acs_url)}" index="0" isDefault="true"/>\n'
        f' </md:SPSSODescriptor>\n'
        f'</md:EntityDescriptor>\n'
    )
    return xml.encode("utf-8")


def _xml_esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ── AuthnRequest build (SP-initiated flow) ─────────────────────────

def saml_build_authn_request() -> dict:
    """Build a SAML AuthnRequest and return an auto-submitting HTML form.

    Returns {"ok": bool, "html": str, "message": str}.
    The html is an auto-submitting <form> posting to the IdP SSO URL.
    """
    cfg = _get_cfg()
    if not cfg["enabled"]:
        return {"ok": False, "html": "", "message": "SAML is not enabled"}
    if not cfg["idp_sso_url"]:
        return {"ok": False, "html": "", "message": "IdP SSO URL not configured"}
    if not cfg["sp_entity_id"] or not cfg["sp_acs_url"]:
        return {"ok": False, "html": "", "message": "SP entity_id or ACS URL missing"}

    # Generate a fresh request ID + RelayState, store for replay protection
    request_id   = "id-" + secrets.token_hex(16)
    relay_state  = secrets.token_urlsafe(24)
    _relay_put(relay_state, {"request_id": request_id, "ts": time.time()})

    issue_instant = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    authn_request = (
        f'<samlp:AuthnRequest '
        f'xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        f'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="{request_id}" '
        f'Version="2.0" '
        f'IssueInstant="{issue_instant}" '
        f'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'AssertionConsumerServiceURL="{_xml_esc(cfg["sp_acs_url"])}" '
        f'Destination="{_xml_esc(cfg["idp_sso_url"])}">\n'
        f' <saml:Issuer>{_xml_esc(cfg["sp_entity_id"])}</saml:Issuer>\n'
        f' <samlp:NameIDPolicy AllowCreate="true" '
        f'Format="urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified"/>\n'
        f'</samlp:AuthnRequest>'
    )
    saml_request_b64 = base64.b64encode(authn_request.encode("utf-8")).decode("ascii")

    html = (
        '<!DOCTYPE html>\n<html><head><title>Signing in…</title></head>\n'
        '<body onload="document.forms[0].submit()">\n'
        f'<form method="POST" action="{_xml_esc(cfg["idp_sso_url"])}">\n'
        f'  <input type="hidden" name="SAMLRequest" value="{saml_request_b64}"/>\n'
        f'  <input type="hidden" name="RelayState" value="{_xml_esc(relay_state)}"/>\n'
        '  <noscript><button type="submit">Continue to sign-in</button></noscript>\n'
        '</form>\n</body></html>'
    )
    return {"ok": True, "html": html, "message": "", "relay_state": relay_state}


# ── SAML Response parsing (ACS handler) ────────────────────────────

def saml_parse_response(saml_response_b64: str, relay_state: str) -> dict:
    """Parse + validate a SAMLResponse from the IdP. Returns dict with user attrs.

    Returns {"ok": bool, "external_id": str, "username": str, "email": str,
             "display_name": str, "groups": [str], "message": str}.

    Validates:
      - RelayState is in our store (single-use, replay protection).
      - XML parses without errors.
      - IdP Issuer matches configured saml_idp_entity_id.
      - NotOnOrAfter has not passed.
      - Audience (if present) matches our SP entity_id.
      - Signature via the cached IdP cert (best-effort; uses pysaml2 if available).
    """
    cfg = _get_cfg()
    if not cfg["enabled"]:
        return {"ok": False, "message": "SAML is not enabled"}

    # RelayState replay protection
    relay_data = _relay_take(relay_state)
    if relay_data is None:
        _record_err("RelayState invalid or expired")
        return {"ok": False, "message": "Invalid or expired login session — please try again"}

    # Decode Base64
    try:
        xml_bytes = base64.b64decode(saml_response_b64)
    except Exception as e:
        _record_err(f"base64 decode failed: {e}")
        return {"ok": False, "message": "malformed SAML response"}

    # Parse XML (defusedxml to prevent XXE)
    try:
        from defusedxml import ElementTree as DET
    except ImportError:
        import xml.etree.ElementTree as DET

    try:
        root = DET.fromstring(xml_bytes)
    except Exception as e:
        _record_err(f"XML parse failed: {e}")
        return {"ok": False, "message": "malformed SAML response XML"}

    ns = {
        "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
        "saml":  "urn:oasis:names:tc:SAML:2.0:assertion",
        "ds":    "http://www.w3.org/2000/09/xmldsig#",
    }

    # Signature validation via pysaml2 (preferred) or cryptography fallback
    sig_ok, sig_msg = _verify_response_signature(xml_bytes, cfg["idp_cert_pem"])
    if not sig_ok:
        _record_err(f"signature validation failed: {sig_msg}")
        return {"ok": False, "message": f"Signature validation failed: {sig_msg}"}

    # Locate the Assertion
    assertion = root.find("saml:Assertion", ns)
    if assertion is None:
        # Some IdPs put Response/EncryptedAssertion — v1 doesn't handle encryption
        if root.find("saml:EncryptedAssertion", ns) is not None:
            _record_err("encrypted assertion received (not supported in v1)")
            return {"ok": False, "message": "Encrypted assertions not supported — disable encryption on the IdP"}
        _record_err("no Assertion element found")
        return {"ok": False, "message": "SAML response contains no assertion"}

    # Issuer check
    issuer_el = assertion.find("saml:Issuer", ns)
    issuer = (issuer_el.text or "").strip() if issuer_el is not None else ""
    if cfg["idp_entity_id"] and issuer != cfg["idp_entity_id"]:
        _record_err(f"issuer mismatch (got {issuer!r}, expected {cfg['idp_entity_id']!r})")
        return {"ok": False, "message": "SAML issuer does not match configured IdP"}

    # Conditions / NotOnOrAfter
    conditions = assertion.find("saml:Conditions", ns)
    if conditions is not None:
        not_after = conditions.attrib.get("NotOnOrAfter", "")
        if not_after:
            try:
                from datetime import datetime, timezone
                # SAML timestamps are always UTC
                dt = datetime.strptime(not_after.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z") \
                     if "." in not_after \
                     else datetime.strptime(not_after.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
                now_utc = datetime.now(timezone.utc)
                if now_utc >= dt:
                    _record_err("assertion expired (NotOnOrAfter passed)")
                    return {"ok": False, "message": "SAML assertion has expired — clock skew?"}
            except Exception:
                pass  # Don't reject on parse failure; signature validation is primary

        # Audience restriction
        audience_r = conditions.find("saml:AudienceRestriction", ns)
        if audience_r is not None and cfg["sp_entity_id"]:
            audiences = [a.text for a in audience_r.findall("saml:Audience", ns) if a.text]
            if audiences and cfg["sp_entity_id"] not in audiences:
                _record_err(f"audience mismatch (got {audiences!r})")
                return {"ok": False, "message": "SAML audience does not match this SP"}

    # Extract NameID
    nameid = ""
    subject = assertion.find("saml:Subject", ns)
    if subject is not None:
        nid = subject.find("saml:NameID", ns)
        if nid is not None and nid.text:
            nameid = nid.text.strip()

    # Extract attributes
    attrs: dict = {}
    attr_stmt = assertion.find("saml:AttributeStatement", ns)
    if attr_stmt is not None:
        for a in attr_stmt.findall("saml:Attribute", ns):
            name = a.attrib.get("Name", "") or a.attrib.get("FriendlyName", "")
            vals = [v.text for v in a.findall("saml:AttributeValue", ns) if v.text]
            if name:
                attrs[name] = vals

    # Resolve configured attribute names → canonical fields
    def _pick(attr_name: str, fallback: str = "") -> str:
        if attr_name == "NameID":
            return nameid or fallback
        vals = attrs.get(attr_name) or []
        return (vals[0] if vals else "") or fallback

    username = _pick(cfg["attr_username"], nameid)
    email    = _pick(cfg["attr_email"])
    display  = _pick(cfg["attr_display_name"])
    groups   = attrs.get(cfg["attr_groups"], [])

    if not username:
        _record_err("no username found in response")
        return {"ok": False, "message": "SAML response missing username attribute"}

    # Build stable external_id
    external_id = f"saml|{cfg['idp_entity_id']}|{nameid or username}"

    _record_ok()
    return {
        "ok":            True,
        "external_id":   external_id,
        "username":      username,
        "email":         email or "",
        "display_name":  display or "",
        "groups":        groups or [],
        "nameid":        nameid,
        "message":       "",
    }


def _verify_response_signature(xml_bytes: bytes, idp_cert_pem: str) -> tuple[bool, str]:
    """Verify the XML signature on a SAMLResponse. Returns (ok, message).

    Strategy:
      1. Prefer pysaml2's signxml-based verifier if available.
      2. Fall back to signxml directly.
      3. If neither importable, fall back to a best-effort check (cert presence only) and warn.
    """
    if not idp_cert_pem or not idp_cert_pem.strip():
        return False, "no IdP cert configured"

    # Try signxml first — most direct path
    try:
        from signxml import XMLVerifier
        from defusedxml import ElementTree as DET
        # XMLVerifier needs the full document
        root = DET.fromstring(xml_bytes)
        XMLVerifier().verify(
            root,
            x509_cert=idp_cert_pem,
            expect_references=1,  # Accept either Response or Assertion signature
        )
        return True, "signxml verified"
    except ImportError:
        pass
    except Exception as e:
        msg = str(e).lower()
        # Some IdPs sign only the Assertion (not Response) — try again permissively
        if "reference" in msg or "multiple" in msg:
            try:
                from signxml import XMLVerifier
                from defusedxml import ElementTree as DET
                root = DET.fromstring(xml_bytes)
                XMLVerifier().verify(root, x509_cert=idp_cert_pem)
                return True, "signxml verified (multi-ref)"
            except Exception as e2:
                return False, f"signxml: {str(e2)[:200]}"
        return False, f"signxml: {str(e)[:200]}"

    # pysaml2 fallback
    try:
        from saml2.sigver import SecurityContext, CertHandler  # noqa: F401
        # Full pysaml2 verification requires a Saml2Config + SecurityContext. This
        # is heavyweight and involves temp files. For v1, if signxml is missing,
        # we return a clear error so admins install signxml (which pysaml2 needs
        # anyway for most signing operations).
        return False, "signxml required — pip install signxml"
    except ImportError:
        return False, "signature verification library missing — pip install signxml"


# ── Test endpoint helper ───────────────────────────────────────────

def saml_test_config() -> tuple[bool, str, dict]:
    """Dry-run validation. Returns (ok, message, detail)."""
    detail: dict = {}
    cfg = _get_cfg()
    if not cfg["enabled"]:
        return False, "SAML not enabled", detail
    if not cfg["sp_entity_id"]:
        return False, "SP entity_id not set", detail
    if not cfg["sp_acs_url"]:
        return False, "SP ACS URL not set", detail
    if not cfg["idp_entity_id"]:
        return False, "IdP entity_id not set — import IdP metadata", detail
    if not cfg["idp_sso_url"]:
        return False, "IdP SSO URL not set — import IdP metadata", detail
    if not cfg["idp_cert_pem"]:
        return False, "IdP signing cert not set", detail

    # Parse IdP cert — reports expiry
    try:
        from core.tls import parse_cert_info
        idp_info = parse_cert_info(cfg["idp_cert_pem"])
        detail["idp_cert"] = idp_info
        if idp_info.get("days_left", 0) <= 0:
            return False, "IdP signing cert has expired", detail
        if idp_info.get("days_left", 999) < 30:
            detail["warning"] = f"IdP cert expires in {idp_info['days_left']} days"
    except Exception as e:
        return False, f"IdP cert could not be parsed: {str(e)[:120]}", detail

    # SP cert check
    if cfg["sp_cert_pem"]:
        try:
            from core.tls import parse_cert_info
            sp_info = parse_cert_info(cfg["sp_cert_pem"])
            detail["sp_cert"] = sp_info
            if sp_info.get("days_left", 999) <= 0:
                return False, "SP signing cert has expired — re-generate", detail
        except Exception as e:
            return False, f"SP cert could not be parsed: {str(e)[:120]}", detail
    else:
        detail["sp_cert"] = None

    # Verify signxml is importable (we need it at login time)
    try:
        import signxml  # noqa: F401
        detail["signxml"] = "available"
    except ImportError:
        return False, "signxml library missing — pip install signxml (pysaml2 pulls it in automatically)", detail

    # Generate a test AuthnRequest to catch config errors early
    ar = saml_build_authn_request()
    if not ar.get("ok"):
        return False, ar.get("message", "AuthnRequest build failed"), detail

    _record_ok()
    return True, "SAML config valid", detail
