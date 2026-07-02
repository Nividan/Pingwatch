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


def is_group_muted(group_name: str) -> bool:
    """Return True if `group_name` is in the user-maintained muted list.

    Backed by `app_settings.muted_groups` (JSON list of group names). Called
    on every flap/threshold transition and alert dispatch, so kept cheap: a
    dict lookup + cached-string JSON parse. Caller is responsible for any
    further semantics (device status display isn't affected — mute only
    suppresses alert/event emission, matching per-device `alerts_muted`).
    """
    if not group_name:
        return False
    import core.settings as _settings
    raw = _settings.get("muted_groups", "") or ""
    if not raw:
        return False
    try:
        lst = json.loads(raw)
        return isinstance(lst, list) and group_name in lst
    except Exception:
        return False


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
        # This single thread drives EVERY sensor. An uncaught exception here
        # would stop all probing while the rest of the app keeps running —
        # the worst possible failure mode for a monitor. Guard the whole body.
        while True:
            try:
                with self._lock:
                    now = time.monotonic()
                    while self._heap and self._heap[0][0] <= now:
                        _, _, did, sid = heapq.heappop(self._heap)
                        if (did, sid) in self._tombstones:
                            continue   # sensor was deleted/stopped — skip
                        try:
                            self._executor.submit(self._run_fn, did, sid)
                        except Exception:
                            # Executor rejected the task (e.g. shut down or
                            # mid-resize). Re-queue with a short delay so this
                            # sensor's probe chain isn't lost if it recovers.
                            self._seq += 1
                            heapq.heappush(self._heap,
                                           (time.monotonic() + 5.0, self._seq, did, sid))
                            raise
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
            except Exception as e:
                try:
                    log_sensors.error(f"Sensor scheduler loop error: {e}")
                except Exception:
                    pass
                time.sleep(1.0)

from monitoring.probes import probe_ping, probe_tcp, probe_http, probe_snmp, probe_dns
from monitoring.probes import probe_tls, probe_http_keyword, probe_banner, probe_smtp, probe_ssh, probe_sftp, probe_radius
from .settings import get as _cfg
from .logger import log_sensors
from .raw_data import build_flap_raw_data

_COUNTER_TYPES  = {"counter32", "counter64", "counter"}
_BYTE_UNITS     = {"bytes"}
_COUNT_UNITS    = {"errors", "packets"}  # counter OIDs that count events, not bytes

# v0.9.7: typed SNMP sensor categorization (Python mirror of frontend's
# _snmpCategory).  Drives the event-transition detector in _run_once so a
# state change from primary to non-primary fires a flap / webhook even though
# SNMP itself succeeded (e.g. ifOperStatus goes 1→2 — the device answered,
# but the interface is down).
import re as _re
_GAUGE_UNITS_PY  = frozenset([
    "%", "percent", "celsius", "fahrenheit", "dbm", "count",
    "seconds", "minutes", "hours", "hz", "volts", "amps", "ratio", "rpm",
])
_ENUM_UNIT_RE_PY = _re.compile(r"\d+\s*=\s*[a-zA-Z][\w-]*")
_ENUM_PAIR_RE_PY = _re.compile(r"(\d+)\s*=\s*([a-zA-Z][\w-]*)")

def _snmp_category_py(snmp_unit: str, snmp_type: str, snmp_oid: str = "") -> str:
    u = (snmp_unit or "").lower().strip()
    if u in _BYTE_UNITS or u in _COUNT_UNITS:
        return "counter_rate"
    if snmp_unit and _ENUM_UNIT_RE_PY.search(snmp_unit):
        return "enum_state"
    # v0.9.7: known-OID enum fallback for "Auto-detect" sensors (unit blank).
    if not snmp_unit and snmp_oid and _enum_for_oid_py(snmp_oid):
        return "enum_state"
    if u in _GAUGE_UNITS_PY:
        return "gauge_numeric"
    if snmp_type == "TimeTicks":
        return "time_duration"
    if snmp_type == "OCTET STRING" or u == "string":
        return "text"
    return "gauge_numeric"

def _parse_enum_legend_py(snmp_unit: str) -> dict:
    if not snmp_unit:
        return {}
    return {m.group(1): m.group(2) for m in _ENUM_PAIR_RE_PY.finditer(snmp_unit)}

# Well-known OID prefix → implicit enum legend (Python mirror of frontend
# _KNOWN_ENUM_OIDS).  Lets event-transition detection fire on "Auto-detect"
# sensors pointed at standard IF-MIB / UPS-MIB enum OIDs, without the user
# having to set snmp_unit manually.
_KNOWN_ENUM_OIDS_PY = [
    ("1.3.6.1.2.1.2.2.1.8.",  {"1":"up","2":"down","3":"testing","4":"unknown","5":"dormant","6":"notPresent","7":"lowerLayerDown"}),
    ("1.3.6.1.2.1.2.2.1.7.",  {"1":"up","2":"down","3":"testing"}),
    ("1.3.6.1.2.1.33.1.2.1.", {"1":"unknown","2":"batteryNormal","3":"batteryLow","4":"batteryDepleted"}),
]

def _enum_for_oid_py(oid: str) -> dict:
    if not oid:
        return {}
    for prefix, legend in _KNOWN_ENUM_OIDS_PY:
        if oid.startswith(prefix):
            return legend
    return {}

def _effective_enum_legend_py(snmp_unit: str, snmp_oid: str) -> dict:
    legend = _parse_enum_legend_py(snmp_unit)
    if legend:
        return legend
    return _enum_for_oid_py(snmp_oid)

def _enum_primary_code_py(snmp_unit: str) -> str:
    """The 'healthy' enum code = the FIRST pair listed in the legend.
    Legends where 1 is healthy ("1=up 2=down") list 1 first, so behavior is
    unchanged for them — but authors can now lead with the real ok-state for
    enums where 1 is a fault ("0=ok 1=alarm", "2=on … 8=failed", "3=ok …")."""
    legend = _parse_enum_legend_py(snmp_unit)
    return next(iter(legend), "1")

