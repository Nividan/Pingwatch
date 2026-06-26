"""routes/diagnostics.py — operator/support console endpoints.

All endpoints are admin-only and grouped under /api/diagnostics/. Most
delegate to existing helpers (auth_health, samples, server_info) — this
module is mostly glue + a few new helpers for things that weren't exposed
before (per-table stats, runtime snapshot, support bundle, NTP/DNS tests,
probe-from-server tool).

Endpoints:
  GET  /api/diagnostics/snapshot          — system overview JSON
  GET  /api/diagnostics/db-stats          — per-table rows + on-disk size
  GET  /api/diagnostics/recent-errors     — last 50 ERROR entries
  POST /api/diagnostics/probe             — ping/tcp/http/dns/tls from server
  POST /api/diagnostics/action/<name>     — vacuum / clear-caches / refresh-auth
  POST /api/diagnostics/test/ntp          — SNTP drift check
  POST /api/diagnostics/test/dns          — DNS resolver check
  GET  /api/diagnostics/bundle            — download sanitized support .zip
"""

from core.config import (
    _RE_DIAG_SNAPSHOT, _RE_DIAG_DB_STATS, _RE_DIAG_RECENT_ERRS,
    _RE_DIAG_PROBE, _RE_DIAG_ACTION, _RE_DIAG_TEST_NTP, _RE_DIAG_TEST_DNS,
    _RE_DIAG_BUNDLE,
)
from core.logger import log
from db import db_log_audit


def handle(h, method, path, body):
    # ── GET /api/diagnostics/snapshot ────────────────────────────
    if _RE_DIAG_SNAPSHOT.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user:
            return True
        try:
            h._json(200, _get_snapshot())
        except Exception as e:
            log.warning(f"diagnostics snapshot failed: {e}")
            h._json(500, {"error": "snapshot failed"})
        return True

    # ── GET /api/diagnostics/db-stats ────────────────────────────
    if _RE_DIAG_DB_STATS.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user:
            return True
        try:
            h._json(200, _get_db_stats())
        except Exception as e:
            log.warning(f"diagnostics db-stats failed: {e}")
            h._json(500, {"error": "db-stats failed"})
        return True

    # ── GET /api/diagnostics/recent-errors ───────────────────────
    if _RE_DIAG_RECENT_ERRS.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user:
            return True
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(h.path).query)
        source = (qs.get("source", ["app"])[0] or "app").lower()
        try:
            limit = max(1, min(200, int(qs.get("limit", ["50"])[0])))
        except (TypeError, ValueError):
            limit = 50
        try:
            h._json(200, _get_recent_errors(source, limit))
        except Exception as e:
            log.warning(f"diagnostics recent-errors failed: {e}")
            h._json(500, {"error": "recent-errors failed"})
        return True

    # ── POST /api/diagnostics/probe ──────────────────────────────
    if _RE_DIAG_PROBE.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user:
            return True
        try:
            result = _run_probe(body or {})
        except ValueError as ve:
            h._json(400, {"error": str(ve)})
            return True
        except Exception as e:
            log.warning(f"diagnostics probe failed: {e}")
            h._json(500, {"error": "probe failed"})
            return True
        try:
            db_log_audit(user, h.client_address[0], "diagnostics_probe",
                         f"type={result.get('type')} target={result.get('target')} ok={result.get('ok')}")
        except Exception:
            pass
        h._json(200, result)
        return True

    # ── POST /api/diagnostics/action/<name> ──────────────────────
    m = _RE_DIAG_ACTION.match(path)
    if m and method == "POST":
        user, _ = h._require("admin")
        if not user:
            return True
        name = m.group(1)
        try:
            result = _run_action(name)
        except ValueError as ve:
            h._json(400, {"error": str(ve)})
            return True
        except Exception as e:
            log.warning(f"diagnostics action {name} failed: {e}")
            h._json(500, {"error": "action failed"})
            return True
        try:
            db_log_audit(user, h.client_address[0], f"diagnostics_action_{name}",
                         f"ok={result.get('ok')}")
        except Exception:
            pass
        h._json(200, result)
        return True

    # ── POST /api/diagnostics/test/ntp ───────────────────────────
    if _RE_DIAG_TEST_NTP.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user:
            return True
        try:
            result = _test_ntp((body or {}).get("server"))
        except Exception as e:
            log.warning(f"diagnostics ntp test failed: {e}")
            h._json(500, {"error": "ntp test failed"})
            return True
        try:
            db_log_audit(user, h.client_address[0], "diagnostics_test_ntp",
                         f"server={result.get('server')} ok={result.get('ok')}")
        except Exception:
            pass
        h._json(200, result)
        return True

    # ── POST /api/diagnostics/test/dns ───────────────────────────
    if _RE_DIAG_TEST_DNS.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user:
            return True
        try:
            result = _test_dns((body or {}).get("host"), (body or {}).get("resolver"))
        except Exception as e:
            log.warning(f"diagnostics dns test failed: {e}")
            h._json(500, {"error": "dns test failed"})
            return True
        try:
            db_log_audit(user, h.client_address[0], "diagnostics_test_dns",
                         f"host={result.get('host')} resolver={result.get('resolver_used')} ok={result.get('ok')}")
        except Exception:
            pass
        h._json(200, result)
        return True

    # ── GET /api/diagnostics/bundle ──────────────────────────────
    if _RE_DIAG_BUNDLE.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user:
            return True
        try:
            _send_bundle(h)
        except Exception as e:
            log.warning(f"diagnostics bundle failed: {e}")
            h._json(500, {"error": "bundle failed"})
            return True
        try:
            db_log_audit(user, h.client_address[0], "diagnostics_bundle",
                         "downloaded")
        except Exception:
            pass
        return True

    return False


