"""core/import_parsers/zabbix_item_map.py — Zabbix item key → PingWatch stype.

Zabbix items use a key syntax like `base[param1,param2]`. Some items
are fully determined by the base key (`icmpping` is always ICMP), others
by the bracketed discriminator (`net.tcp.service[http]` vs `[ssh]`).

We maintain two lookup tables:
  - `EXACT_KEY_MAP`: matched against the full key including brackets.
    Wins over BASE_KEY_MAP.
  - `BASE_KEY_MAP`: matched against just the base (everything before `[`).

Unmapped keys report to the mapping_report with a reason. Agent-side
items (`vfs.*`, `system.*`, `proc.*`, `perf_counter*`) are explicitly
rejected — PingWatch has no agent equivalent on the monitored host.
"""

from __future__ import annotations

# Full-key matches — highest priority. Keys include brackets + discriminator.
EXACT_KEY_MAP: dict = {
    "net.tcp.service[http]":   {"stype": "http"},
    "net.tcp.service[https]":  {"stype": "tls"},
    "net.tcp.service[ssh]":    {"stype": "ssh"},
    "net.tcp.service[ftp]":    {"stype": "banner", "port": 21},
    "net.tcp.service[smtp]":   {"stype": "smtp"},
    "net.tcp.service[pop]":    {"stype": "banner", "port": 110},
    "net.tcp.service[imap]":   {"stype": "banner", "port": 143},
    "net.tcp.service[telnet]": {"stype": "banner", "port": 23},
    "net.tcp.service[ntp]":    {"stype": "tcp",    "port": 123},
    "net.tcp.service[ldap]":   {"stype": "tcp",    "port": 389},
    "net.udp.service[ntp]":    None,   # UDP — not modeled in PW's tcp stype
}

# Base-key matches — any params accepted, params parsed per-key by
# `_apply_key_params()` below.
BASE_KEY_MAP: dict = {
    "icmpping":         {"stype": "ping"},
    "icmppingloss":     {"stype": "ping"},
    "icmppingsec":      {"stype": "ping"},

    "net.tcp.port":     {"stype": "tcp",
                         "param_map": {1: "port"}},     # net.tcp.port[,80]
    "net.tcp.service":  None,   # handled via EXACT_KEY_MAP above

    "net.dns":          {"stype": "dns",
                         "param_map": {1: "dns_query",  # net.dns[,example.com,A]
                                        2: "dns_record_type",
                                        0: "dns_server"}},
    "net.dns.record":   {"stype": "dns",
                         "param_map": {1: "dns_query",
                                        2: "dns_record_type",
                                        0: "dns_server"}},

    "snmp.get":         {"stype": "snmp",
                         "param_map": {0: "snmp_oid"}}, # snmp.get[sysUpTime.0]
    "snmpwalk":         {"stype": "snmp",
                         "param_map": {0: "snmp_oid"}},

    # Agent-side items — all rejected.
    "vfs.fs.size":      None,
    "vfs.fs.inode":     None,
    "vfs.fs.discovery": None,
    "vfs.dev.read":     None,
    "vfs.dev.write":    None,
    "system.cpu.load":  None,
    "system.cpu.util":  None,
    "system.uptime":    None,
    "system.boottime":  None,
    "system.swap.size": None,
    "system.hostname":  None,
    "proc.num":         None,
    "proc.mem":         None,
    "perf_counter":     None,
    "perf_counter_en":  None,
    "net.if.in":        None,   # requires agent/SNMP OID — skip generic
    "net.if.out":       None,
    "net.if.total":     None,
    "agent.ping":       None,
    "agent.version":    None,
    "web.test":         None,   # multi-step web scenario — not modeled
    "log":              None,
    "logrt":            None,
    "eventlog":         None,
}


def _reason_for(key: str, base: str) -> str:
    """Human-readable skip reason for an unmapped/explicitly-rejected key."""
    agent_prefixes = ("vfs.", "system.", "proc.", "perf_counter",
                      "agent.", "net.if.", "log", "logrt", "eventlog")
    if any(base.startswith(p) for p in agent_prefixes):
        return "Zabbix agent item — no PingWatch equivalent"
    if base.startswith("web.test"):
        return "web scenarios not supported"
    if base.startswith("net.udp"):
        return "UDP service probes not supported"
    return f"unsupported Zabbix item key: {key}"


def _parse_key(key: str) -> tuple[str, list[str]]:
    """Split a Zabbix key into (base, [params]).

    `net.tcp.service[ssh]`        → ('net.tcp.service', ['ssh'])
    `net.dns[,example.com,A]`     → ('net.dns', ['', 'example.com', 'A'])
    `icmpping`                    → ('icmpping', [])
    """
    key = (key or "").strip()
    if "[" not in key or not key.endswith("]"):
        return key, []
    base, rest = key.split("[", 1)
    inner = rest[:-1]  # drop trailing ']'
    # Zabbix params are comma-separated. Quoted params allow commas inside
    # but are rare in host/template exports; keep it simple.
    params = [p.strip().strip('"') for p in inner.split(",")]
    return base, params


def map_zabbix_item(key: str) -> tuple[dict | None, str | None]:
    """Resolve a Zabbix item key to a partial PingWatch sensor spec.

    Returns `(spec, None)` on success; `(None, reason)` when unsupported.
    """
    if not key:
        return None, "missing Zabbix item key"

    # 1. Try exact-key match (handles net.tcp.service[xxx] discriminators).
    entry = EXACT_KEY_MAP.get(key)
    if entry is not None:
        spec = {k: v for k, v in entry.items() if k != "param_map"}
        return spec, None
    if key in EXACT_KEY_MAP and EXACT_KEY_MAP[key] is None:
        return None, _reason_for(key, key)

    # 2. Try base-key match.
    base, params = _parse_key(key)
    if base in BASE_KEY_MAP:
        entry = BASE_KEY_MAP[base]
        if entry is None:
            return None, _reason_for(key, base)
        spec = {"stype": entry["stype"]}
        pmap = entry.get("param_map") or {}
        for idx, field in pmap.items():
            if idx < len(params) and params[idx]:
                spec[field] = params[idx]
        return spec, None

    # 3. Fully unknown.
    return None, _reason_for(key, base)
