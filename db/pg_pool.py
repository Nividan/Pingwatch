"""
db/pg_pool.py — PostgreSQL connection pool and context managers.

Provides pg_conn() and pg_cursor() context managers that replace the
``sqlite3.connect()`` / ``con.close()`` pattern used throughout the
SQLite backend.
"""

import threading
import time
from contextlib import contextmanager

from core.logger import log

_pool = None   # psycopg2.pool.ThreadedConnectionPool (created by pg_init_pool)
_pool_sema = None  # threading.Semaphore — gates getconn() so callers block instead of crashing
_pool_closed = False  # set by pg_close_pool(); makes pg_conn fail fast post-shutdown

# ── Outage circuit breaker ───────────────────────────────────────────────────
# When PG goes down (restart, crash, network blip) every background worker —
# sample flush, autosave, report scheduler, etc. — independently retries on its
# own cadence and each one logs a full OperationalError traceback. The result
# is dozens of identical errors per minute until PG comes back.
#
# This breaker collapses that into one WARNING at the start of an outage and
# one INFO when PG reconnects, including how many errors were suppressed.
# Exceptions still propagate normally so callers skip their cycle and retry
# next time — only the log output is throttled, not the control flow.
_breaker_lock = threading.Lock()
_breaker_outage_start = None    # float epoch when current outage began (None = healthy)
_breaker_suppressed_count = 0   # connect failures silenced during the current outage


class PoolClosedError(Exception):
    """Raised when pg_conn() is called after pg_close_pool() has run.

    Signals to any background thread that it must exit, instead of crashing
    with the cryptic AttributeError ('NoneType' object has no attribute
    'getconn') that the previous version produced.
    """
    pass


_pool_max = 0  # effective maxconn of the live pool (read by server.py auto-scale)


def pg_init_pool(max_override: int = 0):
    """Create the connection pool using settings from db.backend config.

    `max_override` > 0 forces that pool size regardless of `pg_pool_max` in
    pingwatch.conf — used by server.py to auto-scale the pool post-load
    once the sensor count (and therefore executor size) is known.
    Explicit `pg_pool_max` in the config still wins over the auto-scale
    path (server.py checks the config before calling with override).
    """
    global _pool, _pool_sema, _pool_closed, _pool_max
    import psycopg2.pool
    from db.backend import get_config

    cfg = get_config()
    maxconn = int(max_override) if max_override and max_override > 0 else int(cfg.get("pg_pool_max", 30))
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
    _pool_closed = False
    _pool_max = maxconn
    log.info(
        "PostgreSQL pool ready: %s:%s/%s (min=%d max=%d)",
        cfg["pg_host"], cfg["pg_port"], cfg["pg_database"],
        cfg.get("pg_pool_min", 2), maxconn,
    )


def get_pool_max() -> int:
    """Return the effective maxconn of the current pool (0 if uninitialised)."""
    return _pool_max


def pg_close_pool():
    """Close all pooled connections (called at shutdown).

    Sets the ``_pool_closed`` flag so subsequent pg_conn() calls raise a clear
    PoolClosedError instead of crashing with ``'NoneType' has no attribute
    'getconn'``. Callers that didn't get a stop signal in time can catch this
    and exit cleanly.
    """
    global _pool, _pool_closed
    _pool_closed = True
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


def is_pg_in_outage() -> bool:
    """Return True if the circuit breaker is currently tracking a PG outage.

    Callers that log their own connection-error tracebacks (sample flush,
    autosave, query helpers) should gate those logs on this so the operator
    sees one WARNING at outage start instead of dozens of repeated ERRORs."""
    # CPython makes a bare load atomic; no lock needed for a hot-path read.
    return _breaker_outage_start is not None


def _breaker_note_failure(exc):
    """Record a connect-phase failure. The first failure of an outage logs a
    single WARNING with a short version of the error; later failures in the
    same outage are silently counted and reported at reconnect time."""
    global _breaker_outage_start, _breaker_suppressed_count
    with _breaker_lock:
        if _breaker_outage_start is None:
            _breaker_outage_start = time.time()
            _breaker_suppressed_count = 0
            first_line = str(exc).strip().splitlines()[0][:200] if exc else ""
            log.warning(
                "PostgreSQL unreachable — suppressing repeated connection errors: %s",
                first_line,
            )
        else:
            _breaker_suppressed_count += 1


def _breaker_note_success():
    """Record a successful connect. If we were tracking an outage, log a
    single INFO with the duration + suppressed-error count and reset state."""
    global _breaker_outage_start, _breaker_suppressed_count
    # Fast path: read without taking the lock. CPython makes this safe and
    # avoids contention on every healthy connection.
    if _breaker_outage_start is None:
        return
    with _breaker_lock:
        if _breaker_outage_start is None:
            return  # another thread already cleared the breaker
        duration_s = time.time() - _breaker_outage_start
        suppressed = _breaker_suppressed_count
        _breaker_outage_start = None
        _breaker_suppressed_count = 0
    # Format the log line outside the lock so I/O doesn't serialize work.
    if suppressed:
        log.info(
            "PostgreSQL reconnected after %.1fs outage (%d suppressed errors)",
            duration_s, suppressed,
        )
    else:
        log.info("PostgreSQL reconnected after %.1fs outage", duration_s)


@contextmanager
def pg_conn(schema="main"):
    """Yield a connection from the pool with ``search_path`` set.

    On clean exit the transaction is committed; on exception it is
    rolled back.  The connection is always returned to the pool.

    A semaphore gates access so callers block (up to 30 s) instead of
    getting an immediate ``PoolError`` when the pool is fully checked out.
    """
    # Fail fast if the pool was closed (shutdown already ran). Without this
    # check, a late background thread blocks on the semaphore and then crashes
    # with AttributeError when _pool is None.
    if _pool_closed or _pool is None:
        raise PoolClosedError("PostgreSQL pool is closed")
    try:
        import core.settings as _s
        _acq_to = max(5, min(120, int(_s.get("pg_pool_acquire_timeout_s", 30) or 30)))
        _stmt_to = max(5, min(600, int(_s.get("pg_statement_timeout_s", 30) or 30)))
    except Exception:
        _acq_to, _stmt_to = 30, 30
    if not _pool_sema.acquire(timeout=_acq_to):
        raise Exception("connection pool timeout")
    con = None
    try:
        # Re-check inside the gated section: pg_close_pool() may have run
        # between the early check and our semaphore acquire.
        if _pool_closed or _pool is None:
            raise PoolClosedError("PostgreSQL pool is closed")
        # ── Connect phase ─────────────────────────────────────────────────
        # Failures here mean PG is unreachable (refused, SSL drop, auth, etc.)
        # — route them through the breaker so log spam stays bounded. The use
        # phase below is intentionally outside this try so caller SQL errors
        # don't get classified as connection outages.
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
            cur.execute(f"SET statement_timeout TO '{_stmt_to}s'")
            cur.close()
        except PoolClosedError:
            raise  # shutdown signal, not an outage
        except Exception as e:
            _breaker_note_failure(e)
            raise
        # Reached PG successfully — close any open outage window.
        _breaker_note_success()
        # ── Use phase ─────────────────────────────────────────────────────
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
