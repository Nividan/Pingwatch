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
from db.helpers import db_query, db_query_one, db_cursor

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

# Flush observability — surfaces DB contention in Diagnostics.
_last_flush_ms    = 0         # wall-clock ms of most recent flush
_last_flush_rows  = 0         # row count of most recent flush
_last_flush_ts    = 0.0       # monotonic ts of most recent flush completion

# Startup grace period — suppresses the "Sample flush slow" WARN during the
# first N seconds after process start.  Cold start briefly pushes flush
# durations above 2s because of concurrent PG pool warmup, sensor history
# restoration, alert-engine cold-cache rebuild, and first-VACUUM-after-migration.
# None of those indicate chronic contention; the WARN only has signal in
# steady state.  Fires at INFO level during grace so operators can still
# see it if they're looking, but the noise doesn't trigger WARN-based
# alerting / log monitoring.
_STARTUP_GRACE_S = 60
_startup_ts = time.monotonic()

# Retention trim batching — bounds DELETE runtime so the flush loop
# can't be blocked for seconds by a mass cleanup on a large rollup table.
_TRIM_BATCH   = 10_000
_TRIM_YIELD_S = 0.05

# SQLite VACUUM gating — see _clean_sqlite. In-memory timestamp: a restart
# re-arms it, which is fine (at most one extra vacuum per boot).
_VACUUM_MIN_INTERVAL_S   = 7 * 86400
_VACUUM_MIN_TRIMMED_ROWS = 10_000
_last_vacuum_ts          = 0.0


