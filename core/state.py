"""
state.py — Data model: Sensor, Device, MonitorState.
"""

import collections
import concurrent.futures
import datetime
import heapq
import json
import queue
import threading
import time


class _SensorScheduler:
    """Single background thread that fires sensor probes at the right time.

    Instead of one thread (or Timer) per sensor sleeping between probes,
    all pending 'run-at' times live in a min-heap.  The scheduler thread
    wakes up only when the next probe is due and submits it to the executor.
    Thread cost: exactly 1, regardless of sensor count.
    """
    def __init__(self, executor, run_fn):
        self._heap       = []        # (run_at, seq, did, sid)
        self._seq        = 0
        self._lock       = threading.RLock()
        self._wake       = threading.Event()
        self._executor   = executor
        self._run_fn     = run_fn
        self._tombstones = set()     # {(did, sid)} — skip these when popped
        self._live_count = 0         # updated by schedule() + cancel(); used for prune threshold
        t = threading.Thread(target=self._loop, daemon=True, name='pw-sched')
        t.start()

    def schedule(self, did, sid, delay):
        """Schedule a probe for (did, sid) to run after `delay` seconds."""
        with self._lock:
            self._seq += 1
            heapq.heappush(self._heap, (time.monotonic() + delay, self._seq, did, sid))
            # Clear any tombstone — sensor has been re-scheduled (add-back case)
            self._tombstones.discard((did, sid))
        self._wake.set()

    def cancel(self, did, sid):
        """Mark (did, sid) entries in the heap as stale. They'll be skipped when popped."""
        with self._lock:
            self._tombstones.add((did, sid))

    def _prune(self, live_count: int):
        """Rebuild the heap without stale/tombstoned entries. Called by _loop when
        the heap has grown to more than 2x the live sensor count, to reclaim memory
        from long-interval sensors whose tombstones have been sitting in the heap."""
        with self._lock:
            if not self._tombstones:
                return
            self._heap = [e for e in self._heap if (e[2], e[3]) not in self._tombstones]
            heapq.heapify(self._heap)
            self._tombstones.clear()

    def _loop(self):
        while True:
            with self._lock:
                now = time.monotonic()
                while self._heap and self._heap[0][0] <= now:
                    _, _, did, sid = heapq.heappop(self._heap)
                    if (did, sid) in self._tombstones:
                        continue   # sensor was deleted/stopped — skip
                    self._executor.submit(self._run_fn, did, sid)
                    now = time.monotonic()
                sleep_for = (self._heap[0][0] - now) if self._heap else 60.0
                heap_size = len(self._heap)
                tomb_size = len(self._tombstones)
            # Prune outside the inner hot path when heap has drifted; the scheduler
            # owner passes live_count via cancel() accounting, but a heuristic
            # threshold on tombstone count keeps this simple and safe.
            if tomb_size > 100 and heap_size > 2 * max(tomb_size, 1):
                self._prune(heap_size - tomb_size)
            self._wake.wait(timeout=min(sleep_for, 1.0))
            self._wake.clear()

from monitoring.probes import probe_ping, probe_tcp, probe_http, probe_snmp, probe_dns
from monitoring.probes import probe_tls, probe_http_keyword, probe_banner, probe_smtp, probe_ssh, probe_sftp, probe_radius
from .settings import get as _cfg
from .logger import log_sensors

_COUNTER_TYPES  = {"counter32", "counter64", "counter"}
_BYTE_UNITS     = {"bytes"}
_COUNT_UNITS    = {"errors", "packets"}  # counter OIDs that count events, not bytes

def _fmt_bps(bps):
    """Format bytes/sec as a human-readable network rate (bits/sec)."""
    bits = bps * 8
    if bits >= 1_000_000_000: return f"{bits/1_000_000_000:.2f} Gbps"
    if bits >= 1_000_000:     return f"{bits/1_000_000:.2f} Mbps"
    if bits >= 1_000:         return f"{bits/1_000:.1f} Kbps"
    return f"{bits:.0f} bps"

def _fmt_rate(rate, unit):
    """Format a per-second counter rate based on its semantic unit."""
    if unit in _BYTE_UNITS or unit == "":
        return _fmt_bps(rate)          # bytes/sec → Kbps/Mbps/Gbps
    if unit == "errors":
        return f"{rate:.2f} err/s" if rate < 10 else f"{rate:.1f} err/s"
    if unit == "packets":
        return f"{rate:.2f} pkt/s" if rate < 10 else f"{rate:.1f} pkt/s"
    # Fallback for any other counter unit
    return f"{rate:.2f}/s"


