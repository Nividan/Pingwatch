"""
reports/data.py — Assemble the data context for a report.

Pure data: talks to the existing DB helpers only. No rendering, no I/O beyond
the DB. The output is a dict consumable by the Jinja2 templates.

Public entrypoint: build_report_context(kind, period, filters, config)
"""

import datetime
import re
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


# ── Config-issue classifier ───────────────────────────────────────────
# Patterns that mean "this sensor is misconfigured", not "the target is down".
# Rows matching any of these are routed out of the incident stream so managers
# don't see debug messages in their report.
_CONFIG_ISSUE_PATTERNS = [
    (re.compile(r"^Unknown metric:", re.I),                 "unknown_metric", "Unknown VMware metric"),
    (re.compile(r"^Metric .* not available",      re.I),    "missing_metric", "Metric not available on target"),
    (re.compile(r"^SSL error.*try disabling Verify SSL", re.I), "ssl_verify", "SSL verify failed — toggle Verify SSL"),
    (re.compile(r"CERTIFICATE_VERIFY_FAILED",     re.I),    "cert_verify",   "TLS certificate verification failed"),
    (re.compile(r"^Invalid OID format",           re.I),    "bad_oid",       "Malformed SNMP OID"),
]


def _classify_config_issues(flaps: list) -> tuple:
    """Split flaps into (real_incidents, config_issues_rollup).

    A flap whose ``detail`` matches any of the config-issue patterns above is
    removed from the incident stream — otherwise it inflates severity counts
    and the noisy-sensors top-N with things the user can't fix by monitoring,
    only by editing the sensor config.

    Aggregation key is ``(did, issue_type)`` — when one misconfiguration (e.g.
    a single wrong SSL setting on a vCenter connection) trips 30 sensors, we
    want ONE row showing the root cause, not 30 near-duplicates. The returned
    dict exposes ``sensor_count`` (distinct sids affected) alongside ``count``
    (total occurrences across all affected sensors).

    Returns:
      clean_flaps       — list[flap dict] — safe to feed to severity/noisy/log
      config_issues     — list[{did, dname, stype, type, label, count,
                                sensor_count, sample_detail, last_ts}]
    """
    clean = []
    agg = {}  # (did, type) → rollup dict
    for f in flaps:
        det = (f.get("detail") or "").strip()
        issue_type = issue_label = None
        if det:
            for pat, t, lbl in _CONFIG_ISSUE_PATTERNS:
                if pat.search(det):
                    issue_type, issue_label = t, lbl
                    break
        if not issue_type:
            clean.append(f)
            continue
        key = (f.get("did"), issue_type)
        a = agg.get(key)
        if a is None:
            a = {
                "did":   f.get("did"),
                "dname": f.get("dname", ""),
                "stype": f.get("stype", ""),
                "type":  issue_type, "label": issue_label,
                "count": 0, "_sids": set(),
                "sample_detail": det[:120], "last_ts": f.get("ts") or 0,
            }
            agg[key] = a
        a["count"] += 1
        if f.get("sid"):
            a["_sids"].add(f.get("sid"))
        if (f.get("ts") or 0) > a["last_ts"]:
            a["last_ts"] = f.get("ts") or 0
    issues = []
    for a in agg.values():
        a["sensor_count"] = len(a.pop("_sids"))
        issues.append(a)
    issues.sort(key=lambda r: (-r["count"], -r["sensor_count"]))
    return clean, issues


# ── Outage clustering ─────────────────────────────────────────────────

def _currently_bad_sensor_keys() -> set:
    """Return {(did, sid)} for sensors that are CURRENTLY in a bad state.

    "Bad" = ``alive is False`` OR ``threshold_state`` in {warn, crit}. Used to
    distinguish a truly-still-open incident from a historical flap_log row
    that simply never had ``resolved_at`` written (older builds recorded
    resolutions lazily, so many resolved rows carry 0). Without this cross-
    check, every such row would render as "open" — misleading the reader.
    """
    bad = set()
    try:
        from core.app_state import STATE
    except Exception:
        return bad
    try:
        with STATE._lock:
            for dev in STATE.devices.values():
                for sid, s in dev.sensors.items():
                    if not getattr(s, "running", True):
                        continue
                    alive = getattr(s, "alive", True)
                    thr = (getattr(s, "threshold_state", "") or "").lower()
                    if alive is False or thr in ("warn", "crit", "threshold_warn", "threshold_crit"):
                        bad.add((dev.device_id, sid))
    except Exception as e:
        log.debug(f"reports.data _currently_bad_sensor_keys failed: {e}")
    return bad


