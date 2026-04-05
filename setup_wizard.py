"""
setup_wizard.py — PingWatch first-run interactive setup wizard.

Called by start.bat when no database exists (first launch) or when
the --setup flag is passed.  Guides the user through:
  1. Required package checks & installs
  2. HTTP port selection
  3. HTTPS / TLS certificate setup (includes HTTP → HTTPS redirect choice)
  4. SNMP trap port selection
  5. Windows Firewall rules
  6. Desktop shortcut
  7. Database initialisation & settings persistence
  8. systemd service install (Linux only)

Exit codes:
  0 — setup completed successfully (start.bat will launch server.py)
  1 — setup failed or was aborted
"""

import atexit
import os
import socket
import subprocess
import sys

# ── readline (enables backspace / line editing in input() on Linux/macOS) ─────
if sys.platform != "win32":
    try:
        import readline  # noqa: F401
    except ImportError:
        pass

# ── Paths (resolve relative to this script's directory) ──────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE)

from core.config import DB_PATH, LOGS_DB_PATH, PORT, TLS_PORT_DEFAULT, CERTS_DIR, SNMP_TRAP_PORT
import core.app_state as app_state

# ── ANSI colour helpers ───────────────────────────────────────────────────────
def _enable_ansi_windows() -> bool:
    """Enable Virtual Terminal Processing on Windows 10+; return True if supported."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes, ctypes.wintypes
        kernel32 = ctypes.windll.kernel32
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if not handle:
            return False
        mode = ctypes.wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except Exception:
        return False

_COLOUR = sys.stdout.isatty() and _enable_ansi_windows()

_C = {
    "green":  "\033[92m" if _COLOUR else "",
    "yellow": "\033[93m" if _COLOUR else "",
    "red":    "\033[91m" if _COLOUR else "",
    "cyan":   "\033[96m" if _COLOUR else "",
    "bold":   "\033[1m"  if _COLOUR else "",
    "reset":  "\033[0m"  if _COLOUR else "",
}

def _tag(kind: str, msg: str):
    tags = {
        "ok":    f"{_C['green']}[OK]   {_C['reset']}",
        "warn":  f"{_C['yellow']}[WARN] {_C['reset']}",
        "error": f"{_C['red']}[ERROR]{_C['reset']}",
        "setup": f"{_C['cyan']}[SETUP]{_C['reset']}",
        "info":  "       ",
    }
    prefix = tags.get(kind, "       ")
    # Indent continuation lines to align with the first line
    indent = " " * 8
    lines = str(msg).splitlines()
    print(prefix + lines[0])
    for line in lines[1:]:
        print(indent + line)


def _ask(prompt: str, default: str = "") -> str:
    """Prompt user for input; return default on empty Enter."""
    if not sys.stdin.isatty():
        return default
    display_default = f" [{default}]" if default else ""
    try:
        val = input(f"       {prompt}{display_default}: ").strip()
        return val if val else default
    except (EOFError, KeyboardInterrupt):
        raise


def _ask_password(prompt: str, default: str = "") -> str:
    """Prompt for a password, echoing '*' per character. Returns default on empty Enter."""
    if not sys.stdin.isatty():
        return default
    hint = " [press Enter to use generated password]" if default else ""
    sys.stdout.write(f"       {prompt}{hint}: ")
    sys.stdout.flush()
    chars = []
    try:
        if sys.platform == "win32":
            import msvcrt
            while True:
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    break
                if ch == "\x03":
                    raise KeyboardInterrupt
                if ch in ("\x08", "\x7f"):   # backspace
                    if chars:
                        chars.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                elif ch >= " ":
                    chars.append(ch)
                    sys.stdout.write("*")
                    sys.stdout.flush()
        else:
            import tty, termios
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                while True:
                    ch = sys.stdin.read(1)
                    if ch in ("\r", "\n"):
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        break
                    if ch == "\x03":
                        raise KeyboardInterrupt
                    if ch in ("\x08", "\x7f"):   # backspace
                        if chars:
                            chars.pop()
                            sys.stdout.write("\b \b")
                            sys.stdout.flush()
                    elif ch >= " ":
                        chars.append(ch)
                        sys.stdout.write("*")
                        sys.stdout.flush()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except EOFError:
        sys.stdout.write("\n")
        sys.stdout.flush()
    val = "".join(chars)
    return val if val else default


def _ask_yn(prompt: str, default: bool = True) -> bool:
    """Prompt for yes/no; return default on Enter."""
    if not sys.stdin.isatty():
        return default
    hint = "[Y/n]" if default else "[y/N]"
    try:
        val = input(f"       {prompt} {hint}: ").strip().lower()
        if not val:
            return default
        return val.startswith("y")
    except (EOFError, KeyboardInterrupt):
        raise


def _launcher_hint(setup: bool = False) -> str:
    """Return the right command to (re-)launch PingWatch for the current platform."""
    import platform as _plat
    suffix = " --setup" if setup else ""
    if _plat.system() == "Windows":
        return f"start.bat{suffix}"
    return f"bash {os.path.join(_BASE, 'start.sh')}{suffix}"


def _separator(char: str = "─", width: int = 56):
    print(_C["bold"] + char * width + _C["reset"])


# ── Wizard state (collected across steps) ────────────────────────────────────
_state = {
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
    "headless":        False,   # True when user opts out of desktop GUI
    # Database backend (populated by step2_database)
    "db_backend":   "sqlite",
    "pg_host":      "localhost",
    "pg_port":      5432,
    "pg_database":  "pingwatch",
    "pg_user":      "pingwatch",
    "pg_password":  "",
}

# Track whether the DB was partially created (for Ctrl+C cleanup)
_db_created = False


def _cleanup_on_abort():
    """Remove a partial DB if the wizard was aborted before completing Step 7."""
    global _db_created
    if _db_created and os.path.isfile(DB_PATH):
        try:
            os.unlink(DB_PATH)
            print(f"\n{_C['yellow']}[WARN]  Setup aborted — partial database removed.{_C['reset']}")
            print(f"        Run '{_launcher_hint()}' again to restart setup.")
        except OSError:
            pass


atexit.register(_cleanup_on_abort)


# ─────────────────────────────────────────────────────────────────────────────
# Step helpers
# ─────────────────────────────────────────────────────────────────────────────

# ── Package definitions ───────────────────────────────────────────────────────
_PACKAGES = [
    {
        "import":   "tkinter",
        "name":     "tkinter",
        "install":  None,   # stdlib — cannot be pip-installed
        "pip":      None,
        "desc":     "status window GUI",
        "required": False,  # server runs headlessly without it
    },
    {
        "import":       "pystray",
        "name":         "pystray",
        "install":      "pystray>=0.19.5",
        "pip":          True,
        "desc":         "system tray icon",
        "required":     False,
        "desktop_only": True,   # skip automatically in headless mode
    },
    {
        "import":       "PIL",
        "name":         "Pillow",
        "install":      "Pillow>=10.0.0",
        "pip":          True,
        "desc":         "image support (tray icon)",
        "required":     False,
        "desktop_only": True,   # skip automatically in headless mode
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

_SNMP_TOOL = "snmpget"


def _check_import(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def _pip_install(package_spec: str) -> "tuple[bool, str]":
    """Try pip install. Returns (success, error_snippet).

    Attempts the current interpreter's pip first, then --user flag on
    non-Windows (avoids permission errors when not running in a venv).
    """
    last_err = ""

    # Try 1: standard pip via current interpreter
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

    # Try 2: --user flag (avoids permission errors outside a venv on Linux/macOS)
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

    # Try 3: --break-system-packages (PEP 668 — Debian/Ubuntu 23.04+ with Python 3.12+)
    if sys.platform != "win32" and "externally-managed-environment" in last_err:
        try:
            r3 = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--break-system-packages", package_spec],
                capture_output=True, text=True,
            )
            if r3.returncode == 0:
                return True, ""
            last_err = (r3.stderr or r3.stdout or last_err).strip()
        except Exception:
            pass

    return False, last_err


def _check_snmpget() -> bool:
    import shutil
    return shutil.which("snmpget") is not None


def step1_packages():
    _separator()
    _tag("setup", f"{_C['bold']}Step 1 — Check Required Packages{_C['reset']}")
    _separator()
    print()

    # ── Verify pip is available before any pip install attempts ──────────────
    import shutil as _sh_pre, platform as _plat_pre
    _pip_ok = False
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "--version"],
                           capture_output=True, text=True)
        _pip_ok = r.returncode == 0
    except Exception:
        pass

    if _pip_ok:
        _tag("ok", "pip — Python package installer")
    else:
        _tag("warn", "pip is not installed or not functional.")
        _sys_pre = _plat_pre.system()
        if _sys_pre == "Linux":
            _mgr_pre = ("apt-get" if _sh_pre.which("apt-get") else
                        "dnf"     if _sh_pre.which("dnf")     else
                        "yum"     if _sh_pre.which("yum")     else None)
            if _mgr_pre:
                _tag("info", f"pip can be installed via {_mgr_pre}.")
                if _ask_yn("Install python3-pip now?", default=True):
                    _tag("info", f"Installing python3-pip via {_mgr_pre} ...")
                    r = subprocess.run(["sudo", _mgr_pre, "install", "-y", "python3-pip"],
                                       capture_output=False)
                    _pip_ok = r.returncode == 0
                    if _pip_ok:
                        _tag("ok", "pip installed successfully")
                    else:
                        _tag("error", "Could not install pip automatically.")
                        _tag("info",  f"Install manually: sudo {_mgr_pre} install python3-pip")
                        _tag("info",  "Then re-run setup.")
                        sys.exit(1)
                else:
                    _tag("error", "pip is required to install packages. Cannot continue without it.")
                    _tag("info",  f"Install manually: sudo {_mgr_pre} install python3-pip")
                    sys.exit(1)
            else:
                _tag("error", "No package manager found (apt-get/dnf/yum). Install pip manually.")
                sys.exit(1)
        elif _sys_pre == "Windows":
            _tag("info", "pip can be bootstrapped using Python's built-in ensurepip.")
            if _ask_yn("Run 'python -m ensurepip --upgrade' to install pip?", default=True):
                try:
                    r = subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"],
                                       capture_output=True, text=True)
                    _pip_ok = r.returncode == 0
                except Exception:
                    pass
                if _pip_ok:
                    _tag("ok", "pip bootstrapped successfully")
                else:
                    _tag("error", "pip is not available.")
                    _tag("info",  "Re-install Python from python.org (tick 'pip' during install),")
                    _tag("info",  "or run: python -m ensurepip --upgrade")
                    sys.exit(1)
            else:
                _tag("error", "pip is required to install packages. Cannot continue without it.")
                _tag("info",  "Re-install Python from python.org and ensure pip is included.")
                sys.exit(1)
        else:
            _tag("warn", "pip not found — package installs will likely fail. Continuing.")
    print()

    all_ok = True
    _headless = False   # set True when user opts out of desktop GUI
    for pkg in _PACKAGES:
        if _check_import(pkg["import"]):
            _tag("ok", f"{pkg['name']} — {pkg['desc']}")
            continue

        # Skip desktop-only packages silently if user chose headless mode
        if _headless and pkg.get("desktop_only"):
            _tag("info", f"Skipping '{pkg['name']}' — not needed in headless/server mode.")
            continue

        severity = "error" if pkg["required"] else "warn"
        _tag(severity, f"Package '{pkg['name']}' is not installed.")
        _tag("info",   f"This enables: {pkg['desc']}")

        if pkg["pip"] is None:
            # stdlib — cannot be pip-installed (tkinter)
            import platform as _plat
            _sys = _plat.system()
            if _sys == "Windows":
                _tag("error", "tkinter is part of the Python standard library but was not found.")
                _tag("info", "Re-install Python from python.org and tick 'tcl/tk and IDLE'.")
                _tag("warn", "The GUI status window will be unavailable without tkinter.")
                _tag("info", "PingWatch will still run and the web dashboard will be accessible.")
            else:
                # On Linux/macOS ask first — servers don't need a GUI at all
                _tag("info", "tkinter enables the native status window (desktop GUI).")
                _tag("info", "Server/headless deployments do not need it — the web")
                _tag("info", "dashboard is the primary interface.")
                print()
                _needs_gui = _ask_yn("Do you need the desktop GUI status window?", default=False)
                if not _needs_gui:
                    _headless = True           # also skip pystray / Pillow below
                    _state["headless"] = True  # persisted to DB → server skips GUI
                    _tag("ok", "Skipping tkinter — running in headless/server mode.")
                    _tag("info", "Access PingWatch via the web dashboard in your browser.")
                    continue
                # User wants GUI — offer to install
                if _sys == "Linux":
                    _tag("info", "Install with:")
                    _tag("info", "  Debian/Ubuntu: sudo apt-get install -y python3-tk")
                    _tag("info", "  RHEL/Fedora:   sudo dnf install python3-tkinter")
                    if _ask_yn("Try to install python3-tk via apt-get now?", default=True):
                        r = subprocess.run(["sudo", "apt-get", "install", "-y", "python3-tk"],
                                           capture_output=False)
                        if r.returncode == 0:
                            _tag("ok", "python3-tk installed — restart the wizard to confirm")
                        else:
                            _tag("warn", "Install failed — try manually, then re-run setup")
                elif _sys == "Darwin":
                    _tag("info", "Install with: brew install python-tk")
                    if _ask_yn("Try to install python-tk via brew now?", default=True):
                        try:
                            r = subprocess.run(["brew", "install", "python-tk"], capture_output=False)
                            if r.returncode == 0:
                                _tag("ok", "python-tk installed — restart the wizard to confirm")
                            else:
                                _tag("warn", "Install failed — try manually, then re-run setup")
                        except FileNotFoundError:
                            _tag("warn", "brew not found — install Homebrew first, then re-run setup")
                _tag("warn", "The GUI status window will be unavailable until tkinter is installed.")
                _tag("info", "PingWatch will still run and the web dashboard will be accessible.")
            continue

        install_now = _ask_yn(f"Install '{pkg['name']}' now?", default=True)
        if install_now:
            _tag("info", f"Installing {pkg['install']} ...")
            ok, err = _pip_install(pkg["install"])
            if ok:
                _tag("ok", f"{pkg['name']} installed successfully")
            else:
                import platform as _plat, shutil as _sh
                _sys = _plat.system()

                # Map pip package name → (apt pkg, optional extra note)
                _apt_map = {
                    "pystray":      ("python3-pystray", "also needs: sudo apt install python3-xlib"),
                    "Pillow":       ("python3-pil",     None),
                    "paramiko":     ("python3-paramiko", None),
                    "cryptography": ("python3-cryptography", None),
                    "ldap3":        ("python3-ldap3",   None),
                    "psutil":       ("python3-psutil",  None),
                }
                _apt_entry = _apt_map.get(pkg["name"])

                _sys_ok = False
                if _sys == "Linux" and _apt_entry:
                    err_lines = [l.strip() for l in err.splitlines() if l.strip()]
                    if err_lines:
                        _tag("info", f"  pip: {err_lines[-1]}")
                    _tag("info", "pip failed — system package manager may have a compatible version.")

                    _apt_pkg = _apt_entry[0]
                    _mgr = ("apt-get" if _sh.which("apt-get") else
                            "dnf"     if _sh.which("dnf")     else
                            "yum"     if _sh.which("yum")     else None)
                    if _mgr and _ask_yn(f"Try installing '{_apt_pkg}' via {_mgr}?", default=True):
                        if _mgr == "apt-get":
                            r = subprocess.run(
                                ["sudo", "apt-get", "install", "-y", _apt_pkg],
                                capture_output=False,
                            )
                            _sys_ok = r.returncode == 0
                        elif _mgr in ("dnf", "yum"):
                            _dnf_map = {
                                "python3-pystray":      "python3-pystray",
                                "python3-pil":          "python3-pillow",
                                "python3-paramiko":     "python3-paramiko",
                                "python3-cryptography": "python3-cryptography",
                                "python3-ldap3":        "python3-ldap3",
                                "python3-psutil":       "python3-psutil",
                            }
                            _dnf_pkg = _dnf_map.get(_apt_pkg, _apt_pkg)
                            r = subprocess.run(
                                ["sudo", _mgr, "install", "-y", _dnf_pkg],
                                capture_output=False,
                            )
                            _sys_ok = r.returncode == 0

                    if _sys_ok:
                        _tag("ok", f"{pkg['name']} installed via system package manager")
                    else:
                        _tag("error", f"Could not install '{pkg['name']}' automatically.")
                        _tag("info", "Install manually:")
                        _tag("info", f"  pip install {pkg['install']}  (requires pip: sudo apt install python3-pip)")
                        _tag("info", f"  or: sudo apt install {_apt_pkg}")
                        if _apt_entry[1]:
                            _tag("info", f"  note: {_apt_entry[1]}")
                        if pkg["required"]:
                            all_ok = False
                else:
                    # Non-Linux or unknown package — show pip error + hint
                    if err:
                        err_lines = [l.strip() for l in err.splitlines() if l.strip()]
                        if err_lines:
                            _tag("info", f"  pip: {err_lines[-1]}")
                    _tag("error", f"Could not install '{pkg['name']}' automatically.")
                    _tag("info", f"  pip install {pkg['install']}")
                    if pkg["required"]:
                        all_ok = False
        else:
            _tag("warn", f"Skipping {pkg['name']} — some features may be unavailable")

    # SNMP tool
    print()
    if _check_snmpget():
        _tag("ok", "net-snmp (snmpget) — SNMP sensor support")
    else:
        _tag("warn", "net-snmp (snmpget) is not installed.")
        _tag("info",  "This enables SNMP OID polling sensors.")
        install_snmp = _ask_yn("Install net-snmp now?", default=True)
        if install_snmp:
            import platform as _plat, shutil as _sh
            _sys = _plat.system()
            _ok_snmp = False
            if _sys == "Windows":
                try:
                    _tag("info", "Trying Chocolatey ...")
                    r = subprocess.run(["choco", "install", "net-snmp", "-y"], capture_output=True)
                    if r.returncode == 0:
                        _tag("ok", "net-snmp installed via Chocolatey")
                        _ok_snmp = True
                except FileNotFoundError:
                    pass  # choco not installed
                if not _ok_snmp:
                    try:
                        _tag("info", "Trying winget ...")
                        r2 = subprocess.run(["winget", "install", "net-snmp.net-snmp"], capture_output=True)
                        if r2.returncode == 0:
                            _tag("ok", "net-snmp installed via winget")
                            _ok_snmp = True
                    except FileNotFoundError:
                        pass  # winget not installed
            elif _sys == "Linux":
                if _sh.which("apt-get"):
                    r = subprocess.run(["sudo", "apt-get", "install", "-y", "snmp"], capture_output=True)
                    _ok_snmp = r.returncode == 0
                elif _sh.which("dnf"):
                    r = subprocess.run(["sudo", "dnf", "install", "-y", "net-snmp-utils"], capture_output=True)
                    _ok_snmp = r.returncode == 0
                elif _sh.which("yum"):
                    r = subprocess.run(["sudo", "yum", "install", "-y", "net-snmp-utils"], capture_output=True)
                    _ok_snmp = r.returncode == 0
                if _ok_snmp:
                    _tag("ok", "net-snmp installed")
            elif _sys == "Darwin":
                if _sh.which("brew"):
                    r = subprocess.run(["brew", "install", "net-snmp"], capture_output=True)
                    _ok_snmp = r.returncode == 0
                    if _ok_snmp:
                        _tag("ok", "net-snmp installed via Homebrew")
            if not _ok_snmp:
                _tag("warn", "Automatic install failed. Install manually:")
                _tag("info",  "Download: https://sourceforge.net/projects/net-snmp/files/net-snmp/")
                _tag("info",  "Windows: choco install net-snmp  OR  winget install net-snmp.net-snmp")
                _tag("info",  "Linux:   sudo apt install snmp  OR  sudo dnf install net-snmp-utils")
                _tag("info",  "macOS:   brew install net-snmp")
                print()
                _tag("info", "After installing, press Enter to check again.")
                _tag("info", "Or type 's' to skip (SNMP sensors will not be available).")
                while True:
                    raw = _ask("Press Enter to check, or type 's' to skip", "")
                    if raw.lower() == "s":
                        _tag("warn", "Skipping — SNMP polling sensors will not be available")
                        break
                    if _check_snmpget():
                        _tag("ok", "net-snmp (snmpget) detected — SNMP sensor support")
                        break
                    _tag("warn", "Still not found. Install it and press Enter again, or type 's' to skip.")
        else:
            _tag("warn", "Skipping — SNMP polling sensors will not be available")

    # ── ping binary (ICMP sensors) ────────────────────────────────────────────
    print()
    import shutil as _sh_ic, platform as _plat_ic
    if _sh_ic.which("ping"):
        _tag("ok", "ping — ICMP ping sensor support")
    else:
        _tag("warn", "ping binary not found.")
        _tag("info",  "Required for ICMP ping sensors (most common sensor type).")
        _sys_ic = _plat_ic.system()
        _ok_ping = False
        if _sys_ic == "Linux":
            _mgr_ic = ("apt-get" if _sh_ic.which("apt-get") else
                       "dnf"     if _sh_ic.which("dnf")     else
                       "yum"     if _sh_ic.which("yum")     else None)
            if _mgr_ic and _ask_yn("Install ping (iputils-ping) now?", default=True):
                _pkg_ic = "iputils-ping" if _mgr_ic == "apt-get" else "iputils"
                r = subprocess.run(["sudo", _mgr_ic, "install", "-y", _pkg_ic],
                                   capture_output=False)
                if r.returncode == 0:
                    _tag("ok", "ping installed")
                    _ok_ping = True
        if not _ok_ping:
            if _sys_ic == "Windows":
                _tag("info", "ping.exe should be present on all Windows installations.")
                _tag("info", "If missing, check your Windows installation or repair Windows.")
            elif _sys_ic == "Linux":
                _tag("warn", "Install manually:")
                _tag("info",  "Debian/Ubuntu: sudo apt install iputils-ping")
                _tag("info",  "RHEL/Fedora:   sudo dnf install iputils")
            elif _sys_ic == "Darwin":
                _tag("info", "Install with: brew install inetutils")
            print()
            _tag("info", "After installing, press Enter to check again.")
            _tag("info", "Or type 's' to skip (ICMP ping sensors will not work).")
            while True:
                raw = _ask("Press Enter to check, or type 's' to skip", "")
                if raw.lower() == "s":
                    _tag("warn", "Skipping — ICMP ping sensors will not work")
                    break
                if _sh_ic.which("ping"):
                    _tag("ok", "ping detected — ICMP ping sensor support")
                    break
                _tag("warn", "Still not found. Install it and press Enter again, or type 's' to skip.")

    print()
    if not all_ok:
        _tag("error", "One or more required packages could not be installed.")
        _tag("info",  f"Fix the issues above and run '{_launcher_hint()}' again.")
        sys.exit(1)


# ── File ownership fix (sudo root → real user) ───────────────────────────────

def _fix_file_ownership():
    """When the wizard runs as root via sudo, chown DB and cert files back to
    the invoking user so the service (which runs as that user) can write them."""
    if sys.platform == "win32" or os.geteuid() != 0:
        return
    sudo_user = os.environ.get("SUDO_USER", "")
    if not sudo_user:
        return
    try:
        import pwd as _pwd
        pw = _pwd.getpwnam(sudo_user)
        uid, gid = pw.pw_uid, pw.pw_gid
    except Exception:
        return
    targets = [
        str(DB_PATH),
        str(DB_PATH) + "-wal",
        str(DB_PATH) + "-shm",
        str(DB_PATH) + ".pre_migrate.bak",
        str(DB_PATH) + ".pending_import",
        str(LOGS_DB_PATH),
        str(LOGS_DB_PATH) + "-wal",
        str(LOGS_DB_PATH) + "-shm",
        str(LOGS_DB_PATH) + ".pending_logs_import",
        str(CERTS_DIR),
        os.path.join(_BASE, "pingwatch.conf"),
    ]
    # Also chown any cert files inside CERTS_DIR
    try:
        for _f in os.listdir(str(CERTS_DIR)):
            targets.append(os.path.join(str(CERTS_DIR), _f))
    except Exception:
        pass
    # Chown the logs directory and all files inside it
    _logs_dir = os.path.join(_BASE, "logs")
    if os.path.isdir(_logs_dir):
        targets.append(_logs_dir)
        try:
            for _f in os.listdir(_logs_dir):
                targets.append(os.path.join(_logs_dir, _f))
        except Exception:
            pass
    changed = 0
    for path in targets:
        if os.path.exists(path):
            try:
                os.chown(path, uid, gid)
                changed += 1
            except Exception:
                pass
    if changed:
        _tag("ok", f"File ownership set to '{sudo_user}' ({changed} item(s))")


# ── Service management (Linux/systemd) ───────────────────────────────────────

def _systemctl(*args) -> list:
    """Return a systemctl command list, omitting sudo when already root."""
    prefix = [] if (sys.platform != "win32" and os.getuid() == 0) else ["sudo"]
    return prefix + ["systemctl"] + list(args)


def _is_service_active() -> bool:
    """Return True if the pingwatch systemd service is currently running."""
    if sys.platform == "win32":
        return False
    import shutil as _sh
    if not _sh.which("systemctl"):
        return False
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "pingwatch"],
            capture_output=True, text=True,
        )
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _stop_service() -> bool:
    """Stop the pingwatch systemd service. Returns True on success."""
    try:
        r = subprocess.run(_systemctl("stop", "pingwatch"),
                           capture_output=True, text=True)
        return r.returncode == 0
    except Exception:
        return False


def _restart_service() -> bool:
    """Start the pingwatch systemd service. Returns True on success."""
    try:
        r = subprocess.run(_systemctl("start", "pingwatch"),
                           capture_output=True, text=True)
        return r.returncode == 0
    except Exception:
        return False


# ── Port helpers ──────────────────────────────────────────────────────────────

def _port_in_use(port: int) -> "int | None":
    """Return a truthy value if the port is in use, None if free.
    Uses a pure-Python socket bind test — works on all platforms without
    PowerShell, lsof, or ss.  Returns 1 (dummy PID) when the port is busy
    so callers can distinguish busy vs free while keeping the same API."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', port))
            return None   # bind succeeded → port is free
        except PermissionError:
            # Cannot bind privileged port (<1024) without root — port may be free.
            # Try an outbound connect to see if something is actually listening.
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as cs:
                    cs.settimeout(0.5)
                    cs.connect(('127.0.0.1', port))
                return 1   # something answered → port is occupied
            except OSError:
                return None   # nothing answered → port is free
        except OSError:
            return 1      # address already in use → port is occupied


