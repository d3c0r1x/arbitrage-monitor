"""
Web dashboard backend for MEXC × DEX Arbitrage Monitor.

Replaces the tkinter GUI with a browser dashboard. Read-only:
serves JSON endpoints over the same data the bot writes
(data/*.jsonl, data/pools_cache.json, data/state/active.sqlite3,
.run/logs/*.log). The bot itself runs headless via `python main.py`.

Run:  python run_dashboard.py   (or: uvicorn dashboard.server:app)
"""

import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from services.refresh_status import read_refresh_status

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SIGNALS_PATH = DATA_DIR / "signals.jsonl"
ALT_CEX_SIGNALS_PATH = DATA_DIR / "alt_cex_signals.jsonl"
DEX_DEX_SIGNALS_PATH = DATA_DIR / "dex_dex_signals.jsonl"
DEX_DEX_OPPS_PATH = DATA_DIR / "dex_dex_opportunities_live.json"
PERF_PATH = DATA_DIR / "performance.jsonl"
POOLS_PATH = DATA_DIR / "pools_cache.json"
OPPS_PATH = DATA_DIR / "opportunities.json"
OPPS_LIVE_PATH = DATA_DIR / "opportunities_live.json"
ARCHIVE_PATH = DATA_DIR / "signals_archive.jsonl"
DB_PATH = DATA_DIR / "state" / "active.sqlite3"
LOGS_DIR = PROJECT_ROOT / ".run" / "logs"
WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(title="MEXC × DEX Arbitrage Dashboard", version="2.0")

# Server start — fallback only. Session filter prefers data/bot_session.json
# written by main.py so dashboard restarts don't hide live MEXC signals.
SERVER_START_TS = time.time()
BOT_SESSION_PATH = DATA_DIR / "bot_session.json"


# ── Data loaders ────────────────────────────────────────────────────────

def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return records


def _load_pools() -> list[dict]:
    if not POOLS_PATH.exists():
        return []
    for attempt in range(2):
        try:
            return json.loads(POOLS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            if attempt == 0:
                time.sleep(0.3)
                continue
            return []
    return []


def _db_connect() -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        return None
    try:
        uri = f"file:{DB_PATH.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _log_files() -> list[Path]:
    candidates: list[Path] = []
    run_dir = PROJECT_ROOT / ".run"
    if LOGS_DIR.exists():
        candidates.extend(LOGS_DIR.glob("*.log"))
    if run_dir.exists():
        candidates.extend(run_dir.glob("*.log"))
    if DATA_DIR.exists():
        candidates.extend(DATA_DIR.glob("*.log"))
        candidates.extend(DATA_DIR.glob("*.txt"))
    # Dedupe by resolved path, newest first.
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in sorted(
        (x for x in candidates if x.is_file()),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    ):
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        uniq.append(p)
    return uniq


_log_level_cache: dict = {"ts": 0, "errors": 0, "warnings": 0}
_LOG_CACHE_TTL = 30  # seconds


def _is_mexc_dex_signal(sig: dict) -> bool:
    """Main dashboard: classic MEXC ↔ DEX directions only."""
    d = str(sig.get("direction") or "")
    return d.startswith("MEXC_")


def _is_alt_cex_signal(sig: dict) -> bool:
    d = str(sig.get("direction") or "")
    return d.startswith("ALT_CEX_")


def _dedupe_signal_rank(sig: dict) -> tuple:
    """Prefer usable size_curve, then newer ts, then higher net $."""
    curve = sig.get("size_curve") or []
    has_curve = 1 if curve else 0
    has_pos = 0
    for p in curve:
        try:
            if float(p.get("net_usd") or 0) > 0:
                has_pos = 1
                break
        except (TypeError, ValueError):
            pass
    try:
        nu = float(sig.get("net_profit_usd") or 0)
    except (TypeError, ValueError):
        nu = 0.0
    return (has_curve, has_pos, str(sig.get("timestamp") or ""), nu)


def _dedupe_best_mexc_signals(signals: list[dict]) -> list[dict]:
    """One row per (network, token, direction)."""
    best: dict[tuple[str, str, str], dict] = {}
    for s in signals:
        key = (
            str(s.get("network") or "").upper(),
            str(s.get("token_coin") or "").upper(),
            str(s.get("direction") or ""),
        )
        prev = best.get(key)
        if prev is None or _dedupe_signal_rank(s) > _dedupe_signal_rank(prev):
            best[key] = s
    # Newest first for UI.
    return sorted(
        best.values(),
        key=lambda x: x.get("timestamp") or "",
        reverse=True,
    )


def _session_cutoff(session_only: bool) -> str | None:
    if not session_only:
        return None
    # Prefer bot start time — dashboard restarts must not wipe the signal list.
    try:
        if BOT_SESSION_PATH.exists():
            data = json.loads(BOT_SESSION_PATH.read_text(encoding="utf-8"))
            started = str(data.get("started_at") or "").strip().replace("Z", "")
            if len(started) >= 19:
                return started[:19]
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(SERVER_START_TS))


