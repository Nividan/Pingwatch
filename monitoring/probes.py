"""
probes.py — Sensor probe implementations: ping, tcp, http, snmp.
"""

import re
import secrets
import ssl
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

from core.config import SYS
from core.logger import log_sensors
from core.validation import _HOST_RE


def _validate_host_quick(host) -> bool:
    """Fast non-raising hostname check for the probe hot path.

    Rejects obviously-malformed strings before spawning a subprocess.
    """
    return isinstance(host, str) and bool(host) and bool(_HOST_RE.match(host.strip()))


# Errno-style network conditions that mean "the target is unreachable" — an
# expected DOWN result, not a PingWatch bug. Logging these at WARNING every
# probe cycle floods the log (and WARN-based alerting) for the entire duration
# of any outage. They're returned as clean {"ok": False} without a warning.
_DOWN_ERRNOS = frozenset(filter(None, (
    getattr(__import__("errno"), n, None) for n in (
        "ENETUNREACH", "EHOSTUNREACH", "ECONNREFUSED", "ECONNRESET",
        "ETIMEDOUT", "ENETDOWN", "EHOSTDOWN", "ENOBUFS", "EPIPE",
    )
)))


def _down_detail(host, exc):
    """If `exc` is an expected 'host down' network error, return a short
    detail string for an ok=False result; otherwise return None so the caller
    logs it as a genuinely unexpected error."""
    if isinstance(exc, (socket.gaierror,)):
        return f"DNS resolution failed: {str(exc)[:60]}"
    if isinstance(exc, (ConnectionError, socket.timeout, TimeoutError)):
        return str(exc)[:80] or "unreachable"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in _DOWN_ERRNOS:
        return str(exc)[:80] or "unreachable"
    return None


def _bounded_getaddrinfo(host, port, timeout, family=0, stype=socket.SOCK_STREAM):
    """getaddrinfo with a hard wall-clock cap.

    socket.getaddrinfo has no timeout parameter and blocks until the OS
    resolver gives up (30-90s with a dead resolv.conf / AD DNS) — so the
    socket `timeout` on the connect that follows never even starts. Running
    it on a joinable thread bounds it; on timeout we raise socket.timeout so
    callers classify it as a normal DOWN.
    """
    out = {"res": None, "err": None}

    def _work():
        try:
            out["res"] = socket.getaddrinfo(host, int(port), family, stype)
        except Exception as e:
            out["err"] = e

    t = threading.Thread(target=_work, daemon=True, name="pw-resolve")
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        raise socket.timeout(f"DNS resolution exceeded {timeout}s")
    if out["err"] is not None:
        raise out["err"]
    return out["res"]


def _connect_bounded(host, port, timeout):
    """create_connection with the DNS-resolution phase bounded by `timeout`.

    Plain socket.create_connection resolves the hostname BEFORE the timeout
    governs anything, so a dead resolver hangs the probe past `timeout`. We
    resolve first (bounded), then connect to the resolved address, applying
    `timeout` to the connect itself.
    """
    err = None
    for af, socktype, proto, _canon, sa in _bounded_getaddrinfo(host, port, timeout):
        s = None
        try:
            s = socket.socket(af, socktype, proto)
            s.settimeout(timeout)
            s.connect(sa)
            return s
        except Exception as e:
            err = e
            if s is not None:
                try: s.close()
                except Exception: pass
    raise err if err else OSError(f"could not connect to {host}:{port}")


def probe_ping(host, timeout=4):
    if not _validate_host_quick(host):
        return {"ok": False, "ms": None, "detail": "invalid hostname"}
    cmd = (["ping", "-n", "2", "-w", str(timeout * 1000), host] if SYS == "Windows"
           else ["ping", "-c", "2", "-W", str(timeout), host])
    try:
        kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if SYS == "Windows" else {}
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2, **kw)
        out = r.stdout + r.stderr
        # Reject ICMP error replies — on Windows "Destination host unreachable"
        # still counts as a received packet (exit code 0) but the host doesn't exist.
        if re.search(r"(Destination host unreachable|TTL expired in transit)", out, re.IGNORECASE):
            return {"ok": False, "ms": None, "detail": "Host unreachable"}
        for pat in [r"time[=<]([\d.]+)\s*ms", r"Zeit[=<]([\d.]+)\s*ms"]:
            m = re.search(pat, out, re.IGNORECASE)
            if m:
                return {"ok": True, "ms": round(float(m.group(1)), 1),
                        "detail": f"ICMP reply {m.group(1)}ms"}
        if r.returncode == 0:
            return {"ok": True, "ms": None, "detail": "ICMP reply (no time)"}
        return {"ok": False, "ms": None, "detail": "No reply / host unreachable"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "ms": None, "detail": f"Timeout after {timeout}s"}
    except Exception as e:
        log_sensors.warning("probe_ping %s: unexpected error: %s", host, e)
        return {"ok": False, "ms": None, "detail": str(e)}


def probe_tcp(host, port, timeout=5):
    if not _validate_host_quick(host):
        return {"ok": False, "ms": None, "detail": "invalid hostname"}
    t0 = time.time()
    s = None
    try:
        s = _connect_bounded(host, int(port), timeout)
        ms = round((time.time() - t0) * 1000, 1)
        return {"ok": True, "ms": ms, "detail": f"Port {port} open ({ms}ms)"}
    except socket.timeout:
        return {"ok": False, "ms": None, "detail": f"Port {port} connection timed out"}
    except ConnectionRefusedError:
        return {"ok": False, "ms": None, "detail": f"Port {port} connection refused"}
    except Exception as e:
        _d = _down_detail(host, e)
        if _d is None:
            log_sensors.warning("probe_tcp %s:%s: unexpected error: %s", host, port, e)
            _d = str(e)[:80]
        return {"ok": False, "ms": None, "detail": _d}
    finally:
        if s is not None:
            try: s.close()
            except Exception: pass


def _peer_cert_expiry_days(host, port, timeout, verify_ssl=True):
    """Best-effort days until the TLS cert at host:port expires (negative if
    already expired), or None when it can't be determined — no cert returned,
    an untrusted chain while verifying, verify disabled (CERT_NONE hides the
    parsed cert), or any handshake error. Backs the optional cert-expiry
    thresholds on HTTP/S sensors; never raises, so it can't turn a healthy
    HTTP check into a failure."""
    import datetime
    ctx = ssl.create_default_context()
    if not verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
    else:
        try:
            from core.ssl_trust import apply_trusted_cas
            apply_trusted_cas(ctx)
        except Exception:
            pass
    raw = conn = None
    try:
        raw = _connect_bounded(host, int(port), timeout)
        conn = ctx.wrap_socket(raw, server_hostname=host)
        raw = None  # ownership transferred to conn
        cert = conn.getpeercert()
        not_after = (cert or {}).get("notAfter", "")
        if not not_after:
            return None
        exp = datetime.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        return (exp - now).days
    except Exception:
        return None
    finally:
        for _s in (conn, raw):
            if _s is not None:
                try: _s.close()
                except Exception: pass


