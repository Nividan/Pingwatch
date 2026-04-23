"""
db/samples.py — Sample write buffer, flush, clean, rollup, and query helpers.

v0.8.0: Added rollup worker (5m + 1h aggregation), tiered retention,
        smart query routing, and PG partition-aware cleanup.
"""

import math
import sqlite3
import threading
import time

import core.settings as _settings
from core.config import LOGS_DB_PATH
from core.logger import log
from db.backend  import is_pg
from db.core   import _logs_enqueue

# ── Sample write buffer (batches per-probe inserts) ───────────────
_SAMPLE_BUF: list    = []
_SAMPLE_BUF_LOCK     = threading.Lock()
_SAMPLE_BUF_MAX      = 50_000   # hard cap — drop oldest if writer stalls
_sample_overflow_logged = 0.0   # rate-limit the overflow warning

# Observability: count drops so operators can see probe data loss in
# /api/stats/sample-buffer instead of grepping logs.
_sample_drops_total   = 0       # monotonic since process start
_sample_drops_window: list = []  # timestamps of recent drops (trimmed to 5 min)
# Pre-drop "heads up" warning — fires once when the buffer first crosses
# 80% of capacity, rearms when it falls back under 50%.
_SAMPLE_BUF_WARN_HI   = int(_SAMPLE_BUF_MAX * 0.8)
_SAMPLE_BUF_WARN_LO   = int(_SAMPLE_BUF_MAX * 0.5)
_sample_highwater_armed = True  # True when we're allowed to fire the next warning


def db_buffer_sample(did, sid, ok, ms, value, ts):
    """Append one probe result to the in-memory buffer (thread-safe, no I/O).

    If the buffer exceeds _SAMPLE_BUF_MAX (writer stalled), the oldest row
    is dropped to prevent unbounded memory growth. Drops bump counters read
    by ``db_sample_buffer_stats()`` so operators get a client-visible signal
    instead of only a rate-limited log line.
    """
    global _sample_overflow_logged, _sample_drops_total, _sample_highwater_armed
    with _SAMPLE_BUF_LOCK:
        buf_len = len(_SAMPLE_BUF)
        if buf_len >= _SAMPLE_BUF_MAX:
            _SAMPLE_BUF.pop(0)   # drop oldest
            _sample_drops_total += 1
            now = time.time()
            _sample_drops_window.append(now)
            if now - _sample_overflow_logged > 60:
                log.warning(
                    "Sample buffer full (%d rows) — oldest row dropped. "
                    "DB writer may be stalled. Total drops this boot: %d",
                    _SAMPLE_BUF_MAX, _sample_drops_total
                )
                _sample_overflow_logged = now
        elif buf_len >= _SAMPLE_BUF_WARN_HI and _sample_highwater_armed:
            log.warning(
                "Sample buffer at %d/%d (80%% capacity) — DB writer may be "
                "falling behind. Drops will start if it doesn't catch up.",
                buf_len, _SAMPLE_BUF_MAX
            )
            _sample_highwater_armed = False
        elif buf_len <= _SAMPLE_BUF_WARN_LO and not _sample_highwater_armed:
            # Rearm once the buffer drains below 50% so we can warn again
            # if pressure recurs later in the same boot.
            _sample_highwater_armed = True
        _SAMPLE_BUF.append((ts, did, sid, int(ok), ms, value))


def db_sample_buffer_stats() -> dict:
    """Snapshot of sample-buffer health for /api/stats/sample-buffer.

    ``total_dropped`` is monotonic since process start. ``dropped_last_5min``
    is computed from a bounded window list that's trimmed on each read —
    cheap (O(dropped-in-5-min)) because there is no background sweeper.
    """
    cutoff = time.time() - 300
    with _SAMPLE_BUF_LOCK:
        # Trim expired window entries in-place so the list stays bounded.
        while _sample_drops_window and _sample_drops_window[0] < cutoff:
            _sample_drops_window.pop(0)
        return {
            "buf_len":           len(_SAMPLE_BUF),
            "buf_cap":           _SAMPLE_BUF_MAX,
            "total_dropped":     _sample_drops_total,
            "dropped_last_5min": len(_sample_drops_window),
        }


def _do_insert_samples(rows):
    """Write a batch of sample rows (called on the writer thread)."""
    if is_pg():
        from db.pg_pool import pg_conn
        import psycopg2.extras
        try:
            with pg_conn("logs") as con:
                psycopg2.extras.execute_values(
                    con.cursor(),
                    "INSERT INTO sensor_samples (ts,did,sid,ok,ms,value) VALUES %s",
                    rows,
                    page_size=1000,
                )
        except Exception as e:
            log.error(f"DB flush samples error: {e}")
        return
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
        con.executemany(
            "INSERT INTO sensor_samples (ts,did,sid,ok,ms,value) VALUES (?,?,?,?,?,?)",
            rows,
        )
        con.commit()
    except Exception as e:
        log.error(f"DB flush samples error: {e}")
    finally:
        if con:
            con.close()


