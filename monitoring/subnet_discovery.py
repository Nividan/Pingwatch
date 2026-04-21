"""subnet_discovery.py — Scan a CIDR for unmonitored hosts.

Two-phase scan:
  Phase 1 — parallel ICMP ping (dedicated executor, isolated from sensor pool)
  Phase 2 — per-alive enrichment: reverse DNS, ARP MAC lookup, optional port scan
  Phase 3 — multi-NIC duplicate detection via hostname fingerprinting

Designed to be callable both from the HTTP route handler and from a future
scheduled-discovery feature without HTTP context.
"""
import concurrent.futures
import ipaddress
import re
import socket
import subprocess
import threading
import time
import uuid

from core.config import SYS
from core.app_state import STATE
from core.logger import log
from monitoring.probes import probe_ping, probe_tcp, probe_http, probe_banner

# ── Dedicated executor — isolated from STATE._executor ──────────────
# 64 workers so /16 (65534 hosts) finishes ping phase in ~30 min worst case.
_SCAN_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=64, thread_name_prefix="pw-discover"
)

# ── In-memory scan registry: scan_id → state dict ───────────────────
_SCANS: dict = {}
_SCANS_LOCK = threading.Lock()
_SCAN_TTL_S = 3600  # 1 hour

# /16 = 65536 addresses → 65534 usable hosts. Larger is rejected.
_MAX_ADDRS = 65536


# ── Tiny built-in OUI → vendor map (~60 common networking vendors) ──
_OUI_VENDORS = {
    "00:00:0c": "Cisco",       "00:01:42": "Cisco",       "00:01:43": "Cisco",
    "00:1b:21": "Intel",       "00:1c:c0": "Intel",       "00:15:17": "Intel",
    "00:50:56": "VMware",      "00:0c:29": "VMware",      "00:05:69": "VMware",
    "00:1c:14": "VMware",      "00:15:5d": "Microsoft",   "00:03:ff": "Microsoft",
    "52:54:00": "QEMU/KVM",    "08:00:27": "VirtualBox",  "00:1c:42": "Parallels",
    "00:0d:3a": "Microsoft",   "00:50:f2": "Microsoft",
    "b8:27:eb": "Raspberry Pi","dc:a6:32": "Raspberry Pi","e4:5f:01": "Raspberry Pi",
    "00:a0:c9": "Intel",       "ac:de:48": "Apple",       "00:1f:5b": "Apple",
    "00:1e:c2": "Apple",       "00:23:32": "Apple",       "f4:5c:89": "Apple",
    "00:18:71": "HP",          "00:1f:29": "HP",          "00:25:b3": "HP",
    "3c:d9:2b": "HPE",         "ec:b1:d7": "HPE",
    "00:0a:b8": "Cisco",       "00:0e:38": "Cisco",       "00:0f:23": "Cisco",
    "00:11:bb": "Cisco",       "00:13:1a": "Cisco",       "00:14:1b": "Cisco",
    "00:14:6a": "Cisco",       "00:1a:6c": "Cisco",       "00:1d:e6": "Cisco",
    "00:30:96": "Cisco",       "f0:9e:63": "Cisco",
    "00:90:0b": "Lanner",      "00:90:fb": "Portwell",
    "00:1f:12": "Juniper",     "00:23:9c": "Juniper",     "2c:6b:f5": "Juniper",
    "00:24:dc": "Juniper",     "84:c1:c1": "Juniper",
    "00:13:c4": "Aruba",       "20:4c:03": "Aruba",       "ac:a3:1e": "Aruba",
    "00:14:bf": "Linksys",     "00:1c:10": "Linksys",     "00:18:39": "Linksys",
    "00:1d:7e": "Linksys",
    "00:09:5b": "Netgear",     "00:0f:b5": "Netgear",     "00:1b:2f": "Netgear",
    "00:1e:2a": "Netgear",     "00:24:b2": "Netgear",     "44:94:fc": "Netgear",
    "00:14:78": "TP-Link",     "00:1d:0f": "TP-Link",     "10:fe:ed": "TP-Link",
    "14:cc:20": "TP-Link",     "50:c7:bf": "TP-Link",     "f4:f2:6d": "TP-Link",
    "00:0c:f6": "Sitecom",     "00:1c:f0": "D-Link",      "00:21:91": "D-Link",
    "00:22:b0": "D-Link",      "00:24:01": "D-Link",      "00:26:5a": "D-Link",
    "78:54:2e": "D-Link",      "f0:7d:68": "D-Link",
    "00:09:0f": "Fortinet",    "00:13:72": "Dell",        "00:14:22": "Dell",
    "00:18:8b": "Dell",        "00:19:b9": "Dell",        "00:21:9b": "Dell",
    "18:03:73": "Dell",        "84:8f:69": "Dell",        "ec:f4:bb": "Dell",
    "00:0b:6b": "Wistron",     "00:1d:60": "ASUSTeK",     "00:24:8c": "ASUSTeK",
    "1c:87:2c": "ASUSTeK",     "30:85:a9": "ASUSTeK",
    "00:0c:42": "Routerboard", "4c:5e:0c": "MikroTik",    "6c:3b:6b": "MikroTik",
    "b8:69:f4": "MikroTik",    "cc:2d:e0": "MikroTik",    "e4:8d:8c": "MikroTik",
    "24:5a:4c": "Ubiquiti",    "44:d9:e7": "Ubiquiti",    "78:8a:20": "Ubiquiti",
    "80:2a:a8": "Ubiquiti",    "fc:ec:da": "Ubiquiti",    "dc:9f:db": "Ubiquiti",
    "00:1b:17": "Palo Alto",   "b4:0c:25": "Palo Alto",
    "00:13:8f": "Asus",        "00:1e:8c": "Asustek",
    "00:50:43": "Marvell",     "00:25:90": "Supermicro",  "0c:c4:7a": "Supermicro",
    "ac:1f:6b": "Supermicro",  "3c:ec:ef": "Supermicro",
    "02:42:ac": "Docker",      "00:16:3e": "Xen",
}


