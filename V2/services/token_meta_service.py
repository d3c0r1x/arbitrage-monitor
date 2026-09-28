"""Token metadata service.

Fetches and caches ERC20 token decimals via on-chain eth_call.
Used by scanner and pool_refresh to provide dynamic decimals.

Fail-closed policy (plan v3 D1/D2/D16): when decimals cannot be
determined (no RPC, call failed, garbage response) the service returns
None and caches NOTHING. Callers must skip such tokens instead of
assuming 18 — a wrong default silently corrupts every quote by 10^12.
"""

import logging

logger = logging.getLogger(__name__)

DECIMALS_CACHE: dict[tuple[str, str], int] = {}

# Hard override table for well-known assets. These values are fixed at
# contract deploy time and can never change; resolving them here costs
# zero RPC and protects against poisoned caches. Keys are lowercase
# contract addresses (unique across the networks we touch).
KNOWN_DECIMALS: dict[str, int] = {
    # -- Ethereum --
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6,   # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,   # USDT
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": 8,   # WBTC
    "0x68749665ff8d2d112fa859aa293f07a622782f38": 6,   # XAUT
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": 18,  # WETH
    # -- Arbitrum --
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831": 6,   # USDC (native)
    "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8": 6,   # USDC.e
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": 6,   # USDT
    "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f": 8,   # WBTC
    "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": 18,  # WETH
    # -- Base --
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 6,   # USDC
    "0xfde4c96c8593536e31f229ea8f37b2ada2699bb2": 6,   # USDT
    "0x4200000000000000000000000000000000000006": 18,  # WETH
    "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": 8,   # cbBTC
    # -- BSC --
    "0x55d398326f99059ff775485246999027b3197955": 18,  # USDT (BSC)
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": 18,  # USDC (BSC)
    "0xe9e7cea3dedca5984780bafc599bd69add087d56": 18,  # BUSD
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": 18,  # WBNB
    "0x2170ed0880ac9a755fd29b2688956bd959f933f8": 18,  # ETH (BSC)
    "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c": 18,  # BTCB
}

# ERC20 decimals fit in uint8; anything larger is a garbage response.
_MAX_SANE_DECIMALS = 36


def _parse_decimals_hex(ret_hex: str) -> int | None:
    """Parse an eth_call decimals() response, rejecting garbage."""
    if not ret_hex or ret_hex == "0x":
        return None
    try:
        val = int(ret_hex, 16)
    except (ValueError, TypeError):
        return None
    if 0 <= val <= _MAX_SANE_DECIMALS:
        return val
    return None


class TokenMetaService:
    """Service for fetching and caching ERC20 token metadata.

    Resolution priority:
    1. KNOWN_DECIMALS override table (no RPC)
    2. Runtime cache
    3. On-chain eth_call / Multicall3 batch (cached forever on success)
    """

    def __init__(self, rpc_client_factory, multicall_client=None):
        self._rpc_client_factory = rpc_client_factory
        self._multicall_client = multicall_client

    async def get_decimals(self, network: str, token_address: str) -> int | None:
        """Get token decimals, with cache.

        Returns:
            int — token decimals (e.g. 6 for USDC on Ethereum), or
            None if decimals cannot be determined (fail-closed: the
            caller must skip the token, never assume 18).
        """
        addr_lower = token_address.lower()

        known = KNOWN_DECIMALS.get(addr_lower)
        if known is not None:
            return known

        key = (network, addr_lower)
        cached = DECIMALS_CACHE.get(key)
        if cached is not None:
            return cached

        rpc = self._rpc_client_factory(network)
        if rpc is None:
            logger.warning("token_decimals_no_rpc: %s %s", network, token_address)
            return None

        try:
            # ERC20 decimals() function selector: 0x313ce567
            result_hex = await rpc.eth_call(
                to=token_address,
                data="0x313ce567",
            )
            val = _parse_decimals_hex(result_hex)
            if val is None:
                logger.warning(
                    "token_decimals_garbage_response: %s %s %r",
                    network, token_address, result_hex,
                )
                return None
            DECIMALS_CACHE[key] = val
            logger.debug("token_decimals: %s %s = %d", network, token_address, val)
            return val
        except Exception as exc:
            logger.warning(
                "token_decimals_fetch_failed: %s %s %s",
                network, token_address, exc,
            )
            # Fail-closed: do NOT cache a fake 18 (D16 poison-cache fix).
            return None

    def clear_cache(self) -> None:
        """Clear the decimals cache."""
        DECIMALS_CACHE.clear()

    async def get_decimals_batch(
        self, network: str, addresses: list[str]
    ) -> dict[str, int]:
        """Fetch decimals for multiple tokens in one Multicall3 batch.

        Args:
            network: Internal network name.
            addresses: List of token contract addresses.

        Returns:
            Dict mapping address (lowercase) → decimals. Addresses whose
            decimals could not be determined are OMITTED (fail-closed) —
            callers must treat missing entries as "skip this token".
        """
        if not addresses:
            return {}

        # Resolve known/cached addresses without RPC.
        uncached = []
        result: dict[str, int] = {}
        for addr in addresses:
            addr_lower = addr.lower()
            known = KNOWN_DECIMALS.get(addr_lower)
            if known is not None:
                result[addr_lower] = known
                continue
            cached = DECIMALS_CACHE.get((network, addr_lower))
            if cached is not None:
                result[addr_lower] = cached
            else:
                uncached.append(addr)

        if not uncached:
            return result

        # Use multicall if available.
        if self._multicall_client is not None:
            # ERC20 decimals() selector: 0x313ce567
            calls = [(addr, "0x313ce567") for addr in uncached]
            try:
                batch_results = await self._multicall_client.try_aggregate(
                    network=network, calls=calls
                )
                failed = 0
                for addr, (success, ret_hex) in zip(
                    uncached, batch_results, strict=False
                ):
                    addr_lower = addr.lower()
                    val = _parse_decimals_hex(ret_hex) if success else None
                    if val is not None:
                        DECIMALS_CACHE[(network, addr_lower)] = val
                        result[addr_lower] = val
                    else:
                        # Fail-closed: omit from result, cache nothing.
                        failed += 1
                if failed:
                    logger.warning(
                        "decimals_batch_unresolved: network=%s failed=%d of %d",
                        network, failed, len(uncached),
                    )
                return result
            except Exception as exc:
                logger.warning("decimals_batch_multicall_failed: %s, falling back", exc)

        # Fallback: sequential individual calls.
        for addr in uncached:
            val = await self.get_decimals(network, addr)
            if val is not None:
                result[addr.lower()] = val

        return result
