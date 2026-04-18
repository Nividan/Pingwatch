"""
core/radius_auth.py — RADIUS authentication (PAP + Access-Challenge 2FA).

Mirrors the shape of core/ldap_auth.py. Uses pyrad (lazy-imported) so installs
without RADIUS configured never pay the cost. Public API:

  radius_test_connection(cfg=None)  -> (ok: bool, message: str)
  radius_authenticate(username, password)                 -> AuthResult | None
  radius_continue_challenge(challenge_id, user_response)  -> AuthResult
  radius_test_auth(username, password)                    -> dict (UI-friendly)
  get_radius_status()                                     -> dict (badge payload)

AuthResult shape:
  {"ok": True,  "attrs": {name: [values]}, "challenge": None}
  {"ok": False, "attrs": {},               "challenge": {"id": str, "prompt": str}}
  None  (server reject)

Failover: primary → secondary on timeout/socket error. Access-Reject is
authoritative; do not fail over on an auth rejection.

Security: shared secrets stored Fernet-encrypted in app_settings and decrypted
here via db.decrypt_pw (same pattern as LDAP bind password). Never logged.
"""

from __future__ import annotations

import io
import secrets
import socket
import threading
import time

import core.settings as _settings
from core.logger import log

# ── Minimal RFC 2865 dictionary (no VSAs) ───────────────────────────
# pyrad's Dictionary is strict — it requires definitions for every attribute
# it sees. Vendor-specific attributes without a dict entry are surfaced as
# raw bytes, which is fine for v1 (admins see hex and can decide). Standard
# attributes — which cover FortiAuthenticator, NPS, FreeRADIUS, ISE default
# configs — are decoded by name (Filter-Id, Class, Reply-Message, etc.).
_RADIUS_DICT_SRC = """
ATTRIBUTE User-Name               1 string
ATTRIBUTE User-Password           2 string encrypt=1
ATTRIBUTE CHAP-Password           3 octets
ATTRIBUTE NAS-IP-Address          4 ipaddr
ATTRIBUTE NAS-Port                5 integer
ATTRIBUTE Service-Type            6 integer
ATTRIBUTE Framed-Protocol         7 integer
ATTRIBUTE Framed-IP-Address       8 ipaddr
ATTRIBUTE Filter-Id              11 string
ATTRIBUTE Reply-Message          18 string
ATTRIBUTE State                  24 octets
ATTRIBUTE Class                  25 octets
ATTRIBUTE Vendor-Specific        26 octets
ATTRIBUTE Session-Timeout        27 integer
ATTRIBUTE Idle-Timeout           28 integer
ATTRIBUTE Called-Station-Id      30 string
ATTRIBUTE Calling-Station-Id     31 string
ATTRIBUTE NAS-Identifier         32 string
ATTRIBUTE Proxy-State            33 octets
ATTRIBUTE Login-LAT-Service      34 string
ATTRIBUTE Acct-Session-Id        44 string
ATTRIBUTE NAS-Port-Type          61 integer
ATTRIBUTE Message-Authenticator  80 octets
"""

# ── Module-level state ───────────────────────────────────────────────

_last_ok_ts: float | None = None
_last_err:   dict = {}   # {"ts": float, "msg": str}

_CHALLENGES: dict = {}   # cid → {"username", "state", "prompt", "created_ts", "server_idx", "nas_id"}
_CHALLENGE_TTL = 120
_CHALLENGES_LOCK = threading.Lock()


def _record_ok() -> None:
    global _last_ok_ts
    _last_ok_ts = time.time()


def _record_err(msg: str) -> None:
    global _last_err
    _last_err = {"ts": time.time(), "msg": (msg or "")[:200]}


def get_radius_status() -> dict:
    """Return {state, last_ok_ts, last_err_ts, last_err_msg} for the badge."""
    enabled = int(_settings.get("radius_enabled", 0) or 0)
    server = (_settings.get("radius_server", "") or "").strip()
    if not enabled or not server:
        state = "unconfigured"
    elif _last_err and (not _last_ok_ts or _last_err["ts"] > _last_ok_ts):
        state = "error"
    elif _last_ok_ts:
        state = "ok"
    else:
        state = "configured"
    return {
        "state":        state,
        "last_ok_ts":   _last_ok_ts,
        "last_err_ts":  _last_err.get("ts") if _last_err else None,
        "last_err_msg": _last_err.get("msg", "") if _last_err else "",
    }


