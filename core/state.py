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
        self._heap     = []          # (run_at, seq, did, sid)
        self._seq      = 0
        self._lock     = threading.RLock()
        self._wake     = threading.Event()
        self._executor = executor
        self._run_fn   = run_fn
        t = threading.Thread(target=self._loop, daemon=True, name='pw-sched')
        t.start()

    def schedule(self, did, sid, delay):
        """Schedule a probe for (did, sid) to run after `delay` seconds."""
        with self._lock:
            self._seq += 1
            heapq.heappush(self._heap, (time.monotonic() + delay, self._seq, did, sid))
        self._wake.set()

    def _loop(self):
        while True:
            with self._lock:
                now = time.monotonic()
                while self._heap and self._heap[0][0] <= now:
                    _, _, did, sid = heapq.heappop(self._heap)
                    self._executor.submit(self._run_fn, did, sid)
                    now = time.monotonic()
                sleep_for = (self._heap[0][0] - now) if self._heap else 60.0
            self._wake.wait(timeout=min(sleep_for, 1.0))
            self._wake.clear()

from monitoring.probes import probe_ping, probe_tcp, probe_http, probe_snmp, probe_dns
from monitoring.probes import probe_tls, probe_http_keyword, probe_banner
from monitoring.smtp_alert import send_alert_email
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