def db_buffer_sample(did, sid, ok, ms, value, ts, rate=None):
    """Append one probe result to the in-memory buffer (thread-safe, no I/O).

    If the buffer exceeds _SAMPLE_BUF_MAX (writer stalled), the oldest row
    is dropped to prevent unbounded memory growth. Drops bump counters read
    by ``db_sample_buffer_stats()`` so operators get a client-visible signal
    instead of only a rate-limited log line.

    ``rate`` (v0.9.7): per-probe rate in native units (bytes/s, events/s) for
    counter-type SNMP sensors. ``None`` for non-counter sensors and the first
    probe after restart (no prior sample to diff).
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
        _SAMPLE_BUF.append((ts, did, sid, int(ok), ms, value, rate))


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
            "last_flush_ms":     _last_flush_ms,
            "last_flush_rows":   _last_flush_rows,
        }


def _requeue_failed_rows(rows):
    """Put a failed flush batch back at the head of the buffer so the next
    flush retries it.

    A transient 'database is locked' / PG restart used to discard the whole
    5 s batch even though the buffer can absorb hours of outage. The buffer
    cap still bounds memory: if requeueing would overflow, the oldest rows
    are dropped and counted (which also self-heals a poison batch — it ages
    out instead of blocking forever)."""
    global _sample_drops_total
    if not rows:
        return
    with _SAMPLE_BUF_LOCK:
        space = _SAMPLE_BUF_MAX - len(_SAMPLE_BUF)
        if space <= 0:
            keep, dropped = [], len(rows)
        elif len(rows) > space:
            keep, dropped = rows[-space:], len(rows) - space   # keep newest
        else:
            keep, dropped = rows, 0
        if keep:
            _SAMPLE_BUF[:0] = keep        # re-prepend, chronological order kept
        if dropped:
            _sample_drops_total += dropped
            now = time.time()
            _sample_drops_window.extend([now] * min(dropped, 5000))


def _do_insert_samples(rows):
    """Write a batch of sample rows (called on the writer thread).
    On failure the batch is requeued into the buffer for the next flush."""
    global _last_flush_ms, _last_flush_rows, _last_flush_ts
    t0 = time.monotonic()
    try:
        if is_pg():
            from db.pg_pool import pg_conn, is_pg_in_outage
            import psycopg2.extras
            try:
                with pg_conn("logs") as con:
                    psycopg2.extras.execute_values(
                        con.cursor(),
                        "INSERT INTO sensor_samples (ts,did,sid,ok,ms,value,rate) VALUES %s",
                        rows,
                        page_size=1000,
                    )
            except Exception as e:
                # During a known PG outage the breaker has already logged the
                # WARNING; let it count this attempt silently. Otherwise log
                # the actual error so non-outage failures still surface.
                if is_pg_in_outage():
                    log.debug(f"DB flush samples failed, requeued (PG outage): {e}")
                else:
                    log.error(f"DB flush samples error ({len(rows)} rows requeued): {e}")
                _requeue_failed_rows(rows)
            return
        # SQLite
        con = None
        try:
            con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
            con.executemany(
                "INSERT INTO sensor_samples (ts,did,sid,ok,ms,value,rate) VALUES (?,?,?,?,?,?,?)",
                rows,
            )
            con.commit()
        except Exception as e:
            log.error(f"DB flush samples error ({len(rows)} rows requeued): {e}")
            _requeue_failed_rows(rows)
        finally:
            if con:
                con.close()
    finally:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _last_flush_ms   = elapsed_ms
        _last_flush_rows = len(rows)
        _last_flush_ts   = time.monotonic()
        if elapsed_ms > 2000:
            _in_grace = (time.monotonic() - _startup_ts) < _STARTUP_GRACE_S
            if _in_grace:
                log.info(
                    f"Sample flush slow: {elapsed_ms} ms for {len(rows)} rows "
                    f"(expected during first {_STARTUP_GRACE_S}s of startup — "
                    f"PG pool warmup + history restore + alert cache rebuild)"
                )
            else:
                log.warning(
                    f"Sample flush slow: {elapsed_ms} ms for {len(rows)} rows "
                    f"(>2s indicates DB contention — check retention trim, vacuum, or pool saturation)"
                )


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

def _try_float(v):
    """Coerce a sensor_samples.value (TEXT) to float, or None if non-numeric.
    Used by the SQLite rollup where we can't rely on a server-side regex."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bucket_raw_rows(raw_rows, bucket_s):
    """Group raw (ts, did, sid, ok, ms, value, rate) rows into (bucket, did, sid)
    buckets; compute per-bucket aggregates for ms, value (v0.9.6), and rate
    (v0.9.7). Non-numeric `value` rows are skipped in value aggregates; `rate`
    is already typed (nullable float) so NULLs are skipped directly. Returns a
    dict keyed by (bucket_ts, did, sid) → aggregate dict."""
    from collections import defaultdict
    out = defaultdict(lambda: {
        'ok': 0, 'fail': 0, 'count': 0,
        'ms_sum': 0.0, 'ms_sq_sum': 0.0, 'ms_count': 0,
        'ms_min': None, 'ms_max': None,
        'vals': [],   # (ts, float) tuples, numeric only
        'rate_sum': 0.0, 'rate_count': 0,
        'rate_min': None, 'rate_max': None,
    })
    for row in raw_rows:
        # v0.9.7: rows carry 7 cols (rate); older callers may still pass 6.
        if len(row) == 7:
            ts, did, sid, ok, ms, value, rate = row
        else:
            ts, did, sid, ok, ms, value = row
            rate = None
        bucket = int(ts // bucket_s) * bucket_s
        b = out[(bucket, did, sid)]
        b['count'] += 1
        if ok:
            b['ok'] += 1
        else:
            b['fail'] += 1
        if ms is not None:
            b['ms_sum']    += ms
            b['ms_sq_sum'] += ms * ms
            b['ms_count']  += 1
            if b['ms_min'] is None or ms < b['ms_min']:
                b['ms_min'] = ms
            if b['ms_max'] is None or ms > b['ms_max']:
                b['ms_max'] = ms
        v = _try_float(value)
        if v is not None:
            b['vals'].append((ts, v))
        if rate is not None:
            b['rate_sum']   += rate
            b['rate_count'] += 1
            if b['rate_min'] is None or rate < b['rate_min']:
                b['rate_min'] = rate
            if b['rate_max'] is None or rate > b['rate_max']:
                b['rate_max'] = rate
    # Finalise aggregates
    for b in out.values():
        c = b['ms_count']
        b['avg_ms']    = (b['ms_sum'] / c) if c else None
        b['avg_ms_sq'] = (b['ms_sq_sum'] / c) if c else None
        vals = b['vals']
        if vals:
            vals.sort(key=lambda x: x[0])
            nums = [v for _, v in vals]
            b['avg_value']   = sum(nums) / len(nums)
            b['min_value']   = min(nums)
            b['max_value']   = max(nums)
            b['first_value'] = vals[0][1]
            b['last_value']  = vals[-1][1]
        else:
            b['avg_value'] = b['min_value'] = b['max_value'] = None
            b['first_value'] = b['last_value'] = None
        rc = b['rate_count']
        b['avg_rate'] = (b['rate_sum'] / rc) if rc else None
        # rate_min / rate_max already set during iteration
    return out


_ROLLUP_5M_CHUNK_S = 3 * 3600   # per-call cap (PG statement_timeout = 30s)
_ROLLUP_1H_CHUNK_S = 3 * 86400  # per-call cap on 1h tier (source = 5m rows)


def _rollup_5m():
    """Aggregate raw sensor_samples into 5-minute buckets.

    Returns True if more work remains (caller can loop for backfill), False
    once caught up to fence. Caps per-call work to a 3-hour window so a
    large backlog (e.g. v0.9.6 value-aggregate migration) doesn't exceed PG's
    statement_timeout. Jumps last_ts forward across empty gaps via an
    index-efficient MIN(ts) lookup.
    """
    now = time.time()
    fence = now - 300  # only complete 5-min windows

    if is_pg():
        from db.pg_pool import pg_conn
        with pg_conn("logs") as con:
            cur = con.cursor()
            cur.execute("SELECT last_ts FROM rollup_state WHERE tier = '5m'")
            row = cur.fetchone()
            last_ts = row[0] if row else 0

            # Jump forward to earliest raw row beyond last_ts. Uses the
            # (did, sid, ts) composite index via GROUP BY → O(sensors) index
            # seeks, not a seq scan. Without this, backfills with last_ts=0
            # would iterate empty epoch windows for decades.
            cur.execute("""
                SELECT MIN(min_ts) FROM (
                    SELECT MIN(ts) AS min_ts FROM sensor_samples
                    WHERE ts > %s GROUP BY did, sid
                ) t
            """, (last_ts,))
            r = cur.fetchone()
            min_next = r[0] if r else None

            if min_next is None or min_next >= fence:
                new_ts = int(fence // 300) * 300
                cur.execute(
                    "UPDATE rollup_state SET last_ts = %s WHERE tier = '5m'",
                    (new_ts,),
                )
                cur.close()
                return False

            window_start = max(last_ts, int(min_next) - 1)
            upper = min(fence, window_start + _ROLLUP_5M_CHUNK_S)

            # v0.9.6: {avg,min,max,first,last}_value aggregate sensor_samples.value
            # (TEXT — some sensors write non-numeric strings). CASE+FILTER guards
            # cast; ARRAY_AGG picks bucket endpoints for counter-rate derivation.
            cur.execute(r"""
                INSERT INTO sensor_samples_5m
                    (ts, did, sid, ok_count, fail_count,
                     avg_ms, min_ms, max_ms, avg_ms_sq, sample_count,
                     avg_value, min_value, max_value, first_value, last_value,
                     avg_rate, min_rate, max_rate)
                SELECT FLOOR(ts / 300) * 300 AS bucket,
                       did, sid,
                       SUM(ok), COUNT(*) - SUM(ok),
                       AVG(ms), MIN(ms), MAX(ms), AVG(ms * ms), COUNT(*),
                       AVG(CASE WHEN value ~ '^-?[0-9]+(\.[0-9]+)?$'
                                THEN value::DOUBLE PRECISION END),
                       MIN(CASE WHEN value ~ '^-?[0-9]+(\.[0-9]+)?$'
                                THEN value::DOUBLE PRECISION END),
                       MAX(CASE WHEN value ~ '^-?[0-9]+(\.[0-9]+)?$'
                                THEN value::DOUBLE PRECISION END),
                       (ARRAY_AGG(value::DOUBLE PRECISION ORDER BY ts ASC)
                           FILTER (WHERE value ~ '^-?[0-9]+(\.[0-9]+)?$'))[1],
                       (ARRAY_AGG(value::DOUBLE PRECISION ORDER BY ts DESC)
                           FILTER (WHERE value ~ '^-?[0-9]+(\.[0-9]+)?$'))[1],
                       AVG(rate), MIN(rate), MAX(rate)
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
                    sample_count = EXCLUDED.sample_count,
                    avg_value    = EXCLUDED.avg_value,
                    min_value    = EXCLUDED.min_value,
                    max_value    = EXCLUDED.max_value,
                    first_value  = EXCLUDED.first_value,
                    last_value   = EXCLUDED.last_value,
                    avg_rate     = EXCLUDED.avg_rate,
                    min_rate     = EXCLUDED.min_rate,
                    max_rate     = EXCLUDED.max_rate
            """, (window_start, upper))

            new_ts = int(upper // 300) * 300
            cur.execute(
                "UPDATE rollup_state SET last_ts = %s WHERE tier = '5m'",
                (new_ts,),
            )
            cur.close()
        return upper < fence

    # SQLite
    import os as _os
    if not _os.path.exists(LOGS_DB_PATH):
        return False  # DB not yet initialised — skip silently
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=30)
        last_ts = con.execute(
            "SELECT last_ts FROM rollup_state WHERE tier = '5m'"
        ).fetchone()
        last_ts = last_ts[0] if last_ts else 0

        # Jump forward across empty gaps (e.g. after v0.9.6 backfill reset).
        min_next = con.execute(
            "SELECT MIN(ts) FROM sensor_samples WHERE ts > ?",
            (last_ts,)
        ).fetchone()[0]
        if min_next is None or min_next >= fence:
            new_ts = int(fence // 300) * 300
            con.execute(
                "UPDATE rollup_state SET last_ts = ? WHERE tier = '5m'", (new_ts,)
            )
            con.commit()
            return False

        window_start = max(last_ts, int(min_next) - 1)
        upper = min(fence, window_start + _ROLLUP_5M_CHUNK_S)

        # v0.9.6: aggregate {avg,min,max,first,last}_value alongside ms in Python
        # — SQLite has no regex for the numeric guard and first/last within a
        # bucket need endpoint picks that are awkward in pure SQL. Fetch raw
        # rows in the window, bucket, compute per-bucket aggregates, UPSERT.
        # v0.9.7: also aggregate rate into {avg,min,max}_rate per bucket.
        raw = con.execute("""
            SELECT ts, did, sid, ok, ms, value, rate
            FROM sensor_samples
            WHERE ts > ? AND ts < ?
        """, (window_start, upper)).fetchall()

        buckets = _bucket_raw_rows(raw, 300)
        for key, b in buckets.items():
            bucket, did, sid = key
            con.execute("""
                INSERT INTO sensor_samples_5m
                    (ts, did, sid, ok_count, fail_count,
                     avg_ms, min_ms, max_ms, avg_ms_sq, sample_count,
                     avg_value, min_value, max_value, first_value, last_value,
                     avg_rate, min_rate, max_rate)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (did, sid, ts) DO UPDATE SET
                    ok_count=excluded.ok_count, fail_count=excluded.fail_count,
                    avg_ms=excluded.avg_ms, min_ms=excluded.min_ms, max_ms=excluded.max_ms,
                    avg_ms_sq=excluded.avg_ms_sq, sample_count=excluded.sample_count,
                    avg_value=excluded.avg_value, min_value=excluded.min_value,
                    max_value=excluded.max_value,
                    first_value=excluded.first_value, last_value=excluded.last_value,
                    avg_rate=excluded.avg_rate, min_rate=excluded.min_rate,
                    max_rate=excluded.max_rate
            """, (bucket, did, sid, b['ok'], b['fail'],
                  b['avg_ms'], b['min_ms'], b['max_ms'], b['avg_ms_sq'], b['count'],
                  b['avg_value'], b['min_value'], b['max_value'],
                  b['first_value'], b['last_value'],
                  b['avg_rate'], b['rate_min'], b['rate_max']))

        new_ts = int(upper // 300) * 300
        con.execute(
            "UPDATE rollup_state SET last_ts = ? WHERE tier = '5m'", (new_ts,)
        )
        con.commit()
        return upper < fence
    finally:
        if con:
            con.close()


def _rollup_1h():
    """Aggregate 5-minute buckets into 1-hour buckets (weighted averages).

    Returns True if more work remains, False once caught up. Same chunking
    strategy as _rollup_5m — 3-day window per call, forward-jump over gaps.
    """
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
                SELECT MIN(min_ts) FROM (
                    SELECT MIN(ts) AS min_ts FROM sensor_samples_5m
                    WHERE ts > %s GROUP BY did, sid
                ) t
            """, (last_ts,))
            r = cur.fetchone()
            min_next = r[0] if r else None

            if min_next is None or min_next >= fence:
                new_ts = int(fence // 3600) * 3600
                cur.execute(
                    "UPDATE rollup_state SET last_ts = %s WHERE tier = '1h'",
                    (new_ts,),
                )
                cur.close()
                return False

            window_start = max(last_ts, int(min_next) - 1)
            upper = min(fence, window_start + _ROLLUP_1H_CHUNK_S)

            # v0.9.6: {avg,min,max,first,last}_value rolled up from 5m endpoints.
            # avg_value is sample_count-weighted over 5m buckets where avg_value
            # is non-NULL; first/last pick the 5m endpoint at the earliest/latest
            # ts in the hour.
            cur.execute("""
                INSERT INTO sensor_samples_1h
                    (ts, did, sid, ok_count, fail_count,
                     avg_ms, min_ms, max_ms, avg_ms_sq, sample_count,
                     avg_value, min_value, max_value, first_value, last_value,
                     avg_rate, min_rate, max_rate)
                SELECT FLOOR(ts / 3600) * 3600 AS bucket,
                       did, sid,
                       SUM(ok_count), SUM(fail_count),
                       SUM(avg_ms * sample_count) / NULLIF(SUM(sample_count), 0),
                       MIN(min_ms), MAX(max_ms),
                       SUM(avg_ms_sq * sample_count) / NULLIF(SUM(sample_count), 0),
                       SUM(sample_count),
                       SUM(avg_value * sample_count) FILTER (WHERE avg_value IS NOT NULL)
                         / NULLIF(SUM(sample_count) FILTER (WHERE avg_value IS NOT NULL), 0),
                       MIN(min_value),
                       MAX(max_value),
                       (ARRAY_AGG(first_value ORDER BY ts ASC)
                           FILTER (WHERE first_value IS NOT NULL))[1],
                       (ARRAY_AGG(last_value ORDER BY ts DESC)
                           FILTER (WHERE last_value IS NOT NULL))[1],
                       SUM(avg_rate * sample_count) FILTER (WHERE avg_rate IS NOT NULL)
                         / NULLIF(SUM(sample_count) FILTER (WHERE avg_rate IS NOT NULL), 0),
                       MIN(min_rate),
                       MAX(max_rate)
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
                    sample_count = EXCLUDED.sample_count,
                    avg_value    = EXCLUDED.avg_value,
                    min_value    = EXCLUDED.min_value,
                    max_value    = EXCLUDED.max_value,
                    first_value  = EXCLUDED.first_value,
                    last_value   = EXCLUDED.last_value,
                    avg_rate     = EXCLUDED.avg_rate,
                    min_rate     = EXCLUDED.min_rate,
                    max_rate     = EXCLUDED.max_rate
            """, (window_start, upper))

            new_ts = int(upper // 3600) * 3600
            cur.execute(
                "UPDATE rollup_state SET last_ts = %s WHERE tier = '1h'",
                (new_ts,),
            )
            cur.close()
        return upper < fence

    # SQLite
    import os as _os
    if not _os.path.exists(LOGS_DB_PATH):
        return False  # DB not yet initialised — skip silently
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=30)
        last_ts = con.execute(
            "SELECT last_ts FROM rollup_state WHERE tier = '1h'"
        ).fetchone()
        last_ts = last_ts[0] if last_ts else 0

        min_next = con.execute(
            "SELECT MIN(ts) FROM sensor_samples_5m WHERE ts > ?",
            (last_ts,)
        ).fetchone()[0]
        if min_next is None or min_next >= fence:
            new_ts = int(fence // 3600) * 3600
            con.execute(
                "UPDATE rollup_state SET last_ts = ? WHERE tier = '1h'", (new_ts,)
            )
            con.commit()
            return False

        window_start = max(last_ts, int(min_next) - 1)
        upper = min(fence, window_start + _ROLLUP_1H_CHUNK_S)

        # v0.9.6: roll up 5m endpoints into 1h buckets in Python — same logic
        # as the PG query above. Fetch 5m rows in the window, group by hour+did+sid,
        # weighted-avg avg_value by sample_count, pick first/last value from the
        # 5m bucket with the min/max ts in that hour.
        # v0.9.7: also roll up avg_rate (sample_count-weighted) + min_rate / max_rate.
        raw = con.execute("""
            SELECT ts, did, sid, ok_count, fail_count,
                   avg_ms, min_ms, max_ms, avg_ms_sq, sample_count,
                   avg_value, min_value, max_value, first_value, last_value,
                   avg_rate, min_rate, max_rate
            FROM sensor_samples_5m
            WHERE ts > ? AND ts < ?
        """, (window_start, upper)).fetchall()

        from collections import defaultdict
        hourly = defaultdict(lambda: {
            'ok_count': 0, 'fail_count': 0, 'sample_count': 0,
            'ms_weighted_sum': 0.0, 'ms_weight': 0,
            'ms_sq_weighted_sum': 0.0,
            'min_ms': None, 'max_ms': None,
            'val_weighted_sum': 0.0, 'val_weight': 0,
            'min_value': None, 'max_value': None,
            'first_ts': None, 'first_value': None,
            'last_ts': None,  'last_value': None,
            'rate_weighted_sum': 0.0, 'rate_weight': 0,
            'min_rate': None, 'max_rate': None,
        })
        for (ts, did, sid, ok_c, fail_c,
             avg_ms, min_ms, max_ms, avg_ms_sq, sc,
             avg_v, min_v, max_v, first_v, last_v,
             avg_r, min_r, max_r) in raw:
            bucket = int(ts // 3600) * 3600
            h = hourly[(bucket, did, sid)]
            h['ok_count']     += ok_c
            h['fail_count']   += fail_c
            h['sample_count'] += sc
            if avg_ms is not None and sc:
                h['ms_weighted_sum']    += avg_ms * sc
                h['ms_sq_weighted_sum'] += (avg_ms_sq or 0) * sc
                h['ms_weight']          += sc
            if min_ms is not None and (h['min_ms'] is None or min_ms < h['min_ms']):
                h['min_ms'] = min_ms
            if max_ms is not None and (h['max_ms'] is None or max_ms > h['max_ms']):
                h['max_ms'] = max_ms
            if avg_v is not None and sc:
                h['val_weighted_sum'] += avg_v * sc
                h['val_weight']       += sc
            if min_v is not None and (h['min_value'] is None or min_v < h['min_value']):
                h['min_value'] = min_v
            if max_v is not None and (h['max_value'] is None or max_v > h['max_value']):
                h['max_value'] = max_v
            if first_v is not None and (h['first_ts'] is None or ts < h['first_ts']):
                h['first_ts'] = ts
                h['first_value'] = first_v
            if last_v is not None and (h['last_ts'] is None or ts > h['last_ts']):
                h['last_ts'] = ts
                h['last_value'] = last_v
            if avg_r is not None and sc:
                h['rate_weighted_sum'] += avg_r * sc
                h['rate_weight']       += sc
            if min_r is not None and (h['min_rate'] is None or min_r < h['min_rate']):
                h['min_rate'] = min_r
            if max_r is not None and (h['max_rate'] is None or max_r > h['max_rate']):
                h['max_rate'] = max_r

        for (bucket, did, sid), h in hourly.items():
            avg_ms    = h['ms_weighted_sum'] / h['ms_weight'] if h['ms_weight'] else None
            avg_ms_sq = h['ms_sq_weighted_sum'] / h['ms_weight'] if h['ms_weight'] else None
            avg_value = h['val_weighted_sum'] / h['val_weight'] if h['val_weight'] else None
            avg_rate  = h['rate_weighted_sum'] / h['rate_weight'] if h['rate_weight'] else None
            con.execute("""
                INSERT INTO sensor_samples_1h
                    (ts, did, sid, ok_count, fail_count,
                     avg_ms, min_ms, max_ms, avg_ms_sq, sample_count,
                     avg_value, min_value, max_value, first_value, last_value,
                     avg_rate, min_rate, max_rate)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (did, sid, ts) DO UPDATE SET
                    ok_count=excluded.ok_count, fail_count=excluded.fail_count,
                    avg_ms=excluded.avg_ms, min_ms=excluded.min_ms, max_ms=excluded.max_ms,
                    avg_ms_sq=excluded.avg_ms_sq, sample_count=excluded.sample_count,
                    avg_value=excluded.avg_value, min_value=excluded.min_value,
                    max_value=excluded.max_value,
                    first_value=excluded.first_value, last_value=excluded.last_value,
                    avg_rate=excluded.avg_rate, min_rate=excluded.min_rate,
                    max_rate=excluded.max_rate
            """, (bucket, did, sid, h['ok_count'], h['fail_count'],
                  avg_ms, h['min_ms'], h['max_ms'], avg_ms_sq, h['sample_count'],
                  avg_value, h['min_value'], h['max_value'],
                  h['first_value'], h['last_value'],
                  avg_rate, h['min_rate'], h['max_rate']))

        new_ts = int(upper // 3600) * 3600
        con.execute(
            "UPDATE rollup_state SET last_ts = ? WHERE tier = '1h'", (new_ts,)
        )
        con.commit()
        return upper < fence
    finally:
        if con:
            con.close()


def _migration_done(key: str) -> bool:
    """Return True if a one-shot migration marker is set in app_settings."""
    try:
        if is_pg():
            from db.pg_pool import pg_cursor
            with pg_cursor("main") as cur:
                cur.execute("SELECT value FROM app_settings WHERE key=%s", (key,))
                row = cur.fetchone()
                return bool(row and row["value"] == "1")
        from core.config import DB_PATH
        c = sqlite3.connect(DB_PATH, timeout=5)
        try:
            r = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
            return bool(r and r[0] == "1")
        finally:
            c.close()
    except Exception:
        return False   # treat unreadable marker as "not done" — safe default


def _mark_migration_done(key: str) -> None:
    """Persist a one-shot migration marker so it doesn't re-run next startup."""
    try:
        if is_pg():
            from db.pg_pool import pg_conn
            with pg_conn("main") as con:
                con.cursor().execute(
                    "INSERT INTO app_settings (key, value) VALUES (%s, '1') "
                    "ON CONFLICT (key) DO UPDATE SET value='1'",
                    (key,),
                )
            return
        from core.config import DB_PATH
        c = sqlite3.connect(DB_PATH, timeout=5)
        try:
            c.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, '1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'",
                (key,),
            )
            c.commit()
        finally:
            c.close()
    except Exception as e:
        log.warning(f"Could not mark migration '{key}' done: {e}")


# Absolute upper bound for an SNMP counter rate (bytes/sec or events/sec).
# 1.25e11 B/s = 1 TB/s = 8 Tbps — beyond any physical interface in use today.
# Anything above is the residue of a counter reset misread as a wrap, or a
# clock anomaly on the probe side. Used by both the probe-time defense in
# core/state.py and the one-shot cleanup below.
_RATE_SANITY_MAX = 1.25e11


def db_cleanup_impossible_rates():
    """Scrub physically-impossible rate values left over from before the
    probe-time defenses landed (Counter64 reset misread as wrap, sub-second
    elapsed division, etc.). Runs once, gated by an app_settings marker.

    NULLs the offending columns rather than deleting rows so the
    surrounding sample/aggregate stays intact for the History chart.
    """
    _KEY = "rate_cleanup_v1_done"
    if _migration_done(_KEY):
        return
    try:
        if is_pg():
            from db.pg_pool import pg_conn
            with pg_conn("logs") as con:
                cur = con.cursor()
                cur.execute(
                    "UPDATE sensor_samples SET rate=NULL WHERE rate > %s",
                    (_RATE_SANITY_MAX,),
                )
                cur.execute(
                    "UPDATE sensor_samples_5m "
                    "SET avg_rate = CASE WHEN avg_rate > %s THEN NULL ELSE avg_rate END, "
                    "    min_rate = CASE WHEN min_rate > %s THEN NULL ELSE min_rate END, "
                    "    max_rate = CASE WHEN max_rate > %s THEN NULL ELSE max_rate END "
                    "WHERE avg_rate > %s OR min_rate > %s OR max_rate > %s",
                    (_RATE_SANITY_MAX,) * 6,
                )
                cur.execute(
                    "UPDATE sensor_samples_1h "
                    "SET avg_rate = CASE WHEN avg_rate > %s THEN NULL ELSE avg_rate END, "
                    "    min_rate = CASE WHEN min_rate > %s THEN NULL ELSE min_rate END, "
                    "    max_rate = CASE WHEN max_rate > %s THEN NULL ELSE max_rate END "
                    "WHERE avg_rate > %s OR min_rate > %s OR max_rate > %s",
                    (_RATE_SANITY_MAX,) * 6,
                )
        else:
            con = sqlite3.connect(LOGS_DB_PATH, timeout=10)
            try:
                con.execute(
                    "UPDATE sensor_samples SET rate=NULL WHERE rate > ?",
                    (_RATE_SANITY_MAX,),
                )
                con.execute(
                    "UPDATE sensor_samples_5m SET "
                    "avg_rate = CASE WHEN avg_rate > ? THEN NULL ELSE avg_rate END, "
                    "min_rate = CASE WHEN min_rate > ? THEN NULL ELSE min_rate END, "
                    "max_rate = CASE WHEN max_rate > ? THEN NULL ELSE max_rate END "
                    "WHERE avg_rate > ? OR min_rate > ? OR max_rate > ?",
                    (_RATE_SANITY_MAX,) * 6,
                )
                con.execute(
                    "UPDATE sensor_samples_1h SET "
                    "avg_rate = CASE WHEN avg_rate > ? THEN NULL ELSE avg_rate END, "
                    "min_rate = CASE WHEN min_rate > ? THEN NULL ELSE min_rate END, "
                    "max_rate = CASE WHEN max_rate > ? THEN NULL ELSE max_rate END "
                    "WHERE avg_rate > ? OR min_rate > ? OR max_rate > ?",
                    (_RATE_SANITY_MAX,) * 6,
                )
                con.commit()
            finally:
                con.close()
        _mark_migration_done(_KEY)
        log.info("Cleaned up impossible SNMP rate values from sample tables")
    except Exception as e:
        log.error(f"db_cleanup_impossible_rates failed: {e}", exc_info=True)


def db_rollup_backfill():
    """Backfill rollup tables from existing raw data if needed.

    Runs on startup.  Three cases trigger a backfill:
    1. sensor_samples_5m is empty — never rolled up yet.
    2. MIN(ts) in sensor_samples predates rollup_state.last_ts by >10 min —
       last_ts jumped ahead (e.g. partition migration interrupted) leaving
       historical data unprocessed.
    3. (v0.9.6) Rollup rows exist with NULL avg_value but raw data still has
       numeric `value` for the same sensor — means the rollup predates v0.9.6's
       value-aggregate columns. Reprocess so value aggregates are populated
       for the full raw-retention window. Guarded by a one-shot `app_settings`
       marker ('migration_v096_value_aggregate_done'): otherwise old rollup
       rows outside the raw-retention window permanently have NULL avg_value
       (unrecoverable) and would make the trigger fire on every startup
       forever.

    Safe to call repeatedly — upsert prevents duplicates.
    """
    _V096_KEY = "migration_v096_value_aggregate_done"
    trigger_reason = None
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor("logs") as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM sensor_samples_5m")
            empty = cur.fetchone()["cnt"] == 0
            if empty:
                cur.execute("SELECT COUNT(*) AS cnt FROM sensor_samples")
                if cur.fetchone()["cnt"] == 0:
                    return  # no raw data to backfill from
                trigger_reason = "rollup empty"
            elif not _migration_done(_V096_KEY):
                # First startup after deploying v0.9.6 — rollup rows missing
                # avg_value need repopulation from raw data within retention.
                cur.execute(
                    "SELECT EXISTS(SELECT 1 FROM sensor_samples_5m "
                    "WHERE avg_value IS NULL LIMIT 1) AS need"
                )
                need_value_backfill = bool(cur.fetchone()["need"])
                if need_value_backfill:
                    cur.execute(
                        "SELECT EXISTS(SELECT 1 FROM sensor_samples "
                        "WHERE value IS NOT NULL LIMIT 1) AS has"
                    )
                    if bool(cur.fetchone()["has"]):
                        trigger_reason = "v0.9.6 value-aggregate migration"
                if not need_value_backfill:
                    # Nothing to backfill — mark as done so we don't re-check.
                    _mark_migration_done(_V096_KEY)
        if trigger_reason is None:
            return
        log.info(f"Backfilling rollup tables from existing PG data ({trigger_reason}) …")
    else:
        con = sqlite3.connect(LOGS_DB_PATH)
        try:
            cnt = con.execute("SELECT COUNT(*) FROM sensor_samples_5m").fetchone()[0]
            empty = cnt == 0
            if empty:
                raw_cnt = con.execute("SELECT COUNT(*) FROM sensor_samples").fetchone()[0]
                if not raw_cnt:
                    return
                trigger_reason = "rollup empty"
            elif not _migration_done(_V096_KEY):
                need_value = con.execute(
                    "SELECT EXISTS(SELECT 1 FROM sensor_samples_5m "
                    "WHERE avg_value IS NULL LIMIT 1)"
                ).fetchone()[0]
                if need_value:
                    has_raw_value = con.execute(
                        "SELECT EXISTS(SELECT 1 FROM sensor_samples "
                        "WHERE value IS NOT NULL LIMIT 1)"
                    ).fetchone()[0]
                    if has_raw_value:
                        trigger_reason = "v0.9.6 value-aggregate migration"
                if not need_value:
                    _mark_migration_done(_V096_KEY)
        finally:
            con.close()
        if trigger_reason is None:
            return
        log.info(f"Backfilling rollup tables from existing SQLite data ({trigger_reason}) …")

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

    # Loop each tier until drained — every call processes at most one chunk
    # (_ROLLUP_5M_CHUNK_S / _ROLLUP_1H_CHUNK_S) so a multi-week backfill never
    # exceeds PG's statement_timeout on any single INSERT.
    try:
        started = time.time()
        chunks_5m = 0
        while _rollup_5m():
            chunks_5m += 1
            if chunks_5m % 20 == 0:
                log.info(f"Rollup backfill: 5m tier processed {chunks_5m} chunks so far …")
            if chunks_5m > 5000:  # safety cap (~1.7 years at 3h/chunk)
                log.error("Rollup backfill: 5m tier chunk cap hit — aborting")
                break
        log.info(f"Rollup backfill: 5m tier complete ({chunks_5m + 1} chunks)")

        chunks_1h = 0
        while _rollup_1h():
            chunks_1h += 1
            if chunks_1h % 20 == 0:
                log.info(f"Rollup backfill: 1h tier processed {chunks_1h} chunks so far …")
            if chunks_1h > 2000:  # safety cap (~16 years at 3d/chunk)
                log.error("Rollup backfill: 1h tier chunk cap hit — aborting")
                break
        log.info(f"Rollup backfill: 1h tier complete ({chunks_1h + 1} chunks)")

        log.info(f"Rollup backfill complete in {time.time() - started:.1f}s")
        # One-shot guard: prevent the v0.9.6 trigger from re-firing next startup
        # just because pre-v0.9.6 rollup rows (outside raw retention) still carry
        # NULL avg_value — those can never be recovered.
        if trigger_reason == "v0.9.6 value-aggregate migration":
            _mark_migration_done(_V096_KEY)
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


def _bounded_delete_pg(con, cur, table, cutoff):
    """Delete rows in batches of _TRIM_BATCH; commit between batches so locks
    release and concurrent sample inserts can proceed. Returns total deleted.

    The outer DELETE carries its own `ts < cutoff` predicate. On the
    range-partitioned sensor_samples table, ctid values are only unique per
    physical partition — a ctid harvested from the expiring partition also
    matches unrelated FRESH rows in every other partition, silently deleting
    live monitoring data. The ts guard limits any collision to rows that are
    themselves expired (a batch may then delete slightly more than LIMIT per
    pass, which is harmless for retention)."""
    from psycopg2 import sql as _pgsql
    query = _pgsql.SQL(
        "DELETE FROM {tbl} WHERE ctid IN "
        "(SELECT ctid FROM {tbl} WHERE ts < %s LIMIT %s) "
        "AND ts < %s"
    ).format(tbl=_pgsql.Identifier(table))
    total = 0
    while True:
        cur.execute(query, (cutoff, _TRIM_BATCH, cutoff))
        deleted = cur.rowcount or 0
        if deleted <= 0:
            break
        total += deleted
        con.commit()
        log.debug(f"{table} trim: {deleted} rows (total={total})")
        if deleted < _TRIM_BATCH:
            break
        time.sleep(_TRIM_YIELD_S)
    return total


def _clean_pg(cutoff_raw, cutoff_5m, cutoff_1h):
    """PG cleanup — drops whole partitions when possible, else bounded DELETE."""
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

            # Fallback bounded DELETE for non-partitioned or partial partitions.
            n_raw = _bounded_delete_pg(con, cur, "sensor_samples",    cutoff_raw)
            n_5m  = _bounded_delete_pg(con, cur, "sensor_samples_5m", cutoff_5m)
            n_1h  = _bounded_delete_pg(con, cur, "sensor_samples_1h", cutoff_1h)
            cur.close()
        if n_raw or n_5m or n_1h:
            log.info(f"PG sample cleanup: raw={n_raw} 5m={n_5m} 1h={n_1h}")
        else:
            log.debug("PG sample cleanup complete (nothing to trim)")
    except Exception as e:
        log.error(f"DB clean samples error: {e}")


def _bounded_delete_sqlite(con, table, cutoff):
    """Delete rows in batches of _TRIM_BATCH; commit between batches so the
    sample-flush loop can grab the write lock. Returns total deleted.
    Table names are hardcoded constants (sensor_samples, _5m, _1h)."""
    total = 0
    while True:
        cur = con.execute(
            f"DELETE FROM {table} WHERE rowid IN "
            f"(SELECT rowid FROM {table} WHERE ts < ? LIMIT ?)",
            (cutoff, _TRIM_BATCH),
        )
        deleted = cur.rowcount or 0
        if deleted <= 0:
            break
        total += deleted
        con.commit()
        log.debug(f"{table} trim: {deleted} rows (total={total})")
        if deleted < _TRIM_BATCH:
            break
        time.sleep(_TRIM_YIELD_S)
    return total


def _clean_sqlite(cutoff_raw, cutoff_5m, cutoff_1h):
    """SQLite cleanup with VACUUM in background thread."""
    con = None
    n_raw = n_5m = n_1h = 0
    try:
        con = sqlite3.connect(LOGS_DB_PATH, timeout=30)
        n_raw = _bounded_delete_sqlite(con, "sensor_samples",    cutoff_raw)
        n_5m  = _bounded_delete_sqlite(con, "sensor_samples_5m", cutoff_5m)
        n_1h  = _bounded_delete_sqlite(con, "sensor_samples_1h", cutoff_1h)
        if n_raw or n_5m or n_1h:
            log.info(f"DB sample cleanup: raw={n_raw} 5m={n_5m} 1h={n_1h}")
        else:
            log.debug("DB sample cleanup complete (nothing to trim)")
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
    # VACUUM at most weekly, and only after a meaningful trim. VACUUM is a
    # full-file rewrite that takes the write lock for the duration — running
    # it hourly blocked sample flushes (whose batches then failed) and burned
    # SSD writes for no benefit: SQLite recycles freed pages for new inserts,
    # so VACUUM only matters for shrinking the file after a large purge.
    global _last_vacuum_ts
    if (n_raw + n_5m + n_1h) < _VACUUM_MIN_TRIMMED_ROWS:
        return
    if time.time() - _last_vacuum_ts < _VACUUM_MIN_INTERVAL_S:
        return
    _last_vacuum_ts = time.time()
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
    # v0.9.7: include rate so the frontend prefers backend-computed
    # (Counter64-safe) rate over client-side diff of consecutive `value`.
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
                        "SELECT ts,ok,ms,value,rate FROM sensor_samples "
                        "WHERE did=%s AND sid=%s AND ts>=%s ORDER BY ts ASC",
                        (did, sid, cutoff)
                    )
                else:
                    cur.execute(
                        "SELECT ts,ok,ms,value,rate FROM ("
                        "  SELECT ts,ok,ms,value,rate,ROW_NUMBER() OVER (ORDER BY ts) rn "
                        "  FROM sensor_samples WHERE did=%s AND sid=%s AND ts>=%s"
                        ") sub WHERE rn %% %s = 1 ORDER BY ts ASC LIMIT %s",
                        (did, sid, cutoff, stride, limit)
                    )
                rows = cur.fetchall()
            return [{"ts": r["ts"], "ok": bool(r["ok"]), "ms": r["ms"],
                     "value": r["value"], "rate": r["rate"]}
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
                "SELECT ts,ok,ms,value,rate FROM sensor_samples "
                "WHERE did=? AND sid=? AND ts>=? ORDER BY ts ASC",
                (did, sid, cutoff)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT ts,ok,ms,value,rate FROM ("
                "  SELECT ts,ok,ms,value,rate,ROW_NUMBER() OVER (ORDER BY ts) rn "
                "  FROM sensor_samples WHERE did=? AND sid=? AND ts>=?"
                ") WHERE rn % ? = 1 ORDER BY ts ASC LIMIT ?",
                (did, sid, cutoff, stride, limit)
            ).fetchall()
        return [{"ts": r[0], "ok": bool(r[1]), "ms": r[2], "value": r[3], "rate": r[4]}
                for r in rows]
    except Exception as e:
        log.error(f"DB load history error: {e}")
        return []
    finally:
        if con: con.close()


def _history_from_rollup(did, sid, cutoff, limit, table):
    """Load history from a rollup table, mapping to the same JSON format.

    v0.9.6: includes {avg,min,max,first,last}_value + bucket_s so the frontend
    can derive rate / render gauges without raw samples.
    v0.9.7: adds {avg,min,max}_rate so the frontend renders peak-preserving
    min/max envelope at rollup tiers for counter-type SNMP sensors."""
    bucket_s = 3600 if table == "sensor_samples_1h" else 300
    cols = ("ts, ok_count, fail_count, avg_ms, "
            "avg_value, min_value, max_value, first_value, last_value, "
            "avg_rate, min_rate, max_rate")
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
                        f"SELECT {cols} "
                        f"FROM {table} WHERE did=%s AND sid=%s AND ts>=%s "
                        f"ORDER BY ts ASC",
                        (did, sid, cutoff)
                    )
                else:
                    cur.execute(
                        f"SELECT {cols} FROM ("
                        f"  SELECT {cols}, ROW_NUMBER() OVER (ORDER BY ts) rn "
                        f"  FROM {table} WHERE did=%s AND sid=%s AND ts>=%s"
                        f") sub WHERE rn %% %s = 1 ORDER BY ts ASC LIMIT %s",
                        (did, sid, cutoff, stride, limit)
                    )
                rows = cur.fetchall()
            return [{"ts": r["ts"], "ok": r["ok_count"] > r["fail_count"],
                     "ms": r["avg_ms"], "value": None, "rate": None,
                     "avg_value":   r["avg_value"],
                     "min_value":   r["min_value"],
                     "max_value":   r["max_value"],
                     "first_value": r["first_value"],
                     "last_value":  r["last_value"],
                     "avg_rate":    r["avg_rate"],
                     "min_rate":    r["min_rate"],
                     "max_rate":    r["max_rate"],
                     "bucket_s":    bucket_s}
                    for r in rows]
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
                f"SELECT {cols} FROM {table} "
                f"WHERE did=? AND sid=? AND ts>=? ORDER BY ts ASC",
                (did, sid, cutoff)
            ).fetchall()
        else:
            rows = con.execute(
                f"SELECT {cols} FROM ("
                f"  SELECT {cols}, ROW_NUMBER() OVER (ORDER BY ts) rn "
                f"  FROM {table} WHERE did=? AND sid=? AND ts>=?"
                f") WHERE rn % ? = 1 ORDER BY ts ASC LIMIT ?",
                (did, sid, cutoff, stride, limit)
            ).fetchall()
        return [{"ts": r[0], "ok": r[1] > r[2], "ms": r[3],
                 "value": None, "rate": None,
                 "avg_value": r[4], "min_value": r[5], "max_value": r[6],
                 "first_value": r[7], "last_value": r[8],
                 "avg_rate": r[9], "min_rate": r[10], "max_rate": r[11],
                 "bucket_s": bucket_s}
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


# ── Scoped availability (backs the MCP get_uptime tool) ───────────────
# Sample-based availability = OK-samples / total-samples over a window, for
# the whole fleet or a subset of device ids. Picks raw / 5m / 1h rollup by
# window size (same rule as db_load_availability). NOTE: this is distinct
# from outage-duration availability (start/stop from flap_log) — see the
# get_outages work for that. Different definition; don't conflate.
def _uptime_cols(table: str):
    """(ok_expr, total_expr) for the chosen sample table."""
    if table == "sensor_samples":
        return "SUM(ok)", "COUNT(*)"
    return "SUM(ok_count)", "SUM(ok_count + fail_count)"


def _uptime_table(start_ts, end_ts):
    minutes = max(1, int((float(end_ts) - float(start_ts)) // 60))
    table, _ = _pick_table(minutes)
    return table


def _uptime_where(start_ts, end_ts, dids):
    """Build a portable WHERE (?-placeholders) + params. db_query rewrites
    ? → %s for PostgreSQL."""
    where = ["ts >= ?", "ts < ?"]
    params = [start_ts, end_ts]
    if dids:
        where.append("did IN (" + ",".join(["?"] * len(dids)) + ")")
        params += list(dids)
    return " AND ".join(where), tuple(params)


def db_uptime_overall(start_ts, end_ts, dids=None) -> dict:
    """Fleet (or scoped) availability % over [start_ts, end_ts)."""
    table = _uptime_table(start_ts, end_ts)
    okc, totc = _uptime_cols(table)
    where, params = _uptime_where(start_ts, end_ts, dids)
    r = db_query_one("logs",
        f"SELECT {okc} AS up, {totc} AS total FROM {table} WHERE {where}", params)
    up    = int(r["up"] or 0) if r and r["up"] is not None else 0
    total = int(r["total"] or 0) if r and r["total"] is not None else 0
    return {"up": up, "total": total,
            "pct": round(up / total * 100, 3) if total else None,
            "table": table}


def db_uptime_by_device(start_ts, end_ts, dids=None, limit=20) -> list:
    """Per-device availability, ascending (worst first). Devices with no
    samples in the window are omitted."""
    table = _uptime_table(start_ts, end_ts)
    okc, totc = _uptime_cols(table)
    where, params = _uptime_where(start_ts, end_ts, dids)
    rows = db_query("logs",
        f"SELECT did, {okc} AS up, {totc} AS total FROM {table} "
        f"WHERE {where} GROUP BY did", params)
    out = []
    for r in rows:
        total = int(r["total"] or 0) if r["total"] is not None else 0
        if not total:
            continue
        up = int(r["up"] or 0) if r["up"] is not None else 0
        out.append({"did": r["did"], "up": up, "total": total,
                    "pct": round(up / total * 100, 3)})
    out.sort(key=lambda o: o["pct"])
    return out[:limit] if limit else out


def db_uptime_series(start_ts, end_ts, dids=None, bucket="day") -> list:
    """Availability % per day/week bucket. Uses is_pg branching for the
    floor expression (SQLite lacks FLOOR; PG's CAST AS INTEGER rounds)."""
    bucket_s = 604800 if bucket == "week" else 86400
    table = _uptime_table(start_ts, end_ts)
    okc, totc = _uptime_cols(table)
    pg = is_pg()
    ph = "%s" if pg else "?"
    floor_expr = (f"FLOOR(ts/{bucket_s})*{bucket_s}" if pg
                  else f"CAST(ts/{bucket_s} AS INTEGER)*{bucket_s}")
    where = [f"ts >= {ph}", f"ts < {ph}"]
    params = [start_ts, end_ts]
    if dids:
        where.append("did IN (" + ",".join([ph] * len(dids)) + ")")
        params += list(dids)
    W = " AND ".join(where)
    sql = (f"SELECT {floor_expr} AS b, {okc} AS up, {totc} AS total "
           f"FROM {table} WHERE {W} GROUP BY b ORDER BY b ASC")
    try:
        with db_cursor("logs") as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    except Exception as e:
        log.error(f"db_uptime_series error: {e}")
        return []
    out = []
    for r in rows:
        total = int(r["total"] or 0) if r["total"] is not None else 0
        up = int(r["up"] or 0) if r["up"] is not None else 0
        out.append({"bucket_ts": int(r["b"]),
                    "pct": round(up / total * 100, 3) if total else None})
    return out