class Sensor:
    MAX = 80

    def __init__(self, device_id, sensor_id, name, stype, host,
                 port=None, url=None, interval=5, timeout=4,
                 verify_ssl=True,
                 snmp_community="public", snmp_oid="1.3.6.1.2.1.1.1.0",
                 snmp_version="2c",
                 fail_after=2, recover_after=1,
                 warn_ms=None, crit_ms=None, loss_warn_pct=0, loss_crit_pct=0,
                 keyword="", keyword_case=False, banner_regex="",
                 alerts_muted=False, snmp_unit="",
                 vmware_user="", vmware_password="",
                 vmware_vm_id="", vmware_vm_name="", vmware_metric="",
                 vmware_disk_path="",
                 smtp_tls="none", smtp_user="", smtp_password="",
                 smtp_from="", smtp_rcpt="", smtp_test_level="ehlo",
                 ssh_user="", ssh_password="", ssh_private_key="",
                 ssh_auth_type="password", ssh_test_level="banner",
                 sftp_user="", sftp_password="", sftp_private_key="",
                 sftp_auth_type="password", sftp_test_level="open",
                 sftp_remote_path="", sftp_expected_sha256="",
                 radius_secret="", radius_test_level="reachable",
                 radius_username="", radius_password="", radius_nas_id=""):
        self.device_id      = device_id
        self.sensor_id      = sensor_id
        self.name           = name
        self.stype          = stype
        self.host           = host
        self.port           = port
        self.url            = url
        self.interval       = interval
        self.timeout        = timeout
        self.verify_ssl     = verify_ssl
        self.snmp_community = snmp_community
        self.snmp_oid       = snmp_oid
        self.snmp_version   = snmp_version
        self.dns_query             = ""
        self.dns_record_type       = "A"
        self.dns_server            = ""
        self.http_expected_status  = 0
        # Debounce — requires N consecutive failures before flap event fires.
        # Default 2 avoids noise from transient single-probe drops (common with ICMP).
        # Alert rules add a second debounce layer (trigger_count / duration).
        self.fail_after    = int(fail_after or 2)
        self.recover_after = int(recover_after or 1)
        # Thresholds
        self.warn_ms       = warn_ms
        self.crit_ms       = crit_ms
        self.loss_warn_pct = int(loss_warn_pct or 0)
        self.loss_crit_pct = int(loss_crit_pct or 0)
        # New probe type fields
        self.keyword       = keyword or ""
        self.keyword_case  = bool(keyword_case)
        self.banner_regex  = banner_regex or ""
        self.alerts_muted  = bool(alerts_muted)
        self.snmp_unit     = snmp_unit or ""   # semantic OID unit: "bytes","errors","packets","%","count", etc.
        self.host_override = False   # True = host was manually set; don't sync from device
        # VMware fields
        self.vmware_user     = vmware_user or ""
        self.vmware_password = vmware_password or ""   # Fernet-encrypted ciphertext
        self.vmware_vm_id    = vmware_vm_id or ""      # VM managed-object ID (e.g. "vm-123")
        self.vmware_vm_name  = vmware_vm_name or ""    # VM display name (e.g. "dc0.bslab.local")
        self.vmware_metric   = vmware_metric or ""     # metric key from VM_METRICS
        self.vmware_disk_path = vmware_disk_path or ""  # for disk_used_pct: e.g. "C:\" or "/"
        # SMTP fields — auth'd probe; password is Fernet-encrypted at rest
        self.smtp_tls        = smtp_tls or "none"       # none | starttls | ssl
        self.smtp_user       = smtp_user or ""
        self.smtp_password   = smtp_password or ""      # Fernet ciphertext
        self.smtp_from       = smtp_from or ""          # envelope sender for MAIL FROM round-trip
        self.smtp_rcpt       = smtp_rcpt or ""          # recipient; probe issues RSET — no DATA
        self.smtp_test_level = smtp_test_level or "ehlo"  # connect | ehlo | starttls | auth | mailfrom
        # SSH fields — auth'd probe; password + private key both Fernet-encrypted
        self.ssh_user        = ssh_user or ""
        self.ssh_password    = ssh_password or ""       # Fernet ciphertext
        self.ssh_private_key = ssh_private_key or ""    # Fernet ciphertext (multi-line PEM)
        self.ssh_auth_type   = ssh_auth_type or "password"  # password | key
        self.ssh_test_level  = ssh_test_level or "banner"   # connect | banner | auth
        # SFTP fields — reuses SSH pattern; read-only probe (open / list / stat / checksum)
        self.sftp_user            = sftp_user or ""
        self.sftp_password        = sftp_password or ""        # Fernet ciphertext
        self.sftp_private_key     = sftp_private_key or ""     # Fernet ciphertext
        self.sftp_auth_type       = sftp_auth_type or "password"
        self.sftp_test_level      = sftp_test_level or "open"
        self.sftp_remote_path     = sftp_remote_path or ""     # dir (list) or file (stat/checksum)
        self.sftp_expected_sha256 = sftp_expected_sha256 or "" # hex; checksum level only
        # RADIUS fields — shared secret + optional user creds, both Fernet-encrypted
        self.radius_secret      = radius_secret or ""      # Fernet ciphertext (shared secret)
        self.radius_test_level  = radius_test_level or "reachable"  # reachable | auth
        self.radius_username    = radius_username or ""
        self.radius_password    = radius_password or ""    # Fernet ciphertext (user password, auth level)
        self.radius_nas_id      = radius_nas_id or ""      # NAS-Identifier attribute; defaults to "pingwatch" at probe time
        # SNMP counter rate tracking (not persisted)
        self._snmp_prev    = None   # previous raw counter value (int)
        self._snmp_prev_ts = None   # timestamp of previous counter read
        self._last_rate    = None   # float bytes/sec (for "bytes") or events/sec (for "errors"/"packets")
        # Runtime state (not persisted)
        self._consec_fail     = 0
        self._consec_ok       = 0
        self._alerted_down    = False
        self._email_sent_down = False   # True only after the delayed DOWN email was actually sent
        self._recovery_pending            = False   # keep sending recovery events to alert engine
        self._threshold_state             = "ok"
        self._consec_threshold            = 0       # consecutive probes in current threshold state
        self._threshold_recovery_pending  = False   # keep sending threshold_ok events to engine
        self._threshold_triggered_ts      = None    # wall-clock time threshold first fired (for duration)
        self._down_since_ts               = None    # wall-clock time sensor first went down (for duration)
        self.history               = collections.deque(maxlen=self.MAX)
        self.thr_history           = collections.deque(maxlen=self.MAX)
        self.total          = 0
        self.success        = 0
        self.last_ms        = None
        self.last_detail    = ""
        self.last_value     = None
        self.alive          = None
        self.running        = False
        self._stopped       = threading.Event()   # set when _run_once exits without rescheduling
        # Alert profile resolver cache (PRTG-style state-trigger system)
        self._resolved_profile_id  = None
        self._resolved_profile_ver = -1
        # Anomaly detection (per-sensor opt-in learned baseline)
        self.anomaly_enabled       = 0          # 0/1 persisted
        self.anomaly_sensitivity   = 2          # 1 strict / 2 balanced / 3 relaxed
        self.anomaly_min_samples   = 50         # bootstrap guard
        self._anom_mean            = None       # EWMA mean (ms)
        self._anom_var             = None       # EWMA variance
        self._anom_count           = 0          # samples contributing to baseline
        self._anom_enabled_since   = None       # epoch; for cold-start time window
        self._anom_consec_fails    = 0          # debounce counter
        self._anom_state           = "ok"       # "ok" | "warn"
        self._anom_triggered_ts    = None       # epoch of current streak start
        self._anom_dirty           = False      # needs DB checkpoint
        self._anom_caused_warn     = False      # true when anomaly flipped _threshold_state for this probe

    @property
    def _valid_history(self):
        return [x for x in self.history if x is not None]

    @property
    def loss_pct(self):
        return 0 if not self.total else round((1 - self.success / self.total) * 100)

    @property
    def avg_ms(self):
        v = self._valid_history
        return round(sum(v) / len(v), 1) if v else None

    @property
    def min_ms(self):
        v = self._valid_history
        return round(min(v), 1) if v else None

    @property
    def max_ms(self):
        v = self._valid_history
        return round(max(v), 1) if v else None

    def probe(self):
        if self.stype == "ping": return probe_ping(self.host, self.timeout)
        if self.stype == "tcp":  return probe_tcp(self.host, self.port or 80, self.timeout)
        if self.stype == "http": return probe_http(self.url or self.host, self.timeout,
                                                   self.verify_ssl, self.http_expected_status)
        if self.stype == "dns":  return probe_dns(self.host, self.dns_query or self.host,
                                                   self.dns_record_type, self.dns_server,
                                                   self.port or 53, self.timeout)
        if self.stype == "snmp": return probe_snmp(self.host, self.snmp_community,
                                                    self.snmp_oid, self.port or 161,
                                                    self.timeout, self.snmp_version)
        if self.stype == "tls":  return probe_tls(self.host, self.port or 443, self.timeout)
        if self.stype == "http_keyword": return probe_http_keyword(
                                                    self.url or self.host, self.keyword,
                                                    self.timeout, self.verify_ssl, self.keyword_case)
        if self.stype == "banner": return probe_banner(
                                                    self.host, self.port or 21,
                                                    self.banner_regex, self.timeout)
        if self.stype == "vmware":
            from vmware import vmware_probe
            from db.backups import decrypt_pw
            return vmware_probe(self.host, self.vmware_user,
                                decrypt_pw(self.vmware_password),
                                self.vmware_vm_id, self.vmware_metric,
                                port=self.port or 443,
                                verify_ssl=self.verify_ssl,
                                timeout=self.timeout,
                                disk_path=self.vmware_disk_path)
        if self.stype == "smtp":
            from db.backups import decrypt_pw
            return probe_smtp(self.host, self.port or 25, self.smtp_tls,
                              self.smtp_user, decrypt_pw(self.smtp_password),
                              self.smtp_from, self.smtp_rcpt,
                              self.smtp_test_level, self.timeout)
        if self.stype == "ssh":
            from db.backups import decrypt_pw
            return probe_ssh(self.host, self.port or 22,
                             self.ssh_user,
                             decrypt_pw(self.ssh_password),
                             decrypt_pw(self.ssh_private_key),
                             self.ssh_auth_type,
                             self.ssh_test_level, self.timeout)
        if self.stype == "sftp":
            from db.backups import decrypt_pw
            return probe_sftp(self.host, self.port or 22,
                              self.sftp_user,
                              decrypt_pw(self.sftp_password),
                              decrypt_pw(self.sftp_private_key),
                              self.sftp_auth_type,
                              self.sftp_test_level,
                              self.sftp_remote_path,
                              self.sftp_expected_sha256,
                              self.timeout)
        if self.stype == "radius":
            from db.backups import decrypt_pw
            return probe_radius(self.host, self.port or 1812,
                                decrypt_pw(self.radius_secret),
                                self.radius_test_level,
                                self.radius_username,
                                decrypt_pw(self.radius_password),
                                self.radius_nas_id or "pingwatch",
                                self.timeout)
        return {"ok": False, "ms": None, "detail": "Unknown sensor type"}

    def to_dict(self):
        return {
            "device_id":      self.device_id,
            "sensor_id":      self.sensor_id,
            "name":           self.name,
            "stype":          self.stype,
            "host":           self.host,
            "port":           self.port,
            "url":            self.url,
            "interval":       self.interval,
            "timeout":        self.timeout,
            "verify_ssl":     self.verify_ssl,
            "snmp_community": self.snmp_community,
            "snmp_oid":       self.snmp_oid,
            "snmp_version":   self.snmp_version,
            "dns_query":             self.dns_query,
            "dns_record_type":       self.dns_record_type,
            "dns_server":            self.dns_server,
            "http_expected_status":  self.http_expected_status,
            "fail_after":            self.fail_after,
            "recover_after":         self.recover_after,
            "warn_ms":               self.warn_ms,
            "crit_ms":               self.crit_ms,
            "loss_warn_pct":         self.loss_warn_pct,
            "loss_crit_pct":         self.loss_crit_pct,
            "keyword":               self.keyword,
            "keyword_case":          self.keyword_case,
            "banner_regex":          self.banner_regex,
            "alerts_muted":          self.alerts_muted,
            "host_override":         self.host_override,
            "snmp_unit":             self.snmp_unit,
            "vmware_user":           self.vmware_user,
            "vmware_vm_id":          self.vmware_vm_id,
            "vmware_vm_name":        self.vmware_vm_name,
            "vmware_metric":         self.vmware_metric,
            "vmware_disk_path":      self.vmware_disk_path,
            "has_vmware_password":   bool(self.vmware_password),
            "smtp_tls":              self.smtp_tls,
            "smtp_user":             self.smtp_user,
            "smtp_from":             self.smtp_from,
            "smtp_rcpt":             self.smtp_rcpt,
            "smtp_test_level":       self.smtp_test_level,
            "has_smtp_password":     bool(self.smtp_password),
            "ssh_user":              self.ssh_user,
            "ssh_auth_type":         self.ssh_auth_type,
            "ssh_test_level":        self.ssh_test_level,
            "has_ssh_password":      bool(self.ssh_password),
            "has_ssh_private_key":   bool(self.ssh_private_key),
            "sftp_user":             self.sftp_user,
            "sftp_auth_type":        self.sftp_auth_type,
            "sftp_test_level":       self.sftp_test_level,
            "sftp_remote_path":      self.sftp_remote_path,
            "sftp_expected_sha256":  self.sftp_expected_sha256,
            "has_sftp_password":     bool(self.sftp_password),
            "has_sftp_private_key":  bool(self.sftp_private_key),
            "radius_test_level":     self.radius_test_level,
            "radius_username":       self.radius_username,
            "radius_nas_id":         self.radius_nas_id,
            "has_radius_secret":     bool(self.radius_secret),
            "has_radius_password":   bool(self.radius_password),
            "threshold_state":       self._threshold_state,
            "anomaly_enabled":       int(getattr(self, "anomaly_enabled", 0) or 0),
            "anomaly_sensitivity":   int(getattr(self, "anomaly_sensitivity", 2) or 2),
            "anomaly_min_samples":   int(getattr(self, "anomaly_min_samples", 50) or 50),
            "anomaly_mean_ms":       self._anom_mean,
            "anomaly_stddev_ms":     (self._anom_var ** 0.5) if self._anom_var else None,
            "anomaly_sample_count":  int(self._anom_count or 0),
            "alive":          self.alive,
            "running":        self.running,
            "last_ms":        self.last_ms,
            "last_detail":    self.last_detail,
            "last_value":     self.last_value,
            "last_rate":      round(self._last_rate, 4) if self._last_rate is not None else None,
            "avg_ms":         self.avg_ms,
            "min_ms":         self.min_ms,
            "max_ms":         self.max_ms,
            "loss_pct":       self.loss_pct,
            "total":          self.total,
            "history":        list(self.history),
            "thr_history":    list(self.thr_history),
        }


