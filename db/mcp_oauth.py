"""
db/mcp_oauth.py — Storage for the MCP OAuth 2.1 authorization server.

Three concerns, all dual-backend via db/helpers:
  • mcp_oauth_clients   — admin-pre-registered OAuth clients (Claude, etc.).
  • mcp_oauth_codes     — single-use, short-lived authorization codes.
  • mcp_oauth_refresh   — rotating refresh tokens.

Access tokens are NOT stored here — they are minted as ordinary mcp-scope
`pw_` api_tokens (see mint_mcp_access_token), so /api/mcp validates an
OAuth-issued token through the exact same path as a static one.

Secrets, codes, and refresh tokens are persisted only as SHA-256 hashes
(via core.auth._hash_token); plaintext is returned to the caller once.
"""
from __future__ import annotations

import json
import secrets
import time

from core.auth    import _hash_token
from core.logger  import log
from db.helpers   import db_query, db_query_one, db_execute, db_cursor
from db.api_tokens import db_create_api_token


# ── Clients ──────────────────────────────────────────────────────────
def db_create_oauth_client(name: str, redirect_uris: list,
                           created_by: str = "") -> tuple | None:
    """Register a client. Returns (client_id, client_secret_plaintext) once,
    or None on failure. Only the secret's hash is stored."""
    client_id     = "mcpc_" + secrets.token_hex(16)
    client_secret = "mcps_" + secrets.token_hex(24)
    ok = db_execute("main",
        "INSERT INTO mcp_oauth_clients "
        "(client_id, client_secret_hash, name, redirect_uris, created_at, created_by) "
        "VALUES (?,?,?,?,?,?)",
        (client_id, _hash_token(client_secret), name,
         json.dumps(list(redirect_uris or [])), time.time(), created_by))
    if not ok:
        return None
    return client_id, client_secret


def _serialise_client(r) -> dict:
    """Public shape — never the secret hash. Accepts a sqlite3.Row or a PG
    dict row; normalised to dict so .get() is safe on both backends."""
    r = dict(r)
    try:
        uris = json.loads(r.get("redirect_uris") or "[]")
    except (ValueError, TypeError):
        uris = []
    return {
        "client_id":     r.get("client_id"),
        "name":          r.get("name") or "",
        "redirect_uris": uris,
        "created_at":    r.get("created_at") or 0,
        "created_by":    r.get("created_by") or "",
    }


def db_list_oauth_clients() -> list:
    rows = db_query("main",
        "SELECT client_id, name, redirect_uris, created_at, created_by "
        "FROM mcp_oauth_clients ORDER BY created_at DESC")
    return [_serialise_client(r) for r in rows]


def db_get_oauth_client(client_id: str) -> dict | None:
    """Full row INCLUDING client_secret_hash + parsed redirect_uris, for
    server-side validation. Returns None if unknown."""
    r = db_query_one("main",
        "SELECT client_id, client_secret_hash, name, redirect_uris, created_at, created_by "
        "FROM mcp_oauth_clients WHERE client_id=?", (client_id,))
    if not r:
        return None
    r = dict(r)
    out = _serialise_client(r)
    out["client_secret_hash"] = r.get("client_secret_hash") or ""
    return out


def db_delete_oauth_client(client_id: str) -> bool:
    """Delete a client and cascade its outstanding codes + refresh tokens."""
    db_execute("main", "DELETE FROM mcp_oauth_codes WHERE client_id=?", (client_id,))
    db_execute("main", "DELETE FROM mcp_oauth_refresh WHERE client_id=?", (client_id,))
    return db_execute("main", "DELETE FROM mcp_oauth_clients WHERE client_id=?", (client_id,))


# ── Authorization codes (single-use, short-lived) ────────────────────
def db_create_oauth_code(client_id: str, redirect_uri: str, code_challenge: str,
                         username: str, scope: str = "mcp", resource: str = "",
                         ttl: int = 60) -> str | None:
    """Mint an authorization code. Returns the plaintext code once."""
    code = secrets.token_urlsafe(32)
    now  = time.time()
    ok = db_execute("main",
        "INSERT INTO mcp_oauth_codes "
        "(code_hash, client_id, redirect_uri, code_challenge, username, scope, resource, expires_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (_hash_token(code), client_id, redirect_uri, code_challenge,
         username, scope, resource, now + ttl, now))
    return code if ok else None


