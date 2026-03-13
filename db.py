"""
db.py — SQLite persistence: init, seed, save/load, log helpers,
        write-queue, and autosave loop.
"""

import queue
import sqlite3
import threading
import time

from auth import _hash_pw, _SESSIONS, _SESSIONS_LOCK
from config import DB_PATH
from logger import log, log_audit
from state import Device, Sensor
import settings as _settings

# ── Single-writer queue ───────────────────────────────────────────
_DB_QUEUE: queue.Queue = queue.Queue()


def _db_writer_loop():
    """Drain _DB_QUEUE and execute each callable sequentially."""
    while True:
        fn = _DB_QUEUE.get()
        if fn is None:   # sentinel for clean shutdown (not yet used)
            break
        try:
            fn()
        except Exception as e:
            log.error(f"DB writer error: {e}")


threading.Thread(target=_db_writer_loop, daemon=True).start()


def _db_enqueue(fn):
    """Queue a zero-argument callable for the single DB writer thread."""
    _DB_QUEUE.put(fn)


# ── Sample write buffer (batches per-probe inserts) ───────────────
_SAMPLE_BUF: list       = []
_SAMPLE_BUF_LOCK        = threading.Lock()


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


# ── Schema init ──────────────────────────────────────────────────

def db_init():
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("PRAGMA journal_mode=WAL")   # safe concurrent reads while probes write
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                pw_hash  TEXT NOT NULL,
                role     TEXT DEFAULT 'admin'
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token    TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires  REAL NOT NULL
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                did TEXT PRIMARY KEY, name TEXT, host TEXT,
                grp TEXT, did_ctr INTEGER DEFAULT 0
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS sensors (
                did TEXT, sid TEXT, name TEXT, stype TEXT,
                host TEXT, port INTEGER, url TEXT,
                interval INTEGER, timeout INTEGER,
                verify_ssl INTEGER DEFAULT 1,
                snmp_community TEXT DEFAULT 'public',
                snmp_oid TEXT DEFAULT '1.3.6.1.2.1.1.1.0',
                snmp_version TEXT DEFAULT '2c',
                sid_ctr INTEGER DEFAULT 0,
                dns_query TEXT DEFAULT '',
                dns_record_type TEXT DEFAULT 'A',
                dns_server TEXT DEFAULT '',
                PRIMARY KEY (did, sid)
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS flap_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT,
                did       TEXT,
                sid       TEXT,
                dname     TEXT,
                sname     TEXT,
                host      TEXT,
                stype     TEXT,
                detail    TEXT,
                direction TEXT DEFAULT 'down'
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS sensor_err_log (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT,
                did     TEXT,
                sid     TEXT,
                sname   TEXT,
                stype   TEXT,
                msg     TEXT
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS sensor_samples (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                ts    REAL    NOT NULL,
                did   TEXT    NOT NULL,
                sid   TEXT    NOT NULL,
                ok    INTEGER NOT NULL,
                ms    REAL,
                value TEXT
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_ds "
            "ON sensor_samples(did, sid, ts)"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS snmp_traps (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT,
                src_ip    TEXT,
                dname     TEXT,
                community TEXT,
                trap_oid  TEXT,
                detail    TEXT
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                ts     REAL    NOT NULL,
                actor  TEXT    NOT NULL,
                ip     TEXT    NOT NULL,
                action TEXT    NOT NULL,
                target TEXT    DEFAULT '',
                detail TEXT    DEFAULT ''
            )""")
        con.commit()
        # Seed defaults in app_settings if not present
        for _k, _v in [
            ("session_ttl",        "86400"),
            ("retention_days",     "365"),
            ("snr_interval",       "5"),
            ("snr_timeout",        "4"),
            ("snr_fail_after",     "1"),
            ("snr_recover_after",  "1"),
            ("max_flaps_display",  "20"),
            ("max_flap_entries",   "500"),
            ("max_trap_entries",   "500"),
            ("login_fail_max",     "5"),
            ("login_fail_window",  "60"),
            ("org_name",           ""),
            ("latency_good_ms",    "100"),
            ("latency_warn_ms",    "300"),
        ]:
            if not con.execute("SELECT 1 FROM app_settings WHERE key=?", (_k,)).fetchone():
                con.execute("INSERT INTO app_settings VALUES (?,?)", (_k, _v))
        con.commit()
        # Migrate: bump retention_days from old default 7 → 365 (only if user never changed it)
        con.execute("UPDATE app_settings SET value='365' WHERE key='retention_days' AND value='7'")
        con.commit()
        # Migrations — add columns when missing (safe to run on existing DBs)
        # users table
        try:
            con.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'admin'")
            con.commit()
        except Exception:
            pass
        # devices table
        try:
            con.execute("ALTER TABLE devices ADD COLUMN did_ctr INTEGER DEFAULT 0")
            con.commit()
        except Exception:
            pass
        # sensors table — dns fields
        for col, default in [("dns_query", "''"), ("dns_record_type", "'A'"), ("dns_server", "''")]:
            try:
                con.execute(f"ALTER TABLE sensors ADD COLUMN {col} TEXT DEFAULT {default}")
                con.commit()
            except Exception:
                pass
        for stmt in [
            "ALTER TABLE devices ADD COLUMN webhook_url TEXT DEFAULT ''",
            "ALTER TABLE sensors ADD COLUMN http_expected_status INTEGER DEFAULT 0",
            "ALTER TABLE flap_log ADD COLUMN direction TEXT DEFAULT 'down'",
        ]:
            try:
                con.execute(stmt)
                con.commit()
            except Exception:
                pass
        # New sensor columns (debounce, thresholds, new probe fields)
        for col_def in [
            "fail_after    INTEGER DEFAULT 1",
            "recover_after INTEGER DEFAULT 1",
            "warn_ms       INTEGER",
            "crit_ms       INTEGER",
            "loss_warn_pct INTEGER DEFAULT 0",
            "loss_crit_pct INTEGER DEFAULT 0",
            "keyword       TEXT DEFAULT ''",
            "keyword_case  INTEGER DEFAULT 0",
            "banner_regex  TEXT DEFAULT ''",
        ]:
            try:
                con.execute(f"ALTER TABLE sensors ADD COLUMN {col_def}")
                con.commit()
            except Exception:
                pass
        # alerts_muted — disable alerts per sensor / device
        for stmt in [
            "ALTER TABLE sensors ADD COLUMN alerts_muted INTEGER DEFAULT 0",
            "ALTER TABLE devices ADD COLUMN alerts_muted INTEGER DEFAULT 0",
        ]:
            try:
                con.execute(stmt)
                con.commit()
            except Exception:
                pass
    finally:
        con.close()
    log.info("DB init: schema ready")


def db_seed_users():
    """Seed default admin user if not present; preload live sessions into memory."""
    import secrets as _sec
    con = sqlite3.connect(DB_PATH)
    try:
        if not con.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
            pw = _sec.token_urlsafe(9)
            con.execute("INSERT INTO users VALUES (?,?,?)", ("admin", _hash_pw(pw), "admin"))
            con.commit()
            log.warning("=" * 51)
            log.warning(f"  Default admin password: {pw}")
            log.warning("  Change it in Settings → Users → Reset Password")
            log.warning("=" * 51)
        rows = con.execute(
            "SELECT s.token, s.username, s.expires, COALESCE(u.role, 'admin') "
            "FROM sessions s LEFT JOIN users u ON s.username = u.username "
            "WHERE s.expires > ?",
            (time.time(),)
        ).fetchall()
        with _SESSIONS_LOCK:
            for token, username, expires, role in rows:
                _SESSIONS[token] = {"username": username, "expires": expires, "role": role}
        if rows:
            log.info(f"DB seed: restored {len(rows)} active session(s) into memory")
    finally:
        con.close()


# ── Error log ────────────────────────────────────────────────────

def db_log_err(did, sid, sname, stype, msg, ts):
    """Append a sensor error entry; keep at most 1 000 per sensor."""
    con = None
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        con.execute(
            "INSERT INTO sensor_err_log (ts,did,sid,sname,stype,msg) VALUES (?,?,?,?,?,?)",
            (ts, did, sid, sname, stype, msg)
        )
        con.execute("""
            DELETE FROM sensor_err_log WHERE did=? AND sid=?
              AND id NOT IN (
                SELECT id FROM sensor_err_log WHERE did=? AND sid=?
                ORDER BY id DESC LIMIT 1000
              )""", (did, sid, did, sid))
        con.commit()
    except Exception as e:
        log.error(f"DB err log error: {e}")
    finally:
        if con:
            con.close()


def db_load_err_logs(did):
    """Return last 200 error entries for a device's sensors, newest first."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT ts,did,sid,sname,stype,msg FROM sensor_err_log "
            "WHERE did=? ORDER BY id DESC LIMIT 200", (did,)
        ).fetchall()
        con.close()
        return [{"ts": r[0], "did": r[1], "sid": r[2],
                 "sname": r[3], "stype": r[4], "msg": r[5], "type": "err"}
                for r in rows]
    except Exception as e:
        log.error(f"DB load err logs error: {e}")
        return []


def db_clear_err_logs(did):
    """Delete all sensor error logs for a device."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM sensor_err_log WHERE did=?", (did,))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"DB clear err logs error: {e}")


def db_clear_sensor_err_logs(did, sid):
    """Delete all sensor error logs for a specific sensor."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM sensor_err_log WHERE did=? AND sid=?", (did, sid))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"DB clear sensor err logs error: {e}")


