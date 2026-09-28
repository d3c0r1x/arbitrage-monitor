"""
Wrapped-native and common base tokens per network.

These are high-liquidity DEX quote assets (WETH, WBNB, WBTC, WMATIC, ...)
that are NOT returned by MEXC capital config as ERC-20 contracts, because
MEXC lists their underlying native/spot coin (ETH, BNB, BTC, POL) without an
on-chain contract for the native gas token.

To count ALL arbitrage paths, the bot must treat pools quoted in these
wrapped tokens as valid. Each wrapped token is mapped to the MEXC spot coin
used to price it in USD (via <COIN>USDT / <COIN>USDC on MEXC).

Addresses are lowercased canonical mainnet addresses.
"""

# network -> { wrapped_token_address(lower): mexc_price_coin }
WRAPPED_NATIVE_QUOTES: dict[str, dict[str, str]] = {
    "ETHEREUM": {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "ETH",   # WETH
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "BTC",   # WBTC
    },
    "BSC": {
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "BNB",   # WBNB
        "0x2170ed0880ac9a755fd29b2688956bd959f933f8": "ETH",   # ETH (Binance-Peg)
        "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c": "BTC",   # BTCB
    },
    "POLYGON": {
        "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270": "POL",   # WMATIC / WPOL
        "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": "ETH",   # WETH
        "0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6": "BTC",   # WBTC
    },
    "ARBITRUM": {
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": "ETH",   # WETH
        "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": "BTC",   # WBTC
    },
    "BASE": {
        "0x4200000000000000000000000000000000000006": "ETH",   # WETH
        "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": "BTC",   # cbBTC
    },
    "ROBINHOOD": {},
}

# Fallback MEXC pricing symbols per coin, tried in order until a price exists.
# Handles the POL/MATIC rebrand and generic USDT/USDC quote pairs.
COIN_PRICE_ALIASES: dict[str, tuple[str, ...]] = {
    "POL": ("POL", "MATIC"),
}


def wrapped_natives_for_network(network: str) -> dict[str, str]:
    """Return {address: price_coin} of wrapped-native quotes for a network."""
    return WRAPPED_NATIVE_QUOTES.get(network, {})


def price_coin_aliases(coin: str) -> tuple[str, ...]:
    """Return the MEXC base-symbol aliases used to price a coin in USD."""
    return COIN_PRICE_ALIASES.get(coin.upper(), (coin.upper(),))
