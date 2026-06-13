"""
vmware/client.py — VMware vCenter/ESXi connection, VM discovery, and metric probing.

Uses pyvmomi (optional dependency).  Session and metric results are cached to
minimise API calls when many sensors target the same vCenter / VM.
"""

import socket
import ssl
import time
import threading
from contextlib import contextmanager

from core.logger import log

# Every SOAP call during discover (RetrieveContent, CreateContainerView, lazy
# property reads on view.view iteration) happens under the process-global
# default socket timeout. Without a bound, one slow vCenter property read
# can park the discover thread for tens of minutes. 120s is generous for a
# large estate (~500 VMs) under moderate vCenter load; if legitimate calls
# hit it, an admin can raise it via a future setting.
_DISCOVER_TIMEOUT_S = 120

# Health-check timeout for cached-session revival. Must be short: if a
# cached session's underlying connection is dead, we want to fail fast and
# reconnect — not wait 60s for the default SmartConnect timeout.
_HEALTH_CHECK_TIMEOUT_S = 5

# Warm-on-connect budget. A freshly-connected vCenter session returns
# "VM not found" / "metric not available" on the very first PropertyCollector
# / QueryPerf calls until ServiceContent + the perf-counter catalog + the
# inventory view are populated server-side. _warm_session() forces that once
# per new session so the first real probe succeeds instead of false-failing.
_WARM_TIMEOUT_S = 25


@contextmanager
def _socket_timeout(seconds: float):
    """Temporarily set the default socket timeout for every socket opened
    inside the block. Restores the previous default in a finally so nested
    users (e.g. discover-inside-polling) don't leak the override.

    Caveat: only affects *newly-created* sockets. pyVmomi keeps an HTTPS
    connection pool; sockets opened before this block ignore the change.
    For a true hard cap, use `_run_with_timeout` below.
    """
    prev = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    try:
        yield
    finally:
        socket.setdefaulttimeout(prev)


class _ProbeTimeout(ConnectionError):
    """Raised by _run_with_timeout when the wall-clock cap trips.

    Subclass of ConnectionError so existing `except ConnectionError` sites (the
    discover functions) still catch it — but the hot probe path catches it
    separately and does NOT invalidate the shared (host, user) session. A slow
    vCenter is not a dead session; nuking it would force every sibling sensor on
    the same vCenter to reconnect into a cold catalog, turning one slow VM into
    an all-devices 'metric not available' cascade. A genuinely dead session is
    still caught by the CurrentTime health check on the next _get_session (with
    the 25-min TTL as a final backstop)."""


class _ReconnectInProgress(Exception):
    """Raised by _get_session(nonblocking_reconnect=True) when another thread is
    already rebuilding the shared session for this vCenter.

    The hot probe path passes nonblocking_reconnect=True so a sibling probe does
    NOT queue behind the in-flight reconnect — blocking there is what synchronised
    every sensor on one vCenter into a single 60s timeout herd. The caller serves
    a last-good cached sample for this cycle instead. NOT a ConnectionError, so it
    never trips the session-invalidation path."""


def _run_with_timeout(label: str, fn, timeout_s: float):
    """Run `fn()` in a daemon thread and raise _ProbeTimeout if it doesn't
    finish within `timeout_s` seconds.

    Why this is needed: pyVmomi's SOAP stub maintains a persistent HTTPS
    connection pool. Sockets in that pool inherit the default socket timeout
    at the moment they were created — which for us is None (infinite) before
    the first SmartConnect. `socket.setdefaulttimeout()` changes made inside
    our discover function don't retroactively apply to those pooled sockets,
    so `RetrieveContent()` / property iteration can still hang indefinitely.
    Thread.join() is the only reliable hard cap without forking pyVmomi.

    The daemon thread keeps running after timeout — it can't be forcibly
    killed in Python. That's acceptable for a rare admin-invoked operation:
    the stuck thread eventually returns (or dies with the process), and the
    cached session is invalidated on exception so the next click starts
    fresh rather than reusing the zombie.
    """
    result = {"val": None, "err": None, "done": False}

    def _worker():
        try:
            result["val"] = fn()
        except BaseException as e:
            result["err"] = e
        finally:
            result["done"] = True

    th = threading.Thread(target=_worker, daemon=True, name=f"pw-vmdisc-{label}")
    th.start()
    th.join(timeout=timeout_s)
    if not result["done"]:
        raise _ProbeTimeout(
            f"{label} timed out after {timeout_s}s — vCenter is slow or overloaded"
        )
    if result["err"] is not None:
        raise result["err"]
    return result["val"]

# ---------------------------------------------------------------------------
# Lazy import helper — pyvmomi is optional
# ---------------------------------------------------------------------------

def _require_pyvmomi():
    """Import and return (SmartConnect, Disconnect, vim, vmodl).  Raises RuntimeError if missing."""
    try:
        from pyVim.connect import SmartConnect, Disconnect
        from pyVmomi import vim, vmodl
        return SmartConnect, Disconnect, vim, vmodl
    except ImportError:
        raise RuntimeError(
            "pyvmomi is required for VMware sensors — pip install pyvmomi"
        )


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

