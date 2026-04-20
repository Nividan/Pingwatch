"""core/import_parsers/zabbix_parser.py — Zabbix host/template XML import.

Parses `<zabbix_export>` XML exports. Zabbix schema:

    <zabbix_export>
      <version>6.0</version>
      <hosts>                              (or <templates>)
        <host>
          <host>router1</host>             (technical id — unique)
          <name>Router 1</name>             (display name — optional)
          <interfaces>
            <interface>
              <type>SNMP</type>              (AGENT|SNMP|JMX|IPMI)
              <useip>YES</useip>
              <ip>10.0.0.1</ip>
              <dns></dns>
              <port>161</port>
            </interface>
          </interfaces>
          <groups>
            <group><name>Network devices</name></group>
          </groups>
          <items>
            <item>
              <name>ICMP Ping</name>
              <type>SIMPLE</type>
              <key>icmpping</key>
              ...
            </item>
          </items>
        </host>
      </hosts>
    </zabbix_export>

Host IP resolution order (first non-empty wins):
  1. SNMP interface with useip=YES → ip
  2. AGENT interface with useip=YES → ip
  3. Any interface with useip=YES → ip
  4. Any interface with useip=NO  → dns
  5. <host> technical name (last-ditch — may be a DNS name)

Items are mapped via zabbix_item_map. Agent-side items (vfs.*, system.*)
and multi-step web scenarios are reported as skipped.

Templates (`<templates>` element) contain item definitions but no host
addresses; they're ignored in v1 (no device can be created from a
template alone).
"""

from __future__ import annotations

try:
    from defusedxml import ElementTree as ET
except ImportError:  # pragma: no cover
    import xml.etree.ElementTree as ET  # type: ignore

from core.import_parsers.zabbix_item_map import map_zabbix_item


def parse_zabbix_xml(text: str) -> dict:
    """Parse a Zabbix XML export. Returns canonical parse result.

    Never raises — malformed XML becomes a single row-0 error.
    """
    text = (text or "").strip()
    if not text:
        return _empty_result("empty file")

    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return _empty_result(f"XML parse failed: {e}")
    except Exception as e:
        return _empty_result(f"XML parse failed: {e}")

    # Accept <zabbix_export><hosts>...</hosts></zabbix_export> or a bare
    # <hosts>...</hosts> fragment. For templates, users are told to
    # export the hosts themselves — v1 does not read templates.
    hosts_elem = None
    if _local_tag(root.tag) == "zabbix_export":
        hosts_elem = _find_direct(root, "hosts")
    elif _local_tag(root.tag) == "hosts":
        hosts_elem = root

    if hosts_elem is None:
        return _empty_result(
            "no <hosts> section found (v1 does not support <templates> exports)"
        )

    devices: list = []
    errors:  list = []
    sensors_total  = 0
    sensors_mapped = 0
    skip_buckets: dict[str, dict] = {}

    for idx, host_elem in enumerate(_find_all_direct(hosts_elem, "host")):
        technical = _child_text(host_elem, "host")
        display   = _child_text(host_elem, "name") or technical
        if not technical:
            errors.append({"row": idx,
                           "reason": "<host> missing <host> technical name"})
            continue

        host_ip = _resolve_host_ip(host_elem) or technical
        if not host_ip:
            errors.append({"row": idx,
                           "reason": f"host {technical!r}: no interface IP/DNS found"})
            continue

        group = _first_group_name(host_elem)

        sensors_out: list = []
        items_elem = _find_direct(host_elem, "items")
        if items_elem is not None:
            for item in _find_all_direct(items_elem, "item"):
                sensors_total += 1
                key   = _child_text(item, "key") or _child_text(item, "key_")
                iname = _child_text(item, "name") or key or "Item"
                spec, skip_reason = map_zabbix_item(key)
                if spec is None:
                    _bucket(skip_buckets, key or "(no key)",
                            skip_reason or "unmapped item")
                    continue
                spec["name"] = iname
                sensors_out.append(spec)
                sensors_mapped += 1

        dev: dict = {
            "external_id": f"zabbix:{technical}",
            "name":        display,
            "host":        host_ip,
            "group":       group,
            "sensors":     sensors_out,
        }
        devices.append(dev)

    mapping_report = {
        "sensors_total":   sensors_total,
        "sensors_mapped":  sensors_mapped,
        "sensors_skipped": [
            {"source_type": k, "count": b["count"], "reason": b["reason"]}
            for k, b in sorted(skip_buckets.items(),
                               key=lambda kv: -kv[1]["count"])
        ],
    }
    if not devices and not errors:
        errors.append({"row": 0, "reason": "no <host> entries found"})
    return {
        "devices":        devices,
        "errors":         errors,
        "mapping_report": mapping_report,
    }


# ── Helpers ────────────────────────────────────────────────────────

# Zabbix interface types by priority for IP resolution.
_IFACE_PRIORITY = ("SNMP", "AGENT", "IPMI", "JMX")


def _resolve_host_ip(host_elem) -> str:
    """Pick the best interface IP (or DNS) for the host."""
    interfaces = _find_direct(host_elem, "interfaces")
    if interfaces is None:
        return ""
    ifaces: list = []
    for iface in _find_all_direct(interfaces, "interface"):
        itype = (_child_text(iface, "type") or "").upper()
        useip = (_child_text(iface, "useip") or "").upper()
        ip    = _child_text(iface, "ip")
        dns   = _child_text(iface, "dns")
        ifaces.append({"type": itype, "useip": useip, "ip": ip, "dns": dns})

    # Priority 1-3: prefer SNMP > AGENT > IPMI > JMX, useip=YES, ip set.
    for wanted in _IFACE_PRIORITY:
        for i in ifaces:
            if i["type"] == wanted and i["useip"] == "YES" and i["ip"]:
                return i["ip"]
    # Priority 4: any useip=YES with ip.
    for i in ifaces:
        if i["useip"] == "YES" and i["ip"]:
            return i["ip"]
    # Priority 5: any useip=NO with dns.
    for i in ifaces:
        if i["dns"]:
            return i["dns"]
    # Last resort: any ip.
    for i in ifaces:
        if i["ip"]:
            return i["ip"]
    return ""


def _first_group_name(host_elem) -> str:
    groups = _find_direct(host_elem, "groups")
    if groups is None:
        return ""
    for g in _find_all_direct(groups, "group"):
        n = _child_text(g, "name")
        if n:
            return n
    return ""


def _local_tag(tag: str) -> str:
    if not tag:
        return ""
    if tag.startswith("{"):
        return tag.split("}", 1)[1].lower()
    return tag.lower()


def _find_direct(parent, tag: str):
    """Find first direct child with matching local tag."""
    tag = tag.lower()
    for child in parent:
        if _local_tag(child.tag) == tag:
            return child
    return None


def _find_all_direct(parent, tag: str):
    """Iterate direct children with matching local tag."""
    tag = tag.lower()
    for child in parent:
        if _local_tag(child.tag) == tag:
            yield child


def _child_text(elem, tag: str) -> str:
    child = _find_direct(elem, tag)
    if child is None:
        return ""
    return (child.text or "").strip()


def _bucket(d: dict, source_type: str, reason: str) -> None:
    key = source_type
    b = d.get(key)
    if b:
        b["count"] += 1
    else:
        d[key] = {"count": 1, "reason": reason}


def _empty_result(reason: str) -> dict:
    return {
        "devices": [],
        "errors":  [{"row": 0, "reason": reason}],
        "mapping_report": {
            "sensors_total":   0,
            "sensors_mapped":  0,
            "sensors_skipped": [],
        },
    }
