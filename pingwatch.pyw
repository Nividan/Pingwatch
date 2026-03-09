"""Launch PingWatch without a console window (double-click to start)."""
import sys, os, subprocess, ctypes

# ── Elevate to admin (needed for SNMP trap port 162) ──────────────────────────
def _is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False

if not _is_admin():
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{os.path.abspath(__file__)}"', None, 1
    )
    sys.exit()

# ── Ensure the script's own directory is on sys.path ──────────────────────────
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

# ── Auto-install missing packages silently ────────────────────────────────────
try:
    import pystray
    from PIL import Image
except ImportError:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "pystray", "Pillow"],
        capture_output=True
    )

from server import main
main()
