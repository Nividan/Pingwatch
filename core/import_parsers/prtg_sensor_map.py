"""core/import_parsers/prtg_sensor_map.py — PRTG sensor type → PingWatch stype.

PRTG has hundreds of sensor types; PingWatch has 13. This table covers the
ones that map cleanly. Unmapped types are reported in the `mapping_report`
with a human-readable reason so admins see exactly what was skipped.

Each value is either:
  - `None` → explicitly unsupported; count + reason surfaced in report.
  - dict with `stype` (required) + optional keys:
      - `snmp_oid`: default OID to preseed on the new PingWatch sensor
      - `attr_map`: {"<prtg-field>": "<pingwatch-field>"} — fields copied
        from the PRTG sensor element's children/attrs to the output sensor

Keys are normalized via `_norm_prtg_type()` (lowercased, non-alnum stripped)
so `"SSL Certificate"`, `"sslcertificate"`, and `"sslcert"` all hit the
same bucket.
"""
from __future__ import annotations


from __future__ import annotations

# Keys MUST be the fully normalized form (lowercase, alphanumeric only).
PRTG_SENSOR_MAP: dict = {
    # ── ICMP / basic reachability ──────────────────────────────────
    "ping":              {"stype": "ping"},
    "cloudping":         {"stype": "ping"},
    "pingjitter":        {"stype": "ping"},  # loss+jitter maps to ping (PW tracks both)

    # ── TCP / port ─────────────────────────────────────────────────
    "port":              {"stype": "tcp", "attr_map": {"targetport": "port",
                                                         "port":       "port"}},
    "portv2":            {"stype": "tcp", "attr_map": {"targetport": "port",
                                                         "port":       "port"}},
    "portrange":         None,   # unsupported — PW sensor is single-port

    # ── HTTP / HTTPS ───────────────────────────────────────────────
    "http":              {"stype": "http",
                          "attr_map": {"httpurl":    "url",
                                        "url":        "url",
                                        "timeout":    None}},
    "httpadvanced":      {"stype": "http",
                          "attr_map": {"httpurl": "url", "url": "url"}},
    "httpurl":           {"stype": "http",
                          "attr_map": {"httpurl": "url", "url": "url"}},
    "httpxml":           {"stype": "http",
                          "attr_map": {"httpurl": "url", "url": "url"}},
    "httpcontent":       {"stype": "http_keyword",
                          "attr_map": {"httpurl": "url", "url": "url",
                                        "keyword": "keyword",
                                        "searchstring": "keyword"}},
    "httptransaction":   None,   # multi-step flow — not modeled in PW
    "httpfull":          None,
    "httppush":          None,   # PRTG server-side receiver, not a probe

    # ── TLS ────────────────────────────────────────────────────────
    "sslcertificate":    {"stype": "tls",
                          "attr_map": {"targetport": "port", "port": "port"}},
    "sslsecuritycheck":  {"stype": "tls",
                          "attr_map": {"targetport": "port", "port": "port"}},

    # ── SNMP ───────────────────────────────────────────────────────
    "snmpcustom":        {"stype": "snmp"},
    "snmpcustomstring":  {"stype": "snmp"},
    "snmpuptime":        {"stype": "snmp", "snmp_oid": "1.3.6.1.2.1.1.3.0"},
    "snmpsystemuptime":  {"stype": "snmp", "snmp_oid": "1.3.6.1.2.1.25.1.1.0"},
    "snmptraffic":       {"stype": "snmp"},   # counter — OID not fixed (ifIndex)
    "snmplibrary":       {"stype": "snmp"},
    "snmpcisco":         {"stype": "snmp"},
    "snmpciscosystemhealth": {"stype": "snmp"},
    "snmpdiskfree":      {"stype": "snmp"},
    "snmpmemory":        {"stype": "snmp"},
    "snmpcpuload":       {"stype": "snmp"},

    # ── DNS ────────────────────────────────────────────────────────
    "dns":               {"stype": "dns",
                          "attr_map": {"domain": "dns_query",
                                        "dnsquery": "dns_query"}},
    "dnsv2":             {"stype": "dns",
                          "attr_map": {"domain": "dns_query",
                                        "dnsquery": "dns_query"}},

    # ── SMTP / Email ───────────────────────────────────────────────
    "smtp":              {"stype": "smtp",
                          "attr_map": {"targetport": "port", "port": "port"}},
    "smtproundtrip":     {"stype": "smtp",
                          "attr_map": {"targetport": "port", "port": "port"}},

    # ── SSH / SFTP ─────────────────────────────────────────────────
    "ssh":               {"stype": "ssh",
                          "attr_map": {"targetport": "port", "port": "port"}},
    "sshscript":         {"stype": "ssh",
                          "attr_map": {"targetport": "port", "port": "port"}},
    "sshscriptadvanced": {"stype": "ssh"},
    "sftp":              {"stype": "sftp",
                          "attr_map": {"targetport": "port", "port": "port"}},

    # ── RADIUS ─────────────────────────────────────────────────────
    "radius":            {"stype": "radius",
                          "attr_map": {"targetport": "port", "port": "port"}},
    "radiusv2":          {"stype": "radius"},

    # ── VMware / VM monitoring ─────────────────────────────────────
    "vmwarehost":        {"stype": "vmware"},
    "vmwarehostperformance": {"stype": "vmware"},
    "vmwarevirtualmachine": {"stype": "vmware"},

    # ── Banner grabs ───────────────────────────────────────────────
    "pop3":              {"stype": "banner",
                          "attr_map": {"targetport": "port", "port": "port"}},
    "imap":              {"stype": "banner",
                          "attr_map": {"targetport": "port", "port": "port"}},
    "ftp":               {"stype": "banner",
                          "attr_map": {"targetport": "port", "port": "port"}},

    # ── Explicitly unsupported (require Windows agent / WMI / etc.) ─
    "wmicpuload":        None,
    "wmimemory":         None,
    "wmidiskfree":       None,
    "wmiservice":        None,
    "wmiuptime":         None,
    "wmiprocess":        None,
    "wminetworkcard":    None,
    "wminetwork":        None,
    "windowsupdates":    None,
    "exechronxml":       None,   # PRTG EXE/Script XML — cannot replicate
    "exexml":            None,
    "exe":               None,
    "exeadvanced":       None,
    "sensorfactory":     None,   # calculated from other sensors
    "businessprocess":   None,
    "common":            None,   # placeholder PRTG type
}