def db_clear_device_traps(src_ip):
    """Delete all SNMP traps from a device (matched by src_ip / host)."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM snmp_traps WHERE src_ip=?", (src_ip,))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"DB clear device traps error: {e}")


# ── Flap log ─────────────────────────────────────────────────────

def db_log_flap(flap):
    """Append a flap/recovery event; keep at most 500 total."""
    con = None
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        con.execute(
            "INSERT INTO flap_log (ts,did,sid,dname,sname,host,stype,detail,direction) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (flap["ts"], flap["did"], flap["sid"], flap.get("dname", ""),
             flap.get("sname", ""), flap.get("host", ""),
             flap.get("stype", ""), flap.get("detail", ""),
             flap.get("direction", "down"))
        )
        _flap_limit = max(50, int(_settings.get('max_flap_entries', 500)))
        con.execute(
            "DELETE FROM flap_log WHERE id NOT IN "
            "(SELECT id FROM flap_log ORDER BY id DESC LIMIT ?)",
            (_flap_limit,)
        )
        con.commit()
    except Exception as e:
        log.error(f"DB flap log error: {e}")
    finally:
        if con:
            con.close()


def db_load_flaps():
    """Return last 500 flap/recovery events, newest first."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT ts,did,sid,dname,sname,host,stype,detail,direction "
            "FROM flap_log ORDER BY id DESC LIMIT 500"
        ).fetchall()
        con.close()
        return [{"ts": r[0], "did": r[1], "sid": r[2], "dname": r[3],
                 "sname": r[4], "host": r[5], "stype": r[6], "detail": r[7],
                 "direction": r[8] or "down"}
                for r in rows]
    except Exception as e:
        log.error(f"DB load flaps error: {e}")
        return []