def _fmt_duration_s(secs: float) -> str:
    if secs is None or secs < 0:
        return "—"
    secs = int(secs)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d > 0: return f"{d}d {h}h"
    if h > 0: return f"{h}h {m}m"
    return f"{m}m {rem % 60}s"

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
                 port=None, url=None, interval=60, timeout=10,
                 verify_ssl=True,
                 snmp_community="public", snmp_oid="1.3.6.1.2.1.1.1.0",
                 snmp_version="2c",
                 fail_after=3, recover_after=2,
                 warn_ms=None, crit_ms=None, loss_warn_pct=0, loss_crit_pct=0,
                 keyword="", keyword_case=False, banner_regex="",
                 alerts_muted=False, snmp_unit="",
                 snmp_scale=0, snmp_oid2="", snmp_pct_mode="",
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
                 radius_username="", radius_password="", radius_nas_id="",
                 snmp_v3_user="", snmp_v3_level="",
                 snmp_v3_auth_proto="", snmp_v3_auth_pass="",
                 snmp_v3_priv_proto="", snmp_v3_priv_pass="",
                 snmp_v3_context="",
                 cert_warn_days=0, cert_crit_days=0):
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
        # HTTPS cert-expiry thresholds (days remaining; 0 = off). Independent
        # of the latency thresholds — an HTTP/S sensor can warn/crit on an
        # approaching cert expiry while still reporting fast and up.
        self.cert_warn_days = int(cert_warn_days or 0)
        self.cert_crit_days = int(cert_crit_days or 0)
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
        # v1.5 template-library fields:
        #   snmp_scale    — static value divisor applied centrally in
        #                   _process_result (deci-°C → 10, KB→MB → 1024).
        #                   0/1 = off. Never applied to counter types (the
        #                   agent computes remote rates from raw values).
        #   snmp_oid2 +   — computed-percentage sensor: probe both OIDs and
        #   snmp_pct_mode   combine per mode (used_total/used_free/free_total).
        try:
            self.snmp_scale = float(snmp_scale or 0)
        except (TypeError, ValueError):
            self.snmp_scale = 0.0
        self.snmp_oid2     = snmp_oid2 or ""
        self.snmp_pct_mode = snmp_pct_mode or ""
        self.snmp_type     = ""   # SNMP ASN.1 type from probe (Counter32 / Counter64 / Gauge32 / Integer / TimeTicks / OCTET STRING) — populated on first successful SNMP probe; empty for non-SNMP sensors. Drives the frontend's typed rendering (enum vs gauge vs duration vs text).
        # v0.9.7: prev-value tracking for typed SNMP event transitions.
        # _prev_enum_code — last seen enum code (str), used to detect state flips
        # _prev_ticks — last TimeTicks value, used to detect reboots (decrease)
        # _prev_text_value — last OCTET STRING value, used to detect changes
        self._prev_enum_code    = None
        self._prev_ticks        = None
        self._prev_text_value   = None
        self.host_override = False   # True = host was manually set; don't sync from device
        # Distributed probes (v1.3) — '' = inherit from device/site cascade,
        # 'central' = explicit pin to central, else a probe_id.
        self.probe_id      = ""
        # Monotonic guard for remote result injection: epoch ts of the last
        # result processed through _process_result. Backfilled/replayed
        # results at or before this ts are dropped. Lazily seeded from
        # MAX(sensor_samples.ts) on the first remote injection after boot.
        self._last_processed_ts = None
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
        # SNMPv3 credentials (per-sensor override; blank field → inherit from device default at probe time)
        # auth_pass / priv_pass are Fernet-encrypted at rest, decrypted just-in-time in probe().
        self.snmp_v3_user       = snmp_v3_user or ""
        self.snmp_v3_level      = snmp_v3_level or ""       # "" | noAuthNoPriv | authNoPriv | authPriv
        self.snmp_v3_auth_proto = snmp_v3_auth_proto or ""  # MD5 | SHA | SHA-224 | SHA-256 | SHA-384 | SHA-512
        self.snmp_v3_auth_pass  = snmp_v3_auth_pass or ""   # Fernet ciphertext
        self.snmp_v3_priv_proto = snmp_v3_priv_proto or ""  # DES | AES | AES-192 | AES-256
        self.snmp_v3_priv_pass  = snmp_v3_priv_pass or ""   # Fernet ciphertext
        self.snmp_v3_context    = snmp_v3_context or ""
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
        # Event-ACK reflection: set when an operator acknowledges this
        # sensor's active incident in Events; cleared on recovery. Drives
        # the muted "acknowledged-down" tile/card rendering ("known down").
        self._ack_by                      = ""
        self._ack_at                      = 0.0
        # HTTPS cert-expiry runtime: last measured days-to-expiry (None until
        # a probe reports one), and whether cert expiry — not latency/loss —
        # drove the current threshold state (for event detail attribution).
        self._cert_days                   = None
        self._cert_caused_thr             = False
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
        self._sched_mode    = "central"   # 'central' = local probe chain, 'probe' = remote agent
        self._inflight_probe  = None    # orphan probe thread past its hard cap — leak guard
        self._alert_has_fired = False   # any profile stage dispatched during the current incident
        self._alert_cleanup_checked = False  # once-per-boot orphaned-event sweep done
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

    def _resolve_snmp_v3_creds(self):
        """Build the v3_creds dict for probe_snmp.

        Per-sensor fields win; blank fields fall back to the parent device's
        snmp_v3_*_default.  auth_pass / priv_pass are Fernet-decrypted at the
        boundary — the net-snmp subprocess needs the plaintext passphrase, and
        we never persist plaintext.
        """
        from db.backups import decrypt_pw
        from core.app_state import STATE
        dev = STATE.devices.get(self.device_id) if STATE else None

        def _pick(sensor_val, dev_attr):
            if sensor_val:
                return sensor_val
            return getattr(dev, dev_attr, "") if dev else ""

        user  = _pick(self.snmp_v3_user,  "snmp_v3_user_default")
        level = _pick(self.snmp_v3_level, "snmp_v3_level_default") or "noAuthNoPriv"
        apr   = _pick(self.snmp_v3_auth_proto, "snmp_v3_auth_proto_default")
        app   = _pick(self.snmp_v3_auth_pass,  "snmp_v3_auth_pass_default")
        ppr   = _pick(self.snmp_v3_priv_proto, "snmp_v3_priv_proto_default")
        ppp   = _pick(self.snmp_v3_priv_pass,  "snmp_v3_priv_pass_default")
        ctx   = _pick(self.snmp_v3_context,    "snmp_v3_context_default")
        return {
            "user":       user,
            "level":      level,
            "auth_proto": apr,
            "auth_pass":  decrypt_pw(app) if app else "",
            "priv_proto": ppr,
            "priv_pass":  decrypt_pw(ppp) if ppp else "",
            "context":    ctx,
        }

    def probe(self):
        if self.stype == "ping": return probe_ping(self.host, self.timeout)
        if self.stype == "tcp":  return probe_tcp(self.host, self.port or 80, self.timeout)
        if self.stype == "http": return probe_http(self.url or self.host, self.timeout,
                                                   self.verify_ssl, self.http_expected_status,
                                                   cert_check=bool(self.cert_warn_days or self.cert_crit_days))
        if self.stype == "dns":  return probe_dns(self.host, self.dns_query or self.host,
                                                   self.dns_record_type, self.dns_server,
                                                   self.port or 53, self.timeout)
        if self.stype == "snmp":
            v3_creds = self._resolve_snmp_v3_creds() if self.snmp_version == "3" else None
            if self.snmp_oid2 and self.snmp_pct_mode:
                from monitoring.probes import probe_snmp_percent
                return probe_snmp_percent(self.host, self.snmp_community,
                                          self.snmp_oid, self.snmp_oid2,
                                          self.snmp_pct_mode, self.port or 161,
                                          self.timeout, self.snmp_version, v3_creds)
            return probe_snmp(self.host, self.snmp_community,
                              self.snmp_oid, self.port or 161,
                              self.timeout, self.snmp_version, v3_creds)
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
            # SNMPv3 per-sensor override fields — passphrases never leave the
            # server, surfaced as has_* flags so the UI can render "stored"
            # placeholders without round-tripping ciphertext.
            "snmp_v3_user":        self.snmp_v3_user,
            "snmp_v3_level":       self.snmp_v3_level,
            "snmp_v3_auth_proto":  self.snmp_v3_auth_proto,
            "snmp_v3_priv_proto":  self.snmp_v3_priv_proto,
            "snmp_v3_context":     self.snmp_v3_context,
            "has_snmp_v3_auth_pass": bool(self.snmp_v3_auth_pass),
            "has_snmp_v3_priv_pass": bool(self.snmp_v3_priv_pass),
            "dns_query":             self.dns_query,
            "dns_record_type":       self.dns_record_type,
            "dns_server":            self.dns_server,
            "http_expected_status":  self.http_expected_status,
            "cert_warn_days":        self.cert_warn_days,
            "cert_crit_days":        self.cert_crit_days,
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
            "probe_id":              getattr(self, "probe_id", "") or "",
            "snmp_unit":             self.snmp_unit,
            "snmp_type":             self.snmp_type,
            "snmp_scale":            getattr(self, "snmp_scale", 0) or 0,
            "snmp_oid2":             getattr(self, "snmp_oid2", ""),
            "snmp_pct_mode":         getattr(self, "snmp_pct_mode", ""),
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
            "ack_by":                self._ack_by,
            "ack_at":                self._ack_at,
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


