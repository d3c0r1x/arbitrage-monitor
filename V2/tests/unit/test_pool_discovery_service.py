"""
Unit tests for PoolDiscoveryService.

Tests the generalized quote filter: pools are valid when the other side
is ANY priceable quote (stablecoin, wrapped native, priced MEXC coin),
not only stablecoins.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from models.pool_models import DiscoveredPool
from services.pool_discovery_service import PoolDiscoveryService
from services.stablecoin_registry_service import QuoteRecord


def make_pool(token0: str, token1: str, addr: str = "0xpool", network: str = "ETHEREUM"):
    return DiscoveredPool(
        network=network,
        pool_address=addr,
        dex="uniswap",
        token0_address=token0,
        token1_address=token1,
        sources={"dex_screener"},
    )


def make_quote(address: str, coin: str, is_stable: bool, price_coin: str | None = None):
    return QuoteRecord(
        coin=coin,
        network="ETHEREUM",
        address=address,
        is_stable=is_stable,
        price_coin=price_coin or coin,
        withdraw_fee=Decimal("0.5") if is_stable else None,
    )


class TestFilterPools:
    @pytest.fixture
    def service(self):
        svc = PoolDiscoveryService.__new__(PoolDiscoveryService)
        svc._source_manager = MagicMock()
        svc._stablecoins = MagicMock()
        return svc

    def test_stable_quote_pool_kept(self, service):
        quotes = {"0xusdt": make_quote("0xusdt", "USDT", True)}
        pools = service._filter_pools(
            network="ETHEREUM",
            token_address="0xtoken",
            quote_records=quotes,
            discovered_pools=[make_pool("0xtoken", "0xusdt")],
        )
        assert len(pools) == 1
        assert pools[0].quote_coin == "USDT"
        assert pools[0].quote_is_stable is True
        assert pools[0].quote_withdraw_fee == "0.5"

    def test_weth_quote_pool_kept(self, service):
        """token<->WETH pool is now valid (was discarded before)."""
        quotes = {"0xweth": make_quote("0xweth", "ETH", False, "ETH")}
        pools = service._filter_pools(
            network="ETHEREUM",
            token_address="0xtoken",
            quote_records=quotes,
            discovered_pools=[make_pool("0xweth", "0xtoken")],
        )
        assert len(pools) == 1
        assert pools[0].stablecoin_address == "0xweth"
        assert pools[0].quote_is_stable is False
        assert pools[0].quote_price_coin == "ETH"

    def test_unknown_quote_pool_dropped(self, service):
        quotes = {"0xusdt": make_quote("0xusdt", "USDT", True)}
        pools = service._filter_pools(
            network="ETHEREUM",
            token_address="0xtoken",
            quote_records=quotes,
            discovered_pools=[make_pool("0xtoken", "0xjunk")],
        )
        assert pools == []

    def test_wrong_network_dropped(self, service):
        quotes = {"0xusdt": make_quote("0xusdt", "USDT", True)}
        pools = service._filter_pools(
            network="ETHEREUM",
            token_address="0xtoken",
            quote_records=quotes,
            discovered_pools=[make_pool("0xtoken", "0xusdt", network="BSC")],
        )
        assert pools == []

    def test_backward_compat_set(self, service):
        """Plain stablecoin-address set still works (treated as stable $1)."""
        pools = service._filter_pools(
            network="ETHEREUM",
            token_address="0xtoken",
            quote_records={"0xusdt"},
            discovered_pools=[make_pool("0xtoken", "0xusdt")],
        )
        assert len(pools) == 1
        assert pools[0].quote_is_stable is True
        assert pools[0].quote_coin is None

    def test_token_equals_quote_skipped(self, service):
        """Degenerate pool where the MEXC token IS the quote is skipped."""
        quotes = {"0xusdt": make_quote("0xusdt", "USDT", True)}
        pools = service._filter_pools(
            network="ETHEREUM",
            token_address="0xusdt",
            quote_records=quotes,
            discovered_pools=[make_pool("0xusdt", "0xusdt")],
        )
        assert pools == []

    @pytest.mark.asyncio
    async def test_discover_and_filter_passes_quotes(self, service):
        quotes = {"0xusdt": make_quote("0xusdt", "USDT", True)}
        service._source_manager.discover_from_all_sources = AsyncMock(
            return_value=[make_pool("0xtoken", "0xusdt")],
        )
        pools = await service.discover_and_filter_pools(
            network="ETHEREUM",
            token_address="0xTOKEN",
            quote_records=quotes,
        )
        assert len(pools) == 1
        assert pools[0].token_address == "0xtoken"
