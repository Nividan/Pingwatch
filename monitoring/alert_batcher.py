"""
monitoring/alert_batcher.py — Cross-sensor notification batching.

Holds outbound email/webhook notifications for a short window and emits one
combined notification per (channel, destination, severity) bucket instead of N
individual ones. Designed to absorb "alert storms" — e.g. a switch dies and 12
sensors behind it all flap to DOWN within a few seconds.

Critical safety property:
    A bug here MUST NOT silence alerts. Every code path is wrapped so that on
    any failure the caller falls back to immediate per-event dispatch.

Architecture:
    enqueue(channel, dest_key, severity, cfg, ctx)  ──►  in-memory queue
                                                          │
                                  daemon flusher thread ──┤
                                                          ▼
                                     dispatch_*_batch()  OR  dispatch_*()  (single)

Batching is decided per-call by `_should_batch()`. Settings are re-read on
every enqueue so live PATCHes take effect without restart.

Syslog and browser notifications never go through this module — they're
either inherently low-spam (browser) or downstream collectors want each
event as a discrete log line (syslog).
"""

import threading
import time
from typing import Callable

from core.logger import log


_QUEUE_LOCK = threading.Lock()
_QUEUES: dict = {}                  # bucket_key -> list[QueueItem]
_FLUSHER_STARTED = False
_FLUSHER_LOCK = threading.Lock()
_SHUTDOWN = threading.Event()


class QueueItem:
    """One queued notification — a (cfg, ctx) pair tagged with arrival time."""
    __slots__ = ("cfg", "ctx", "ts", "is_recovery")

    def __init__(self, cfg: dict, ctx: dict, is_recovery: bool):
        self.cfg = cfg
        self.ctx = ctx
        self.ts = time.time()
        self.is_recovery = is_recovery


# ── Settings access (re-read on every enqueue — live PATCH takes effect) ──

def _read_settings() -> dict:
    """Snapshot the current batch settings. Cheap — dict lookups."""
    try:
        from core.settings import get as _get
        return {
            "enabled":  str(_get("alert_batch_enabled", "1")).strip() == "1",
            "window_s": max(5, min(3600, int(_get("alert_batch_window_s", 60) or 60))),
            "max_size": max(2, min(500, int(_get("alert_batch_max_size", 20) or 20))),
        }
    except Exception as e:
        # Bad settings → safest behaviour is "batching off, immediate dispatch".
        log.warning(f"alert_batcher: settings read failed, batching disabled: {e}")
        return {"enabled": False, "window_s": 60, "max_size": 20}


# ── Bucket key helpers ───────────────────────────────────────────────

def _bucket_key(channel: str, dest_key: str, severity: str) -> tuple:
    """Build the dict key. Items in the same bucket are eligible to batch."""
    return (channel, dest_key, severity)


def _email_dest_key(cfg: dict) -> str:
    """Stable destination identifier for an email action template.

    Includes resolved recipients so two profiles emailing the same address
    share a bucket; differs across distinct recipient sets.
    """
    try:
        from monitoring.alert_dispatchers import _resolve_email_recipients
        emails = sorted(_resolve_email_recipients(cfg))
    except Exception:
        emails = []
    # Subject template differences cause different headers; keep them separate
    # so a "weekly digest"-flavoured profile doesn't end up bundled with an
    # incident-flavoured one.
    subj = (cfg.get("subject") or "").strip()
    return f"{','.join(emails)}|{subj}"


def _webhook_dest_key(cfg: dict) -> str:
    """Stable destination identifier for a webhook action template."""
    return (cfg.get("url") or "").strip()


# ── Public enqueue API — called from alert_dispatchers.dispatch() ────

def try_enqueue_email(cfg: dict, ctx: dict) -> bool:
    """Return True if the email was queued for batched send; False if the
    caller should dispatch immediately (batching disabled or enqueue failed).
    """
    try:
        s = _read_settings()
        if not s["enabled"]:
            return False
        dest = _email_dest_key(cfg)
        if not dest:
            return False  # no recipients — let the immediate dispatcher log "skipped"
        sev = (ctx.get("severity") or "info").lower()
        is_recovery = sev == "recovery"
        _enqueue("email", dest, sev, cfg, ctx, is_recovery)
        _ensure_flusher()
        return True
    except Exception as e:
        log.warning(f"alert_batcher: email enqueue failed, falling back to immediate: {e}")
        return False


