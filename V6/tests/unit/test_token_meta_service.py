"""TokenMetaService decimals batch fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.token_meta_service import DECIMALS_CACHE, TokenMetaService


@pytest.mark.asyncio
async def test_decimals_batch_sequential_after_multicall_soft_fail():
    """Multicall all-False must not skip sequential fill (ETH wipe bug)."""
    DECIMALS_CACHE.clear()
    # Unknown ETH token address (not in KNOWN_DECIMALS).
    addr = "0x1111111111111111111111111111111111111111"

    multicall = MagicMock()
    multicall.try_aggregate = AsyncMock(
        return_value=[(False, "0x")]  # circuit_open soft-fail shape
    )

    rpc = MagicMock()
    # decimals() = 18 → 0x...12
    rpc.eth_call = AsyncMock(
        return_value="0x0000000000000000000000000000000000000000000000000000000000000012"
    )

    svc = TokenMetaService(rpc_client_factory=lambda _n: rpc, multicall_client=multicall)
    out = await svc.get_decimals_batch("ETHEREUM", [addr])
    assert out.get(addr.lower()) == 18
    multicall.try_aggregate.assert_awaited()
    rpc.eth_call.assert_awaited()