def _cluster_flaps_into_outages(flaps: list, idle_gap_s: float = 300.0,
                                currently_bad: set = None) -> list:
    """Collapse consecutive bad-state events for the same (did, sid) into
    one outage row — the managerial view of ``incident_log``.

    A new outage starts when the gap between the previous event's recovery
    (``resolved_at`` — or ``ts`` if never resolved) and the next event's
    ``ts`` exceeds ``idle_gap_s`` seconds. Default 5 minutes.

    ``currently_bad`` is a set of (did, sid) tuples from STATE whose sensors
    are presently unhealthy. Only outages for keys in this set may be flagged
    ``ongoing=True`` — historical rows with a missing ``resolved_at`` are
    reported with an unknown duration rather than a misleading "open".
    """
    if currently_bad is None:
        currently_bad = _currently_bad_sensor_keys()

    by_sensor: dict = {}
    for f in flaps:
        key = (f.get("did"), f.get("sid"))
        if not key[0]:
            continue
        by_sensor.setdefault(key, []).append(f)

    outages = []
    for (did, sid), events in by_sensor.items():
        is_live_bad = (did, sid) in currently_bad
        events.sort(key=lambda r: r.get("ts") or 0)
        cur = None
        for f in events:
            ts = f.get("ts") or 0
            resolved = f.get("resolved_at") or 0
            if cur is None:
                cur = _new_outage_from(f)
                continue
            # The previous event's "end" is either its resolve time or — if it
            # never resolved — its own ts. If we're within the idle gap, merge;
            # otherwise start a new outage.
            prev_end = cur["_running_end"] if cur["_running_end"] else cur["first_ts"]
            if ts - prev_end <= idle_gap_s:
                _merge_into_outage(cur, f)
            else:
                outages.append(_finalise_outage(cur, is_live_bad))
                cur = _new_outage_from(f)
        if cur is not None:
            outages.append(_finalise_outage(cur, is_live_bad))

    # Most recent first
    outages.sort(key=lambda o: -o["first_ts"])
    return outages


def _new_outage_from(f: dict) -> dict:
    ts = f.get("ts") or 0
    resolved = f.get("resolved_at") or 0
    det = (f.get("detail") or "").strip()
    sev = _severity_of(f.get("direction"))
    return {
        "did":   f.get("did"),   "sid":   f.get("sid"),
        "dname": f.get("dname", ""), "sname": f.get("sname", ""),
        "stype": f.get("stype", ""), "host":  f.get("host", ""),
        "first_ts":      ts,
        "_running_end":  resolved,        # max resolved_at seen, 0 if still open
        "event_count":   1,
        "max_severity":  sev,
        "sample_detail": det[:80],
        "_any_open":     not resolved,
    }


def _merge_into_outage(cur: dict, f: dict):
    resolved = f.get("resolved_at") or 0
    if resolved > (cur["_running_end"] or 0):
        cur["_running_end"] = resolved
    cur["event_count"] += 1
    if _severity_of(f.get("direction")) == "crit":
        cur["max_severity"] = "crit"
    if not cur["sample_detail"]:
        cur["sample_detail"] = (f.get("detail") or "").strip()[:80]
    if not resolved:
        cur["_any_open"] = True


def _finalise_outage(cur: dict, is_live_bad: bool = False) -> dict:
    end = cur["_running_end"] or None
    first = cur["first_ts"]
    # ONLY flag ongoing when the sensor is still unhealthy right now. Without
    # that cross-check, every historical flap with a missing resolved_at would
    # claim to be open — which is exactly the bug the UI surfaced.
    ongoing = bool(cur["_any_open"] and not end and is_live_bad)
    duration_s = None
    if end and end >= first:
        duration_s = end - first
    return {
        "did":             cur["did"],   "sid":   cur["sid"],
        "dname":           cur["dname"], "sname": cur["sname"],
        "stype":           cur["stype"], "host":  cur["host"],
        "first_ts":        first,
        "last_end":        end,
        "duration_s":      duration_s,
        "event_count":     cur["event_count"],
        "max_severity":    cur["max_severity"],
        "sample_detail":   cur["sample_detail"],
        "ongoing":         ongoing,
    }


# ── Major incident detection ──────────────────────────────────────────

