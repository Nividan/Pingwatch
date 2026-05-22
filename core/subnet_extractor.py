"""
core/subnet_extractor.py — Parse subnet definitions out of device config text.

Used by the Backups → Extract Subnets flow: take the most recent config
text for a device, find every L3 interface with an IP address, and turn
that into rows the Import Subnets modal can consume.

Three vendors covered out of the box (matching the supported-backups list
in README.md):
  • FortiGate  — `config system interface` / `edit "X" ... next` blocks
  • Cisco-IOS / Arista / Cisco-NX-OS — `interface X` + `ip address A B`
  • Juniper JUNOS — flat `set interfaces X unit N family inet address X/N`

Anything else falls through to vendor="" with an empty row list — the UI
surfaces that as "couldn't extract, paste manually". Parsers are deliberately
defensive: malformed lines are skipped, never raise.

Each parser returns a list of `{cidr, name, vlan}` dicts. `cidr` is always
canonicalised through ipaddress.IPv4Network so the Import preview's
duplicate-detection works whether the source config wrote 10.0.0.1/24 or
10.0.0.0 255.255.255.0.
"""
from __future__ import annotations

import ipaddress
import re


# ── Vendor detection ──────────────────────────────────────────────

_RE_FORTIGATE_HINT = re.compile(r"^\s*config\s+(?:system|firewall|vdom)\b", re.M | re.I)
_RE_JUNIPER_SET    = re.compile(r"^\s*set\s+interfaces\s+\S+\s+unit\s+\d+", re.M)
_RE_JUNIPER_HIER   = re.compile(r"^\s*interfaces\s*\{", re.M)
_RE_CISCO_VER      = re.compile(r"^\s*(?:!.*\n)?version\s+\d+\.", re.M)
_RE_CISCO_INTF     = re.compile(r"^\s*interface\s+(?:GigabitEthernet|TenGig|FastEthernet|"
                                r"Vlan|Loopback|Port-channel|Ethernet|Management)",
                                re.M | re.I)


def detect_vendor(text: str) -> str:
    """Return one of 'fortigate' / 'cisco' / 'juniper' / ''.

    Looks at the first 4KB only — config files can be huge and we just
    need a vendor hint. Order matters: FortiGate's `config system` is
    distinctive enough to win before we even look at Cisco markers.
    """
    head = (text or "")[:4096]
    if not head.strip():
        return ""
    if _RE_FORTIGATE_HINT.search(head):
        return "fortigate"
    if _RE_JUNIPER_SET.search(head) or _RE_JUNIPER_HIER.search(head):
        return "juniper"
    if _RE_CISCO_VER.search(head) or _RE_CISCO_INTF.search(head):
        return "cisco"
    return ""


# ── Helpers ──────────────────────────────────────────────────────

def _mask_to_prefix(mask: str):
    """Dotted-quad netmask → integer prefix length, or None on failure.
    Accepts already-numeric prefix strings ('24') too."""
    if not mask:
        return None
    m = mask.strip()
    if m.isdigit():
        n = int(m)
        return n if 0 <= n <= 32 else None
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{m}", strict=False).prefixlen
    except (ValueError, TypeError):
        return None


def _to_network(ip: str, prefix) -> "str | None":
    """Build a canonical CIDR string from (host ip, prefix). Returns None
    on any failure so callers can just check truthiness."""
    if prefix is None or not ip:
        return None
    try:
        return str(ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False))
    except (ValueError, TypeError):
        return None


def _clean_name(s: str, fallback: str = "") -> str:
    """Strip surrounding quotes/whitespace and clamp length to 100 — the
    Add Subnet modal's `name` field cap."""
    if not s:
        return fallback[:100]
    s = s.strip().strip('"').strip("'")
    return (s or fallback)[:100]


# ── FortiGate ────────────────────────────────────────────────────

# `edit "name"` ... `next` blocks inside `config system interface`.
_RE_FG_SECT = re.compile(
    r"config\s+system\s+interface\s*(.*?)(?:^end\s*$)",
    re.S | re.M | re.I,
)
_RE_FG_EDIT = re.compile(
    r'edit\s+"([^"]+)"(.*?)\n\s*next\b',
    re.S | re.I,
)
_RE_FG_IP   = re.compile(
    r"set\s+ip\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+|\d{1,2})",
    re.I,
)
_RE_FG_VLAN = re.compile(r"set\s+vlanid\s+(\d+)", re.I)
_RE_FG_DESC = re.compile(r'set\s+description\s+"([^"]*)"', re.I)