def db_flush_samples():
    """Drain the buffer and write directly (used at shutdown — no queue).

    Errors are logged inside _do_insert_samples; samples that fail at
    shutdown are intentionally discarded rather than risking duplicates
    on the next start.
    """
    with _SAMPLE_BUF_LOCK:
        if not _SAMPLE_BUF:
            return
        rows = _SAMPLE_BUF[:]
        _SAMPLE_BUF.clear()
    log.debug(f"Sample flush: {len(rows)} rows")
    _do_insert_samples(rows)


_flush_stop = threading.Event()


def _sample_flush_loop():
    """Every 5 s drain the buffer and enqueue a write (serialised with VACUUM).
    Exits promptly when _flush_stop is set so shutdown doesn't race the pool close."""
    while not _flush_stop.is_set():
        # Wait() returns True when the event fires — lets shutdown short-circuit
        # the 5s interval instead of sleeping through it.
        if _flush_stop.wait(5):
            break
        with _SAMPLE_BUF_LOCK:
            if not _SAMPLE_BUF:
                continue
            rows = _SAMPLE_BUF[:]
            _SAMPLE_BUF.clear()
        log.debug(f"Sample flush: {len(rows)} rows")
        _logs_enqueue(lambda r=rows: _do_insert_samples(r))


def stop_sample_flush() -> None:
    """Stop the periodic flush loop (called at shutdown before pg_close_pool)."""
    _flush_stop.set()


_flush_thread = threading.Thread(target=_sample_flush_loop, daemon=True, name="sample-flush")
_flush_thread.start()


# ── Rollup worker (v0.8.0) ───────────────────────────────────────