# ── CIDR validation ────────────────────────────────────────────────
def _validate_cidr(cidr: str):
    """Parse & validate a CIDR string. Returns (network_or_None, error_msg)."""
    try:
        net = ipaddress.ip_network(cidr.strip(), strict=False)
    except (ValueError, TypeError) as e:
        return None, "Invalid CIDR format"
    if not isinstance(net, ipaddress.IPv4Network):
        return None, "Only IPv4 supported"
    if net.num_addresses > _MAX_ADDRS:
        return None, "Subnet too large (max /16 = 65534 hosts)"
    return net, ""


# ── Existing-device IP set (for skip-monitored option) ─────────────
def _monitored_ip_set() -> set:
    """Build a set of IPs that are already monitored.

    Includes primary hosts and all secondary IPs.
    Resolves any non-IP host with a single 1s lookup attempt.
    """
    ips = set()
    with STATE._lock:
        hosts = [d.host for d in STATE.devices.values() if getattr(d, "host", "")]
        for d in STATE.devices.values():
            for sip in getattr(d, "secondary_ips", []) or []:
                if sip:
                    hosts.append(sip)
    for h in hosts:
        try:
            ipaddress.ip_address(h)
            ips.add(h)
            continue
        except ValueError:
            pass
        try:
            ips.add(socket.gethostbyname(h))
        except Exception:
            pass
    return ips


# ── Hostname fingerprinting (multi-NIC duplicate detection) ────────
_NIC_SUFFIX_RE = re.compile(
    r"[-_](mgmt|mgt|admin|data|iscsi|backup|drac|ilo|ipmi|bmc|wan|lan|"
    r"int|ext|pub|priv|vlan\d+|nic\d+|eth\d+|en\d+|p\d+|\d+)$",
    re.IGNORECASE,
)


