"""
GUI Dashboard for MEXC × DEX Arbitrage Monitor.

Tabbed read-only interface showing all bot data in real time:
  📋 Activity Log   — copyable coloured log
  📊 Dashboard      — summary cards, stats, distributions
  🔍 Discovery      — source health, candidate tokens, pools
  ⚡ Scanner        — cycles, quotes, performance
  💰 Signals        — arbitrage opportunities with profit stats
  📊 MEXC Data      — assets, prices, stablecoins, volumes
  🏦 Database       — all DB tables, rows, sample data
  ⚙️ Performance     — duration metrics, error timeline, log stats
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

# ── Colours ──────────────────────────────────────────────────────────────
BG_DARK = "#1a1a2e"
BG_MID = "#16213e"
BG_CARD = "#0f3460"
FG_TEXT = "#e0e0e0"
FG_DIM = "#8899aa"
FG_GREEN = "#00ff88"
FG_RED = "#ff4466"
FG_YELLOW = "#ffcc00"
FG_CYAN = "#00ddff"
FG_ORANGE = "#ff8844"
FG_PURPLE = "#bc8cff"
BG_ROW_EVEN = BG_MID
BG_ROW_ODD = "#1c2a4a"


# ── Helper: read-only but selectable Text widget ─────────────────────────
class ReadOnlyText(tk.Text):
    """Text widget that is selectable/copyable but not editable."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bind("<KeyPress>", self._block_edit)

    def _block_edit(self, event):
        if event.state & 0x0004:
            return None
        if event.keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R"):
            return None
        return "break"


def _build_treeview(parent, columns, col_widths, headings, height=8):
    tree = ttk.Treeview(
        parent, columns=columns, show="headings",
        height=height, style="Dark.Treeview",
    )
    for col in columns:
        tree.heading(col, text=headings[col])
        tree.column(col, width=col_widths.get(col, 100), anchor=tk.W)
    scroll = tk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.tag_configure("even", background=BG_ROW_EVEN)
    tree.tag_configure("odd", background=BG_ROW_ODD)
    return tree, scroll


def _pack_treeview(tree, scroll, side=tk.LEFT, fill=tk.BOTH, expand=True):
    tree.pack(side=side, fill=fill, expand=expand)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)


def _alt_tag(tree):
    return "even" if len(tree.get_children()) % 2 == 0 else "odd"


def _build_label(parent, text, font_size=9, bold=False, fg=FG_TEXT):
    return tk.Label(
        parent, text=text,
        font=("Consolas", font_size, "bold" if bold else "normal"),
        fg=fg, bg=BG_DARK,
    )


def _build_card(parent, label, value, fg=FG_CYAN):
    frame = tk.Frame(parent, bg=BG_MID, highlightbackground=BG_CARD,
                     highlightthickness=1, padx=8, pady=6)
    frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3, pady=2)
    _build_label(frame, label, 7, fg=fg).pack(anchor=tk.W)
    val = _build_label(frame, value, 16, bold=True, fg=FG_TEXT)
    val.pack(anchor=tk.W)
    return val


def _make_scrollable(frame):
    """Wrap a frame in a Canvas+Scrollbar and return the inner scroll frame."""
    canvas = tk.Canvas(frame, bg=BG_DARK, highlightthickness=0)
    scrollbar = tk.Scrollbar(frame, orient=tk.VERTICAL, command=canvas.yview)
    sf = tk.Frame(canvas, bg=BG_DARK)
    sf.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=sf, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    return sf


# ── Data helpers ─────────────────────────────────────────────────────────

def _load_signals(path: Path) -> list[dict]:
    if not path.exists():
        return []
    signals = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    signals.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return signals


