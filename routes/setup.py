"""
routes/setup.py — First-run setup wizard API endpoints.

Handles: /api/setup/check-pg, /api/setup/test-connection, /api/setup/complete
"""

import platform
import re
import shutil
import subprocess

from core.logger import log


_HOST_RE  = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,252}$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _collect_optional_settings(body: dict) -> dict:
    """Extract optional settings from the setup payload.

    Invalid/out-of-range values are logged and dropped — they never fail
    the whole setup request. An empty dict means "user skipped."
    """
    out = {}

    def _warn(field, val, reason):
        # Avoid leaking full input back; just note that a field was rejected.
        preview = str(val)[:40] if val is not None else ""
        log.warning(f"setup: rejected optional setting '{field}' "
                    f"(reason: {reason}, input preview: {preview!r})")

    # Organisation name — free-form, just length-limit
    org = body.get("org_name")
    if isinstance(org, str) and org.strip():
        out["org_name"] = org.strip()[:120]

    # SMTP
    smtp_host = body.get("smtp_host")
    if isinstance(smtp_host, str) and smtp_host.strip():
        host = smtp_host.strip()
        if not _HOST_RE.match(host):
            _warn("smtp_host", host, "hostname format")
            # Save anyway — user may know what they're doing and server-side
            # SMTP connect will surface the real failure when alerts fire.
        out["smtp_host"] = host
        try:
            p = int(body.get("smtp_port", 587))
            out["smtp_port"] = str(p if 1 <= p <= 65535 else 587)
        except (TypeError, ValueError):
            _warn("smtp_port", body.get("smtp_port"), "not an integer")
            out["smtp_port"] = "587"
        tls = str(body.get("smtp_tls", "starttls")).strip().lower()
        if tls not in ("starttls", "ssl", "none"):
            _warn("smtp_tls", tls, "unknown mode")
            tls = "starttls"
        out["smtp_tls"] = tls
        user = body.get("smtp_user")
        if isinstance(user, str):
            out["smtp_user"] = user.strip()[:256]
        pw = body.get("smtp_pass")
        if isinstance(pw, str) and pw:
            out["smtp_pass"] = pw
        frm = body.get("smtp_from")
        if isinstance(frm, str) and frm.strip():
            frm = frm.strip()
            if not _EMAIL_RE.match(frm):
                _warn("smtp_from", frm, "not an email address")
            out["smtp_from"] = frm[:256]

    # Syslog
    sl_host = body.get("syslog_host")
    if isinstance(sl_host, str) and sl_host.strip():
        host = sl_host.strip()
        if not _HOST_RE.match(host):
            _warn("syslog_host", host, "hostname format")
        out["syslog_host"] = host
        try:
            p = int(body.get("syslog_port", 514))
            out["syslog_port"] = str(p if 1 <= p <= 65535 else 514)
        except (TypeError, ValueError):
            _warn("syslog_port", body.get("syslog_port"), "not an integer")
            out["syslog_port"] = "514"
        proto = str(body.get("syslog_proto", "udp")).strip().lower()
        if proto not in ("udp", "tcp"):
            _warn("syslog_proto", proto, "must be udp/tcp")
            proto = "udp"
        out["syslog_proto"] = proto
        sev = str(body.get("syslog_min_severity", "warning")).strip().lower()
        if sev not in ("critical", "warning", "info"):
            _warn("syslog_min_severity", sev, "must be critical/warning/info")
            sev = "warning"
        out["syslog_min_severity"] = sev

    # Anomaly default
    anom = body.get("anomaly_default_new_sensors")
    if anom:
        try:
            out["anomaly_default_new_sensors"] = "1" if int(anom) else "0"
        except (TypeError, ValueError):
            # Truthy-but-unparseable values (e.g. "yes") — treat as on.
            out["anomaly_default_new_sensors"] = "1"

    return out


def _persist_optional_settings(settings: dict) -> None:
    """Best-effort save. Any exception is logged and swallowed — setup must
    not fail because an optional field hit a DB quirk."""
    if not settings:
        return
    try:
        from db.users import db_save_settings
        db_save_settings(settings)
        log.info(f"setup: saved {len(settings)} optional setting(s): "
                 f"{sorted(settings.keys())}")
    except Exception as e:
        log.warning(f"setup: failed to persist optional settings "
                    f"({type(e).__name__}: {e}) — user can re-enter via Settings UI.")


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
            from db.core import db_seed_alert_profiles
            db_init()
            logs_db_init()
            db_seed_users()
            db_seed_alert_profiles()
            _persist_optional_settings(_collect_optional_settings(body))
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
            from db.core import db_seed_alert_profiles
            db_init()
            logs_db_init()
            db_seed_users()
            db_seed_alert_profiles()
            _persist_optional_settings(_collect_optional_settings(body))
            log.info("Setup complete: PostgreSQL backend selected")
            h._json(200, {"ok": True, "restart_required": True})
        except Exception as e:
            log.error(f"Setup PG init failed: {e}")
            h._json(500, {"ok": False, "error": "PostgreSQL setup failed — check server logs"})
        return True

    return False
