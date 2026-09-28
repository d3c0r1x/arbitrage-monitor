"""
Base class for pool discovery sources.

Each source must:
- Search only by token contract address.
- Return pool address, network, token0/token1 addresses, dex id.
- NOT return price, liquidity, volume.
"""

from abc import ABC, abstractmethod

from models.pool_models import DiscoveredPool


class BaseSource(ABC):
    """Abstract base class for pool discovery sources."""

    source_name: str = "base"

    @abstractmethod
    async def fetch_pools_by_token(
        self,
        network: str,
        token_address: str,
    ) -> list[DiscoveredPool]:
        """Fetch pools containing the given token address.

        Args:
            network: Internal network name (e.g. "ETHEREUM", "BSC").
            token_address: Lowercased EVM token contract address.

        Returns:
            List of DiscoveredPool objects. Empty list if no pools found.

        Raises:
            SourceError: If the source is unavailable or returns errors.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this source is available (has API keys, etc.)."""
        ...


class SourceError(Exception):
    """Raised when a discovery source fails."""
    pass
