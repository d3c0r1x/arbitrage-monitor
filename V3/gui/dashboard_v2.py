"""
MEXC × DEX Arbitrage Monitor — GUI Dashboard v2.

Modern dark-theme dashboard with real-time bot telemetry.
Launch: python gui/dashboard.py
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter
from pathlib import Path

import tkinter as tk
from tkinter import ttk

# ── Design System ────────────────────────────────────────────────────────
# Color palette: deep navy with electric accents.
C_BG = "#0d1117"          # GitHub dark bg
C_BG_RAISED = "#161b22"   # Card/panel bg
C_BG_INSET = "#0d1117"    # Inset areas
C_BORDER = "#30363d"      # Subtle borders
C_BORDER_ACCENT = "#1f6feb"  # Blue accent border

C_TEXT = "#e6edf3"        # Primary text
C_TEXT_DIM = "#8b949e"    # Secondary text
C_TEXT_FAINT = "#484f58"  # Faint text

C_GREEN = "#3fb950"       # Success / profit
C_RED = "#f85149"         # Error / loss
C_YELLOW = "#d29922"      # Warning
C_BLUE = "#58a6ff"        # Info / accent
C_PURPLE = "#bc8cff"      # Special
C_ORANGE = "#f0883e"      # Highlight
C_CYAN = "#39d2c0"        # Metrics

FONT = "Segoe UI"
FONT_MONO = "Cascadia Code"


# ── Widgets ──────────────────────────────────────────────────────────────

class Card(tk.Frame):
    """Metric card with label, value, and optional accent color."""

    def __init__(self, parent, label: str, value: str = "—",
                 accent: str = C_BLUE, **kw):
        super().__init__(parent, bg=C_BG_RAISED, highlightbackground=C_BORDER,
                         highlightthickness=1, **kw)
        self._accent = accent

        # Accent bar (top).
        tk.Frame(self, bg=accent, height=3).pack(fill=tk.X)

        inner = tk.Frame(self, bg=C_BG_RAISED, padx=12, pady=8)
        inner.pack(fill=tk.BOTH, expand=True)

        self._label = tk.Label(inner, text=label.upper(), font=(FONT, 8),
                               fg=C_TEXT_DIM, bg=C_BG_RAISED, anchor="w")
        self._label.pack(fill=tk.X)

        self._value = tk.Label(inner, text=value, font=(FONT_MONO, 18, "bold"),
                               fg=C_TEXT, bg=C_BG_RAISED, anchor="w")
        self._value.pack(fill=tk.X)

    def set_value(self, text: str, color: str | None = None):
        self._value.config(text=text, fg=color or C_TEXT)


class StatusDot(tk.Canvas):
    """Small colored status indicator dot."""

    def __init__(self, parent, color: str = C_GREEN, size: int = 10, **kw):
        super().__init__(parent, width=size, height=size,
                         bg=C_BG, highlightthickness=0, **kw)
        self._size = size
        self._color = color
        self._draw()

    def _draw(self):
        self.delete("all")
        s = self._size
        self.create_oval(1, 1, s - 1, s - 1, fill=self._color, outline="")

    def set_color(self, color: str):
        self._color = color
        self._draw()


class ReadOnlyText(tk.Text):
    """Selectable but non-editable text widget."""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.bind("<KeyPress>", self._block)

    def _block(self, e):
        if e.state & 0x4:  # Ctrl held.
            return None
        if e.keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R"):
            return None
        return "break"


# ── Data Helpers ─────────────────────────────────────────────────────────

def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    items = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return items


def _load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _db_connect() -> sqlite3.Connection | None:
    db = Path("data/state/active.sqlite3")
    if not db.exists():
        return None
    try:
        uri = f"file:{db.resolve()}?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error:
        return None


# ── Main Dashboard ───────────────────────────────────────────────────────

class Dashboard:
    """Main application window."""

    def __init__(self, project_root: str):
        self.root_path = Path(project_root)
        self.data = self.root_path / "data"
        self.signals_path = self.data / "signals.jsonl"
        self.perf_path = self.data / "performance.jsonl"
        self.pools_path = self.data / "pools_cache.json"

        self._process = None
        self._stop = threading.Event()
        self._sig_pos = 0
        self._log_lines = 0
        self._start_time = time.time()

        self._build_window()
        self._build_tabs()
        self._start_bot()

        # Polling loops.
        self.root.after(200, self._poll_signals)
        self.root.after(1000, self._refresh_all)
        self.root.after(500, self._tick_timer)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Window Setup ─────────────────────────────────────────────────────

    def _build_window(self):
        self.root = tk.Tk()
        self.root.title("MEXC × DEX Arbitrage Monitor")
        self.root.configure(bg=C_BG)
        try:
            self.root.state("zoomed")
        except tk.TclError:
            w, h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.geometry(f"{w}x{h}+0+0")

        # Style.
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=C_BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=C_BG_RAISED, foreground=C_TEXT_DIM,
                        font=(FONT, 10, "bold"), padding=[16, 6], borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", C_BG)],
                  foreground=[("selected", C_BLUE)])
        style.configure("Treeview", background=C_BG_RAISED, foreground=C_TEXT,
                        fieldbackground=C_BG_RAISED, font=(FONT_MONO, 9),
                        rowheight=24, borderwidth=0)
        style.configure("Treeview.Heading", background=C_BG, foreground=C_BLUE,
                        font=(FONT, 9, "bold"), relief=tk.FLAT)
        style.map("Treeview", background=[("selected", "#1f6feb")],
                  foreground=[("selected", C_BLUE)])

        # ── Top bar ──────────────────────────────────────────────────────
        top = tk.Frame(self.root, bg=C_BG, height=48)
        top.pack(fill=tk.X, padx=16, pady=(12, 0))
        top.pack_propagate(False)

        tk.Label(top, text="⚡ MEXC × DEX Arbitrage", font=(FONT, 16, "bold"),
                 fg=C_TEXT, bg=C_BG).pack(side=tk.LEFT)

        self._status_dot = StatusDot(top, C_YELLOW, 12)
        self._status_dot.pack(side=tk.LEFT, padx=(12, 4))
        self._status_label = tk.Label(top, text="Starting...", font=(FONT, 10),
                                      fg=C_YELLOW, bg=C_BG)
        self._status_label.pack(side=tk.LEFT)

        # Right side metrics.
        self._timer_label = tk.Label(top, text="00:00", font=(FONT_MONO, 11),
                                     fg=C_TEXT_DIM, bg=C_BG)
        self._timer_label.pack(side=tk.RIGHT, padx=(8, 0))

        self._sig_count = tk.Label(top, text="0 signals", font=(FONT, 10, "bold"),
                                   fg=C_GREEN, bg=C_BG)
        self._sig_count.pack(side=tk.RIGHT, padx=(16, 0))

        self._pool_count = tk.Label(top, text="0 pools", font=(FONT, 10),
                                    fg=C_CYAN, bg=C_BG)
        self._pool_count.pack(side=tk.RIGHT, padx=(16, 0))

        self._cycle_count = tk.Label(top, text="0 cycles", font=(FONT, 10),
                                     fg=C_ORANGE, bg=C_BG)
        self._cycle_count.pack(side=tk.RIGHT, padx=(16, 0))

        # Separator.
        tk.Frame(self.root, bg=C_BORDER, height=1).pack(fill=tk.X, padx=16, pady=(8, 0))

    def _build_tabs(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 8))

        self._build_tab_signals()
        self._build_tab_log()
        self._build_tab_overview()
        self._build_tab_scanner()
        self._build_tab_pools()

    # ── Tab: Signals (primary view) ──────────────────────────────────────

    def _build_tab_signals(self):
        frame = tk.Frame(self.nb, bg=C_BG)
        self.nb.add(frame, text="  💰 Signals  ")

        # Summary cards row.
        cards = tk.Frame(frame, bg=C_BG)
        cards.pack(fill=tk.X, padx=12, pady=(8, 4))

        self._c_total = Card(cards, "Total Signals", "0", C_GREEN)
        self._c_total.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self._c_clean = Card(cards, "Clean", "0", C_CYAN)
        self._c_clean.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self._c_median = Card(cards, "Median Profit", "—", C_BLUE)
        self._c_median.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self._c_max = Card(cards, "Max Profit", "—", C_PURPLE)
        self._c_max.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        # Signal table.
        cols = ("time", "network", "token", "dex", "dir", "gross%", "net%", "net$", "warnings")
        widths = {"time": 70, "network": 80, "token": 70, "dex": 110,
                  "dir": 130, "gross%": 80, "net%": 80, "net$": 90, "warnings": 200}
        heads = {"time": "Time", "network": "Network", "token": "Token",
                 "dex": "DEX", "dir": "Direction", "gross%": "Gross %",
                 "net%": "Net %", "net$": "Net $", "warnings": "Warnings"}

        tf = tk.Frame(frame, bg=C_BG)
        tf.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 8))

        self._sig_tree = ttk.Treeview(tf, columns=cols, show="headings", height=20)
        for c in cols:
            self._sig_tree.heading(c, text=heads[c])
            self._sig_tree.column(c, width=widths.get(c, 80), anchor=tk.W)

        sb = tk.Scrollbar(tf, orient=tk.VERTICAL, command=self._sig_tree.yview)
        self._sig_tree.configure(yscrollcommand=sb.set)
        self._sig_tree.tag_configure("profit", foreground=C_GREEN)
        self._sig_tree.tag_configure("high", foreground=C_PURPLE)
        self._sig_tree.tag_configure("warn", foreground=C_YELLOW)
        self._sig_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    # ── Tab: Activity Log ────────────────────────────────────────────────

    def _build_tab_log(self):
        frame = tk.Frame(self.nb, bg=C_BG)
        self.nb.add(frame, text="  📋 Log  ")

        hdr = tk.Frame(frame, bg=C_BG)
        hdr.pack(fill=tk.X, padx=12, pady=(8, 2))
        tk.Label(hdr, text="Bot Output", font=(FONT, 11, "bold"),
                 fg=C_TEXT, bg=C_BG).pack(side=tk.LEFT)
        self._log_count = tk.Label(hdr, text="0 lines", font=(FONT, 9),
                                   fg=C_TEXT_DIM, bg=C_BG)
        self._log_count.pack(side=tk.RIGHT)

        self._log_text = ReadOnlyText(
            frame, wrap=tk.WORD, font=(FONT_MONO, 9),
            bg=C_BG_INSET, fg=C_TEXT, insertbackground=C_TEXT,
            relief=tk.FLAT, padx=10, pady=8,
            selectbackground="#1f6feb", selectforeground=C_BLUE)
        lsb = tk.Scrollbar(frame, orient=tk.VERTICAL, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=lsb.set)

        for tag, color in [("err", C_RED), ("warn", C_YELLOW), ("sig", C_GREEN),
                           ("info", C_BLUE), ("init", C_ORANGE), ("dim", C_TEXT_FAINT),
                           ("default", C_TEXT)]:
            self._log_text.tag_configure(tag, foreground=color)

        self._log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0), pady=(0, 8))
        lsb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 12), pady=(0, 8))

    # ── Tab: Overview ────────────────────────────────────────────────────

    def _build_tab_overview(self):
        frame = tk.Frame(self.nb, bg=C_BG)
        self.nb.add(frame, text="  📊 Overview  ")

        sf = tk.Frame(frame, bg=C_BG)
        sf.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        # Cards row 1.
        r1 = tk.Frame(sf, bg=C_BG)
        r1.pack(fill=tk.X, pady=(0, 4))
        self._ov_pools = Card(r1, "Pools", "—", C_CYAN)
        self._ov_pools.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self._ov_assets = Card(r1, "MEXC Assets", "—", C_PURPLE)
        self._ov_assets.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self._ov_cycles = Card(r1, "Scan Cycles", "—", C_ORANGE)
        self._ov_cycles.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self._ov_uptime = Card(r1, "Uptime", "—", C_TEXT_DIM)
        self._ov_uptime.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        # Cards row 2.
        r2 = tk.Frame(sf, bg=C_BG)
        r2.pack(fill=tk.X, pady=(0, 8))
        self._ov_errors = Card(r2, "Errors", "0", C_RED)
        self._ov_errors.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self._ov_warns = Card(r2, "Warnings", "0", C_YELLOW)
        self._ov_warns.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self._ov_quotes = Card(r2, "Quotes OK", "—", C_GREEN)
        self._ov_quotes.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self._ov_duration = Card(r2, "Avg Cycle", "—", C_BLUE)
        self._ov_duration.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        # Network distribution.
        tk.Label(sf, text="Network Distribution", font=(FONT, 11, "bold"),
                 fg=C_TEXT, bg=C_BG).pack(anchor=tk.W, pady=(4, 2))
        self._net_text = ReadOnlyText(sf, height=6, font=(FONT_MONO, 10),
                                      bg=C_BG_INSET, fg=C_TEXT, relief=tk.FLAT, padx=8, pady=6)
        self._net_text.pack(fill=tk.X)

        # DEX distribution.
        tk.Label(sf, text="DEX Distribution", font=(FONT, 11, "bold"),
                 fg=C_TEXT, bg=C_BG).pack(anchor=tk.W, pady=(8, 2))
        self._dex_text = ReadOnlyText(sf, height=6, font=(FONT_MONO, 10),
                                      bg=C_BG_INSET, fg=C_TEXT, relief=tk.FLAT, padx=8, pady=6)
        self._dex_text.pack(fill=tk.X)

    # ── Tab: Scanner ─────────────────────────────────────────────────────

    def _build_tab_scanner(self):
        frame = tk.Frame(self.nb, bg=C_BG)
        self.nb.add(frame, text="  ⚡ Scanner  ")

        cols = ("time", "pools", "quotes", "failed", "signals", "ms")
        heads = {"time": "Time", "pools": "Pools", "quotes": "Quotes OK",
                 "failed": "Failed", "signals": "Signals", "ms": "Duration ms"}
        widths = {"time": 80, "pools": 70, "quotes": 80, "failed": 70,
                  "signals": 70, "ms": 100}

        tf = tk.Frame(frame, bg=C_BG)
        tf.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        self._scan_tree = ttk.Treeview(tf, columns=cols, show="headings", height=20)
        for c in cols:
            self._scan_tree.heading(c, text=heads[c])
            self._scan_tree.column(c, width=widths.get(c, 80), anchor=tk.W)
        sb = tk.Scrollbar(tf, orient=tk.VERTICAL, command=self._scan_tree.yview)
        self._scan_tree.configure(yscrollcommand=sb.set)
        self._scan_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    # ── Tab: Pools ───────────────────────────────────────────────────────

    def _build_tab_pools(self):
        frame = tk.Frame(self.nb, bg=C_BG)
        self.nb.add(frame, text="  🏊 Pools  ")

        cols = ("network", "dex", "pool", "token", "stable", "ver")
        heads = {"network": "Network", "dex": "DEX", "pool": "Pool Address",
                 "token": "Token", "stable": "Stablecoin", "ver": "Ver"}
        widths = {"network": 80, "dex": 120, "pool": 300, "token": 150,
                  "stable": 150, "ver": 50}

        tf = tk.Frame(frame, bg=C_BG)
        tf.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        self._pool_tree = ttk.Treeview(tf, columns=cols, show="headings", height=20)
        for c in cols:
            self._pool_tree.heading(c, text=heads[c])
            self._pool_tree.column(c, width=widths.get(c, 100), anchor=tk.W)
        sb = tk.Scrollbar(tf, orient=tk.VERTICAL, command=self._pool_tree.yview)
        self._pool_tree.configure(yscrollcommand=sb.set)
        self._pool_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    # ── Bot Subprocess ───────────────────────────────────────────────────

    def _start_bot(self):
        main_path = self.root_path / "main.py"
        env = os.environ.copy()
        si = None
        if sys.platform == "win32":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        self._process = subprocess.Popen(
            [sys.executable, str(main_path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, cwd=str(self.root_path),
            bufsize=1, universal_newlines=True, startupinfo=si)
        self._status_dot.set_color(C_GREEN)
        self._status_label.config(text="Running", fg=C_GREEN)
        threading.Thread(target=self._read_stdout, daemon=True).start()

    def _read_stdout(self):
        try:
            for line in iter(self._process.stdout.readline, ""):
                if self._stop.is_set():
                    break
                if line:
                    self.root.after(0, self._on_log_line, line.rstrip())
        except Exception:
            pass
        finally:
            self.root.after(0, self._on_bot_exit)

    def _on_log_line(self, line: str):
        self._append_log(line)
        self._parse_log(line)

    def _append_log(self, line: str):
        if "ERROR" in line or "error=" in line.lower():
            tag = "err"
        elif "WARNING" in line:
            tag = "warn"
        elif '"net_profit_pct"' in line:
            tag = "sig"
        elif "scanner_cycle" in line or "pool_refresh" in line:
            tag = "info"
        elif "app_initializing" in line or "app_starting" in line:
            tag = "init"
        elif "httpx" in line:
            tag = "dim"
        else:
            tag = "default"

        at_bottom = self._log_text.yview()[1] >= 0.98
        self._log_text.insert(tk.END, line + "\n", (tag,))
        if at_bottom:
            self._log_text.see(tk.END)
        self._log_lines += 1
        self._log_count.config(text=f"{self._log_lines} lines")

    def _parse_log(self, line: str):
        if "scanner_cycle" in line:
            ts = time.strftime("%H:%M:%S")
            vals = {"pools": "—", "ok": "—", "failed": "—", "signals": "—"}
            for tok in line.split():
                for k in vals:
                    if tok.startswith(f"{k}=") or tok.startswith(f"quote={k}="):
                        vals[k] = tok.split("=")[-1]
                if "pools=" in tok:
                    vals["pools"] = tok.split("=")[-1]
                if "ok=" in tok:
                    vals["ok"] = tok.split("=")[-1]
                if "signals=" in tok:
                    vals["signals"] = tok.split("=")[-1]
            self._scan_tree.insert("", 0, values=(
                ts, vals["pools"], vals["ok"], vals.get("failed", "—"),
                vals["signals"], "—"))
            n = len(self._scan_tree.get_children())
            self._cycle_count.config(text=f"{n} cycles")

    def _on_bot_exit(self):
        self._status_dot.set_color(C_RED)
        self._status_label.config(text="Stopped", fg=C_RED)
        self._append_log("[GUI] Bot process exited.")

    # ── Signal Polling ───────────────────────────────────────────────────

    def _poll_signals(self):
        if self._stop.is_set():
            return
        try:
            if self.signals_path.exists():
                with open(self.signals_path, encoding="utf-8") as f:
                    f.seek(self._sig_pos)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            sig = json.loads(line)
                            self._add_signal(sig)
                        except json.JSONDecodeError:
                            pass
                    self._sig_pos = f.tell()
        except OSError:
            pass
        if not self._stop.is_set():
            self.root.after(2000, self._poll_signals)

    def _add_signal(self, sig: dict):
        ts = (sig.get("timestamp") or "")[11:19]
        net_pct = sig.get("net_profit_pct", 0)
        try:
            net_pct_f = float(net_pct)
        except (ValueError, TypeError):
            net_pct_f = 0

        tag = "profit"
        if net_pct_f > 10:
            tag = "high"
        elif sig.get("warnings"):
            tag = "warn"

        vals = (
            ts, sig.get("network", ""), sig.get("token_coin", ""),
            sig.get("dex", ""), sig.get("direction", ""),
            f'{sig.get("gross_profit_pct", "")}%',
            f'{net_pct}%',
            f'${sig.get("net_profit_usd", "")}',
            ", ".join(sig.get("warnings", []) or []))
        self._sig_tree.insert("", 0, values=vals, tags=(tag,))

        n = len(self._sig_tree.get_children())
        self._sig_count.config(text=f"{n} signals")

        # Flash status.
        self._status_label.config(text="💰 SIGNAL!", fg=C_GREEN)
        self.root.after(2000, lambda: self._status_label.config(
            text="Running" if self._process and self._process.poll() is None else "Stopped",
            fg=C_GREEN if self._process and self._process.poll() is None else C_RED))

    # ── Periodic Refresh ─────────────────────────────────────────────────

    def _refresh_all(self):
        if self._stop.is_set():
            return
        try:
            self._refresh_overview()
        except Exception as e:
            self._append_log(f"[GUI] refresh error: {e}")
        try:
            self._refresh_pools()
        except Exception as e:
            self._append_log(f"[GUI] pools refresh error: {e}")
        if not self._stop.is_set():
            self.root.after(5000, self._refresh_all)

    def _refresh_overview(self):
        signals = _load_jsonl(self.signals_path)
        pools = _load_json(self.pools_path)
        perf = _load_jsonl(self.perf_path)

        total = len(signals)
        clean = sum(1 for s in signals if not s.get("warnings"))
        profits = [float(s.get("net_profit_pct", 0)) for s in signals
                   if s.get("net_profit_pct")]
        median_p = sorted(profits)[len(profits) // 2] if profits else 0
        max_p = max(profits) if profits else 0

        self._c_total.set_value(str(total))
        self._c_clean.set_value(str(clean))
        self._c_median.set_value(f"{median_p:.1f}%")
        self._c_max.set_value(f"{max_p:.1f}%")

        self._ov_pools.set_value(str(len(pools)))
        self._pool_count.config(text=f"{len(pools)} pools")

        # DB stats.
        conn = _db_connect()
        if conn:
            try:
                assets = conn.execute("SELECT COUNT(*) FROM mexc_assets").fetchone()[0]
                self._ov_assets.set_value(str(assets))
            except sqlite3.Error:
                pass
            conn.close()

        # Scanner stats.
        scanner_recs = [r for r in perf if r.get("stage") == "scanner_cycle"]
        if scanner_recs:
            self._ov_cycles.set_value(str(len(scanner_recs)))
            durs = [r.get("duration_ms", 0) for r in scanner_recs]
            self._ov_duration.set_value(f"{sum(durs) / len(durs):.0f}ms")
            self._ov_quotes.set_value(str(sum(r.get("items_success", 0) for r in scanner_recs)))

        # Uptime.
        u = int(time.time() - self._start_time)
        h, m = divmod(u // 60, 60)
        self._ov_uptime.set_value(f"{h}h {m}m")

        # Distributions.
        nets = Counter(s.get("network", "?") for s in signals)
        dexes = Counter(s.get("dex", "?") for s in signals)

        self._net_text.delete("1.0", tk.END)
        if nets:
            mx = max(nets.values())
            lines = [f"  {n:<12} {'█' * max(1, int(c / mx * 30))} {c}"
                     for n, c in nets.most_common()]
            self._net_text.insert("1.0", "\n".join(lines))

        self._dex_text.delete("1.0", tk.END)
        if dexes:
            mx = max(dexes.values())
            lines = [f"  {d:<20} {'█' * max(1, int(c / mx * 25))} {c}"
                     for d, c in dexes.most_common()]
            self._dex_text.insert("1.0", "\n".join(lines))

    def _refresh_pools(self):
        pools = _load_json(self.pools_path)
        self._pool_tree.delete(*self._pool_tree.get_children())
        for p in pools[:200]:
            self._pool_tree.insert("", tk.END, values=(
                p.get("network", ""), p.get("dex", ""),
                (p.get("pool_address", "") or "")[:40],
                (p.get("token_address", "") or "")[:20],
                (p.get("stablecoin_address", "") or "")[:20],
                p.get("pool_version", "")))

    # ── Timer ────────────────────────────────────────────────────────────

    def _tick_timer(self):
        if self._stop.is_set():
            return
        elapsed = int(time.time() - self._start_time)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        self._timer_label.config(text=f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}")
        if not self._stop.is_set():
            self.root.after(1000, self._tick_timer)

    # ── Shutdown ─────────────────────────────────────────────────────────

    def _on_close(self):
        self._stop.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ── Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    project_root = str(Path(__file__).resolve().parent.parent)
    os.chdir(project_root)
    app = Dashboard(project_root)
    app.run()
