"""
db/pg_migrate.py — Migrate data from SQLite databases to PostgreSQL.

Used by:
  - Settings UI "Migrate to PostgreSQL" button
  - Could be invoked from CLI in the future

The migration copies all rows from both SQLite databases (main + logs) into the
corresponding PostgreSQL schemas, then resets sequences so auto-increment IDs
continue correctly.
"""

import sqlite3

from core.logger import log


# Tables to migrate from Main DB → PG 'main' schema
_MAIN_TABLES = [
    "users",
    "sessions",
    "devices",
    "sensors",
    "app_settings",
    "audit_log",
    "dashboard_widgets",
    "schema_version",
    "enterprise_oid_map",
    "trap_definitions",
    "trap_categories",
    "alert_rules",
    "alert_rule_conditions",
    "alert_rule_actions",
    "alert_events",
    "alert_dedup",
    "maintenance_windows",
    "user_groups",
]

# Tables to migrate from Logs DB → PG 'logs' schema
_LOGS_TABLES = [
    "sensor_samples",
    "flap_log",
    "sensor_err_log",
    "snmp_traps",
]

# Tables with SERIAL/BIGSERIAL columns that need sequence resets
_SERIAL_TABLES = {
    "main": [
        ("audit_log", "id"),
        ("alert_rules", "id"),
        ("alert_rule_conditions", "id"),
        ("alert_rule_actions", "id"),
        ("alert_events", "id"),
        ("maintenance_windows", "id"),
        ("user_groups", "id"),
    ],
    "logs": [
        ("sensor_samples", "id"),
        ("flap_log", "id"),
        ("sensor_err_log", "id"),
        ("snmp_traps", "id"),
    ],
}

_CHUNK = 5000


def migrate_sqlite_to_pg(main_db_path, logs_db_path, pg_config, progress_cb=None):
    """Copy all data from SQLite DBs to PostgreSQL.

    Parameters
    ----------
    main_db_path : str
        Path to the main SQLite database (pingwatch.db).
    logs_db_path : str
        Path to the logs SQLite database (pingwatch_logs.db).
    pg_config : dict
        Keys: pg_host, pg_port, pg_database, pg_user, pg_password.
    progress_cb : callable or None
        Called as progress_cb(table_name, rows_done, rows_total) per chunk.

    Returns
    -------
    (bool, str) — (success, message)
    """
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        return False, "psycopg2 not installed — run: pip install psycopg2-binary"

    # Connect to PostgreSQL
    try:
        pg_con = psycopg2.connect(
            host=pg_config["pg_host"],
            port=pg_config["pg_port"],
            dbname=pg_config["pg_database"],
            user=pg_config["pg_user"],
            password=pg_config["pg_password"],
        )
        pg_con.autocommit = False
    except Exception as e:
        return False, f"PG connection failed: {e}"

    try:
        cur = pg_con.cursor()

        # Create schemas if needed
        from db.pg_schema import pg_create_main_schema, pg_create_logs_schema, pg_seed_defaults
        pg_create_main_schema(cur)
        pg_create_logs_schema(cur)
        pg_seed_defaults(cur)
        pg_con.commit()

        # Migrate main DB tables
        try:
            main_con = sqlite3.connect(main_db_path)
            main_con.row_factory = sqlite3.Row
        except Exception as e:
            return False, f"Cannot open main SQLite DB: {e}"

        for table in _MAIN_TABLES:
            try:
                _migrate_table(main_con, cur, "main", table, progress_cb)
                pg_con.commit()
            except Exception as e:
                pg_con.rollback()
                log.warning(f"Migration: skipping main.{table}: {e}")
        main_con.close()

        # Migrate logs DB tables
        try:
            logs_con = sqlite3.connect(logs_db_path)
            logs_con.row_factory = sqlite3.Row
        except Exception as e:
            return False, f"Cannot open logs SQLite DB: {e}"

        for table in _LOGS_TABLES:
            try:
                _migrate_table(logs_con, cur, "logs", table, progress_cb)
                pg_con.commit()
            except Exception as e:
                pg_con.rollback()
                log.warning(f"Migration: skipping logs.{table}: {e}")
        logs_con.close()

        # Reset sequences
        for schema, tables in _SERIAL_TABLES.items():
            cur.execute(f"SET search_path TO {schema}, public")
            for table, col in tables:
                try:
                    cur.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
                        f"COALESCE((SELECT MAX({col}) FROM {table}), 0) + 1, false)"
                    )
                except Exception as e:
                    log.warning(f"Migration: sequence reset for {schema}.{table}.{col}: {e}")
                    pg_con.rollback()
                    cur.execute(f"SET search_path TO {schema}, public")
            pg_con.commit()

        # Validate row counts
        main_con = sqlite3.connect(main_db_path)
        logs_con = sqlite3.connect(logs_db_path)
        mismatches = []
        for table in _MAIN_TABLES:
            sq_count = _safe_count(main_con, table)
            cur.execute(f"SET search_path TO main, public")
            pg_count = _safe_count_pg(cur, table)
            if sq_count > 0 and pg_count < sq_count:
                mismatches.append(f"main.{table}: SQLite={sq_count}, PG={pg_count}")
        for table in _LOGS_TABLES:
            sq_count = _safe_count(logs_con, table)
            cur.execute(f"SET search_path TO logs, public")
            pg_count = _safe_count_pg(cur, table)
            if sq_count > 0 and pg_count < sq_count:
                mismatches.append(f"logs.{table}: SQLite={sq_count}, PG={pg_count}")
        main_con.close()
        logs_con.close()
        pg_con.commit()

        if mismatches:
            log.warning(f"Migration row count mismatches: {mismatches}")
            return True, f"Migration completed with warnings: {'; '.join(mismatches)}"

        log.info("SQLite → PostgreSQL migration completed successfully")
        return True, "Migration completed successfully"

    except Exception as e:
        try:
            pg_con.rollback()
        except Exception:
            pass
        log.error(f"Migration failed: {e}")
        return False, str(e)
    finally:
        try:
            pg_con.close()
        except Exception:
            pass


