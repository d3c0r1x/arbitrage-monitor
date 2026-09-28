"""Canonical token address registry (plan v3 D4).

A pool can carry a well-known ticker (BAT, LINK, USDT...) while pointing
at an impostor contract. For symbols listed here the pool's token address
MUST match the canonical one; unknown symbols pass through (new listings
are handled by bytecode security checks instead).

Addresses are stored lowercase. Verified against block explorers.
"""

import logging

logger = logging.getLogger(__name__)

VERIFIED_TOKENS: dict[str, dict[str, str]] = {
    "BSC": {
        "CAKE": "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",
        "USDT": "0x55d398326f99059ff775485246999027b3197955",
        "USDC": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
        "BUSD": "0xe9e7cea3dedca5984780bafc599bd69add087d56",
        "WBNB": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
        "ETH": "0x2170ed0880ac9a755fd29b2688956bd959f933f8",
        "BTCB": "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",
        "LINK": "0xf8a0bf9cf54bb92f17374d9e9a321e6a111a51bd",
        "DOGE": "0xba2ae424d960c26247dd6c32edc70b295c744c43",
        "SHIB": "0x2859e4544c4bb03966803b044a93563bd2d0dd4d",
    },
    "ARBITRUM": {
        "WETH": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
        "USDC": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
        "USDT": "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
        "ARB": "0x912ce59144191c1204e64559fe8253a0e49e6548",
        "GMX": "0xfc5a1a6eb076a2c7ad06ed22c90d7e710e35ad0a",
        "LINK": "0xf97f4df75117a78c1a5a0dbb814af92458539fb4",
        "WBTC": "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f",
    },
    "BASE": {
        "WETH": "0x4200000000000000000000000000000000000006",
        "USDC": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        "USDT": "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2",
        "cbBTC": "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",
    },
    "ETHEREUM": {
        # NOTE (plan v3): 0x0d8775...2887ef IS the canonical ETH BAT.
        "BAT": "0x0d8775f648430679a709e98d2b0cb6250d2887ef",
        "USDC": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "USDT": "0xdac17f958d2ee523a2206206994597c13d831ec7",
        "WETH": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        "WBTC": "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",
        "LINK": "0x514910771af9ca656af840dff83e8264ecf986ca",
        "XAUT": "0x68749665ff8d2d112fa859aa293f07a622782f38",
        "SHIB": "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce",
    },
}


def verify_token(network: str, symbol: str, address: str) -> tuple[bool, str]:
    """Check the token address against the canonical registry.

    Returns:
        (True, "verified") — symbol known, address matches.
        (True, "not_in_whitelist") — symbol unknown, pass through.
        (False, "FAKE:...") — symbol known, address DIFFERS: impostor.
    """
    if not symbol or not address:
        return True, "not_in_whitelist"

    expected = VERIFIED_TOKENS.get(network.upper(), {}).get(symbol.upper())
    if expected is None:
        return True, "not_in_whitelist"

    if address.lower() == expected:
        return True, "verified"

    return False, f"FAKE:expected={expected},got={address.lower()}"
