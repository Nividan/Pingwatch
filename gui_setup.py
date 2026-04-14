"""
gui_setup.py — PingWatch tkinter setup wizard.

Professional dark-themed multi-step wizard that replaces the CLI
``setup_wizard.py`` for interactive first-run configuration.  Falls
back to the CLI wizard if tkinter is unavailable.

Entry point:  ``run_wizard() -> bool``
"""

import os
import sys
import threading

# ── Ensure project root on sys.path ─────────────────────────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

import tkinter as tk
from tkinter import ttk, filedialog

from core.setup_logic import (
    PACKAGES, check_import, pip_available, pip_install,
    check_snmpget, check_ping, install_snmpget,
    port_in_use, kill_port_processes,
    detect_pg_server, generate_pg_password, test_pg_connection,
    pg_install_instructions,
    default_wizard_state, save_wizard_config, initialize_database,
    win_firewall_check, win_firewall_add, get_firewall_rules,
    win_create_shortcut, win_task_exists, win_install_task,
)

# ── High-DPI awareness (Windows) ────────────────────────────────────────────
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

# ── Color palette (matches PingWatch web UI & gui.py) ───────────────────────
BG     = "#0d1117"
BG2    = "#161b22"
BG3    = "#1c2128"
BORDER = "#30363d"
TEXT   = "#e6edf3"
TEXT2  = "#8b949e"
TEXT3  = "#484f58"
GREEN  = "#23d18b"
RED    = "#f85149"
YELLOW = "#f0a500"
ACCENT = "#2f81f7"

_FNT = "Segoe UI" if sys.platform == "win32" else \
       "Helvetica Neue" if sys.platform == "darwin" else "DejaVu Sans"
_MONO = "Consolas" if sys.platform == "win32" else \
        "Menlo" if sys.platform == "darwin" else "DejaVu Sans Mono"

# ── Step names ──────────────────────────────────────────────────────────────
_STEPS = ["Welcome", "Packages", "Database", "Network", "Security", "System", "Summary"]


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _entry(parent, **kw):
    """Create a dark-themed Entry widget."""
    e = tk.Entry(parent, bg=BG3, fg=TEXT, insertbackground=TEXT,
                 relief="flat", highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=ACCENT,
                 font=(_FNT, 11), **kw)
    return e


def _label(parent, text, size=11, color=TEXT, bold=False, **kw):
    w = "bold" if bold else "normal"
    return tk.Label(parent, text=text, bg=BG, fg=color,
                    font=(_FNT, size, w), **kw)


def _btn(parent, text, command, style="default"):
    colors = {
        "default": (BG3, TEXT2, BORDER),
        "accent":  (ACCENT, "#fff", ACCENT),
        "danger":  (RED, "#fff", RED),
    }
    bg, fg, bd = colors.get(style, colors["default"])
    b = tk.Button(parent, text=text, command=command,
                  bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
                  disabledforeground=fg,
                  relief="flat", bd=0, padx=16, pady=6,
                  font=(_FNT, 10), cursor="hand2",
                  highlightthickness=1, highlightbackground=bd)
    return b


# ═══════════════════════════════════════════════════════════════════════════
# WizardController
# ═══════════════════════════════════════════════════════════════════════════