def parse_fortigate(text: str) -> list:
    out = []
    if not text:
        return out
    sect = _RE_FG_SECT.search(text)
    if not sect:
        return out
    block = sect.group(1)
    for em in _RE_FG_EDIT.finditer(block):
        name = em.group(1)
        body = em.group(2)
        ipm = _RE_FG_IP.search(body)
        if not ipm:
            continue
        cidr = _to_network(ipm.group(1), _mask_to_prefix(ipm.group(2)))
        if not cidr:
            continue
        vlan_m = _RE_FG_VLAN.search(body)
        vlan = int(vlan_m.group(1)) if vlan_m else 0
        if not (0 <= vlan <= 4094):
            vlan = 0
        desc_m = _RE_FG_DESC.search(body)
        out.append({
            "cidr": cidr,
            "name": _clean_name(desc_m.group(1) if desc_m else "", fallback=name),
            "vlan": vlan,
        })
    return out


# ── Cisco IOS / Arista / NX-OS ──────────────────────────────────

# Split a config into per-`interface` blocks. Each block runs until the
# next interface line or a top-level `!` separator.
_RE_CSCO_SPLIT = re.compile(r"\n(?=interface\s)", re.I)
_RE_CSCO_NAME  = re.compile(r"^\s*interface\s+(\S+)", re.I)
_RE_CSCO_IP    = re.compile(
    r"^\s*ip(?:v4)?\s+address\s+(\d+\.\d+\.\d+\.\d+)\s+"
    r"(\d+\.\d+\.\d+\.\d+)(?:\s+secondary)?\s*$",
    re.M | re.I,
)
# Some IOS variants and Arista write `ip address A.B.C.D/N`.
_RE_CSCO_IP_SLASH = re.compile(
    r"^\s*ip(?:v4)?\s+address\s+(\d+\.\d+\.\d+\.\d+)/(\d{1,2})\s*$",
    re.M | re.I,
)
_RE_CSCO_VLAN_INTF = re.compile(r"^Vlan(\d+)$", re.I)
_RE_CSCO_SUBINTF   = re.compile(r"\.(\d+)$")
_RE_CSCO_ENCAP     = re.compile(r"^\s*encapsulation\s+dot1q\s+(\d+)", re.M | re.I)
_RE_CSCO_DESC      = re.compile(r"^\s*description\s+(.+?)\s*$", re.M)
_RE_CSCO_TERMINATE = re.compile(r"^!\s*$", re.M)


def parse_cisco(text: str) -> list:
    out = []
    if not text:
        return out
    chunks = _RE_CSCO_SPLIT.split(text)
    for blk in chunks:
        if not blk.lstrip().lower().startswith("interface"):
            continue
        # Stop reading at the first standalone `!` so we don't bleed into
        # the next stanza (extra-safety; the split should already handle it).
        term = _RE_CSCO_TERMINATE.search(blk)
        if term:
            blk = blk[: term.start()]
        first = blk.split("\n", 1)[0]
        nm = _RE_CSCO_NAME.match(first)
        if not nm:
            continue
        intf = nm.group(1)
        # ip address (dotted-quad mask)
        cidr = None
        m_addr = _RE_CSCO_IP.search(blk)
        if m_addr:
            cidr = _to_network(m_addr.group(1), _mask_to_prefix(m_addr.group(2)))
        else:
            m_slash = _RE_CSCO_IP_SLASH.search(blk)
            if m_slash:
                cidr = _to_network(m_slash.group(1), _mask_to_prefix(m_slash.group(2)))
        if not cidr:
            continue
        # VLAN id — interface name (Vlan20), sub-interface (.20), or encapsulation
        vlan = 0
        vm = _RE_CSCO_VLAN_INTF.match(intf)
        if vm:
            vlan = int(vm.group(1))
        else:
            sm = _RE_CSCO_SUBINTF.search(intf)
            if sm:
                vlan = int(sm.group(1))
            else:
                em = _RE_CSCO_ENCAP.search(blk)
                if em:
                    vlan = int(em.group(1))
        if not (0 <= vlan <= 4094):
            vlan = 0
        dm = _RE_CSCO_DESC.search(blk)
        out.append({
            "cidr": cidr,
            "name": _clean_name(dm.group(1) if dm else "", fallback=intf),
            "vlan": vlan,
        })
    return out


