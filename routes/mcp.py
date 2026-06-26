"""
routes/mcp.py — Model Context Protocol (MCP) server for AI agents.

Exposes a small, **read-only** set of curated tools over MCP so AI agents
(Claude, etc.) can answer NOC questions — "what's down, why, what's the
trend, what's the layout" — without touching anything mutable.

Design (see the design discussion / CHANGELOG for the full rationale):
  • Transport : MCP *Streamable HTTP*, **stateless JSON-only**. One POST to
                /api/mcp returns one JSON-RPC 2.0 response. No SSE upgrade,
                no session IDs (GET → 405). Fits the synchronous
                ThreadingHTTPServer with no asyncio.
  • Protocol  : hand-rolled JSON-RPC (initialize / tools/list / tools/call +
                the notifications/initialized no-op). No new dependencies.
  • Auth      : a dedicated `mcp`-scope API token, jailed to /api/mcp exactly
                like `probe` tokens are jailed to /api/agent/* (enforced both
                here and in server.py's _auth/_require). `read`/`full` tokens
                and cookie sessions are rejected.
  • Enable    : opt-in `mcp_enabled` setting (default off) → 503 until an
                admin turns it on.
  • Safety    : every tool is read-only and hard-capped (default/max row
                limits, max history window, cursor pagination). Truncated
                responses carry `truncated`/`next_cursor` so the agent
                paginates deliberately. Caps are dialect-neutral, so the
                SQLite and PostgreSQL paths are equally protected.
  • Audit     : one db_log_audit row per tools/call (token owner, ip, tool,
                short arg summary).
"""
from __future__ import annotations

import json
import time

import core.app_state as app_state
import core.settings as _settings
from core.logger import log
from db import db_log_audit

# MCP protocol revision we implement. Echoed back in `initialize`.
_PROTOCOL_VERSION = "2025-06-18"

# JSON-RPC 2.0 standard error codes.
_PARSE_ERROR      = -32700
_INVALID_REQUEST  = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS   = -32602
_INTERNAL_ERROR   = -32603

# Global row-limit guard rails. Individual tools narrow these further.
_DEFAULT_LIMIT = 50
_MAX_LIMIT     = 200
# History window guard rail (minutes). 30 days.
_MAX_HISTORY_MINUTES = 43_200
_DEFAULT_HISTORY_MINUTES = 1_440  # 24h
# Aggregation/uptime window guard rail (seconds). 366 days.
_MAX_WINDOW_S = 366 * 86_400


# ─────────────────────────────────────────────────────────────────────
# Enablement
# ─────────────────────────────────────────────────────────────────────
def mcp_enabled() -> bool:
    """True when the admin has switched MCP on (default off)."""
    return str(_settings.get("mcp_enabled", "0") or "0") in ("1", "true", "True", "on")


# ─────────────────────────────────────────────────────────────────────
# Pagination / cap helpers
# ─────────────────────────────────────────────────────────────────────
def _clamp_limit(raw, default=_DEFAULT_LIMIT, maximum=_MAX_LIMIT) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default
    return min(n, maximum)


def _cursor_offset(raw) -> int:
    """Decode an opaque cursor (a stringified non-negative offset)."""
    if raw in (None, ""):
        return 0
    try:
        n = int(raw)
        return n if n >= 0 else 0
    except (TypeError, ValueError):
        return 0


def _page(items: list, limit: int, offset: int) -> dict:
    """Slice `items` and report truncation. Caller has already clamped limit."""
    total = len(items)
    window = items[offset:offset + limit]
    end = offset + len(window)
    more = end < total
    return {
        "items":       window,
        "returned":    len(window),
        "total":       total,
        "truncated":   more,
        "next_cursor": str(end) if more else None,
    }


# ─────────────────────────────────────────────────────────────────────
# Slim serialisers — only agent-relevant fields, never config/secrets
# ─────────────────────────────────────────────────────────────────────
def _slim_sensor(s: dict) -> dict:
    return {
        "sensor_id":       s.get("sensor_id"),
        "name":            s.get("name"),
        "stype":           s.get("stype"),
        "alive":           s.get("alive"),
        "running":         s.get("running"),
        "last_ms":         s.get("last_ms"),
        "last_value":      s.get("last_value"),
        "loss_pct":        s.get("loss_pct"),
        "threshold_state": s.get("threshold_state"),
        "alerts_muted":    s.get("alerts_muted"),
        "last_detail":     s.get("last_detail"),
    }