VM_METRICS = [
    {"v": "cpu_usage",        "l": "CPU Usage",                 "group": "cpu",       "counter": "cpu.usage.average",                       "unit": "%",      "divisor": 100},
    {"v": "cpu_ready",        "l": "CPU Ready (Percent)",       "group": "cpu",       "counter": "cpu.ready.summation",                     "unit": "%",      "convert": "ready_pct"},
    {"v": "mem_active",       "l": "Memory Active",             "group": "mem",       "counter": "mem.active.average",                      "unit": "MB",     "divisor": 1024},
    {"v": "mem_consumed",     "l": "Memory Consumed",           "group": "mem",       "counter": "mem.consumed.average",                    "unit": "MB",     "divisor": 1024},
    {"v": "disk_read",        "l": "Disk Read",                 "group": "disk",      "counter": "disk.read.average",                       "unit": "KBps"},
    {"v": "disk_write",       "l": "Disk Write",                "group": "disk",      "counter": "disk.write.average",                      "unit": "KBps"},
    {"v": "disk_usage",       "l": "Disk Usage",                "group": "disk",      "counter": "disk.usage.average",                      "unit": "KBps"},
    {"v": "ds_read_lat",      "l": "Datastore ReadLatency",     "group": "datastore", "counter": "datastore.totalReadLatency.average",      "unit": "ms"},
    {"v": "ds_write_lat",     "l": "Datastore WriteLatency",    "group": "datastore", "counter": "datastore.totalWriteLatency.average",     "unit": "ms"},
    {"v": "net_rx",           "l": "Network Received",          "group": "net",       "counter": "net.received.average",                    "unit": "KBps"},
    {"v": "net_tx",           "l": "Network Transmitted",       "group": "net",       "counter": "net.transmitted.average",                 "unit": "KBps"},
    {"v": "net_usage",        "l": "Network Usage",             "group": "net",       "counter": "net.usage.average",                       "unit": "KBps"},
    {"v": "disk_used_pct",    "l": "Disk Used (%)",             "group": "disk",      "counter": None,                                      "unit": "%"},
    {"v": "uptime",           "l": "Uptime",                    "group": "sys",       "counter": "sys.uptime.latest",                       "unit": "seconds"},
    {"v": "on",               "l": "Power State",               "group": "sys",       "counter": None,                                      "unit": ""},
]

HOST_METRICS = [
    # CPU
    {"v": "host_cpu_usage",      "l": "CPU Usage",              "group": "cpu",       "counter": "cpu.usage.average",                    "unit": "%",      "divisor": 100},
    {"v": "host_cpu_ready",      "l": "CPU Ready (%)",          "group": "cpu",       "counter": "cpu.ready.summation",                  "unit": "%",      "convert": "ready_pct"},
    # Memory
    {"v": "host_mem_active",     "l": "Memory Active",          "group": "mem",       "counter": "mem.active.average",                   "unit": "MB",     "divisor": 1024},
    {"v": "host_mem_consumed",   "l": "Memory Consumed",        "group": "mem",       "counter": "mem.consumed.average",                 "unit": "MB",     "divisor": 1024},
    {"v": "host_mem_usage_pct",  "l": "Memory Usage (%)",       "group": "mem",       "counter": "mem.usage.average",                    "unit": "%",      "divisor": 100},
    {"v": "host_mem_swap",       "l": "Memory Swap Used",       "group": "mem",       "counter": "mem.swapused.average",                 "unit": "MB",     "divisor": 1024},
    # Disk
    {"v": "host_disk_read",      "l": "Disk Read",              "group": "disk",      "counter": "disk.read.average",                    "unit": "KBps"},
    {"v": "host_disk_write",     "l": "Disk Write",             "group": "disk",      "counter": "disk.write.average",                   "unit": "KBps"},
    {"v": "host_disk_usage",     "l": "Disk Usage",             "group": "disk",      "counter": "disk.usage.average",                   "unit": "KBps"},
    {"v": "host_disk_dev_lat",   "l": "Disk Device Latency",    "group": "disk",      "counter": "disk.deviceLatency.average",            "unit": "ms"},
    {"v": "host_disk_kern_lat",  "l": "Disk Kernel Latency",    "group": "disk",      "counter": "disk.kernelLatency.average",            "unit": "ms"},
    # Datastore
    {"v": "host_ds_read_lat",    "l": "Datastore Read Latency", "group": "datastore", "counter": "datastore.totalReadLatency.average",    "unit": "ms"},
    {"v": "host_ds_write_lat",   "l": "Datastore Write Latency","group": "datastore", "counter": "datastore.totalWriteLatency.average",   "unit": "ms"},
    # Network
    {"v": "host_net_rx",         "l": "Network Received",       "group": "net",       "counter": "net.received.average",                 "unit": "KBps"},
    {"v": "host_net_tx",         "l": "Network Transmitted",    "group": "net",       "counter": "net.transmitted.average",               "unit": "KBps"},
    {"v": "host_net_usage",      "l": "Network Usage",          "group": "net",       "counter": "net.usage.average",                    "unit": "KBps"},
    # System
    {"v": "host_power",          "l": "Power Consumption",      "group": "sys",       "counter": "power.power.average",                  "unit": "watt"},
    {"v": "host_uptime",         "l": "Uptime",                 "group": "sys",       "counter": "sys.uptime.latest",                    "unit": "seconds"},
]

DATASTORE_METRICS = [
    {"v": "dstore_free_gb",   "l": "Free Space (GB)",           "group": "capacity",  "counter": None,                                      "unit": "GB"},
]

# Quick lookup: metric key → definition
_METRIC_BY_KEY       = {m["v"]: m for m in VM_METRICS}
_HOST_METRIC_BY_KEY  = {m["v"]: m for m in HOST_METRICS}
_DSTORE_METRIC_BY_KEY = {m["v"]: m for m in DATASTORE_METRICS}

# pyvmomi counter name → metric key
_COUNTER_TO_KEY = {m["counter"]: m["v"] for m in VM_METRICS}


def _fmt_bytes(n):
    """Render an integer byte count as a compact human-readable string."""
    if n is None:
        return "—"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if n >= 1024 ** 4:
        return f"{n / 1024 ** 4:.2f} TB"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{int(n)} B"


# ---------------------------------------------------------------------------
# Session cache
# ---------------------------------------------------------------------------

_sessions = {}          # (host, user) → (ServiceInstance, expiry_mono)
_sessions_lock = threading.Lock()
_SESSION_TTL = 25 * 60  # 25 min (vCenter default timeout = 30 min)


def _make_ssl_ctx(verify_ssl):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if not verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        # Bare SSLContext doesn't auto-load defaults — populate system CAs first,
        # then layer on any user-uploaded trusted CAs from /api/tls/ca-certs.
        ctx.load_default_certs()
        from core.ssl_trust import apply_trusted_cas
        apply_trusted_cas(ctx)
    return ctx


# Per-(host,user) reconnect locks — single-flight session creation. When a
# session expires or fails its health check, exactly ONE thread rebuilds it
# under this lock while every other probe to the same vCenter waits and reuses
# the result. Without it, all N sensors targeting a vCenter reconnect at once
# on the 25-min TTL boundary, and each publish Disconnect()s the previous
# winner's session — including one a sibling probe is mid-SOAP-call on, which
# surfaces as vim.fault.NotAuthenticated. Per-key (not global) so a slow login
# to one vCenter never blocks probes to another.
_connect_locks = {}
_connect_locks_guard = threading.Lock()


