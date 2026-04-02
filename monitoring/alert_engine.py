"""
monitoring/alert_engine.py — Rules-based alert engine for PingWatch.

Design mirrors syslog_client.py: a bounded queue fed by non-blocking
alert_engine_send() calls from core/state.py, consumed by a daemon worker
thread that evaluates rules and dispatches actions.

Actions: email (smtp_alert), webhook (HTTP POST), syslog (syslog_client), browser (SSE push)
Cooldown: DB-persisted via alert_dedup (survives restarts)
History:  every rule firing written to alert_events table
Maintenance: suppresses actions during active maintenance windows
"""

import datetime
import json
import queue
import socket
import threading
import time
import urllib.request
import urllib.error

from core.logger import log


# ── Internal queue + worker state ────────────────────────────────
_Q: queue.Queue = queue.Queue(maxsize=1000)
_started = False
_start_lock = threading.Lock()

# Rules cache: reloaded from DB every _CACHE_TTL seconds
_rules_cache: list = []
_rules_cache_ts: float = 0.0
_CACHE_TTL = 30.0


# ── Public API ────────────────────────────────────────────────────

def alert_engine_send(event_type: str, data: dict):
    """Non-blocking enqueue. Called from core/state.py _broadcast()."""
    _ensure_started()
    try:
        _Q.put_nowait((event_type, data))
    except queue.Full:
        pass   # drop silently; never block monitor thread


def alert_engine_start():
    """Explicitly start the daemon thread (called from server.py)."""
    _ensure_started()


# ── Internal ──────────────────────────────────────────────────────

def _ensure_started():
    global _started
    if _started:
        return
    with _start_lock:
        if _started:
            return
        t = threading.Thread(target=_worker_loop, daemon=True, name="alert-engine")
        t.start()
        _started = True
        log.info("Alert engine started")


def _worker_loop():
    while True:
        try:
            item = _Q.get(timeout=5)
        except queue.Empty:
            continue
        except Exception as e:
            log.error(f"alert_engine: queue error: {e}")
            continue
        try:
            event_type, data = item
            _process(event_type, data)
        except Exception as e:
            log.error(f"alert_engine worker error: {e}")
        finally:
            _Q.task_done()


def _get_rules() -> list:
    """Return enabled rules from cache, refreshing every _CACHE_TTL seconds."""
    global _rules_cache, _rules_cache_ts
    now = time.monotonic()
    if now - _rules_cache_ts < _CACHE_TTL and _rules_cache is not None:
        return _rules_cache
    try:
        from db.alert_rules import db_list_rules
        all_rules = db_list_rules()
        _rules_cache = [r for r in all_rules if r["enabled"]]
        _rules_cache_ts = now
    except Exception as e:
        log.error(f"alert_engine: failed to load rules: {e}")
    return _rules_cache


def invalidate_rules_cache():
    """Force rules to reload on next evaluation (call after save/delete)."""
    global _rules_cache_ts
    _rules_cache_ts = 0.0


def _process(event_type: str, data: dict):
    """Evaluate all enabled rules against this event and dispatch matches."""
    if event_type not in ("flap_down", "flap_recovered",
                           "threshold_warning", "threshold_critical",
                           "threshold_ok"):
        return

    # Skip if the sensor/device was deleted between enqueue and processing
    did = data.get("did")
    sid = data.get("sid")
    if did and sid:
        from core.app_state import STATE
        with STATE._lock:
            dev = STATE.devices.get(did)
            if not dev or sid not in dev.sensors:
                return

    rules = _get_rules()
    if not rules:
        return

    ctx = _build_ctx(event_type, data)

    for rule in rules:
        try:
            if not _match_conditions(rule, ctx):
                continue
            in_maintenance, mw_name = _check_maintenance(ctx)
            if in_maintenance:
                from db.alert_events import db_log_event
                db_log_event(rule["id"], rule["name"], ctx, state='suppressed')
                log.debug(f"alert_engine: rule {rule['id']} suppressed by window '{mw_name}'")
                continue
            if _is_acked(rule, ctx):
                continue
            if not _check_cooldown(rule, ctx):
                continue
            _dispatch(rule, ctx)
            from db.alert_events import db_log_event
            db_log_event(rule["id"], rule["name"], ctx, state='active')
        except Exception as e:
            log.error(f"alert_engine: rule {rule.get('id')} error: {e}")


