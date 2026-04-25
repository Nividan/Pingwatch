"""Build the structured `raw_data` payload that accompanies every flap_log row.

One pure function. No I/O. The output is a dict that the caller json.dumps()
into `flap_log.raw_data` and the frontend parses to render the
"Debug / Raw Data" section as a structured key/value list — replacing the
previous behavior of just echoing `detail` (which already appears as the
Message field above).

The assembler is driven by two inputs:

  * `direction` — what kind of event this is (down, threshold_warn,
    anomaly_warn, state_down, reboot, value_change, license_warn, ...).
  * `sensor.stype` — which probe family the sensor belongs to. Per-stype
    extras pick up fields the probe already returned but currently discards
    after formatting (HTTP status code, DNS records, SNMP raw counter, etc).

Returns `{}` when there is genuinely nothing useful to add — the frontend
will hide the section in that case.

Anomaly math is pulled from monitoring/anomaly to avoid duplicating the
formula that produced the alert in the first place.
"""

from __future__ import annotations

from typing import Any


def _safe(val):
    """Coerce to JSON-safe primitives. Strings are length-capped at 400 chars."""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, (list, tuple)):
        return [_safe(v) for v in val]
    if isinstance(val, dict):
        return {str(k): _safe(v) for k, v in val.items()}
    s = str(val)
    return s if len(s) <= 400 else s[:400] + "…"


def _common(sensor, result):
    """Keys included on every flap regardless of direction or stype."""
    out = {}
    host = getattr(sensor, "host", "") or ""
    if host:
        out["host"] = host
    port = getattr(sensor, "port", None)
    if port:
        out["port"] = int(port)
    if result is not None:
        ms = result.get("ms") if isinstance(result, dict) else None
        if ms is not None:
            out["probe_ms"] = ms
    if getattr(sensor, "warn_ms", None):
        out["warn_ms"] = sensor.warn_ms
    if getattr(sensor, "crit_ms", None):
        out["crit_ms"] = sensor.crit_ms
    return out


def _per_stype(sensor, result):
    """Pick up probe-family-specific fields the probe returned."""
    stype = getattr(sensor, "stype", "") or ""
    out = {}
    res = result if isinstance(result, dict) else {}

    if stype in ("http", "http_keyword"):
        url = getattr(sensor, "url", "") or getattr(sensor, "host", "")
        if url:
            out["url"] = url
        code = res.get("code")
        if code is not None:
            out["http_code"] = code
        if stype == "http_keyword":
            kw = getattr(sensor, "keyword", "")
            if kw:
                out["keyword"] = kw

    elif stype == "dns":
        rec_type = getattr(sensor, "dns_record_type", "") or "A"
        out["record_type"] = rec_type
        query = getattr(sensor, "dns_query", "") or getattr(sensor, "host", "")
        if query:
            out["query"] = query
        srv = getattr(sensor, "dns_server", "")
        if srv:
            out["dns_server"] = srv
        val = res.get("value")
        if val:
            out["records"] = [val] if isinstance(val, str) else list(val)

    elif stype == "snmp":
        oid = getattr(sensor, "snmp_oid", "")
        if oid:
            out["oid"] = oid
        unit = getattr(sensor, "snmp_unit", "")
        if unit:
            out["unit"] = unit
        snmp_type = res.get("snmp_type") or getattr(sensor, "snmp_type", "")
        if snmp_type:
            out["snmp_type"] = snmp_type
        # Counter-rate sensors carry the raw counter, prev counter, elapsed,
        # and computed rate — none of which currently survive past the
        # rate calculation in core.state.
        rate = getattr(sensor, "_last_rate", None)
        if rate is not None:
            out["rate"] = rate
        prev = getattr(sensor, "_snmp_prev", None)
        if prev is not None:
            out["prev_counter"] = prev
        prev_ts = getattr(sensor, "_snmp_prev_ts", None)
        if prev_ts is not None:
            out["prev_counter_ts"] = prev_ts
        # Raw counter at the moment of the probe — the probe returns it in
        # `value` for counter types as well as gauge/enum/text.
        raw_val = res.get("value")
        if raw_val is not None:
            out["value"] = raw_val
        last_disp = getattr(sensor, "last_value", None)
        if last_disp and last_disp != raw_val:
            out["display"] = last_disp

    elif stype == "tls":
        # probe_tls returns days-to-expiry in `value`.
        days = res.get("value")
        if days is not None:
            try:
                out["days_remaining"] = int(days)
            except (ValueError, TypeError):
                out["days_remaining"] = days

    elif stype == "banner":
        regex = getattr(sensor, "banner_regex", "")
        if regex:
            out["regex"] = regex
        # probe_banner stuffs the banner into result["value"] when matched
        bnr = res.get("value")
        if bnr:
            out["banner_excerpt"] = bnr

    elif stype == "smtp":
        lvl = getattr(sensor, "smtp_test_level", "")
        if lvl:
            out["test_level"] = lvl

    elif stype in ("ssh", "sftp"):
        lvl = getattr(sensor, f"{stype}_test_level", "")
        if lvl:
            out["test_level"] = lvl
        if stype == "sftp":
            rp = getattr(sensor, "sftp_remote_path", "")
            if rp:
                out["remote_path"] = rp

    elif stype == "vmware":
        m = getattr(sensor, "vmware_metric", "")
        if m:
            out["metric"] = m
        vm = getattr(sensor, "vmware_vm_name", "")
        if vm:
            out["vm"] = vm

    elif stype == "radius":
        lvl = getattr(sensor, "radius_test_level", "")
        if lvl:
            out["test_level"] = lvl

    return out


