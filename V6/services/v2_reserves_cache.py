"""
V2 pool reserves: Multicall3 batch fetch + local getAmountOut.

Faster than per-pool router.getAmountsOut / Quoter for executable sizing:
one RPC batch → many local amountOut simulations (size sweep free).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from eth_abi import decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

logger = logging.getLogger(__name__)

_GET_RESERVES_SEL = function_signature_to_4byte_selector("getReserves()")
_TOKEN0_SEL = function_signature_to_4byte_selector("token0()")


@dataclass(frozen=True)
class V2Reserves:
    """Reserves oriented as (reserve_token0, reserve_token1) + token0 address."""

    pool: str
    token0: str
    reserve0: Decimal
    reserve1: Decimal

    def oriented(self, token_in: str) -> tuple[Decimal, Decimal] | None:
        tin = token_in.lower()
        if tin == self.token0:
            return self.reserve0, self.reserve1
        return self.reserve1, self.reserve0


def amount_out_v2(
    amount_in: Decimal,
    reserve_in: Decimal,
    reserve_out: Decimal,
    fee_bps: Decimal = Decimal("25"),
) -> Decimal:
    """Uniswap/Pancake V2 constant-product amountOut (fee in bps, default 0.25%).

    Matches Solidity integer division (floor) so local quotes == getAmountsOut.
    """
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return Decimal("0")
    # Integer CPMM (same as UniswapV2Library.getAmountOut).
    ain = int(amount_in)
    rin = int(reserve_in)
    rout = int(reserve_out)
    bps = int(fee_bps)
    amount_in_with_fee = ain * (10000 - bps)
    numerator = amount_in_with_fee * rout
    denominator = rin * 10000 + amount_in_with_fee
    if denominator <= 0:
        return Decimal("0")
    return Decimal(numerator // denominator)


class V2ReservesCache:
    """Cycle-scoped cache of V2 getReserves via Multicall3."""

    def __init__(self, multicall_client=None):
        self._multicall = multicall_client
        # (network, pool_lower) -> V2Reserves
        self._data: dict[tuple[str, str], V2Reserves] = {}
        self._fee_bps_default = Decimal("25")

    def clear(self) -> None:
        self._data.clear()

    def get(self, network: str, pool: str) -> V2Reserves | None:
        return self._data.get((network, pool.lower()))

    def quote(
        self,
        network: str,
        pool: str,
        token_in: str,
        amount_in: int | Decimal,
        fee_bps: Decimal | None = None,
    ) -> Decimal:
        row = self.get(network, pool)
        if row is None:
            return Decimal("0")
        oriented = row.oriented(token_in)
        if oriented is None:
            return Decimal("0")
        rin, rout = oriented
        return amount_out_v2(
            Decimal(amount_in),
            rin,
            rout,
            fee_bps if fee_bps is not None else self._fee_bps_default,
        )

    def reserves_for(
        self,
        network: str,
        pool: str,
        token_in: str,
    ) -> tuple[Decimal, Decimal] | None:
        row = self.get(network, pool)
        if row is None:
            return None
        return row.oriented(token_in)

    async def prefetch(
        self,
        network: str,
        pool_addresses: list[str],
    ) -> int:
        """Batch-fetch token0 + getReserves for V2 pools. Returns hits."""
        if not pool_addresses or self._multicall is None:
            return 0
        uniq: list[str] = []
        seen: set[str] = set()
        for p in pool_addresses:
            pl = p.lower()
            if pl in seen:
                continue
            seen.add(pl)
            uniq.append(pl)

        # Two calls per pool: token0, getReserves
        calls: list[tuple[str, str]] = []
        for pl in uniq:
            addr = Web3.to_checksum_address(pl)
            calls.append((addr, "0x" + _TOKEN0_SEL.hex()))
            calls.append((addr, "0x" + _GET_RESERVES_SEL.hex()))

        try:
            results = await self._multicall.try_aggregate(network, calls)
        except Exception as exc:
            logger.warning("v2_reserves_multicall_failed: %s %s", network, exc)
            return 0

        hits = 0
        for i, pl in enumerate(uniq):
            ok0, data0 = results[2 * i] if 2 * i < len(results) else (False, "0x")
            ok1, data1 = results[2 * i + 1] if 2 * i + 1 < len(results) else (False, "0x")
            if not ok0 or not ok1 or len(data0) < 66 or len(data1) < 66:
                continue
            try:
                (token0,) = decode(["address"], bytes.fromhex(data0[2:]))
                r0, r1, _ts = decode(
                    ["uint112", "uint112", "uint32"],
                    bytes.fromhex(data1[2:]),
                )
            except Exception:
                continue
            self._data[(network, pl)] = V2Reserves(
                pool=pl,
                token0=str(token0).lower(),
                reserve0=Decimal(r0),
                reserve1=Decimal(r1),
            )
            hits += 1
        logger.info(
            "v2_reserves_prefetched: network=%s pools=%d hits=%d",
            network,
            len(uniq),
            hits,
        )
        return hits
