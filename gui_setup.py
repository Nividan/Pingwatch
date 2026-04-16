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
    valid_host, valid_email,
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
BG4    = "#243044"   # elevated surface — hover / active states
BORDER = "#30363d"
TEXT   = "#e6edf3"
TEXT2  = "#8b949e"
TEXT3  = "#484f58"
GREEN  = "#23d18b"
RED    = "#f85149"
YELLOW = "#f0a500"
ACCENT = "#2f81f7"
ACCENT_HOVER = "#388bfd"

_FNT = "Segoe UI" if sys.platform == "win32" else \
       "Helvetica Neue" if sys.platform == "darwin" else "DejaVu Sans"
_MONO = "Consolas" if sys.platform == "win32" else \
        "Menlo" if sys.platform == "darwin" else "DejaVu Sans Mono"

# ── Step names ──────────────────────────────────────────────────────────────
_STEPS = ["Welcome", "Packages", "Database", "Network", "Security",
          "Alerts", "System", "Summary"]

# ── Braille spinner frames (reused across pages) ────────────────────────────
_SPIN_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


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
    # (bg, fg, border, hover_bg)
    colors = {
        "default": (BG3,    TEXT2, BORDER, BG4),
        "accent":  (ACCENT, "#fff", ACCENT, ACCENT_HOVER),
        "danger":  (RED,    "#fff", RED,    "#ff6b63"),
        "ghost":   (BG,     TEXT3, BG,     BG2),
    }
    bg, fg, bd, hover_bg = colors.get(style, colors["default"])
    b = tk.Button(parent, text=text, command=command,
                  bg=bg, fg=fg, activebackground=hover_bg, activeforeground=fg,
                  disabledforeground=fg,
                  relief="flat", bd=0, padx=16, pady=6,
                  font=(_FNT, 10), cursor="hand2",
                  highlightthickness=1, highlightbackground=bd,
                  highlightcolor=bd)
    # Hover feedback — only when button is in its normal state
    def _on_enter(_e, w=b, c=hover_bg):
        if str(w["state"]) == "normal":
            w.config(bg=c)
    def _on_leave(_e, w=b, c=bg):
        if str(w["state"]) == "normal":
            w.config(bg=c)
    b.bind("<Enter>", _on_enter)
    b.bind("<Leave>", _on_leave)
    return b


def _page_title(parent, title, subtitle=None):
    """Render the standard page-title + subtitle stack. Returns (title_lbl, sub_lbl)."""
    t = _label(parent, title, size=16, bold=True)
    t.pack(anchor="w", pady=(8, 2))
    s = None
    if subtitle:
        s = _label(parent, subtitle, size=10, color=TEXT2)
        s.pack(anchor="w", pady=(0, 12))
    return t, s


def _as_pill(label_widget, text, color, bg=BG2):
    """Style an existing Label as an inline tinted pill with the given color."""
    label_widget.config(
        text=f"  {text}  ", fg=color, bg=bg,
        highlightbackground=color, highlightcolor=color,
        highlightthickness=1, bd=0, padx=0, pady=0,
        font=(_FNT, 9, "bold"),
    )


def _start_spinner(root, label_widget, color=YELLOW):
    """Animate a braille spinner on `label_widget`.

    Returns a ``stop()`` callable — invoke it to halt the animation.
    Safe to call when the widget has been destroyed.
    """
    state = {"idx": 0, "after_id": None, "alive": True}

    def tick():
        if not state["alive"]:
            return
        try:
            label_widget.config(text=_SPIN_FRAMES[state["idx"]], fg=color)
        except tk.TclError:
            state["alive"] = False
            return
        state["idx"] = (state["idx"] + 1) % len(_SPIN_FRAMES)
        state["after_id"] = root.after(80, tick)

    def stop():
        state["alive"] = False
        if state["after_id"]:
            try:
                root.after_cancel(state["after_id"])
            except Exception:
                pass
            state["after_id"] = None

    state["after_id"] = root.after(0, tick)
    return stop


def _bind_row_hover(row, normal_bg, hover_bg):
    """Highlight a row (Frame + its Label children) on mouse hover.

    Uses a deferred-leave trick so that moving the cursor from the row onto
    one of its child labels does NOT cause a flicker back to normal_bg.
    """
    widgets = [row]
    for child in row.winfo_children():
        if isinstance(child, tk.Label):
            widgets.append(child)

    pending = [None]  # after-id of the deferred _set(normal) call

    def _set(bg):
        for w in widgets:
            try:
                w.config(bg=bg)
            except tk.TclError:
                pass

    def _enter(_e):
        if pending[0]:
            try:
                row.after_cancel(pending[0])
            except Exception:
                pass
            pending[0] = None
        _set(hover_bg)

    def _leave(_e):
        if pending[0]:
            try:
                row.after_cancel(pending[0])
            except Exception:
                pass

        def _apply():
            _set(normal_bg)
            pending[0] = None

        pending[0] = row.after(1, _apply)

    for w in widgets:
        w.bind("<Enter>", _enter)
        w.bind("<Leave>", _leave)


