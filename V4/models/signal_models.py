"""
Arbitrage signal output model.

All monetary values use Decimal.
No float money fields.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from models.fee_models import FeeBreakdown


class ArbitrageSignal(BaseModel):
    """Complete arbitrage opportunity signal."""
    timestamp: datetime
    network: str
    token_coin: str
    token_address: str
    mexc_quote_asset: str
    mexc_symbol: str
    pool_stablecoin_coin: str
    pool_stablecoin_address: str
    pool_address: str
    dex: str
    pool_version: str
    direction: str
    base_amount_usd: Decimal
    mexc_price_usd: Decimal
    dex_amount_in: Decimal
    dex_amount_out: Decimal
    gross_profit_usd: Decimal
    gross_profit_pct: Decimal
    fees: FeeBreakdown
    net_profit_usd: Decimal
    net_profit_pct: Decimal
    full_cycle: bool = True
    warnings: list[str] = Field(default_factory=list)
    # Final MEXC order-book validation (optional; filled when depth check runs).
    orderbook_avg_price: Decimal | None = None
    orderbook_impact_pct: Decimal | None = None
    orderbook_fully_filled: bool | None = None
    # Size slider curve (built from book + optional CPMM reserves; no extra RPC).
    size_min_usd: Decimal | None = None
    size_max_usd: Decimal | None = None
    size_optimal_usd: Decimal | None = None
    size_curve: list[dict] = Field(default_factory=list)