def _slim_device(d: dict, with_sensors: bool = True) -> dict:
    sensors = d.get("sensors", []) or []
    out = {
        "device_id":    d.get("device_id"),
        "name":         d.get("name"),
        "host":         d.get("host"),
        "group":        d.get("group", ""),
        "site":         d.get("site", ""),
        "status":       d.get("status"),
        "alerts_muted": d.get("alerts_muted"),
        "sensor_count": len(sensors),
    }
    if with_sensors:
        out["sensors"] = [_slim_sensor(s) for s in sensors]
    return out


# ─────────────────────────────────────────────────────────────────────
# Tool implementations. Each takes (args: dict) and returns a JSON-able
# dict. Raise ValueError for bad input → surfaced as an MCP tool error.
# ─────────────────────────────────────────────────────────────────────
def _tool_list_devices(args: dict) -> dict:
    STATE = app_state.STATE
    status = (args.get("status") or "").strip().lower() or None
    site   = (args.get("site") or "").strip().lower() or None
    group  = (args.get("group") or "").strip().lower() or None
    q      = (args.get("q") or "").strip().lower() or None
    limit  = _clamp_limit(args.get("limit"))
    offset = _cursor_offset(args.get("cursor"))

    devices = STATE.all_devices()  # list of full to_dict()
    out = []
    for d in devices:
        if status and str(d.get("status", "")).lower() != status:
            continue
        if site and str(d.get("site", "")).lower() != site:
            continue
        if group and str(d.get("group", "")).lower() != group:
            continue
        if q and q not in (str(d.get("name", "")) + " " + str(d.get("host", ""))).lower():
            continue
        out.append(_slim_device(d, with_sensors=False))
    out.sort(key=lambda x: (str(x.get("name") or "").lower()))
    pg = _page(out, limit, offset)
    return {"devices": pg.pop("items"), **pg}


def _tool_get_device(args: dict) -> dict:
    STATE = app_state.STATE
    did = (args.get("device_id") or "").strip()
    if not did:
        raise ValueError("device_id is required")
    dev = STATE.get_device(did)
    if not dev:
        raise ValueError(f"device not found: {did}")
    return {"device": _slim_device(dev.to_dict(), with_sensors=True)}


_ALERT_STATES = ("active", "acknowledged", "suppressed", "resolved", "all")


def _tool_get_active_alerts(args: dict) -> dict:
    from db import db_list_events
    severity = (args.get("severity") or "").strip().lower() or None
    state    = (args.get("state") or "active").strip().lower()
    if state not in _ALERT_STATES:
        raise ValueError(f"state must be one of {', '.join(_ALERT_STATES)}")
    limit    = _clamp_limit(args.get("limit"))
    offset   = _cursor_offset(args.get("cursor"))
    # db_list_events treats 'all' as no state filter. For point-in-time states
    # (active/acknowledged/suppressed) the result set is small; for resolved/all
    # use get_alert_history instead — this tool caps at one page of _MAX_LIMIT.
    db_state = None if state == "all" else state
    events = db_list_events(state=db_state, limit=_MAX_LIMIT, offset=0) or []
    if severity:
        events = [e for e in events
                  if str(e.get("severity", "")).lower() == severity]
    pg = _page(events, limit, offset)
    return {"state": state, "alerts": pg.pop("items"), **pg}


def _tool_get_incidents(args: dict) -> dict:
    from monitoring.root_cause import active_incidents
    return active_incidents()


