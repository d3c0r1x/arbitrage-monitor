"""
V3 DEX adapter.

Uses QuoterV2.quoteExactInputSingle for on-chain swap simulation.
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

        w3 = self._web3_factory(network)
        if w3 is None:
            return None

        try:
            pool = w3.eth.contract(
                address=w3.to_checksum_address(pool_address),
                abi=UNISWAP_V3_POOL_ABI,
            )
            fee = await pool.functions.fee().call()
            self._fee_cache[pool_address] = fee
            return fee
        except Exception:
            return None

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
        """Simulate swap via QuoterV2.quoteExactInputSingle.

        Stores the gas estimate from quoter response for FeeService.
        """
        self._last_gas_estimate = None
        w3 = self._web3_factory(network)
        if w3 is None:
            return Decimal("0")

        try:
            fee = await self._get_pool_fee(network, pool_address)
            if fee is None:
                return Decimal("0")

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
                # Some RPC providers return result directly (no revert).
                amount_out = result[0]
                if len(result) > 3:
                    self._last_gas_estimate = int(result[3])
                return Decimal(amount_out)
            except Exception as call_exc:
                # QuoterV2 reverts BY DESIGN with result in revert data.
                decoded = self._decode_quoter_revert(call_exc)
                if decoded is not None:
                    return Decimal(decoded[0])
                return Decimal("0")

        except Exception:
            return Decimal("0")

    @staticmethod
    def _decode_quoter_revert(exc) -> tuple | None:
        """Decode QuoterV2 revert data: (amountOut, sqrtPriceX96, tick, gasEstimate)."""
        data = getattr(exc, 'data', None)
        if not data:
            # Try nested args (web3 v7 wraps in ContractCustomError).
            args = getattr(exc, 'args', ())
            if args and isinstance(args[0], str) and args[0].startswith('0x'):
                data = args[0]
        if not data:
            return None
        try:
            if isinstance(data, str):
                raw = bytes.fromhex(data[2:] if data.startswith('0x') else data)
            else:
                raw = bytes(data)
            # Strip 4-byte error selector if present (QuoteExactInputSingleReturns).
            # Output is 4 uint256-sized words = 128 bytes.
            if len(raw) >= 132:
                raw = raw[4:]  # strip selector
            elif len(raw) == 128:
                pass  # already stripped
            else:
                return None
            from eth_abi import decode as abi_decode
            decoded = abi_decode(
                ['uint256', 'uint160', 'int24', 'uint256'], raw[:128]
            )
            return decoded
        except Exception:
            return None

    def get_last_gas_estimate(self) -> int | None:
        """Return the gas estimate from the most recent quote."""
        return getattr(self, '_last_gas_estimate', None)

    async def get_pool_fee_bps(
        self,
        network: str,
        pool_address: str,
    ) -> Decimal | None:
        """Get V3 pool fee in bps.

        Uniswap V3 fee is stored in hundredths of a bip.
        3000 = 0.30% = 30 bps.
        Uses cached fee value.
        """
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
        """Approximate V3 reserves via token balances held by the pool.

        V3 has no getReserves; token balanceOf(pool) is the standard
        upper-bound proxy for available liquidity, good enough for
        swap sizing and liquidity floors.
        """
        w3 = self._web3_factory(network)
        if w3 is None:
            return None

        try:
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
        except Exception:
            return None
