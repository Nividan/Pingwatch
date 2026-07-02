#!/usr/bin/env python3
"""
PingWatch Remote Probe Agent
============================

Runs sensor probes inside a branch network and ships raw results to the
central PingWatch server over outbound HTTPS. Stdlib-only for the core
sensor types (ping/tcp/http/dns/snmp*/tls/http_keyword/banner/smtp/radius);
ssh/sftp need paramiko (see requirements-optional.txt), snmp needs the
net-snmp `snmpget` binary on this host.

Files next to this script:
  config.json        server_url + enrollment token + cert pin (pre-filled
                     by the server's "Download package" button)
  agent_state.json   long-lived probe token + cached sensor config
  spool.jsonl        results buffered while the server is unreachable
  agent.log          rotating log

Design notes:
  • The agent is deliberately dumb: it schedules probes locally and ships
    {ok, ms, value, detail, ts, rate?, snmp_type?} — debounce, thresholds,
    flap detection, and alerting all run server-side.
  • One rhythm: a checkin POST every ~10s carries the result batch and
    doubles as the heartbeat; the response piggybacks config_version and
    pending tasks. When any sensor's ok-state flips, the batch is flushed
    immediately so the server can alert within ~1s.
  • Offline: results spool to disk (bounded, restart-safe) and drain
    oldest-first before live results, so per-sensor timestamps stay
    monotonic — the server backfills history without replaying events.
"""

import collections
import hashlib
import heapq
import http.client
import json
import logging
import os
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging.handlers import RotatingFileHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)   # probes.py + core/ shims live next to us


def _arg(name):
    """Read --name VALUE or --name=VALUE from argv (the supervisor passes these)."""
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


# Managed-update model (v1.4+): the supervisor runs agent.py from a swappable
# releases/<build_id>/ dir and passes --data-dir — the persistent base dir
# where config/state/spool/logs + the bundled ca.pem live. A standalone /
# legacy flat install passes nothing, so DATA_DIR defaults to BASE_DIR
# (backward compatible). Exported BEFORE importing probes so the core/ shims
# (ssl_trust) resolve ca.pem from the base dir, not the swappable release dir.
DATA_DIR = os.path.abspath(_arg("--data-dir")
                           or os.environ.get("PW_AGENT_DATA_DIR") or BASE_DIR)
os.environ["PW_AGENT_DATA_DIR"] = DATA_DIR

import probes  # noqa: E402  (verbatim copy of the server's monitoring/probes.py)

# Keep AGENT_VERSION in lock-step with the server's APP_VERSION at release
# time — the Probes page flags agents whose version differs.
AGENT_VERSION = "1.5"
PROTOCOL_VERSION = 1