class WizardController:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.state = default_wizard_state()
        self.completed = False
        self._busy = False  # blocks nav during background ops
        self.pages = []
        self.current = 0

        root.title("PingWatch Setup")
        root.configure(bg=BG)
        root.geometry("760x740")
        root.minsize(700, 640)
        root.resizable(True, True)

        # Window icon (title bar + taskbar)
        _ico = os.path.join(_BASE, "frontend", "favicon.ico")
        if os.path.isfile(_ico):
            try:
                root.iconbitmap(_ico)
            except Exception:
                pass

        # ── Header ───────────────────────────────────────────────
        hdr = tk.Frame(root, bg=BG2, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="◉ PingWatch Setup", bg=BG2, fg=TEXT,
                 font=(_FNT, 14, "bold"), padx=16).pack(side="left")

        # ── Step dots ────────────────────────────────────────────
        self._dot_frame = tk.Frame(root, bg=BG, pady=10)
        self._dot_frame.pack(fill="x")
        self._dots = []
        for i, name in enumerate(_STEPS):
            f = tk.Frame(self._dot_frame, bg=BG)
            f.pack(side="left", expand=True)
            dot = tk.Label(f, text="●", bg=BG, fg=TEXT3, font=(_FNT, 10))
            dot.pack()
            lbl = tk.Label(f, text=name, bg=BG, fg=TEXT3, font=(_FNT, 8))
            lbl.pack()
            self._dots.append((dot, lbl))

        # ── Content frame ────────────────────────────────────────
        self._content = tk.Frame(root, bg=BG)
        self._content.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        # ── Navigation bar ───────────────────────────────────────
        nav = tk.Frame(root, bg=BG, pady=10)
        nav.pack(fill="x", padx=24)
        self.btn_cancel = _btn(nav, "Cancel", self._on_cancel)
        self.btn_cancel.pack(side="left")
        self.btn_finish = _btn(nav, "Finish", self._on_finish, "accent")
        self.btn_finish.pack(side="right")
        self.btn_next = _btn(nav, "Next →", self._on_next, "accent")
        self.btn_next.pack(side="right", padx=(0, 8))
        self.btn_back = _btn(nav, "← Back", self._on_back)
        self.btn_back.pack(side="right", padx=(0, 8))

        root.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # ── Page management ──────────────────────────────────────────
    def add_page(self, page_cls):
        page = page_cls(self._content, self)
        # Don't place yet — show_page() will place the active page
        self.pages.append(page)

    def show_page(self, idx):
        if idx < 0 or idx >= len(self.pages):
            return
        self.pages[self.current].place_forget()
        self.current = idx
        self.pages[idx].place(x=0, y=0, relwidth=1, relheight=1)
        self.pages[idx].on_enter()
        self._update_chrome()

    def _update_chrome(self):
        is_first = (self.current == 0)
        is_last = (self.current == len(self.pages) - 1)
        self.btn_back.config(state="normal" if not is_first and not self._busy else "disabled")
        self.btn_next.pack_forget() if is_last else self.btn_next.pack(side="right", padx=(0, 8))
        self.btn_finish.pack_forget() if not is_last else self.btn_finish.pack(side="right")
        self.btn_next.config(state="normal" if not self._busy else "disabled")
        self.btn_finish.config(state="normal" if not self._busy else "disabled")
        for i, (dot, lbl) in enumerate(self._dots):
            if i < self.current:
                dot.config(fg=GREEN)
                lbl.config(fg=GREEN)
            elif i == self.current:
                dot.config(fg=ACCENT)
                lbl.config(fg=ACCENT)
            else:
                dot.config(fg=TEXT3)
                lbl.config(fg=TEXT3)

    def set_busy(self, busy):
        self._busy = busy
        self._update_chrome()

    # ── Navigation callbacks ─────────────────────────────────────
    def _on_back(self):
        if self._busy:
            return
        self.pages[self.current].on_leave()
        self.show_page(self.current - 1)

    def _on_next(self):
        if self._busy:
            return
        page = self.pages[self.current]
        if not page.validate():
            return
        page.on_leave()
        self.show_page(self.current + 1)

    def _on_finish(self):
        if self._busy:
            return
        page = self.pages[self.current]
        if hasattr(page, "do_finish"):
            page.do_finish()

    def _on_cancel(self):
        self.completed = False
        self.root.destroy()

    def finish_ok(self):
        self.completed = True
        self.root.destroy()


# ═══════════════════════════════════════════════════════════════════════════
# Pages
# ═══════════════════════════════════════════════════════════════════════════

class WizardPage(tk.Frame):
    """Base class for wizard pages."""
    def __init__(self, parent, ctrl):
        super().__init__(parent, bg=BG)
        self.ctrl = ctrl

    def on_enter(self):
        pass

    def on_leave(self):
        pass

    def validate(self) -> bool:
        return True


# ── 1. Welcome ──────────────────────────────────────────────────────────────

class WelcomePage(WizardPage):
    def __init__(self, parent, ctrl):
        super().__init__(parent, ctrl)
        _label(self, "Welcome to PingWatch", size=18, bold=True).pack(pady=(30, 8))
        _label(self, "Network Monitoring Made Simple", size=12, color=TEXT2).pack()
        _label(self, "", size=6).pack()  # spacer
        info = (
            "This wizard will guide you through the initial setup:\n\n"
            "  ●  Check and install required packages\n"
            "  ●  Choose your database backend\n"
            "  ●  Configure network ports and TLS\n"
            "  ●  Create your administrator account\n\n"
            "You can re-run this wizard later with  --setup"
        )
        _label(self, info, size=11, color=TEXT2, justify="left",
               anchor="w", wraplength=500).pack(fill="x", pady=10)


# ── 2. Packages ─────────────────────────────────────────────────────────────

