"""
V1 DEX adapter — Solidly-family AMM (network-agnostic).

Works on ANY EVM network where the pool implements:
  getAmountOut(uint256 amountIn, address tokenIn) → uint256

Used by Aerodrome (Base), Thena (BSC), Ramses/Chronos (Arbitrum),
Pearl (Polygon), and other Solidly V1 forks. Not tied to a single chain.
"""

from decimal import Decimal

from dex.base_adapter import BaseDexAdapter

_SOLIDLY_PAIR_ABI = [
    {
        "name": "getAmountOut",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "tokenIn", "type": "address"},
        ],
        "outputs": [{"name": "amountOut", "type": "uint256"}],
    },
    {
        "name": "token0",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "name": "token1",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "name": "getReserves",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [
            {"name": "reserve0", "type": "uint256"},
            {"name": "reserve1", "type": "uint256"},
            {"name": "blockTimestampLast", "type": "uint256"},
        ],
    },
    {
        "name": "stable",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "bool"}],
    },
]


class V1Adapter(BaseDexAdapter):
    """Solidly V1-style pool adapter (any network)."""

    version = "v1"

    def __init__(
        self,
        web3_factory,
        default_fee_bps: int = 30,
        router_address: str | None = None,
    ):
        self._web3_factory = web3_factory
        self._default_fee_bps = Decimal(default_fee_bps)
        self._router_address = router_address

    async def quote_exact_input(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
        amount_in: int,
    ) -> Decimal:
        w3 = self._web3_factory(network)
        if w3 is None or amount_in <= 0:
            return Decimal("0")
        try:
            pair = w3.eth.contract(
                address=w3.to_checksum_address(pool_address),
                abi=_SOLIDLY_PAIR_ABI,
            )
            out = await pair.functions.getAmountOut(
                int(amount_in),
                w3.to_checksum_address(token_in),
            ).call()
            return Decimal(out)
        except Exception:
            return Decimal("0")

    async def get_pool_fee_bps(self, network: str, pool_address: str) -> Decimal | None:
        return self._default_fee_bps

    async def get_reserves(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
    ) -> tuple[Decimal, Decimal] | None:
        w3 = self._web3_factory(network)
        if w3 is None:
            return None
        try:
            pair = w3.eth.contract(
                address=w3.to_checksum_address(pool_address),
                abi=_SOLIDLY_PAIR_ABI,
            )
            token0 = await pair.functions.token0().call()
            reserves = await pair.functions.getReserves().call()
            r0, r1 = Decimal(reserves[0]), Decimal(reserves[1])
            if token0.lower() == token_in.lower():
                return r0, r1
            return r1, r0
        except Exception:
            return None