def _smtp_down_delayed(sensor, data):
    """Sleep smtp_down_delay seconds, then send alert only if sensor is still down."""
    delay = max(0, int(_cfg('smtp_down_delay', 10)))
    time.sleep(delay)
    if sensor._alerted_down and sensor.running:
        send_alert_email('down', data)
        sensor._email_sent_down = True


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
                 vmware_disk_path=""):
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
        # Debounce — now handled by alert rules (trigger_count / recover_count).
        # Hardcoded to 1 so sensor fires events immediately on first state change.
        self.fail_after    = 1
        self.recover_after = 1
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
            "threshold_state":       self._threshold_state,
            "alive":          self.alive,
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
        self.sensors      = {}
        self._sid_ctr     = 0
        # Device-level default credentials (pre-fill for new sensors)
        self.snmp_community_default  = ""
        self.snmp_version_default    = ""
        self.vmware_user_default     = ""
        self.vmware_password_default = ""

    def next_sid(self):
        self._sid_ctr += 1
        return f"s{self._sid_ctr}"

    @property
    def status(self):
        active = [s for s in self.sensors.values() if not s.alerts_muted]
        vals = [s.alive for s in active]
        if not vals or all(v is None for v in vals): return "unknown"
        if any(v is False for v in vals): return "down"
        if any(s._threshold_state == "crit" for s in active): return "down"
        if any(s._threshold_state == "warn" for s in active): return "warn"
        return "up"

    def to_dict(self):
        return {
            "device_id":    self.device_id,
            "name":         self.name,
            "host":         self.host,
            "group":        self.group,
            "webhook_url":  self.webhook_url,
            "alerts_muted": self.alerts_muted,
            "status":       self.status,
            "sensors":      [s.to_dict() for s in self.sensors.values()],
            "snmp_community_default":      self.snmp_community_default,
            "snmp_version_default":        self.snmp_version_default,
            "vmware_user_default":         self.vmware_user_default,
            "has_vmware_password_default":  bool(self.vmware_password_default),
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
        self._lock     = threading.RLock()
        self.devices   = {}
        self._did_ctr  = 0
        self._sse      = []
        self._executor  = concurrent.futures.ThreadPoolExecutor(
            max_workers=64, thread_name_prefix='pw-sensor'
        )
        self._scheduler = _SensorScheduler(self._executor, self._run_once)

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
            for s in dev.sensors.values(): s.running = False
            del self.devices[did]
        return True

    def get_device(self, did):
        with self._lock:
            return self.devices.get(did)

    def add_sensor(self, did, name, stype, host=None,
                   port=None, url=None, interval=5, timeout=4,
                   verify_ssl=True, snmp_community="public",
                   snmp_oid="1.3.6.1.2.1.1.1.0", snmp_version="2c",
                   fail_after=1, recover_after=1,
                   warn_ms=None, crit_ms=None, loss_warn_pct=0, loss_crit_pct=0,
                   keyword="", keyword_case=False, banner_regex="", snmp_unit="",
                   vmware_user="", vmware_password="",
                   vmware_vm_id="", vmware_vm_name="", vmware_metric="",
                   vmware_disk_path=""):
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
                       vmware_metric=vmware_metric, vmware_disk_path=vmware_disk_path)
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
                        "vmware_disk_path"]
            for k, v in kwargs.items():
                if k in editable and v is not None:
                    if k == 'host':
                        if v:  # Non-empty: manually overridden — unlink from device
                            s.host_override = True
                        else:  # Cleared: re-link to device host
                            v = dev.host
                            s.host_override = False
                    setattr(s, k, v)
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
            del dev.sensors[sid]
        return True

    def start_sensor(self, did, sid):
        with self._lock:
            dev = self.devices.get(did)
            if not dev: return
            s = dev.sensors.get(sid)
        if not s or s.running: return
        s.running = True
        s._stopped.clear()
        self._executor.submit(self._run_once, did, sid)

    def stop_sensor(self, did, sid):
        with self._lock:
            dev = self.devices.get(did)
            if dev:
                s = dev.sensors.get(sid)
                if s:
                    s.running = False
                    # Scheduler entry will be ignored — _run_once checks s.running at entry

    def start_device(self, did):
        with self._lock:
            dev = self.devices.get(did)
            sids = list(dev.sensors) if dev else []
        for sid in sids:
            self.start_sensor(did, sid)

    def stop_device(self, did):
        with self._lock:
            dev = self.devices.get(did)
            sids = list(dev.sensors) if dev else []
        for sid in sids:
            self.stop_sensor(did, sid)

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
                        s.last_value = None   # first poll — no rate yet
                        s._last_rate = None
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
                rec_data = {
                    "did": did, "sid": sid, "dname": dev.name, "sname": s.name,
                    "host": s.host, "stype": s.stype, "ts": _ts,
                    "detail": "Recovered", "direction": "recovered",
                    "grp": dev.group,
                    "consec_count": s._consec_ok,
                }
                if not _muted:
                    self._broadcast("flap_recovered", rec_data)
                    log_sensors.info(f"RECOVERED: {dev.name}/{s.name} ({s.host})")
                    _rec_cap = dict(rec_data)
                    _db_enqueue(lambda: db_auto_resolve_flap(
                        _rec_cap["did"], _rec_cap["sid"], _rec_cap["ts"],
                        directions=("down",)
                    ))
                    if s._email_sent_down:
                        _smtp_cap = dict(rec_data)
                        threading.Thread(target=send_alert_email, args=('recovered', _smtp_cap), daemon=True).start()
                s._alerted_down    = False
                s._email_sent_down = False
                s._recovery_pending = True
                s._consec_ok       = 0
            elif s._recovery_pending and not _muted:
                # Subsequent successes after recovery — alert engine only (skip SSE/syslog)
                _eng_data = {
                    "did": did, "sid": sid, "dname": dev.name, "sname": s.name,
                    "host": s.host, "stype": s.stype, "ts": _ts,
                    "detail": "Recovered", "direction": "recovered",
                    "grp": dev.group,
                    "consec_count": s._consec_ok,
                }
                try:
                    from monitoring.alert_engine import alert_engine_send
                    alert_engine_send("flap_recovered", _eng_data)
                except Exception:
                    pass
                if s._consec_ok >= 60:
                    s._recovery_pending = False
        else:
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
                    _smtp_cap = dict(flap_data)
                    s._email_sent_down = False
                    threading.Thread(target=_smtp_down_delayed, args=(s, _smtp_cap), daemon=True).start()
                s._alerted_down = True
            elif s._alerted_down and not _muted:
                # Subsequent failures — alert engine only (skip SSE/syslog)
                _eng_data = {
                    "did": did, "sid": sid, "dname": dev.name, "sname": s.name,
                    "host": s.host, "stype": s.stype, "ts": _ts,
                    "detail": result["detail"], "direction": "down",
                    "grp": dev.group,
                    "consec_count": s._consec_fail,
                }
                try:
                    from monitoring.alert_engine import alert_engine_send
                    alert_engine_send("flap_down", _eng_data)
                except Exception:
                    pass

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
                if s.crit_ms and _thr_chk >= s.crit_ms:    _new_thr = "crit"
                elif s.warn_ms and _thr_chk >= s.warn_ms:  _new_thr = "warn"
        if s.loss_crit_pct and s.loss_pct >= s.loss_crit_pct:
            _new_thr = "crit"
        elif s.loss_warn_pct and s.loss_pct >= s.loss_warn_pct:
            if _new_thr != "crit": _new_thr = "warn"
        if _new_thr != s._threshold_state:
            # State CHANGED — reset counter, do full broadcast
            _prev_thr = s._threshold_state
            s._threshold_state = _new_thr
            s._consec_threshold = 1
            if _new_thr != "ok" and not _muted:
                s._threshold_recovery_pending = False
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
                    _u3 = s.snmp_unit if s.stype == 'snmp' else ''
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
                _thr_flap["direction"] = "threshold_crit" if _new_thr == "crit" else "threshold_warn"
                _thr_flap["detail"]    = _val_disp
                _db_enqueue(lambda _f=_thr_flap: db_log_flap(_f))
            elif _new_thr == "ok" and _prev_thr != "ok" and not _muted:
                # Threshold recovered — broadcast and resolve existing event
                s._threshold_recovery_pending = True
                _thr_rec_data = {
                    "did": did, "sid": sid, "dname": dev.name,
                    "sname": s.name, "host": s.host, "stype": s.stype,
                    "state": "ok", "ts": _ts,
                    "ms": s.last_ms, "loss_pct": s.loss_pct,
                    "grp": dev.group,
                    "consec_count": 1,
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
            if s._threshold_state != "ok" and not _muted:
                _tevt = "threshold_critical" if s._threshold_state == "crit" else "threshold_warning"
                _eng_thr = {
                    "did": did, "sid": sid, "dname": dev.name,
                    "sname": s.name, "host": s.host, "stype": s.stype,
                    "state": s._threshold_state, "ts": _ts,
                    "ms": s.last_ms, "loss_pct": s.loss_pct,
                    "grp": dev.group,
                    "consec_count": s._consec_threshold,
                }
                try:
                    from monitoring.alert_engine import alert_engine_send
                    alert_engine_send(_tevt, _eng_thr)
                except Exception:
                    pass
            elif s._threshold_state == "ok" and s._threshold_recovery_pending and not _muted:
                _eng_thr_ok = {
                    "did": did, "sid": sid, "dname": dev.name,
                    "sname": s.name, "host": s.host, "stype": s.stype,
                    "state": "ok", "ts": _ts,
                    "ms": s.last_ms, "loss_pct": s.loss_pct,
                    "grp": dev.group,
                    "consec_count": s._consec_threshold,
                }
                try:
                    from monitoring.alert_engine import alert_engine_send
                    alert_engine_send("threshold_ok", _eng_thr_ok)
                except Exception:
                    pass
                if s._consec_threshold >= 60:
                    s._threshold_recovery_pending = False

        s.thr_history.append(s._threshold_state)
        if result["ok"]:
            _log_type = "err" if s._threshold_state == "crit" else ("warn" if s._threshold_state == "warn" else "ok")
            self._broadcast("log", {"did": did, "sid": sid, "msg": _log_msg, "type": _log_type})
        self._broadcast("sensor", s.to_dict())
        self._broadcast("device_status", {"did": did, "status": dev.status})

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
            self._sse.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            try: self._sse.remove(q)
            except ValueError: pass

    def _broadcast(self, event, data):
        msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        with self._lock:
            subscribers = list(self._sse)
        dead = []
        for q in subscribers:
            try: q.put_nowait(msg)
            except queue.Full: dead.append(q)
        if dead:
            with self._lock:
                for d in dead:
                    try: self._sse.remove(d)
                    except ValueError: pass
        if event in ('flap_down', 'flap_recovered', 'snmp_trap',
                     'threshold_critical', 'threshold_warning', 'threshold_ok'):
            try:
                from monitoring.syslog_client import syslog_send
                syslog_send(event, data)
            except Exception:
                pass
            try:
                from monitoring.alert_engine import alert_engine_send
                alert_engine_send(event, data)
            except Exception:
                pass

    def all_devices(self):
        with self._lock:
            return [d.to_dict() for d in self.devices.values()]