class PackagesPage(WizardPage):
    def __init__(self, parent, ctrl):
        super().__init__(parent, ctrl)
        _label(self, "Package Check", size=14, bold=True).pack(anchor="w", pady=(8, 4))
        _label(self, "Verifying required and optional dependencies",
               size=10, color=TEXT2).pack(anchor="w")

        # Scrollable container for package rows
        self._canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self._scrollbar = tk.Scrollbar(self, orient="vertical",
                                       command=self._canvas.yview,
                                       bg=BG3, troughcolor=BG2)
        self._rows_frame = tk.Frame(self._canvas, bg=BG)
        self._rows_frame.bind("<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas_win = self._canvas.create_window((0, 0), window=self._rows_frame, anchor="nw")
        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        self._canvas.bind("<Configure>",
            lambda e: self._canvas.itemconfig(self._canvas_win, width=e.width))
        self._scrollbar.pack(side="right", fill="y", pady=10)
        self._canvas.pack(side="left", fill="both", expand=True, pady=10)
        # Mouse wheel scrolling
        def _on_mousewheel(e):
            self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        self._canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self._pkg_widgets = {}
        self._checked = False

    def on_enter(self):
        if not self._checked:
            self._build_rows()
            self._check_all()

    def _build_rows(self):
        for w in self._rows_frame.winfo_children():
            w.destroy()
        self._pkg_widgets = {}
        for i, pkg in enumerate(PACKAGES):
            row = tk.Frame(self._rows_frame, bg=BG2 if i % 2 == 0 else BG,
                           pady=4, padx=8)
            row.pack(fill="x")
            icon = tk.Label(row, text="…", bg=row["bg"], fg=TEXT3,
                            font=(_MONO, 12), width=2)
            icon.pack(side="left")
            name_lbl = tk.Label(row, text=f"{pkg['name']}",
                                bg=row["bg"], fg=TEXT, font=(_FNT, 10, "bold"))
            name_lbl.pack(side="left", padx=(4, 0))
            desc_lbl = tk.Label(row, text=f"— {pkg['desc']}",
                                bg=row["bg"], fg=TEXT2, font=(_FNT, 10))
            desc_lbl.pack(side="left", padx=(4, 0))
            req_lbl = tk.Label(row, text="(required)" if pkg.get("required") else "",
                               bg=row["bg"], fg=YELLOW if pkg.get("required") else TEXT3,
                               font=(_FNT, 9))
            req_lbl.pack(side="left", padx=(4, 0))
            btn = _btn(row, "Install", lambda p=pkg: self._install_pkg(p), "accent")
            btn.pack(side="right")
            btn.pack_forget()  # hidden until needed
            self._pkg_widgets[pkg["import"]] = {"icon": icon, "btn": btn, "row": row}

        # System tools (not pip-installable)
        n = len(PACKAGES)
        _snmp_hint = (
            "Windows: choco install net-snmp  or  winget install net-snmp.net-snmp\n"
            "Linux: sudo apt install snmp  or  sudo dnf install net-snmp-utils\n"
            "macOS: brew install net-snmp\n"
            "Download: https://sourceforge.net/projects/net-snmp/files/net-snmp/"
        )
        self._add_tool_row(n,     "_snmpget", "snmpget",
                           "SNMP sensor polling (net-snmp)", hint=_snmp_hint,
                           install_fn=install_snmpget)
        _ping_hint = (
            "Windows: ping is built-in — check your PATH\n"
            "Linux: sudo apt install iputils-ping"
        )
        self._add_tool_row(n + 1, "_ping",    "ping",
                           "ICMP ping sensor", hint=_ping_hint)

    def _add_tool_row(self, idx, key, name, desc, hint="", install_fn=None):
        """Add a system tool row (snmpget, ping) with optional install button."""
        outer = tk.Frame(self._rows_frame, bg=BG2 if idx % 2 == 0 else BG)
        outer.pack(fill="x")
        row = tk.Frame(outer, bg=outer["bg"], pady=4, padx=8)
        row.pack(fill="x")
        icon = tk.Label(row, text="…", bg=row["bg"], fg=TEXT3,
                        font=(_MONO, 12), width=2)
        icon.pack(side="left")
        tk.Label(row, text=name, bg=row["bg"], fg=TEXT,
                 font=(_FNT, 10, "bold")).pack(side="left", padx=(4, 0))
        tk.Label(row, text=f"— {desc}", bg=row["bg"], fg=TEXT2,
                 font=(_FNT, 10)).pack(side="left", padx=(4, 0))
        # Install button (hidden until tool is missing and install_fn provided)
        btn = None
        if install_fn:
            btn = _btn(row, "Install", lambda: self._install_tool(key, install_fn),
                       "accent")
        # Hint — selectable Text widget so users can copy commands
        hint_w = None
        if hint:
            lines = hint.count("\n") + 1
            hint_w = tk.Text(outer, bg=outer["bg"], fg=TEXT3, font=(_MONO, 9),
                             height=lines, relief="flat", bd=0, padx=32,
                             highlightthickness=0, wrap="word", cursor="arrow")
            hint_w.insert("1.0", hint)
            hint_w.config(state="disabled")  # read-only but selectable
        self._pkg_widgets[key] = {"icon": icon, "btn": btn, "row": row,
                                  "hint": hint_w}

    def _install_tool(self, key, install_fn):
        """Run a system tool installer in a background thread."""
        w = self._pkg_widgets[key]
        if w["btn"]:
            w["btn"].config(state="disabled", text="Installing…")
        w["icon"].config(text="⟳", fg=YELLOW)
        self.ctrl.set_busy(True)

        def _worker():
            ok, msg = install_fn()
            self.ctrl.root.after(0, lambda: self._tool_install_done(key, ok, msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _tool_install_done(self, key, ok, msg):
        w = self._pkg_widgets[key]
        self.ctrl.set_busy(False)
        check_fn = check_snmpget if key == "_snmpget" else check_ping
        if ok or check_fn():
            w["icon"].config(text="✓", fg=GREEN)
            if w["btn"]:
                w["btn"].pack_forget()
            if w.get("hint"):
                w["hint"].pack_forget()
            if w.get("err_lbl"):
                w["err_lbl"].pack_forget()
        else:
            w["icon"].config(text="✗", fg=YELLOW)
            if w["btn"]:
                w["btn"].config(state="normal", text="Retry")
            # Show error message
            if not w.get("err_lbl"):
                parent = w["row"].master  # outer frame
                w["err_lbl"] = tk.Label(parent, text="", bg=parent["bg"], fg=RED,
                                        font=(_FNT, 9), anchor="w", padx=32)
            w["err_lbl"].config(text=f"Install failed: {msg}")
            w["err_lbl"].pack(fill="x")

    def _check_all(self):
        self._checked = True
        # Python packages
        for pkg in PACKAGES:
            ok = check_import(pkg["import"])
            w = self._pkg_widgets[pkg["import"]]
            if ok:
                w["icon"].config(text="✓", fg=GREEN)
            else:
                w["icon"].config(text="✗", fg=RED)
                if pkg.get("pip"):
                    w["btn"].pack(side="right")
        # System tools
        for key, check_fn in [("_snmpget", check_snmpget), ("_ping", check_ping)]:
            if key in self._pkg_widgets:
                w = self._pkg_widgets[key]
                if check_fn():
                    w["icon"].config(text="✓", fg=GREEN)
                else:
                    w["icon"].config(text="✗", fg=YELLOW)
                    if w.get("btn"):
                        w["btn"].pack(side="right")
                    if w.get("hint"):
                        w["hint"].pack(fill="x", pady=(0, 4))

    def _install_pkg(self, pkg):
        w = self._pkg_widgets[pkg["import"]]
        w["btn"].config(state="disabled", text="Installing…")
        w["icon"].config(text="⟳", fg=YELLOW)
        self.ctrl.set_busy(True)

        def _worker():
            ok, err = pip_install(pkg["install"])
            self.ctrl.root.after(0, lambda: self._install_done(pkg, ok, err))

        threading.Thread(target=_worker, daemon=True).start()

    def _install_done(self, pkg, ok, err):
        w = self._pkg_widgets[pkg["import"]]
        self.ctrl.set_busy(False)
        if ok or check_import(pkg["import"]):
            w["icon"].config(text="✓", fg=GREEN)
            w["btn"].pack_forget()
            if w.get("err_lbl"):
                w["err_lbl"].pack_forget()
        else:
            w["icon"].config(text="✗", fg=RED)
            w["btn"].config(state="normal", text="Retry")
            # Show error message
            if not w.get("err_lbl"):
                parent = w["row"].master
                w["err_lbl"] = tk.Label(parent, text="", bg=parent["bg"], fg=RED,
                                        font=(_FNT, 9), anchor="w", padx=32,
                                        wraplength=600, justify="left")
            # Truncate long pip errors to first meaningful line
            short_err = (err or "Unknown error").strip().splitlines()[-1][:200]
            w["err_lbl"].config(text=f"Install failed: {short_err}")
            w["err_lbl"].pack(fill="x")

    def validate(self) -> bool:
        for pkg in PACKAGES:
            if pkg.get("required") and not check_import(pkg["import"]):
                w = self._pkg_widgets[pkg["import"]]
                w["icon"].config(fg=RED)
                return False
        return True


# ── 3. Database ─────────────────────────────────────────────────────────────

class DatabasePage(WizardPage):
    def __init__(self, parent, ctrl):
        super().__init__(parent, ctrl)
        _label(self, "Database Backend", size=14, bold=True).pack(anchor="w", pady=(8, 4))
        _label(self, "Choose where PingWatch stores its data",
               size=10, color=TEXT2).pack(anchor="w", pady=(0, 12))

        self._choice = tk.StringVar(value="sqlite")

        # ── SQLite card ──────────────────────────────────────────
        f1 = tk.Frame(self, bg=BG2, highlightthickness=1,
                      highlightbackground=BORDER, padx=12, pady=10)
        f1.pack(fill="x", pady=4)
        tk.Radiobutton(f1, text="SQLite — Zero configuration",
                       variable=self._choice, value="sqlite",
                       bg=BG2, fg=TEXT, selectcolor=BG3,
                       activebackground=BG2, activeforeground=TEXT,
                       font=(_FNT, 11, "bold"),
                       command=self._on_choice).pack(anchor="w")
        tk.Label(f1, text="Data stored locally. Best for single-server deployments.",
                 bg=BG2, fg=TEXT2, font=(_FNT, 10)).pack(anchor="w", padx=(20, 0))

        # ── PostgreSQL card ──────────────────────────────────────
        f2 = tk.Frame(self, bg=BG2, highlightthickness=1,
                      highlightbackground=BORDER, padx=12, pady=10)
        f2.pack(fill="x", pady=4)
        tk.Radiobutton(f2, text="PostgreSQL — External database server",
                       variable=self._choice, value="postgresql",
                       bg=BG2, fg=TEXT, selectcolor=BG3,
                       activebackground=BG2, activeforeground=TEXT,
                       font=(_FNT, 11, "bold"),
                       command=self._on_choice).pack(anchor="w")
        tk.Label(f2, text="Best for production environments.",
                 bg=BG2, fg=TEXT2, font=(_FNT, 10)).pack(anchor="w", padx=(20, 0))

        # ── PG connection form (hidden by default) ───────────────
        self._pg_frame = tk.Frame(self, bg=BG)
        fields = [
            ("Host", "pg_host"), ("Port", "pg_port"),
            ("Database", "pg_database"), ("User", "pg_user"),
            ("Password", "pg_password"),
        ]
        self._pg_entries = {}
        for label_text, key in fields:
            row = tk.Frame(self._pg_frame, bg=BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label_text, bg=BG, fg=TEXT2, font=(_FNT, 10),
                     width=10, anchor="e").pack(side="left")
            e = _entry(row, show="*" if key == "pg_password" else "")
            e.insert(0, str(self.ctrl.state.get(key, "")))
            e.pack(side="left", fill="x", expand=True, padx=(6, 0))
            self._pg_entries[key] = e

        btn_row = tk.Frame(self._pg_frame, bg=BG)
        btn_row.pack(fill="x", pady=(8, 0))
        self._test_btn = _btn(btn_row, "Test Connection", self._test_connection, "accent")
        self._test_btn.pack(side="left")
        self._test_lbl = tk.Label(btn_row, text="", bg=BG, fg=TEXT2,
                                  font=(_FNT, 10))
        self._test_lbl.pack(side="left", padx=8)

    def on_enter(self):
        self._choice.set(self.ctrl.state.get("db_backend", "sqlite"))
        self._on_choice()

    def _on_choice(self):
        if self._choice.get() == "postgresql":
            self._pg_frame.pack(fill="x", pady=(8, 0))
        else:
            self._pg_frame.pack_forget()

    def _test_connection(self):
        self._test_btn.config(state="disabled", text="Testing…")
        self._test_lbl.config(text="", fg=TEXT2)
        self.ctrl.set_busy(True)
        vals = {k: e.get() for k, e in self._pg_entries.items()}

        def _worker():
            ok, msg = test_pg_connection(
                vals["pg_host"], vals["pg_port"],
                vals["pg_database"], vals["pg_user"], vals["pg_password"])
            self.ctrl.root.after(0, lambda: self._test_done(ok, msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _test_done(self, ok, msg):
        self.ctrl.set_busy(False)
        self._test_btn.config(state="normal", text="Test Connection")
        if ok:
            self._test_lbl.config(text=f"✓ {msg}", fg=GREEN)
        else:
            self._test_lbl.config(text=f"✗ {msg}", fg=RED)

    def on_leave(self):
        self.ctrl.state["db_backend"] = self._choice.get()
        if self._choice.get() == "postgresql":
            for k, e in self._pg_entries.items():
                self.ctrl.state[k] = e.get()

    def validate(self) -> bool:
        return True


# ── 4. Network ──────────────────────────────────────────────────────────────

class NetworkPage(WizardPage):
    def __init__(self, parent, ctrl):
        super().__init__(parent, ctrl)
        _label(self, "Network Configuration", size=14, bold=True).pack(anchor="w", pady=(8, 4))
        _label(self, "Configure ports and TLS encryption",
               size=10, color=TEXT2).pack(anchor="w", pady=(0, 10))

        # ── Port fields ──────────────────────────────────────────
        ports_f = tk.Frame(self, bg=BG)
        ports_f.pack(fill="x")

        self._port_entries = {}
        self._port_status = {}
        for label_text, key, default in [
            ("HTTP Port", "http_port", "7070"),
            ("HTTPS Port", "tls_port", "8443"),
            ("SNMP Trap Port", "snmp_port", "162"),
        ]:
            row = tk.Frame(ports_f, bg=BG)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label_text, bg=BG, fg=TEXT2,
                     font=(_FNT, 10), width=14, anchor="e").pack(side="left")
            e = _entry(row, width=8)
            e.insert(0, str(ctrl.state.get(key, default)))
            e.pack(side="left", padx=(6, 0))
            self._port_entries[key] = e
            status = tk.Label(row, text="", bg=BG, fg=TEXT3, font=(_FNT, 9))
            status.pack(side="left", padx=6)
            self._port_status[key] = status

        # ── TLS section ──────────────────────────────────────────
        _label(self, "", size=4).pack()  # spacer
        _label(self, "TLS / HTTPS", size=12, bold=True).pack(anchor="w")

        self._tls_enabled = tk.BooleanVar(value=True)
        tk.Checkbutton(self, text="Enable HTTPS (recommended)",
                       variable=self._tls_enabled,
                       bg=BG, fg=TEXT, selectcolor=BG3,
                       activebackground=BG, activeforeground=TEXT,
                       font=(_FNT, 10)).pack(anchor="w", pady=(4, 0))

        self._tls_mode = tk.StringVar(value="generate")
        modes_f = tk.Frame(self, bg=BG)
        modes_f.pack(anchor="w", padx=(20, 0), pady=4)
        tk.Radiobutton(modes_f, text="Generate self-signed certificate",
                       variable=self._tls_mode, value="generate",
                       bg=BG, fg=TEXT2, selectcolor=BG3,
                       activebackground=BG, activeforeground=TEXT2,
                       font=(_FNT, 10)).pack(anchor="w")
        tk.Radiobutton(modes_f, text="Import existing certificate",
                       variable=self._tls_mode, value="import",
                       bg=BG, fg=TEXT2, selectcolor=BG3,
                       activebackground=BG, activeforeground=TEXT2,
                       font=(_FNT, 10)).pack(anchor="w")

        # ── Organization name ────────────────────────────────────
        org_f = tk.Frame(self, bg=BG)
        org_f.pack(fill="x", pady=(8, 0))
        tk.Label(org_f, text="Organization", bg=BG, fg=TEXT2,
                 font=(_FNT, 10), width=14, anchor="e").pack(side="left")
        self._org_entry = _entry(org_f)
        self._org_entry.insert(0, ctrl.state.get("org_name", "PingWatch"))
        self._org_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))

    def on_enter(self):
        # Check port availability
        for key, e in self._port_entries.items():
            try:
                p = int(e.get())
                pid = port_in_use(p)
                lbl = self._port_status[key]
                if pid is None:
                    lbl.config(text="✓ Available", fg=GREEN)
                else:
                    lbl.config(text="✗ In use", fg=RED)
            except ValueError:
                self._port_status[key].config(text="Invalid", fg=RED)

    def on_leave(self):
        for key, e in self._port_entries.items():
            try:
                self.ctrl.state[key] = int(e.get())
            except ValueError:
                pass
        self.ctrl.state["tls_enabled"] = self._tls_enabled.get()
        self.ctrl.state["tls_cert_source"] = "generated" if self._tls_mode.get() == "generate" else "imported"
        self.ctrl.state["org_name"] = self._org_entry.get().strip() or "PingWatch"

    def validate(self) -> bool:
        for key, e in self._port_entries.items():
            try:
                p = int(e.get())
                if not (1 <= p <= 65535):
                    raise ValueError
            except ValueError:
                self._port_status[key].config(text="Invalid port", fg=RED)
                return False
        return True


# ── 5. Security ─────────────────────────────────────────────────────────────

class SecurityPage(WizardPage):
    def __init__(self, parent, ctrl):
        super().__init__(parent, ctrl)
        _label(self, "Administrator Account", size=14, bold=True).pack(anchor="w", pady=(8, 4))
        _label(self, "Create the initial admin user for the web dashboard",
               size=10, color=TEXT2).pack(anchor="w", pady=(0, 16))

        fields_f = tk.Frame(self, bg=BG)
        fields_f.pack(fill="x")

        # Username
        row1 = tk.Frame(fields_f, bg=BG)
        row1.pack(fill="x", pady=4)
        tk.Label(row1, text="Username", bg=BG, fg=TEXT2,
                 font=(_FNT, 10), width=16, anchor="e").pack(side="left")
        self._user_entry = _entry(row1)
        self._user_entry.insert(0, "admin")
        self._user_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))

        # Password
        row2 = tk.Frame(fields_f, bg=BG)
        row2.pack(fill="x", pady=4)
        tk.Label(row2, text="Password", bg=BG, fg=TEXT2,
                 font=(_FNT, 10), width=16, anchor="e").pack(side="left")
        self._pass_entry = _entry(row2, show="●")
        self._pass_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))

        # Confirm
        row3 = tk.Frame(fields_f, bg=BG)
        row3.pack(fill="x", pady=4)
        tk.Label(row3, text="Confirm Password", bg=BG, fg=TEXT2,
                 font=(_FNT, 10), width=16, anchor="e").pack(side="left")
        self._pass2_entry = _entry(row3, show="●")
        self._pass2_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))

        self._err_lbl = tk.Label(self, text="", bg=BG, fg=RED, font=(_FNT, 10))
        self._err_lbl.pack(anchor="w", pady=(8, 0))

    def on_leave(self):
        self.ctrl.state["admin_user"] = self._user_entry.get().strip()
        self.ctrl.state["admin_pass"] = self._pass_entry.get()

    def validate(self) -> bool:
        user = self._user_entry.get().strip()
        pw = self._pass_entry.get()
        pw2 = self._pass2_entry.get()
        if not user:
            self._err_lbl.config(text="Username is required")
            return False
        if len(pw) < 6:
            self._err_lbl.config(text="Password must be at least 6 characters")
            return False
        if pw != pw2:
            self._err_lbl.config(text="Passwords do not match")
            return False
        self._err_lbl.config(text="")
        return True


