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

# ── Paths (resolve relative to this script's directory) ──────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE)

from config import DB_PATH, PORT, TLS_PORT_DEFAULT, CERTS_DIR, SNMP_TRAP_PORT
import app_state

# ── ANSI colour helpers ───────────────────────────────────────────────────────
def _enable_ansi_windows() -> bool:
    """Enable Virtual Terminal Processing on Windows; return True if supported."""
    try:
        import ctypes, ctypes.wintypes
        kernel32 = ctypes.windll.kernel32
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if handle == INVALID_HANDLE_VALUE or handle == 0:
            return False
        mode = ctypes.wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True  # already on
        return bool(kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except Exception:
        return False

_COLOUR = sys.stdout.isatty() and (
    sys.platform != "win32" or _enable_ansi_windows()
)

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
            print(       "        Run start.bat again to restart setup.")
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
        "required": True,
    },
    {
        "import":   "pystray",
        "name":     "pystray",
        "install":  "pystray>=0.19.5",
        "pip":      True,
        "desc":     "system tray icon",
        "required": False,
    },
    {
        "import":   "PIL",
        "name":     "Pillow",
        "install":  "Pillow>=10.0.0",
        "pip":      True,
        "desc":     "image support (tray icon)",
        "required": False,
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


def _pip_install(package_spec: str) -> bool:
    """Run pip install; return True on success."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", package_spec, "--quiet"],
            capture_output=True, text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _check_snmpget() -> bool:
    try:
        result = subprocess.run(["where", "snmpget"], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False


def step1_packages():
    _separator()
    _tag("setup", f"{_C['bold']}Step 1 — Check Required Packages{_C['reset']}")
    _separator()
    print()

    all_ok = True
    for pkg in _PACKAGES:
        if _check_import(pkg["import"]):
            _tag("ok", f"{pkg['name']} — {pkg['desc']}")
            continue

        severity = "error" if pkg["required"] else "warn"
        _tag(severity, f"Package '{pkg['name']}' is not installed.")
        _tag("info",   f"This enables: {pkg['desc']}")

        if pkg["pip"] is None:
            # stdlib — cannot be pip-installed (tkinter)
            _tag("error", "tkinter is part of the Python standard library but was not found.")
            _tag("info",  "Re-install Python and tick 'tcl/tk and IDLE' during setup.")
            if pkg["required"]:
                all_ok = False
            continue

        install_now = _ask_yn(f"Install '{pkg['name']}' now?", default=True)
        if install_now:
            _tag("info", f"Installing {pkg['install']} ...")
            ok = _pip_install(pkg["install"])
            if ok:
                _tag("ok", f"{pkg['name']} installed successfully")
            else:
                _tag("error", f"Failed to install {pkg['name']}.")
                _tag("info",  "Manual installation guide:")
                _tag("info",  "  1. Open Command Prompt as Administrator")
                _tag("info",  f"  2. Run: pip install {pkg['install']}")
                _tag("info",  "  3. Re-run start.bat when done")
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
            choco_ok = False
            try:
                _tag("info", "Trying Chocolatey ...")
                r = subprocess.run(["choco", "install", "net-snmp", "-y"], capture_output=True)
                if r.returncode == 0:
                    _tag("ok", "net-snmp installed via Chocolatey")
                    choco_ok = True
                else:
                    _tag("warn", "Chocolatey install failed (exit code {})".format(r.returncode))
            except FileNotFoundError:
                _tag("warn", "Chocolatey (choco) is not installed on this system.")
            except Exception as _e:
                _tag("warn", f"Chocolatey error: {_e}")

            if not choco_ok:
                winget_ok = False
                try:
                    _tag("info", "Trying winget ...")
                    r2 = subprocess.run(["winget", "install", "net-snmp.net-snmp"], capture_output=True)
                    if r2.returncode == 0:
                        _tag("ok", "net-snmp installed via winget")
                        winget_ok = True
                    else:
                        _tag("warn", "winget install also failed.")
                except FileNotFoundError:
                    _tag("warn", "winget is not available on this system.")
                except Exception as _e:
                    _tag("warn", f"winget error: {_e}")

                if not winget_ok:
                    _tag("warn", "Automatic install failed. Manual installation guide:")
                    _tag("info", "  1. Go to: https://sourceforge.net/projects/net-snmp/")
                    _tag("info", "  2. Download the Windows installer (net-snmp-X.X.X-win64.exe)")
                    _tag("info", "  3. Run the installer — tick 'Add to PATH' if prompted")
                    _tag("info", "  4. Reboot or open a new terminal, then re-run start.bat")
        else:
            _tag("warn", "Skipping — SNMP polling sensors will not be available")

    print()
    if not all_ok:
        _tag("error", "One or more required packages could not be installed.")
        _tag("info",  "Fix the issues above and run start.bat again.")
        sys.exit(1)


# ── Port helpers ──────────────────────────────────────────────────────────────

def _port_in_use(port: int) -> "int | None":
    """Return PID using the port, or None if free."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-NetTCPConnection -LocalPort {port} -State Listen -EA SilentlyContinue)"
             f".OwningProcess | Select-Object -First 1"],
            capture_output=True, text=True,
        )
        pid_str = result.stdout.strip()
        return int(pid_str) if pid_str.isdigit() else None
    except Exception:
        return None