def _tool_get_metric_history(args: dict) -> dict:
    from db import db_load_history
    STATE = app_state.STATE
    did = (args.get("device_id") or "").strip()
    sid = (args.get("sensor_id") or "").strip()
    if not did or not sid:
        raise ValueError("device_id and sensor_id are required")
    dev = STATE.get_device(did)
    if not dev:
        raise ValueError(f"device not found: {did}")
    if sid not in dev.sensors:
        raise ValueError(f"sensor not found on {did}: {sid}")
    try:
        minutes = int(args.get("minutes") or _DEFAULT_HISTORY_MINUTES)
    except (TypeError, ValueError):
        minutes = _DEFAULT_HISTORY_MINUTES
    minutes = max(1, min(minutes, _MAX_HISTORY_MINUTES))
    limit = _clamp_limit(args.get("limit"), default=200, maximum=1000)
    rows = db_load_history(did, sid, minutes=minutes, limit=limit) or []
    return {
        "device_id":  did,
        "sensor_id":  sid,
        "minutes":    minutes,
        "samples":    rows,
        "returned":   len(rows),
        "truncated":  len(rows) >= limit,
    }


def _tool_get_noc_summary(args: dict) -> dict:
    from monitoring.site_rollup import noc_summary
    return noc_summary()


def _tool_list_sites(args: dict) -> dict:
    from monitoring.site_rollup import site_summary_list
    limit  = _clamp_limit(args.get("limit"))
    offset = _cursor_offset(args.get("cursor"))
    sites = site_summary_list() or []
    pg = _page(sites, limit, offset)
    return {"sites": pg.pop("items"), **pg}


def _tool_get_topology(args: dict) -> dict:
    """Live parent/child dependency view derived from device.parent_device_ids
    (the same graph the RCA engine uses). With device_id, returns that node
    plus its direct parents and children; without, the whole graph (capped)."""
    STATE = app_state.STATE
    devices = STATE.all_devices()
    by_id = {d["device_id"]: d for d in devices}
    # children index
    children: dict = {}
    for d in devices:
        for pid in (d.get("parent_device_ids") or []):
            children.setdefault(pid, []).append(d["device_id"])

    def _node(d):
        return {
            "device_id": d["device_id"],
            "name":      d.get("name"),
            "status":    d.get("status"),
            "site":      d.get("site", ""),
            "parents":   list(d.get("parent_device_ids") or []),
            "children":  children.get(d["device_id"], []),
        }

    did = (args.get("device_id") or "").strip()
    if did:
        d = by_id.get(did)
        if not d:
            raise ValueError(f"device not found: {did}")
        return {
            "device":  _node(d),
            "parents": [_node(by_id[p]) for p in (d.get("parent_device_ids") or []) if p in by_id],
            "children": [_node(by_id[c]) for c in children.get(did, []) if c in by_id],
        }

    limit  = _clamp_limit(args.get("limit"), default=_MAX_LIMIT, maximum=_MAX_LIMIT)
    offset = _cursor_offset(args.get("cursor"))
    nodes = [_node(d) for d in devices]
    nodes.sort(key=lambda x: (str(x.get("name") or "").lower()))
    pg = _page(nodes, limit, offset)
    return {"nodes": pg.pop("items"), **pg}


def _tool_search(args: dict) -> dict:
    STATE = app_state.STATE
    q = (args.get("query") or "").strip().lower()
    if not q:
        raise ValueError("query is required")
    limit = _clamp_limit(args.get("limit"))
    hits = []
    for d in STATE.all_devices():
        hay = " ".join(str(d.get(k, "")) for k in ("name", "host", "group", "site")).lower()
        if q in hay:
            hits.append({"type": "device", "device_id": d["device_id"],
                         "name": d.get("name"), "host": d.get("host"),
                         "site": d.get("site", ""), "group": d.get("group", ""),
                         "status": d.get("status")})
        if len(hits) >= limit:
            break
    # sites
    try:
        from monitoring.site_rollup import site_summary_list
        for s in site_summary_list() or []:
            if len(hits) >= limit:
                break
            nm = str(s.get("name", "")) + " " + str(s.get("display_name", ""))
            if q in nm.lower():
                hits.append({"type": "site", "name": s.get("name"),
                             "devices": s.get("devices"), "down": s.get("down")})
    except Exception:
        log.exception("mcp search: site scan failed")
    return {"results": hits[:limit], "returned": min(len(hits), limit),
            "truncated": len(hits) >= limit}