def _password_strength(pw):
    """Return a 0.0–1.0 score for the given password. Purely cosmetic."""
    if not pw:
        return 0.0
    score = min(len(pw) / 20.0, 0.6)           # length contributes up to 0.6
    if any(c.isupper() for c in pw) and any(c.islower() for c in pw):
        score += 0.1
    if any(c.isdigit() for c in pw):
        score += 0.1
    if any(not c.isalnum() for c in pw):
        score += 0.1
    if len(pw) >= 12:
        score += 0.1
    return min(score, 1.0)


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
        root.geometry("820x780")
        root.minsize(760, 700)
        root.resizable(True, True)

        # Window icon (title bar + taskbar)
        _ico = os.path.join(_BASE, "frontend", "favicon.ico")
        if os.path.isfile(_ico):
            try:
                root.iconbitmap(_ico)
            except Exception:
                pass

        # ── Header — Ping + Watch wordmark (mirrors gui.py) ──────
        hdr = tk.Frame(root, bg=BG2, height=58)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        logo = tk.Frame(hdr, bg=BG2)
        logo.pack(side="left", padx=(18, 0), pady=8)
        tk.Label(logo, text="●", fg=GREEN, bg=BG2,
                 font=(_FNT, 17)).pack(side="left", padx=(0, 6))
        tk.Label(logo, text="Ping", fg=TEXT, bg=BG2,
                 font=(_FNT, 19, "bold")).pack(side="left")
        tk.Label(logo, text="Watch", fg=ACCENT, bg=BG2,
                 font=(_FNT, 19, "bold")).pack(side="left")
        tk.Label(logo, text="   First-run Setup", fg=TEXT2, bg=BG2,
                 font=(_FNT, 11)).pack(side="left", pady=(4, 0))

        # Thin accent strip underneath header — ties wizard to status window
        tk.Frame(root, bg=ACCENT, height=3).pack(fill="x")

        # ── Step indicator — dots + connecting lines ─────────────
        self._dot_frame = tk.Frame(root, bg=BG, pady=12)
        self._dot_frame.pack(fill="x", padx=24)
        self._dots = []
        self._lines = []
        N = len(_STEPS)
        # Dots live at even columns (weight 0, fixed min); lines at odd (weight 1, expand)
        for i in range(N):
            self._dot_frame.columnconfigure(2 * i, weight=0, minsize=70)
        for i in range(N - 1):
            self._dot_frame.columnconfigure(2 * i + 1, weight=1)
        for i, name in enumerate(_STEPS):
            dot = tk.Label(self._dot_frame, text="○", bg=BG, fg=TEXT2,
                           font=(_FNT, 14))
            dot.grid(row=0, column=2 * i, sticky="n")
            lbl = tk.Label(self._dot_frame, text=name, bg=BG, fg=TEXT2,
                           font=(_FNT, 9))
            lbl.grid(row=1, column=2 * i, pady=(2, 0), sticky="n")
            self._dots.append((dot, lbl))
            if i < N - 1:
                line = tk.Frame(self._dot_frame, bg=BORDER, height=2)
                line.grid(row=0, column=2 * i + 1, sticky="ew",
                          padx=4, pady=(11, 0))
                self._lines.append(line)

        # ── Content frame ────────────────────────────────────────
        self._content = tk.Frame(root, bg=BG)
        self._content.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        # ── Navigation bar — top divider + styled buttons ────────
        tk.Frame(root, bg=BORDER, height=1).pack(fill="x")
        nav = tk.Frame(root, bg=BG, pady=10)
        nav.pack(fill="x", padx=24)
        self.btn_cancel = _btn(nav, "Cancel", self._on_cancel, "ghost")
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
                # Completed — green checkmark
                dot.config(text="✓", fg=GREEN, font=(_FNT, 13, "bold"))
                lbl.config(fg=GREEN)
            elif i == self.current:
                # Current step — filled accent dot
                dot.config(text="●", fg=ACCENT, font=(_FNT, 14))
                lbl.config(fg=ACCENT, font=(_FNT, 9, "bold"))
            else:
                # Pending — hollow dot
                dot.config(text="○", fg=TEXT2, font=(_FNT, 14))
                lbl.config(fg=TEXT2, font=(_FNT, 9))
        # Color connector segments: GREEN where the step before it is completed
        for i, line in enumerate(self._lines):
            line.config(bg=GREEN if i < self.current else BORDER)

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

        # ── Big logomark ─────────────────────────────────────────
        mark = tk.Frame(self, bg=BG)
        mark.pack(pady=(28, 4))
        tk.Label(mark, text="●", fg=GREEN, bg=BG,
                 font=(_FNT, 44)).pack(side="left", padx=(0, 10))
        tk.Label(mark, text="Ping", fg=TEXT, bg=BG,
                 font=(_FNT, 34, "bold")).pack(side="left")
        tk.Label(mark, text="Watch", fg=ACCENT, bg=BG,
                 font=(_FNT, 34, "bold")).pack(side="left")

        _label(self, "Network Monitoring Made Simple", size=12,
               color=TEXT2).pack(pady=(0, 18))

        # ── Feature card panel ───────────────────────────────────
        card = tk.Frame(self, bg=BG2, highlightthickness=1,
                        highlightbackground=BORDER, padx=22, pady=16)
        card.pack(fill="x", padx=40)

        tk.Label(card, text="This wizard will guide you through:",
                 bg=BG2, fg=TEXT, font=(_FNT, 11, "bold"),
                 anchor="w").pack(fill="x", pady=(0, 10))

        for glyph, color, label in [
            ("◉", ACCENT, "Check and install required packages"),
            ("◈", GREEN,  "Choose your database backend"),
            ("⚡", YELLOW, "Configure network ports and TLS"),
            ("⚿", ACCENT, "Create your administrator account"),
        ]:
            row = tk.Frame(card, bg=BG2)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=glyph, bg=BG2, fg=color,
                     font=(_FNT, 13), width=3).pack(side="left")
            tk.Label(row, text=label, bg=BG2, fg=TEXT2,
                     font=(_FNT, 11), anchor="w").pack(side="left")

        _label(self, "You can re-run this wizard later with  --setup",
               size=9, color=TEXT3).pack(pady=(18, 0))


# ── 2. Packages ─────────────────────────────────────────────────────────────