# ── SNMP trap log ────────────────────────────────────────────────

def db_log_trap(t):
    """Append one SNMP trap; keep at most 500."""
    con = None
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        con.execute(
            "INSERT INTO snmp_traps (ts,src_ip,dname,community,trap_oid,detail) "
            "VALUES (?,?,?,?,?,?)",
            (t.get("ts", ""), t.get("src_ip", ""), t.get("dname", ""),
             t.get("community", ""), t.get("trap_oid", ""), t.get("detail", ""))
        )
        _trap_limit = max(50, int(_settings.get('max_trap_entries', 500)))
        con.execute(
            "DELETE FROM snmp_traps WHERE id NOT IN "
            "(SELECT id FROM snmp_traps ORDER BY id DESC LIMIT ?)",
            (_trap_limit,)
        )
        con.commit()
    except Exception as e:
        log.error(f"DB trap log error: {e}")
    finally:
        if con:
            con.close()


def db_load_traps():
    """Return last 500 SNMP traps, newest first."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT ts,src_ip,dname,community,trap_oid,detail "
            "FROM snmp_traps ORDER BY id DESC LIMIT 500"
        ).fetchall()
        con.close()
        return [{"ts": r[0], "src_ip": r[1], "dname": r[2],
                 "community": r[3], "trap_oid": r[4], "detail": r[5],
                 "_direction": "trap"}
                for r in rows]
    except Exception as e:
        log.error(f"DB load traps error: {e}")
        return []


# ── Device / sensor persistence ──────────────────────────────────

def db_save(state):
    """Upsert all devices and sensors; remove deleted rows."""
    # Snapshot under lock — no I/O while holding it
    with state._lock:
        dev_rows = [
            (dev.device_id, dev.name, dev.host, dev.group, dev._sid_ctr,
             getattr(dev, "webhook_url", ""),
             int(getattr(dev, "alerts_muted", False)))
            for dev in state.devices.values()
        ]
        snr_rows = [
            (s.device_id, s.sensor_id, s.name, s.stype,
             s.host, s.port, s.url, s.interval, s.timeout,
             int(s.verify_ssl), s.snmp_community,
             s.snmp_oid, s.snmp_version, dev._sid_ctr,
             s.dns_query, s.dns_record_type, s.dns_server,
             getattr(s, "http_expected_status", 0),
             getattr(s, "fail_after", 1), getattr(s, "recover_after", 1),
             getattr(s, "warn_ms", None), getattr(s, "crit_ms", None),
             getattr(s, "loss_warn_pct", 0), getattr(s, "loss_crit_pct", 0),
             getattr(s, "keyword", ""), int(getattr(s, "keyword_case", False)),
             getattr(s, "banner_regex", ""),
             int(getattr(s, "alerts_muted", False)))
            for dev in state.devices.values()
            for s in dev.sensors.values()
        ]
        live_dids = {dev.device_id for dev in state.devices.values()}
        live_sids = {(s.device_id, s.sensor_id)
                     for dev in state.devices.values()
                     for s in dev.sensors.values()}

    con = None
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        cur = con.cursor()
        cur.executemany("INSERT OR REPLACE INTO devices VALUES (?,?,?,?,?,?,?)", dev_rows)
        if live_dids:
            cur.execute(
                f"DELETE FROM devices WHERE did NOT IN ({','.join('?'*len(live_dids))})",
                list(live_dids)
            )
        else:
            cur.execute("DELETE FROM devices")
        cur.executemany(
            "INSERT OR REPLACE INTO sensors VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            snr_rows
        )
        if live_sids:
            cur.execute(
                "DELETE FROM sensors WHERE did||'/'||sid NOT IN ({})".format(
                    ",".join("?" * len(live_sids))
                ),
                [f"{d}/{s}" for d, s in live_sids]
            )
        else:
            cur.execute("DELETE FROM sensors")
        con.commit()
    except Exception as e:
        log.error(f"DB save error: {e}")
    finally:
        if con:
            con.close()


def db_load(state):
    """Restore devices and sensors from SQLite; auto-start all sensors."""
    con = None
    try:
        con = sqlite3.connect(DB_PATH)
        devs = con.execute(
            "SELECT did,name,host,grp,did_ctr,webhook_url,alerts_muted FROM devices"
        ).fetchall()
        srows = con.execute(
            "SELECT did,sid,name,stype,host,port,url,interval,timeout,"
            "verify_ssl,snmp_community,snmp_oid,snmp_version,sid_ctr,"
            "dns_query,dns_record_type,dns_server,http_expected_status,"
            "fail_after,recover_after,warn_ms,crit_ms,"
            "loss_warn_pct,loss_crit_pct,keyword,keyword_case,banner_regex,alerts_muted "
            "FROM sensors"
        ).fetchall()
    except Exception as e:
        log.error(f"DB load error: {e}")
        return
    finally:
        if con:
            con.close()

    log.info(f"DB load: found {len(devs)} device(s), {len(srows)} sensor(s) in {DB_PATH}")
    if not devs:
        log.info("DB load: no devices in database — starting with empty state")
        return

    max_did = 0
    for (did, name, host, grp, sid_ctr, webhook_url, alerts_muted) in devs:
        dev = Device(did, name, host, grp)
        try:
            n = int(did.replace("d", ""))
            if n > max_did: max_did = n
        except Exception:
            pass
        dev._sid_ctr      = sid_ctr or 0
        dev.webhook_url   = webhook_url or ""
        dev.alerts_muted  = bool(alerts_muted or 0)
        state.devices[did] = dev

    for (did, sid, name, stype, host, port, url, interval, timeout,
         vssl, comm, oid, sver, sid_ctr,
         dns_query, dns_record_type, dns_server, http_expected_status,
         fail_after, recover_after, warn_ms, crit_ms,
         loss_warn_pct, loss_crit_pct, keyword, keyword_case, banner_regex,
         alerts_muted) in srows:
        dev = state.devices.get(did)
        if not dev: continue
        s = Sensor(did, sid, name, stype, host,
                   port=port, url=url, interval=interval, timeout=timeout,
                   verify_ssl=bool(vssl), snmp_community=comm or "public",
                   snmp_oid=oid or "1.3.6.1.2.1.1.1.0",
                   snmp_version=sver or "2c",
                   fail_after=int(fail_after or 1), recover_after=int(recover_after or 1),
                   warn_ms=warn_ms, crit_ms=crit_ms,
                   loss_warn_pct=int(loss_warn_pct or 0),
                   loss_crit_pct=int(loss_crit_pct or 0),
                   keyword=keyword or "", keyword_case=bool(keyword_case),
                   banner_regex=banner_regex or "")
        s.dns_query            = dns_query or ""
        s.dns_record_type      = dns_record_type or "A"
        s.dns_server           = dns_server or ""
        s.http_expected_status = int(http_expected_status or 0)
        s.alerts_muted         = bool(alerts_muted or 0)
        dev.sensors[sid] = s

    state._did_ctr = max_did
    snr_total = sum(len(d.sensors) for d in state.devices.values())
    log.info(f"DB load: restored {len(state.devices)} device(s), {snr_total} sensor(s) into state")

    # ── Restore runtime state from sensor_samples ─────────────────
    # Populates history sparkline, alive status, last_ms/value, and
    # total/success counters so the dashboard is live immediately on restart.
    _rcon = None
    try:
        _cutoff = time.time() - int(_settings.get("retention_days", 7)) * 86400
        # Open read-only to avoid triggering a WAL checkpoint on close
        _rcon = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

        # ── Batch-fetch all counts in one query ────────────────────
        _count_map = {}  # (did, sid) -> (total, success)
        for _row in _rcon.execute(
            "SELECT did, sid, COUNT(*), SUM(ok) FROM sensor_samples "
            "WHERE ts>=? GROUP BY did, sid", (_cutoff,)
        ).fetchall():
            _count_map[(_row[0], _row[1])] = (int(_row[2] or 0), int(_row[3] or 0))

        for _dev in state.devices.values():
            for _s in _dev.sensors.values():
                # Last 80 samples newest-first → reverse for history deque
                _rows = _rcon.execute(
                    "SELECT ok, ms, value FROM sensor_samples "
                    "WHERE did=? AND sid=? ORDER BY ts DESC LIMIT 80",
                    (_s.device_id, _s.sensor_id)
                ).fetchall()
                if _rows:
                    for _ok, _ms, _val in reversed(_rows):
                        _s.history.append(_ms)
                    _last = _rows[0]
                    _s.alive      = bool(_last[0])
                    _s.last_ms    = _last[1]
                    _s.last_value = _last[2]
                _s.total, _s.success = _count_map.get(
                    (_s.device_id, _s.sensor_id), (0, 0))
        log.info("Runtime state restored from sensor_samples.")
    except Exception as _e:
        log.error(f"DB restore runtime error: {_e}")
    finally:
        if _rcon:
            try: _rcon.close()
            except Exception: pass

    for did in list(state.devices):
        state.start_device(did)
    log.info("Auto-started all sensors.")


# ── Background autosave ──────────────────────────────────────────

def autosave_loop(state):
    """Save state to DB every 60 s; clean old samples every ~1 hour."""
    _iter = 0
    while True:
        time.sleep(60)
        _db_enqueue(lambda: db_save(state))
        _iter += 1
        if _iter % 60 == 0:    # every ~hour
            _days = db_load_settings().get("retention_days", 365)
            _d = _days
            _db_enqueue(lambda d=_d: db_clean_samples(d))


# ── Time-series sample log ────────────────────────────────────────

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


# ── App settings ─────────────────────────────────────────────────

def db_load_settings() -> dict:
    """Return all app_settings rows as a plain dict (values cast to int where numeric)."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT key, value FROM app_settings").fetchall()
        con.close()
        result = {}
        for k, v in rows:
            try:
                result[k] = int(v)
            except (ValueError, TypeError):
                result[k] = v
        return result
    except Exception as e:
        log.error(f"DB load settings error: {e}")
        return {}


