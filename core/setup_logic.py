"""
core/setup_logic.py — Shared setup logic for CLI and GUI wizards.

Pure-functional helpers with no UI coupling.  Both ``setup_wizard.py``
(CLI) and ``gui_setup.py`` (tkinter) import from here.
"""

import os
import socket
import subprocess
import sys

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
        # Find choco — check PATH first, then default install location
        choco = shutil.which("choco")
        if not choco:
            _default = r"C:\ProgramData\chocolatey\bin\choco.exe"
            if os.path.isfile(_default):
                choco = _default
        if choco:
            try:
                r = subprocess.run([choco, "install", "net-snmp", "-y", "--no-progress"],
                                   capture_output=True, text=True, timeout=120)
                if r.returncode == 0:
                    return True, "Installed via Chocolatey"
                # Extract useful error from choco output
                out = (r.stdout or "") + "\n" + (r.stderr or "")
                # Find lines with "Error" or "not installed" or meaningful info
                err_lines = [ln.strip() for ln in out.splitlines()
                             if ln.strip() and any(kw in ln.lower()
                             for kw in ("error", "fail", "not found", "not install",
                                        "unable", "cannot", "packages failed"))]
                err_msg = err_lines[-1][:200] if err_lines else f"choco exited with code {r.returncode}"
                return False, err_msg
            except subprocess.TimeoutExpired:
                return False, "Chocolatey install timed out (120s)"
            except Exception as e:
                return False, str(e)
        # Find winget — check PATH then default locations
        winget = shutil.which("winget")
        if not winget:
            import glob
            _candidates = glob.glob(
                r"C:\Users\*\AppData\Local\Microsoft\WindowsApps\winget.exe"
            )
            if _candidates:
                winget = _candidates[0]
        if winget:
            try:
                r = subprocess.run([winget, "install", "net-snmp.net-snmp"],
                                   capture_output=True, text=True)
                if r.returncode == 0:
                    return True, "Installed via winget"
                return False, (r.stderr or r.stdout or "winget install failed").strip().splitlines()[-1][:200]
            except Exception as e:
                return False, str(e)
        return False, "Neither Chocolatey nor winget found. Install Chocolatey first, then click Retry."
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
    }


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