def probe_http(url, timeout=8, verify_ssl=True, expected_status=0, cert_check=False):
    if not url.startswith("http"):
        url = "http://" + url

    def _cert_days():
        # Only peek for https URLs when the sensor has cert-expiry thresholds.
        # Done on the up paths only (a separate, bounded TLS handshake — does
        # not count toward the HTTP latency `ms`).
        if not (cert_check and url.startswith("https://")):
            return None
        try:
            from urllib.parse import urlparse
            u = urlparse(url)
            if u.hostname:
                return _peer_cert_expiry_days(u.hostname, u.port or 443,
                                              timeout, verify_ssl)
        except Exception:
            return None
        return None

    t0 = time.time()
    try:
        ctx = None
        if not verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode    = ssl.CERT_NONE
        elif url.startswith("https://"):
            from core.ssl_trust import apply_trusted_cas, get_trusted_ca_pem
            if get_trusted_ca_pem():
                ctx = ssl.create_default_context()
                apply_trusted_cas(ctx)
        req = urllib.request.Request(url, headers={"User-Agent": "PingWatch/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            ms   = round((time.time() - t0) * 1000, 1)
            code = resp.getcode()
            ssl_note = " [SSL ignored]" if not verify_ssl else ""
            ok = (code == expected_status) if expected_status else (200 <= code < 400)
            result = {"ok": ok, "ms": ms,
                      "detail": f"HTTP {code} ({ms}ms){ssl_note}", "code": code}
            _cd = _cert_days()
            if _cd is not None:
                result["cert_days"] = _cd
            return result
    except urllib.error.HTTPError as e:
        ms = round((time.time() - t0) * 1000, 1)
        ok = (e.code == expected_status) if expected_status else (e.code < 400)
        result = {"ok": ok, "ms": ms, "detail": f"HTTP {e.code}", "code": e.code}
        _cd = _cert_days()
        if _cd is not None:
            result["cert_days"] = _cd
        return result
    except urllib.error.URLError as e:
        return {"ok": False, "ms": None, "detail": str(e.reason)[:80]}
    except Exception as e:
        log_sensors.warning("probe_http %s: unexpected error: %s", url, e)
        return {"ok": False, "ms": None, "detail": str(e)[:80]}


def probe_dns(host, query, record_type="A", dns_server=None, port=53, timeout=5):
    """
    Resolve `query` using Python's socket library (for A/AAAA) or a raw UDP
    DNS request for other record types.  Uses the system resolver when
    dns_server is empty; otherwise sends directly to the specified server.
    No external libraries required.
    """
    import struct

    query = query.strip()
    record_types = {"A": 1, "AAAA": 28, "MX": 15, "CNAME": 5,
                    "NS": 2, "TXT": 16, "PTR": 12, "SOA": 6}
    qtype_num = record_types.get(record_type.upper(), 1)

    # ── For A / AAAA we can use the system resolver (simplest path) ──
    if not dns_server and record_type.upper() in ("A", "AAAA"):
        t0 = time.time()
        try:
            family = socket.AF_INET6 if record_type.upper() == "AAAA" else socket.AF_INET
            # Bounded: socket.getaddrinfo ignores any timeout and would block on
            # a dead resolver far past the sensor's configured timeout.
            results = _bounded_getaddrinfo(query, 0, timeout, family=family,
                                           stype=socket.SOCK_STREAM)
            ms = round((time.time() - t0) * 1000, 1)
            addrs = list({r[4][0] for r in results})
            return {"ok": True, "ms": ms,
                    "detail": f"{record_type} {', '.join(addrs[:3])} ({ms}ms)",
                    "value": addrs[0] if addrs else ""}
        except socket.gaierror as e:
            return {"ok": False, "ms": None, "detail": f"DNS error: {e}"}
        except socket.timeout as e:
            return {"ok": False, "ms": None, "detail": str(e)}
        except Exception as e:
            log_sensors.warning("probe_dns %s (system resolver): unexpected error: %s", query, e)
            return {"ok": False, "ms": None, "detail": str(e)[:80]}

    # ── Raw UDP DNS query (works for all types and custom servers) ──
    server = dns_server.strip() if dns_server else "8.8.8.8"
    try:
        # Build a minimal DNS query packet
        tx_id = 0x1234
        flags = 0x0100          # standard query, recursion desired
        qdcount = 1
        header = struct.pack(">HHHHHH", tx_id, flags, qdcount, 0, 0, 0)
        # Encode QNAME
        qname = b""
        for label in query.rstrip(".").split("."):
            enc = label.encode()
            qname += bytes([len(enc)]) + enc
        qname += b"\x00"
        question = qname + struct.pack(">HH", qtype_num, 1)   # QTYPE, QCLASS=IN
        packet = header + question

        t0 = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
            sock.sendto(packet, (server, int(port)))
            data, _ = sock.recvfrom(512)
            ms = round((time.time() - t0) * 1000, 1)
        except Exception:
            sock.close()
            raise
        sock.close()

        # Parse response header
        r_flags = struct.unpack(">H", data[2:4])[0]
        rcode   = r_flags & 0x000F
        ancount = struct.unpack(">H", data[6:8])[0]

        rcode_names = {0:"OK",1:"Format error",2:"Server fail",
                       3:"NX Domain",4:"Not impl",5:"Refused"}
        if rcode != 0:
            return {"ok": False, "ms": ms,
                    "detail": f"DNS {rcode_names.get(rcode, f'rcode {rcode}')} ({ms}ms)"}
        if ancount == 0:
            return {"ok": False, "ms": ms, "detail": f"No records returned ({ms}ms)"}

        # Parse first answer — skip question section first
        pos = 12
        # Skip question
        while pos < len(data) and data[pos] != 0:
            pos += data[pos] + 1
        pos += 5   # null byte + QTYPE + QCLASS

        # Read first answer record
        answers = []
        for _ in range(min(ancount, 3)):
            if pos + 12 > len(data):
                break
            # Skip name (may be pointer)
            if data[pos] & 0xC0 == 0xC0:
                pos += 2
            else:
                while pos < len(data) and data[pos] != 0:
                    pos += data[pos] + 1
                pos += 1
            if pos + 10 > len(data):
                break
            atype, _, _, rdlen = struct.unpack(">HHIH", data[pos:pos+10])
            pos += 10
            rdata = data[pos:pos+rdlen]
            pos += rdlen
            if atype == 1 and len(rdata) == 4:      # A
                answers.append(socket.inet_ntoa(rdata))
            elif atype == 28 and len(rdata) == 16:  # AAAA
                answers.append(socket.inet_ntop(socket.AF_INET6, rdata))
            elif atype in (2, 5, 12):               # NS, CNAME, PTR
                answers.append(_dns_decode_name(data, pos - rdlen))
            elif atype == 15 and len(rdata) >= 3:   # MX
                answers.append(_dns_decode_name(data, pos - rdlen + 2))
            elif atype == 16:                        # TXT
                answers.append(rdata[1:].decode(errors="replace"))
            else:
                answers.append(f"[type {atype}]")

        result_str = ", ".join(answers[:3]) if answers else "resolved"
        via = f" via {server}" if dns_server else ""
        return {"ok": True, "ms": ms,
                "detail": f"{record_type} {result_str} ({ms}ms){via}",
                "value": answers[0] if answers else ""}
    except socket.timeout:
        return {"ok": False, "ms": None, "detail": f"DNS timeout after {timeout}s"}
    except Exception as e:
        log_sensors.warning("probe_dns %s via %s: unexpected error: %s", query, server, e)
        return {"ok": False, "ms": None, "detail": str(e)[:80]}


def _dns_decode_name(data: bytes, offset: int) -> str:
    """Decode a DNS name (with pointer support) from raw packet bytes."""
    labels = []
    visited = set()
    pos = offset
    while pos < len(data):
        if pos in visited:
            break
        visited.add(pos)
        length = data[pos]
        if length == 0:
            break
        if length & 0xC0 == 0xC0:          # pointer
            if pos + 1 >= len(data): break
            ptr = ((length & 0x3F) << 8) | data[pos + 1]
            if ptr >= len(data): break
            pos = ptr
            continue
        labels.append(data[pos + 1: pos + 1 + length].decode(errors="replace"))
        pos += 1 + length
    return ".".join(labels)


_OID_RE = re.compile(r'^\.?[0-9]+(\.[0-9]+)*$')

# Whitelists for SNMPv3 parameters — passed to net-snmp binaries, so we never
# want to forward user input verbatim.  Protocol names match net-snmp's -a/-x
# flag values exactly.
_SNMP_V3_LEVELS = {"noAuthNoPriv", "authNoPriv", "authPriv"}
_SNMP_V3_AUTH_PROTOS = {"MD5", "SHA", "SHA-224", "SHA-256", "SHA-384", "SHA-512"}
_SNMP_V3_PRIV_PROTOS = {"DES", "AES", "AES-192", "AES-256"}


def _snmp_auth_args(community, version, v3_creds):
    """Build net-snmp -v / -c / -l / -u / -a / -A / -x / -X / -n flags.

    Returns (args_list, err_str).  err_str is non-None when v3 credentials
    are incomplete or contain unsafe values, in which case args_list is [].
    v3_creds is a dict with: user, level, auth_proto, auth_pass, priv_proto,
    priv_pass, context.  Unused at v1/v2c.
    """
    v = (version or "2c").strip()
    if v in ("1", "2c"):
        return ["-v", v, "-c", community or "public"], None
    if v != "3":
        return [], f"unsupported SNMP version: {v}"
    creds = v3_creds or {}
    user  = (creds.get("user") or "").strip()
    level = (creds.get("level") or "noAuthNoPriv").strip()
    if not user:
        return [], "SNMPv3 requires a username"
    if level not in _SNMP_V3_LEVELS:
        return [], f"invalid SNMPv3 level: {level}"
    args = ["-v", "3", "-l", level, "-u", user]
    if level in ("authNoPriv", "authPriv"):
        ap = (creds.get("auth_proto") or "").strip()
        apw = creds.get("auth_pass") or ""
        if ap not in _SNMP_V3_AUTH_PROTOS:
            return [], f"invalid SNMPv3 auth protocol: {ap}"
        if not apw:
            return [], "SNMPv3 auth passphrase required"
        args += ["-a", ap, "-A", apw]
    if level == "authPriv":
        pp = (creds.get("priv_proto") or "").strip()
        ppw = creds.get("priv_pass") or ""
        if pp not in _SNMP_V3_PRIV_PROTOS:
            return [], f"invalid SNMPv3 priv protocol: {pp}"
        if not ppw:
            return [], "SNMPv3 privacy passphrase required"
        args += ["-x", pp, "-X", ppw]
    ctx = (creds.get("context") or "").strip()
    if ctx:
        args += ["-n", ctx]
    return args, None


def probe_snmp(host, community, oid, port=161, timeout=5, version="2c", v3_creds=None):
    """Run snmpget via subprocess. Requires net-snmp tools installed."""
    if not _OID_RE.match(oid):
        return {"ok": False, "ms": 0, "detail": "Invalid OID format", "value": None}
    auth_args, auth_err = _snmp_auth_args(community, version, v3_creds)
    if auth_err:
        return {"ok": False, "ms": None, "detail": f"SNMP config: {auth_err}"}
    # -On: numeric OIDs in output — avoids MIB translation surprises
    cmd = ["snmpget", *auth_args, "-On",
           "-t", str(timeout), "-r", "1",
           f"{host}:{port}", oid]
    t0 = time.time()
    try:
        kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if SYS == "Windows" else {}
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 3, **kw)
        ms  = round((time.time() - t0) * 1000, 1)
        # Use stdout for value; stderr may contain MIB warnings with '=' that corrupt parsing
        raw = (r.stdout or r.stderr).strip()
        if r.returncode == 0 and raw:
            # Find the last line containing '=' — the actual OID result line
            val_line = ""
            for line in reversed(raw.splitlines()):
                if "=" in line:
                    val_line = line
                    break
            if not val_line:
                val_line = raw
            rhs = val_line.split("=", 1)[-1].strip()
            snmp_type = ""
            if ":" in rhs:
                snmp_type, _, val = rhs.partition(":")
                snmp_type = snmp_type.strip()
                val = val.strip()
            else:
                val = rhs
            # net-snmp returns rc=0 with an exception varbind for an absent OID
            # (noSuchObject / noSuchInstance / endOfMibView), printed as plain
            # text with no type prefix. Treat as a non-response — otherwise a
            # sensor on a dead OID reads "up" with a junk value and discovery
            # lists dead OIDs as responders.
            vlow = val.lower()
            if not snmp_type and (vlow.startswith("no such object")
                                  or vlow.startswith("no such instance")
                                  or vlow.startswith("no more variables")):
                return {"ok": False, "ms": None, "detail": "No object at this OID"}
            return {"ok": True, "ms": ms, "detail": f"{val}", "value": val,
                    "snmp_type": snmp_type, "raw": raw}
        err = raw[:120] if raw else "No response"
        return {"ok": False, "ms": None, "detail": err}
    except FileNotFoundError:
        return {"ok": False, "ms": None, "detail": "snmpget not found — install net-snmp"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "ms": None, "detail": f"SNMP timeout after {timeout}s"}
    except Exception as e:
        log_sensors.warning("probe_snmp %s oid=%s: unexpected error: %s", host, oid, e)
        return {"ok": False, "ms": None, "detail": str(e)[:80]}


# Computed-percentage modes: how two OID readings (A, B) combine into a
# utilisation %. Most vendors expose memory/storage as raw used+free/total
# counters rather than a native percent gauge.
_PCT_MODES = ("used_total", "used_free", "free_total")


def _snmp_pct_value(a, b, mode):
    """Combine two numeric readings into a percentage per mode. Returns None
    on a non-numeric operand or zero denominator (the probe reports failure
    rather than a fake 0%)."""
    try:
        a = float(a)
        b = float(b)
    except (TypeError, ValueError):
        return None
    if mode == "used_free":            # A=used, B=free → used/(used+free)
        den = a + b
        return (a / den) * 100.0 if den > 0 else None
    if mode == "free_total":           # A=free, B=total → (total-free)/total
        return ((b - a) / b) * 100.0 if b > 0 else None
    return (a / b) * 100.0 if b > 0 else None   # used_total: A=used, B=total


def probe_snmp_percent(host, community, oid_a, oid_b, mode="used_total",
                       port=161, timeout=5, version="2c", v3_creds=None):
    """Two-OID computed-percentage probe (e.g. memory used/total). Reads both
    OIDs via probe_snmp and returns the combined % as the sensor value."""
    ra = probe_snmp(host, community, oid_a, port=port, timeout=timeout,
                    version=version, v3_creds=v3_creds)
    if not ra.get("ok"):
        return ra
    rb = probe_snmp(host, community, oid_b, port=port, timeout=timeout,
                    version=version, v3_creds=v3_creds)
    if not rb.get("ok"):
        return rb
    pct = _snmp_pct_value(ra.get("value"), rb.get("value"), mode)
    if pct is None:
        return {"ok": False, "ms": None,
                "detail": "Non-numeric reading or zero denominator for % computation"}
    val = ("%.1f" % pct).rstrip("0").rstrip(".")
    ms = round((ra.get("ms") or 0) + (rb.get("ms") or 0), 1)
    return {"ok": True, "ms": ms, "detail": val, "value": val,
            "snmp_type": "Gauge32", "raw": ""}


def _snmpwalk_indexed(auth_args, target, base_oid, timeout):
    """Run `snmpwalk -O nq` (numeric OIDs, quick/no type) on one subtree and
    return {index-suffix:str -> value:str}, where the key is the full OID
    remainder after the base — "3" for single-index tables (ifTable), but
    "9.1.0.0" for composite-index tables (jnxOperatingTable, cempMemPool,
    F5 per-core CPU). Keying on only the last component would collapse a
    composite-index table to a single row. Returns None if the snmpwalk
    binary is missing, {} on timeout / no rows. Shared by
    snmpwalk_interfaces and snmpwalk_oid so both walk identically."""
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if SYS == "Windows" else {}
    cmd = ["snmpwalk", *auth_args,
           "-t", str(timeout), "-r", "1",
           "-O", "nq",    # numeric OIDs + quick (no type prefix)
           target, base_oid]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout + 5, **kw)
    except FileNotFoundError:
        return None          # snmpwalk not installed
    except subprocess.TimeoutExpired:
        return {}
    base_n = str(base_oid).strip().lstrip(".").rstrip(".")
    result = {}
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        oid_str, val = parts
        val = val.strip().strip('"')
        # Skip net-snmp exception varbinds (absent OID / past end of MIB view) —
        # otherwise the subtree's trailing index maps to a junk "No Such Object…"
        # string and discovery emits a phantom row.
        vlow = val.lower()
        if (vlow.startswith("no such object") or vlow.startswith("no such instance")
                or vlow.startswith("no more variables")):
            continue
        oid_n = oid_str.strip().lstrip(".").rstrip(".")
        if not oid_n.startswith(base_n + "."):
            continue   # response outside the requested subtree
        suffix = oid_n[len(base_n) + 1:]
        if not suffix or not all(c in "0123456789." for c in suffix):
            continue
        result[suffix] = val
    return result


