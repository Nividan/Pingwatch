"""
db/samples.py — Sample write buffer, flush, clean, and query helpers.
"""

import sqlite3
import threading
import time

from config    import DB_PATH
from logger    import log
from db.core   import _db_enqueue

# ── Sample write buffer (batches per-probe inserts) ───────────────
_SAMPLE_BUF: list    = []
_SAMPLE_BUF_LOCK     = threading.Lock()


def db_buffer_sample(did, sid, ok, ms, value, ts):
    """Append one probe result to the in-memory buffer (thread-safe, no I/O)."""
    with _SAMPLE_BUF_LOCK:
        _SAMPLE_BUF.append((ts, did, sid, int(ok), ms, value))


def _do_insert_samples(rows):
    """Write a batch of sample rows (called on the writer thread)."""
    con = None
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
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
    _do_insert_samples(rows)


def _sample_flush_loop():
    """Every 5 s drain the buffer and enqueue the write on the single writer thread."""
    while True:
        time.sleep(5)
        with _SAMPLE_BUF_LOCK:
            if not _SAMPLE_BUF:
                continue
            rows = _SAMPLE_BUF[:]
            _SAMPLE_BUF.clear()
        _db_enqueue(lambda r=rows: _do_insert_samples(r))


threading.Thread(target=_sample_flush_loop, daemon=True).start()


def db_log_sample(did, sid, ok, ms, value, ts):
    """Insert one probe result into sensor_samples. Always call via _db_enqueue."""
    con = None
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        con.execute(
            "INSERT INTO sensor_samples (ts,did,sid,ok,ms,value) VALUES (?,?,?,?,?,?)",
            (ts, did, sid, int(ok), ms, value)
        )
        con.commit()
    except Exception as e:
        log.error(f"DB log sample error: {e}")
    finally:
        if con:
            con.close()


def db_clean_samples(retention_days=365):
    """Delete sensor_samples older than retention_days; cap total rows at 10 million."""
    con = None
    try:
        cutoff = time.time() - retention_days * 86400
        con = sqlite3.connect(DB_PATH, timeout=15)
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
    except Exception as e:
        log.error(f"DB clean samples error: {e}")
        if "malformed" in str(e).lower():
            log.error(
                "DB CORRUPTION DETECTED — database disk image is malformed. "
                "Stop PingWatch, run: sqlite3 pingwatch.db 'PRAGMA integrity_check' "
                "to assess damage, or delete pingwatch.db to start fresh."
            )
            # Force WAL checkpoint to release any pending WAL locks
            try:
                if con:
                    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
    finally:
        if con:
            con.close()

    # Reclaim free pages left by the DELETE (must be outside any transaction)
    try:
        vac = sqlite3.connect(DB_PATH, timeout=30)
        vac.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        vac.execute("VACUUM")
        vac.close()
        log.info("DB vacuum complete")
    except Exception as e:
        log.warning("DB vacuum error: %s", e)


def db_load_history(did, sid, minutes=1440, limit=1000):
    """Return up to `limit` evenly-distributed samples from the last `minutes` minutes, oldest first."""
    try:
        cutoff = time.time() - minutes * 60
        con = sqlite3.connect(DB_PATH)
        total = con.execute(
            "SELECT COUNT(*) FROM sensor_samples WHERE did=? AND sid=? AND ts>=?",
            (did, sid, cutoff)
        ).fetchone()[0]
        stride = total // limit
        if stride < 2:
            # total is at or near limit — return all rows directly
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
        con.close()
        return [{"ts": r[0], "ok": bool(r[1]), "ms": r[2], "value": r[3]}
                for r in rows]
    except Exception as e:
        log.error(f"DB load history error: {e}")
        return []


def db_load_summary(did, sid, minutes=1440):
    """Return per-hour aggregation over the last `minutes` minutes."""
    try:
        cutoff = time.time() - minutes * 60
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("""
            SELECT CAST(ts/3600 AS INTEGER)*3600 AS hour_ts,
                   SUM(ok), COUNT(*)-SUM(ok),
                   AVG(ms), MIN(ms), MAX(ms)
            FROM sensor_samples
            WHERE did=? AND sid=? AND ts>=?
            GROUP BY hour_ts ORDER BY hour_ts ASC
        """, (did, sid, cutoff)).fetchall()
        con.close()
        return [{"ts": r[0], "ok": int(r[1] or 0), "fail": int(r[2] or 0),
                 "avg_ms": round(r[3], 1) if r[3] is not None else None,
                 "min_ms": round(r[4], 1) if r[4] is not None else None,
                 "max_ms": round(r[5], 1) if r[5] is not None else None}
                for r in rows]
    except Exception as e:
        log.error(f"DB load summary error: {e}")
        return []
