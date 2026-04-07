"""
db/pg_pool.py — PostgreSQL connection pool and context managers.

Provides pg_conn() and pg_cursor() context managers that replace the
``sqlite3.connect()`` / ``con.close()`` pattern used throughout the
SQLite backend.
"""

import threading
from contextlib import contextmanager

from core.logger import log

_pool = None   # psycopg2.pool.ThreadedConnectionPool (created by pg_init_pool)
_pool_sema = None  # threading.Semaphore — gates getconn() so callers block instead of crashing


def pg_init_pool():
    """Create the connection pool using settings from db.backend config."""
    global _pool, _pool_sema
    import psycopg2.pool
    from db.backend import get_config

    cfg = get_config()
    maxconn = cfg.get("pg_pool_max", 30)
    _pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=cfg.get("pg_pool_min", 2),
        maxconn=maxconn,
        host=cfg["pg_host"],
        port=cfg["pg_port"],
        dbname=cfg["pg_database"],
        user=cfg["pg_user"],
        password=cfg["pg_password"],
    )
    _pool_sema = threading.Semaphore(maxconn)
    log.info(
        "PostgreSQL pool ready: %s:%s/%s (min=%d max=%d)",
        cfg["pg_host"], cfg["pg_port"], cfg["pg_database"],
        cfg.get("pg_pool_min", 2), maxconn,
    )


def pg_close_pool():
    """Close all pooled connections (called at shutdown)."""
    global _pool
    if _pool:
        try:
            _pool.closeall()
        except Exception as e:
            log.warning(f"Error closing PG pool: {e}")
        _pool = None


def pg_test_connection(host, port, dbname, user, password):
    """Test a PostgreSQL connection.  Returns ``(True, '')`` on success,
    or ``(False, error_message)`` on failure.
    """
    try:
        import psycopg2
    except ImportError:
        return False, "psycopg2 not installed — run: pip install psycopg2-binary"
    try:
        con = psycopg2.connect(
            host=host, port=port, dbname=dbname, user=user, password=password,
            connect_timeout=5,
        )
        con.cursor().execute("SELECT 1")
        con.close()
        return True, ""
    except psycopg2.OperationalError as e:
        msg = str(e).strip()
        if "Connection refused" in msg or "could not connect" in msg:
            return False, f"PostgreSQL server not running at {host}:{port}"
        if "authentication failed" in msg or "password" in msg:
            return False, f"Authentication failed for user '{user}'"
        if "does not exist" in msg:
            return False, f"Database '{dbname}' not found — create it with: CREATE DATABASE {dbname}"
        return False, msg
    except Exception as e:
        return False, str(e)


@contextmanager
def pg_conn(schema="main"):
    """Yield a connection from the pool with ``search_path`` set.

    On clean exit the transaction is committed; on exception it is
    rolled back.  The connection is always returned to the pool.

    A semaphore gates access so callers block (up to 30 s) instead of
    getting an immediate ``PoolError`` when the pool is fully checked out.
    """
    if not _pool_sema.acquire(timeout=30):
        raise Exception("connection pool timeout")
    con = None
    try:
        con = _pool.getconn()
        # Health check: detect stale connections after PG restart
        try:
            con.cursor().execute("SELECT 1")
        except Exception:
            _pool.putconn(con, close=True)   # properly discard stale connection
            con = None
            con = _pool.getconn()
        cur = con.cursor()
        cur.execute("SET search_path TO %s, public", (schema,))
        cur.execute("SET statement_timeout TO '30s'")
        cur.close()
        yield con
        con.commit()
    except Exception:
        if con:
            try:
                con.rollback()
            except Exception:
                pass
        raise
    finally:
        if con:
            try:
                _pool.putconn(con)
            except Exception:
                pass
        _pool_sema.release()


@contextmanager
def pg_cursor(schema="main"):
    """Yield a ``RealDictCursor`` with auto-commit / rollback semantics.

    Rows returned by ``cur.fetchone()`` / ``cur.fetchall()`` behave like
    dicts (keyed by column name), replacing ``sqlite3.Row``.
    """
    import psycopg2.extras
    with pg_conn(schema) as con:
        cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
        finally:
            cur.close()