def _build_ctx(event_type: str, data: dict) -> dict:
    """Build the unified context dict used for condition matching + templates."""
    _sev_map = {
        "flap_down":          "critical",
        "flap_recovered":     "info",
        "threshold_warning":  "warning",
        "threshold_critical": "critical",
        "threshold_ok":       "info",
    }
    # Normalize internal SSE event names to user-friendly values for condition matching
    _etype_norm = {
        "flap_down":          "down",
        "flap_recovered":     "recovered",
        "threshold_warning":  "threshold_warning",
        "threshold_critical": "threshold_critical",
        "threshold_ok":       "threshold_ok",
    }
    ctx = dict(data)
    ctx["event_type"] = _etype_norm.get(event_type, event_type)
    ctx["severity"]   = _sev_map.get(event_type, "info")

    if "grp" not in ctx or not ctx.get("grp"):
        try:
            from core.app_state import STATE
            did = ctx.get("did", "")
            with STATE._lock:
                dev = STATE.devices.get(did)
                if dev:
                    ctx["grp"] = dev.group
        except Exception:
            ctx["grp"] = ""

    ctx.setdefault("grp", "")
    ctx.setdefault("direction", "")
    ctx.setdefault("state", "")
    ctx.setdefault("ms", None)
    ctx.setdefault("loss_pct", 0)
    ctx.setdefault("ts", "")
    ctx.setdefault("detail", "")
    return ctx


# ── Condition matching ────────────────────────────────────────────

_FIELD_MAP = {
    "event_type":      lambda ctx: ctx.get("event_type", ""),
    "sensor_type":     lambda ctx: ctx.get("stype", ""),
    "device_group":    lambda ctx: ctx.get("grp", ""),
    "threshold_state": lambda ctx: ctx.get("state", ""),
    "direction":       lambda ctx: ctx.get("direction", ""),
    "loss_pct":        lambda ctx: float(ctx.get("loss_pct") or 0),
    "severity":        lambda ctx: ctx.get("severity", ""),
}


def _match_conditions(rule: dict, ctx: dict) -> bool:
    conditions = rule.get("conditions", [])
    if not conditions:
        return True

    logic = rule.get("condition_logic", "AND").upper()
    results = [_eval_condition(c, ctx) for c in conditions]
    return all(results) if logic == "AND" else any(results)


def _eval_condition(cond: dict, ctx: dict) -> bool:
    field = cond.get("field", "")
    op    = cond.get("op", "eq")
    value = cond.get("value", "")

    getter = _FIELD_MAP.get(field)
    if not getter:
        log.warning(f"alert_engine: unknown condition field {field!r} — condition skipped")
        return False

    actual = getter(ctx)

    if op in ("gt", "gte", "lt", "lte"):
        try:
            return _num_op(float(actual), op, float(value))
        except (TypeError, ValueError):
            return False

    if op == "eq":      return str(actual).lower() == str(value).lower()
    if op == "ne":      return str(actual).lower() != str(value).lower()
    if op == "contains": return str(value).lower() in str(actual).lower()
    if op == "in":
        options = [v.strip().lower() for v in str(value).split(",")]
        return str(actual).lower() in options

    return True


def _num_op(actual: float, op: str, target: float) -> bool:
    if op == "gt":  return actual > target
    if op == "gte": return actual >= target
    if op == "lt":  return actual < target
    if op == "lte": return actual <= target
    return False


# ── Maintenance window check ──────────────────────────────────────

