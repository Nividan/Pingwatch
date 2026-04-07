"""
core/constants.py — Centralized magic-number constants used across the codebase.

Anything imported here should be a value used in 2+ places. Keep this file small
and stable — it's the single source of truth for tunable defaults.
"""

# ── Time constants ───────────────────────────────────────────────
SECONDS_PER_MINUTE      = 60
SECONDS_PER_HOUR        = 3600
SECONDS_PER_DAY         = 86400

# History window defaults (used by route handlers + frontend display)
HISTORY_DEFAULT_MINUTES = 1440      # 24 hours
HISTORY_MAX_SAMPLES     = 10000

# Background loops
SAMPLE_BUFFER_FLUSH_SEC = 5         # core/state.py — sample buffer flush interval
AUTOSAVE_INTERVAL_SEC   = 60        # db/persistence.py — autosave loop

# ── Probe defaults ───────────────────────────────────────────────
PROBE_DEFAULT_INTERVAL  = 5
PROBE_DEFAULT_TIMEOUT   = 4

# ── Network constraints ──────────────────────────────────────────
PORT_MIN                = 1
PORT_MAX                = 65535

# Hostname max length per RFC 1035
HOSTNAME_MAX            = 253

# ── Session / auth defaults ──────────────────────────────────────
SESSION_TTL_DEFAULT_SEC = SECONDS_PER_DAY   # 24h default session length

# ── Sensor history (in-memory deque size) ────────────────────────
SENSOR_HISTORY_SIZE     = 80
