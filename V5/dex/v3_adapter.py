"""
V3 DEX adapter.

Uses QuoterV2.quoteExactInputSingle for on-chain swap simulation.
Retries across all Web3 RPC providers on soft-fail / 429.
"""

from decimal import Decimal

from abi.erc20 import ERC20_ABI
from abi.uniswap_v3 import UNISWAP_V3_POOL_ABI, UNISWAP_V3_QUOTER_V2_ABI
from dex.base_adapter import BaseDexAdapter


class V3Adapter(BaseDexAdapter):
    """Adapter for Uniswap V3 style DEX pools.

    Caches pool fee per pool_address since V3 fees never change.
    """

    version = "v3"

    def __init__(self, web3_factory, quoter_v2_address: str):
        self._web3_factory = web3_factory
        self._quoter_v2_address = quoter_v2_address
        self._fee_cache: dict[str, int] = {}

    async def _get_pool_fee(self, network: str, pool_address: str) -> int | None:
        """Get V3 pool fee (cached). Fee never changes, cache indefinitely."""
        cached = self._fee_cache.get(pool_address)
        if cached is not None:
            return cached

        async def _read(w3) -> int:
            pool = w3.eth.contract(
                address=w3.to_checksum_address(pool_address),
                abi=UNISWAP_V3_POOL_ABI,
            )
            return int(await pool.functions.fee().call())

        fee = await self._with_web3_failover(network, _read)
        if fee is None:
            return None
        self._fee_cache[pool_address] = fee
        return fee

    def clear_fee_cache(self) -> None:
        """Clear the fee cache (e.g. if pool addresses change)."""
        self._fee_cache.clear()

    async def quote_exact_input(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
        amount_in: int,
    ) -> Decimal:
        """Simulate swap via QuoterV2.quoteExactInputSingle."""
        self._last_gas_estimate = None
        fee = await self._get_pool_fee(network, pool_address)
        if fee is None:
            return Decimal("0")

        async def _quote(w3) -> Decimal:
            quoter = w3.eth.contract(
                address=w3.to_checksum_address(self._quoter_v2_address),
                abi=UNISWAP_V3_QUOTER_V2_ABI,
            )
            params = (
                w3.to_checksum_address(token_in),
                w3.to_checksum_address(token_out),
                int(amount_in),
                int(fee),
                0,  # sqrtPriceLimitX96
            )
            try:
                result = await quoter.functions.quoteExactInputSingle(params).call()
                amount_out = result[0]
                if len(result) > 3:
                    self._last_gas_estimate = int(result[3])
                return Decimal(amount_out)
            except Exception as call_exc:
                # QuoterV2 reverts BY DESIGN with result in revert data.
                decoded = self._decode_quoter_revert(call_exc)
                if decoded is not None:
                    return Decimal(decoded[0])
                raise

        out = await self._with_web3_failover(
            network,
            _quote,
            is_ok=lambda r: r is not None and r > 0,
        )
        return out if out is not None else Decimal("0")

    @staticmethod
    def _decode_quoter_revert(exc) -> tuple | None:
        """Decode QuoterV2 revert data: (amountOut, sqrtPriceX96, tick, gasEstimate)."""
        data = getattr(exc, "data", None)
        if not data:
            args = getattr(exc, "args", ())
            if args and isinstance(args[0], str) and args[0].startswith("0x"):
                data = args[0]
        if not data:
            return None
        try:
            if isinstance(data, str):
                raw = bytes.fromhex(data[2:] if data.startswith("0x") else data)
            else:
                raw = bytes(data)
            if len(raw) >= 132:
                raw = raw[4:]
            elif len(raw) == 128:
                pass
            else:
                return None
            from eth_abi import decode as abi_decode

            decoded = abi_decode(
                ["uint256", "uint160", "int24", "uint256"], raw[:128]
            )
            return decoded
        except Exception:
            return None

    def get_last_gas_estimate(self) -> int | None:
        """Return the gas estimate from the most recent quote."""
        return getattr(self, "_last_gas_estimate", None)

    async def get_pool_fee_bps(
        self,
        network: str,
        pool_address: str,
    ) -> Decimal | None:
        """Get V3 pool fee in bps (3000 hundredths-of-bip → 30 bps)."""
        fee = await self._get_pool_fee(network, pool_address)
        if fee is None:
            return None
        return Decimal(fee) / Decimal(100)

    async def get_reserves(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
    ) -> tuple[Decimal, Decimal] | None:
        """Approximate V3 reserves via token balances held by the pool."""

        async def _read(w3):
            pool_cs = w3.to_checksum_address(pool_address)
            bal_in = await w3.eth.contract(
                address=w3.to_checksum_address(token_in),
                abi=ERC20_ABI,
            ).functions.balanceOf(pool_cs).call()
            bal_out = await w3.eth.contract(
                address=w3.to_checksum_address(token_out),
                abi=ERC20_ABI,
            ).functions.balanceOf(pool_cs).call()
            return (Decimal(bal_in), Decimal(bal_out))

        return await self._with_web3_failover(network, _read)