def _tool_get_alert_history(args: dict) -> dict:
    """Newest-first alert events with cursor pagination. Optional `since`
    (epoch seconds) stops paging once events predate it — correct because
    results are newest-first."""
    from db import db_list_events
    limit  = _clamp_limit(args.get("limit"))
    offset = _cursor_offset(args.get("cursor"))
    since  = args.get("since")
    try:
        since = float(since) if since not in (None, "") else None
    except (TypeError, ValueError):
        since = None

    events = db_list_events(state="all", limit=limit, offset=offset) or []
    hit_floor = False
    if since is not None:
        kept = []
        for e in events:
            if float(e.get("triggered_at", 0) or 0) < since:
                hit_floor = True
                break
            kept.append(e)
        events = kept
    more = (not hit_floor) and len(events) >= limit
    return {
        "alerts":      events,
        "returned":    len(events),
        "truncated":   more,
        "next_cursor": str(offset + len(events)) if more else None,
    }


def _tool_list_maintenance_windows(args: dict) -> dict:
    from db.maintenance_windows import db_list_windows
    return {"windows": db_list_windows() or []}


def _tool_get_flaps(args: dict) -> dict:
    from db import db_load_flaps
    limit  = _clamp_limit(args.get("limit"))
    offset = _cursor_offset(args.get("cursor"))
    flaps = db_load_flaps() or []
    pg = _page(flaps, limit, offset)
    return {"flaps": pg.pop("items"), **pg}


def _window(args: dict, default_span_s: int) -> tuple:
    """Resolve (since, until) epoch seconds from args, clamped to _MAX_WINDOW_S."""
    now = time.time()
    try:
        until = float(args.get("until")) if args.get("until") not in (None, "") else now
    except (TypeError, ValueError):
        until = now
    try:
        since = float(args.get("since")) if args.get("since") not in (None, "") else until - default_span_s
    except (TypeError, ValueError):
        since = until - default_span_s
    if since >= until:
        raise ValueError("since must be earlier than until")
    if until - since > _MAX_WINDOW_S:
        since = until - _MAX_WINDOW_S   # clamp rather than reject
    return since, until


def _tool_get_alert_stats(args: dict) -> dict:
    from db import db_alert_stats
    STATE = app_state.STATE
    since, until = _window(args, 86_400)
    device_id = (args.get("device_id") or "").strip() or None
    severity  = (args.get("severity") or "").strip().lower() or None
    state     = (args.get("state") or "").strip().lower() or None
    include_suppressed = args.get("include_suppressed")
    include_suppressed = True if include_suppressed is None else bool(include_suppressed)
    group_by = args.get("group_by") or []
    if isinstance(group_by, str):
        group_by = [group_by]
    geo = [d for d in group_by if d in ("site", "group")]

    if geo:
        if len(group_by) > 1:
            raise ValueError("group_by 'site'/'group' must be the only dimension")
        # Aggregate by device in SQL, then roll up did→site/group from live STATE.
        stats = db_alert_stats(since, until, device_id=device_id, severity=severity,
                               state=state, include_suppressed=include_suppressed,
                               group_by=["device"])
        dim = geo[0]
        roll: dict = {}
        for b in stats["buckets"]:
            dev = STATE.get_device(b.get("did")) if b.get("did") else None
            if dim == "site":
                key = (getattr(dev, "site", "") or "(unsited)") if dev else "(unknown)"
            else:
                key = (getattr(dev, "group", "") or "(ungrouped)") if dev else "(unknown)"
            roll[key] = roll.get(key, 0) + b["count"]
        stats["buckets"] = [{"key": k, "count": v}
                            for k, v in sorted(roll.items(), key=lambda x: -x[1])]
        stats["bucket_note"] = "site/group rolled up from the top-200 devices by alert count"
    else:
        stats = db_alert_stats(since, until, device_id=device_id, severity=severity,
                               state=state, include_suppressed=include_suppressed,
                               group_by=group_by)

    return {
        "window": {"since": int(since), "until": int(until),
                   "days": round((until - since) / 86_400, 2)},
        **stats,
    }


