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
    import socket
    _prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(60)          # cap SmartConnect at 60s
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
        if isinstance(e, socket.timeout) or "timed out" in err.lower():
            raise ConnectionError("Connection timed out (60s) — check vCenter/ESXi host is reachable")
        raise ConnectionError(f"Connection failed: {err}")
    finally:
        socket.setdefaulttimeout(_prev_timeout)

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
# ESXi host discovery
# ---------------------------------------------------------------------------

def vmware_discover_hosts(host, user, password, port=443, verify_ssl=False):
    """Connect to vCenter/ESXi and return a list of ESXi host dicts.

    Each dict: {host_id, name, connection_state, cpu_model, cpu_count,
                cpu_cores, memory_mb, num_vms, version}
    """
    _, _, vim, _ = _require_pyvmomi()

    try:
        si = _get_session(host, user, password, port, verify_ssl)
    except (PermissionError, ConnectionError):
        _invalidate_session(host, user)
        raise

    content = si.RetrieveContent()
    view = content.viewManager.CreateContainerView(
        content.rootFolder, [vim.HostSystem], recursive=True
    )

    hosts = []
    try:
        for h in view.view:
            try:
                rt  = h.runtime
                hw  = h.summary.hardware if h.summary else None
                cfg = h.summary.config   if h.summary else None
                hosts.append({
                    "host_id":          h._moId,
                    "name":             h.name or "",
                    "connection_state": str(rt.connectionState) if rt else "unknown",
                    "cpu_model":        hw.cpuModel if hw else "",
                    "cpu_count":        hw.numCpuPkgs if hw else 0,
                    "cpu_cores":        hw.numCpuCores if hw else 0,
                    "memory_mb":        int(hw.memorySize / (1024 * 1024)) if hw and hw.memorySize else 0,
                    "num_vms":          len(h.vm) if h.vm else 0,
                    "version":          (cfg.product.fullName if cfg and cfg.product else ""),
                })
            except Exception:
                continue
    finally:
        view.Destroy()

    hosts.sort(key=lambda x: x["name"].lower())
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

    try:
        si = _get_session(host, user, password, port, verify_ssl)
    except (PermissionError, ConnectionError):
        _invalidate_session(host, user)
        raise

    content = si.RetrieveContent()
    view = content.viewManager.CreateContainerView(
        content.rootFolder, [vim.Datastore], recursive=True
    )

    datastores = []
    try:
        for ds in view.view:
            try:
                summary = ds.summary
                capacity = int(summary.capacity or 0)
                free     = int(summary.freeSpace or 0)
                datastores.append({
                    "ds_id":           ds._moId,
                    "name":            summary.name or ds.name or "",
                    "type":            summary.type or "",
                    "capacity_bytes":  capacity,
                    "free_bytes":      free,
                    "capacity_gb":     round(capacity / 1024 ** 3, 1),
                    "free_gb":         round(free / 1024 ** 3, 1),
                    "free_pct":        round((free / capacity) * 100, 1) if capacity else 0.0,
                    "accessible":      bool(summary.accessible),
                })
            except Exception:
                continue  # skip datastores we can't read
    finally:
        view.Destroy()

    datastores.sort(key=lambda d: d["name"].lower())
    return datastores


# ---------------------------------------------------------------------------
# Metric cache — avoids redundant QueryPerf when many sensors target same VM/Host
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

    try:
        si = _get_session(host, user, password, port, verify_ssl)
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
        ds_moref = None
        try:
            view = content.viewManager.CreateContainerView(
                content.rootFolder, [vim.Datastore], recursive=True
            )
            for ds in view.view:
                if ds._moId == vm_id:
                    ds_moref = ds
                    break
            view.Destroy()
        except Exception:
            pass

        if ds_moref is None:
            return {"ok": False, "ms": None,
                    "detail": f"Datastore {vm_id} not found"}

        try:
            summary   = ds_moref.summary
            ds_name   = summary.name or ds_moref.name or "datastore"
            cap_bytes = int(summary.capacity or 0)
            free_bytes = int(summary.freeSpace or 0)
            accessible = bool(summary.accessible)
        except Exception:
            return {"ok": False, "ms": None,
                    "detail": "Could not read datastore summary"}

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
        host_moref = None
        num_pcpu = 1
        try:
            view = content.viewManager.CreateContainerView(
                content.rootFolder, [vim.HostSystem], recursive=True
            )
            for h in view.view:
                if h._moId == vm_id:
                    host_moref = h
                    hw = h.summary.hardware if h.summary else None
                    if hw:
                        num_pcpu = hw.numCpuPkgs or 1
                    break
            view.Destroy()
        except Exception:
            pass

        if host_moref is None:
            return {"ok": False, "ms": None,
                    "detail": f"Host {vm_id} not found"}

        # Power state metric — return before connection guard
        if metric == "on":
            try:
                power = str(host_moref.runtime.powerState) if host_moref.runtime else "unknown"
            except Exception:
                power = "unknown"
            is_on = (power == "poweredOn")
            return {"ok": is_on, "ms": 1.0 if is_on else 0.0,
                    "detail": f"Power State: {power}",
                    "value": power}

        # Connection state guard
        try:
            conn_state = str(host_moref.runtime.connectionState) if host_moref.runtime else "unknown"
        except Exception:
            conn_state = "unknown"

        if conn_state != "connected":
            return {"ok": False, "ms": None,
                    "detail": f"Host {conn_state}"}

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
    # VM metric branch (existing logic)
    # ══════════════════════════════════════════════════════════════════
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
        # "on" (Power State) is valid for hosts too — try HostSystem fallback
        if metric == "on":
            host_moref = None
            try:
                view = content.viewManager.CreateContainerView(
                    content.rootFolder, [vim.HostSystem], recursive=True
                )
                for h in view.view:
                    if h._moId == vm_id:
                        host_moref = h
                        break
                view.Destroy()
            except Exception:
                pass
            if host_moref is not None:
                try:
                    power = str(host_moref.runtime.powerState) if host_moref.runtime else "unknown"
                except Exception:
                    power = "unknown"
                is_on = (power == "poweredOn")
                return {"ok": is_on, "ms": 1.0 if is_on else 0.0,
                        "detail": f"Power State: {power}",
                        "value": power}
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
