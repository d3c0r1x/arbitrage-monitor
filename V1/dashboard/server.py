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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SIGNALS_PATH = DATA_DIR / "signals.jsonl"
PERF_PATH = DATA_DIR / "performance.jsonl"
POOLS_PATH = DATA_DIR / "pools_cache.json"
OPPS_PATH = DATA_DIR / "opportunities.json"
DB_PATH = DATA_DIR / "state" / "active.sqlite3"
LOGS_DIR = PROJECT_ROOT / ".run" / "logs"
WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(title="MEXC × DEX Arbitrage Dashboard", version="2.0")

# Server start time for session filtering (hide stale data from previous runs).
SERVER_START_TS = time.time()


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
    if LOGS_DIR.exists():
        candidates.extend(LOGS_DIR.glob("*.log"))
    if DATA_DIR.exists():
        candidates.extend(DATA_DIR.glob("*.log"))
        candidates.extend(DATA_DIR.glob("*.txt"))
    return sorted(
        (p for p in candidates if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


_log_level_cache: dict = {"ts": 0, "errors": 0, "warnings": 0}
_LOG_CACHE_TTL = 30  # seconds


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


@app.get("/api/summary")
async def summary(session_only: bool = Query(False)):
    signals = _load_jsonl(SIGNALS_PATH)
    if session_only:
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(SERVER_START_TS))
        signals = [s for s in signals if (s.get("timestamp") or "") >= cutoff]
    pools = _load_pools()
    perf = _load_jsonl(PERF_PATH)

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
    pool_nets = Counter(p.get("network", "?") for p in pools)
    warns = Counter()
    for s in signals:
        for w in s.get("warnings", []) or []:
            warns[w] += 1

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

    last_signal_ts = signals[-1].get("timestamp", "") if signals else ""

    return {
        "signals_total": total,
        "signals_clean": clean,
        "signals_thin": thin,
        "median_profit_pct": round(median_p, 2),
        "max_profit_pct": round(max_p, 2),
        "pools_cached": len(pools),
        "db_assets": db_assets,
        "db_pools": db_pools,
        "db_stables": db_stables,
        "db_sources": db_sources,
        "scanner_cycles": len(scanner_recs),
        "avg_scan_ms": round(sum(sc_durs) / len(sc_durs), 0) if sc_durs else 0,
        "errors": errors,
        "warnings": warnings,
        "perf_records": len(perf),
        "last_signal_ts": last_signal_ts,
        "server_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "uptime_sec": int(time.time() - SERVER_START_TS),
        "network_dist": dict(nets.most_common()),
        "dex_dist": dict(dexes.most_common()),
        "pool_net_dist": dict(pool_nets.most_common()),
        "warning_dist": dict(warns.most_common()),
    }


@app.get("/api/signals")
async def signals(limit: int = Query(200, ge=1, le=5000), session_only: bool = Query(False)):
    data = _load_jsonl(SIGNALS_PATH)
    if session_only:
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(SERVER_START_TS))
        data = [s for s in data if (s.get("timestamp") or "") >= cutoff]
    return {"count": len(data), "signals": data[-limit:][::-1]}


@app.get("/api/opportunities")
async def opportunities():
    """Live watcher opportunities with `fresh` flag (executable right now)."""
    if not OPPS_PATH.exists():
        return {"count": 0, "fresh_count": 0, "stale_export": True, "opportunities": []}
    try:
        payload = json.loads(OPPS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"count": 0, "fresh_count": 0, "stale_export": True, "opportunities": []}

    now = time.time()
    exported_ts = float(payload.get("exported_ts") or 0)
    export_age = now - exported_ts
    fresh_max = float(payload.get("fresh_max_age_sec") or 30)
    # Re-derive freshness at read time: requote age grows while file sits on disk.
    opps = payload.get("opportunities") or []
    for o in opps:
        requote_age = float(o.get("requote_age_sec") or 0) + max(export_age, 0)
        o["requote_age_sec"] = round(requote_age, 1)
        o["fresh"] = bool(o.get("fresh")) and requote_age < fresh_max
    return {
        "count": len(opps),
        "fresh_count": sum(1 for o in opps if o["fresh"]),
        "export_age_sec": round(export_age, 1),
        "stale_export": export_age > fresh_max,
        "opportunities": opps,
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
async def logs(tail: int = Query(300, ge=1, le=5000)):
    files = _log_files()
    lines: list[str] = []
    if files:
        try:
            content = files[0].read_text(encoding="utf-8", errors="replace").splitlines()
            lines = content[-tail:]
        except OSError:
            pass
    return {
        "file": files[0].name if files else None,
        "available": [f.name for f in files],
        "lines": lines,
    }


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "db": DB_PATH.exists(),
        "pools_cache": POOLS_PATH.exists(),
        "signals": SIGNALS_PATH.exists(),
        "server_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# Static frontend (mounted last so /api and / win).
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
