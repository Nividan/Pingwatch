"""
core/setup_logic.py — Shared setup logic for CLI and GUI wizards.

Pure-functional helpers with no UI coupling.  Both ``setup_wizard.py``
(CLI) and ``gui_setup.py`` (tkinter) import from here.
"""

import os
import re
import socket
import subprocess
import sys

# ── Shared validation helpers ───────────────────────────────────────────────
# Used by both wizards for optional SMTP / Syslog fields. Validation is
# permissive: bad input is logged and saved anyway, so the wizard never
# blocks on cosmetic issues.

_HOST_RE  = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,252}$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def valid_host(s: str) -> bool:
    s = (s or "").strip()
    return bool(s and len(s) <= 253 and _HOST_RE.match(s))


def valid_email(s: str) -> bool:
    return bool(s and _EMAIL_RE.match(str(s).strip()))

# ── Package definitions ─────────────────────────────────────────────────────

PACKAGES = [
    {
        "import":   "tkinter",
        "name":     "tkinter",
        "install":  None,   # stdlib — cannot be pip-installed
        "pip":      None,
        "desc":     "status window GUI",
        "required": False,
    },
    {
        "import":       "pystray",
        "name":         "pystray",
        "install":      "pystray>=0.19.5",
        "pip":          True,
        "desc":         "system tray icon",
        "required":     False,
        "desktop_only": True,
    },
    {
        "import":       "PIL",
        "name":         "Pillow",
        "install":      "Pillow>=10.0.0",
        "pip":          True,
        "desc":         "image support (tray icon)",
        "required":     False,
        "desktop_only": True,
    },
    {
        "import":   "paramiko",
        "name":     "paramiko",
        "install":  "paramiko>=3.0.0",
        "pip":      True,
        "desc":     "SSH device backups",
        "required": False,
    },
    {
        "import":   "smbclient",
        "name":     "smbprotocol",
        "install":  "smbprotocol>=1.10.0",
        "pip":      True,
        "desc":     "SMB / CIFS remote database backup upload",
        "required": False,
    },
    {
        "import":   "pyrad",
        "name":     "pyrad",
        "install":  "pyrad>=2.4",
        "pip":      True,
        "desc":     "RADIUS authentication",
        "required": False,
    },
    {
        "import":   "saml2",
        "name":     "pysaml2",
        "install":  "pysaml2>=7.5",
        "pip":      True,
        "desc":     "SAML 2.0 SSO (enterprise identity federation)",
        "required": False,
    },
    {
        "import":   "signxml",
        "name":     "signxml",
        "install":  "signxml>=3.2",
        "pip":      True,
        "desc":     "SAML XML signature verification (pairs with pysaml2)",
        "required": False,
    },
    {
        "import":   "authlib",
        "name":     "authlib",
        "install":  "authlib>=1.3",
        "pip":      True,
        "desc":     "OpenID Connect SSO (Azure AD, Okta, Google, Keycloak)",
        "required": False,
    },
    {
        "import":   "cryptography",
        "name":     "cryptography",
        "install":  "cryptography>=41.0.0",
        "pip":      True,
        "desc":     "TLS certificate generation & encryption",
        "required": True,
    },
    {
        "import":   "ldap3",
        "name":     "ldap3",
        "install":  "ldap3>=2.9.0",
        "pip":      True,
        "desc":     "LDAP / Active Directory authentication",
        "required": False,
    },
    {
        "import":   "psutil",
        "name":     "psutil",
        "install":  "psutil>=5.9.0",
        "pip":      True,
        "desc":     "server CPU / RAM / disk monitoring widget",
        "required": False,
    },
    {
        "import":   "pyVmomi",
        "name":     "pyvmomi",
        "install":  "pyvmomi>=8.0.0",
        "pip":      True,
        "desc":     "VMware vCenter / ESXi VM metrics",
        "required": False,
    },
    {
        "import":   "pyotp",
        "name":     "pyotp",
        "install":  "pyotp>=2.9.0",
        "pip":      True,
        "desc":     "two-factor authentication (TOTP)",
        "required": False,
    },
    {
        "import":   "qrcode",
        "name":     "qrcode",
        "install":  "qrcode>=7.4.0",
        "pip":      True,
        "desc":     "QR code image rendering for 2FA enrolment",
        "required": False,
    },
    {
        "import":   "jinja2",
        "name":     "Jinja2",
        "install":  "Jinja2>=3.1",
        "pip":      True,
        "desc":     "report HTML template rendering",
        "required": False,
    },
    {
        "import":   "matplotlib",
        "name":     "matplotlib",
        "install":  "matplotlib>=3.7",
        "pip":      True,
        "desc":     "report charts (rendered to PNG)",
        "required": False,
    },
    {
        "import":   "weasyprint",
        "name":     "weasyprint",
        "install":  "weasyprint>=62.0",
        "pip":      True,
        "desc":     "PDF report generation (HTML→PDF; Linux also needs libpango/libcairo)",
        "required": False,
    },
    {
        "import":   "openpyxl",
        "name":     "openpyxl",
        "install":  "openpyxl>=3.1",
        "pip":      True,
        "desc":     "XLSX reader for SolarWinds bulk device imports",
        "required": False,
    },
]


