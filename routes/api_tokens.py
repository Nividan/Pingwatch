"""
routes/api_tokens.py — Bearer-token management (scripts / CI / Terraform).

GET    /api/tokens           admin — list tokens (?user=<name> to filter)
POST   /api/tokens           admin — create {name, scope, expires_at?} → 201 with PLAINTEXT (one-time)
DELETE /api/tokens/<id>      admin — revoke

The plaintext token value is returned exactly once, in the create response.
Only its SHA-256 hash is stored. Cookie-session callers create + revoke;
API-token callers are explicitly blocked from creating new tokens.
"""
from __future__ import annotations

import re
import secrets
from urllib.parse import urlparse, parse_qs

from core.auth   import _hash_token, auth_evict_api_token_hash
from core.logger import log
from db import (
    db_log_audit,
    db_create_api_token, db_list_api_tokens,
    db_get_api_token, db_revoke_api_token,
)


_RE_TOKENS_COLL = re.compile(r"^/api/tokens/?$")
_RE_TOKENS_ITEM = re.compile(r"^/api/tokens/(\d+)/?$")

_NAME_MAX  = 100
# 'mcp' tokens are jailed to /api/mcp (read-only AI-agent tooling), exactly
# as 'probe' tokens are jailed to /api/agent/* — see server.py _auth/_require.
_VALID_SCOPES = ("read", "full", "mcp")


def _gen_token() -> str:
    """`pw_` + 32 random bytes hex → 67 chars, visually distinct from
    session cookies and existing GitHub/Stripe-style identifiers."""
    return "pw_" + secrets.token_hex(32)


def _serialise(row: dict) -> dict:
    """Public row shape — never includes plaintext or the stored hash."""
    return {
        "id":           row.get("id"),
        "name":         row.get("name") or "",
        "username":     row.get("username") or "",
        "scope":        row.get("scope") or "",
        "role":         row.get("role") or "viewer",
        "created_at":   row.get("created_at") or 0,
        "expires_at":   row.get("expires_at"),
        "last_used_at": row.get("last_used_at"),
        "revoked_at":   row.get("revoked_at"),
    }


def _query_value(path: str, key: str) -> str:
    """Read a single query-string value (first occurrence)."""
    q = urlparse(path).query
    if not q:
        return ""
    vals = parse_qs(q).get(key, [])
    return vals[0] if vals else ""


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""
    base_path = path.split("?", 1)[0]

    # ── GET /api/tokens — list (admin; ?user=<name> to filter) ──
    if _RE_TOKENS_COLL.match(base_path) and method == "GET":
        user, _ = h._require("admin")
        if not user:
            return True
        filt_user = _query_value(path, "user").strip() or None
        rows = db_list_api_tokens(username=filt_user, include_revoked=False)
        h._json(200, {"tokens": [_serialise(r) for r in rows]})
        return True

    # ── POST /api/tokens — create (admin only, never via api_token) ──
    if _RE_TOKENS_COLL.match(base_path) and method == "POST":
        user, role = h._require("admin")
        if not user:
            return True
        # Block token-creates-token: an api_token cannot mint another.
        _, _, _, kind = h._auth_principal()
        if kind == "api_token":
            h._json(403, {"error": "API tokens cannot create other tokens"})
            return True

        name  = (body.get("name") or "").strip()
        scope = (body.get("scope") or "").strip().lower()
        exp   = body.get("expires_at")

        if not name:
            h._json(400, {"error": "name is required"}); return True
        if len(name) > _NAME_MAX:
            h._json(400, {"error": f"name too long (max {_NAME_MAX})"}); return True
        if scope not in _VALID_SCOPES:
            h._json(400, {"error": "scope must be 'read', 'full', or 'mcp'"}); return True
        # expires_at is optional; accept None/"" or a positive numeric epoch.
        if exp in (None, ""):
            expires_at = None
        else:
            try:
                expires_at = float(exp)
                if expires_at <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                h._json(400, {"error": "expires_at must be a positive epoch seconds value"})
                return True

        plaintext = _gen_token()
        token_hash = _hash_token(plaintext)
        tid = db_create_api_token(token_hash, name, user, scope, expires_at)
        if tid is None:
            # Generic error to the client; full detail is server-logged by the
            # db layer (see project convention — never leak str(e) outwards).
            h._json(500, {"error": "could not create token"})
            return True

        db_log_audit(user, h.client_address[0], "api_token_create",
                     f"{name}#{tid}", f"scope={scope}")
        h._json(201, {
            "id":         tid,
            "name":       name,
            "username":   user,
            "scope":      scope,
            "expires_at": expires_at,
            "token":      plaintext,         # ONE-TIME plaintext reveal
        })
        return True

    # ── DELETE /api/tokens/<id> — revoke (admin) ──
    m = _RE_TOKENS_ITEM.match(base_path)
    if m and method == "DELETE":
        user, _ = h._require("admin")
        if not user:
            return True
        try:
            tid = int(m.group(1))
        except ValueError:
            h._json(400, {"error": "invalid token id"}); return True

        existing = db_get_api_token(tid)
        if not existing:
            h._json(404, {"error": "token not found"}); return True
        if existing.get("revoked_at") is not None:
            h._json(409, {"error": "token already revoked"}); return True

        token_hash = db_revoke_api_token(tid)
        if not token_hash:
            log.error(f"api_token revoke: db update failed for id={tid}")
            h._json(500, {"error": "could not revoke token"})
            return True

        # Evict the in-memory cache so the next request misses and sees the
        # fresh revoked_at — bounds revoke latency to ~0s for active clients.
        auth_evict_api_token_hash(token_hash)
        db_log_audit(user, h.client_address[0], "api_token_revoke",
                     f"{existing.get('name')}#{tid}",
                     f"owner={existing.get('username')}")
        h._json(200, {"ok": True})
        return True

    return False