def _connect_lock_for(key):
    with _connect_locks_guard:
        lk = _connect_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _connect_locks[key] = lk
        return lk


def _healthy_cached(entry, now):
    """Return the ServiceInstance from a cache entry if it's unexpired and
    answers a cheap authenticated call within the health-check timeout, else
    None. The check is a network round-trip — callers run it outside every
    lock so one hung-but-reachable vCenter can't stall probes to others."""
    if entry is None:
        return None
    si, expiry = entry
    if now >= expiry:
        return None
    try:
        with _socket_timeout(_HEALTH_CHECK_TIMEOUT_S):
            si.CurrentTime()
        return si
    except Exception:
        return None


def _get_session(host, user, password, port=443, verify_ssl=False,
                 nonblocking_reconnect=False):
    """Return a cached or fresh ServiceInstance.

    Concurrency model — single-flight per (host, user):
      * A healthy cached session is validated and returned WITHOUT any lock —
        the health check is a network round-trip, and serialising it would let
        one slow vCenter stall probes to every other vCenter.
      * When the session is expired or unhealthy, exactly ONE thread rebuilds
        it under a per-key connect lock. With nonblocking_reconnect=False (admin
        / discovery callers) siblings wait and reuse the result. With
        nonblocking_reconnect=True (the hot probe path) a sibling that finds the
        reconnect already in flight raises _ReconnectInProgress immediately
        instead of waiting — blocking there synchronised every sensor on one
        vCenter into a single 60s timeout herd. Either way: no login storm, no
        Disconnect() of an in-flight session (the NotAuthenticated cascade).

    Every ServiceInstance we stop using is Disconnect()-ed (deferred by a grace
    delay — see _disconnect_later) — pyVmomi holds an HTTPS connection AND a
    server-side vCenter session per instance, and vCenter caps concurrent
    sessions. Dropping a reference without Disconnect() leaked one session
    every TTL (25 min) until vCenter locked everyone out.
    """
    SmartConnect, Disconnect, vim, vmodl = _require_pyvmomi()

    key = (host, user)

    # ── Fast path: a healthy cached session, validated outside every lock ──
    with _sessions_lock:
        entry = _sessions.get(key)
    si = _healthy_cached(entry, time.monotonic())
    if si is not None:
        return si

    # ── Slow path: (re)connect under the per-key lock (single-flight) ──
    lk = _connect_lock_for(key)
    if not lk.acquire(blocking=not nonblocking_reconnect):
        # nonblocking_reconnect=True and another probe already holds the
        # reconnect for this vCenter. Don't queue behind it — that's the herd.
        raise _ReconnectInProgress(f"{host}: session reconnect already in progress")
    try:
        # Another thread may have rebuilt the session while we waited on the
        # lock — re-check before logging in again.
        with _sessions_lock:
            entry = _sessions.get(key)
        si = _healthy_cached(entry, time.monotonic())
        if si is not None:
            return si

        # Evict the dead/expired session and close it after a grace delay, so a
        # probe that grabbed it just before expiry finishes its SOAP call first
        # (probes are hard-capped near 60s) instead of being yanked mid-flight.
        with _sessions_lock:
            old = _sessions.pop(key, None)
        if old is not None:
            _disconnect_later(Disconnect, old[0])

        # Create new connection (still under the per-key lock — siblings wait).
        ctx = _make_ssl_ctx(verify_ssl)
        try:
            with _socket_timeout(60):          # cap SmartConnect at 60s
                si = SmartConnect(
                    host=host, user=user, pwd=password,
                    port=int(port), sslContext=ctx
                )
        except Exception as e:
            err = str(e)
            if "incorrect user name or password" in err.lower() or "InvalidLogin" in err:
                raise PermissionError("Authentication failed")
            if "ssl" in err.lower() or "certificate" in err.lower():
                raise ConnectionError("SSL error — try disabling Verify SSL")
            if isinstance(e, socket.timeout) or "timed out" in err.lower():
                raise ConnectionError("Connection timed out (60s) — check vCenter/ESXi host is reachable")
            raise ConnectionError(f"Connection failed: {err}")

        # Warm the heavy server-side caches before the session is published, so
        # the first probe doesn't race a cold perfManager/inventory. Best-effort.
        _warm_session(si)

        with _sessions_lock:
            _sessions[key] = (si, time.monotonic() + _SESSION_TTL)
        return si
    finally:
        lk.release()


def _warm_session(si) -> None:
    """Force the two server-side caches a cold session would otherwise miss on
    the first probe: ServiceContent (RetrieveContent) and the perf-counter
    catalog (→ 'metric not available' on the first QueryPerf). Best-effort and
    time-bounded — never fails the session; a probe racing this still works.

    Deliberately does NOT enumerate the inventory. The probe path resolves each
    VM/host/datastore by direct MoRef — vim.VirtualMachine(vm_id, si._stub) +
    a single-object PropertyCollector (_fetch_single_object_props) — which does
    not depend on a CreateContainerView, so warming the full inventory bought
    it nothing. That CreateContainerView + view.view traversal was the heavy
    per-reconnect cost that, under a synchronized session-expiry herd, piled
    dozens of full-inventory walks onto vCenter at once and overloaded it."""
    try:
        with _socket_timeout(_WARM_TIMEOUT_S):
            content = si.RetrieveContent()          # forces ServiceContent
            _ = content.perfManager.perfCounter     # perf-counter catalog
    except Exception as e:
        log.debug(f"vmware warm-on-connect skipped: {e}")


def _disconnect_quietly(Disconnect, si) -> None:
    try:
        Disconnect(si)
    except Exception:
        pass


