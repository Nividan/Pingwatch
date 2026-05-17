"""
monitoring/alert_dispatchers.py — Action dispatchers for the alert profile engine.

Each function takes (cfg: dict, ctx: dict) and synchronously dispatches one
side-effect — email, webhook POST, syslog packet, browser notification.
Extracted from the legacy alert_engine so the new profile engine can call them
without dragging the rules cache or condition matcher along.

Also exports:
    check_maintenance(ctx) → (suppressed: bool, window_name: str)
    is_safe_url(url)       → SSRF guard for webhooks
"""

import datetime
import json
import queue
import socket
import urllib.error
import urllib.request

from core.logger import log


# ── Maintenance window check (engine-level alert suppression) ────

def _mw_parse_groups(val: str) -> list:
    """Parse scope_value for group windows — JSON array (new) or plain string (legacy)."""
    if not val:
        return []
    try:
        parsed = json.loads(val)
        if isinstance(parsed, list):
            return parsed
    except (ValueError, TypeError):
        pass
    return [val]


def check_maintenance(ctx: dict) -> tuple:
    """Return (is_suppressed: bool, window_name: str).

    A maintenance window suppresses alerts for the matched scope while it is
    active. Recurring windows are also evaluated against the current weekday
    and time-of-day.
    """
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
            re_ = w.get("recur_end", "")
            if rs and re_:
                try:
                    rs_t = datetime.datetime.strptime(rs, "%H:%M").time()
                    re_t = datetime.datetime.strptime(re_, "%H:%M").time()
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
        if scope == "group" and ctx.get("grp", "") not in _mw_parse_groups(w.get("scope_value", "")):
            continue
        if scope == "device" and ctx.get("did", "") != w.get("scope_value", ""):
            continue
        # Site scope was added in v1.0 alongside Site → Group → Device hierarchy.
        # ctx["site"] is populated by _build_ctx in alert_profile_engine.
        if scope == "site" and ctx.get("site", "") != w.get("scope_value", ""):
            continue

        return True, w.get("name", "")

    return False, ""


# ── SSRF guard for webhooks ──────────────────────────────────────

def is_safe_url(url: str) -> bool:
    """Return True if url is safe to request (not localhost/link-local/private).

    Resolves hostnames to IPs first — prevents DNS-based SSRF where an
    external hostname points to an internal address.
    """
    import ipaddress
    from urllib.parse import urlparse
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        host = (p.hostname or "").lower()
        if not host:
            return False
        try:
            addr = socket.gethostbyname(host)
            ip = ipaddress.ip_address(addr)
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
                return False
            return True
        except (socket.gaierror, ValueError):
            return False  # DNS failed — fail closed
    except Exception:
        return False


# ── Email dispatcher ─────────────────────────────────────────────

def _resolve_email_recipients(cfg: dict) -> set:
    """Resolve a template config to a set of email addresses.

    Recognized fields:
        to_users   — list of usernames; their email column is fetched
        to_groups  — list of user_group ids; all members' emails fetched
        to_emails  — list/CSV of raw addresses
        to         — legacy alias for to_emails
    """
    emails: set = set()

    # Usernames → email column
    users = cfg.get("to_users") or []
    if isinstance(users, str):
        users = [u.strip() for u in users.split(",") if u.strip()]
    if users:
        try:
            from db.helpers import db_query
            ph = ",".join("?" * len(users))
            rows = db_query(
                "main",
                f"SELECT email FROM users WHERE username IN ({ph}) "
                f"AND email IS NOT NULL AND email != ''",
                tuple(users)
            )
            for r in rows:
                emails.add(r["email"])
        except Exception as e:
            log.warning(f"alert_dispatchers: user email resolve error: {e}")

    # Group ids → member emails
    groups = cfg.get("to_groups") or []
    if isinstance(groups, str):
        groups = [g.strip() for g in groups.split(",") if g.strip()]
    for gid in groups:
        try:
            from db.groups import db_resolve_group_emails
            emails.update(db_resolve_group_emails(int(gid)))
        except Exception as e:
            log.warning(f"alert_dispatchers: group {gid} email resolve error: {e}")

    # Raw addresses
    raw = cfg.get("to_emails") or cfg.get("to") or ""
    if isinstance(raw, list):
        raw = ",".join(raw)
    for addr in str(raw).split(","):
        addr = addr.strip()
        if addr:
            emails.add(addr)

    return emails


