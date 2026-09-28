"""Tests for native→USDT closing pools and Direction B settlement."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.closing_pools import get_closing_pool
from models.fee_models import FeeBreakdown


class TestClosingPoolsConfig:
    def test_bsc_wbnb_usdt_pool(self):
        pool = get_closing_pool(
            "BSC", "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
        )
        assert pool is not None
        assert pool.pool_address == "0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae"
        assert pool.dex_id == "pancakeswap_v2"
        assert pool.token_out_decimals == 18
        assert pool.settlement_coin == "USDT"

    def test_unknown_quote_returns_none(self):
        assert get_closing_pool("BSC", "0xdead") is None
        assert get_closing_pool("ETHEREUM", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c") is None


class TestProfitCalculatorClosing:
    @pytest.fixture
    def fee_service(self):
        mock = MagicMock()
        mock.calculate_fees_direction_b = AsyncMock(
            return_value=FeeBreakdown(
                dex_network_fee_usd=Decimal("0.60"),
                mexc_trading_fee_usd=Decimal("0.10"),
                mexc_deposit_fee_usd=Decimal("0"),
                mexc_withdraw_fee_usd=Decimal("0.10"),
                slippage_usd=Decimal("0"),
            )
        )
        return mock

    @pytest.fixture
    def calculator(self, fee_service):
        from services.profit_calculator import ProfitCalculator

        calc = ProfitCalculator.__new__(ProfitCalculator)
        calc._fee_service = fee_service
        calc._base_amount_usd = Decimal("10")
        calc._min_net_profit_pct = Decimal("1")
        return calc

    @pytest.mark.asyncio
    async def test_direction_b_closing_usdt_at_par(self, calculator, fee_service):
        """Closed USDT output valued at $1, not via native spot."""
        result = await calculator.calculate_direction_b(
            network="BSC",
            mexc_price_usd=Decimal("0.01"),
            dex_amount_out=Decimal("12500000000000000000"),  # 12.5 USDT (18d)
            stablecoin_decimals=18,
            quote_price_usd=Decimal("1"),
            swap_hops=2,
            closing_pool_version="v2",
            settlement_coin="USDT",
            closing_applied=True,
        )
        assert result["closing_applied"] is True
        assert result["settlement_coin"] == "USDT"
        assert result["settlement_out_usd"] == Decimal("12.5")
        fee_service.calculate_fees_direction_b.assert_awaited()
        kwargs = fee_service.calculate_fees_direction_b.await_args.kwargs
        assert kwargs["swap_hops"] == 2
        assert kwargs["closing_pool_version"] == "v2"


class TestScannerClosingQuote:
    @pytest.mark.asyncio
    async def test_quote_closing_to_usdt(self):
        from scanner.scanner import Scanner

        scanner = Scanner.__new__(Scanner)
        adapter = MagicMock()
        adapter.quote_exact_input = AsyncMock(
            return_value=Decimal("123450000000000000000")
        )
        scanner._adapter_factory = MagicMock()
        scanner._adapter_factory.get_adapter = MagicMock(return_value=adapter)

        out = await scanner._quote_closing_to_usdt(
            network="BSC",
            quote_address="0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
            amount_in=10**17,  # 0.1 WBNB
        )
        assert out is not None
        amount, meta = out
        assert amount == Decimal("123450000000000000000")
        assert meta["settlement_coin"] == "USDT"
        assert meta["decimals"] == 18
        adapter.quote_exact_input.assert_awaited_once()
        call_kw = adapter.quote_exact_input.await_args.kwargs
        assert call_kw["pool_address"] == "0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae"
        assert call_kw["token_out"] == "0x55d398326f99059ff775485246999027b3197955"

    @pytest.mark.asyncio
    async def test_quote_closing_missing_adapter(self):
        from scanner.scanner import Scanner

        scanner = Scanner.__new__(Scanner)
        scanner._adapter_factory = MagicMock()
        scanner._adapter_factory.get_adapter = MagicMock(return_value=None)

        out = await scanner._quote_closing_to_usdt(
            network="BSC",
            quote_address="0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
            amount_in=10**17,
        )
        assert out is None