def _check_maintenance(ctx: dict) -> tuple:
    """Return (is_suppressed: bool, window_name: str)."""
    try:
        from db.maintenance_windows import db_active_windows
        windows = db_active_windows()
    except Exception:
        return False, ""

    now_dt  = datetime.datetime.now()
    now_day = now_dt.isoweekday()   # 1=Mon, 7=Sun

    for w in windows:
        if w.get("recurring"):
            days = [d.strip() for d in str(w.get("recur_days", "")).split(",") if d.strip()]
            if days and str(now_day) not in days:
                continue
            rs = w.get("recur_start", "")
            re = w.get("recur_end", "")
            if rs and re:
                try:
                    rs_t = datetime.datetime.strptime(rs, "%H:%M").time()
                    re_t = datetime.datetime.strptime(re, "%H:%M").time()
                    now_t = now_dt.time()
                    if rs_t <= re_t:
                        active = rs_t <= now_t <= re_t
                    else:  # crosses midnight
                        active = now_t >= rs_t or now_t <= re_t
                    if not active:
                        continue
                except ValueError:
                    continue

        scope = w.get("scope_type", "all")
        if scope == "group" and ctx.get("grp", "") != w.get("scope_value", ""):
            continue
        if scope == "device" and ctx.get("did", "") != w.get("scope_value", ""):
            continue

        return True, w.get("name", "")

    return False, ""


# ── ACK suppression ──────────────────────────────────────────────

def _is_acked(rule: dict, ctx: dict) -> bool:
    """Return True if this rule+device+sensor has an acknowledged event (suppress until resolved)."""
    try:
        from db.alert_events import db_has_acked_event
        return db_has_acked_event(rule["id"], ctx.get("did", ""), ctx.get("sid", ""))
    except Exception as e:
        log.warning(f"alert_engine: ACK check error: {e}")
        return False


# ── Cooldown / dedup ──────────────────────────────────────────────

def _check_cooldown(rule: dict, ctx: dict) -> bool:
    """Return True if rule may fire. Updates DB dedup record."""
    cooldown_s = int(rule.get("cooldown_s", 0))
    if cooldown_s <= 0:
        return True

    rule_id = rule["id"]
    sig     = f"{rule_id}:{ctx.get('did', '')}:{ctx.get('sid', '')}"
    now     = time.time()

    try:
        from db.alert_events import db_get_dedup, db_upsert_dedup
        rec = db_get_dedup(sig)
        if rec and (now - rec["last_fired"]) < cooldown_s:
            return False
        db_upsert_dedup(sig, now)
    except Exception as e:
        log.warning(f"alert_engine: dedup DB error: {e}")

    return True


# ── Action dispatch ───────────────────────────────────────────────

def _dispatch(rule: dict, ctx: dict):
    for action in rule.get("actions", []):
        atype = action.get("atype", "")
        cfg   = action.get("config", {})
        try:
            if atype == "email":
                _dispatch_email(cfg, ctx)
            elif atype == "webhook":
                _dispatch_webhook(cfg, ctx)
            elif atype == "syslog":
                _dispatch_syslog(cfg, ctx)
            elif atype == "browser":
                _dispatch_browser(cfg, ctx)
            else:
                log.debug(f"alert_engine: unsupported action type '{atype}' (skipped)")
        except Exception as e:
            log.error(f"alert_engine: action '{atype}' dispatch error: {e}")


def _dispatch_email(cfg: dict, ctx: dict):
    from monitoring.smtp_alert import send_rule_email
    from db.groups import db_resolve_group_emails

    emails: set = set()

    # Resolve groups → member emails
    for gid in (cfg.get("groups") or []):
        try:
            emails.update(db_resolve_group_emails(int(gid)))
        except Exception as e:
            log.warning(f"alert_engine: group {gid} email resolve error: {e}")

    # Extra/legacy raw emails  (extra_to = new field, to = legacy field)
    raw = cfg.get("extra_to") or cfg.get("to") or ""
    for addr in str(raw).split(","):
        addr = addr.strip()
        if addr:
            emails.add(addr)

    if not emails:
        log.warning("alert_engine: email action has no recipients — skipped")
        return

    send_rule_email(",".join(sorted(emails)),
                    cfg.get("subject", "").strip(),
                    cfg.get("body", "").strip(),
                    ctx)


def _is_safe_url(url: str) -> bool:
    """Return True if url is safe to request (not localhost/link-local/private)."""
    import ipaddress
    from urllib.parse import urlparse
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        host = (p.hostname or "").lower()
        if not host:
            return False
        if host in ("localhost",) or host.startswith("127.") or host == "::1":
            return False
        if host.endswith(".local"):
            return False
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_global
        except ValueError:
            return True  # hostname — allow (DNS resolves at request time)
    except Exception:
        return False