class Device:
    def __init__(self, device_id, name, host, group="Default Group"):
        self.device_id   = device_id
        self.name        = name
        self.host        = host
        self.group       = group
        self.webhook_url  = ""
        self.alerts_muted = False
        self.secondary_ips = []
        # Bulk-import linkage — populated by the import path when the device
        # was created from a file. NULL for manual / Discovery additions.
        # Format: "<source>:<native_id>" e.g. "prtg:2001".
        self.external_id  = None
        # Auto-Discovery origin breadcrumb. Both 0/"" unless the device was
        # created by monitoring/auto_discovery.py.
        self.discovered_at        = 0.0
        self.discovered_from_cidr = ""
        self.sensors      = {}
        self._sid_ctr     = 0
        # Device-level default credentials (pre-fill for new sensors)
        self.snmp_community_default  = ""
        self.snmp_version_default    = ""
        self.vmware_user_default     = ""
        self.vmware_password_default = ""
        # Cached status string; invalidated (set to None) whenever a sensor's
        # alive / _threshold_state / running / alerts_muted changes, or when
        # a sensor is added/removed. Recomputed on next read.
        self._cached_status = None

    def next_sid(self):
        self._sid_ctr += 1
        return f"s{self._sid_ctr}"

    def invalidate_status(self):
        """Clear cached status so the next read recomputes from live sensor state."""
        self._cached_status = None

    @property
    def status(self):
        if self._cached_status is not None:
            return self._cached_status
        active = [s for s in self.sensors.values() if not s.alerts_muted and s.running]
        vals = [s.alive for s in active]
        if not vals or all(v is None for v in vals):
            result = "unknown"
        elif any(v is False for v in vals):
            result = "down"
        elif any(s._threshold_state == "crit" for s in active):
            result = "down"
        elif any(s._threshold_state == "warn" for s in active):
            result = "warn"
        else:
            result = "up"
        self._cached_status = result
        return result

    def to_dict(self):
        return {
            "device_id":    self.device_id,
            "name":         self.name,
            "host":         self.host,
            "secondary_ips": self.secondary_ips or [],
            "group":        self.group,
            "webhook_url":  self.webhook_url,
            "alerts_muted": self.alerts_muted,
            "status":       self.status,
            "sensors":      [s.to_dict() for s in self.sensors.values()],
            "snmp_community_default":      self.snmp_community_default,
            "snmp_version_default":        self.snmp_version_default,
            "vmware_user_default":         self.vmware_user_default,
            "has_vmware_password_default":  bool(self.vmware_password_default),
            # Origin breadcrumb (Auto-Discovery). 0/"" for manually-added devices.
            "discovered_at":         float(getattr(self, "discovered_at", 0) or 0),
            "discovered_from_cidr":  getattr(self, "discovered_from_cidr", "") or "",
        }