def _disconnect_later(Disconnect, si, delay=65) -> None:
    """Disconnect a superseded session after a grace delay so a probe that
    grabbed it just before expiry can finish its in-flight SOAP call first —
    probes are hard-capped near 60s (see vmware_probe). Closing it immediately
    would yank the session mid-call and surface as vim.fault.NotAuthenticated.
    Still bounded — the session is closed ~delay seconds later, so vCenter's
    concurrent-session cap isn't leaked. Daemon thread: dies with the process."""
    def _later():
        time.sleep(delay)
        _disconnect_quietly(Disconnect, si)
    threading.Thread(target=_later, daemon=True, name="pw-vmdisc-close").start()


def _invalidate_session(host, user):
    """Remove a cached session (e.g. after auth failure)."""
    _, Disconnect, _, _ = _require_pyvmomi()
    with _sessions_lock:
        entry = _sessions.pop((host, user), None)
    if entry:
        try:
            Disconnect(entry[0])
        except Exception:
            pass


def prewarm_session(host, user, password, port=443, verify_ssl=False) -> bool:
    """Establish, warm, and cache a vCenter session ahead of the first probe.

    _get_session both logs in (a cold SmartConnect takes 5-20s) and warms the
    server-side caches (_warm_session: perf catalog + inventory) — so the
    first probe cycle after a (server or agent) restart reuses a fully warm
    session instead of false-failing with "VM not found" / "metric not
    available". Called from a background thread at startup; never raises.
    """
    try:
        _get_session(host, user, password, port=port, verify_ssl=verify_ssl)
        return True
    except Exception as e:
        log.debug(f"vmware prewarm {host}:{port} failed: {e}")
        return False


# ---------------------------------------------------------------------------
# PropertyCollector helper — one SOAP call for N objects × M properties
# ---------------------------------------------------------------------------

def _fetch_single_object_props(si, obj_moref, property_paths):
    """Fetch properties for ONE ManagedObject in a single SOAP call.

    Used on the hot probe path where we have a MoRef (VM/Host/Datastore)
    and need several of its properties. Replaces the classic pyVmomi
    anti-pattern of `obj.property_a; obj.property_b; obj.property_c` which
    issues one SOAP round-trip per property access.

    Returns a dict of {path: value}. If the object doesn't exist, or the
    PropertyCollector query fails, returns {}. Individual property-fetch
    errors (e.g. NoPermission on one field) silently drop that key — use
    `.get(path, default)` when reading.
    """
    _, _, vim, vmodl = _require_pyvmomi()
    obj_spec = vmodl.query.PropertyCollector.ObjectSpec(obj=obj_moref, skip=False)
    prop_spec = vmodl.query.PropertyCollector.PropertySpec(
        type=type(obj_moref), pathSet=list(property_paths), all=False
    )
    filter_spec = vmodl.query.PropertyCollector.FilterSpec(
        objectSet=[obj_spec], propSet=[prop_spec]
    )
    try:
        results = si.content.propertyCollector.RetrieveContents([filter_spec]) or []
    except Exception as e:
        # Don't escalate — caller reads {} as "object not found" and returns a
        # clean error. A real connection drop will be caught by the session
        # layer on the next probe.
        log.debug(f"_fetch_single_object_props({obj_moref}) failed: {e}")
        return {}
    if not results:
        return {}
    return {dp.name: dp.val for dp in (results[0].propSet or [])}


def _collect_properties(si, view_obj, obj_type, property_paths):
    """Bulk-fetch properties for every object in `view_obj` in ONE SOAP call.

    pyVmomi's idiomatic `for obj in view.view: obj.property` pattern issues
    one SOAP round-trip **per property access**. For a 100-VM inventory
    that's 500+ round-trips; PRTG-style tools do the same enumeration in
    one call using PropertyCollector. This helper wraps that pattern.

    Returns a list of dicts. Each dict keys:
      - '_moId'  — the managed object reference id (e.g. 'vm-123')
      - '<path>' — the requested property path, value as returned by
                   vCenter (may be a primitive, enum, list, or MoRef)

    Properties that vCenter couldn't return for a specific object are
    simply absent from that object's dict — use `.get(path, default)`.
    """
    _, _, vim, vmodl = _require_pyvmomi()

    traversal = vmodl.query.PropertyCollector.TraversalSpec(
        name='traverseContainer',
        type=vim.view.ContainerView,
        path='view',
        skip=False,
    )
    obj_spec = vmodl.query.PropertyCollector.ObjectSpec(
        obj=view_obj, skip=True, selectSet=[traversal]
    )
    prop_spec = vmodl.query.PropertyCollector.PropertySpec(
        type=obj_type, pathSet=list(property_paths), all=False
    )
    filter_spec = vmodl.query.PropertyCollector.FilterSpec(
        objectSet=[obj_spec], propSet=[prop_spec]
    )

    pc = si.content.propertyCollector
    results = pc.RetrieveContents([filter_spec]) or []

    rows = []
    for oc in results:
        row = {'_moId': oc.obj._moId}
        for dp in (oc.propSet or []):
            row[dp.name] = dp.val
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# VM discovery
# ---------------------------------------------------------------------------

