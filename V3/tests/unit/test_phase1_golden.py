"""
Golden tests for Phase 1 money-critical fixes (A1–A5, D1–D2).

Each test uses exact Decimal arithmetic — no approximations.
"""

import sqlite3
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.fee_models import FeeBreakdown

# ─── A1: Direction A uses STABLECOIN withdraw fee ────────────────────────────


class TestA1StablecoinWithdrawFee:
    """Direction A must use stablecoin withdraw fee, not token fee."""

    @pytest.mark.asyncio
    async def test_direction_a_uses_stablecoin_withdraw_fee(self):
        """Fee service receives stablecoin_withdraw_fee_usd for direction A."""
        from services.fee_service import FeeService

        mock_rpc = MagicMock()
        mock_rpc.get_gas_price = AsyncMock(return_value=Decimal("20000000000"))  # 20 gwei

        mock_price = MagicMock()
        mock_price.get_price = MagicMock(return_value=Decimal("3000"))  # ETH=$3000

        fee_svc = FeeService(
            rpc_client_factory=lambda n: mock_rpc,
            price_service=mock_price,
            mexc_taker_fee_bps=10,
        )

        result = await fee_svc.calculate_fees_direction_a(
            network="ETHEREUM",
            token_coin="TEST",
            base_amount_usd=Decimal("10"),
            mexc_price_usd=Decimal("0.5"),
            stablecoin_withdraw_fee_usd=Decimal("3.0"),  # USDT withdraw = $3
            mexc_deposit_fee_usd=Decimal("0"),
            gross_value_usd=Decimal("12"),
            pool_version="v2",
        )

        # The withdraw fee in the breakdown must be the STABLECOIN fee ($3).
        assert result.mexc_withdraw_fee_usd == Decimal("3.0")
        # Trading fee = 12 * 10/10000 = 0.012
        assert result.mexc_trading_fee_usd == Decimal("12") * Decimal("10") / Decimal("10000")

    @pytest.mark.asyncio
    async def test_direction_a_not_token_fee(self):
        """Ensure token withdraw fee is NOT used in direction A."""
        from services.profit_calculator import ProfitCalculator

        mock_fee_svc = MagicMock()
        mock_fee_svc.calculate_fees_direction_a = AsyncMock(
            return_value=FeeBreakdown(
                dex_network_fee_usd=Decimal("0.5"),
                mexc_trading_fee_usd=Decimal("0.01"),
                mexc_withdraw_fee_usd=Decimal("5.0"),  # stablecoin fee
            )
        )

        calc = ProfitCalculator.__new__(ProfitCalculator)
        calc._fee_service = mock_fee_svc
        calc._base_amount_usd = Decimal("10")
        calc._min_net_profit_pct = Decimal("1")

        await calc.calculate_direction_a(
            network="BSC",
            token_coin="TEST",
            mexc_price_usd=Decimal("0.5"),
            dex_amount_out=Decimal("24000000000000000000"),  # 24 tokens
            stablecoin_withdraw_fee_usd=Decimal("5.0"),
            pool_version="v2",
        )

        # Verify the fee service was called with stablecoin_withdraw_fee_usd.
        call_kwargs = mock_fee_svc.calculate_fees_direction_a.call_args[1]
        assert call_kwargs["stablecoin_withdraw_fee_usd"] == Decimal("5.0")
        assert "mexc_withdraw_fee_usd" not in call_kwargs


# ─── A2: stable_deposit_network_fee_usd included in FeeBreakdown ──────────────


class TestA2StableDepositFee:
    """Direction B must include stable_deposit_network_fee_usd in total."""

    def test_fee_breakdown_includes_stable_deposit(self):
        fees = FeeBreakdown(
            dex_network_fee_usd=Decimal("0.5"),
            mexc_trading_fee_usd=Decimal("0.01"),
            stable_deposit_network_fee_usd=Decimal("0.5"),
        )
        assert fees.total() == Decimal("1.01")

    @pytest.mark.asyncio
    async def test_direction_b_includes_stable_deposit_fee(self):
        """Fee service puts stable_deposit_network_fee_usd into FeeBreakdown."""
        from services.fee_service import FeeService

        mock_rpc = MagicMock()
        mock_rpc.get_gas_price = AsyncMock(return_value=Decimal("5000000000"))  # 5 gwei

        mock_price = MagicMock()
        mock_price.get_price = MagicMock(return_value=Decimal("600"))  # BNB=$600

        fee_svc = FeeService(
            rpc_client_factory=lambda n: mock_rpc,
            price_service=mock_price,
            mexc_taker_fee_bps=10,
        )

        result = await fee_svc.calculate_fees_direction_b(
            network="BSC",
            base_amount_usd=Decimal("10"),
            mexc_withdraw_fee_usd=Decimal("0.2"),
            mexc_deposit_fee_usd=Decimal("0"),
            stable_deposit_network_fee_usd=Decimal("0.5"),
            pool_version="v2",
        )

        assert result.stable_deposit_network_fee_usd == Decimal("0.5")
        # Total must include the 0.5
        assert result.total() >= Decimal("0.5")


