"""
app_state.py — Shared runtime globals accessible by both server.py and routes/*.

This module exists so that routes/* can import STATE, effective ports, etc.
without creating circular imports (routes should never import from server.py).
"""

import time
from .state import MonitorState

# ── Application state ─────────────────────────────────────────────
STATE = MonitorState()

# ── Version & uptime ─────────────────────────────────────────────
# Release checklist: bump AGENT_VERSION in agent/agent.py alongside this —
# the Probes page shows "update available" when a deployed agent differs.
APP_VERSION      = "1.3"
APP_VERSION_NAME = "distributed probes"
SERVER_START = time.time()

# Distributed-probes wire protocol (v1.3). Bump only on breaking changes to
# the /api/agent/* contract; the server rejects agents whose protocol_version
# doesn't match with a clear 409 so they fail loudly instead of misbehaving.
PROBE_PROTOCOL_VERSION = 1

# ── Effective network ports (overwritten by main() from settings) ─
effective_port      = 7070
effective_snmp_port = 162

# ── TLS state (set by main() during startup) ─────────────────────
tls_active = False   # True when the server socket is SSL-wrapped

# ── Server readiness (set True after db_load completes) ──────────
ready = False

# ── System-tray icon reference (set by main(); used by DB import) ─
tray_icon = None
