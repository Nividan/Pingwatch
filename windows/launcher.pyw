"""
windows/launcher.pyw — PingWatch Windows launcher.

Replaces the logic of start.bat:
  1. Admin elevation (for SNMP trap port 162)
  2. First-run detection (pingwatch.conf missing → setup wizard)
  3. Port cleanup (kill stale processes on HTTP/HTTPS ports)
  4. Launch server.py

Uses .pyw extension → no console window on Windows.
"""

import os
import sys
import subprocess
import time

# ── Resolve project root ────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
os.chdir(_ROOT)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ── Admin elevation (Windows only) ──────────────────────────────────────────
def _is_admin():
    if os.name != "nt":
        return True  # not Windows — skip
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


if os.name == "nt" and not _is_admin():
    import ctypes
    # Re-launch self as admin
    params = f'"{os.path.abspath(__file__)}"'
    if sys.argv[1:]:
        params += " " + " ".join(f'"{a}"' for a in sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, _ROOT, 1,
    )
    sys.exit(0)


# ── Resolve the active code release (managed-upgrade layout) ────────────────
# Windows analogue of linux bootstrap.py's role: pick the code root to import
# the server from. Flat checkout → code_root is _ROOT, no data override (a
# no-op). releases/<version>/ layout → imports come from the active release and
# persistent state lives in <base>/data. Must run BEFORE the first server-module
# import below, since under the managed layout db/, core/, server.py live in the
# release dir, not next to this launcher.
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    import bootstrap as _bootstrap
    _code_root, _data_dir = _bootstrap.resolve_code_root()
except Exception as _e:
    _code_root, _data_dir = _ROOT, None
if not _code_root:
    sys.stderr.write("launcher: managed layout but no runnable release\n")
    sys.exit(1)
if _data_dir:
    os.environ["PW_DATA_DIR"] = _data_dir
    os.environ["PW_BASE_DIR"] = _ROOT
if _code_root != _ROOT:
    os.chdir(_code_root)
    sys.path.insert(0, _code_root)


# ── First-run detection ─────────────────────────────────────────────────────
from db.backend import needs_setup

if needs_setup() or "--setup" in sys.argv:
    wizard_ok = False
    try:
        from gui_setup import run_wizard
        wizard_ok = run_wizard()
    except ImportError:
        # tkinter not available — fall back to CLI wizard
        r = subprocess.run(
            [sys.executable, os.path.join(_ROOT, "setup_wizard.py")]
            + [a for a in sys.argv[1:] if a != "--setup"]
        )
        wizard_ok = (r.returncode == 0)
    except Exception as e:
        # GUI wizard crashed — fall back to CLI
        r = subprocess.run(
            [sys.executable, os.path.join(_ROOT, "setup_wizard.py")]
            + [a for a in sys.argv[1:] if a != "--setup"]
        )
        wizard_ok = (r.returncode == 0)

    if not wizard_ok:
        sys.exit(1)

    # Small delay for port release after wizard
    time.sleep(0.5)


# ── Port cleanup ────────────────────────────────────────────────────────────
from core.setup_logic import kill_port_processes

# Read configured ports from config (if available)
try:
    import core.settings as _settings
    from db import db_load_settings
    from db.backend import load_config
    load_config()
    _http_port = int(_settings.get("http_port", 7070) or 7070)
    _tls_port = int(_settings.get("tls_port", 8443) or 8443)
except Exception:
    _http_port, _tls_port = 7070, 8443

kill_port_processes(_http_port, _tls_port)
time.sleep(1)  # allow ports to release


# ── Launch server ───────────────────────────────────────────────────────────
from server import main
main()
