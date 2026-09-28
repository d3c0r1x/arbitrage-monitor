"""
Strict whitelist of stablecoins.

Coins not in this whitelist are not considered stablecoins,
even if their price is near 1 USD.
"""

STABLECOIN_WHITELIST: frozenset[str] = frozenset(
    {
        "USDT",
        "USDC",
        "DAI",
        "FDUSD",
        "USDe",
        "PYUSD",
        "TUSD",
        "GUSD",
        "FRAX",
        "GHO",
        "LUSD",
        "crvUSD",
        "DOLA",
        "USDP",
        "MIM",
        "sUSD",
    }
)

# MEXC quote assets used for token pricing.
# Signals for USDT and USDC are calculated separately.
MEXC_QUOTE_ASSETS: frozenset[str] = frozenset(
    {
        "USDT",
        "USDC",
    }
)