def try_enqueue_webhook(cfg: dict, ctx: dict) -> bool:
    """Return True if the webhook was queued; False to fall back to immediate.

    Webhook batching is **opt-in per template** via cfg["batch_aware"] — many
    webhook receivers expect one alert per POST and would break on a batched
    payload.
    """
    try:
        if not bool(cfg.get("batch_aware")):
            return False  # template hasn't opted in
        s = _read_settings()
        if not s["enabled"]:
            return False
        dest = _webhook_dest_key(cfg)
        if not dest:
            return False
        sev = (ctx.get("severity") or "info").lower()
        is_recovery = sev == "recovery"
        _enqueue("webhook", dest, sev, cfg, ctx, is_recovery)
        _ensure_flusher()
        return True
    except Exception as e:
        log.warning(f"alert_batcher: webhook enqueue failed, falling back to immediate: {e}")
        return False


def _enqueue(channel: str, dest: str, severity: str, cfg: dict, ctx: dict,
             is_recovery: bool) -> None:
    """Append to the bucket. Caller has already validated inputs."""
    key = _bucket_key(channel, dest, severity)
    item = QueueItem(cfg, ctx, is_recovery)
    with _QUEUE_LOCK:
        _QUEUES.setdefault(key, []).append(item)


# ── Flusher thread ──────────────────────────────────────────────────

def _ensure_flusher() -> None:
    """Lazily start the daemon flusher on first enqueue."""
    global _FLUSHER_STARTED
    if _FLUSHER_STARTED:
        return
    with _FLUSHER_LOCK:
        if _FLUSHER_STARTED:
            return
        t = threading.Thread(target=_flusher_loop, daemon=True, name="pw-alert-batch")
        t.start()
        _FLUSHER_STARTED = True
        log.info("alert_batcher: flusher thread started")


def _flusher_loop() -> None:
    """Tick every 5 seconds, flush any bucket that's full or aged out."""
    TICK = 5.0
    while not _SHUTDOWN.is_set():
        try:
            _tick_once()
        except Exception as e:
            # Never let the loop die — that would silently disable batching.
            log.error(f"alert_batcher: flusher tick error: {e}")
        if _SHUTDOWN.wait(TICK):
            break
    # On shutdown, drain everything synchronously so we don't lose pending alerts.
    try:
        _drain_all()
    except Exception as e:
        log.error(f"alert_batcher: shutdown drain error: {e}")


def _tick_once() -> None:
    """One flusher pass — checks each bucket against window/max-size triggers."""
    s = _read_settings()
    window = s["window_s"]
    max_size = s["max_size"]
    now = time.time()

    # Snapshot keys + decide which to flush; mutate _QUEUES under lock.
    to_flush: list = []   # list of (key, items_list)
    with _QUEUE_LOCK:
        for key, items in list(_QUEUES.items()):
            if not items:
                _QUEUES.pop(key, None)
                continue
            age = now - items[0].ts
            if len(items) >= max_size or age >= window:
                to_flush.append((key, items))
                _QUEUES.pop(key, None)

    # Dispatch outside the lock so a slow SMTP server doesn't stall enqueue.
    for key, items in to_flush:
        try:
            _flush_bucket(key, items)
        except Exception as e:
            log.error(f"alert_batcher: flush of bucket {key!r} failed "
                      f"({len(items)} item(s) lost): {e}")


def _flush_bucket(key: tuple, items: list) -> None:
    """Send one bucket — single-item buckets use the original per-event format
    (no behavioural change); multi-item buckets use the batch format."""
    channel, _dest, _sev = key
    if not items:
        return
    if len(items) == 1:
        # Single event — preserve the original look exactly.
        _dispatch_single(channel, items[0])
        return
    _dispatch_batch(channel, items)