# ── Config loader ───────────────────────────────────────────────────

def _get_cfg(overrides: dict | None = None) -> dict:
    """Pull RADIUS settings, decrypting secrets. Overrides win for specific keys."""
    from db.backups import decrypt_pw
    overrides = overrides or {}

    def _pick(key, default=""):
        v = overrides.get(key)
        if v is None or v == "":
            return _settings.get(key, default)
        return v

    secret = overrides.get("secret")
    if not secret:
        secret = decrypt_pw(_settings.get("radius_secret_enc", "") or "")
    secret2 = overrides.get("secret2")
    if not secret2:
        secret2 = decrypt_pw(_settings.get("radius_secret2_enc", "") or "")

    return {
        "server":         _pick("radius_server", ""),
        "port":           int(_pick("radius_port", 1812) or 1812),
        "secret":         secret or "",
        "server2":        _pick("radius_server2", ""),
        "port2":          int(_pick("radius_port2", 1812) or 1812),
        "secret2":        secret2 or "",
        "timeout":        max(1, int(_pick("radius_timeout", 5) or 5)),
        "retries":        max(1, int(_pick("radius_retries", 3) or 3)),
        "nas_identifier": (_pick("radius_nas_identifier", "pingwatch") or "pingwatch").strip(),
        "realm_prefix":   (_pick("radius_realm_prefix", "") or ""),
        "realm_suffix":   (_pick("radius_realm_suffix", "") or ""),
        "debug":          int(_settings.get("radius_debug", 0) or 0),
    }


def _apply_realm(cfg: dict, username: str) -> str:
    return f"{cfg['realm_prefix']}{username}{cfg['realm_suffix']}"


# ── pyrad plumbing ──────────────────────────────────────────────────

_dict_cache = None


def _build_dict():
    global _dict_cache
    if _dict_cache is not None:
        return _dict_cache
    from pyrad.dictionary import Dictionary  # lazy
    _dict_cache = Dictionary(io.StringIO(_RADIUS_DICT_SRC))
    return _dict_cache


def _make_client(host: str, port: int, secret: str, timeout: int, retries: int):
    """Build a pyrad Client. Raises ImportError if pyrad missing."""
    from pyrad.client import Client  # lazy
    c = Client(server=host, authport=int(port),
               secret=secret.encode("utf-8"),
               dict=_build_dict())
    c.timeout = timeout
    c.retries = retries
    return c


def _decode_attrs(reply) -> dict:
    """Return a JSON-friendly {name: [str-or-hex, ...]} from a pyrad reply."""
    out: dict = {}
    try:
        for name in reply.keys():
            vals = reply[name]
            cleaned = []
            for v in vals:
                if isinstance(v, bytes):
                    try:
                        cleaned.append(v.decode("utf-8"))
                    except Exception:
                        cleaned.append(v.hex())
                else:
                    cleaned.append(str(v))
            out[str(name)] = cleaned
    except Exception:
        pass
    return out


def _mk_request(client, username: str, password: str, nas_id: str, state: bytes | None = None):
    """Build an Access-Request packet. `password` is the plaintext secret or OTP."""
    import pyrad.packet as pkt
    req = client.CreateAuthPacket(code=pkt.AccessRequest)
    req["User-Name"] = username
    req["User-Password"] = req.PwCrypt(password)
    req["NAS-Identifier"] = nas_id
    if state is not None:
        req["State"] = state
    return req


# ── Core authentication ─────────────────────────────────────────────