def _rollup_5m():
    """Aggregate raw sensor_samples into 5-minute buckets."""
    now = time.time()
    fence = now - 300  # only complete 5-min windows

    if is_pg():
        from db.pg_pool import pg_conn
        with pg_conn("logs") as con:
            cur = con.cursor()
            cur.execute("SELECT last_ts FROM rollup_state WHERE tier = '5m'")
            row = cur.fetchone()
            last_ts = row[0] if row else 0

            cur.execute("""
                INSERT INTO sensor_samples_5m
                    (ts, did, sid, ok_count, fail_count, avg_ms, min_ms, max_ms, avg_ms_sq, sample_count)
                SELECT FLOOR(ts / 300) * 300 AS bucket,
                       did, sid,
                       SUM(ok), COUNT(*) - SUM(ok),
                       AVG(ms), MIN(ms), MAX(ms), AVG(ms * ms), COUNT(*)
                FROM sensor_samples
                WHERE ts > %s AND ts < %s
                GROUP BY bucket, did, sid
                ON CONFLICT (did, sid, ts) DO UPDATE SET
                    ok_count     = EXCLUDED.ok_count,
                    fail_count   = EXCLUDED.fail_count,
                    avg_ms       = EXCLUDED.avg_ms,
                    min_ms       = EXCLUDED.min_ms,
                    max_ms       = EXCLUDED.max_ms,
                    avg_ms_sq    = EXCLUDED.avg_ms_sq,
                    sample_count = EXCLUDED.sample_count
            """, (last_ts, fence))

            # Advance last_ts to the latest completed bucket
            new_ts = (fence // 300) * 300
            cur.execute(
                "UPDATE rollup_state SET last_ts = %s WHERE tier = '5m'",
                (new_ts,),
            )
            cur.close()
        return

    # SQLite
    import os as _os
    if not _os.path.exists(LOGS_DB_PATH):
        return  # DB not yet initialised — skip silently
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=30)
        last_ts = con.execute(
            "SELECT last_ts FROM rollup_state WHERE tier = '5m'"
        ).fetchone()
        last_ts = last_ts[0] if last_ts else 0

        rows = con.execute("""
            SELECT CAST(ts / 300 AS INTEGER) * 300 AS bucket,
                   did, sid,
                   SUM(ok), COUNT(*) - SUM(ok),
                   AVG(ms), MIN(ms), MAX(ms), AVG(ms * ms), COUNT(*)
            FROM sensor_samples
            WHERE ts > ? AND ts < ?
            GROUP BY bucket, did, sid
        """, (last_ts, fence)).fetchall()

        for r in rows:
            con.execute("""
                INSERT INTO sensor_samples_5m
                    (ts, did, sid, ok_count, fail_count, avg_ms, min_ms, max_ms, avg_ms_sq, sample_count)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (did, sid, ts) DO UPDATE SET
                    ok_count=excluded.ok_count, fail_count=excluded.fail_count,
                    avg_ms=excluded.avg_ms, min_ms=excluded.min_ms, max_ms=excluded.max_ms,
                    avg_ms_sq=excluded.avg_ms_sq, sample_count=excluded.sample_count
            """, r)

        new_ts = (fence // 300) * 300
        con.execute(
            "UPDATE rollup_state SET last_ts = ? WHERE tier = '5m'", (new_ts,)
        )
        con.commit()
    finally:
        if con:
            con.close()


def _rollup_1h():
    """Aggregate 5-minute buckets into 1-hour buckets (weighted averages)."""
    now = time.time()
    fence = now - 3600  # only complete 1-hour windows

    if is_pg():
        from db.pg_pool import pg_conn
        with pg_conn("logs") as con:
            cur = con.cursor()
            cur.execute("SELECT last_ts FROM rollup_state WHERE tier = '1h'")
            row = cur.fetchone()
            last_ts = row[0] if row else 0

            cur.execute("""
                INSERT INTO sensor_samples_1h
                    (ts, did, sid, ok_count, fail_count, avg_ms, min_ms, max_ms, avg_ms_sq, sample_count)
                SELECT FLOOR(ts / 3600) * 3600 AS bucket,
                       did, sid,
                       SUM(ok_count), SUM(fail_count),
                       SUM(avg_ms * sample_count) / NULLIF(SUM(sample_count), 0),
                       MIN(min_ms), MAX(max_ms),
                       SUM(avg_ms_sq * sample_count) / NULLIF(SUM(sample_count), 0),
                       SUM(sample_count)
                FROM sensor_samples_5m
                WHERE ts > %s AND ts < %s
                GROUP BY bucket, did, sid
                ON CONFLICT (did, sid, ts) DO UPDATE SET
                    ok_count     = EXCLUDED.ok_count,
                    fail_count   = EXCLUDED.fail_count,
                    avg_ms       = EXCLUDED.avg_ms,
                    min_ms       = EXCLUDED.min_ms,
                    max_ms       = EXCLUDED.max_ms,
                    avg_ms_sq    = EXCLUDED.avg_ms_sq,
                    sample_count = EXCLUDED.sample_count
            """, (last_ts, fence))

            new_ts = (fence // 3600) * 3600
            cur.execute(
                "UPDATE rollup_state SET last_ts = %s WHERE tier = '1h'",
                (new_ts,),
            )
            cur.close()
        return

    # SQLite
    import os as _os
    if not _os.path.exists(LOGS_DB_PATH):
        return  # DB not yet initialised — skip silently
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=30)
        last_ts = con.execute(
            "SELECT last_ts FROM rollup_state WHERE tier = '1h'"
        ).fetchone()
        last_ts = last_ts[0] if last_ts else 0

        rows = con.execute("""
            SELECT CAST(ts / 3600 AS INTEGER) * 3600 AS bucket,
                   did, sid,
                   SUM(ok_count), SUM(fail_count),
                   SUM(avg_ms * sample_count) / MAX(1, SUM(sample_count)),
                   MIN(min_ms), MAX(max_ms),
                   SUM(avg_ms_sq * sample_count) / MAX(1, SUM(sample_count)),
                   SUM(sample_count)
            FROM sensor_samples_5m
            WHERE ts > ? AND ts < ?
            GROUP BY bucket, did, sid
        """, (last_ts, fence)).fetchall()

        for r in rows:
            con.execute("""
                INSERT INTO sensor_samples_1h
                    (ts, did, sid, ok_count, fail_count, avg_ms, min_ms, max_ms, avg_ms_sq, sample_count)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (did, sid, ts) DO UPDATE SET
                    ok_count=excluded.ok_count, fail_count=excluded.fail_count,
                    avg_ms=excluded.avg_ms, min_ms=excluded.min_ms, max_ms=excluded.max_ms,
                    avg_ms_sq=excluded.avg_ms_sq, sample_count=excluded.sample_count
            """, r)

        new_ts = (fence // 3600) * 3600
        con.execute(
            "UPDATE rollup_state SET last_ts = ? WHERE tier = '1h'", (new_ts,)
        )
        con.commit()
    finally:
        if con:
            con.close()


def db_rollup_backfill():
    """Backfill rollup tables from existing raw data if needed.

    Runs on startup.  Two cases trigger a backfill:
    1. sensor_samples_5m is empty — never rolled up yet.
    2. MIN(ts) in sensor_samples predates rollup_state.last_ts by >10 min —
       last_ts jumped ahead (e.g. partition migration interrupted) leaving
       historical data unprocessed.

    Safe to call repeatedly — upsert prevents duplicates.
    """
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor("logs") as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM sensor_samples_5m")
            empty = cur.fetchone()["cnt"] == 0
            if not empty:
                return  # already backfilled on a previous run
            cur.execute("SELECT COUNT(*) AS cnt FROM sensor_samples")
            if cur.fetchone()["cnt"] == 0:
                return  # no raw data to backfill from
        log.info("Backfilling rollup tables from existing PG data …")
    else:
        con = sqlite3.connect(LOGS_DB_PATH)
        try:
            cnt = con.execute("SELECT COUNT(*) FROM sensor_samples_5m").fetchone()[0]
            empty = cnt == 0
            if not empty:
                return  # already backfilled on a previous run
            raw_cnt = con.execute("SELECT COUNT(*) FROM sensor_samples").fetchone()[0]
        finally:
            con.close()
        if not raw_cnt:
            return  # no raw data to backfill from
        log.info("Backfilling rollup tables from existing SQLite data …")

    # Reset rollup_state to 0 so the worker processes everything
    if is_pg():
        from db.pg_pool import pg_conn
        with pg_conn("logs") as pgc:
            pgc.cursor().execute("UPDATE rollup_state SET last_ts = 0")
    else:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
        try:
            con.execute("UPDATE rollup_state SET last_ts = 0")
            con.commit()
        finally:
            con.close()

    # Run 5m rollup (processes all pending data)
    try:
        _rollup_5m()
        _rollup_1h()
        log.info("Rollup backfill complete")
    except Exception as e:
        log.error(f"Rollup backfill error: {e}")


_rollup_stop = threading.Event()


def _rollup_loop():
    """Background daemon: aggregate raw → 5min every 5 min, 5min → 1h every ~30 min.

    Exits promptly when ``_rollup_stop`` is set so shutdown doesn't race
    ``pg_close_pool()``.  Both sleeps below are interruptible — without the
    Event-based wait, a stop signal mid-cycle would still take up to 5 min
    to take effect, and any DB call after the pool closed would crash with
    ``'NoneType' object has no attribute 'getconn'``.
    """
    if _rollup_stop.wait(30):  # startup delay (interruptible)
        return
    _iter = 0
    while not _rollup_stop.is_set():
        try:
            _rollup_5m()
        except Exception as e:
            _emsg = str(e)
            # Quiet known-benign cases: empty schema (startup race) and pool
            # closed (shutdown race — stop signal was set but this iteration
            # was already in flight).
            if ("no such table" not in _emsg
                    and "does not exist" not in _emsg
                    and "pool is closed" not in _emsg):
                log.error(f"Rollup 5m error: {e}")
        _iter += 1
        if _iter % 6 == 0:  # every ~30 min
            try:
                _rollup_1h()
            except Exception as e:
                _emsg = str(e)
                if ("no such table" not in _emsg
                        and "does not exist" not in _emsg
                        and "pool is closed" not in _emsg):
                    log.error(f"Rollup 1h error: {e}")
        if _rollup_stop.wait(300):
            return


def stop_rollup_worker() -> None:
    """Stop the rollup background loop (call at shutdown before pg_close_pool)."""
    _rollup_stop.set()


threading.Thread(target=_rollup_loop, daemon=True, name="rollup-worker").start()


# ── Tiered cleanup (v0.8.0) ──────────────────────────────────────

def db_clean_samples(retention_days=None):
    """Tiered cleanup: raw samples, 5m rollups, 1h rollups.

    retention_days is legacy compat — used as raw retention if tiered
    settings are not yet configured.
    """
    raw_days = int(_settings.get("retention_raw_days", 0) or 0)
    if raw_days < 1:
        raw_days = int(retention_days or _settings.get("retention_days", 7) or 7)
    days_5m = int(_settings.get("retention_5m_days", 90) or 90)
    days_1h = int(_settings.get("retention_1h_days", 1095) or 1095)

    cutoff_raw = time.time() - raw_days * 86400
    cutoff_5m  = time.time() - days_5m * 86400
    cutoff_1h  = time.time() - days_1h * 86400

    if is_pg():
        _clean_pg(cutoff_raw, cutoff_5m, cutoff_1h)
    else:
        _clean_sqlite(cutoff_raw, cutoff_5m, cutoff_1h)


def _clean_pg(cutoff_raw, cutoff_5m, cutoff_1h):
    """PG cleanup — drops whole partitions when possible, else DELETE."""
    from db.pg_pool import pg_conn
    from psycopg2 import sql as _pgsql
    try:
        with pg_conn("logs") as con:
            cur = con.cursor()

            # ── Raw samples: try to drop entire expired partitions ─────
            try:
                cur.execute("""
                    SELECT c.oid, c.relname,
                           pg_get_expr(c.relpartbound, c.oid) AS bound_expr
                    FROM pg_inherits i
                    JOIN pg_class c ON c.oid = i.inhrelid
                    WHERE i.inhparent = (
                        SELECT c2.oid FROM pg_class c2
                        JOIN pg_namespace n ON n.oid = c2.relnamespace
                        WHERE c2.relname = 'sensor_samples' AND n.nspname = 'logs'
                    )
                """)
                for part in cur.fetchall():
                    # part = (oid, relname, bound_expr)
                    # Parse "FOR VALUES FROM ('X') TO ('Y')" to extract upper bound
                    expr = part[2] or ""
                    # Format: FOR VALUES FROM ('1234.0') TO ('5678.0')
                    try:
                        parts = expr.replace("'", "").split("TO")
                        if len(parts) == 2:
                            upper = float(parts[1].strip().strip("() "))
                            if upper <= cutoff_raw:
                                cur.execute(_pgsql.SQL(
                                    "DROP TABLE IF EXISTS {}"
                                ).format(_pgsql.Identifier(part[1])))
                                log.info(f"Dropped expired partition {part[1]}")
                                continue
                    except (ValueError, IndexError):
                        pass
            except Exception as e:
                log.warning(f"Partition cleanup: {e}")

            # Fallback DELETE for non-partitioned or partial partitions
            cur.execute(
                "DELETE FROM sensor_samples WHERE ts < %s", (cutoff_raw,)
            )

            # ── Rollup tables ──────────────────────────────────────────
            cur.execute(
                "DELETE FROM sensor_samples_5m WHERE ts < %s", (cutoff_5m,)
            )
            cur.execute(
                "DELETE FROM sensor_samples_1h WHERE ts < %s", (cutoff_1h,)
            )
            cur.close()
        log.debug("PG sample cleanup complete")
    except Exception as e:
        log.error(f"DB clean samples error: {e}")


def _clean_sqlite(cutoff_raw, cutoff_5m, cutoff_1h):
    """SQLite cleanup with VACUUM in background thread."""
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=30)
        con.execute("DELETE FROM sensor_samples WHERE ts < ?", (cutoff_raw,))
        con.execute("DELETE FROM sensor_samples_5m WHERE ts < ?", (cutoff_5m,))
        con.execute("DELETE FROM sensor_samples_1h WHERE ts < ?", (cutoff_1h,))
        con.commit()
        log.debug("DB sample cleanup complete")
    except Exception as e:
        log.error(f"DB clean samples error: {e}")
        if "malformed" in str(e).lower():
            log.error(
                "DB CORRUPTION DETECTED — database disk image is malformed. "
                "Stop PingWatch, run: sqlite3 pingwatch_logs.db 'PRAGMA integrity_check' "
                "to assess damage, or delete pingwatch_logs.db to start fresh."
            )
    finally:
        if con:
            con.close()
    # VACUUM in a separate thread so sample writes are not blocked
    def _vacuum_bg():
        try:
            _vcon = sqlite3.connect(LOGS_DB_PATH, timeout=60)
            _vcon.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            _vcon.execute("VACUUM")
            _vcon.close()
            log.debug("DB vacuum complete (background)")
        except Exception as _ve:
            log.warning(f"Background vacuum failed (non-fatal): {_ve}")
    threading.Thread(target=_vacuum_bg, daemon=True, name="db-vacuum").start()


# ── Smart query routing (v0.8.0) ─────────────────────────────────

def _pick_table(minutes):
    """Return (table_name, bucket_seconds) based on requested time range.

    <=1 day   → raw sensor_samples  (no bucketing)
    1-90 days → sensor_samples_5m   (300s buckets)
    >90 days  → sensor_samples_1h   (3600s buckets)
    """
    if minutes <= 1440:
        return "sensor_samples", None
    if minutes <= 129600:
        return "sensor_samples_5m", 300
    return "sensor_samples_1h", 3600


# ── Query helpers ─────────────────────────────────────────────────

def db_load_availability(minutes: int = 1440, *, start_ts=None, end_ts=None):
    """Return per-hour aggregate availability across ALL sensors.

    Default: last ``minutes`` minutes relative to now (used by live dashboards).

    Absolute window: pass ``start_ts`` / ``end_ts`` (epoch seconds) to query a
    fixed historical period. The report engine uses this so the availability
    chart matches the report's declared reporting period — without it, the
    chart rendered "last N minutes relative to report-generation time", which
    leaked data past the period's end.
    """
    if start_ts is None:
        start_ts = time.time() - minutes * 60
    else:
        # Re-derive minutes from the explicit window so _pick_table still
        # chooses the right rollup resolution (raw / 5m / 1h).
        minutes = max(1, int(((end_ts or time.time()) - start_ts) // 60))
    table, bucket = _pick_table(minutes)

    if table == "sensor_samples":
        return _avail_from_raw(start_ts, end_ts)
    return _avail_from_rollup(start_ts, end_ts, table)


def _avail_from_raw(start_ts, end_ts):
    where, params_pg, params_sq = _ts_bounds(start_ts, end_ts, pg=True), None, None
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    f"SELECT FLOOR(ts/3600)*3600 AS h, SUM(ok) AS sum_ok, COUNT(*) AS cnt "
                    f"FROM sensor_samples WHERE {where[0]} GROUP BY h ORDER BY h ASC",
                    where[1]
                )
                rows = cur.fetchall()
            return [{"ts": r["h"], "pct": round(r["sum_ok"] / r["cnt"] * 100, 1) if r["cnt"] else 0,
                     "up": int(r["sum_ok"] or 0), "total": int(r["cnt"] or 0)} for r in rows]
        except Exception as e:
            log.error(f"DB load availability error: {e}")
            return []
    # SQLite
    sq = _ts_bounds(start_ts, end_ts, pg=False)
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH)
        rows = con.execute(
            f"SELECT CAST(ts/3600 AS INTEGER)*3600 AS h, SUM(ok), COUNT(*) "
            f"FROM sensor_samples WHERE {sq[0]} GROUP BY h ORDER BY h ASC",
            sq[1]
        ).fetchall()
        return [{"ts": r[0], "pct": round(r[1] / r[2] * 100, 1) if r[2] else 0,
                 "up": int(r[1] or 0), "total": int(r[2] or 0)} for r in rows]
    except Exception as e:
        log.error(f"DB load availability error: {e}")
        return []
    finally:
        if con: con.close()


def _avail_from_rollup(start_ts, end_ts, table):
    if is_pg():
        from db.pg_pool import pg_cursor
        pg = _ts_bounds(start_ts, end_ts, pg=True)
        try:
            with pg_cursor("logs") as cur:
                if table == "sensor_samples_1h":
                    cur.execute(
                        f"SELECT ts AS h, SUM(ok_count) AS sum_ok, "
                        f"SUM(ok_count + fail_count) AS cnt "
                        f"FROM {table} WHERE {pg[0]} GROUP BY h ORDER BY h ASC",
                        pg[1]
                    )
                else:
                    cur.execute(
                        f"SELECT FLOOR(ts/3600)*3600 AS h, SUM(ok_count) AS sum_ok, "
                        f"SUM(ok_count + fail_count) AS cnt "
                        f"FROM {table} WHERE {pg[0]} GROUP BY h ORDER BY h ASC",
                        pg[1]
                    )
                rows = cur.fetchall()
            return [{"ts": r["h"], "pct": round(r["sum_ok"] / r["cnt"] * 100, 1) if r["cnt"] else 0,
                     "up": int(r["sum_ok"] or 0), "total": int(r["cnt"] or 0)} for r in rows]
        except Exception as e:
            log.error(f"DB load availability (rollup) error: {e}")
            return []
    # SQLite
    sq = _ts_bounds(start_ts, end_ts, pg=False)
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH)
        if table == "sensor_samples_1h":
            rows = con.execute(
                f"SELECT ts AS h, SUM(ok_count), SUM(ok_count + fail_count) "
                f"FROM {table} WHERE {sq[0]} GROUP BY h ORDER BY h ASC",
                sq[1]
            ).fetchall()
        else:
            rows = con.execute(
                f"SELECT CAST(ts/3600 AS INTEGER)*3600 AS h, SUM(ok_count), "
                f"SUM(ok_count + fail_count) "
                f"FROM {table} WHERE {sq[0]} GROUP BY h ORDER BY h ASC",
                sq[1]
            ).fetchall()
        return [{"ts": r[0], "pct": round(r[1] / r[2] * 100, 1) if r[2] else 0,
                 "up": int(r[1] or 0), "total": int(r[2] or 0)} for r in rows]
    except Exception as e:
        log.error(f"DB load availability (rollup) error: {e}")
        return []
    finally:
        if con: con.close()