def _per_direction(direction, context, sensor, result):
    """Direction-specific keys (threshold limits, anomaly stats, enum legends, etc)."""
    out = {}
    ctx = context or {}
    res = result if isinstance(result, dict) else {}

    if direction == "down":
        out["consec_fail"] = ctx.get("consec_fail")
        last_err = res.get("detail")
        if last_err:
            out["last_error"] = last_err

    elif direction == "recovered":
        dur = ctx.get("duration_s")
        if dur is not None:
            out["down_duration_s"] = dur

    elif direction in ("threshold_warn", "threshold_crit"):
        actual = ctx.get("actual")
        if actual is not None:
            out["actual"] = actual
        unit = ctx.get("unit")
        if unit:
            out["unit"] = unit
        limit = ctx.get("limit")
        if limit is not None:
            out["limit"] = limit
        metric = ctx.get("metric")
        if metric:
            out["metric"] = metric
        prev_state = ctx.get("prev_state")
        if prev_state:
            out["previous_state"] = prev_state
        loss = getattr(sensor, "loss_pct", 0)
        if loss:
            out["loss_pct"] = loss
            if getattr(sensor, "loss_warn_pct", 0):
                out["loss_warn_pct"] = sensor.loss_warn_pct
            if getattr(sensor, "loss_crit_pct", 0):
                out["loss_crit_pct"] = sensor.loss_crit_pct

    elif direction == "anomaly_warn":
        # Pull stats from the live detector state — same numbers that
        # produced the alert. No formula duplication: import the constants
        # from monitoring.anomaly.
        try:
            from monitoring.anomaly import _K_BY_SENSITIVITY
            import math
            mean = float(getattr(sensor, "_anom_mean", 0.0) or 0.0)
            var = float(getattr(sensor, "_anom_var", 0.0) or 0.0)
            stddev = math.sqrt(max(0.0, var))
            sigma_eff = max(stddev, 10.0, 0.2 * mean)
            sens = int(getattr(sensor, "anomaly_sensitivity", 2) or 2)
            k = _K_BY_SENSITIVITY.get(sens, _K_BY_SENSITIVITY[2])
            threshold = mean + k * sigma_eff
            cur_ms = ctx.get("actual") if ctx.get("actual") is not None else getattr(sensor, "last_ms", None)
            if cur_ms is not None and sigma_eff > 0:
                z = (float(cur_ms) - mean) / sigma_eff
            else:
                z = None
            out["baseline_mean_ms"] = round(mean, 2)
            out["baseline_stddev_ms"] = round(stddev, 2)
            out["sigma_eff_ms"] = round(sigma_eff, 2)
            out["threshold_ms"] = round(threshold, 2)
            if z is not None:
                out["z_score"] = round(z, 2)
            out["sensitivity"] = sens
            out["sample_count"] = int(getattr(sensor, "_anom_count", 0) or 0)
            if cur_ms is not None:
                out["actual_ms"] = cur_ms
        except Exception:
            pass

    elif direction in ("state_down", "state_up", "state_change"):
        if "from_state" in ctx:
            out["from_state"] = ctx["from_state"]
        if "to_state" in ctx:
            out["to_state"] = ctx["to_state"]
        if "from_code" in ctx:
            out["from_code"] = ctx["from_code"]
        if "to_code" in ctx:
            out["to_code"] = ctx["to_code"]
        if "legend" in ctx and ctx["legend"]:
            out["legend"] = dict(ctx["legend"])

    elif direction == "reboot":
        if "prev_uptime_s" in ctx:
            out["prev_uptime_s"] = ctx["prev_uptime_s"]
        if "new_uptime_s" in ctx:
            out["new_uptime_s"] = ctx["new_uptime_s"]

    elif direction == "value_change":
        if "prev_value" in ctx:
            out["prev_value"] = ctx["prev_value"]
        if "new_value" in ctx:
            out["new_value"] = ctx["new_value"]

    elif direction in ("license_warn", "license_crit"):
        for k in ("license_name", "expires_at", "days_left"):
            if ctx.get(k) is not None:
                out[k] = ctx[k]

    return out


def build_flap_raw_data(sensor, result=None, direction="down", context=None) -> dict:
    """Assemble the raw_data payload for one flap event.

    Args:
        sensor: the Sensor object (or anything duck-typed with the same attrs).
            May be None for synthetic events (license checker passes None).
        result: the probe result dict (or None when there is no live probe,
            e.g. license events).
        direction: the flap direction string ("down", "threshold_warn", ...).
        context: optional dict of direction-specific extras supplied by the
            caller (threshold actual/limit/unit, enum from/to states,
            reboot prev/new uptime, license days_left, etc.).

    Returns:
        A JSON-safe dict. Empty dict means "nothing useful to render" — the
        frontend will then hide the Debug section.
    """
    payload: dict[str, Any] = {}
    try:
        if sensor is not None:
            payload.update(_common(sensor, result))
            payload.update(_per_stype(sensor, result))
        payload.update(_per_direction(direction, context, sensor, result))
    except Exception:
        # Never raise from the hot path. Better to log a thinner payload
        # than break flap insertion.
        pass
    # Strip None values to keep the payload compact.
    payload = {k: _safe(v) for k, v in payload.items() if v is not None and v != ""}
    return payload
