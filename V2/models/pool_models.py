"""
Pool discovery and validated pool models.
"""

from pydantic import BaseModel


class DiscoveredPool(BaseModel):
    """Raw pool from a discovery source (no liquidity/volume fields used)."""
    network: str
    pool_address: str
    dex: str | None
    token0_address: str
    token1_address: str
    sources: set[str]

    class Config:
        arbitrary_types_allowed = True


class ValidPool(BaseModel):
    """Validated pool matched against a MEXC token and a priceable quote.

    Historically the quote side was restricted to stablecoins; it is now any
    priceable MEXC asset (stablecoin, wrapped-native, or MEXC-listed coin).
    The ``stablecoin_address`` field is kept for backward compatibility and
    holds the matched quote token's address.
    """
    network: str
    token_address: str
    stablecoin_address: str
    pool_address: str
    dex: str | None
    sources: set[str]
    pool_version: str | None = None
    # Broadened quote metadata.
    quote_coin: str | None = None
    quote_is_stable: bool = True
    quote_price_coin: str | None = None
    quote_withdraw_fee: str | None = None

    class Config:
        arbitrary_types_allowed = True