def _scope_dids(scope: str):
    """Resolve an uptime scope to (label, dids|None). None = whole fleet."""
    STATE = app_state.STATE
    scope = (scope or "all").strip()
    if scope == "all":
        return "all", None
    if ":" not in scope:
        raise ValueError("scope must be 'all' or '<kind>:<value>' (device/site/group)")
    kind, val = scope.split(":", 1)
    kind = kind.strip().lower(); val = val.strip()
    if kind == "device":
        if not STATE.get_device(val):
            raise ValueError(f"device not found: {val}")
        return scope, [val]
    if kind in ("site", "group"):
        dids = [d["device_id"] for d in STATE.all_devices()
                if str(d.get(kind, "")) == val]
        if not dids:
            raise ValueError(f"no devices in {kind} '{val}'")
        return scope, dids
    raise ValueError(f"unknown scope kind: {kind}")


def _tool_get_uptime(args: dict) -> dict:
    from db import db_uptime_overall, db_uptime_by_device, db_uptime_series
    STATE = app_state.STATE
    scope_label, dids = _scope_dids(args.get("scope") or "all")
    since, until = _window(args, 2_592_000)  # default 30d
    gran = (args.get("granularity") or "").strip().lower() or None
    if gran and gran not in ("day", "week"):
        raise ValueError("granularity must be 'day' or 'week'")

    overall = db_uptime_overall(since, until, dids)
    pct = overall["pct"]
    out = {
        "scope":         scope_label,
        "window":        {"since": int(since), "until": int(until),
                          "days": round((until - since) / 86_400, 2)},
        "method":        "sample_based",
        "availability_pct":     pct,
        "ok_samples":           overall["up"],
        "total_samples":        overall["total"],
        "downtime_seconds_est": round((1 - pct / 100.0) * (until - since)) if pct is not None else None,
        "note": "Availability = OK samples / total samples. Outage-duration "
                "metrics (MTTR/MTBF/outage count) are not yet exposed.",
    }
    # worst devices when the scope spans more than one device
    if dids is None or len(dids) > 1:
        worst = db_uptime_by_device(since, until, dids, limit=10)
        for w in worst:
            dev = STATE.get_device(w["did"])
            w["name"] = getattr(dev, "name", "") if dev else ""
        out["worst_devices"] = worst
    if gran:
        out["series"] = db_uptime_series(since, until, dids, bucket=gran)
    return out


def _tool_get_audit_log(args: dict) -> dict:
    from db import db_query_audit
    limit  = _clamp_limit(args.get("limit"))
    offset = _cursor_offset(args.get("cursor"))
    since  = args.get("since")
    until  = args.get("until")
    actor  = (args.get("actor") or "").strip() or None
    action = (args.get("action_prefix") or "").strip() or None
    try:
        since = float(since) if since not in (None, "") else None
        until = float(until) if until not in (None, "") else None
    except (TypeError, ValueError):
        since = until = None
    rows = db_query_audit(since=since, until=until, actor=actor,
                          action_prefix=action, limit=limit, offset=offset) or []
    more = len(rows) >= limit
    return {
        "entries":     rows,
        "returned":    len(rows),
        "truncated":   more,
        "next_cursor": str(offset + len(rows)) if more else None,
    }


# ─────────────────────────────────────────────────────────────────────
# Tool registry — name → (description, inputSchema, handler)
# ─────────────────────────────────────────────────────────────────────
def _obj(props: dict, required=None) -> dict:
    return {"type": "object", "properties": props, "required": required or [],
            "additionalProperties": False}

_LIMIT_PROP  = {"type": "integer", "description": f"Max rows (default {_DEFAULT_LIMIT}, hard max {_MAX_LIMIT})."}
_CURSOR_PROP = {"type": "string", "description": "Opaque pagination cursor from a previous truncated response."}

