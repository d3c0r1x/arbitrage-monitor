"""Token metadata service.

Fetches and caches ERC20 token decimals via on-chain eth_call.
Used by scanner and pool_refresh to provide dynamic decimals
instead of hardcoded 18.
"""

import logging

logger = logging.getLogger(__name__)

DECIMALS_CACHE: dict[tuple[str, str], int] = {}


class TokenMetaService:
    """Service for fetching and caching ERC20 token metadata.

    Caches decimals per (network, token_address) to avoid repeated
    RPC calls for the same token. Supports batch fetching via Multicall3.
    """

    def __init__(self, rpc_client_factory, multicall_client=None):
        self._rpc_client_factory = rpc_client_factory
        self._multicall_client = multicall_client

    async def get_decimals(self, network: str, token_address: str) -> int:
        """Get token decimals, with cache.

        Returns:
            int — token decimals (e.g. 6 for USDC on Ethereum).
            Returns 18 as safe default if RPC unavailable.
        """
        key = (network, token_address.lower())
        cached = DECIMALS_CACHE.get(key)
        if cached is not None:
            return cached

        rpc = self._rpc_client_factory(network)
        if rpc is None:
            logger.warning("token_decimals_no_rpc: %s %s", network, token_address)
            return 18

        try:
            # ERC20 decimals() function selector: 0x313ce567
            result_hex = await rpc.eth_call(
                to=token_address,
                data="0x313ce567",
            )
            # eth_call returns hex-encoded uint8 padded to 32 bytes
            # Python int() handles the 0x prefix natively
            val = int(result_hex, 16)
            DECIMALS_CACHE[key] = val
            logger.debug("token_decimals: %s %s = %d", network, token_address, val)
            return val
        except Exception as exc:
            logger.warning(
                "token_decimals_fetch_failed: %s %s %s",
                network, token_address, exc,
            )
            DECIMALS_CACHE[key] = 18
            return 18

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
            Dict mapping address (lowercase) → decimals.
            Missing/failed addresses default to 18.
        """
        if not addresses:
            return {}

        # Filter out already-cached addresses.
        uncached = []
        result: dict[str, int] = {}
        for addr in addresses:
            key = (network, addr.lower())
            cached = DECIMALS_CACHE.get(key)
            if cached is not None:
                result[addr.lower()] = cached
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
                for addr, (success, ret_hex) in zip(uncached, batch_results):
                    key = (network, addr.lower())
                    if success and ret_hex and ret_hex != "0x":
                        try:
                            val = int(ret_hex, 16)
                            DECIMALS_CACHE[key] = val
                            result[addr.lower()] = val
                        except (ValueError, TypeError):
                            DECIMALS_CACHE[key] = 18
                            result[addr.lower()] = 18
                    else:
                        DECIMALS_CACHE[key] = 18
                        result[addr.lower()] = 18
                return result
            except Exception as exc:
                logger.warning("decimals_batch_multicall_failed: %s, falling back", exc)

        # Fallback: sequential individual calls.
        for addr in uncached:
            val = await self.get_decimals(network, addr)
            result[addr.lower()] = val

        return result
