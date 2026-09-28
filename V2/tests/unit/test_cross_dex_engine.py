"""Unit tests for same-pair cross-DEX engine."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from scanner.cross_dex_engine import CrossDexEngine


def _pool(net, token, quote, dex, pool, ver="v2", coin="TOK", qcoin="USDT"):
    return {
        "network": net,
        "token_address": token,
        "stablecoin_address": quote,
        "dex": dex,
        "pool_address": pool,
        "pool_version": ver,
        "token_coin": coin,
        "stablecoin_coin": qcoin,
        "quote_coin": qcoin,
        "token_decimals": 18,
        "stablecoin_decimals": 18,
    }


class TestCrossDexEngine:
    def test_build_groups_requires_two_dexes(self):
        eng = CrossDexEngine(MagicMock(), MagicMock())
        pools = [
            _pool("BSC", "0xaa", "0xbb", "pancakeswap", "0xp1"),
            _pool("BSC", "0xaa", "0xbb", "pancakeswap", "0xp2"),  # same dex
            _pool("BSC", "0xaa", "0xbb", "sushiswap", "0xp3"),
            _pool("ETHEREUM", "0xaa", "0xbb", "uniswap", "0xp4"),  # inactive
        ]
        n = eng.build_groups(pools)
        assert n == 1
        assert ("BSC", "0xaa", "0xbb") in eng._groups

    @pytest.mark.asyncio
    async def test_scan_finds_profitable_route(self):
        async def quote(**kwargs):
            # buy: quote→token returns 2e18; sell: token→quote returns 11e18
            if kwargs["token_in"].startswith("0xbb"):
                return Decimal("2000000000000000000")
            return Decimal("11000000000000000000")

        adapter = MagicMock()
        adapter.quote_exact_input = AsyncMock(side_effect=quote)

        af = MagicMock()
        af.get_adapter = MagicMock(return_value=adapter)

        price = MagicMock()
        price.get_price = MagicMock(return_value=Decimal("1"))

        fee = MagicMock()
        fee._estimate_gas_cost_usd = AsyncMock(return_value=Decimal("0.01"))

        eng = CrossDexEngine(af, price, fee)
        eng.build_groups(
            [
                _pool("BSC", "0xaa", "0xbb", "pancakeswap", "0xp1"),
                _pool("BSC", "0xaa", "0xbb", "sushiswap", "0xp2"),
            ]
        )
        results = await eng.scan(base_amount_usd=Decimal("10"), max_pairs=10)
        assert len(results) >= 1
        assert results[0]["direction"] == "DEX_DEX"
        assert results[0]["net_profit_pct"] > 0
