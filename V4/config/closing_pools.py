"""
Closing pools: wrap native quote → USDT.

Direction B often sells into WBNB/WETH. Profit was previously marked
to USD via MEXC spot. These pools let the scanner quote a real second
hop so net profit is measured in USDT received on-chain.

Addresses are lowercase. Liquidity figures are approximate (DexScreener).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ClosingPool:
    """Canonical pool used to settle a wrapped-native quote into USDT."""

    network: str
    dex_id: str
    pool_version: str
    pool_address: str
    token_in: str
    token_out: str
    token_in_decimals: int
    token_out_decimals: int
    settlement_coin: str = "USDT"


# network -> { wrapped_native_address: ClosingPool }
# Max route shape A-B-C-A where A is USDT (or USDC). Intermediate B/C = wrapped native.
CLOSING_POOLS: dict[str, dict[str, ClosingPool]] = {
    "BSC": {
        # PancakeSwap V2 WBNB/USDT — ~$33M liquidity.
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": ClosingPool(
            network="BSC",
            dex_id="pancakeswap_v2",
            pool_version="v2",
            pool_address="0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae",
            token_in="0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
            token_out="0x55d398326f99059ff775485246999027b3197955",
            token_in_decimals=18,
            token_out_decimals=18,
            settlement_coin="USDT",
        ),
        # PancakeSwap V3 BTCB/USDT
        "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c": ClosingPool(
            network="BSC",
            dex_id="pancakeswap_v3",
            pool_version="v3",
            pool_address="0x46cf1cf8c69595804ba91dfdd8d6b960c9b0a7c4",
            token_in="0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",
            token_out="0x55d398326f99059ff775485246999027b3197955",
            token_in_decimals=18,
            token_out_decimals=18,
            settlement_coin="USDT",
        ),
        # PancakeSwap V3 ETH/USDT (Binance-Peg ETH)
        "0x2170ed0880ac9a755fd29b2688956bd959f933f8": ClosingPool(
            network="BSC",
            dex_id="pancakeswap_v3",
            pool_version="v3",
            pool_address="0xbe141893e4c6ad9272e8c04bab7e6a10604501a5",
            token_in="0x2170ed0880ac9a755fd29b2688956bd959f933f8",
            token_out="0x55d398326f99059ff775485246999027b3197955",
            token_in_decimals=18,
            token_out_decimals=18,
            settlement_coin="USDT",
        ),
    },
    "ARBITRUM": {
        # Kept for inactive compatibility; not scanned.
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": ClosingPool(
            network="ARBITRUM",
            dex_id="uniswap_v3",
            pool_version="v3",
            pool_address="0x641c00a822e8b671738d32a431a4fb6074e5c79d",
            token_in="0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
            token_out="0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
            token_in_decimals=18,
            token_out_decimals=6,
            settlement_coin="USDT",
        ),
    },
    "BASE": {
        # Uniswap V3 WETH/USDC 0.05% — deepest ETH/stable on Base.
        "0x4200000000000000000000000000000000000006": ClosingPool(
            network="BASE",
            dex_id="uniswap_v3",
            pool_version="v3",
            pool_address="0xd0b53d9277642d899df5c87a3966a349a798f224",
            token_in="0x4200000000000000000000000000000000000006",
            token_out="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            token_in_decimals=18,
            token_out_decimals=6,
            settlement_coin="USDC",
        ),
    },
}


def get_closing_pool(network: str, quote_address: str) -> ClosingPool | None:
    """Return the USDT closing pool for a wrapped-native quote, if any."""
    if not network or not quote_address:
        return None
    return CLOSING_POOLS.get(network, {}).get(quote_address.lower())
