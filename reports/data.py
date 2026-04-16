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
    Derived from sensor_samples joined with in-memory STATE for device names.
    """
    from core.app_state import STATE

    # Aggregate per device_id from raw samples over the window
    results: dict = {}
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "SELECT did, SUM(ok) AS up, COUNT(*) AS total "
                    "FROM sensor_samples WHERE ts>=%s AND ts<%s "
                    "GROUP BY did",
                    (start_ts, end_ts)
                )
                for r in cur.fetchall():
                    results[r["did"]] = {"up": int(r["up"] or 0),
                                         "total": int(r["total"] or 0)}
        except Exception as e:
            log.error(f"reports.data availability PG error: {e}")
    else:
        con = None
        try:
            con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
            rows = con.execute(
                "SELECT did, SUM(ok), COUNT(*) "
                "FROM sensor_samples WHERE ts>=? AND ts<? "
                "GROUP BY did",
                (start_ts, end_ts)
            ).fetchall()
            for did, up, total in rows:
                results[did] = {"up": int(up or 0), "total": int(total or 0)}
        except Exception as e:
            log.error(f"reports.data availability SQLite error: {e}")
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

def _flaps_in_window(start_ts: float, end_ts: float) -> list:
    """Return all flap events (incidents) in the window, oldest first."""
    rows = []
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("logs") as cur:
                cur.execute(
                    "SELECT ts,did,sid,dname,sname,host,stype,direction,detail,"
                    "COALESCE(duration,0) AS duration,"
                    "COALESCE(resolved_at,0) AS resolved_at "
                    "FROM flap_log "
                    "WHERE CAST(ts AS DOUBLE PRECISION) >= %s "
                    "AND   CAST(ts AS DOUBLE PRECISION) <  %s "
                    "AND direction NOT IN ('recovered','threshold_ok') "
                    "ORDER BY CAST(ts AS DOUBLE PRECISION) ASC",
                    (start_ts, end_ts)
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
                (start_ts, end_ts)
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
    return rows


def _severity_counts(flaps: list) -> dict:
    """Group flaps by severity bucket."""
    out = {"crit": 0, "warn": 0, "ack": 0, "resolved": 0, "total": len(flaps)}
    for f in flaps:
        d = (f.get("direction") or "").lower()
        if d in ("down", "threshold_crit"):
            out["crit"] += 1
        elif d in ("threshold_warn", "anomaly_warn"):
            out["warn"] += 1
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
    Per-sensor p50/p95/p99 over the window.
    Computed in Python from a sampled row set — cheap at a few 100k rows,
    expensive beyond. For reporting we cap the per-sensor sample.
    """
    from core.app_state import STATE

    # Collect ms arrays per (did,sid)
    buckets: dict = {}
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

    # Resolve names from STATE
    out = []
    with STATE._lock:
        for (did, sid), arr in buckets.items():
            dev = STATE.devices.get(did)
            if not dev:
                continue
            s = None
            for _sid, _s in dev.sensors.items():
                if _sid == sid:
                    s = _s
                    break
            if not s:
                continue
            out.append({
                "did": did, "sid": sid,
                "dname": dev.name, "sname": s.name, "stype": s.stype,
                "samples": len(arr),
                "p50": pct(arr, 50),
                "p95": pct(arr, 95),
                "p99": pct(arr, 99),
            })
    out.sort(key=lambda r: -(r["p95"] or 0))
    return out[:limit]


# ── SNMP traps ─────────────────────────────────────────────────────────

def _top_traps(start_ts: float, end_ts: float, n: int = 10) -> list:
    """Top N SNMP trap types (by count) in the window."""
    rows = []
    try:
        if is_pg():
            from db.pg_pool import pg_cursor
            with pg_cursor("logs") as cur:
                cur.execute(
                    "SELECT COALESCE(trap_name,'') AS name, "
                    "COALESCE(vendor,'') AS vendor, COALESCE(severity,'') AS severity, "
                    "COUNT(*) AS c "
                    "FROM snmp_traps "
                    "WHERE CAST(ts AS DOUBLE PRECISION) >= %s "
                    "AND   CAST(ts AS DOUBLE PRECISION) <  %s "
                    "GROUP BY name, vendor, severity "
                    "ORDER BY c DESC LIMIT %s",
                    (start_ts, end_ts, n)
                )
                for r in cur.fetchall():
                    rows.append({"name": r["name"] or "(unknown)",
                                 "vendor": r["vendor"], "severity": r["severity"],
                                 "count": int(r["c"])})
        else:
            con = None
            try:
                con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
                cur = con.execute(
                    "SELECT COALESCE(trap_name,''), COALESCE(vendor,''), "
                    "COALESCE(severity,''), COUNT(*) "
                    "FROM snmp_traps WHERE ts>=? AND ts<? "
                    "GROUP BY trap_name, vendor, severity "
                    "ORDER BY 4 DESC LIMIT ?",
                    (start_ts, end_ts, n)
                )
                for r in cur.fetchall():
                    rows.append({"name": r[0] or "(unknown)",
                                 "vendor": r[1], "severity": r[2],
                                 "count": int(r[3])})
            finally:
                if con: con.close()
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

    device_ids = filters.get("device_ids") or None
    availability = _availability_by_device(start_ts, end_ts, device_ids)
    overall      = _overall_availability(availability)
    flaps        = _flaps_in_window(start_ts, end_ts)
    severity     = _severity_counts(flaps)
    worst        = _top_worst_devices(availability, 5)
    noisy        = _top_noisy_sensors(flaps, 5)
    mtr          = _mttr_mtbf(flaps, window_s)

    # ── Company branding ─────────────────────────────────────────────
    company = {
        "name":   _cfg("company_name", "") or _cfg("report_company_name", "") or "PingWatch",
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

    if kind == "technical" or "latency" in (config.get("sections") or []):
        ctx["latency"] = _latency_percentiles(start_ts, end_ts)
    if kind == "technical" or "traps" in (config.get("sections") or []):
        ctx["top_traps"] = _top_traps(start_ts, end_ts, 10)
    if kind == "technical" or "tls" in (config.get("sections") or []):
        ctx["tls_expiring"] = _tls_expiring(90)

    ctx["maint_windows"] = _maint_windows(start_ts, end_ts)

    meta["render_ms"] = int((time.time() - t0) * 1000)
    return ctx


def _default_title(kind: str) -> str:
    return {
        "executive": "Network Monitoring Report — Executive Summary",
        "technical": "Network Monitoring Report — Technical / Operations",
        "custom":    "Network Monitoring Report",
    }.get(kind, "Network Monitoring Report")
