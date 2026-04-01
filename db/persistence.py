"""
db/persistence.py — Device/sensor save, load, and autosave loop.
"""

import sqlite3
import time

from core.config  import DB_PATH, LOGS_DB_PATH
from core.logger  import log
from core.state   import Device, Sensor
from db.backend   import is_pg
from db.core      import _db_enqueue, _logs_enqueue
import core.settings as _settings


def _pg_save(state):
    """Upsert all devices and sensors into PostgreSQL."""
    from db.pg_pool import pg_conn
    import psycopg2.extras

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
             int(getattr(s, "alerts_muted", False)),
             int(getattr(s, "host_override", False)),
             getattr(s, "snmp_unit", ""))
            for dev in state.devices.values()
            for s in dev.sensors.values()
        ]
        live_dids = {dev.device_id for dev in state.devices.values()}
        live_sids = {(s.device_id, s.sensor_id)
                     for dev in state.devices.values()
                     for s in dev.sensors.values()}

    try:
        with pg_conn("main") as con:
            cur = con.cursor()
            # Upsert devices
            if dev_rows:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO devices (did,name,host,grp,did_ctr,webhook_url,alerts_muted) "
                    "VALUES %s "
                    "ON CONFLICT (did) DO UPDATE SET "
                    "name=EXCLUDED.name, host=EXCLUDED.host, grp=EXCLUDED.grp, "
                    "did_ctr=EXCLUDED.did_ctr, webhook_url=EXCLUDED.webhook_url, "
                    "alerts_muted=EXCLUDED.alerts_muted",
                    dev_rows,
                )
            # Delete orphaned devices
            if live_dids:
                cur.execute(
                    "DELETE FROM devices WHERE did NOT IN %s",
                    (tuple(live_dids),)
                )
            else:
                cur.execute("DELETE FROM devices")
            # Upsert sensors
            if snr_rows:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO sensors "
                    "(did,sid,name,stype,host,port,url,interval,timeout,"
                    "verify_ssl,snmp_community,snmp_oid,snmp_version,sid_ctr,"
                    "dns_query,dns_record_type,dns_server,http_expected_status,"
                    "fail_after,recover_after,warn_ms,crit_ms,"
                    "loss_warn_pct,loss_crit_pct,keyword,keyword_case,banner_regex,"
                    "alerts_muted,host_override,snmp_unit) "
                    "VALUES %s "
                    "ON CONFLICT (did, sid) DO UPDATE SET "
                    "name=EXCLUDED.name, stype=EXCLUDED.stype, host=EXCLUDED.host, "
                    "port=EXCLUDED.port, url=EXCLUDED.url, interval=EXCLUDED.interval, "
                    "timeout=EXCLUDED.timeout, verify_ssl=EXCLUDED.verify_ssl, "
                    "snmp_community=EXCLUDED.snmp_community, snmp_oid=EXCLUDED.snmp_oid, "
                    "snmp_version=EXCLUDED.snmp_version, sid_ctr=EXCLUDED.sid_ctr, "
                    "dns_query=EXCLUDED.dns_query, dns_record_type=EXCLUDED.dns_record_type, "
                    "dns_server=EXCLUDED.dns_server, http_expected_status=EXCLUDED.http_expected_status, "
                    "fail_after=EXCLUDED.fail_after, recover_after=EXCLUDED.recover_after, "
                    "warn_ms=EXCLUDED.warn_ms, crit_ms=EXCLUDED.crit_ms, "
                    "loss_warn_pct=EXCLUDED.loss_warn_pct, loss_crit_pct=EXCLUDED.loss_crit_pct, "
                    "keyword=EXCLUDED.keyword, keyword_case=EXCLUDED.keyword_case, "
                    "banner_regex=EXCLUDED.banner_regex, alerts_muted=EXCLUDED.alerts_muted, "
                    "host_override=EXCLUDED.host_override, snmp_unit=EXCLUDED.snmp_unit",
                    snr_rows,
                )
            # Delete orphaned sensors
            if live_sids:
                cur.execute(
                    "DELETE FROM sensors WHERE did||'/'||sid NOT IN %s",
                    (tuple(f"{d}/{s}" for d, s in live_sids),)
                )
            else:
                cur.execute("DELETE FROM sensors")
            cur.close()
    except Exception as e:
        log.error(f"DB save error: {e}")