def _ts_bounds(start_ts, end_ts, *, pg: bool):
    """Build the (WHERE-fragment, params-tuple) pair for a ts window.

    The fragment always binds the lower bound; the upper bound is added only
    when ``end_ts`` is supplied so live "last N minutes" callers retain their
    original shape (no upper bound → open-ended to now).
    """
    ph = "%s" if pg else "?"
    if end_ts is None:
        return (f"ts>={ph}", (start_ts,))
    return (f"ts>={ph} AND ts<{ph}", (start_ts, end_ts))


def db_load_history(did, sid, minutes=1440, limit=1000):
    """Return up to `limit` evenly-distributed samples from the last `minutes` minutes, oldest first."""
    cutoff = time.time() - minutes * 60
    table, bucket = _pick_table(minutes)

    if table == "sensor_samples":
        return _history_from_raw(did, sid, cutoff, limit)
    return _history_from_rollup(did, sid, cutoff, limit, table)


def _history_from_raw(did, sid, cutoff, limit):
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM sensor_samples WHERE did=%s AND sid=%s AND ts>=%s",
                    (did, sid, cutoff)
                )
                total = cur.fetchone()["cnt"]
                stride = total // limit
                if stride < 2:
                    cur.execute(
                        "SELECT ts,ok,ms,value FROM sensor_samples "
                        "WHERE did=%s AND sid=%s AND ts>=%s ORDER BY ts ASC",
                        (did, sid, cutoff)
                    )
                else:
                    cur.execute(
                        "SELECT ts,ok,ms,value FROM ("
                        "  SELECT ts,ok,ms,value,ROW_NUMBER() OVER (ORDER BY ts) rn "
                        "  FROM sensor_samples WHERE did=%s AND sid=%s AND ts>=%s"
                        ") sub WHERE rn %% %s = 1 ORDER BY ts ASC LIMIT %s",
                        (did, sid, cutoff, stride, limit)
                    )
                rows = cur.fetchall()
            return [{"ts": r["ts"], "ok": bool(r["ok"]), "ms": r["ms"], "value": r["value"]}
                    for r in rows]
        except Exception as e:
            log.error(f"DB load history error: {e}")
            return []
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH)
        total = con.execute(
            "SELECT COUNT(*) FROM sensor_samples WHERE did=? AND sid=? AND ts>=?",
            (did, sid, cutoff)
        ).fetchone()[0]
        stride = total // limit
        if stride < 2:
            rows = con.execute(
                "SELECT ts,ok,ms,value FROM sensor_samples "
                "WHERE did=? AND sid=? AND ts>=? ORDER BY ts ASC",
                (did, sid, cutoff)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT ts,ok,ms,value FROM ("
                "  SELECT ts,ok,ms,value,ROW_NUMBER() OVER (ORDER BY ts) rn "
                "  FROM sensor_samples WHERE did=? AND sid=? AND ts>=?"
                ") WHERE rn % ? = 1 ORDER BY ts ASC LIMIT ?",
                (did, sid, cutoff, stride, limit)
            ).fetchall()
        return [{"ts": r[0], "ok": bool(r[1]), "ms": r[2], "value": r[3]}
                for r in rows]
    except Exception as e:
        log.error(f"DB load history error: {e}")
        return []
    finally:
        if con: con.close()


def _history_from_rollup(did, sid, cutoff, limit, table):
    """Load history from a rollup table, mapping to the same JSON format."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    f"SELECT COUNT(*) AS cnt FROM {table} "
                    f"WHERE did=%s AND sid=%s AND ts>=%s",
                    (did, sid, cutoff)
                )
                total = cur.fetchone()["cnt"]
                stride = total // limit
                if stride < 2:
                    cur.execute(
                        f"SELECT ts, ok_count, fail_count, avg_ms "
                        f"FROM {table} WHERE did=%s AND sid=%s AND ts>=%s "
                        f"ORDER BY ts ASC",
                        (did, sid, cutoff)
                    )
                else:
                    cur.execute(
                        f"SELECT ts, ok_count, fail_count, avg_ms FROM ("
                        f"  SELECT ts, ok_count, fail_count, avg_ms, "
                        f"    ROW_NUMBER() OVER (ORDER BY ts) rn "
                        f"  FROM {table} WHERE did=%s AND sid=%s AND ts>=%s"
                        f") sub WHERE rn %% %s = 1 ORDER BY ts ASC LIMIT %s",
                        (did, sid, cutoff, stride, limit)
                    )
                rows = cur.fetchall()
            return [{"ts": r["ts"], "ok": r["ok_count"] > r["fail_count"],
                     "ms": r["avg_ms"], "value": None} for r in rows]
        except Exception as e:
            log.error(f"DB load history (rollup) error: {e}")
            return []
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH)
        total = con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE did=? AND sid=? AND ts>=?",
            (did, sid, cutoff)
        ).fetchone()[0]
        stride = total // limit
        if stride < 2:
            rows = con.execute(
                f"SELECT ts, ok_count, fail_count, avg_ms FROM {table} "
                f"WHERE did=? AND sid=? AND ts>=? ORDER BY ts ASC",
                (did, sid, cutoff)
            ).fetchall()
        else:
            rows = con.execute(
                f"SELECT ts, ok_count, fail_count, avg_ms FROM ("
                f"  SELECT ts, ok_count, fail_count, avg_ms, "
                f"    ROW_NUMBER() OVER (ORDER BY ts) rn "
                f"  FROM {table} WHERE did=? AND sid=? AND ts>=?"
                f") WHERE rn % ? = 1 ORDER BY ts ASC LIMIT ?",
                (did, sid, cutoff, stride, limit)
            ).fetchall()
        return [{"ts": r[0], "ok": r[1] > r[2], "ms": r[3], "value": None}
                for r in rows]
    except Exception as e:
        log.error(f"DB load history (rollup) error: {e}")
        return []
    finally:
        if con: con.close()


def db_load_summary(did, sid, minutes=1440):
    """Return per-hour aggregation over the last `minutes` minutes."""
    cutoff = time.time() - minutes * 60
    table, bucket = _pick_table(minutes)

    if table == "sensor_samples":
        return _summary_from_raw(did, sid, cutoff)
    if table == "sensor_samples_1h":
        return _summary_from_1h(did, sid, cutoff)
    return _summary_from_5m(did, sid, cutoff)


def _format_summary_row(ok, fail, avg_ms, min_ms, max_ms, avg_ms_sq, ts):
    """Build a summary dict from aggregate values."""
    avg_ms_v = avg_ms or 0.0
    avg_ms_sq_v = avg_ms_sq or 0.0
    jitter_ms = round(math.sqrt(max(0.0, avg_ms_sq_v - avg_ms_v ** 2)), 1)
    total = ok + fail
    loss_pct = round(fail / total * 100, 1) if total > 0 else 0.0
    return {
        "ts":        ts,
        "ok":        ok,
        "fail":      fail,
        "avg_ms":    round(avg_ms, 1) if avg_ms is not None else None,
        "min_ms":    round(min_ms, 1) if min_ms is not None else None,
        "max_ms":    round(max_ms, 1) if max_ms is not None else None,
        "jitter_ms": jitter_ms,
        "loss_pct":  loss_pct,
    }


def _summary_from_raw(did, sid, cutoff):
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute("""
                    SELECT FLOOR(ts/3600)*3600 AS hour_ts,
                           SUM(ok) AS sum_ok, COUNT(*)-SUM(ok) AS fail_cnt,
                           AVG(ms) AS avg_ms, MIN(ms) AS min_ms,
                           MAX(ms) AS max_ms, AVG(ms*ms) AS avg_ms_sq
                    FROM sensor_samples
                    WHERE did=%s AND sid=%s AND ts>=%s
                    GROUP BY hour_ts ORDER BY hour_ts ASC
                """, (did, sid, cutoff))
                rows = cur.fetchall()
            return [_format_summary_row(
                int(r["sum_ok"] or 0), int(r["fail_cnt"] or 0),
                r["avg_ms"], r["min_ms"], r["max_ms"], r["avg_ms_sq"], r["hour_ts"]
            ) for r in rows]
        except Exception as e:
            log.error(f"DB load summary error: {e}")
            return []
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH)
        rows = con.execute("""
            SELECT CAST(ts/3600 AS INTEGER)*3600 AS hour_ts,
                   SUM(ok), COUNT(*)-SUM(ok),
                   AVG(ms), MIN(ms), MAX(ms), AVG(ms*ms)
            FROM sensor_samples
            WHERE did=? AND sid=? AND ts>=?
            GROUP BY hour_ts ORDER BY hour_ts ASC
        """, (did, sid, cutoff)).fetchall()
        return [_format_summary_row(
            int(r[1] or 0), int(r[2] or 0), r[3], r[4], r[5], r[6], r[0]
        ) for r in rows]
    except Exception as e:
        log.error(f"DB load summary error: {e}")
        return []
    finally:
        if con: con.close()


def _summary_from_1h(did, sid, cutoff):
    """Summary from 1-hour rollup — each row IS an hourly bucket."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "SELECT ts, ok_count, fail_count, avg_ms, min_ms, max_ms, avg_ms_sq "
                    "FROM sensor_samples_1h WHERE did=%s AND sid=%s AND ts>=%s "
                    "ORDER BY ts ASC",
                    (did, sid, cutoff)
                )
                rows = cur.fetchall()
            return [_format_summary_row(
                int(r["ok_count"]), int(r["fail_count"]),
                r["avg_ms"], r["min_ms"], r["max_ms"], r["avg_ms_sq"], r["ts"]
            ) for r in rows]
        except Exception as e:
            log.error(f"DB load summary (1h) error: {e}")
            return []
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH)
        rows = con.execute(
            "SELECT ts, ok_count, fail_count, avg_ms, min_ms, max_ms, avg_ms_sq "
            "FROM sensor_samples_1h WHERE did=? AND sid=? AND ts>=? "
            "ORDER BY ts ASC",
            (did, sid, cutoff)
        ).fetchall()
        return [_format_summary_row(
            int(r[1]), int(r[2]), r[3], r[4], r[5], r[6], r[0]
        ) for r in rows]
    except Exception as e:
        log.error(f"DB load summary (1h) error: {e}")
        return []
    finally:
        if con: con.close()


