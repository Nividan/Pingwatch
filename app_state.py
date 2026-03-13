"""
app_state.py — Shared runtime globals accessible by both server.py and routes/*.

This module exists so that routes/* can import STATE, effective ports, etc.
without creating circular imports (routes should never import from server.py).
"""

import time
from state import MonitorState

# ── Application state ─────────────────────────────────────────────
STATE = MonitorState()

# ── Version & uptime ─────────────────────────────────────────────
APP_VERSION  = "0.5"
SERVER_START = time.time()

# ── Effective network ports (overwritten by main() from settings) ─
effective_port      = 7070
effective_snmp_port = 162

# ── System-tray icon reference (set by main(); used by DB import) ─
tray_icon = None
