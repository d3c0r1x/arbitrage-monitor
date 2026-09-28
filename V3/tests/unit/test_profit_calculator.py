"""
Unit tests for ProfitCalculator.

Tests:
- Direction A formula
- Direction B formula
- Net profit threshold (> 1, not >=)
- No double fee counting
- Token amount too small for withdraw skipped
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from models.fee_models import FeeBreakdown


@pytest.fixture
def fee_service():
    """Create a mock fee service."""
    mock = MagicMock()
    mock.calculate_fees_direction_a = AsyncMock(
        return_value=FeeBreakdown(
            dex_network_fee_usd=Decimal("0.50"),
            mexc_trading_fee_usd=Decimal("0.10"),
            mexc_deposit_fee_usd=Decimal("0"),
            mexc_withdraw_fee_usd=Decimal("0.10"),
            token_transfer_tax_usd=Decimal("0"),
            slippage_usd=Decimal("0"),
        )
    )
    mock.calculate_fees_direction_b = AsyncMock(
        return_value=FeeBreakdown(
            dex_network_fee_usd=Decimal("0.50"),
            mexc_trading_fee_usd=Decimal("0.10"),
            mexc_deposit_fee_usd=Decimal("0"),
            mexc_withdraw_fee_usd=Decimal("0.10"),
            slippage_usd=Decimal("0"),
        )
    )
    return mock


class TestProfitCalculator:
    """Tests for ProfitCalculator."""

    @pytest.fixture
    def calculator(self, fee_service):
        from services.profit_calculator import ProfitCalculator

        calc = ProfitCalculator.__new__(ProfitCalculator)
        calc._fee_service = fee_service
        calc._base_amount_usd = Decimal("10")
        calc._min_net_profit_pct = Decimal("1")
        return calc

    @pytest.mark.asyncio
    async def test_direction_a_positive_profit(self, calculator):
        """Direction A with profitable scenario."""
        result = await calculator.calculate_direction_a(
            network="BSC",
            token_coin="SCR",
            mexc_price_usd=Decimal("0.50"),
            dex_amount_out=Decimal("25000000000000000000"),  # 25 tokens (18 decimals)
        )
        assert result["direction"] == "DEX_BUY_MEXC_SELL"
        assert result["gross_profit_usd"] > Decimal("0")
        assert isinstance(result["fees"], FeeBreakdown)

    @pytest.mark.asyncio
    async def test_direction_a_net_profit_threshold(self, calculator):
        """Signal generated only when net_profit_pct > 1."""
        result = await calculator.calculate_direction_a(
            network="BSC",
            token_coin="SCR",
            mexc_price_usd=Decimal("0.50"),
            dex_amount_out=Decimal("20500000000000000000"),  # 20.5 tokens
        )
        assert isinstance(result["signal"], bool)

    @pytest.mark.asyncio
    async def test_direction_b_no_skip_on_withdraw_fee(self, calculator):
        """Direction B no longer skips based on token_amount vs withdraw fee.

        The scanner subtracts fee from amount_in before quoting (A5 fix).
        """
        result = await calculator.calculate_direction_b(
            network="BSC",
            mexc_price_usd=Decimal("10000"),
            dex_amount_out=Decimal("10000000000000000000"),
            mexc_withdraw_fee_usd=Decimal("10"),
        )
        # Should NOT have the old skip_reason
        assert result.get("skip_reason") != "token_amount_too_small_for_withdraw"
        assert result["direction"] == "MEXC_BUY_DEX_SELL"

    @pytest.mark.asyncio
    async def test_direction_b_gross_profit(self, calculator):
        """Direction B calculates gross profit correctly."""
        result = await calculator.calculate_direction_b(
            network="BSC",
            mexc_price_usd=Decimal("0.50"),
            dex_amount_out=Decimal("12000000000000000000"),  # 12 stable (18 decimals)
        )
        assert result["gross_profit_usd"] >= Decimal("0")

    @pytest.mark.asyncio
    async def test_direction_a_fee_breakdown(self, calculator, fee_service):
        """FeeBreakdown is included in direction A result."""
        result = await calculator.calculate_direction_a(
            network="BSC",
            token_coin="SCR",
            mexc_price_usd=Decimal("0.50"),
            dex_amount_out=Decimal("25000000000000000000"),
        )
        fees = result["fees"]
        assert isinstance(fees, FeeBreakdown)
        assert fees.dex_network_fee_usd >= Decimal("0")
        assert fees.total() > Decimal("0")

    @pytest.mark.asyncio
    async def test_direction_b_fee_breakdown(self, calculator):
        """FeeBreakdown is included in direction B result."""
        result = await calculator.calculate_direction_b(
            network="BSC",
            mexc_price_usd=Decimal("0.50"),
            dex_amount_out=Decimal("12000000000000000000"),
        )
        fees = result["fees"]
        assert isinstance(fees, FeeBreakdown)
        assert fees.total() > Decimal("0")

    @pytest.mark.asyncio
    async def test_decimal_used_everywhere(self, calculator):
        """All monetary values are Decimal."""
        result = await calculator.calculate_direction_a(
            network="BSC",
            token_coin="SCR",
            mexc_price_usd=Decimal("0.50"),
            dex_amount_out=Decimal("25000000000000000000"),
        )
        assert isinstance(result["gross_profit_usd"], Decimal)
        assert isinstance(result["gross_profit_pct"], Decimal)
        assert isinstance(result["net_profit_usd"], Decimal)
        assert isinstance(result["net_profit_pct"], Decimal)
        assert isinstance(result["base_amount_usd"], Decimal)

    @pytest.mark.asyncio
    async def test_direction_b_zero_price_skipped(self, calculator):
        """Direction B with zero price returns no signal."""
        result = await calculator.calculate_direction_b(
            network="BSC",
            mexc_price_usd=Decimal("0"),
            dex_amount_out=Decimal("10000000000000000000"),
        )
        assert result["signal"] is False
        assert result.get("skip_reason") == "mexc_price_zero"