_TOOLS = [
    {
        "name": "list_devices",
        "description": "List monitored devices with their live status. Filter by status (up/warn/down/paused), site, group, or substring q. Paginated; returns at most "
                       f"{_MAX_LIMIT} per page.",
        "inputSchema": _obj({
            "status": {"type": "string", "description": "Filter by device status (up/warn/down/paused)."},
            "site":   {"type": "string", "description": "Filter by exact site name."},
            "group":  {"type": "string", "description": "Filter by exact group name."},
            "q":      {"type": "string", "description": "Case-insensitive substring match on name/host."},
            "limit":  _LIMIT_PROP, "cursor": _CURSOR_PROP,
        }),
        "handler": _tool_list_devices,
    },
    {
        "name": "get_device",
        "description": "Get one device by device_id, including the live status of each of its sensors.",
        "inputSchema": _obj({"device_id": {"type": "string", "description": "The device id."}},
                            required=["device_id"]),
        "handler": _tool_get_device,
    },
    {
        "name": "get_active_alerts",
        "description": "List alert events by state. Default state='active' (firing/unacked). Use state='acknowledged' or 'suppressed' to see those (which do NOT appear under 'active'); use get_alert_history for resolved/all history. Optionally filter by severity.",
        "inputSchema": _obj({
            "state":    {"type": "string", "description": "active | acknowledged | suppressed | resolved | all (default active)."},
            "severity": {"type": "string", "description": "Optional severity filter (e.g. critical/warning)."},
            "limit": _LIMIT_PROP, "cursor": _CURSOR_PROP,
        }),
        "handler": _tool_get_active_alerts,
    },
    {
        "name": "get_incidents",
        "description": "Root-cause-correlated live outages: each incident groups one upstream root device with the downstream devices its outage explains. Best first call to understand WHY things are down.",
        "inputSchema": _obj({}),
        "handler": _tool_get_incidents,
    },
    {
        "name": "get_metric_history",
        "description": "Time-series samples for one sensor over the last N minutes (default 1440 = 24h, max 43200 = 30d). Returns evenly-distributed points, oldest first.",
        "inputSchema": _obj({
            "device_id": {"type": "string"},
            "sensor_id": {"type": "string"},
            "minutes":   {"type": "integer", "description": "Look-back window in minutes (max 43200)."},
            "limit":     {"type": "integer", "description": "Max points (default 200, max 1000)."},
        }, required=["device_id", "sensor_id"]),
        "handler": _tool_get_metric_history,
    },
    {
        "name": "get_noc_summary",
        "description": "One-call situational overview: site/device/alert rollups, 24h uptime, flap and incident counts, top problems, recent alerts.",
        "inputSchema": _obj({}),
        "handler": _tool_get_noc_summary,
    },
    {
        "name": "list_sites",
        "description": "Per-site rollup (device counts and up/warn/down/alert totals). Paginated.",
        "inputSchema": _obj({"limit": _LIMIT_PROP, "cursor": _CURSOR_PROP}),
        "handler": _tool_list_sites,
    },
    {
        "name": "get_topology",
        "description": "Live parent/child dependency graph (the same one the RCA engine uses). With device_id, returns that node plus its direct parents and children; without, the whole graph (paginated).",
        "inputSchema": _obj({
            "device_id": {"type": "string", "description": "Optional — focus on one device."},
            "limit": _LIMIT_PROP, "cursor": _CURSOR_PROP,
        }),
        "handler": _tool_get_topology,
    },
    {
        "name": "search",
        "description": "Cross-entity substring search across devices (name/host/group/site) and sites. Escape hatch when you don't have an id.",
        "inputSchema": _obj({"query": {"type": "string"}, "limit": _LIMIT_PROP},
                            required=["query"]),
        "handler": _tool_search,
    },
    {
        "name": "get_alert_history",
        "description": "Newest-first alert event history (resolved and active), with cursor pagination. Optional 'since' (epoch seconds) bounds how far back to read.",
        "inputSchema": _obj({
            "since":  {"type": "number", "description": "Only events at/after this epoch-seconds timestamp."},
            "limit":  _LIMIT_PROP, "cursor": _CURSOR_PROP,
        }),
        "handler": _tool_get_alert_history,
    },
    {
        "name": "list_maintenance_windows",
        "description": "List all configured maintenance windows (scheduled suppression periods).",
        "inputSchema": _obj({}),
        "handler": _tool_list_maintenance_windows,
    },
    {
        "name": "get_flaps",
        "description": "Recent flaps (sensor state transitions / up-down events), newest first. Paginated.",
        "inputSchema": _obj({"limit": _LIMIT_PROP, "cursor": _CURSOR_PROP}),
        "handler": _tool_get_flaps,
    },
    {
        "name": "get_alert_stats",
        "description": "Server-side AGGREGATED alert metrics over a window (default last 24h, max 366d) — total, by_state, by_severity, suppressed_by_reason, and MTTR (avg/p50/p95 over resolved events). Use this instead of pulling raw events for reporting/trends. group_by produces buckets by device/sensor/severity/state/event_type/suppress_reason (SQL) or site/group (rolled up from the top-200 devices).",
        "inputSchema": _obj({
            "since":    {"type": "number", "description": "Window start, epoch seconds (default now-24h)."},
            "until":    {"type": "number", "description": "Window end, epoch seconds (default now)."},
            "group_by": {"type": "array", "items": {"type": "string"},
                         "description": "Bucket dimensions: device, sensor, severity, state, event_type, suppress_reason, site, group. site/group must be the only dimension."},
            "device_id": {"type": "string", "description": "Restrict to one device."},
            "severity":  {"type": "string"},
            "state":     {"type": "string", "description": "active | acknowledged | suppressed | resolved."},
            "include_suppressed": {"type": "boolean", "description": "Include suppressed events (default true)."},
        }),
        "handler": _tool_get_alert_stats,
    },
    {
        "name": "get_uptime",
        "description": "Historical availability % over a window (default last 30d, max 366d) for the whole fleet or a scope. Availability is sample-based (OK samples / total samples). Optional day/week time-series and worst-device ranking. Scope: 'all' | 'device:<id>' | 'site:<name>' | 'group:<name>'.",
        "inputSchema": _obj({
            "scope":       {"type": "string", "description": "'all' (default), 'device:<id>', 'site:<name>', or 'group:<name>'."},
            "since":       {"type": "number", "description": "Window start, epoch seconds (default now-30d)."},
            "until":       {"type": "number", "description": "Window end, epoch seconds (default now)."},
            "granularity": {"type": "string", "description": "Optional 'day' or 'week' for a time series."},
        }),
        "handler": _tool_get_uptime,
    },
    {
        "name": "get_audit_log",
        "description": "Read the audit trail (config changes, ack/mute, logins, MCP/OAuth actions), newest first. Filter by time window, exact actor, and/or an action prefix (e.g. 'mcp_', 'alert_event_'). Paginated.",
        "inputSchema": _obj({
            "since":         {"type": "number", "description": "Only entries at/after this epoch-seconds timestamp."},
            "until":         {"type": "number", "description": "Only entries before this epoch-seconds timestamp."},
            "actor":         {"type": "string", "description": "Exact username filter."},
            "action_prefix": {"type": "string", "description": "Match actions starting with this prefix."},
            "limit": _LIMIT_PROP, "cursor": _CURSOR_PROP,
        }),
        "handler": _tool_get_audit_log,
    },
]

