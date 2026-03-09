"""
trap_receiver.py — SNMPv2c trap listener (stdlib only).

Binds a UDP socket on port 162 (falls back to 1162 / 2162 if unavailable),
parses incoming SNMPv2c trap packets using raw BER decoding, and broadcasts
them to all SSE clients via STATE._broadcast('snmp_trap', ...).
"""

import datetime
import socket

from config import SNMP_TRAP_PORT
from db import _db_enqueue, db_log_trap
from logger import log


# ── BER / ASN.1 helpers ──────────────────────────────────────────────────────

def _tlv(data, pos):
    """Parse one Tag-Length-Value at pos. Returns (tag, value_bytes, next_pos)."""
    tag = data[pos]; pos += 1
    ln  = data[pos]; pos += 1
    if ln & 0x80:                           # long-form length
        nb = ln & 0x7F
        ln = int.from_bytes(data[pos:pos + nb], 'big')
        pos += nb
    return tag, data[pos:pos + ln], pos + ln


def _decode_oid(b):
    """Decode BER OID bytes to dotted string (e.g. '1.3.6.1.4.1.12356.101')."""
    if not b:
        return ''
    parts = [str(b[0] // 40), str(b[0] % 40)]
    i = 1
    while i < len(b):
        v = 0
        while True:
            byte = b[i]; v = (v << 7) | (byte & 0x7F); i += 1
            if not (byte & 0x80):
                break
        parts.append(str(v))
    return '.'.join(parts)


def _decode_val(tag, b):
    """Decode a BER value to a printable string based on its tag."""
    if tag == 0x02:                         # INTEGER (signed)
        return str(int.from_bytes(b, 'big', signed=True))
    if tag == 0x04:                         # OCTET STRING
        return b.decode(errors='replace')
    if tag == 0x06:                         # OID
        return _decode_oid(b)
    if tag in (0x40, 0x41, 0x42, 0x43):    # [0-3] APPLICATION (IpAddr, Counter, Gauge, TimeTicks)
        return str(int.from_bytes(b, 'big'))
    return b.hex()


def parse_trap(data):
    """
    Parse a raw SNMPv2c (or v1) trap UDP payload.
    Returns a dict with keys: community, trap_oid, varbinds, detail.
    Returns None if the packet cannot be decoded or is not a trap PDU.
    """
    try:
        pos = 0
        # Skip outer SEQUENCE header — advance past tag + length bytes only,
        # leaving pos at the first byte of the SEQUENCE content.
        if data[pos] != 0x30:
            return None
        pos += 1
        _sq_ln = data[pos]; pos += 1
        if _sq_ln & 0x80:               # long-form length: skip the length bytes
            pos += _sq_ln & 0x7F

        _, ver_b, pos = _tlv(data, pos)     # version INTEGER
        version = int.from_bytes(ver_b, 'big')
        if version not in (0, 1):           # 0=v1, 1=v2c
            return None

        _, comm_b, pos = _tlv(data, pos)    # community OCTET STRING
        community = comm_b.decode(errors='replace')

        pdu_tag, pdu_b, _ = _tlv(data, pos) # PDU
        # 0xA4 = Trap-PDU (v1), 0xA7 = SNMPv2-Trap-PDU (v2c)
        if pdu_tag not in (0xA4, 0xA7):
            return None

        # Parse varbinds from inside the PDU bytes
        varbinds = []
        p2 = 0
        if pdu_tag == 0xA7:
            # v2c: skip request-id, error-status, error-index (3 integers)
            for _ in range(3):
                _, _, p2 = _tlv(pdu_b, p2)
            # then VarBindList SEQUENCE
            _, vbl_b, _ = _tlv(pdu_b, p2)
        else:
            # v1: skip enterprise OID, agent-addr, generic-trap, specific-trap, time-stamp
            for _ in range(5):
                _, _, p2 = _tlv(pdu_b, p2)
            _, vbl_b, _ = _tlv(pdu_b, p2)

        # Each varbind is SEQUENCE { OID, value }
        vp = 0
        while vp < len(vbl_b):
            try:
                _, vb_b, vp = _tlv(vbl_b, vp)
                vp2 = 0
                _, oid_b, vp2 = _tlv(vb_b, vp2)
                vtag, val_b, _  = _tlv(vb_b, vp2)
                varbinds.append({'oid': _decode_oid(oid_b), 'value': _decode_val(vtag, val_b)})
            except Exception:
                break

        # snmpTrapOID.0 is the 2nd varbind (index 1) in v2c traps
        trap_oid = ''
        if pdu_tag == 0xA7 and len(varbinds) > 1:
            trap_oid = varbinds[1].get('value', '')
        elif varbinds:
            trap_oid = varbinds[0].get('oid', '')

        # Build a human-readable detail from remaining varbinds (skip first 2 in v2c)
        skip = 2 if pdu_tag == 0xA7 else 0
        detail = '; '.join(
            f"{v['oid'].split('.')[-3]}.{v['oid'].split('.')[-2]}.{v['oid'].split('.')[-1]}={v['value']}"
            for v in varbinds[skip:]
        )
        if not detail and varbinds:
            detail = varbinds[0].get('value', '')

        return {
            'community': community,
            'trap_oid':  trap_oid,
            'varbinds':  varbinds,
            'detail':    detail[:300],
        }

    except Exception as e:
        log.debug(f"Trap parse error: {e}")
        return None


# ── Listener ─────────────────────────────────────────────────────────────────

def trap_receiver_loop(state, port=None):
    """Try to bind on the configured port → 1162 → 2162. Run forever once bound."""
    base = port if port is not None else SNMP_TRAP_PORT
    fallbacks = [p for p in [1162, 2162] if p != base]
    for try_port in [base] + fallbacks:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('0.0.0.0', try_port))
            sock.settimeout(2.0)
            log.info(f"SNMP trap listener on UDP port {try_port}")
            _recv_loop(sock, state)
            return
        except OSError as e:
            log.warning(f"SNMP trap: cannot bind port {try_port}: {e}")
    log.error("SNMP trap receiver disabled — ports 162, 1162, 2162 all unavailable.")


def _recv_loop(sock, state):
    """Inner receive loop — runs until the process exits."""
    while True:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except Exception as e:
            log.warning(f"SNMP trap recv error: {e}")
            continue

        src_ip = addr[0]
        log.info(f"SNMP trap: received {len(data)} bytes from {src_ip}")
        parsed = parse_trap(data)
        if not parsed:
            log.warning(f"SNMP trap: could not parse packet from {src_ip} ({len(data)} bytes)")
            continue

        ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        # Try to match source IP to a known device
        dname = ''
        with state._lock:
            for dev in state.devices.values():
                if dev.host == src_ip:
                    dname = dev.name
                    break

        evt = {
            'ts':        ts,
            'src_ip':    src_ip,
            'dname':     dname,
            'community': parsed['community'],
            'trap_oid':  parsed['trap_oid'],
            'detail':    parsed['detail'],
            '_direction': 'trap',
        }

        state._broadcast('snmp_trap', evt)

        _cap = dict(evt)
        _db_enqueue(lambda: db_log_trap(_cap))

        log.info(f"SNMP trap from {src_ip} ({dname or 'unknown'}): {parsed['trap_oid'] or '?'}")