def _dispatch_single(channel: str, item: QueueItem) -> None:
    """Wrapper around the per-event dispatchers used when only one event was
    queued before the window elapsed."""
    from monitoring.alert_dispatchers import _DISPATCHERS  # local to avoid cycle
    fn = _DISPATCHERS.get(channel)
    if not fn:
        log.warning(f"alert_batcher: no single-dispatcher for channel {channel!r}")
        return
    try:
        fn(item.cfg, item.ctx)
    except Exception as e:
        log.error(f"alert_batcher: single dispatch ({channel}) failed: {e}")


def _dispatch_batch(channel: str, items: list) -> None:
    """Send a multi-event batch via the channel-specific batched sender."""
    from monitoring import alert_dispatchers as _ad
    cfg = items[0].cfg  # all items in a bucket share template-level cfg
    batch_ctx = _build_batch_ctx(items)
    if channel == "email":
        try:
            _ad.dispatch_email_batch(cfg, batch_ctx)
        except Exception as e:
            log.error(f"alert_batcher: email batch dispatch failed: {e}")
            # Best-effort fallback: try sending each item individually so we
            # don't lose the alerts entirely.
            _fallback_individual("email", items)
    elif channel == "webhook":
        try:
            _ad.dispatch_webhook_batch(cfg, batch_ctx)
        except Exception as e:
            log.error(f"alert_batcher: webhook batch dispatch failed: {e}")
            _fallback_individual("webhook", items)
    else:
        log.warning(f"alert_batcher: no batch dispatcher for channel {channel!r}, "
                    f"falling back to individual sends")
        _fallback_individual(channel, items)


def _fallback_individual(channel: str, items: list) -> None:
    """Last-resort: if batched send failed, try each item one-by-one. Better
    to spam than to silently lose an alert."""
    for it in items:
        try:
            _dispatch_single(channel, it)
        except Exception as e:
            log.error(f"alert_batcher: fallback dispatch failed for one item: {e}")


def _build_batch_ctx(items: list) -> dict:
    """Aggregate per-event ctxs into a single batch ctx for batched senders.

    Severity breakdown: counts of critical/warning/recovery/info, in that order.
    """
    sev_counts = {"critical": 0, "warning": 0, "recovery": 0, "info": 0}
    alerts = []
    for it in items:
        sev = (it.ctx.get("severity") or "info").lower()
        if sev not in sev_counts:
            sev = "info"
        sev_counts[sev] += 1
        alerts.append(it.ctx)
    sev_label_parts = [f"{n} {label}" for label, n in sev_counts.items() if n]
    return {
        "count":             len(items),
        "alerts":            alerts,
        "severity_counts":   sev_counts,
        "severity_label":    ", ".join(sev_label_parts),
        "window_start_ts":   items[0].ts,
        "window_end_ts":     items[-1].ts,
    }


def _drain_all() -> None:
    """Synchronously flush every queued bucket — called on shutdown."""
    with _QUEUE_LOCK:
        snapshot = list(_QUEUES.items())
        _QUEUES.clear()
    drained = 0
    for key, items in snapshot:
        try:
            _flush_bucket(key, items)
            drained += len(items)
        except Exception as e:
            log.error(f"alert_batcher: drain of bucket {key!r} failed: {e}")
    if drained:
        log.info(f"alert_batcher: drained {drained} pending alert(s) on shutdown")


def shutdown() -> None:
    """Stop the flusher and drain remaining items. Safe to call multiple times."""
    _SHUTDOWN.set()


# Register atexit so pending batches don't vanish when the process stops.
import atexit as _atexit
_atexit.register(shutdown)


# ── Diagnostic accessor (used by Diagnostics tab if surfaced later) ──

def get_stats() -> dict:
    """Return a snapshot of batcher state — for Diagnostics."""
    with _QUEUE_LOCK:
        bucket_count = len(_QUEUES)
        item_count = sum(len(v) for v in _QUEUES.values())
        oldest_age = 0.0
        if _QUEUES:
            now = time.time()
            oldest_age = max(
                (now - v[0].ts) for v in _QUEUES.values() if v
            ) if any(_QUEUES.values()) else 0.0
    return {
        "buckets":      bucket_count,
        "queued_items": item_count,
        "oldest_age_s": round(oldest_age, 1),
        "flusher_alive": _FLUSHER_STARTED and not _SHUTDOWN.is_set(),
    }
