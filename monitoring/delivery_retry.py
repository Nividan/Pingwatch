"""
monitoring/delivery_retry.py — Delivery execution layer for notifications:
dedicated dispatch pool, bounded retry queue with exponential backoff, and
per-destination circuit breakers.

Why this exists:
  * Every notification channel used to be single-attempt — a transient SMTP
    greylist or webhook timeout permanently dropped the alert (the engine had
    already recorded the stage as fired).
  * Non-batched sends ran inline on the shared probe worker pool, so one dead
    webhook receiver (10 s timeout × N alerts) delayed probing fleet-wide
    during the exact outage that generated the alerts.
  * A dead SMTP relay was re-connected (and re-logged) for every alert in a
    storm with zero backoff.

API:
    submit_delivery(channel, dest_key, send_fn, describe)
        Run send_fn on the dispatch pool with retry + breaker semantics.
        Returns immediately — never blocks the calling (probe) thread.
        send_fn MUST raise on failure for retries to work.

    run_with_retry(channel, dest_key, send_fn, describe)
        Same semantics, but the first attempt runs on the CALLER's thread.
        Used by the alert batcher's flusher, which is already a dedicated
        thread and wants its sends serialized.

Retry: 30 s → 2 min → 8 min, then the notification is dropped with an ERROR
log ("PERMANENTLY FAILED"). The retry queue is bounded (500); on overflow the
oldest pending retry is dropped with a warning. Queue is in-memory: retries
pending at process exit are lost (the batcher's synchronous shutdown drain
still covers the main shutdown path).

Breaker (per dest_key, e.g. "smtp:relay:25", "webhook:https://…"):
    CLOSED    normal operation. Opens after 5 consecutive failures.
    OPEN      sends skip straight to retry scheduling for 10 min — a dead
              relay isn't hammered or re-logged per alert during a storm.
    HALF-OPEN after the cooldown one probe attempt is let through;
              success closes the circuit and queued retries drain.
"""

import heapq
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from core.logger import log

_BACKOFF_S      = (30, 120, 480)     # delay before retry #1, #2, #3
_RETRY_MAX      = len(_BACKOFF_S)
_QUEUE_CAP      = 500                # max pending retries
_MAX_ITEM_AGE_S = 3600               # give up on anything this old (breaker deferrals)

_BRK_THRESHOLD  = 5                  # consecutive failures → open
_BRK_COOLDOWN_S = 600                # open → half-open after this long
_BRK_RECHECK_S  = 60                 # deferred-item re-check cadence while open

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pw-dispatch")

_lock = threading.Lock()
_breakers: dict = {}                 # dest_key → {fails, opened_ts, probing}
_retry_heap: list = []               # (due_ts, seq, item)
_retry_seq = 0
_retry_wake = threading.Event()
_worker_started = False


# ── Circuit breaker ───────────────────────────────────────────────

def _breaker(dest_key: str) -> dict:
    b = _breakers.get(dest_key)
    if b is None:
        b = {"fails": 0, "opened_ts": 0.0, "probing": False}
        _breakers[dest_key] = b
    return b


def _breaker_allows(dest_key: str) -> bool:
    """True if a send to dest_key may proceed now (closed, or half-open probe)."""
    now = time.time()
    with _lock:
        b = _breaker(dest_key)
        if b["fails"] < _BRK_THRESHOLD:
            return True
        if now - b["opened_ts"] >= _BRK_COOLDOWN_S and not b["probing"]:
            b["probing"] = True      # half-open: exactly one probe attempt
            return True
        return False


def record_success(dest_key: str) -> None:
    with _lock:
        b = _breaker(dest_key)
        was_open = b["fails"] >= _BRK_THRESHOLD
        b["fails"] = 0
        b["opened_ts"] = 0.0
        b["probing"] = False
    if was_open:
        log.info(f"delivery: circuit CLOSED for {dest_key} — destination recovered")


def record_failure(dest_key: str) -> None:
    with _lock:
        b = _breaker(dest_key)
        b["fails"] += 1
        b["probing"] = False
        just_opened = b["fails"] == _BRK_THRESHOLD
        if b["fails"] >= _BRK_THRESHOLD:
            b["opened_ts"] = time.time()   # (re-)open, incl. failed half-open probe
    if just_opened:
        log.warning(f"delivery: circuit OPEN for {dest_key} after "
                    f"{_BRK_THRESHOLD} consecutive failures — suppressing "
                    f"send attempts for {_BRK_COOLDOWN_S // 60} min "
                    f"(pending notifications retry after recovery)")


