"""
reports/data.py — Assemble the data context for a report.

Pure data: talks to the existing DB helpers only. No rendering, no I/O beyond
the DB. The output is a dict consumable by the Jinja2 templates.

Public entrypoint: build_report_context(kind, period, filters, config)
"""

import datetime
import sqlite3
import time

from core.config   import LOGS_DB_PATH
from core.logger   import log
from core.settings import get as _cfg
from db.backend    import is_pg
from db.helpers    import db_query


# ── Period helpers ─────────────────────────────────────────────────────

def resolve_period(period: str, anchor_ts: float = None) -> tuple:
    """
    Map a symbolic period to (start_ts, end_ts, label) triple.

    Supported:
      last_7d, last_30d, last_90d
      last_month  (calendar month preceding anchor_ts)
      last_quarter
      last_year
      month_to_date
      custom:<start_ts>:<end_ts>
    """
    anchor = anchor_ts if anchor_ts is not None else time.time()
    now_dt = datetime.datetime.fromtimestamp(anchor)

    if period == "last_7d":
        start = anchor - 7 * 86400
        return (start, anchor, "Last 7 days")
    if period == "last_30d":
        start = anchor - 30 * 86400
        return (start, anchor, "Last 30 days")
    if period == "last_90d":
        start = anchor - 90 * 86400
        return (start, anchor, "Last 90 days")
    if period == "last_year":
        start = anchor - 365 * 86400
        return (start, anchor, "Last 365 days")
    if period == "month_to_date":
        first = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return (first.timestamp(), anchor, f"{first.strftime('%B %Y')} (to date)")
    if period == "last_month":
        first_this = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_prev = first_this - datetime.timedelta(seconds=1)
        first_prev = last_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return (first_prev.timestamp(), first_this.timestamp(),
                first_prev.strftime("%B %Y"))
    if period == "last_quarter":
        q = (now_dt.month - 1) // 3
        first_this_q = now_dt.replace(month=q * 3 + 1, day=1,
                                      hour=0, minute=0, second=0, microsecond=0)
        last_prev_q = first_this_q - datetime.timedelta(seconds=1)
        first_prev_q = last_prev_q.replace(month=((q - 1) % 4) * 3 + 1, day=1,
                                           hour=0, minute=0, second=0, microsecond=0)
        label = f"Q{((q - 1) % 4) + 1} {first_prev_q.year}"
        return (first_prev_q.timestamp(), first_this_q.timestamp(), label)
    if period.startswith("custom:"):
        try:
            _, s, e = period.split(":", 2)
            return (float(s), float(e), "Custom range")
        except Exception:
            pass
    # Fallback — last 30 days
    return (anchor - 30 * 86400, anchor, "Last 30 days")


# ── Availability / uptime ──────────────────────────────────────────────

def _availability_by_device(start_ts: float, end_ts: float, device_ids: list = None) -> list:
    """
    Return [{did, dname, samples, up, pct, fail}] for each device in scope.
    Reads from the same tiered samples storage PingWatch uses for charts:
      • ≤ 1 day  → raw  sensor_samples       (SUM(ok) / COUNT(*))
      • ≤ 90 d   → 5-minute rollup            (SUM(ok_count) / SUM(ok_count+fail_count))
      • > 90 d   → 1-hour  rollup             (same as 5m)
    Raw samples have a short retention window (default 7 days), so querying
    a month-long report needs the rollup tables to find any history at all.
    """
    from core.app_state import STATE
    from db.samples    import _pick_table

    minutes = max(1, int((end_ts - start_ts) / 60))
    table, _bucket = _pick_table(minutes)
    is_rollup = table != "sensor_samples"

    results: dict = {}

    if is_rollup:
        # Rollup schema: ok_count + fail_count + sample_count
        sql_pg     = (f"SELECT did, SUM(ok_count) AS up, "
                      f"SUM(ok_count + fail_count) AS total "
                      f"FROM {table} WHERE ts>=%s AND ts<%s GROUP BY did")
        sql_sqlite = (f"SELECT did, SUM(ok_count), SUM(ok_count + fail_count) "
                      f"FROM {table} WHERE ts>=? AND ts<? GROUP BY did")
    else:
        # Raw schema: one row per probe, ok = 0 or 1
        sql_pg     = ("SELECT did, SUM(ok) AS up, COUNT(*) AS total "
                      "FROM sensor_samples WHERE ts>=%s AND ts<%s GROUP BY did")
        sql_sqlite = ("SELECT did, SUM(ok), COUNT(*) "
                      "FROM sensor_samples WHERE ts>=? AND ts<? GROUP BY did")

    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(sql_pg, (start_ts, end_ts))
                for r in cur.fetchall():
                    results[r["did"]] = {"up": int(r["up"] or 0),
                                         "total": int(r["total"] or 0)}
        except Exception as e:
            log.error(f"reports.data availability PG error ({table}): {e}")
    else:
        con = None
        try:
            con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
            rows = con.execute(sql_sqlite, (start_ts, end_ts)).fetchall()
            for did, up, total in rows:
                results[did] = {"up": int(up or 0), "total": int(total or 0)}
        except Exception as e:
            log.error(f"reports.data availability SQLite error ({table}): {e}")
        finally:
            if con: con.close()

    # Join with device metadata
    scope = set(device_ids) if device_ids else None
    out = []
    with STATE._lock:
        for did, dev in STATE.devices.items():
            if scope and did not in scope:
                continue
            agg = results.get(did, {"up": 0, "total": 0})
            total = agg["total"]
            up    = agg["up"]
            pct   = round(up / total * 100, 2) if total else None
            out.append({
                "did":    did,
                "dname":  dev.name,
                "host":   getattr(dev, "host", ""),
                "group":  getattr(dev, "group", ""),
                "total":  total,
                "up":     up,
                "fail":   total - up,
                "pct":    pct,
            })
    out.sort(key=lambda r: (r["pct"] if r["pct"] is not None else 999, -r["fail"]))
    return out