# ─── A3: SLIPPAGE_BUFFER_BPS nonzero by default ──────────────────────────────


class TestA3SlippageDefault:
    def test_slippage_nonzero_by_default(self):
        """Default SLIPPAGE_BUFFER_BPS must be > 0."""
        # Re-import fresh settings to get default (env may override).
        with patch.dict("os.environ", {}, clear=True):
            from config.settings import Settings
            s = Settings()
            assert s.SLIPPAGE_BUFFER_BPS == 30

    @pytest.mark.asyncio
    async def test_slippage_applied_in_fees(self):
        """With SLIPPAGE_BUFFER_BPS=30, slippage_usd = base * 0.30 / 100."""
        from services.fee_service import FeeService

        mock_rpc = MagicMock()
        mock_rpc.get_gas_price = AsyncMock(return_value=Decimal("1000000000"))
        mock_price = MagicMock()
        mock_price.get_price = MagicMock(return_value=Decimal("3000"))

        fee_svc = FeeService(
            rpc_client_factory=lambda n: mock_rpc,
            price_service=mock_price,
            mexc_taker_fee_bps=10,
        )

        with patch("services.fee_service.settings") as mock_settings:
            mock_settings.SLIPPAGE_BUFFER_BPS = 30
            mock_settings.MEXC_TAKER_FEE_BPS = 10

            result = await fee_svc.calculate_fees_direction_a(
                network="ETHEREUM",
                token_coin="X",
                base_amount_usd=Decimal("10"),
                mexc_price_usd=Decimal("1"),
                stablecoin_withdraw_fee_usd=Decimal("0"),
                mexc_deposit_fee_usd=Decimal("0"),
                gross_value_usd=Decimal("10"),
            )

        # slippage = 10 * (30/100) / 100 = 10 * 0.3 / 100 = 0.03
        expected_slippage = Decimal("10") * Decimal("30") / Decimal("100") / Decimal("100")
        assert result.slippage_usd == expected_slippage


# ─── A4: Signal written to SQLite ────────────────────────────────────────────


class TestA4SignalToSqlite:
    @pytest.mark.asyncio
    async def test_signal_written_to_sqlite(self):
        """write_signal inserts a row into the signals table."""
        from datetime import UTC, datetime

        from models.signal_models import ArbitrageSignal
        from scanner.signal_writer import SignalWriter
        from storage.database import create_connection, initialize_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            jsonl_path = Path(tmpdir) / "signals.jsonl"

            # Initialize schema.
            conn = create_connection(db_path)
            initialize_schema(conn)
            conn.close()

            writer = SignalWriter(
                signals_jsonl_path=str(jsonl_path),
                db_path=str(db_path),
            )

            signal = ArbitrageSignal(
                timestamp=datetime.now(tz=UTC),
                network="BSC",
                token_coin="TEST",
                token_address="0x" + "ab" * 20,
                mexc_quote_asset="USDT",
                mexc_symbol="TESTUSDT",
                pool_stablecoin_coin="USDT",
                pool_stablecoin_address="0x" + "cd" * 20,
                pool_address="0x" + "ef" * 20,
                dex="pancakeswap_v2",
                pool_version="v2",
                direction="DEX_BUY_MEXC_SELL",
                base_amount_usd=Decimal("10"),
                mexc_price_usd=Decimal("0.5"),
                dex_amount_in=Decimal("10"),
                dex_amount_out=Decimal("22000000000000000000"),
                gross_profit_usd=Decimal("1"),
                gross_profit_pct=Decimal("10"),
                fees=FeeBreakdown(dex_network_fee_usd=Decimal("0.3")),
                net_profit_usd=Decimal("0.7"),
                net_profit_pct=Decimal("7"),
                full_cycle=True,
                warnings=[],
            )

            await writer.write_signal(signal)

            # Verify row exists.
            conn = sqlite3.connect(str(db_path))
            count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            conn.close()
            assert count == 1


# ─── A5: Direction B quotes net token amount ─────────────────────────────────