_TOOLS_BY_NAME = {t["name"]: t for t in _TOOLS}


# ─────────────────────────────────────────────────────────────────────
# JSON-RPC plumbing
# ─────────────────────────────────────────────────────────────────────
def _unauthorized(h):
    """401 carrying the RFC 9728 resource-metadata pointer so MCP OAuth
    clients (claude.ai connector) can discover the authorization server.
    Static-token clients simply ignore the header."""
    try:
        from routes.mcp_oauth import _base_url
        meta = f'{_base_url(h)}/.well-known/oauth-protected-resource'
    except Exception:
        meta = "/.well-known/oauth-protected-resource"
    body = json.dumps({"error": "unauthorized"}).encode()
    h.send_response(401)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("WWW-Authenticate",
                  f'Bearer resource_metadata="{meta}"')
    h._sec_headers()
    h.end_headers()
    h.wfile.write(body)


def _rpc_result(rid, result) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _rpc_error(rid, code, message) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _arg_summary(args: dict) -> str:
    """Short, log-safe one-liner of the call arguments for the audit detail."""
    try:
        parts = []
        for k, v in (args or {}).items():
            sv = str(v)
            if len(sv) > 40:
                sv = sv[:40] + "…"
            parts.append(f"{k}={sv}")
        return ", ".join(parts)[:200]
    except Exception:
        return ""


