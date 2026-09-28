"""
Uniswap V4 adapter — network-agnostic soft quote.

V4 pools are identified by PoolKey (currency0/1, fee, tickSpacing, hooks),
not a classic pair address. When we lack a full PoolKey we return 0
(fail-soft) instead of raising — scanner treats 0 as no-edge.

When ``quoter`` is configured, attempts ``quoteExactInputSingle`` if the
caller later passes enough metadata via pool extras (future). Today the
primary path is fail-soft so v4 is a first-class version in the pipeline
without hard-skipping.
"""

from decimal import Decimal
import logging

from dex.base_adapter import BaseDexAdapter

logger = logging.getLogger(__name__)

# Minimal V4 Quoter ABI (quoteExactInputSingle).
UNISWAP_V4_QUOTER_ABI = [
    {
        "name": "quoteExactInputSingle",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {
                "name": "params",
                "type": "tuple",
                "components": [
                    {
                        "name": "poolKey",
                        "type": "tuple",
                        "components": [
                            {"name": "currency0", "type": "address"},
                            {"name": "currency1", "type": "address"},
                            {"name": "fee", "type": "uint24"},
                            {"name": "tickSpacing", "type": "int24"},
                            {"name": "hooks", "type": "address"},
                        ],
                    },
                    {"name": "zeroForOne", "type": "bool"},
                    {"name": "exactAmount", "type": "uint128"},
                    {"name": "hookData", "type": "bytes"},
                ],
            }
        ],
        "outputs": [
            {"name": "amountOut", "type": "uint256"},
            {"name": "gasEstimate", "type": "uint256"},
        ],
    }
]


class V4Adapter(BaseDexAdapter):
    """Uniswap V4 adapter (any network with a Quoter deployment)."""

    version = "v4"

    def __init__(
        self,
        web3_factory,
        quoter_address: str | None = None,
        pool_manager: str | None = None,
        default_fee_bps: int = 30,
    ):
        self._web3_factory = web3_factory
        self._quoter_address = (quoter_address or "").lower() or None
        self._pool_manager = (pool_manager or "").lower() or None
        self._default_fee_bps = Decimal(default_fee_bps)

    async def quote_exact_input(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
        amount_in: int,
    ) -> Decimal:
        """Quote V4 swap. Returns 0 when PoolKey cannot be reconstructed.

        ``pool_address`` for V4 is often a poolId / manager-scoped id, not
        a pair contract — without fee/tickSpacing/hooks we cannot quote.
        """
        if amount_in <= 0:
            return Decimal("0")
        # Soft fail: pipeline stays version-equal; no hard skip in scanner.
        # Full PoolKey quoting lands when discovery stores fee/hooks/tickSpacing.
        logger.debug(
            "v4_quote_soft_zero: network=%s pool=%s (need PoolKey metadata)",
            network,
            pool_address,
        )
        return Decimal("0")

    async def get_pool_fee_bps(self, network: str, pool_address: str) -> Decimal | None:
        return self._default_fee_bps
