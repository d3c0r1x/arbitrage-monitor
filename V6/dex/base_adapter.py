"""
Base adapter for DEX swap simulation.

All adapters must:
- Use on-chain simulation (eth_call) for pricing.
- Never return off-chain prices.
- Return Decimal amounts.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from decimal import Decimal
from typing import TypeVar

T = TypeVar("T")


class BaseDexAdapter(ABC):
    """Abstract base class for DEX adapters.

    DEX on-chain quotes (getAmountsOut / quoteExactInputSingle) already
    account for the pool fee in their output. This flag prevents double
    counting in FeeService — fee should only be subtracted once.
    """

    version: str = "base"
    quote_includes_pool_fee: bool = True
    _web3_factory = None  # set by subclasses

    def _web3_providers(self, network: str) -> list:
        """All RPC providers for failover (or single get_web3 fallback)."""
        factory = self._web3_factory
        if factory is None:
            return []
        all_fn = getattr(factory, "all", None)
        if callable(all_fn):
            providers = all_fn(network) or []
            if providers:
                return providers
        w3 = factory(network)
        return [w3] if w3 is not None else []

    async def _with_web3_failover(
        self,
        network: str,
        op: Callable,
        *,
        is_ok: Callable[[T], bool] | None = None,
    ) -> T | None:
        """Try op(w3) on each RPC until is_ok(result)."""
        check = is_ok or (lambda r: r is not None)
        for w3 in self._web3_providers(network):
            try:
                result = await op(w3)
                if check(result):
                    return result
            except Exception:
                continue
        return None

    @abstractmethod
    async def quote_exact_input(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
        amount_in: int,
    ) -> Decimal:
        """Simulate a swap on-chain.

        Args:
            network: Internal network name.
            pool_address: Lowercased pool contract address.
            token_in: Lowercased input token address.
            token_out: Lowercased output token address.
            amount_in: Input amount in smallest unit (wei).

        Returns:
            Raw amount_out from on-chain simulation.

        Raises:
            NotImplementedError: If adapter not implemented.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_pool_fee_bps(
        self,
        network: str,
        pool_address: str,
    ) -> Decimal | None:
        """Get pool fee in basis points.

        Args:
            network: Internal network name.
            pool_address: Lowercased pool contract address.

        Returns:
            Fee in bps or None if unavailable.
        """
        raise NotImplementedError

    async def get_reserves(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
    ) -> tuple[Decimal, Decimal] | None:
        """Get pool reserves for token_in and token_out.

        Default: None (unknown). Scanner then falls back to the fixed
        BASE_AMOUNT_USD sizing instead of reserve-based sizing.
        Subclasses override with a real implementation.
        """
        return None