def _try_server(host: str, port: int, secret: str, cfg: dict,
                username: str, password: str,
                state: bytes | None = None) -> tuple[str, object]:
    """
    Send one Access-Request to one server with built-in retries.

    Returns:
      ("accept",    reply)    — Access-Accept
      ("reject",    reply)    — Access-Reject (definitive, do not fail over)
      ("challenge", reply)    — Access-Challenge
      ("error",     str_msg)  — network/protocol error (fail over)
    """
    try:
        import pyrad.packet as pkt
    except ImportError:
        return "error", "pyrad not installed"

    try:
        client = _make_client(host, int(port), secret,
                              timeout=cfg["timeout"], retries=cfg["retries"])
    except Exception as e:
        return "error", f"client init failed: {e}"

    try:
        req = _mk_request(client, username, password, cfg["nas_identifier"], state)
    except Exception as e:
        return "error", f"packet build failed: {e}"

    try:
        reply = client.SendPacket(req)
    except socket.timeout:
        return "error", f"timeout connecting to {host}:{port}"
    except socket.gaierror as e:
        return "error", f"dns resolution failed for {host}: {e}"
    except Exception as e:
        return "error", f"send failed: {e}"

    code = getattr(reply, "code", None)
    if code == pkt.AccessAccept:
        return "accept", reply
    if code == pkt.AccessReject:
        return "reject", reply
    if code == pkt.AccessChallenge:
        return "challenge", reply
    return "error", f"unexpected packet code {code}"


def _iter_servers(cfg: dict):
    """Yield (index, host, port, secret) for each configured server in order."""
    if cfg["server"]:
        yield 0, cfg["server"], cfg["port"], cfg["secret"]
    if cfg["server2"]:
        yield 1, cfg["server2"], cfg["port2"], cfg["secret2"]


def _new_challenge_id() -> str:
    return secrets.token_urlsafe(24)


def _prune_challenges_locked() -> None:
    now = time.time()
    expired = [k for k, v in _CHALLENGES.items()
               if now - v.get("created_ts", 0) > _CHALLENGE_TTL]
    for k in expired:
        _CHALLENGES.pop(k, None)


def _prompt_from_reply(reply) -> str:
    try:
        msgs = reply.get("Reply-Message") or []
        if msgs:
            first = msgs[0]
            if isinstance(first, bytes):
                return first.decode("utf-8", errors="replace")
            return str(first)
    except Exception:
        pass
    return "Additional verification required"


def _state_from_reply(reply) -> bytes | None:
    try:
        vals = reply.get("State") or []
        if not vals:
            return None
        v = vals[0]
        if isinstance(v, bytes):
            return v
        return str(v).encode("latin-1", errors="replace")
    except Exception:
        return None


def radius_authenticate(username: str, password: str) -> dict | None:
    """Phase 1: password submission. Returns AuthResult dict or None on reject."""
    if not username or password is None:
        return None
    cfg = _get_cfg()
    if not cfg["server"]:
        _record_err("no RADIUS server configured")
        return None

    send_name = _apply_realm(cfg, username)
    last_err = ""
    for idx, host, port, secret in _iter_servers(cfg):
        if not secret:
            last_err = f"server {idx+1}: no shared secret configured"
            continue
        outcome, payload = _try_server(host, port, secret, cfg,
                                       send_name, password)
        if outcome == "accept":
            _record_ok()
            attrs = _decode_attrs(payload)
            log.info(f"RADIUS authenticate: SUCCESS for {username!r} (server {idx+1})")
            return {"ok": True, "attrs": attrs, "challenge": None, "server_idx": idx}
        if outcome == "reject":
            _record_ok()  # server answered — not a connectivity problem
            log.info(f"RADIUS: Access-Reject for user {username!r} from server {idx+1}")
            return None
        if outcome == "challenge":
            reply = payload
            state = _state_from_reply(reply)
            prompt = _prompt_from_reply(reply)
            cid = _new_challenge_id()
            with _CHALLENGES_LOCK:
                _prune_challenges_locked()
                _CHALLENGES[cid] = {
                    "username":   username,
                    "send_name":  send_name,
                    "state":      state,
                    "prompt":     prompt,
                    "created_ts": time.time(),
                    "server_idx": idx,
                    "nas_id":     cfg["nas_identifier"],
                }
            _record_ok()
            log.info(f"RADIUS: Access-Challenge issued for user {username!r} (server {idx+1})")
            return {"ok": False, "attrs": {},
                    "challenge": {"id": cid, "prompt": prompt}}
        # error — try next server
        last_err = f"server {idx+1} ({host}:{port}): {payload}"
        log.warning(f"RADIUS: {last_err}")

    _record_err(last_err or "all servers unreachable")
    return None


