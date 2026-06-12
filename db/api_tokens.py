"""
db/api_tokens.py — CRUD for the api_tokens table (Bearer-token auth).

Tokens are SHA-256 hashed before storage; the plaintext is returned to the
caller exactly once at creation time. Backend-agnostic via db.helpers — the
single CRUD module serves both SQLite and PostgreSQL.
"""
from __future__ import annotations

import time

from core.logger import log
from db.backend  import is_pg
from db.helpers  import db_query, db_query_one, db_execute, db_cursor


def db_create_api_token(token_hash: str, name: str, username: str,
                        scope: str, expires_at: float | None = None,
                        probe_id: str | None = None) -> int | None:
    """Insert a new API token row. Returns the new id, or None on failure.

    scope='probe' tokens belong to a remote probe (distributed probes,
    v1.3): username is the synthetic 'probe:<probe_id>' principal — no
    users row exists for it — and probe_id links back to the probe record.
    """
    if scope not in ("read", "full", "probe"):
        return None
    now = time.time()
    try:
        with db_cursor("main") as cur:
            if is_pg():
                cur.execute(
                    "INSERT INTO api_tokens "
                    "(token_hash, name, username, scope, created_at, expires_at, probe_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (token_hash, name, username, scope, now, expires_at, probe_id))
                row = cur.fetchone()
                return row["id"] if row else None
            else:
                cur.execute(
                    "INSERT INTO api_tokens "
                    "(token_hash, name, username, scope, created_at, expires_at, probe_id) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (token_hash, name, username, scope, now, expires_at, probe_id))
                return cur.lastrowid
    except Exception as e:
        log.error(f"db_create_api_token failed: {type(e).__name__}: {e}")
        return None


def db_get_api_token_by_hash(token_hash: str) -> dict | None:
    """Look a token up by its SHA-256 hash. Returns the row joined with the
    owning user's role, or None if not found / revoked / expired.

    LEFT JOIN: probe-scoped tokens have no users row (their username is the
    synthetic 'probe:<probe_id>'); role comes back '' for them — the
    /api/agent/* scope jail in server.py is their only authority."""
    row = db_query_one("main", """
        SELECT t.id, t.token_hash, t.name, t.username, t.scope,
               t.created_at, t.expires_at, t.last_used_at, t.revoked_at,
               t.probe_id,
               COALESCE(u.role, '') AS role
        FROM api_tokens t
        LEFT JOIN users u ON u.username = t.username
        WHERE t.token_hash = ?
    """, (token_hash,))
    if not row:
        return None
    now = time.time()
    if row["revoked_at"] is not None:
        return None
    if row["expires_at"] is not None and row["expires_at"] < now:
        return None
    return dict(row)


def db_list_api_tokens(username: str | None = None,
                       include_revoked: bool = False) -> list:
    """List tokens (no plaintext, never the hash). Optionally filter by user.
    Revoked tokens are excluded by default."""
    where = []
    params: list = []
    if username:
        where.append("t.username = ?")
        params.append(username)
    if not include_revoked:
        where.append("t.revoked_at IS NULL")
    sql = """
        SELECT t.id, t.name, t.username, t.scope,
               t.created_at, t.expires_at, t.last_used_at, t.revoked_at,
               COALESCE(u.role, '') AS role
        FROM api_tokens t
        LEFT JOIN users u ON u.username = t.username
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.created_at DESC"
    rows = db_query("main", sql, tuple(params))
    return [dict(r) for r in rows]


def db_get_api_token(token_id: int) -> dict | None:
    """Look one token up by id (without plaintext / hash). Used for revoke
    audit so the caller can log the token name."""
    row = db_query_one("main", """
        SELECT id, token_hash, name, username, scope,
               created_at, expires_at, last_used_at, revoked_at
        FROM api_tokens WHERE id = ?
    """, (token_id,))
    return dict(row) if row else None


def db_revoke_api_token(token_id: int) -> str | None:
    """Mark a token revoked. Returns the row's token_hash so the caller can
    evict the in-memory cache. Returns None if not found or already revoked."""
    row = db_get_api_token(token_id)
    if not row or row["revoked_at"] is not None:
        return None
    if not db_execute("main",
                      "UPDATE api_tokens SET revoked_at = ? WHERE id = ?",
                      (time.time(), token_id)):
        return None
    return row["token_hash"]


def db_touch_api_token_last_used(token_id: int) -> None:
    """Slide the last_used_at timestamp forward. Fire-and-forget — failures
    don't affect the calling request."""
    db_execute("main",
               "UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
               (time.time(), token_id))
