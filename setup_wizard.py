"""
setup_wizard.py — PingWatch first-run interactive setup wizard.

Called by start.bat when no database exists (first launch) or when
the --setup flag is passed.  Guides the user through:
  1. Required package checks & installs
  2. HTTP port selection
  3. HTTPS / TLS certificate setup
  4. SNMP trap port selection
  5. Windows Firewall rules
  6. Desktop shortcut
  7. Database initialisation & settings persistence

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

from core.config import DB_PATH, PORT, TLS_PORT_DEFAULT, CERTS_DIR, SNMP_TRAP_PORT
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

    return False, last_err


def _check_snmpget() -> bool:
    import shutil
    return shutil.which("snmpget") is not None


def step1_packages():
    _separator()
    _tag("setup", f"{_C['bold']}Step 1 — Check Required Packages{_C['reset']}")
    _separator()
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
                    _headless = True   # also skip pystray / Pillow below
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
                        r = subprocess.run(["brew", "install", "python-tk"], capture_output=False)
                        if r.returncode == 0:
                            _tag("ok", "python-tk installed — restart the wizard to confirm")
                        else:
                            _tag("warn", "Install failed — try manually, then re-run setup")
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
                _tag("error", f"Could not install '{pkg['name']}' automatically.")
                if err:
                    # Show the last meaningful pip error line (skip full traceback)
                    err_lines = [l.strip() for l in err.splitlines() if l.strip()]
                    if err_lines:
                        _tag("info", f"  pip: {err_lines[-1]}")
                import platform as _plat
                _sys = _plat.system()
                _tag("info", "Install manually:")
                _tag("info", f"  pip install {pkg['install']}")
                if _sys == "Linux":
                    _apt_map = {
                        "pystray":      ("python3-pystray", "also needs: sudo apt install python3-xlib"),
                        "Pillow":       ("python3-pil",     None),
                        "paramiko":     ("python3-paramiko", None),
                        "cryptography": ("python3-cryptography", None),
                    }
                    _apt = _apt_map.get(pkg["name"])
                    if _apt:
                        _tag("info", f"  or: sudo apt install {_apt[0]}")
                        if _apt[1]:
                            _tag("info", f"  note: {_apt[1]}")
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
        install_snmp = _ask_yn("Install net-snmp now?", default=False)
        if install_snmp:
            import platform as _plat, shutil as _sh
            _sys = _plat.system()
            _ok_snmp = False
            if _sys == "Windows":
                _tag("info", "Trying Chocolatey ...")
                r = subprocess.run(["choco", "install", "net-snmp", "-y"], capture_output=True)
                if r.returncode == 0:
                    _tag("ok", "net-snmp installed via Chocolatey")
                    _ok_snmp = True
                else:
                    _tag("info", "Trying winget ...")
                    r2 = subprocess.run(["winget", "install", "net-snmp.net-snmp"], capture_output=True)
                    if r2.returncode == 0:
                        _tag("ok", "net-snmp installed via winget")
                        _ok_snmp = True
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
                _tag("warn", "Automatic install failed.")
                _tag("info",  "Windows: choco install net-snmp  OR  winget install net-snmp.net-snmp")
                _tag("info",  "Linux:   sudo apt install snmp  OR  sudo dnf install net-snmp-utils")
                _tag("info",  "macOS:   brew install net-snmp")
        else:
            _tag("warn", "Skipping — SNMP polling sensors will not be available")

    print()
    if not all_ok:
        _tag("error", "One or more required packages could not be installed.")
        _tag("info",  f"Fix the issues above and run '{_launcher_hint()}' again.")
        sys.exit(1)


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
        except OSError:
            return 1      # bind failed → port is in use


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


def step2_http_port():
    _separator()
    _tag("setup", f"{_C['bold']}Step 2 — HTTP Port{_C['reset']}")
    _separator()
    _tag("info", "The HTTP dashboard port (used for HTTP access or redirect to HTTPS).")
    print()
    _state["http_port"] = _ask_port("HTTP port", PORT)
    _tag("ok", f"HTTP port set to {_state['http_port']}")
    print()


def step3_tls():
    _separator()
    _tag("setup", f"{_C['bold']}Step 3 — HTTPS / TLS{_C['reset']}")
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
    _tag("setup", f"{_C['bold']}Step 4 — SNMP Trap Port{_C['reset']}")
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
    _tag("setup", f"{_C['bold']}Step 5 — Firewall Rules{_C['reset']}")
    _separator()
    print()

    rules = []
    rules.append(("TCP", _state["http_port"],  "PingWatch HTTP dashboard"))
    if _state["tls_enabled"]:
        rules.append(("TCP", _state["tls_port"], "PingWatch HTTPS"))
    rules.append(("UDP", _state["snmp_port"],  "PingWatch SNMP traps"))

    _tag("info", "The following firewall rules will be added:")
    for proto, port, desc in rules:
        _tag("info", f"  {proto} {port:5d}  — {desc}")
    print()

    if not _ask_yn("Add these firewall rules now?", default=True):
        _tag("warn", "Skipping — PingWatch may be unreachable from other machines")
        print()
        return

    if _sys == "Windows":
        for proto, port, name in rules:
            chk = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule", f'name="{name}"'],
                capture_output=True,
            )
            if chk.returncode == 0:
                _tag("ok", f"Rule already exists: {name}")
                continue
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
            for proto, port, name in rules:
                _proto = proto.lower()
                r = subprocess.run(["sudo", "ufw", "allow", f"{port}/{_proto}"],
                                   capture_output=True)
                if r.returncode == 0:
                    _tag("ok", f"ufw rule added: {proto} {port}")
                else:
                    _tag("warn", f"ufw failed for {proto} {port} — add manually if needed")
        elif _sh.which("firewall-cmd"):
            for proto, port, name in rules:
                _proto = proto.lower()
                r = subprocess.run(
                    ["sudo", "firewall-cmd", "--permanent",
                     f"--add-port={port}/{_proto}"],
                    capture_output=True,
                )
                if r.returncode == 0:
                    _tag("ok", f"firewall-cmd rule added: {proto} {port}")
            subprocess.run(["sudo", "firewall-cmd", "--reload"], capture_output=True)
        else:
            _tag("warn", "No recognised firewall tool found (ufw / firewall-cmd).")
            _tag("info", "Add rules manually:")
            for proto, port, _ in rules:
                _tag("info", f"  sudo iptables -A INPUT -p {proto.lower()} --dport {port} -j ACCEPT")

    elif _sys == "Darwin":
        _tag("info", "macOS: add rules in System Settings → Network → Firewall, or use pfctl.")
        for proto, port, _ in rules:
            _tag("info", f"  Port {port}/{proto} — allow incoming")

    else:
        _tag("warn", f"Firewall configuration not supported on {_sys}. Add rules manually.")

    print()


def step6_shortcut():
    import platform as _plat
    _sys = _plat.system()
    _separator()
    _tag("setup", f"{_C['bold']}Step 6 — Desktop Shortcut{_C['reset']}")
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
    ps_cmd = (
        f'$s=(New-Object -COM WScript.Shell).CreateShortcut("{shortcut_path}");'
        f'$s.TargetPath="{target}";'
        f'$s.WorkingDirectory="{_BASE}";'
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
    _tag("setup", f"{_C['bold']}Step 7 — Initialise Database & Save Settings{_C['reset']}")
    _separator()
    print()

    from db.core import db_init
    from db.users import db_save_settings

    _tag("info", "Creating database schema ...")
    _db_created = True   # mark so atexit cleanup can remove it on abort
    try:
        db_init()
    except Exception as e:
        _tag("error", f"Database initialisation failed: {e}")
        sys.exit(1)
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


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # ── Check for re-run mode (--setup with existing DB) ─────────────────────
    rerun = "--setup" in sys.argv
    if rerun and os.path.isfile(DB_PATH):
        # Load existing settings as defaults
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

    # Initialise DB schema before any step so encrypt_pw / db helpers work
    try:
        from db.core import db_init
        db_init()
    except Exception as _e:
        _tag("error", f"Failed to initialise database schema: {_e}")
        sys.exit(1)

    try:
        step1_packages()
        step2_http_port()
        step3_tls()
        step4_snmp_port()
        step5_firewall()
        step6_shortcut()
        step7_init_db()
    except KeyboardInterrupt:
        print()
        _tag("warn", "Setup aborted by user.")
        sys.exit(1)

    _separator("═")
    _tag("ok", f"{_C['bold']}Setup complete — starting PingWatch...{_C['reset']}")
    _separator("═")
    print()
    sys.exit(0)


if __name__ == "__main__":
    main()
