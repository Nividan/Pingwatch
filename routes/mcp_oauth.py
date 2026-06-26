"""
routes/mcp_oauth.py — OAuth 2.1 authorization server for the MCP endpoint.

Lets the claude.ai / mobile "Add custom connector" dialog connect to
/api/mcp, which (per the MCP spec) speaks OAuth rather than static bearer
headers. Static `pw_` mcp-scope tokens keep working unchanged — this just
adds the discovery + authorization-code flow on top.

Endpoints
  GET  /.well-known/oauth-protected-resource[/api/mcp]   (public) RFC 9728
  GET  /.well-known/oauth-authorization-server[/api/mcp] (public) RFC 8414
  GET  /api/mcp/oauth/authorize     admin consent screen
  POST /api/mcp/oauth/authorize     consent submit → auth code (redirect)
  POST /api/mcp/oauth/token         code / refresh → access (+refresh) token
  GET  /api/mcp/oauth/clients       admin — list registered clients
  POST /api/mcp/oauth/clients       admin — register a client (secret once)
  DELETE /api/mcp/oauth/clients/{id} admin — delete a client

Design decisions (see CHANGELOG / API.md):
  • Clients are ADMIN-PRE-REGISTERED (no Dynamic Client Registration).
  • Only an ADMIN PingWatch session may complete the consent.
  • PKCE S256 is mandatory; redirect_uri is exact-matched.
  • Access tokens are minted as mcp-scope api_tokens (1 h); refresh tokens
    rotate. The whole surface is gated by the `mcp_enabled` setting.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from urllib.parse import urlparse, parse_qs, urlencode

import core.settings as _settings
from core.logger import log
from db import (
    db_log_audit,
    db_get_oauth_client, db_list_oauth_clients, db_create_oauth_client,
    db_delete_oauth_client,
    db_create_oauth_code, db_consume_oauth_code,
    db_create_oauth_refresh, db_rotate_oauth_refresh,
    mint_mcp_access_token, db_cleanup_oauth_expired,
)
from core.auth import _hash_token

_ACCESS_TTL   = 3600       # access token lifetime (s)
_REFRESH_DAYS = 30         # refresh token lifetime (days)
_CODE_TTL     = 60         # authorization code lifetime (s)
_MAX_CLIENTS  = 50

_RE_AUTHORIZE = re.compile(r"^/api/mcp/oauth/authorize/?$")
_RE_TOKEN     = re.compile(r"^/api/mcp/oauth/token/?$")
_RE_CLIENTS   = re.compile(r"^/api/mcp/oauth/clients/?$")
_RE_CLIENT_ID = re.compile(r"^/api/mcp/oauth/clients/([A-Za-z0-9_]+)/?$")
_RE_WK_RESOURCE = re.compile(r"^/\.well-known/oauth-protected-resource(/.*)?$")
_RE_WK_AS       = re.compile(r"^/\.well-known/oauth-authorization-server(/.*)?$")


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _enabled() -> bool:
    return str(_settings.get("mcp_enabled", "0") or "0") in ("1", "true", "True", "on")


def _base_url(h) -> str:
    """Public origin (scheme://host), honouring reverse-proxy headers. OAuth
    metadata URLs must be absolute and match what the client connected to."""
    xf_proto = (h.headers.get("X-Forwarded-Proto", "") or "").split(",")[0].strip()
    xf_host  = (h.headers.get("X-Forwarded-Host", "") or "").split(",")[0].strip()
    host = xf_host or (h.headers.get("Host", "") or "").strip()
    if xf_proto:
        scheme = xf_proto
    else:
        scheme = "https" if str(_settings.get("tls_enabled", "1")) == "1" else "http"
    return f"{scheme}://{host or 'localhost'}"


def _html(h, code: int, body: str):
    data = body.encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Content-Length", str(len(data)))
    h.send_header("Cache-Control", "no-store")
    h._sec_headers()
    h.end_headers()
    h.wfile.write(data)


def _redirect(h, url: str):
    h.send_response(302)
    h.send_header("Location", url)
    h.send_header("Content-Length", "0")
    h.send_header("Cache-Control", "no-store")
    h.end_headers()


def _redirect_err(h, redirect_uri: str, error: str, state: str):
    q = {"error": error}
    if state:
        q["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    _redirect(h, f"{redirect_uri}{sep}{urlencode(q)}")


def _oauth_err(h, code: int, error: str, desc: str = ""):
    """RFC 6749 error response on the token endpoint."""
    obj = {"error": error}
    if desc:
        obj["error_description"] = desc
    h._json(code, obj)


def _pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _consent_nonce(h, client_id: str, redirect_uri: str) -> str:
    """CSRF token binding the consent form to the current admin session."""
    sess = h._get_token() or ""
    return _hash_token(f"{sess}|mcpconsent|{client_id}|{redirect_uri}")


def _admin_session(h):
    """Return the admin username for the current COOKIE session, else None.
    OAuth consent must be a real interactive admin — never an api_token."""
    user, role, _scope, kind = h._auth_principal()
    if not user or kind != "session" or role != "admin":
        return None
    return user


def _valid_redirect(client: dict, redirect_uri: str) -> bool:
    return bool(redirect_uri) and redirect_uri in (client.get("redirect_uris") or [])


# ─────────────────────────────────────────────────────────────────────
# Discovery metadata (public)
# ─────────────────────────────────────────────────────────────────────
def _meta_protected_resource(h):
    base = _base_url(h)
    h._json(200, {
        "resource":                 f"{base}/api/mcp",
        "authorization_servers":    [base],
        "scopes_supported":         ["mcp"],
        "bearer_methods_supported": ["header"],
    })


def _meta_authorization_server(h):
    base = _base_url(h)
    h._json(200, {
        "issuer":                                base,
        "authorization_endpoint":                f"{base}/api/mcp/oauth/authorize",
        "token_endpoint":                        f"{base}/api/mcp/oauth/token",
        "response_types_supported":              ["code"],
        "grant_types_supported":                 ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported":      ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "scopes_supported":                      ["mcp"],
    })


# ─────────────────────────────────────────────────────────────────────
# Consent screen
# ─────────────────────────────────────────────────────────────────────
def _page(title: str, inner: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{title}</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 html,body{{margin:0;background:#0d1117;color:#e6edf3;height:100%;
   font-family:'Segoe UI',-apple-system,sans-serif;}}
 .wrap{{max-width:480px;margin:10vh auto;padding:28px 32px;background:#161b22;
   border:1px solid #30363d;border-radius:12px;}}
 h1{{margin:0 0 4px;font-size:20px;}} h1 .dot{{color:#23d18b;margin-right:6px;}}
 h1 .a{{color:#2f81f7;}}
 p{{color:#8b949e;line-height:1.55;font-size:14px;}}
 .scope{{margin:16px 0;padding:12px 14px;background:#0d1117;border:1px solid #30363d;
   border-radius:8px;font-size:13px;}}
 .scope b{{color:#e6edf3;}} code{{color:#2f81f7;}}
 .row{{display:flex;gap:10px;margin-top:22px;}}
 button{{flex:1;padding:10px;border-radius:8px;border:1px solid #30363d;
   font-size:14px;cursor:pointer;}}
 .approve{{background:#2f81f7;color:#fff;border-color:#2f81f7;}}
 .deny{{background:#21262d;color:#e6edf3;}}
 .who{{color:#484f58;font-size:12px;margin-top:18px;}}
</style></head><body><div class="wrap">{inner}</div></body></html>"""


def _consent_page(h, user, params, client):
    nonce = _consent_nonce(h, params["client_id"], params["redirect_uri"])
    hid = "".join(
        f'<input type="hidden" name="{k}" value="{_esc(v)}">'
        for k, v in params.items())
    inner = f"""
 <h1><span class="dot">●</span>Ping<span class="a">Watch</span></h1>
 <p><b style="color:#e6edf3">{_esc(client.get("name") or "An application")}</b>
    wants to connect to your PingWatch monitoring data.</p>
 <div class="scope">
   <b>Read-only access</b> (<code>mcp</code> scope) — list devices, alerts,
   incidents, metrics, topology. It <b>cannot</b> change anything.
 </div>
 <form method="POST" action="/api/mcp/oauth/authorize">
   {hid}
   <input type="hidden" name="csrf" value="{nonce}">
   <div class="row">
     <button class="deny"    type="submit" name="decision" value="deny">Deny</button>
     <button class="approve" type="submit" name="decision" value="approve">Approve</button>
   </div>
 </form>
 <div class="who">Signed in as <b>{_esc(user)}</b> (admin).</div>"""
    _html(h, 200, _page("Authorize — PingWatch", inner))


def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ─────────────────────────────────────────────────────────────────────
# /authorize
# ─────────────────────────────────────────────────────────────────────
def _handle_authorize_get(h):
    q = parse_qs(urlparse(h.path).query)
    def g(k): return (q.get(k, [""])[0] or "").strip()
    client_id     = g("client_id")
    redirect_uri  = g("redirect_uri")
    response_type = g("response_type")
    challenge     = g("code_challenge")
    method        = g("code_challenge_method")
    state         = g("state")
    scope         = g("scope") or "mcp"
    resource      = g("resource")

    client = db_get_oauth_client(client_id) if client_id else None
    if not client or not _valid_redirect(client, redirect_uri):
        # Never redirect to an unvalidated URI — show an error page instead.
        _html(h, 400, _page("Authorization error", _bad(
            "Invalid <code>client_id</code> or <code>redirect_uri</code>. "
            "Ask your PingWatch admin to register this connector.")))
        return

    # redirect_uri is now trusted — protocol errors go back via redirect.
    if response_type != "code":
        _redirect_err(h, redirect_uri, "unsupported_response_type", state); return
    if not challenge or method != "S256":
        _redirect_err(h, redirect_uri, "invalid_request", state); return

    user = _admin_session(h)
    if not user:
        # Distinguish "not signed in" from "signed in but not admin".
        who, _r, _s, kind = h._auth_principal()
        base = _base_url(h)
        if who and kind == "session":
            _html(h, 403, _page("Admin required", _bad(
                f"You are signed in as <b>{_esc(who)}</b>, but only an "
                "<b>admin</b> can authorize an MCP connection. Sign in as an "
                "admin and try again.")))
        else:
            _html(h, 401, _page("Sign in required", _bad(
                "Open <a style='color:#2f81f7' href='" + _esc(base) + "/'>PingWatch</a>, "
                "sign in as an <b>admin</b>, then click Connect again in your AI client.")))
        return

    _consent_page(h, user, {
        "client_id": client_id, "redirect_uri": redirect_uri,
        "response_type": response_type, "code_challenge": challenge,
        "code_challenge_method": method, "state": state,
        "scope": scope, "resource": resource,
    }, client)


def _bad(msg: str) -> str:
    return (f'<h1><span class="dot" style="color:#f85149">●</span>'
            f'Ping<span class="a">Watch</span></h1><p>{msg}</p>')


def _handle_authorize_post(h, body):
    def g(k): return (body.get(k, "") or "").strip()
    client_id    = g("client_id")
    redirect_uri = g("redirect_uri")
    challenge    = g("code_challenge")
    method       = g("code_challenge_method")
    state        = g("state")
    scope        = g("scope") or "mcp"
    resource     = g("resource")
    decision     = g("decision")
    csrf         = g("csrf")

    client = db_get_oauth_client(client_id) if client_id else None
    if not client or not _valid_redirect(client, redirect_uri):
        _html(h, 400, _page("Authorization error",
                            _bad("Invalid client or redirect URI.")))
        return

    user = _admin_session(h)
    if not user:
        _html(h, 403, _page("Admin required",
                            _bad("Admin session required to authorize.")))
        return

    # CSRF: form must carry the nonce bound to this admin session.
    if not csrf or csrf != _consent_nonce(h, client_id, redirect_uri):
        _html(h, 400, _page("Authorization error",
                            _bad("Consent verification failed — please retry.")))
        return

    if decision != "approve":
        db_log_audit(user, h.client_address[0], "mcp_oauth_consent",
                     client_id, "denied")
        _redirect_err(h, redirect_uri, "access_denied", state); return

    if not challenge or method != "S256":
        _redirect_err(h, redirect_uri, "invalid_request", state); return

    code = db_create_oauth_code(client_id, redirect_uri, challenge, user,
                                scope="mcp", resource=resource, ttl=_CODE_TTL)
    if not code:
        _redirect_err(h, redirect_uri, "server_error", state); return

    db_log_audit(user, h.client_address[0], "mcp_oauth_consent",
                 client_id, "approved scope=mcp")
    q = {"code": code}
    if state:
        q["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    _redirect(h, f"{redirect_uri}{sep}{urlencode(q)}")


# ─────────────────────────────────────────────────────────────────────
# /token
# ─────────────────────────────────────────────────────────────────────
def _authenticate_client(body):
    """Validate client_id + client_secret. Returns the client dict or None."""
    cid = (body.get("client_id", "") or "").strip()
    sec = (body.get("client_secret", "") or "").strip()
    if not cid or not sec:
        return None
    client = db_get_oauth_client(cid)
    if not client:
        return None
    if _hash_token(sec) != client.get("client_secret_hash"):
        return None
    return client


def _handle_token(h, body):
    db_cleanup_oauth_expired()  # lazy GC
    grant = (body.get("grant_type", "") or "").strip()

    client = _authenticate_client(body)
    if not client:
        _oauth_err(h, 401, "invalid_client", "client authentication failed")
        return
    cid = client["client_id"]

    if grant == "authorization_code":
        code         = (body.get("code", "") or "").strip()
        redirect_uri = (body.get("redirect_uri", "") or "").strip()
        verifier     = (body.get("code_verifier", "") or "").strip()
        row = db_consume_oauth_code(code) if code else None
        if not row:
            _oauth_err(h, 400, "invalid_grant", "code invalid or expired"); return
        if str(row.get("client_id")) != cid or row.get("redirect_uri") != redirect_uri:
            _oauth_err(h, 400, "invalid_grant", "client/redirect mismatch"); return
        if not verifier or _pkce_s256(verifier) != row.get("code_challenge"):
            _oauth_err(h, 400, "invalid_grant", "PKCE verification failed"); return
        _issue_tokens(h, cid, row.get("username"), row.get("scope") or "mcp")
        return

    if grant == "refresh_token":
        rtok = (body.get("refresh_token", "") or "").strip()
        rot = db_rotate_oauth_refresh(rtok, cid) if rtok else None
        if not rot:
            _oauth_err(h, 400, "invalid_grant", "refresh token invalid"); return
        _issue_tokens(h, cid, rot.get("username"), rot.get("scope") or "mcp")
        return

    _oauth_err(h, 400, "unsupported_grant_type", grant or "(none)")


def _issue_tokens(h, client_id, username, scope):
    access = mint_mcp_access_token(username, client_id, ttl=_ACCESS_TTL)
    if not access:
        _oauth_err(h, 500, "server_error", "could not issue access token"); return
    refresh = db_create_oauth_refresh(client_id, username, scope, ttl_days=_REFRESH_DAYS)
    log.info(f"mcp oauth: issued access token for {username} via client {client_id}")
    h._json(200, {
        "access_token":  access,
        "token_type":    "Bearer",
        "expires_in":    _ACCESS_TTL,
        "refresh_token": refresh,
        "scope":         scope,
    })


# ─────────────────────────────────────────────────────────────────────
# Client management (admin, JSON — used by the SPA)
# ─────────────────────────────────────────────────────────────────────
_REDIRECT_MAX = 10
_NAME_MAX     = 100


def _handle_clients_get(h):
    user, _ = h._require("admin")
    if not user:
        return
    h._json(200, {"clients": db_list_oauth_clients()})


def _handle_clients_post(h, body):
    user, _ = h._require("admin")
    if not user:
        return
    # A registration is a credential issue — never allow it via an api_token.
    _, _, _, kind = h._auth_principal()
    if kind == "api_token":
        h._json(403, {"error": "API tokens cannot register OAuth clients"}); return

    if len(db_list_oauth_clients()) >= _MAX_CLIENTS:
        h._json(400, {"error": f"client limit reached (max {_MAX_CLIENTS})"}); return

    name = (body.get("name") or "").strip()
    uris = body.get("redirect_uris")
    if not name or len(name) > _NAME_MAX:
        h._json(400, {"error": "name required (max 100 chars)"}); return
    if not isinstance(uris, list) or not uris or len(uris) > _REDIRECT_MAX:
        h._json(400, {"error": f"redirect_uris must be a list of 1-{_REDIRECT_MAX} URLs"}); return
    clean = []
    for u in uris:
        u = (str(u) or "").strip()
        if not (u.startswith("https://") or u.startswith("http://")):
            h._json(400, {"error": f"redirect_uri must be http(s): {u[:60]}"}); return
        clean.append(u)

    res = db_create_oauth_client(name, clean, created_by=user)
    if not res:
        h._json(500, {"error": "could not create client"}); return
    client_id, client_secret = res
    db_log_audit(user, h.client_address[0], "mcp_oauth_client_create",
                 client_id, f"name={name}")
    h._json(201, {
        "client_id":     client_id,
        "client_secret": client_secret,   # ONE-TIME reveal
        "name":          name,
        "redirect_uris": clean,
    })


def _handle_client_delete(h, client_id):
    user, _ = h._require("admin")
    if not user:
        return
    if not db_get_oauth_client(client_id):
        h._json(404, {"error": "client not found"}); return
    db_delete_oauth_client(client_id)
    db_log_audit(user, h.client_address[0], "mcp_oauth_client_delete", client_id)
    h._json(200, {"ok": True})


# ─────────────────────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────────────────────
def handle(h, method, path, body):
    p = path.split("?", 1)[0]

    # ── Discovery metadata (public). Hidden entirely when MCP is disabled. ──
    if method == "GET" and (_RE_WK_RESOURCE.match(p) or _RE_WK_AS.match(p)):
        if not _enabled():
            h._json(404, {"error": "not found"}); return True
        if _RE_WK_RESOURCE.match(p):
            _meta_protected_resource(h)
        else:
            _meta_authorization_server(h)
        return True

    if not p.startswith("/api/mcp/oauth/"):
        return False

    # Everything below is gated on the opt-in toggle.
    if not _enabled():
        if method in ("GET",) and _RE_AUTHORIZE.match(p):
            _html(h, 503, _page("MCP disabled", _bad("The MCP server is disabled.")))
        else:
            h._json(503, {"error": "MCP is disabled"})
        return True

    # /authorize
    if _RE_AUTHORIZE.match(p):
        if method == "GET":
            _handle_authorize_get(h); return True
        if method == "POST":
            _handle_authorize_post(h, body or {}); return True
        return False

    # /token
    if _RE_TOKEN.match(p) and method == "POST":
        _handle_token(h, body or {}); return True

    # /clients collection
    if _RE_CLIENTS.match(p):
        if method == "GET":
            _handle_clients_get(h); return True
        if method == "POST":
            _handle_clients_post(h, body or {}); return True
        return False

    # /clients/{id}
    m = _RE_CLIENT_ID.match(p)
    if m and method == "DELETE":
        _handle_client_delete(h, m.group(1)); return True

    return False
