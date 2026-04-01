"""
db/samples.py — Sample write buffer, flush, clean, and query helpers.
"""

import math
import sqlite3
import threading
import time

from core.config import LOGS_DB_PATH
from core.logger import log
from db.backend  import is_pg
from db.core   import _logs_enqueue

# ── Sample write buffer (batches per-probe inserts) ───────────────
_SAMPLE_BUF: list    = []
_SAMPLE_BUF_LOCK     = threading.Lock()


def db_buffer_sample(did, sid, ok, ms, value, ts):
    """Append one probe result to the in-memory buffer (thread-safe, no I/O)."""
    with _SAMPLE_BUF_LOCK:
        _SAMPLE_BUF.append((ts, did, sid, int(ok), ms, value))


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
    """Drain the buffer and write directly (used at shutdown — no queue)."""
    with _SAMPLE_BUF_LOCK:
        if not _SAMPLE_BUF:
            return
        rows = _SAMPLE_BUF[:]
        _SAMPLE_BUF.clear()
    try:
        _do_insert_samples(rows)
    except Exception:
        # Re-prepend rows so they are retried on the next flush
        with _SAMPLE_BUF_LOCK:
            for r in reversed(rows):
                _SAMPLE_BUF.insert(0, r)


def _sample_flush_loop():
    """Every 5 s drain the buffer and enqueue a write (serialised with VACUUM)."""
    while True:
        time.sleep(5)
        with _SAMPLE_BUF_LOCK:
            if not _SAMPLE_BUF:
                continue
            rows = _SAMPLE_BUF[:]
            _SAMPLE_BUF.clear()
        _logs_enqueue(lambda r=rows: _do_insert_samples(r))


threading.Thread(target=_sample_flush_loop, daemon=True).start()


def db_clean_samples(retention_days=365):
    """Delete sensor_samples older than retention_days; cap total rows at 10 million."""
    if is_pg():
        from db.pg_pool import pg_conn
        try:
            cutoff = time.time() - retention_days * 86400
            with pg_conn("logs") as con:
                cur = con.cursor()
                cur.execute("DELETE FROM sensor_samples WHERE ts < %s", (cutoff,))
                cur.execute("SELECT COUNT(*) FROM sensor_samples")
                total = cur.fetchone()[0]
                if total > 10_000_000:
                    cur.execute(
                        "DELETE FROM sensor_samples WHERE id IN "
                        "(SELECT id FROM sensor_samples ORDER BY ts ASC LIMIT %s)",
                        (total - 10_000_000,)
                    )
                cur.close()
            log.debug("PG sample cleanup complete")
        except Exception as e:
            log.error(f"DB clean samples error: {e}")
        return
    # SQLite
    con = None
    try:
        cutoff = time.time() - retention_days * 86400
        con = sqlite3.connect(LOGS_DB_PATH, timeout=30)
        con.execute("DELETE FROM sensor_samples WHERE ts < ?", (cutoff,))
        # Row-count cap: keep newest 10M rows (~600 MB) regardless of probe rate
        total = con.execute("SELECT COUNT(*) FROM sensor_samples").fetchone()[0]
        if total > 10_000_000:
            con.execute(
                "DELETE FROM sensor_samples WHERE id IN "
                "(SELECT id FROM sensor_samples ORDER BY ts ASC LIMIT ?)",
                (total - 10_000_000,)
            )
        con.commit()
        # VACUUM inside the same connection (serialised via _logs_enqueue caller)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
        log.debug("DB vacuum complete")
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


def db_load_availability(minutes: int = 1440):
    """Return per-hour aggregate availability across ALL sensors for the last `minutes` minutes."""
    cutoff = time.time() - minutes * 60
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "SELECT FLOOR(ts/3600)*3600 AS h, SUM(ok) AS sum_ok, COUNT(*) AS cnt "
                    "FROM sensor_samples WHERE ts>=%s GROUP BY h ORDER BY h ASC",
                    (cutoff,)
                )
                rows = cur.fetchall()
            return [{"ts": r["h"], "pct": round(r["sum_ok"] / r["cnt"] * 100, 1) if r["cnt"] else 0,
                     "up": int(r["sum_ok"] or 0), "total": int(r["cnt"] or 0)} for r in rows]
        except Exception as e:
            log.error(f"DB load availability error: {e}")
            return []
    # SQLite
    con = None
    try:
        con = sqlite3.connect(LOGS_DB_PATH)
        rows = con.execute(
            "SELECT CAST(ts/3600 AS INTEGER)*3600 AS h, SUM(ok), COUNT(*) "
            "FROM sensor_samples WHERE ts>=? GROUP BY h ORDER BY h ASC",
            (cutoff,)
        ).fetchall()
        return [{"ts": r[0], "pct": round(r[1] / r[2] * 100, 1) if r[2] else 0,
                 "up": int(r[1] or 0), "total": int(r[2] or 0)} for r in rows]
    except Exception as e:
        log.error(f"DB load availability error: {e}")
        return []
    finally:
        if con: con.close()


def db_load_history(did, sid, minutes=1440, limit=1000):
    """Return up to `limit` evenly-distributed samples from the last `minutes` minutes, oldest first."""
    cutoff = time.time() - minutes * 60
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


def db_load_summary(did, sid, minutes=1440):
    """Return per-hour aggregation over the last `minutes` minutes."""
    cutoff = time.time() - minutes * 60
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
            result = []
            for r in rows:
                ok   = int(r["sum_ok"] or 0)
                fail = int(r["fail_cnt"] or 0)
                avg_ms    = round(r["avg_ms"], 1) if r["avg_ms"] is not None else None
                avg_ms_sq = r["avg_ms_sq"] or 0.0
                avg_ms_v  = r["avg_ms"] or 0.0
                jitter_ms = round(math.sqrt(max(0.0, avg_ms_sq - avg_ms_v ** 2)), 1)
                total     = ok + fail
                loss_pct  = round(fail / total * 100, 1) if total > 0 else 0.0
                result.append({
                    "ts":       r["hour_ts"],
                    "ok":       ok,
                    "fail":     fail,
                    "avg_ms":   avg_ms,
                    "min_ms":   round(r["min_ms"], 1) if r["min_ms"] is not None else None,
                    "max_ms":   round(r["max_ms"], 1) if r["max_ms"] is not None else None,
                    "jitter_ms": jitter_ms,
                    "loss_pct":  loss_pct,
                })
            return result
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
        result = []
        for r in rows:
            ok   = int(r[1] or 0)
            fail = int(r[2] or 0)
            avg_ms    = round(r[3], 1) if r[3] is not None else None
            avg_ms_sq = r[6] or 0.0
            avg_ms_v  = r[3] or 0.0
            jitter_ms = round(math.sqrt(max(0.0, avg_ms_sq - avg_ms_v ** 2)), 1)
            total     = ok + fail
            loss_pct  = round(fail / total * 100, 1) if total > 0 else 0.0
            result.append({
                "ts":       r[0],
                "ok":       ok,
                "fail":     fail,
                "avg_ms":   avg_ms,
                "min_ms":   round(r[4], 1) if r[4] is not None else None,
                "max_ms":   round(r[5], 1) if r[5] is not None else None,
                "jitter_ms": jitter_ms,
                "loss_pct":  loss_pct,
            })
        return result
    except Exception as e:
        log.error(f"DB load summary error: {e}")
        return []
    finally:
        if con: con.close()