# ── Implementation stubs (filled in subsequent steps) ──────────────

def _get_snapshot() -> dict:
    """Compose the System Overview payload. One round-trip for the UI card."""
    import os
    import socket
    import sys
    import time
    from core import app_state
    from core.config import SYS, DB_PATH, LOGS_DB_PATH
    from db.backend import is_pg
    from db.samples import db_sample_buffer_stats
    from db import core as _dbcore

    STATE = app_state.STATE
    snap  = STATE.get_runtime_snapshot()
    buf   = db_sample_buffer_stats()

    # Log-dir size (same logic as /api/server_info)
    _log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
    log_bytes = 0
    if os.path.isdir(_log_dir):
        try:
            log_bytes = sum(
                os.path.getsize(os.path.join(_log_dir, f))
                for f in os.listdir(_log_dir)
                if os.path.isfile(os.path.join(_log_dir, f))
            )
        except OSError:
            log_bytes = 0

    # psutil is optional — surface the fact rather than 500ing.
    perf = {"available": False}
    try:
        import psutil
        cpu  = psutil.cpu_percent(interval=None)
        ram  = psutil.virtual_memory()
        _dp  = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        disk = psutil.disk_usage(_dp)
        perf = {
            "available":  True,
            "cpu_pct":    round(cpu, 1),
            "ram_pct":    round(ram.percent, 1),
            "ram_used":   ram.used,
            "ram_total":  ram.total,
            "disk_pct":   round(disk.percent, 1),
            "disk_used":  disk.used,
            "disk_total": disk.total,
        }
    except ImportError:
        pass
    except Exception as e:
        log.warning(f"snapshot psutil read failed: {e}")

    # DB on-disk size — reuse the same logic as /api/server_info for consistency.
    if is_pg():
        from db.pg_pool import pg_cursor
        _sz_q = (
            "SELECT COALESCE(SUM(pg_total_relation_size(c.oid)), 0)::bigint AS sz "
            "FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind = 'r'"
        )
        try:
            with pg_cursor("main") as _cur:
                _cur.execute(_sz_q, ("main",))
                main_sz = _cur.fetchone()["sz"]
                _cur.execute(_sz_q, ("logs",))
                logs_sz = _cur.fetchone()["sz"]
        except Exception:
            main_sz = logs_sz = 0
    else:
        main_sz = os.path.getsize(DB_PATH)      if os.path.exists(DB_PATH)      else 0
        logs_sz = os.path.getsize(LOGS_DB_PATH) if os.path.exists(LOGS_DB_PATH) else 0

    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = ""

    return {
        "version":      app_state.APP_VERSION,
        "version_name": app_state.APP_VERSION_NAME,
        # Managed-upgrade release id (version + payload hash, e.g. 1.5+424f955a55);
        # empty on a flat install. Set by bootstrap.py via PW_RELEASE.
        "build":        os.environ.get("PW_RELEASE", ""),
        "uptime_s":     int(time.time() - app_state.SERVER_START),
        "started_at":   int(app_state.SERVER_START),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform":     SYS,
        "hostname":     hostname,
        "devices":      len(STATE.devices),
        "sensors":      sum(len(d.sensors) for d in STATE.devices.values()),
        "db_backend":   "postgresql" if is_pg() else "sqlite",
        "db_size_bytes":      main_sz,
        "logs_db_size_bytes": logs_sz,
        "log_size_bytes":     log_bytes,
        "perf":         perf,
        "runtime":      {
            **snap,
            "db_writer_main_pending": _dbcore._DB_QUEUE.qsize(),
            "db_writer_logs_pending": _dbcore._LOGS_QUEUE.qsize(),
        },
        "sample_buffer": buf,
    }

