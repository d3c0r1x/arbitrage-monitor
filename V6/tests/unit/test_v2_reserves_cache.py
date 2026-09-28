"""Unit tests for V2 local reserves / amountOut helpers."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from services.v2_reserves_cache import V2Reserves, V2ReservesCache, amount_out_v2


class TestAmountOutV2:
    def test_basic_swap(self):
        # 1000 in, 1000/1000 reserves, 0.25% fee → classic CPMM
        out = amount_out_v2(
            Decimal("1000"),
            Decimal("100000"),
            Decimal("100000"),
            fee_bps=Decimal("25"),
        )
        assert out > 0
        assert out < Decimal("1000")
        # ≈ 987.16 with 0.25% fee on equal reserves
        assert Decimal("980") < out < Decimal("990")

    def test_zero_inputs(self):
        assert amount_out_v2(Decimal("0"), Decimal("1"), Decimal("1")) == 0
        assert amount_out_v2(Decimal("1"), Decimal("0"), Decimal("1")) == 0


class TestV2ReservesCache:
    def test_quote_oriented(self):
        cache = V2ReservesCache(multicall_client=None)
        cache._data[("BSC", "0xpool")] = V2Reserves(
            pool="0xpool",
            token0="0xaa",
            reserve0=Decimal("100000"),
            reserve1=Decimal("200000"),
        )
        out = cache.quote("BSC", "0xpool", "0xaa", 1000, fee_bps=Decimal("25"))
        assert out > 0
        out_rev = cache.quote("BSC", "0xpool", "0xbb", 1000, fee_bps=Decimal("25"))
        assert out_rev > 0
        # Selling scarce side (token0) into larger reserve_out → more tokens out
        assert out > out_rev

    def test_miss_returns_zero(self):
        cache = V2ReservesCache(multicall_client=None)
        assert cache.quote("BSC", "0xmissing", "0xaa", 1000) == 0


@pytest.mark.asyncio
async def test_mid_headroom_skips_orderbook(tmp_path):
    """Thin mid edges soft-promote without hitting the book."""
    from scanner.scanner import Scanner

    scanner = Scanner.__new__(Scanner)
    scanner._pools_cache_path = tmp_path / "pools_cache.json"
    scanner._price_service = MagicMock()
    scanner._profit_calculator = MagicMock()
    scanner._adapter_factory = MagicMock()
    scanner._signal_writer = MagicMock()
    scanner._signal_writer.write_signal = MagicMock()
    scanner._rpc_client_factory = MagicMock()
    scanner._token_security_checker = None
    scanner._pool_security_checker = None
    scanner._orderbook_service = MagicMock()
    scanner._signal_watcher = None
    scanner._promote_radar_soft = MagicMock(return_value=None)

    # Make promote async
    async def _soft(**kwargs):
        return None

    scanner._promote_radar_soft = _soft
    scanner._apply_orderbook_final = MagicMock()

    from models.signal_models import FeeBreakdown
    from unittest.mock import patch

    fees = FeeBreakdown(
        dex_network_fee_usd=Decimal("0.05"),
        mexc_trading_fee_usd=Decimal("0.01"),
        total=Decimal("0.06"),
    )
    result = {
        "net_profit_pct": Decimal("1.10"),  # below 1.0 + 0.25 headroom
        "direction": "DEX_BUY_MEXC_SELL",
        "base_amount_usd": Decimal("10"),
        "gross_profit_usd": Decimal("0.15"),
        "gross_profit_pct": Decimal("1.5"),
        "fees": fees,
        "net_profit_usd": Decimal("0.11"),
    }
    pool = {
        "network": "BSC",
        "token_address": "0xtoken",
        "stablecoin_address": "0xstable",
        "pool_address": "0xpool",
        "dex": "pancakeswap",
    }
    with patch("scanner.scanner.settings") as mock_settings:
        mock_settings.MIN_NET_PROFIT_PCT = Decimal("1")
        mock_settings.ETH_MIN_NET_PROFIT_PCT = Decimal("2")
        mock_settings.ORDERBOOK_MID_HEADROOM_PCT = Decimal("0.25")
        mock_settings.MAX_NET_PROFIT_PCT = Decimal("50")
        written = await scanner._write_signal(
            "BSC",
            pool,
            "TOKEN",
            "USDT",
            result,
            Decimal("1.0"),
            Decimal("1000000000000000000"),
        )
    assert written is False
    scanner._apply_orderbook_final.assert_not_called()
