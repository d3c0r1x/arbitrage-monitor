"""
Signal writer.

Writes arbitrage signals to:
1. stdout (JSON lines)
2. data/signals.jsonl (JSON lines file)
3. SQLite signals table
"""

import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from models.signal_models import ArbitrageSignal
from utils.jsonl_rotator import JsonlRotator

logger = logging.getLogger(__name__)


class SignalWriter:
    """Writes arbitrage signals to multiple outputs."""

    def __init__(self, signals_jsonl_path: str = "data/signals.jsonl", db_path: str | None = None):
        self._signals_path = Path(signals_jsonl_path)
        self._signals_path.parent.mkdir(parents=True, exist_ok=True)
        self._rotator = JsonlRotator(signals_jsonl_path)
        self._db_path = db_path

    async def write_signal(
        self,
        signal: ArbitrageSignal,
    ) -> None:
        """Write an arbitrage signal."""
        # 1. Write to stdout as JSON.
        print(json.dumps(signal.model_dump(mode="json", serialize_as_any=True), default=str))

        # 2. Write to JSONL file (with rotation).
        line = json.dumps(signal.model_dump(mode="json", serialize_as_any=True), default=str)
        self._rotator.write_line(line)

        # G2: Update health metrics on signal.
        try:
            from metrics.health import increment_metrics, update_metrics
            increment_metrics(signals_total=1)
            update_metrics(last_signal_ts=time.time())
        except Exception:
            pass

        # 3. Write to SQLite if db_path configured.
        if self._db_path:
            try:
                conn = sqlite3.connect(self._db_path)
                conn.execute("PRAGMA busy_timeout=5000;")
                conn.execute(
                    """
                    INSERT INTO signals
                    (timestamp, network, token_coin, token_address, mexc_quote_asset,
                     mexc_symbol, pool_stablecoin_coin, pool_stablecoin_address,
                     pool_address, dex, pool_version, direction, base_amount_usd,
                     mexc_price_usd, dex_amount_in, dex_amount_out,
                     gross_profit_usd, gross_profit_pct, fees_json,
                     net_profit_usd, net_profit_pct, full_cycle, warnings_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(signal.timestamp.timestamp()),
                        signal.network,
                        signal.token_coin,
                        signal.token_address,
                        signal.mexc_quote_asset,
                        signal.mexc_symbol,
                        signal.pool_stablecoin_coin,
                        signal.pool_stablecoin_address,
                        signal.pool_address,
                        signal.dex,
                        signal.pool_version,
                        signal.direction,
                        str(signal.base_amount_usd),
                        str(signal.mexc_price_usd),
                        str(signal.dex_amount_in),
                        str(signal.dex_amount_out),
                        str(signal.gross_profit_usd),
                        str(signal.gross_profit_pct),
                        signal.fees.model_dump_json(),
                        str(signal.net_profit_usd),
                        str(signal.net_profit_pct),
                        1 if signal.full_cycle else 0,
                        json.dumps(signal.warnings),
                        int(datetime.now(tz=UTC).timestamp()),
                    ),
                )
                conn.commit()
                conn.close()
            except Exception as exc:
                logger.error("failed_to_write_signal_to_db: %s", exc)