def _get_db_stats() -> dict:
    """Return per-table row counts + on-disk size (PG only for per-table size).

    Tables surveyed are the ones most useful for an operator debugging growth:
    logs-schema: sensor_samples, flap_log, sensor_err_log, snmp_traps.
    main-schema: audit_log, alert_events, devices, sensors, users.
    """
    import os
    from db.backend import is_pg
    from core.config import DB_PATH, LOGS_DB_PATH

    # Each entry: (schema, table)
    _TABLES = [
        ("logs", "sensor_samples"),
        ("logs", "flap_log"),
        ("logs", "sensor_err_log"),
        ("logs", "snmp_traps"),
        ("main", "audit_log"),
        ("main", "alert_events"),
        ("main", "devices"),
        ("main", "sensors"),
        ("main", "users"),
    ]

    tables: list = []
    if is_pg():
        from db.pg_pool import pg_cursor
        for schema, tbl in _TABLES:
            try:
                with pg_cursor(schema) as cur:
                    cur.execute(f'SELECT COUNT(*)::bigint AS n FROM "{tbl}"')
                    n = int(cur.fetchone()["n"])
                    # Size query must handle both cases:
                    #   - partitioned table (sensor_samples): parent is empty,
                    #     real data lives in month-named child partitions
                    #   - regular table: just its own pg_total_relation_size
                    # Direct sum = self + children-via-pg_inherits. Works for
                    # both; pg_partition_tree returns NULL on non-partitioned
                    # tables, which is why the previous query reported 0 B.
                    cur.execute(
                        "SELECT (pg_total_relation_size(%s::regclass) + "
                        "COALESCE((SELECT SUM(pg_total_relation_size(i.inhrelid)) "
                        "          FROM pg_catalog.pg_inherits i "
                        "          WHERE i.inhparent = %s::regclass), 0))::bigint AS sz",
                        (f'{schema}.{tbl}', f'{schema}.{tbl}'),
                    )
                    row = cur.fetchone()
                    sz = int(row["sz"]) if row else 0
                tables.append({"schema": schema, "table": tbl, "rows": n, "size_bytes": sz})
            except Exception as e:
                log.warning(f"db-stats {schema}.{tbl}: {e}")
                tables.append({"schema": schema, "table": tbl, "rows": -1, "size_bytes": -1})
    else:
        import sqlite3
        main_con = sqlite3.connect(DB_PATH)
        logs_con = sqlite3.connect(LOGS_DB_PATH)
        try:
            for schema, tbl in _TABLES:
                con = logs_con if schema == "logs" else main_con
                try:
                    n = con.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
                    tables.append({"schema": schema, "table": tbl, "rows": int(n), "size_bytes": -1})
                except Exception as e:
                    log.warning(f"db-stats {schema}.{tbl}: {e}")
                    tables.append({"schema": schema, "table": tbl, "rows": -1, "size_bytes": -1})
        finally:
            main_con.close()
            logs_con.close()

    # Whole-DB size (re-used from snapshot path, kept here so the UI can show it
    # on the Database card without having to also call /snapshot).
    if is_pg():
        from db.pg_pool import pg_cursor
        _sz_q = (
            "SELECT COALESCE(SUM(pg_total_relation_size(c.oid)), 0)::bigint AS sz "
            "FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind = 'r'"
        )
        try:
            with pg_cursor("main") as _cur:
                _cur.execute(_sz_q, ("main",))
                main_sz = int(_cur.fetchone()["sz"])
                _cur.execute(_sz_q, ("logs",))
                logs_sz = int(_cur.fetchone()["sz"])
        except Exception:
            main_sz = logs_sz = 0
    else:
        main_sz = os.path.getsize(DB_PATH)      if os.path.exists(DB_PATH)      else 0
        logs_sz = os.path.getsize(LOGS_DB_PATH) if os.path.exists(LOGS_DB_PATH) else 0

    # Last VACUUM ts (app_settings stores _vacuum_last_ts if samples.py writes it;
    # otherwise 0). Kept best-effort.
    import core.settings as _cfg
    try:
        last_vacuum = int(float(_cfg.get("_vacuum_last_ts", 0) or 0))
    except (TypeError, ValueError):
        last_vacuum = 0

    return {
        "backend":         "postgresql" if is_pg() else "sqlite",
        "main_size_bytes": main_sz,
        "logs_size_bytes": logs_sz,
        "last_vacuum_ts":  last_vacuum,
        "tables":          tables,
    }

