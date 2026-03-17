"""
settings.py — Mutable runtime settings, loaded from DB at startup.

All modules that need a configurable value import from here instead of config.py.
Values are seeded with defaults from config.py and overridden by db_load_settings()
in main() before any request is served.
"""

from .config import SESSION_TTL as _DEFAULT_TTL

_data: dict = {
    "session_ttl": _DEFAULT_TTL,
}


def get(key, default=None):
    return _data.get(key, default)


def load(d: dict):
    """Bulk-update settings (called at startup with DB values)."""
    _data.update(d)