def _hostname_fingerprint(hostname: str) -> str:
    """Normalize a hostname for multi-NIC duplicate detection.

    Strips trailing domain (.local, .lan, etc.), lowercases, and removes
    common NIC suffixes (-mgmt, -data, -iscsi, ...). Returns '' for empty
    or IP-like input.
    """
    if not hostname:
        return ""
    s = str(hostname).strip().lower()
    if not s:
        return ""
    # Bare IP — no fingerprint
    try:
        ipaddress.ip_address(s)
        return ""
    except ValueError:
        pass
    s = s.split(".")[0]  # drop domain
    while True:
        new = _NIC_SUFFIX_RE.sub("", s)
        if new == s or not new:
            break
        s = new
    return s


def _monitored_hostname_map() -> dict:
    """Build {fingerprint: [(device_name, host), ...]} from existing devices."""
    out: dict = {}
    with STATE._lock:
        devs = [(d.name, d.host) for d in STATE.devices.values() if getattr(d, "host", "")]
    for name, host in devs:
        candidates = [name]
        try:
            ipaddress.ip_address(host)
            try:
                candidates.append(socket.gethostbyaddr(host)[0])
            except Exception:
                pass
        except ValueError:
            candidates.append(host)
        for c in candidates:
            fp = _hostname_fingerprint(c)
            if fp:
                out.setdefault(fp, []).append((name, host))
    return out


def _flag_duplicates(results: list, existing_map: dict) -> None:
    """Mark each result row whose hostname fingerprint matches an existing
    device or another scan result. Mutates results in place."""
    by_fp: dict = {}
    for row in results:
        fp = _hostname_fingerprint(row.get("hostname", ""))
        row["fingerprint"] = fp
        if fp:
            by_fp.setdefault(fp, []).append(row)

    for row in results:
        fp = row.get("fingerprint", "")
        if not fp:
            row["possible_duplicate_of"] = None
            continue
        if fp in existing_map:
            name, host = existing_map[fp][0]
            row["possible_duplicate_of"] = {
                "kind": "existing_device", "name": name, "host": host,
            }
            continue
        peers = [r for r in by_fp.get(fp, []) if r is not row]
        if peers:
            row["possible_duplicate_of"] = {
                "kind": "scan_peer",
                "name": peers[0].get("hostname", "") or peers[0]["ip"],
                "host": peers[0]["ip"],
            }
        else:
            row["possible_duplicate_of"] = None


# ── ARP / MAC lookup ───────────────────────────────────────────────
_MAC_RE = re.compile(r"([0-9a-f]{2}[:-]){5}[0-9a-f]{2}", re.IGNORECASE)


def _get_mac(ip: str) -> str:
    """Cross-platform ARP table lookup for an IP. Returns '' if not found."""
    try:
        cmd = ["arp", "-a", ip] if SYS == "Windows" else ["arp", "-n", ip]
        kw = {}
        if SYS == "Windows":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=2, **kw)
        m = _MAC_RE.search(r.stdout or "")
        if m:
            return m.group(0).lower().replace("-", ":")
    except Exception:
        pass
    return ""


def _vendor_for_mac(mac: str) -> str:
    if not mac or len(mac) < 8:
        return ""
    return _OUI_VENDORS.get(mac[:8].lower(), "")


# ── Device-type guess from open ports ──────────────────────────────
def _guess_device_type(ports: list) -> str:
    p = {item.get("port") for item in ports if item.get("port")}
    if 22 in p and (80 in p or 443 in p) and 3389 not in p:
        return "Linux server"
    if 3389 in p:
        return "Windows host"
    if 161 in p and 22 not in p and 3389 not in p:
        return "Network device"
    if 25 in p and (143 in p or 993 in p):
        return "Mail server"
    if 53 in p and 22 not in p:
        return "DNS server"
    if {3306, 5432, 27017} & p:
        return "Database"
    if 80 in p or 443 in p or 8080 in p or 8443 in p:
        return "Web / Appliance"
    if 22 in p:
        return "SSH host"
    return "Unknown"


