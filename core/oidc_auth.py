"""
core/oidc_auth.py — OpenID Connect Relying Party for PingWatch.

Authorization Code flow with PKCE (S256). JWKS-based JWT validation.
Auto-discovery via .well-known/openid-configuration. Uses authlib
(lazy-imported) so installs without OIDC configured never pay the cost.

Public API:
  _get_cfg()                                -> dict (decrypts client secret)
  get_oidc_status()                         -> dict (badge payload)
  oidc_fetch_discovery(issuer_url)          -> dict (discovery doc)
  oidc_refresh_discovery()                  -> dict (refresh + persist)
  oidc_build_auth_url()                     -> dict {url, state, ...}
  oidc_exchange_code(code, state)           -> dict (parsed claims)
  oidc_test_config()                        -> (ok, message, detail)
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
import urllib.request

import core.settings as _settings
from core.logger import log


# ── Status tracking ─────────────────────────────────────────────────

_last_ok_ts: float | None = None
_last_err: dict = {}
_last_warn: dict = {}

_status_lock = threading.Lock()


def _record_ok() -> None:
    global _last_ok_ts
    with _status_lock:
        _last_ok_ts = time.time()


def _record_err(msg: str) -> None:
    global _last_err
    with _status_lock:
        _last_err = {"ts": time.time(), "msg": (msg or "")[:300]}


def _record_warn(msg: str) -> None:
    global _last_warn
    with _status_lock:
        _last_warn = {"ts": time.time(), "msg": (msg or "")[:300]}


def get_oidc_status() -> dict:
    enabled = int(_settings.get("oidc_enabled", 0) or 0)
    issuer  = (_settings.get("oidc_issuer_url", "") or "").strip()
    client  = (_settings.get("oidc_client_id", "") or "").strip()
    secret  = (_settings.get("oidc_client_secret_enc", "") or "").strip()
    cache   = (_settings.get("oidc_discovery_cache", "") or "").strip()
    err_ts  = _last_err.get("ts") if _last_err else None
    warn_ts = _last_warn.get("ts") if _last_warn else None
    if not enabled or not issuer or not client or not secret:
        state = "unconfigured"
    elif not cache:
        state = "configured"
    elif err_ts and (not _last_ok_ts or err_ts > _last_ok_ts):
        state = "error"
    elif warn_ts and warn_ts > (_last_ok_ts or 0) and warn_ts > (err_ts or 0):
        state = "warning"
    elif _last_ok_ts:
        state = "ok"
    else:
        state = "configured"
    return {
        "state":         state,
        "last_ok_ts":    _last_ok_ts,
        "last_err_ts":   err_ts,
        "last_err_msg":  _last_err.get("msg", "") if _last_err else "",
        "last_warn_ts":  warn_ts,
        "last_warn_msg": _last_warn.get("msg", "") if _last_warn else "",
    }


# ── In-memory state store (CSRF + PKCE) ────────────────────────────

_STATES: dict = {}
_STATE_LOCK = threading.Lock()
_STATE_TTL  = 300  # seconds


def _state_put(state: str, data: dict) -> None:
    with _STATE_LOCK:
        _prune_states_locked()
        _STATES[state] = {"data": data, "created": time.time()}


def _state_take(state: str) -> dict | None:
    with _STATE_LOCK:
        _prune_states_locked()
        entry = _STATES.pop(state, None)
    return entry["data"] if entry else None


def _prune_states_locked() -> None:
    now = time.time()
    stale = [k for k, v in _STATES.items() if now - v["created"] > _STATE_TTL]
    for k in stale:
        _STATES.pop(k, None)


# ── Config loader ──────────────────────────────────────────────────

def _get_cfg() -> dict:
    from db.backups import decrypt_pw
    secret_enc = _settings.get("oidc_client_secret_enc", "") or ""
    client_secret = decrypt_pw(secret_enc) if secret_enc else ""
    discovery_raw = _settings.get("oidc_discovery_cache", "") or ""
    discovery = {}
    if discovery_raw:
        try:
            discovery = json.loads(discovery_raw)
        except (ValueError, TypeError):
            discovery = {}
    return {
        "enabled":           bool(int(_settings.get("oidc_enabled", 0) or 0)),
        "issuer_url":        (_settings.get("oidc_issuer_url", "") or "").strip(),
        "client_id":         (_settings.get("oidc_client_id", "") or "").strip(),
        "client_secret":     client_secret,
        "redirect_uri":      (_settings.get("oidc_redirect_uri", "") or "").strip(),
        "scopes":            (_settings.get("oidc_scopes", "") or "openid profile email groups").strip(),
        "discovery":         discovery,
        "discovery_ts":      int(_settings.get("oidc_discovery_fetched_ts", 0) or 0),
        "claim_username":    (_settings.get("oidc_claim_username", "") or "preferred_username").strip(),
        "claim_email":       (_settings.get("oidc_claim_email", "") or "email").strip(),
        "claim_display_name":(_settings.get("oidc_claim_display_name", "") or "name").strip(),
        "claim_groups":      (_settings.get("oidc_claim_groups", "") or "groups").strip(),
        "auto_provision":    bool(int(_settings.get("oidc_auto_provision", 1) or 0)),
        "default_role":      (_settings.get("oidc_default_role", "") or "viewer").strip(),
        "allow_unmapped":    bool(int(_settings.get("oidc_allow_unmapped", 1) or 0)),
        "display_name":      (_settings.get("oidc_display_name", "") or "Single Sign-On").strip(),
    }


# ── Discovery fetch ────────────────────────────────────────────────

def oidc_fetch_discovery(issuer_url: str) -> dict:
    """Fetch .well-known/openid-configuration from the issuer. Returns parsed dict.
    Raises on network error / invalid JSON.
    """
    if not issuer_url:
        raise ValueError("issuer_url is empty")
    url = issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    req = urllib.request.Request(url, headers={
        "User-Agent": "PingWatch/OIDC-RP",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
    try:
        disc = json.loads(data.decode("utf-8", errors="replace"))
    except ValueError:
        raise ValueError("discovery document is not valid JSON")
    # Basic sanity — check required fields
    for required in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        if required not in disc:
            raise ValueError(f"discovery document missing required field: {required}")
    return disc


def oidc_refresh_discovery() -> dict:
    """Re-fetch discovery for the configured issuer_url, persist, return result."""
    from db import db_save_settings
    cfg = _get_cfg()
    if not cfg["issuer_url"]:
        return {"ok": False, "message": "issuer_url not configured"}
    try:
        disc = oidc_fetch_discovery(cfg["issuer_url"])
    except Exception as e:
        _record_err(f"discovery fetch failed: {str(e)[:200]}")
        return {"ok": False, "message": f"discovery fetch failed: {str(e)[:200]}"}
    disc_json = json.dumps(disc)
    fetched_ts = int(time.time())
    _settings.load({
        "oidc_discovery_cache":        disc_json,
        "oidc_discovery_fetched_ts":   str(fetched_ts),
    })
    db_save_settings({
        "oidc_discovery_cache":        disc_json,
        "oidc_discovery_fetched_ts":   str(fetched_ts),
    })
    _record_ok()
    log.info(f"OIDC discovery refreshed — issuer={disc.get('issuer','?')}")
    return {
        "ok": True,
        "issuer":                disc.get("issuer", ""),
        "authorization_endpoint":disc.get("authorization_endpoint", ""),
        "token_endpoint":        disc.get("token_endpoint", ""),
        "userinfo_endpoint":     disc.get("userinfo_endpoint", ""),
        "jwks_uri":              disc.get("jwks_uri", ""),
        "end_session_endpoint":  disc.get("end_session_endpoint", ""),
        "message":               "discovery refreshed",
    }


# ── Authorization URL build (PKCE S256) ────────────────────────────

def oidc_build_auth_url() -> dict:
    """Build the authorization URL with PKCE + state. Stores verifier in state cache.

    Returns {"ok": bool, "url": str, "message": str}.
    """
    cfg = _get_cfg()
    if not cfg["enabled"]:
        return {"ok": False, "url": "", "message": "OIDC is not enabled"}
    if not cfg["client_id"]:
        return {"ok": False, "url": "", "message": "Client ID not configured"}
    if not cfg["redirect_uri"]:
        return {"ok": False, "url": "", "message": "Redirect URI not configured"}

    disc = cfg["discovery"]
    if not disc or not disc.get("authorization_endpoint"):
        return {"ok": False, "url": "", "message": "Discovery not fetched — click Refresh"}

    auth_ep = disc["authorization_endpoint"]

    # PKCE
    code_verifier  = secrets.token_urlsafe(64)[:96]
    digest         = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    # CSRF state + OIDC nonce
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(16)

    _state_put(state, {
        "code_verifier": code_verifier,
        "nonce":         nonce,
        "created":       time.time(),
    })

    params = {
        "response_type":        "code",
        "client_id":            cfg["client_id"],
        "redirect_uri":         cfg["redirect_uri"],
        "scope":                cfg["scopes"],
        "state":                state,
        "nonce":                nonce,
        "code_challenge":       code_challenge,
        "code_challenge_method":"S256",
    }
    url = auth_ep + ("&" if "?" in auth_ep else "?") + urllib.parse.urlencode(params)
    return {"ok": True, "url": url, "state": state, "message": ""}


# ── Token exchange + ID Token validation ──────────────────────────

def oidc_exchange_code(code: str, state: str) -> dict:
    """Exchange authorization code for tokens, validate ID Token, extract claims.

    Returns {"ok": bool, "external_id": str, "username": str, "email": str,
             "display_name": str, "groups": [str], "message": str}.
    """
    cfg = _get_cfg()
    if not cfg["enabled"]:
        return {"ok": False, "message": "OIDC is not enabled"}

    # State replay protection + PKCE verifier recovery
    entry = _state_take(state)
    if entry is None:
        _record_err("state invalid or expired")
        return {"ok": False, "message": "Invalid or expired sign-in session — please try again"}

    code_verifier = entry.get("code_verifier", "")
    expected_nonce = entry.get("nonce", "")

    disc = cfg["discovery"]
    if not disc or not disc.get("token_endpoint"):
        _record_err("discovery missing token endpoint")
        return {"ok": False, "message": "OIDC discovery is stale"}

    # Exchange code
    token_data = urllib.parse.urlencode({
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  cfg["redirect_uri"],
        "client_id":     cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "code_verifier": code_verifier,
    }).encode("ascii")
    req = urllib.request.Request(disc["token_endpoint"], data=token_data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept":       "application/json",
        "User-Agent":   "PingWatch/OIDC-RP",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            token_resp = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        _record_err(f"token exchange failed: {str(e)[:200]}")
        return {"ok": False, "message": f"token exchange failed: {str(e)[:200]}"}

    id_token = token_resp.get("id_token", "")
    if not id_token:
        _record_err("token response missing id_token")
        return {"ok": False, "message": "id_token missing from token response"}

    # Validate JWT via JWKS
    claims = _validate_id_token(id_token, disc.get("jwks_uri", ""),
                                 cfg["client_id"], disc.get("issuer", ""),
                                 expected_nonce)
    if not claims.get("ok"):
        _record_err(f"id_token validation failed: {claims.get('message','')}")
        return {"ok": False, "message": claims.get("message", "id_token invalid")}

    # Extract user fields
    c = claims["claims"]
    sub = str(c.get("sub", "") or "").strip()
    if not sub:
        _record_err("id_token missing sub")
        return {"ok": False, "message": "id_token missing subject"}

    username_raw = str(c.get(cfg["claim_username"], "")
                       or c.get("preferred_username", "")
                       or c.get("email", "")
                       or c.get("sub", "")).strip()
    email        = str(c.get(cfg["claim_email"], "") or c.get("email", "")).strip()
    display_name = str(c.get(cfg["claim_display_name"], "")
                       or c.get("name", "")
                       or c.get("given_name", "")).strip()

    groups_claim = c.get(cfg["claim_groups"])
    if groups_claim is None:
        groups_claim = c.get("groups", [])
    if isinstance(groups_claim, str):
        groups = [g.strip() for g in groups_claim.split(",") if g.strip()]
    elif isinstance(groups_claim, list):
        groups = [str(g).strip() for g in groups_claim if str(g).strip()]
    else:
        groups = []

    external_id = f"oidc|{disc.get('issuer','')}|{sub}"

    _record_ok()
    return {
        "ok":           True,
        "external_id":  external_id,
        "username":     username_raw,
        "email":        email,
        "display_name": display_name,
        "groups":       groups,
        "sub":          sub,
        "message":      "",
    }


def _validate_id_token(id_token: str, jwks_uri: str, client_id: str,
                       issuer: str, expected_nonce: str) -> dict:
    """Verify JWT signature via JWKS, check iss/aud/exp/nonce.

    Returns {"ok": bool, "claims": dict, "message": str}.
    """
    # Lazy-import authlib — graceful fallback if missing
    try:
        from authlib.jose import jwt, JsonWebKey
    except ImportError:
        return {"ok": False, "claims": {},
                "message": "authlib not installed — run setup wizard"}

    # Fetch JWKS
    if not jwks_uri:
        return {"ok": False, "claims": {}, "message": "no jwks_uri in discovery"}
    try:
        req = urllib.request.Request(jwks_uri, headers={"User-Agent": "PingWatch/OIDC-RP"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            jwks = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        return {"ok": False, "claims": {}, "message": f"JWKS fetch failed: {str(e)[:150]}"}

    try:
        key_set = JsonWebKey.import_key_set(jwks)
        claims = jwt.decode(
            id_token,
            key=key_set,
            claims_options={
                "iss": {"essential": True, "value": issuer} if issuer else {"essential": True},
                "aud": {"essential": True, "value": client_id},
                "exp": {"essential": True},
            },
        )
        claims.validate(leeway=60)  # 60s clock skew tolerance
    except Exception as e:
        return {"ok": False, "claims": {}, "message": f"JWT validation failed: {str(e)[:150]}"}

    # Nonce check (OIDC spec requirement)
    if expected_nonce and claims.get("nonce") != expected_nonce:
        return {"ok": False, "claims": {}, "message": "nonce mismatch — possible replay attack"}

    return {"ok": True, "claims": dict(claims), "message": ""}


# ── Test helper ────────────────────────────────────────────────────

def oidc_test_config() -> tuple[bool, str, dict]:
    """Dry-run validation. Returns (ok, message, detail)."""
    detail: dict = {}
    cfg = _get_cfg()
    if not cfg["enabled"]:
        return False, "OIDC not enabled", detail
    if not cfg["issuer_url"]:
        return False, "issuer URL not set", detail
    if not cfg["client_id"]:
        return False, "client_id not set", detail
    if not cfg["client_secret"]:
        return False, "client_secret not set", detail
    if not cfg["redirect_uri"]:
        return False, "redirect_uri not set", detail

    # Discovery
    try:
        disc = oidc_fetch_discovery(cfg["issuer_url"])
    except Exception as e:
        return False, f"discovery fetch failed: {str(e)[:150]}", detail
    detail["issuer"]                = disc.get("issuer", "")
    detail["authorization_endpoint"] = disc.get("authorization_endpoint", "")
    detail["token_endpoint"]        = disc.get("token_endpoint", "")
    detail["jwks_uri"]              = disc.get("jwks_uri", "")

    # Check authlib importable
    try:
        import authlib  # noqa: F401
        detail["authlib"] = "available"
    except ImportError:
        return False, "authlib not installed — run setup wizard", detail

    # JWKS reachable
    try:
        req = urllib.request.Request(disc["jwks_uri"],
                                     headers={"User-Agent": "PingWatch/OIDC-RP"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            jwks = json.loads(resp.read().decode("utf-8", errors="replace"))
        detail["jwks_keys"] = len(jwks.get("keys", []))
    except Exception as e:
        return False, f"JWKS fetch failed: {str(e)[:150]}", detail

    _record_ok()
    return True, "OIDC config valid", detail