def _get_recent_errors(source: str, limit: int) -> dict:
    """Return the last `limit` error-ish entries from either the app log
    (grep ERROR out of pingwatch.log) or sensor_err_log (newest first).
    """
    if source == "sensors":
        return {"source": "sensors", "entries": _recent_sensor_errors(limit)}
    return {"source": "app", "entries": _recent_app_errors(limit)}


def _recent_app_errors(limit: int) -> list:
    """Tail pingwatch.log and keep lines at ERROR level or above."""
    import os
    from core.logger import LOG_FILES
    fpath = LOG_FILES.get("app")
    if not fpath or not os.path.isfile(fpath):
        return []
    try:
        with open(fpath, "r", encoding="utf-8", errors="replace") as _f:
            all_lines = _f.readlines()
    except OSError:
        return []
    # Cheap filter — match ERROR/CRITICAL anywhere in the line. The primary
    # log format already prefixes level; false positives are rare enough
    # that this beats pulling in the full regex parser from routes/export.py.
    out: list = []
    for raw in reversed(all_lines):
        line = raw.rstrip("\n")
        if not line:
            continue
        if (" ERROR " in line) or (" CRITICAL " in line):
            out.append(line)
            if len(out) >= limit:
                break
    return list(reversed(out))


def _recent_sensor_errors(limit: int) -> list:
    """Newest `limit` rows from sensor_err_log across all devices/sensors."""
    import sqlite3
    from db.backend import is_pg
    from core.config import LOGS_DB_PATH
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "SELECT ts, did, sid, sname, stype, msg FROM sensor_err_log "
                    "ORDER BY id DESC LIMIT %s", (int(limit),)
                )
                rows = cur.fetchall()
            return [{"ts": r["ts"], "did": r["did"], "sid": r["sid"],
                     "sname": r["sname"], "stype": r["stype"], "msg": r["msg"]}
                    for r in rows]
        except Exception as e:
            log.warning(f"recent sensor errors (pg): {e}")
            return []
    con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
    try:
        rows = con.execute(
            "SELECT ts, did, sid, sname, stype, msg FROM sensor_err_log "
            "ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [{"ts": r[0], "did": r[1], "sid": r[2],
                 "sname": r[3], "stype": r[4], "msg": r[5]}
                for r in rows]
    except Exception as e:
        log.warning(f"recent sensor errors (sqlite): {e}")
        return []
    finally:
        con.close()