class TestA5NetTokenAmount:
    @pytest.mark.asyncio
    async def test_direction_b_no_double_fee_deduction(self):
        """Profit calculator B does NOT skip based on token_amount vs fee.

        The scanner already subtracts fee from amount_in before quoting.
        """
        from services.profit_calculator import ProfitCalculator

        mock_fee_svc = MagicMock()
        mock_fee_svc.calculate_fees_direction_b = AsyncMock(
            return_value=FeeBreakdown(
                dex_network_fee_usd=Decimal("0.3"),
                mexc_trading_fee_usd=Decimal("0.01"),
                mexc_withdraw_fee_usd=Decimal("0.2"),
                stable_deposit_network_fee_usd=Decimal("0.5"),
            )
        )

        calc = ProfitCalculator.__new__(ProfitCalculator)
        calc._fee_service = mock_fee_svc
        calc._base_amount_usd = Decimal("10")
        calc._min_net_profit_pct = Decimal("1")

        # Even with a large withdraw fee, direction B should NOT skip
        # (the old "token_amount_too_small_for_withdraw" check is removed).
        result = await calc.calculate_direction_b(
            network="BSC",
            mexc_price_usd=Decimal("0.001"),
            dex_amount_out=Decimal("11000000000000000000"),  # 11 stable
            stablecoin_decimals=18,
            mexc_withdraw_fee_usd=Decimal("5"),  # Large fee
        )

        # Should NOT have skip_reason "token_amount_too_small_for_withdraw"
        assert result.get("skip_reason") != "token_amount_too_small_for_withdraw"
        # Should produce a valid result
        assert result["direction"] == "MEXC_BUY_DEX_SELL"
        assert "signal" in result


# ─── D1: Gas fallback logs warning ───────────────────────────────────────────


class TestD1GasFallback:
    @pytest.mark.asyncio
    async def test_gas_fallback_logs_warning(self, caplog):
        """When RPC is None, gas estimate uses per-network fallback + logs."""
        import logging

        from services.fee_service import FeeService

        fee_svc = FeeService(
            rpc_client_factory=lambda n: None,  # No RPC
            price_service=MagicMock(),
            mexc_taker_fee_bps=10,
        )

        with caplog.at_level(logging.WARNING):
            result = await fee_svc._estimate_gas_cost_usd("ETHEREUM", "v2")

        # Should return the Ethereum fallback ($5.0)
        assert result == Decimal("5.0")
        assert "gas_estimate_fallback" in caplog.text

    @pytest.mark.asyncio
    async def test_gas_fallback_per_network(self):
        """Different networks have different fallback values."""
        from services.fee_service import FeeService

        fee_svc = FeeService(
            rpc_client_factory=lambda n: None,
            price_service=MagicMock(),
        )

        eth = await fee_svc._estimate_gas_cost_usd("ETHEREUM")
        bsc = await fee_svc._estimate_gas_cost_usd("BSC")
        polygon = await fee_svc._estimate_gas_cost_usd("POLYGON")

        assert eth == Decimal("5.0")
        assert bsc == Decimal("0.3")
        assert polygon == Decimal("0.01")


# ─── D2: Gas units differ by pool version ────────────────────────────────────


class TestD2GasUnitsByVersion:
    @pytest.mark.asyncio
    async def test_gas_units_differ_by_version(self):
        """v2 uses 120k gas, v3 uses 180k gas."""
        from services.fee_service import _GAS_UNITS_BY_VERSION

        assert _GAS_UNITS_BY_VERSION["v2"] == 120_000
        assert _GAS_UNITS_BY_VERSION["v3"] == 180_000
        assert _GAS_UNITS_BY_VERSION["v2"] < _GAS_UNITS_BY_VERSION["v3"]

    @pytest.mark.asyncio
    async def test_v3_costs_more_than_v2(self):
        """With same gas price, v3 swap costs more than v2."""
        from services.fee_service import FeeService

        mock_rpc = MagicMock()
        mock_rpc.get_gas_price = AsyncMock(return_value=Decimal("10000000000"))  # 10 gwei

        mock_price = MagicMock()
        mock_price.get_price = MagicMock(return_value=Decimal("3000"))  # ETH=$3000

        fee_svc = FeeService(
            rpc_client_factory=lambda n: mock_rpc,
            price_service=mock_price,
        )

        cost_v2 = await fee_svc._estimate_gas_cost_usd("ETHEREUM", "v2")
        cost_v3 = await fee_svc._estimate_gas_cost_usd("ETHEREUM", "v3")

        assert cost_v3 > cost_v2
        # Ratio should be 180k/120k = 1.5
        ratio = cost_v3 / cost_v2
        assert ratio == Decimal("1.5")
