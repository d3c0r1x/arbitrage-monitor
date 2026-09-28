"""
Adapter factory.

Creates and caches DEX adapters based on the DEX registry.
Handles TODO_VERIFY addresses by returning None.
"""

import logging

from config.dex_registry import DEX_REGISTRY
from dex.v2_adapter import V2Adapter
from dex.v3_adapter import V3Adapter

logger = logging.getLogger(__name__)


# Mapping from pool-discovered DEX names to registry DEX IDs.
# Discovery sources (DexScreener, etc.) return short names like
# "pancakeswap", "uniswap" — but the registry uses versioned IDs
# like "pancakeswap_v2", "pancakeswap_v3".
_DEX_ALIASES: dict[str, list[str]] = {
    # Pool-discovered DEX names → registry DEX IDs to try.
    # If dex_id is already a registry ID, the fallback [dex_id] in
    # get_adapter handles it — only aliases that DIFFER from the
    # registry ID need explicit entries here.
    "pancakeswap": ["pancakeswap_v2", "pancakeswap_v3"],
    "uniswap": ["uniswap_v2", "uniswap_v3"],
    "sushiswap": ["sushiswap_v2"],
    "quickswap": ["quickswap_v3"],
    "camelot": ["camelot_v2"],
}


class AdapterFactory:
    """Factory for creating and caching DEX adapters."""

    def __init__(self, web3_manager=None, web3_factory=None):
        """Initialize adapter factory.

        Args:
            web3_manager: Web3Manager instance (preferred). If provided,
                creates a factory callable wrapping web3_manager.get_web3.
            web3_factory: Legacy callable (network: str) -> AsyncWeb3 | None.
                Only used if web3_manager is None.
        """
        if web3_manager is not None:
            # Create a factory callable from Web3Manager.
            self._web3_factory = web3_manager.get_web3
        else:
            self._web3_factory = web3_factory
        self._adapters: dict = {}

    def get_adapter(self, network: str, dex_id: str, pool_version: str | None = None):
        """Get or create a DEX adapter.

        Resolves DEX aliases so pool-discovered names like "pancakeswap"
        are mapped to registry IDs like "pancakeswap_v2".

        When pool_version is known ("v2" / "v3"), only candidates
        matching that version are tried, avoiding wrong-adapter selection.

        Args:
            network: Internal network name.
            dex_id: DEX identifier (from pool discovery).
            pool_version: Optional "v2", "v3", or None for auto-detect.

        Returns:
            BaseDexAdapter instance or None if DEX is not available.
        """
        # Normalize empty string to None for consistent cache keys.
        pool_version = pool_version or None
        key = (network, dex_id, pool_version)

        if key in self._adapters:
            return self._adapters[key]

        network_config = DEX_REGISTRY.get(network)
        if not network_config:
            logger.warning("network_not_found_in_registry: %s", network)
            return None

        # Determine which registry IDs to try.
        candidates = _DEX_ALIASES.get(dex_id, [dex_id])

        # If pool_version is known, filter to matching version only.
        if pool_version:
            candidates = [
                c for c in candidates
                if c.endswith(f"_{pool_version}")
            ]

        for rid in candidates:
            adapter = self._create_adapter(network_config, network, rid)
            if adapter is not None:
                # Cache by the ORIGINAL key (network, dex_id, pool_version)
                # so future lookups with the same params are fast.
                self._adapters[key] = adapter
                return adapter

        return None

    def _create_adapter(self, network_config: dict, network: str, registry_id: str):
        """Try to create an adapter for a specific registry ID.

        Returns:
            BaseDexAdapter or None.
        """
        for dex_config in network_config.get("dexes", []):
            if dex_config["dex_id"] != registry_id:
                continue

            version = dex_config["version"]

            # Skip DEX with TODO_VERIFY addresses.
            if self._has_todo_addresses(dex_config):
                logger.warning(
                    "dex_has_todo_addresses_skipping: %s on %s",
                    registry_id,
                    network,
                )
                return None

            if version == "v2":
                return V2Adapter(
                    web3_factory=self._web3_factory,
                    router_address=dex_config["router"],
                    default_fee_bps=dex_config.get("default_fee_bps", 30),
                )
            elif version == "v3":
                return V3Adapter(
                    web3_factory=self._web3_factory,
                    quoter_v2_address=dex_config["quoter_v2"],
                )
            elif version == "v4":
                logger.warning(
                    "v4_adapter_not_implemented: %s on %s",
                    registry_id,
                    network,
                )
                return None
            else:
                logger.warning("unknown_dex_version: %s for %s", version, registry_id)
                return None

        return None

    @staticmethod
    def _has_todo_addresses(dex_config: dict) -> bool:
        """Check if any address in the DEX config starts with TODO_."""
        for _key, value in dex_config.items():
            if isinstance(value, str) and value.startswith("TODO_"):
                return True
        return False