def snmpwalk_oid(host, base_oid, community="public", port=161, timeout=8,
                 version="2c", v3_creds=None):
    """Walk an arbitrary OID subtree → {index-suffix:str -> value:str}. Suffix
    keys carry the full composite index ("3", or "9.1.0.0" for multi-component
    tables). Returns None if the snmpwalk binary is missing or the SNMP auth is
    invalid. Generic building block for template/table discovery (generalizes
    snmpwalk_interfaces)."""
    auth_args, auth_err = _snmp_auth_args(community, version, v3_creds)
    if auth_err:
        log_sensors.warning("snmpwalk_oid %s: %s", host, auth_err)
        return None
    return _snmpwalk_indexed(auth_args, f"{host}:{port}", base_oid, timeout)


def snmpwalk_interfaces(host, community="public", port=161, timeout=8, version="2c", v3_creds=None):
    """
    Walk the ifTable and ifXTable on a live device to discover its interfaces.
    Returns a list of dicts, or None if snmpwalk is not installed.
    Each dict: {index, name, descr, alias, status, speed, speed_raw}
    """
    auth_args, auth_err = _snmp_auth_args(community, version, v3_creds)
    if auth_err:
        log_sensors.warning("snmpwalk_interfaces %s: %s", host, auth_err)
        return []
    target = f"{host}:{port}"

    def _walk(base_oid):
        rows = _snmpwalk_indexed(auth_args, target, base_oid, timeout)
        if rows is None:
            return None
        # ifTable/ifXTable rows are single-component (ifIndex) — coerce the
        # suffix keys back to ints so interface discovery keeps its
        # historical {int: value} shape byte-for-byte.
        out = {}
        for k, v in rows.items():
            try:
                out[int(k)] = v
            except ValueError:
                pass
        return out

    # Walk the five tables we care about
    descrs   = _walk("1.3.6.1.2.1.2.2.1.2")      # ifDescr
    if descrs is None:
        return None                                  # snmpwalk not found
    statuses = _walk("1.3.6.1.2.1.2.2.1.8")  or {} # ifOperStatus
    speeds   = _walk("1.3.6.1.2.1.2.2.1.5")  or {} # ifSpeed (bps, 32-bit)
    names    = _walk("1.3.6.1.2.1.31.1.1.1.1") or {} # ifName (ifXTable)
    aliases  = _walk("1.3.6.1.2.1.31.1.1.1.18") or {} # ifAlias (admin description)

    interfaces = []
    for idx in sorted(set(descrs) | set(names)):
        descr = descrs.get(idx, "")
        name  = names.get(idx, descr) or descr
        alias = aliases.get(idx, "")
        raw_st = statuses.get(idx, "")

        # Normalize status: snmpwalk -Oq gives "1" or "up(1)" depending on version
        st_core = raw_st.split("(")[0].strip() if "(" in raw_st else raw_st
        try:
            status = "up" if int(st_core) == 1 else "down"
        except (ValueError, TypeError):
            status = raw_st or "unknown"

        # Normalize speed to human-readable string
        try:
            spd = int(speeds.get(idx, 0))
            if spd >= 1_000_000_000:
                speed_str = f"{spd // 1_000_000_000}G"
            elif spd >= 1_000_000:
                speed_str = f"{spd // 1_000_000}M"
            elif spd > 0:
                speed_str = f"{spd // 1_000}K"
            else:
                speed_str = ""
        except (ValueError, TypeError):
            spd = 0
            speed_str = ""

        interfaces.append({
            "index":     idx,
            "name":      name,
            "descr":     descr,
            "alias":     alias,
            "status":    status,
            "speed":     speed_str,
            "speed_raw": spd,
        })

    return interfaces


