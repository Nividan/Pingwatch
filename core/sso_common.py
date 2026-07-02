"""
core/sso_common.py — shared JIT provisioning for SAML and OIDC SSO.

Mirrors the LDAP/RADIUS provisioning pattern: on first successful IdP
authentication, create a local `users` row with a sentinel password
(`__saml__` or `__oidc__`), sync the display_name / email / group
membership, and return (username, role). Subsequent logins update the
synced fields but never create duplicates.

Single entry point: sso_provision_or_sync().
"""

from __future__ import annotations

import threading
import time

from core.logger import log


# ── Pending SSO 2FA challenges (post-IdP, pre-TOTP) ──────────────────
# When a TOTP-enrolled user authenticates via SAML/OIDC, the IdP step is done
# but the second factor isn't. We park (username, role) here keyed by a random
# `pending` id and redirect the browser to /?sso_totp=<pending>; the frontend
# collects the code and POSTs /api/login/sso-totp, which verifies and issues the
# real session. Shared by both SSO backends so the consumer is provider-neutral.
# (Previously each backend wrote its own store and NOTHING consumed it, so a
# TOTP-enrolled SSO user could never complete login.)
_SSO_TOTP: dict = {}
_SSO_TOTP_LOCK = threading.Lock()
_SSO_TOTP_TTL = 300  # seconds


def _sso_totp_prune_locked() -> None:
    now = time.time()
    for k in [k for k, v in _SSO_TOTP.items() if now - v["created"] > _SSO_TOTP_TTL]:
        _SSO_TOTP.pop(k, None)


def sso_totp_put(pending: str, username: str, role: str) -> None:
    with _SSO_TOTP_LOCK:
        _sso_totp_prune_locked()
        _SSO_TOTP[pending] = {"username": username, "role": role,
                              "created": time.time()}


def sso_totp_peek(pending: str) -> dict | None:
    """Return {username, role} WITHOUT removing (so a wrong 2FA code can be
    retried within the TTL), or None if unknown/expired."""
    if not pending:
        return None
    with _SSO_TOTP_LOCK:
        _sso_totp_prune_locked()
        entry = _SSO_TOTP.get(pending)
    return {"username": entry["username"], "role": entry["role"]} if entry else None


def sso_totp_consume(pending: str) -> None:
    """Remove a pending challenge (call once the 2FA code has verified)."""
    with _SSO_TOTP_LOCK:
        _SSO_TOTP.pop(pending, None)


def _match_group(groups: list, mapped_groups: list, value_key: str) -> dict | None:
    """Find the first mapped group whose value_key appears in the user's group list.

    mapped_groups: list of {id, name, <value_key>, default_role} dicts.
    groups:        list of string values from the IdP (SAML attribute values,
                   OIDC claim values).
    value_key:     'saml_group_value' or 'oidc_group_value'.
    Case-insensitive comparison — tolerates common formatting differences.
    """
    if not groups or not mapped_groups:
        return None
    norm = {str(g).strip().lower() for g in groups if g}
    for mg in mapped_groups:
        v = (mg.get(value_key) or "").strip().lower()
        if v and v in norm:
            return mg
    return None


def sso_provision_or_sync(*, external_id: str, username_hint: str,
                          email: str, display_name: str,
                          groups: list, auth_type: str,
                          default_role: str = "viewer",
                          allow_unmapped: bool = True) -> tuple[str, str] | None:
    """Resolve or JIT-create a local user row for an SSO login.

    Flow:
      1. Look up by external_id (stable across username changes in the IdP).
      2. If found → sync profile fields + group/role + return (username, role).
      3. If not found and username_hint exists with matching auth_type but
         external_id was empty → adopt the row (admin pre-created a shell).
      4. If not found → provision a new row IF the user matches a mapped
         group (or allow_unmapped=True with default_role).

    auth_type: 'saml' or 'oidc'.
    Returns (username, role) on success, or None if user is rejected
    (no matching group + allow_unmapped=False).
    """
    from db import (db_add_sso_user, db_get_user_by_external_id,
                    db_update_external_id, db_update_profile,
                    db_get_saml_mapped_groups, db_get_oidc_mapped_groups)

    if auth_type not in ("saml", "oidc"):
        log.error(f"sso_provision_or_sync: invalid auth_type {auth_type!r}")
        return None

    # Group → role mapping — pick the source lookup + column name
    if auth_type == "saml":
        mapped_groups = db_get_saml_mapped_groups()
        value_key = "saml_group_value"
    else:
        mapped_groups = db_get_oidc_mapped_groups()
        value_key = "oidc_group_value"

    match = _match_group(groups or [], mapped_groups, value_key)
    matched_role    = (match["default_role"] if match else None)
    matched_gid     = (match["id"]          if match else None)

    # Rejection gate — if no mapped group and policy disallows unmapped, reject
    if match is None and not allow_unmapped:
        log.info(f"SSO login rejected ({auth_type}): {username_hint!r} "
                 f"not in any mapped group")
        return None

    # Resolve role: mapped group wins, else default
    role = matched_role or default_role or "viewer"

    # 1. Look up by external_id
    existing = db_get_user_by_external_id(external_id) if external_id else None
    if existing:
        # Sync profile — tolerate DB errors silently, still return success
        try:
            db_update_profile(existing["username"],
                              display_name or existing["full_name"],
                              email or existing["email"],
                              group_id=matched_gid if match else existing.get("group_id"),
                              role=role)
        except Exception as e:
            log.warning(f"SSO profile sync failed for {existing['username']!r}: {e}")
        return (existing["username"], role)

    # 2. Adoption path — admin pre-created a shell with matching username + auth_type
    try:
        from db.helpers import db_query
        rows = db_query("main",
                        "SELECT username, auth_type, external_id FROM users "
                        "WHERE username=?", (username_hint,))
        if rows:
            r = rows[0]
            if (r.get("auth_type") == auth_type
                    and not (r.get("external_id") or "")):
                # Claim the shell row
                db_update_external_id(username_hint, external_id)
                db_update_profile(username_hint, display_name, email,
                                  group_id=matched_gid if match else None,
                                  role=role)
                log.info(f"SSO adoption: claimed pre-existing shell {username_hint!r} "
                         f"for {auth_type}")
                return (username_hint, role)
    except Exception as e:
        log.warning(f"SSO adoption check failed: {e}")

    # 3. JIT provisioning — create a fresh row
    ok = db_add_sso_user(
        username=username_hint,
        auth_type=auth_type,
        external_id=external_id,
        role=role,
        full_name=display_name or "",
        email=email or "",
        group_id=matched_gid,
    )
    if not ok:
        log.error(f"SSO JIT provisioning failed for {username_hint!r} "
                  f"({auth_type}) — username or external_id collision")
        return None

    log.info(f"SSO JIT provisioned: {username_hint!r} ({auth_type}, role={role})")
    return (username_hint, role)


def sanitize_username(raw: str) -> str:
    """Strip whitespace; collapse email-style to local-part; fall back to raw."""
    if not raw:
        return ""
    raw = raw.strip()
    if "@" in raw and raw.count("@") == 1:
        local = raw.split("@", 1)[0]
        if local:
            return local
    return raw