def _pid_name(pid: int) -> str:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Id {pid} -EA SilentlyContinue).Name"],
            capture_output=True, text=True,
        )
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _kill_pid(pid: int) -> bool:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Stop-Process -Id {pid} -Force -EA SilentlyContinue"],
            capture_output=True,
        )
        return r.returncode == 0
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

        proc = _pid_name(pid)
        _tag("warn", f"Port {port} is already in use by PID {pid} ({proc}).")
        print()
        print("       Options:")
        print(f"         [1] Stop PID {pid} ({proc}) and use port {port}")
        print(f"         [2] Enter a different port")
        print(f"         [3] Keep port {port} anyway (may fail at startup)")
        print()
        choice = _ask("Choose", "1")
        if choice == "1":
            ok = _kill_pid(pid)
            if ok:
                _tag("ok", f"Process {pid} stopped — port {port} is now free")
                return port
            else:
                _tag("error", f"Could not stop PID {pid}. Try option 2.")
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
        from tls import generate_self_signed_cert
    except ImportError:
        _tag("error", "The 'cryptography' package is required for certificate generation.")
        _tag("info",  "Install it first: pip install cryptography>=41.0.0")
        _tag("warn",  "Falling back to HTTP-only mode — enable HTTPS later in Settings.")
        _step3_http_only()
        return

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
        _tag("warn",  "Falling back to HTTP-only mode — enable HTTPS later in Settings.")
        _step3_http_only()
        return

    try:
        from tls import parse_cert_info
        info = parse_cert_info(cert_pem)
    except Exception:
        info = {}
    _tag("ok", "Certificate generated:")
    _tag("info", f"  CN      : {info.get('subject', cn)}")
    _tag("info", f"  Issuer  : {info.get('issuer', org)}")
    _tag("info", f"  Expires : {info.get('not_after', '?')}  ({info.get('days_left', days)} days)")

    try:
        from db.backups import encrypt_pw
        key_enc = encrypt_pw(key_pem)
    except Exception as _e:
        _tag("warn", f"Could not encrypt private key ({_e}) — storing unencrypted.")
        key_enc = key_pem

    _state["tls_enabled"]     = True
    _state["tls_port"]        = tls_port
    _state["tls_cert_pem"]    = cert_pem
    _state["tls_key_pem_enc"] = key_enc
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
        _tag("info",  "Then run start.bat --setup to try again.")
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

    try:
        from tls import validate_cert_key_pair, parse_cert_info
        err = validate_cert_key_pair(cert_pem, key_pem)
    except Exception as _e:
        _tag("error", f"TLS module error: {_e}")
        _tag("info",  "Falling back to self-signed certificate generation.")
        _step3_generate()
        return

    if err:
        _tag("error", f"Certificate validation failed: {err}")
        _tag("info",  "Fix the certificate files and run start.bat --setup again,")
        _tag("info",  "or choose option 1 to generate a new self-signed certificate.")
        _step3_generate()
        return

    try:
        info = parse_cert_info(cert_pem)
    except Exception:
        info = {}
    _tag("ok", "Certificate validated:")
    _tag("info", f"  CN      : {info.get('subject', '?')}")
    _tag("info", f"  Expires : {info.get('not_after', '?')}  ({info.get('days_left', '?')} days)")

    confirm = _ask_yn("Import this certificate?", default=True)
    if not confirm:
        _tag("info", "Falling back to self-signed certificate generation.")
        _step3_generate()
        return

    tls_port = _ask_port("HTTPS port", TLS_PORT_DEFAULT)

    try:
        from db.backups import encrypt_pw
        key_enc = encrypt_pw(key_pem)
    except Exception as _e:
        _tag("warn", f"Could not encrypt private key ({_e}) — storing unencrypted.")
        key_enc = key_pem

    _state["tls_enabled"]     = True
    _state["tls_port"]        = tls_port
    _state["tls_cert_pem"]    = cert_pem
    _state["tls_key_pem_enc"] = key_enc
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
    _tag("info", "UDP port to receive SNMP traps from network devices.")
    _tag("info", "Port 162 is the standard — requires admin privileges (already elevated).")
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
    _separator()
    _tag("setup", f"{_C['bold']}Step 5 — Windows Firewall Rules{_C['reset']}")
    _separator()
    print()

    rules = []
    rules.append(("TCP", _state["http_port"],  "PingWatch HTTP dashboard"))
    if _state["tls_enabled"]:
        rules.append(("TCP", _state["tls_port"], "PingWatch HTTPS"))
    rules.append(("UDP", _state["snmp_port"],  "PingWatch SNMP traps"))

    # Pre-check which rules already exist
    def _rule_exists(name):
        try:
            chk = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule", f'name="{name}"'],
                capture_output=True,
            )
            return chk.returncode == 0
        except Exception:
            return False

    existing  = [(proto, port, name) for proto, port, name in rules if     _rule_exists(name)]
    missing   = [(proto, port, name) for proto, port, name in rules if not _rule_exists(name)]

    if existing:
        _tag("info", "Rules already present:")
        for proto, port, name in existing:
            _tag("ok", f"  {proto} {port:5d}  — {name}")
        print()

    if not missing:
        _tag("ok", "All firewall rules already exist — nothing to add.")
        print()
        return

    _tag("info", "The following rules need to be added:")
    for proto, port, name in missing:
        _tag("info", f"  {proto} {port:5d}  — {name}")
    print()

    if not _ask_yn("Add these firewall rules now?", default=True):
        _tag("warn", "Skipping — PingWatch may be unreachable from other machines")
        print()
        return

    for proto, port, name in missing:
        try:
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
        except FileNotFoundError:
            _tag("warn", f"netsh not found — add firewall rule manually: {proto} {port} ({name})")
        except Exception as _e:
            _tag("warn", f"Firewall rule error for {proto} {port}: {_e}")
    print()