def vmware_discover_vms(host, user, password, port=443, verify_ssl=False):
    """Connect to vCenter/ESXi and return a list of VM dicts.

    Each dict: {vm_id, name, power_state, guest_os, num_cpu, memory_mb, host_name}
    """
    _, _, vim, _ = _require_pyvmomi()

    t0 = time.monotonic()
    # Force-fresh session — a zombie from a previously-hung discover click
    # can linger in the cache with a dead underlying socket. Revive cleanly.
    _invalidate_session(host, user)
    log.info(f"VMware discover VMs: starting ({host})")

    def _do():
        log.debug(f"VMware discover VMs: connecting ({host})")
        si = _get_session(host, user, password, port, verify_ssl)
        log.debug(f"VMware discover VMs: session ok ({host})")
        content = si.RetrieveContent()
        log.debug(f"VMware discover VMs: content retrieved ({host})")

        # Prefetch host MoRef→name map so we can populate host_name without a
        # per-VM round-trip. One SOAP call for all ESXi hosts.
        host_view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.HostSystem], recursive=True
        )
        try:
            host_rows = _collect_properties(si, host_view, vim.HostSystem, ['name'])
        finally:
            host_view.Destroy()
        host_name_by_id = {r['_moId']: (r.get('name') or '') for r in host_rows}
        log.debug(f"VMware discover VMs: host map built "
                  f"({len(host_name_by_id)} hosts) ({host})")

        # Fetch every VM with all the fields we need in ONE SOAP call — replaces
        # the naive `for vm in view.view: vm.config; vm.runtime; …` pattern which
        # would issue 5+ round-trips per VM.
        vm_view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], recursive=True
        )
        try:
            rows = _collect_properties(si, vm_view, vim.VirtualMachine, [
                'name',
                'runtime.powerState',
                'runtime.host',
                'config.guestFullName',
                'config.guestId',
                'config.hardware.numCPU',
                'config.hardware.memoryMB',
            ])
        finally:
            vm_view.Destroy()
        log.debug(f"VMware discover VMs: batch fetched {len(rows)} VMs ({host})")

        out = []
        for r in rows:
            host_ref = r.get('runtime.host')
            host_id  = host_ref._moId if host_ref is not None else ''
            out.append({
                "vm_id":       r['_moId'],
                "name":        r.get('name') or "",
                "power_state": str(r.get('runtime.powerState') or "unknown"),
                "guest_os":    (r.get('config.guestFullName') or r.get('config.guestId') or ""),
                "num_cpu":     int(r.get('config.hardware.numCPU') or 0),
                "memory_mb":   int(r.get('config.hardware.memoryMB') or 0),
                "host_name":   host_name_by_id.get(host_id, ""),
            })
        return out

    try:
        vms = _run_with_timeout("discover-VMs", _do, _DISCOVER_TIMEOUT_S)
    except ConnectionError as e:
        log.warning(f"VMware discover VMs: {e} ({host})")
        _invalidate_session(host, user)   # session may be zombied — don't reuse
        raise
    except (PermissionError,):
        _invalidate_session(host, user)
        raise

    vms.sort(key=lambda v: v["name"].lower())
    log.info(f"VMware discover VMs: found {len(vms)} in {(time.monotonic()-t0)*1000:.0f}ms ({host})")
    return vms


# ---------------------------------------------------------------------------
# ESXi host discovery
# ---------------------------------------------------------------------------

def vmware_discover_hosts(host, user, password, port=443, verify_ssl=False):
    """Connect to vCenter/ESXi and return a list of ESXi host dicts.

    Each dict: {host_id, name, connection_state, cpu_model, cpu_count,
                cpu_cores, memory_mb, num_vms, version}
    """
    _, _, vim, _ = _require_pyvmomi()

    t0 = time.monotonic()
    _invalidate_session(host, user)   # force-fresh — see rationale in vmware_discover_vms
    log.info(f"VMware discover Hosts: starting ({host})")

    def _do():
        log.debug(f"VMware discover Hosts: connecting ({host})")
        si = _get_session(host, user, password, port, verify_ssl)
        log.debug(f"VMware discover Hosts: session ok ({host})")
        content = si.RetrieveContent()
        log.debug(f"VMware discover Hosts: content retrieved ({host})")

        view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.HostSystem], recursive=True
        )
        try:
            rows = _collect_properties(si, view, vim.HostSystem, [
                'name',
                'runtime.connectionState',
                'summary.hardware.cpuModel',
                'summary.hardware.numCpuPkgs',
                'summary.hardware.numCpuCores',
                'summary.hardware.memorySize',
                'summary.config.product.fullName',
                'vm',   # ArrayOfManagedObjectReference — len() gives num_vms
            ])
        finally:
            view.Destroy()
        log.debug(f"VMware discover Hosts: batch fetched {len(rows)} hosts ({host})")

        out = []
        for r in rows:
            mem_bytes = int(r.get('summary.hardware.memorySize') or 0)
            vm_list = r.get('vm') or []
            out.append({
                "host_id":          r['_moId'],
                "name":             r.get('name') or "",
                "connection_state": str(r.get('runtime.connectionState') or "unknown"),
                "cpu_model":        r.get('summary.hardware.cpuModel') or "",
                "cpu_count":        int(r.get('summary.hardware.numCpuPkgs') or 0),
                "cpu_cores":        int(r.get('summary.hardware.numCpuCores') or 0),
                "memory_mb":        int(mem_bytes / (1024 * 1024)) if mem_bytes else 0,
                "num_vms":          len(vm_list),
                "version":          r.get('summary.config.product.fullName') or "",
            })
        return out

    try:
        hosts = _run_with_timeout("discover-Hosts", _do, _DISCOVER_TIMEOUT_S)
    except ConnectionError as e:
        log.warning(f"VMware discover Hosts: {e} ({host})")
        _invalidate_session(host, user)
        raise
    except (PermissionError,):
        _invalidate_session(host, user)
        raise

    hosts.sort(key=lambda x: x["name"].lower())
    log.info(f"VMware discover Hosts: found {len(hosts)} in {(time.monotonic()-t0)*1000:.0f}ms ({host})")
    return hosts


# ---------------------------------------------------------------------------
# Datastore discovery
# ---------------------------------------------------------------------------

def vmware_discover_datastores(host, user, password, port=443, verify_ssl=False):
    """Connect to vCenter/ESXi and return a list of Datastore dicts.

    Each dict: {ds_id, name, type, capacity_bytes, free_bytes,
                capacity_gb, free_gb, free_pct, accessible}
    """
    _, _, vim, _ = _require_pyvmomi()

    t0 = time.monotonic()
    _invalidate_session(host, user)   # force-fresh — see rationale in vmware_discover_vms
    log.info(f"VMware discover Datastores: starting ({host})")

    def _do():
        log.debug(f"VMware discover Datastores: connecting ({host})")
        si = _get_session(host, user, password, port, verify_ssl)
        log.debug(f"VMware discover Datastores: session ok ({host})")
        content = si.RetrieveContent()
        log.debug(f"VMware discover Datastores: content retrieved ({host})")

        view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.Datastore], recursive=True
        )
        try:
            rows = _collect_properties(si, view, vim.Datastore, [
                'summary.name',
                'summary.type',
                'summary.capacity',
                'summary.freeSpace',
                'summary.accessible',
            ])
        finally:
            view.Destroy()
        log.debug(f"VMware discover Datastores: batch fetched {len(rows)} datastores ({host})")

        out = []
        for r in rows:
            capacity = int(r.get('summary.capacity') or 0)
            free     = int(r.get('summary.freeSpace') or 0)
            out.append({
                "ds_id":           r['_moId'],
                "name":            r.get('summary.name') or "",
                "type":            r.get('summary.type') or "",
                "capacity_bytes":  capacity,
                "free_bytes":      free,
                "capacity_gb":     round(capacity / 1024 ** 3, 1),
                "free_gb":         round(free / 1024 ** 3, 1),
                "free_pct":        round((free / capacity) * 100, 1) if capacity else 0.0,
                "accessible":      bool(r.get('summary.accessible')),
            })
        return out

    try:
        datastores = _run_with_timeout("discover-Datastores", _do, _DISCOVER_TIMEOUT_S)
    except ConnectionError as e:
        log.warning(f"VMware discover Datastores: {e} ({host})")
        _invalidate_session(host, user)
        raise
    except (PermissionError,):
        _invalidate_session(host, user)
        raise

    datastores.sort(key=lambda d: d["name"].lower())
    log.info(f"VMware discover Datastores: found {len(datastores)} in {(time.monotonic()-t0)*1000:.0f}ms ({host})")
    return datastores


