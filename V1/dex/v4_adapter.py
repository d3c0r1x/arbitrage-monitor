"""
V4 DEX adapter — placeholder.

Uniswap V4 requires PoolManager and hook-aware simulation.
Not yet implemented.
"""

from decimal import Decimal

from dex.base_adapter import BaseDexAdapter


class V4Adapter(BaseDexAdapter):
    """Placeholder for Uniswap V4 adapter."""

    version = "v4"

    async def quote_exact_input(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
        amount_in: int,
    ) -> Decimal:
        """V4 adapter not implemented.

        Implementation requirements:
        1. Use official V4 Quoter if available.
        2. If hook changes output, use static simulation through PoolManager.
        3. If quote cannot be safely simulated, return 0 and add warning.
        """
        raise NotImplementedError("Uniswap V4 adapter must be implemented separately.")

    async def get_pool_fee_bps(
        self,
        network: str,
        pool_address: str,
    ) -> Decimal | None:
        raise NotImplementedError("Uniswap V4 adapter must be implemented separately.")
