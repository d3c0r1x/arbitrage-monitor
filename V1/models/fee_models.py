"""
Fee breakdown and aggregation models.

All monetary values use Decimal.
"""

from decimal import Decimal

from pydantic import BaseModel


class FeeBreakdown(BaseModel):
    """Breakdown of all fees incurred in a round-trip arbitrage."""
    dex_network_fee_usd: Decimal = Decimal("0")
    dex_pool_fee_usd: Decimal = Decimal("0")
    mexc_deposit_fee_usd: Decimal = Decimal("0")
    mexc_withdraw_fee_usd: Decimal = Decimal("0")
    mexc_trading_fee_usd: Decimal = Decimal("0")
    token_transfer_tax_usd: Decimal = Decimal("0")
    slippage_usd: Decimal = Decimal("0")
    stable_deposit_network_fee_usd: Decimal = Decimal("0")

    def total(self) -> Decimal:
        """Sum of all fees."""
        return (
            self.dex_network_fee_usd
            + self.dex_pool_fee_usd
            + self.mexc_deposit_fee_usd
            + self.mexc_withdraw_fee_usd
            + self.mexc_trading_fee_usd
            + self.token_transfer_tax_usd
            + self.slippage_usd
            + self.stable_deposit_network_fee_usd
        )