def _dispatch(h, req: dict, owner: str):
    """Handle a single JSON-RPC request object. Returns a response dict, or
    None for notifications (no `id`)."""
    if not isinstance(req, dict) or req.get("jsonrpc") != "2.0":
        return _rpc_error(None, _INVALID_REQUEST, "Invalid JSON-RPC request")

    rid    = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    is_notification = "id" not in req

    if method == "initialize":
        result = {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities":    {"tools": {}},
            "serverInfo":      {"name": "pingwatch",
                                "version": getattr(app_state, "APP_VERSION", "")},
        }
        return None if is_notification else _rpc_result(rid, result)

    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None  # notification — no response

    if method == "ping":
        return None if is_notification else _rpc_result(rid, {})

    if method == "tools/list":
        tools = [{"name": t["name"], "description": t["description"],
                  "inputSchema": t["inputSchema"]} for t in _TOOLS]
        return _rpc_result(rid, {"tools": tools})

    if method == "tools/call":
        name = (params or {}).get("name")
        args = (params or {}).get("arguments") or {}
        tool = _TOOLS_BY_NAME.get(name)
        if not tool:
            return _rpc_error(rid, _INVALID_PARAMS, f"Unknown tool: {name}")
        # Per-call audit (token owner + short arg summary).
        try:
            db_log_audit(owner, h.client_address[0], "mcp_tool_call",
                         str(name), _arg_summary(args))
        except Exception:
            log.exception("mcp audit failed")
        try:
            payload = tool["handler"](args)
        except ValueError as e:
            # Bad input from the agent — report as a tool error, not a crash.
            return _rpc_result(rid, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })
        except Exception:
            # Never leak internals to the client (project convention).
            log.exception(f"mcp tool '{name}' failed")
            return _rpc_result(rid, {
                "content": [{"type": "text", "text": "Error: internal error executing tool"}],
                "isError": True,
            })
        text = json.dumps(payload, default=str)
        return _rpc_result(rid, {"content": [{"type": "text", "text": text}], "isError": False})

    if is_notification:
        return None
    return _rpc_error(rid, _METHOD_NOT_FOUND, f"Method not found: {method}")


# ─────────────────────────────────────────────────────────────────────
# HTTP entry point (registered in server.py's GET + POST dispatch)
# ─────────────────────────────────────────────────────────────────────
def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""
    if path.split("?", 1)[0] != "/api/mcp":
        return False

    # Streamable HTTP optional GET (server→client SSE) is not supported in the
    # stateless JSON-only profile.
    if method == "GET":
        h.send_response(405)
        h.send_header("Allow", "POST")
        h.send_header("Content-Length", "0")
        h.end_headers()
        return True

    if method != "POST":
        h._json(405, {"error": "method not allowed"})
        return True

    # ── Opt-in gate ──────────────────────────────────────────────────
    if not mcp_enabled():
        h._json(503, {"error": "MCP is disabled"})
        return True

    # ── Auth: mcp-scope API token ONLY ───────────────────────────────
    user, role, scope, kind = h._auth_principal()
    if not user:
        _unauthorized(h)   # 401 + WWW-Authenticate → triggers OAuth discovery
        return True
    if kind != "api_token" or scope != "mcp":
        h._json(403, {"error": "MCP requires an mcp-scope API token"})
        return True

    # ── JSON-RPC dispatch ────────────────────────────────────────────
    # Batching was removed in the 2025-06-18 spec; accept single objects only.
    if isinstance(body, list):
        h._json(200, _rpc_error(None, _INVALID_REQUEST,
                                "JSON-RPC batching is not supported"))
        return True
    if not isinstance(body, dict) or not body:
        h._json(200, _rpc_error(None, _PARSE_ERROR, "Invalid or empty JSON-RPC body"))
        return True

    resp = _dispatch(h, body, user)
    if resp is None:
        # Notification — acknowledge with 202 and no body.
        h.send_response(202)
        h.send_header("Content-Length", "0")
        h.end_headers()
        return True

    h._json(200, resp)
    return True