def _send_webhook(url: str, payload: dict):
    """POST a flap event to a webhook URL. Runs in a daemon thread."""
    from core.logger import log
    import ipaddress as _ip, socket as _sock, urllib.parse as _up

    # ── Scheme check: only http/https are allowed ─────────────────────
    _parsed = _up.urlparse(url)
    if _parsed.scheme not in ('http', 'https'):
        log.warning(f"Webhook blocked — disallowed scheme '{_parsed.scheme}': {url}")
        return

    # ── SSRF guard: resolve hostname and reject internal addresses ────
    # Fail-closed: if DNS resolution or IP parsing fails, abort rather
    # than falling through to the request.
    try:
        _host = _parsed.hostname or ""
        if not _host:
            log.warning(f"Webhook blocked — no hostname: {url}")
            return
        _addr = _sock.gethostbyname(_host)
        _obj  = _ip.ip_address(_addr)
        if _obj.is_loopback or _obj.is_private or _obj.is_link_local or _obj.is_reserved:
            log.warning(f"Webhook blocked — private/reserved address {_addr}: {url}")
            return
    except Exception as _e:
        log.warning(f"Webhook blocked — DNS/IP resolution failed for {url}: {_e}")
        return

    try:
        import json as _json
        import urllib.request
        data = _json.dumps(payload).encode()
        req  = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as _resp:
            _resp.read(1024)  # consume response to release socket
    except Exception as e:
        log.warning(f"Webhook failed ({url}): {e}")


