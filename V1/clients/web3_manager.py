"""Web3 instance manager with caching.

Caches one AsyncWeb3 instance per network to avoid redundant
Web3 initialization on every adapter call.
"""

import logging

from web3 import AsyncWeb3

from config.networks import resolve_rpc_url

logger = logging.getLogger(__name__)


class Web3Manager:
    """Manages and caches AsyncWeb3 instances per network."""

    def __init__(self):
        self._cache: dict[str, AsyncWeb3] = {}

    def get_web3(self, network: str) -> AsyncWeb3 | None:
        """Get or create an AsyncWeb3 instance for the given network.

        Args:
            network: Internal network name.

        Returns:
            AsyncWeb3 instance or None if no RPC URL configured.
        """
        cached = self._cache.get(network)
        if cached is not None:
            return cached

        rpc_url = resolve_rpc_url(network)
        if not rpc_url:
            logger.warning("no_rpc_url_for_network: %s", network)
            return None

        w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))
        self._cache[network] = w3
        return w3

    def clear(self) -> None:
        """Clear the Web3 cache (e.g. on RPC URL rotation)."""
        self._cache.clear()