def _run_probe(body: dict) -> dict:
    """Dispatch a one-off probe to monitoring.probes.* and return the raw result.

    Admin-only (enforced by caller). No SSRF restrictions — internal-target
    debugging is the entire point.
    """
    import time as _time
    ptype = (body.get("type") or "").strip().lower()
    target = (body.get("target") or "").strip()
    if not ptype:
        raise ValueError("type is required")
    if not target:
        raise ValueError("target is required")
    if len(target) > 512:
        raise ValueError("target too long")

    t0 = _time.monotonic()
    if ptype == "ping":
        from monitoring.probes import probe_ping
        timeout = _clamp(body.get("timeout"), 1, 30, 4)
        res = probe_ping(target, timeout=timeout)
    elif ptype == "tcp":
        from monitoring.probes import probe_tcp
        try:
            port = int(body.get("port"))
        except (TypeError, ValueError):
            raise ValueError("port is required for tcp probe")
        if not (1 <= port <= 65535):
            raise ValueError("port must be 1-65535")
        timeout = _clamp(body.get("timeout"), 1, 30, 5)
        res = probe_tcp(target, port, timeout=timeout)
    elif ptype == "http":
        from monitoring.probes import probe_http
        timeout = _clamp(body.get("timeout"), 1, 60, 8)
        verify_ssl = bool(body.get("verify_ssl", True))
        try:
            expected = int(body.get("expected_status") or 0)
        except (TypeError, ValueError):
            expected = 0
        res = probe_http(target, timeout=timeout, verify_ssl=verify_ssl,
                         expected_status=expected)
    elif ptype == "dns":
        from monitoring.probes import probe_dns
        query = (body.get("query") or target).strip()
        rtype = (body.get("record_type") or "A").strip().upper()
        dns_server = (body.get("dns_server") or "").strip() or None
        timeout = _clamp(body.get("timeout"), 1, 30, 5)
        res = probe_dns(target, query, record_type=rtype,
                        dns_server=dns_server, timeout=timeout)
    elif ptype == "tls":
        from monitoring.probes import probe_tls
        try:
            port = int(body.get("port") or 443)
        except (TypeError, ValueError):
            port = 443
        if not (1 <= port <= 65535):
            raise ValueError("port must be 1-65535")
        timeout = _clamp(body.get("timeout"), 1, 30, 10)
        res = probe_tls(target, port=port, timeout=timeout)
    else:
        raise ValueError(f"unsupported probe type: {ptype}")

    elapsed_ms = int((_time.monotonic() - t0) * 1000)
    out = {"type": ptype, "target": target, "elapsed_ms": elapsed_ms}
    # Flatten the probe's native dict onto the response.
    if isinstance(res, dict):
        out.update(res)
    else:
        out["raw"] = str(res)
    return out


def _clamp(v, lo: int, hi: int, default: int) -> int:
    try:
        iv = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, iv))

def _run_action(name: str) -> dict:
    """One-shot maintenance actions. Each is idempotent and safe to spam."""
    if name == "vacuum":
        return _action_vacuum()
    if name == "clear-caches":
        return _action_clear_caches()
    if name == "refresh-auth":
        return _action_refresh_auth()
    raise ValueError(f"unknown action: {name}")


def _action_vacuum() -> dict:
    """VACUUM the logs DB (biggest) + main DB. SQLite: run inline with a long
    timeout. PG: run VACUUM (ANALYZE) on the four biggest tables in each schema.
    Writes _vacuum_last_ts on success."""
    import time as _time
    from db.backend import is_pg
    t0 = _time.monotonic()
    if is_pg():
        # VACUUM cannot run inside a transaction block — use a direct
        # autocommit connection rather than the pool (which wraps everything
        # in begin/commit). Bypassing the pool is fine: VACUUM is rare and
        # short-lived, and the pool is not contended on demand.
        import psycopg2
        import core.settings as _s
        from core.config import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD
        _tbls = [("logs", "sensor_samples"), ("logs", "flap_log"),
                 ("logs", "sensor_err_log"), ("logs", "snmp_traps"),
                 ("main", "audit_log"), ("main", "alert_events")]
        try:
            con = psycopg2.connect(
                host=_s.get("pg_host", PG_HOST) or PG_HOST,
                port=int(_s.get("pg_port", PG_PORT) or PG_PORT),
                dbname=_s.get("pg_database", PG_DATABASE) or PG_DATABASE,
                user=_s.get("pg_user", PG_USER) or PG_USER,
                password=_s.get("pg_password", PG_PASSWORD) or PG_PASSWORD,
            )
            try:
                con.autocommit = True
                cur = con.cursor()
                for schema, tbl in _tbls:
                    try:
                        cur.execute(f'VACUUM (ANALYZE) "{schema}"."{tbl}"')
                    except Exception as e:
                        log.warning(f"VACUUM {schema}.{tbl} failed: {e}")
                cur.close()
            finally:
                con.close()
        except Exception as e:
            log.warning(f"VACUUM connect failed: {e}")
            return {"ok": False, "error": "vacuum connect failed"}
    else:
        import sqlite3
        from core.config import DB_PATH, LOGS_DB_PATH
        for _p in (LOGS_DB_PATH, DB_PATH):
            try:
                _vcon = sqlite3.connect(_p, timeout=60)
                _vcon.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                _vcon.execute("VACUUM")
                _vcon.close()
            except Exception as e:
                log.warning(f"VACUUM {_p} failed: {e}")

    elapsed_ms = int((_time.monotonic() - t0) * 1000)
    # Stamp the last-vacuum ts so the Diagnostics card can surface it.
    try:
        import core.settings as _cfg
        from db import db_save_settings, _db_enqueue
        ts = str(int(_time.time()))
        _cfg.load({"_vacuum_last_ts": ts})
        _db_enqueue(lambda t=ts: db_save_settings({"_vacuum_last_ts": t}))
    except Exception:
        pass
    return {"ok": True, "elapsed_ms": elapsed_ms}