# Explicit reason strings shown in the mapping report. Keys are the
# *normalized* PRTG sensor type; fallback reason covers everything else.
_REASONS: dict = {
    "wmicpuload":        "WMI not supported (Windows agent-only)",
    "wmimemory":         "WMI not supported (Windows agent-only)",
    "wmidiskfree":       "WMI not supported (Windows agent-only)",
    "wmiservice":        "WMI not supported (Windows agent-only)",
    "wmiuptime":         "WMI not supported (Windows agent-only)",
    "wmiprocess":        "WMI not supported (Windows agent-only)",
    "wminetworkcard":    "WMI not supported (Windows agent-only)",
    "wminetwork":        "WMI not supported (Windows agent-only)",
    "windowsupdates":    "WMI not supported (Windows agent-only)",
    "exechronxml":       "script-based sensors not supported",
    "exexml":            "script-based sensors not supported",
    "exe":               "script-based sensors not supported",
    "exeadvanced":       "script-based sensors not supported",
    "sensorfactory":     "calculated/derived sensors not supported",
    "businessprocess":   "business-process sensors not supported",
    "httptransaction":   "multi-step transactions not supported",
    "httpfull":          "multi-step transactions not supported",
    "httppush":          "push-receiver sensors not supported",
    "portrange":         "multi-port scans not supported",
    "common":            "placeholder / unknown PRTG type",
}


def _norm_prtg_type(raw: str) -> str:
    """Normalize a raw PRTG sensor type string for lookup.

    `"SSL Certificate v2"` → `sslcertificatev2`
    `"snmp_uptime"` → `snmpuptime`
    """
    if not raw:
        return ""
    raw = raw.strip().lower()
    return "".join(ch for ch in raw if ch.isalnum())


def map_prtg_sensor(raw_type: str, attrs: dict | None = None
                    ) -> tuple[dict | None, str | None]:
    """Look up a PRTG sensor type and return a partial PingWatch sensor spec.

    Returns `(sensor_spec_or_None, skip_reason_or_None)`:
      - `(spec, None)`  when the type mapped — caller should populate
        `name` + any other device-level defaults.
      - `(None, reason)` when the type is explicitly unsupported or unknown.

    `attrs` is an optional dict of the PRTG sensor element's attributes +
    simple child values (both merged into one namespace) — used to copy
    over port / URL / community values when the mapping entry has an
    `attr_map`.
    """
    key = _norm_prtg_type(raw_type)
    if not key:
        return None, "missing PRTG sensor type"

    entry = PRTG_SENSOR_MAP.get(key)
    if entry is None:
        reason = _REASONS.get(key) or f"unsupported PRTG type: {raw_type}"
        return None, reason

    spec: dict = {"stype": entry["stype"]}
    if entry.get("snmp_oid"):
        spec["snmp_oid"] = entry["snmp_oid"]
    attr_map = entry.get("attr_map") or {}
    if attr_map and attrs:
        for src, dst in attr_map.items():
            if dst is None:
                continue
            v = attrs.get(src)
            if v not in (None, ""):
                spec[dst] = v
    return spec, None
