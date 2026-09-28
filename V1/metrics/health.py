"""
Source health tracking + HTTP health/metrics endpoint.

Tracks consecutive failures, last success/failure times in SQLite.
Provides an optional HTTP server (thread) with:
  GET /health  → JSON status
  GET /metrics → Prometheus-style text metrics
"""

import json
import logging
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from config.settings import settings

logger = logging.getLogger(__name__)

# ── Shared metrics state (updated by main.py at runtime) ────────────────
_metrics_state: dict = {
    "started_at": 0.0,
    "signals_total": 0,
    "scanner_cycles": 0,
    "pools_cached": 0,
    "mexc_assets": 0,
    "pools_in_db": 0,
    "stablecoins": 0,
    "errors_seen": 0,
    "last_scanner_cycle_ts": "",
    "last_signal_ts": "",
    "rpc_clients_count": 0,
    "http_clients_active": 0,
}

_metrics_lock = threading.Lock()


def update_metrics(**kwargs) -> None:
    """Thread-safe update of shared metrics state."""
    with _metrics_lock:
        _metrics_state.update(kwargs)


def increment_metrics(**kwargs) -> None:
    """Thread-safe increment of numeric metrics (counters)."""
    with _metrics_lock:
        for key, value in kwargs.items():
            _metrics_state[key] = _metrics_state.get(key, 0) + value


def get_metrics() -> dict:
    """Thread-safe snapshot of shared metrics state."""
    with _metrics_lock:
        return dict(_metrics_state)


# ── HTTP request handler ────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    """HTTP handler for /health and /metrics endpoints."""

    def do_GET(self):
        if self.path == "/health":
            self._json_response(200, {
                "status": "ok",
                "uptime_sec": int(time.time() - get_metrics().get("started_at", time.time())),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "bot_running": True,
            })
        elif self.path == "/metrics":
            m = get_metrics()
            lines = [
                "# HELP arb_bot_uptime_seconds Bot uptime",
                "# TYPE arb_bot_uptime_seconds gauge",
                f"arb_bot_uptime_seconds {int(time.time() - m.get('started_at', time.time()))}",
                "",
                "# HELP arb_bot_signals_total Total signals found",
                "# TYPE arb_bot_signals_total counter",
                f"arb_bot_signals_total {m.get('signals_total', 0)}",
                "",
                "# HELP arb_bot_scanner_cycles_total Scanner cycles completed",
                "# TYPE arb_bot_scanner_cycles_total counter",
                f"arb_bot_scanner_cycles_total {m.get('scanner_cycles', 0)}",
                "",
                "# HELP arb_bot_pools_cached Pools in cache file",
                "# TYPE arb_bot_pools_cached gauge",
                f"arb_bot_pools_cached {m.get('pools_cached', 0)}",
                "",
                "# HELP arb_bot_mexc_assets MEXC assets in DB",
                "# TYPE arb_bot_mexc_assets gauge",
                f"arb_bot_mexc_assets {m.get('mexc_assets', 0)}",
                "",
                "# HELP arb_bot_pools_in_db Pools in DB",
                "# TYPE arb_bot_pools_in_db gauge",
                f"arb_bot_pools_in_db {m.get('pools_in_db', 0)}",
                "",
                "# HELP arb_bot_stablecoins Stablecoin records in DB",
                "# TYPE arb_bot_stablecoins gauge",
                f"arb_bot_stablecoins {m.get('stablecoins', 0)}",
                "",
                "# HELP arb_bot_errors_seen_total Errors observed in logs",
                "# TYPE arb_bot_errors_seen_total counter",
                f"arb_bot_errors_seen_total {m.get('errors_seen', 0)}",
            ]
            self._text_response(200, "\n".join(lines) + "\n")
        else:
            self._json_response(404, {"error": "not_found", "path": self.path})

    def _json_response(self, status: int, data: dict):
        body = json.dumps(data, indent=2) + "\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def _text_response(self, status: int, body: str):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, fmt, *args):
        logger.debug("health_http: " + fmt, *args)


# ── Server launcher ─────────────────────────────────────────────────────

def start_health_server(host: str = "127.0.0.1", port: int = 8765) -> HTTPServer:
    """Start the health/metrics HTTP server in a daemon thread.

    Returns the server object (can be .shutdown() later).
    """
    server = HTTPServer((host, port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("health_server_started: http://%s:%d", host, port)
    return server


# ── SourceHealthTracker (unchanged) ─────────────────────────────────────

class SourceHealthTracker:
    """Tracks health state of pool discovery sources.

    Rules:
    - Successful response: healthy=1, consecutive_failures=0
    - Failure: consecutive_failures += 1
    - If consecutive_failures >= max_failures: healthy=0
    """

    def __init__(
        self,
        db_path: str,
        max_failures: int | None = None,
    ):
        self._db_path = db_path
        self._max_failures = max_failures or settings.DISCOVERY_SOURCE_MAX_FAILURES

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS source_health (
                source TEXT PRIMARY KEY,
                healthy INTEGER NOT NULL DEFAULT 1,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                last_success_at INTEGER,
                last_failure_at INTEGER,
                updated_at INTEGER NOT NULL
            )"""
        )
        return conn

    async def record_success(self, source: str) -> None:
        now = int(time.time())
        try:
            conn = self._connect()
            conn.execute(
                """INSERT INTO source_health (source, healthy, consecutive_failures, last_success_at, updated_at)
                   VALUES (?, 1, 0, ?, ?)
                   ON CONFLICT(source) DO UPDATE SET
                       healthy=1, consecutive_failures=0,
                       last_success_at=excluded.last_success_at,
                       updated_at=excluded.updated_at""",
                (source, now, now),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("source_health_write_error for %s: %s", source, exc)

    async def record_failure(self, source: str, error: str) -> None:
        now = int(time.time())
        try:
            conn = self._connect()
            conn.execute(
                """INSERT INTO source_health (source, healthy, consecutive_failures, last_error, last_failure_at, updated_at)
                   VALUES (?, 1, 1, ?, ?, ?)
                   ON CONFLICT(source) DO UPDATE SET
                       consecutive_failures = consecutive_failures + 1,
                       last_error = excluded.last_error,
                       last_failure_at = excluded.last_failure_at,
                       updated_at = excluded.updated_at""",
                (source, error[:500], now, now),
            )
            cursor = conn.execute(
                "SELECT consecutive_failures FROM source_health WHERE source = ?", (source,)
            )
            row = cursor.fetchone()
            if row and row[0] >= self._max_failures:
                conn.execute("UPDATE source_health SET healthy = 0 WHERE source = ?", (source,))
                logger.warning("source_marked_unhealthy: %s", source)
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("source_health_write_error for %s: %s", source, exc)

    def is_healthy(self, source: str) -> bool:
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT healthy FROM source_health WHERE source = ?", (source,)
            ).fetchone()
            conn.close()
            if row is None:
                return True
            return bool(row[0])
        except Exception:
            return True
