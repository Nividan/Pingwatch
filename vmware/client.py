"""
vmware/client.py — VMware vCenter/ESXi connection, VM discovery, and metric probing.

Uses pyvmomi (optional dependency).  Session and metric results are cached to
minimise API calls when many sensors target the same vCenter / VM.
"""

import ssl
import time
import threading

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
    {"v": "mem_consumed_pct", "l": "Memory Consumed (Percent)", "group": "mem",       "counter": None,                                      "unit": "%"},
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

# Quick lookup: metric key → definition
_METRIC_BY_KEY = {m["v"]: m for m in VM_METRICS}

# pyvmomi counter name → metric key
_COUNTER_TO_KEY = {m["counter"]: m["v"] for m in VM_METRICS}


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
    return ctx


def _get_session(host, user, password, port=443, verify_ssl=False):
    """Return a cached or fresh ServiceInstance."""
    SmartConnect, Disconnect, vim, vmodl = _require_pyvmomi()

    key = (host, user)
    now = time.monotonic()

    with _sessions_lock:
        if key in _sessions:
            si, expiry = _sessions[key]
            if now < expiry:
                # Quick health check
                try:
                    si.CurrentTime()
                    return si
                except Exception:
                    # Session stale — reconnect below
                    try:
                        Disconnect(si)
                    except Exception:
                        pass
                    del _sessions[key]

    # Create new connection (outside lock — may block on network)
    ctx = _make_ssl_ctx(verify_ssl)
    try:
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
        raise ConnectionError(f"Connection failed: {err}")

    with _sessions_lock:
        _sessions[key] = (si, now + _SESSION_TTL)
    return si


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


# ---------------------------------------------------------------------------
# VM discovery
# ---------------------------------------------------------------------------

def vmware_discover_vms(host, user, password, port=443, verify_ssl=False):
    """Connect to vCenter/ESXi and return a list of VM dicts.

    Each dict: {vm_id, name, power_state, guest_os, num_cpu, memory_mb, host_name}
    """
    _, _, vim, _ = _require_pyvmomi()

    try:
        si = _get_session(host, user, password, port, verify_ssl)
    except (PermissionError, ConnectionError):
        _invalidate_session(host, user)
        raise

    content = si.RetrieveContent()
    container = content.rootFolder
    view = content.viewManager.CreateContainerView(
        container, [vim.VirtualMachine], recursive=True
    )

    vms = []
    try:
        for vm in view.view:
            try:
                cfg = vm.config
                rt  = vm.runtime
                vms.append({
                    "vm_id":       vm._moId,
                    "name":        vm.name or "",
                    "power_state": str(rt.powerState) if rt else "unknown",
                    "guest_os":    (cfg.guestFullName or cfg.guestId or "") if cfg else "",
                    "num_cpu":     cfg.hardware.numCPU if cfg and cfg.hardware else 0,
                    "memory_mb":   cfg.hardware.memoryMB if cfg and cfg.hardware else 0,
                    "host_name":   (rt.host.name if rt and rt.host else ""),
                })
            except Exception:
                continue  # skip VMs we can't read
    finally:
        view.Destroy()

    vms.sort(key=lambda v: v["name"].lower())
    return vms


# ---------------------------------------------------------------------------
# Metric cache — avoids redundant QueryPerf when many sensors target same VM
# ---------------------------------------------------------------------------

_metric_cache = {}          # (host, vm_id) → {"ts": monotonic, "data": {key: value}}
_metric_cache_lock = threading.Lock()
_METRIC_CACHE_TTL = 20      # seconds (matches vSphere realtime interval)


def _build_counter_map(perf_manager):
    """Build {counter_name → counterId} from perfManager.perfCounter."""
    cmap = {}
    for c in perf_manager.perfCounter:
        name = f"{c.groupInfo.key}.{c.nameInfo.key}.{c.rollupType}"
        cmap[name] = c.key
    return cmap