# ---------------------------------------------------------------------------
# Metric cache — avoids redundant QueryPerf when many sensors target same VM/Host
# ---------------------------------------------------------------------------

_metric_cache = {}          # (host, vm_id) → {"ts": monotonic, "data": {key: value}}
_metric_cache_lock = threading.Lock()
_METRIC_CACHE_TTL = 20      # seconds (matches vSphere realtime interval)
# How long a last-good perf sample may be served while a transient slow window
# or in-flight reconnect clears. Beyond this, a real outage is reported DOWN.
_STALE_SERVE_MAX_S = 180


def _resolve_mdef(metric):
    """Return the metric definition for a metric key (VM / host / datastore)."""
    if metric.startswith("dstore_"):
        return _DSTORE_METRIC_BY_KEY.get(metric)
    if metric.startswith("host_"):
        return _HOST_METRIC_BY_KEY.get(metric)
    return _METRIC_BY_KEY.get(metric)


def _serve_last_good(cache_key, metric, reason):
    """Return an ok=True probe result from the last cached perf sample if it is
    recent enough (< _STALE_SERVE_MAX_S), else None.

    Used on a transient vCenter timeout / in-flight reconnect so a momentary slow
    window doesn't flap every sensor on the vCenter DOWN together. The detail
    surfaces the staleness so it's never silently masked. Datastore metrics are
    not cached, so they correctly fall through to a real failure."""
    with _metric_cache_lock:
        cached = _metric_cache.get(cache_key)
    if not cached:
        return None
    age = time.monotonic() - cached["ts"]
    if age > _STALE_SERVE_MAX_S:
        return None
    val = cached["data"].get(metric)
    if val is None:
        return None
    mdef = _resolve_mdef(metric)
    label = mdef["l"] if mdef else metric
    unit  = mdef["unit"] if mdef else ""
    return {"ok": True, "ms": float(val),
            "detail": f"{label}: {val} {unit} (cached {int(age)}s ago — {reason})",
            "value": str(val)}


def _build_counter_map(perf_manager):
    """Build {counter_name → counterId} from perfManager.perfCounter."""
    cmap = {}
    for c in perf_manager.perfCounter:
        name = f"{c.groupInfo.key}.{c.nameInfo.key}.{c.rollupType}"
        cmap[name] = c.key
    return cmap


def _query_all_metrics(si, entity_moref, metrics, num_cpu=1):
    """Query perf counters from *metrics* list for a managed entity (VM or Host).

    Returns dict {metric_key: numeric_value} with unit conversions applied.
    """
    _, _, vim, _ = _require_pyvmomi()

    perf = si.content.perfManager
    cmap = _build_counter_map(perf)

    # Build metric IDs for the counters we care about
    metric_ids = []
    counter_key_to_metric = {}  # counterId → metric def
    for m in metrics:
        if not m.get("counter"):
            continue  # runtime-only metrics (e.g. power state) have no perf counter
        cid = cmap.get(m["counter"])
        if cid is not None:
            metric_ids.append(
                vim.PerformanceManager.MetricId(counterId=cid, instance="")
            )
            counter_key_to_metric[cid] = m

    if not metric_ids:
        return {}

    query = vim.PerformanceManager.QuerySpec(
        entity=entity_moref,
        metricId=metric_ids,
        intervalId=20,      # realtime (20-second samples)
        maxSample=1,
    )

    try:
        results = perf.QueryPerf(querySpec=[query])
    except Exception:
        return {}

    # Pre-fill 0 for every counter we requested — counters with no samples
    # (e.g. idle VM with no IO) return empty lists in vSphere; 0 is correct.
    data = {m["v"]: 0 for m in counter_key_to_metric.values()}

    for result in results:
        for val in result.value:
            m = counter_key_to_metric.get(val.id.counterId)
            if not m:
                continue
            # vSphere uses -1 as a sentinel for "no data this interval"
            raw = val.value[-1] if val.value else -1
            if raw == -1:
                continue  # keep the 0 default

            # Unit conversions
            if m.get("convert") == "ready_pct":
                # cpu.ready.summation is in milliseconds over the sample interval
                # Convert to percentage: (ms / (interval_ms * num_vcpu)) * 100
                data[m["v"]] = round((raw / (20000 * max(num_cpu, 1))) * 100, 2)
            elif m.get("divisor"):
                data[m["v"]] = round(raw / m["divisor"], 2)
            else:
                data[m["v"]] = raw

    return data


# ---------------------------------------------------------------------------
# Probe function (called by Sensor.probe)
# ---------------------------------------------------------------------------

