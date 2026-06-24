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


def _tool_get_active_alerts(args: dict) -> dict:
    from db import db_list_events
    severity = (args.get("severity") or "").strip().lower() or None
    limit    = _clamp_limit(args.get("limit"))
    offset   = _cursor_offset(args.get("cursor"))
    events = db_list_events(state="active", limit=_MAX_LIMIT, offset=0) or []
    if severity:
        events = [e for e in events
                  if str(e.get("severity", "")).lower() == severity]
    pg = _page(events, limit, offset)
    return {"alerts": pg.pop("items"), **pg}


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
        "description": "List currently active (unresolved/acknowledged) alert events, optionally filtered by severity. Use this to answer 'what is alarming right now'.",
        "inputSchema": _obj({
            "severity": {"type": "string", "description": "Optional severity filter (e.g. crit/warn)."},
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
]

_TOOLS_BY_NAME = {t["name"]: t for t in _TOOLS}


# ─────────────────────────────────────────────────────────────────────
# JSON-RPC plumbing
# ─────────────────────────────────────────────────────────────────────
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
        h._json(401, {"error": "unauthorized"})
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
