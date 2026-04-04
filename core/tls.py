"""
tls.py — TLS/HTTPS support for PingWatch.

Handles: certificate generation, discovery, validation, SSL context creation,
         certificate info parsing, and expiry warnings.
"""

import datetime
import ipaddress
import os
import socket
import ssl
import tempfile
from pathlib import Path

from .logger import log

# ── CERTS_DIR (discovery folder) ─────────────────────────────────────────────
# Imported lazily inside functions to avoid circular imports at module load time.

def _certs_dir() -> Path:
    from .config import CERTS_DIR
    return Path(CERTS_DIR)


# ── Certificate generation ────────────────────────────────────────────────────

def generate_self_signed_cert(
    org_name:   str = "PingWatch",
    hostname:   str = "localhost",
    org_unit:   str = "",
    country:    str = "",
    state:      str = "",
    locality:   str = "",
    days:       int = 825,
    extra_sans: list = None,
) -> tuple:
    """
    Generate a self-signed RSA-2048 certificate.

    Args:
        org_name:   Organization name (O)
        hostname:   Common name / hostname (CN) — also used for SAN entries
        org_unit:   Organizational unit (OU), optional
        country:    Two-letter country code (C), optional
        state:      State or province (ST), optional
        locality:   Locality / city (L), optional
        days:       Validity period in days (default 825)
        extra_sans: Optional list of additional SAN strings.
                    Each entry is treated as a DNS name unless it parses as
                    an IP address, in which case it is added as an IPAddress SAN.

    Returns (cert_pem: str, key_pem: str).
    Includes SAN entries for the hostname and, if hostname resolves to an IP,
    that IP address as well.
    """
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    days = max(1, int(days))

    # ── Key ──────────────────────────────────────────────────────────────────
    # cryptography < 36 required an explicit 'backend' argument; it was removed
    # in 36.0. Inspect the signature so we pass it only when the parameter
    # actually exists — safe on both old and new versions.
    import inspect as _inspect
    _backend_kwargs = {}
    if 'backend' in _inspect.signature(rsa.generate_private_key).parameters:
        from cryptography.hazmat.backends import default_backend as _default_backend
        _backend_kwargs = {'backend': _default_backend()}
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048,
                                   **_backend_kwargs)

    # ── Subject / Issuer — only include optional fields when non-empty ────────
    name_attrs = []
    if country:
        name_attrs.append(x509.NameAttribute(NameOID.COUNTRY_NAME,             country[:2].upper()))
    if state:
        name_attrs.append(x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME,   state))
    if locality:
        name_attrs.append(x509.NameAttribute(NameOID.LOCALITY_NAME,            locality))
    name_attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME,        org_name or "PingWatch"))
    if org_unit:
        name_attrs.append(x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, org_unit))
    name_attrs.append(x509.NameAttribute(NameOID.COMMON_NAME,              hostname or "localhost"))
    subject = x509.Name(name_attrs)

    # ── SAN entries ──────────────────────────────────────────────────────────
    san_entries = [x509.DNSName(hostname or "localhost"), x509.DNSName("localhost")]
    # Also add the local machine hostname
    try:
        local_host = socket.gethostname()
        if local_host and local_host != hostname:
            san_entries.append(x509.DNSName(local_host))
    except Exception:
        pass
    # Add IPv4 loopback and any resolved IP
    san_entries.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
    try:
        resolved = socket.gethostbyname(hostname)
        san_entries.append(x509.IPAddress(ipaddress.ip_address(resolved)))
    except Exception:
        pass
    # Extra user-supplied SANs (DNS names or IP addresses)
    _seen = {str(e.value) for e in san_entries}
    for san in (extra_sans or []):
        san = san.strip()
        if not san or san in _seen:
            continue
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(san)))
        except ValueError:
            san_entries.append(x509.DNSName(san))
        _seen.add(san)

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)                          # self-signed
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256(), **_backend_kwargs)
    )

    cert_pem = cert.public_bytes(
        serialization.Encoding.PEM
    ).decode("utf-8")

    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    not_after = (now + datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    _extra_count = len([s for s in (extra_sans or []) if s.strip()])
    log.info(
        f"TLS: generated self-signed certificate — "
        f"CN={hostname}, org={org_name or 'PingWatch'}, "
        f"valid until {not_after} ({days}d)"
        + (f", {_extra_count} extra SAN(s)" if _extra_count else "")
    )
    return cert_pem, key_pem


# ── Certificate discovery (3-step: DB → folder → generate) ───────────────────

def discover_or_generate_cert(org_name: str = "PingWatch",
                               hostname: str = "localhost") -> tuple:
    """
    Discover an existing certificate or generate a new self-signed one.

    Discovery order:
      1. DB (app_settings keys tls_cert_pem + tls_key_pem_enc)
      2. certs/ folder (cert.pem + key.pem)
      3. Auto-generate self-signed

    Returns (cert_pem: str, key_pem: str, source: str)
    where source is "db" | "imported" | "generated".
    """
    from db import db_load_settings
    from db.backups import decrypt_pw

    # ── Step 1: check DB ─────────────────────────────────────────────────────
    current_settings = db_load_settings()
    cert_source  = current_settings.get("tls_cert_source", "")
    cert_pem     = current_settings.get("tls_cert_pem", "")
    key_pem_enc  = current_settings.get("tls_key_pem_enc", "")
    if cert_source == "csr_pending":
        # A CSR has been generated: the stored key belongs to the pending CSR,
        # not to any existing cert. Skip DB to avoid a key-mismatch SSL error.
        log.info("TLS: CSR pending — skipping DB cert, falling back to folder/generate")
    elif cert_pem and key_pem_enc:
        key_pem = decrypt_pw(key_pem_enc)
        if key_pem:
            err = validate_cert_key_pair(cert_pem, key_pem)
            if err:
                log.warning(f"TLS: cert/key in DB are invalid ({err}) — will try folder/generate")
            else:
                # Also probe with the real ssl module — cryptography and OpenSSL
                # parsers can disagree on edge cases (line endings, encoding, etc.)
                try:
                    build_ssl_context(cert_pem, key_pem)
                    log.info("TLS: loaded certificate from database")
                    return cert_pem, key_pem, "db"
                except Exception as probe_err:
                    log.warning(
                        f"TLS: cert/key in DB failed SSL-layer probe ({probe_err}) — "
                        "will try folder/generate"
                    )
        else:
            log.warning("TLS: cert found in DB but key decryption failed — will try folder/generate")

    # ── Step 2: check certs/ folder ──────────────────────────────────────────
    cert_file = _certs_dir() / "cert.pem"
    key_file  = _certs_dir() / "key.pem"
    if cert_file.is_file() and key_file.is_file():
        try:
            cert_pem = cert_file.read_text(encoding="utf-8")
            key_pem  = key_file.read_text(encoding="utf-8")
            err = validate_cert_key_pair(cert_pem, key_pem)
            if err:
                log.warning(f"TLS: cert files in certs/ are invalid ({err}) — generating new cert")
            else:
                log.info(f"TLS: imported certificate from {cert_file}")
                return cert_pem, key_pem, "imported"
        except Exception as e:
            log.warning(f"TLS: failed to read cert files from certs/ folder: {e}")

    # ── Step 3: generate self-signed ─────────────────────────────────────────
    log.info("TLS: generating new self-signed certificate")
    cert_pem, key_pem = generate_self_signed_cert(org_name, hostname)
    return cert_pem, key_pem, "generated"


# ── SSL context builder ───────────────────────────────────────────────────────

def build_ssl_context(cert_pem: str, key_pem: str) -> ssl.SSLContext:
    """
    Build an SSLContext from PEM strings.
    Writes to temporary files (deleted immediately after load), uses TLS 1.2+.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.options |= ssl.OP_NO_COMPRESSION

    # Normalize PEM line endings — OpenSSL's ssl module is stricter than the
    # cryptography library and rejects \r\n or stray \r characters.
    cert_pem = cert_pem.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    key_pem  = key_pem.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"

    # Write PEMs to temp files so SSLContext.load_cert_chain() can read them
    fd_cert = fd_key = None
    tmp_cert = tmp_key = None
    try:
        fd_cert, tmp_cert = tempfile.mkstemp(suffix=".pem")
        os.write(fd_cert, cert_pem.encode("utf-8"))
        os.close(fd_cert); fd_cert = None

        fd_key, tmp_key = tempfile.mkstemp(suffix=".pem")
        os.write(fd_key, key_pem.encode("utf-8"))
        os.close(fd_key); fd_key = None

        ctx.load_cert_chain(certfile=tmp_cert, keyfile=tmp_key)
        log.debug("TLS: SSLContext built successfully (TLS 1.2+ enforced)")
    finally:
        if fd_cert is not None:
            try: os.close(fd_cert)
            except OSError: pass
        if fd_key is not None:
            try: os.close(fd_key)
            except OSError: pass
        for tmp in (tmp_cert, tmp_key):
            if tmp:
                try: os.unlink(tmp)
                except OSError: pass

    return ctx


# ── Certificate info parser ───────────────────────────────────────────────────

def parse_cert_info(cert_pem: str) -> dict:
    """
    Parse certificate metadata for UI display.

    Returns dict with keys:
        subject, issuer, not_after (ISO date string),
        days_left (int), self_signed (bool).
    Returns {} if cert_pem is empty/invalid.
    """
    if not cert_pem:
        return {}
    try:
        from cryptography import x509 as _x509
        from cryptography.hazmat.primitives.serialization import Encoding
        cert = _x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))

        def _name_str(name) -> str:
            parts = []
            for attr in name:
                parts.append(f"{attr.oid.dotted_string.split('.')[-1]}={attr.value}")
            # Try to return CN= value if present, fall back to full name
            for attr in name:
                from cryptography.x509.oid import NameOID
                if attr.oid == NameOID.COMMON_NAME:
                    return attr.value
            return ", ".join(parts) or "(unknown)"

        # not_valid_after_utc added in cryptography 42; fall back to naive attr
        not_after = getattr(cert, "not_valid_after_utc", None) or \
            datetime.datetime.combine(cert.not_valid_after.date(),
                                      datetime.time.min,
                                      tzinfo=datetime.timezone.utc)
        days_left = (not_after - datetime.datetime.now(datetime.timezone.utc)).days

        subject_str = _name_str(cert.subject)
        issuer_str  = _name_str(cert.issuer)

        return {
            "subject":     subject_str,
            "issuer":      issuer_str,
            "not_after":   not_after.strftime("%Y-%m-%d"),
            "days_left":   days_left,
            "self_signed": (cert.subject == cert.issuer),
        }
    except Exception as e:
        log.debug(f"TLS: parse_cert_info failed: {e}")
        return {}


# ── Cert / key pair validator ─────────────────────────────────────────────────

def validate_cert_key_pair(cert_pem: str, key_pem: str) -> "str | None":
    """
    Verify that cert_pem and key_pem are a matching pair.
    Returns an error string on failure, None on success.
    """
    if not cert_pem or not cert_pem.strip():
        return "Certificate PEM is empty"
    if not key_pem or not key_pem.strip():
        return "Private key PEM is empty"
    try:
        from cryptography import x509 as _x509
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives.asymmetric import padding
        import hashlib

        cert = _x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
        pkey = load_pem_private_key(key_pem.encode("utf-8"), password=None)

        # Compare public key bytes
        cert_pub = cert.public_key().public_bytes(
            encoding=__import__("cryptography").hazmat.primitives.serialization.Encoding.DER,
            format=__import__("cryptography").hazmat.primitives.serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        key_pub = pkey.public_key().public_bytes(
            encoding=__import__("cryptography").hazmat.primitives.serialization.Encoding.DER,
            format=__import__("cryptography").hazmat.primitives.serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if cert_pub != key_pub:
            return "Certificate and private key do not match"

        # Check cert is not expired
        not_after = getattr(cert, "not_valid_after_utc", None) or \
            datetime.datetime.combine(cert.not_valid_after.date(),
                                      datetime.time.min,
                                      tzinfo=datetime.timezone.utc)
        if not_after < datetime.datetime.now(datetime.timezone.utc):
            return f"Certificate expired on {not_after.strftime('%Y-%m-%d')}"

        return None
    except Exception as e:
        return f"Invalid certificate or key: {e}"


# ── PFX / PKCS#12 parsing ────────────────────────────────────────────────────

def parse_pfx(pfx_bytes: bytes, password: str = "") -> "tuple[str, str]":
    """
    Extract certificate PEM and private key PEM from a PKCS#12 (.pfx / .p12) file.

    Args:
        pfx_bytes: Raw bytes of the PFX file.
        password:  Optional password protecting the PFX (empty string = no password).

    Returns (cert_pem: str, key_pem: str).
    Raises ValueError on any parsing failure.
    """
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, NoEncryption, pkcs12,
    )

    pwd = password.encode("utf-8") if password else None
    try:
        private_key, certificate, chain = pkcs12.load_key_and_certificates(pfx_bytes, pwd)
    except Exception as e:
        raise ValueError(f"Failed to parse PFX file: {e}")

    if certificate is None:
        raise ValueError("PFX file does not contain a certificate")
    if private_key is None:
        raise ValueError("PFX file does not contain a private key")

    cert_pem = certificate.public_bytes(Encoding.PEM).decode("utf-8")
    # Append any intermediate CA certs in the chain
    if chain:
        for ca in chain:
            cert_pem += ca.public_bytes(Encoding.PEM).decode("utf-8")

    key_pem = private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=NoEncryption(),
    ).decode("utf-8")

    return cert_pem, key_pem


# ── DER certificate conversion ──────────────────────────────────────────────

def load_der_cert(der_bytes: bytes) -> str:
    """
    Convert a DER-encoded certificate (.cer / .der) to PEM string.
    Also accepts PEM input (returns it unchanged).

    Returns cert_pem string.
    Raises ValueError on failure.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding

    # If it already looks like PEM, try PEM first
    if der_bytes.lstrip().startswith(b"-----BEGIN"):
        try:
            cert = x509.load_pem_x509_certificate(der_bytes)
            return cert.public_bytes(Encoding.PEM).decode("utf-8")
        except Exception:
            pass

    # Try DER
    try:
        cert = x509.load_der_x509_certificate(der_bytes)
        return cert.public_bytes(Encoding.PEM).decode("utf-8")
    except Exception as e:
        raise ValueError(f"Failed to parse certificate file: {e}")


# ── CSR generation ───────────────────────────────────────────────────────────

def generate_csr(
    hostname:   str = "localhost",
    org_name:   str = "",
    org_unit:   str = "",
    country:    str = "",
    state:      str = "",
    locality:   str = "",
    key_size:   int = 2048,
    extra_sans: list = None,
) -> "tuple[str, str]":
    """
    Generate a new RSA private key and Certificate Signing Request (CSR).

    Returns (csr_pem: str, key_pem: str).
    """
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key_size = max(2048, min(4096, int(key_size)))

    import inspect as _inspect
    _backend_kwargs = {}
    if 'backend' in _inspect.signature(rsa.generate_private_key).parameters:
        from cryptography.hazmat.backends import default_backend as _default_backend
        _backend_kwargs = {'backend': _default_backend()}
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size,
                                   **_backend_kwargs)

    # ── Subject ──────────────────────────────────────────────────────────────
    name_attrs = []
    if country:
        name_attrs.append(x509.NameAttribute(NameOID.COUNTRY_NAME, country[:2].upper()))
    if state:
        name_attrs.append(x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, state))
    if locality:
        name_attrs.append(x509.NameAttribute(NameOID.LOCALITY_NAME, locality))
    if org_name:
        name_attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, org_name))
    if org_unit:
        name_attrs.append(x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, org_unit))
    name_attrs.append(x509.NameAttribute(NameOID.COMMON_NAME, hostname or "localhost"))
    subject = x509.Name(name_attrs)

    # ── SAN entries ──────────────────────────────────────────────────────────
    san_entries = [x509.DNSName(hostname or "localhost")]
    _seen = {hostname or "localhost"}
    for san in (extra_sans or []):
        san = san.strip()
        if not san or san in _seen:
            continue
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(san)))
        except ValueError:
            san_entries.append(x509.DNSName(san))
        _seen.add(san)

    builder = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
    )
    csr = builder.sign(key, hashes.SHA256(), **_backend_kwargs)

    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    log.info(
        f"TLS: generated CSR — CN={hostname}, key_size={key_size}, "
        f"SANs={len(san_entries)}"
    )
    return csr_pem, key_pem


# ── Expiry warning on startup ─────────────────────────────────────────────────

def check_cert_expiry_warn(cert_pem: str, warn_days: int = 30) -> None:
    """
    Log a WARNING if the cert expires within warn_days, or an ERROR if already expired.
    """
    info = parse_cert_info(cert_pem)
    if not info:
        return
    days = info.get("days_left", 999)
    if days < 0:
        log.error(
            f"TLS: certificate EXPIRED {-days} day(s) ago "
            f"(expired {info.get('not_after', '?')}). "
            "Upload a new certificate in Settings → Networking."
        )
    elif days <= warn_days:
        log.warning(
            f"TLS: certificate expires in {days} day(s) ({info.get('not_after', '?')}). "
            "Upload a new certificate in Settings → Networking."
        )