def _filter_session(rows: list[dict], cutoff: str | None) -> list[dict]:
    if not cutoff:
        return rows
    return [s for s in rows if (s.get("timestamp") or "") >= cutoff]


def _load_alt_cex_signals() -> list[dict]:
    """Dedicated alt file + any legacy ALT_CEX_* rows still in signals.jsonl."""
    primary = _load_jsonl(ALT_CEX_SIGNALS_PATH)
    legacy = [s for s in _load_jsonl(SIGNALS_PATH) if _is_alt_cex_signal(s)]
    if not legacy:
        return primary
    # Prefer dedicated file; append legacy not already present by timestamp+token+dir.
    seen = {
        (s.get("timestamp"), s.get("token_coin"), s.get("direction"), s.get("pool_address"))
        for s in primary
    }
    merged = list(primary)
    for s in legacy:
        key = (s.get("timestamp"), s.get("token_coin"), s.get("direction"), s.get("pool_address"))
        if key not in seen:
            merged.append(s)
    merged.sort(key=lambda x: x.get("timestamp") or "")
    return merged


def _signal_stats(signals: list[dict]) -> dict:
    total = len(signals)
    clean = sum(1 for s in signals if not s.get("warnings"))
    thin = sum(
        1 for s in signals
        if "thin_liquidity_quote_unreliable" in (s.get("warnings") or [])
    )
    profits = [
        float(s["net_profit_pct"])
        for s in signals
        if s.get("net_profit_pct") is not None
    ]
    median_p = sorted(profits)[len(profits) // 2] if profits else 0.0
    max_p = max(profits) if profits else 0.0
    nets = Counter(s.get("network", "?") for s in signals)
    dexes = Counter(s.get("dex", "?") for s in signals)
    dirs = Counter(s.get("direction", "?") for s in signals)
    exchanges = Counter()
    for s in signals:
        for w in s.get("warnings") or []:
            ws = str(w)
            if ws.startswith("alt_cex="):
                exchanges[ws.split("=", 1)[1]] += 1
                break
        else:
            # Fallback: dex field often holds exchange name for alt signals.
            if _is_alt_cex_signal(s):
                exchanges[str(s.get("dex") or "?")] += 1
    warns = Counter()
    for s in signals:
        for w in s.get("warnings", []) or []:
            warns[str(w).split("=")[0] if "=" in str(w) else str(w)] += 1
    last_ts = signals[-1].get("timestamp", "") if signals else ""
    return {
        "signals_total": total,
        "signals_clean": clean,
        "signals_thin": thin,
        "median_profit_pct": round(median_p, 2),
        "max_profit_pct": round(max_p, 2),
        "last_signal_ts": last_ts,
        "network_dist": dict(nets.most_common()),
        "dex_dist": dict(dexes.most_common()),
        "direction_dist": dict(dirs.most_common()),
        "exchange_dist": dict(exchanges.most_common()),
        "warning_dist": dict(warns.most_common(20)),
    }


def _count_log_levels() -> tuple[int, int]:
    now = time.time()
    if now - _log_level_cache["ts"] < _LOG_CACHE_TTL:
        return _log_level_cache["errors"], _log_level_cache["warnings"]

    errors = warnings = 0
    err_kw = ("ERROR", "FATAL", "CRITICAL", "EXCEPTION", "TRACEBACK")
    # Only the 2 most recent log files, so old archived runs don't inflate
    # the live error/warning counters.
    for f in _log_files()[:2]:
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                up = line.upper()
                if any(k in up for k in err_kw) and "Unclosed" not in up:
                    errors += 1
                elif "WARNING" in up:
                    warnings += 1
        except OSError:
            pass

    _log_level_cache.update(ts=now, errors=errors, warnings=warnings)
    return errors, warnings


# ── Endpoints ───────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/alt-cex")
async def alt_cex_page():
    return FileResponse(WEB_DIR / "alt_cex.html")


@app.get("/dex-dex")
async def dex_dex_page():
    return FileResponse(WEB_DIR / "dex_dex.html")


@app.get("/ops")
async def ops_page():
    return FileResponse(WEB_DIR / "ops.html")


@app.get("/api/refresh_status")
async def refresh_status():
    """Live pool-refresh progress (percent + phase) for the dashboard."""
    return read_refresh_status()


@app.get("/api/summary")
async def summary(session_only: bool = Query(False)):
    cutoff = _session_cutoff(session_only)
    signals = _dedupe_best_mexc_signals(
        [
            s for s in _filter_session(_load_jsonl(SIGNALS_PATH), cutoff)
            if _is_mexc_dex_signal(s)
        ]
    )
    pools = _load_pools()
    perf = _load_jsonl(PERF_PATH)
    stats = _signal_stats(signals)

    pool_nets = Counter(p.get("network", "?") for p in pools)

    db_assets = db_pools = db_stables = db_sources = 0
    conn = _db_connect()
    if conn:
        try:
            db_assets = conn.execute("SELECT COUNT(*) FROM mexc_assets").fetchone()[0]
            db_pools = conn.execute("SELECT COUNT(*) FROM pools").fetchone()[0]
            db_stables = conn.execute("SELECT COUNT(*) FROM stablecoins").fetchone()[0]
            db_sources = conn.execute("SELECT COUNT(*) FROM source_health").fetchone()[0]
        except sqlite3.Error:
            pass
        conn.close()

    errors, warnings = _count_log_levels()

    scanner_recs = [r for r in perf if r.get("stage") == "scanner_cycle"]
    sc_durs = [r.get("duration_ms", 0) for r in scanner_recs if r.get("duration_ms")]
    refresh_recs = [r for r in perf if r.get("stage") == "pool_refresh"]
    rf_durs = [r.get("duration_ms", 0) for r in refresh_recs if r.get("duration_ms")]

    # Stage breakdown for performance panel.
    by_stage: dict[str, list[float]] = {}
    for r in perf[-400:]:
        st = str(r.get("stage") or "?")
        ms = r.get("duration_ms")
        if ms is None:
            continue
        by_stage.setdefault(st, []).append(float(ms))
    stage_stats = {
        k: {
            "count": len(v),
            "avg_ms": round(sum(v) / len(v), 0) if v else 0,
            "max_ms": round(max(v), 0) if v else 0,
        }
        for k, v in sorted(by_stage.items(), key=lambda x: -len(x[1]))
    }

    refresh = read_refresh_status()

    return {
        **stats,
        "scope": "mexc_dex",
        "pools_cached": len(pools),
        "db_assets": db_assets,
        "db_pools": db_pools,
        "db_stables": db_stables,
        "db_sources": db_sources,
        "scanner_cycles": len(scanner_recs),
        "avg_scan_ms": round(sum(sc_durs) / len(sc_durs), 0) if sc_durs else 0,
        "max_scan_ms": round(max(sc_durs), 0) if sc_durs else 0,
        "refresh_cycles": len(refresh_recs),
        "avg_refresh_ms": round(sum(rf_durs) / len(rf_durs), 0) if rf_durs else 0,
        "stage_stats": stage_stats,
        "errors": errors,
        "warnings": warnings,
        "perf_records": len(perf),
        "server_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "uptime_sec": int(time.time() - SERVER_START_TS),
        "pool_net_dist": dict(pool_nets.most_common()),
        "refresh": refresh,
        "alt_cex_href": "/alt-cex",
        "dex_dex_href": "/dex-dex",
        "ops_href": "/ops",
        "archive_count": len(_load_jsonl(ARCHIVE_PATH)),
    }


@app.get("/api/signals")
async def signals(limit: int = Query(200, ge=1, le=5000), session_only: bool = Query(False)):
    cutoff = _session_cutoff(session_only)
    data = _dedupe_best_mexc_signals(
        [
            s for s in _filter_session(_load_jsonl(SIGNALS_PATH), cutoff)
            if _is_mexc_dex_signal(s)
        ]
    )
    return {"count": len(data), "scope": "mexc_dex", "signals": data[:limit]}


@app.get("/api/alt_cex/summary")
async def alt_cex_summary(session_only: bool = Query(False)):
    cutoff = _session_cutoff(session_only)
    signals = _filter_session(_load_alt_cex_signals(), cutoff)
    stats = _signal_stats(signals)
    return {
        **stats,
        "scope": "alt_cex",
        "server_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "uptime_sec": int(time.time() - SERVER_START_TS),
        "source_file": "data/alt_cex_signals.jsonl",
    }


@app.get("/api/alt_cex/signals")
async def alt_cex_signals(
    limit: int = Query(200, ge=1, le=5000),
    session_only: bool = Query(False),
):
    cutoff = _session_cutoff(session_only)
    data = _filter_session(_load_alt_cex_signals(), cutoff)
    return {"count": len(data), "scope": "alt_cex", "signals": data[-limit:][::-1]}


@app.get("/api/scan_mode")
async def api_scan_mode_get():
    from services.scan_mode import get_scan_mode, refresh_scan_mode

    mode = refresh_scan_mode()
    return {"mode": mode, "valid": ["mexc", "dex_dex"]}


@app.post("/api/scan_mode")
async def api_scan_mode_set(payload: dict):
    from services.scan_mode import set_scan_mode

    mode = str((payload or {}).get("mode") or "").strip().lower()
    try:
        applied = set_scan_mode(mode)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "mode": applied}