def _action_clear_caches() -> dict:
    """Invalidate in-memory caches and the OIDC discovery cache."""
    import core.settings as _cfg
    # OIDC discovery cache lives in app_settings; clearing it forces a refetch
    # on next login / next refresh-auth pass.
    try:
        from db import db_save_settings, _db_enqueue
        _cfg.load({"oidc_discovery_cache": ""})
        _db_enqueue(lambda: db_save_settings({"oidc_discovery_cache": ""}))
    except Exception as e:
        log.warning(f"clear-caches: oidc cache reset failed: {e}")
    # Reload settings from DB so the in-memory cache matches persisted state.
    try:
        from db import db_load_settings
        _cfg.load(db_load_settings())
    except Exception as e:
        log.warning(f"clear-caches: settings reload failed: {e}")
    return {"ok": True}


def _action_refresh_auth() -> dict:
    """Kick the auth-health refresh loop to run immediately."""
    try:
        from core import auth_health
        auth_health.trigger_run_now()
        return {"ok": True}
    except Exception as e:
        log.warning(f"refresh-auth: {e}")
        return {"ok": False, "error": "refresh failed"}

def _test_ntp(server) -> dict:
    """SNTP v4 query (RFC 5905). Returns local-vs-server clock drift in seconds.
    Reads `ntp_server` from app_settings if caller passes no override.
    """
    import socket
    import struct
    import time as _time
    import core.settings as _s

    server = (server or _s.get("ntp_server", "") or "pool.ntp.org").strip()
    # SNTP packet: LI(2) | VN(3) | Mode(3) | plus 47 zero bytes.
    # LI=0 (no warning), VN=4, Mode=3 (client) → 0b00_100_011 = 0x23.
    packet = b"\x1b" + 47 * b"\0"
    # 1900-to-1970 offset in seconds (NTP epoch → Unix epoch).
    NTP_DELTA = 2208988800

    t0 = _time.monotonic()
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5.0)
        send_time = _time.time()
        sock.sendto(packet, (server, 123))
        data, _addr = sock.recvfrom(1024)
        recv_time = _time.time()
    except socket.gaierror:
        return {"ok": False, "server": server, "error": "DNS lookup failed"}
    except socket.timeout:
        return {"ok": False, "server": server, "error": "timeout"}
    except OSError as e:
        return {"ok": False, "server": server, "error": f"network error ({e.errno})"}
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    if len(data) < 48:
        return {"ok": False, "server": server, "error": "short response"}

    # Transmit timestamp is bytes 40..48: seconds (4) + fraction (4).
    tx_sec, tx_frac = struct.unpack("!II", data[40:48])
    if tx_sec == 0:
        return {"ok": False, "server": server, "error": "server clock invalid"}
    server_time = (tx_sec - NTP_DELTA) + (tx_frac / 2**32)

    # Stratum is byte 1; values 0/16+ are invalid / kiss-o-death.
    stratum = data[1]

    # Drift: local clock minus server time (average of send+recv for the local
    # side to cancel round-trip). Positive = local ahead of server.
    local_mid = (send_time + recv_time) / 2.0
    drift = local_mid - server_time
    latency_ms = int((_time.monotonic() - t0) * 1000)

    # Threshold buckets: ok < 5s, warn 5-60s, error > 60s or bad stratum.
    abs_d = abs(drift)
    if stratum == 0 or stratum >= 16:
        state = "error"
    elif abs_d > 60:
        state = "error"
    elif abs_d > 5:
        state = "warn"
    else:
        state = "ok"

    return {
        "ok":         state == "ok",
        "state":      state,
        "server":     server,
        "stratum":    int(stratum),
        "drift_s":    round(drift, 3),
        "latency_ms": latency_ms,
    }


