"""
Unit tests for FeeService.

Tests:
- Direction A fees: trading fee, gas, slippage, deposit/withdraw
- Direction B fees: trading fee, gas, slippage, deposit/withdraw
- Gas estimation: default 1.0 when RPC unavailable
- Gas estimation: calculates correctly when RPC returns gas price
- Native coin mapping for all networks
- FeeBreakdown totals
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestFeeService:
    """Tests for FeeService."""

    @pytest.fixture
    def price_service(self):
        """Mock price service that returns native token prices."""
        mock = MagicMock()
        mock.get_price = MagicMock(return_value=Decimal("3000"))  # ETH/USDT = $3000
        return mock

    @pytest.fixture
    def fee_service(self, price_service):
        """Create FeeService with mocked dependencies and 10 bps taker fee."""
        from services.fee_service import FeeService

        svc = FeeService.__new__(FeeService)
        svc._rpc_client_factory = MagicMock()
        svc._price_service = price_service
        svc._mexc_taker_fee_bps = Decimal("10")  # 0.1%
        return svc

    # ── Direction A ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_direction_a_returns_fee_breakdown(self, fee_service):
        """Direction A returns a valid FeeBreakdown with all fields."""
        result = await fee_service.calculate_fees_direction_a(
            network="ETHEREUM",
            token_coin="TEST",
            base_amount_usd=Decimal("10"),
            mexc_price_usd=Decimal("1.00"),
            stablecoin_withdraw_fee_usd=Decimal("0.10"),
            mexc_deposit_fee_usd=Decimal("0"),
            gross_value_usd=Decimal("12.00"),
        )
        from models.fee_models import FeeBreakdown
        assert isinstance(result, FeeBreakdown)
        # Gross value $12, trade fee = 12 * 10 / 10000 = $0.012
        assert result.mexc_trading_fee_usd == Decimal("0.012")
        assert result.dex_network_fee_usd > Decimal("0")
        assert result.mexc_deposit_fee_usd == Decimal("0")
        assert result.mexc_withdraw_fee_usd == Decimal("0.10")
        assert result.slippage_usd >= Decimal("0")
        assert result.total() > Decimal("0")

    @pytest.mark.asyncio
    async def test_direction_a_slippage_formula(self, fee_service):
        """Slippage = base_amount * slippage_bps / 100 / 100."""
        base = Decimal("10")
        # Default SLIPPAGE_BUFFER_BPS = 30 -> slippage = 10 * 30 / 100 / 100 = 0.03
        result = await fee_service.calculate_fees_direction_a(
            network="ETHEREUM",
            token_coin="TEST",
            base_amount_usd=base,
            mexc_price_usd=Decimal("1.00"),
            stablecoin_withdraw_fee_usd=Decimal("0"),
            mexc_deposit_fee_usd=Decimal("0"),
            gross_value_usd=Decimal("12.00"),
        )
        # With SLIPPAGE_BUFFER_BPS=30: slippage = 10 * 30 / 100 / 100 = 0.03
        assert result.slippage_usd == Decimal("0.03")

    @pytest.mark.asyncio
    async def test_direction_a_mexc_trade_fee(self, fee_service):
        """MEXC trading fee = gross_value * taker_fee_bps / 10000."""
        # gross=$50, fee=10bps -> 50 * 10 / 10000 = $0.05
        result = await fee_service.calculate_fees_direction_a(
            network="BSC",
            token_coin="TEST",
            base_amount_usd=Decimal("10"),
            mexc_price_usd=Decimal("1.00"),
            stablecoin_withdraw_fee_usd=Decimal("0.10"),
            mexc_deposit_fee_usd=Decimal("0"),
            gross_value_usd=Decimal("50.00"),
        )
        assert result.mexc_trading_fee_usd == Decimal("0.05")

    # ── Direction B ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_direction_b_returns_fee_breakdown(self, fee_service):
        """Direction B returns a valid FeeBreakdown."""
        result = await fee_service.calculate_fees_direction_b(
            network="ETHEREUM",
            base_amount_usd=Decimal("10"),
            mexc_withdraw_fee_usd=Decimal("0.10"),
            mexc_deposit_fee_usd=Decimal("0"),
            stable_deposit_network_fee_usd=Decimal("0"),
        )
        from models.fee_models import FeeBreakdown
        assert isinstance(result, FeeBreakdown)

    @pytest.mark.asyncio
    async def test_direction_b_mexc_buy_fee(self, fee_service):
        """Direction B buy fee = base_amount * taker_fee_bps / 10000."""
        result = await fee_service.calculate_fees_direction_b(
            network="BSC",
            base_amount_usd=Decimal("100"),
            mexc_withdraw_fee_usd=Decimal("0.10"),
            mexc_deposit_fee_usd=Decimal("0"),
            stable_deposit_network_fee_usd=Decimal("0"),
        )
        # 100 * 10 / 10000 = $0.10
        assert result.mexc_trading_fee_usd == Decimal("0.10")

    @pytest.mark.asyncio
    async def test_direction_b_preserves_withdraw_fee(self, fee_service):
        """Withdraw fee passed to direction B is preserved."""
        result = await fee_service.calculate_fees_direction_b(
            network="ARBITRUM",
            base_amount_usd=Decimal("10"),
            mexc_withdraw_fee_usd=Decimal("0.50"),
            mexc_deposit_fee_usd=Decimal("0"),
            stable_deposit_network_fee_usd=Decimal("0"),
        )
        assert result.mexc_withdraw_fee_usd == Decimal("0.50")

    # ── Gas estimation ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_estimate_gas_default_when_rpc_none(self, fee_service):
        """Gas estimate returns per-network fallback when RPC factory returns None."""
        fee_service._rpc_client_factory = MagicMock(return_value=None)
        result = await fee_service._estimate_gas_cost_usd("ETHEREUM")
        assert result == Decimal("5.0")  # ETHEREUM fallback

    @pytest.mark.asyncio
    async def test_estimate_gas_calculates_correctly(self, fee_service):
        """Gas estimate calculates correctly with mock RPC."""
        mock_rpc = MagicMock()
        mock_rpc.get_gas_price = AsyncMock(return_value=Decimal("10000000000"))  # 10 gwei
        fee_service._rpc_client_factory = MagicMock(return_value=mock_rpc)

        result = await fee_service._estimate_gas_cost_usd("ETHEREUM")
        # gas_cost_wei = 10 gwei * 150000 (default) = 1500000000000000 wei
        # gas_cost_native = 1500000000000000 / 10^18 = 0.0015 ETH
        # gas_cost_usd = 0.0015 * 3000 (ETH price) = $4.5
        assert result == Decimal("4.5")

    @pytest.mark.asyncio
    async def test_estimate_gas_default_on_exception(self, fee_service):
        """Gas estimate returns per-network fallback when RPC raises exception."""
        mock_rpc = MagicMock()
        mock_rpc.get_gas_price = AsyncMock(side_effect=Exception("RPC failed"))
        fee_service._rpc_client_factory = MagicMock(return_value=mock_rpc)

        result = await fee_service._estimate_gas_cost_usd("ETHEREUM")
        assert result == Decimal("5.0")  # ETHEREUM fallback

    @pytest.mark.asyncio
    async def test_estimate_gas_default_when_no_price(self, fee_service):
        """Gas estimate returns per-network fallback when native token price unavailable."""
        mock_rpc = MagicMock()
        mock_rpc.get_gas_price = AsyncMock(return_value=Decimal("10000000000"))
        fee_service._rpc_client_factory = MagicMock(return_value=mock_rpc)
        fee_service._price_service.get_price = MagicMock(return_value=None)  # No price

        result = await fee_service._estimate_gas_cost_usd("ETHEREUM")
        assert result == Decimal("5.0")  # ETHEREUM fallback

    # ── Native coin mapping ──────────────────────────────────

    def test_get_native_coin_ethereum(self, fee_service):
        """ETHEREUM -> ETH."""
        assert fee_service._get_native_coin("ETHEREUM") == "ETH"

    def test_get_native_coin_bsc(self, fee_service):
        """BSC -> BNB."""
        assert fee_service._get_native_coin("BSC") == "BNB"

    def test_get_native_coin_polygon(self, fee_service):
        """POLYGON -> POL."""
        assert fee_service._get_native_coin("POLYGON") == "POL"

    def test_get_native_coin_arbitrum(self, fee_service):
        """ARBITRUM -> ETH."""
        assert fee_service._get_native_coin("ARBITRUM") == "ETH"

    def test_get_native_coin_robinhood(self, fee_service):
        """ROBINHOOD -> ETH."""
        assert fee_service._get_native_coin("ROBINHOOD") == "ETH"

    def test_get_native_coin_unknown_default_eth(self, fee_service):
        """Unknown network defaults to ETH."""
        assert fee_service._get_native_coin("SOLANA") == "ETH"

    # ── FeeBreakdown totals ──────────────────────────────────

    def test_fee_breakdown_total(self):
        """FeeBreakdown.total() sums all components."""
        from models.fee_models import FeeBreakdown

        fb = FeeBreakdown(
            dex_network_fee_usd=Decimal("0.50"),
            dex_pool_fee_usd=Decimal("0.05"),
            mexc_deposit_fee_usd=Decimal("0"),
            mexc_withdraw_fee_usd=Decimal("0.10"),
            mexc_trading_fee_usd=Decimal("0.03"),
            token_transfer_tax_usd=Decimal("0"),
            slippage_usd=Decimal("0.02"),
        )
        assert fb.total() == Decimal("0.70")

    def test_fee_breakdown_total_zero(self):
        """FeeBreakdown.total() with all zeroes."""
        from models.fee_models import FeeBreakdown

        fb = FeeBreakdown()
        assert fb.total() == Decimal("0")

    # ── Custom taker fee ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_custom_taker_fee_bps(self, price_service):
        """Custom taker fee BPS overrides default."""
        from services.fee_service import FeeService

        svc = FeeService.__new__(FeeService)
        svc._rpc_client_factory = MagicMock()
        svc._price_service = price_service
        svc._mexc_taker_fee_bps = Decimal("5")  # 0.05%

        result = await svc.calculate_fees_direction_a(
            network="BSC",
            token_coin="TEST",
            base_amount_usd=Decimal("10"),
            mexc_price_usd=Decimal("1.00"),
            stablecoin_withdraw_fee_usd=Decimal("0"),
            mexc_deposit_fee_usd=Decimal("0"),
            gross_value_usd=Decimal("100.00"),
        )
        # 100 * 5 / 10000 = $0.05
        assert result.mexc_trading_fee_usd == Decimal("0.05")
