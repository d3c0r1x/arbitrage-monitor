"""
Unit tests for SignalWriter.

Tests:
- write_signal prints to stdout
- write_signal appends to JSONL file
- write_signal writes to SQLite
- SQLite write failure handled gracefully
"""

import json
import os
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest

from models.signal_models import ArbitrageSignal, FeeBreakdown


class TestSignalWriter:
    """Tests for SignalWriter."""

    def make_signal(self) -> ArbitrageSignal:
        return ArbitrageSignal(
            timestamp=datetime(2026, 7, 25, tzinfo=UTC),
            network="ETHEREUM",
            token_coin="TEST",
            token_address="0xtest",
            mexc_quote_asset="USDT",
            mexc_symbol="TESTUSDT",
            pool_stablecoin_coin="USDT",
            pool_stablecoin_address="0xstable",
            pool_address="0xpool",
            dex="uniswap",
            pool_version="v3",
            direction="DEX_BUY_MEXC_SELL",
            base_amount_usd=Decimal("10"),
            mexc_price_usd=Decimal("1.50"),
            dex_amount_in=Decimal("10"),
            dex_amount_out=Decimal("15000000000000000000"),
            gross_profit_usd=Decimal("0.50"),
            gross_profit_pct=Decimal("5.0"),
            fees=FeeBreakdown(
                dex_network_fee_usd=Decimal("0.05"),
                mexc_trading_fee_usd=Decimal("0.01"),
                total=Decimal("0.06"),
            ),
            net_profit_usd=Decimal("0.44"),
            net_profit_pct=Decimal("4.4"),
            full_cycle=True,
            warnings=[],
        )

    @pytest.mark.asyncio
    async def test_writes_to_stdout(self):
        """write_signal prints JSON to stdout."""
        from scanner.signal_writer import SignalWriter

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            jsonl_path = f.name

        try:
            writer = SignalWriter(signals_jsonl_path=jsonl_path)
            signal = self.make_signal()

            with patch("builtins.print") as mock_print:
                await writer.write_signal(signal)

            mock_print.assert_called_once()
            printed = json.loads(mock_print.call_args[0][0])
            assert printed["network"] == "ETHEREUM"
            assert printed["token_coin"] == "TEST"
        finally:
            os.unlink(jsonl_path)

    @pytest.mark.asyncio
    async def test_writes_to_jsonl(self):
        """write_signal appends to JSONL file."""
        from scanner.signal_writer import SignalWriter

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            jsonl_path = f.name

        try:
            writer = SignalWriter(signals_jsonl_path=jsonl_path)
            signal = self.make_signal()

            with patch("builtins.print"):
                await writer.write_signal(signal)

            with open(jsonl_path) as f:
                lines = f.readlines()
            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["network"] == "ETHEREUM"
            assert record["net_profit_pct"] == "4.4"
        finally:
            os.unlink(jsonl_path)

    @pytest.mark.asyncio
    async def test_writes_to_sqlite(self):
        """write_signal writes to SQLite when db_path is configured in constructor."""
        from scanner.signal_writer import SignalWriter

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            jsonl_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            writer = SignalWriter(signals_jsonl_path=jsonl_path, db_path=db_path)
            signal = self.make_signal()

            # Create signals table in file-based DB.
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER, network TEXT, token_coin TEXT,
                    token_address TEXT, mexc_quote_asset TEXT, mexc_symbol TEXT,
                    pool_stablecoin_coin TEXT, pool_stablecoin_address TEXT,
                    pool_address TEXT, dex TEXT, pool_version TEXT,
                    direction TEXT, base_amount_usd TEXT, mexc_price_usd TEXT,
                    dex_amount_in TEXT, dex_amount_out TEXT,
                    gross_profit_usd TEXT, gross_profit_pct TEXT,
                    fees_json TEXT, net_profit_usd TEXT, net_profit_pct TEXT,
                    full_cycle INTEGER, warnings_json TEXT, created_at INTEGER
                )
            """)
            conn.commit()
            conn.close()

            with patch("builtins.print"):
                await writer.write_signal(signal)

            conn = sqlite3.connect(db_path)
            row = conn.execute("SELECT network, token_coin, net_profit_pct FROM signals").fetchone()
            conn.close()

            assert row is not None
            assert row[0] == "ETHEREUM"
            assert row[1] == "TEST"
            assert row[2] == "4.4"

        finally:
            os.unlink(jsonl_path)
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_sqlite_failure_does_not_crash(self):
        """SQLite write failure is caught and logged, does not crash."""
        from scanner.signal_writer import SignalWriter

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            jsonl_path = f.name

        try:
            # Use :memory: which doesn't have the signals table.
            writer = SignalWriter(signals_jsonl_path=jsonl_path, db_path=":memory:")
            signal = self.make_signal()

            with patch("builtins.print"):
                await writer.write_signal(signal)

            # Verify JSONL was still written.
            with open(jsonl_path) as f:
                lines = f.readlines()
            assert len(lines) == 1

        finally:
            os.unlink(jsonl_path)

    @pytest.mark.asyncio
    async def test_writes_multiple_signals(self):
        """Multiple write_signal calls append correctly."""
        from scanner.signal_writer import SignalWriter

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            jsonl_path = f.name

        try:
            writer = SignalWriter(signals_jsonl_path=jsonl_path)
            signal = self.make_signal()

            with patch("builtins.print"):
                await writer.write_signal(signal)
                await writer.write_signal(signal)
                await writer.write_signal(signal)

            with open(jsonl_path) as f:
                lines = f.readlines()
            assert len(lines) == 3
        finally:
            os.unlink(jsonl_path)

    @pytest.mark.asyncio
    async def test_default_path_in_data_dir(self):
        """Default signals path is data/signals.jsonl."""
        from scanner.signal_writer import SignalWriter

        writer = SignalWriter()
        assert "data" in str(writer._signals_path)
        assert writer._signals_path.name == "signals.jsonl"