def _summary_from_5m(did, sid, cutoff):
    """Summary from 5-min rollup — re-aggregate into hourly buckets."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute("""
                    SELECT FLOOR(ts/3600)*3600 AS hour_ts,
                           SUM(ok_count) AS ok, SUM(fail_count) AS fail,
                           SUM(avg_ms * sample_count) / NULLIF(SUM(sample_count), 0) AS avg_ms,
                           MIN(min_ms) AS min_ms, MAX(max_ms) AS max_ms,
                           SUM(avg_ms_sq * sample_count) / NULLIF(SUM(sample_count), 0) AS avg_ms_sq
                    FROM sensor_samples_5m
                    WHERE did=%s AND sid=%s AND ts>=%s
                    GROUP BY hour_ts ORDER BY hour_ts ASC
                """, (did, sid, cutoff))
                rows = cur.fetchall()
            return [_format_summary_row(
                int(r["ok"] or 0), int(r["fail"] or 0),
                r["avg_ms"], r["min_ms"], r["max_ms"], r["avg_ms_sq"], r["hour_ts"]
            ) for r in rows]
        except Exception as e:
            log.error(f"DB load summary (5m) error: {e}")
            return []
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH)
        rows = con.execute("""
            SELECT CAST(ts/3600 AS INTEGER)*3600 AS hour_ts,
                   SUM(ok_count), SUM(fail_count),
                   SUM(avg_ms * sample_count) / MAX(1, SUM(sample_count)),
                   MIN(min_ms), MAX(max_ms),
                   SUM(avg_ms_sq * sample_count) / MAX(1, SUM(sample_count))
            FROM sensor_samples_5m
            WHERE did=? AND sid=? AND ts>=?
            GROUP BY hour_ts ORDER BY hour_ts ASC
        """, (did, sid, cutoff)).fetchall()
        return [_format_summary_row(
            int(r[1] or 0), int(r[2] or 0), r[3], r[4], r[5], r[6], r[0]
        ) for r in rows]
    except Exception as e:
        log.error(f"DB load summary (5m) error: {e}")
        return []
    finally:
        if con: con.close()