def _test_dns(host, resolver) -> dict:
    """Resolve `host` (A + AAAA). If `resolver` is given, send to that server;
    otherwise use the system resolver. Reads `dns_server` and
    `diag_dns_test_host` from app_settings for defaults.
    """
    import time as _time
    import core.settings as _s

    host = (host or _s.get("diag_dns_test_host", "") or "example.com").strip()
    resolver = (resolver or _s.get("dns_server", "") or "").strip()

    t0 = _time.monotonic()
    addrs: list = []
    resolver_used = resolver or "system"

    try:
        if resolver:
            # Custom resolver — use dnspython (already a dependency).
            import dns.resolver
            r = dns.resolver.Resolver(configure=False)
            r.nameservers = [resolver]
            r.lifetime = 5.0
            r.timeout = 5.0
            for rtype in ("A", "AAAA"):
                try:
                    ans = r.resolve(host, rtype)
                    addrs.extend(str(a) for a in ans)
                except Exception:
                    # Missing AAAA is normal; missing A on a live host is a fail,
                    # but we let the empty-list check below handle both.
                    pass
        else:
            # System resolver — socket.getaddrinfo covers A + AAAA in one call.
            import socket
            try:
                infos = socket.getaddrinfo(host, None)
                seen: set = set()
                for info in infos:
                    ip = info[4][0]
                    if ip not in seen:
                        seen.add(ip)
                        addrs.append(ip)
            except socket.gaierror as e:
                return {"ok": False, "host": host, "resolver_used": resolver_used,
                        "error": f"gaierror {e.errno}"}
    except Exception as e:
        return {"ok": False, "host": host, "resolver_used": resolver_used,
                "error": "resolver error"}

    latency_ms = int((_time.monotonic() - t0) * 1000)
    if not addrs:
        return {"ok": False, "host": host, "resolver_used": resolver_used,
                "latency_ms": latency_ms, "error": "no addresses returned"}
    return {
        "ok":            True,
        "state":         "ok",
        "host":          host,
        "resolver_used": resolver_used,
        "addresses":     addrs,
        "latency_ms":    latency_ms,
    }

# Deny-list for secret-like settings keys. Any key whose lower-cased name
# contains one of these substrings has its value redacted before serialization.
_SECRET_KEY_SUBSTRINGS = (
    "_enc", "secret", "password", "_pass", "bind_pass",
    "fernet_key", "cert_pem", "key_pem", "private_key",
    "webhook_", "token", "api_key", "smtp_pass",
    "radius_secret", "client_secret",
    # Cert/metadata containers whose key name doesn't include "_pem" but whose
    # value embeds PEM blocks or base64 cert material:
    "metadata_xml",   # saml_metadata_xml — IdP metadata with inline <X509Certificate>
    "trusted_ca",     # trusted_ca_certs — CA bundle
    "csr_pem",        # tls_csr_pem, csr_pem — certificate signing requests
)

# Value-level markers that indicate a raw secret slipped through the key
# filter (self-defence). If any value starts with one of these prefixes,
# redact it even if the key looks safe.
_SECRET_VALUE_PREFIXES = (
    "-----BEGIN",      # PEM block (cert, key, csr)
    "gAAAAA",          # Fernet token prefix
    "ssh-rsa ",        # SSH public key would be fine, but a paste of
    "ssh-ed25519 ",    # private material often starts similarly; be safe
)


def _is_secret_key(key: str) -> bool:
    k = (key or "").lower()
    return any(s in k for s in _SECRET_KEY_SUBSTRINGS)


def _is_secret_value(value) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    return any(s.startswith(p) for p in _SECRET_VALUE_PREFIXES)


def _sanitize_settings(settings: dict) -> dict:
    """Return a copy of settings with secret-like keys/values redacted."""
    out: dict = {}
    for k, v in (settings or {}).items():
        if _is_secret_key(k) or _is_secret_value(v):
            out[k] = "<redacted>"
        else:
            out[k] = v
    return out