# RFC 3433 EntitySensorDataScale exponents, shared by the standard
# ENTITY-SENSOR-MIB (1.3.6.1.2.1.99) and CISCO-ENTITY-SENSOR-MIB (9.9.91).
# Note the RFC's own ordering quirk: exa(14)=10^18 but peta(15)=10^15.
_ENT_SCALE_EXP = {1: -24, 2: -21, 3: -18, 4: -15, 5: -12, 6: -9, 7: -6, 8: -3,
                  9: 0, 10: 3, 11: 6, 12: 9, 13: 12, 14: 18, 15: 15, 16: 21, 17: 24}


def _clean_scale(v):
    """Validate an item's static scale divisor → float > 0, or 0 (= none)."""
    try:
        f = float(v)
        return f if f > 0 else 0
    except (TypeError, ValueError):
        return 0


def _apply_scale(val, scale):
    """Divide a raw reading by a scale divisor, rendered compactly
    (415 ÷ 10 → "41.5"; 240 ÷ 10 → "24")."""
    if not scale or scale == 1:
        return val
    try:
        f = float(val) / float(scale)
    except (TypeError, ValueError, ZeroDivisionError):
        return val
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return ("%.4f" % f).rstrip("0").rstrip(".")


def _idx_sort_key(sfx):
    """Numeric sort for index suffixes ("2" < "10"; composite "1.2" < "1.10")."""
    try:
        return tuple(int(p) for p in str(sfx).split("."))
    except (TypeError, ValueError):
        return (1 << 30,)