@app.get("/api/dex_dex/opportunities")
async def dex_dex_opportunities():
    if not DEX_DEX_OPPS_PATH.exists():
        payload = {"exported_ts": 0, "opportunities": [], "fresh_max_age_sec": 45}
    else:
        try:
            payload = json.loads(DEX_DEX_OPPS_PATH.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            payload = {"exported_ts": 0, "opportunities": [], "fresh_max_age_sec": 45}
    if not isinstance(payload, dict):
        payload = {"exported_ts": 0, "opportunities": [], "fresh_max_age_sec": 45}
    now = time.time()
    exported_ts = float(payload.get("exported_ts") or 0)
    export_age = now - exported_ts if exported_ts else 9999
    fresh_max = float(payload.get("fresh_max_age_sec") or 45)
    opps = payload.get("opportunities") or []
    if not isinstance(opps, list):
        opps = []

    # Merge near-miss from diagnostics so UI always shows funnel leftovers.
    diag_path = DATA_DIR / "dex_dex_diagnostics.json"
    if diag_path.exists():
        try:
            diag = json.loads(diag_path.read_text(encoding="utf-8-sig"))
            existing = {
                (o.get("key") or "")
                for o in opps
                if isinstance(o, dict)
            }
            for eng_name in ("cross", "chain"):
                for nm in ((diag.get(eng_name) or {}).get("near_miss") or []):
                    if not isinstance(nm, dict):
                        continue
                    key = (
                        f"near:{nm.get('network')}:{nm.get('token_coin')}:"
                        f"{nm.get('buy_dex')}:{nm.get('sell_dex')}:{nm.get('hops')}"
                    )
                    if key in existing:
                        continue
                    existing.add(key)
                    opps.append(
                        {
                            "key": key,
                            "network": nm.get("network"),
                            "token_coin": nm.get("token_coin") or "",
                            "direction": (
                                "DEX_DEX_NEAR"
                                if nm.get("engine") == "cross"
                                else "CHAIN_NEAR"
                            ),
                            "buy_dex": nm.get("buy_dex") or "",
                            "sell_dex": nm.get("sell_dex") or "",
                            "dex": (
                                f"{nm.get('buy_dex')}→{nm.get('sell_dex')}"
                                if nm.get("buy_dex")
                                else "multi"
                            ),
                            "hops": nm.get("hops") or 2,
                            "base_amount_usd": 0,
                            "gross_profit_pct": nm.get("gross_pct") or 0,
                            "net_profit_pct": nm.get("net_pct") or 0,
                            "net_profit_usd": 0,
                            "gas_usd": nm.get("gas_usd") or 0,
                            "warnings": ["near_miss", "from_diagnostics"],
                            "fresh": export_age < fresh_max,
                            "quoted_ts": diag.get("exported_ts") or exported_ts,
                        }
                    )
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    for o in opps:
        if not isinstance(o, dict):
            continue
        age = max(export_age, 0)
        o["export_age_sec"] = round(age, 1)
        o["fresh"] = bool(o.get("fresh", True)) and age < fresh_max
    return {
        "count": len(opps),
        "fresh_count": sum(1 for o in opps if isinstance(o, dict) and o.get("fresh")),
        "export_age_sec": round(export_age, 1) if exported_ts else None,
        "stale_export": export_age > fresh_max,
        "opportunities": opps,
    }


@app.get("/api/dex_dex/universe")
async def dex_dex_universe_stats():
    from services.dex_dex_universe import DEX_DEX_POOLS_PATH, POOLS_CACHE_PATH, load_dex_dex_pools
    from collections import Counter

    pools = load_dex_dex_pools()
    coins = {
        (p.get("network"), (p.get("token_coin") or "").upper())
        for p in pools
        if p.get("token_coin")
    }
    return {
        "pools": len(pools),
        "coins": len(coins),
        "by_network": dict(Counter(p.get("network") for p in pools)),
        "expanded_cache": DEX_DEX_POOLS_PATH.exists(),
        "mexc_cache": POOLS_CACHE_PATH.exists(),
        "source": str(DEX_DEX_POOLS_PATH if DEX_DEX_POOLS_PATH.exists() else POOLS_CACHE_PATH),
    }


@app.get("/api/dex_dex/diagnostics")
async def dex_dex_diagnostics():
    path = DATA_DIR / "dex_dex_diagnostics.json"
    if not path.exists():
        return {"ok": False, "error": "no_diagnostics_yet"}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/dex_dex/signals")
async def dex_dex_signals(
    limit: int = Query(200, ge=1, le=5000),
    session_only: bool = Query(False),
):
    cutoff = _session_cutoff(session_only)
    data = _filter_session(_load_jsonl(DEX_DEX_SIGNALS_PATH), cutoff)
    return {
        "count": len(data),
        "scope": "dex_dex",
        "signals": data[-limit:][::-1],
        "source_file": "data/dex_dex_signals.jsonl",
    }


@app.get("/api/opportunities")
async def opportunities():
    """Live watcher opportunities with `fresh` flag (executable right now)."""
    path = OPPS_LIVE_PATH if OPPS_LIVE_PATH.exists() else OPPS_PATH
    if not path.exists():
        return {"count": 0, "fresh_count": 0, "stale_export": True, "opportunities": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {"count": 0, "fresh_count": 0, "stale_export": True, "opportunities": []}

    # Accept legacy bare list or watcher dict export.
    if isinstance(payload, list):
        payload = {"exported_ts": time.time(), "fresh_max_age_sec": 30, "opportunities": payload}
    if not isinstance(payload, dict):
        return {"count": 0, "fresh_count": 0, "stale_export": True, "opportunities": []}

    now = time.time()
    exported_ts = float(payload.get("exported_ts") or 0)
    export_age = now - exported_ts
    fresh_max = float(payload.get("fresh_max_age_sec") or 30)
    # Re-derive freshness at read time: requote age grows while file sits on disk.
    opps = payload.get("opportunities") or []
    if not isinstance(opps, list):
        opps = []
    for o in opps:
        if not isinstance(o, dict):
            continue
        requote_age = float(o.get("requote_age_sec") or 0) + max(export_age, 0)
        o["requote_age_sec"] = round(requote_age, 1)
        o["fresh"] = bool(o.get("fresh")) and requote_age < fresh_max
    return {
        "count": len(opps),
        "fresh_count": sum(1 for o in opps if isinstance(o, dict) and o.get("fresh")),
        "export_age_sec": round(export_age, 1),
        "stale_export": export_age > fresh_max,
        "opportunities": [o for o in opps if isinstance(o, dict)],
    }


@app.get("/api/archive")
async def archive(limit: int = Query(200, ge=1, le=5000)):
    """Expired / unprofitable opportunities kept for history."""
    rows = _load_jsonl(ARCHIVE_PATH)
    reasons = Counter(str(r.get("reason") or "?") for r in rows)
    nets = Counter(str(r.get("network") or "?") for r in rows)
    return {
        "count": len(rows),
        "items": rows[-limit:][::-1],
        "reason_dist": dict(reasons.most_common()),
        "network_dist": dict(nets.most_common()),
    }


@app.get("/api/pools")
async def pools(limit: int = Query(500, ge=1, le=20000)):
    data = _load_pools()
    return {"count": len(data), "pools": data[:limit]}


@app.get("/api/performance")
async def performance(limit: int = Query(100, ge=1, le=2000)):
    data = _load_jsonl(PERF_PATH)
    return {"count": len(data), "records": data[-limit:][::-1]}


@app.get("/api/db/tables")
async def db_tables():
    conn = _db_connect()
    if not conn:
        return JSONResponse({"error": "db_unavailable"}, status_code=503)
    tables = []
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for r in rows:
            name = r["name"]
            try:
                cnt = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                cols = [c["name"] for c in conn.execute(f'PRAGMA table_info("{name}")')]
                tables.append({"name": name, "rows": cnt, "columns": cols})
            except sqlite3.Error:
                tables.append({"name": name, "rows": -1, "columns": []})
    finally:
        conn.close()
    return {"tables": tables}


@app.get("/api/db/table/{name}")
async def db_table(name: str, limit: int = Query(50, ge=1, le=1000)):
    conn = _db_connect()
    if not conn:
        return JSONResponse({"error": "db_unavailable"}, status_code=503)
    try:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if name not in tables:
            return JSONResponse({"error": "table_not_found"}, status_code=404)
        cols = [c["name"] for c in conn.execute(f'PRAGMA table_info("{name}")')]
        rows = conn.execute(
            f'SELECT * FROM "{name}" ORDER BY rowid DESC LIMIT ?', (limit,)
        ).fetchall()
        return {
            "table": name,
            "columns": cols,
            "rows": [dict(r) for r in rows],
        }
    except sqlite3.Error as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        conn.close()


@app.get("/api/logs")
async def logs(
    tail: int = Query(500, ge=1, le=100000),
    file: str | None = Query(None, description="Log filename from /api/logs available list"),
    offset: int = Query(0, ge=0, description="Skip N lines from start when reading full file"),
):
    """Read bot logs. Pass a large `tail` (or file + high limit) to see full history."""
    files = _log_files()
    chosen = None
    if file:
        for f in files:
            if f.name == file or str(f).replace("\\", "/").endswith(file.replace("\\", "/")):
                chosen = f
                break
        if chosen is None:
            return JSONResponse(
                {"error": "file_not_found", "available": [f.name for f in files]},
                status_code=404,
            )
    elif files:
        chosen = files[0]

    lines: list[str] = []
    total_lines = 0
    if chosen:
        try:
            content = chosen.read_text(encoding="utf-8", errors="replace").splitlines()
            total_lines = len(content)
            if offset:
                content = content[offset:]
            lines = content[-tail:]
        except OSError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    return {
        "file": chosen.name if chosen else None,
        "path": str(chosen.relative_to(PROJECT_ROOT)).replace("\\", "/") if chosen else None,
        "available": [
            {
                "name": f.name,
                "path": str(f.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "size": f.stat().st_size,
                "mtime": f.stat().st_mtime,
            }
            for f in files
        ],
        "total_lines": total_lines,
        "returned": len(lines),
        "lines": lines,
    }


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "db": DB_PATH.exists(),
        "pools_cache": POOLS_PATH.exists(),
        "signals": SIGNALS_PATH.exists(),
        "alt_cex_signals": ALT_CEX_SIGNALS_PATH.exists(),
        "server_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "refresh": read_refresh_status(),
    }


# Static frontend (mounted last so /api and / win).
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