def _detect_major_incidents(flaps: list, min_devices: int = 10,
                            gap_minutes: int = 5,
                            currently_bad: set = None) -> list:
    """Cluster simultaneous device-DOWN events into single Major Incidents.

    Only ``direction='down'`` rows count — threshold_crit is a value threshold,
    not a real offline event. Adjacent 1-minute buckets (or buckets separated
    by ≤ gap_minutes) are merged into one window; a window with ≥ min_devices
    distinct devices going DOWN inside it becomes a Major Incident.

    Pure stats. No root-cause inference. See plan for reasoning.
    """
    if min_devices < 1:
        min_devices = 1
    if currently_bad is None:
        currently_bad = _currently_bad_sensor_keys()
    downs = [f for f in flaps if (f.get("direction") or "").lower() == "down"]
    if not downs:
        return []
    downs.sort(key=lambda f: f.get("ts") or 0)

    # Collect into minute buckets
    buckets = {}  # minute (int) → list of flap dicts
    for f in downs:
        m = int((f.get("ts") or 0) // 60)
        buckets.setdefault(m, []).append(f)

    # Walk ordered minutes, merge any within gap_minutes of the previous minute
    minutes = sorted(buckets.keys())
    windows = []    # list of lists of flap dicts
    cur = []
    prev_m = None
    for m in minutes:
        if prev_m is None or (m - prev_m) <= gap_minutes:
            cur.extend(buckets[m])
        else:
            windows.append(cur)
            cur = list(buckets[m])
        prev_m = m
    if cur:
        windows.append(cur)

    # flap_log rows don't carry the device's GROUP — only STATE does. Resolve
    # (did → group, dname) once for the whole detection pass.
    try:
        from core.app_state import STATE
        dev_meta = {}
        with STATE._lock:
            for dev in STATE.devices.values():
                dev_meta[dev.device_id] = (
                    getattr(dev, "group", "") or "",
                    getattr(dev, "name", "") or "",
                )
    except Exception:
        dev_meta = {}

    majors = []
    for w in windows:
        distinct_devs = {f.get("did") for f in w if f.get("did")}
        if len(distinct_devs) < min_devices:
            continue
        start_ts = min(f.get("ts") or 0 for f in w)
        resolves = [f.get("resolved_at") or 0 for f in w]
        # A cluster counts as "ongoing" only if SOME event in it has no
        # resolve stamp AND at least one of that cluster's sensors is still
        # unhealthy right now. Two-week-old flaps that were never resolve-
        # stamped are not ongoing — they're just unrecorded.
        unstamped_keys = {(f.get("did"), f.get("sid"))
                          for f in w if not (f.get("resolved_at") or 0)}
        unresolved = bool(unstamped_keys & currently_bad)
        end_ts = max(resolves) if any(resolves) else None
        duration_s = None
        if end_ts and end_ts >= start_ts and not unresolved:
            duration_s = end_ts - start_ts
        dnames = sorted({
            (dev_meta.get(d, ("", ""))[1] or
             next((f.get("dname") for f in w if f.get("did") == d and f.get("dname")), "") or
             d)
            for d in distinct_devs
        })
        groups = sorted({dev_meta.get(d, ("", ""))[0] for d in distinct_devs} - {""})
        sensors = {(f.get("did"), f.get("sid")) for f in w}
        majors.append({
            "start_ts":         start_ts,
            "end_ts":           end_ts,
            "duration_s":       duration_s,
            "devices_affected": dnames,
            "device_count":     len(distinct_devs),
            "groups_affected":  groups,
            "sensors_affected": len(sensors),
            "ongoing":          unresolved,
            # Internal: did set, used by _suppress_outages_in_majors so the
            # Incident Log can drop per-sensor outages already rolled up here.
            "_dids_affected":   distinct_devs,
        })
    majors.sort(key=lambda m: -m["start_ts"])
    return majors


def _suppress_outages_in_majors(outages: list, majors: list) -> list:
    """Drop per-sensor outages already summarised by a Major Incident.

    An outage whose first event falls inside a Major Incident's time window
    AND whose device is one of the cluster's devices is redundant in the
    Incident Log — the Major Incidents table already counts it. Outages that
    start outside the window, or on devices unrelated to the cluster, are
    kept. Ongoing majors extend to +∞ so trailing events are caught.
    """
    if not majors or not outages:
        return outages
    windows = []
    for m in majors:
        start = m.get("start_ts") or 0
        if not start:
            continue
        end = m.get("end_ts") or 0
        if not end and m.get("duration_s"):
            end = start + m["duration_s"]
        if m.get("ongoing") or not end:
            end = float("inf")
        dids = set(m.get("_dids_affected") or ())
        if dids:
            windows.append((start, end, dids))
    if not windows:
        return outages
    kept = []
    for o in outages:
        ts = o.get("first_ts") or 0
        did = o.get("did")
        swallowed = False
        for (ws, we, wdids) in windows:
            if ws <= ts <= we and did in wdids:
                swallowed = True
                break
        if not swallowed:
            kept.append(o)
    return kept


# ── Device health score ───────────────────────────────────────────────

def _health_band(score: float) -> str:
    if score >= 90: return "good"
    if score >= 70: return "warn"
    return "bad"


def _device_health_scores(availability: list, flaps: list, limit: int = 25) -> list:
    """Per-device composite health score (0-100, higher = better).

    Formula (start at 100, subtract, floor at 0):
      downtime   : 50 × (1 − pct/100)       [0..50]
      incidents  : min(20, count × 0.5)      [0..20]
      currently DOWN : +20   (read from STATE)
      currently WARN : +10   (read from STATE)

    Returns rows sorted worst-to-best. limit ≤ 0 means "all".
    """
    try:
        from core.app_state import STATE
    except Exception:
        STATE = None

    # Incident counts by did (excluding config-issue rows — caller should
    # already have filtered those before calling us, but be defensive).
    inc_by_did = {}
    for f in flaps:
        d = f.get("did")
        if not d: continue
        inc_by_did[d] = inc_by_did.get(d, 0) + 1

    # Current status by did from in-memory STATE
    status_by_did = {}
    if STATE is not None:
        try:
            with STATE._lock:
                for dev in STATE.devices.values():
                    # A device is DOWN if any running sensor is alive==False;
                    # WARN if any sensor is in threshold_warn state.
                    any_down = False
                    any_warn = False
                    for s in dev.sensors.values():
                        if not getattr(s, "running", True):
                            continue
                        if getattr(s, "alive", True) is False:
                            any_down = True
                        ts_state = (getattr(s, "threshold_state", "") or "").lower()
                        if ts_state in ("warn", "threshold_warn"):
                            any_warn = True
                    status_by_did[dev.device_id] = "down" if any_down else ("warn" if any_warn else "up")
        except Exception as e:
            log.debug(f"reports.data health score STATE read failed: {e}")

    rows = []
    for a in availability:
        did = a["did"]
        pct = a.get("pct")
        # Skip devices with no probe data in the window — they can't be scored
        # honestly, and including them handed out free 100s to unmonitored
        # devices (pct=None was treated as "no penalty" even though it's
        # actually "no signal").
        if pct is None or (a.get("total") or 0) == 0:
            continue
        uptime_pct = pct
        incidents = inc_by_did.get(did, 0)
        status = status_by_did.get(did, "up")

        score = 100.0
        # Downtime penalty — non-linear so sub-percent outages still move the
        # needle. Previous formula was linear × 50, which meant 99.96% uptime
        # cost 0.02 pts — every healthy device tied at 100.0, wiping out any
        # signal between good/great. Shape: (downtime%)^0.7 × 15, capped at 50.
        #   99.99% → 0.5   (barely visible)
        #   99.9%  → 3.0
        #   99%    → 15.0
        #   95%    → 46.3
        #   ≤90%   → 50 (cap)
        downtime = max(0.0, min(100.0, 100.0 - pct))
        score -= min(50.0, (downtime ** 0.7) * 15.0)
        # Incident load — 2 pts each, capped at 20 (was 0.5 × count, too gentle).
        score -= min(20.0, incidents * 2.0)
        # Current status penalty
        if status == "down":
            score -= 20
        elif status == "warn":
            score -= 10
        score = max(0.0, round(score, 1))

        rows.append({
            "did":        did,
            "dname":      a.get("dname", ""),
            "host":       a.get("host", ""),
            "group":      a.get("group", ""),
            "uptime_pct": round(uptime_pct, 2),
            "incidents":  incidents,
            "current":    status,
            "score":      score,
            "band":       _health_band(score),
        })

    rows.sort(key=lambda r: r["score"])  # worst first
    if limit and limit > 0:
        rows = rows[:limit]
    return rows


# ── Latency percentiles ────────────────────────────────────────────────

_LATENCY_STYPES = {"ping", "tcp", "http", "http_keyword", "dns", "tls", "banner", "snmp"}
# VMware (and anything else that stores a metric value in the `ms` column
# instead of a probe round-trip) is excluded — their numbers would look
# insane in a latency section. See vmware/client.py vmware_probe() which
# writes the metric value into result['ms'] for charting purposes.


def _weighted_pct(vals_weights, p: float):
    """Weighted p-th percentile (0–100). Each pair is (value, weight).

    Used by the rollup-backed latency path to produce meaningfully different
    p50/p95/p99 — the rollup only stores (avg, max) per bucket, but weighting
    by sample_count recovers a usable distribution shape.
    """
    pairs = [(float(v), float(w)) for v, w in vals_weights if w and w > 0]
    if not pairs:
        return None
    pairs.sort(key=lambda x: x[0])
    total = sum(w for _, w in pairs)
    target = total * (p / 100.0)
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= target:
            return round(v, 1)
    return round(pairs[-1][0], 1)


def _latency_percentiles(start_ts: float, end_ts: float, limit: int = 100) -> list:
    """
    Per-sensor latency stats over the window, restricted to probe types whose
    `ms` column is an actual round-trip time (see _LATENCY_STYPES).

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
        # Per-bucket rows — NOT pre-aggregated. We need the distribution of
        # avg_ms / max_ms across buckets to compute percentiles meaningfully.
        # Previous version collapsed every sensor to one row and set
        # p95 = p99 = max(max_ms), which made both columns identical in every
        # report — visible as "p95 == p99" on every line for windows > 1 day.
        sql_pg = (f"SELECT did, sid, COALESCE(sample_count,0) AS n, "
                  f"COALESCE(avg_ms,0) AS avg_ms, "
                  f"COALESCE(min_ms,0) AS min_ms, "
                  f"COALESCE(max_ms,0) AS max_ms "
                  f"FROM {table} WHERE ts>=%s AND ts<%s AND ok_count > 0")
        sql_sqlite = (f"SELECT did, sid, COALESCE(sample_count,0), "
                      f"COALESCE(avg_ms,0), COALESCE(min_ms,0), COALESCE(max_ms,0) "
                      f"FROM {table} WHERE ts>=? AND ts<? AND ok_count > 0")
        bucket_rows: dict = {}  # (did,sid) -> list of (n, avg, mn, mx)
        if is_pg():
            from db.pg_pool import pg_cursor
            try:
                with pg_cursor("logs") as cur:
                    cur.execute(sql_pg, (start_ts, end_ts))
                    for r in cur.fetchall():
                        bucket_rows.setdefault((r["did"], r["sid"]), []).append(
                            (int(r["n"]), float(r["avg_ms"]), float(r["min_ms"]), float(r["max_ms"]))
                        )
            except Exception as e:
                log.error(f"reports.data latency PG error ({table}): {e}")
        else:
            con = None
            try:
                con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
                for r in con.execute(sql_sqlite, (start_ts, end_ts)).fetchall():
                    bucket_rows.setdefault((r[0], r[1]), []).append(
                        (int(r[2]), float(r[3]), float(r[4]), float(r[5]))
                    )
            except Exception as e:
                log.error(f"reports.data latency SQLite error ({table}): {e}")
            finally:
                if con: con.close()

        out = []
        with STATE._lock:
            for (did, sid), rows in bucket_rows.items():
                dev = STATE.devices.get(did)
                if not dev:
                    continue
                s = dev.sensors.get(sid)
                if not s or s.stype not in _LATENCY_STYPES:
                    continue
                # Weighted percentiles across buckets. p50 tracks typical
                # latency (weighted by probe count), p95 tracks worst-bucket
                # latency (tail), p99 is the single worst bucket max.
                n_total = sum(n for n, *_ in rows) or 0
                if n_total <= 0:
                    continue
                p50 = _weighted_pct([(avg, n) for n, avg, _, _ in rows], 50)
                p95 = _weighted_pct([(mx, n)  for n, _, _, mx in rows], 95)
                p99 = max((mx for _, _, _, mx in rows), default=0.0)
                mn = min((m for _, _, m, _ in rows if m > 0), default=None)
                mx = max((m for _, _, _, m in rows), default=0.0)
                out.append({
                    "did": did, "sid": sid,
                    "dname": dev.name, "sname": s.name, "stype": s.stype,
                    "samples": n_total,
                    "p50": p50,
                    "p95": p95,
                    "p99": round(p99, 1),
                    "min": round(mn, 1) if mn is not None else None,
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
            if s.stype not in _LATENCY_STYPES:
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

_SEVERITY_RANK = {"critical": 4, "crit": 4, "error": 3, "warning": 3, "warn": 3, "info": 2, "": 1}


def _top_traps(start_ts: float, end_ts: float, n: int = 10) -> list:
    """Top N SNMP trap types (by count) in the window. ts is ISO-8601 text.

    Groups on (trap_name, trap_oid) only — vendor/severity are *not* part of
    the key. Including them split rows like trap 1.3.6.1.2.1.47.2.0.1 into
    two near-duplicate entries ("Unknown" vs "—") when enrichment was
    inconsistent across traps. We now aggregate in Python on the label/oid
    pair and pick a representative vendor (any non-empty) and the highest-
    severity tag seen.
    """
    start_iso = _epoch_to_iso(start_ts)
    end_iso   = _epoch_to_iso(end_ts)
    raw = []
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
                    (start_iso, end_iso, n * 4)
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
                    (start_iso, end_iso, n * 4)
                )
                raw = [(r[0], r[1], r[2], r[3], int(r[4])) for r in cur.fetchall()]
            finally:
                if con: con.close()
    except Exception as e:
        log.error(f"reports.data top_traps error: {e}")
        return []

    # Merge rows that share the same label — i.e. same trap type, even if
    # vendor/severity enrichment disagrees across occurrences.
    merged: dict = {}
    for name, oid, vendor, severity, count in raw:
        nm = (name or "").strip()
        od = (oid or "").strip()
        label = nm or od or "Unidentified trap"
        key = (label, od)
        v = (vendor or "").strip()
        s = (severity or "").strip().lower()
        agg = merged.get(key)
        if agg is None:
            merged[key] = {"name": label, "vendor": v, "severity": s, "count": count}
            continue
        agg["count"] += count
        # Prefer a real vendor over blank/"Unknown".
        if (not agg["vendor"] or agg["vendor"].lower() == "unknown") and v and v.lower() != "unknown":
            agg["vendor"] = v
        # Keep the highest severity ever tagged on this trap.
        if _SEVERITY_RANK.get(s, 0) > _SEVERITY_RANK.get(agg["severity"], 0):
            agg["severity"] = s

    rows = sorted(merged.values(), key=lambda r: -r["count"])[:n]
    for r in rows:
        r["vendor"] = r["vendor"] or "—"
        r["severity"] = r["severity"] or "info"
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
    """Recent audit entries — admin actions / logins.

    Consecutive same-user login events are collapsed into one row with a
    repeat count. Without this, a user who logged in 15 times in a day
    fills the table with near-duplicate rows and buries the one real
    settings_update or user_* action managers actually want to see.
    """
    try:
        from db.audit import db_get_audit
    except Exception:
        return []
    # Fetch more than requested so collapsing leaves us with ~limit rows.
    raw = (db_get_audit(max(limit * 3, 100)) or [])
    return _collapse_audit(raw, limit)


_COLLAPSIBLE_ACTIONS = {
    "login_ok", "login_ok_trusted_device", "login_fail",
    "ldap_sync_complete",
}


def _collapse_audit(rows: list, limit: int) -> list:
    """Merge runs of consecutive identical (actor, action) entries into one.

    Only collapses actions in ``_COLLAPSIBLE_ACTIONS`` — real admin actions
    (settings_update, user_*, report_*, etc.) always render individually.
    """
    if not rows:
        return []
    out = []
    for r in rows:
        action = (r.get("action") or "").strip()
        actor  = (r.get("actor") or "").strip()
        if out and action in _COLLAPSIBLE_ACTIONS:
            prev = out[-1]
            if prev.get("action") == action and prev.get("actor") == actor:
                prev["_repeat"] = prev.get("_repeat", 1) + 1
                # Keep the earliest ts in 'first_ts', latest in main ts.
                prev["first_ts"] = r.get("ts")  # rows arrive newest-first, so the older repeat is the "first"
                continue
        out.append(dict(r))
        if len(out) >= limit:
            break
    # Decorate collapsed rows for the template
    for r in out:
        rep = r.get("_repeat")
        if rep and rep > 1:
            base = r.get("target") or r.get("detail") or ""
            r["target"] = f"{base} · ×{rep}" if base else f"×{rep}"
    return out


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


# ── Anomaly Detection sections ─────────────────────────────────────────
#
# Six section computations powering the dedicated `anomaly` report kind
# (and the matching opt-in checkboxes in the Custom builder). All read
# from the existing `sensor_anomaly_baselines` table + `sensors` table +
# `flap_log` rows with `direction='anomaly_warn'`. No new tables, no new
# settings, no new API endpoints — pure aggregation of data PingWatch
# already collects.

# Probe types that can have anomaly detection enabled. Mirrors
# monitoring/anomaly.py SUPPORTED_STYPES — duplicated here so the report
# layer doesn't import from monitoring.
_ANOMALY_ELIGIBLE_STYPES = {"ping", "tcp", "http", "dns", "http_keyword", "banner"}


def _anom_overview() -> dict:
    """Coverage stats: master switch state, per-stype enabled/eligible counts."""
    from core.app_state import STATE
    master = bool(int(_cfg("anomaly_global_enabled", 1) or 0))
    auto_new = bool(int(_cfg("anomaly_default_new_sensors", 0) or 0))
    cold_start_h = int(_cfg("anomaly_cold_start_hours", 24) or 0)

    by_stype: dict = {}
    total_sensors = 0
    eligible_total = 0
    enabled_total  = 0
    with STATE._lock:
        for dev in STATE.devices.values():
            for s in dev.sensors.values():
                total_sensors += 1
                stype = getattr(s, "stype", "") or ""
                eligible = stype in _ANOMALY_ELIGIBLE_STYPES
                enabled  = bool(int(getattr(s, "anomaly_enabled", 0) or 0))
                if eligible:
                    eligible_total += 1
                    if enabled:
                        enabled_total += 1
                row = by_stype.setdefault(stype, {"stype": stype, "total": 0,
                                                   "eligible": 0, "enabled": 0})
                row["total"] += 1
                if eligible: row["eligible"] += 1
                if enabled:  row["enabled"]  += 1

    coverage_pct = round(100.0 * enabled_total / eligible_total, 1) if eligible_total else None
    by_stype_rows = sorted(by_stype.values(), key=lambda r: (-r["eligible"], r["stype"]))
    return {
        "master_enabled":   master,
        "auto_new_sensors": auto_new,
        "cold_start_hours": cold_start_h,
        "total_sensors":    total_sensors,
        "eligible_total":   eligible_total,
        "enabled_total":    enabled_total,
        "disabled_eligible": eligible_total - enabled_total,
        "coverage_pct":     coverage_pct,
        "by_stype":         by_stype_rows,
        "supported_stypes": sorted(_ANOMALY_ELIGIBLE_STYPES),
    }


def _anom_baseline_table(limit: int = 50) -> list:
    """Top N sensors by sample_count from sensor_anomaly_baselines.

    Returns a learning-state snapshot per sensor — mean, std-dev, sample
    count, age-of-baseline (since enabled_since), sensitivity, and joined
    device/sensor names (resolved via STATE so we don't query the main DB).
    """
    from core.app_state import STATE
    rows: list = []
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "SELECT did, sid, mean_ms, var_ms, sample_count, "
                    "enabled_since, updated_at "
                    "FROM sensor_anomaly_baselines "
                    "ORDER BY sample_count DESC NULLS LAST LIMIT %s",
                    (int(limit),)
                )
                rows = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            log.error(f"reports.data anomaly baselines PG error: {e}")
    else:
        from core.config import DB_PATH
        con = None
        try:
            con = sqlite3.connect(DB_PATH, timeout=15)
            cur = con.execute(
                "SELECT did, sid, mean_ms, var_ms, sample_count, "
                "enabled_since, updated_at "
                "FROM sensor_anomaly_baselines "
                "ORDER BY sample_count DESC LIMIT ?",
                (int(limit),)
            )
            for r in cur.fetchall():
                rows.append({"did": r[0], "sid": r[1], "mean_ms": r[2],
                             "var_ms": r[3], "sample_count": r[4],
                             "enabled_since": r[5], "updated_at": r[6]})
        except Exception as e:
            log.error(f"reports.data anomaly baselines SQLite error: {e}")
        finally:
            if con: con.close()

    # Resolve names + sensitivity from STATE.
    name_idx: dict = {}
    sens_idx: dict = {}
    with STATE._lock:
        for dev in STATE.devices.values():
            for s in dev.sensors.values():
                key = (dev.device_id, s.sensor_id)
                name_idx[key] = (dev.name, s.name, getattr(s, "stype", ""))
                sens_idx[key] = int(getattr(s, "anomaly_sensitivity", 2) or 2)

    now = time.time()
    out: list = []
    for r in rows:
        key = (r.get("did"), r.get("sid"))
        dname, sname, stype = name_idx.get(key, ("(deleted)", "(deleted)", ""))
        var = r.get("var_ms") or 0
        std = (var ** 0.5) if var and var > 0 else 0.0
        enabled_since = r.get("enabled_since") or 0
        age_days = ((now - enabled_since) / 86400.0) if enabled_since else None
        out.append({
            "did":         r.get("did"),
            "sid":         r.get("sid"),
            "dname":       dname,
            "sname":       sname,
            "stype":       stype,
            "mean_ms":     round(r.get("mean_ms") or 0, 2),
            "stddev_ms":   round(std, 2),
            "samples":     int(r.get("sample_count") or 0),
            "sensitivity": sens_idx.get(key, 2),
            "age_days":    round(age_days, 1) if age_days is not None else None,
        })
    return out


def _anom_fires_log(flaps: list) -> list:
    """All anomaly_warn flaps in the report period, oldest first.
    Filters from the already-loaded flap list — no extra DB hit.
    """
    return [f for f in flaps if (f.get("direction") or "").lower() == "anomaly_warn"]


def _anom_fires_top(anom_fires: list, n: int = 10) -> list:
    """Top N sensors ranked by number of anomaly fires in the period."""
    counts: dict = {}
    for f in anom_fires:
        key = (f.get("did"), f.get("sid"))
        if key[0] is None: continue
        counts.setdefault(key, {"did": key[0], "sid": key[1],
                                 "dname": f.get("dname") or "",
                                 "sname": f.get("sname") or "",
                                 "stype": f.get("stype") or "",
                                 "count": 0,
                                 "last_ts": 0.0})
        counts[key]["count"] += 1
        ts = f.get("ts") or 0
        if ts > counts[key]["last_ts"]:
            counts[key]["last_ts"] = ts
    rows = sorted(counts.values(), key=lambda r: (-r["count"], -r["last_ts"]))
    return rows[:max(1, int(n))]


def _anom_vs_threshold(flaps: list) -> dict:
    """Donut data: how many WARN events came from anomaly vs static threshold.
    CRIT events are always threshold (anomaly only fires warn) — exclude them
    so the donut answers "of the warns, what was the source?".
    """
    anom = thr = 0
    for f in flaps:
        d = (f.get("direction") or "").lower()
        if d == "anomaly_warn":   anom += 1
        elif d == "threshold_warn": thr += 1
    total = anom + thr
    return {
        "anomaly_warn":   anom,
        "threshold_warn": thr,
        "total":          total,
        "anomaly_pct":    round(100.0 * anom / total, 1) if total else None,
    }


def _anom_recommendations(limit: int = 50) -> list:
    """Sensors that are eligible for anomaly detection but don't have it
    enabled. Sorted by stype, then device name. Capped at `limit` for
    readability — the count is what matters for the operator narrative.
    """
    from core.app_state import STATE
    out: list = []
    with STATE._lock:
        for dev in STATE.devices.values():
            for s in dev.sensors.values():
                stype = getattr(s, "stype", "") or ""
                if stype not in _ANOMALY_ELIGIBLE_STYPES:
                    continue
                if int(getattr(s, "anomaly_enabled", 0) or 0):
                    continue
                out.append({
                    "did":   dev.device_id,
                    "sid":   s.sensor_id,
                    "dname": dev.name,
                    "sname": s.name,
                    "stype": stype,
                    "host":  getattr(dev, "host", ""),
                })
    out.sort(key=lambda r: (r["stype"], (r["dname"] or "").lower(),
                            (r["sname"] or "").lower()))
    return {
        "total":   len(out),
        "shown":   min(limit, len(out)),
        "limit":   int(limit),
        "rows":    out[:int(limit)],
    }


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
                   "per_device_uptime", "top_worst_devices",
                   "device_health"}
    _INC_SECS   = {"incident_summary", "incident_timeline",
                   "top_noisy_sensors", "incident_log",
                   "major_incidents", "sensor_config_issues",
                   "device_health"}
    need_avail = (not is_custom) or bool(sections & _AVAIL_SECS)
    need_flaps = (not is_custom) or bool(sections & _INC_SECS)

    top_worst_n  = int(opts.get("top_worst_n", 5))  if is_custom else 5
    top_noisy_n  = int(opts.get("top_noisy_n", 5))  if is_custom else 5

    availability = _availability_by_device(start_ts, end_ts, device_ids) if need_avail else []
    overall      = _overall_availability(availability) if need_avail else {"up": 0, "total": 0, "pct": None}
    flaps_raw    = _flaps_in_window(start_ts, end_ts) if need_flaps else []
    flaps        = _filter_flaps_by_severity(flaps_raw, severity_min)
    # Split out sensor-misconfig noise ("Unknown metric: on", SSL-verify errors,
    # bad OIDs, etc.) so they don't inflate the incident summary or noisy-sensor
    # rankings. config_issues goes into its own report section.
    clean_flaps, config_issues = _classify_config_issues(flaps)
    severity     = _severity_counts(clean_flaps)
    worst        = _top_worst_devices(availability, top_worst_n)
    noisy        = _top_noisy_sensors(clean_flaps, top_noisy_n)
    mtr          = _mttr_mtbf(clean_flaps, window_s)

    # Compute the "live bad" set once — reused by outage clustering AND by
    # the raw-event list to decide whether a row with resolved_at=0 is truly
    # ongoing or just a historical row that was never resolve-stamped.
    currently_bad = _currently_bad_sensor_keys() if need_flaps else set()

    # Annotate raw flaps with the real open/closed state the template should
    # display. A row is only "open" when resolved_at=0 AND the sensor is
    # unhealthy right now; otherwise the duration is simply unknown.
    for f in clean_flaps:
        resolved = f.get("resolved_at") or 0
        f["is_open"] = bool(not resolved and (f.get("did"), f.get("sid")) in currently_bad)
    for f in flaps:
        resolved = f.get("resolved_at") or 0
        f["is_open"] = bool(not resolved and (f.get("did"), f.get("sid")) in currently_bad)

    # Detect Major Incidents early so we can (a) expose them to templates that
    # want them, and (b) suppress redundant per-sensor outages from the main
    # Incident Log. Threshold follows the user's option when Custom, else 10.
    major_min_cfg = int(opts.get("major_min_devices", 10)) if is_custom else 10
    majors = (_detect_major_incidents(clean_flaps, min_devices=major_min_cfg,
                                      currently_bad=currently_bad)
              if need_flaps else [])
    outages = (_cluster_flaps_into_outages(clean_flaps, currently_bad=currently_bad)
               if need_flaps else [])
    outages = _suppress_outages_in_majors(outages, majors)

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
        "show_individual_events": bool(opts.get("show_individual_events")) if is_custom else False,
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
            "flaps":    clean_flaps,     # noise-filtered — feeds all incident tables
            "flaps_raw": flaps,          # pre-classification, for engineers who want it
            "outages":  outages,         # aggregated — one row per outage
            "severity": severity,
            "worst_5":  worst,
            "noisy_5":  noisy,
            "mttr":     mtr,
        },
    }

    # ── Report Polish: new blocks ─────────────────────────────────────
    # These blocks are available to ALL templates that want them — not just
    # Custom. The fixed kinds (executive / technical / inventory) pull them
    # automatically so improvements apply wherever the same data is shown.

    # Major outages: cluster simultaneous device-DOWN events.
    # Already computed above (used for outage suppression). Expose to the
    # templates that want the section rendered. Inventory kind doesn't
    # surface the incident stream, so skip there.
    if want("major_incidents") or kind in ("executive", "technical"):
        ctx["major_incidents"] = majors

    # Sensor config issues — always expose. Templates that don't render it just
    # ignore ctx["sensor_config_issues"]; templates that do get the data.
    ctx["sensor_config_issues"] = config_issues

    # Per-device health score — useful in exec (manager scorecard),
    # technical (ops triage), and inventory (estate health). Always compute
    # for non-custom kinds so every template can choose to render it.
    # Inventory is the "compliance / full estate" report — it must list every
    # scored device, not a top-25. Executive and technical keep the top-25 cap
    # so their managerial summary stays scannable.
    if want("device_health") or kind in ("executive", "technical", "inventory"):
        if is_custom:
            hs_top = int(opts.get("health_top_n", 25))
        elif kind == "inventory":
            hs_top = 0  # no cap — list all devices with data
        else:
            hs_top = 25
        ctx["device_health"] = _device_health_scores(
            availability, clean_flaps, limit=hs_top
        )

    # Expose config_issue_count in meta so templates can surface a one-line
    # callout ("N sensors have configuration issues") without rendering the
    # full block.
    meta["config_issue_count"] = len(config_issues)

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

    # ── Anomaly Detection sections ────────────────────────────────────
    # Computed for kind='anomaly' (always all six) or for individual
    # checkboxes ticked in the Custom builder. Each block is independent
    # so a user can pick just the recommendations table if that's all
    # they care about.
    is_anomaly_kind = (kind == "anomaly")
    def want_anom(name: str) -> bool:
        return is_anomaly_kind or (is_custom and name in sections)

    if want_anom("anom_overview"):
        ctx["anom_overview"] = _anom_overview()
    if want_anom("anom_baseline_table"):
        bl_n = int(opts.get("anom_baseline_top_n", 50)) if is_custom else 50
        ctx["anom_baselines"] = _anom_baseline_table(limit=bl_n)
    # Fires log + top-N + vs-threshold all derive from the in-period flap list
    # we already loaded as `clean_flaps`. If flaps weren't loaded (custom user
    # only ticked anomaly sections without any incident sections), reload.
    _need_anom_fires = (want_anom("anom_fires_log") or want_anom("anom_fires_top")
                        or want_anom("anom_vs_threshold"))
    if _need_anom_fires and not need_flaps:
        clean_flaps = _flaps_in_window(start_ts, end_ts)
        clean_flaps, _ = _classify_config_issues(
            _filter_flaps_by_severity(clean_flaps, severity_min)
        )
    if want_anom("anom_fires_log"):
        ctx["anom_fires"] = _anom_fires_log(clean_flaps)
    if want_anom("anom_fires_top"):
        top_n = int(opts.get("anom_top_n", 10)) if is_custom else 10
        ctx["anom_fires_top"] = _anom_fires_top(_anom_fires_log(clean_flaps), top_n)
    if want_anom("anom_vs_threshold"):
        ctx["anom_vs_threshold"] = _anom_vs_threshold(clean_flaps)
    if want_anom("anom_recommendations"):
        rec_lim = int(opts.get("anom_rec_limit", 50)) if is_custom else 50
        ctx["anom_recommendations"] = _anom_recommendations(limit=rec_lim)

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
        "anomaly":   "Anomaly Detection Report",
        "custom":    "Network Monitoring Report",
    }.get(kind, "Network Monitoring Report")