def db_consume_oauth_code(code: str) -> dict | None:
    """Atomically look up and delete a code (single-use). Returns the row
    dict if present AND unexpired, else None. The row is always deleted."""
    chash = _hash_token(code)
    row = db_query_one("main",
        "SELECT client_id, redirect_uri, code_challenge, username, scope, resource, expires_at "
        "FROM mcp_oauth_codes WHERE code_hash=?", (chash,))
    if not row:
        return None
    row = dict(row)
    db_execute("main", "DELETE FROM mcp_oauth_codes WHERE code_hash=?", (chash,))
    if float(row.get("expires_at") or 0) < time.time():
        return None
    return row


# ── Refresh tokens (rotating) ────────────────────────────────────────
def db_create_oauth_refresh(client_id: str, username: str, scope: str = "mcp",
                            ttl_days: int = 30) -> str | None:
    """Mint a refresh token. Returns the plaintext once."""
    tok = "mcpr_" + secrets.token_urlsafe(32)
    now = time.time()
    ok = db_execute("main",
        "INSERT INTO mcp_oauth_refresh "
        "(token_hash, client_id, username, scope, expires_at, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (_hash_token(tok), client_id, username, scope,
         now + ttl_days * 86400, now))
    return tok if ok else None


def db_rotate_oauth_refresh(refresh_token: str, client_id: str) -> dict | None:
    """Validate a refresh token for `client_id`, revoke it (rotation), and
    return {username, scope} so the caller can mint a fresh pair. Returns
    None if unknown / revoked / expired / wrong client."""
    thash = _hash_token(refresh_token)
    row = db_query_one("main",
        "SELECT client_id, username, scope, expires_at, revoked_at "
        "FROM mcp_oauth_refresh WHERE token_hash=?", (thash,))
    if not row:
        return None
    row = dict(row)
    if row.get("revoked_at") is not None:
        return None
    if str(row.get("client_id")) != str(client_id):
        return None
    if row.get("expires_at") is not None and float(row["expires_at"]) < time.time():
        return None
    # Revoke (single rotation) before issuing the replacement.
    db_execute("main",
        "UPDATE mcp_oauth_refresh SET revoked_at=? WHERE token_hash=?",
        (time.time(), thash))
    return {"username": row.get("username"), "scope": row.get("scope") or "mcp"}


# ── Access-token mint (reuses the mcp-scope api_tokens path) ──────────
def mint_mcp_access_token(username: str, client_id: str, ttl: int = 3600) -> str | None:
    """Create a short-lived mcp-scope api_token and return its plaintext.
    Validation at /api/mcp then flows through auth_check_api_token unchanged."""
    plaintext = "pw_" + secrets.token_hex(32)
    tid = db_create_api_token(_hash_token(plaintext), f"mcp-oauth:{client_id}",
                              username, "mcp", time.time() + ttl)
    return plaintext if tid is not None else None


# ── Housekeeping ─────────────────────────────────────────────────────
def db_cleanup_oauth_expired() -> None:
    """Best-effort lazy GC: drop expired codes, expired/revoked refresh
    tokens, and expired oauth-minted access tokens. Called from /token."""
    now = time.time()
    try:
        with db_cursor("main") as cur:
            from db.backend import is_pg
            ph = "%s" if is_pg() else "?"
            cur.execute(f"DELETE FROM mcp_oauth_codes WHERE expires_at < {ph}", (now,))
            cur.execute(
                f"DELETE FROM mcp_oauth_refresh WHERE revoked_at IS NOT NULL "
                f"OR (expires_at IS NOT NULL AND expires_at < {ph})", (now,))
            cur.execute(
                f"DELETE FROM api_tokens WHERE scope='mcp' AND name LIKE 'mcp-oauth:%' "
                f"AND expires_at IS NOT NULL AND expires_at < {ph}", (now,))
    except Exception as e:
        log.debug(f"oauth cleanup skipped: {type(e).__name__}: {e}")