def db_save(state):
    """Upsert all devices and sensors; remove deleted rows."""
    if is_pg():
        _pg_save(state)
        return

    # ── SQLite path ──────────────────────────────────────────────────
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
             int(getattr(s, "alerts_muted", False)),
             int(getattr(s, "host_override", False)),
             getattr(s, "snmp_unit", ""))
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
            "INSERT OR REPLACE INTO sensors "
            "(did,sid,name,stype,host,port,url,interval,timeout,"
            "verify_ssl,snmp_community,snmp_oid,snmp_version,sid_ctr,"
            "dns_query,dns_record_type,dns_server,http_expected_status,"
            "fail_after,recover_after,warn_ms,crit_ms,"
            "loss_warn_pct,loss_crit_pct,keyword,keyword_case,banner_regex,"
            "alerts_muted,host_override,snmp_unit) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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


def _pg_load(state):
    """Restore devices and sensors from PostgreSQL; auto-start all sensors."""
    from db.pg_pool import pg_conn

    try:
        with pg_conn("main") as con:
            cur = con.cursor()
            cur.execute(
                "SELECT did,name,host,grp,did_ctr,webhook_url,alerts_muted FROM devices"
            )
            devs = cur.fetchall()
            cur.execute(
                "SELECT did,sid,name,stype,host,port,url,interval,timeout,"
                "verify_ssl,snmp_community,snmp_oid,snmp_version,sid_ctr,"
                "dns_query,dns_record_type,dns_server,http_expected_status,"
                "fail_after,recover_after,warn_ms,crit_ms,"
                "loss_warn_pct,loss_crit_pct,keyword,keyword_case,banner_regex,"
                "alerts_muted,host_override,COALESCE(snmp_unit,'') AS snmp_unit "
                "FROM sensors"
            )
            srows = cur.fetchall()
            cur.close()
    except Exception as e:
        log.error(f"DB load error: {e}")
        return

    log.info(f"DB load: found {len(devs)} device(s), {len(srows)} sensor(s) in PostgreSQL")
    if not devs:
        log.info("DB load: no devices in database — starting with empty state")
        return

    max_did = 0
    for row in devs:
        did, name, host, grp = row[0], row[1], row[2], row[3]
        dev = Device(did, name, host, grp)
        try:
            n = int(did.replace("d", ""))
            if n > max_did: max_did = n
        except Exception:
            pass
        dev._sid_ctr      = row[4] or 0
        dev.webhook_url   = row[5] or ""
        dev.alerts_muted  = bool(row[6] or 0)
        state.devices[did] = dev

    for row in srows:
        did = row[0]
        dev = state.devices.get(did)
        if not dev: continue
        s = Sensor(did, row[1], row[2], row[3], row[4] or dev.host,
                   port=row[5], url=row[6], interval=row[7], timeout=row[8],
                   verify_ssl=bool(row[9]), snmp_community=row[10] or "public",
                   snmp_oid=row[11] or "1.3.6.1.2.1.1.1.0",
                   snmp_version=row[12] or "2c",
                   fail_after=int(row[18] or 1), recover_after=int(row[19] or 1),
                   warn_ms=row[20], crit_ms=row[21],
                   loss_warn_pct=int(row[22] or 0),
                   loss_crit_pct=int(row[23] or 0),
                   keyword=row[24] or "", keyword_case=bool(row[25]),
                   banner_regex=row[26] or "",
                   snmp_unit=row[29] or "")
        s.dns_query            = row[14] or ""
        s.dns_record_type      = row[15] or "A"
        s.dns_server           = row[16] or ""
        s.http_expected_status = int(row[17] or 0)
        s.alerts_muted         = bool(row[27] or 0)
        s.host_override        = bool(row[28] or 0)
        dev.sensors[row[1]] = s

    state._did_ctr = max_did
    snr_total = sum(len(d.sensors) for d in state.devices.values())
    log.info(f"DB load: restored {len(state.devices)} device(s), {snr_total} sensor(s) into state")

    # ── Restore runtime state from sensor_samples ─────────────────
    _count_window = min(int(_settings.get("retention_days", 30)), 30)
    _cutoff = time.time() - _count_window * 86400
    try:
        with pg_conn("logs") as con:
            cur = con.cursor()
            for _dev in state.devices.values():
                for _s in _dev.sensors.values():
                    cur.execute(
                        "SELECT ok, ms FROM sensor_samples "
                        "WHERE did=%s AND sid=%s ORDER BY ts DESC LIMIT 80",
                        (_s.device_id, _s.sensor_id)
                    )
                    _rows = cur.fetchall()
                    if _rows:
                        for _r in reversed(_rows):
                            _s.history.append(_r[1])
                        _last = _rows[0]
                        _s.alive   = bool(_last[0])
                        _s.last_ms = _last[1]
                        cur.execute(
                            "SELECT value FROM sensor_samples "
                            "WHERE did=%s AND sid=%s ORDER BY ts DESC LIMIT 1",
                            (_s.device_id, _s.sensor_id)
                        )
                        _vrow = cur.fetchone()
                        _s.last_value = _vrow[0] if _vrow else None
                    cur.execute(
                        "SELECT COUNT(*), SUM(ok) FROM sensor_samples "
                        "WHERE did=%s AND sid=%s AND ts>=%s",
                        (_s.device_id, _s.sensor_id, _cutoff)
                    )
                    _cnt = cur.fetchone()
                    _s.total   = int(_cnt[0] or 0)
                    _s.success = int(_cnt[1] or 0)
            cur.close()
        log.info("Runtime state restored from sensor_samples.")
    except Exception as _e:
        log.error(f"DB restore runtime error: {_e}")

    for did in list(state.devices):
        state.start_device(did)
    log.info("Auto-started all sensors.")