def _query_all_vm_metrics(si, vm_moref, num_cpu=1):
    """Query all VM_METRICS for a single VM in one QueryPerf call.

    Returns dict {metric_key: numeric_value} with unit conversions applied.
    """
    _, _, vim, _ = _require_pyvmomi()

    perf = si.content.perfManager
    cmap = _build_counter_map(perf)

    # Build metric IDs for the counters we care about
    metric_ids = []
    counter_key_to_metric = {}  # counterId → metric def
    for m in VM_METRICS:
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
        entity=vm_moref,
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
    """Probe a single VMware metric for a specific VM.

    Returns {ok, ms, detail, value} matching the PingWatch probe contract.
    """
    mdef = _METRIC_BY_KEY.get(metric)
    if not mdef:
        return {"ok": False, "ms": None,
                "detail": f"Unknown metric: {metric}"}

    t0 = time.time()

    # ── Check metric cache ────────────────────────────────────────────
    cache_key = (host, vm_id)
    now_mono = time.monotonic()

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

    try:
        si = _get_session(host, user, password, port, verify_ssl)
    except PermissionError as e:
        _invalidate_session(host, user)
        return {"ok": False, "ms": None, "detail": str(e)}
    except ConnectionError as e:
        _invalidate_session(host, user)
        return {"ok": False, "ms": None, "detail": str(e)}

    # Find VM by moId
    content = si.RetrieveContent()
    vm_moref = None
    num_cpu = 1
    memory_mb = 0
    try:
        view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], recursive=True
        )
        for vm in view.view:
            if vm._moId == vm_id:
                vm_moref = vm
                if vm.config and vm.config.hardware:
                    num_cpu = vm.config.hardware.numCPU or 1
                    memory_mb = vm.config.hardware.memoryMB or 0
                break
        view.Destroy()
    except Exception:
        pass

    if vm_moref is None:
        return {"ok": False, "ms": None,
                "detail": f"VM {vm_id} not found"}

    # Power state — used both as a guard and as the "on" metric value
    try:
        power_state = str(vm_moref.runtime.powerState) if vm_moref.runtime else "unknown"
    except Exception:
        power_state = "unknown"

    # "on" metric: just report power state, no perf counters needed
    if metric == "on":
        is_on = (power_state == "poweredOn")
        return {"ok": is_on, "ms": 1.0 if is_on else 0.0,
                "detail": f"Power State: {power_state}",
                "value": power_state}

    if power_state != "poweredOn":
        return {"ok": False, "ms": None, "detail": "VM powered off"}

    # ── Disk used % — reads guest.disk (requires VMware Tools) ───────────
    if metric == "disk_used_pct":
        try:
            disks = (vm_moref.guest.disk or []) if vm_moref.guest else []
        except Exception:
            disks = []
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

    # Query all metrics (cached for other sensors targeting same VM)
    data = _query_all_vm_metrics(si, vm_moref, num_cpu)

    # Compute mem_consumed_pct — use hostMemoryUsage from quickStats, which
    # reflects actual host-side memory granted to the VM and closely matches
    # what the guest OS reports (e.g. Windows Task Manager "In Use").
    # guestMemoryUsage (VMware Tools) often under-reports because it tracks
    # only "active" pages, not committed/standby memory.
    if memory_mb > 0:
        host_mem_mb = 0
        try:
            qs = vm_moref.summary.quickStats
            host_mem_mb = int(qs.hostMemoryUsage or 0)
        except Exception:
            pass
        if host_mem_mb > 0:
            data['mem_consumed_pct'] = round(host_mem_mb / memory_mb * 100, 2)
        elif data.get('mem_consumed'):
            # Fallback: mem.consumed.average (perf counter) / configured RAM
            data['mem_consumed_pct'] = round(data['mem_consumed'] / memory_mb * 100, 2)

    with _metric_cache_lock:
        _metric_cache[cache_key] = {"ts": time.monotonic(), "data": data}

    val = data.get(metric)
    if val is None:
        return {"ok": False, "ms": None,
                "detail": f"Metric {mdef['l']} not available for this VM"}

    return {"ok": True, "ms": float(val),
            "detail": f"{mdef['l']}: {val} {mdef['unit']}",
            "value": str(val)}