class MonitorState:
    def __init__(self):
        self._lock            = threading.RLock()
        self.devices          = {}
        self._did_ctr         = 0
        self._sse             = []
        self._sse_registered  = {}   # Queue → monotonic timestamp of subscribe()
        self._executor  = concurrent.futures.ThreadPoolExecutor(
            max_workers=64, thread_name_prefix='pw-sensor'
        )
        self._scheduler = _SensorScheduler(self._executor, self._run_once)
        # Bumped on every alert profile/template write — sensors compare this
        # to their cached _resolved_profile_ver to know when to re-resolve.
        self._profile_cache_ver = 0

    def _next_did(self):
        self._did_ctr += 1
        return f"d{self._did_ctr}"

    def add_device(self, name, host, group="Default Group"):
        with self._lock:
            did = self._next_did()
            self.devices[did] = Device(did, name, host, group)
        return did

    def remove_device(self, did):
        with self._lock:
            dev = self.devices.get(did)
            if not dev: return False
            sids = list(dev.sensors.keys())
            for s in dev.sensors.values(): s.running = False
            dev.invalidate_status()
            del self.devices[did]
        for sid in sids:
            self._scheduler.cancel(did, sid)
        return True

    def get_device(self, did):
        with self._lock:
            return self.devices.get(did)

    def add_sensor(self, did, name, stype, host=None,
                   port=None, url=None, interval=5, timeout=4,
                   verify_ssl=True, snmp_community="public",
                   snmp_oid="1.3.6.1.2.1.1.1.0", snmp_version="2c",
                   fail_after=2, recover_after=1,
                   warn_ms=None, crit_ms=None, loss_warn_pct=0, loss_crit_pct=0,
                   keyword="", keyword_case=False, banner_regex="", snmp_unit="",
                   vmware_user="", vmware_password="",
                   vmware_vm_id="", vmware_vm_name="", vmware_metric="",
                   vmware_disk_path="",
                   smtp_tls="none", smtp_user="", smtp_password="",
                   smtp_from="", smtp_rcpt="", smtp_test_level="ehlo",
                   ssh_user="", ssh_password="", ssh_private_key="",
                   ssh_auth_type="password", ssh_test_level="banner",
                   sftp_user="", sftp_password="", sftp_private_key="",
                   sftp_auth_type="password", sftp_test_level="open",
                   sftp_remote_path="", sftp_expected_sha256="",
                   radius_secret="", radius_test_level="reachable",
                   radius_username="", radius_password="", radius_nas_id=""):
        with self._lock:
            dev = self.devices.get(did)
            if not dev: return None
            sid   = dev.next_sid()
            thost = host or dev.host
            s = Sensor(did, sid, name, stype, thost,
                       port=port, url=url, interval=interval, timeout=timeout,
                       verify_ssl=verify_ssl, snmp_community=snmp_community,
                       snmp_oid=snmp_oid, snmp_version=snmp_version,
                       fail_after=fail_after, recover_after=recover_after,
                       warn_ms=warn_ms, crit_ms=crit_ms,
                       loss_warn_pct=loss_warn_pct, loss_crit_pct=loss_crit_pct,
                       keyword=keyword, keyword_case=keyword_case, banner_regex=banner_regex,
                       snmp_unit=snmp_unit,
                       vmware_user=vmware_user, vmware_password=vmware_password,
                       vmware_vm_id=vmware_vm_id, vmware_vm_name=vmware_vm_name,
                       vmware_metric=vmware_metric, vmware_disk_path=vmware_disk_path,
                       smtp_tls=smtp_tls, smtp_user=smtp_user, smtp_password=smtp_password,
                       smtp_from=smtp_from, smtp_rcpt=smtp_rcpt,
                       smtp_test_level=smtp_test_level,
                       ssh_user=ssh_user, ssh_password=ssh_password,
                       ssh_private_key=ssh_private_key,
                       ssh_auth_type=ssh_auth_type, ssh_test_level=ssh_test_level,
                       sftp_user=sftp_user, sftp_password=sftp_password,
                       sftp_private_key=sftp_private_key,
                       sftp_auth_type=sftp_auth_type,
                       sftp_test_level=sftp_test_level,
                       sftp_remote_path=sftp_remote_path,
                       sftp_expected_sha256=sftp_expected_sha256,
                       radius_secret=radius_secret,
                       radius_test_level=radius_test_level,
                       radius_username=radius_username,
                       radius_password=radius_password,
                       radius_nas_id=radius_nas_id)
            dev.sensors[sid] = s
            s.host_override = bool(host)  # True only when caller explicitly passed a host
        return sid

    def update_sensor(self, did, sid, **kwargs):
        """Update sensor config. Restarts the sensor loop if it was running."""
        with self._lock:
            dev = self.devices.get(did)
            if not dev: return False
            s = dev.sensors.get(sid)
            if not s: return False
            was_running = s.running
            s.running = False
            dev.invalidate_status()
            s._stopped.clear()
        s._stopped.wait(timeout=3.0)   # wait for _run_once to exit (≤0.5s normally)
        if not s._stopped.is_set():
            log_sensors.warning(f"Sensor {did}/{sid} did not stop within 3s — forcing config update")
        with self._lock:
            dev = self.devices.get(did)
            if not dev: return False
            s = dev.sensors.get(sid)
            if not s: return False
            editable = ["name", "stype", "host", "port", "url", "interval", "timeout",
                        "verify_ssl", "snmp_community", "snmp_oid", "snmp_version",
                        "dns_query", "dns_record_type", "dns_server",
                        "http_expected_status",
                        "warn_ms", "crit_ms", "loss_warn_pct", "loss_crit_pct",
                        "keyword", "keyword_case", "banner_regex", "alerts_muted",
                        "snmp_unit",
                        "vmware_user", "vmware_password",
                        "vmware_vm_id", "vmware_vm_name", "vmware_metric",
                        "vmware_disk_path",
                        "smtp_tls", "smtp_user", "smtp_password",
                        "smtp_from", "smtp_rcpt", "smtp_test_level",
                        "ssh_user", "ssh_password", "ssh_private_key",
                        "ssh_auth_type", "ssh_test_level",
                        "sftp_user", "sftp_password", "sftp_private_key",
                        "sftp_auth_type", "sftp_test_level",
                        "sftp_remote_path", "sftp_expected_sha256",
                        "radius_secret", "radius_test_level",
                        "radius_username", "radius_password", "radius_nas_id",
                        "anomaly_enabled", "anomaly_sensitivity", "anomaly_min_samples"]
            _anom_enabled_before = int(getattr(s, "anomaly_enabled", 0) or 0)
            _anom_sens_before    = int(getattr(s, "anomaly_sensitivity", 2) or 2)
            # Numeric fields must never land as "" on the Sensor — PG's INTEGER
            # columns reject empty strings at save time. Treat "" as "not provided".
            _nullable_int_fields = {"port", "warn_ms", "crit_ms"}
            _int_fields = {"interval", "timeout", "http_expected_status",
                           "fail_after", "recover_after",
                           "loss_warn_pct", "loss_crit_pct",
                           "anomaly_enabled", "anomaly_sensitivity", "anomaly_min_samples"}
            for k, v in kwargs.items():
                if k not in editable or v is None:
                    continue
                if k in _nullable_int_fields and v == "":
                    v = None
                elif k in _int_fields and v == "":
                    continue   # keep existing value
                if k == 'host':
                    if v:  # Non-empty: manually overridden — unlink from device
                        s.host_override = True
                    else:  # Cleared: re-link to device host
                        v = dev.host
                        s.host_override = False
                setattr(s, k, v)
            _anom_enabled_after = int(getattr(s, "anomaly_enabled", 0) or 0)
            _anom_sens_after    = int(getattr(s, "anomaly_sensitivity", 2) or 2)
            # Reset baseline when user enables anomaly (off→on) or changes sensitivity —
            # stale variance should not leak across configuration changes.
            _needs_reset = False
            if _anom_enabled_after and not _anom_enabled_before:
                _needs_reset = True
            elif _anom_enabled_after and (_anom_sens_after != _anom_sens_before):
                _needs_reset = True
            if _needs_reset:
                from monitoring.anomaly import reset_baseline as _anom_reset
                _anom_reset(s)
                _reset_did, _reset_sid = did, sid
            else:
                _reset_did = _reset_sid = None
            dev.invalidate_status()
        if _reset_did:
            try:
                from db import db_reset_anomaly_baseline
                db_reset_anomaly_baseline(_reset_did, _reset_sid)
            except Exception:
                pass
        if was_running:
            self.start_sensor(did, sid)
        return True

    def remove_sensor(self, did, sid):
        with self._lock:
            dev = self.devices.get(did)
            if not dev: return False
            s = dev.sensors.get(sid)
            if not s: return False
            s.running = False
            dev.invalidate_status()
            del dev.sensors[sid]
        self._scheduler.cancel(did, sid)
        return True

    def start_sensor(self, did, sid):
        with self._lock:
            dev = self.devices.get(did)
            if not dev: return
            s = dev.sensors.get(sid)
        if not s or s.running: return
        s.running = True
        dev.invalidate_status()
        s._stopped.clear()
        self._executor.submit(self._run_once, did, sid)

    def stop_sensor(self, did, sid):
        with self._lock:
            dev = self.devices.get(did)
            if dev:
                s = dev.sensors.get(sid)
                if s:
                    s.running = False
                    dev.invalidate_status()
        self._scheduler.cancel(did, sid)
                    # Scheduler entry will be ignored — _run_once checks s.running at entry

    def start_device(self, did):
        with self._lock:
            dev = self.devices.get(did)
            sids = list(dev.sensors) if dev else []
        for sid in sids:
            self.start_sensor(did, sid)

    def stop_device(self, did):
        from db import _logs_enqueue
        from db.events import db_resolve_flaps_by_sensor
        with self._lock:
            dev = self.devices.get(did)
            sids = list(dev.sensors) if dev else []
        for sid in sids:
            self.stop_sensor(did, sid)
        # Broadcast updated state so UI immediately reflects stopped/paused status
        with self._lock:
            dev = self.devices.get(did)
            sensor_dicts = [s.to_dict() for s in dev.sensors.values()] if dev else []
            new_status = dev.status if dev else "unknown"
        batch = [("sensor", sd) for sd in sensor_dicts]
        batch.append(("device_status", {"did": did, "status": new_status}))
        self._broadcast_batch(batch)
        # Auto-resolve active flap events — device is intentionally stopped
        for sid in sids:
            _logs_enqueue(lambda d=did, s_=sid: db_resolve_flaps_by_sensor(d, s_))

    def start_all(self):
        for did in list(self.devices):
            self.start_device(did)

    def stop_all(self):
        for did in list(self.devices):
            self.stop_device(did)

    def _run_once(self, did, sid):
        # Import here to avoid circular import at module load time
        from db import db_log_err, db_log_flap, db_buffer_sample, _db_enqueue
        from db.events import db_auto_resolve_flap

        with self._lock:
            dev = self.devices.get(did)
            s   = dev.sensors.get(sid) if dev else None
        if not s or not s.running:
            if s: s._stopped.set()
            return

        if s.total == 0:
            self._broadcast("log", {"did": did, "sid": sid,
                                     "msg": f"[START] {s.name} on {s.host}", "type": "info"})

        dev = self.devices.get(did)   # re-fetch (unprotected, Device is stable)
        result = s.probe()
        s.total += 1
        _ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _ts_float = time.time()
        _muted = s.alerts_muted or dev.alerts_muted

        # ── Log sample to DB (non-blocking) ──
        _ok_cap  = result["ok"]
        _ms_cap  = result["ms"]
        _val_cap = str(result.get("value", "")) if result.get("value") is not None else None
        _ts_f_cap = _ts_float
        _did_cap, _sid_cap = did, sid
        db_buffer_sample(_did_cap, _sid_cap, _ok_cap, _ms_cap, _val_cap, _ts_f_cap)

        _log_msg = result.get("detail", "")   # default; overridden in ok branch for SNMP
        if result["ok"]:
            s.success    += 1
            if s.alive is not True:
                dev.invalidate_status()
            s.alive       = True
            s.last_ms     = result["ms"]
            s.last_detail = result["detail"]
            # ── SNMP counter rate calculation ─────────────────
            _raw_val = result.get("value")
            _stype   = result.get("snmp_type", "").lower()
            if s.stype == "snmp" and _stype in _COUNTER_TYPES and _raw_val is not None:
                try:
                    _cur = int(_raw_val)
                    _now = time.time()
                    if s._snmp_prev is not None and s._snmp_prev_ts is not None:
                        _elapsed = _now - s._snmp_prev_ts
                        if _elapsed > 0:
                            _delta = _cur - s._snmp_prev
                            if _delta < 0:  # counter wrapped
                                _delta += (2**32 if _stype == "counter32" else 2**64)
                            _rate = _delta / _elapsed  # bytes/sec OR events/sec
                            s._last_rate = _rate
                            s.last_value = _fmt_rate(_rate, s.snmp_unit)
                        else:
                            s.last_value = _raw_val
                            s._last_rate = None
                    else:
                        s.last_value  = None  # first poll — no rate yet
                        s._last_rate  = None
                        s.last_detail = "—"   # suppress raw counter from UI on first poll
                    s._snmp_prev    = _cur
                    s._snmp_prev_ts = _now
                except (ValueError, TypeError):
                    s.last_value    = _raw_val
                    s._last_rate    = None
                    s._snmp_prev    = None
                    s._snmp_prev_ts = None
            else:
                s.last_value = _raw_val
                s._last_rate = None
            # ─────────────────────────────────────────────────
            s.history.append(result["ms"])
            _log_msg = s.last_value if (s.stype == "snmp" and s._last_rate is not None and s.last_value) else result["detail"]
            # ── Debounce: track consecutive successes ──
            s._consec_fail = 0
            s._consec_ok  += 1
            if s._alerted_down and s._consec_ok >= s.recover_after:
                _flap_dur = None
                if s._down_since_ts:
                    _flap_dur = int(time.time() - s._down_since_ts)
                    s._down_since_ts = None
                rec_data = {
                    "did": did, "sid": sid, "dname": dev.name, "sname": s.name,
                    "host": s.host, "stype": s.stype, "ts": _ts,
                    "detail": "Recovered", "direction": "recovered",
                    "grp": dev.group,
                    "consec_count": s._consec_ok,
                    "duration_s": _flap_dur,
                }
                if not _muted:
                    self._broadcast("flap_recovered", rec_data)
                    log_sensors.info(f"RECOVERED: {dev.name}/{s.name} ({s.host})")
                    _rec_cap = dict(rec_data)
                    _db_enqueue(lambda: db_auto_resolve_flap(
                        _rec_cap["did"], _rec_cap["sid"], _rec_cap["ts"],
                        directions=("down",)
                    ))
                s._alerted_down    = False
                s._email_sent_down = False
                s._recovery_pending = True
                s._consec_ok       = 0
            elif s._recovery_pending and not _muted:
                # Subsequent successes after recovery — clear pending after a stable run
                if s._consec_ok >= 60:
                    s._recovery_pending = False
        else:
            if s.alive is not False:
                dev.invalidate_status()
            s.alive       = False
            s.last_ms     = None
            s.last_detail = result["detail"]
            s.last_value  = None
            s.history.append(None)
            self._broadcast("log", {"did": did, "sid": sid,
                                     "msg": result["detail"], "type": "err"})
            _ts_captured    = _ts
            detail_captured = result["detail"]
            _db_enqueue(lambda: db_log_err(
                did, sid, s.name, s.stype, detail_captured, _ts_captured))
            # ── Debounce: track consecutive failures ──
            s._consec_ok   = 0
            s._consec_fail += 1
            s._recovery_pending = False
            if not s._alerted_down and s._consec_fail >= s.fail_after:
                flap_data = {
                    "did": did, "sid": sid, "dname": dev.name, "sname": s.name,
                    "host": s.host, "stype": s.stype, "ts": _ts,
                    "detail": result["detail"], "direction": "down",
                    "grp": dev.group,
                    "consec_count": s._consec_fail,
                }
                if not _muted:
                    self._broadcast("flap_down", flap_data)
                    log_sensors.warning(f"DOWN: {dev.name}/{s.name} ({s.host}) — {result['detail']}")
                    _flap_cap = dict(flap_data)
                    _db_enqueue(lambda: db_log_flap(_flap_cap))
                    if dev.webhook_url:
                        wh_url = dev.webhook_url
                        threading.Thread(
                            target=_send_webhook,
                            args=(wh_url, _flap_cap),
                            daemon=True,
                        ).start()
                s._alerted_down    = True
                s._down_since_ts   = time.time()

        # ── Threshold state check (transitions only) ──
        _new_thr = "ok"
        if result["ok"]:
            _thr_chk = None
            if s.stype in ('snmp', 'tls'):
                if s._last_rate is not None:
                    _u = s.snmp_unit
                    if _u in _BYTE_UNITS or _u == "":
                        # Traffic bytes counter — compare in Mbps
                        _thr_chk = s._last_rate * 8 / 1_000_000
                    else:
                        # Event counter (errors, packets) — compare raw events/sec
                        _thr_chk = s._last_rate
                else:
                    try: _thr_chk = float(s.last_value)
                    except (TypeError, ValueError): pass
            elif s.stype == 'vmware' and s.vmware_metric in ('uptime', 'on', 'disk_read', 'disk_write', 'disk_usage'):
                pass  # no threshold comparison for informational metrics
            elif s.last_ms is not None:
                _thr_chk = s.last_ms
            if _thr_chk is not None:
                # Inverted-threshold metrics: lower value = worse (alert when ≤ threshold).
                # Covers TLS cert expiry (days-to-expiry) and VMware datastore free-space (GB).
                _inverted = (
                    (s.stype == 'tls' and s._last_rate is None) or
                    (s.stype == 'vmware' and s.vmware_metric.startswith('dstore_'))
                )
                if _inverted:
                    if s.crit_ms and _thr_chk <= s.crit_ms:    _new_thr = "crit"
                    elif s.warn_ms and _thr_chk <= s.warn_ms:  _new_thr = "warn"
                else:
                    if s.crit_ms and _thr_chk >= s.crit_ms:    _new_thr = "crit"
                    elif s.warn_ms and _thr_chk >= s.warn_ms:  _new_thr = "warn"
        if s.loss_crit_pct and s.loss_pct >= s.loss_crit_pct:
            _new_thr = "crit"
        elif s.loss_warn_pct and s.loss_pct >= s.loss_warn_pct:
            if _new_thr != "crit": _new_thr = "warn"
        # Anomaly detection — opt-in per-sensor learned-baseline check.
        # Can only promote ok → warn; never fires crit. Static thresholds win.
        s._anom_caused_warn = False
        if (s.anomaly_enabled and result["ok"] and s.last_ms is not None
                and _new_thr == "ok"):
            from monitoring.anomaly import evaluate_anomaly, SUPPORTED_STYPES
            if s.stype in SUPPORTED_STYPES:
                if evaluate_anomaly(s, s.last_ms) == "warn":
                    _new_thr = "warn"
                    s._anom_caused_warn = True
        if _new_thr != s._threshold_state:
            # State CHANGED — reset counter, do full broadcast
            _prev_thr = s._threshold_state
            s._threshold_state = _new_thr
            dev.invalidate_status()
            s._consec_threshold = 1
            if _new_thr != "ok" and not _muted:
                s._threshold_recovery_pending = False
                s._threshold_triggered_ts = time.time()
                _tevt = "threshold_critical" if _new_thr == "crit" else "threshold_warning"
                _thr_evt_data = {
                    "did": did, "sid": sid, "dname": dev.name,
                    "sname": s.name, "host": s.host, "stype": s.stype,
                    "state": _new_thr, "ts": _ts,
                    "ms": s.last_ms, "loss_pct": s.loss_pct,
                    "grp": dev.group,
                    "consec_count": 1,
                }
                self._broadcast(_tevt, _thr_evt_data)
                if s._last_rate is not None:
                    _u2 = s.snmp_unit
                    if _u2 in _BYTE_UNITS or _u2 == "":
                        _unit = 'Mbps'
                    else:
                        _unit = _u2 + '/s' if _u2 else '/s'
                    _val_disp = s.last_value or f"{s._last_rate:.2f}{_unit}"
                elif s.stype == 'vmware':
                    _unit = ''
                    _val_disp = s.last_detail or s.last_value or ''
                elif s.stype in ('snmp', 'tls'):
                    _u3 = s.snmp_unit if s.stype == 'snmp' else 'days'
                    _unit = f" {_u3}" if _u3 else ''
                    _rv = s.last_value or ''
                    _val_disp = f"{_rv} {_u3}" if _u3 else _rv
                else:
                    _unit = 'ms'; _val_disp = f"{s.last_ms}ms"
                if _new_thr == "crit":
                    log_sensors.error(f"THRESHOLD CRIT: {dev.name}/{s.name} — {_val_disp} (limit {s.crit_ms}{_unit})")
                else:
                    log_sensors.warning(f"THRESHOLD WARN: {dev.name}/{s.name} — {_val_disp} (limit {s.warn_ms}{_unit})")
                _thr_flap = dict(_thr_evt_data)
                if _new_thr == "crit":
                    _thr_flap["direction"] = "threshold_crit"
                elif s._anom_caused_warn:
                    _thr_flap["direction"] = "anomaly_warn"
                else:
                    _thr_flap["direction"] = "threshold_warn"
                _thr_flap["detail"]    = _val_disp
                _db_enqueue(lambda _f=_thr_flap: db_log_flap(_f))
                # Escalation / de-escalation (warn↔crit) — auto-resolve the
                # PREVIOUS active threshold entry so only the current state
                # stays "active". Without this, a warn→crit transition leaves
                # the old warn row unresolved forever (OK → LIMIT 1 can't
                # catch up). Only fires for transitions between warn/crit;
                # ok→warn and ok→crit have nothing to resolve.
                if _prev_thr in ("warn", "crit"):
                    _esc_ts  = _ts
                    _esc_did = did
                    _esc_sid = sid
                    _prev_dir = "threshold_crit" if _prev_thr == "crit" else "threshold_warn"
                    _db_enqueue(lambda _d=_esc_did, _s=_esc_sid, _t=_esc_ts, _dir=_prev_dir:
                                db_auto_resolve_flap(_d, _s, _t, directions=(_dir,)))
            elif _new_thr == "ok" and _prev_thr != "ok" and not _muted:
                # Threshold recovered — broadcast and resolve existing event
                s._threshold_recovery_pending = True
                _thr_dur = None
                if s._threshold_triggered_ts:
                    _thr_dur = int(time.time() - s._threshold_triggered_ts)
                    s._threshold_triggered_ts = None
                # Build a human-readable current value for the detail field
                if s._last_rate is not None:
                    _rec_val = s.last_value or f"{s._last_rate:.2f}"
                elif s.stype == 'vmware':
                    _rec_val = s.last_detail or s.last_value or ''
                elif s.stype in ('snmp', 'tls'):
                    _rec_val = s.last_value or ''
                else:
                    _rec_val = f"{s.last_ms}ms" if s.last_ms is not None else ''
                _rec_detail = (f"Recovered from {_prev_thr}: {_rec_val}" if _rec_val
                               else f"Recovered from {_prev_thr}")
                _thr_rec_data = {
                    "did": did, "sid": sid, "dname": dev.name,
                    "sname": s.name, "host": s.host, "stype": s.stype,
                    "state": "ok", "ts": _ts,
                    "ms": s.last_ms, "loss_pct": s.loss_pct,
                    "grp": dev.group,
                    "consec_count": 1,
                    "duration_s": _thr_dur,
                    "detail": _rec_detail,
                }
                self._broadcast("threshold_ok", _thr_rec_data)
                log_sensors.info(f"THRESHOLD OK: {dev.name}/{s.name} — value back within limits")
                _thr_rec_ts = _ts
                _thr_rec_did = did
                _thr_rec_sid = sid
                _db_enqueue(lambda: db_auto_resolve_flap(
                    _thr_rec_did, _thr_rec_sid, _thr_rec_ts,
                    directions=("threshold_warn", "threshold_crit")
                ))
        else:
            # SAME state as previous probe — increment counter, engine only
            s._consec_threshold += 1
            if (s._threshold_state == "ok" and s._threshold_recovery_pending
                    and s._consec_threshold >= 60):
                s._threshold_recovery_pending = False

        # ── Alert profile evaluation (PRTG-style state-trigger system) ──
        if not _muted:
            try:
                from monitoring.alert_profile_engine import evaluate_and_fire
                evaluate_and_fire(dev, s)
            except Exception as _ape:
                log_sensors.warning(f"alert_profile_engine error: {_ape}")

        s.thr_history.append(s._threshold_state)
        _probe_end_batch = []
        if result["ok"]:
            _log_type = "err" if s._threshold_state == "crit" else ("warn" if s._threshold_state == "warn" else "ok")
            _probe_end_batch.append(("log", {"did": did, "sid": sid, "msg": _log_msg, "type": _log_type}))
        _probe_end_batch.append(("sensor", s.to_dict()))
        _probe_end_batch.append(("device_status", {"did": did, "status": dev.status}))
        self._broadcast_batch(_probe_end_batch)

        # Release thread immediately — scheduler fires next probe after interval
        if s.running:
            self._scheduler.schedule(did, sid, s.interval)
        else:
            self._broadcast("log", {"did": did, "sid": sid,
                                     "msg": f"[STOP] {s.name}", "type": "info"})
            s._stopped.set()

    def subscribe(self):
        q = queue.Queue(maxsize=300)
        with self._lock:
            if len(self._sse) >= 200:
                oldest = self._sse.pop(0)
                self._sse_registered.pop(oldest, None)
            self._sse.append(q)
            self._sse_registered[q] = time.monotonic()
        return q

    def unsubscribe(self, q):
        with self._lock:
            try: self._sse.remove(q)
            except ValueError: pass
            self._sse_registered.pop(q, None)

    # Event types that are forwarded to remote syslog.
    _SYSLOG_EVENTS = ('flap_down', 'flap_recovered', 'snmp_trap',
                      'threshold_critical', 'threshold_warning', 'threshold_ok')

    def _start_broadcaster(self):
        """Start the dedicated broadcaster thread that decouples probe workers
        from SSE fan-out. Probe workers push events onto `_bcast_queue`; this
        thread pulls and serialises fan-out to every subscriber.
        """
        if getattr(self, "_bcast_started", False):
            return
        self._bcast_queue = queue.Queue(maxsize=10000)
        self._bcast_started = True
        t = threading.Thread(target=self._broadcaster_loop, daemon=True, name='pw-sse-fanout')
        t.start()
        sw = threading.Thread(target=self._sse_sweeper, daemon=True, name='pw-sse-sweep')
        sw.start()

    def _sse_sweeper(self):
        """Periodic cleanup of abandoned SSE subscriber queues (silent disconnects)."""
        while True:
            time.sleep(60)
            cutoff = time.monotonic() - 300  # 5-min TTL for queues that haven't been drained
            with self._lock:
                stale = [q for q, ts in list(self._sse_registered.items())
                         if ts < cutoff and q.qsize() > 50]
                for q in stale:
                    try: self._sse.remove(q)
                    except ValueError: pass
                    self._sse_registered.pop(q, None)
            if stale:
                log_sensors.info("SSE sweeper: removed %d stale subscriber(s)", len(stale))

    def _broadcaster_loop(self):
        while True:
            events = self._bcast_queue.get()
            if events is None:
                break
            try:
                self._fanout(events)
            except Exception as e:
                log_sensors.warning("broadcaster loop error: %s", e)

    def _broadcast(self, event, data):
        self._broadcast_batch([(event, data)])

    def _broadcast_batch(self, events):
        """Enqueue a list of (event, data) tuples onto the broadcaster queue.

        Probe workers return immediately — fan-out happens on a dedicated thread.
        Callers on the probe hot path pass multiple events per cycle; batching
        avoids N queue puts and N subscribers-lock acquisitions.
        """
        if not events:
            return
        self._start_broadcaster()
        try:
            self._bcast_queue.put_nowait(events)
        except queue.Full:
            # Broadcaster is saturated (10k pending batches). Fan out inline as a
            # fallback so events aren't silently dropped.
            self._fanout(events)

    def _fanout(self, events):
        """Actual subscriber fan-out. Runs on the broadcaster thread."""
        msgs = [f"event: {ev}\ndata: {json.dumps(dt)}\n\n" for ev, dt in events]
        with self._lock:
            subscribers = list(self._sse)
        dead = []
        for q in subscribers:
            try:
                for msg in msgs:
                    q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        if dead:
            with self._lock:
                for d in dead:
                    try: self._sse.remove(d)
                    except ValueError: pass
                    self._sse_registered.pop(d, None)
        # Forward alert-category events to remote syslog (best-effort, non-blocking)
        for ev, dt in events:
            if ev in self._SYSLOG_EVENTS:
                try:
                    from monitoring.syslog_client import syslog_send
                    syslog_send(ev, dt)
                except Exception:
                    pass

    def all_devices(self):
        with self._lock:
            return [d.to_dict() for d in self.devices.values()]