# ── 6. System (Firewall + Shortcut + Auto-Start) ───────────────────────────

class SystemPage(WizardPage):
    def __init__(self, parent, ctrl):
        super().__init__(parent, ctrl)
        _label(self, "System Integration", size=14, bold=True).pack(anchor="w", pady=(8, 4))
        _label(self, "Firewall rules, desktop shortcut, and auto-start",
               size=10, color=TEXT2).pack(anchor="w", pady=(0, 12))

        self._rows = {}

        # ── Firewall section ─────────────────────────────────────
        if sys.platform == "win32":
            _label(self, "Windows Firewall", size=11, bold=True,
                   color=TEXT2).pack(anchor="w", pady=(4, 2))
            self._fw_frame = tk.Frame(self, bg=BG)
            self._fw_frame.pack(fill="x", pady=(0, 8))

        # ── Desktop shortcut ─────────────────────────────────────
        sc_row = tk.Frame(self, bg=BG2, padx=8, pady=6)
        sc_row.pack(fill="x", pady=2)
        self._sc_icon = tk.Label(sc_row, text="○", bg=BG2, fg=TEXT3,
                                 font=(_MONO, 12), width=2)
        self._sc_icon.pack(side="left")
        tk.Label(sc_row, text="Desktop Shortcut", bg=BG2, fg=TEXT,
                 font=(_FNT, 10, "bold")).pack(side="left", padx=(4, 0))
        tk.Label(sc_row, text="— PingWatch.lnk on your desktop",
                 bg=BG2, fg=TEXT2, font=(_FNT, 10)).pack(side="left", padx=(4, 0))
        self._sc_btn = _btn(sc_row, "Create", self._create_shortcut, "accent")
        self._sc_btn.pack(side="right")
        self._sc_status = tk.Label(sc_row, text="", bg=BG2, fg=TEXT3,
                                   font=(_FNT, 9))
        self._sc_status.pack(side="right", padx=(0, 8))

        # ── Auto-start ───────────────────────────────────────────
        if sys.platform == "win32":
            _label(self, "", size=4).pack()
            _label(self, "Auto-Start", size=11, bold=True,
                   color=TEXT2).pack(anchor="w", pady=(4, 2))

            as_row = tk.Frame(self, bg=BG, padx=8, pady=4)
            as_row.pack(fill="x")
            self._as_mode = tk.StringVar(value="system")
            tk.Radiobutton(as_row, text="Start at boot (SYSTEM, headless — no login required)",
                           variable=self._as_mode, value="system",
                           bg=BG, fg=TEXT2, selectcolor=BG3,
                           activebackground=BG, activeforeground=TEXT2,
                           font=(_FNT, 10)).pack(anchor="w")
            tk.Radiobutton(as_row, text="Start at logon (current user — tray icon works)",
                           variable=self._as_mode, value="user",
                           bg=BG, fg=TEXT2, selectcolor=BG3,
                           activebackground=BG, activeforeground=TEXT2,
                           font=(_FNT, 10)).pack(anchor="w")

            btn_row = tk.Frame(self, bg=BG, padx=8)
            btn_row.pack(fill="x", pady=(4, 0))
            self._as_icon = tk.Label(btn_row, text="", bg=BG, fg=TEXT3,
                                     font=(_MONO, 12), width=2)
            self._as_btn = _btn(btn_row, "Install Task", self._install_task, "accent")
            self._as_btn.pack(side="left", padx=(4, 0))
            self._as_status = tk.Label(btn_row, text="", bg=BG, fg=TEXT3,
                                       font=(_FNT, 9))
            self._as_status.pack(side="left", padx=8)

    def on_enter(self):
        if sys.platform == "win32":
            self._check_firewall()
            self._check_task()
        self._check_shortcut()

    def _check_firewall(self):
        for w in self._fw_frame.winfo_children():
            w.destroy()
        rules = get_firewall_rules(self.ctrl.state)
        for i, (proto, port, name) in enumerate(rules):
            row = tk.Frame(self._fw_frame, bg=BG2 if i % 2 == 0 else BG,
                           padx=8, pady=3)
            row.pack(fill="x")
            exists = win_firewall_check(name)
            icon = tk.Label(row, text="✓" if exists else "✗",
                            bg=row["bg"], fg=GREEN if exists else YELLOW,
                            font=(_MONO, 12), width=2)
            icon.pack(side="left")
            tk.Label(row, text=f"{proto} {port}", bg=row["bg"], fg=TEXT,
                     font=(_MONO, 10)).pack(side="left", padx=(4, 0))
            tk.Label(row, text=f"— {name}", bg=row["bg"], fg=TEXT2,
                     font=(_FNT, 10)).pack(side="left", padx=(4, 0))
            if not exists:
                btn = _btn(row, "Add Rule",
                           lambda n=name, p=proto, pt=port, r=row, ic=icon:
                               self._add_fw_rule(n, p, pt, r, ic),
                           "accent")
                btn.pack(side="right")

    def _add_fw_rule(self, name, proto, port, row, icon):
        ok, msg = win_firewall_add(name, proto, port)
        if ok:
            icon.config(text="✓", fg=GREEN)
            # Remove the Add Rule button
            for w in row.winfo_children():
                if isinstance(w, tk.Button):
                    w.pack_forget()
        else:
            icon.config(text="✗", fg=RED)

    def _check_shortcut(self):
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        exists = os.path.isfile(os.path.join(desktop, "PingWatch.lnk"))
        if exists:
            self._sc_icon.config(text="✓", fg=GREEN)
            self._sc_btn.pack_forget()
            self._sc_status.config(text="Already exists", fg=GREEN)

    def _create_shortcut(self):
        self._sc_btn.config(state="disabled", text="Creating…")
        ok, msg = win_create_shortcut()
        if ok:
            self._sc_icon.config(text="✓", fg=GREEN)
            self._sc_btn.pack_forget()
            self._sc_status.config(text=msg, fg=GREEN)
        else:
            self._sc_btn.config(state="normal", text="Retry")
            self._sc_status.config(text=msg, fg=RED)

    def _check_task(self):
        if win_task_exists():
            self._as_icon.config(text="✓", fg=GREEN)
            self._as_status.config(text="Task already installed", fg=GREEN)
            self._as_btn.config(text="Reinstall")

    def _install_task(self):
        self._as_btn.config(state="disabled", text="Installing…")
        self._as_status.config(text="", fg=TEXT3)
        self.ctrl.set_busy(True)
        as_system = (self._as_mode.get() == "system")

        def _worker():
            ok, msg = win_install_task(as_system=as_system)
            self.ctrl.root.after(0, lambda: self._task_done(ok, msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _task_done(self, ok, msg):
        self.ctrl.set_busy(False)
        if ok:
            self._as_icon.config(text="✓", fg=GREEN)
            self._as_btn.config(state="normal", text="Reinstall")
            self._as_status.config(text=msg, fg=GREEN)
        else:
            self._as_icon.config(text="✗", fg=RED)
            self._as_btn.config(state="normal", text="Retry")
            self._as_status.config(text=msg, fg=RED)


# ── 7. Summary ──────────────────────────────────────────────────────────────

class SummaryPage(WizardPage):
    def __init__(self, parent, ctrl):
        super().__init__(parent, ctrl)
        _label(self, "Review & Finish", size=14, bold=True).pack(anchor="w", pady=(8, 4))
        _label(self, "Review your settings and click Finish to complete setup",
               size=10, color=TEXT2).pack(anchor="w", pady=(0, 10))

        self._summary_frame = tk.Frame(self, bg=BG2, highlightthickness=1,
                                       highlightbackground=BORDER, padx=12, pady=10)
        self._summary_frame.pack(fill="both", expand=True)

        self._status_lbl = tk.Label(self, text="", bg=BG, fg=TEXT2,
                                    font=(_FNT, 10))
        self._status_lbl.pack(pady=(8, 0))

        self._progress = ttk.Progressbar(self, mode="indeterminate", length=300)
        # Don't pack yet — shown during install

    def on_enter(self):
        # Rebuild summary
        for w in self._summary_frame.winfo_children():
            w.destroy()
        st = self.ctrl.state
        rows = [
            ("Database", st["db_backend"].title()),
            ("HTTP Port", str(st.get("http_port", 7070))),
            ("HTTPS", "Enabled" if st.get("tls_enabled") else "Disabled"),
            ("HTTPS Port", str(st.get("tls_port", 8443)) if st.get("tls_enabled") else "—"),
            ("SNMP Port", str(st.get("snmp_port", 162))),
            ("Organization", st.get("org_name", "PingWatch")),
            ("Admin User", st.get("admin_user", "admin")),
        ]
        if st["db_backend"] == "postgresql":
            rows.insert(1, ("PG Host", f"{st.get('pg_host', 'localhost')}:{st.get('pg_port', 5432)}"))
            rows.insert(2, ("PG Database", st.get("pg_database", "pingwatch")))

        for i, (label, value) in enumerate(rows):
            bg = BG2 if i % 2 == 0 else BG3
            row = tk.Frame(self._summary_frame, bg=bg, padx=8, pady=4)
            row.pack(fill="x")
            tk.Label(row, text=label, bg=bg, fg=TEXT2,
                     font=(_FNT, 10), width=16, anchor="e").pack(side="left")
            tk.Label(row, text=value, bg=bg, fg=TEXT,
                     font=(_FNT, 10, "bold")).pack(side="left", padx=(8, 0))

    def do_finish(self):
        """Called by Finish button — runs DB init in background."""
        self.ctrl.set_busy(True)
        self._status_lbl.config(text="Initializing…", fg=YELLOW)
        self._progress.pack(pady=(4, 0))
        self._progress.start(15)

        def _worker():
            ok, err = initialize_database(
                self.ctrl.state,
                progress_cb=lambda i, msg: self.ctrl.root.after(
                    0, lambda m=msg: self._status_lbl.config(text=m)),
            )
            self.ctrl.root.after(0, lambda: self._finish_done(ok, err))

        threading.Thread(target=_worker, daemon=True).start()

    def _finish_done(self, ok, err):
        self._progress.stop()
        self._progress.pack_forget()
        self.ctrl.set_busy(False)
        if ok:
            self._status_lbl.config(text="✓ Setup complete!", fg=GREEN)
            self.ctrl.root.after(800, self.ctrl.finish_ok)
        else:
            self._status_lbl.config(text=f"✗ {err}", fg=RED)


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

def run_wizard() -> bool:
    """Launch the setup wizard.  Returns True if completed, False if cancelled."""
    root = tk.Tk()

    # Apply ttk dark theme
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TProgressbar", background=ACCENT,
                    troughcolor=BG3, borderwidth=0)

    ctrl = WizardController(root)
    ctrl.add_page(WelcomePage)
    ctrl.add_page(PackagesPage)
    ctrl.add_page(DatabasePage)
    ctrl.add_page(NetworkPage)
    ctrl.add_page(SecurityPage)
    ctrl.add_page(SystemPage)
    ctrl.add_page(SummaryPage)
    ctrl.show_page(0)

    # Center on screen
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (w // 2)
    y = (root.winfo_screenheight() // 2) - (h // 2)
    root.geometry(f"+{x}+{y}")

    root.mainloop()
    return ctrl.completed


if __name__ == "__main__":
    ok = run_wizard()
    sys.exit(0 if ok else 1)
