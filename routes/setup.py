"""
routes/setup.py — First-run setup wizard API endpoints.

Handles: /api/setup/check-pg, /api/setup/test-connection, /api/setup/complete
"""

import platform
import shutil
import subprocess

from core.logger import log


def _detect_pg():
    """Detect if PostgreSQL client tools are available on the host.

    Returns dict with keys: installed, version, os_name, install_instructions.
    """
    info = {
        "installed": False,
        "version": "",
        "os_name": platform.system(),
        "install_instructions": "",
    }

    # Try pg_isready first (part of PostgreSQL client package)
    pg_isready = shutil.which("pg_isready")
    psql = shutil.which("psql")

    if psql:
        try:
            out = subprocess.check_output(
                [psql, "--version"], timeout=5, stderr=subprocess.STDOUT
            ).decode().strip()
            info["installed"] = True
            info["version"] = out
            return info
        except Exception:
            info["installed"] = True
            info["version"] = "unknown"
            return info

    if pg_isready:
        info["installed"] = True
        info["version"] = "pg_isready found"
        return info

    # Not installed — provide OS-specific instructions
    sys_name = platform.system()
    if sys_name == "Linux":
        # Try to detect distro
        distro = ""
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("ID="):
                        distro = line.strip().split("=")[1].strip('"').lower()
                        break
        except Exception:
            pass
        if distro in ("ubuntu", "debian", "pop", "mint", "elementary"):
            info["install_instructions"] = "sudo apt install postgresql postgresql-contrib"
        elif distro in ("rhel", "centos", "rocky", "almalinux", "fedora"):
            info["install_instructions"] = (
                "sudo dnf install postgresql-server postgresql && "
                "sudo postgresql-setup --initdb && "
                "sudo systemctl start postgresql"
            )
        else:
            info["install_instructions"] = (
                "Install PostgreSQL using your distribution's package manager"
            )
    elif sys_name == "Darwin":
        info["install_instructions"] = "brew install postgresql@16 && brew services start postgresql@16"
    elif sys_name == "Windows":
        info["install_instructions"] = (
            "Download and install from https://www.postgresql.org/download/windows/"
        )
    else:
        info["install_instructions"] = (
            "Install PostgreSQL from https://www.postgresql.org/download/"
        )
    return info


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # ── /api/setup/check-pg GET ──────────────────────────────────
    if path == "/api/setup/check-pg" and method == "GET":
        info = _detect_pg()
        h._json(200, info)
        return True

    # ── /api/setup/test-connection POST ──────────────────────────
    if path == "/api/setup/test-connection" and method == "POST":
        host   = str(body.get("host", "localhost")).strip()
        try:
            port = int(body.get("port", 5432))
        except (TypeError, ValueError):
            h._json(400, {"ok": False, "error": "port must be an integer"}); return True
        dbname = str(body.get("database", "pingwatch")).strip()
        user   = str(body.get("user", "pingwatch")).strip()
        pw     = str(body.get("password", ""))
        if not host or not dbname or not user:
            h._json(400, {"ok": False, "error": "host, database, and user are required"})
            return True
        from db.pg_pool import pg_test_connection
        ok, err = pg_test_connection(host, port, dbname, user, pw)
        h._json(200, {"ok": ok, "error": err})
        return True

    # ── /api/setup/complete POST ─────────────────────────────────
    if path == "/api/setup/complete" and method == "POST":
        backend = str(body.get("backend", "sqlite")).strip().lower()
        if backend not in ("sqlite", "postgresql"):
            h._json(400, {"error": "backend must be 'sqlite' or 'postgresql'"})
            return True

        from db.backend import save_config, load_config

        if backend == "sqlite":
            save_config({"db_backend": "sqlite"})
            load_config()
            # Initialize SQLite databases
            from db import db_init, logs_db_init, db_seed_users
            db_init()
            logs_db_init()
            db_seed_users()
            log.info("Setup complete: SQLite backend selected")
            h._json(200, {"ok": True, "restart_required": True})
            return True

        # PostgreSQL setup
        host   = str(body.get("host", "localhost")).strip()
        try:
            port = int(body.get("port", 5432))
        except (TypeError, ValueError):
            h._json(400, {"ok": False, "error": "port must be an integer"}); return True
        dbname = str(body.get("database", "pingwatch")).strip()
        user   = str(body.get("user", "pingwatch")).strip()
        pw     = str(body.get("password", ""))

        # Test connection first
        from db.pg_pool import pg_test_connection
        ok, err = pg_test_connection(host, port, dbname, user, pw)
        if not ok:
            h._json(400, {"ok": False, "error": f"Connection failed: {err}"})
            return True

        # Save config
        cfg = {
            "db_backend":  "postgresql",
            "pg_host":     host,
            "pg_port":     port,
            "pg_database": dbname,
            "pg_user":     user,
            "pg_password": pw,
        }
        save_config(cfg)
        load_config()

        # Initialize PG pool, schemas, seed users
        try:
            from db.pg_pool import pg_init_pool
            pg_init_pool()
            from db import db_init, logs_db_init, db_seed_users
            db_init()
            logs_db_init()
            db_seed_users()
            log.info("Setup complete: PostgreSQL backend selected")
            h._json(200, {"ok": True, "restart_required": True})
        except Exception as e:
            log.error(f"Setup PG init failed: {e}")
            h._json(500, {"ok": False, "error": "PostgreSQL setup failed — check server logs"})
        return True

    return False