def _load_perf(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def _load_pools(path: Path) -> list[dict]:
    if not path.exists():
        return []
    for attempt in range(2):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            if attempt == 0:
                time.sleep(0.5)
                continue
            return []
    return []


def _db_connect() -> sqlite3.Connection | None:
    db = Path("data/state/active.sqlite3")
    if not db.exists():
        return None
    try:
        # G4: Use read-only URI + busy_timeout to avoid locking during refresh.
        uri = f"file:{db.resolve()}?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error:
        return None


# ══════════════════════════════════════════════════════════════════════════
# Dashboard
# ══════════════════════════════════════════════════════════════════════════

class Dashboard:
    """Main dashboard window with tabbed interface and full bot telemetry."""

    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.data_dir = self.project_root / "data"
        self.signals_path = self.data_dir / "signals.jsonl"
        self.perf_path = self.data_dir / "performance.jsonl"
        self.pools_path = self.data_dir / "pools_cache.json"
        self.logs_dir = self.project_root / ".run" / "logs"
        self._gui_log_path = self.project_root / ".run" / "logs" / "gui_errors.log"

        # Ensure log dir exists
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self._last_signal_pos = 0
        self._process = None
        self._stop_event = threading.Event()
        self._last_log_line = 0

        # Window
        self.root = tk.Tk()
        self.root.title("MEXC × DEX Arbitrage Monitor")
        self.root.configure(bg=BG_DARK)
        try:
            self.root.state("zoomed")
        except tk.TclError:
            w = self.root.winfo_screenwidth()
            h = self.root.winfo_screenheight()
            self.root.geometry(f"{w}x{h}+0+0")

        # Title bar
        title_frame = tk.Frame(self.root, bg=BG_DARK, height=40)
        title_frame.pack(fill=tk.X, padx=12, pady=(8, 0))
        _build_label(title_frame, "MEXC × DEX Arbitrage Monitor",
                     16, bold=True, fg=FG_CYAN).pack(side=tk.LEFT)
        self._stage_label = _build_label(title_frame, "Stage: —", 9, fg=FG_DIM)
        self._stage_label.pack(side=tk.LEFT, padx=16)
        self._title_sig_label = _build_label(title_frame, "Sig: 0", 9, fg=FG_GREEN)
        self._title_sig_label.pack(side=tk.LEFT, padx=4)
        self._title_pool_label = _build_label(title_frame, "Pools: 0", 9, fg=FG_CYAN)
        self._title_pool_label.pack(side=tk.LEFT, padx=4)
        self._title_cycle_label = _build_label(title_frame, "Cyc: 0", 9, fg=FG_ORANGE)
        self._title_cycle_label.pack(side=tk.LEFT, padx=4)
        self.status_label = _build_label(title_frame, "⏳ Initialising...", 10, fg=FG_YELLOW)
        self.status_label.pack(side=tk.RIGHT, padx=8)
        self.timer_label = _build_label(title_frame, "00:00", 10, fg=FG_DIM)
        self.timer_label.pack(side=tk.RIGHT, padx=8)
        self._start_time = time.time()

        # Style
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TNotebook", background=BG_DARK, borderwidth=0)
        style.configure("Dark.TNotebook.Tab", background=BG_MID, foreground=FG_TEXT,
                        font=("Consolas", 10, "bold"), padding=[14, 4], borderwidth=0)
        style.map("Dark.TNotebook.Tab", background=[("selected", BG_CARD)],
                  foreground=[("selected", FG_CYAN)])
        style.configure("Dark.Treeview", background=BG_MID, foreground=FG_TEXT,
                        fieldbackground=BG_MID, font=("Consolas", 9), rowheight=22)
        style.configure("Dark.Treeview.Heading", background=BG_CARD, foreground=FG_CYAN,
                        font=("Consolas", 9, "bold"), relief=tk.FLAT)
        style.map("Dark.Treeview", background=[("selected", BG_CARD)],
                  foreground=[("selected", FG_GREEN)])

        self.notebook = ttk.Notebook(self.root, style="Dark.TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

        # Build 8 tabs
        self._build_log_tab()
        self._build_dashboard_tab()
        self._build_discovery_tab()
        self._build_scanner_tab()
        self._build_signals_tab()
        self._build_mexc_tab()
        self._build_db_tab()
        self._build_perf_tab()

        # Start health/metrics HTTP server
        self._health_server = None
        try:
            from metrics.health import start_health_server, update_metrics
            self._health_server = start_health_server(port=8765)
            update_metrics(started_at=time.time())
            self._append_log("[GUI] Health server started on http://127.0.0.1:8765")
        except Exception as exc:
            self._append_log(f"[GUI] Health server failed: {exc}")

        # Start background tasks
        self.root.after(100, self._update_timer)
        self.root.after(300, self._start_bot)
        self.root.after(600, self._poll_signals)
        self.root.after(1000, self._refresh_dashboard)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ══════════════════════════════════════════════════════════════════════
    # TAB 1: 📋 Activity Log
    # ══════════════════════════════════════════════════════════════════════
    def _build_log_tab(self):
        frame = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(frame, text="  📋 Activity Log  ")
        ctrl = tk.Frame(frame, bg=BG_DARK)
        ctrl.pack(fill=tk.X, padx=4, pady=(4, 2))
        _build_label(ctrl, "Bot stdout — coloured: ", 9, fg=FG_DIM).pack(side=tk.LEFT)
        for tag, color, label in [
            ("red", FG_RED, "ERROR"), ("yellow", FG_YELLOW, "WARN"),
            ("green", FG_GREEN, "SIGNAL"), ("cyan", FG_CYAN, "SCAN"),
            ("orange", FG_ORANGE, "INIT"), ("dim", FG_DIM, "DEBUG"),
        ]:
            tk.Label(ctrl, text=label, font=("Consolas", 8),
                     fg=color, bg=BG_MID, padx=4).pack(side=tk.LEFT, padx=2)
        self._log_line_label = _build_label(ctrl, "0 lines", 9, fg=FG_DIM)
        self._log_line_label.pack(side=tk.RIGHT, padx=4)

        text_frame = tk.Frame(frame, bg=BG_DARK)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        self.log_text = ReadOnlyText(
            text_frame, wrap=tk.WORD, font=("Consolas", 9),
            bg=BG_MID, fg=FG_TEXT, insertbackground=FG_TEXT,
            relief=tk.FLAT, borderwidth=0, padx=8, pady=6,
            selectbackground=BG_CARD, selectforeground=FG_CYAN)
        log_scroll = tk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        for tag, color in [("red", FG_RED), ("yellow", FG_YELLOW), ("green", FG_GREEN),
                           ("cyan", FG_CYAN), ("orange", FG_ORANGE),
                           ("default", FG_TEXT), ("dim", FG_DIM)]:
            self.log_text.tag_configure(tag, foreground=color)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # ══════════════════════════════════════════════════════════════════════
    # TAB 2: 📊 Dashboard — summary cards + stats
    # ══════════════════════════════════════════════════════════════════════
    def _build_dashboard_tab(self):
        frame = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(frame, text="  📊 Dashboard  ")
        sf = _make_scrollable(frame)

        _build_label(sf, "Bot Overview", 14, bold=True, fg=FG_CYAN).pack(anchor=tk.W, padx=8, pady=(8, 4))
        tk.Frame(sf, bg=BG_CARD, height=1).pack(fill=tk.X, padx=8)

        c1 = tk.Frame(sf, bg=BG_DARK)
        c1.pack(fill=tk.X, padx=8, pady=6)
        self._card_sigs = _build_card(c1, "SIGNALS", "—")
        self._card_clean = _build_card(c1, "CLEAN", "—", FG_GREEN)
        self._card_pools = _build_card(c1, "POOLS", "—", FG_CYAN)
        self._card_assets = _build_card(c1, "ASSETS", "—", FG_PURPLE)
        self._card_cycles = _build_card(c1, "CYCLES", "—", FG_ORANGE)
        self._card_profit = _build_card(c1, "MEDIAN PROFIT", "—", FG_GREEN)

        c2 = tk.Frame(sf, bg=BG_DARK)
        c2.pack(fill=tk.X, padx=8, pady=2)
        self._card_thin = _build_card(c2, "THIN LIQ %", "—", FG_YELLOW)
        self._card_errors = _build_card(c2, "ERRORS", "—", FG_RED)
        self._card_warns = _build_card(c2, "WARNINGS", "—", FG_YELLOW)
        self._card_db_pools = _build_card(c2, "DB POOLS", "—", FG_CYAN)
        self._card_sources = _build_card(c2, "SOURCES", "—", FG_PURPLE)
        self._card_uptime = _build_card(c2, "UPTIME", "—", FG_CYAN)

        # Network distribution
        _build_label(sf, "Network Distribution (Signals)", 11, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(10, 2))
        nf = tk.Frame(sf, bg=BG_DARK)
        nf.pack(fill=tk.X, padx=8, pady=2)
        self._net_tree, ns = _build_treeview(nf, ("network", "signals", "bar"),
            {"network": 120, "signals": 80, "bar": 400},
            {"network": "Network", "signals": "Signals", "bar": "Distribution"}, height=4)
        _pack_treeview(self._net_tree, ns)

        # DEX distribution
        _build_label(sf, "DEX Distribution (Signals)", 11, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(6, 2))
        df = tk.Frame(sf, bg=BG_DARK)
        df.pack(fill=tk.X, padx=8, pady=2)
        self._dex_tree, ds = _build_treeview(df, ("dex", "signals", "bar"),
            {"dex": 140, "signals": 80, "bar": 380},
            {"dex": "DEX", "signals": "Signals", "bar": "Distribution"}, height=4)
        _pack_treeview(self._dex_tree, ds)

        # Pool distribution
        _build_label(sf, "Pool Distribution (Cache)", 11, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(6, 2))
        pf = tk.Frame(sf, bg=BG_DARK)
        pf.pack(fill=tk.X, padx=8, pady=2)
        self._pool_net_tree, ps = _build_treeview(pf, ("network", "pools", "bar"),
            {"network": 120, "pools": 80, "bar": 400},
            {"network": "Network", "pools": "Pools", "bar": "Distribution"}, height=4)
        _pack_treeview(self._pool_net_tree, ps)

        # Warnings breakdown
        _build_label(sf, "Signal Warnings Breakdown", 11, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(6, 2))
        wf = tk.Frame(sf, bg=BG_DARK)
        wf.pack(fill=tk.X, padx=8, pady=2)
        self._warn_tree, ws = _build_treeview(wf, ("warning", "count", "bar"),
            {"warning": 320, "count": 80, "bar": 200},
            {"warning": "Warning Type", "count": "Count", "bar": ""}, height=4)
        _pack_treeview(self._warn_tree, ws)

    # ══════════════════════════════════════════════════════════════════════
    # TAB 3: 🔍 Discovery
    # ══════════════════════════════════════════════════════════════════════
    def _build_discovery_tab(self):
        frame = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(frame, text="  🔍 Discovery  ")
        sf = _make_scrollable(frame)

        _build_label(sf, "Pool Discovery Stage", 11, bold=True, fg=FG_CYAN).pack(anchor=tk.W, padx=6, pady=(6, 2))
        _build_label(sf, "Source Health", 9, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(2, 0))
        sf1 = tk.Frame(sf, bg=BG_DARK, height=90)
        sf1.pack(fill=tk.X, padx=8, pady=(0, 4)); sf1.pack_propagate(False)
        self.src_tree, ss = _build_treeview(sf1, ("source", "healthy", "failures", "last_error"),
            {"source": 160, "healthy": 70, "failures": 70, "last_error": 400},
            {"source": "Source", "healthy": "Healthy", "failures": "Failures", "last_error": "Last Error"}, height=3)
        _pack_treeview(self.src_tree, ss)

        _build_label(sf, "Candidate Tokens (from DB)", 9, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(2, 0))
        cf = tk.Frame(sf, bg=BG_DARK)
        cf.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.cand_tree, cs = _build_treeview(cf, ("coin", "network", "contract", "quote", "pools_found"),
            {"coin": 80, "network": 80, "contract": 280, "quote": 60, "pools_found": 90},
            {"coin": "Coin", "network": "Network", "contract": "Contract", "quote": "Quote", "pools_found": "Pools Found"}, height=5)
        _pack_treeview(self.cand_tree, cs)

        _build_label(sf, "Discovered Pools (from cache)", 9, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(2, 0))
        pf = tk.Frame(sf, bg=BG_DARK, height=150)
        pf.pack(fill=tk.X, padx=8, pady=(0, 6)); pf.pack_propagate(False)
        self.pool_tree, ps2 = _build_treeview(pf, ("network", "dex", "pool_address", "token0", "token1"),
            {"network": 80, "dex": 120, "pool_address": 280, "token0": 130, "token1": 130},
            {"network": "Network", "dex": "DEX", "pool_address": "Pool Address", "token0": "Token", "token1": "Stablecoin"}, height=5)
        _pack_treeview(self.pool_tree, ps2)

    # ══════════════════════════════════════════════════════════════════════
    # TAB 4: ⚡ Scanner
    # ══════════════════════════════════════════════════════════════════════
    def _build_scanner_tab(self):
        frame = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(frame, text="  ⚡ Scanner  ")
        sf = _make_scrollable(frame)

        _build_label(sf, "Scanner Stage", 11, bold=True, fg=FG_CYAN).pack(anchor=tk.W, padx=6, pady=(6, 2))
        sc = tk.Frame(sf, bg=BG_DARK)
        sc.pack(fill=tk.X, padx=8, pady=4)
        self._sc_pools = _build_card(sc, "POOLS/CYCLE", "—", FG_CYAN)
        self._sc_quotes = _build_card(sc, "QUOTES OK", "—", FG_GREEN)
        self._sc_failed = _build_card(sc, "QUOTES FAIL", "—", FG_RED)
        self._sc_signals = _build_card(sc, "SIGNALS/CYCLE", "—", FG_YELLOW)
        self._sc_duration = _build_card(sc, "DURATION", "—", FG_ORANGE)
        self._sc_cycles = _build_card(sc, "CYCLES", "—", FG_PURPLE)

        _build_label(sf, "Scanner Cycles (from perf data)", 9, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(4, 0))
        cyc_f = tk.Frame(sf, bg=BG_DARK, height=140)
        cyc_f.pack(fill=tk.X, padx=8, pady=(0, 4)); cyc_f.pack_propagate(False)
        self.cyc_tree, cys = _build_treeview(cyc_f, ("time", "pools", "quotes_ok", "quotes_fail", "signals", "duration_ms"),
            {"time": 100, "pools": 75, "quotes_ok": 80, "quotes_fail": 85, "signals": 70, "duration_ms": 90},
            {"time": "Time", "pools": "Pools", "quotes_ok": "Quote OK", "quotes_fail": "Quote Fail", "signals": "Signals", "duration_ms": "Duration (ms)"}, height=4)
        _pack_treeview(self.cyc_tree, cys)

        _build_label(sf, "Recent DEX Quotes", 9, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(2, 0))
        qf = tk.Frame(sf, bg=BG_DARK)
        qf.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))
        self.q_tree, qs = _build_treeview(qf, ("time", "network", "dex", "pool_short", "direction", "amount_out", "success"),
            {"time": 90, "network": 70, "dex": 110, "pool_short": 220, "direction": 95, "amount_out": 100, "success": 65},
            {"time": "Time", "network": "Network", "dex": "DEX", "pool_short": "Pool", "direction": "Direction", "amount_out": "Amount Out", "success": "OK"}, height=6)
        _pack_treeview(self.q_tree, qs)

    # ══════════════════════════════════════════════════════════════════════
    # TAB 5: 💰 Signals
    # ══════════════════════════════════════════════════════════════════════
    def _build_signals_tab(self):
        frame = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(frame, text="  💰 Signals  ")
        hf = tk.Frame(frame, bg=BG_DARK)
        hf.pack(fill=tk.X, padx=8, pady=(6, 2))
        _build_label(hf, "Arbitrage Signals", 11, bold=True, fg=FG_GREEN).pack(side=tk.LEFT)
        self.signal_count_label = _build_label(hf, "0 signals", 10, fg=FG_DIM)
        self.signal_count_label.pack(side=tk.RIGHT, padx=8)

        sc = tk.Frame(frame, bg=BG_DARK)
        sc.pack(fill=tk.X, padx=8, pady=2)
        self._sig_total = _build_card(sc, "TOTAL", "—", FG_CYAN)
        self._sig_clean = _build_card(sc, "CLEAN", "—", FG_GREEN)
        self._sig_thin = _build_card(sc, "THIN LIQ", "—", FG_YELLOW)
        self._sig_med = _build_card(sc, "MEDIAN %", "—", FG_ORANGE)
        self._sig_max = _build_card(sc, "MAX %", "—", FG_RED)
        self._sig_time = _build_card(sc, "TIME SPAN", "—", FG_DIM)

        sig_f = tk.Frame(frame, bg=BG_DARK)
        sig_f.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))
        self.sig_tree, sgs = _build_treeview(sig_f,
            ("ts", "network", "token", "dex", "direction", "gross_pct", "net_pct", "net_usd", "pool", "warnings"),
            {"ts": 80, "network": 70, "token": 70, "dex": 100, "direction": 80, "gross_pct": 70, "net_pct": 70, "net_usd": 70, "pool": 200, "warnings": 120},
            {"ts": "Time", "network": "Network", "token": "Token", "dex": "DEX", "direction": "Direction", "gross_pct": "Gross %", "net_pct": "Net %", "net_usd": "Net $", "pool": "Pool Addr", "warnings": "Warnings"}, height=12)
        _pack_treeview(self.sig_tree, sgs)

    # ══════════════════════════════════════════════════════════════════════
    # TAB 6: 📊 MEXC Data
    # ══════════════════════════════════════════════════════════════════════
    def _build_mexc_tab(self):
        frame = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(frame, text="  📊 MEXC Data  ")
        _build_label(frame, "MEXC Exchange Data (from DB)", 11, bold=True, fg=FG_CYAN).pack(anchor=tk.W, padx=6, pady=(6, 2))

        am = tk.Frame(frame, bg=BG_DARK)
        am.pack(fill=tk.X, padx=8, pady=2)
        self._me_assets = _build_card(am, "DB ASSETS", "—", FG_PURPLE)
        self._me_pools = _build_card(am, "DB POOLS", "—", FG_GREEN)
        self._me_stables = _build_card(am, "STABLECOINS", "—", FG_CYAN)
        self._me_vols = _build_card(am, "24HR VOLUMES", "—", FG_YELLOW)

        _build_label(frame, "MEXC Assets (active networks)", 9, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(4, 0))
        af = tk.Frame(frame, bg=BG_DARK, height=140)
        af.pack(fill=tk.X, padx=8, pady=(0, 4)); af.pack_propagate(False)
        self.asset_tree, a_scroll = _build_treeview(af, ("coin", "network", "contract", "deposit", "withdraw", "fee"),
            {"coin": 70, "network": 80, "contract": 280, "deposit": 60, "withdraw": 60, "fee": 80},
            {"coin": "Coin", "network": "Network", "contract": "Contract", "deposit": "Deposit", "withdraw": "Withdraw", "fee": "Fee"}, height=5)
        _pack_treeview(self.asset_tree, a_scroll)

        _build_label(frame, "Stablecoin Registry", 9, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(2, 0))
        sf = tk.Frame(frame, bg=BG_DARK)
        sf.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))
        self.stable_tree, st_scroll = _build_treeview(sf, ("coin", "network", "address", "deposit", "withdraw", "fee"),
            {"coin": 70, "network": 80, "address": 280, "deposit": 60, "withdraw": 60, "fee": 80},
            {"coin": "Coin", "network": "Network", "address": "Address", "deposit": "Deposit", "withdraw": "Withdraw", "fee": "Fee"}, height=5)
        _pack_treeview(self.stable_tree, st_scroll)

    # ══════════════════════════════════════════════════════════════════════
    # TAB 7: 🏦 Database
    # ══════════════════════════════════════════════════════════════════════
    def _build_db_tab(self):
        frame = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(frame, text="  🏦 Database  ")
        _build_label(frame, "SQLite Database Explorer", 11, bold=True, fg=FG_CYAN).pack(anchor=tk.W, padx=6, pady=(6, 2))
        self._db_status = _build_label(frame, "DB: active.sqlite3", 9, fg=FG_DIM)
        self._db_status.pack(anchor=tk.W, padx=8)

        _build_label(frame, "Tables Overview", 9, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(4, 0))
        dt_f = tk.Frame(frame, bg=BG_DARK, height=160)
        dt_f.pack(fill=tk.X, padx=8, pady=(0, 4)); dt_f.pack_propagate(False)
        self.db_table_tree, dt_scroll = _build_treeview(dt_f, ("name", "rows", "columns"),
            {"name": 180, "rows": 80, "columns": 400},
            {"name": "Table Name", "rows": "Rows", "columns": "Columns"}, height=6)
        _pack_treeview(self.db_table_tree, dt_scroll)

        _build_label(frame, "Sample Data (last 10 rows)", 9, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(2, 0))
        detail_f = tk.Frame(frame, bg=BG_DARK)
        detail_f.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))
        self.db_detail_text = ReadOnlyText(detail_f, wrap=tk.NONE, font=("Consolas", 8),
            bg=BG_MID, fg=FG_TEXT, relief=tk.FLAT, borderwidth=0, padx=6, pady=4)
        db_ds = tk.Scrollbar(detail_f, orient=tk.VERTICAL, command=self.db_detail_text.yview)
        self.db_detail_text.configure(yscrollcommand=db_ds.set)
        self.db_detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        db_ds.pack(side=tk.RIGHT, fill=tk.Y)

    # ══════════════════════════════════════════════════════════════════════
    # TAB 8: ⚙️ Performance
    # ══════════════════════════════════════════════════════════════════════
    def _build_perf_tab(self):
        frame = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(frame, text="  ⚙️ Performance  ")
        sf = _make_scrollable(frame)

        _build_label(sf, "Performance & Error Metrics", 11, bold=True, fg=FG_CYAN).pack(anchor=tk.W, padx=6, pady=(6, 2))
        pc = tk.Frame(sf, bg=BG_DARK)
        pc.pack(fill=tk.X, padx=8, pady=4)
        self._pf_records = _build_card(pc, "PERF RECORDS", "—", FG_CYAN)
        self._pf_avg_dur = _build_card(pc, "AVG DURATION", "—", FG_GREEN)
        self._pf_total_items = _build_card(pc, "TOTAL ITEMS", "—", FG_ORANGE)
        self._pf_cache_hits = _build_card(pc, "CACHE HITS", "—", FG_PURPLE)
        self._pf_total_err = _build_card(pc, "LOG ERRORS", "—", FG_RED)
        self._pf_total_warn = _build_card(pc, "LOG WARNINGS", "—", FG_YELLOW)

        _build_label(sf, "Log Files Analysis", 9, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(4, 0))
        lf = tk.Frame(sf, bg=BG_DARK, height=100)
        lf.pack(fill=tk.X, padx=8, pady=(0, 4)); lf.pack_propagate(False)
        self.logs_tree, ls_scroll = _build_treeview(lf, ("file", "size_kb", "errors", "warnings", "lines"),
            {"file": 280, "size_kb": 80, "errors": 70, "warnings": 80, "lines": 70},
            {"file": "Log File", "size_kb": "Size (KB)", "errors": "Errors", "warnings": "Warnings", "lines": "Lines"}, height=3)
        _pack_treeview(self.logs_tree, ls_scroll)

        _build_label(sf, "Performance Timeline (recent 50 records)", 9, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(4, 0))
        pt_f = tk.Frame(sf, bg=BG_DARK, height=160)
        pt_f.pack(fill=tk.X, padx=8, pady=(0, 4)); pt_f.pack_propagate(False)
        self.perf_tree, pt_scroll = _build_treeview(pt_f, ("stage", "started", "duration_ms", "items", "success", "failed", "source"),
            {"stage": 130, "started": 120, "duration_ms": 80, "items": 60, "success": 65, "failed": 65, "source": 160},
            {"stage": "Stage", "started": "Started", "duration_ms": "Dur (ms)", "items": "Items", "success": "OK", "failed": "Fail", "source": "Source"}, height=5)
        _pack_treeview(self.perf_tree, pt_scroll)

        _build_label(sf, "Recent Log Errors (up to 50)", 9, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(2, 0))
        ef = tk.Frame(sf, bg=BG_DARK, height=120)
        ef.pack(fill=tk.X, padx=8, pady=(0, 4)); ef.pack_propagate(False)
        self.err_tree, err_scroll = _build_treeview(ef, ("source", "message"),
            {"source": 220, "message": 480}, {"source": "Source Log", "message": "Error Line"}, height=4)
        _pack_treeview(self.err_tree, err_scroll)

        _build_label(sf, "Scanner Cycle Durations", 9, bold=True, fg=FG_TEXT).pack(anchor=tk.W, padx=8, pady=(2, 0))
        sd = tk.Frame(sf, bg=BG_DARK)
        sd.pack(fill=tk.X, padx=8, pady=(0, 6))
        self.dur_text = ReadOnlyText(sd, wrap=tk.NONE, font=("Consolas", 8), height=6,
            bg=BG_MID, fg=FG_TEXT, relief=tk.FLAT, borderwidth=0)
        self.dur_text.pack(fill=tk.X)

    # ══════════════════════════════════════════════════════════════════════
    # Bot subprocess
    # ══════════════════════════════════════════════════════════════════════
    def _start_bot(self):
        main_path = self.project_root / "main.py"
        env = os.environ.copy()
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        self._process = subprocess.Popen(
            [sys.executable, str(main_path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, cwd=str(self.project_root),
            bufsize=1, universal_newlines=True, startupinfo=startupinfo)
        self.status_label.config(text="🟢 Running", fg=FG_GREEN)
        threading.Thread(target=self._read_stdout, daemon=True).start()

    def _read_stdout(self):
        try:
            for line in iter(self._process.stdout.readline, ""):
                if self._stop_event.is_set():
                    break
                if line:
                    self.root.after(0, self._process_log_line, line.rstrip())
        except Exception:
            pass
        finally:
            self.root.after(0, self._on_bot_exit)

    def _process_log_line(self, line: str):
        self._append_log(line)
        try:
            self._parse_log_for_tables(line)
        except Exception:
            pass

    def _append_log(self, line: str):
        if "ERROR" in line or "error=" in line.lower():
            tag = "red"
        elif "WARNING" in line or "warning" in line.lower():
            tag = "yellow"
        elif "SIGNAL" in line or "arbitrage" in line.lower():
            tag = "green"
        elif "pool_discovery" in line or "scanner_cycle" in line:
            tag = "cyan"
        elif "app_initializing" in line or "app_starting" in line:
            tag = "orange"
        elif "debug" in line.lower() and "httpx" not in line:
            tag = "dim"
        else:
            tag = "default"
        at_bottom = self.log_text.yview()[1] >= 0.99
        self.log_text.insert(tk.END, line + "\n", (tag,))
        if at_bottom:
            self.log_text.see(tk.END)
        self._last_log_line += 1
        self._log_line_label.config(text=f"{self._last_log_line} lines")

    def _parse_log_for_tables(self, line: str):
        if "source_marked_unhealthy" in line:
            src = line.split("source_marked_unhealthy:")[-1].strip()
            self._upsert_src_tree(src, "0", "3", "max failures")
        elif "source_unexpected_error" in line:
            parts = line.split("source_unexpected_error:")
            if len(parts) > 1:
                rest = parts[1].strip()
                src = rest.split("error=")[0].strip() if "error=" in rest else rest
                err = rest.split("error=")[-1] if "error=" in rest else ""
                self._upsert_src_tree(src, "0", "?", err)
        elif "source_pools_found" in line:
            parts = line.split("source_pools_found:")
            if len(parts) > 1:
                rest = parts[1].strip()
                src = rest.split()[0] if rest.split() else ""
                self._upsert_src_tree(src, "1", "0", "")
        for sk in ("app_initializing", "pool_discovery", "scanner_cycle",
                    "signal_write", "mexc_capital_config_fetch", "mexc_price_fetch"):
            if sk in line:
                self._stage_label.config(text=f"Stage: {sk.replace('_', ' ').title()}")
                break
        if "scanner_cycle" in line:
            ts = time.strftime("%H:%M:%S")
            pools = "—"
            signals = "—"
            for token in line.split():
                if "pools=" in token:
                    pools = token.split("=")[-1]
                elif "signals=" in token:
                    signals = token.split("=")[-1]
            self.cyc_tree.insert("", 0, values=(ts, pools, "—", "—", signals, "—"),
                                 tags=(_alt_tag(self.cyc_tree),))
            self._title_cycle_label.config(text=f"Cyc: {len(self.cyc_tree.get_children())}")

    def _upsert_src_tree(self, source, healthy, failures, error):
        for child in self.src_tree.get_children():
            vals = self.src_tree.item(child, "values")
            if vals and vals[0] == source:
                self.src_tree.item(child, values=(source, healthy, failures, error))
                return
        self.src_tree.insert("", tk.END, values=(source, healthy, failures, error),
                             tags=(_alt_tag(self.src_tree),))

    # ──────────────────────── Signal polling ───────────────────────────────
    def _poll_signals(self):
        if self._stop_event.is_set():
            return
        try:
            path = self.signals_path
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    f.seek(self._last_signal_pos)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            sig = json.loads(line)
                            self._add_signal_row(sig)
                        except json.JSONDecodeError:
                            pass
                    self._last_signal_pos = f.tell()
        except OSError:
            pass
        if not self._stop_event.is_set():
            self.root.after(2000, self._poll_signals)

    def _add_signal_row(self, sig: dict):
        ts = sig.get("timestamp", "")[11:19] if sig.get("timestamp") else ""
        vals = (
            ts, sig.get("network", ""), sig.get("token_coin", ""), sig.get("dex", ""),
            sig.get("direction", ""),
            f'{sig.get("gross_profit_pct", "")}%',
            f'{sig.get("net_profit_pct", "")}%',
            f'${sig.get("net_profit_usd", "")}',
            (sig.get("pool_address", "") or "")[:20],
            ", ".join(sig.get("warnings", []) or []))
        self.sig_tree.insert("", 0, values=vals, tags=(_alt_tag(self.sig_tree),))
        count = len(self.sig_tree.get_children())
        self.signal_count_label.config(text=f"{count} signals")
        self._title_sig_label.config(text=f"Sig: {count}")
        self.status_label.config(text="💰 SIGNAL!", fg=FG_GREEN)
        self.root.after(2000, lambda: self.status_label.config(
            text="🟢 Running" if self._process and self._process.poll() is None else "🔴 Stopped"))

    # ══════════════════════════════════════════════════════════════════════
    # Dashboard refresh — periodic update of all stats from files/DB
    # ══════════════════════════════════════════════════════════════════════
    def _gui_error(self, context: str, exc: Exception):
        """Log GUI errors to file and append to activity log."""
        msg = f"[GUI] {context}: {exc}"
        try:
            with open(self._gui_log_path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
                traceback.print_exc(file=f)
        except OSError:
            pass
        self._append_log(msg)

    def _refresh_dashboard(self):
        if self._stop_event.is_set():
            return
        try:
            self._refresh_cards()
        except Exception as e:
            self._gui_error("Cards refresh", e)
        try:
            self._refresh_distributions()
        except Exception as e:
            self._gui_error("Distributions refresh", e)
        try:
            self._refresh_pools_table()
        except Exception as e:
            self._gui_error("Pools table refresh", e)
        try:
            self._refresh_db_explorer()
        except Exception as e:
            self._gui_error("DB explorer refresh", e)
        try:
            self._refresh_performance()
        except Exception as e:
            self._gui_error("Performance refresh", e)
        if not self._stop_event.is_set():
            self.root.after(5000, self._refresh_dashboard)

    def _refresh_cards(self):
        signals = _load_signals(self.signals_path)
        pools = _load_pools(self.pools_path)
        perf = _load_perf(self.perf_path)

        total = len(signals)
        clean_count = sum(1 for s in signals if not s.get("warnings"))
        thin_count = sum(1 for s in signals
                         if "thin_liquidity_quote_unreliable" in (s.get("warnings") or []))
        profits = [float(s.get("net_profit_pct", 0)) for s in signals if s.get("net_profit_pct")]
        median_p = sorted(profits)[len(profits) // 2] if profits else 0

        conn = _db_connect()
        db_assets = db_pools = db_stables = db_sources = 0
        if conn:
            try:
                db_assets = conn.execute("SELECT COUNT(*) FROM mexc_assets").fetchone()[0]
                db_pools = conn.execute("SELECT COUNT(*) FROM pools").fetchone()[0]
                db_stables = conn.execute("SELECT COUNT(*) FROM stablecoins").fetchone()[0]
                db_sources = conn.execute("SELECT COUNT(*) FROM source_health").fetchone()[0]
            except sqlite3.Error:
                pass
            conn.close()

        err_count = warn_count = 0
        if self.logs_dir.exists():
            for f in sorted(self.logs_dir.iterdir()):
                if f.suffix == ".log":
                    try:
                        for l in f.read_text(encoding="utf-8", errors="replace").split("\n"):
                            u = l.upper()
                            if any(kw in u for kw in ["ERROR", "FATAL", "CRITICAL", "EXCEPTION", "TRACEBACK"]):
                                if "Unclosed" not in u:
                                    err_count += 1
                            elif "WARNING" in u:
                                warn_count += 1
                    except OSError:
                        pass

        self._card_sigs.config(text=str(total))
        self._card_clean.config(text=str(clean_count))
        self._card_pools.config(text=str(len(pools)))
        self._card_assets.config(text=str(db_assets))
        self._card_cycles.config(text=str(len(self.cyc_tree.get_children())))
        self._card_profit.config(text=f"{median_p:.1f}%")
        self._card_thin.config(text=f"{thin_count / total * 100:.1f}%" if total else "0%")
        self._card_errors.config(text=str(err_count))
        self._card_warns.config(text=str(warn_count))
        self._card_db_pools.config(text=str(db_pools))
        self._card_sources.config(text=str(db_sources))
        u = int(time.time() - self._start_time)
        h, m = divmod(u // 60, 60)
        self._card_uptime.config(text=f"{h}h {m}m")

        # Signal stats
        self._sig_total.config(text=str(len(self.sig_tree.get_children())))
        self._sig_clean.config(text=str(clean_count))
        self._sig_thin.config(text=str(thin_count))
        self._sig_med.config(text=f"{median_p:.1f}%")
        self._sig_max.config(text=f"{max(profits):.1f}%" if profits else "0%")
        if len(signals) >= 2:
            ts0, ts1 = signals[0].get("timestamp", ""), signals[-1].get("timestamp", "")
            if ts0 and ts1:
                try:
                    from datetime import datetime
                    fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
                    t0 = datetime.strptime(ts0[:26] + "Z", fmt)
                    t1 = datetime.strptime(ts1[:26] + "Z", fmt)
                    self._sig_time.config(text=f"{(t1 - t0).total_seconds() / 3600:.1f}h")
                except ValueError:
                    pass

        # MEXC cards
        self._me_assets.config(text=str(db_assets))
        self._me_stables.config(text=str(db_stables))
        if db_pools > 0:
            self._me_pools.config(text=str(db_pools))

        # Performance cards
        self._pf_records.config(text=str(len(perf)))
        if perf:
            durs = [r.get("duration_ms", 0) for r in perf if r.get("duration_ms")]
            self._pf_avg_dur.config(text=f"{sum(durs) / len(durs):.0f} ms" if durs else "—")
            self._pf_total_items.config(text=str(sum(r.get("items_total", 0) for r in perf)))
            self._pf_cache_hits.config(text=str(sum(r.get("cache_hits", 0) for r in perf)))
        self._pf_total_err.config(text=str(err_count))
        self._pf_total_warn.config(text=str(warn_count))

        # Scanner cards
        scanner_recs = [r for r in perf if r.get("stage") == "scanner_cycle"]
        if scanner_recs:
            durs = [r.get("duration_ms", 0) for r in scanner_recs]
            self._sc_duration.config(text=f"{sum(durs) / len(durs):.0f} ms")
            self._sc_pools.config(text=f"{sum(r.get('items_total', 0) for r in scanner_recs) // len(scanner_recs)}")
            self._sc_quotes.config(text=str(sum(r.get("items_success", 0) for r in scanner_recs)))
            self._sc_failed.config(text=str(sum(r.get("items_failed", 0) for r in scanner_recs)))
            self._sc_signals.config(text=str(len(self.sig_tree.get_children())))
            self._sc_cycles.config(text=str(len(scanner_recs)))

        # Scanner cycle duration bars
        self.dur_text.delete("1.0", tk.END)
        if scanner_recs:
            durs = [r.get("duration_ms", 0) for r in scanner_recs[-30:]]
            md = max(durs) if durs else 1
            lines = [f"#{len(durs) - i:3d} │ {'█' * max(1, int(d / md * 30))} {d:.0f}ms"
                     for i, d in enumerate(durs)]
            self.dur_text.insert("1.0", "\n".join(lines))
        else:
            self.dur_text.insert("1.0", "No scanner cycle data yet.\n")

    def _refresh_distributions(self):
        signals = _load_signals(self.signals_path)
        pools = _load_pools(self.pools_path)

        # Network
        nets = Counter(s.get("network", "?") for s in signals)
        mn = max(nets.values()) if nets else 1
        self._net_tree.delete(*self._net_tree.get_children())
        for n, c in nets.most_common():
            self._net_tree.insert("", tk.END, values=(n, c, f"{'█' * max(1, int(c / mn * 30))} {c}"),
                                  tags=(_alt_tag(self._net_tree),))
        # DEX
        dexes = Counter(s.get("dex", "?") for s in signals)
        md = max(dexes.values()) if dexes else 1
        self._dex_tree.delete(*self._dex_tree.get_children())
        for d, c in dexes.most_common():
            self._dex_tree.insert("", tk.END, values=(d, c, f"{'█' * max(1, int(c / md * 30))} {c}"),
                                  tags=(_alt_tag(self._dex_tree),))
        # Pool networks
        pn = Counter(p.get("network", "?") for p in pools)
        mp = max(pn.values()) if pn else 1
        self._pool_net_tree.delete(*self._pool_net_tree.get_children())
        for n, c in pn.most_common():
            self._pool_net_tree.insert("", tk.END, values=(n, c, f"{'█' * max(1, int(c / mp * 30))} {c}"),
                                       tags=(_alt_tag(self._pool_net_tree),))
        # Warnings
        warns = Counter()
        for s in signals:
            for w in s.get("warnings", []):
                warns[w] += 1
        mw = max(warns.values()) if warns else 1
        self._warn_tree.delete(*self._warn_tree.get_children())
        for w, c in warns.most_common():
            self._warn_tree.insert("", tk.END, values=(w, c, f"{'█' * max(1, int(c / mw * 20))} {c}"),
                                   tags=(_alt_tag(self._warn_tree),))

    def _refresh_pools_table(self):
        pools = _load_pools(self.pools_path)
        self.pool_tree.delete(*self.pool_tree.get_children())
        for pool in pools[:100]:
            self.pool_tree.insert("", tk.END, values=(
                pool.get("network", ""), pool.get("dex", ""),
                (pool.get("pool_address", "") or "")[:30],
                (pool.get("token_address", "") or "")[:20],
                (pool.get("stablecoin_address", "") or "")[:20]),
                tags=(_alt_tag(self.pool_tree),))

    def _refresh_db_explorer(self):
        conn = _db_connect()
        if not conn:
            return
        self.db_table_tree.delete(*self.db_table_tree.get_children())
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for t in tables:
            try:
                name = t[0]
                cnt = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                cols = ", ".join(c[1] for c in conn.execute(f'PRAGMA table_info("{name}")').fetchall()[:6])
                if len(cols) > 60:
                    cols = cols[:57] + "..."
                self.db_table_tree.insert("", tk.END, values=(name, cnt, cols), tags=("even",))
            except sqlite3.Error:
                pass
        if tables:
            try:
                first = tables[0][0]
                rows = conn.execute(f'SELECT * FROM "{first}" ORDER BY rowid DESC LIMIT 10').fetchall()
                cols = [c[1] for c in conn.execute(f'PRAGMA table_info("{first}")').fetchall()]
                header = " │ ".join(cols)
                data = [header, "─" * min(80, len(header) * 2)]
                for row in rows:
                    data.append(" │ ".join(str(c)[:20] for c in row))
                self.db_detail_text.delete("1.0", tk.END)
                self.db_detail_text.insert("1.0", "\n".join(data))
            except sqlite3.Error:
                pass
        conn.close()

    def _refresh_performance(self):
        perf = _load_perf(self.perf_path)
        # Log files analysis
        self.logs_tree.delete(*self.logs_tree.get_children())
        if self.logs_dir.exists():
            for f in sorted(self.logs_dir.iterdir()):
                if f.suffix == ".log":
                    try:
                        lines = f.read_text(encoding="utf-8", errors="replace").split("\n")
                        errs = sum(1 for l in lines if any(kw in l.upper() for kw in
                                   ["ERROR", "FATAL", "CRITICAL", "EXCEPTION", "TRACEBACK"]))
                        warns = sum(1 for l in lines if "WARNING" in l.upper())
                        self.logs_tree.insert("", 0, values=(f.name, f.stat().st_size // 1024, errs, warns, len(lines)),
                                               tags=("even",))
                    except OSError:
                        pass
        # Perf timeline
        self.perf_tree.delete(*self.perf_tree.get_children())
        for rec in perf[-50:]:
            self.perf_tree.insert("", 0, values=(
                rec.get("stage", ""), str(rec.get("started_at", ""))[11:19],
                f'{rec.get("duration_ms", 0):.0f}', rec.get("items_total", ""),
                rec.get("items_success", ""), rec.get("items_failed", ""),
                (rec.get("source", "") or "")[:20]), tags=(_alt_tag(self.perf_tree),))

    # ──────────────────────── Timer ────────────────────────────────────────
    def _update_timer(self):
        if self._stop_event.is_set():
            return
        elapsed = int(time.time() - self._start_time)
        h, m = divmod(elapsed // 60, 60)
        self.timer_label.config(text=f"{h:02d}:{m:02d}:{elapsed % 60:02d}" if h else f"{m:02d}:{elapsed % 60:02d}")
        if not self._stop_event.is_set():
            self.root.after(1000, self._update_timer)

    # ──────────────────────── Shutdown ─────────────────────────────────────
    def _on_bot_exit(self):
        self.status_label.config(text="🔴 Stopped", fg=FG_RED)
        self._append_log("[GUI] Bot process exited.")

    def _on_close(self):
        self._stop_event.set()
        # Shutdown health server
        if self._health_server:
            try:
                self._health_server.shutdown()
            except Exception:
                pass
        # Kill bot process
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    root = Path(__file__).resolve().parent.parent
    Dashboard(str(root)).run()


if __name__ == "__main__":
    main()
