"""
DEX quote and simulation result models.
"""

from decimal import Decimal

from pydantic import BaseModel


class DexQuoteResult(BaseModel):
    """Result of an on-chain DEX swap simulation."""
    token_in: str
    token_out: str
    amount_in: Decimal
    amount_out: Decimal
    amount_out_after_slippage: Decimal | None = None
    pool_fee_bps: Decimal | None = None
    gas_estimate: int | None = None
    success: bool = True
    error: str | None = None