# ── Sensor auto-suggestion ─────────────────────────────────────────
def _suggest_sensors(ip: str, hostname: str, ports: list) -> list:
    """Build list of suggested sensor specs for a discovered host."""
    out = [{"stype": "ping", "name": "Ping", "port": None, "enabled": True}]
    seen_keys = set()  # (stype, port) — allows TLS + HTTP on the same port
    for svc in ports:
        st = svc.get("stype", "")
        port = svc.get("port")
        if not port:
            continue
        if (st, port) in seen_keys:
            continue
        seen_keys.add((st, port))
        if st == "http":
            out.append({"stype": "http", "name": f"HTTP {port}", "port": port,
                        "url": f"http://{ip}:{port}", "enabled": False})
        elif st == "tls":
            out.append({"stype": "http", "name": f"HTTPS {port}", "port": port,
                        "url": f"https://{ip}:{port}", "verify_ssl": False,
                        "enabled": False})
        elif port == 161:
            out.append({"stype": "snmp", "name": "SNMP sysUpTime", "port": 161,
                        "enabled": False})
        elif st in ("tcp", "banner"):
            out.append({"stype": "tcp", "name": f"{svc.get('name', 'TCP')} ({port})",
                        "port": port, "enabled": False})
    return out


# ── Per-host enrichment ────────────────────────────────────────────
def _enrich_host(ip: str, targets, deadline_s: float, mode: str = "full") -> dict:
    """Reverse DNS + ARP MAC + (full mode only) port scan."""
    t0 = time.monotonic()
    hostname = ""
    try:
        hostname = socket.gethostbyaddr(ip)[0]
    except Exception:
        pass

    ports = []
    if mode == "full" and targets:
        def _probe(t):
            if time.monotonic() - t0 > deadline_s:
                return
            stype = t.get("stype", "")
            port = t.get("port")
            tout = min(int(t.get("tout", 2) or 2), 2)
            try:
                if stype == "ping":
                    return
                elif stype == "tcp":
                    r = probe_tcp(ip, port, timeout=tout)
                elif stype == "http":
                    r = probe_http(f"http://{ip}:{port}", timeout=tout, verify_ssl=False)
                elif stype == "tls":
                    # Use TCP reachability for discovery — cert validation is
                    # irrelevant here and would cause false-negatives on
                    # self-signed certs (e.g. PRTG, ESXi, internal appliances)
                    r = probe_tcp(ip, port, timeout=tout)
                elif stype == "banner":
                    r = probe_banner(ip, port, timeout=tout)
                else:
                    return
            except Exception:
                return
            if r and r.get("ok"):
                ports.append({
                    "stype":  stype,
                    "name":   t.get("name", ""),
                    "port":   port,
                    "detail": (r.get("detail") or "")[:120],
                })

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(_probe, targets))

    mac = _get_mac(ip)
    return {
        "ip":        ip,
        "hostname":  hostname,
        "mac":       mac,
        "vendor":    _vendor_for_mac(mac),
        "ports":     sorted(ports, key=lambda x: x.get("port") or 0),
        "guess":     _guess_device_type(ports) if mode == "full" else "",
        "suggested": _suggest_sensors(ip, hostname, ports),
    }