def radius_continue_challenge(challenge_id: str, user_response: str) -> dict | None:
    """Phase 2: challenge response. Returns AuthResult or None on reject/expired."""
    if not challenge_id or user_response is None:
        return None

    with _CHALLENGES_LOCK:
        _prune_challenges_locked()
        ch = _CHALLENGES.get(challenge_id)
        if ch is None:
            return None
        # Consume the entry — if the server returns another challenge, re-insert.
        _CHALLENGES.pop(challenge_id, None)

    cfg = _get_cfg()
    # Continuation must go to the same server that issued the State blob.
    target = None
    for idx, host, port, secret in _iter_servers(cfg):
        if idx == ch["server_idx"]:
            target = (idx, host, port, secret)
            break
    if target is None:
        _record_err("challenge continuation server no longer configured")
        return None

    idx, host, port, secret = target
    outcome, payload = _try_server(host, port, secret, cfg,
                                   ch["send_name"], user_response,
                                   state=ch.get("state"))
    if outcome == "accept":
        _record_ok()
        attrs = _decode_attrs(payload)
        log.info(f"RADIUS authenticate: SUCCESS for {ch['username']!r} "
                 f"(server {idx+1}, challenge completed)")
        return {"ok": True, "attrs": attrs, "challenge": None,
                "server_idx": idx, "challenge_used": True}
    if outcome == "reject":
        _record_ok()
        log.info(f"RADIUS: challenge reject for user {ch['username']!r}")
        return None
    if outcome == "challenge":
        reply = payload
        state = _state_from_reply(reply)
        prompt = _prompt_from_reply(reply)
        cid = _new_challenge_id()
        with _CHALLENGES_LOCK:
            _prune_challenges_locked()
            _CHALLENGES[cid] = {
                **ch,
                "state":      state,
                "prompt":     prompt,
                "created_ts": time.time(),
            }
        return {"ok": False, "attrs": {},
                "challenge": {"id": cid, "prompt": prompt}}

    # Network error on continuation — fail
    _record_err(f"challenge continuation failed: {payload}")
    return None


# ── Admin helpers ───────────────────────────────────────────────────

def radius_test_connection(cfg_overrides: dict | None = None) -> tuple[bool, str]:
    """Poke the server with a bogus Access-Request and confirm it replies.
    Any response (including Access-Reject) proves host+port+secret are valid."""
    try:
        import pyrad  # noqa: F401
    except ImportError:
        return False, "pyrad not installed — run setup to add it"

    cfg = _get_cfg(cfg_overrides or {})
    if not cfg["server"]:
        return False, "no RADIUS server configured"
    if not cfg["secret"]:
        return False, "no shared secret configured"

    # Randomise the probe user so we don't accidentally match a real account
    probe_user = f"__pingwatch_probe_{secrets.token_hex(4)}"
    outcome, payload = _try_server(cfg["server"], cfg["port"], cfg["secret"],
                                   cfg, probe_user, "probe-" + secrets.token_hex(8))
    if outcome in ("accept", "reject", "challenge"):
        _record_ok()
        return True, f"server responded ({outcome})"

    # primary failed — try secondary if set
    if cfg["server2"] and cfg["secret2"]:
        outcome, payload = _try_server(cfg["server2"], cfg["port2"], cfg["secret2"],
                                       cfg, probe_user, "probe-" + secrets.token_hex(8))
        if outcome in ("accept", "reject", "challenge"):
            _record_ok()
            return True, f"secondary server responded ({outcome}) — primary unreachable"

    msg = str(payload) if isinstance(payload, str) else "no response"
    _record_err(msg)
    return False, msg


def radius_test_auth(username: str, password: str) -> dict:
    """Admin helper — returns a UI-friendly dict, including discovered attributes."""
    try:
        import pyrad  # noqa: F401
    except ImportError:
        return {"ok": False, "message": "pyrad not installed — run setup to add it"}

    res = radius_authenticate(username, password)
    if res is None:
        return {"ok": False, "message": "authentication rejected"}
    if res["ok"]:
        return {"ok": True, "attrs": res["attrs"], "message": "authentication succeeded"}
    # Access-Challenge
    return {"ok": False,
            "challenge": res["challenge"],
            "message": "server returned Access-Challenge"}