def step6_shortcut():
    _separator()
    _tag("setup", f"{_C['bold']}Step 6 — Desktop Shortcut{_C['reset']}")
    _separator()
    print()

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

    target   = os.path.join(_BASE, "pingwatch.pyw")
    icon_loc = ""
    ico_path = os.path.join(_BASE, "pingwatch-icon.ico")
    png_path = os.path.join(_BASE, "pingwatch-icon.png")
    if os.path.isfile(ico_path):
        icon_loc = ico_path
    elif os.path.isfile(png_path):
        # Convert PNG → ICO using Pillow so Windows can use it as a shortcut icon
        try:
            from PIL import Image
            img = Image.open(png_path)
            img.save(ico_path, format="ICO", sizes=[(256, 256), (64, 64), (32, 32), (16, 16)])
            icon_loc = ico_path
            _tag("ok", "Icon converted: pingwatch-icon.png → pingwatch-icon.ico")
        except Exception as _e:
            _tag("warn", f"Could not convert icon: {_e}")
    ps_cmd = (
        f'$s=(New-Object -COM WScript.Shell).CreateShortcut("{shortcut_path}");'
        f'$s.TargetPath="{target}";'
        f'$s.WorkingDirectory="{_BASE}";'
        f'$s.Description="PingWatch Network Monitor";'
        + (f'$s.IconLocation="{icon_loc}";' if icon_loc else "")
        + f'$s.Save()'
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
        )
        if r.returncode == 0:
            _tag("ok", f"Desktop shortcut created: {shortcut_path}")
        else:
            _tag("warn", "Could not create desktop shortcut — create it manually if needed")
    except FileNotFoundError:
        _tag("warn", "PowerShell not found — desktop shortcut not created")
    except Exception as _e:
        _tag("warn", f"Desktop shortcut error: {_e}")
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
