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
APP_VERSION      = "1.1"
APP_VERSION_NAME = "REST API tokens"
SERVER_START = time.time()

# ── Effective network ports (overwritten by main() from settings) ─
effective_port      = 7070
effective_snmp_port = 162

# ── TLS state (set by main() during startup) ─────────────────────
tls_active = False   # True when the server socket is SSL-wrapped

# ── Server readiness (set True after db_load completes) ──────────
ready = False

# ── System-tray icon reference (set by main(); used by DB import) ─
tray_icon = None