def _migrate_table(sq_con, pg_cur, schema, table, progress_cb):
    """Copy all rows from a SQLite table to the corresponding PG table."""
    # Get column names from SQLite
    try:
        sq_cur = sq_con.execute(f"SELECT * FROM {table} LIMIT 0")
        cols = [desc[0] for desc in sq_cur.description]
    except Exception:
        return  # table doesn't exist in SQLite — skip

    total = sq_con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if total == 0:
        return

    pg_cur.execute(f"SET search_path TO {schema}, public")

    # Find which columns are numeric in PG so we can coerce "" → None.
    # SQLite's loose typing allows storing "" in INTEGER columns; PG rejects it.
    pg_cur.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = %s AND table_name = %s
             AND data_type IN ('integer','bigint','smallint','double precision','numeric','real')""",
        (schema, table),
    )
    numeric_cols = {row[0] for row in pg_cur.fetchall()}
    numeric_idx = {i for i, c in enumerate(cols) if c in numeric_cols}

    # Delete existing PG rows to avoid conflicts
    pg_cur.execute(f"DELETE FROM {table}")

    # Read in chunks and insert
    col_list = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    import psycopg2.extras

    offset = 0
    skipped = 0
    first_err = None  # capture first distinct error for diagnosis

    while offset < total:
        rows = sq_con.execute(
            f"SELECT {col_list} FROM {table} LIMIT {_CHUNK} OFFSET {offset}"
        ).fetchall()
        if not rows:
            break
        # Coerce empty strings to None for numeric columns
        values = [
            tuple(None if (i in numeric_idx and v == "") else v for i, v in enumerate(row))
            for row in rows
        ]

        # Try bulk insert first; if it fails, fall back row-by-row with SAVEPOINTs
        # so a single bad row doesn't abort the whole transaction.
        try:
            pg_cur.execute("SAVEPOINT sp_batch")
            psycopg2.extras.execute_batch(pg_cur, insert_sql, values, page_size=500)
            pg_cur.execute("RELEASE SAVEPOINT sp_batch")
        except Exception:
            pg_cur.execute("ROLLBACK TO SAVEPOINT sp_batch")
            for v in values:
                try:
                    pg_cur.execute("SAVEPOINT sp_row")
                    pg_cur.execute(insert_sql, v)
                    pg_cur.execute("RELEASE SAVEPOINT sp_row")
                except Exception as row_err:
                    pg_cur.execute("ROLLBACK TO SAVEPOINT sp_row")
                    skipped += 1
                    if first_err is None:
                        first_err = str(row_err).split("\n")[0]  # first line only
                        # Log the failing row's key columns for diagnosis
                        key_idx = {c: i for i, c in enumerate(cols)}
                        did_v = v[key_idx["did"]] if "did" in key_idx else "?"
                        sid_v = v[key_idx["sid"]] if "sid" in key_idx else "?"
                        log.warning(
                            f"Migration: first skipped row in {schema}.{table} "
                            f"(did={did_v!r}, sid={sid_v!r}): {first_err}"
                        )

        offset += len(rows)
        if progress_cb:
            progress_cb(f"{schema}.{table}", offset, total)

    if skipped:
        log.warning(
            f"Migration: {schema}.{table} — {skipped} rows skipped "
            f"(first error: {first_err})"
        )

    log.info(f"Migrated {schema}.{table}: {total} rows")


def _safe_count(sq_con, table):
    try:
        return sq_con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        return 0


def _safe_count_pg(cur, table):
    try:
        cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
        return cur.fetchone()[0]
    except Exception:
        return 0