def vmware_probe(host, user, password, vm_id, metric,
                 port=443, verify_ssl=False, timeout=30, disk_path=""):
    """Probe a single VMware metric for a VM or ESXi host.

    Thin wrapper: runs the real probe under a hard wall-clock cap. The probe
    path makes SOAP calls (RetrieveContent / QueryPerf) on pyVmomi's pooled
    HTTPS sockets, which ignore socket.setdefaulttimeout — so without
    _run_with_timeout a hung-but-reachable vCenter could block the probe far
    past `timeout`, leaking an orphan thread every cycle. The cache TTL means
    most probes are cache hits and never reach here.
    """
    _cap = max(float(timeout or 30) + 5, 60)
    try:
        return _run_with_timeout(
            f"probe-{vm_id}-{metric}",
            lambda: _vmware_probe_impl(host, user, password, vm_id, metric,
                                       port, verify_ssl, timeout, disk_path),
            _cap,
        )
    except _ReconnectInProgress:
        # A sibling probe is rebuilding the shared session right now. Serve this
        # metric's last-good sample this cycle instead of blocking behind the
        # reconnect (which is what synchronised a whole vCenter's sensors into
        # one 60s timeout herd). No stale sample yet (cold) → soft fail.
        stale = _serve_last_good((host, vm_id), metric, "session reconnecting")
        if stale is not None:
            return stale
        return {"ok": False, "ms": None, "detail": "vCenter session reconnecting"}
    except _ProbeTimeout as e:
        # Slow vCenter, NOT a dead session — KEEP the shared (host, user) session
        # so sibling sensors don't all reconnect into a cold catalog. Serve the
        # last-good sample (bounded staleness) so a momentary slow window doesn't
        # flap the sensor DOWN; only a sustained outage (> _STALE_SERVE_MAX_S)
        # falls through to a real DOWN.
        stale = _serve_last_good((host, vm_id), metric, "vCenter slow")
        if stale is not None:
            return stale
        return {"ok": False, "ms": None, "detail": str(e)}
    except ConnectionError as e:
        # Real connection/auth drop mid-probe — invalidate so the next probe
        # reconnects rather than reusing a wedged one.
        _invalidate_session(host, user)
        return {"ok": False, "ms": None, "detail": str(e)}
    except Exception as e:
        return {"ok": False, "ms": None, "detail": f"VMware probe error: {e}"}