def _resolve_build_id():
    bid = _arg("--build-id") or os.environ.get("PW_AGENT_BUILD_ID")
    if bid:
        return bid
    try:   # fallback: BUILD_ID marker the supervisor writes into the release dir
        with open(os.path.join(BASE_DIR, "BUILD_ID"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


BUILD_ID = _resolve_build_id()
# "supervisor-managed" = launched by the supervisor (separate data dir) or a
# supervisor_state.json sits alongside our data. Gates update handling + the
# health beacon, and is reported so the server knows the probe can be updated.
MANAGED = (DATA_DIR != BASE_DIR) or os.path.isfile(
    os.path.join(DATA_DIR, "supervisor_state.json"))

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STATE_PATH  = os.path.join(DATA_DIR, "agent_state.json")
SPOOL_PATH  = os.path.join(DATA_DIR, "spool.jsonl")
LOG_PATH    = os.path.join(DATA_DIR, "agent.log")
HEALTH_PATH         = os.path.join(DATA_DIR, "agent_health.json")
PENDING_SWITCH_PATH = os.path.join(DATA_DIR, "pending_switch.json")
UPDATE_REPORT_PATH  = os.path.join(DATA_DIR, "update_report.json")
RELEASES_DIR        = os.path.join(DATA_DIR, "releases")

_COUNTER_TYPES = {"counter32", "counter64", "counter"}

# ── Logging ───────────────────────────────────────────────────────
log = logging.getLogger("pingwatch-agent")


def _setup_logging():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3,
                             encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    log.propagate = False


# ── Config / state files ──────────────────────────────────────────
def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json_atomic(path, data, private=False):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    if private and os.name == "posix":
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, path)


# ── Transport (outbound HTTPS with optional cert pinning) ─────────
class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that trusts exactly one server certificate,
    identified by its SHA-256 fingerprint — the right model for the
    self-signed certs PingWatch generates by default."""

    def __init__(self, host, port, pin, timeout):
        ctx = ssl._create_unverified_context()
        super().__init__(host, port, timeout=timeout, context=ctx)
        self._pin = pin.lower().replace(":", "")

    def connect(self):
        super().connect()
        der = self.sock.getpeercert(binary_form=True)
        fp = hashlib.sha256(der).hexdigest()
        if fp != self._pin:
            self.close()
            # ASCII only (branch consoles/log viewers are often cp1252), and
            # the FULL observed fingerprint so the operator can paste it
            # straight into config.json if it's legitimate.
            raise ssl.SSLError(
                f"server certificate fingerprint mismatch: pinned {self._pin} "
                f"but the server presented {fp}. If the new cert is "
                f"legitimate (rotated cert or a reverse proxy in front), set "
                f"server_cert_sha256 in config.json to the presented value -- "
                f"or to \"\" to use normal CA validation (the right choice "
                f"for publicly-trusted certificates).")


class ServerClient:
    def __init__(self, cfg):
        u = urllib.parse.urlparse(cfg["server_url"].rstrip("/"))
        self.scheme = u.scheme or "https"
        self.host = u.hostname
        self.port = u.port or (443 if self.scheme == "https" else 80)
        self.pin = (cfg.get("server_cert_sha256") or "").strip()
        # Private-CA support: a PEM file (path relative to the agent folder)
        # used as the trust root instead of the system store — drop your
        # internal CA next to config.json, no root / system-store changes.
        # Precedence: pin > ca file > system CAs. Full CA + hostname
        # verification still applies with a ca file.
        self.ca_file = (cfg.get("server_ca_file") or "").strip()
        if self.ca_file and not os.path.isabs(self.ca_file):
            # ca.pem lives in the persistent base dir (alongside config.json),
            # NOT in the swappable release dir the script runs from.
            self.ca_file = os.path.join(DATA_DIR, self.ca_file)
        if self.ca_file and not os.path.exists(self.ca_file):
            log.error("server_ca_file not found: %s — falling back to system CAs",
                      self.ca_file)
            self.ca_file = ""
        self.timeout = 20

    def request(self, method, path, body=None, token=None):
        """Returns (status_code, parsed_json_or_{}); raises on transport error."""
        if self.scheme == "https":
            if self.pin:
                conn = PinnedHTTPSConnection(self.host, self.port, self.pin,
                                             self.timeout)
            else:
                # ca_file is ADDITIVE: system CAs keep working (publicly
                # trusted proxy certs) and the bundled private CAs / server
                # cert are trusted on top — mirrors the server's
                # apply_trusted_cas() semantics.
                ctx = ssl.create_default_context()
                if self.ca_file:
                    try:
                        ctx.load_verify_locations(cafile=self.ca_file)
                    except Exception as e:
                        log.error("failed to load server_ca_file %s: %s",
                                  self.ca_file, e)
                conn = http.client.HTTPSConnection(
                    self.host, self.port, timeout=self.timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(self.host, self.port,
                                              timeout=self.timeout)
        try:
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            payload = json.dumps(body).encode() if body is not None else None
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                data = {}
            return resp.status, data
        finally:
            conn.close()

    def _open(self):
        if self.scheme == "https":
            if self.pin:
                return PinnedHTTPSConnection(self.host, self.port, self.pin,
                                             self.timeout)
            ctx = ssl.create_default_context()
            if self.ca_file:
                try:
                    ctx.load_verify_locations(cafile=self.ca_file)
                except Exception as e:
                    log.error("failed to load server_ca_file %s: %s",
                              self.ca_file, e)
            return http.client.HTTPSConnection(self.host, self.port,
                                               timeout=self.timeout, context=ctx)
        return http.client.HTTPConnection(self.host, self.port,
                                          timeout=self.timeout)

    def request_raw(self, method, path, token=None, timeout=120):
        """Like request() but returns (status, raw_bytes) — for the binary
        agent package download. Larger timeout than a checkin (MB payload).
        Transport is the same TLS + cert-pin / CA-bundle as every other call."""
        conn = self._open()
        try:
            conn.timeout = timeout
        except Exception:
            pass
        try:
            headers = {}
            if token:
                headers["Authorization"] = "Bearer " + token
            conn.request(method, path, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()


# ── Disk spool (JSONL, ordered, bounded, restart-safe) ────────────
class Spool:
    """Append-only line file of result dicts. Ordering contract: the spool
    always holds results OLDER than anything in memory, and is drained
    fully before live results ship — per-sensor ts stays monotonic and the
    server's dedupe guard never discards the backfill tail."""

    def __init__(self, path, cap):
        self.path = path
        self.cap = max(1000, int(cap or 50000))
        self.lock = threading.Lock()
        self.count = self._count_lines()

    def _count_lines(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    def append(self, results):
        if not results:
            return
        with self.lock:
            with open(self.path, "a", encoding="utf-8") as f:
                for r in results:
                    f.write(json.dumps(r, separators=(",", ":")) + "\n")
            self.count += len(results)
            if self.count > self.cap:
                self._trim_oldest(self.count - self.cap)

    def _trim_oldest(self, n):
        # (lock held) drop the n oldest lines via rewrite + atomic replace
        tmp = self.path + ".tmp"
        kept = 0
        with open(self.path, "r", encoding="utf-8") as src, \
                open(tmp, "w", encoding="utf-8") as dst:
            for i, line in enumerate(src):
                if i >= n:
                    dst.write(line)
                    kept += 1
        os.replace(tmp, self.path)
        self.count = kept
        log.warning("spool over capacity — dropped %d oldest results", n)

    def peek(self, n):
        """Oldest ≤n results without removing them."""
        out = []
        with self.lock:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    for line in f:
                        if len(out) >= n:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            out.append(json.loads(line))
                        except Exception:
                            pass
            except OSError:
                pass
        return out

    def remove_oldest(self, n):
        """Drop the n oldest lines after a successful (acked) send."""
        if n <= 0:
            return
        with self.lock:
            self._trim_oldest_silent(n)

    def _trim_oldest_silent(self, n):
        tmp = self.path + ".tmp"
        kept = 0
        try:
            with open(self.path, "r", encoding="utf-8") as src, \
                    open(tmp, "w", encoding="utf-8") as dst:
                for i, line in enumerate(src):
                    if i >= n:
                        dst.write(line)
                        kept += 1
            os.replace(tmp, self.path)
        except OSError:
            kept = self._count_lines()
        self.count = kept
        if kept == 0:
            try:
                os.remove(self.path)
            except OSError:
                pass


# ── Probe execution ───────────────────────────────────────────────
def _have_module(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


def detect_capabilities():
    return {
        "snmpget":  shutil.which("snmpget") is not None,
        "paramiko": _have_module("paramiko"),
        "pyvmomi":  _have_module("pyVim") or _have_module("pyVmomi"),
    }


def run_probe(cfg):
    """Dispatch one sensor config to the matching probe function — mirrors
    the server's Sensor.probe() but reads a plain dict (credentials arrive
    already decrypted over the pinned TLS channel)."""
    st = cfg.get("stype")
    host = cfg.get("host") or ""
    timeout = int(cfg.get("timeout") or 4)
    port = cfg.get("port")
    try:
        if st == "ping":
            return probes.probe_ping(host, timeout)
        if st == "tcp":
            return probes.probe_tcp(host, port or 80, timeout)
        if st == "http":
            return probes.probe_http(cfg.get("url") or host, timeout,
                                     bool(cfg.get("verify_ssl", True)),
                                     int(cfg.get("http_expected_status") or 0),
                                     cert_check=bool(cfg.get("cert_check")))
        if st == "dns":
            return probes.probe_dns(host, cfg.get("dns_query") or host,
                                    cfg.get("dns_record_type") or "A",
                                    cfg.get("dns_server") or "",
                                    port or 53, timeout)
        if st == "snmp":
            if cfg.get("snmp_oid2") and cfg.get("snmp_pct_mode"):
                return probes.probe_snmp_percent(
                    host, cfg.get("snmp_community") or "public",
                    cfg.get("snmp_oid") or "1.3.6.1.2.1.1.1.0",
                    cfg.get("snmp_oid2"), cfg.get("snmp_pct_mode"),
                    port or 161, timeout,
                    cfg.get("snmp_version") or "2c",
                    cfg.get("snmp_v3"))
            return probes.probe_snmp(host, cfg.get("snmp_community") or "public",
                                     cfg.get("snmp_oid") or "1.3.6.1.2.1.1.1.0",
                                     port or 161, timeout,
                                     cfg.get("snmp_version") or "2c",
                                     cfg.get("snmp_v3"))
        if st == "tls":
            return probes.probe_tls(host, port or 443, timeout)
        if st == "http_keyword":
            return probes.probe_http_keyword(cfg.get("url") or host,
                                             cfg.get("keyword") or "",
                                             timeout,
                                             bool(cfg.get("verify_ssl", True)),
                                             bool(cfg.get("keyword_case")))
        if st == "banner":
            return probes.probe_banner(host, port or 21,
                                       cfg.get("banner_regex") or "", timeout)
        if st == "smtp":
            return probes.probe_smtp(host, port or 25,
                                     cfg.get("smtp_tls") or "none",
                                     cfg.get("smtp_user") or "",
                                     cfg.get("smtp_password") or "",
                                     cfg.get("smtp_from") or "",
                                     cfg.get("smtp_rcpt") or "",
                                     cfg.get("smtp_test_level") or "ehlo",
                                     timeout)
        if st == "ssh":
            if not _have_module("paramiko"):
                return {"ok": False, "ms": None,
                        "detail": "capability missing on probe: paramiko", "value": None}
            return probes.probe_ssh(host, port or 22,
                                    cfg.get("ssh_user") or "",
                                    cfg.get("ssh_password") or "",
                                    cfg.get("ssh_private_key") or "",
                                    cfg.get("ssh_auth_type") or "password",
                                    cfg.get("ssh_test_level") or "banner",
                                    timeout)
        if st == "sftp":
            if not _have_module("paramiko"):
                return {"ok": False, "ms": None,
                        "detail": "capability missing on probe: paramiko", "value": None}
            return probes.probe_sftp(host, port or 22,
                                     cfg.get("sftp_user") or "",
                                     cfg.get("sftp_password") or "",
                                     cfg.get("sftp_private_key") or "",
                                     cfg.get("sftp_auth_type") or "password",
                                     cfg.get("sftp_test_level") or "open",
                                     cfg.get("sftp_remote_path") or "",
                                     cfg.get("sftp_expected_sha256") or "",
                                     timeout)
        if st == "radius":
            return probes.probe_radius(host, port or 1812,
                                       cfg.get("radius_secret") or "",
                                       cfg.get("radius_test_level") or "reachable",
                                       cfg.get("radius_username") or "",
                                       cfg.get("radius_password") or "",
                                       cfg.get("radius_nas_id") or "pingwatch",
                                       timeout)
        if st == "vmware":
            # The vmware/ module ships in the package; pyvmomi is the only
            # branch-host install. Distinct details so the operator knows
            # whether to pip-install or re-download the package.
            if not (_have_module("pyVim") or _have_module("pyVmomi")):
                return {"ok": False, "ms": None,
                        "detail": "capability missing on probe: pyvmomi "
                                  "(pip install pyvmomi, or re-run the "
                                  "installer)", "value": None}
            try:
                from vmware import vmware_probe
            except Exception:
                return {"ok": False, "ms": None,
                        "detail": "vmware module missing on probe — download "
                                  "a fresh agent package", "value": None}
            return vmware_probe(host, cfg.get("vmware_user") or "",
                                cfg.get("vmware_password") or "",
                                cfg.get("vmware_vm_id") or "",
                                cfg.get("vmware_metric") or "",
                                port=port or 443,
                                verify_ssl=bool(cfg.get("verify_ssl", True)),
                                timeout=timeout,
                                disk_path=cfg.get("vmware_disk_path") or "")
        return {"ok": False, "ms": None, "detail": f"Unknown sensor type: {st}",
                "value": None}
    except Exception as e:
        return {"ok": False, "ms": None,
                "detail": f"probe crashed: {type(e).__name__}", "value": None}


# ── ARP MAC (for discovery tasks) — mirror of subnet_discovery._get_mac ──
import re as _re
_MAC_RE = _re.compile(r"([0-9a-f]{2}[:-]){5}[0-9a-f]{2}", _re.IGNORECASE)


def _get_mac(ip):
    try:
        win = platform.system() == "Windows"
        cmd = ["arp", "-a", ip] if win else ["arp", "-n", ip]
        kw = {}
        if win:
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=2, **kw)
        m = _MAC_RE.search(r.stdout or "")
        if m:
            return m.group(0).lower().replace("-", ":")
    except Exception:
        pass
    return ""


# ── The agent ─────────────────────────────────────────────────────
class Agent:
    def __init__(self):
        self.cfg = load_json(CONFIG_PATH)
        if not self.cfg.get("server_url"):
            log.error("config.json missing or has no server_url — cannot start")
            sys.exit(2)
        self.state = load_json(STATE_PATH)
        self.client = ServerClient(self.cfg)
        self.checkin_interval = max(5, int(self.cfg.get("checkin_interval") or 10))
        self.spool = Spool(SPOOL_PATH, self.cfg.get("spool_max") or 50000)

        self.sensors = {}                 # "did/sid" → cfg dict
        self.config_version = int(self.state.get("config_version") or 0)
        for s in self.state.get("sensors") or []:
            self.sensors[f"{s['did']}/{s['sid']}"] = s

        self.buf = collections.deque()    # live results (newer than spool)
        self.buf_lock = threading.Lock()
        self.flush_now = threading.Event()
        self.stop = threading.Event()

        self.executor = ThreadPoolExecutor(max_workers=16,
                                           thread_name_prefix="pwa-probe")
        self.last_ok = {}                 # key → bool (ok-flip detector)
        self.counter_prev = {}            # key → (raw_value, ts)
        self.inflight = set()             # keys with a probe currently running
        self.inflight_lock = threading.Lock()

        self.heap = []                    # (due_monotonic, seq, key)
        self.heap_seq = 0
        self.heap_lock = threading.Lock()
        self.heap_wake = threading.Event()

        self.tasks = collections.deque()  # pending task dicts from checkin
        self.cancelled_tasks = set()
        self.task_wake = threading.Event()

        self.checkin_count = 0
        self.proto_block_logged = 0
        self._last_terr = ("", 0.0)   # (error key, ts) — transport-warning throttle
        # Managed-update bookkeeping: consecutive good checkins feed the
        # supervisor's probation beacon; _restart_requested makes run() exit so
        # the supervisor can swap + probate a staged release.
        self.consecutive_good = 0
        self._restart_requested = False

    # ── Scheduler ────────────────────────────────────────────────
    def _schedule(self, key, delay):
        with self.heap_lock:
            self.heap_seq += 1
            heapq.heappush(self.heap, (time.monotonic() + delay,
                                       self.heap_seq, key))
        self.heap_wake.set()

    def _scheduler_loop(self):
        while not self.stop.is_set():
            try:
                with self.heap_lock:
                    now = time.monotonic()
                    while self.heap and self.heap[0][0] <= now:
                        _, _, key = heapq.heappop(self.heap)
                        if key not in self.sensors:
                            continue   # removed by a config sync
                        with self.inflight_lock:
                            if key in self.inflight:
                                # previous cycle still running (hung target);
                                # skip — it reschedules when it finishes
                                continue
                            self.inflight.add(key)
                        self.executor.submit(self._probe_cycle, key)
                    sleep_for = (self.heap[0][0] - now) if self.heap else 30.0
                self.heap_wake.wait(timeout=min(max(sleep_for, 0.05), 1.0))
                self.heap_wake.clear()
            except Exception as e:
                log.error("scheduler loop error: %s", e)
                time.sleep(1.0)

    def _probe_cycle(self, key):
        try:
            cfg = self.sensors.get(key)
            if not cfg:
                return
            result = run_probe(cfg)
            ts = time.time()
            rec = {
                "did": cfg["did"], "sid": cfg["sid"],
                "ok": bool(result.get("ok")),
                "ms": result.get("ms"),
                "detail": str(result.get("detail") or "")[:512],
                "value": result.get("value"),
                "ts": round(ts, 3),
            }
            if result.get("snmp_type"):
                rec["snmp_type"] = result["snmp_type"]
                rec["rate"] = self._counter_rate(key, result, ts)
            with self.buf_lock:
                self.buf.append(rec)
            prev_ok = self.last_ok.get(key)
            self.last_ok[key] = rec["ok"]
            if prev_ok is not None and prev_ok != rec["ok"]:
                # state flip → ship immediately so the server can alert fast
                self.flush_now.set()
        except Exception as e:
            log.error("probe cycle error for %s: %s", key, e)
        finally:
            with self.inflight_lock:
                self.inflight.discard(key)
            cfg = self.sensors.get(key)
            if cfg and not self.stop.is_set():
                self._schedule(key, max(1, int(cfg.get("interval") or 5)))

    def _counter_rate(self, key, result, ts):
        """Agent-side SNMP counter rate — same wrap/sanity rules the server
        applies to local probes (core/state.py). The agent owns counter
        continuity: server-side deltas over arrival times would be garbage."""
        stype = str(result.get("snmp_type") or "").lower()
        if stype not in _COUNTER_TYPES or result.get("value") is None:
            return None
        try:
            cur = int(result["value"])
        except (TypeError, ValueError):
            self.counter_prev.pop(key, None)
            return None
        prev = self.counter_prev.get(key)
        self.counter_prev[key] = (cur, ts)
        if not prev:
            return None
        elapsed = ts - prev[1]
        delta = cur - prev[0]
        if elapsed < 1.0:
            return None
        if delta < 0:
            if stype == "counter32":
                delta += 2 ** 32
            else:
                return None   # counter64 reset — rate unknowable
        rate = delta / elapsed
        if rate > 1.25e11:    # > 8 Tbps — garbage from reset/clock anomaly
            return None
        return rate

    def _log_transport(self, where, e, extra=""):
        """WARN once per distinct transport error, then stay quiet for 60s —
        a deterministic failure (bad pin, refused connection) otherwise
        floods the log with an identical line every checkin."""
        key = f"{where}:{type(e).__name__}:{e}"
        now = time.time()
        if key == self._last_terr[0] and now - self._last_terr[1] < 60:
            return
        self._last_terr = (key, now)
        log.warning("%s failed (transport): %s%s (identical errors suppressed for 60s)",
                    where, e, extra)

    # ── Enrollment / auth ────────────────────────────────────────
    def _enroll(self):
        tok = (self.cfg.get("enrollment_token") or "").strip()
        if not tok:
            return False
        consumed = self.state.get("consumed_enroll_sha256") or ""
        if consumed == hashlib.sha256(tok.encode()).hexdigest():
            return False   # this token was already used — need a fresh one
        body = {
            "enrollment_token": tok,
            "agent_version": AGENT_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "os": f"{platform.system()} {platform.release()}",
            "capabilities": detect_capabilities(),
        }
        try:
            status, data = self.client.request("POST", "/api/agent/enroll", body)
        except Exception as e:
            self._log_transport("enroll", e)
            return False
        if status == 200 and data.get("probe_token"):
            self.state["probe_token"] = data["probe_token"]
            self.state["probe_id"] = data.get("probe_id") or self.cfg.get("probe_id")
            self.state["consumed_enroll_sha256"] = hashlib.sha256(tok.encode()).hexdigest()
            self.state["config_version"] = 0   # force a config pull
            save_json_atomic(STATE_PATH, self.state, private=True)
            log.info("enrolled successfully as probe %s", self.state["probe_id"])
            return True
        if status == 409:
            log.error("enrollment rejected: server protocol %s ≠ agent protocol %s "
                      "— update the agent package", data.get("server_protocol"),
                      PROTOCOL_VERSION)
        else:
            log.warning("enrollment rejected (HTTP %s) — token used/expired? "
                        "Re-enroll from the server's Probes page.", status)
        return False

    # ── Config sync ──────────────────────────────────────────────
    def _sync_config(self):
        try:
            status, data = self.client.request(
                "GET", "/api/agent/config", token=self.state.get("probe_token"))
        except Exception as e:
            self._log_transport("config sync", e)
            return
        if status != 200 or not data.get("ok"):
            log.warning("config sync rejected (HTTP %s)", status)
            return
        sensors = data.get("sensors") or []
        new = {f"{s['did']}/{s['sid']}": s for s in sensors
               if s.get("did") and s.get("sid")}
        added   = [k for k in new if k not in self.sensors]
        removed = [k for k in self.sensors if k not in new]
        self.sensors = new
        self.config_version = int(data.get("config_version") or 0)
        self.checkin_interval = max(5, int(data.get("checkin_interval")
                                           or self.checkin_interval))
        for k in removed:
            self.last_ok.pop(k, None)
            self.counter_prev.pop(k, None)
        for k in added:
            self._schedule(k, 0.5)
        self.state["config_version"] = self.config_version
        self.state["sensors"] = sensors
        save_json_atomic(STATE_PATH, self.state, private=True)
        log.info("config synced: v%s — %d sensors (%d added, %d removed)",
                 self.config_version, len(new), len(added), len(removed))

    # ── Checkin ──────────────────────────────────────────────────
    def _drain_batch(self):
        """Next batch honoring the ordering contract: spool before live."""
        if self.spool.count > 0:
            batch = self.spool.peek(4000)
            return batch, "spool"
        with self.buf_lock:
            batch = []
            while self.buf and len(batch) < 4000:
                batch.append(self.buf.popleft())
        return batch, "live"

    def _overflow_to_spool(self):
        """Server unreachable: bound memory by moving live results to disk
        (appended AFTER existing spool lines — chronological order holds)."""
        with self.buf_lock:
            if len(self.buf) < 2000:
                return
            moved = list(self.buf)
            self.buf.clear()
        self.spool.append(moved)

    # ── Managed-update support ───────────────────────────────────
    def _flush_buffer_to_spool(self):
        """Move all in-memory results to the on-disk spool. Called before an
        update handoff so nothing buffered is lost across the restart/rollback
        (the spool lives in the base dir, outside swappable releases)."""
        with self.buf_lock:
            moved = list(self.buf)
            self.buf.clear()
        if moved:
            self.spool.append(moved)

    def _write_health(self):
        """Beacon the supervisor polls during probation: how many consecutive
        good checkins this build has achieved (commit at GOOD_CHECKINS)."""
        if not MANAGED:
            return
        try:
            save_json_atomic(HEALTH_PATH, {
                "build_id": BUILD_ID,
                "consecutive_good": self.consecutive_good,
                "ts": time.time(),
            }, private=True)
        except Exception:
            pass

    def request_restart(self):
        """Exit the run loop so the supervisor takes over (used after staging an
        update). Daemon threads die with the process; the supervisor sees the
        clean exit + pending_switch.json and performs the swap."""
        self._restart_requested = True
        self.stop.set()
        self.flush_now.set()

    def _upload_pending_report(self):
        """On startup, ship any update outcome the supervisor left behind
        (success one-liner or rollback log tail), then clear it. Runs whether
        we're the freshly-committed build or the reverted previous one."""
        if not MANAGED or not os.path.exists(UPDATE_REPORT_PATH):
            return
        rep = load_json(UPDATE_REPORT_PATH)
        if not rep:
            try:
                os.remove(UPDATE_REPORT_PATH)
            except OSError:
                pass
            return
        try:
            status, _ = self.client.request(
                "POST", "/api/agent/update-report", rep,
                token=self.state.get("probe_token"))
            if status == 200:
                os.remove(UPDATE_REPORT_PATH)
                log.info("update report (%s) uploaded", rep.get("outcome"))
            else:
                log.warning("update-report upload HTTP %s — will retry", status)
        except Exception as e:
            log.warning("update-report upload failed: %s — will retry",
                        type(e).__name__)

    def _checkin_once(self):
        token = self.state.get("probe_token")
        if not token:
            if not self._enroll():
                return False
            token = self.state.get("probe_token")
        batch, origin = self._drain_batch()
        self.checkin_count += 1
        body = {
            "protocol_version": PROTOCOL_VERSION,
            "agent_version": AGENT_VERSION,
            "build_id": BUILD_ID,
            "config_version": self.config_version,
            "results": batch,
            "tasks": TASK_RUNNER.progress_payload() if TASK_RUNNER else [],
        }
        if self.checkin_count == 1 or self.checkin_count % 30 == 0:
            body["status"] = {
                "os": f"{platform.system()} {platform.release()}",
                "capabilities": detect_capabilities(),
                "spool_depth": self.spool.count + len(self.buf),
                "agent_now": time.time(),
                # Supervisor-managed = this probe can take remote updates; the
                # server gates update campaigns on this and badges legacy probes.
                "supervisor": MANAGED,
                "build_id": BUILD_ID,
            }
        try:
            status, data = self.client.request("POST", "/api/agent/checkin",
                                               body, token=token)
        except Exception as e:
            self._log_transport("checkin", e,
                                extra=f" -- spooling {len(batch)} results")
            if origin == "live" and batch:
                self.spool.append(batch)
            self._overflow_to_spool()
            return False
        if status == 401:
            log.warning("checkin unauthorized — token revoked? trying re-enroll")
            if origin == "live" and batch:
                self.spool.append(batch)
            self.state.pop("probe_token", None)
            save_json_atomic(STATE_PATH, self.state, private=True)
            return False
        if status == 409:
            if time.time() - self.proto_block_logged > 300:
                log.error("checkin rejected: protocol mismatch (server %s, agent %s)"
                          " — update this agent package from the Probes page",
                          data.get("server_protocol"), PROTOCOL_VERSION)
                self.proto_block_logged = time.time()
            if origin == "live" and batch:
                self.spool.append(batch)
            return False
        if status != 200 or not data.get("ok"):
            log.warning("checkin rejected (HTTP %s)", status)
            if origin == "live" and batch:
                self.spool.append(batch)
            return False
        # acked — spooled lines may now be deleted
        if origin == "spool":
            self.spool.remove_oldest(len(batch))
        for t in data.get("tasks") or []:
            self.tasks.append(t)
            self.task_wake.set()
        for tid in data.get("cancelled_tasks") or []:
            self.cancelled_tasks.add(int(tid))
        srv_cfg_v = int(data.get("config_version") or 0)
        if srv_cfg_v != self.config_version:
            self._sync_config()
        return True

    def _prewarm_vmware(self):
        """Log into and warm each distinct vCenter once, before its sensors
        first fire — prevents the false-DOWN burst after an agent restart
        (cold perfManager/inventory → 'VM not found' / 'metric not
        available'). One thread per vCenter so the first-probe head start
        covers them in parallel."""
        specs = {}
        for cfg in list(self.sensors.values()):
            if cfg.get("stype") != "vmware":
                continue
            host = cfg.get("host") or ""
            user = cfg.get("vmware_user") or ""
            pw = cfg.get("vmware_password") or ""
            if not (host and user and pw):
                continue
            specs[(host, user, int(cfg.get("port") or 443))] = (
                host, user, pw, int(cfg.get("port") or 443),
                bool(cfg.get("verify_ssl", True)))
        if not specs:
            return
        try:
            from vmware.client import prewarm_session
        except Exception:
            return                      # vmware module / pyvmomi not present

        def _warm_one(host, user, pw, port, vssl):
            ok = prewarm_session(host, user, pw, port=port, verify_ssl=vssl)
            log.info("vCenter session prewarm %s: %s:%s",
                     "ok" if ok else "failed", host, port)
        for host, user, pw, port, vssl in specs.values():
            threading.Thread(target=_warm_one,
                             args=(host, user, pw, port, vssl),
                             daemon=True, name="pwa-vmw-warm").start()

    # ── Main loop ────────────────────────────────────────────────
    def run(self):
        log.info("PingWatch agent %s starting (probe=%s, server=%s, verify=%s)",
                 AGENT_VERSION, self.cfg.get("probe_name") or self.cfg.get("probe_id"),
                 self.cfg.get("server_url"),
                 "pin" if self.client.pin
                 else f"ca-file:{os.path.basename(self.client.ca_file)}"
                 if self.client.ca_file else "system-ca")
        threading.Thread(target=self._scheduler_loop, daemon=True,
                         name="pwa-sched").start()
        # vCenter sessions are cached in-memory — cold after a restart, and
        # a fresh SmartConnect login (5-20s) outlives most sensor timeouts.
        # Warm them in the background and give vmware sensors a small head
        # start so their first probe finds a cached session.
        threading.Thread(target=self._prewarm_vmware, daemon=True,
                         name="pwa-vmw-warm").start()
        for key in list(self.sensors):
            _first = 12.0 if (self.sensors[key].get("stype") == "vmware") else 1.0
            self._schedule(key, _first)

        last_full_sync = 0.0
        while not self.stop.is_set():
            # Ship (and retry) any update outcome the supervisor left for us.
            self._upload_pending_report()
            ok = False
            try:
                ok = self._checkin_once()
            except Exception as e:
                log.error("checkin crashed: %s", e)
            # drain the spool fast after a reconnect: keep posting while
            # backlog remains and the server is answering
            while ok and self.spool.count > 0 and not self.stop.is_set():
                time.sleep(0.5)
                try:
                    ok = self._checkin_once()
                except Exception:
                    break
            # Probation beacon: count consecutive good checkins for the
            # supervisor; a single failure resets the streak.
            if ok:
                self.consecutive_good += 1
            else:
                self.consecutive_good = 0
            self._write_health()
            if ok and time.time() - last_full_sync > 3600:
                self._sync_config()        # hourly belt-and-braces re-pull
                last_full_sync = time.time()
            self.flush_now.wait(timeout=self.checkin_interval)
            self.flush_now.clear()


# ── Task runner (IPAM / discovery sweeps) ─────────────────────────
class TaskRunner:
    def __init__(self, agent):
        self.agent = agent
        self.progress = {}        # task_id → {"state","progress"} for checkin
        self.progress_lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="pwa-tasks").start()

    def progress_payload(self):
        with self.progress_lock:
            out = [{"task_id": tid, **info} for tid, info in self.progress.items()]
            # errors/final states are reported once, then dropped
            self.progress = {tid: info for tid, info in self.progress.items()
                             if info.get("state") == "running"}
        return out

    def _set(self, tid, state, progress=None, error=None):
        with self.progress_lock:
            info = {"state": state}
            if progress is not None:
                info["progress"] = progress
            if error:
                info["error"] = error
            self.progress[tid] = info

    def _loop(self):
        while not self.agent.stop.is_set():
            self.agent.task_wake.wait(timeout=5.0)
            self.agent.task_wake.clear()
            while self.agent.tasks:
                task = self.agent.tasks.popleft()
                try:
                    self._run_task(task)
                except Exception as e:
                    log.error("task %s crashed: %s", task.get("task_id"), e)
                    self._set(task.get("task_id"), "error",
                              error=f"task crashed: {type(e).__name__}")

    def _cancelled(self, tid):
        return tid in self.agent.cancelled_tasks

    def _upload_chunk(self, tid, rows, done, error=""):
        body = {"rows": rows, "done": done}
        if error:
            body["error"] = error
        status, _ = self.agent.client.request(
            "POST", f"/api/agent/tasks/{tid}/result", body,
            token=self.agent.state.get("probe_token"))
        return status == 200

    def _run_task(self, task):
        import ipaddress
        tid = int(task.get("task_id"))
        ttype = task.get("task_type") or ""
        payload = task.get("payload") or {}
        if ttype == "agent_update":
            self._run_agent_update(tid, payload)
            return
        if ttype == "device_scan":
            self._run_device_scan(tid, payload)
            return
        if ttype == "snmp_interfaces":
            self._run_snmp_interfaces(tid, payload)
            return
        if ttype == "snmp_discover":
            self._run_snmp_discover(tid, payload)
            return
        cidr = str(payload.get("cidr") or "")
        mode = str(payload.get("mode") or "ping")
        if ttype not in ("ipam_scan", "discovery_scan") or not cidr:
            self._set(tid, "error", error=f"unsupported task: {ttype}")
            return
        log.info("task %d: %s %s (mode=%s)", tid, ttype, cidr, mode)
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            self._set(tid, "error", error="invalid CIDR")
            return
        if net.num_addresses > 65536:
            self._set(tid, "error", error="CIDR too large (max /16)")
            return
        if net.prefixlen >= 31:
            ips = [str(ip) for ip in net] if net.prefixlen == 31 \
                else [str(net.network_address)]
        else:
            ips = [str(ip) for ip in net.hosts()]

        checked = [0]
        alive = []
        self._set(tid, "running", progress={"phase": "pinging",
                                            "total": len(ips), "checked": 0,
                                            "alive": 0})
        with ThreadPoolExecutor(max_workers=32,
                                thread_name_prefix="pwa-sweep") as ex:
            futs = {ex.submit(probes.probe_ping, ip, 2): ip for ip in ips}
            for fut in as_completed(futs):
                if self._cancelled(tid):
                    for f in futs:
                        f.cancel()
                    break
                checked[0] += 1
                try:
                    r = fut.result()
                    if r and r.get("ok"):
                        alive.append((futs[fut], r.get("ms")))
                except Exception:
                    pass
                if checked[0] % 64 == 0:
                    self._set(tid, "running",
                              progress={"phase": "pinging", "total": len(ips),
                                        "checked": checked[0],
                                        "alive": len(alive)})
        if self._cancelled(tid):
            log.info("task %d cancelled during sweep", tid)
            self._set(tid, "error", error="cancelled")
            return

        targets = payload.get("targets") if mode == "full" else None
        rows = []
        enriched = 0
        self._set(tid, "running", progress={"phase": "enriching",
                                            "total": len(ips),
                                            "checked": checked[0],
                                            "alive": len(alive),
                                            "enrich_total": len(alive),
                                            "enriched": 0})
        for ip, ms in alive:
            if self._cancelled(tid):
                self._set(tid, "error", error="cancelled")
                return
            rows.append(self._enrich(ip, ms, targets))
            enriched += 1
            if enriched % 16 == 0:
                self._set(tid, "running",
                          progress={"phase": "enriching",
                                    "enrich_total": len(alive),
                                    "enriched": enriched,
                                    "alive": len(alive),
                                    "total": len(ips), "checked": checked[0]})
            if len(rows) >= 1500:
                if not self._upload_chunk(tid, rows, done=False):
                    self._set(tid, "error", error="result upload failed")
                    return
                rows = []
        if self._upload_chunk(tid, rows, done=True):
            log.info("task %d complete: %d alive hosts", tid, len(alive))
            self._set(tid, "done")
        else:
            self._set(tid, "error", error="final upload failed")

    def _run_device_scan(self, tid, payload):
        """Single-host service scan (Devices → Scan button on the server).

        Mirrors central's /api/devices/{did}/scan fanout: one thread per
        target from the server's scan_ports setting (shipped in the
        payload), 8s global deadline, only responding services reported.
        The server handler long-polls for this result, so upload promptly.
        """
        host = str(payload.get("host") or "")
        targets = payload.get("targets") or []
        if not host or not isinstance(targets, list) or not targets:
            self._set(tid, "error", error="bad device_scan payload")
            return
        log.info("task %d: device_scan %s (%d targets)",
                 tid, host, len(targets))
        self._set(tid, "running", progress={"phase": "scanning",
                                            "total": len(targets)})
        rows = []
        lock = threading.Lock()

        def _scan_one(t):
            stype = str(t.get("stype") or "")
            port = t.get("port")
            try:
                tout = int(t.get("tout", 2) or 2)
            except (TypeError, ValueError):
                tout = 2
            try:
                if stype == "ping":
                    r = probes.probe_ping(host, timeout=tout)
                elif stype == "tcp":
                    r = probes.probe_tcp(host, port, timeout=tout)
                elif stype == "http":
                    url = f"http://{host}" if port == 80 \
                        else f"http://{host}:{port}"
                    r = probes.probe_http(url, timeout=tout, verify_ssl=False)
                elif stype == "tls":
                    r = probes.probe_tls(host, port, timeout=tout)
                elif stype == "banner":
                    r = probes.probe_banner(host, port, timeout=tout)
                else:
                    return
            except Exception:
                return
            if r and r.get("ok"):
                with lock:
                    rows.append({"stype": stype,
                                 "name": str(t.get("name") or stype)[:64],
                                 "port": port, "ms": r.get("ms"),
                                 "detail": str(r.get("detail") or "")[:512]})

        threads = [threading.Thread(target=_scan_one, args=(t,), daemon=True)
                   for t in targets[:64]]
        deadline = time.monotonic() + 8     # same budget as central
        for th in threads:
            th.start()
        for th in threads:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                th.join(timeout=remaining)
        if self._cancelled(tid):
            log.info("task %d cancelled (device_scan)", tid)
            self._set(tid, "error", error="cancelled")
            return
        if self._upload_chunk(tid, rows, done=True):
            log.info("task %d complete: %d services on %s",
                     tid, len(rows), host)
            self._set(tid, "done")
        else:
            self._set(tid, "error", error="result upload failed")

    def _run_snmp_interfaces(self, tid, payload):
        """SNMP interface discovery (Add/Edit Sensor → Discover Interfaces on a
        probe-bound device). Walks ifTable/ifXTable locally — the same
        probes.snmpwalk_interfaces central runs — so discovery egresses from
        the branch. The server long-polls for this result, so report promptly.
        Errors are uploaded (not just set locally) so the waiting handler gets
        an immediate reason instead of a timeout."""
        host = str(payload.get("host") or "")
        comm = str(payload.get("community") or "public")
        try:
            port = int(payload.get("port") or 161)
        except (TypeError, ValueError):
            port = 161
        ver = str(payload.get("version") or "2c")
        v3 = payload.get("v3_creds")
        if not isinstance(v3, dict):
            v3 = None
        if not host:
            self._upload_chunk(tid, [], done=True, error="bad snmp_interfaces payload")
            self._set(tid, "error", error="bad snmp_interfaces payload")
            return
        log.info("task %d: snmp_interfaces %s (v%s)", tid, host, ver)
        self._set(tid, "running", progress={"phase": "snmpwalk"})
        try:
            ifaces = probes.snmpwalk_interfaces(host, comm, port, timeout=10,
                                                version=ver, v3_creds=v3)
        except Exception as e:
            msg = f"snmpwalk failed: {type(e).__name__}"
            self._upload_chunk(tid, [], done=True, error=msg)
            self._set(tid, "error", error=msg)
            return
        if ifaces is None:
            msg = "snmpwalk not found on probe — install net-snmp"
            self._upload_chunk(tid, [], done=True, error=msg)
            self._set(tid, "error", error=msg)
            return
        if self._cancelled(tid):
            self._set(tid, "error", error="cancelled")
            return
        if self._upload_chunk(tid, ifaces, done=True):
            log.info("task %d complete: %d interfaces on %s",
                     tid, len(ifaces), host)
            self._set(tid, "done")
        else:
            self._set(tid, "error", error="result upload failed")

    def _run_snmp_discover(self, tid, payload):
        """SNMP template discovery (Add Sensor → Discover with template on a
        probe-bound device). Runs the same probes.snmp_discover_template central
        runs, so scalar gets + table walks egress from the branch. Uploads the
        candidate rows; errors are uploaded (not just set) so the waiting handler
        gets an immediate reason instead of a timeout."""
        host = str(payload.get("host") or "")
        comm = str(payload.get("community") or "public")
        try:
            port = int(payload.get("port") or 161)
        except (TypeError, ValueError):
            port = 161
        ver = str(payload.get("version") or "2c")
        v3 = payload.get("v3_creds")
        if not isinstance(v3, dict):
            v3 = None
        items = payload.get("items")
        if not host or not isinstance(items, list) or not items:
            self._upload_chunk(tid, [], done=True, error="bad snmp_discover payload")
            self._set(tid, "error", error="bad snmp_discover payload")
            return
        try:
            op_to = int(payload.get("op_timeout") or 10)
        except (TypeError, ValueError):
            op_to = 10
        op_to = max(2, min(15, op_to))   # clamp; scans pass a short per-OID timeout
        log.info("task %d: snmp_discover %s (%d items, v%s)",
                 tid, host, len(items), ver)
        self._set(tid, "running", progress={"phase": "snmp"})
        try:
            cands = probes.snmp_discover_template(host, items, comm, port,
                                                  timeout=op_to, version=ver, v3_creds=v3)
        except Exception as e:
            msg = f"snmp discovery failed: {type(e).__name__}"
            self._upload_chunk(tid, [], done=True, error=msg)
            self._set(tid, "error", error=msg)
            return
        if cands is None:
            msg = "snmpwalk not found on probe — install net-snmp"
            self._upload_chunk(tid, [], done=True, error=msg)
            self._set(tid, "error", error=msg)
            return
        if self._cancelled(tid):
            self._set(tid, "error", error="cancelled")
            return
        if self._upload_chunk(tid, cands, done=True):
            log.info("task %d complete: %d candidates on %s",
                     tid, len(cands), host)
            self._set(tid, "done")
        else:
            self._set(tid, "error", error="result upload failed")

    def _run_agent_update(self, tid, payload):
        """Managed self-update: download the target release, verify its
        checksum, stage it into releases/<build_id>/, then hand off to the
        supervisor (which swaps + probates + rolls back). The terminal outcome
        reaches the server via /api/agent/update-report, not this task — this
        task just tracks download/stage and ends at 'staged'."""
        if not MANAGED:
            self._set(tid, "error", error="probe is not supervisor-managed")
            return
        target = str(payload.get("build_id") or "")
        sha = str(payload.get("package_sha256") or "").lower()
        try:
            window = int(payload.get("probation_window") or 120)
        except (TypeError, ValueError):
            window = 120
        if not target or len(sha) != 64:
            self._set(tid, "error", error="missing build_id/package_sha256")
            return
        if target == BUILD_ID:
            self._set(tid, "done")   # already running the target build
            return
        log.info("agent_update task %d → build %s", tid, target)
        self._set(tid, "running", progress=10)            # downloading
        try:
            status, data = self.agent.client.request_raw(
                "GET", "/api/agent/package?build=" + urllib.parse.quote(target),
                token=self.agent.state.get("probe_token"))
        except Exception as e:
            self._set(tid, "error", error=f"download failed: {type(e).__name__}")
            return
        if status != 200 or not data:
            self._set(tid, "error", error=f"download HTTP {status}")
            return
        got = hashlib.sha256(data).hexdigest()
        if got != sha:
            log.error("agent_update %d checksum mismatch (want %s got %s)",
                      tid, sha[:12], got[:12])
            self._set(tid, "error", error="checksum mismatch")
            return
        self._set(tid, "running", progress=55)            # staged
        rel_dir = os.path.join(RELEASES_DIR, target)
        try:
            self._extract_release(data, rel_dir)
        except Exception as e:
            self._set(tid, "error", error=f"extract failed: {type(e).__name__}")
            return
        try:
            with open(os.path.join(rel_dir, "BUILD_ID"), "w", encoding="utf-8") as f:
                f.write(target + "\n")
        except Exception:
            pass
        # Flush buffered results to the spool so the restart/rollback loses none.
        self.agent._flush_buffer_to_spool()
        # Hand off to the supervisor: it sees the clean exit + this file and
        # performs the swap + probation.
        save_json_atomic(PENDING_SWITCH_PATH, {
            "target_release":   target,
            "package_sha256":   sha,
            "probation_window": window,
            "campaign_id":      payload.get("campaign_id"),
            "attempt_id":       payload.get("attempt_id"),
        }, private=True)
        self._set(tid, "done", progress=100)
        log.info("agent_update %d staged %s — restarting into supervisor",
                 tid, target)
        self.agent.request_restart()

    def _extract_release(self, data, rel_dir):
        """Extract the payload zip into rel_dir atomically (temp dir → replace),
        with a zip-slip guard so a crafted package can't escape releases/."""
        import io as _io
        import zipfile
        os.makedirs(os.path.dirname(rel_dir), exist_ok=True)
        tmp = rel_dir + ".tmp"
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        base = os.path.abspath(tmp)
        with zipfile.ZipFile(_io.BytesIO(data)) as zf:
            for m in zf.namelist():
                dest = os.path.abspath(os.path.join(tmp, m))
                if dest != base and not dest.startswith(base + os.sep):
                    raise ValueError("unsafe path in package: " + m)
            zf.extractall(tmp)
        if os.path.isdir(rel_dir):
            shutil.rmtree(rel_dir, ignore_errors=True)
        os.replace(tmp, rel_dir)

    def _enrich(self, ip, ms, targets):
        hostname = ""
        try:
            hostname = socket.gethostbyaddr(ip)[0]
        except Exception:
            pass
        ports = []
        if targets:
            t0 = time.monotonic()

            def _probe_target(t):
                if time.monotonic() - t0 > 6.0:
                    return
                stype = t.get("stype", "")
                port = t.get("port")
                tout = min(int(t.get("tout", 2) or 2), 2)
                try:
                    if stype == "tcp":
                        r = probes.probe_tcp(ip, port, timeout=tout)
                    elif stype == "http":
                        r = probes.probe_http(f"http://{ip}:{port}",
                                              timeout=tout, verify_ssl=False)
                    elif stype == "tls":
                        r = probes.probe_tcp(ip, port, timeout=tout)
                    elif stype == "banner":
                        r = probes.probe_banner(ip, port, timeout=tout)
                    else:
                        return
                except Exception:
                    return
                if r and r.get("ok"):
                    ports.append({"stype": stype, "name": t.get("name", ""),
                                  "port": port,
                                  "detail": (r.get("detail") or "")[:120]})

            with ThreadPoolExecutor(max_workers=8) as ex:
                list(ex.map(_probe_target,
                            [t for t in targets if t.get("port")]))
        row = {"ip": ip, "hostname": hostname, "mac": _get_mac(ip),
               "ports": sorted(ports, key=lambda x: x.get("port") or 0)}
        if ms is not None:
            row["ms"] = ms
        return row


TASK_RUNNER = None


def main():
    global TASK_RUNNER
    _setup_logging()
    agent = Agent()
    TASK_RUNNER = TaskRunner(agent)
    try:
        agent.run()
    except KeyboardInterrupt:
        log.info("agent stopped (Ctrl+C)")
        agent.stop.set()


if __name__ == "__main__":
    main()
