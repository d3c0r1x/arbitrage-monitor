"""
Arbitrage signal output model.

All monetary values use Decimal.
No float money fields.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

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
    warnings: list[str] = []
