"""
db/migration.py — One-time safe split of the legacy single pingwatch.db into
                  pingwatch.db (Main) + pingwatch_logs.db (Logs).

Trigger: run_migration_if_needed() called from server.py at startup, before
         db_init() / logs_db_init().  It is a no-op if already done.

Guard:   app_settings key 'db_split_complete' == '1' in Main DB.
"""

import os
import shutil
import sqlite3

from core.config import DB_PATH, LOGS_DB_PATH
from core.logger import log

# Tables that belong exclusively in the Logs DB
_LOGS_TABLES = ("sensor_samples", "flap_log", "sensor_err_log", "snmp_traps")

# Chunk size for streaming INSERT OR IGNORE (limits peak memory for large tables)
_CHUNK = 10_000


def _table_exists(con, name):
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)


def _row_count(con, table):
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _copy_table_chunked(src_path, dst_path, table):
    """
    Copy all rows from `table` in src_path → dst_path using INSERT OR IGNORE
    in chunks of _CHUNK rows.  Resumable: re-running is safe because of OR IGNORE.
    Returns (src_count, dst_count_after).
    """
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path, timeout=30)
    try:
        dst.execute("PRAGMA journal_mode=WAL")
        if not _table_exists(src, table):
            return 0, 0
        src_count = _row_count(src, table)
        if src_count == 0:
            return 0, 0

        # Discover column names from src so the INSERT is explicit
        cols = [
            r[1] for r in src.execute(f"PRAGMA table_info({table})").fetchall()
        ]
        placeholders = ",".join("?" * len(cols))
        col_list     = ",".join(cols)

        offset = 0
        while True:
            rows = src.execute(
                f"SELECT {col_list} FROM {table} ORDER BY rowid ASC LIMIT ? OFFSET ?",
                (_CHUNK, offset)
            ).fetchall()
            if not rows:
                break
            dst.executemany(
                f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})",
                rows
            )
            dst.commit()
            offset += len(rows)
            if offset % 100_000 == 0:
                log.info(f"Migration: copied {offset:,}/{src_count:,} rows for '{table}'")

        dst_count = _row_count(dst, table)
        return src_count, dst_count
    finally:
        src.close()
        dst.close()


def _drop_logs_tables_from_main():
    """Drop the four logs tables from Main DB and VACUUM to reclaim space."""
    con = sqlite3.connect(DB_PATH, timeout=30)
    try:
        for tbl in _LOGS_TABLES:
            if _table_exists(con, tbl):
                con.execute(f"DROP TABLE IF EXISTS {tbl}")
                log.info(f"Migration: dropped '{tbl}' from Main DB")
        con.commit()
    finally:
        con.close()

    # VACUUM outside any transaction to reclaim freed pages
    try:
        vac = sqlite3.connect(DB_PATH, timeout=60)
        vac.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        vac.execute("VACUUM")
        vac.close()
        log.info("Migration: Main DB vacuumed")
    except Exception as e:
        log.warning(f"Migration: VACUUM failed (non-fatal): {e}")


def _set_split_complete():
    """Mark migration done in app_settings (direct write — threads not started yet)."""
    con = sqlite3.connect(DB_PATH, timeout=15)
    try:
        con.execute(
            "INSERT OR REPLACE INTO app_settings (key,value) VALUES ('db_split_complete','1')"
        )
        con.commit()
    finally:
        con.close()


def _is_split_complete():
    try:
        con = sqlite3.connect(DB_PATH)
        row = con.execute(
            "SELECT value FROM app_settings WHERE key='db_split_complete'"
        ).fetchone()
        con.close()
        return row is not None and row[0] == "1"
    except Exception:
        return False


def run_migration_if_needed():
    """
    Entry point called from server.py before db_init().

    Cases handled:
      1. Already migrated (guard set)  → no-op, fast return
      2. No Main DB yet               → fresh install; no migration needed
      3. Main DB lacks sensor_samples → post-split DB imported; create empty Logs DB schema
      4. Main DB has sensor_samples   → legacy single-DB; run the split
    """
    # ── Case 1: already done ──────────────────────────────────────
    if _is_split_complete():
        log.debug("Migration: already complete — skipping")
        return

    # ── Case 2: fresh install, no DB yet ─────────────────────────
    if not os.path.exists(DB_PATH):
        log.debug("Migration: no existing DB — skipping (fresh install)")
        return

    # ── Case 3/4: inspect existing DB ───────────────────────────
    try:
        con = sqlite3.connect(DB_PATH)
        has_samples = _table_exists(con, "sensor_samples")
        con.close()
    except Exception as e:
        log.error(f"Migration: cannot open Main DB to inspect — {e}")
        return

    if not has_samples:
        # Case 3: already split main DB (e.g. imported from another split install)
        log.info("Migration: Main DB has no sensor_samples → post-split import detected; "
                 "marking migration complete")
        _set_split_complete()
        return

    # ── Case 4: legacy single DB — run the split ─────────────────
    log.info("Migration: legacy single-DB detected — starting split to dual-DB architecture")

    # Safety backup (once)
    bak = str(DB_PATH) + ".pre_split.bak"
    if not os.path.exists(bak):
        try:
            shutil.copy2(DB_PATH, bak)
            log.info(f"Migration: safety backup created → {bak}")
        except Exception as e:
            log.error(f"Migration: safety backup failed — aborting split: {e}")
            return

    # Create Logs DB and its schema
    from db.core import logs_db_init
    try:
        logs_db_init()
    except Exception as e:
        log.error(f"Migration: logs_db_init failed — aborting: {e}")
        # Remove partial Logs DB so next restart retries cleanly
        try:
            if os.path.exists(LOGS_DB_PATH):
                os.unlink(LOGS_DB_PATH)
        except Exception:
            pass
        return

    # Copy each logs table from Main → Logs DB
    failed = False
    for tbl in _LOGS_TABLES:
        try:
            src_n, dst_n = _copy_table_chunked(str(DB_PATH), str(LOGS_DB_PATH), tbl)
            if src_n != dst_n:
                log.error(
                    f"Migration: row-count mismatch for '{tbl}' "
                    f"(src={src_n}, dst={dst_n}) — aborting"
                )
                failed = True
                break
            log.info(f"Migration: '{tbl}' copied — {dst_n:,} rows")
        except Exception as e:
            log.error(f"Migration: error copying '{tbl}' — {e}")
            failed = True
            break

    if failed:
        log.error(
            "Migration: aborted — Logs DB left in place for next retry "
            "(pre-split backup preserved original data)"
        )
        return

    # Mark complete (before dropping tables so a crash here is safe to retry)
    try:
        _set_split_complete()
    except Exception as e:
        log.error(f"Migration: could not set guard flag — {e}")
        return

    # Drop logs tables from Main DB and vacuum
    try:
        _drop_logs_tables_from_main()
    except Exception as e:
        log.warning(f"Migration: failed to drop tables from Main DB (non-fatal): {e}")

    # Report final sizes
    try:
        main_mb = os.path.getsize(DB_PATH)      / 1024 / 1024
        logs_mb = os.path.getsize(LOGS_DB_PATH) / 1024 / 1024
        log.info(
            f"Migration complete — Main DB: {main_mb:.1f} MB, "
            f"Logs DB: {logs_mb:.1f} MB"
        )
    except Exception:
        log.info("Migration complete")