# ── Background scan worker ─────────────────────────────────────────
def _scan_worker(scan_id: str, cidr: str, skip_monitored: bool, mode: str):
    """Background thread body. Mutates _SCANS[scan_id] as it progresses."""
    # Lazy import to avoid a circular dependency at module load.
    try:
        from routes.devices import _get_scan_targets
    except Exception:
        _get_scan_targets = lambda: []   # noqa: E731

    st = _SCANS.get(scan_id)
    if not st:
        return
    try:
        net, err = _validate_cidr(cidr)
        if err or net is None:
            st["state"] = "error"
            st["error"] = err or "invalid CIDR"
            return

        # Build the candidate IP list. /31 and /32 have no .hosts() — fall back.
        if net.prefixlen >= 31:
            all_ips = [str(net.network_address)]
            if net.prefixlen == 31 and net.num_addresses == 2:
                all_ips = [str(ip) for ip in net]
        else:
            all_ips = [str(ip) for ip in net.hosts()]

        monitored = _monitored_ip_set() if skip_monitored else set()
        candidates = [ip for ip in all_ips if ip not in monitored]
        st["progress"]["total"] = len(candidates)
        st["progress"]["monitored_skipped"] = len(all_ips) - len(candidates)

        # ── Phase 1 — parallel ping ──
        st["phase"] = "pinging"
        alive = []
        futures = {_SCAN_EXECUTOR.submit(probe_ping, ip, 2): ip for ip in candidates}
        try:
            for fut in concurrent.futures.as_completed(futures):
                if st.get("cancel"):
                    for f in futures:
                        f.cancel()
                    break
                ip = futures[fut]
                st["progress"]["checked"] += 1
                try:
                    r = fut.result()
                    if r and r.get("ok"):
                        alive.append((ip, r.get("ms")))
                        st["progress"]["alive"] += 1
                except Exception:
                    pass
        finally:
            futures.clear()

        # ── Phase 2 — enrichment ──
        if not st.get("cancel"):
            targets = _get_scan_targets() if mode == "full" else None
            st["phase"] = "enriching"
            st["progress"]["enrich_total"] = len(alive)
            for ip, ms in alive:
                if st.get("cancel"):
                    break
                row = _enrich_host(ip, targets, deadline_s=6.0, mode=mode)
                row["ms"] = ms
                st["results"].append(row)
                st["progress"]["enriched"] += 1

        # ── Phase 3 — multi-NIC duplicate detection ──
        if not st.get("cancel"):
            st["phase"] = "analyzing"
            try:
                existing_map = _monitored_hostname_map()
                _flag_duplicates(st["results"], existing_map)
            except Exception as e:
                log.warning(f"subnet_discovery: duplicate analysis failed: {e}")

        st["state"] = "cancelled" if st.get("cancel") else "done"
    except Exception as e:
        log.error(f"Subnet scan failed (cidr={cidr}): {e}")
        st["state"] = "error"
        st["error"] = "Scan failed — see server logs"
    finally:
        st["finished_at"] = time.time()


# ── Public API ─────────────────────────────────────────────────────
def start_scan(cidr: str, skip_monitored: bool = True, mode: str = "full"):
    """Validate CIDR, register scan state, spawn background thread.

    Returns (scan_id_or_None, error_message).
    """
    if mode not in ("full", "ping"):
        return None, "Invalid scan mode"
    net, err = _validate_cidr(cidr)
    if err:
        return None, err

    _purge_old_scans()
    scan_id = uuid.uuid4().hex[:16]
    with _SCANS_LOCK:
        _SCANS[scan_id] = {
            "scan_id":     scan_id,
            "cidr":        cidr,
            "mode":        mode,
            "state":       "running",
            "phase":       "starting",
            "started_at":  time.time(),
            "finished_at": None,
            "progress": {
                "total":             0,
                "checked":           0,
                "alive":             0,
                "enrich_total":      0,
                "enriched":          0,
                "monitored_skipped": 0,
            },
            "results": [],
            "error":   "",
            "cancel":  False,
        }
    threading.Thread(
        target=_scan_worker,
        args=(scan_id, cidr, skip_monitored, mode),
        daemon=True,
        name=f"pw-scan-{scan_id}",
    ).start()
    return scan_id, ""


def get_scan(scan_id: str):
    with _SCANS_LOCK:
        st = _SCANS.get(scan_id)
        if not st:
            return None
        # Shallow copy so callers can serialize without racing the worker
        return dict(st)


def cancel_scan(scan_id: str) -> bool:
    with _SCANS_LOCK:
        st = _SCANS.get(scan_id)
        if not st or st.get("state") != "running":
            return False
        st["cancel"] = True
        return True


def _purge_old_scans():
    now = time.time()
    with _SCANS_LOCK:
        for sid in list(_SCANS.keys()):
            f = _SCANS[sid].get("finished_at")
            if f and (now - f) > _SCAN_TTL_S:
                del _SCANS[sid]