def breaker_states() -> dict:
    """Snapshot for diagnostics: dest_key → open/closed + failure count."""
    out = {}
    with _lock:
        for k, b in _breakers.items():
            out[k] = {
                "state": "open" if b["fails"] >= _BRK_THRESHOLD else "closed",
                "consecutive_failures": b["fails"],
            }
    return out


# ── Retry queue ───────────────────────────────────────────────────

def _ensure_worker() -> None:
    global _worker_started
    with _lock:
        if _worker_started:
            return
        _worker_started = True
    t = threading.Thread(target=_worker_loop, daemon=True, name="pw-delivery-retry")
    t.start()


def _schedule_at(item: dict, due_ts: float) -> None:
    global _retry_seq
    with _lock:
        if len(_retry_heap) >= _QUEUE_CAP:
            # Drop the oldest pending item (by creation time) — bounded loss
            # beats unbounded growth while a destination is down for hours.
            oldest_i = min(range(len(_retry_heap)),
                           key=lambda i: _retry_heap[i][2]["created_ts"])
            dropped = _retry_heap.pop(oldest_i)[2]
            heapq.heapify(_retry_heap)
            log.warning(f"delivery: retry queue full ({_QUEUE_CAP}) — dropped "
                        f"oldest pending: {dropped['describe']}")
        _retry_seq += 1
        heapq.heappush(_retry_heap, (due_ts, _retry_seq, item))
    _retry_wake.set()


def _worker_loop() -> None:
    while True:
        try:
            with _lock:
                due = _retry_heap[0][0] if _retry_heap else None
            now = time.time()
            if due is None or due > now:
                wait_s = 30.0 if due is None else min(due - now, 30.0)
                _retry_wake.wait(timeout=max(wait_s, 0.05))
                _retry_wake.clear()
                continue
            with _lock:
                if not _retry_heap or _retry_heap[0][0] > time.time():
                    continue
                _, _, item = heapq.heappop(_retry_heap)
            _attempt(item)
        except Exception as e:
            try:
                log.error(f"delivery: retry worker error: {e}")
            except Exception:
                pass
            time.sleep(1.0)


def _attempt(item: dict) -> None:
    dest = item["dest_key"]
    if time.time() - item["created_ts"] > _MAX_ITEM_AGE_S:
        log.error(f"delivery: {item['describe']} dropped — older than "
                  f"{_MAX_ITEM_AGE_S // 60} min (destination never recovered)")
        return
    if not _breaker_allows(dest):
        # Circuit open: defer without consuming a retry attempt.
        _schedule_at(item, time.time() + _BRK_RECHECK_S)
        return
    try:
        item["send_fn"]()
        record_success(dest)
        if item["attempt"] > 0:
            log.info(f"delivery: {item['describe']} succeeded on retry "
                     f"#{item['attempt']}")
    except Exception as e:
        record_failure(dest)
        item["attempt"] += 1
        if item["attempt"] <= _RETRY_MAX:
            delay = _BACKOFF_S[item["attempt"] - 1]
            log.warning(f"delivery: {item['describe']} failed ({e}) — "
                        f"retry #{item['attempt']} in {delay}s")
            _schedule_at(item, time.time() + delay)
        else:
            log.error(f"delivery: {item['describe']} PERMANENTLY FAILED "
                      f"after {_RETRY_MAX} retries: {e}")


# ── Public API ────────────────────────────────────────────────────

def submit_delivery(channel: str, dest_key: str, send_fn, describe: str) -> None:
    """Run send_fn on the dispatch pool with retry + breaker semantics.
    Never blocks the caller. send_fn must raise on failure."""
    _ensure_worker()
    item = {"channel": channel, "dest_key": dest_key, "send_fn": send_fn,
            "describe": describe, "attempt": 0, "created_ts": time.time()}
    try:
        _pool.submit(_attempt, item)
    except Exception as e:    # pool torn down at interpreter exit
        log.error(f"delivery: submit failed for {describe}: {e}")


def run_with_retry(channel: str, dest_key: str, send_fn, describe: str) -> None:
    """Like submit_delivery but the first attempt runs on the caller's
    thread (retries still go to the worker). For already-dedicated threads
    like the alert batcher's flusher."""
    _ensure_worker()
    item = {"channel": channel, "dest_key": dest_key, "send_fn": send_fn,
            "describe": describe, "attempt": 0, "created_ts": time.time()}
    _attempt(item)