# ── Package checks ──────────────────────────────────────────────────────────

def check_import(module_name: str) -> bool:
    """Return True if the module is importable."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def pip_available() -> bool:
    """Return True if pip is usable."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True, text=True,
        )
        return r.returncode == 0
    except Exception:
        return False


def pip_install(package_spec: str) -> "tuple[bool, str]":
    """Try pip install with escalating fallbacks.

    Returns (success, error_snippet).
    """
    last_err = ""

    # Try 1: standard pip
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", package_spec],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return True, ""
        last_err = (r.stderr or r.stdout or "").strip()
    except Exception as e:
        last_err = str(e)

    # Try 2: --user (Linux/macOS, avoids permission errors outside venv)
    if sys.platform != "win32":
        try:
            r2 = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--user", package_spec],
                capture_output=True, text=True,
            )
            if r2.returncode == 0:
                return True, ""
            last_err = (r2.stderr or r2.stdout or last_err).strip()
        except Exception:
            pass

    # Try 3: --break-system-packages (PEP 668 — Debian/Ubuntu 23.04+)
    if sys.platform != "win32" and "externally-managed-environment" in last_err:
        try:
            r3 = subprocess.run(
                [sys.executable, "-m", "pip", "install",
                 "--break-system-packages", package_spec],
                capture_output=True, text=True,
            )
            if r3.returncode == 0:
                return True, ""
            last_err = (r3.stderr or r3.stdout or last_err).strip()
        except Exception:
            pass

    return False, last_err


def check_snmpget() -> bool:
    """Return True if snmpget is in PATH."""
    import shutil
    return shutil.which("snmpget") is not None


def check_ping() -> bool:
    """Return True if the ping binary is available."""
    import shutil
    return shutil.which("ping") is not None


def install_snmpget() -> "tuple[bool, str]":
    """Attempt to install net-snmp via the system package manager.

    Returns (success, message).
    """
    import platform
    import shutil
    _sys = platform.system()
    if _sys == "Windows":
        import struct
        _bits = struct.calcsize("P") * 8
        if _bits == 64:
            _url = "https://sourceforge.net/projects/net-snmp/files/net-snmp%20binaries/5.5-binaries/net-snmp-5.5.0-2.x64.exe/download"
            _ver = "5.5.0 x64"
        else:
            _url = "https://sourceforge.net/projects/net-snmp/files/net-snmp%20binaries/5.7-binaries/net-snmp-5.7.0-1.x86.exe/download"
            _ver = "5.7.0 x86"
        _installer = os.path.join(os.environ.get("TEMP", "."), "net-snmp-setup.exe")
        try:
            import urllib.request
            urllib.request.urlretrieve(_url, _installer)
        except Exception as e:
            return False, f"Download failed: {e}"
        try:
            # /S = silent install; net-snmp NSIS installer supports it
            r = subprocess.run([_installer, "/S"], capture_output=True,
                               text=True, timeout=120)
            # Check if snmpget is now available in the default install path
            _snmp_bin = r"C:\usr\bin\snmpget.exe"
            if os.path.isfile(_snmp_bin) or shutil.which("snmpget"):
                return True, f"Installed net-snmp {_ver}"
            return False, f"Installer ran but snmpget not found (exit code {r.returncode})"
        except subprocess.TimeoutExpired:
            return False, "Installer timed out (120s)"
        except Exception as e:
            return False, f"Install failed: {e}"
        finally:
            try:
                os.unlink(_installer)
            except Exception:
                pass
    elif _sys == "Linux":
        for pkg_mgr, pkg_name in [
            ("apt-get", "snmp"), ("dnf", "net-snmp-utils"), ("yum", "net-snmp-utils"),
        ]:
            if shutil.which(pkg_mgr):
                try:
                    r = subprocess.run(
                        ["sudo", pkg_mgr, "install", "-y", pkg_name],
                        capture_output=True, text=True)
                    if r.returncode == 0:
                        return True, f"Installed via {pkg_mgr}"
                except Exception:
                    pass
        return False, "No supported package manager found"
    elif _sys == "Darwin":
        if shutil.which("brew"):
            try:
                r = subprocess.run(["brew", "install", "net-snmp"],
                                   capture_output=True, text=True)
                if r.returncode == 0:
                    return True, "Installed via Homebrew"
            except Exception:
                pass
        return False, "Homebrew not available"
    return False, "Unsupported platform"