def _dispatch_webhook(cfg: dict, ctx: dict):
    """HTTP POST webhook with JSON-template body. Runs synchronously in engine thread."""
    url = cfg.get("url", "").strip()
    if not url:
        log.warning("alert_engine: webhook action has no URL — skipped")
        return
    if not _is_safe_url(url):
        log.error(f"alert_engine: webhook URL blocked (SSRF guard): {url!r}")
        return

    body_tpl = cfg.get("body", "").strip()
    method   = cfg.get("method", "POST").strip().upper()

    def _safe(v):
        return str(v or "").replace("\r", "").replace("\n", " ")

    if body_tpl:
        try:
            payload = body_tpl.format(**{k: _safe(str(v)) for k, v in ctx.items()})
        except (KeyError, ValueError):
            payload = body_tpl
        payload_bytes = payload.encode("utf-8")
        content_type  = "application/json"
    else:
        import json as _json
        payload_bytes = _json.dumps({k: str(v) if v is not None else None
                                     for k, v in ctx.items()}).encode("utf-8")
        content_type  = "application/json"

    headers = {"Content-Type": content_type, "User-Agent": "PingWatch-AlertEngine/1.0"}
    extra = cfg.get("headers", {})
    if isinstance(extra, dict):
        headers.update(extra)

    req = urllib.request.Request(url, data=payload_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
        log.info(f"alert_engine: webhook {method} {url} → {status}")
    except urllib.error.HTTPError as e:
        log.error(f"alert_engine: webhook {url} HTTP {e.code}: {e.reason}")
    except Exception as e:
        log.error(f"alert_engine: webhook {url} error: {e}")


def _dispatch_syslog(cfg: dict, ctx: dict):
    """Send rule-fired event via syslog using the global syslog_client."""
    try:
        from monitoring.syslog_client import (
            _reload, _ensure_started, _format_rfc5424,
            _send_one, _FACILITY, _SEV_MAP, _Q as _SQ,
        )
    except ImportError as e:
        log.error(f"alert_engine: syslog import failed: {e}")
        return

    settings = _reload()
    host = cfg.get("host", "").strip() or settings.get("host", "")
    port = int(cfg.get("port", 0) or settings.get("port", 514))
    proto = str(cfg.get("proto", "") or settings.get("proto", "udp")).lower()

    if not host:
        log.warning("alert_engine: syslog action has no host — skipped")
        return

    sev_label = ctx.get("severity", "info")
    pri       = _FACILITY * 8 + _SEV_MAP.get(sev_label, 6)
    hostname  = socket.gethostname()

    dname = ctx.get("dname", ctx.get("did", "?"))
    sname = ctx.get("sname", ctx.get("sid", "?"))
    etype = ctx.get("event_type", "")
    detail = ctx.get("detail", "")
    msg   = f"[ALERT] {dname}/{sname} — {etype}" + (f" — {detail}" if detail else "")

    payload = _format_rfc5424(pri, hostname, msg)
    try:
        _ensure_started()
        _SQ.put_nowait((payload, host, port, proto))
    except queue.Full:
        pass
    except Exception as e:
        log.error(f"alert_engine: syslog enqueue error: {e}")


def _dispatch_browser(cfg: dict, ctx: dict):
    """Push a browser_notification SSE event to all connected clients."""
    def _render(tpl, default):
        if not tpl:
            return default
        try:
            return tpl.format(**{k: str(v or '') for k, v in ctx.items()})
        except (KeyError, ValueError):
            return tpl

    title = _render(
        cfg.get("title", ""),
        f"[{ctx.get('severity', '?')}] {ctx.get('dname', '?')}/{ctx.get('sname', '?')}"
    )
    body  = _render(
        cfg.get("body", ""),
        f"{ctx.get('event_type', '?')}: {ctx.get('detail', '')}"
    )
    sound = cfg.get("sound", "alert")  # "alert" | "double" | "none"

    push_payload = {
        "title":    title,
        "body":     body,
        "sound":    sound,
        "severity": ctx.get("severity", "info"),
    }
    try:
        from core.app_state import STATE
        STATE._broadcast("browser_notification", push_payload)
    except Exception as e:
        log.error(f"alert_engine: browser notification error: {e}")