def _send_bundle(h) -> None:
    """Build a sanitized support-bundle ZIP in memory and stream it to the client."""
    import io
    import json
    import os
    import socket
    import time as _time
    import zipfile

    from core import app_state
    from core.config import SYS
    from core.logger import LOG_FILES

    # Assemble bundle contents. Per-file caps keep the download snappy even
    # on a busy box — 10MB per log is plenty for triage.
    _LOG_CAP_BYTES = 10 * 1024 * 1024

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Logs (current file only — rotated files would bloat the zip too much
        # for the common bug-report flow).
        for key, fpath in (LOG_FILES or {}).items():
            if not fpath or not os.path.isfile(fpath):
                continue
            try:
                size = os.path.getsize(fpath)
                if size <= _LOG_CAP_BYTES:
                    zf.write(fpath, arcname=f"logs/{os.path.basename(fpath)}")
                else:
                    # Tail the last _LOG_CAP_BYTES bytes — most recent events
                    # are what matter for triage.
                    with open(fpath, "rb") as _lf:
                        _lf.seek(-_LOG_CAP_BYTES, os.SEEK_END)
                        tail = _lf.read()
                    zf.writestr(f"logs/{os.path.basename(fpath)}.tail", tail)
            except OSError as e:
                zf.writestr(f"logs/{key}.error.txt", f"could not read: {e}")

        # Snapshots — reuse the same JSON the UI sees.
        try:
            zf.writestr("snapshot.json", json.dumps(_get_snapshot(), indent=2, default=str))
        except Exception as e:
            zf.writestr("snapshot.error.txt", str(e))

        try:
            zf.writestr("db_stats.json", json.dumps(_get_db_stats(), indent=2, default=str))
        except Exception as e:
            zf.writestr("db_stats.error.txt", str(e))

        try:
            zf.writestr("recent_errors.json", json.dumps({
                "app":     _recent_app_errors(200),
                "sensors": _recent_sensor_errors(200),
            }, indent=2, default=str))
        except Exception as e:
            zf.writestr("recent_errors.error.txt", str(e))

        # Sanitized settings dump.
        try:
            from db import db_load_settings
            raw = db_load_settings()
        except Exception:
            raw = {}
        sanitized = _sanitize_settings(raw)
        # Self-defence: scan the serialized JSON for leaked markers. If one
        # slips through, fail loudly rather than shipping a leaky bundle.
        _sj = json.dumps(sanitized, indent=2, default=str)
        _lower = _sj
        _markers = ("-----BEGIN", "gAAAAA")
        if any(m in _lower for m in _markers):
            # Replace aggressively and note it in the bundle.
            for m in _markers:
                _sj = _sj.replace(m, "<redacted>")
            zf.writestr("settings.warning.txt",
                        "Settings JSON contained secret markers after key-based "
                        "sanitization; values were replaced. Please file a bug.")
        zf.writestr("settings_sanitized.json", _sj)

        # Manifest — quick overview of what's inside.
        try:
            hostname = socket.gethostname()
        except Exception:
            hostname = ""
        manifest = {
            "generated_at": int(_time.time()),
            "hostname":     hostname,
            "version":      app_state.APP_VERSION,
            "version_name": app_state.APP_VERSION_NAME,
            "build":        os.environ.get("PW_RELEASE", ""),
            "platform":     SYS,
            "files": [
                "logs/pingwatch.log (or .tail if >10MB)",
                "logs/pingwatchsensors.log (or .tail)",
                "logs/pingwatchaudit.log (or .tail)",
                "logs/pingwatchbackup.log (or .tail)",
                "snapshot.json — /api/diagnostics/snapshot output",
                "db_stats.json — per-table rows/sizes",
                "recent_errors.json — last 200 app + sensor errors",
                "settings_sanitized.json — app_settings with secrets redacted",
            ],
            "redaction_policy": {
                "key_substrings":  list(_SECRET_KEY_SUBSTRINGS),
                "value_prefixes":  list(_SECRET_VALUE_PREFIXES),
            },
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    buf.seek(0)
    payload = buf.getvalue()

    # Filename: pingwatch-diag-{hostname}-{ts}.zip
    try:
        hostname = socket.gethostname() or "pingwatch"
    except Exception:
        hostname = "pingwatch"
    ts = _time.strftime("%Y%m%d-%H%M%S")
    # Sanitize hostname for Content-Disposition.
    safe_host = "".join(c if c.isalnum() or c in "-_" else "-" for c in hostname)
    filename = f"pingwatch-diag-{safe_host}-{ts}.zip"

    h.send_response(200)
    h.send_header("Content-Type", "application/zip")
    h.send_header("Content-Length", str(len(payload)))
    h.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    h.end_headers()
    h.wfile.write(payload)
