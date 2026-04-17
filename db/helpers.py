"""
db/helpers.py — Backend-agnostic query/execute helpers.

These helpers wrap the most common SQLite/PostgreSQL dual-backend pattern.
They eliminate ~50% of the if/is_pg() boilerplate scattered across db/*.py
without requiring a full DAL rewrite.

USAGE
─────
    rows = db_query("main", "SELECT key, value FROM app_settings")
    for r in rows:
        print(r["key"], r["value"])

    db_execute("main", "INSERT INTO foo (a,b) VALUES (?,?)", (1, 2))

DESIGN NOTES
────────────
- Both backends return dict-like rows so callers use r["col"] uniformly.
  SQLite is configured with row_factory = sqlite3.Row.
- Placeholders are normalized: write "?" in queries; this layer rewrites
  to %s for PG. Keep queries portable (no PG-only or SQLite-only syntax).
- For complex queries (window functions, ON CONFLICT, EXPLAIN, bulk
  inserts) use db_cursor() directly and branch on is_pg() yourself.
- Errors are logged with the first 60 chars of the query for context;
  callers receive an empty list / False on failure (graceful degradation).
"""

import sqlite3
from contextlib import contextmanager

from core.config import DB_PATH, LOGS_DB_PATH
from core.logger import log
from db.backend  import is_pg


# ── Internals ────────────────────────────────────────────────────

def _path_for(schema: str) -> str:
    """Map a logical schema name to the SQLite file path."""
    return LOGS_DB_PATH if schema == "logs" else DB_PATH


def _ph(query: str) -> str:
    """Convert ? placeholders to %s for PostgreSQL. Pass-through for SQLite."""
    return query.replace("?", "%s") if is_pg() else query


# ── Public API ───────────────────────────────────────────────────

@contextmanager
def db_cursor(schema: str = "main"):
    """Unified cursor context manager.

    Yields a cursor object that supports `cur.execute(query, params)` and
    `cur.fetchall()`. Commits on successful exit (PG handled by pg_cursor).

    Use this directly when you need control over multiple statements,
    bulk inserts, or backend-specific syntax.
    """
    if is_pg():
        # Lazy import — psycopg2 only loaded when PG backend is active
        from db.pg_pool import pg_cursor
        with pg_cursor(schema) as cur:
            yield cur
    else:
        con = sqlite3.connect(_path_for(schema), timeout=15)
        con.row_factory = sqlite3.Row  # Enable dict-like row access
        cur = None
        try:
            cur = con.cursor()
            yield cur
            con.commit()
        finally:
            if cur:
                cur.close()
            con.close()


def db_query(schema: str, query: str, params: tuple = ()) -> list:
    """Run a SELECT and return rows as a list of dict-like objects.

    Returns [] on error (logged). Both PG and SQLite rows support
    bracket access: row["column_name"].
    """
    try:
        with db_cursor(schema) as cur:
            cur.execute(_ph(query), params)
            return cur.fetchall()
    except Exception as e:
        # Quiet path for the post-shutdown race: a background thread that
        # didn't notice its stop signal in time hits a closed pool. Log at
        # DEBUG so we don't spam ERROR for an expected shutdown condition.
        if type(e).__name__ == "PoolClosedError":
            log.debug(f"db_query skipped (pool closed): {query[:60]}…")
            return []
        log.error(f"db_query failed [{query[:60]}…]: {type(e).__name__}: {e}")
        return []


def db_query_one(schema: str, query: str, params: tuple = ()):
    """Run a SELECT and return the first row, or None if no rows / on error."""
    rows = db_query(schema, query, params)
    return rows[0] if rows else None


def db_execute(schema: str, query: str, params: tuple = ()) -> bool:
    """Run an INSERT/UPDATE/DELETE. Returns True on success, False on error.

    Errors are logged. Use db_cursor() directly if you need to know how
    many rows were affected (cur.rowcount).
    """
    try:
        with db_cursor(schema) as cur:
            cur.execute(_ph(query), params)
        return True
    except Exception as e:
        log.error(f"db_execute failed [{query[:60]}…]: {type(e).__name__}: {e}")
        return False


def db_executemany(schema: str, query: str, seq_of_params) -> bool:
    """Run an INSERT/UPDATE for many rows. Returns True on success."""
    try:
        with db_cursor(schema) as cur:
            cur.executemany(_ph(query), seq_of_params)
        return True
    except Exception as e:
        log.error(f"db_executemany failed [{query[:60]}…]: {type(e).__name__}: {e}")
        return False


def db_upsert(schema: str, table: str, columns: list, values: tuple,
              conflict_col: str) -> bool:
    """Insert or update — abstracts SQLite's INSERT OR REPLACE vs PG's ON CONFLICT.

    Example:
        db_upsert("main", "app_settings", ["key", "value"], ("foo", "bar"), "key")
    """
    cols_csv = ",".join(columns)
    placeholders = ",".join(["?"] * len(columns))
    if is_pg():
        update_set = ",".join(f"{c}=EXCLUDED.{c}" for c in columns if c != conflict_col)
        query = (f"INSERT INTO {table} ({cols_csv}) VALUES ({placeholders}) "
                 f"ON CONFLICT ({conflict_col}) DO UPDATE SET {update_set}")
    else:
        query = f"INSERT OR REPLACE INTO {table} ({cols_csv}) VALUES ({placeholders})"
    return db_execute(schema, query, values)