def snmp_discover_template(host, items, community="public", port=161, timeout=8,
                           version="2c", v3_creds=None):
    """Probe a device against a template's items; return candidate rows for only
    the metrics that responded.

    Item kinds:
      scalar  — one snmpget (concurrent across items); kept if it answers.
      percent — two OIDs (oid + oid2) combined per percent_mode
                (used_total / used_free / free_total).
      table   — the base OID is walked once; one candidate per responding row,
                named via name_oid → name_oid2 → index suffix. Composite
                indexes are preserved ("9.1.0.0" for jnxOperatingTable).

    Per-item value transforms (all optional):
      scale                     — static divisor (deci-°C → scale 10).
      scale_oid + precision_oid — RFC 3433 entity-sensor columns walked
                                  alongside a table base; each row gets an
                                  auto-computed divisor (beats static scale).
      oid2 + percent_mode       — on a table item, walks the partner column
                                  and emits per-row computed percentages.

    Returns None if the snmpwalk binary is missing (so the caller can tell
    "install net-snmp" apart from "device exposes nothing"). Total candidates
    are capped at 4096 to keep the result inside a single sync-waiter chunk.
    """
    from concurrent.futures import ThreadPoolExecutor
    _CAP = 4096
    items = items or []
    scalars = [it for it in items
               if (it.get("kind") or "scalar") in ("scalar", "percent")]
    tables  = [it for it in items if it.get("kind") == "table"]
    candidates = []

    # ── Scalars & scalar percents — concurrent snmpget, keep responders ──
    def _do_scalar(it):
        oid = (it.get("oid") or "").strip()
        if not oid:
            return None
        kind = it.get("kind") or "scalar"
        scale = _clean_scale(it.get("scale"))
        if kind == "percent":
            oid2 = (it.get("oid2") or "").strip()
            mode = it.get("percent_mode") or "used_total"
            if not oid2 or mode not in _PCT_MODES:
                return None
            r = probe_snmp_percent(host, community, oid, oid2, mode,
                                   port=port, timeout=timeout,
                                   version=version, v3_creds=v3_creds)
        else:
            r = probe_snmp(host, community, oid, port=port, timeout=timeout,
                           version=version, v3_creds=v3_creds)
        if not r.get("ok"):
            return None
        label = it.get("label") or oid
        c = {"kind": kind, "item_label": label, "oid": oid,
             "unit": it.get("unit") or ("%" if kind == "percent" else ""),
             "value": (r.get("value") if kind == "percent"
                       else _apply_scale(r.get("value"), scale)),
             "snmp_type": r.get("snmp_type") or "",
             "warn": it.get("warn"), "crit": it.get("crit"),
             "interval": it.get("interval"), "suggested_name": label,
             "group": it.get("group") or ""}
        if kind == "percent":
            c["oid2"] = oid2
            c["percent_mode"] = mode
        elif scale:
            c["scale"] = scale
        return c
    if scalars:
        with ThreadPoolExecutor(max_workers=min(16, len(scalars))) as ex:
            for c in ex.map(_do_scalar, scalars):
                if c:
                    candidates.append(c)

    # ── Tables — walk each base once; shared columns (names, speeds, scale/
    #    precision, percent partners) are walked once and cached across items ──
    _walk_cache = {}   # oid -> {index-suffix: value}

    def _walk_cached(oid):
        oid = (oid or "").strip().rstrip(".")
        if not oid:
            return {}
        if oid not in _walk_cache:
            _walk_cache[oid] = snmpwalk_oid(host, oid, community, port,
                                            timeout, version, v3_creds) or {}
        return _walk_cache[oid]

    for it in tables:
        if len(candidates) >= _CAP:
            break
        base = (it.get("oid") or "").strip().rstrip(".")
        if not base:
            continue
        vals = snmpwalk_oid(host, base, community, port, timeout, version, v3_creds)
        if vals is None:
            return None   # snmpwalk binary missing → abort whole discovery
        if not vals:
            continue
        names1 = _walk_cached(it.get("name_oid"))
        names2 = _walk_cached(it.get("name_oid2"))
        speed_base = (it.get("speed_oid") or "").strip().rstrip(".")
        speeds = _walk_cached(speed_base) if (it.get("speed_auto_threshold") and speed_base) else {}
        hc_base = (it.get("hc_variant_oid") or "").strip().rstrip(".")
        pct_base = (it.get("oid2") or "").strip().rstrip(".")
        pct_mode = it.get("percent_mode") or "used_total"
        pcts = _walk_cached(pct_base) if (pct_base and pct_mode in _PCT_MODES) else {}
        if pct_base and not pcts:
            pct_base = ""   # partner column didn't answer → plain table rows
        static_scale = _clean_scale(it.get("scale"))
        sc_base = (it.get("scale_oid") or "").strip().rstrip(".")
        pr_base = (it.get("precision_oid") or "").strip().rstrip(".")
        ent_scales = _walk_cached(sc_base) if sc_base else {}
        ent_precs  = _walk_cached(pr_base) if pr_base else {}
        label = it.get("label") or base
        for idx in sorted(vals, key=_idx_sort_key):
            row_name = names1.get(idx) or names2.get(idx) or str(idx)
            try:
                speed_raw = int(speeds.get(idx) or 0)
            except (TypeError, ValueError):
                speed_raw = 0
            # Effective per-row divisor: entity-sensor scale/precision wins
            # over the item's static scale.
            row_scale = static_scale
            if ent_scales or ent_precs:
                try:
                    exp = _ENT_SCALE_EXP.get(int(ent_scales.get(idx)))
                    prec = int(ent_precs.get(idx) or 0)
                    if exp is not None:
                        d = 10.0 ** (prec - exp)
                        if d > 0:
                            row_scale = d
                except (TypeError, ValueError):
                    pass
            if pct_base:
                pv = _snmp_pct_value(vals.get(idx), pcts.get(idx), pct_mode)
                if pv is None:
                    continue   # partner row missing/non-numeric → skip row
                disp_val = ("%.1f" % pv).rstrip("0").rstrip(".")
            else:
                disp_val = _apply_scale(vals.get(idx), row_scale)
            c = {
                "kind": "percent" if pct_base else "table",
                "item_label": label, "index": idx,
                "row_name": row_name, "oid": f"{base}.{idx}",
                "hc_oid": f"{hc_base}.{idx}" if hc_base else "",
                "unit": it.get("unit") or ("%" if pct_base else ""),
                "value": disp_val,
                "speed_auto_threshold": bool(it.get("speed_auto_threshold")),
                "speed_raw": speed_raw,
                "warn": it.get("warn"), "crit": it.get("crit"),
                "interval": it.get("interval"),
                "suggested_name": f"{row_name} {label}".strip(),
                "group": it.get("group") or ""}
            if pct_base:
                c["oid2"] = f"{pct_base}.{idx}"
                c["percent_mode"] = pct_mode
            elif row_scale and row_scale != 1:
                c["scale"] = row_scale
            candidates.append(c)
            if len(candidates) >= _CAP:
                break
    return candidates


def probe_tls(host, port=443, timeout=10):
    """Connect via TLS and report certificate expiry in days."""
    import ssl, datetime
    # Strip scheme if user accidentally entered a URL (e.g. https://example.com)
    for _pfx in ("https://", "http://"):
        if host.lower().startswith(_pfx):
            host = host[len(_pfx):].split("/")[0]
            break
    ctx = ssl.create_default_context()
    from core.ssl_trust import apply_trusted_cas
    apply_trusted_cas(ctx)
    t0 = time.time()
    conn = None
    raw = None
    try:
        raw = _connect_bounded(host, int(port), timeout)
        conn = ctx.wrap_socket(raw, server_hostname=host)
        raw = None  # ownership transferred to conn
        ms = round((time.time() - t0) * 1000, 1)
        cert = conn.getpeercert()
        conn.close(); conn = None
        not_after = (cert or {}).get("notAfter", "")
        if not not_after:
            # Handshake succeeded but no cert presented (session resumption,
            # some proxies). Not a PingWatch error — report cleanly, no WARN.
            return {"ok": False, "ms": None,
                    "detail": "TLS: no certificate returned by peer"}
        exp = datetime.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
        days = (exp - datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).days
        ok = days > 0
        detail = (f"TLS valid, expires in {days}d ({ms}ms)" if ok
                  else f"TLS cert expired {-days}d ago ({ms}ms)")
        return {"ok": ok, "ms": ms, "detail": detail, "value": str(days)}
    except ssl.CertificateError as e:
        return {"ok": False, "ms": None, "detail": f"TLS cert error: {str(e)[:80]}"}
    except ssl.SSLError as e:
        return {"ok": False, "ms": None, "detail": f"TLS SSL error: {str(e)[:80]}"}
    except socket.timeout:
        return {"ok": False, "ms": None, "detail": f"TLS timeout after {timeout}s"}
    except ValueError as e:
        # strptime on an odd notAfter format — clean down, not a crash.
        return {"ok": False, "ms": None, "detail": f"TLS cert parse error: {str(e)[:60]}"}
    except Exception as e:
        _d = _down_detail(host, e)
        if _d is None:
            log_sensors.warning("probe_tls %s:%s: unexpected error: %s", host, port, e)
            _d = str(e)[:80]
        return {"ok": False, "ms": None, "detail": _d}
    finally:
        for _sock in (conn, raw):
            if _sock is not None:
                try: _sock.close()
                except Exception: pass