def db_save_settings(d: dict):
    """Upsert a dict of settings into app_settings."""
    try:
        con = sqlite3.connect(DB_PATH)
        for k, v in d.items():
            con.execute("INSERT OR REPLACE INTO app_settings VALUES (?,?)", (k, str(v)))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"DB save settings error: {e}")


# ── User management ───────────────────────────────────────────────

def db_list_users() -> list:
    """Return all users as [{username, role}]."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT username, role FROM users ORDER BY username").fetchall()
        con.close()
        return [{"username": r[0], "role": r[1]} for r in rows]
    except Exception as e:
        log.error(f"DB list users error: {e}")
        return []


def db_add_user(username: str, password: str, role: str = "admin") -> bool:
    """Insert a new user. Returns False if username already exists."""
    from auth import _hash_pw
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("INSERT INTO users VALUES (?,?,?)", (username, _hash_pw(password), role))
        con.commit()
        con.close()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        log.error(f"DB add user error: {e}")
        return False


def db_delete_user(username: str) -> bool:
    """Delete a user. Returns False if not found."""
    try:
        con = sqlite3.connect(DB_PATH)
        cur = con.execute("DELETE FROM users WHERE username=?", (username,))
        con.commit()
        con.close()
        return cur.rowcount > 0
    except Exception as e:
        log.error(f"DB delete user error: {e}")
        return False


def db_set_password(username: str, password: str):
    """Update the password hash for an existing user."""
    from auth import _hash_pw
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("UPDATE users SET pw_hash=? WHERE username=?",
                    (_hash_pw(password), username))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"DB set password error: {e}")


def db_log_audit(actor: str, ip: str, action: str, target: str = '', detail: str = ''):
    """Append one audit entry; trim to last 2000 rows."""
    _t = f" → {target}" if target else ""
    _d = f" | {detail}" if detail else ""
    log_audit.info(f"{actor} [{ip}] {action}{_t}{_d}")
    def _write():
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT INTO audit_log(ts,actor,ip,action,target,detail) VALUES(?,?,?,?,?,?)",
            (time.time(), actor, ip, action, target, detail)
        )
        con.execute("DELETE FROM audit_log WHERE id NOT IN "
                    "(SELECT id FROM audit_log ORDER BY ts DESC LIMIT 2000)")
        con.commit()
        con.close()
    _db_enqueue(_write)


def db_get_audit(limit: int = 200) -> list:
    """Return newest-first audit entries."""
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT ts,actor,ip,action,target,detail FROM audit_log "
            "ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        con.close()
        return [{"ts": r[0], "actor": r[1], "ip": r[2],
                 "action": r[3], "target": r[4], "detail": r[5]} for r in rows]
    except Exception:
        return []