class PackagesPage(WizardPage):
    def __init__(self, parent, ctrl):
        super().__init__(parent, ctrl)
        _page_title(self, "Package Check",
                    "Verifying required and optional dependencies")

        # Scrollable container for package rows
        self._canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self._scrollbar = ttk.Scrollbar(self, orient="vertical",
                                        command=self._canvas.yview)
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
            row_bg = BG2 if i % 2 == 0 else BG
            row = tk.Frame(self._rows_frame, bg=row_bg, pady=5, padx=8)
            row.pack(fill="x")
            icon = tk.Label(row, text="○", bg=row_bg, fg=TEXT2,
                            font=(_MONO, 12), width=3)
            icon.pack(side="left")
            name_lbl = tk.Label(row, text=f"{pkg['name']}",
                                bg=row_bg, fg=TEXT, font=(_FNT, 10, "bold"))
            name_lbl.pack(side="left", padx=(4, 0))
            desc_lbl = tk.Label(row, text=f"— {pkg['desc']}",
                                bg=row_bg, fg=TEXT2, font=(_FNT, 10))
            desc_lbl.pack(side="left", padx=(4, 0))
            # "(required)" tag: informational, use muted TEXT2 bold (not warning-yellow)
            if pkg.get("required"):
                req_lbl = tk.Label(row, text="(required)",
                                   bg=row_bg, fg=TEXT2, font=(_FNT, 9, "bold"))
                req_lbl.pack(side="left", padx=(4, 0))
            btn = _btn(row, "Install", lambda p=pkg: self._install_pkg(p), "accent")
            btn.pack(side="right")
            btn.pack_forget()  # hidden until needed
            self._pkg_widgets[pkg["import"]] = {
                "icon": icon, "btn": btn, "row": row,
                "normal_bg": row_bg, "spin_stop": None,
            }
            _bind_row_hover(row, row_bg, BG3)

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
        row_bg = BG2 if idx % 2 == 0 else BG
        outer = tk.Frame(self._rows_frame, bg=row_bg)
        outer.pack(fill="x")
        row = tk.Frame(outer, bg=row_bg, pady=5, padx=8)
        row.pack(fill="x")
        icon = tk.Label(row, text="○", bg=row_bg, fg=TEXT2,
                        font=(_MONO, 12), width=3)
        icon.pack(side="left")
        tk.Label(row, text=name, bg=row_bg, fg=TEXT,
                 font=(_FNT, 10, "bold")).pack(side="left", padx=(4, 0))
        tk.Label(row, text=f"— {desc}", bg=row_bg, fg=TEXT2,
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
            hint_w = tk.Text(outer, bg=row_bg, fg=TEXT3, font=(_MONO, 9),
                             height=lines, relief="flat", bd=0, padx=32,
                             highlightthickness=0, wrap="word", cursor="arrow")
            hint_w.insert("1.0", hint)
            hint_w.config(state="disabled")  # read-only but selectable
        self._pkg_widgets[key] = {
            "icon": icon, "btn": btn, "row": row, "hint": hint_w,
            "normal_bg": row_bg, "spin_stop": None,
        }
        _bind_row_hover(row, row_bg, BG3)

    def _install_tool(self, key, install_fn):
        """Run a system tool installer in a background thread."""
        w = self._pkg_widgets[key]
        if w["btn"]:
            w["btn"].config(state="disabled", text="Installing…")
        # Animated spinner instead of static glyph
        w["spin_stop"] = _start_spinner(self.ctrl.root, w["icon"], YELLOW)
        self.ctrl.set_busy(True)

        def _worker():
            ok, msg = install_fn()
            self.ctrl.root.after(0, lambda: self._tool_install_done(key, ok, msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _tool_install_done(self, key, ok, msg):
        w = self._pkg_widgets[key]
        if w.get("spin_stop"):
            w["spin_stop"]()
            w["spin_stop"] = None
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
        w["spin_stop"] = _start_spinner(self.ctrl.root, w["icon"], YELLOW)
        self.ctrl.set_busy(True)

        def _worker():
            ok, err = pip_install(pkg["install"])
            self.ctrl.root.after(0, lambda: self._install_done(pkg, ok, err))

        threading.Thread(target=_worker, daemon=True).start()

    def _install_done(self, pkg, ok, err):
        w = self._pkg_widgets[pkg["import"]]
        if w.get("spin_stop"):
            w["spin_stop"]()
            w["spin_stop"] = None
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
        # ── Header row: title + help button ──────────────────────
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", pady=(8, 2))
        _label(hdr, "Database Backend", size=16, bold=True).pack(side="left")
        tk.Button(hdr, text=" ? ", command=self._show_pg_help,
                  bg=BG3, fg=ACCENT, activebackground=BG3, activeforeground=ACCENT,
                  relief="flat", bd=0, padx=5, pady=0,
                  font=(_FNT, 10, "bold"), cursor="hand2",
                  highlightthickness=1, highlightbackground=BORDER
                  ).pack(side="left", padx=(8, 0))
        _label(self, "Choose where PingWatch stores its data",
               size=10, color=TEXT2).pack(anchor="w", pady=(0, 12))

        self._choice = tk.StringVar(value="sqlite")
        self._cards = {}  # value -> card Frame (for selection styling)

        # ── SQLite card ──────────────────────────────────────────
        f1 = self._make_card("sqlite",
                             "SQLite — Zero configuration",
                             "Data stored locally. Best for single-server deployments.")
        # ── PostgreSQL card ──────────────────────────────────────
        f2 = self._make_card("postgresql",
                             "PostgreSQL — External database server",
                             "Best for production environments.")

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

    def _make_card(self, value, title, subtitle):
        """Build a clickable backend-choice card. Selection → accent border."""
        card = tk.Frame(self, bg=BG2, highlightthickness=2,
                        highlightbackground=BORDER, padx=14, pady=12)
        card.pack(fill="x", pady=5)
        rb = tk.Radiobutton(card, text=title,
                            variable=self._choice, value=value,
                            bg=BG2, fg=TEXT, selectcolor=BG3,
                            activebackground=BG2, activeforeground=TEXT,
                            font=(_FNT, 11, "bold"),
                            command=self._on_choice)
        rb.pack(anchor="w")
        sub = tk.Label(card, text=subtitle,
                       bg=BG2, fg=TEXT2, font=(_FNT, 10))
        sub.pack(anchor="w", padx=(22, 0))

        # Make the whole card clickable (not just the radio)
        def _pick(_e=None):
            self._choice.set(value)
            self._on_choice()
        for w in (card, rb, sub):
            w.bind("<Button-1>", _pick)
            w.configure(cursor="hand2")

        self._cards[value] = card
        return card

    def on_enter(self):
        self._choice.set(self.ctrl.state.get("db_backend", "sqlite"))
        self._on_choice()

    def _on_choice(self):
        # Update card borders: selected = ACCENT, others = BORDER
        sel = self._choice.get()
        for val, card in self._cards.items():
            card.config(highlightbackground=ACCENT if val == sel else BORDER)
        # Show/hide PG connection form
        if sel == "postgresql":
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
        color = GREEN if ok else RED
        glyph = "✓" if ok else "✗"
        self._test_lbl.config(text=f"{glyph} {msg}", fg=color,
                              font=(_FNT, 10, "bold"))

    def on_leave(self):
        self.ctrl.state["db_backend"] = self._choice.get()
        if self._choice.get() == "postgresql":
            for k, e in self._pg_entries.items():
                self.ctrl.state[k] = e.get()

    def validate(self) -> bool:
        return True

    def _show_pg_help(self):
        """Open a dark-themed modal with PostgreSQL installation instructions."""
        win = tk.Toplevel(self.ctrl.root)
        win.title("How to Install PostgreSQL")
        win.geometry("560x500")
        win.resizable(False, False)
        win.configure(bg=BG)
        win.grab_set()

        tk.Label(win, text="PostgreSQL Installation Guide",
                 bg=BG, fg=TEXT, font=(_FNT, 13, "bold"),
                 pady=14).pack(fill="x", padx=20, anchor="w")

        # ── Scrollable text area ──────────────────────────────────
        frm = tk.Frame(win, bg=BG2, highlightthickness=1,
                       highlightbackground=BORDER)
        frm.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        # selectbackground uses a neutral mid-tone (BG4) so every tag colour
        # (green commands, blue link, gray paragraphs, yellow notes, white
        # headings) stays readable when highlighted. Tag foregrounds override
        # selectforeground in Tk, so we don't set it.
        txt = tk.Text(frm, bg=BG2, fg=TEXT, font=(_FNT, 10),
                      relief="flat", wrap="word", padx=14, pady=10,
                      cursor="xterm",
                      selectbackground="#3a4561", inactiveselectbackground="#3a4561",
                      exportselection=True, state="normal")
        sb = ttk.Scrollbar(frm, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True)

        txt.tag_configure("h",    font=(_FNT, 11, "bold"), foreground=TEXT,
                          spacing1=12, spacing3=4)
        txt.tag_configure("p",    font=(_FNT, 10), foreground=TEXT2, spacing3=3)
        # selectbackground on the tag overrides the tag background during
        # selection — without this, BG3 would paint over the selection
        # highlight and commands couldn't be visually marked.
        txt.tag_configure("cmd",  font=(_MONO, 10), foreground=GREEN,
                          background=BG3, selectbackground="#3a4561",
                          lmargin1=14, lmargin2=14,
                          spacing1=2, spacing3=2)
        txt.tag_configure("note", font=(_FNT, 9), foreground=YELLOW, spacing1=6)
        txt.tag_configure("link", font=(_FNT, 10), foreground=ACCENT, underline=True)

        is_win = sys.platform == "win32"
        install_cmd = pg_install_instructions()

        def ins(text, tag="p"):
            txt.insert("end", text + "\n", tag)

        # Step 1 — Install
        ins("Step 1 — Install PostgreSQL", "h")
        if is_win:
            ins("Option A — via winget (Windows 10/11 built-in):", "p")
            ins("winget install -e --id PostgreSQL.PostgreSQL.16", "cmd")
            ins("This launches the GUI installer — note the superuser password you set.", "note")
            ins("\nOption B — download the official installer:", "p")
            link_url = "https://www.postgresql.org/download/windows/"
            txt.insert("end", link_url + "\n", "link")
            txt.tag_bind("link", "<Button-1>",
                         lambda e: __import__("webbrowser").open(link_url))
            txt.tag_bind("link", "<Enter>", lambda e: txt.configure(cursor="hand2"))
            txt.tag_bind("link", "<Leave>", lambda e: txt.configure(cursor="arrow"))
        else:
            ins("Run in a terminal:", "p")
            ins(install_cmd, "cmd")

        # Step 2 — Create DB + user
        ins("\nStep 2 — Create the PingWatch database and user", "h")
        if is_win:
            ins('Open "SQL Shell (psql)" from the Start menu, log in as postgres, then run:', "p")
        else:
            ins("Open a psql session as the postgres superuser:", "p")
            ins("sudo -u postgres psql", "cmd")
            ins("Then run:", "p")
        ins("CREATE USER pingwatch WITH PASSWORD 'your_password';", "cmd")
        ins("CREATE DATABASE pingwatch OWNER pingwatch;", "cmd")
        ins("\\q", "cmd")

        # Step 3 — Fill in wizard
        ins("\nStep 3 — Enter the connection details here", "h")
        ins("  Host:      localhost\n"
            "  Port:      5432\n"
            "  Database:  pingwatch\n"
            "  User:      pingwatch\n"
            "  Password:  (the password you chose above)", "p")
        ins("Click Test Connection to verify before continuing.", "note")

        # ── Read-only but selectable/copyable ─────────────────────
        # Explicit Ctrl+C / Ctrl+A handlers bypass flaky event.state parsing.

        def _copy_selection(_e=None):
            try:
                sel = txt.get("sel.first", "sel.last")
            except tk.TclError:
                return "break"  # nothing selected
            if sel:
                txt.clipboard_clear()
                txt.clipboard_append(sel)
                txt.update()  # flush clipboard on Windows
            return "break"

        def _select_all(_e=None):
            txt.tag_add("sel", "1.0", "end-1c")
            txt.mark_set("insert", "1.0")
            return "break"

        # Explicit accelerator bindings (handle both lowercase and Shift variants)
        for seq in ("<Control-c>", "<Control-C>"):
            txt.bind(seq, _copy_selection)
        for seq in ("<Control-a>", "<Control-A>"):
            txt.bind(seq, _select_all)

        # Block every other key so the widget stays read-only, but let
        # navigation keys through so arrow/Home/End still work.
        _NAV = {"Left", "Right", "Up", "Down", "Home", "End",
                "Prior", "Next", "Shift_L", "Shift_R",
                "Control_L", "Control_R"}

        def _block_edit(event):
            if event.keysym in _NAV:
                return None
            return "break"
        txt.bind("<Key>", _block_edit)

        # Right-click context menu — gives mouse users an obvious Copy path
        ctx = tk.Menu(txt, tearoff=0, bg=BG3, fg=TEXT,
                      activebackground=BG4, activeforeground=TEXT,
                      bd=0, relief="flat")
        ctx.add_command(label="Copy", command=_copy_selection)
        ctx.add_command(label="Select All", command=_select_all)

        def _show_ctx(event):
            try:
                ctx.tk_popup(event.x_root, event.y_root)
            finally:
                ctx.grab_release()
        txt.bind("<Button-3>", _show_ctx)

        _btn(win, "Close", win.destroy, "accent").pack(pady=(0, 16))


# ── 4. Network ──────────────────────────────────────────────────────────────

class NetworkPage(WizardPage):
    def __init__(self, parent, ctrl):
        super().__init__(parent, ctrl)
        _page_title(self, "Network Configuration",
                    "Configure ports and TLS encryption")

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
            # Status pill — configured later in on_enter via _as_pill()
            status = tk.Label(row, text="", bg=BG, fg=TEXT3, font=(_FNT, 9))
            status.pack(side="left", padx=(10, 0), ipady=1)
            self._port_status[key] = status

        # ── TLS section ──────────────────────────────────────────
        _label(self, "", size=4).pack()  # spacer
        _label(self, "TLS / HTTPS", size=11, bold=True, color=TEXT).pack(
            anchor="w", pady=(4, 2))

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
        # Check port availability — render each status as a tinted pill
        for key, e in self._port_entries.items():
            lbl = self._port_status[key]
            try:
                p = int(e.get())
                pid = port_in_use(p)
                if pid is None:
                    _as_pill(lbl, "✓ Available", GREEN)
                else:
                    _as_pill(lbl, "✗ In use", RED)
            except ValueError:
                _as_pill(lbl, "Invalid", RED)

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
                _as_pill(self._port_status[key], "Invalid port", RED)
                return False
        return True


# ── 5. Security ─────────────────────────────────────────────────────────────

class SecurityPage(WizardPage):
    def __init__(self, parent, ctrl):
        super().__init__(parent, ctrl)
        _page_title(self, "Administrator Account",
                    "Create the initial admin user for the web dashboard")

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

        # Password + show/hide toggle
        self._pw_visible = False
        row2 = tk.Frame(fields_f, bg=BG)
        row2.pack(fill="x", pady=4)
        tk.Label(row2, text="Password", bg=BG, fg=TEXT2,
                 font=(_FNT, 10), width=16, anchor="e").pack(side="left")
        self._pass_entry = _entry(row2, show="●")
        self._pass_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self._pass_entry.bind("<KeyRelease>", self._update_strength)
        self._toggle_btn = _btn(row2, "Show", self._toggle_pw)
        self._toggle_btn.pack(side="left", padx=(6, 0))

        # Strength bar — thin colored fill below the password row
        bar_row = tk.Frame(fields_f, bg=BG)
        bar_row.pack(fill="x", padx=(110, 58))  # align with entry width
        self._pw_bar = tk.Canvas(bar_row, bg=BG2, height=4,
                                 highlightthickness=0)
        self._pw_bar.pack(fill="x")
        self._pw_bar_rect = self._pw_bar.create_rectangle(
            0, 0, 0, 4, fill=RED, outline="")

        # Confirm
        row3 = tk.Frame(fields_f, bg=BG)
        row3.pack(fill="x", pady=(6, 4))
        tk.Label(row3, text="Confirm Password", bg=BG, fg=TEXT2,
                 font=(_FNT, 10), width=16, anchor="e").pack(side="left")
        self._pass2_entry = _entry(row3, show="●")
        self._pass2_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        # Spacer matching the Show/Hide button so Confirm row aligns
        tk.Label(row3, text=" ", bg=BG, width=7).pack(side="left", padx=(6, 0))

        self._err_lbl = tk.Label(self, text="", bg=BG, fg=RED, font=(_FNT, 10))
        self._err_lbl.pack(anchor="w", pady=(8, 0))

    def _toggle_pw(self):
        self._pw_visible = not self._pw_visible
        show = "" if self._pw_visible else "●"
        self._pass_entry.config(show=show)
        self._pass2_entry.config(show=show)
        self._toggle_btn.config(text="Hide" if self._pw_visible else "Show")

    def _update_strength(self, _event=None):
        pw = self._pass_entry.get()
        strength = _password_strength(pw)
        # Redraw fill width proportional to strength
        self._pw_bar.update_idletasks()
        full = max(self._pw_bar.winfo_width(), 1)
        width = int(full * strength)
        if strength < 0.4:
            color = RED
        elif strength < 0.75:
            color = YELLOW
        else:
            color = GREEN
        self._pw_bar.coords(self._pw_bar_rect, 0, 0, width, 4)
        self._pw_bar.itemconfig(self._pw_bar_rect, fill=color)

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


# ── 6. Alerts (SMTP + Syslog + Anomaly) ────────────────────────────────────

class AlertsPage(WizardPage):
    """Optional alert integrations — SMTP, remote syslog, anomaly default.

    Every section is off by default.  Validation is permissive: bad values
    are flagged in-place but never block Next — the wizard should never
    fail on cosmetic input issues.
    """

    def __init__(self, parent, ctrl):
        super().__init__(parent, ctrl)
        _page_title(self, "Alerts & Integrations",
                    "Optional — enable only what you need. All fields can be "
                    "changed later in Settings → Integrations.")

        # ── Scrollable body (content grows past window height) ───
        self._canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self._sb     = ttk.Scrollbar(self, orient="vertical",
                                     command=self._canvas.yview)
        body = tk.Frame(self._canvas, bg=BG)
        body.bind("<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._body_win = self._canvas.create_window((0, 0), window=body, anchor="nw")
        self._canvas.configure(yscrollcommand=self._sb.set)
        self._canvas.bind("<Configure>",
            lambda e: self._canvas.itemconfig(self._body_win, width=e.width))
        self._sb.pack(side="right", fill="y", pady=(0, 2))
        self._canvas.pack(side="left", fill="both", expand=True, pady=(0, 2))

        def _on_mousewheel(e):
            self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        self._canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # ── SMTP section ─────────────────────────────────────────
        self._smtp_on = tk.BooleanVar(value=bool(ctrl.state.get("smtp_enabled")))
        smtp_card = tk.Frame(body, bg=BG2, highlightthickness=1,
                             highlightbackground=BORDER, padx=14, pady=10)
        smtp_card.pack(fill="x", pady=(4, 8))
        tk.Checkbutton(smtp_card, text="Email Alerts (SMTP)",
                       variable=self._smtp_on, command=self._toggle_smtp,
                       bg=BG2, fg=TEXT, selectcolor=BG3,
                       activebackground=BG2, activeforeground=TEXT,
                       font=(_FNT, 11, "bold")).pack(anchor="w")
        tk.Label(smtp_card, text="Send alert notifications by email. "
                 "Recipients come from user accounts — this configures only the sender.",
                 bg=BG2, fg=TEXT2, font=(_FNT, 9),
                 wraplength=700, justify="left").pack(anchor="w",
                                                       padx=(22, 0), pady=(0, 4))

        self._smtp_fields = tk.Frame(smtp_card, bg=BG2)
        self._smtp_entries = {}
        for label_text, key, default, is_pw, width in [
            ("SMTP Host",     "smtp_host", "",                   False, 0),
            ("Port",          "smtp_port", "587",                False, 8),
            ("Username",      "smtp_user", "",                   False, 0),
            ("Password",      "smtp_pass", "",                   True,  0),
            ("From Address",  "smtp_from", "",                   False, 0),
        ]:
            row = tk.Frame(self._smtp_fields, bg=BG2)
            row.pack(fill="x", pady=2, padx=(22, 0))
            tk.Label(row, text=label_text, bg=BG2, fg=TEXT2,
                     font=(_FNT, 10), width=13, anchor="e").pack(side="left")
            kw = {"show": "*"} if is_pw else {}
            if width:
                kw["width"] = width
            e = _entry(row, **kw)
            # Re-parent background to match card
            e.configure(bg=BG3)
            e.insert(0, str(ctrl.state.get(key, default)))
            if width:
                e.pack(side="left", padx=(6, 0))
            else:
                e.pack(side="left", fill="x", expand=True, padx=(6, 0))
            self._smtp_entries[key] = e

        tls_row = tk.Frame(self._smtp_fields, bg=BG2)
        tls_row.pack(fill="x", pady=2, padx=(22, 0))
        tk.Label(tls_row, text="Security", bg=BG2, fg=TEXT2,
                 font=(_FNT, 10), width=13, anchor="e").pack(side="left")
        self._smtp_tls = tk.StringVar(
            value=str(ctrl.state.get("smtp_tls", "starttls")).lower())
        for mode, txt in (("starttls", "STARTTLS"), ("ssl", "SSL/TLS"), ("none", "None")):
            tk.Radiobutton(tls_row, text=txt,
                           variable=self._smtp_tls, value=mode,
                           bg=BG2, fg=TEXT2, selectcolor=BG3,
                           activebackground=BG2, activeforeground=TEXT,
                           font=(_FNT, 10)).pack(side="left", padx=(6, 0))

        self._smtp_warn = tk.Label(smtp_card, text="", bg=BG2, fg=YELLOW,
                                   font=(_FNT, 9), wraplength=700, justify="left")

        # ── Syslog section ───────────────────────────────────────
        self._sys_on = tk.BooleanVar(value=bool(ctrl.state.get("syslog_enabled")))
        sys_card = tk.Frame(body, bg=BG2, highlightthickness=1,
                            highlightbackground=BORDER, padx=14, pady=10)
        sys_card.pack(fill="x", pady=8)
        tk.Checkbutton(sys_card, text="Remote Syslog Forwarding",
                       variable=self._sys_on, command=self._toggle_sys,
                       bg=BG2, fg=TEXT, selectcolor=BG3,
                       activebackground=BG2, activeforeground=TEXT,
                       font=(_FNT, 11, "bold")).pack(anchor="w")
        tk.Label(sys_card, text="Forward events to a remote syslog or SIEM server.",
                 bg=BG2, fg=TEXT2, font=(_FNT, 9)).pack(anchor="w",
                                                         padx=(22, 0), pady=(0, 4))

        self._sys_fields = tk.Frame(sys_card, bg=BG2)
        self._sys_entries = {}
        for label_text, key, default, width in [
            ("Host", "syslog_host", "",    0),
            ("Port", "syslog_port", "514", 8),
        ]:
            row = tk.Frame(self._sys_fields, bg=BG2)
            row.pack(fill="x", pady=2, padx=(22, 0))
            tk.Label(row, text=label_text, bg=BG2, fg=TEXT2,
                     font=(_FNT, 10), width=13, anchor="e").pack(side="left")
            kw = {"width": width} if width else {}
            e = _entry(row, **kw)
            e.configure(bg=BG3)
            e.insert(0, str(ctrl.state.get(key, default)))
            if width:
                e.pack(side="left", padx=(6, 0))
            else:
                e.pack(side="left", fill="x", expand=True, padx=(6, 0))
            self._sys_entries[key] = e

        proto_row = tk.Frame(self._sys_fields, bg=BG2)
        proto_row.pack(fill="x", pady=2, padx=(22, 0))
        tk.Label(proto_row, text="Protocol", bg=BG2, fg=TEXT2,
                 font=(_FNT, 10), width=13, anchor="e").pack(side="left")
        self._sys_proto = tk.StringVar(
            value=str(ctrl.state.get("syslog_proto", "udp")).lower())
        for mode, txt in (("udp", "UDP"), ("tcp", "TCP")):
            tk.Radiobutton(proto_row, text=txt,
                           variable=self._sys_proto, value=mode,
                           bg=BG2, fg=TEXT2, selectcolor=BG3,
                           activebackground=BG2, activeforeground=TEXT,
                           font=(_FNT, 10)).pack(side="left", padx=(6, 0))

        sev_row = tk.Frame(self._sys_fields, bg=BG2)
        sev_row.pack(fill="x", pady=2, padx=(22, 0))
        tk.Label(sev_row, text="Min Severity", bg=BG2, fg=TEXT2,
                 font=(_FNT, 10), width=13, anchor="e").pack(side="left")
        self._sys_sev = tk.StringVar(
            value=str(ctrl.state.get("syslog_min_severity", "warning")).lower())
        for mode, txt in (("critical", "Critical"), ("warning", "Warning"), ("info", "Info")):
            tk.Radiobutton(sev_row, text=txt,
                           variable=self._sys_sev, value=mode,
                           bg=BG2, fg=TEXT2, selectcolor=BG3,
                           activebackground=BG2, activeforeground=TEXT,
                           font=(_FNT, 10)).pack(side="left", padx=(6, 0))

        self._sys_warn = tk.Label(sys_card, text="", bg=BG2, fg=YELLOW,
                                  font=(_FNT, 9), wraplength=700, justify="left")

        # ── Anomaly detection ────────────────────────────────────
        anom_card = tk.Frame(body, bg=BG2, highlightthickness=1,
                             highlightbackground=BORDER, padx=14, pady=10)
        anom_card.pack(fill="x", pady=(8, 4))
        self._anom_on = tk.BooleanVar(
            value=bool(ctrl.state.get("anomaly_default_new_sensors")))
        tk.Checkbutton(anom_card,
                       text="Enable Anomaly Detection on new sensors by default",
                       variable=self._anom_on,
                       bg=BG2, fg=TEXT, selectcolor=BG3,
                       activebackground=BG2, activeforeground=TEXT,
                       font=(_FNT, 11, "bold")).pack(anchor="w")
        tk.Label(anom_card,
                 text="Learns normal sensor behaviour and flags deviations. "
                      "Existing sensors are not affected — individual sensors "
                      "can still opt in or out after creation.",
                 bg=BG2, fg=TEXT2, font=(_FNT, 9),
                 wraplength=700, justify="left").pack(anchor="w",
                                                       padx=(22, 0), pady=(0, 2))

    # ── Section visibility toggles ───────────────────────────────
    def _toggle_smtp(self):
        if self._smtp_on.get():
            self._smtp_fields.pack(fill="x", pady=(4, 0))
        else:
            self._smtp_fields.pack_forget()
            self._smtp_warn.pack_forget()

    def _toggle_sys(self):
        if self._sys_on.get():
            self._sys_fields.pack(fill="x", pady=(4, 0))
        else:
            self._sys_fields.pack_forget()
            self._sys_warn.pack_forget()

    def on_enter(self):
        self._toggle_smtp()
        self._toggle_sys()

    def on_leave(self):
        st = self.ctrl.state

        # ── SMTP ──
        st["smtp_enabled"] = bool(self._smtp_on.get())
        if st["smtp_enabled"]:
            st["smtp_host"] = self._smtp_entries["smtp_host"].get().strip()
            try:
                p = int(self._smtp_entries["smtp_port"].get())
                st["smtp_port"] = p if 1 <= p <= 65535 else 587
            except ValueError:
                st["smtp_port"] = 587
            st["smtp_tls"]  = self._smtp_tls.get()
            st["smtp_user"] = self._smtp_entries["smtp_user"].get().strip()
            st["smtp_pass"] = self._smtp_entries["smtp_pass"].get()
            st["smtp_from"] = self._smtp_entries["smtp_from"].get().strip()

        # ── Syslog ──
        st["syslog_enabled"] = bool(self._sys_on.get())
        if st["syslog_enabled"]:
            st["syslog_host"] = self._sys_entries["syslog_host"].get().strip()
            try:
                p = int(self._sys_entries["syslog_port"].get())
                st["syslog_port"] = p if 1 <= p <= 65535 else 514
            except ValueError:
                st["syslog_port"] = 514
            st["syslog_proto"]        = self._sys_proto.get()
            st["syslog_min_severity"] = self._sys_sev.get()

        # ── Anomaly ──
        st["anomaly_default_new_sensors"] = bool(self._anom_on.get())

    def validate(self) -> bool:
        """Never block Next — just warn on questionable input.

        The philosophy across all three wizards is: save whatever the user
        entered, log warnings, let them fix it later in Settings. This keeps
        first-run setup from stalling on cosmetic validation failures.
        """
        warns = []

        # SMTP warnings
        self._smtp_warn.pack_forget()
        if self._smtp_on.get():
            host = self._smtp_entries["smtp_host"].get().strip()
            if host and not valid_host(host):
                warns.append(f"SMTP host '{host}' looks unusual — saved anyway.")
            frm = self._smtp_entries["smtp_from"].get().strip()
            if frm and not valid_email(frm):
                warns.append(f"From address '{frm}' is not a valid email — "
                             f"saved anyway.")
            if warns:
                self._smtp_warn.config(text="  ".join(warns))
                self._smtp_warn.pack(anchor="w", padx=(22, 0), pady=(4, 0))

        # Syslog warnings
        self._sys_warn.pack_forget()
        if self._sys_on.get():
            host = self._sys_entries["syslog_host"].get().strip()
            if host and not valid_host(host):
                self._sys_warn.config(
                    text=f"Syslog host '{host}' looks unusual — saved anyway.")
                self._sys_warn.pack(anchor="w", padx=(22, 0), pady=(4, 0))

        return True


# ── 7. System (Firewall + Shortcut + Auto-Start) ───────────────────────────

class SystemPage(WizardPage):
    def __init__(self, parent, ctrl):
        super().__init__(parent, ctrl)
        _page_title(self, "System Integration",
                    "Firewall rules, desktop shortcut, and auto-start")

        self._rows = {}

        # ── Firewall section ─────────────────────────────────────
        if sys.platform == "win32":
            _label(self, "Windows Firewall", size=11, bold=True,
                   color=TEXT).pack(anchor="w", pady=(6, 2))
            self._fw_frame = tk.Frame(self, bg=BG)
            self._fw_frame.pack(fill="x", pady=(0, 8))

        # ── Desktop shortcut ─────────────────────────────────────
        sc_row = tk.Frame(self, bg=BG2, padx=8, pady=6)
        sc_row.pack(fill="x", pady=2)
        self._sc_icon = tk.Label(sc_row, text="○", bg=BG2, fg=TEXT2,
                                 font=(_MONO, 12), width=3)
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
                   color=TEXT).pack(anchor="w", pady=(6, 2))

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
        self._as_spin = _start_spinner(self.ctrl.root, self._as_icon, YELLOW)
        self.ctrl.set_busy(True)
        as_system = (self._as_mode.get() == "system")

        def _worker():
            ok, msg = win_install_task(as_system=as_system)
            self.ctrl.root.after(0, lambda: self._task_done(ok, msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _task_done(self, ok, msg):
        if getattr(self, "_as_spin", None):
            self._as_spin()
            self._as_spin = None
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
        _page_title(self, "Review & Finish",
                    "Review your settings and click Finish to complete setup")

        self._summary_frame = tk.Frame(self, bg=BG2, highlightthickness=1,
                                       highlightbackground=BORDER, padx=12, pady=10)
        self._summary_frame.pack(fill="both", expand=True)

        # Big success checkmark — only shown after DB init succeeds
        self._success_lbl = tk.Label(self, text="", bg=BG, fg=GREEN,
                                     font=(_FNT, 36, "bold"))

        self._status_lbl = tk.Label(self, text="", bg=BG, fg=TEXT2,
                                    font=(_FNT, 11))
        self._status_lbl.pack(pady=(8, 0))

        self._progress = ttk.Progressbar(self, mode="indeterminate", length=500)
        # Don't pack yet — shown during install

    def _add_section(self, title):
        """Render a small-caps section heading + thin divider inside the summary frame."""
        # Top divider (skip for first section)
        if self._summary_frame.winfo_children():
            tk.Frame(self._summary_frame, bg=BORDER, height=1).pack(
                fill="x", pady=(8, 4))
        tk.Label(self._summary_frame, text=title,
                 bg=BG2, fg=TEXT3, font=(_FNT, 9, "bold"),
                 anchor="w").pack(fill="x", pady=(0, 4))

    def _add_row(self, idx_in_section, label, value):
        bg = BG2 if idx_in_section % 2 == 0 else BG3
        row = tk.Frame(self._summary_frame, bg=bg, padx=8, pady=4)
        row.pack(fill="x")
        tk.Label(row, text=label, bg=bg, fg=TEXT2,
                 font=(_FNT, 10), width=16, anchor="e").pack(side="left")
        tk.Label(row, text=value, bg=bg, fg=TEXT,
                 font=(_FNT, 10, "bold")).pack(side="left", padx=(8, 0))

    def on_enter(self):
        # Rebuild summary (clear + render by section)
        for w in self._summary_frame.winfo_children():
            w.destroy()
        st = self.ctrl.state

        # ── Database ──
        self._add_section("DATABASE")
        db_rows = [("Backend", st["db_backend"].title())]
        if st["db_backend"] == "postgresql":
            db_rows.append(("PG Host",
                            f"{st.get('pg_host', 'localhost')}:{st.get('pg_port', 5432)}"))
            db_rows.append(("PG Database", st.get("pg_database", "pingwatch")))
        for i, (l, v) in enumerate(db_rows):
            self._add_row(i, l, v)

        # ── Network ──
        self._add_section("NETWORK")
        tls_on = bool(st.get("tls_enabled"))
        net_rows = [
            ("HTTP Port",  str(st.get("http_port", 7070))),
            ("HTTPS",      "Enabled" if tls_on else "Disabled"),
            ("HTTPS Port", str(st.get("tls_port", 8443)) if tls_on else "—"),
            ("SNMP Port",  str(st.get("snmp_port", 162))),
            ("Organization", st.get("org_name", "PingWatch")),
        ]
        for i, (l, v) in enumerate(net_rows):
            self._add_row(i, l, v)

        # ── Security ──
        self._add_section("SECURITY")
        self._add_row(0, "Admin User", st.get("admin_user", "admin"))

        # ── Alerts (only show sections the user enabled) ──
        alerts_rows = []
        if st.get("smtp_enabled") and st.get("smtp_host"):
            alerts_rows.append(("Email (SMTP)",
                f"{st.get('smtp_host')}:{st.get('smtp_port', 587)} "
                f"({st.get('smtp_tls', 'starttls')})"))
        else:
            alerts_rows.append(("Email (SMTP)", "Not configured"))
        if st.get("syslog_enabled") and st.get("syslog_host"):
            alerts_rows.append(("Syslog",
                f"{st.get('syslog_host')}:{st.get('syslog_port', 514)} "
                f"/{st.get('syslog_proto', 'udp')} "
                f"({st.get('syslog_min_severity', 'warning')}+)"))
        else:
            alerts_rows.append(("Syslog", "Not configured"))
        alerts_rows.append(("Anomaly Default",
            "On for new sensors" if st.get("anomaly_default_new_sensors")
            else "Off"))
        self._add_section("ALERTS")
        for i, (l, v) in enumerate(alerts_rows):
            self._add_row(i, l, v)

    def do_finish(self):
        """Called by Finish button — runs DB init in background."""
        self.ctrl.set_busy(True)
        self._status_lbl.config(text="Initializing…", fg=YELLOW)
        self._progress.pack(fill="x", padx=40, pady=(6, 0))
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
            # Large green ✓ before auto-close
            self._success_lbl.config(text="✓")
            self._success_lbl.pack(pady=(10, 0), before=self._status_lbl)
            self._status_lbl.config(text="Setup complete!", fg=GREEN,
                                    font=(_FNT, 12, "bold"))
            self.ctrl.root.after(1200, self.ctrl.finish_ok)
        else:
            self._status_lbl.config(text=f"✗ {err}", fg=RED)


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

def run_wizard() -> bool:
    """Launch the setup wizard.  Returns True if completed, False if cancelled."""
    root = tk.Tk()

    # Apply ttk dark theme — progressbar + scrollbar blend with dark chrome
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TProgressbar", background=ACCENT,
                    troughcolor=BG3, borderwidth=0, thickness=6)
    style.configure("Vertical.TScrollbar",
                    background=BG3, troughcolor=BG, bordercolor=BG,
                    arrowcolor=TEXT2, gripcount=0, relief="flat")
    style.map("Vertical.TScrollbar",
              background=[("active", BG4), ("pressed", BG4)],
              arrowcolor=[("active", TEXT)])
    style.configure("Horizontal.TScrollbar",
                    background=BG3, troughcolor=BG, bordercolor=BG,
                    arrowcolor=TEXT2, gripcount=0, relief="flat")

    ctrl = WizardController(root)
    ctrl.add_page(WelcomePage)
    ctrl.add_page(PackagesPage)
    ctrl.add_page(DatabasePage)
    ctrl.add_page(NetworkPage)
    ctrl.add_page(SecurityPage)
    ctrl.add_page(AlertsPage)
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
