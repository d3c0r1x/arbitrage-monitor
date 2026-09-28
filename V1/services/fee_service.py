"""
Fee service.

Calculates all fees for a round-trip arbitrage:
- DEX network fee (gas)
- MEXC deposit/withdraw fees
- MEXC trading fee
- Token transfer tax
- Slippage cost

No double counting of fees.
"""

import logging
from decimal import Decimal

from config.settings import settings
from models.fee_models import FeeBreakdown

logger = logging.getLogger(__name__)

# Per-network gas cost fallback (USD) when RPC is unavailable.
_GAS_FALLBACK_USD: dict[str, Decimal] = {
    "ETHEREUM": Decimal("5.0"),
    "BSC": Decimal("0.3"),
    "POLYGON": Decimal("0.01"),
    "ARBITRUM": Decimal("0.1"),
    "ROBINHOOD": Decimal("0.01"),
}

# Gas units per swap by pool version.
_GAS_UNITS_BY_VERSION: dict[str, int] = {
    "v2": 120_000,
    "v3": 180_000,
    "v4": 250_000,
}
_DEFAULT_GAS_UNITS = 150_000


class FeeService:
    """Service for calculating arbitrage round-trip fees."""

    def __init__(
        self,
        rpc_client_factory,
        price_service,
        mexc_taker_fee_bps: int | None = None,
    ):
        self._rpc_client_factory = rpc_client_factory
        self._price_service = price_service
        self._mexc_taker_fee_bps = Decimal(
            mexc_taker_fee_bps or settings.MEXC_TAKER_FEE_BPS
        )

    async def calculate_fees_direction_a(
        self,
        network: str,
        token_coin: str,
        base_amount_usd: Decimal,
        mexc_price_usd: Decimal,
        stablecoin_withdraw_fee_usd: Decimal,
        mexc_deposit_fee_usd: Decimal,
        gross_value_usd: Decimal,
        pool_version: str = "",
    ) -> FeeBreakdown:
        """Calculate fees for Direction A: DEX_BUY_MEXC_SELL.

        wallet stable
        → DEX buy token
        → transfer token to MEXC
        → sell token on MEXC
        → withdraw stable from MEXC

        Args:
            stablecoin_withdraw_fee_usd: MEXC withdraw fee for the STABLECOIN
                (the asset withdrawn at the end of direction A).
        """
        # 1. MEXC sell fee (trading fee on MEXC)
        mexc_sell_fee_usd = gross_value_usd * self._mexc_taker_fee_bps / Decimal(10000)

        # 2. DEX network fee (gas)
        dex_network_fee_usd = await self._estimate_gas_cost_usd(network, pool_version)

        # 3. Token transfer tax (assume 0 unless detected)
        token_transfer_tax_usd = Decimal("0")

        # 4. MEXC stablecoin withdraw fee (withdraw stable at end of direction A)

        # 5. Slippage
        slippage = Decimal(str(settings.SLIPPAGE_BUFFER_BPS)) / Decimal(100)
        slippage_usd = base_amount_usd * slippage / Decimal(100)

        return FeeBreakdown(
            dex_network_fee_usd=dex_network_fee_usd,
            mexc_trading_fee_usd=mexc_sell_fee_usd,
            mexc_deposit_fee_usd=mexc_deposit_fee_usd,
            mexc_withdraw_fee_usd=stablecoin_withdraw_fee_usd,
            token_transfer_tax_usd=token_transfer_tax_usd,
            slippage_usd=slippage_usd,
        )

    async def calculate_fees_direction_b(
        self,
        network: str,
        base_amount_usd: Decimal,
        mexc_withdraw_fee_usd: Decimal,
        mexc_deposit_fee_usd: Decimal,
        stable_deposit_network_fee_usd: Decimal,
        pool_version: str = "",
    ) -> FeeBreakdown:
        """Calculate fees for Direction B: MEXC_BUY_DEX_SELL.

        wallet stable
        → deposit stable to MEXC
        → buy token on MEXC
        → withdraw token from MEXC
        → sell token on DEX

        Args:
            mexc_withdraw_fee_usd: MEXC withdraw fee for the TOKEN
                (the asset withdrawn from MEXC in direction B).
            stable_deposit_network_fee_usd: On-chain gas cost for depositing
                stablecoin to MEXC (network transfer fee).
        """
        # 1. MEXC buy fee
        mexc_buy_fee_usd = base_amount_usd * self._mexc_taker_fee_bps / Decimal(10000)

        # 2. DEX network fee for selling token
        dex_network_fee_usd = await self._estimate_gas_cost_usd(network, pool_version)

        # 3. Slippage
        slippage = Decimal(str(settings.SLIPPAGE_BUFFER_BPS)) / Decimal(100)
        slippage_usd = base_amount_usd * slippage / Decimal(100)

        return FeeBreakdown(
            dex_network_fee_usd=dex_network_fee_usd,
            mexc_trading_fee_usd=mexc_buy_fee_usd,
            mexc_deposit_fee_usd=mexc_deposit_fee_usd,
            mexc_withdraw_fee_usd=mexc_withdraw_fee_usd,
            slippage_usd=slippage_usd,
            stable_deposit_network_fee_usd=stable_deposit_network_fee_usd,
        )

    async def _estimate_gas_cost_usd(self, network: str, pool_version: str = "") -> Decimal:
        """Estimate DEX transaction gas cost in USD.

        Uses per-network fallback defaults on failure (never silent).
        Gas units vary by pool version (v2 < v3 < v4).
        """
        fallback = _GAS_FALLBACK_USD.get(network, Decimal("1.0"))
        try:
            rpc = self._rpc_client_factory(network)
            if rpc is None:
                logger.warning(
                    "gas_estimate_fallback: no_rpc network=%s fallback=$%s",
                    network, fallback,
                )
                return fallback

            gas_price_wei = await rpc.get_gas_price()
            gas_units = Decimal(
                _GAS_UNITS_BY_VERSION.get(pool_version, _DEFAULT_GAS_UNITS)
            )
            gas_cost_wei = gas_price_wei * gas_units

            # Convert to native token (ETH/BNB/POL).
            gas_cost_native = gas_cost_wei / Decimal(10**18)

            # Get native token price in USD.
            native_coin = self._get_native_coin(network)
            native_price = self._price_service.get_price(native_coin, "USDT")

            if native_price is None:
                logger.warning(
                    "gas_estimate_fallback: no_native_price network=%s coin=%s fallback=$%s",
                    network, native_coin, fallback,
                )
                return fallback

            return gas_cost_native * native_price

        except Exception as exc:
            logger.warning(
                "gas_estimate_fallback: error network=%s err=%s fallback=$%s",
                network, exc, fallback,
            )
            return fallback

    @staticmethod
    def _get_native_coin(network: str) -> str:
        mapping = {
            "ETHEREUM": "ETH",
            "BSC": "BNB",
            "POLYGON": "POL",
            "ARBITRUM": "ETH",
            "ROBINHOOD": "ETH",
        }
        return mapping.get(network, "ETH")
