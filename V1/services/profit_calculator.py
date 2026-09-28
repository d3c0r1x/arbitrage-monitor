"""
Profit calculator.

Calculates net profit for both arbitrage directions:
A: DEX_BUY_MEXC_SELL
B: MEXC_BUY_DEX_SELL

Net profit threshold: net_profit_pct > 1 (not >=).
"""

import logging
from decimal import Decimal

from config.settings import settings
from models.fee_models import FeeBreakdown

logger = logging.getLogger(__name__)


class ProfitCalculator:
    """Calculates arbitrage round-trip profit."""

    def __init__(self, fee_service):
        self._fee_service = fee_service
        self._base_amount_usd = settings.BASE_AMOUNT_USD
        self._min_net_profit_pct = settings.MIN_NET_PROFIT_PCT

    async def calculate_direction_a(
        self,
        network: str,
        token_coin: str,
        mexc_price_usd: Decimal,
        dex_amount_out: Decimal,
        token_decimals: int = 18,
        stablecoin_withdraw_fee_usd: Decimal = Decimal("0"),
        mexc_deposit_fee_usd: Decimal = Decimal("0"),
        pool_version: str = "",
        base_amount_usd: Decimal | None = None,
    ) -> dict:
        """Calculate profit for Direction A: DEX_BUY_MEXC_SELL.

        wallet stable (base_amount_usd)
        → DEX buy token (swap stable -> token)
        → transfer token to MEXC
        → sell token on MEXC
        → withdraw stable from MEXC

        Args:
            network: Internal network name.
            token_coin: Token coin symbol (e.g. "SCR").
            mexc_price_usd: Token price on MEXC in USD.
            dex_amount_out: Token amount received from DEX in smallest unit.
            token_decimals: Token decimals (default 18).
            stablecoin_withdraw_fee_usd: MEXC withdraw fee for the STABLECOIN in USD.
            mexc_deposit_fee_usd: MEXC deposit fee in USD.
            pool_version: Pool version for gas estimation (v2/v3).
            base_amount_usd: Base amount in USD (default from settings).

        Returns:
            dict with keys:
                - gross_profit_usd, gross_profit_pct
                - net_profit_usd, net_profit_pct
                - fees: FeeBreakdown
                - signal: bool (True if net_profit_pct > min)
        """
        if base_amount_usd is None:
            base_amount_usd = self._base_amount_usd
        # Convert DEX amount from smallest unit to token amount.
        token_amount = dex_amount_out / (Decimal(10) ** token_decimals)

        # Gross value: token amount * MEXC price
        gross_value_usd = token_amount * mexc_price_usd

        # Calculate fees.
        fees = await self._fee_service.calculate_fees_direction_a(
            network=network,
            token_coin=token_coin,
            base_amount_usd=base_amount_usd,
            mexc_price_usd=mexc_price_usd,
            stablecoin_withdraw_fee_usd=stablecoin_withdraw_fee_usd,
            mexc_deposit_fee_usd=mexc_deposit_fee_usd,
            gross_value_usd=gross_value_usd,
            pool_version=pool_version,
        )

        # Gross profit.
        gross_profit_usd = gross_value_usd - base_amount_usd
        gross_profit_pct = (
            (gross_profit_usd / base_amount_usd) * Decimal("100")
            if base_amount_usd > 0
            else Decimal("0")
        )

        # Net profit.
        net_profit_usd = gross_value_usd - base_amount_usd - fees.total()
        net_profit_pct = (
            (net_profit_usd / base_amount_usd) * Decimal("100")
            if base_amount_usd > 0
            else Decimal("0")
        )

        signal = net_profit_pct > self._min_net_profit_pct

        return {
            "direction": "DEX_BUY_MEXC_SELL",
            "base_amount_usd": base_amount_usd,
            "gross_profit_usd": gross_profit_usd,
            "gross_profit_pct": gross_profit_pct,
            "fees": fees,
            "net_profit_usd": net_profit_usd,
            "net_profit_pct": net_profit_pct,
            "signal": signal,
        }

    async def calculate_direction_b(
        self,
        network: str,
        mexc_price_usd: Decimal,
        dex_amount_out: Decimal,
        stablecoin_decimals: int = 18,
        mexc_withdraw_fee_usd: Decimal = Decimal("0"),
        mexc_deposit_fee_usd: Decimal = Decimal("0"),
        pool_version: str = "",
        base_amount_usd: Decimal | None = None,
        quote_price_usd: Decimal = Decimal("1"),
    ) -> dict:
        """Calculate profit for Direction B: MEXC_BUY_DEX_SELL.

        wallet stable (base_amount_usd)
        → deposit stable to MEXC
        → buy token on MEXC
        → withdraw token from MEXC
        → sell token on DEX
        → receive stable

        Note: dex_amount_out should already reflect the NET token amount
        (after subtracting MEXC withdraw fee in tokens). The scanner handles
        this subtraction before quoting. The withdraw fee is still shown in
        FeeBreakdown for transparency.

        Args:
            network: Internal network name.
            mexc_price_usd: Token price on MEXC in USD.
            dex_amount_out: Stablecoin amount received from DEX in smallest unit.
            stablecoin_decimals: Quote asset decimals (default 18).
            mexc_withdraw_fee_usd: MEXC token withdraw fee in USD (for display).
            mexc_deposit_fee_usd: MEXC stable deposit fee in USD.
            pool_version: Pool version for gas estimation (v2/v3).
            base_amount_usd: Base amount in USD (default from settings).
            quote_price_usd: USD price of the pool's quote asset
                (1 for stablecoins; e.g. ETH price for WETH-quoted pools).

        Returns:
            dict with profit breakdown and signal.
        """
        if base_amount_usd is None:
            base_amount_usd = self._base_amount_usd
        if mexc_price_usd <= 0:
            return {
                "direction": "MEXC_BUY_DEX_SELL",
                "base_amount_usd": base_amount_usd,
                "gross_profit_usd": Decimal("0"),
                "gross_profit_pct": Decimal("0"),
                "fees": FeeBreakdown(),
                "net_profit_usd": Decimal("0"),
                "net_profit_pct": Decimal("0"),
                "signal": False,
                "skip_reason": "mexc_price_zero",
            }

        # Convert DEX amount from smallest unit.
        stable_out = dex_amount_out / (Decimal(10) ** stablecoin_decimals)

        # Value the quote-asset output in USD (stablecoins: price = 1).
        stable_out_usd = stable_out * quote_price_usd

        # Fees (direction B).
        fees = await self._fee_service.calculate_fees_direction_b(
            network=network,
            base_amount_usd=base_amount_usd,
            mexc_withdraw_fee_usd=mexc_withdraw_fee_usd,
            mexc_deposit_fee_usd=mexc_deposit_fee_usd,
            stable_deposit_network_fee_usd=Decimal("0.5"),
            pool_version=pool_version,
        )

        # Gross profit.
        gross_profit_usd = stable_out_usd - base_amount_usd
        gross_profit_pct = (
            (gross_profit_usd / base_amount_usd) * Decimal("100")
            if base_amount_usd > 0
            else Decimal("0")
        )

        # Net profit.
        net_profit_usd = stable_out_usd - base_amount_usd - fees.total()
        net_profit_pct = (
            (net_profit_usd / base_amount_usd) * Decimal("100")
            if base_amount_usd > 0
            else Decimal("0")
        )

        signal = net_profit_pct > self._min_net_profit_pct

        return {
            "direction": "MEXC_BUY_DEX_SELL",
            "base_amount_usd": base_amount_usd,
            "gross_profit_usd": gross_profit_usd,
            "gross_profit_pct": gross_profit_pct,
            "fees": fees,
            "net_profit_usd": net_profit_usd,
            "net_profit_pct": net_profit_pct,
            "signal": signal,
        }
