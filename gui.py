"""
gui.py — Native status window for PingWatch (tkinter, stdlib only).

Opened on startup and via the system-tray "Status Window" menu item.
Closing the window hides it to the tray; only Quit actually exits.
"""

import datetime
import os
import tkinter as tk
import webbrowser

# ── Colour palette (matches web UI) ───────────────────────────────
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
PURPLE = "#8e44ad"

_STYPE_ICO = {"ping": "◉", "tcp": "⇌", "http": "◈", "snmp": "◎",
              "dns": "⬡", "tls": "⚿", "http_keyword": "K", "banner": "B"}


class StatusWindow:
    """
    Dark-themed status window.

    Parameters
    ----------
    state      : MonitorState  — live server state (devices/sensors)
    log_buffer : _MemoryHandler — ring buffer of formatted log strings
    port       : int            — HTTP port (for "Open Dashboard")
    quit_fn    : callable       — called when user chooses Quit
    """

    def __init__(self, state, log_buffer, port, quit_fn):
        self._state  = state
        self._buf    = log_buffer
        self._port   = port
        self._quit   = quit_fn
        self._start  = datetime.datetime.now()
        self._root   = None
        self._log_text       = None
        self._last_log_count = 0
        # label/widget refs updated by _refresh
        self._dev_labels      = {}
        self._lbl_uptime      = None
        self._lbl_snr_tot     = None
        self._lbl_snr_alive   = None
        self._lbl_snr_typ     = None
        self._status_strip    = None
        self._health_canvas   = None
        self._lbl_health_pct  = None
        self._lbl_dev_total   = None
        # ── system status labels ───────────────────────────────────
        self._lbl_sys_version = None
        self._lbl_sys_uptime  = None
        self._lbl_sys_devices = None
        self._lbl_sys_sensors = None
        self._lbl_sys_dbsize  = None
        self._lbl_sys_logsize = None
        # ── log source selection ───────────────────────────────────
        _here = os.path.dirname(os.path.abspath(__file__))
        _logdir = os.path.join(_here, "logs")
        self._log_source = "app"   # "app" | "sensors" | "audit" | "backup"
        self._log_paths  = {
            "sensors": os.path.join(_logdir, "pingwatchsensors.log"),
            "audit":   os.path.join(_logdir, "pingwatchaudit.log"),
            "backup":  os.path.join(_logdir, "pingwatchbackup.log"),
        }
        self._file_pos   = {}   # path -> byte offset (for tailing)
        self._file_lines = []   # accumulated lines for current file source
        self._src_btns   = {}   # source -> Button widget
        self._btn_clear  = None

    # ── public API ────────────────────────────────────────────────

    def build_and_show(self):
        """Build the window and enter the Tk main-loop (blocks)."""
        self._build()
        self._refresh()
        self._root.mainloop()

    def show(self):
        """Raise / un-hide the window (safe to call from any thread)."""
        if self._root:
            self._root.after(0, self._do_show)

    def hide(self):
        """Withdraw the window (X button behaviour)."""
        if self._root:
            self._root.withdraw()

    def destroy(self):
        """Destroy the Tk root, ending the main-loop."""
        if self._root:
            self._root.after(0, self._root.destroy)

    # ── internal helpers ──────────────────────────────────────────

    def _do_show(self):
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()

    def _build(self):
        r = tk.Tk()
        r.title("PingWatch — Status")
        r.geometry("860x780")
        r.minsize(720, 640)
        r.resizable(True, True)
        r.configure(bg=BG)
        r.protocol("WM_DELETE_WINDOW", self.hide)

        try:
            import os
            ico = os.path.join(os.path.dirname(__file__), "favicon.ico")
            if os.path.exists(ico):
                r.iconbitmap(ico)
        except Exception:
            pass

        self._root = r
        self._build_header()
        self._build_status_strip()
        self._build_summary()
        self._build_footer()   # must be packed before the expanding log frame
        self._build_log()

    # ── header ────────────────────────────────────────────────────

    def _build_header(self):
        r = self._root
        hdr = tk.Frame(r, bg=BG2, pady=10)
        hdr.pack(fill="x")

        # Logo area
        logo = tk.Frame(hdr, bg=BG2)
        logo.pack(side="left", padx=(16, 0))
        tk.Label(logo, text="●", fg=GREEN, bg=BG2,
                 font=("Segoe UI", 17)).pack(side="left", padx=(0, 6))
        tk.Label(logo, text="Ping", fg=TEXT, bg=BG2,
                 font=("Segoe UI", 19, "bold")).pack(side="left")
        tk.Label(logo, text="Watch", fg=ACCENT, bg=BG2,
                 font=("Segoe UI", 19, "bold")).pack(side="left")
        tk.Label(logo, text="  Network Monitor", fg=TEXT2, bg=BG2,
                 font=("Segoe UI", 11)).pack(side="left")

        # Right side: uptime
        right = tk.Frame(hdr, bg=BG2)
        right.pack(side="right", padx=16)
        self._lbl_uptime = tk.Label(right, text="", fg=TEXT3, bg=BG2,
                                     font=("Consolas", 13))
        self._lbl_uptime.pack(anchor="e")

    # ── thin status strip (green / yellow / red) ──────────────────

    def _build_status_strip(self):
        self._status_strip = tk.Frame(self._root, bg=TEXT3, height=3)
        self._status_strip.pack(fill="x")
        self._status_strip.pack_propagate(False)

    # ── summary row ───────────────────────────────────────────────

    def _build_summary(self):
        mid = tk.Frame(self._root, bg=BG)
        mid.pack(fill="x", padx=14, pady=(10, 4))

        # ── Devices panel ──────────────────────────────────────────
        df = self._panel(mid, "DEVICES")
        df.pack(side="left", fill="both", expand=True, padx=(0, 6))

        for sym, key, color, name in [
            ("↑", "up",   GREEN,  "Up"),
            ("↓", "down", RED,    "Down"),
            ("⚠", "warn", YELLOW, "Warning"),
            ("—", "idle", TEXT2,  "Idle"),
        ]:
            row = tk.Frame(df, bg=BG)
            row.pack(fill="x", padx=10, pady=2)
            tk.Label(row, text=sym, fg=color, bg=BG,
                     font=("Segoe UI", 16), width=2).pack(side="left")
            lbl = tk.Label(row, text="0", fg=color, bg=BG,
                           font=("Segoe UI", 21, "bold"), width=3)
            lbl.pack(side="left")
            tk.Label(row, text=name, fg=TEXT2, bg=BG,
                     font=("Segoe UI", 13)).pack(side="left", padx=(4, 0))
            self._dev_labels[key] = lbl

        # Total devices count
        self._lbl_dev_total = tk.Label(df, text="", fg=TEXT3, bg=BG,
                                        font=("Segoe UI", 12))
        self._lbl_dev_total.pack(anchor="e", padx=10, pady=(0, 4))

        # ── Sensors panel ──────────────────────────────────────────
        sf = self._panel(mid, "SENSORS")
        sf.pack(side="left", fill="both", expand=True, padx=(6, 0))

        # Total / running
        self._lbl_snr_tot = tk.Label(sf, text="Total: 0   Running: 0",
                                      fg=TEXT, bg=BG,
                                      font=("Segoe UI", 14, "bold"))
        self._lbl_snr_tot.pack(anchor="w", padx=10, pady=(6, 2))

        # Health bar canvas
        bar_wrap = tk.Frame(sf, bg=BG3, height=5)
        bar_wrap.pack(fill="x", padx=10, pady=(0, 4))
        bar_wrap.pack_propagate(False)
        self._health_canvas = tk.Canvas(bar_wrap, height=5, bg=BG3,
                                         highlightthickness=0, bd=0)
        self._health_canvas.pack(fill="both", expand=True)

        # Alive / fail / pending row
        alive_row = tk.Frame(sf, bg=BG)
        alive_row.pack(fill="x", padx=10, pady=(0, 4))
        self._lbl_snr_alive = tk.Label(alive_row, text="", fg=TEXT2, bg=BG,
                                        font=("Segoe UI", 13))
        self._lbl_snr_alive.pack(side="left")
        self._lbl_health_pct = tk.Label(alive_row, text="", fg=TEXT3, bg=BG,
                                         font=("Segoe UI", 13))
        self._lbl_health_pct.pack(side="right")

        # Type breakdown
        self._lbl_snr_typ = tk.Label(sf, text="—", fg=TEXT2, bg=BG,
                                      font=("Consolas", 13), justify="left")
        self._lbl_snr_typ.pack(anchor="w", padx=10, pady=(0, 6))

        # ── System Status row (full width, below Devices + Sensors) ──
        syf = self._panel(self._root, "SYSTEM STATUS")
        syf.pack(fill="x", padx=14, pady=(0, 4))
        inner = tk.Frame(syf, bg=BG)
        inner.pack(fill="x", padx=8, pady=6)
        for col, (attr, label, fg) in enumerate([
            ("version", "Version",  ACCENT),
            ("uptime",  "Uptime",   TEXT),
            ("devices", "Devices",  TEXT),
            ("sensors", "Sensors",  TEXT),
            ("dbsize",  "DB Size",  TEXT2),
            ("logsize", "Log Size", TEXT2),
        ]):
            inner.columnconfigure(col, weight=1)
            cell = tk.Frame(inner, bg=BG)
            cell.grid(row=0, column=col, padx=8, sticky="w")
            tk.Label(cell, text=label, fg=TEXT3, bg=BG,
                     font=("Segoe UI", 10)).pack(anchor="w")
            lbl = tk.Label(cell, text="—", fg=fg, bg=BG,
                           font=("Consolas", 12, "bold"))
            lbl.pack(anchor="w")
            setattr(self, f"_lbl_sys_{attr}", lbl)

    def _panel(self, parent, title):
        """Return a styled LabelFrame."""
        return tk.LabelFrame(parent, text=f"  {title}  ", bg=BG,
                             fg=TEXT3, font=("Segoe UI", 12, "bold"),
                             bd=1, relief="groove",
                             highlightbackground=BORDER,
                             highlightthickness=1)

    # ── log viewer ────────────────────────────────────────────────

    def _build_log(self):
        outer = tk.Frame(self._root, bg=BG)
        outer.pack(fill="both", expand=True, padx=14, pady=(6, 0))

        # Log header bar
        lhdr = tk.Frame(outer, bg=BG)
        lhdr.pack(fill="x")
        tk.Label(lhdr, text="LOG", fg=TEXT3, bg=BG,
                 font=("Segoe UI", 11, "bold")).pack(side="left")

        # Source selector buttons
        for src, label in [("app", "App"), ("sensors", "Sensors"), ("audit", "Audit"), ("backup", "Backup")]:
            _src = src
            btn = tk.Button(
                lhdr, text=label, relief="flat", bd=0, cursor="hand2",
                font=("Segoe UI", 11), bg=BG, padx=6,
                activebackground=BG2,
                command=lambda s=_src: self._switch_log_source(s),
            )
            btn.pack(side="left", padx=(4, 0))
            self._src_btns[src] = btn

        self._btn_clear = tk.Button(
            lhdr, text="✕ Clear", bg=BG3, fg=TEXT2, relief="flat",
            font=("Segoe UI", 11), cursor="hand2", bd=0,
            activebackground=BG3, activeforeground=TEXT,
            command=self._clear_log,
        )
        self._btn_clear.pack(side="right")
        self._apply_src_style()  # set initial active style

        # Text widget + scrollbars
        frame = tk.Frame(outer, bg=BORDER, bd=1, relief="flat")
        frame.pack(fill="both", expand=True, pady=(4, 0))

        self._log_text = tk.Text(
            frame, bg=BG2, fg=TEXT2,
            font=("Consolas", 14),
            relief="flat", bd=0, wrap="none",
            selectbackground="#2f81f7",
            selectforeground="#ffffff",
            insertbackground=TEXT,
            state="disabled",
        )
        sb_y = tk.Scrollbar(frame, command=self._log_text.yview,
                            bg=BG3, troughcolor=BG, width=10)
        sb_x = tk.Scrollbar(frame, orient="horizontal",
                            command=self._log_text.xview,
                            bg=BG3, troughcolor=BG, width=8)
        self._log_text.configure(yscrollcommand=sb_y.set,
                                 xscrollcommand=sb_x.set)
        sb_y.pack(side="right", fill="y")
        sb_x.pack(side="bottom", fill="x")
        self._log_text.pack(fill="both", expand=True)

        # Colour tags
        self._log_text.tag_config("err",  foreground=RED)
        self._log_text.tag_config("warn", foreground=YELLOW)
        self._log_text.tag_config("info", foreground=TEXT2)
        self._log_text.tag_config("trap", foreground=PURPLE)

    # ── footer ────────────────────────────────────────────────────

    def _build_footer(self):
        ft = tk.Frame(self._root, bg=BG2, pady=9)
        ft.pack(fill="x", side="bottom")

        tk.Button(
            ft, text="Open Dashboard",
            bg=ACCENT, fg="white", relief="flat",
            font=("Segoe UI", 13, "bold"), padx=16, cursor="hand2",
            activebackground="#1a6ed4", activeforeground="white", bd=0,
            command=lambda: webbrowser.open(f"http://127.0.0.1:{self._port}"),
        ).pack(side="left", padx=14)

        tk.Button(
            ft, text="Network Map",
            bg=PURPLE, fg="white", relief="flat",
            font=("Segoe UI", 13, "bold"), padx=16, cursor="hand2",
            activebackground="#6c3483", activeforeground="white", bd=0,
            command=lambda: webbrowser.open(f"http://127.0.0.1:{self._port}/map"),
        ).pack(side="left", padx=4)

        tk.Button(
            ft, text="Quit PingWatch",
            bg=BG3, fg=RED, relief="flat",
            font=("Segoe UI", 13), padx=16, cursor="hand2",
            activebackground=BG3, activeforeground=RED, bd=0,
            command=self._do_quit,
        ).pack(side="right", padx=(4, 14))

        tk.Button(
            ft, text="↺ Restart",
            bg=BG3, fg=YELLOW, relief="flat",
            font=("Segoe UI", 13), padx=16, cursor="hand2",
            activebackground=BG3, activeforeground=YELLOW, bd=0,
            command=self._do_restart,
        ).pack(side="right", padx=4)

    # ── periodic refresh ──────────────────────────────────────────

    def _refresh(self):
        if not self._root:
            return
        try:
            self._update_uptime()
            self._update_summary()
            self._update_system_status()
            self._update_log()
        except Exception:
            pass
        self._root.after(2000, self._refresh)

    def _update_uptime(self):
        elapsed = datetime.datetime.now() - self._start
        h, rem  = divmod(int(elapsed.total_seconds()), 3600)
        m, s    = divmod(rem, 60)
        self._lbl_uptime.config(
            text=f"Port {self._port}   Uptime {h:02d}:{m:02d}:{s:02d}"
        )

    def _update_system_status(self):
        import app_state
        from config import DB_PATH as _DB_PATH

        # Version
        self._lbl_sys_version.config(text=f"v{app_state.APP_VERSION}")

        # Uptime (same source as header)
        elapsed = datetime.datetime.now() - self._start
        secs    = int(elapsed.total_seconds())
        h, rem  = divmod(secs, 3600)
        m, s    = divmod(rem, 60)
        self._lbl_sys_uptime.config(
            text=f"{h}h {m}m {s}s" if h else f"{m}m {s}s"
        )

        # Devices / Sensors
        devs = len(self._state.devices)
        snrs = sum(len(d.sensors) for d in self._state.devices.values())
        self._lbl_sys_devices.config(text=str(devs))
        self._lbl_sys_sensors.config(text=str(snrs))

        # DB size
        try:
            db_mb = os.path.getsize(_DB_PATH) / 1_048_576
            self._lbl_sys_dbsize.config(text=f"{db_mb:.2f} MB")
        except Exception:
            self._lbl_sys_dbsize.config(text="—")

        # Log size
        try:
            _here   = os.path.dirname(os.path.abspath(__file__))
            _logdir = os.path.join(_here, "logs")
            log_bytes = sum(
                os.path.getsize(os.path.join(_logdir, f))
                for f in os.listdir(_logdir)
                if os.path.isfile(os.path.join(_logdir, f))
            ) if os.path.isdir(_logdir) else 0
            self._lbl_sys_logsize.config(text=f"{log_bytes / 1_048_576:.2f} MB")
        except Exception:
            self._lbl_sys_logsize.config(text="—")

    def _update_summary(self):
        counts    = {"up": 0, "down": 0, "warn": 0, "idle": 0}
        snr_types = {}
        total   = 0
        running = 0
        snr_up   = 0
        snr_down = 0

        for dev in self._state.devices.values():
            st = getattr(dev, "status", None) or "idle"
            # Device.status returns "unknown" when sensors haven't probed yet
            if st not in counts:
                st = "idle"
            counts[st] += 1

            for snr in dev.sensors.values():          # ← fix: was dev.sensors (keys only)
                total += 1
                if getattr(snr, "running", False):
                    running += 1
                alive = getattr(snr, "alive", None)
                if alive is True:
                    snr_up += 1
                elif alive is False:
                    snr_down += 1
                t = getattr(snr, "stype", "?")
                snr_types[t] = snr_types.get(t, 0) + 1

        dev_total = sum(counts.values())
        snr_pend  = total - snr_up - snr_down

        # ── Status strip colour ────────────────────────────────────
        if total == 0:
            strip_col = TEXT3
        elif snr_down > 0 or counts["down"] > 0:
            strip_col = RED
        elif snr_up == total:
            strip_col = GREEN
        else:
            strip_col = YELLOW
        self._status_strip.config(bg=strip_col)

        # ── Device counts ──────────────────────────────────────────
        for key, lbl in self._dev_labels.items():
            lbl.config(text=str(counts[key]))
        self._lbl_dev_total.config(text=f"Total devices: {dev_total}")

        # ── Sensor summary ─────────────────────────────────────────
        self._lbl_snr_tot.config(
            text=f"Total: {total}   Running: {running}"
        )

        # Health bar
        ratio = snr_up / total if total else 0
        self._draw_health_bar(ratio, strip_col)

        # Alive / fail / pending
        self._lbl_snr_alive.config(
            text=f"↑ {snr_up}  ↓ {snr_down}  ? {snr_pend}"
        )
        pct = int(ratio * 100)
        pct_color = GREEN if pct == 100 else (RED if pct < 50 else YELLOW)
        self._lbl_health_pct.config(text=f"{pct}% healthy", fg=pct_color)

        # Type breakdown
        parts = [f"{_STYPE_ICO.get(t, '?')} {n:>3}  {t}"
                 for t, n in sorted(snr_types.items())]
        self._lbl_snr_typ.config(text="\n".join(parts) if parts else "—")

    def _draw_health_bar(self, ratio, color):
        c = self._health_canvas
        if not c:
            return
        c.update_idletasks()
        w = c.winfo_width()
        if w <= 1:
            return
        c.delete("all")
        c.create_rectangle(0, 0, w, 5, fill=BG3, outline="")
        fill_w = max(0, int(w * ratio))
        if fill_w:
            c.create_rectangle(0, 0, fill_w, 5, fill=color, outline="")

    def _update_log(self):
        if self._log_source == "app":
            lines = list(self._buf.lines)
            if len(lines) == self._last_log_count:
                return
            self._last_log_count = len(lines)
            self._render_log(lines)
        else:
            self._tail_log_file(self._log_paths[self._log_source])

    def _tail_log_file(self, path):
        """Read any new bytes from path since last read; append to display."""
        try:
            pos = self._file_pos.get(path, None)
            if pos is None:
                return   # not yet initialised (switch_log_source handles init)
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                new_text = f.read()
                self._file_pos[path] = f.tell()
            if not new_text:
                return
            new_lines = new_text.splitlines()
            self._file_lines.extend(new_lines)
            if len(self._file_lines) > 500:
                self._file_lines = self._file_lines[-500:]
            self._render_log(self._file_lines)
        except FileNotFoundError:
            pass
        except Exception:
            pass

    def _render_log(self, lines):
        t = self._log_text
        if not t:
            return
        t.config(state="normal")
        t.delete("1.0", "end")
        for line in lines:
            if " ERROR " in line or " CRITICAL " in line:
                tag = "err"
            elif " WARNING " in line:
                tag = "warn"
            elif "[TRAP]" in line:
                tag = "trap"
            else:
                tag = "info"
            t.insert("end", line + "\n", tag)
        t.see("end")
        t.config(state="disabled")

    def _switch_log_source(self, src):
        """Switch the log panel to app buffer or a specific log file."""
        self._log_source = src
        self._file_lines = []
        self._last_log_count = 0
        if src != "app":
            path = self._log_paths[src]
            # Load last 500 lines from file
            self._file_lines = self._read_last_lines(path, 500)
            # Set tail position to current end-of-file
            try:
                self._file_pos[path] = os.path.getsize(path)
            except Exception:
                self._file_pos[path] = 0
        self._apply_src_style()
        # Immediately redraw
        if src == "app":
            self._render_log(list(self._buf.lines))
        else:
            self._render_log(self._file_lines)

    def _apply_src_style(self):
        """Highlight the active source button; dim/enable clear button."""
        for s, btn in self._src_btns.items():
            if s == self._log_source:
                btn.config(fg=ACCENT, font=("Segoe UI", 11, "bold"),
                           activeforeground=ACCENT)
            else:
                btn.config(fg=TEXT3, font=("Segoe UI", 11),
                           activeforeground=TEXT2)
        if self._btn_clear:
            if self._log_source == "app":
                self._btn_clear.config(fg=TEXT2, state="normal")
            else:
                self._btn_clear.config(fg=TEXT3, state="disabled")

    def _read_last_lines(self, path, n):
        """Return last n lines from a log file as a list of strings."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return [l.rstrip("\n") for l in lines[-n:]]
        except Exception:
            return []

    def _clear_log(self):
        self._buf.lines.clear()
        self._last_log_count = 0
        t = self._log_text
        if t:
            t.config(state="normal")
            t.delete("1.0", "end")
            t.config(state="disabled")

    def _do_quit(self):
        self._quit()   # caller stops tray + calls self.destroy()

    def _do_restart(self):
        import sys, subprocess
        subprocess.Popen([sys.executable] + sys.argv)
        self._quit()