def _overall_availability(rows: list) -> dict:
    """Aggregate per-device rows into a single headline uptime %."""
    total = sum(r["total"] for r in rows)
    up    = sum(r["up"]    for r in rows)
    pct   = round(up / total * 100, 3) if total else None
    return {"up": up, "total": total, "pct": pct}


# ── Incidents (flaps) ──────────────────────────────────────────────────

def _epoch_to_iso(ts: float) -> str:
    """Convert a Unix epoch to 'YYYY-MM-DDTHH:MM:SSZ' — matches flap_log.ts format."""
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(v):
    """Parse whatever flap_log.ts contains → Unix epoch float (or None)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).strip()
        if not s:
            return None
        # Numeric-string timestamp?
        try:
            return float(s)
        except ValueError:
            pass
        # ISO 8601 with Z suffix
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _flaps_in_window(start_ts: float, end_ts: float) -> list:
    """Return all flap events (incidents) in the window, oldest first.

    flap_log.ts is stored as an ISO-8601 string ("YYYY-MM-DDTHH:MM:SSZ").
    We compare as text (ISO-8601 sorts correctly) then convert to epoch for
    downstream charts/filters.
    """
    start_iso = _epoch_to_iso(start_ts)
    end_iso   = _epoch_to_iso(end_ts)

    rows = []
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "SELECT ts,did,sid,dname,sname,host,stype,direction,detail,"
                    "COALESCE(duration,0) AS duration,"
                    "COALESCE(resolved_at,0) AS resolved_at "
                    "FROM flap_log WHERE ts >= %s AND ts < %s "
                    "AND direction NOT IN ('recovered','threshold_ok') "
                    "ORDER BY ts ASC",
                    (start_iso, end_iso)
                )
                rows = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            log.error(f"reports.data flaps PG error: {e}")
    else:
        con = None
        try:
            con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
            cur = con.execute(
                "SELECT ts,did,sid,dname,sname,host,stype,direction,detail,"
                "COALESCE(duration,0),COALESCE(resolved_at,0) "
                "FROM flap_log WHERE ts>=? AND ts<? "
                "AND direction NOT IN ('recovered','threshold_ok') "
                "ORDER BY ts ASC",
                (start_iso, end_iso)
            )
            for r in cur.fetchall():
                rows.append({
                    "ts": r[0], "did": r[1], "sid": r[2], "dname": r[3],
                    "sname": r[4], "host": r[5], "stype": r[6],
                    "direction": r[7], "detail": r[8],
                    "duration": r[9], "resolved_at": r[10],
                })
        except Exception as e:
            log.error(f"reports.data flaps SQLite error: {e}")
        finally:
            if con: con.close()

    # Normalize ts back to epoch floats for downstream code (charts expect floats)
    for r in rows:
        r["ts"] = _parse_ts(r.get("ts")) or 0.0
    return rows


def _severity_of(direction: str) -> str:
    """Map a flap direction string to a severity bucket: 'crit' | 'warn' | 'other'."""
    d = (direction or "").lower()
    if d in ("down", "threshold_crit"):             return "crit"
    if d in ("threshold_warn", "anomaly_warn"):     return "warn"
    return "other"


def _filter_flaps_by_severity(flaps: list, severity_min: str) -> list:
    """Drop flaps below the configured severity floor."""
    floor = (severity_min or "all").lower()
    if floor in ("all", "", "other"):
        return flaps
    if floor == "warn":
        return [f for f in flaps if _severity_of(f.get("direction")) in ("crit", "warn")]
    if floor == "crit":
        return [f for f in flaps if _severity_of(f.get("direction")) == "crit"]
    return flaps


def _severity_counts(flaps: list) -> dict:
    """Group flaps by severity bucket."""
    out = {"crit": 0, "warn": 0, "ack": 0, "resolved": 0, "total": len(flaps)}
    for f in flaps:
        sev = _severity_of(f.get("direction"))
        if sev == "crit": out["crit"] += 1
        elif sev == "warn": out["warn"] += 1
        if f.get("resolved_at"):
            out["resolved"] += 1
    return out


def _top_worst_devices(avail: list, n: int = 5) -> list:
    """Devices with the lowest uptime % (filtered to those with data)."""
    with_data = [r for r in avail if r["pct"] is not None]
    return sorted(with_data, key=lambda r: r["pct"])[:n]


def _top_noisy_sensors(flaps: list, n: int = 5) -> list:
    """Sensors with the most incidents."""
    agg: dict = {}
    for f in flaps:
        key = (f.get("did"), f.get("sid"))
        if not key[0]:
            continue
        a = agg.setdefault(key, {
            "did": f.get("did"), "sid": f.get("sid"),
            "dname": f.get("dname", ""), "sname": f.get("sname", ""),
            "stype": f.get("stype", ""), "count": 0,
        })
        a["count"] += 1
    return sorted(agg.values(), key=lambda r: -r["count"])[:n]


def _mttr_mtbf(flaps: list, window_s: float) -> dict:
    """
    Mean time to recovery + mean time between failures.

    MTTR: average duration of resolved incidents (seconds).
    MTBF: window_s / count_of_failures (seconds).
    """
    durations = [f["duration"] for f in flaps if f.get("duration")]
    mttr = (sum(durations) / len(durations)) if durations else None
    fails = sum(1 for f in flaps
                if (f.get("direction") or "") in ("down", "threshold_crit"))
    mtbf = (window_s / fails) if fails else None
    return {"mttr_s": mttr, "mtbf_s": mtbf, "resolved": len(durations), "fails": fails}


# ── Latency percentiles ────────────────────────────────────────────────

def _latency_percentiles(start_ts: float, end_ts: float, limit: int = 100) -> list:
    """
    Per-sensor latency stats over the window.

    For windows ≤ 1 day we compute true percentiles from raw samples.
    For longer windows we read from the rollup tables — which only store
    avg_ms / min_ms / max_ms per 5-minute or 1-hour bucket — so the
    "p95" / "p99" columns are best-effort from bucket maxima, and "p50"
    from sample-weighted averages. We flag this in the sample count so
    the template can distinguish (samples == number of BUCKETS, not probes).
    """
    from core.app_state import STATE
    from db.samples    import _pick_table

    minutes = max(1, int((end_ts - start_ts) / 60))
    table, _bucket = _pick_table(minutes)
    is_rollup = table != "sensor_samples"

    # did,sid → list of (ms, count) tuples (count = 1 for raw rows)
    buckets: dict = {}

    if is_rollup:
        # rollup: one bucket row per (did,sid,ts). Aggregate its avg_ms (weighted by sample_count) and max_ms separately.
        sql_pg = (f"SELECT did, sid, "
                  f"SUM(COALESCE(avg_ms,0) * COALESCE(sample_count,0)) AS wsum, "
                  f"SUM(COALESCE(sample_count,0)) AS n, "
                  f"MAX(COALESCE(max_ms,0)) AS mx, "
                  f"MIN(COALESCE(min_ms, 999999)) AS mn "
                  f"FROM {table} WHERE ts>=%s AND ts<%s "
                  f"AND ok_count > 0 "
                  f"GROUP BY did, sid")
        sql_sqlite = (f"SELECT did, sid, "
                      f"SUM(COALESCE(avg_ms,0) * COALESCE(sample_count,0)), "
                      f"SUM(COALESCE(sample_count,0)), "
                      f"MAX(COALESCE(max_ms,0)), "
                      f"MIN(COALESCE(min_ms, 999999)) "
                      f"FROM {table} WHERE ts>=? AND ts<? "
                      f"AND ok_count > 0 "
                      f"GROUP BY did, sid")
        rows = []
        if is_pg():
            from db.pg_pool import pg_cursor
            try:
                with pg_cursor("logs") as cur:
                    cur.execute(sql_pg, (start_ts, end_ts))
                    rows = [(r["did"], r["sid"],
                             float(r["wsum"] or 0), int(r["n"] or 0),
                             float(r["mx"] or 0), float(r["mn"] or 0))
                            for r in cur.fetchall()]
            except Exception as e:
                log.error(f"reports.data latency PG error ({table}): {e}")
        else:
            con = None
            try:
                con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
                for r in con.execute(sql_sqlite, (start_ts, end_ts)).fetchall():
                    rows.append((r[0], r[1], float(r[2] or 0),
                                 int(r[3] or 0), float(r[4] or 0), float(r[5] or 0)))
            except Exception as e:
                log.error(f"reports.data latency SQLite error ({table}): {e}")
            finally:
                if con: con.close()

        out = []
        with STATE._lock:
            for did, sid, wsum, n, mx, mn in rows:
                if n <= 0:
                    continue
                dev = STATE.devices.get(did)
                if not dev:
                    continue
                s = dev.sensors.get(sid)
                if not s:
                    continue
                avg = round(wsum / n, 1)
                # Rollups don't store true percentiles — approximate:
                #   p50 ≈ weighted avg,  p95/p99 ≈ bucket max (upper bound)
                out.append({
                    "did": did, "sid": sid,
                    "dname": dev.name, "sname": s.name, "stype": s.stype,
                    "samples": n,
                    "p50": avg,
                    "p95": round(mx, 1),
                    "p99": round(mx, 1),
                    "min": round(mn, 1) if mn < 999999 else None,
                    "max": round(mx, 1),
                    "approx": True,
                })
        out.sort(key=lambda r: -(r.get("p95") or 0))
        return out[:limit]

    # Raw-table path: true percentiles
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "SELECT did, sid, ms FROM sensor_samples "
                    "WHERE ts>=%s AND ts<%s AND ok=1 AND ms IS NOT NULL",
                    (start_ts, end_ts)
                )
                for r in cur.fetchall():
                    buckets.setdefault((r["did"], r["sid"]), []).append(float(r["ms"]))
        except Exception as e:
            log.error(f"reports.data latency PG error: {e}")
    else:
        con = None
        try:
            con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
            rows = con.execute(
                "SELECT did, sid, ms FROM sensor_samples "
                "WHERE ts>=? AND ts<? AND ok=1 AND ms IS NOT NULL",
                (start_ts, end_ts)
            ).fetchall()
            for did, sid, ms in rows:
                buckets.setdefault((did, sid), []).append(float(ms))
        except Exception as e:
            log.error(f"reports.data latency SQLite error: {e}")
        finally:
            if con: con.close()

    def pct(arr, p):
        if not arr: return None
        s = sorted(arr)
        k = max(0, min(len(s) - 1, int(round(p / 100 * (len(s) - 1)))))
        return round(s[k], 1)

    out = []
    with STATE._lock:
        for (did, sid), arr in buckets.items():
            dev = STATE.devices.get(did)
            if not dev:
                continue
            s = dev.sensors.get(sid)
            if not s:
                continue
            out.append({
                "did": did, "sid": sid,
                "dname": dev.name, "sname": s.name, "stype": s.stype,
                "samples": len(arr),
                "p50": pct(arr, 50),
                "p95": pct(arr, 95),
                "p99": pct(arr, 99),
                "approx": False,
            })
    out.sort(key=lambda r: -(r["p95"] or 0))
    return out[:limit]


# ── SNMP traps ─────────────────────────────────────────────────────────

def _top_traps(start_ts: float, end_ts: float, n: int = 10) -> list:
    """Top N SNMP trap types (by count) in the window. ts is ISO-8601 text.

    Groups on (trap_name, trap_oid). When trap_name is empty we fall back to the
    OID; when both are empty the row is labelled 'Unidentified trap'. Empty
    vendor renders as '—' rather than a blank cell. This keeps the report
    presentable even when enrichment hasn't run yet.
    """
    start_iso = _epoch_to_iso(start_ts)
    end_iso   = _epoch_to_iso(end_ts)
    rows = []
    try:
        if is_pg():
            from db.pg_pool import pg_cursor
            with pg_cursor("logs") as cur:
                cur.execute(
                    "SELECT COALESCE(trap_name,'') AS name, "
                    "COALESCE(trap_oid,'') AS oid, "
                    "COALESCE(vendor,'') AS vendor, COALESCE(severity,'') AS severity, "
                    "COUNT(*) AS c "
                    "FROM snmp_traps WHERE ts >= %s AND ts < %s "
                    "GROUP BY name, oid, vendor, severity "
                    "ORDER BY c DESC LIMIT %s",
                    (start_iso, end_iso, n)
                )
                raw = [(r["name"], r["oid"], r["vendor"], r["severity"], int(r["c"]))
                       for r in cur.fetchall()]
        else:
            con = None
            try:
                con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
                cur = con.execute(
                    "SELECT COALESCE(trap_name,''), COALESCE(trap_oid,''), "
                    "COALESCE(vendor,''), COALESCE(severity,''), COUNT(*) "
                    "FROM snmp_traps WHERE ts>=? AND ts<? "
                    "GROUP BY trap_name, trap_oid, vendor, severity "
                    "ORDER BY 5 DESC LIMIT ?",
                    (start_iso, end_iso, n)
                )
                raw = [(r[0], r[1], r[2], r[3], int(r[4])) for r in cur.fetchall()]
            finally:
                if con: con.close()

        for name, oid, vendor, severity, count in raw:
            nm = (name or "").strip()
            od = (oid or "").strip()
            label = nm or od or "Unidentified trap"
            rows.append({
                "name":     label,
                "vendor":   (vendor or "").strip() or "—",
                "severity": (severity or "").strip() or "info",
                "count":    count,
            })
    except Exception as e:
        log.error(f"reports.data top_traps error: {e}")
    return rows


# ── TLS certs expiring ─────────────────────────────────────────────────

def _tls_expiring(days_ahead: int = 90) -> list:
    """
    Scan in-memory TLS sensor state for certificates expiring within N days.
    We use each sensor's last_value (days-until-expiry) as recorded by probe_tls.
    """
    from core.app_state import STATE
    out = []
    now = time.time()
    with STATE._lock:
        for dev in STATE.devices.values():
            for sid, s in dev.sensors.items():
                if getattr(s, "stype", "") != "tls":
                    continue
                days = getattr(s, "last_value", None)
                if days is None:
                    continue
                try:
                    d = float(days)
                except Exception:
                    continue
                if d > days_ahead:
                    continue
                bucket = ("expired"   if d <= 0 else
                          "30"        if d <= 30 else
                          "60"        if d <= 60 else
                          "90")
                out.append({
                    "did": dev.device_id,
                    "dname": dev.name,
                    "sname": s.name,
                    "host":  getattr(s, "host", "") or getattr(dev, "host", ""),
                    "days":  round(d, 1),
                    "bucket": bucket,
                    "last_ms": getattr(s, "last_ms", None),
                })
    out.sort(key=lambda r: r["days"])
    return out


# ── Maintenance windows in range ───────────────────────────────────────

def _maint_windows(start_ts: float, end_ts: float) -> list:
    rows = db_query("main",
                    "SELECT id,name,start_ts,end_ts FROM maintenance_windows "
                    "WHERE end_ts>=? AND start_ts<? "
                    "ORDER BY start_ts",
                    (start_ts, end_ts))
    out = []
    for r in rows:
        d = dict(r)
        dur = max(0, min(d["end_ts"], end_ts) - max(d["start_ts"], start_ts))
        d["duration_s"] = dur
        out.append(d)
    return out


# ── Device / sensor counts for cover page ──────────────────────────────

def _inventory_summary() -> dict:
    from core.app_state import STATE
    dev_count = 0
    sen_count = 0
    dev_up = dev_down = dev_warn = 0
    with STATE._lock:
        for dev in STATE.devices.values():
            dev_count += 1
            sen_count += len(dev.sensors)
            alive = any(getattr(s, "alive", False) for s in dev.sensors.values())
            any_down = any(getattr(s, "alive", False) is False for s in dev.sensors.values())
            if alive and not any_down: dev_up += 1
            elif alive: dev_warn += 1
            else: dev_down += 1
    return {"devices": dev_count, "sensors": sen_count,
            "dev_up": dev_up, "dev_warn": dev_warn, "dev_down": dev_down}


# ── Inventory / compliance payload ─────────────────────────────────────

def _inventory_devices() -> list:
    """All devices with live status + sensor counts (from STATE)."""
    from core.app_state import STATE
    out = []
    with STATE._lock:
        for dev in STATE.devices.values():
            sensors = list(dev.sensors.values())
            up    = sum(1 for s in sensors if getattr(s, "alive", False))
            down  = sum(1 for s in sensors if getattr(s, "alive", None) is False)
            types = sorted({getattr(s, "stype", "") for s in sensors if getattr(s, "stype", None)})
            out.append({
                "did":          dev.device_id,
                "dname":        dev.name,
                "host":         getattr(dev, "host", ""),
                "group":        getattr(dev, "group", ""),
                "sensor_count": len(sensors),
                "up":           up,
                "down":         down,
                "types":        types,
            })
    out.sort(key=lambda r: r["dname"].lower())
    return out


def _inventory_backup_coverage() -> dict:
    """Backup coverage: X of Y devices have recent backups; which are stale/never.

    db_get_backup_list() only returns the device id — the friendly name lives
    in STATE.devices, so we resolve it there.
    """
    try:
        from db.backups    import db_get_backup_list
        from core.app_state import STATE
    except Exception:
        return {"total": 0, "recent": 0, "stale": 0, "never": 0, "stale_list": [], "never_list": []}

    bl = db_get_backup_list() or []
    name_by_did: dict = {}
    with STATE._lock:
        for dev in STATE.devices.values():
            name_by_did[dev.device_id] = dev.name

    now = time.time()
    stale_cutoff = now - 7 * 86400
    recent, stale_list, never_list = [], [], []
    for b in bl:
        did = b.get("did")
        # Skip records for devices that no longer exist — they can't be acted
        # on and showing raw IDs in a report looks unprofessional.
        if did not in name_by_did:
            continue
        dname = name_by_did[did]
        ts = _parse_ts(b.get("last_ts"))   # backup_runs.ts is ISO-8601 TEXT
        if not ts:
            never_list.append({"did": did, "dname": dname})
        elif ts < stale_cutoff:
            stale_list.append({"did": did, "dname": dname,
                               "last_ts": ts,
                               "days": round((now - ts) / 86400, 1)})
        else:
            recent.append(b)
    total = len(recent) + len(stale_list) + len(never_list)
    return {
        "total":      total,
        "recent":     len(recent),
        "stale":      len(stale_list),
        "never":      len(never_list),
        "stale_list": sorted(stale_list, key=lambda r: r["last_ts"]),
        "never_list": sorted(never_list, key=lambda r: (r["dname"] or "").lower()),
    }


def _inventory_ipam() -> list:
    """Subnet utilisation. Returns [{cidr, name, total, used, pct_used}]."""
    try:
        from db.ipam import db_list_subnets, db_get_allocations
    except Exception:
        return []
    import ipaddress
    out = []
    for s in db_list_subnets() or []:
        try:
            net = ipaddress.ip_network(s["cidr"], strict=False)
            total = max(1, net.num_addresses - (2 if net.prefixlen < 31 else 0))
            alloc = db_get_allocations(s["id"]) or {}
            # Any row in ip_allocations for this subnet is a tracked/used IP —
            # rows are only created when a device binds the IP or the user
            # labels it. There is no separate 'status' column.
            if isinstance(alloc, dict):
                used = sum(1 for v in alloc.values()
                           if isinstance(v, dict)
                           and (v.get("name") or v.get("device_id") or v.get("dns_name")))
            else:
                used = 0
        except Exception:
            total, used = 0, 0
        out.append({
            "cidr": s["cidr"], "name": s.get("name", ""),
            "total": total, "used": used,
            "pct_used": round(used / total * 100, 1) if total else None,
        })
    out.sort(key=lambda r: -(r["pct_used"] or 0))
    return out


def _inventory_licenses() -> dict:
    """Summary of device licenses by state. Resolves friendly device names from STATE."""
    try:
        from db.licenses   import db_get_all_licenses
        from core.app_state import STATE
    except Exception:
        return {"total": 0, "ok": 0, "warn": 0, "crit": 0, "expired": 0, "expiring_list": []}

    items = db_get_all_licenses() or []
    name_by_did: dict = {}
    with STATE._lock:
        for dev in STATE.devices.values():
            name_by_did[dev.device_id] = dev.name

    out = {"total": len(items), "ok": 0, "warn": 0, "crit": 0, "expired": 0, "expiring_list": []}
    today = datetime.date.today()
    for lic in items:
        st = (lic.get("last_status") or "ok").lower()
        if st in out: out[st] += 1
        # Build a 90-day expiring watch list
        try:
            exp = datetime.date.fromisoformat(lic.get("expiry_date") or "")
            days = (exp - today).days
            if days <= 90:
                did = lic.get("did")
                out["expiring_list"].append({
                    "did":     did,
                    "dname":   name_by_did.get(did) or did or "",
                    "name":    lic.get("license_name", ""),
                    "expires": lic.get("expiry_date", ""),
                    "days":    days,
                })
        except Exception:
            pass
    out["expiring_list"].sort(key=lambda r: r["days"])
    return out


def _inventory_audit(limit: int = 50) -> list:
    """Recent audit entries — admin actions / logins."""
    try:
        from db.audit import db_get_audit
    except Exception:
        return []
    return (db_get_audit(limit) or [])


def _inventory_users() -> dict:
    """User breakdown by role."""
    try:
        from db.users import db_list_users
    except Exception:
        return {"total": 0, "admin": 0, "operator": 0, "viewer": 0, "ldap": 0}
    users = db_list_users() or []
    out = {"total": len(users), "admin": 0, "operator": 0, "viewer": 0, "ldap": 0}
    for u in users:
        role = (u.get("role") or "viewer").lower()
        if role in out: out[role] += 1
        if (u.get("auth_type") or "").lower() == "ldap":
            out["ldap"] += 1
    return out


# ── Public entrypoint ──────────────────────────────────────────────────

def build_report_context(kind: str,
                         period: str = "last_month",
                         filters: dict = None,
                         config: dict = None,
                         anchor_ts: float = None) -> dict:
    """
    Assemble the full report context consumable by the Jinja2 templates.

    Args:
      kind     — 'executive' | 'technical' | 'custom'
      period   — symbolic period (see resolve_period)
      filters  — {'device_ids': [...], 'group': '...'} (optional)
      config   — per-template overrides (sections to include, intro text, etc.)
    Returns:
      dict with keys: meta, company, period, inventory, uptime, incidents,
                      sections (per-kind payload)
    """
    t0 = time.time()
    filters = filters or {}
    config  = config  or {}

    start_ts, end_ts, period_label = resolve_period(period, anchor_ts)
    window_s = max(1, end_ts - start_ts)

    device_ids  = filters.get("device_ids") or None
    severity_min = (config.get("severity_min") or "all").lower()

    # ── Custom-kind section gating ────────────────────────────────────
    # For kind=custom, `config.sections` is a strict whitelist: only
    # compute + include the blocks the user ticked. Other kinds keep
    # their original, hard-wired behaviour.
    sections  = set(config.get("sections") or [])
    opts      = config.get("options") or {}
    is_custom = (kind == "custom")
    def want(name: str) -> bool:
        return is_custom and (name in sections)

    _AVAIL_SECS = {"overall_uptime", "availability_trend",
                   "per_device_uptime", "top_worst_devices"}
    _INC_SECS   = {"incident_summary", "incident_timeline",
                   "top_noisy_sensors", "incident_log"}
    need_avail = (not is_custom) or bool(sections & _AVAIL_SECS)
    need_flaps = (not is_custom) or bool(sections & _INC_SECS)

    top_worst_n  = int(opts.get("top_worst_n", 5))  if is_custom else 5
    top_noisy_n  = int(opts.get("top_noisy_n", 5))  if is_custom else 5

    availability = _availability_by_device(start_ts, end_ts, device_ids) if need_avail else []
    overall      = _overall_availability(availability) if need_avail else {"up": 0, "total": 0, "pct": None}
    flaps_raw    = _flaps_in_window(start_ts, end_ts) if need_flaps else []
    flaps        = _filter_flaps_by_severity(flaps_raw, severity_min)
    severity     = _severity_counts(flaps)
    worst        = _top_worst_devices(availability, top_worst_n)
    noisy        = _top_noisy_sensors(flaps, top_noisy_n)
    mtr          = _mttr_mtbf(flaps, window_s)

    # ── Company branding ─────────────────────────────────────────────
    company = {
        "name":   _cfg("org_name", "") or _cfg("company_name", "") or _cfg("report_company_name", "") or "PingWatch",
        "logo":   _cfg("email_logo_data", "") or _cfg("report_logo_data", ""),
        "color":  _cfg("report_brand_color", "") or _cfg("theme_accent", "") or "#0969da",
        "footer": _cfg("report_footer_text", ""),
        "tz":     _cfg("report_tz", "") or time.strftime("%Z"),
    }

    meta = {
        "kind":         kind,
        "title":        config.get("title") or _default_title(kind),
        "subtitle":     config.get("subtitle", ""),
        "intro":        config.get("intro", ""),
        "generated_at": time.time(),
        "generated_by": config.get("triggered_by", ""),
        "render_ms":    None,   # filled below
        "severity_min": severity_min,
        "sections":     sorted(sections) if is_custom else [],
    }

    period_dict = {
        "start_ts": start_ts,
        "end_ts":   end_ts,
        "label":    period_label,
        "days":     round(window_s / 86400, 1),
    }

    ctx = {
        "meta":         meta,
        "company":      company,
        "period":       period_dict,
        "inventory":    _inventory_summary(),
        "availability": availability,
        "overall":      overall,
        "incidents":    {
            "flaps":    flaps,
            "severity": severity,
            "worst_5":  worst,
            "noisy_5":  noisy,
            "mttr":     mtr,
        },
    }

    if kind == "technical" or want("latency_percentiles"):
        lat_limit = int(opts.get("latency_top_n", 100)) if is_custom else 100
        ctx["latency"] = _latency_percentiles(start_ts, end_ts, limit=lat_limit)
    if kind == "technical" or want("snmp_traps"):
        trap_n = int(opts.get("top_traps_n", 10)) if is_custom else 10
        ctx["top_traps"] = _top_traps(start_ts, end_ts, trap_n)
    if kind == "technical" or kind == "inventory" or want("tls_expiring"):
        tls_days = int(opts.get("tls_days_ahead", 90)) if is_custom else 90
        ctx["tls_expiring"] = _tls_expiring(tls_days)

    if kind == "inventory" or want("device_inventory"):
        ctx["inventory_devices"] = _inventory_devices()
    if kind == "inventory" or want("backup_coverage"):
        ctx["backup_coverage"]   = _inventory_backup_coverage()
    if kind == "inventory" or want("ipam"):
        ctx["ipam"]              = _inventory_ipam()
    if kind == "inventory" or want("licenses"):
        ctx["licenses"]          = _inventory_licenses()
    if kind == "inventory" or want("estate_overview"):
        ctx["users"]             = _inventory_users()
    if kind == "inventory" or want("audit_log"):
        audit_lim = int(opts.get("audit_limit", 50)) if is_custom else 50
        ctx["audit_recent"]      = _inventory_audit(audit_lim)

    if (not is_custom) or want("maint_windows") or want("incident_summary"):
        ctx["maint_windows"] = _maint_windows(start_ts, end_ts)
    else:
        ctx["maint_windows"] = []

    # ── Compare-to-previous-period (Δ) ─────────────────────────────
    # Only compute for windowed report kinds where it actually means something.
    _custom_wants_compare = is_custom and bool(sections & (_AVAIL_SECS | _INC_SECS))
    if (kind in ("executive", "technical") or _custom_wants_compare) and config.get("compare", True):
        prev_end   = start_ts
        prev_start = start_ts - window_s
        try:
            prev_avail    = _availability_by_device(prev_start, prev_end, device_ids)
            prev_overall  = _overall_availability(prev_avail)
            prev_flaps    = _filter_flaps_by_severity(
                                _flaps_in_window(prev_start, prev_end), severity_min)
            prev_sev      = _severity_counts(prev_flaps)
            prev_mtr      = _mttr_mtbf(prev_flaps, window_s)
            def _delta(now, before):
                if now is None or before is None:
                    return None
                return round(now - before, 3)
            ctx["previous"] = {
                "label":    f"previous {round(window_s/86400,0):.0f} days",
                "start_ts": prev_start, "end_ts": prev_end,
                "overall":  prev_overall,
                "severity": prev_sev,
                "mttr":     prev_mtr,
                "delta": {
                    "uptime_pp":    _delta(overall.get("pct"),   prev_overall.get("pct")),
                    "incidents":    _delta(severity.get("total"),prev_sev.get("total")),
                    "crit":         _delta(severity.get("crit"), prev_sev.get("crit")),
                    "warn":         _delta(severity.get("warn"), prev_sev.get("warn")),
                    "mttr_s":       _delta(mtr.get("mttr_s"),    prev_mtr.get("mttr_s")),
                },
            }
        except Exception as e:
            log.debug(f"reports.data previous-period compute failed: {e}")
            ctx["previous"] = None

    meta["render_ms"] = int((time.time() - t0) * 1000)
    return ctx


def _default_title(kind: str) -> str:
    return {
        "executive": "Network Monitoring Report — Executive Summary",
        "technical": "Network Monitoring Report — Technical / Operations",
        "inventory": "Network Inventory & Compliance Report",
        "custom":    "Network Monitoring Report",
    }.get(kind, "Network Monitoring Report")