def db_load(state):
    """Restore devices and sensors from DB; auto-start all sensors."""
    if is_pg():
        _pg_load(state)
        return

    # ── SQLite path ──────────────────────────────────────────────────
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
            "loss_warn_pct,loss_crit_pct,keyword,keyword_case,banner_regex,alerts_muted,host_override,"
            "COALESCE(snmp_unit,'') "
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
         alerts_muted, host_override, snmp_unit) in srows:
        dev = state.devices.get(did)
        if not dev: continue
        s = Sensor(did, sid, name, stype, host or dev.host,
                   port=port, url=url, interval=interval, timeout=timeout,
                   verify_ssl=bool(vssl), snmp_community=comm or "public",
                   snmp_oid=oid or "1.3.6.1.2.1.1.1.0",
                   snmp_version=sver or "2c",
                   fail_after=int(fail_after or 1), recover_after=int(recover_after or 1),
                   warn_ms=warn_ms, crit_ms=crit_ms,
                   loss_warn_pct=int(loss_warn_pct or 0),
                   loss_crit_pct=int(loss_crit_pct or 0),
                   keyword=keyword or "", keyword_case=bool(keyword_case),
                   banner_regex=banner_regex or "",
                   snmp_unit=snmp_unit or "")
        s.dns_query            = dns_query or ""
        s.dns_record_type      = dns_record_type or "A"
        s.dns_server           = dns_server or ""
        s.http_expected_status = int(http_expected_status or 0)
        s.alerts_muted         = bool(alerts_muted or 0)
        s.host_override        = bool(host_override or 0)
        dev.sensors[sid] = s

    state._did_ctr = max_did
    snr_total = sum(len(d.sensors) for d in state.devices.values())
    log.info(f"DB load: restored {len(state.devices)} device(s), {snr_total} sensor(s) into state")

    # ── Restore runtime state from sensor_samples ─────────────────
    _rcon = None
    try:
        _count_window = min(int(_settings.get("retention_days", 30)), 30)
        _cutoff = time.time() - _count_window * 86400
        _rcon = sqlite3.connect(f"file:{LOGS_DB_PATH}?mode=ro", uri=True)

        for _dev in state.devices.values():
            for _s in _dev.sensors.values():
                _rows = _rcon.execute(
                    "SELECT ok, ms FROM sensor_samples "
                    "WHERE did=? AND sid=? ORDER BY ts DESC LIMIT 80",
                    (_s.device_id, _s.sensor_id)
                ).fetchall()
                if _rows:
                    for _ok, _ms in reversed(_rows):
                        _s.history.append(_ms)
                    _last = _rows[0]
                    _s.alive   = bool(_last[0])
                    _s.last_ms = _last[1]
                    _vrow = _rcon.execute(
                        "SELECT value FROM sensor_samples "
                        "WHERE did=? AND sid=? ORDER BY ts DESC LIMIT 1",
                        (_s.device_id, _s.sensor_id)
                    ).fetchone()
                    _s.last_value = _vrow[0] if _vrow else None
                _cnt = _rcon.execute(
                    "SELECT COUNT(*), SUM(ok) FROM sensor_samples "
                    "WHERE did=? AND sid=? AND ts>=?",
                    (_s.device_id, _s.sensor_id, _cutoff)
                ).fetchone()
                _s.total   = int(_cnt[0] or 0)
                _s.success = int(_cnt[1] or 0)
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
    import time as _time
    from db.samples import db_clean_samples
    from db.users   import db_load_settings
    _iter = 0
    while True:
        _time.sleep(60)
        _db_enqueue(lambda: db_save(state))
        _iter += 1
        if _iter % 60 == 0:    # every ~hour
            _days = db_load_settings().get("retention_days", 365)
            _d = _days
            _logs_enqueue(lambda d=_d: db_clean_samples(d))