def dispatch_email(cfg: dict, ctx: dict) -> None:
    from monitoring.smtp_alert import send_rule_email

    emails = _resolve_email_recipients(cfg)
    if not emails:
        log.warning("alert_dispatchers: email action has no recipients — skipped")
        return

    send_rule_email(",".join(sorted(emails)),
                    str(cfg.get("subject") or "").strip(),
                    str(cfg.get("body") or "").strip(),
                    ctx)


def dispatch_email_batch(cfg: dict, batch_ctx: dict) -> None:
    """Flush a batched email — called by alert_batcher when 2+ items bucketed."""
    from monitoring.smtp_alert import send_rule_email_batch

    emails = _resolve_email_recipients(cfg)
    if not emails:
        log.warning("alert_dispatchers: batched email has no recipients — skipped")
        return

    send_rule_email_batch(",".join(sorted(emails)),
                          str(cfg.get("subject") or "").strip(),
                          batch_ctx)


# ── Webhook dispatcher ───────────────────────────────────────────

def dispatch_webhook(cfg: dict, ctx: dict) -> None:
    """HTTP POST webhook with optional body template. Synchronous."""
    url = str(cfg.get("url") or "").strip()
    if not url:
        log.warning("alert_dispatchers: webhook action has no URL — skipped")
        return
    if not is_safe_url(url):
        log.error(f"alert_dispatchers: webhook URL blocked (SSRF guard): {url!r}")
        return

    body_tpl = str(cfg.get("body") or "").strip()
    method   = str(cfg.get("method") or "POST").strip().upper()

    def _safe(v):
        return str(v or "").replace("\r", "").replace("\n", " ")

    if body_tpl:
        try:
            payload = body_tpl.format(**{k: _safe(str(v)) for k, v in ctx.items()})
        except (KeyError, ValueError):
            payload = body_tpl
        payload_bytes = payload.encode("utf-8")
    else:
        payload_bytes = json.dumps(
            {k: str(v) if v is not None else None for k, v in ctx.items()}
        ).encode("utf-8")

    headers = {"Content-Type": "application/json",
               "User-Agent":   "PingWatch-AlertEngine/2.0"}
    extra = cfg.get("headers") or {}
    if isinstance(extra, dict):
        headers.update(extra)

    req = urllib.request.Request(url, data=payload_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
        log.info(f"alert_dispatchers: webhook {method} {url} → {status}")
    except urllib.error.HTTPError as e:
        log.error(f"alert_dispatchers: webhook {url} HTTP {e.code}: {e.reason}")
    except Exception as e:
        log.error(f"alert_dispatchers: webhook {url} error: {e}")


def dispatch_webhook_batch(cfg: dict, batch_ctx: dict) -> None:
    """HTTP POST/PUT a batched webhook payload.

    Only invoked when cfg['batch_aware'] is True — receiver has opted in to
    the array payload shape:
        { "count": 12, "severity_counts": {...}, "alerts": [ctx, ctx, ...] }
    """
    url = str(cfg.get("url") or "").strip()
    if not url:
        log.warning("alert_dispatchers: batched webhook action has no URL — skipped")
        return
    if not is_safe_url(url):
        log.error(f"alert_dispatchers: batched webhook URL blocked (SSRF guard): {url!r}")
        return

    method   = str(cfg.get("method") or "POST").strip().upper()
    # Convert batch_ctx to JSON-safe shape — each alert ctx already contains
    # primitive-ish values, but enforce str-coercion for anything unusual.
    payload = {
        "count":           int(batch_ctx.get("count") or 0),
        "severity_counts": batch_ctx.get("severity_counts") or {},
        "severity_label":  batch_ctx.get("severity_label") or "",
        "window_start_ts": batch_ctx.get("window_start_ts"),
        "window_end_ts":   batch_ctx.get("window_end_ts"),
        "alerts": [
            {k: (v if isinstance(v, (int, float, bool)) or v is None else str(v))
             for k, v in a.items()}
            for a in (batch_ctx.get("alerts") or [])
        ],
    }
    payload_bytes = json.dumps(payload).encode("utf-8")

    headers = {"Content-Type": "application/json",
               "User-Agent":   "PingWatch-AlertEngine/2.0",
               "X-PingWatch-Batch": "1"}
    extra = cfg.get("headers") or {}
    if isinstance(extra, dict):
        headers.update(extra)

    req = urllib.request.Request(url, data=payload_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
        log.info(f"alert_dispatchers: batched webhook {method} {url} "
                 f"→ {status} ({payload['count']} alerts)")
    except urllib.error.HTTPError as e:
        log.error(f"alert_dispatchers: batched webhook {url} HTTP {e.code}: {e.reason}")
        raise
    except Exception as e:
        log.error(f"alert_dispatchers: batched webhook {url} error: {e}")
        raise


# ── Syslog dispatcher ────────────────────────────────────────────

def dispatch_syslog(cfg: dict, ctx: dict) -> None:
    try:
        from monitoring.syslog_client import (
            _reload, _ensure_started, _format_rfc5424,
            _FACILITY, _SEV_MAP, _Q as _SQ,
        )
    except ImportError as e:
        log.error(f"alert_dispatchers: syslog import failed: {e}")
        return

    settings = _reload()
    host  = str(cfg.get("host") or settings.get("host") or "").strip()
    port  = int(cfg.get("port") or settings.get("port") or 514)
    proto = str(cfg.get("proto") or settings.get("proto") or "udp").lower()

    if not host:
        log.warning("alert_dispatchers: syslog action has no host — skipped")
        return

    sev_label = ctx.get("severity", "info")
    pri       = _FACILITY * 8 + _SEV_MAP.get(sev_label, 6)
    hostname  = socket.gethostname()

    dname  = ctx.get("dname",  ctx.get("did", "?"))
    sname  = ctx.get("sname",  ctx.get("sid", "?"))
    etype  = ctx.get("event_type", "")
    detail = ctx.get("detail", "")
    # SNMP enum-state sensors carry the raw enum code ("2") in detail; translate
    # to the human label ("down") via the unit legend or well-known OID fallback.
    if ctx.get("stype") == "snmp" and detail:
        try:
            from core.state import _effective_enum_legend_py
            legend = _effective_enum_legend_py(ctx.get("snmp_unit", ""),
                                               ctx.get("snmp_oid", ""))
            if legend:
                try:
                    code = str(int(float(detail)))
                except (ValueError, TypeError):
                    code = str(detail)
                if code in legend:
                    detail = legend[code]
        except Exception:
            pass
    msg    = f"[ALERT] {dname}/{sname} — {etype}" + (f" — {detail}" if detail else "")

    payload = _format_rfc5424(pri, hostname, msg)
    try:
        _ensure_started()
        _SQ.put_nowait((payload, host, port, proto))
    except queue.Full:
        pass
    except Exception as e:
        log.error(f"alert_dispatchers: syslog enqueue error: {e}")


# ── Browser notification dispatcher (SSE push) ───────────────────

def dispatch_browser(cfg: dict, ctx: dict) -> None:
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
        log.error(f"alert_dispatchers: browser notification error: {e}")


# ── Unified entry point ──────────────────────────────────────────

_DISPATCHERS = {
    "email":   dispatch_email,
    "webhook": dispatch_webhook,
    "syslog":  dispatch_syslog,
    "browser": dispatch_browser,
}


def dispatch(atype: str, cfg: dict, ctx: dict) -> None:
    """Dispatch one action by type.

    Email and webhook actions pass through the alert_batcher first — if the
    batcher accepts them, they're held briefly and sent as part of a combined
    notification. If the batcher refuses (disabled, webhook receiver not
    batch-aware, or any internal error), we fall straight through to the
    existing per-event dispatchers.

    **Safety invariant:** no matter what happens inside the batcher, this
    function must attempt the per-event send. A bug in batching cannot
    silence alerts.
    """
    fn = _DISPATCHERS.get(atype)
    if not fn:
        log.debug(f"alert_dispatchers: unsupported action type {atype!r}")
        return
    # Try to enqueue email / webhook actions first. Any error → fall through.
    if atype == "email":
        try:
            from monitoring.alert_batcher import try_enqueue_email
            if try_enqueue_email(cfg, ctx):
                return
        except Exception as e:
            log.warning(f"alert_dispatchers: batcher (email) errored, sending immediately: {e}")
    elif atype == "webhook":
        try:
            from monitoring.alert_batcher import try_enqueue_webhook
            if try_enqueue_webhook(cfg, ctx):
                return
        except Exception as e:
            log.warning(f"alert_dispatchers: batcher (webhook) errored, sending immediately: {e}")
    try:
        fn(cfg, ctx)
    except Exception as e:
        log.error(f"alert_dispatchers: action {atype!r} error: {e}")