def probe_http_keyword(url, keyword, timeout=8, verify_ssl=True, case_sensitive=False):
    """HTTP probe that checks for keyword presence in the response body."""
    if not url.startswith("http"):
        url = "http://" + url
    if not keyword:
        return {"ok": False, "ms": None, "detail": "No keyword configured"}
    t0 = time.time()
    try:
        ctx = None
        if not verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode    = ssl.CERT_NONE
        elif url.startswith("https://"):
            from core.ssl_trust import apply_trusted_cas, get_trusted_ca_pem
            if get_trusted_ca_pem():
                ctx = ssl.create_default_context()
                apply_trusted_cas(ctx)
        req = urllib.request.Request(url, headers={"User-Agent": "PingWatch/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            ms   = round((time.time() - t0) * 1000, 1)
            code = resp.getcode()
            body = resp.read(65536).decode(errors="replace")
            found = (keyword in body) if case_sensitive else (keyword.lower() in body.lower())
            status = "found" if found else "not found"
            return {"ok": found, "ms": ms,
                    "detail": f"Keyword {status} in HTTP {code} ({ms}ms)"}
    except urllib.error.HTTPError as e:
        ms = round((time.time() - t0) * 1000, 1)
        return {"ok": False, "ms": ms, "detail": f"HTTP {e.code} — keyword not checked"}
    except urllib.error.URLError as e:
        return {"ok": False, "ms": None, "detail": str(e.reason)[:80]}
    except Exception as e:
        log_sensors.warning("probe_http_keyword %s: unexpected error: %s", url, e)
        return {"ok": False, "ms": None, "detail": str(e)[:80]}


def probe_banner(host, port, banner_regex="", timeout=5):
    """TCP connect, read banner bytes, optionally match regex."""
    t0 = time.time()
    s = None
    try:
        s = _connect_bounded(host, int(port), timeout)
        s.settimeout(timeout)
        try:
            banner = s.recv(256).decode(errors="replace").strip()
        except Exception:
            banner = ""
        s.close(); s = None
        ms = round((time.time() - t0) * 1000, 1)
        if banner_regex:
            _result = [False]
            _exc    = [None]
            def _do_match(_r=banner_regex, _b=banner):
                try:   _result[0] = bool(re.search(_r, _b))
                except re.error as _e: _exc[0] = _e
            _mt = threading.Thread(target=_do_match, daemon=True)
            _mt.start()
            _mt.join(timeout=2.0)
            if _mt.is_alive():
                return {"ok": False, "ms": ms, "detail": "Banner regex timed out"}
            if _exc[0]:
                return {"ok": False, "ms": ms, "detail": f"Invalid banner regex: {_exc[0]}"}
            ok = _result[0]
            status = "matched" if ok else "no match"
            detail = f"Banner {status}: {banner[:60]!r} ({ms}ms)"
        else:
            ok = True
            detail = (f"Banner: {banner[:60]!r} ({ms}ms)" if banner
                      else f"Connected, no banner ({ms}ms)")
        return {"ok": ok, "ms": ms, "detail": detail, "value": banner[:200]}
    except socket.timeout:
        return {"ok": False, "ms": None, "detail": f"Connection timed out after {timeout}s"}
    except ConnectionRefusedError:
        return {"ok": False, "ms": None, "detail": f"Port {port} connection refused"}
    except Exception as e:
        _d = _down_detail(host, e)
        if _d is None:
            log_sensors.warning("probe_banner %s:%s: unexpected error: %s", host, port, e)
            _d = str(e)[:80]
        return {"ok": False, "ms": None, "detail": _d}
    finally:
        if s:
            try: s.close()
            except Exception: pass


_SMTP_LEVELS = ("connect", "ehlo", "starttls", "auth", "mailfrom")


def probe_smtp(host, port=25, tls="none", user="", password="",
               from_addr="", rcpt="", test_level="ehlo", timeout=10):
    """Probe an SMTP server — layered depth from TCP connect up to MAIL FROM round-trip.

    test_level: one of connect | ehlo | starttls | auth | mailfrom. Each level
                runs all prior steps. MAIL FROM level does MAIL FROM → RCPT TO
                → RSET (no DATA — no real mail is sent).
    tls:        none | starttls | ssl.
    """
    import smtplib

    if not _validate_host_quick(host):
        return {"ok": False, "ms": None, "detail": "invalid hostname"}
    if test_level not in _SMTP_LEVELS:
        test_level = "ehlo"
    depth = _SMTP_LEVELS.index(test_level)
    t0 = time.time()

    # ── Level 0: plain TCP connect + read 220 banner ────────────────────
    if depth == 0:
        sock = None
        try:
            sock = socket.create_connection((host, int(port)), timeout=timeout)
            ms = round((time.time() - t0) * 1000, 1)
            if tls == "ssl":
                return {"ok": True, "ms": ms,
                        "detail": f"SMTPS port open ({ms}ms)"}
            sock.settimeout(timeout)
            try:
                banner = sock.recv(256).decode(errors="replace").strip()
            except Exception:
                banner = ""
            ms = round((time.time() - t0) * 1000, 1)
            if banner.startswith("220"):
                return {"ok": True, "ms": ms,
                        "detail": f"SMTP banner: {banner[:80]} ({ms}ms)"}
            return {"ok": False, "ms": ms,
                    "detail": f"Unexpected banner: {banner[:80] or '(empty)'}"}
        except socket.timeout:
            return {"ok": False, "ms": None, "detail": f"Connect timeout after {timeout}s"}
        except ConnectionRefusedError:
            return {"ok": False, "ms": None, "detail": f"Port {port} connection refused"}
        except Exception as e:
            log_sensors.warning("probe_smtp %s:%s: connect error: %s", host, port, e)
            return {"ok": False, "ms": None, "detail": str(e)[:80]}
        finally:
            if sock:
                try: sock.close()
                except Exception: pass

    # ── Levels 1-4: use smtplib — constructor does CONNECT + EHLO ───────
    srv = None
    try:
        if tls in ("ssl", "starttls"):
            _ssl_ctx = ssl.create_default_context()
            from core.ssl_trust import apply_trusted_cas
            apply_trusted_cas(_ssl_ctx)
        else:
            _ssl_ctx = None
        if tls == "ssl":
            srv = smtplib.SMTP_SSL(host, int(port), timeout=timeout, context=_ssl_ctx)
        else:
            srv = smtplib.SMTP(host, int(port), timeout=timeout)

        # Level 1: ehlo (already done by constructor — nothing extra)
        if depth == 1:
            ms = round((time.time() - t0) * 1000, 1)
            return {"ok": True, "ms": ms, "detail": f"SMTP EHLO OK ({ms}ms)"}

        # Level 2: starttls (only when tls=starttls — ssl already encrypted)
        if tls == "starttls":
            try:
                srv.starttls(context=_ssl_ctx)
                srv.ehlo()
            except smtplib.SMTPException as e:
                return {"ok": False, "ms": None,
                        "detail": f"STARTTLS failed: {str(e)[:100]}"}
        if depth == 2:
            ms = round((time.time() - t0) * 1000, 1)
            return {"ok": True, "ms": ms, "detail": f"SMTP STARTTLS OK ({ms}ms)"}

        # Level 3: auth
        if not user:
            return {"ok": False, "ms": None,
                    "detail": "AUTH level requires user + password"}
        try:
            srv.login(user, password)
        except smtplib.SMTPAuthenticationError as e:
            return {"ok": False, "ms": None,
                    "detail": f"AUTH failed: {str(e)[:100]}"}
        except smtplib.SMTPException as e:
            return {"ok": False, "ms": None,
                    "detail": f"AUTH error: {str(e)[:100]}"}
        if depth == 3:
            ms = round((time.time() - t0) * 1000, 1)
            return {"ok": True, "ms": ms, "detail": f"SMTP AUTH OK ({ms}ms)"}

        # Level 4: mailfrom round-trip (RSET after RCPT — no DATA)
        if not from_addr or not rcpt:
            return {"ok": False, "ms": None,
                    "detail": "MAIL FROM level requires From and To addresses"}
        try:
            code, msg = srv.mail(from_addr)
        except smtplib.SMTPException as e:
            return {"ok": False, "ms": None, "detail": f"MAIL FROM error: {str(e)[:100]}"}
        if code != 250:
            return {"ok": False, "ms": None,
                    "detail": f"MAIL FROM rejected: {code} {_decode_resp(msg)[:80]}"}
        try:
            code, msg = srv.rcpt(rcpt)
        except smtplib.SMTPException as e:
            return {"ok": False, "ms": None, "detail": f"RCPT TO error: {str(e)[:100]}"}
        if code not in (250, 251):
            return {"ok": False, "ms": None,
                    "detail": f"RCPT TO rejected: {code} {_decode_resp(msg)[:80]}"}
        try: srv.rset()
        except Exception: pass
        ms = round((time.time() - t0) * 1000, 1)
        return {"ok": True, "ms": ms,
                "detail": f"SMTP MAIL FROM round-trip OK ({ms}ms)"}

    except smtplib.SMTPConnectError as e:
        return {"ok": False, "ms": None, "detail": f"SMTP connect error: {str(e)[:100]}"}
    except smtplib.SMTPServerDisconnected as e:
        return {"ok": False, "ms": None, "detail": f"SMTP server disconnected: {str(e)[:100]}"}
    except smtplib.SMTPException as e:
        return {"ok": False, "ms": None, "detail": f"SMTP error: {str(e)[:100]}"}
    except socket.timeout:
        return {"ok": False, "ms": None, "detail": f"SMTP timeout after {timeout}s"}
    except (ConnectionRefusedError, OSError) as e:
        return {"ok": False, "ms": None, "detail": f"SMTP connect: {str(e)[:80]}"}
    except Exception as e:
        log_sensors.warning("probe_smtp %s:%s: unexpected error: %s", host, port, e)
        return {"ok": False, "ms": None, "detail": str(e)[:100]}
    finally:
        if srv:
            try: srv.quit()
            except Exception:
                try: srv.close()
                except Exception: pass


def _decode_resp(msg) -> str:
    if isinstance(msg, bytes):
        try: return msg.decode("utf-8", "replace")
        except Exception: return ""
    return str(msg or "")


_SFTP_LEVELS = ("open", "list", "stat", "checksum")
_SFTP_CHECKSUM_MAX_BYTES = 10 * 1024 * 1024   # 10 MB hard cap (fallback — overridden by setting `sftp_checksum_max_mb`)


def _sftp_cap_bytes() -> int:
    try:
        import core.settings as _s
        mb = max(1, min(500, int(_s.get("sftp_checksum_max_mb", 10) or 10)))
        return mb * 1024 * 1024
    except Exception:
        return _SFTP_CHECKSUM_MAX_BYTES


def _fmt_bytes(n: int) -> str:
    if n < 1024: return f"{n} B"
    if n < 1024 ** 2: return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3: return f"{n / (1024 ** 2):.1f} MB"
    return f"{n / (1024 ** 3):.2f} GB"


def probe_sftp(host, port=22, user="", password="", private_key="",
               auth_type="password", test_level="open",
               remote_path="", expected_sha256="", timeout=10):
    """Probe an SFTP server — 4 layered depths.

    test_level: open | list | stat | checksum. Each level runs all prior steps.
    auth_type:  password | key.

    The probe is read-only — never writes, renames, or deletes on the remote.
    Checksum level streams up to 10 MB and computes SHA256 locally; files over
    the cap fail with a clear detail (monitoring huge files is the wrong fit).
    """
    import hashlib

    if not _validate_host_quick(host):
        return {"ok": False, "ms": None, "detail": "invalid hostname"}
    if test_level not in _SFTP_LEVELS:
        test_level = "open"
    depth = _SFTP_LEVELS.index(test_level)

    try:
        import paramiko
    except ImportError:
        return {"ok": False, "ms": None,
                "detail": "paramiko not installed — run setup wizard"}

    if not user:
        return {"ok": False, "ms": None, "detail": "SFTP requires a username"}

    pkey = None
    if auth_type == "key":
        if not private_key:
            return {"ok": False, "ms": None,
                    "detail": "SFTP/key auth requires a private key"}
        pkey, kerr = _load_ssh_key(private_key)
        if kerr:
            return {"ok": False, "ms": None, "detail": f"Key load failed: {kerr}"}
    elif auth_type != "password":
        return {"ok": False, "ms": None, "detail": f"Unknown auth_type: {auth_type!r}"}

    if depth >= 1 and not remote_path:
        return {"ok": False, "ms": None,
                "detail": f"{test_level}: remote path is required"}
    if depth == 3 and not expected_sha256:
        return {"ok": False, "ms": None,
                "detail": "checksum: expected SHA256 is required"}

    t0 = time.time()
    client = None
    sftp = None
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.MissingHostKeyPolicy())
        kw = dict(
            hostname=host, port=int(port), username=user,
            timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
            allow_agent=False, look_for_keys=False,
        )
        if pkey is not None: kw["pkey"] = pkey
        else:                kw["password"] = password
        client.connect(**kw)
        sftp = client.open_sftp()
        # Bound the SFTP data channel: connect()'s timeout only covers TCP +
        # auth. Without this, a server that authenticates then stalls at the
        # SFTP layer (or mid-read during the checksum loop) hangs until the
        # _run_once hard cap, leaking the transport for that window each cycle.
        try:
            sftp.get_channel().settimeout(timeout)
        except Exception:
            pass

        # Level 0: open
        if depth == 0:
            ms = round((time.time() - t0) * 1000, 1)
            return {"ok": True, "ms": ms, "detail": f"SFTP subsystem OK ({ms}ms)"}

        # Level 1: list
        if depth == 1:
            try:
                entries = sftp.listdir(remote_path)
            except IOError as e:
                return {"ok": False, "ms": None,
                        "detail": f"list: {str(e)[:100]}"}
            ms = round((time.time() - t0) * 1000, 1)
            return {"ok": True, "ms": ms,
                    "detail": f"list {remote_path}: {len(entries)} entries ({ms}ms)",
                    "value": str(len(entries))}

        # Level 2: stat
        if depth == 2:
            try:
                st = sftp.stat(remote_path)
            except IOError as e:
                return {"ok": False, "ms": None,
                        "detail": f"stat: {str(e)[:100]}"}
            size = int(getattr(st, "st_size", 0) or 0)
            ms = round((time.time() - t0) * 1000, 1)
            return {"ok": True, "ms": ms,
                    "detail": f"stat {remote_path}: {_fmt_bytes(size)} ({ms}ms)",
                    "value": str(size)}

        # Level 3: checksum (stream SHA256, cap per setting)
        _cap = _sftp_cap_bytes()
        try:
            # Pre-flight: fail fast if stat says the file is over the cap
            try:
                st = sftp.stat(remote_path)
                if int(getattr(st, "st_size", 0) or 0) > _cap:
                    return {"ok": False, "ms": None,
                            "detail": f"checksum: file exceeds {_fmt_bytes(_cap)} cap"}
            except IOError as e:
                return {"ok": False, "ms": None,
                        "detail": f"checksum/stat: {str(e)[:100]}"}
            h = hashlib.sha256()
            total = 0
            with sftp.open(remote_path, "rb") as rf:
                while True:
                    chunk = rf.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _cap:
                        return {"ok": False, "ms": None,
                                "detail": f"checksum: file exceeds {_fmt_bytes(_cap)} cap"}
                    h.update(chunk)
        except IOError as e:
            return {"ok": False, "ms": None,
                    "detail": f"checksum/read: {str(e)[:100]}"}
        got = h.hexdigest()
        want = (expected_sha256 or "").strip().lower()
        if got.lower() != want:
            return {"ok": False, "ms": None,
                    "detail": f"checksum: mismatch (got {got[:12]}…, expected {want[:12]}…)"}
        ms = round((time.time() - t0) * 1000, 1)
        return {"ok": True, "ms": ms,
                "detail": f"checksum OK ({_fmt_bytes(total)}, {ms}ms)",
                "value": got[:12]}

    except paramiko.AuthenticationException:
        return {"ok": False, "ms": None, "detail": "auth: authentication failed"}
    except paramiko.BadHostKeyException as e:
        return {"ok": False, "ms": None, "detail": f"auth: bad host key — {str(e)[:80]}"}
    except paramiko.SSHException as e:
        return {"ok": False, "ms": None, "detail": f"sftp: {str(e)[:100]}"}
    except socket.timeout:
        return {"ok": False, "ms": None, "detail": f"sftp timeout after {timeout}s"}
    except (ConnectionRefusedError, OSError) as e:
        return {"ok": False, "ms": None, "detail": f"sftp connect: {str(e)[:80]}"}
    except Exception as e:
        log_sensors.warning("probe_sftp %s:%s: unexpected error: %s", host, port, e)
        return {"ok": False, "ms": None, "detail": str(e)[:100]}
    finally:
        if sftp:
            try: sftp.close()
            except Exception: pass
        if client:
            try: client.close()
            except Exception: pass


_SSH_LEVELS = ("connect", "banner", "auth")


def _load_ssh_key(blob: str):
    """Try Ed25519/RSA/ECDSA in order. Returns (pkey, err_or_None).

    Mirrors backup/remote_upload.py:_sftp_load_key. No passphrase support in
    v1 — a key with a passphrase will fail all loaders and surface as
    'unrecognised private key format'.
    """
    import io
    import paramiko
    for loader in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return loader.from_private_key(io.StringIO(blob)), None
        except Exception:
            continue
    return None, "unrecognised private key format"


def probe_ssh(host, port=22, user="", password="", private_key="",
              auth_type="password", test_level="banner", timeout=8):
    """Probe an SSH server — layered depth: connect → banner → auth.

    test_level: connect | banner | auth. Each level runs all prior steps.
    auth_type:  password | key (only consulted at the `auth` level).

    Host key verification is deliberately disabled (MissingHostKeyPolicy) —
    a reachability probe shouldn't page on key rotation. The backup engine's
    TOFU store is for command execution, not monitoring.
    """
    if not _validate_host_quick(host):
        return {"ok": False, "ms": None, "detail": "invalid hostname"}
    if test_level not in _SSH_LEVELS:
        test_level = "banner"
    depth = _SSH_LEVELS.index(test_level)
    t0 = time.time()

    # ── Level 0: TCP connect only ────────────────────────────────────────
    if depth == 0:
        sock = None
        try:
            sock = socket.create_connection((host, int(port)), timeout=timeout)
            ms = round((time.time() - t0) * 1000, 1)
            return {"ok": True, "ms": ms,
                    "detail": f"SSH port open ({ms}ms)"}
        except socket.timeout:
            return {"ok": False, "ms": None, "detail": f"Connect timeout after {timeout}s"}
        except ConnectionRefusedError:
            return {"ok": False, "ms": None, "detail": f"Port {port} connection refused"}
        except Exception as e:
            log_sensors.warning("probe_ssh %s:%s: connect error: %s", host, port, e)
            return {"ok": False, "ms": None, "detail": str(e)[:80]}
        finally:
            if sock:
                try: sock.close()
                except Exception: pass

    # ── Level 1: banner read (raw socket — no paramiko needed) ───────────
    if depth == 1:
        sock = None
        try:
            sock = socket.create_connection((host, int(port)), timeout=timeout)
            sock.settimeout(timeout)
            # SSH banner is terminated by \r\n and should arrive within one recv
            try:
                raw = sock.recv(256).decode("utf-8", "replace").strip()
            except Exception:
                raw = ""
            ms = round((time.time() - t0) * 1000, 1)
            first = raw.split("\n", 1)[0].strip()
            if first.startswith("SSH-"):
                return {"ok": True, "ms": ms,
                        "detail": f"{first[:80]} ({ms}ms)",
                        "value": first[:100]}
            return {"ok": False, "ms": ms,
                    "detail": f"Not SSH (got {first[:60]!r})" if first
                              else "Not SSH (empty banner)"}
        except socket.timeout:
            return {"ok": False, "ms": None, "detail": f"Banner timeout after {timeout}s"}
        except ConnectionRefusedError:
            return {"ok": False, "ms": None, "detail": f"Port {port} connection refused"}
        except Exception as e:
            log_sensors.warning("probe_ssh %s:%s: banner error: %s", host, port, e)
            return {"ok": False, "ms": None, "detail": str(e)[:80]}
        finally:
            if sock:
                try: sock.close()
                except Exception: pass

    # ── Level 2: full auth via paramiko ──────────────────────────────────
    try:
        import paramiko
    except ImportError:
        return {"ok": False, "ms": None,
                "detail": "paramiko not installed — run setup wizard"}

    if not user:
        return {"ok": False, "ms": None, "detail": "AUTH level requires a username"}

    pkey = None
    if auth_type == "key":
        if not private_key:
            return {"ok": False, "ms": None,
                    "detail": "AUTH/key level requires a private key"}
        pkey, kerr = _load_ssh_key(private_key)
        if kerr:
            return {"ok": False, "ms": None, "detail": f"Key load failed: {kerr}"}
    elif auth_type != "password":
        return {"ok": False, "ms": None, "detail": f"Unknown auth_type: {auth_type!r}"}
    # password mode falls through with pkey=None

    client = None
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.MissingHostKeyPolicy())
        kw = dict(
            hostname=host, port=int(port), username=user,
            timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
            allow_agent=False, look_for_keys=False,
        )
        if pkey is not None:
            kw["pkey"] = pkey
        else:
            kw["password"] = password
        client.connect(**kw)
        ms = round((time.time() - t0) * 1000, 1)
        return {"ok": True, "ms": ms,
                "detail": f"SSH auth OK ({auth_type}, {ms}ms)"}
    except paramiko.AuthenticationException:
        return {"ok": False, "ms": None, "detail": "auth: authentication failed"}
    except paramiko.BadHostKeyException as e:
        return {"ok": False, "ms": None, "detail": f"auth: bad host key — {str(e)[:80]}"}
    except paramiko.SSHException as e:
        return {"ok": False, "ms": None, "detail": f"auth: {str(e)[:100]}"}
    except socket.timeout:
        return {"ok": False, "ms": None, "detail": f"auth timeout after {timeout}s"}
    except (ConnectionRefusedError, OSError) as e:
        return {"ok": False, "ms": None, "detail": f"auth connect: {str(e)[:80]}"}
    except Exception as e:
        log_sensors.warning("probe_ssh %s:%s: auth error: %s", host, port, e)
        return {"ok": False, "ms": None, "detail": str(e)[:100]}
    finally:
        if client:
            try: client.close()
            except Exception: pass