def _check_webserver_on_port(port: int) -> "str | None":
    """Return the name of a web server occupying the port, or None.
    Only checked on Linux when a process IS listening (helps surface
    Apache2/nginx conflicts that would otherwise silently block PingWatch)."""
    if sys.platform == "win32":
        return None
    try:
        import shutil as _sh
        # ss -tlnp is fastest; fall back to lsof
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


def _pid_name(pid: int) -> str:
    """Best-effort process name for the given PID (or 'unknown')."""
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Process -Id {pid} -EA SilentlyContinue).Name"],
                capture_output=True, text=True,
            )
            return r.stdout.strip() or "unknown"
        else:
            # /proc/PID/comm on Linux; ps on macOS
            import shutil as _sh
            if _sh.which("ps"):
                r = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                                   capture_output=True, text=True)
                return r.stdout.strip() or "unknown"
    except Exception:
        pass
    return "unknown"


def _kill_pid(pid: int) -> bool:
    """Attempt to kill the given PID. Cross-platform."""
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


def _ask_port(label: str, default: int, protocol: str = "TCP") -> int:
    """Interactive port selection with conflict detection. Returns chosen port."""
    while True:
        raw = _ask(f"{label}", str(default))
        try:
            port = int(raw)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            _tag("error", "Port must be a number between 1 and 65535. Try again.")
            continue

        pid = _port_in_use(port) if protocol == "TCP" else None
        if pid is None:
            return port

        svc = _check_webserver_on_port(port)
        if svc:
            _tag("warn", f"Port {port} is occupied by '{svc}' (a web server).")
            _tag("info", f"PingWatch cannot share port {port} with {svc}.")
            _tag("info", f"Either stop {svc} first:")
            _tag("info", f"  sudo systemctl stop {svc}")
            _tag("info", f"  sudo systemctl disable {svc}   (prevent it restarting)")
            _tag("info", f"or choose a different port for PingWatch (e.g. 8443 for HTTPS).")
            _tag("info", f"Note: if you remove {svc} later with 'apt remove {svc}',")
            _tag("info",  "  run 'apt autoremove' carefully — it should NOT affect PingWatch.")
        else:
            _tag("warn", f"Port {port} is already in use by another process.")
        print()
        print("       Options:")
        print(f"         [1] Try to free port {port} and use it")
        print(f"         [2] Enter a different port")
        print(f"         [3] Keep port {port} anyway (may fail at startup)")
        print()
        choice = _ask("Choose", "2")
        if choice == "1":
            ok = False
            if sys.platform == "win32":
                # On Windows we still have PowerShell available for kill
                try:
                    r = subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         f"(Get-NetTCPConnection -LocalPort {port} -State Listen -EA SilentlyContinue)"
                         f".OwningProcess | ForEach-Object {{ Stop-Process -Id $_ -Force -EA SilentlyContinue }}"],
                        capture_output=True,
                    )
                    ok = r.returncode == 0
                except Exception:
                    ok = False
            else:
                # On Unix use lsof/fuser if available
                import shutil as _sh
                if _sh.which("fuser"):
                    r = subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)
                    ok = r.returncode == 0
                elif _sh.which("lsof"):
                    r = subprocess.run(["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True)
                    for _pid in r.stdout.strip().splitlines():
                        try: os.kill(int(_pid), 15)
                        except Exception: pass
                    ok = True
            if ok:
                _tag("ok", f"Process on port {port} signalled — port {port} may now be free")
                return port
            else:
                _tag("error", f"Could not free port {port}. Try option 2.")
        elif choice == "3":
            _tag("warn", f"Keeping port {port} — may fail if the process is still running")
            return port
        # choice == "2" or invalid → loop back and ask for port again
        _tag("info", "Enter a different port:")


def _generate_pg_password(length: int = 20) -> str:
    """Generate a random alphanumeric password."""
    import random, string
    chars = string.ascii_letters + string.digits
    return "".join(random.SystemRandom().choices(chars, k=length))


def _detect_pg_server() -> "tuple[bool, str]":
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
    # Windows: psql is rarely in PATH — scan default install directories
    if sys.platform == "win32":
        import glob as _glob
        _candidates = _glob.glob(
            r"C:\Program Files\PostgreSQL\*\bin\psql.exe"
        )
        if _candidates:
            _psql_exe = sorted(_candidates)[-1]  # highest version
            try:
                r = subprocess.run([_psql_exe, "--version"],
                                   capture_output=True, text=True, timeout=5)
                ver = (r.stdout or r.stderr or "").strip().splitlines()[0]
                return True, ver
            except Exception:
                return True, _psql_exe
    return False, ""


def _pg_install_instructions() -> str:
    """Return distro-specific install instructions for PostgreSQL."""
    import platform as _plat, shutil as _sh
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
        # Generic: try to detect package manager
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


def step2_database():
    """Step 2 — Choose and configure the database backend."""
    _separator()
    _tag("setup", f"{_C['bold']}Step 2 — Database Backend{_C['reset']}")
    _separator()
    print()
    _tag("info", "Choose where PingWatch stores its data:")
    print()
    print("         [1] SQLite  — Zero configuration. Data stored locally.")
    print("                       Best for most single-server deployments.")
    print()
    print("         [2] PostgreSQL — External database server.")
    print("                       Best for production environments.")
    print()

    # Pre-select based on existing config (re-run mode)
    _default_choice = "2" if _state["db_backend"] == "postgresql" else "1"
    choice = _ask("Choose", _default_choice)

    if choice != "2":
        # ── SQLite ─────────────────────────────────────────────────────────────
        from db.backend import save_config, load_config
        save_config({"db_backend": "sqlite"})
        load_config()
        _state["db_backend"] = "sqlite"
        _tag("ok", "SQLite selected — no additional configuration needed.")
        print()
        return

    # ── PostgreSQL ─────────────────────────────────────────────────────────────
    _tag("info", "PostgreSQL selected.")
    print()
    import shutil as _sh
    _is_root = sys.platform != "win32" and os.getuid() == 0
    _sctl = ["systemctl"] if _is_root else ["sudo", "systemctl"]

    # 2a. Install psycopg2-binary ───────────────────────────────────────────────
    try:
        import psycopg2  # noqa: F401
        _tag("ok", "psycopg2 — Python PostgreSQL driver")
    except ImportError:
        _tag("warn", "psycopg2 is not installed (required for PostgreSQL).")
        if _ask_yn("Install psycopg2-binary now?", default=True):
            _tag("info", "Installing psycopg2-binary ...")
            ok, err = _pip_install("psycopg2-binary>=2.9.9")
            if ok:
                _tag("ok", "psycopg2-binary installed successfully")
            else:
                # Try system package as fallback
                import shutil as _sh
                _sys_ok = False
                if sys.platform != "win32" and _sh.which("apt-get"):
                    _tag("info", "pip failed — system package python3-psycopg2 may work.")
                    if _ask_yn("Try installing python3-psycopg2 via apt-get?", default=True):
                        r = subprocess.run(
                            ["sudo", "apt-get", "install", "-y", "python3-psycopg2"],
                            capture_output=False,
                        )
                        _sys_ok = r.returncode == 0
                if _sys_ok:
                    _tag("ok", "psycopg2 installed via system package manager")
                else:
                    _tag("error", "Could not install psycopg2-binary automatically.")
                    _tag("info", "Install manually:")
                    _tag("info", "  pip install psycopg2-binary")
                    _tag("info", "  or: sudo apt install python3-psycopg2")
                    if not _ask_yn("Continue anyway?", default=False):
                        _tag("info", "Switching to SQLite.")
                        from db.backend import save_config, load_config
                        save_config({"db_backend": "sqlite"})
                        load_config()
                        _state["db_backend"] = "sqlite"
                        return
        else:
            _tag("warn", "Skipping — PostgreSQL backend requires psycopg2.")
            _tag("info", "Switching to SQLite.")
            from db.backend import save_config, load_config
            save_config({"db_backend": "sqlite"})
            load_config()
            _state["db_backend"] = "sqlite"
            return
    print()

    # 2b. Check PostgreSQL server installed ─────────────────────────────────────
    _separator("·")
    _tag("info", "Checking for PostgreSQL server on this host...")
    print()
    _pg_installed, _pg_ver = _detect_pg_server()

    if _pg_installed:
        _tag("ok", f"PostgreSQL detected: {_pg_ver}")
    else:
        _tag("warn", "PostgreSQL server not found on this host.")
        print()
        _instructions = _pg_install_instructions()
        _tag("info", "Install PostgreSQL with:")
        _tag("info", f"  {_C['cyan']}{_instructions}{_C['reset']}")
        print()

        # Offer automatic install on Linux where we know the exact command
        _auto_installed = False
        if sys.platform != "win32" and _instructions.startswith("sudo apt"):
            if _ask_yn("Install PostgreSQL automatically now?", default=True):
                _tag("info", "Installing PostgreSQL (this may take a minute) ...")
                _mgr = "apt-get" if _sh.which("apt-get") else "apt"
                _apt_cmd = [_mgr] if _is_root else ["sudo", _mgr]
                r = subprocess.run(
                    _apt_cmd + ["install", "-y", "postgresql", "postgresql-contrib"],
                    capture_output=False,
                )
                if r.returncode == 0:
                    # Ensure service is running
                    subprocess.run(_sctl + ["start", "postgresql"],
                                   capture_output=True)
                    _pg_installed, _pg_ver = _detect_pg_server()
                    if _pg_installed:
                        _tag("ok", f"PostgreSQL installed and detected: {_pg_ver}")
                        _auto_installed = True
                    else:
                        _tag("warn", "Installed but still not detected — continuing anyway.")
                        _auto_installed = True
                else:
                    _tag("warn", "Automatic install failed — install manually and press Enter.")

        if not _auto_installed and not _pg_installed:
            _tag("info", "After installing, press Enter to check again.")
            _tag("info", "Or choose [s] to skip (useful if PostgreSQL runs on another host).")
            print()
            while True:
                raw = _ask("Press Enter to check, or type 's' to skip", "")
                if raw.lower() == "s":
                    _tag("info", "Skipping server check — continuing with connection details.")
                    break
                _pg_installed, _pg_ver = _detect_pg_server()
                if _pg_installed:
                    _tag("ok", f"PostgreSQL detected: {_pg_ver}")
                    break
                else:
                    _tag("warn", "Still not found. Install it and press Enter again, or type 's' to skip.")
    print()

    # 2c. Create database and user ───────────────────────────────────────────────
    _separator("·")
    _tag("info", "Create a PostgreSQL database and user for PingWatch.")
    print()
    _gen_pw = _generate_pg_password()

    # ── Check / start PostgreSQL service before attempting auto-create ─────────
    if sys.platform != "win32" and _pg_installed:
        _svc_running = False
        try:
            r = subprocess.run(_sctl + ["is-active", "postgresql"],
                               capture_output=True, text=True)
            _svc_running = r.stdout.strip() == "active"
        except Exception:
            pass
        if not _svc_running:
            # Try pg_isready as a lighter check (works without systemd)
            _pgready = _sh.which("pg_isready")
            if _pgready:
                try:
                    r = subprocess.run([_pgready], capture_output=True, text=True)
                    _svc_running = r.returncode == 0
                except Exception:
                    pass
        if not _svc_running:
            _tag("warn", "PostgreSQL service does not appear to be running.")
            if _ask_yn("Try to start the PostgreSQL service now?", default=True):
                _tag("info", "Starting PostgreSQL ...")
                r = subprocess.run(_sctl + ["start", "postgresql"],
                                   capture_output=True, text=True)
                if r.returncode == 0:
                    _tag("ok", "PostgreSQL service started.")
                else:
                    _err_svc = (r.stderr or r.stdout or "").strip()
                    if _err_svc:
                        _tag("warn", f"Start failed: {_err_svc}")
                    else:
                        _tag("warn", "Could not start PostgreSQL service.")
                    _pfx = "" if _is_root else "sudo "
                    _tag("info", f"Try manually: {_pfx}systemctl start postgresql")
                    _tag("info", f"Then check:   {_pfx}systemctl status postgresql")

    # ── Verify postgres OS user exists (server installed, not just client tools) ─
    if sys.platform != "win32" and _pg_installed:
        try:
            _id_r = subprocess.run(["id", "postgres"], capture_output=True, text=True)
            _pg_user_exists = _id_r.returncode == 0
        except Exception:
            _pg_user_exists = True  # assume OK if 'id' not available
        if not _pg_user_exists:
            _tag("warn", "The 'postgres' system user does not exist.")
            _tag("warn", "The PostgreSQL server package is not installed (only client tools were found).")
            _inst = _pg_install_instructions()
            _tag("info", f"Install the server with: {_inst}")
            print()
            _pg_installed = False  # skip auto-create — server not ready
            # Offer automatic install on apt-based systems (same as "not found" path)
            _mgr2 = "apt-get" if _sh.which("apt-get") else ("apt" if _sh.which("apt") else None)
            if _mgr2:
                if _ask_yn("Install PostgreSQL server automatically now?", default=True):
                    _tag("info", "Installing PostgreSQL (this may take a minute) ...")
                    _apt_cmd2 = [_mgr2] if _is_root else ["sudo", _mgr2]
                    r = subprocess.run(
                        _apt_cmd2 + ["install", "-y", "postgresql", "postgresql-contrib"],
                        capture_output=False,
                    )
                    if r.returncode == 0:
                        subprocess.run(_sctl + ["start", "postgresql"], capture_output=True)
                        try:
                            _id_r2 = subprocess.run(["id", "postgres"],
                                                     capture_output=True, text=True)
                            if _id_r2.returncode == 0:
                                _tag("ok", "PostgreSQL server installed successfully.")
                                _pg_installed = True
                            else:
                                _tag("warn", "Installed but postgres user still missing — check service.")
                        except Exception:
                            _pg_installed = True  # best-effort
                    else:
                        _tag("warn", "Automatic install failed — install manually then re-run the wizard.")

    # Offer to create the DB/user automatically
    _db_auto_ok = False
    _pw = _gen_pw

    # Find psql executable (may not be in PATH on Windows)
    def _find_psql():
        import shutil as _sh2, glob as _glob2
        _p = _sh2.which("psql")
        if _p:
            return _p
        if sys.platform == "win32":
            _cands = sorted(_glob2.glob(r"C:\Program Files\PostgreSQL\*\bin\psql.exe"))
            if _cands:
                return _cands[-1]
        return None

    if _pg_installed:
        print(_C["bold"] + "       The wizard can create the database and user automatically." + _C["reset"])
        if sys.platform == "win32":
            print(_C["bold"] + "       It will connect as the 'postgres' superuser." + _C["reset"])
        else:
            _access = "as root" if _is_root else "requires sudo / postgres access"
            print(_C["bold"] + f"       It will run ({_access}):" + _C["reset"])
        print()
        print(_C["cyan"] + f"         CREATE USER pingwatch WITH PASSWORD '****';" + _C["reset"])
        print(_C["cyan"] +  "         CREATE DATABASE pingwatch OWNER pingwatch;" + _C["reset"])
        print()
        if _ask_yn("Create database and user automatically?", default=True):
            _psql = _find_psql()
            if not _psql:
                _tag("warn", "psql not found — cannot run automatically.")
            elif sys.platform == "win32":
                # Windows: connect as postgres superuser (needs its password)
                _pg_sa_pw = _ask_password("Password for the PostgreSQL 'postgres' superuser", "")
                if not _pg_sa_pw:
                    _tag("warn", "No password entered — skipping auto-create.")
                else:
                    _tag("info", "Creating PostgreSQL user and database ...")
                    _cmds = [
                        f"CREATE USER pingwatch WITH PASSWORD '{_gen_pw}';",
                        "CREATE DATABASE pingwatch OWNER pingwatch;",
                    ]
                    _all_ok = True
                    _env = {**os.environ, "PGPASSWORD": _pg_sa_pw}
                    for _sql in _cmds:
                        r = subprocess.run(
                            [_psql, "-U", "postgres", "-h", "localhost", "-c", _sql],
                            capture_output=True, text=True, env=_env,
                        )
                        if r.returncode != 0:
                            _err_out = (r.stderr or r.stdout or "").strip()
                            if "already exists" in _err_out.lower():
                                _tag("ok", f"Already exists (skipping): {_sql.split()[2]}")
                            else:
                                _tag("warn", f"Command failed: {_err_out}")
                                _all_ok = False
                        else:
                            _tag("ok", _sql.split(";")[0])
                    if _all_ok:
                        _tag("ok", "Database and user created successfully.")
                        _pw = _gen_pw
                        _db_auto_ok = True
                    else:
                        _tag("warn", "Some commands failed. You can create them manually.")
            else:
                _tag("info", "Creating PostgreSQL user and database ...")
                _cmds = [
                    f"CREATE USER pingwatch WITH PASSWORD '{_gen_pw}';",
                    "CREATE DATABASE pingwatch OWNER pingwatch;",
                ]
                _all_ok = True
                for _sql in _cmds:
                    if _is_root:
                        _pg_cmd = ["su", "-", "postgres", "-c", f'{_psql} -c "{_sql}"']
                    else:
                        _pg_cmd = ["sudo", "-u", "postgres", _psql, "-c", _sql]
                    r = subprocess.run(_pg_cmd, capture_output=True, text=True)
                    if r.returncode != 0:
                        _err_out = (r.stderr or r.stdout or "").strip()
                        if "already exists" in _err_out.lower():
                            _tag("ok", f"Already exists (skipping): {_sql.split()[2]}")
                        else:
                            _tag("warn", f"Command failed: {_err_out}")
                            _all_ok = False
                    else:
                        _tag("ok", _sql.split(";")[0])
                if _all_ok:
                    _tag("ok", "Database and user created successfully.")
                    _pw = _gen_pw
                    _db_auto_ok = True
                else:
                    _tag("warn", "Some commands failed. You can create them manually:")
            print()

    if not _db_auto_ok:
        # Fall back to showing manual instructions
        print(_C["bold"] + "       Run these commands in a terminal:" + _C["reset"])
        print()
        if sys.platform == "win32":
            _psql_path = _find_psql() or r'"C:\Program Files\PostgreSQL\<version>\bin\psql.exe"'
            print(_C["cyan"] + f'         {_psql_path} -U postgres' + _C["reset"])
        elif _is_root:
            print(_C["cyan"] + "         su - postgres" + _C["reset"])
        else:
            print(_C["cyan"] + "         sudo -u postgres psql" + _C["reset"])
        print(_C["cyan"] + f"         CREATE USER pingwatch WITH PASSWORD '{_gen_pw}';" + _C["reset"])
        print(_C["cyan"] +  "         CREATE DATABASE pingwatch OWNER pingwatch;" + _C["reset"])
        print(_C["cyan"] +  "         \\q" + _C["reset"])
        print()
        _tag("info", "Copy the password above or enter a custom one below.")
        _pw = _ask_password("Password for the 'pingwatch' user", _gen_pw)
        print()

    # 2d. Connection details ─────────────────────────────────────────────────────
    _separator("·")
    _tag("info", "Connection details (press Enter to accept defaults):")
    print()
    _host = _ask("PostgreSQL host", _state["pg_host"])
    _port_raw = _ask("PostgreSQL port", str(_state["pg_port"]))
    try:
        _port = int(_port_raw)
    except ValueError:
        _port = 5432
    _dbname = _ask("Database name", _state["pg_database"])
    _user   = _ask("Username",      _state["pg_user"])
    _password = _pw
    print()

    # 2e. Test connection loop ───────────────────────────────────────────────────
    _separator("·")
    _tag("info", "Testing connection...")
    print()
    _conn_ok = False
    while True:
        from db.pg_pool import pg_test_connection
        _ok, _err = pg_test_connection(_host, _port, _dbname, _user, _password)
        if _ok:
            _tag("ok", f"Connected to PostgreSQL at {_host}:{_port}/{_dbname}")
            _conn_ok = True
            break
        _tag("error", f"Connection failed: {_err}")
        print()
        print("         Options:")
        print("           [1] Edit connection details and try again")
        print("           [2] Continue anyway (skip validation)")
        print("           [3] Switch to SQLite instead")
        if sys.platform != "win32":
            print("           [4] Start PostgreSQL service and retry")
        print()
        _opt = _ask("Choose", "1")
        if _opt == "2":
            _tag("warn", "Proceeding without a confirmed connection.")
            _conn_ok = False
            break
        if _opt == "3":
            _tag("info", "Switching to SQLite.")
            from db.backend import save_config, load_config
            save_config({"db_backend": "sqlite"})
            load_config()
            _state["db_backend"] = "sqlite"
            return
        if _opt == "4" and sys.platform != "win32":
            _tag("info", "Starting PostgreSQL service ...")
            r = subprocess.run(_sctl + ["start", "postgresql"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                _tag("ok", "Service started — retrying connection ...")
            else:
                _err_svc = (r.stderr or r.stdout or "").strip()
                _tag("warn", f"Could not start service: {_err_svc}" if _err_svc else "Could not start service.")
                _pfx = "" if _is_root else "sudo "
                _tag("info", f"Check: {_pfx}systemctl status postgresql")
            continue
        # re-ask details
        _host     = _ask("PostgreSQL host", _host)
        _port_raw = _ask("PostgreSQL port", str(_port))
        try:
            _port = int(_port_raw)
        except ValueError:
            _port = 5432
        _dbname   = _ask("Database name", _dbname)
        _user     = _ask("Username",      _user)
        _password = _ask_password("Password", _password)
        print()
        _tag("info", "Retrying connection...")
        print()
    print()

    # ── Persist backend settings ────────────────────────────────────────────────
    _state["db_backend"]  = "postgresql"
    _state["pg_host"]     = _host
    _state["pg_port"]     = _port
    _state["pg_database"] = _dbname
    _state["pg_user"]     = _user
    _state["pg_password"] = _password

    from db.backend import save_config, load_config
    save_config({
        "db_backend":  "postgresql",
        "pg_host":     _host,
        "pg_port":     _port,
        "pg_database": _dbname,
        "pg_user":     _user,
        "pg_password": _password,
    })
    load_config()

    # ── Init PG pool + schemas ──────────────────────────────────────────────────
    if _conn_ok:
        try:
            from db.pg_pool import pg_init_pool
            pg_init_pool()
            from db.core import db_init
            db_init()
            _tag("ok", "PostgreSQL schemas created.")
        except Exception as _e:
            _tag("error", f"PostgreSQL init failed: {_e}")
            _tag("info", "You can retry later or use Settings → Database to migrate.")

    # 2f. PostgreSQL client tools (psql / pg_dump) ─────────────────────────────
    import shutil as _sh2, platform as _plat2
    _has_psql    = _sh2.which("psql")    is not None
    _has_pg_dump = _sh2.which("pg_dump") is not None
    _separator("·")
    if _has_psql and _has_pg_dump:
        _tag("ok", "PostgreSQL client tools (psql, pg_dump) — DB export/import support")
    else:
        _missing = ", ".join(x for x, ok in [("psql", _has_psql), ("pg_dump", _has_pg_dump)] if not ok)
        _tag("warn", f"PostgreSQL client tools ({_missing}) are not installed.")
        _tag("info",  "Required for database export and import.")
        _sys2 = _plat2.system()
        _ok_pg = False

        # On Windows, check if PG is already installed but just not in PATH
        _pg_bin_dir = None
        if _sys2 == "Windows":
            import glob as _gl2
            _bins = _gl2.glob(r"C:\Program Files\PostgreSQL\*\bin")
            if _bins:
                _pg_bin_dir = sorted(_bins)[-1]  # highest version
                _psql_found = os.path.isfile(os.path.join(_pg_bin_dir, "psql.exe"))
                _pgdump_found = os.path.isfile(os.path.join(_pg_bin_dir, "pg_dump.exe"))
                if _psql_found and _pgdump_found:
                    _tag("ok", f"Found client tools in: {_pg_bin_dir}")
                    _tag("warn", "They are not in your system PATH.")
                    _tag("info", f"Add this to your PATH environment variable:")
                    _tag("info", f"  {_pg_bin_dir}")
                    _tag("info", "Or run in PowerShell (as Administrator) to add permanently:")
                    _tag("info", f'  [Environment]::SetEnvironmentVariable("Path", $env:Path + ";{_pg_bin_dir}", "Machine")')
                    # Add to current process PATH so rescan works
                    os.environ["PATH"] = _pg_bin_dir + os.pathsep + os.environ.get("PATH", "")
                    _ok_pg = True
                    _tag("ok", "Added to PATH for this session.")

        if not _ok_pg and _ask_yn("Install PostgreSQL client tools now?", default=True):
            if _sys2 == "Windows":
                try:
                    _tag("info", "Trying Chocolatey ...")
                    r = subprocess.run(["choco", "install", "postgresql", "-y"], capture_output=True)
                    if r.returncode == 0:
                        _tag("ok", "PostgreSQL client tools installed via Chocolatey")
                        _ok_pg = True
                except FileNotFoundError:
                    pass  # choco not installed
                if not _ok_pg:
                    try:
                        _tag("info", "Trying winget ...")
                        r2 = subprocess.run(["winget", "install", "PostgreSQL.PostgreSQL"], capture_output=True)
                        if r2.returncode == 0:
                            _tag("ok", "PostgreSQL client tools installed via winget")
                            _ok_pg = True
                    except FileNotFoundError:
                        pass  # winget not installed
            elif _sys2 == "Linux":
                if _sh2.which("apt-get"):
                    r = subprocess.run(["sudo", "apt-get", "install", "-y", "postgresql-client"], capture_output=False)
                    _ok_pg = r.returncode == 0
                elif _sh2.which("dnf"):
                    r = subprocess.run(["sudo", "dnf", "install", "-y", "postgresql"], capture_output=False)
                    _ok_pg = r.returncode == 0
                elif _sh2.which("yum"):
                    r = subprocess.run(["sudo", "yum", "install", "-y", "postgresql"], capture_output=False)
                    _ok_pg = r.returncode == 0
                if _ok_pg:
                    _tag("ok", "PostgreSQL client tools installed")
            elif _sys2 == "Darwin":
                if _sh2.which("brew"):
                    r = subprocess.run(["brew", "install", "libpq"], capture_output=True)
                    _ok_pg = r.returncode == 0
                    if _ok_pg:
                        _tag("ok", "PostgreSQL client tools installed via Homebrew")
                        _tag("info", "Run: brew link --force libpq  (to add psql/pg_dump to PATH)")
            if not _ok_pg:
                _tag("warn", "Automatic install failed. Install manually:")
                if _sys2 == "Windows":
                    _tag("info", "psql and pg_dump come with the PostgreSQL server installer.")
                    _tag("info", "If PG is on another machine, install it locally and select")
                    _tag("info", "'Command Line Tools' only during setup:")
                    _tag("info", "  https://www.enterprisedb.com/downloads/postgres-postgresql-downloads")
                    _tag("info", "After install, add the bin folder to PATH:")
                    _tag("info", r"  C:\Program Files\PostgreSQL\<version>\bin")
                else:
                    _tag("info", "Linux: sudo apt install postgresql-client  OR  sudo dnf install postgresql")
                    _tag("info", "macOS: brew install libpq && brew link --force libpq")
                print()
                _tag("info", "After installing, press Enter to check again.")
                _tag("info", "Or type 's' to skip (DB export/import will not be available).")
                while True:
                    raw = _ask("Press Enter to check, or type 's' to skip", "")
                    if raw.lower() == "s":
                        _tag("warn", "Skipping — DB export/import will not be available")
                        break
                    # Also re-check the PG bin directory on Windows
                    if _sys2 == "Windows":
                        _bins2 = _gl2.glob(r"C:\Program Files\PostgreSQL\*\bin")
                        if _bins2:
                            _d = sorted(_bins2)[-1]
                            os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
                    _has_psql    = _sh2.which("psql")    is not None
                    _has_pg_dump = _sh2.which("pg_dump") is not None
                    if _has_psql and _has_pg_dump:
                        _tag("ok", "PostgreSQL client tools (psql, pg_dump) detected")
                        break
                    _still = ", ".join(x for x, ok in [("psql", _has_psql), ("pg_dump", _has_pg_dump)] if not ok)
                    _tag("warn", f"Still missing: {_still}. Install and press Enter again, or type 's' to skip.")
        else:
            _tag("warn", "Skipping — DB export/import will not be available")
    print()

    # 2g. Migration offer (existing SQLite data) ────────────────────────────────
    if os.path.isfile(DB_PATH) and _conn_ok:
        _sz = os.path.getsize(DB_PATH)
        _sz_str = f"{_sz / 1048576:.1f} MB" if _sz >= 1048576 else f"{_sz // 1024} KB"
        print()
        _separator("·")
        _tag("info", f"Existing SQLite database detected ({_sz_str}).")
        _tag("info", "Migrate all data (devices, sensors, settings, history) to PostgreSQL?")
        print()
        if _ask_yn("Migrate existing data to PostgreSQL?", default=True):
            _tag("info", "Migrating data — this may take a minute for large databases...")
            print()
            try:
                from db.pg_migrate import migrate_sqlite_to_pg
                def _progress(table, done, total):
                    pct = int(done / total * 100) if total else 100
                    print(f"\r         {table:<35} {pct:3d}%", end="", flush=True)
                _ok_mig, _msg_mig = migrate_sqlite_to_pg(
                    str(DB_PATH), str(LOGS_DB_PATH),
                    {"pg_host": _host, "pg_port": _port, "pg_database": _dbname,
                     "pg_user": _user, "pg_password": _password},
                    progress_cb=_progress,
                )
                print()  # newline after progress
                if _ok_mig:
                    _tag("ok", f"Migration complete: {_msg_mig}")
                    _tag("info", "SQLite files kept as backup. You can delete them once you've")
                    _tag("info", "verified all data is present in PostgreSQL.")
                else:
                    _tag("warn", f"Migration finished with issues: {_msg_mig}")
                    _tag("info", "You can retry from Settings → Database after startup.")
            except Exception as _me:
                print()
                _tag("error", f"Migration error: {_me}")
                _tag("info", "You can retry from Settings → Database after startup.")
        else:
            _tag("info", "Skipping migration. Your SQLite data remains untouched.")
            _tag("info", "Migrate later from Settings → Database if needed.")
    print()


def step2_http_port():
    _separator()
    _tag("setup", f"{_C['bold']}Step 3 — HTTP Port{_C['reset']}")
    _separator()
    _tag("info", "The HTTP dashboard port (used for HTTP access or redirect to HTTPS).")
    print()
    _state["http_port"] = _ask_port("HTTP port", PORT)
    _tag("ok", f"HTTP port set to {_state['http_port']}")
    print()


def step3_tls():
    _separator()
    _tag("setup", f"{_C['bold']}Step 4 — HTTPS / TLS{_C['reset']}")
    _separator()
    print()
    print("       Options:")
    print("         [1] Generate a new self-signed certificate  (recommended)")
    print("         [2] Import existing certificate from the certs/ folder")
    print("         [3] Disable HTTPS — HTTP only  (not recommended)")
    print()
    choice = _ask("Choose", "1")

    if choice == "2":
        _step3_import()
    elif choice == "3":
        _step3_http_only()
    else:
        _step3_generate()

    if _state["tls_enabled"]:
        print()
        _tag("info", "HTTP → HTTPS redirect: when enabled, visiting the HTTP port")
        _tag("info", "automatically redirects browsers to the HTTPS port.")
        _state["http_redirect"] = _ask_yn("Enable HTTP → HTTPS redirect?", default=True)
        if not _state["http_redirect"]:
            _tag("info", "Disabled — both ports will serve the dashboard independently.")

    print()


def _step3_generate():
    _tag("setup", "Certificate details — press Enter to accept the default")
    print()

    default_cn = socket.gethostname() or "localhost"
    cn       = _ask("Common Name / hostname (CN)", default_cn)
    org      = _ask("Organization (O)",            "PingWatch")
    org_unit = _ask("Organizational Unit (OU)",    "")
    country  = _ask("Country (2-letter code, C)",  "")
    state    = _ask("State / Province (ST)",       "")
    locality = _ask("Locality / City (L)",         "")
    days_raw = _ask("Validity period (days)",      "825")
    tls_port = _ask_port("HTTPS port", TLS_PORT_DEFAULT)
    _tag("info", "Additional SANs — extra DNS names or IP addresses to include in the")
    _tag("info", "certificate (comma-separated). Press Enter to skip.")
    sans_raw = _ask("Extra SANs (DNS/IP, comma-separated)", "")

    try:
        days = max(1, int(days_raw))
    except ValueError:
        days = 825
        _tag("warn", f"Invalid validity — using {days} days")

    if country and len(country) != 2:
        country = country[:2].upper()
        _tag("warn", f"Country code trimmed to 2 letters: {country!r}")

    extra_sans = [s.strip() for s in sans_raw.split(",") if s.strip()] if sans_raw else []

    # Ensure cryptography is available before calling
    try:
        from core.tls import generate_self_signed_cert
    except ImportError:
        _tag("error", "The 'cryptography' package is required for certificate generation.")
        _tag("info",  "Install it first: pip install cryptography>=41.0.0")
        sys.exit(1)

    _tag("info", "Generating certificate ...")
    try:
        cert_pem, key_pem = generate_self_signed_cert(
            org_name=org,
            hostname=cn,
            org_unit=org_unit,
            country=country,
            state=state,
            locality=locality,
            days=days,
            extra_sans=extra_sans,
        )
    except Exception as e:
        _tag("error", f"Certificate generation failed: {e}")
        sys.exit(1)

    from core.tls import parse_cert_info
    info = parse_cert_info(cert_pem)
    _tag("ok", "Certificate generated:")
    _tag("info", f"  CN      : {info.get('subject', cn)}")
    _tag("info", f"  Issuer  : {info.get('issuer', org)}")
    _tag("info", f"  Expires : {info.get('not_after', '?')}  ({info.get('days_left', days)} days)")

    from db.backups import encrypt_pw
    _state["tls_enabled"]     = True
    _state["tls_port"]        = tls_port
    _state["tls_cert_pem"]    = cert_pem
    _state["tls_key_pem_enc"] = encrypt_pw(key_pem)
    _state["tls_cert_source"] = "generated"
    _state["tls_cn"]          = cn
    _state["org_name"]        = org


def _step3_import():
    cert_file = os.path.join(CERTS_DIR, "cert.pem")
    key_file  = os.path.join(CERTS_DIR, "key.pem")
    _tag("info", f"Looking for cert.pem and key.pem in: {CERTS_DIR}")

    if not os.path.isfile(cert_file) or not os.path.isfile(key_file):
        _tag("error", "cert.pem or key.pem not found in the certs/ folder.")
        _tag("info",  f"Place both files in: {CERTS_DIR}")
        _tag("info",  f"Then run '{_launcher_hint(setup=True)}' to try again.")
        _tag("info",  "Falling back to self-signed certificate generation.")
        _step3_generate()
        return

    try:
        cert_pem = open(cert_file, encoding="utf-8").read()
        key_pem  = open(key_file,  encoding="utf-8").read()
    except Exception as e:
        _tag("error", f"Could not read cert files: {e}")
        _step3_generate()
        return

    from core.tls import validate_cert_key_pair, parse_cert_info
    err = validate_cert_key_pair(cert_pem, key_pem)
    if err:
        _tag("error", f"Certificate validation failed: {err}")
        _tag("info",  f"Fix the certificate files and run '{_launcher_hint(setup=True)}' again,")
        _tag("info",  "or choose option 1 to generate a new self-signed certificate.")
        _step3_generate()
        return

    info = parse_cert_info(cert_pem)
    _tag("ok", "Certificate validated:")
    _tag("info", f"  CN      : {info.get('subject', '?')}")
    _tag("info", f"  Expires : {info.get('not_after', '?')}  ({info.get('days_left', '?')} days)")

    confirm = _ask_yn("Import this certificate?", default=True)
    if not confirm:
        _tag("info", "Falling back to self-signed certificate generation.")
        _step3_generate()
        return

    tls_port = _ask_port("HTTPS port", TLS_PORT_DEFAULT)

    from db.backups import encrypt_pw
    _state["tls_enabled"]     = True
    _state["tls_port"]        = tls_port
    _state["tls_cert_pem"]    = cert_pem
    _state["tls_key_pem_enc"] = encrypt_pw(key_pem)
    _state["tls_cert_source"] = "imported"
    _state["tls_cn"]          = info.get("subject", "")
    _tag("ok", "Certificate imported.")


def _step3_http_only():
    _tag("warn", "HTTPS disabled — PingWatch will serve plain HTTP.")
    _tag("info",  "You can enable HTTPS later in Settings → Networking.")
    _state["tls_enabled"]  = False
    _state["http_redirect"] = False


def step4_snmp_port():
    _separator()
    _tag("setup", f"{_C['bold']}Step 5 — SNMP Trap Port{_C['reset']}")
    _separator()
    import platform as _plat
    _sys = _plat.system()
    _tag("info", "UDP port to receive SNMP traps from network devices.")
    _tag("info", "Port 162 is the standard SNMP trap port.")
    if _sys == "Windows":
        _tag("info", "  Requires admin privileges (already elevated on Windows).")
    elif _sys in ("Linux", "Darwin"):
        _tag("info", "  Requires root on Linux/macOS. Options:")
        _tag("info", "    sudo bash start.sh          (run the server as root)")
        _tag("info", "    Use port 1162 (no root) + redirect:")
        _tag("info", "      sudo iptables -t nat -A PREROUTING -p udp --dport 162 -j REDIRECT --to-ports 1162")
    _tag("info", "PingWatch auto-falls back to 1162 then 2162 if 162 cannot be bound.")
    print()
    # SNMP uses UDP so skip TCP conflict detection
    raw = _ask("SNMP Trap port", str(SNMP_TRAP_PORT))
    try:
        port = int(raw)
        if not (1 <= port <= 65535):
            raise ValueError
        _state["snmp_port"] = port
        _tag("ok", f"SNMP Trap port set to {port}")
    except ValueError:
        _tag("warn", f"Invalid port — using default {SNMP_TRAP_PORT}")
        _state["snmp_port"] = SNMP_TRAP_PORT
    print()


def step5_firewall():
    import platform as _plat, shutil as _sh
    _sys = _plat.system()
    _separator()
    _tag("setup", f"{_C['bold']}Step 6 — Firewall Rules{_C['reset']}")
    _separator()
    print()

    rules = []
    rules.append(("TCP", _state["http_port"],  "PingWatch HTTP dashboard"))
    if _state["tls_enabled"]:
        rules.append(("TCP", _state["tls_port"], "PingWatch HTTPS"))
    rules.append(("UDP", _state["snmp_port"],  "PingWatch SNMP traps"))

    # ── Check which rules already exist ──────────────────────
    def _win_has(name):
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f'name="{name}"'],
            capture_output=True,
        )
        return r.returncode == 0

    def _ufw_status():
        """Return ufw status text, or '' if unavailable."""
        try:
            r = subprocess.run(["sudo", "ufw", "status"], capture_output=True, text=True)
            return r.stdout if r.returncode == 0 else ""
        except Exception:
            return ""

    def _fcmd_ports():
        """Return firewall-cmd --list-ports text, or '' if unavailable."""
        try:
            r = subprocess.run(["sudo", "firewall-cmd", "--list-ports"],
                               capture_output=True, text=True)
            return r.stdout if r.returncode == 0 else ""
        except Exception:
            return ""

    # Classify each rule as existing or missing
    existing, missing = [], []
    if _sys == "Windows":
        for rule in rules:
            (existing if _win_has(rule[2]) else missing).append(rule)
    elif _sys == "Linux" and _sh.which("ufw"):
        _ufw = _ufw_status()
        for rule in rules:
            proto, port, name = rule
            (existing if f"{port}/{proto.lower()}" in _ufw else missing).append(rule)
    elif _sys == "Linux" and _sh.which("firewall-cmd"):
        _fcmd = _fcmd_ports()
        for rule in rules:
            proto, port, name = rule
            (existing if f"{port}/{proto.lower()}" in _fcmd else missing).append(rule)
    elif _sys == "Linux" and _sh.which("iptables"):
        # Fallback: inspect the INPUT chain directly (covers plain iptables setups)
        try:
            r = subprocess.run(["sudo", "iptables", "-L", "INPUT", "-n"],
                               capture_output=True, text=True)
            _ipt = r.stdout if r.returncode == 0 else ""
        except Exception:
            _ipt = ""
        for rule in rules:
            proto, port, name = rule
            found = any(
                proto.lower() in line and f"dpt:{port}" in line
                for line in _ipt.splitlines()
            )
            (existing if found else missing).append(rule)
    else:
        missing = list(rules)   # can't check — assume all missing

    for proto, port, desc in existing:
        _tag("ok", f"Rule already in place: {proto} {port} — {desc}")

    if not missing:
        _tag("ok", "All firewall rules already configured — nothing to do")
        print()
        return

    print()
    _tag("info", "The following rules still need to be added:")
    for proto, port, desc in missing:
        _tag("info", f"  {proto} {port:5d}  — {desc}")
    print()

    if not _ask_yn("Add these firewall rules now?", default=True):
        _tag("warn", "Skipping — PingWatch may be unreachable from other machines")
        print()
        return

    # ── Add only the missing rules ────────────────────────────
    if _sys == "Windows":
        for proto, port, name in missing:
            r = subprocess.run(
                ["netsh", "advfirewall", "firewall", "add", "rule",
                 f"name={name}", "dir=in", "action=allow",
                 f"protocol={proto}", f"localport={port}"],
                capture_output=True,
            )
            if r.returncode == 0:
                _tag("ok", f"Firewall rule added: {proto} {port} ({name})")
            else:
                _tag("warn", f"Could not add rule for {proto} {port} — add manually if needed")

    elif _sys == "Linux":
        if _sh.which("ufw"):
            for proto, port, name in missing:
                r = subprocess.run(["sudo", "ufw", "allow", f"{port}/{proto.lower()}"],
                                   capture_output=True)
                if r.returncode == 0:
                    _tag("ok", f"ufw rule added: {proto} {port}")
                else:
                    _tag("warn", f"ufw failed for {proto} {port} — add manually if needed")
        elif _sh.which("firewall-cmd"):
            for proto, port, name in missing:
                r = subprocess.run(
                    ["sudo", "firewall-cmd", "--permanent",
                     f"--add-port={port}/{proto.lower()}"],
                    capture_output=True,
                )
                if r.returncode == 0:
                    _tag("ok", f"firewall-cmd rule added: {proto} {port}")
            subprocess.run(["sudo", "firewall-cmd", "--reload"], capture_output=True)
        else:
            _tag("warn", "No recognised firewall tool found (ufw / firewall-cmd).")
            _tag("info", "Add rules manually:")
            for proto, port, _ in missing:
                _tag("info", f"  sudo iptables -A INPUT -p {proto.lower()} --dport {port} -j ACCEPT")

    elif _sys == "Darwin":
        _tag("info", "macOS: add rules in System Settings → Network → Firewall, or use pfctl.")
        for proto, port, _ in missing:
            _tag("info", f"  Port {port}/{proto} — allow incoming")

    else:
        _tag("warn", f"Firewall configuration not supported on {_sys}. Add rules manually.")

    print()


def step6_shortcut():
    import platform as _plat
    _sys = _plat.system()
    _separator()
    _tag("setup", f"{_C['bold']}Step 7 — Desktop Shortcut{_C['reset']}")
    _separator()
    print()

    if _sys != "Windows":
        _tag("info", "Desktop shortcut creation is only supported on Windows.")
        _tag("info", f"To start PingWatch on {_sys}, run:")
        if _sys == "Linux" or _sys == "Darwin":
            _tag("info", f"  bash {os.path.join(_BASE, 'start.sh')}")
        else:
            _tag("info", f"  python3 {os.path.join(_BASE, 'server.py')}")
        print()
        return

    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    shortcut_path = os.path.join(desktop, "PingWatch.lnk")

    if os.path.isfile(shortcut_path):
        _tag("ok", "Desktop shortcut already exists — skipping")
        print()
        return

    if not _ask_yn("Create a desktop shortcut to launch PingWatch?", default=True):
        _tag("info", "Skipping shortcut")
        print()
        return

    target = os.path.join(_BASE, "start.bat")
    icon   = os.path.join(_BASE, "frontend", "favicon.ico")
    ps_cmd = (
        f'$s=(New-Object -COM WScript.Shell).CreateShortcut("{shortcut_path}");'
        f'$s.TargetPath="{target}";'
        f'$s.WorkingDirectory="{_BASE}";'
        f'$s.IconLocation="{icon},0";'
        f'$s.Description="PingWatch Network Monitor";'
        f'$s.Save()'
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True,
    )
    if r.returncode == 0:
        _tag("ok", f"Desktop shortcut created: {shortcut_path}")
    else:
        _tag("warn", "Could not create desktop shortcut — create it manually if needed")
    print()


def step7_init_db():
    global _db_created
    _separator()
    _tag("setup", f"{_C['bold']}Step 8 — Initialise Database & Save Settings{_C['reset']}")
    _separator()
    print()

    from db.backend import is_pg
    from db.core import db_init
    from db.users import db_save_settings

    _tag("info", "Verifying database schema ...")
    _db_created = True   # mark so atexit cleanup can remove it on abort
    try:
        db_init()
    except Exception as e:
        _tag("error", f"Database initialisation failed: {e}")
        sys.exit(1)
    if is_pg():
        _tag("ok", f"PostgreSQL database ready: {_state['pg_host']}:{_state['pg_port']}/{_state['pg_database']}")
    else:
        _tag("ok", f"Database created: {DB_PATH}")

    # Build settings dict from wizard state
    settings = {
        "http_port":   str(_state["http_port"]),
        "snmp_port":   str(_state["snmp_port"]),
        "tls_enabled": "1" if _state["tls_enabled"] else "0",
        "tls_port":    str(_state["tls_port"]),
        "tls_cn":      _state["tls_cn"],
        "org_name":    _state["org_name"],
        "http_redirect": "1" if _state["http_redirect"] else "0",
        "headless":    "1" if _state["headless"] else "0",
    }
    if _state["tls_cert_pem"]:
        settings["tls_cert_pem"]    = _state["tls_cert_pem"]
        settings["tls_key_pem_enc"] = _state["tls_key_pem_enc"]
        settings["tls_cert_source"] = _state["tls_cert_source"]

    try:
        db_save_settings(settings)
    except Exception as e:
        _tag("error", f"Failed to save settings: {e}")
        sys.exit(1)
    _tag("ok", "Settings saved to database")

    # ── Create initial admin account ──────────────────────────────────────────
    print()
    _separator("·")
    _tag("info", "Create your admin account for the web dashboard.")
    print()
    from db.users import db_add_user, db_list_users
    try:
        _existing_users = db_list_users()
    except Exception:
        _existing_users = []

    if not _existing_users:
        _admin_user = _ask("Admin username", "admin")
        while True:
            _admin_pw = _ask_password("Admin password (min 8 characters)", "")
            if not sys.stdin.isatty():
                # Non-interactive: generate a password
                import secrets as _sec_adm
                _admin_pw = _sec_adm.token_urlsafe(9)
                break
            if len(_admin_pw) >= 8:
                _admin_pw2 = _ask_password("Confirm password", "")
                if _admin_pw == _admin_pw2:
                    break
                _tag("error", "Passwords do not match — try again.")
            else:
                _tag("error", "Password must be at least 8 characters — try again.")
        try:
            ok = db_add_user(_admin_user, _admin_pw, "admin")
            if ok:
                _tag("ok", f"Admin account '{_admin_user}' created.")
                _tag("info", "Use these credentials to log in to the web dashboard.")
            else:
                _tag("warn", f"Account '{_admin_user}' may already exist — skipping.")
        except Exception as _ae:
            _tag("warn", f"Could not create admin account: {_ae}")
            _tag("info", "A randomly-generated password will appear at first server start.")
    else:
        _tag("info", f"Existing accounts found ({len(_existing_users)}) — skipping admin creation.")
    print()

    # Print summary
    print()
    _tag("info", "Configuration summary:")
    _tag("info", f"  HTTP port    : {_state['http_port']}")
    if _state["tls_enabled"]:
        _tag("info", f"  HTTPS port   : {_state['tls_port']}")
        _tag("info", f"  Certificate  : {_state['tls_cert_source']} (CN={_state['tls_cn']})")
        _tag("info", f"  HTTP redirect: {'yes' if _state['http_redirect'] else 'no'}")
    else:
        _tag("info",  "  HTTPS        : disabled")
    _tag("info", f"  SNMP port    : {_state['snmp_port']}")

    # Setup is done — disarm the cleanup so the DB is NOT deleted on normal exit
    _db_created = False
    print()


def step8_service():
    """Offer to install PingWatch as a systemd service (Linux only)."""
    import platform as _plat, shutil as _sh
    if _plat.system() != "Linux" or not _sh.which("systemctl"):
        return

    _separator()
    _tag("setup", f"{_C['bold']}Step 9 — System Service (systemd){_C['reset']}")
    _separator()
    _tag("info", "Install PingWatch as a systemd service so it starts automatically on boot.")
    print()

    service_src = os.path.join(_BASE, "pingwatch.service")
    service_dst = "/etc/systemd/system/pingwatch.service"

    if not os.path.isfile(service_src):
        _tag("warn", "pingwatch.service not found — cannot install service.")
        _tag("info", f"Expected: {service_src}")
        print()
        return

    already = os.path.isfile(service_dst)
    if already:
        _tag("ok", "Service is already installed.")
        if not _ask_yn("Reinstall / update the service unit?", default=False):
            print()
            return
    else:
        if not _ask_yn("Install PingWatch as a systemd service?", default=True):
            _tag("info", "Skipping. To install later:")
            _tag("info", f"  sudo bash {os.path.join(_BASE, 'start.sh')} --install-service")
            print()
            return

    # Determine the actual user (SUDO_USER when run via sudo, else current user)
    import pwd as _pwd, grp as _grp
    actual_user = os.environ.get("SUDO_USER") or ""
    if not actual_user:
        try:
            actual_user = _pwd.getpwuid(os.getuid()).pw_name
        except Exception:
            actual_user = os.environ.get("USER", "root")
    try:
        pw = _pwd.getpwnam(actual_user)
        actual_group = _grp.getgrgid(pw.pw_gid).gr_name
    except Exception:
        actual_group = actual_user

    # Read and patch the service template
    try:
        content = open(service_src, encoding="utf-8").read()
    except Exception as e:
        _tag("error", f"Could not read service template: {e}")
        print()
        return

    python_path = _sh.which("python3") or sys.executable
    content = content.replace("/opt/pingwatch", _BASE)
    content = content.replace("/usr/bin/python3", python_path)
    content = content.replace("# User=pingwatch",  f"User={actual_user}")
    content = content.replace("# Group=pingwatch", f"Group={actual_group}")

    # Write service file (direct if root, via sudo cp otherwise)
    _tag("info", f"Installing service (User={actual_user}, Group={actual_group}) ...")
    wrote_ok = False
    if os.geteuid() == 0:
        try:
            with open(service_dst, "w", encoding="utf-8") as f:
                f.write(content)
            wrote_ok = True
        except Exception as e:
            _tag("error", f"Could not write {service_dst}: {e}")
    else:
        import tempfile as _tmp
        try:
            with _tmp.NamedTemporaryFile(mode="w", suffix=".service",
                                         delete=False, encoding="utf-8") as tf:
                tf.write(content)
                tmp_path = tf.name
            r = subprocess.run(["sudo", "cp", tmp_path, service_dst],
                               capture_output=True, text=True)
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            if r.returncode == 0:
                wrote_ok = True
            else:
                _tag("error", f"sudo cp failed: {r.stderr.strip()}")
                _tag("info",  f"Retry with root:  sudo bash {os.path.join(_BASE, 'start.sh')} --install-service")
        except Exception as e:
            _tag("error", f"Could not install service file: {e}")

    if not wrote_ok:
        print()
        return

    # Reload, enable, start
    all_ok = True
    for cmd in [
        _systemctl("daemon-reload"),
        _systemctl("enable", "pingwatch"),
        _systemctl("start",  "pingwatch"),
    ]:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            _tag("warn", f"  {' '.join(cmd[1:])} failed: {(r.stderr or r.stdout).strip()}")
            all_ok = False
            break

    if all_ok:
        _tag("ok", "Service installed, enabled, and started.")
        _tag("info", "Auto-starts on boot. Useful commands:")
        _pfx = "" if (sys.platform != "win32" and os.getuid() == 0) else "sudo "
        _tag("info", f"  {_pfx}systemctl status pingwatch")
        _tag("info", f"  {_pfx}systemctl restart pingwatch")
        _tag("info", "  journalctl -u pingwatch -f")
    else:
        _tag("warn", "Service install may be incomplete — check errors above.")
        _tag("info", f"Retry:  sudo bash {os.path.join(_BASE, 'start.sh')} --install-service")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # ── Check for re-run mode (--setup with existing config/DB) ──────────────
    rerun = "--setup" in sys.argv

    # Load existing pingwatch.conf backend settings as defaults (re-run mode)
    try:
        from db.backend import load_config as _load_backend_config, get_config as _get_cfg
        _load_backend_config()
        _cfg = _get_cfg()
        _state["db_backend"]  = _cfg.get("db_backend",  "sqlite")
        _state["pg_host"]     = _cfg.get("pg_host",     "localhost")
        _state["pg_port"]     = int(_cfg.get("pg_port", 5432))
        _state["pg_database"] = _cfg.get("pg_database", "pingwatch")
        _state["pg_user"]     = _cfg.get("pg_user",     "pingwatch")
        _state["pg_password"] = _cfg.get("pg_password", "")
    except Exception:
        pass

    if rerun:
        # Load existing app settings (port, TLS, etc.) as defaults
        try:
            from db.users import db_load_settings
            existing = db_load_settings()
            _state["http_port"]    = int(existing.get("http_port",    PORT))
            _state["snmp_port"]    = int(existing.get("snmp_port",    SNMP_TRAP_PORT))
            _state["tls_enabled"]  = bool(int(existing.get("tls_enabled", 1)))
            _state["tls_port"]     = int(existing.get("tls_port",     TLS_PORT_DEFAULT))
            _state["tls_cn"]       = existing.get("tls_cn",           "")
            _state["org_name"]     = existing.get("org_name",         "PingWatch")
            _state["http_redirect"] = bool(int(existing.get("http_redirect", 1)))
        except Exception:
            pass

    # ── Banner ────────────────────────────────────────────────────────────────
    print()
    _separator("═")
    _tag("setup", f"{_C['bold']}PingWatch v{app_state.APP_VERSION} — First Run Setup Wizard{_C['reset']}")
    _separator("═")
    if rerun:
        _tag("info", "Re-running setup. Existing settings shown as defaults.")
    else:
        _tag("info", "Welcome! This wizard runs once to configure PingWatch.")
        _tag("info", "Press Enter to accept defaults. You can change everything")
        _tag("info", "later in Settings once the server is running.")
    print()

    # ── Stop the service before touching the DB ───────────────────────────────
    # Two concurrent writers sharing the same SQLite WAL will race and can leave
    # the service's connection in a "readonly database" state.  Stop the service
    # first; we'll restart it (with the new config) when the wizard finishes.
    _svc_stopped = False
    if _is_service_active():
        _tag("warn", "The PingWatch service is currently running.")
        _tag("info", "It must be stopped before the wizard modifies the database.")
        if _ask_yn("Stop the service now? (recommended)", default=True):
            if _stop_service():
                _svc_stopped = True
                _tag("ok", "Service stopped.")
            else:
                _tag("warn", "Could not stop service — proceeding anyway.")
                _tag("warn", "You may see database errors. Restart the service after setup.")
        else:
            _tag("warn", "Proceeding with service running — database conflicts may occur.")
        print()

    try:
        step1_packages()
        # Step 2: database backend (must run before db_init so the right backend is used)
        step2_database()

        # Initialise DB schema now that the backend is known; this also makes
        # encrypt_pw / db helpers available for the TLS step that follows.
        # For PostgreSQL the pool was already opened inside step2_database();
        # db_init() here is idempotent (creates schemas if missing, no-ops otherwise).
        try:
            from db.core import db_init, logs_db_init
            db_init()
            logs_db_init()
        except Exception as _e:
            _tag("error", f"Failed to initialise database schema: {_e}")
            sys.exit(1)
        _fix_file_ownership()   # chown DB back to SUDO_USER if running as root

        step2_http_port()
        step3_tls()
        step4_snmp_port()
        step5_firewall()
        step6_shortcut()
        step7_init_db()
        step8_service()
    except KeyboardInterrupt:
        print()
        _tag("warn", "Setup aborted by user.")
        sys.exit(1)

    _fix_file_ownership()   # chown certs + DB again after step3 may have written certs

    _separator("═")
    _tag("ok", f"{_C['bold']}Setup complete — starting PingWatch...{_C['reset']}")
    _separator("═")
    print()

    # ── Restart the service if we stopped it ──────────────────────────────────
    if _svc_stopped:
        _tag("info", "Restarting service with updated configuration...")
        if _restart_service():
            _tag("ok", "Service restarted — settings are now live.")
            _tag("info", "Follow logs: journalctl -u pingwatch -f")
        else:
            _tag("warn", "Could not restart service automatically.")
            _pfx = "" if (sys.platform != "win32" and os.getuid() == 0) else "sudo "
            _tag("info", f"Start it manually: {_pfx}systemctl start pingwatch")
        print()

    sys.exit(0)


if __name__ == "__main__":
    main()
