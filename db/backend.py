"""
db/backend.py — Backend selection and pingwatch.conf management.

The DB backend choice is stored in a JSON config file (not in the database
itself, since you are choosing *which* database to use).  Environment
variables override file values when set.
"""

import json
import os
import tempfile

from core.config import _ROOT
from core.logger import log

_CONF_PATH = os.path.join(_ROOT, "pingwatch.conf")

# ── In-memory cache (populated by load_config) ──────────────────────
_cfg: dict = {}


def _defaults() -> dict:
    return {
        "db_backend": "sqlite",
        "pg_host": "localhost",
        "pg_port": 5432,
        "pg_database": "pingwatch",
        "pg_user": "pingwatch",
        "pg_password": "",
    }


def load_config() -> dict:
    """Load pingwatch.conf into the module-level cache.

    Environment variables (PW_DB_BACKEND, PW_PG_HOST, …) take precedence
    over file values when set.
    """
    global _cfg
    cfg = _defaults()

    # Read file if it exists
    if os.path.exists(_CONF_PATH):
        try:
            with open(_CONF_PATH, "r", encoding="utf-8") as f:
                stored = json.load(f)
            cfg.update(stored)
        except Exception as e:
            log.warning(f"Failed to read {_CONF_PATH}: {e} — using defaults")

    # Environment overrides
    _env_map = {
        "PW_DB_BACKEND": ("db_backend", str),
        "PW_PG_HOST":    ("pg_host",     str),
        "PW_PG_PORT":    ("pg_port",     int),
        "PW_PG_DATABASE":("pg_database", str),
        "PW_PG_USER":    ("pg_user",     str),
        "PW_PG_PASSWORD":("pg_password", str),
    }
    for env_key, (cfg_key, cast) in _env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            try:
                cfg[cfg_key] = cast(val)
            except (ValueError, TypeError):
                pass

    _cfg = cfg
    return cfg


def save_config(cfg: dict):
    """Write pingwatch.conf atomically (cross-platform safe)."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(_CONF_PATH), suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp_path, _CONF_PATH)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    # Update in-memory cache
    global _cfg
    _cfg = {**_defaults(), **cfg}


def get_config() -> dict:
    """Return the current in-memory config (call load_config first)."""
    return dict(_cfg)


def is_pg() -> bool:
    """True when the active backend is PostgreSQL."""
    return _cfg.get("db_backend", "sqlite") == "postgresql"


def needs_setup() -> bool:
    """True when pingwatch.conf does not exist (first-run scenario)."""
    return not os.path.exists(_CONF_PATH)
