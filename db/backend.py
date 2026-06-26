"""
db/backend.py — Backend selection and pingwatch.conf management.

The DB backend choice is stored in a JSON config file (not in the database
itself, since you are choosing *which* database to use).  Environment
variables override file values when set.
"""

import json
import os
import tempfile

from core.config import DATA_ROOT
from core.logger import log

_CONF_PATH = os.path.join(DATA_ROOT, "pingwatch.conf")

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
    if cfg.get("db_backend") == "postgresql" and not cfg.get("pg_password"):
        log.warning("PostgreSQL backend selected but pg_password is empty — connection may fail")
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


def pg_env(cfg: dict) -> tuple:
    """Return (env_dict, pgpass_path) for use with pg_dump / psql subprocesses.

    Creates a temporary .pgpass file (mode 0600) so the password is never
    exposed in the process environment (readable via /proc/<pid>/environ).
    The caller MUST delete pgpass_path after the subprocess completes:

        env, pgpass = pg_env(cfg)
        try:
            subprocess.run(cmd, env=env, ...)
        finally:
            try: os.unlink(pgpass)
            except OSError: pass
    """
    import stat
    password = cfg.get("pg_password", "")
    host     = cfg.get("pg_host", "localhost")
    port     = str(cfg.get("pg_port", 5432))
    dbname   = cfg.get("pg_database", "pingwatch")
    user     = cfg.get("pg_user", "pingwatch")
    # pgpass format: hostname:port:database:username:password
    # Use '*' wildcards for host/port/db/user so one file covers both schemas
    pgpass_content = f"*:*:*:{user}:{password}\n"
    fd, pgpass_path = tempfile.mkstemp(prefix="pgpass_", suffix=".conf")
    try:
        os.write(fd, pgpass_content.encode())
    finally:
        os.close(fd)
    try:
        os.chmod(pgpass_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass  # Windows — chmod not supported, acceptable
    env = {**os.environ, "PGPASSFILE": pgpass_path}
    # Remove PGPASSWORD if it was inherited from the parent environment
    env.pop("PGPASSWORD", None)
    return env, pgpass_path