_RADIUS_LEVELS = ("reachable", "auth")


def probe_radius(host, port=1812, secret="", test_level="reachable",
                 user="", password="", nas_id="pingwatch", timeout=5):
    """Probe a RADIUS auth server — layered depth: reachable → auth.

    reachable: send Access-Request with a random probe user; any RADIUS
               reply (Accept / Reject / Challenge) proves the server is up
               and the shared secret is correct.
    auth:      send a real user + password, require Access-Accept.
               Access-Reject / Access-Challenge are failures with clear detail.

    Dispatches via core.radius_auth.radius_probe_once(), reusing the same
    pyrad plumbing that powers PingWatch's own RADIUS user-login backend.
    """
    if not _validate_host_quick(host):
        return {"ok": False, "ms": None, "detail": "invalid hostname"}
    if test_level not in _RADIUS_LEVELS:
        test_level = "reachable"
    if not secret:
        return {"ok": False, "ms": None, "detail": "shared secret required"}

    try:
        import pyrad  # noqa: F401
    except ImportError:
        return {"ok": False, "ms": None,
                "detail": "pyrad not installed — run setup wizard"}

    from core.radius_auth import radius_probe_once

    # Build probe credentials
    if test_level == "auth":
        if not user:
            return {"ok": False, "ms": None,
                    "detail": "auth level requires a username"}
        if not password:
            return {"ok": False, "ms": None,
                    "detail": "auth level requires a password"}
        send_user = user
        send_pw   = password
    else:  # reachable
        send_user = f"__pingwatch_probe_{secrets.token_hex(4)}"
        send_pw   = "probe-" + secrets.token_hex(8)

    t0 = time.time()
    try:
        outcome, payload = radius_probe_once(
            host, int(port or 1812), secret,
            send_user, send_pw,
            nas_id=(nas_id or "pingwatch"),
            timeout=timeout, retries=1,
        )
    except Exception as e:
        log_sensors.warning("probe_radius %s:%s: unexpected error: %s", host, port, e)
        return {"ok": False, "ms": None, "detail": f"probe error: {str(e)[:100]}"}

    ms = round((time.time() - t0) * 1000, 1)

    if test_level == "reachable":
        if outcome in ("accept", "reject", "challenge"):
            pretty = {"accept": "Access-Accept",
                      "reject": "Access-Reject",
                      "challenge": "Access-Challenge"}[outcome]
            return {"ok": True, "ms": ms,
                    "detail": f"reachable: server responded ({pretty}) in {ms}ms",
                    "value": outcome}
        # error
        msg = str(payload) if payload else "no response"
        return {"ok": False, "ms": None, "detail": f"reachable: {msg[:120]}"}

    # auth level
    if outcome == "accept":
        # Count reply attributes for a richer detail
        attr_count = 0
        try:
            from core.radius_auth import _decode_attrs
            attr_count = len(_decode_attrs(payload))
        except Exception:
            pass
        attr_suffix = f" ({attr_count} attrs)" if attr_count else ""
        return {"ok": True, "ms": ms,
                "detail": f"auth: Access-Accept{attr_suffix} in {ms}ms",
                "value": "accept"}
    if outcome == "reject":
        return {"ok": False, "ms": ms,
                "detail": "auth: Access-Reject (wrong credentials or account disabled)",
                "value": "reject"}
    if outcome == "challenge":
        return {"ok": False, "ms": ms,
                "detail": "auth: Access-Challenge received (2FA required, not supported for probes)",
                "value": "challenge"}
    # error
    msg = str(payload) if payload else "no response"
    return {"ok": False, "ms": None, "detail": f"auth: {msg[:120]}"}