# ── Port helpers ────────────────────────────────────────────────────────────

def port_in_use(port: int) -> "int | None":
    """Return a truthy value (dummy PID 1) if port is occupied, None if free.

    Uses a pure-Python socket bind test — works on all platforms.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return None
        except PermissionError:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as cs:
                    cs.settimeout(0.5)
                    cs.connect(("127.0.0.1", port))
                return 1
            except OSError:
                return None
        except OSError:
            return 1


def check_webserver_on_port(port: int) -> "str | None":
    """Return the name of a known web server on this port, or None."""
    if sys.platform == "win32":
        return None
    try:
        import shutil as _sh
        if _sh.which("ss"):
            r = subprocess.run(
                ["ss", "-tlnp", f"sport = :{port}"],
                capture_output=True, text=True,
            )
            out = r.stdout.lower()
        elif _sh.which("lsof"):
            r = subprocess.run(
                ["lsof", "-i", f"tcp:{port}", "-s", "tcp:LISTEN", "-F", "c"],
                capture_output=True, text=True,
            )
            out = r.stdout.lower()
        else:
            return None
        for svc in ("apache2", "apache", "httpd", "nginx", "lighttpd", "caddy"):
            if svc in out:
                return svc
    except Exception:
        pass
    return None


def pid_name(pid: int) -> str:
    """Best-effort process name for the given PID."""
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Process -Id {pid} -EA SilentlyContinue).Name"],
                capture_output=True, text=True,
            )
            return r.stdout.strip() or "unknown"
        else:
            import shutil as _sh
            if _sh.which("ps"):
                r = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                                   capture_output=True, text=True)
                return r.stdout.strip() or "unknown"
    except Exception:
        pass
    return "unknown"


def kill_pid(pid: int) -> bool:
    """Attempt to kill the given PID.  Cross-platform."""
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Stop-Process -Id {pid} -Force -EA SilentlyContinue"],
                capture_output=True,
            )
            return r.returncode == 0
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
            return True
    except Exception:
        return False


def kill_port_processes(*ports: int) -> bool:
    """Kill processes listening on the given TCP ports.  Cross-platform.

    Used by the launcher to clean up stale processes before starting the
    server — replaces the PowerShell one-liner from the old start.bat.
    """
    ok = True
    for port in ports:
        if port_in_use(port) is None:
            continue
        try:
            if sys.platform == "win32":
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"(Get-NetTCPConnection -LocalPort {port} -State Listen "
                     f"-EA SilentlyContinue).OwningProcess | ForEach-Object "
                     f"{{ Stop-Process -Id $_ -Force -EA SilentlyContinue }}"],
                    capture_output=True,
                )
                if r.returncode != 0:
                    ok = False
            else:
                import shutil as _sh
                if _sh.which("fuser"):
                    subprocess.run(["fuser", "-k", f"{port}/tcp"],
                                   capture_output=True)
                elif _sh.which("lsof"):
                    r = subprocess.run(
                        ["lsof", "-ti", f"tcp:{port}"],
                        capture_output=True, text=True)
                    for _pid in r.stdout.strip().splitlines():
                        try:
                            os.kill(int(_pid), 15)
                        except Exception:
                            pass
        except Exception:
            ok = False
    return ok


# ── PostgreSQL helpers ──────────────────────────────────────────────────────

def generate_pg_password(length: int = 20) -> str:
    """Generate a random alphanumeric password."""
    import random
    import string
    chars = string.ascii_letters + string.digits
    return "".join(random.SystemRandom().choices(chars, k=length))


def detect_pg_server() -> "tuple[bool, str]":
    """Return (installed, version_string)."""
    import shutil as _sh
    for cmd in (["psql", "--version"], ["pg_isready", "--version"]):
        if _sh.which(cmd[0]):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                ver = (r.stdout or r.stderr or "").strip().splitlines()[0]
                return True, ver
            except Exception:
                return True, cmd[0]
    if sys.platform == "win32":
        import glob as _glob
        _candidates = _glob.glob(
            r"C:\Program Files\PostgreSQL\*\bin\psql.exe"
        )
        if _candidates:
            _psql_exe = sorted(_candidates)[-1]
            try:
                r = subprocess.run([_psql_exe, "--version"],
                                   capture_output=True, text=True, timeout=5)
                ver = (r.stdout or r.stderr or "").strip().splitlines()[0]
                return True, ver
            except Exception:
                return True, _psql_exe
    return False, ""


def pg_install_instructions() -> str:
    """Return distro-specific PostgreSQL install instructions."""
    import platform as _plat
    import shutil as _sh
    _sys = _plat.system()
    if _sys == "Linux":
        distro = ""
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("ID="):
                        distro = line.strip().split("=")[1].strip('"').lower()
                        break
        except Exception:
            pass
        if distro in ("ubuntu", "debian", "pop", "mint", "elementary", "raspbian"):
            return "sudo apt install postgresql postgresql-contrib"
        if distro in ("rhel", "centos", "rocky", "almalinux"):
            return ("sudo dnf install postgresql-server postgresql && "
                    "sudo postgresql-setup --initdb && "
                    "sudo systemctl enable --now postgresql")
        if distro == "fedora":
            return ("sudo dnf install postgresql-server && "
                    "sudo postgresql-setup --initdb && "
                    "sudo systemctl enable --now postgresql")
        if _sh.which("apt-get"):
            return "sudo apt install postgresql postgresql-contrib"
        if _sh.which("dnf"):
            return "sudo dnf install postgresql-server postgresql"
        if _sh.which("yum"):
            return "sudo yum install postgresql-server postgresql"
        return "Install PostgreSQL using your distribution's package manager"
    if _sys == "Darwin":
        return "brew install postgresql@16 && brew services start postgresql@16"
    return "Download from https://www.postgresql.org/download/"


def test_pg_connection(host, port, database, user, password) -> "tuple[bool, str]":
    """Test a PostgreSQL connection.  Returns (ok, error_message)."""
    try:
        import psycopg2
        con = psycopg2.connect(
            host=host, port=int(port), dbname=database,
            user=user, password=password, connect_timeout=5,
        )
        ver = con.server_version
        con.close()
        major, minor = divmod(ver, 10000)
        return True, f"Connected — PostgreSQL {major}.{minor}"
    except ImportError:
        return False, "psycopg2 not installed — install it first on the Packages step"
    except Exception as e:
        return False, str(e)


# ── Database init ───────────────────────────────────────────────────────────

def default_wizard_state() -> dict:
    """Return the default wizard state dict."""
    from core.config import PORT, SNMP_TRAP_PORT, TLS_PORT_DEFAULT
    return {
        "http_enabled":    True,
        "http_port":       PORT,
        "snmp_port":       SNMP_TRAP_PORT,
        "tls_enabled":     True,
        "tls_port":        TLS_PORT_DEFAULT,
        "tls_cert_pem":    "",
        "tls_key_pem_enc": "",
        "tls_cert_source": "",
        "tls_cn":          "",
        "org_name":        "PingWatch",
        "http_redirect":   True,
        "headless":        False,
        "db_backend":      "sqlite",
        "pg_host":         "localhost",
        "pg_port":         5432,
        "pg_database":     "pingwatch",
        "pg_user":         "pingwatch",
        "pg_password":     "",
        "admin_user":      "admin",
        "admin_pass":      "",
        # Optional — populated by the Alerts page / step_smtp / step_syslog /
        # step_anomaly.  Absent keys mean "user skipped."
        "smtp_enabled":               False,
        "smtp_host":                  "",
        "smtp_port":                  587,
        "smtp_tls":                   "starttls",
        "smtp_user":                  "",
        "smtp_pass":                  "",
        "smtp_from":                  "",
        "syslog_enabled":             False,
        "syslog_host":                "",
        "syslog_port":                514,
        "syslog_proto":               "udp",
        "syslog_min_severity":        "warning",
        "anomaly_default_new_sensors": False,
    }


def collect_optional_settings(state: dict) -> dict:
    """Extract optional SMTP / Syslog / Anomaly fields from wizard state.

    Validation is permissive — invalid-looking values are left in place so
    the user can correct them in Settings later.  Only writes keys the user
    actually enabled; skipped sections produce no DB rows.
    """
    out = {}

    # Organisation name — always save if non-empty (length-capped)
    org = state.get("org_name")
    if isinstance(org, str) and org.strip():
        out["org_name"] = org.strip()[:120]

    # SMTP — only persist if the user enabled it AND provided a host
    if state.get("smtp_enabled") and isinstance(state.get("smtp_host"), str) \
            and state["smtp_host"].strip():
        host = state["smtp_host"].strip()
        out["smtp_host"] = host
        try:
            p = int(state.get("smtp_port", 587))
            out["smtp_port"] = str(p if 1 <= p <= 65535 else 587)
        except (TypeError, ValueError):
            out["smtp_port"] = "587"
        tls = str(state.get("smtp_tls", "starttls")).strip().lower()
        out["smtp_tls"] = tls if tls in ("starttls", "ssl", "none") else "starttls"
        user = state.get("smtp_user")
        if isinstance(user, str):
            out["smtp_user"] = user.strip()[:256]
        pw = state.get("smtp_pass")
        if isinstance(pw, str) and pw:
            out["smtp_pass"] = pw
        frm = state.get("smtp_from")
        if isinstance(frm, str) and frm.strip():
            out["smtp_from"] = frm.strip()[:256]

    # Syslog — only persist if enabled + host set
    if state.get("syslog_enabled") and isinstance(state.get("syslog_host"), str) \
            and state["syslog_host"].strip():
        out["syslog_host"] = state["syslog_host"].strip()
        try:
            p = int(state.get("syslog_port", 514))
            out["syslog_port"] = str(p if 1 <= p <= 65535 else 514)
        except (TypeError, ValueError):
            out["syslog_port"] = "514"
        proto = str(state.get("syslog_proto", "udp")).strip().lower()
        out["syslog_proto"] = proto if proto in ("udp", "tcp") else "udp"
        sev = str(state.get("syslog_min_severity", "warning")).strip().lower()
        out["syslog_min_severity"] = sev if sev in ("critical", "warning", "info") else "warning"

    # Anomaly default — always persist (0 or 1)
    anom = state.get("anomaly_default_new_sensors")
    out["anomaly_default_new_sensors"] = "1" if anom else "0"

    return out


def save_wizard_config(state: dict):
    """Write pingwatch.conf from wizard state."""
    from db.backend import save_config
    cfg = {
        "db_backend":  state["db_backend"],
        "pg_host":     state.get("pg_host", "localhost"),
        "pg_port":     int(state.get("pg_port", 5432)),
        "pg_database": state.get("pg_database", "pingwatch"),
        "pg_user":     state.get("pg_user", "pingwatch"),
        "pg_password": state.get("pg_password", ""),
    }
    save_config(cfg)


def initialize_database(state: dict, progress_cb=None) -> "tuple[bool, str]":
    """Create DB schemas, seed defaults, save settings, create admin user.

    Returns (success, error_message).  ``progress_cb`` is called with
    (step_index, step_label) for UI updates.
    """
    try:
        if progress_cb:
            progress_cb(0, "Saving configuration…")
        save_wizard_config(state)

        if progress_cb:
            progress_cb(1, "Loading database backend…")
        from db.backend import load_config
        load_config()

        if progress_cb:
            progress_cb(2, "Creating database schemas…")
        from db.core import db_init, logs_db_init
        db_init()
        logs_db_init()

        if progress_cb:
            progress_cb(3, "Saving application settings…")
        from db import db_save_settings
        settings = {}
        for k in ("http_port", "tls_enabled", "tls_port", "tls_cert_pem",
                   "tls_key_pem_enc", "tls_cert_source", "tls_cn",
                   "org_name", "http_redirect", "headless", "snmp_port",
                   "http_enabled"):
            if k in state:
                val = state[k]
                if isinstance(val, bool):
                    val = "1" if val else "0"
                settings[k] = str(val)
        # Merge in optional SMTP / Syslog / Anomaly entries
        settings.update(collect_optional_settings(state))
        db_save_settings(settings)

        if progress_cb:
            progress_cb(4, "Creating admin user…")
        admin_user = state.get("admin_user", "admin")
        admin_pass = state.get("admin_pass", "")
        if admin_user and admin_pass:
            from db import db_add_user
            db_add_user(admin_user, admin_pass, "admin")

        if progress_cb:
            progress_cb(5, "Done!")
        return True, ""
    except Exception as e:
        return False, str(e)


# ── Windows Firewall ────────────────────────────────────────────────────────

def win_firewall_check(name: str) -> bool:
    """Return True if a Windows Firewall rule with this name exists."""
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
            capture_output=True)
        return r.returncode == 0
    except Exception:
        return False


def win_firewall_add(name: str, proto: str, port: int) -> "tuple[bool, str]":
    """Add a Windows Firewall inbound allow rule. Returns (ok, message)."""
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={name}", "dir=in", "action=allow",
             f"protocol={proto}", f"localport={port}"],
            capture_output=True, text=True)
        if r.returncode == 0:
            return True, f"Rule added: {proto} {port}"
        return False, (r.stderr or r.stdout or "Unknown error").strip()
    except Exception as e:
        return False, str(e)


def get_firewall_rules(state: dict) -> list:
    """Return list of (proto, port, rule_name) tuples from wizard state."""
    rules = [("TCP", state.get("http_port", 7070), "PingWatch HTTP")]
    if state.get("tls_enabled"):
        rules.append(("TCP", state.get("tls_port", 8443), "PingWatch HTTPS"))
    rules.append(("UDP", state.get("snmp_port", 162), "PingWatch SNMP traps"))
    return rules


# ── Desktop shortcut (Windows) ──────────────────────────────────────────────

def win_create_shortcut() -> "tuple[bool, str]":
    """Create a PingWatch desktop shortcut. Returns (ok, message)."""
    import sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    shortcut_path = os.path.join(desktop, "PingWatch.lnk")
    if os.path.isfile(shortcut_path):
        return True, "Shortcut already exists"
    icon = os.path.join(_root, "frontend", "favicon.ico")

    # Prefer pythonw.exe + launcher.pyw (no console window) over start.bat
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    launcher = os.path.join(_root, "windows", "launcher.pyw")
    if os.path.isfile(pythonw) and os.path.isfile(launcher):
        target = pythonw
        # Backtick-escape double quotes inside a PowerShell double-quoted string
        args_line = f'$s.Arguments="`"{launcher}`"";'
    else:
        target = os.path.join(_root, "windows", "start.bat")
        args_line = ''

    ps_cmd = (
        f'$s=(New-Object -COM WScript.Shell).CreateShortcut("{shortcut_path}");'
        f'$s.TargetPath="{target}";'
        f'{args_line}'
        f'$s.WorkingDirectory="{_root}";'
        f'$s.IconLocation="{icon},0";'
        f'$s.Description="PingWatch Network Monitor";'
        f'$s.Save()'
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return True, f"Created: {shortcut_path}"
        return False, (r.stderr or "PowerShell shortcut creation failed").strip()
    except Exception as e:
        return False, str(e)


# ── Windows Task Scheduler (auto-start) ────────────────────────────────────

def win_task_exists(task_name: str = "PingWatch") -> bool:
    """Return True if the scheduled task already exists."""
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", task_name],
                           capture_output=True)
        return r.returncode == 0
    except Exception:
        return False


def win_install_task(as_system: bool = True,
                     task_name: str = "PingWatch") -> "tuple[bool, str]":
    """Register PingWatch as a Windows startup task. Returns (ok, message)."""
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.isfile(_pythonw):
        _pythonw = sys.executable
    _server_py = os.path.join(_root, "server.py")

    def _pse(s):
        return s.replace("'", "''")

    _args = f'"{_pse(_server_py)}"'
    if as_system:
        _args += " --headless"
        _principal = "$p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest"
        _trigger = "$t = New-ScheduledTaskTrigger -AtStartup"
    else:
        import getpass
        _cur_user = os.environ.get("USERNAME") or getpass.getuser()
        _principal = (
            f"$p = New-ScheduledTaskPrincipal "
            f"-UserId '{_pse(_cur_user)}' -RunLevel Highest -LogonType Interactive"
        )
        _trigger = "$t = New-ScheduledTaskTrigger -AtLogOn"

    _ps_lines = [
        f"$a = New-ScheduledTaskAction -Execute '{_pse(_pythonw)}' "
        f"-Argument '{_args}' -WorkingDirectory '{_pse(_root)}'",
        _trigger,
        _principal,
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries",
        f"Register-ScheduledTask -TaskName '{task_name}' "
        f"-Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null",
    ]
    _ps_script = "; ".join(_ps_lines)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _ps_script],
            capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            mode = "at boot (SYSTEM, headless)" if as_system else "at logon (current user)"
            return True, f"Task '{task_name}' installed — starts {mode}"
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return False, err[0][:200] if err else "Task registration failed"
    except subprocess.TimeoutExpired:
        return False, "PowerShell timed out (30s)"
    except Exception as e:
        return False, str(e)