def _vmware_probe_impl(host, user, password, vm_id, metric,
                       port=443, verify_ssl=False, timeout=30, disk_path=""):
    """Probe a single VMware metric for a VM or ESXi host.

    Host metrics are identified by the ``host_`` prefix on the metric key.
    The *vm_id* parameter carries either a VM moId (``vm-123``) or a host
    moId (``host-28``).

    Returns {ok, ms, detail, value} matching the PingWatch probe contract.
    """
    is_datastore = metric.startswith("dstore_")
    is_host      = (not is_datastore) and metric.startswith("host_")

    if is_datastore:
        mdef = _DSTORE_METRIC_BY_KEY.get(metric)
    else:
        mdef = (_HOST_METRIC_BY_KEY if is_host else _METRIC_BY_KEY).get(metric)
    if not mdef:
        return {"ok": False, "ms": None,
                "detail": f"Unknown metric: {metric}"}

    t0 = time.time()

    # ── Check metric cache (skip for datastore — cheap property read) ─
    cache_key = (host, vm_id)
    now_mono = time.monotonic()

    if not is_datastore:
        with _metric_cache_lock:
            cached = _metric_cache.get(cache_key)
            if cached and (now_mono - cached["ts"]) < _METRIC_CACHE_TTL:
                val = cached["data"].get(metric)
                if val is not None:
                    return {"ok": True, "ms": float(val),
                            "detail": f"{mdef['l']}: {val} {mdef['unit']}",
                            "value": str(val)}

    # ── Cache miss — query vCenter ────────────────────────────────────
    _, _, vim, _ = _require_pyvmomi()

    # nonblocking_reconnect=True: if another probe is already rebuilding this
    # vCenter's session, raise _ReconnectInProgress (handled in vmware_probe →
    # serve last-good) rather than queueing behind it and timing out as a herd.
    try:
        si = _get_session(host, user, password, port, verify_ssl,
                          nonblocking_reconnect=True)
    except PermissionError as e:
        _invalidate_session(host, user)
        return {"ok": False, "ms": None, "detail": str(e)}
    except ConnectionError as e:
        _invalidate_session(host, user)
        return {"ok": False, "ms": None, "detail": str(e)}

    content = si.RetrieveContent()

    # ══════════════════════════════════════════════════════════════════
    # Datastore metric branch
    # ══════════════════════════════════════════════════════════════════
    if is_datastore:
        # Build a client-side proxy to the datastore by its MoID — no SOAP
        # round-trip, no view enumeration. Replaces the previous pattern of
        # listing EVERY datastore in vCenter and scanning for a matching moId.
        try:
            ds_moref = vim.Datastore(vm_id, si._stub)
        except Exception as e:
            log.debug(f"vmware_probe: invalid datastore ID {vm_id}: {e}")
            return {"ok": False, "ms": None, "detail": f"Invalid datastore ID: {vm_id}"}

        props = _fetch_single_object_props(si, ds_moref, [
            'summary.name',
            'summary.capacity',
            'summary.freeSpace',
            'summary.accessible',
        ])
        if not props:
            return {"ok": False, "ms": None,
                    "detail": f"Datastore {vm_id} not found"}

        ds_name    = props.get('summary.name') or "datastore"
        cap_bytes  = int(props.get('summary.capacity') or 0)
        free_bytes = int(props.get('summary.freeSpace') or 0)
        accessible = bool(props.get('summary.accessible'))

        if not accessible:
            return {"ok": False, "ms": None,
                    "detail": f"{ds_name}: datastore not accessible (maintenance / unmounted)"}
        if cap_bytes <= 0:
            return {"ok": False, "ms": None,
                    "detail": f"{ds_name}: capacity reported as 0"}

        free_gb  = round(free_bytes / 1024 ** 3, 1)
        free_pct = round(free_bytes / cap_bytes * 100, 1)
        detail = f"{ds_name}: {_fmt_bytes(free_bytes)} free ({free_pct}% of {_fmt_bytes(cap_bytes)})"
        return {"ok": True, "ms": free_gb,
                "detail": detail, "value": str(free_gb)}

    # ══════════════════════════════════════════════════════════════════
    # ESXi host metric branch
    # ══════════════════════════════════════════════════════════════════
    if is_host:
        # Direct MoRef — skip the view enumeration that used to scan every
        # HostSystem in vCenter on each probe.
        try:
            host_moref = vim.HostSystem(vm_id, si._stub)
        except Exception as e:
            log.debug(f"vmware_probe: invalid host ID {vm_id}: {e}")
            return {"ok": False, "ms": None, "detail": f"Invalid host ID: {vm_id}"}

        # All HOST_METRICS keys are prefixed `host_` and backed by a perf
        # counter. We need the connection state as a guard and num_pcpu for
        # the ready_pct math — fetched in ONE SOAP call.
        # (The "on" power-state metric for hosts flows through the VM branch
        # below and falls back to HostSystem there — see T9 in the test.)
        props = _fetch_single_object_props(si, host_moref, [
            'runtime.connectionState',
            'summary.hardware.numCpuPkgs',
        ])
        if not props:
            return {"ok": False, "ms": None, "detail": f"Host {vm_id} not found"}

        conn_state = str(props.get('runtime.connectionState') or "unknown")
        if conn_state != "connected":
            return {"ok": False, "ms": None, "detail": f"Host {conn_state}"}

        num_pcpu = int(props.get('summary.hardware.numCpuPkgs') or 1)

        data = _query_all_metrics(si, host_moref, HOST_METRICS, num_pcpu)

        with _metric_cache_lock:
            _metric_cache[cache_key] = {"ts": time.monotonic(), "data": data}

        val = data.get(metric)
        if val is None:
            return {"ok": False, "ms": None,
                    "detail": f"Metric {mdef['l']} not available for this host"}

        return {"ok": True, "ms": float(val),
                "detail": f"{mdef['l']}: {val} {mdef['unit']}",
                "value": str(val)}

    # ══════════════════════════════════════════════════════════════════
    # VM metric branch
    # ══════════════════════════════════════════════════════════════════

    # Build a client-side VM proxy — no SOAP round-trip, no view enumeration.
    # Replaces the previous pattern which listed EVERY VM in vCenter and
    # scanned for the matching moId on every single probe cycle (~5+ SOAP
    # calls per probe × 105 sensors every 60s).
    try:
        vm_moref = vim.VirtualMachine(vm_id, si._stub)
    except Exception as e:
        log.debug(f"vmware_probe: invalid VM ID {vm_id}: {e}")
        return {"ok": False, "ms": None, "detail": f"Invalid VM ID: {vm_id}"}

    # Pick the property set we need:
    #   - always: runtime.powerState (guard + "on" metric value)
    #   - perf-counter metrics: + config.hardware.numCPU (ready_pct math)
    #                           + config.hardware.memoryMB (future memory-% use)
    #   - disk_used_pct:         + guest.disk (list of GuestDiskInfo)
    needed = ['runtime.powerState']
    if metric != "on":
        needed.extend(['config.hardware.numCPU', 'config.hardware.memoryMB'])
        if metric == "disk_used_pct":
            needed.append('guest.disk')

    props = _fetch_single_object_props(si, vm_moref, needed)

    # "on" is shared between VM and Host sensor types — if the VM lookup
    # came up empty, fall back to a HostSystem proxy before giving up.
    if not props and metric == "on":
        try:
            host_moref = vim.HostSystem(vm_id, si._stub)
        except Exception:
            return {"ok": False, "ms": None, "detail": f"Object {vm_id} not found"}
        host_props = _fetch_single_object_props(si, host_moref, ['runtime.powerState'])
        if not host_props:
            return {"ok": False, "ms": None, "detail": f"VM or Host {vm_id} not found"}
        power = str(host_props.get('runtime.powerState') or "unknown")
        is_on = (power == "poweredOn")
        return {"ok": is_on, "ms": 1.0 if is_on else 0.0,
                "detail": f"Power State: {power}",
                "value": power}

    if not props:
        return {"ok": False, "ms": None, "detail": f"VM {vm_id} not found"}

    power_state = str(props.get('runtime.powerState') or "unknown")

    # "on" metric: just report power state, no perf counters needed
    if metric == "on":
        is_on = (power_state == "poweredOn")
        return {"ok": is_on, "ms": 1.0 if is_on else 0.0,
                "detail": f"Power State: {power_state}",
                "value": power_state}

    if power_state != "poweredOn":
        return {"ok": False, "ms": None, "detail": "VM powered off"}

    num_cpu   = int(props.get('config.hardware.numCPU') or 1)
    # memory_mb retained for future metrics that need total VM memory
    # (e.g. mem_consumed_pct would divide mem.consumed.average / memory_mb)
    _memory_mb = int(props.get('config.hardware.memoryMB') or 0)

    # ── Disk used % — uses guest.disk fetched above (requires VMware Tools) ─
    if metric == "disk_used_pct":
        disks = props.get('guest.disk') or []
        if not disks:
            return {"ok": False, "ms": None,
                    "detail": "No disk info — VMware Tools must be installed and running"}
        if disk_path:
            matched = next(
                (d for d in disks if d.diskPath.rstrip("/\\").lower() ==
                 disk_path.rstrip("/\\").lower()), None)
            if not matched:
                paths = ", ".join(d.diskPath for d in disks)
                return {"ok": False, "ms": None,
                        "detail": f"Path '{disk_path}' not found. Available: {paths}"}
            target = matched
        else:
            target = max(
                disks,
                key=lambda d: (d.capacity - d.freeSpace) / d.capacity if d.capacity else 0)
        if not target.capacity:
            return {"ok": False, "ms": None, "detail": "Disk capacity reported as 0"}
        pct = round((target.capacity - target.freeSpace) / target.capacity * 100, 1)
        return {"ok": True, "ms": pct,
                "detail": f"Disk {target.diskPath}: {pct}% used",
                "value": str(pct)}

    # Query all perf-counter metrics (cached for other sensors targeting same VM)
    data = _query_all_metrics(si, vm_moref, VM_METRICS, num_cpu)

    with _metric_cache_lock:
        _metric_cache[cache_key] = {"ts": time.monotonic(), "data": data}

    val = data.get(metric)
    if val is None:
        return {"ok": False, "ms": None,
                "detail": f"Metric {mdef['l']} not available for this VM"}

    return {"ok": True, "ms": float(val),
            "detail": f"{mdef['l']}: {val} {mdef['unit']}",
            "value": str(val)}