def _coerce_parent_ports(raw) -> dict:
    """Return parent_device_ports in canonical list-of-pairs shape regardless
    of whether the in-memory value uses the legacy single-dict form."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for pid, val in raw.items():
        if not isinstance(pid, str) or not pid:
            continue
        if isinstance(val, list):
            pairs = [p for p in val if isinstance(p, dict)]
        elif isinstance(val, dict):
            pairs = [val]
        else:
            continue
        clean = []
        for p in pairs:
            l = p.get("lport", "") if isinstance(p.get("lport", ""), str) else ""
            r = p.get("rport", "") if isinstance(p.get("rport", ""), str) else ""
            if l or r:
                clean.append({"lport": l, "rport": r})
        if clean:
            out[pid] = clean
    return out


class Device:
    def __init__(self, device_id, name, host, group="Default Group", site=""):
        self.device_id   = device_id
        self.name        = name
        self.host        = host
        self.group       = group
        # Site tag (v1.0+) — parent of Group in the Site → Group → Device
        # hierarchy. Free-text; sourced via /api/sites from UNION(ipam_subnets,
        # devices). Empty = "Unsited" bucket on the Devices tab.
        self.site        = site
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
        # Distributed probes (v1.3) — device-level probe assignment.
        # '' = inherit from site binding, 'central' = explicit central pin.
        self.probe_id     = ""
        # Device-level default credentials (pre-fill for new sensors)
        self.snmp_community_default  = ""
        self.snmp_version_default    = ""
        self.vmware_user_default     = ""
        self.vmware_password_default = ""
        # SNMPv3 device defaults — sensor.probe() falls back to these when the
        # per-sensor v3 field is blank. auth / priv passphrases Fernet-encrypted.
        self.snmp_v3_user_default       = ""
        self.snmp_v3_level_default      = ""   # noAuthNoPriv | authNoPriv | authPriv
        self.snmp_v3_auth_proto_default = ""   # MD5 | SHA | SHA-224 | SHA-256 | SHA-384 | SHA-512
        self.snmp_v3_auth_pass_default  = ""   # Fernet ciphertext
        self.snmp_v3_priv_proto_default = ""   # DES | AES | AES-192 | AES-256
        self.snmp_v3_priv_pass_default  = ""   # Fernet ciphertext
        self.snmp_v3_context_default    = ""
        # Live Map parent linking (v1.0+) — JSON-persisted list of device IDs
        # this device hangs off. Empty list = orphan / root. Multi-parent
        # supports dual-NIC and dual-homed devices. Group-level fallback lives
        # in topo_settings('pw_group_parents').
        self.parent_device_ids = []
        # Per-parent port wiring (v1.x+) — {pid: {"lport": str, "rport": str}}.
        # Keys only ever reference device ids (never "group:<name>"); group
        # parents don't carry port info. Walker code on parent_device_ids is
        # unaffected — read this map only when surfacing wiring details.
        self.parent_device_ports = {}
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
        # list() snapshot: this property is read from probe/HTTP/broadcast
        # threads while routes mutate the sensors dict. Iterating the live
        # view raises "dictionary changed size during iteration".
        sensors = list(self.sensors.values())
        running = [s for s in sensors if s.running]
        active  = [s for s in running if not s.alerts_muted]
        vals = [s.alive for s in active]
        if sensors and not running:
            # Every sensor is explicitly stopped — the device is paused, not
            # down or unknown. A distinct status lets the UI grey it out and
            # the Pause filter / dashboard pie count it instead of lumping a
            # deliberately-stopped device in with real outages. (A device with
            # no sensors at all still falls through to "unknown".)
            result = "pause"
        elif not vals or all(v is None for v in vals):
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
            "site":         getattr(self, "site", ""),
            "probe_id":     getattr(self, "probe_id", "") or "",
            "webhook_url":  self.webhook_url,
            "alerts_muted": self.alerts_muted,
            "status":       self.status,
            "sensors":      [s.to_dict() for s in self.sensors.values()],
            "snmp_community_default":      self.snmp_community_default,
            "snmp_version_default":        self.snmp_version_default,
            "vmware_user_default":         self.vmware_user_default,
            "has_vmware_password_default":  bool(self.vmware_password_default),
            # SNMPv3 defaults — passphrases exposed only as has_* flags
            "snmp_v3_user_default":          self.snmp_v3_user_default,
            "snmp_v3_level_default":         self.snmp_v3_level_default,
            "snmp_v3_auth_proto_default":    self.snmp_v3_auth_proto_default,
            "snmp_v3_priv_proto_default":    self.snmp_v3_priv_proto_default,
            "snmp_v3_context_default":       self.snmp_v3_context_default,
            "has_snmp_v3_auth_pass_default": bool(self.snmp_v3_auth_pass_default),
            "has_snmp_v3_priv_pass_default": bool(self.snmp_v3_priv_pass_default),
            # Origin breadcrumb (Auto-Discovery). 0/"" for manually-added devices.
            "discovered_at":         float(getattr(self, "discovered_at", 0) or 0),
            "discovered_from_cidr":  getattr(self, "discovered_from_cidr", "") or "",
            # Live Map parent linking — list of device IDs this device hangs off.
            "parent_device_ids":     list(getattr(self, "parent_device_ids", []) or []),
            # Per-parent port wiring (Live Map link info). Canonical shape is
            # {pid: [{lport, rport}, ...]} — a list to support LACP / multiple
            # physical links between the same device pair. Coerce here so even
            # if something writes the legacy single-dict shape to the field
            # directly, the API output stays uniform.
            "parent_device_ports":   _coerce_parent_ports(getattr(self, "parent_device_ports", {})),
        }


def _bump_probe_config(probe_id: str):
    """Advance a probe's config_version so its agent re-pulls config on the
    next checkin. Called from start/stop_sensor for remote sensors — covers
    sensor edits (stop+start restart), pause/resume, and sensor adds."""
    if not probe_id:
        return
    try:
        from db.probes import db_bump_config_version
        db_bump_config_version(probe_id)
    except Exception:
        pass


def _max_sample_ts(did: str, sid: str) -> float:
    """Newest persisted sample ts for a sensor (0.0 when none / on error).

    Seeds Sensor._last_processed_ts on the first remote-result injection
    after boot so agent batch re-sends across a server restart can't
    double-insert samples.
    """
    try:
        from db.helpers import db_query_one
        row = db_query_one(
            "logs",
            "SELECT MAX(ts) AS m FROM sensor_samples WHERE did = ? AND sid = ?",
            (did, sid))
        return float(row["m"]) if row and row["m"] is not None else 0.0
    except Exception:
        return 0.0


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
    # than falling through to the request. Checks EVERY resolved address
    # (incl. IPv4-mapped IPv6) so a multi-record rebinding host can't slip an
    # internal IP past a first-record-only check.
    def _internal(_o):
        if (_o.is_loopback or _o.is_private or _o.is_link_local
                or _o.is_reserved or _o.is_multicast or _o.is_unspecified):
            return True
        _m = getattr(_o, "ipv4_mapped", None)
        return _m is not None and _internal(_m)
    try:
        _host = _parsed.hostname or ""
        if not _host:
            log.warning(f"Webhook blocked — no hostname: {url}")
            return
        try:
            _infos = _sock.getaddrinfo(_host, None)
        except Exception as _de:
            log.warning(f"Webhook blocked — DNS resolution failed for {url}: {_de}")
            return
        for _info in _infos:
            _addr = _info[4][0].split("%")[0]
            if _internal(_ip.ip_address(_addr)):
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


# ── Webhook dispatcher queue ──────────────────────────────────────────
# Flap events used to spawn a fresh daemon thread per dispatch. During a
# flap storm (e.g. uplink drop, many sensors going DOWN together) that
# pattern could accumulate hundreds of threads. A single dispatcher thread
# + bounded queue caps concurrency and makes overload observable.
_WEBHOOK_Q: "queue.Queue" = queue.Queue(maxsize=100)
_WEBHOOK_DISPATCHER_STARTED = False
_WEBHOOK_LOCK = threading.Lock()


def _webhook_dispatcher_loop():
    from core.logger import log as _log
    while True:
        url, payload = _WEBHOOK_Q.get()
        try:
            _send_webhook(url, payload)
        except Exception as _e:
            _log.warning(f"Webhook dispatcher error: {_e}")


def _enqueue_webhook(url: str, payload: dict):
    """Enqueue a flap event for webhook delivery. When saturated (queue full),
    drops the new event and logs a WARN so operators can see overload."""
    global _WEBHOOK_DISPATCHER_STARTED
    from core.logger import log as _log
    with _WEBHOOK_LOCK:
        if not _WEBHOOK_DISPATCHER_STARTED:
            threading.Thread(target=_webhook_dispatcher_loop, daemon=True,
                             name="pw-webhook").start()
            _WEBHOOK_DISPATCHER_STARTED = True
    try:
        _WEBHOOK_Q.put_nowait((url, payload))
    except queue.Full:
        _log.warning(f"Webhook queue full (cap=100), dropping event for {url}")


class MonitorState:
    def __init__(self):
        self._lock            = threading.RLock()
        self.devices          = {}
        self._did_ctr         = 0
        self._sse             = []
        self._sse_registered  = {}   # Queue → monotonic timestamp of subscribe()
        # Set true once shutdown teardown begins (server.py, after the bounded
        # probe drain). Late results from wedged probe workers that finish
        # AFTER this point — e.g. a VMware probe sitting on its 60s hard cap —
        # are dropped in _process_result so they don't dispatch alerts or write
        # to an already-closed DB pool ("PostgreSQL pool is closed" spam +
        # false "missing template" warnings when template lookups return None).
        self._shutting_down   = False
        self._executor  = concurrent.futures.ThreadPoolExecutor(
            max_workers=64, thread_name_prefix='pw-sensor'
        )
        self._scheduler = _SensorScheduler(self._executor, self._run_once)
        # Bumped on every alert profile/template write — sensors compare this
        # to their cached _resolved_profile_ver to know when to re-resolve.
        self._profile_cache_ver = 0
        # Startup grace (settings: startup_grace_s): down/threshold events
        # born in the first seconds after boot are parked here instead of
        # emitted — cold vCenter sessions and slow first probes otherwise
        # spray events that auto-resolve one cycle later. _flush_grace()
        # emits whatever is STILL bad when the window closes (with the
        # original transition timestamps); blips vanish without a trace.
        self._grace_active = False        # parking on/off (not time-based)
        self._grace_min_end = 0.0         # earliest flush time (configured grace)
        self._grace_hardcap = 0.0         # latest flush time (safety bound)
        self._grace_parked = {}           # (did, sid) → {kind: entry}
        self._grace_lock   = threading.Lock()

    # ── Startup grace ─────────────────────────────────────────────
    def begin_startup_grace(self):
        """Arm the startup grace window. Called once from server.py main()
        after settings are loaded; startup_grace_s=0 disables.

        The window does NOT close on a blind timer: it stays open until the
        configured grace has elapsed AND every central sensor has produced
        its first post-boot result (so slow first probes — cold vCenter,
        big SNMP walks — get parked, not emitted after the window). A hard
        cap bounds the wait so a permanently-stuck sensor can't defer real
        alerts forever."""
        try:
            grace = max(0, int(float(_cfg("startup_grace_s", "60") or 0)))
        except (TypeError, ValueError):
            grace = 60
        if not grace:
            return
        now = time.time()
        self._grace_active  = True
        self._grace_min_end = now + grace
        # Cap the first-cycle wait: never defer events past this even if some
        # sensor never reports (offline probe, hung host).
        self._grace_hardcap = now + max(grace * 2, 180)
        t = threading.Timer(grace + 1.0, self._maybe_flush_grace)
        t.daemon = True
        t.start()
        log_sensors.info(
            f"Startup grace armed: down/threshold events deferred for ~{grace}s "
            f"(until the first probe cycle completes, capped at "
            f"{int(self._grace_hardcap - now)}s); still-failing sensors emit "
            f"with their true timestamps")

    def _in_grace(self) -> bool:
        return self._grace_active

    def _first_cycle_done(self) -> bool:
        """True once every running central sensor has reported ≥1 result since
        boot. Probe-measured sensors report via checkin (also bumps total);
        an offline probe's sensors never do — the hard cap covers that."""
        with self._lock:
            for dev in self.devices.values():
                for s in dev.sensors.values():
                    if s.running and getattr(s, "total", 0) < 1:
                        return False
        return True

    def _maybe_flush_grace(self):
        """Timer callback: flush once the grace minimum has elapsed and the
        first probe cycle is done (or the hard cap is hit); otherwise re-arm
        a short re-check. Keeps parking active until the real flush."""
        if not self._grace_active:
            return
        now = time.time()
        if now < self._grace_hardcap and not self._first_cycle_done():
            t = threading.Timer(3.0, self._maybe_flush_grace)
            t.daemon = True
            t.start()
            return
        self._flush_grace()

    def _grace_park(self, did, sid, kind, entry):
        with self._grace_lock:
            self._grace_parked.setdefault((did, sid), {})[kind] = entry

    def _grace_unpark(self, did, sid, kind):
        """Pop a parked entry (recovery during grace ⇒ the blip dissolves).
        Cheap no-op outside grace — the dict is empty then."""
        if not self._grace_parked:
            return None
        with self._grace_lock:
            kinds = self._grace_parked.get((did, sid))
            if not kinds:
                return None
            entry = kinds.pop(kind, None)
            if not kinds:
                self._grace_parked.pop((did, sid), None)
            return entry

    def _grace_has(self, did, sid) -> bool:
        if not self._grace_parked:
            return False
        with self._grace_lock:
            return (did, sid) in self._grace_parked

    def _flush_grace(self):
        """End of startup grace: emit parked incidents whose sensor is still
        in the bad state; everything else was a restart blip and is dropped.
        Runs once on a Timer thread."""
        from db import db_log_flap, _db_enqueue
        from db.events import db_auto_resolve_flap
        self._grace_active = False
        with self._grace_lock:
            parked = self._grace_parked
            self._grace_parked = {}
        if not parked:
            return
        emitted = 0
        total = sum(len(kinds) for kinds in parked.values())
        for (did, sid), kinds in parked.items():
            with self._lock:
                dev = self.devices.get(did)
                s = dev.sensors.get(sid) if dev else None
            if not s:
                continue
            e = kinds.get("down")
            if e and s._alerted_down:
                flap = e["flap"]
                log_sensors.warning(
                    f"DOWN (confirmed after startup grace): "
                    f"{flap.get('dname')}/{flap.get('sname')} — {flap.get('detail')}")
                self._broadcast("flap_down", flap)
                _db_enqueue(lambda _f=flap: db_log_flap(_f))
                if e.get("webhook"):
                    _enqueue_webhook(e["webhook"], flap)
                emitted += 1
            e = kinds.get("thr")
            if e and s._threshold_state == e.get("state"):
                log_sensors.warning(
                    f"THRESHOLD {str(e.get('state')).upper()} (confirmed after "
                    f"startup grace): {e['flap'].get('dname')}/{e['flap'].get('sname')}")
                self._broadcast(e["evt"], e["evt_data"])
                _db_enqueue(lambda _f=e["flap"]: db_log_flap(_f))
                if e.get("resolve_prev"):
                    _d, _s2, _t, _dir = e["resolve_prev"]
                    _db_enqueue(lambda _a=_d, _b=_s2, _c=_t, _e=_dir:
                                db_auto_resolve_flap(_a, _b, _c, directions=(_e,)))
                emitted += 1
        log_sensors.info(
            f"Startup grace ended: {emitted} still-failing incident(s) emitted, "
            f"{total - emitted} restart blip(s) suppressed")

    def get_runtime_snapshot(self) -> dict:
        """Cheap read-only snapshot for the Diagnostics tab. No locks held
        beyond brief peeks — values are best-effort.
        """
        try:
            heap_len = len(self._scheduler._heap)
            tomb_len = len(self._scheduler._tombstones)
        except Exception:
            heap_len = tomb_len = 0
        try:
            sse_count = len(self._sse)
        except Exception:
            sse_count = 0
        try:
            worker_max = self._executor._max_workers
        except Exception:
            worker_max = 0
        flush_ms = flush_rows = 0
        try:
            from db.samples import _last_flush_ms, _last_flush_rows
            flush_ms   = _last_flush_ms
            flush_rows = _last_flush_rows
        except Exception:
            pass
        # v0.9.7: peak-rate coverage — % of last-hour raw samples that carry
        # a populated rate column. Near-0% right after deploy (rate computed
        # forward-looking only); should climb to ~95%+ within 2 probe cycles
        # on a counter-heavy deployment.
        peak_rate_coverage = None
        try:
            from db.backend import is_pg
            if is_pg():
                from db.pg_pool import pg_cursor
                with pg_cursor("logs") as cur:
                    cur.execute(
                        "SELECT COUNT(*) AS n, COUNT(rate) AS nr "
                        "FROM sensor_samples WHERE ts > %s",
                        (time.time() - 3600,),
                    )
                    r = cur.fetchone()
                    if r and r["n"]:
                        peak_rate_coverage = round(100.0 * r["nr"] / r["n"], 1)
            else:
                import sqlite3
                from core.config import LOGS_DB_PATH
                c = sqlite3.connect(LOGS_DB_PATH, timeout=2)
                try:
                    row = c.execute(
                        "SELECT COUNT(*), COUNT(rate) FROM sensor_samples WHERE ts > ?",
                        (time.time() - 3600,),
                    ).fetchone()
                    if row and row[0]:
                        peak_rate_coverage = round(100.0 * row[1] / row[0], 1)
                finally:
                    c.close()
        except Exception:
            pass
        return {
            "scheduler_heap":       heap_len,
            "scheduler_tombstones": tomb_len,
            "sse_listeners":        sse_count,
            "worker_max":           worker_max,
            "last_flush_ms":        flush_ms,
            "last_flush_rows":      flush_rows,
            "peak_rate_coverage":   peak_rate_coverage,
        }

    def _next_did(self):
        self._did_ctr += 1
        return f"d{self._did_ctr}"

    def add_device(self, name, host, group="Default Group", site=""):
        with self._lock:
            did = self._next_did()
            self.devices[did] = Device(did, name, host, group, site=site)
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
                   port=None, url=None, interval=60, timeout=10,
                   verify_ssl=True, snmp_community="public",
                   snmp_oid="1.3.6.1.2.1.1.1.0", snmp_version="2c",
                   fail_after=3, recover_after=2,
                   warn_ms=None, crit_ms=None, loss_warn_pct=0, loss_crit_pct=0,
                   keyword="", keyword_case=False, banner_regex="", snmp_unit="",
                   snmp_scale=0, snmp_oid2="", snmp_pct_mode="",
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
                   radius_username="", radius_password="", radius_nas_id="",
                   snmp_v3_user="", snmp_v3_level="",
                   snmp_v3_auth_proto="", snmp_v3_auth_pass="",
                   snmp_v3_priv_proto="", snmp_v3_priv_pass="",
                   snmp_v3_context=""):
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
                       snmp_scale=snmp_scale, snmp_oid2=snmp_oid2,
                       snmp_pct_mode=snmp_pct_mode,
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
                       radius_nas_id=radius_nas_id,
                       snmp_v3_user=snmp_v3_user, snmp_v3_level=snmp_v3_level,
                       snmp_v3_auth_proto=snmp_v3_auth_proto,
                       snmp_v3_auth_pass=snmp_v3_auth_pass,
                       snmp_v3_priv_proto=snmp_v3_priv_proto,
                       snmp_v3_priv_pass=snmp_v3_priv_pass,
                       snmp_v3_context=snmp_v3_context)
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
                        "http_expected_status", "cert_warn_days", "cert_crit_days",
                        "warn_ms", "crit_ms", "loss_warn_pct", "loss_crit_pct",
                        "keyword", "keyword_case", "banner_regex", "alerts_muted",
                        "snmp_unit", "snmp_scale", "snmp_oid2", "snmp_pct_mode",
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
                        "snmp_v3_user", "snmp_v3_level",
                        "snmp_v3_auth_proto", "snmp_v3_auth_pass",
                        "snmp_v3_priv_proto", "snmp_v3_priv_pass",
                        "snmp_v3_context",
                        "anomaly_enabled", "anomaly_sensitivity", "anomaly_min_samples",
                        "probe_id"]
            _anom_enabled_before = int(getattr(s, "anomaly_enabled", 0) or 0)
            _anom_sens_before    = int(getattr(s, "anomaly_sensitivity", 2) or 2)
            # Numeric fields must never land as "" on the Sensor — PG's INTEGER
            # columns reject empty strings at save time. Treat "" as "not provided".
            _nullable_int_fields = {"port", "warn_ms", "crit_ms"}
            _int_fields = {"interval", "timeout", "http_expected_status",
                           "cert_warn_days", "cert_crit_days",
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

    def start_sensor(self, did, sid, first_delay=0.0):
        from core.probe_assign import effective_probe
        with self._lock:
            # Check-and-set must happen under the lock: two concurrent calls
            # (UI double-click, start_device racing start_sensor) would both
            # pass an unlocked check and spawn duplicate probe chains that
            # each reschedule forever — doubling samples and events.
            dev = self.devices.get(did)
            if not dev: return
            s = dev.sensors.get(sid)
            if not s or s.running: return
            s.running = True
            dev.invalidate_status()
            s._stopped.clear()
            # Distributed probes: sensors measured by a remote agent get no
            # central probe chain — their results arrive via /api/agent/checkin.
            # running stays True so the UI doesn't render them as paused.
            _eff = effective_probe(dev, s)
            s._sched_mode = "probe" if _eff else "central"
        if _eff:
            _bump_probe_config(_eff)   # agent re-pulls config → starts probing
        elif s.stype == "vmware":
            # Give vCenter prewarm a head start so the FIRST probe reuses a
            # warm session (cold perfManager/inventory otherwise returns
            # "VM not found" / "metric not available" → false DOWN that
            # recovers a cycle later). Mirrors the agent's vmware stagger.
            self._scheduler.schedule(did, sid, max(12.0, first_delay))
        elif first_delay > 0:
            # Bulk start (boot restore / 'Start all'): spread the first probe so
            # a few hundred sensors don't fire one synchronized burst (executor
            # congestion → ping 4s timeouts; vCenter cold-cache reconnect herd).
            self._scheduler.schedule(did, sid, first_delay)
        else:
            self._executor.submit(self._run_once, did, sid)

    def stop_sensor(self, did, sid):
        from core.probe_assign import effective_probe
        _eff = ""
        with self._lock:
            dev = self.devices.get(did)
            if dev:
                s = dev.sensors.get(sid)
                if s:
                    s.running = False
                    dev.invalidate_status()
                    _eff = effective_probe(dev, s)
        self._scheduler.cancel(did, sid)
        # Scheduler entry will be ignored — _run_once checks s.running at entry.
        # Remote sensors: the agent must drop the sensor from its schedule too.
        if _eff:
            _bump_probe_config(_eff)

    def start_device(self, did):
        with self._lock:
            dev = self.devices.get(did)
            sids = list(dev.sensors) if dev else []
        for sid in sids:
            self.start_sensor(did, sid)

    def stop_device(self, did, resolve_events=True):
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
        # Auto-resolve active flap events — device is intentionally stopped.
        # Skipped at process shutdown (resolve_events=False): restarting the
        # monitor mid-outage must not close open incidents — that destroys
        # downtime continuity / ack state and re-fires fresh flaps on boot.
        if resolve_events:
            for sid in sids:
                _logs_enqueue(lambda d=did, s_=sid: db_resolve_flaps_by_sensor(d, s_))

    def start_sensors_staggered(self, pairs):
        """Start many sensors with their first probe spread across a window so a
        bulk start (boot restore or 'Start all') doesn't fire one synchronized
        probe burst — which congests the executor (ping 4s timeouts) and
        stampedes vCenter sessions (cold-cache reconnect herd). Returns the
        number started. Single starts elsewhere stay immediate (first_delay=0)."""
        _STAGGER_S = 30.0
        n = len(pairs)
        if not n:
            return 0
        window = _STAGGER_S if n > 1 else 0.0
        for i, (did, sid) in enumerate(pairs):
            self.start_sensor(did, sid, first_delay=(i / n) * window)
        return n

    def start_all(self):
        pairs = [(did, sid) for did in list(self.devices)
                 for sid in list(self.devices[did].sensors)]
        self.start_sensors_staggered(pairs)

    def stop_all(self, resolve_events=True):
        for did in list(self.devices):
            self.stop_device(did, resolve_events=resolve_events)

    def _run_once(self, did, sid):
        """Safety wrapper around the probe cycle.

        The executor never reads the returned Future, so an exception escaping
        the cycle body would (a) vanish without a log line and (b) skip the
        reschedule at the end of the body — silently freezing the sensor
        forever while the UI keeps showing its last status. Catch, log, and
        reschedule so one bad cycle can never kill a sensor's probe loop.
        """
        try:
            self._run_once_inner(did, sid)
        except Exception:
            log_sensors.exception(
                f"Probe cycle error for {did}/{sid} — sensor rescheduled")
            try:
                with self._lock:
                    dev = self.devices.get(did)
                    s   = dev.sensors.get(sid) if dev else None
                if s is not None:
                    if s.running:
                        self._scheduler.schedule(did, sid, max(float(s.interval or 5), 1.0))
                    else:
                        s._stopped.set()
            except Exception:
                log_sensors.error(f"Probe cycle recovery failed for {did}/{sid}")

    def _run_once_inner(self, did, sid):
        from core.probe_assign import effective_probe

        with self._lock:
            dev = self.devices.get(did)
            s   = dev.sensors.get(sid) if dev else None
        if not s or not s.running:
            if s: s._stopped.set()
            return

        # Distributed probes: a sensor measured by a remote agent has no
        # central probe chain — results arrive via /api/agent/checkin and are
        # injected through _process_result(). End this chain; running stays
        # True so the UI doesn't render the sensor as paused.
        if effective_probe(dev, s):
            s._sched_mode = "probe"
            s._stopped.set()
            return

        dev = self.devices.get(did)   # re-fetch (unprotected, Device is stable)
        if dev is None:               # device deleted while this probe was queued
            s._stopped.set()
            return
        # Hard timeout guard: if s.probe() hangs (misbehaving stack, stuck DNS/TLS),
        # run it on an orphan daemon thread and abandon it after a generous
        # upper bound so the worker returns to the pool instead of staying pinned.
        # Per-stype ceilings cover the worst well-behaved case for each probe:
        #   - VMware:  SmartConnect caps at 60s (vmware/client.py), add metric fetch
        #   - SMTP:    5-stage probe (connect/EHLO/STARTTLS/AUTH/MAILFROM) × timeout
        #   - SSH/SFTP: paramiko auth + optional file stat/checksum can run long
        # For fast probes (ping/tcp/http/dns/snmp/tls/banner/radius), their own
        # internal timeouts finish long before the cap — it only triggers when
        # the stack is truly stuck.
        _PROBE_HARD_CAP_S = {
            "vmware": 90, "smtp": 60, "ssh": 45, "sftp": 60,
        }
        _cap = max((s.timeout or 5) + 3, _PROBE_HARD_CAP_S.get(s.stype, 15))
        _prev_pt = getattr(s, "_inflight_probe", None)
        if _prev_pt is not None and _prev_pt.is_alive():
            # The previous cycle's probe blew past its hard cap and is STILL
            # stuck (its internal timeouts never fired). Spawning another
            # thread would leak one unkillable thread per interval against a
            # truly hung target. Count this cycle as a failure; re-check the
            # orphan next interval.
            result = {"ok": False, "ms": None,
                      "detail": "Probe exceeded hard timeout (still running)",
                      "value": None}
        else:
            s._inflight_probe = None
            _probe_result = [None]
            def _probe_runner():
                try:
                    _probe_result[0] = s.probe()
                except Exception as _pe:
                    _probe_result[0] = {"ok": False, "ms": None,
                                        "detail": f"probe crashed: {type(_pe).__name__}",
                                        "value": None}
            _pt = threading.Thread(target=_probe_runner, daemon=True,
                                   name=f"pw-probe-{did}-{sid}")
            _pt.start()
            _pt.join(timeout=_cap)
            if _probe_result[0] is None:
                s._inflight_probe = _pt   # remember the orphan — don't stack another
                log_sensors.warning(f"Probe hard-timeout ({_cap}s): "
                                    f"{dev.name if dev else did}/{s.name} "
                                    f"({s.host}) — worker released, orphan thread continues")
                result = {"ok": False, "ms": None,
                          "detail": "Probe exceeded hard timeout", "value": None}
            else:
                result = _probe_result[0]

        self._process_result(did, sid, result, ts_float=time.time(), source="local")

        # Release thread immediately — scheduler fires next probe after interval.
        # Re-check the assignment: if the sensor moved to a remote probe while
        # this cycle was in flight, end the chain instead of rescheduling
        # (schedule() would clear the tombstone apply_probe_assignment planted).
        if s.running and not effective_probe(dev, s):
            self._scheduler.schedule(did, sid, s.interval)
        elif s.running:
            s._sched_mode = "probe"
            s._stopped.set()
        else:
            self._broadcast("log", {"did": did, "sid": sid,
                                     "msg": f"[STOP] {s.name}", "type": "info"})
            s._stopped.set()

    def _process_result(self, did, sid, result, ts_float, source="local",
                        state_eval=True):
        """Run one probe result through the full state machine.

        Everything downstream of probe execution lives here: sample
        buffering, debounce, typed-SNMP transitions, threshold evaluation,
        flap logging, alert profiles, and SSE broadcast. Local probe chains
        call it with source='local'; the agent checkin handler injects
        remote results with source='probe'.

        source='probe' contract:
          - the caller (routes/agent.py) holds a per-probe lock, making this
            thread the sensor's single writer (probe-assigned sensors have no
            central chain);
          - result may carry agent-computed 'rate' and 'snmp_type' — the
            server never derives counter rates from remote values, since
            arrival time says nothing about sample spacing;
          - ts_float is the agent-side probe timestamp; a monotonic
            per-sensor guard drops duplicates/out-of-order replays;
          - state_eval=False marks a stale backfilled result: the sample is
            persisted for charts, but no sensor state, events, or alerts are
            touched (offline spool catch-up must not replay history).

        Returns True when the result was applied, False when dropped.
        """
        # Shutdown teardown has begun: drop late results from probe workers that
        # finished after the bounded drain (e.g. wedged on a 60s hard cap). The
        # sample buffer is already flushed and the DB pool is closing, so
        # processing them now only dispatches alerts and spams the closed pool.
        if self._shutting_down:
            return False

        # Import here to avoid circular import at module load time
        from db import db_log_err, db_log_flap, db_buffer_sample, _db_enqueue
        from db.events import db_auto_resolve_flap

        with self._lock:
            dev = self.devices.get(did)
            s   = dev.sensors.get(sid) if dev else None
        if not s or dev is None:
            return False

        # Sensor was stopped while this probe was in flight. _run_once_inner
        # guards at entry, but a probe that already started when stop_sensor
        # ran still lands here — process it and we'd record a sample, flip
        # alive, and possibly fire a flap/alert for a sensor the user just
        # paused. Drop it: a stopped sensor records nothing.
        if not s.running:
            return False

        if source == "probe":
            if s._last_processed_ts is None:
                # First remote injection since boot: seed from the newest
                # persisted sample so an agent re-sending a batch whose ack
                # was lost across a server restart can't double-insert.
                s._last_processed_ts = _max_sample_ts(did, sid)
            if ts_float <= s._last_processed_ts:
                return False   # duplicate / out-of-order replay
        s._last_processed_ts = ts_float

        # v1.5: static value scale — normalize raw SNMP gauge readings
        # (deci-°C, KB, RFC-3433 milli-volts) into display units before
        # anything downstream sees them: the sample buffer (live AND stale
        # backfill), last_value, threshold evaluation, and the typed-
        # transition detector. Applied centrally so local and remote
        # (probe-injected) results scale identically. Counter types are
        # exempt — the agent computes remote rates from raw values and knows
        # nothing of the scale, so scaling local counters would diverge from
        # remote ones.
        if s.stype == "snmp" and result.get("ok") and result.get("value") is not None:
            _sc = getattr(s, "snmp_scale", 0) or 0
            if _sc and _sc != 1 and \
                    str(result.get("snmp_type", "")).lower() not in _COUNTER_TYPES:
                try:
                    _sv = float(result["value"]) / _sc
                    result = dict(result)   # never mutate the caller's dict
                    if _sv == int(_sv) and abs(_sv) < 1e15:
                        result["value"] = str(int(_sv))
                    else:
                        result["value"] = ("%.4f" % _sv).rstrip("0").rstrip(".")
                    result["detail"] = result["value"]
                except (ValueError, TypeError):
                    pass

        if not state_eval:
            # Stale backfill — persist the sample for gapless charts, touch
            # nothing else (no debounce, no flaps, no alerts, no broadcast).
            _bf_val = (str(result.get("value", ""))
                       if result.get("value") is not None else None)
            db_buffer_sample(did, sid, result["ok"], result["ms"], _bf_val,
                             ts_float, rate=result.get("rate"))
            return True

        if s.total == 0:
            self._broadcast("log", {"did": did, "sid": sid,
                                     "msg": f"[START] {s.name} on {s.host}", "type": "info"})

        s.total += 1
        _ts = datetime.datetime.fromtimestamp(
            ts_float, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _ts_float = ts_float
        _muted = s.alerts_muted or dev.alerts_muted or is_group_muted(dev.group)
        # HTTPS cert-expiry: the probe (local or agent) reports days remaining
        # only for https sensors with thresholds set. Absent ⇒ None ⇒ no cert
        # contribution to this probe's threshold evaluation.
        s._cert_days = result.get("cert_days")

        # Sample buffered after the ok/fail branch so s._last_rate reflects
        # THIS probe's rate (computed in the ok branch below for SNMP
        # counters), not the previous probe's.
        _ok_cap  = result["ok"]
        _ms_cap  = result["ms"]
        _val_cap = str(result.get("value", "")) if result.get("value") is not None else None
        _ts_f_cap = _ts_float
        _did_cap, _sid_cap = did, sid

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
            # Persist the raw ASN.1 type on the sensor so the frontend can
            # branch its rendering (enum / gauge / duration / text) without
            # needing per-probe type info in every sample.
            _raw_stype = result.get("snmp_type", "")
            if s.stype == "snmp" and _raw_stype:
                s.snmp_type = _raw_stype
            if source == "probe":
                # Remote results carry an agent-computed rate — the agent owns
                # counter continuity across checkins and spool gaps. Deriving a
                # rate here from arrival spacing would corrupt the delta.
                _agent_rate = result.get("rate")
                if s.stype == "snmp" and _stype in _COUNTER_TYPES and _raw_val is not None:
                    if _agent_rate is not None:
                        try:
                            s._last_rate = float(_agent_rate)
                            s.last_value = _fmt_rate(s._last_rate, s.snmp_unit)
                        except (ValueError, TypeError):
                            s._last_rate = None
                            s.last_value = None
                    else:
                        s.last_value  = None   # agent's first poll — no rate yet
                        s._last_rate  = None
                        s.last_detail = "—"
                else:
                    s.last_value = _raw_val
                    s._last_rate = None
            elif s.stype == "snmp" and _stype in _COUNTER_TYPES and _raw_val is not None:
                try:
                    _cur = int(_raw_val)
                    _now = _ts_float
                    if s._snmp_prev is not None and s._snmp_prev_ts is not None:
                        _elapsed = _now - s._snmp_prev_ts
                        _delta   = _cur - s._snmp_prev
                        _rate    = None
                        if _elapsed < 1.0:
                            # Sub-second elapsed → division would amplify timing
                            # noise into huge rate spikes. Skip this sample.
                            pass
                        elif _delta < 0:
                            # Counter32 wraps every ~34s at 1Gbps — adding 2^32 is
                            # legitimate. Counter64 wraps take decades; a negative
                            # delta there means the agent reset (reboot, ifIndex
                            # reuse, snmpd restart) and the rate is unknowable.
                            if _stype == "counter32":
                                _delta += 2**32
                                _rate = _delta / _elapsed
                            # else: leave _rate=None for counter64 reset
                        else:
                            _rate = _delta / _elapsed
                        # Absolute sanity ceiling: 1.25e11 B/s = 1 TB/s = 8 Tbps.
                        # No physical interface today exceeds this; anything
                        # above is garbage from a missed reset or clock anomaly.
                        if _rate is not None and _rate > 1.25e11:
                            _rate = None
                        if _rate is not None:
                            s._last_rate = _rate
                            s.last_value = _fmt_rate(_rate, s.snmp_unit)
                        else:
                            s.last_value = None
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
            # v0.9.7: typed SNMP event transition detector.  Emits synthetic
            # flap events for state changes the connectivity flap pipeline
            # misses because SNMP itself succeeded (interface goes down →
            # SNMP still answers with value=2 → s.alive stays True).  Each
            # category has its own trigger; all go through db_log_flap so
            # the Events tab and webhook path fire automatically.
            if s.stype == "snmp" and _raw_val is not None and not _muted:
                try:
                    _cat = _snmp_category_py(s.snmp_unit, _raw_stype, s.snmp_oid)
                except Exception:
                    _cat = None
                if _cat == "enum_state":
                    try: _cur_code = str(int(float(_raw_val)))
                    except (ValueError, TypeError): _cur_code = None
                    if _cur_code is not None:
                        _prev_code = s._prev_enum_code
                        if _prev_code is not None and _cur_code != _prev_code:
                            legend  = _effective_enum_legend_py(s.snmp_unit, s.snmp_oid)
                            # First-listed code = healthy (mirrors _enum_primary_code_py)
                            primary = next(iter(legend), "1") if legend else "1"
                            prev_lbl = legend.get(_prev_code, f"state {_prev_code}")
                            cur_lbl  = legend.get(_cur_code,  f"state {_cur_code}")
                            if _cur_code != primary and _prev_code == primary:
                                _dir, _msg = "state_down", f"State changed: {prev_lbl} → {cur_lbl}"
                                log_sensors.warning(f"STATE DOWN: {dev.name}/{s.name} ({s.host}) — {_msg}")
                            elif _cur_code == primary and _prev_code != primary:
                                _dir, _msg = "state_up", f"State recovered: {prev_lbl} → {cur_lbl}"
                                log_sensors.info(f"STATE UP: {dev.name}/{s.name} ({s.host}) — {_msg}")
                            else:
                                _dir, _msg = "state_change", f"State changed: {prev_lbl} → {cur_lbl}"
                                log_sensors.info(f"STATE CHANGE: {dev.name}/{s.name} ({s.host}) — {_msg}")
                            _flap = {
                                "did": did, "sid": sid, "dname": dev.name, "sname": s.name,
                                "host": s.host, "stype": s.stype, "ts": _ts,
                                "detail": _msg, "direction": _dir,
                                "grp": dev.group, "consec_count": 1,
                            }
                            _flap["raw_data"] = json.dumps(build_flap_raw_data(
                                s, result, _dir,
                                {"from_state": prev_lbl, "to_state": cur_lbl,
                                 "from_code": _prev_code, "to_code": _cur_code,
                                 "legend": legend}
                            ))
                            # Drive _threshold_state from the typed transition so
                            # Device.status, the alert profile engine, and the
                            # right-side live panel all see this as a real incident.
                            # We deliberately do NOT broadcast threshold_critical /
                            # threshold_warning / threshold_ok here — the flap_state_*
                            # broadcast above is the single source of truth for the
                            # event; piping a second broadcast would re-introduce
                            # the duplicate alert row we just removed.
                            _thr_target = {"state_down": "crit", "state_change": "warn",
                                           "state_up": "ok"}.get(_dir)
                            if _thr_target is not None and _thr_target != s._threshold_state:
                                s._threshold_state = _thr_target
                                if _thr_target == "ok":
                                    s._threshold_triggered_ts     = None
                                    s._threshold_recovery_pending = False
                                    s._alerted_down               = False
                                    s._email_sent_down            = False
                                    s._ack_by                     = ""
                                    s._ack_at                     = 0.0
                                else:
                                    s._threshold_triggered_ts     = _ts_float
                                    s._threshold_recovery_pending = False
                                dev.invalidate_status()
                            self._broadcast(f"flap_{_dir}", _flap)
                            _db_enqueue(lambda _f=_flap: db_log_flap(_f))
                            if _dir == "state_up":
                                _db_enqueue(lambda _d=did, _s=sid, _t=_ts: db_auto_resolve_flap(
                                    _d, _s, _t, directions=("state_down", "state_change")))
                            if dev.webhook_url and _dir in ("state_down", "state_change"):
                                _enqueue_webhook(dev.webhook_url, _flap)
                        s._prev_enum_code = _cur_code
                elif _cat == "time_duration":
                    try: _cur_ticks = int(float(_raw_val))
                    except (ValueError, TypeError): _cur_ticks = None
                    if _cur_ticks is not None:
                        _prev_ticks_v = s._prev_ticks
                        # Threshold 100 ticks = 1s — avoids false positives
                        # from small measurement jitter / TimeTicks wrap on
                        # the rare 2^32/100s boundary (~497 days).
                        if _prev_ticks_v is not None and _cur_ticks < _prev_ticks_v - 100:
                            _prev_up = _fmt_duration_s(_prev_ticks_v / 100)
                            _msg = f"Device rebooted (was up {_prev_up})"
                            log_sensors.warning(f"REBOOT: {dev.name}/{s.name} ({s.host}) — {_msg}")
                            _flap = {
                                "did": did, "sid": sid, "dname": dev.name, "sname": s.name,
                                "host": s.host, "stype": s.stype, "ts": _ts,
                                "detail": _msg, "direction": "reboot",
                                "grp": dev.group, "consec_count": 1,
                            }
                            _flap["raw_data"] = json.dumps(build_flap_raw_data(
                                s, result, "reboot",
                                {"prev_uptime_s": _prev_ticks_v / 100,
                                 "new_uptime_s": _cur_ticks / 100}
                            ))
                            self._broadcast("flap_reboot", _flap)
                            _db_enqueue(lambda _f=_flap: db_log_flap(_f))
                            if dev.webhook_url:
                                _enqueue_webhook(dev.webhook_url, _flap)
                        s._prev_ticks = _cur_ticks
                elif _cat == "text":
                    _cur_text = str(_raw_val)
                    _prev_text = s._prev_text_value
                    if _prev_text is not None and _cur_text != _prev_text:
                        _prev_disp = (_prev_text[:60] + "…") if len(_prev_text) > 60 else _prev_text
                        _cur_disp  = (_cur_text[:60]  + "…") if len(_cur_text)  > 60 else _cur_text
                        _msg = f"Value changed: {_prev_disp} → {_cur_disp}"
                        log_sensors.info(f"VALUE CHANGE: {dev.name}/{s.name} ({s.host}) — {_msg}")
                        _flap = {
                            "did": did, "sid": sid, "dname": dev.name, "sname": s.name,
                            "host": s.host, "stype": s.stype, "ts": _ts,
                            "detail": _msg, "direction": "value_change",
                            "grp": dev.group, "consec_count": 1,
                        }
                        _flap["raw_data"] = json.dumps(build_flap_raw_data(
                            s, result, "value_change",
                            {"prev_value": _prev_text, "new_value": _cur_text}
                        ))
                        self._broadcast("flap_value_change", _flap)
                        _db_enqueue(lambda _f=_flap: db_log_flap(_f))
                        if dev.webhook_url:
                            _enqueue_webhook(dev.webhook_url, _flap)
                    s._prev_text_value = _cur_text
            s.history.append(result["ms"])
            _log_msg = s.last_value if (s.stype == "snmp" and s._last_rate is not None and s.last_value) else result["detail"]
            # ── Debounce: track consecutive successes ──
            s._consec_fail = 0
            s._consec_ok  += 1
            if s._alerted_down and s._consec_ok >= s.recover_after:
                _flap_dur = None
                if s._down_since_ts:
                    _flap_dur = int(_ts_float - s._down_since_ts)
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
                    _rec_cap = dict(rec_data)
                    if self._grace_unpark(did, sid, "down"):
                        # Down began inside startup grace and was never
                        # emitted — the restart blip dissolves eventlessly.
                        log_sensors.info(f"RECOVERED (startup-grace blip suppressed): {dev.name}/{s.name} ({s.host})")
                    else:
                        self._broadcast("flap_recovered", rec_data)
                        log_sensors.info(f"RECOVERED: {dev.name}/{s.name} ({s.host})")
                    # Resolve runs either way — no-op for a parked blip, but
                    # closes any pre-restart unresolved down row.
                    _db_enqueue(lambda: db_auto_resolve_flap(
                        _rec_cap["did"], _rec_cap["sid"], _rec_cap["ts"],
                        directions=("down",)
                    ))
                s._alerted_down    = False
                s._email_sent_down = False
                # ACK covered this incident only — a future down starts loud.
                # Kept while a threshold incident is still open on the sensor.
                if s._threshold_state == "ok":
                    s._ack_by = ""
                    s._ack_at = 0.0
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
            s._last_rate  = None   # v0.9.7: don't persist stale rate on probe failure
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
                flap_data["raw_data"] = json.dumps(build_flap_raw_data(
                    s, result, "down", {"consec_fail": s._consec_fail}
                ))
                if not _muted:
                    if self._in_grace():
                        # Startup grace: park — _flush_grace emits it (with
                        # this original ts) only if still down at window end.
                        self._grace_park(did, sid, "down", {
                            "flap": dict(flap_data),
                            "webhook": dev.webhook_url or "",
                        })
                        log_sensors.info(f"DOWN (startup grace, parked): {dev.name}/{s.name} ({s.host}) — {result['detail']}")
                    else:
                        self._broadcast("flap_down", flap_data)
                        log_sensors.warning(f"DOWN: {dev.name}/{s.name} ({s.host}) — {result['detail']}")
                        _flap_cap = dict(flap_data)
                        _db_enqueue(lambda: db_log_flap(_flap_cap))
                        if dev.webhook_url:
                            _enqueue_webhook(dev.webhook_url, _flap_cap)
                s._alerted_down    = True
                s._down_since_ts   = _ts_float

        # ── Log sample to DB (non-blocking) ──
        # v0.9.7: pass rate so counter-type SNMP sensors store the per-probe
        # rate alongside the raw counter value.  None for non-counter sensors.
        db_buffer_sample(_did_cap, _sid_cap, _ok_cap, _ms_cap, _val_cap, _ts_f_cap,
                         rate=getattr(s, "_last_rate", None))

        # ── Threshold state check (transitions only) ──
        _new_thr = "ok"
        _thr_chk = None
        if result["ok"]:
            if s.stype == 'snmp':
                # Skip numeric threshold comparison for non-numeric SNMP categories.
                # Why: enum codes (1=up,2=down,...), uptime ticks, and OCTET STRINGs
                # are not numeric metrics — comparing the raw enum code (e.g. 2)
                # against crit_ms triggers a meaningless "Threshold Alert (crit): 2"
                # alongside the proper state_down event from the typed detector above.
                try:
                    _cat_skip = _snmp_category_py(s.snmp_unit, s.snmp_type, s.snmp_oid) in (
                        "enum_state", "time_duration", "text"
                    )
                except Exception:
                    _cat_skip = False
                if _cat_skip:
                    # Typed detector owns _threshold_state for these categories.
                    # Preserve it so the transition block below stays a no-op
                    # (otherwise _new_thr=ok != threshold_state=crit would fire
                    # a spurious threshold_ok recovery on every probe).
                    _new_thr = s._threshold_state
                elif s._last_rate is not None:
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
            elif s.stype == 'tls':
                if s._last_rate is not None:
                    _u = s.snmp_unit
                    if _u in _BYTE_UNITS or _u == "":
                        _thr_chk = s._last_rate * 8 / 1_000_000
                    else:
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
        # ── HTTPS cert-expiry threshold (worst-of with latency/loss) ──
        # An http sensor can warn/crit on an approaching cert expiry while
        # still fast and up. Cert only *escalates* — if latency/loss already
        # set a worse-or-equal state, that keeps the attribution.
        s._cert_caused_thr = False
        if s.stype == "http" and result["ok"] and s._cert_days is not None:
            _cw = int(getattr(s, "cert_warn_days", 0) or 0)
            _cc = int(getattr(s, "cert_crit_days", 0) or 0)
            _cert_sev = "ok"
            if _cc and s._cert_days <= _cc:
                _cert_sev = "crit"
            elif _cw and s._cert_days <= _cw:
                _cert_sev = "warn"
            _rank = {"ok": 0, "warn": 1, "crit": 2}
            if _rank[_cert_sev] > _rank[_new_thr]:
                _new_thr = _cert_sev
                s._cert_caused_thr = True
        if _new_thr != s._threshold_state:
            # State CHANGED — reset counter, do full broadcast
            _prev_thr = s._threshold_state
            s._threshold_state = _new_thr
            dev.invalidate_status()
            s._consec_threshold = 1
            if _new_thr != "ok" and not _muted:
                s._threshold_recovery_pending = False
                s._threshold_triggered_ts = _ts_float
                _tevt = "threshold_critical" if _new_thr == "crit" else "threshold_warning"
                # Compute display unit + per-event value string up-front so that
                # both the SSE broadcast AND the persisted flap row carry the
                # same raw_data payload.
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
                if s._anom_caused_warn:
                    from monitoring.anomaly import format_anomaly_detail
                    _val_disp = format_anomaly_detail(s, s.last_ms)
                if s._cert_caused_thr:
                    _cd = s._cert_days
                    _val_disp = (f"Certificate expires in {_cd}d"
                                 if _cd is not None and _cd >= 0
                                 else f"Certificate expired {abs(_cd)}d ago")
                    _unit = 'days'
                # Resolve the flap direction once. Cert-expiry reuses the
                # threshold_* directions (the detail names the cause) so the
                # Events/ack/resolve pipeline needs no new direction.
                if _new_thr == "crit":
                    _thr_dir = "threshold_crit"
                elif s._anom_caused_warn:
                    _thr_dir = "anomaly_warn"
                else:
                    _thr_dir = "threshold_warn"
                # Pick the metric name + comparison values that match the cause.
                if s._cert_caused_thr:
                    _metric = "cert_days"
                    _ctx_actual = s._cert_days
                    _ctx_limit  = s.cert_crit_days if _new_thr == "crit" else s.cert_warn_days
                elif s._last_rate is not None:
                    _metric = "rate"
                    _ctx_actual = _thr_chk
                    _ctx_limit  = s.crit_ms if _new_thr == "crit" else s.warn_ms
                else:
                    _metric = "value" if s.stype in ("snmp", "tls", "vmware") else "ms"
                    _ctx_actual = _thr_chk
                    _ctx_limit  = s.crit_ms if _new_thr == "crit" else s.warn_ms
                _thr_ctx = {
                    "metric": _metric,
                    "actual": _ctx_actual,
                    "unit": _unit.strip() if _unit else "",
                    "limit": _ctx_limit,
                    "prev_state": _prev_thr,
                }
                _thr_raw_json = json.dumps(build_flap_raw_data(
                    s, result, _thr_dir, _thr_ctx
                ))
                _thr_evt_data = {
                    "did": did, "sid": sid, "dname": dev.name,
                    "sname": s.name, "host": s.host, "stype": s.stype,
                    "state": _new_thr, "ts": _ts,
                    "ms": s.last_ms, "loss_pct": s.loss_pct,
                    "grp": dev.group,
                    "consec_count": 1,
                    "raw_data": _thr_raw_json,
                    "detail": _val_disp,
                }
                _thr_flap = dict(_thr_evt_data)
                _thr_flap["direction"] = _thr_dir
                if self._in_grace():
                    # Startup grace: park instead of emitting — _flush_grace
                    # emits it only if the sensor is still in this state when
                    # the window closes. An escalation during grace simply
                    # replaces the parked entry (last state wins).
                    _resolve_prev = None
                    if _prev_thr in ("warn", "crit"):
                        _resolve_prev = (did, sid, _ts,
                                         "threshold_crit" if _prev_thr == "crit"
                                         else "threshold_warn")
                    self._grace_park(did, sid, "thr", {
                        "state": _new_thr, "evt": _tevt,
                        "evt_data": dict(_thr_evt_data), "flap": _thr_flap,
                        "resolve_prev": _resolve_prev,
                    })
                    log_sensors.info(f"THRESHOLD {_new_thr.upper()} (startup grace, parked): {dev.name}/{s.name} — {_val_disp}")
                else:
                    self._broadcast(_tevt, _thr_evt_data)
                    if _new_thr == "crit":
                        log_sensors.error(f"THRESHOLD CRIT: {dev.name}/{s.name} — {_val_disp} (limit {_ctx_limit}{_unit})")
                    elif s._anom_caused_warn:
                        log_sensors.warning(f"ANOMALY WARN: {dev.name}/{s.name} — {_val_disp}")
                    else:
                        log_sensors.warning(f"THRESHOLD WARN: {dev.name}/{s.name} — {_val_disp} (limit {_ctx_limit}{_unit})")
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
                    _thr_dur = int(_ts_float - s._threshold_triggered_ts)
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
                _thr_rec_ts = _ts
                _thr_rec_did = did
                _thr_rec_sid = sid
                # Threshold incident over — drop the ACK badge unless the
                # sensor is also down (that incident still owns it).
                if not s._alerted_down:
                    s._ack_by = ""
                    s._ack_at = 0.0
                if self._grace_unpark(did, sid, "thr"):
                    # Triggered and cleared inside startup grace — no event
                    # row was written, nothing to broadcast.
                    log_sensors.info(f"THRESHOLD OK (startup-grace blip suppressed): {dev.name}/{s.name}")
                else:
                    self._broadcast("threshold_ok", _thr_rec_data)
                    log_sensors.info(f"THRESHOLD OK: {dev.name}/{s.name} — value back within limits")
                # Resolve runs either way — no-op for a parked blip, but
                # closes any pre-restart unresolved threshold row.
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
        # Skipped while this sensor has a grace-parked incident: profiles fire
        # once the incident survives _flush_grace, with stage delays measured
        # from the real transition ts — so a "notify after 5 min" stage isn't
        # even delayed by the grace window.
        if not _muted and not self._grace_has(did, sid):
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
        return True

    def apply_probe_assignment(self, pairs):
        """React to probe_id changes on sensors / devices / sites.

        pairs is a list of (did, sid). For each sensor whose scheduling mode
        actually changed: newly remote → cancel its central schedule (an
        in-flight cycle exits at the next precondition or reschedule check);
        newly central → restart the chain via stop+start (probe-mode sensors
        have no live chain, so the locked check-and-set can't double-chain).
        Broadcasts the updated sensor dicts so UI badges flip immediately.
        """
        from core.probe_assign import effective_probe
        batch = []
        for did, sid in pairs:
            with self._lock:
                dev = self.devices.get(did)
                s = dev.sensors.get(sid) if dev else None
                if not s:
                    continue
                new_mode = "probe" if effective_probe(dev, s) else "central"
                cur_mode = getattr(s, "_sched_mode", "central")
                was_running = s.running
            if new_mode == cur_mode:
                continue
            if new_mode == "probe":
                s._sched_mode = "probe"
                self._scheduler.cancel(did, sid)
            elif was_running:
                self.stop_sensor(did, sid)
                self.start_sensor(did, sid)   # sets _sched_mode='central'
            else:
                s._sched_mode = "central"
            batch.append(("sensor", s.to_dict()))
        if batch:
            self._broadcast_batch(batch)

    @staticmethod
    def _poison_sse(q):
        """Push a close sentinel onto an evicted subscriber queue.

        Eviction removes the queue from the fan-out list, but the /events
        handler thread keeps blocking on q.get() and sending heartbeats over a
        healthy TCP connection — a zombie stream: the browser looks connected
        but never receives another event and never reconnects. The sentinel
        makes the handler close the stream so EventSource auto-reconnects.
        """
        try:
            q.put_nowait(None)
        except queue.Full:
            try:
                q.get_nowait()           # make room — the client is stalled anyway
                q.put_nowait(None)
            except Exception:
                pass

    def subscribe(self):
        # Queue sized for 5k-sensor bursts: ~200 events/s × 5s buffer = 1000 msgs.
        q = queue.Queue(maxsize=1000)
        with self._lock:
            if len(self._sse) >= 200:
                oldest = self._sse.pop(0)
                self._sse_registered.pop(oldest, None)
                self._poison_sse(oldest)
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
                    self._poison_sse(q)
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
        """Actual subscriber fan-out. Runs on the broadcaster thread.

        Each subscriber gets at most a 20 ms grace window on a full queue
        before eviction — absorbs brief GC / network hiccups without stalling
        the broadcaster. At 200 subscribers this bounds worst-case fan-out
        to ~4 s (only if every subscriber is saturated at once).
        """
        msgs = [f"event: {ev}\ndata: {json.dumps(dt)}\n\n" for ev, dt in events]
        with self._lock:
            subscribers = list(self._sse)
        dead = []
        for q in subscribers:
            try:
                for msg in msgs:
                    try:
                        q.put_nowait(msg)
                    except queue.Full:
                        # Slow subscriber: give it one short grace window before
                        # evicting. If still full, it's not keeping up — drop it.
                        q.put(msg, timeout=0.02)
            except queue.Full:
                dead.append(q)
        if dead:
            with self._lock:
                for d in dead:
                    try: self._sse.remove(d)
                    except ValueError: pass
                    self._sse_registered.pop(d, None)
                    self._poison_sse(d)
            log_sensors.warning(
                "SSE back-pressure: evicted %d slow subscriber(s) "
                "(queue full after 20ms grace)", len(dead)
            )
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