# ── Juniper JUNOS (flat `set` format) ───────────────────────────

_RE_JUN_ADDR = re.compile(
    r"set\s+interfaces\s+(\S+)\s+unit\s+(\d+)\s+family\s+inet\s+address\s+"
    r"(\d+\.\d+\.\d+\.\d+/\d{1,2})\b",
    re.I,
)
_RE_JUN_DESC = re.compile(
    r'set\s+interfaces\s+(\S+)\s+unit\s+(\d+)\s+description\s+"?([^"\n]+?)"?\s*$',
    re.I | re.M,
)
_RE_JUN_VLANID = re.compile(
    r"set\s+interfaces\s+(\S+)\s+unit\s+(\d+)\s+vlan-id\s+(\d+)",
    re.I,
)


def parse_juniper(text: str) -> list:
    """Handles the `set ...` (`show | display set`) output format.

    The hierarchical brace format would need a real parser; teams who run
    `show configuration | display set` (the common diff-friendly format)
    get full coverage here. If we ever need brace-format support we can
    add a separate parser without changing this one's signature.
    """
    out = {}
    if not text:
        return []
    # Pass 1 — addresses
    for m in _RE_JUN_ADDR.finditer(text):
        intf, unit, cidr = m.group(1), m.group(2), m.group(3)
        canonical = _to_network(cidr.split("/")[0], _mask_to_prefix(cidr.split("/")[1]))
        if not canonical:
            continue
        try:
            vlan = int(unit) if 0 <= int(unit) <= 4094 else 0
        except ValueError:
            vlan = 0
        out[(intf, unit)] = {
            "cidr": canonical,
            "name": f"{intf}.{unit}",
            "vlan": vlan,
        }
    # Pass 2 — descriptions overwrite the default name
    for m in _RE_JUN_DESC.finditer(text):
        intf, unit, desc = m.group(1), m.group(2), m.group(3)
        if (intf, unit) in out:
            out[(intf, unit)]["name"] = _clean_name(desc, fallback=f"{intf}.{unit}")
    # Pass 3 — explicit vlan-id overrides the unit-number heuristic
    for m in _RE_JUN_VLANID.finditer(text):
        intf, unit, vid = m.group(1), m.group(2), m.group(3)
        if (intf, unit) in out:
            try:
                v = int(vid)
                if 0 <= v <= 4094:
                    out[(intf, unit)]["vlan"] = v
            except ValueError:
                pass
    return list(out.values())


# ── Public API ───────────────────────────────────────────────────

_PARSERS = {
    "fortigate": parse_fortigate,
    "cisco":     parse_cisco,
    "juniper":   parse_juniper,
}


def extract_subnets(text: str, vendor_hint: str = "") -> "tuple[str, list]":
    """Return (detected_vendor, rows).

    `vendor_hint` lets the caller force a parser when auto-detection is
    ambiguous (e.g. a Fortinet device with no `config system` header at
    the top of the snippet). Empty/unknown hint → fall back to detect.
    """
    vendor = (vendor_hint or "").lower().strip()
    if vendor not in _PARSERS:
        vendor = detect_vendor(text)
    parser = _PARSERS.get(vendor)
    rows = parser(text) if parser else []
    # Dedup within the result — a vendor config can list the same subnet
    # on multiple interfaces (e.g. VRRP / HA mirror). First win.
    seen = set()
    deduped = []
    for r in rows:
        cidr = r.get("cidr")
        if not cidr or cidr in seen:
            continue
        seen.add(cidr)
        deduped.append(r)
    return vendor, deduped


def rows_to_csv(rows: list, site: str = "") -> str:
    """Render a list of `{cidr,name,vlan}` rows into the CSV text the
    Import Subnets modal accepts. `site` is broadcast to every row — the
    caller is expected to pass the source device's site so the user only
    has to override it for the rows that belong elsewhere."""
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(["cidr", "name", "site", "vlan"])
    for r in rows:
        w.writerow([
            r.get("cidr", ""),
            r.get("name", ""),
            site or "",
            str(int(r.get("vlan") or 0) or "") if (r.get("vlan") or 0) else "",
        ])
    return buf.getvalue()
