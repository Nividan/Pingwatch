"""
db/persistence.py — Device/sensor save, load, and autosave loop.
"""

import json
import sqlite3
import threading
import time

from core.config  import DB_PATH, LOGS_DB_PATH
from core.logger  import log
from core.state   import Device, Sensor
from db.backend   import is_pg
from db.core      import _db_enqueue, _logs_enqueue
import core.settings as _settings

# Set True only after a load pass completes without error. db_save() refuses
# to run until then: its "delete every row not present in memory" semantics
# would otherwise wipe the whole device/sensor configuration on the first
# autosave after a transiently failed load (PG blip at boot, SQLite file
# locked past its timeout, …).
_LOAD_OK = False


def _mark_load_ok():
    global _LOAD_OK
    _LOAD_OK = True


def _int_or_none(v):
    """Coerce a value to int or None. Accepts None, '' → None. Everything else
    goes through int(). Guards the save path from stray empty-string integers
    that made it onto a Sensor attribute (PG rejects '' for INTEGER columns).
    """
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _normalize_pp_shape(raw) -> dict:
    """Coerce parent_device_ports into the canonical list-of-pairs shape.

    Accepts the legacy single-dict shape `{pid: {lport, rport}}` (pre-LACP
    support) and the new shape `{pid: [{lport, rport}, ...]}`. Always returns
    the list shape so every downstream consumer can iterate uniformly.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for pid, val in raw.items():
        if not isinstance(pid, str) or not pid:
            continue
        if isinstance(val, list):
            pairs = [p for p in val if isinstance(p, dict)]
        elif isinstance(val, dict):
            pairs = [val]
        else:
            continue
        clean = []
        for p in pairs:
            l = p.get("lport", "") if isinstance(p.get("lport", ""), str) else ""
            r = p.get("rport", "") if isinstance(p.get("rport", ""), str) else ""
            if l or r:
                clean.append({"lport": l, "rport": r})
        if clean:
            out[pid] = clean
    return out


def _pg_save(state):
    """Upsert all devices and sensors into PostgreSQL."""
    from db.pg_pool import pg_conn
    import psycopg2.extras

    # Snapshot under lock — no I/O while holding it
    with state._lock:
        dev_rows = [
            (dev.device_id, dev.name, dev.host, dev.group,
             getattr(dev, "site", ""),
             dev._sid_ctr,
             getattr(dev, "webhook_url", ""),
             int(getattr(dev, "alerts_muted", False)),
             getattr(dev, "snmp_community_default", ""),
             getattr(dev, "snmp_version_default", ""),
             getattr(dev, "vmware_user_default", ""),
             getattr(dev, "vmware_password_default", ""),
             json.dumps(getattr(dev, "secondary_ips", []) or []),
             getattr(dev, "external_id", None),
             float(getattr(dev, "discovered_at", 0) or 0),
             getattr(dev, "discovered_from_cidr", "") or "",
             getattr(dev, "snmp_v3_user_default", ""),
             getattr(dev, "snmp_v3_level_default", ""),
             getattr(dev, "snmp_v3_auth_proto_default", ""),
             getattr(dev, "snmp_v3_auth_pass_default", ""),
             getattr(dev, "snmp_v3_priv_proto_default", ""),
             getattr(dev, "snmp_v3_priv_pass_default", ""),
             getattr(dev, "snmp_v3_context_default", ""),
             json.dumps(getattr(dev, "parent_device_ids", []) or []),
             json.dumps(getattr(dev, "parent_device_ports", {}) or {}),
             getattr(dev, "probe_id", "") or "")
            for dev in state.devices.values()
        ]
        snr_rows = [
            (s.device_id, s.sensor_id, s.name, s.stype,
             s.host, _int_or_none(s.port), s.url,
             _int_or_none(s.interval), _int_or_none(s.timeout),
             int(s.verify_ssl), s.snmp_community,
             s.snmp_oid, s.snmp_version, dev._sid_ctr,
             s.dns_query, s.dns_record_type, s.dns_server,
             _int_or_none(getattr(s, "http_expected_status", 0)) or 0,
             _int_or_none(getattr(s, "fail_after", 2)) or 2,
             _int_or_none(getattr(s, "recover_after", 1)) or 1,
             _int_or_none(getattr(s, "warn_ms", None)),
             _int_or_none(getattr(s, "crit_ms", None)),
             _int_or_none(getattr(s, "loss_warn_pct", 0)) or 0,
             _int_or_none(getattr(s, "loss_crit_pct", 0)) or 0,
             getattr(s, "keyword", ""), int(getattr(s, "keyword_case", False)),
             getattr(s, "banner_regex", ""),
             int(getattr(s, "alerts_muted", False)),
             int(getattr(s, "host_override", False)),
             getattr(s, "snmp_unit", ""),
             getattr(s, "vmware_user", ""),
             getattr(s, "vmware_password", ""),
             getattr(s, "vmware_vm_id", ""),
             getattr(s, "vmware_vm_name", ""),
             getattr(s, "vmware_metric", ""),
             int(getattr(s, "anomaly_enabled", 0) or 0),
             int(getattr(s, "anomaly_sensitivity", 2) or 2),
             int(getattr(s, "anomaly_min_samples", 50) or 50),
             getattr(s, "smtp_tls", "none") or "none",
             getattr(s, "smtp_user", ""),
             getattr(s, "smtp_password", ""),
             getattr(s, "smtp_from", ""),
             getattr(s, "smtp_rcpt", ""),
             getattr(s, "smtp_test_level", "ehlo") or "ehlo",
             getattr(s, "ssh_user", ""),
             getattr(s, "ssh_password", ""),
             getattr(s, "ssh_private_key", ""),
             getattr(s, "ssh_auth_type", "password") or "password",
             getattr(s, "ssh_test_level", "banner") or "banner",
             getattr(s, "sftp_user", ""),
             getattr(s, "sftp_password", ""),
             getattr(s, "sftp_private_key", ""),
             getattr(s, "sftp_auth_type", "password") or "password",
             getattr(s, "sftp_test_level", "open") or "open",
             getattr(s, "sftp_remote_path", ""),
             getattr(s, "sftp_expected_sha256", ""),
             getattr(s, "radius_secret", ""),
             getattr(s, "radius_test_level", "reachable") or "reachable",
             getattr(s, "radius_username", ""),
             getattr(s, "radius_password", ""),
             getattr(s, "radius_nas_id", ""),
             getattr(s, "snmp_v3_user", ""),
             getattr(s, "snmp_v3_level", ""),
             getattr(s, "snmp_v3_auth_proto", ""),
             getattr(s, "snmp_v3_auth_pass", ""),
             getattr(s, "snmp_v3_priv_proto", ""),
             getattr(s, "snmp_v3_priv_pass", ""),
             getattr(s, "snmp_v3_context", ""),
             getattr(s, "probe_id", "") or "")
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
                    "INSERT INTO devices (did,name,host,grp,site,did_ctr,webhook_url,alerts_muted,"
                    "snmp_community_default,snmp_version_default,vmware_user_default,"
                    "vmware_password_default,secondary_ips,external_id,"
                    "discovered_at,discovered_from_cidr,"
                    "snmp_v3_user_default,snmp_v3_level_default,"
                    "snmp_v3_auth_proto_default,snmp_v3_auth_pass_default,"
                    "snmp_v3_priv_proto_default,snmp_v3_priv_pass_default,"
                    "snmp_v3_context_default,parent_device_ids,parent_device_ports,probe_id) "
                    "VALUES %s "
                    "ON CONFLICT (did) DO UPDATE SET "
                    "name=EXCLUDED.name, host=EXCLUDED.host, grp=EXCLUDED.grp, site=EXCLUDED.site, "
                    "did_ctr=EXCLUDED.did_ctr, webhook_url=EXCLUDED.webhook_url, "
                    "alerts_muted=EXCLUDED.alerts_muted, "
                    "snmp_community_default=EXCLUDED.snmp_community_default, "
                    "snmp_version_default=EXCLUDED.snmp_version_default, "
                    "vmware_user_default=EXCLUDED.vmware_user_default, "
                    "vmware_password_default=EXCLUDED.vmware_password_default, "
                    "secondary_ips=EXCLUDED.secondary_ips, "
                    "external_id=EXCLUDED.external_id, "
                    "discovered_at=EXCLUDED.discovered_at, "
                    "discovered_from_cidr=EXCLUDED.discovered_from_cidr, "
                    "snmp_v3_user_default=EXCLUDED.snmp_v3_user_default, "
                    "snmp_v3_level_default=EXCLUDED.snmp_v3_level_default, "
                    "snmp_v3_auth_proto_default=EXCLUDED.snmp_v3_auth_proto_default, "
                    "snmp_v3_auth_pass_default=EXCLUDED.snmp_v3_auth_pass_default, "
                    "snmp_v3_priv_proto_default=EXCLUDED.snmp_v3_priv_proto_default, "
                    "snmp_v3_priv_pass_default=EXCLUDED.snmp_v3_priv_pass_default, "
                    "snmp_v3_context_default=EXCLUDED.snmp_v3_context_default, "
                    "parent_device_ids=EXCLUDED.parent_device_ids, "
                    "parent_device_ports=EXCLUDED.parent_device_ports, "
                    "probe_id=EXCLUDED.probe_id",
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
                    "alerts_muted,host_override,snmp_unit,"
                    "vmware_user,vmware_password,vmware_vm_id,vmware_vm_name,vmware_metric,"
                    "anomaly_enabled,anomaly_sensitivity,anomaly_min_samples,"
                    "smtp_tls,smtp_user,smtp_password,smtp_from,smtp_rcpt,smtp_test_level,"
                    "ssh_user,ssh_password,ssh_private_key,ssh_auth_type,ssh_test_level,"
                    "sftp_user,sftp_password,sftp_private_key,sftp_auth_type,sftp_test_level,"
                    "sftp_remote_path,sftp_expected_sha256,"
                    "radius_secret,radius_test_level,radius_username,radius_password,radius_nas_id,"
                    "snmp_v3_user,snmp_v3_level,snmp_v3_auth_proto,snmp_v3_auth_pass,"
                    "snmp_v3_priv_proto,snmp_v3_priv_pass,snmp_v3_context,probe_id) "
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
                    "host_override=EXCLUDED.host_override, snmp_unit=EXCLUDED.snmp_unit, "
                    "vmware_user=EXCLUDED.vmware_user, vmware_password=EXCLUDED.vmware_password, "
                    "vmware_vm_id=EXCLUDED.vmware_vm_id, vmware_vm_name=EXCLUDED.vmware_vm_name, "
                    "vmware_metric=EXCLUDED.vmware_metric, "
                    "anomaly_enabled=EXCLUDED.anomaly_enabled, "
                    "anomaly_sensitivity=EXCLUDED.anomaly_sensitivity, "
                    "anomaly_min_samples=EXCLUDED.anomaly_min_samples, "
                    "smtp_tls=EXCLUDED.smtp_tls, smtp_user=EXCLUDED.smtp_user, "
                    "smtp_password=EXCLUDED.smtp_password, smtp_from=EXCLUDED.smtp_from, "
                    "smtp_rcpt=EXCLUDED.smtp_rcpt, smtp_test_level=EXCLUDED.smtp_test_level, "
                    "ssh_user=EXCLUDED.ssh_user, ssh_password=EXCLUDED.ssh_password, "
                    "ssh_private_key=EXCLUDED.ssh_private_key, "
                    "ssh_auth_type=EXCLUDED.ssh_auth_type, ssh_test_level=EXCLUDED.ssh_test_level, "
                    "sftp_user=EXCLUDED.sftp_user, sftp_password=EXCLUDED.sftp_password, "
                    "sftp_private_key=EXCLUDED.sftp_private_key, "
                    "sftp_auth_type=EXCLUDED.sftp_auth_type, sftp_test_level=EXCLUDED.sftp_test_level, "
                    "sftp_remote_path=EXCLUDED.sftp_remote_path, "
                    "sftp_expected_sha256=EXCLUDED.sftp_expected_sha256, "
                    "radius_secret=EXCLUDED.radius_secret, "
                    "radius_test_level=EXCLUDED.radius_test_level, "
                    "radius_username=EXCLUDED.radius_username, "
                    "radius_password=EXCLUDED.radius_password, "
                    "radius_nas_id=EXCLUDED.radius_nas_id, "
                    "snmp_v3_user=EXCLUDED.snmp_v3_user, "
                    "snmp_v3_level=EXCLUDED.snmp_v3_level, "
                    "snmp_v3_auth_proto=EXCLUDED.snmp_v3_auth_proto, "
                    "snmp_v3_auth_pass=EXCLUDED.snmp_v3_auth_pass, "
                    "snmp_v3_priv_proto=EXCLUDED.snmp_v3_priv_proto, "
                    "snmp_v3_priv_pass=EXCLUDED.snmp_v3_priv_pass, "
                    "snmp_v3_context=EXCLUDED.snmp_v3_context, "
                    "probe_id=EXCLUDED.probe_id",
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
        # Stay quiet during a known PG outage — the breaker has already raised
        # the WARNING. Surface real errors otherwise.
        from db.pg_pool import is_pg_in_outage
        if is_pg_in_outage():
            log.debug(f"DB save skipped (PG outage): {e}")
        else:
            log.error(f"DB save error: {e}")


def db_save(state):
    """Upsert all devices and sensors; remove deleted rows."""
    if not _LOAD_OK:
        log.warning("DB save skipped — initial device/sensor load has not "
                    "completed successfully; saving now could erase the stored "
                    "configuration. Fix the load error and restart.")
        return
    with state._lock:
        _nd = len(state.devices)
        _ns = sum(len(d.sensors) for d in state.devices.values())
    log.debug(f"DB save: {_nd} devices, {_ns} sensors")

    if is_pg():
        _pg_save(state)
        return

    # ── SQLite path ──────────────────────────────────────────────────
    # Snapshot under lock — no I/O while holding it
    with state._lock:
        dev_rows = [
            (dev.device_id, dev.name, dev.host, dev.group,
             getattr(dev, "site", ""),
             dev._sid_ctr,
             getattr(dev, "webhook_url", ""),
             int(getattr(dev, "alerts_muted", False)),
             getattr(dev, "snmp_community_default", ""),
             getattr(dev, "snmp_version_default", ""),
             getattr(dev, "vmware_user_default", ""),
             getattr(dev, "vmware_password_default", ""),
             json.dumps(getattr(dev, "secondary_ips", []) or []),
             getattr(dev, "external_id", None),
             float(getattr(dev, "discovered_at", 0) or 0),
             getattr(dev, "discovered_from_cidr", "") or "",
             getattr(dev, "snmp_v3_user_default", ""),
             getattr(dev, "snmp_v3_level_default", ""),
             getattr(dev, "snmp_v3_auth_proto_default", ""),
             getattr(dev, "snmp_v3_auth_pass_default", ""),
             getattr(dev, "snmp_v3_priv_proto_default", ""),
             getattr(dev, "snmp_v3_priv_pass_default", ""),
             getattr(dev, "snmp_v3_context_default", ""),
             json.dumps(getattr(dev, "parent_device_ids", []) or []),
             json.dumps(getattr(dev, "parent_device_ports", {}) or {}),
             getattr(dev, "probe_id", "") or "")
            for dev in state.devices.values()
        ]
        snr_rows = [
            (s.device_id, s.sensor_id, s.name, s.stype,
             s.host, _int_or_none(s.port), s.url,
             _int_or_none(s.interval), _int_or_none(s.timeout),
             int(s.verify_ssl), s.snmp_community,
             s.snmp_oid, s.snmp_version, dev._sid_ctr,
             s.dns_query, s.dns_record_type, s.dns_server,
             _int_or_none(getattr(s, "http_expected_status", 0)) or 0,
             _int_or_none(getattr(s, "fail_after", 2)) or 2,
             _int_or_none(getattr(s, "recover_after", 1)) or 1,
             _int_or_none(getattr(s, "warn_ms", None)),
             _int_or_none(getattr(s, "crit_ms", None)),
             _int_or_none(getattr(s, "loss_warn_pct", 0)) or 0,
             _int_or_none(getattr(s, "loss_crit_pct", 0)) or 0,
             getattr(s, "keyword", ""), int(getattr(s, "keyword_case", False)),
             getattr(s, "banner_regex", ""),
             int(getattr(s, "alerts_muted", False)),
             int(getattr(s, "host_override", False)),
             getattr(s, "snmp_unit", ""),
             getattr(s, "vmware_user", ""),
             getattr(s, "vmware_password", ""),
             getattr(s, "vmware_vm_id", ""),
             getattr(s, "vmware_vm_name", ""),
             getattr(s, "vmware_metric", ""),
             int(getattr(s, "anomaly_enabled", 0) or 0),
             int(getattr(s, "anomaly_sensitivity", 2) or 2),
             int(getattr(s, "anomaly_min_samples", 50) or 50),
             getattr(s, "smtp_tls", "none") or "none",
             getattr(s, "smtp_user", ""),
             getattr(s, "smtp_password", ""),
             getattr(s, "smtp_from", ""),
             getattr(s, "smtp_rcpt", ""),
             getattr(s, "smtp_test_level", "ehlo") or "ehlo",
             getattr(s, "ssh_user", ""),
             getattr(s, "ssh_password", ""),
             getattr(s, "ssh_private_key", ""),
             getattr(s, "ssh_auth_type", "password") or "password",
             getattr(s, "ssh_test_level", "banner") or "banner",
             getattr(s, "sftp_user", ""),
             getattr(s, "sftp_password", ""),
             getattr(s, "sftp_private_key", ""),
             getattr(s, "sftp_auth_type", "password") or "password",
             getattr(s, "sftp_test_level", "open") or "open",
             getattr(s, "sftp_remote_path", ""),
             getattr(s, "sftp_expected_sha256", ""),
             getattr(s, "radius_secret", ""),
             getattr(s, "radius_test_level", "reachable") or "reachable",
             getattr(s, "radius_username", ""),
             getattr(s, "radius_password", ""),
             getattr(s, "radius_nas_id", ""),
             getattr(s, "snmp_v3_user", ""),
             getattr(s, "snmp_v3_level", ""),
             getattr(s, "snmp_v3_auth_proto", ""),
             getattr(s, "snmp_v3_auth_pass", ""),
             getattr(s, "snmp_v3_priv_proto", ""),
             getattr(s, "snmp_v3_priv_pass", ""),
             getattr(s, "snmp_v3_context", ""),
             getattr(s, "probe_id", "") or "")
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
        cur.executemany(
            "INSERT OR REPLACE INTO devices "
            "(did,name,host,grp,site,did_ctr,webhook_url,alerts_muted,"
            "snmp_community_default,snmp_version_default,vmware_user_default,"
            "vmware_password_default,secondary_ips,external_id,"
            "discovered_at,discovered_from_cidr,"
            "snmp_v3_user_default,snmp_v3_level_default,"
            "snmp_v3_auth_proto_default,snmp_v3_auth_pass_default,"
            "snmp_v3_priv_proto_default,snmp_v3_priv_pass_default,"
            "snmp_v3_context_default,parent_device_ids,parent_device_ports,probe_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", dev_rows)
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
            "alerts_muted,host_override,snmp_unit,"
            "vmware_user,vmware_password,vmware_vm_id,vmware_vm_name,vmware_metric,"
            "anomaly_enabled,anomaly_sensitivity,anomaly_min_samples,"
            "smtp_tls,smtp_user,smtp_password,smtp_from,smtp_rcpt,smtp_test_level,"
            "ssh_user,ssh_password,ssh_private_key,ssh_auth_type,ssh_test_level,"
            "sftp_user,sftp_password,sftp_private_key,sftp_auth_type,sftp_test_level,"
            "sftp_remote_path,sftp_expected_sha256,"
            "radius_secret,radius_test_level,radius_username,radius_password,radius_nas_id,"
            "snmp_v3_user,snmp_v3_level,snmp_v3_auth_proto,snmp_v3_auth_pass,"
            "snmp_v3_priv_proto,snmp_v3_priv_pass,snmp_v3_context,probe_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                "SELECT did,name,host,grp,did_ctr,webhook_url,alerts_muted,"
                "COALESCE(snmp_community_default,'') AS snmp_community_default,"
                "COALESCE(snmp_version_default,'') AS snmp_version_default,"
                "COALESCE(vmware_user_default,'') AS vmware_user_default,"
                "COALESCE(vmware_password_default,'') AS vmware_password_default,"
                "COALESCE(secondary_ips,'[]') AS secondary_ips,"
                "external_id,"
                "COALESCE(discovered_at,0) AS discovered_at,"
                "COALESCE(discovered_from_cidr,'') AS discovered_from_cidr,"
                "COALESCE(snmp_v3_user_default,'') AS snmp_v3_user_default,"
                "COALESCE(snmp_v3_level_default,'') AS snmp_v3_level_default,"
                "COALESCE(snmp_v3_auth_proto_default,'') AS snmp_v3_auth_proto_default,"
                "COALESCE(snmp_v3_auth_pass_default,'') AS snmp_v3_auth_pass_default,"
                "COALESCE(snmp_v3_priv_proto_default,'') AS snmp_v3_priv_proto_default,"
                "COALESCE(snmp_v3_priv_pass_default,'') AS snmp_v3_priv_pass_default,"
                "COALESCE(snmp_v3_context_default,'') AS snmp_v3_context_default,"
                "COALESCE(site,'') AS site,"
                "COALESCE(parent_device_ids,'[]') AS parent_device_ids,"
                "COALESCE(parent_device_ports,'{}') AS parent_device_ports,"
                "COALESCE(probe_id,'') AS probe_id "
                "FROM devices"
            )
            devs = cur.fetchall()
            cur.execute(
                "SELECT did,sid,name,stype,host,port,url,interval,timeout,"
                "verify_ssl,snmp_community,snmp_oid,snmp_version,sid_ctr,"
                "dns_query,dns_record_type,dns_server,http_expected_status,"
                "fail_after,recover_after,warn_ms,crit_ms,"
                "loss_warn_pct,loss_crit_pct,keyword,keyword_case,banner_regex,"
                "alerts_muted,host_override,COALESCE(snmp_unit,'') AS snmp_unit,"
                "COALESCE(vmware_user,'') AS vmware_user,"
                "COALESCE(vmware_password,'') AS vmware_password,"
                "COALESCE(vmware_vm_id,'') AS vmware_vm_id,"
                "COALESCE(vmware_vm_name,'') AS vmware_vm_name,"
                "COALESCE(vmware_metric,'') AS vmware_metric,"
                "COALESCE(anomaly_enabled,0) AS anomaly_enabled,"
                "COALESCE(anomaly_sensitivity,2) AS anomaly_sensitivity,"
                "COALESCE(anomaly_min_samples,50) AS anomaly_min_samples,"
                "COALESCE(smtp_tls,'none') AS smtp_tls,"
                "COALESCE(smtp_user,'') AS smtp_user,"
                "COALESCE(smtp_password,'') AS smtp_password,"
                "COALESCE(smtp_from,'') AS smtp_from,"
                "COALESCE(smtp_rcpt,'') AS smtp_rcpt,"
                "COALESCE(smtp_test_level,'ehlo') AS smtp_test_level,"
                "COALESCE(ssh_user,'') AS ssh_user,"
                "COALESCE(ssh_password,'') AS ssh_password,"
                "COALESCE(ssh_private_key,'') AS ssh_private_key,"
                "COALESCE(ssh_auth_type,'password') AS ssh_auth_type,"
                "COALESCE(ssh_test_level,'banner') AS ssh_test_level,"
                "COALESCE(sftp_user,'') AS sftp_user,"
                "COALESCE(sftp_password,'') AS sftp_password,"
                "COALESCE(sftp_private_key,'') AS sftp_private_key,"
                "COALESCE(sftp_auth_type,'password') AS sftp_auth_type,"
                "COALESCE(sftp_test_level,'open') AS sftp_test_level,"
                "COALESCE(sftp_remote_path,'') AS sftp_remote_path,"
                "COALESCE(sftp_expected_sha256,'') AS sftp_expected_sha256,"
                "COALESCE(radius_secret,'') AS radius_secret,"
                "COALESCE(radius_test_level,'reachable') AS radius_test_level,"
                "COALESCE(radius_username,'') AS radius_username,"
                "COALESCE(radius_password,'') AS radius_password,"
                "COALESCE(radius_nas_id,'') AS radius_nas_id,"
                "COALESCE(snmp_v3_user,'') AS snmp_v3_user,"
                "COALESCE(snmp_v3_level,'') AS snmp_v3_level,"
                "COALESCE(snmp_v3_auth_proto,'') AS snmp_v3_auth_proto,"
                "COALESCE(snmp_v3_auth_pass,'') AS snmp_v3_auth_pass,"
                "COALESCE(snmp_v3_priv_proto,'') AS snmp_v3_priv_proto,"
                "COALESCE(snmp_v3_priv_pass,'') AS snmp_v3_priv_pass,"
                "COALESCE(snmp_v3_context,'') AS snmp_v3_context,"
                "COALESCE(probe_id,'') AS probe_id "
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
        _mark_load_ok()   # successful load of an empty DB — saves are safe
        return

    max_did = 0
    for row in devs:
        did, name, host, grp = row[0], row[1], row[2], row[3]
        dev = Device(did, name, host, grp, site=(row[22] or "") if len(row) > 22 else "")
        try:
            n = int(did.replace("d", ""))
            if n > max_did: max_did = n
        except Exception:
            pass
        dev._sid_ctr      = row[4] or 0
        dev.webhook_url   = row[5] or ""
        dev.alerts_muted  = bool(row[6] or 0)
        dev.snmp_community_default  = row[7] or ""
        dev.snmp_version_default    = row[8] or ""
        dev.vmware_user_default     = row[9] or ""
        dev.vmware_password_default = row[10] or ""
        try:
            dev.secondary_ips = json.loads(row[11] or "[]")
        except (json.JSONDecodeError, TypeError):
            dev.secondary_ips = []
        dev.external_id = row[12] if len(row) > 12 else None
        dev.discovered_at        = float(row[13] or 0) if len(row) > 13 else 0.0
        dev.discovered_from_cidr = (row[14] or "")    if len(row) > 14 else ""
        dev.snmp_v3_user_default       = (row[15] or "") if len(row) > 15 else ""
        dev.snmp_v3_level_default      = (row[16] or "") if len(row) > 16 else ""
        dev.snmp_v3_auth_proto_default = (row[17] or "") if len(row) > 17 else ""
        dev.snmp_v3_auth_pass_default  = (row[18] or "") if len(row) > 18 else ""
        dev.snmp_v3_priv_proto_default = (row[19] or "") if len(row) > 19 else ""
        dev.snmp_v3_priv_pass_default  = (row[20] or "") if len(row) > 20 else ""
        dev.snmp_v3_context_default    = (row[21] or "") if len(row) > 21 else ""
        # dev.site set at Device() construction above (row[22])
        try:
            dev.parent_device_ids = json.loads(row[23] or "[]") if len(row) > 23 else []
            if not isinstance(dev.parent_device_ids, list):
                dev.parent_device_ids = []
        except (json.JSONDecodeError, TypeError):
            dev.parent_device_ids = []
        try:
            _pp_raw = json.loads(row[24] or "{}") if len(row) > 24 else {}
            dev.parent_device_ports = _normalize_pp_shape(_pp_raw)
        except (json.JSONDecodeError, TypeError):
            dev.parent_device_ports = {}
        dev.probe_id = (row[25] or "") if len(row) > 25 else ""
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
                   fail_after=int(row[18] or 2), recover_after=int(row[19] or 1),
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
        s.vmware_user          = row[30] or ""
        s.vmware_password      = row[31] or ""
        s.vmware_vm_id         = row[32] or ""
        s.vmware_vm_name       = row[33] or ""
        s.vmware_metric        = row[34] or ""
        s.anomaly_enabled      = int(row[35] or 0)
        s.anomaly_sensitivity  = int(row[36] or 2)
        s.anomaly_min_samples  = int(row[37] or 50)
        s.smtp_tls             = row[38] or "none"
        s.smtp_user            = row[39] or ""
        s.smtp_password        = row[40] or ""
        s.smtp_from            = row[41] or ""
        s.smtp_rcpt            = row[42] or ""
        s.smtp_test_level      = row[43] or "ehlo"
        s.ssh_user             = row[44] or ""
        s.ssh_password         = row[45] or ""
        s.ssh_private_key      = row[46] or ""
        s.ssh_auth_type        = row[47] or "password"
        s.ssh_test_level       = row[48] or "banner"
        s.sftp_user            = row[49] or ""
        s.sftp_password        = row[50] or ""
        s.sftp_private_key     = row[51] or ""
        s.sftp_auth_type       = row[52] or "password"
        s.sftp_test_level      = row[53] or "open"
        s.sftp_remote_path     = row[54] or ""
        s.sftp_expected_sha256 = row[55] or ""
        s.radius_secret        = row[56] or ""
        s.radius_test_level    = row[57] or "reachable"
        s.radius_username      = row[58] or ""
        s.radius_password      = row[59] or ""
        s.radius_nas_id        = row[60] or ""
        s.snmp_v3_user         = row[61] or "" if len(row) > 61 else ""
        s.snmp_v3_level        = row[62] or "" if len(row) > 62 else ""
        s.snmp_v3_auth_proto   = row[63] or "" if len(row) > 63 else ""
        s.snmp_v3_auth_pass    = row[64] or "" if len(row) > 64 else ""
        s.snmp_v3_priv_proto   = row[65] or "" if len(row) > 65 else ""
        s.snmp_v3_priv_pass    = row[66] or "" if len(row) > 66 else ""
        s.snmp_v3_context      = row[67] or "" if len(row) > 67 else ""
        s.probe_id             = row[68] or "" if len(row) > 68 else ""
        dev.sensors[row[1]] = s

    state._did_ctr = max_did
    snr_total = sum(len(d.sensors) for d in state.devices.values())
    log.info(f"DB load: restored {len(state.devices)} device(s), {snr_total} sensor(s) into state")

    # ── Restore runtime state from sensor_samples ────────────────────
    # History: per-sensor indexed seeks — each query uses the (did,sid,ts) composite
    # index and stops at LIMIT 80.  A single window-function query over the full table
    # is dramatically slower because it cannot exploit that index.
    # Stats: one batched GROUP BY — genuinely benefits from a single round trip.
    _count_window = min(int(_settings.get("retention_days", 30)), 30)
    _cutoff = time.time() - _count_window * 86400
    try:
        with pg_conn("logs") as con:
            cur = con.cursor()
            _hist_by_key = {}  # (did, sid) → list of (ok, ms, value), newest-first
            for _dev in state.devices.values():
                for _s in _dev.sensors.values():
                    cur.execute(
                        "SELECT ok, ms, value FROM sensor_samples "
                        "WHERE did=%s AND sid=%s ORDER BY ts DESC LIMIT 80",
                        (_s.device_id, _s.sensor_id)
                    )
                    _rows = cur.fetchall()
                    if _rows:
                        _hist_by_key[(_s.device_id, _s.sensor_id)] = _rows
            # One query for availability stats across all sensors
            cur.execute(
                "SELECT did, sid, COUNT(*), COALESCE(SUM(ok),0) "
                "FROM sensor_samples WHERE ts>=%s GROUP BY did, sid",
                (_cutoff,)
            )
            _stats_by_key = {(r[0], r[1]): (int(r[2] or 0), int(r[3] or 0)) for r in cur.fetchall()}
            cur.close()
        # Apply to in-memory state
        for _dev in state.devices.values():
            for _s in _dev.sensors.values():
                _key = (_s.device_id, _s.sensor_id)
                _rows = _hist_by_key.get(_key)
                if _rows:
                    # rows are newest-first (ORDER BY ts DESC); reverse to append oldest→newest
                    _rows_chrono = list(reversed(_rows))
                    for _ok, _ms, _val in _rows_chrono:
                        _s.history.append(_ms)
                    _last = _rows_chrono[-1]  # most recent
                    _s.alive      = bool(_last[0])
                    _s.last_ms    = _last[1]
                    _s.last_value = _last[2]
                _cnt, _suc = _stats_by_key.get(_key, (0, 0))
                _s.total   = _cnt
                _s.success = _suc
        log.info("Runtime state restored from sensor_samples.")
    except Exception as _e:
        log.error(f"DB restore runtime error: {_e}")

    db_load_anomaly_baselines(state)

    # Re-hydrate _alerted_down / _threshold_state from unresolved flap_log rows
    # so post-restart probes don't fire duplicate 'down'/'threshold_*' flap
    # entries for sensors that were already in those states pre-restart.
    try:
        from db.events import db_load_unresolved_flap_state
        db_load_unresolved_flap_state(state)
    except Exception as _e:
        log.error(f"db_load_unresolved_flap_state hook error: {_e}")

    for did in list(state.devices):
        state.start_device(did)
    log.info("Auto-started all sensors.")
    _mark_load_ok()


def db_load(state):
    """Restore devices and sensors from DB; auto-start all sensors."""
    if is_pg():
        _pg_load(state)
        return

    # ── SQLite path ──────────────────────────────────────────────────
    con = None
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        devs = con.execute(
            "SELECT did,name,host,grp,did_ctr,webhook_url,alerts_muted,"
            "COALESCE(snmp_community_default,''),COALESCE(snmp_version_default,''),"
            "COALESCE(vmware_user_default,''),COALESCE(vmware_password_default,''),"
            "COALESCE(secondary_ips,'[]'),external_id,"
            "COALESCE(discovered_at,0),COALESCE(discovered_from_cidr,''),"
            "COALESCE(snmp_v3_user_default,''),COALESCE(snmp_v3_level_default,''),"
            "COALESCE(snmp_v3_auth_proto_default,''),COALESCE(snmp_v3_auth_pass_default,''),"
            "COALESCE(snmp_v3_priv_proto_default,''),COALESCE(snmp_v3_priv_pass_default,''),"
            "COALESCE(snmp_v3_context_default,''),"
            "COALESCE(site,''),"
            "COALESCE(parent_device_ids,'[]'),"
            "COALESCE(parent_device_ports,'{}'),"
            "COALESCE(probe_id,'') "
            "FROM devices"
        ).fetchall()
        srows = con.execute(
            "SELECT did,sid,name,stype,host,port,url,interval,timeout,"
            "verify_ssl,snmp_community,snmp_oid,snmp_version,sid_ctr,"
            "dns_query,dns_record_type,dns_server,http_expected_status,"
            "fail_after,recover_after,warn_ms,crit_ms,"
            "loss_warn_pct,loss_crit_pct,keyword,keyword_case,banner_regex,alerts_muted,host_override,"
            "COALESCE(snmp_unit,''),"
            "COALESCE(vmware_user,''),COALESCE(vmware_password,''),"
            "COALESCE(vmware_vm_id,''),COALESCE(vmware_vm_name,''),COALESCE(vmware_metric,''),"
            "COALESCE(anomaly_enabled,0),COALESCE(anomaly_sensitivity,2),"
            "COALESCE(anomaly_min_samples,50),"
            "COALESCE(smtp_tls,'none'),COALESCE(smtp_user,''),"
            "COALESCE(smtp_password,''),COALESCE(smtp_from,''),"
            "COALESCE(smtp_rcpt,''),COALESCE(smtp_test_level,'ehlo'),"
            "COALESCE(ssh_user,''),COALESCE(ssh_password,''),"
            "COALESCE(ssh_private_key,''),COALESCE(ssh_auth_type,'password'),"
            "COALESCE(ssh_test_level,'banner'),"
            "COALESCE(sftp_user,''),COALESCE(sftp_password,''),"
            "COALESCE(sftp_private_key,''),COALESCE(sftp_auth_type,'password'),"
            "COALESCE(sftp_test_level,'open'),"
            "COALESCE(sftp_remote_path,''),COALESCE(sftp_expected_sha256,''),"
            "COALESCE(radius_secret,''),COALESCE(radius_test_level,'reachable'),"
            "COALESCE(radius_username,''),COALESCE(radius_password,''),"
            "COALESCE(radius_nas_id,''),"
            "COALESCE(snmp_v3_user,''),COALESCE(snmp_v3_level,''),"
            "COALESCE(snmp_v3_auth_proto,''),COALESCE(snmp_v3_auth_pass,''),"
            "COALESCE(snmp_v3_priv_proto,''),COALESCE(snmp_v3_priv_pass,''),"
            "COALESCE(snmp_v3_context,''),"
            "COALESCE(probe_id,'') "
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
        _mark_load_ok()   # successful load of an empty DB — saves are safe
        return

    max_did = 0
    for _row in devs:
        (did, name, host, grp, sid_ctr, webhook_url, alerts_muted,
         snmp_community_default, snmp_version_default, vmware_user_default,
         vmware_password_default, secondary_ips_json, external_id,
         discovered_at, discovered_from_cidr,
         v3_user_default, v3_level_default,
         v3_auth_proto_default, v3_auth_pass_default,
         v3_priv_proto_default, v3_priv_pass_default,
         v3_context_default, site, parent_ids_json,
         parent_ports_json, dev_probe_id) = _row
        dev = Device(did, name, host, grp, site=site or "")
        try:
            n = int(did.replace("d", ""))
            if n > max_did: max_did = n
        except Exception:
            pass
        dev._sid_ctr      = sid_ctr or 0
        dev.webhook_url   = webhook_url or ""
        dev.alerts_muted  = bool(alerts_muted or 0)
        dev.snmp_community_default  = snmp_community_default or ""
        dev.snmp_version_default    = snmp_version_default or ""
        dev.vmware_user_default     = vmware_user_default or ""
        dev.vmware_password_default = vmware_password_default or ""
        try:
            dev.secondary_ips = json.loads(secondary_ips_json or "[]")
        except (json.JSONDecodeError, TypeError):
            dev.secondary_ips = []
        dev.external_id = external_id or None
        dev.discovered_at        = float(discovered_at or 0)
        dev.discovered_from_cidr = discovered_from_cidr or ""
        dev.snmp_v3_user_default       = v3_user_default or ""
        dev.snmp_v3_level_default      = v3_level_default or ""
        dev.snmp_v3_auth_proto_default = v3_auth_proto_default or ""
        dev.snmp_v3_auth_pass_default  = v3_auth_pass_default or ""
        dev.snmp_v3_priv_proto_default = v3_priv_proto_default or ""
        dev.snmp_v3_priv_pass_default  = v3_priv_pass_default or ""
        dev.snmp_v3_context_default    = v3_context_default or ""
        try:
            _pids = json.loads(parent_ids_json or "[]")
            dev.parent_device_ids = _pids if isinstance(_pids, list) else []
        except (json.JSONDecodeError, TypeError):
            dev.parent_device_ids = []
        try:
            _pports = json.loads(parent_ports_json or "{}")
            dev.parent_device_ports = _normalize_pp_shape(_pports)
        except (json.JSONDecodeError, TypeError):
            dev.parent_device_ports = {}
        dev.probe_id = dev_probe_id or ""
        state.devices[did] = dev

    for (did, sid, name, stype, host, port, url, interval, timeout,
         vssl, comm, oid, sver, sid_ctr,
         dns_query, dns_record_type, dns_server, http_expected_status,
         fail_after, recover_after, warn_ms, crit_ms,
         loss_warn_pct, loss_crit_pct, keyword, keyword_case, banner_regex,
         alerts_muted, host_override, snmp_unit,
         vmware_user, vmware_password, vmware_vm_id, vmware_vm_name, vmware_metric,
         anomaly_enabled, anomaly_sensitivity, anomaly_min_samples,
         smtp_tls, smtp_user, smtp_password,
         smtp_from, smtp_rcpt, smtp_test_level,
         ssh_user, ssh_password, ssh_private_key,
         ssh_auth_type, ssh_test_level,
         sftp_user, sftp_password, sftp_private_key,
         sftp_auth_type, sftp_test_level,
         sftp_remote_path, sftp_expected_sha256,
         radius_secret, radius_test_level,
         radius_username, radius_password, radius_nas_id,
         snmp_v3_user, snmp_v3_level,
         snmp_v3_auth_proto, snmp_v3_auth_pass,
         snmp_v3_priv_proto, snmp_v3_priv_pass,
         snmp_v3_context, snr_probe_id) in srows:
        dev = state.devices.get(did)
        if not dev: continue
        s = Sensor(did, sid, name, stype, host or dev.host,
                   port=port, url=url, interval=interval, timeout=timeout,
                   verify_ssl=bool(vssl), snmp_community=comm or "public",
                   snmp_oid=oid or "1.3.6.1.2.1.1.1.0",
                   snmp_version=sver or "2c",
                   fail_after=int(fail_after or 2), recover_after=int(recover_after or 1),
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
        s.vmware_user          = vmware_user or ""
        s.vmware_password      = vmware_password or ""
        s.vmware_vm_id         = vmware_vm_id or ""
        s.vmware_vm_name       = vmware_vm_name or ""
        s.vmware_metric        = vmware_metric or ""
        s.anomaly_enabled      = int(anomaly_enabled or 0)
        s.anomaly_sensitivity  = int(anomaly_sensitivity or 2)
        s.anomaly_min_samples  = int(anomaly_min_samples or 50)
        s.smtp_tls             = smtp_tls or "none"
        s.smtp_user            = smtp_user or ""
        s.smtp_password        = smtp_password or ""
        s.smtp_from            = smtp_from or ""
        s.smtp_rcpt            = smtp_rcpt or ""
        s.smtp_test_level      = smtp_test_level or "ehlo"
        s.ssh_user             = ssh_user or ""
        s.ssh_password         = ssh_password or ""
        s.ssh_private_key      = ssh_private_key or ""
        s.ssh_auth_type        = ssh_auth_type or "password"
        s.ssh_test_level       = ssh_test_level or "banner"
        s.sftp_user            = sftp_user or ""
        s.sftp_password        = sftp_password or ""
        s.sftp_private_key     = sftp_private_key or ""
        s.sftp_auth_type       = sftp_auth_type or "password"
        s.sftp_test_level      = sftp_test_level or "open"
        s.sftp_remote_path     = sftp_remote_path or ""
        s.sftp_expected_sha256 = sftp_expected_sha256 or ""
        s.radius_secret        = radius_secret or ""
        s.radius_test_level    = radius_test_level or "reachable"
        s.radius_username      = radius_username or ""
        s.radius_password      = radius_password or ""
        s.radius_nas_id        = radius_nas_id or ""
        s.snmp_v3_user         = snmp_v3_user or ""
        s.snmp_v3_level        = snmp_v3_level or ""
        s.snmp_v3_auth_proto   = snmp_v3_auth_proto or ""
        s.snmp_v3_auth_pass    = snmp_v3_auth_pass or ""
        s.snmp_v3_priv_proto   = snmp_v3_priv_proto or ""
        s.snmp_v3_priv_pass    = snmp_v3_priv_pass or ""
        s.snmp_v3_context      = snmp_v3_context or ""
        s.probe_id             = snr_probe_id or ""
        dev.sensors[sid] = s

    state._did_ctr = max_did
    snr_total = sum(len(d.sensors) for d in state.devices.values())
    log.info(f"DB load: restored {len(state.devices)} device(s), {snr_total} sensor(s) into state")

    # ── Restore runtime state from sensor_samples ────────────────────
    # History: per-sensor indexed seeks (fast); stats: one batched GROUP BY.
    _rcon = None
    try:
        _count_window = min(int(_settings.get("retention_days", 30)), 30)
        _cutoff = time.time() - _count_window * 86400
        _rcon = sqlite3.connect(f"file:{LOGS_DB_PATH}?mode=ro", uri=True, timeout=15)

        # History: individual indexed queries — exploits the (did, sid, ts) index
        _hist_by_key = {}  # (did, sid) → list of (ok, ms, value), newest-first
        for _dev in state.devices.values():
            for _s in _dev.sensors.values():
                _rows = _rcon.execute(
                    "SELECT ok, ms, value FROM sensor_samples "
                    "WHERE did=? AND sid=? ORDER BY ts DESC LIMIT 80",
                    (_s.device_id, _s.sensor_id)
                ).fetchall()
                if _rows:
                    _hist_by_key[(_s.device_id, _s.sensor_id)] = _rows

        # Stats: one batched query
        _stats_rows = _rcon.execute(
            "SELECT did, sid, COUNT(*), COALESCE(SUM(ok),0) "
            "FROM sensor_samples WHERE ts>=? GROUP BY did, sid",
            (_cutoff,)
        ).fetchall()
        _stats_by_key = {(r[0], r[1]): (int(r[2] or 0), int(r[3] or 0)) for r in _stats_rows}

        # Apply to in-memory state
        for _dev in state.devices.values():
            for _s in _dev.sensors.values():
                _key = (_s.device_id, _s.sensor_id)
                _rows = _hist_by_key.get(_key)
                if _rows:
                    # rows are newest-first (ORDER BY ts DESC); reverse to append oldest→newest
                    _rows_chrono = list(reversed(_rows))
                    for _ok, _ms, _val in _rows_chrono:
                        _s.history.append(_ms)
                    _last = _rows_chrono[-1]  # most recent
                    _s.alive      = bool(_last[0])
                    _s.last_ms    = _last[1]
                    _s.last_value = _last[2]
                _cnt, _suc = _stats_by_key.get(_key, (0, 0))
                _s.total   = _cnt
                _s.success = _suc
        log.info("Runtime state restored from sensor_samples.")
    except Exception as _e:
        log.error(f"DB restore runtime error: {_e}")
    finally:
        if _rcon:
            try: _rcon.close()
            except Exception: pass

    db_load_anomaly_baselines(state)

    # Re-hydrate _alerted_down / _threshold_state from unresolved flap_log rows
    # so post-restart probes don't fire duplicate 'down'/'threshold_*' flap
    # entries for sensors that were already in those states pre-restart.
    try:
        from db.events import db_load_unresolved_flap_state
        db_load_unresolved_flap_state(state)
    except Exception as _e:
        log.error(f"db_load_unresolved_flap_state hook error: {_e}")

    for did in list(state.devices):
        state.start_device(did)
    log.info("Auto-started all sensors.")
    _mark_load_ok()


# ── Background autosave ──────────────────────────────────────────

_autosave_stop = threading.Event()


def stop_autosave() -> None:
    """Signal the autosave loop to exit. Call before pg_close_pool() at shutdown
    so the 60 s sleep doesn't land mid-save after the pool is gone (which emits
    a spurious 'PostgreSQL pool is closed' error)."""
    _autosave_stop.set()


def autosave_loop(state):
    """Save state to DB every 60 s; clean old samples every ~1 hour;
    maintain PG partitions daily."""
    from db.samples import db_clean_samples
    _iter = 0
    while not _autosave_stop.is_set():
        # Interruptible wait — shutdown signals _autosave_stop so we exit
        # before pg_close_pool() runs, avoiding 'pool is closed' errors.
        if _autosave_stop.wait(60):
            break
        _db_enqueue(lambda: db_save(state))
        _iter += 1
        if _iter % 60 == 0:    # every ~hour
            _logs_enqueue(db_clean_samples)
            # Prune resolved alert events (main DB) on the same cadence.
            def _clean_alert_events():
                try:
                    from db.alert_events import db_clean_alert_events
                    _rd = int(_settings.get("alert_events_retain_days", 90) or 90)
                    db_clean_alert_events(_rd)
                except Exception as _ae:
                    log.warning(f"alert_events retention error: {_ae}")
            _db_enqueue(_clean_alert_events)
        if _iter % 360 == 0:   # every ~6 hours
            # Check license expirations
            try:
                from monitoring.license_checker import check_license_expirations
                check_license_expirations()
            except Exception as _le:
                from core.logger import log as _llog
                _llog.warning(f"License check error: {_le}")
            # Sweep expired trusted-device rows
            try:
                from db.users import db_sweep_expired_trusted_devices
                _n = db_sweep_expired_trusted_devices()
                if _n:
                    from core.logger import log as _llog
                    _llog.debug(f"Swept {_n} expired trusted-device row(s)")
            except Exception as _tde:
                from core.logger import log as _llog
                _llog.warning(f"Trusted device sweep error: {_tde}")
        if _iter % 1440 == 0:  # every ~24 hours — maintain PG partitions
            if is_pg():
                try:
                    from db.pg_pool import pg_conn
                    from db.pg_schema import pg_ensure_sample_partitions
                    with pg_conn("logs") as con:
                        pg_ensure_sample_partitions(con.cursor())
                except Exception as e:
                    from core.logger import log
                    log.warning(f"Partition maintenance error: {e}")
        # Anomaly baseline checkpoint — configurable cadence (default 1 h)
        try:
            _ckpt_every_min = max(1, int(
                _settings.get("anomaly_checkpoint_interval_s", 3600)) // 60)
        except Exception:
            _ckpt_every_min = 60
        if _iter % _ckpt_every_min == 0:
            try:
                db_checkpoint_anomaly_baselines(state)
            except Exception as _ae:
                from core.logger import log as _llog
                _llog.warning(f"Anomaly checkpoint error: {_ae}")


def db_load_anomaly_baselines(state):
    """Restore EWMA baseline state from sensor_anomaly_baselines into
    in-memory sensor attributes. Run after sensors are loaded."""
    try:
        if is_pg():
            from db.pg_pool import pg_conn
            with pg_conn("main") as con:
                cur = con.cursor()
                cur.execute(
                    "SELECT did, sid, mean_ms, var_ms, sample_count, enabled_since "
                    "FROM sensor_anomaly_baselines"
                )
                rows = cur.fetchall()
                cur.close()
            def _get(row, key, idx):
                return row[key] if isinstance(row, dict) else row[idx]
            for row in rows:
                did = _get(row, "did", 0)
                sid = _get(row, "sid", 1)
                dev = state.devices.get(did)
                if not dev:
                    continue
                s = dev.sensors.get(sid)
                if not s:
                    continue
                s._anom_mean = _get(row, "mean_ms", 2)
                s._anom_var = _get(row, "var_ms", 3) or 0.0
                s._anom_count = int(_get(row, "sample_count", 4) or 0)
                s._anom_enabled_since = _get(row, "enabled_since", 5)
                s._anom_dirty = False
        else:
            con = None
            try:
                con = sqlite3.connect(DB_PATH, timeout=15)
                rows = con.execute(
                    "SELECT did, sid, mean_ms, var_ms, sample_count, enabled_since "
                    "FROM sensor_anomaly_baselines"
                ).fetchall()
            finally:
                if con:
                    con.close()
            for did, sid, mean_ms, var_ms, sample_count, enabled_since in rows:
                dev = state.devices.get(did)
                if not dev:
                    continue
                s = dev.sensors.get(sid)
                if not s:
                    continue
                s._anom_mean = mean_ms
                s._anom_var = var_ms or 0.0
                s._anom_count = int(sample_count or 0)
                s._anom_enabled_since = enabled_since
                s._anom_dirty = False
        log.info("Anomaly baselines restored from sensor_anomaly_baselines.")
    except Exception as e:
        log.warning(f"Anomaly baseline restore error: {e}")


def db_checkpoint_anomaly_baselines(state):
    """Persist dirty in-memory EWMA baselines to sensor_anomaly_baselines.
    Safe to call at any cadence; bulk upsert with no lock held during I/O."""
    rows = []
    with state._lock:
        for dev in state.devices.values():
            for s in dev.sensors.values():
                if (getattr(s, "anomaly_enabled", 0)
                        and getattr(s, "_anom_dirty", False)
                        and s._anom_mean is not None):
                    rows.append((
                        s.device_id, s.sensor_id,
                        float(s._anom_mean),
                        float(s._anom_var or 0.0),
                        int(s._anom_count or 0),
                        float(s._anom_enabled_since or time.time()),
                        time.time(),
                    ))
                    s._anom_dirty = False
    if not rows:
        return 0
    if is_pg():
        try:
            from db.pg_pool import pg_conn
            import psycopg2.extras
            with pg_conn("main") as con:
                cur = con.cursor()
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO sensor_anomaly_baselines "
                    "(did, sid, mean_ms, var_ms, sample_count, enabled_since, updated_at) "
                    "VALUES %s "
                    "ON CONFLICT (did, sid) DO UPDATE SET "
                    "mean_ms=EXCLUDED.mean_ms, var_ms=EXCLUDED.var_ms, "
                    "sample_count=EXCLUDED.sample_count, "
                    "enabled_since=EXCLUDED.enabled_since, "
                    "updated_at=EXCLUDED.updated_at",
                    rows,
                )
                cur.close()
        except Exception as e:
            log.warning(f"Anomaly checkpoint (pg) error: {e}")
            return 0
    else:
        con = None
        try:
            con = sqlite3.connect(DB_PATH, timeout=15)
            con.executemany(
                "INSERT OR REPLACE INTO sensor_anomaly_baselines "
                "(did, sid, mean_ms, var_ms, sample_count, enabled_since, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                rows,
            )
            con.commit()
        except Exception as e:
            log.warning(f"Anomaly checkpoint (sqlite) error: {e}")
            return 0
        finally:
            if con:
                con.close()
    return len(rows)


def db_reset_anomaly_baseline(did: str, sid: str) -> bool:
    """Delete the checkpoint row for a single sensor. In-memory reset is
    the caller's responsibility (use monitoring.anomaly.reset_baseline)."""
    try:
        if is_pg():
            from db.pg_pool import pg_conn
            with pg_conn("main") as con:
                cur = con.cursor()
                cur.execute(
                    "DELETE FROM sensor_anomaly_baselines WHERE did=%s AND sid=%s",
                    (did, sid),
                )
                cur.close()
            return True
        con = None
        try:
            con = sqlite3.connect(DB_PATH, timeout=15)
            con.execute(
                "DELETE FROM sensor_anomaly_baselines WHERE did=? AND sid=?",
                (did, sid),
            )
            con.commit()
        finally:
            if con:
                con.close()
        return True
    except Exception as e:
        log.warning(f"Anomaly baseline delete error: {e}")
        return False
