"""
V2 DEX adapter.

Uses router.getAmountsOut for quote.
Falls back to reserve calculation if router is unavailable.
"""

from decimal import Decimal

from abi.uniswap_v2 import UNISWAP_V2_PAIR_ABI, UNISWAP_V2_ROUTER_ABI
from dex.base_adapter import BaseDexAdapter


class V2Adapter(BaseDexAdapter):
    """Adapter for Uniswap V2 style DEX pools."""

    version = "v2"

    def __init__(self, web3_factory, router_address: str, default_fee_bps: int):
        self._web3_factory = web3_factory
        self._router_address = router_address
        self._default_fee_bps = Decimal(default_fee_bps)
        self._token0_cache: dict[str, str] = {}

    async def _get_token0(self, network: str, pool_address: str) -> str | None:
        """Get token0 of a pool (cached). Immutable on-chain."""
        cached = self._token0_cache.get(pool_address)
        if cached is not None:
            return cached

        w3 = self._web3_factory(network)
        if w3 is None:
            return None

        try:
            pair = w3.eth.contract(
                address=w3.to_checksum_address(pool_address),
                abi=UNISWAP_V2_PAIR_ABI,
            )
            token0 = await pair.functions.token0().call()
            self._token0_cache[pool_address] = token0.lower()
            return token0.lower()
        except Exception:
            return None

    async def quote_exact_input(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
        amount_in: int,
    ) -> Decimal:
        """Simulate swap via router.getAmountsOut or reserves.

        Prefers router method. Falls back to reserve-based calculation.
        """
        w3 = self._web3_factory(network)
        if w3 is None:
            return Decimal("0")

        router = w3.eth.contract(
            address=w3.to_checksum_address(self._router_address),
            abi=UNISWAP_V2_ROUTER_ABI,
        )

        path = [
            w3.to_checksum_address(token_in),
            w3.to_checksum_address(token_out),
        ]

        try:
            amounts = await router.functions.getAmountsOut(
                amount_in,
                path,
            ).call()

            return Decimal(amounts[-1])

        except Exception:
            return await self._quote_from_reserves(
                network=network,
                pool_address=pool_address,
                token_in=token_in,
                token_out=token_out,
                amount_in=Decimal(amount_in),
            )

    async def _quote_from_reserves(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
        amount_in: Decimal,
    ) -> Decimal:
        """Calculate quote from pool reserves directly."""
        w3 = self._web3_factory(network)
        if w3 is None:
            return Decimal("0")

        try:
            pair = w3.eth.contract(
                address=w3.to_checksum_address(pool_address),
                abi=UNISWAP_V2_PAIR_ABI,
            )

            token0 = await self._get_token0(network, pool_address)
            if token0 is None:
                return Decimal("0")
            reserves = await pair.functions.getReserves().call()

            reserve0 = Decimal(reserves[0])
            reserve1 = Decimal(reserves[1])

            if token0.lower() == token_in.lower():
                reserve_in = reserve0
                reserve_out = reserve1
            else:
                reserve_in = reserve1
                reserve_out = reserve0

            if reserve_in <= 0 or reserve_out <= 0:
                return Decimal("0")

            fee_bps = self._default_fee_bps
            amount_in_with_fee = amount_in * (Decimal(10000) - fee_bps)
            numerator = amount_in_with_fee * reserve_out
            denominator = reserve_in * Decimal(10000) + amount_in_with_fee

            return numerator / denominator

        except Exception:
            return Decimal("0")

    async def get_pool_fee_bps(
        self,
        network: str,
        pool_address: str,
    ) -> Decimal | None:
        return self._default_fee_bps

    async def get_reserves(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
    ) -> tuple[Decimal, Decimal] | None:
        """Get pool reserves for token_in and token_out.

        Returns (reserve_in, reserve_out) in smallest units.
        """
        w3 = self._web3_factory(network)
        if w3 is None:
            return None

        try:
            pair = w3.eth.contract(
                address=w3.to_checksum_address(pool_address),
                abi=UNISWAP_V2_PAIR_ABI,
            )

            token0 = await self._get_token0(network, pool_address)
            if token0 is None:
                return None

            reserves = await pair.functions.getReserves().call()
            reserve0 = Decimal(reserves[0])
            reserve1 = Decimal(reserves[1])

            if token0.lower() == token_in.lower():
                return (reserve0, reserve1)
            else:
                return (reserve1, reserve0)

        except Exception:
            return None
