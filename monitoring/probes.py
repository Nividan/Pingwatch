"""
probes.py — Sensor probe implementations: ping, tcp, http, snmp.
"""

import re
import ssl
import socket
import subprocess
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
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
        ms = round((time.time() - t0) * 1000, 1)
        s.close()
        return {"ok": True, "ms": ms, "detail": f"Port {port} open ({ms}ms)"}
    except socket.timeout:
        return {"ok": False, "ms": None, "detail": f"Port {port} connection timed out"}
    except ConnectionRefusedError:
        return {"ok": False, "ms": None, "detail": f"Port {port} connection refused"}
    except Exception as e:
        log_sensors.warning("probe_tcp %s:%s: unexpected error: %s", host, port, e)
        return {"ok": False, "ms": None, "detail": str(e)}


def probe_http(url, timeout=8, verify_ssl=True, expected_status=0):
    if not url.startswith("http"):
        url = "http://" + url
    t0 = time.time()
    try:
        ctx = None
        if not verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode    = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "PingWatch/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            ms   = round((time.time() - t0) * 1000, 1)
            code = resp.getcode()
            ssl_note = " [SSL ignored]" if not verify_ssl else ""
            ok = (code == expected_status) if expected_status else (200 <= code < 400)
            return {"ok": ok, "ms": ms,
                    "detail": f"HTTP {code} ({ms}ms){ssl_note}", "code": code}
    except urllib.error.HTTPError as e:
        ms = round((time.time() - t0) * 1000, 1)
        ok = (e.code == expected_status) if expected_status else (e.code < 400)
        return {"ok": ok, "ms": ms, "detail": f"HTTP {e.code}", "code": e.code}
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
            results = socket.getaddrinfo(query, None, family)
            ms = round((time.time() - t0) * 1000, 1)
            addrs = list({r[4][0] for r in results})
            return {"ok": True, "ms": ms,
                    "detail": f"{record_type} {', '.join(addrs[:3])} ({ms}ms)",
                    "value": addrs[0] if addrs else ""}
        except socket.gaierror as e:
            return {"ok": False, "ms": None, "detail": f"DNS error: {e}"}
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

def probe_snmp(host, community, oid, port=161, timeout=5, version="2c"):
    """Run snmpget via subprocess. Requires net-snmp tools installed."""
    if not _OID_RE.match(oid):
        return {"ok": False, "ms": 0, "detail": "Invalid OID format", "value": None}
    ver_flag = f"-v{version}"
    # -On: numeric OIDs in output — avoids MIB translation surprises
    cmd = ["snmpget", ver_flag, "-On", "-c", community,
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


def snmpwalk_interfaces(host, community="public", port=161, timeout=8, version="2c"):
    """
    Walk the ifTable and ifXTable on a live device to discover its interfaces.
    Returns a list of dicts, or None if snmpwalk is not installed.
    Each dict: {index, name, descr, alias, status, speed, speed_raw}
    """
    ver_flag = f"-v{version}"
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if SYS == "Windows" else {}
    target = f"{host}:{port}"

    def _walk(base_oid):
        """Run snmpwalk -OnQ (numeric OIDs, quick/no type) and return {index: value}."""
        cmd = ["snmpwalk", ver_flag, "-c", community,
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
        result = {}
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            oid_str, val = parts
            try:
                idx = int(oid_str.rstrip(".").rsplit(".", 1)[-1])
                result[idx] = val.strip().strip('"')
            except (ValueError, IndexError):
                pass
        return result

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


def probe_tls(host, port=443, timeout=10):
    """Connect via TLS and report certificate expiry in days."""
    import ssl, datetime
    # Strip scheme if user accidentally entered a URL (e.g. https://example.com)
    for _pfx in ("https://", "http://"):
        if host.lower().startswith(_pfx):
            host = host[len(_pfx):].split("/")[0]
            break
    ctx = ssl.create_default_context()
    t0 = time.time()
    conn = None
    try:
        raw = socket.create_connection((host, int(port)), timeout=timeout)
        conn = ctx.wrap_socket(raw, server_hostname=host)
        ms = round((time.time() - t0) * 1000, 1)
        cert = conn.getpeercert()
        conn.close(); conn = None
        not_after = cert.get("notAfter", "")
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
    except Exception as e:
        log_sensors.warning("probe_tls %s:%s: unexpected error: %s", host, port, e)
        return {"ok": False, "ms": None, "detail": str(e)[:80]}
    finally:
        if conn:
            try: conn.close()
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
        s = socket.create_connection((host, int(port)), timeout=timeout)
        s.settimeout(timeout)
        try:
            banner = s.recv(256).decode(errors="replace").strip()
        except Exception:
            banner = ""
        s.close(); s = None
        ms = round((time.time() - t0) * 1000, 1)
        if banner_regex:
            try:
                ok = bool(re.search(banner_regex, banner))
            except re.error as exc:
                return {"ok": False, "ms": ms, "detail": f"Invalid banner regex: {exc}"}
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
        log_sensors.warning("probe_banner %s:%s: unexpected error: %s", host, port, e)
        return {"ok": False, "ms": None, "detail": str(e)[:80]}
    finally:
        if s:
            try: s.close()
            except Exception: pass
