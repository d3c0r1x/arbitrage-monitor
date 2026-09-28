"""Tests for MEXC order-book fill simulation and optimal sizing."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from models.fee_models import FeeBreakdown
from services.orderbook_service import OrderbookService


class TestOrderbookSimulate:
    def test_buy_walks_asks(self):
        svc = OrderbookService(MagicMock())
        book = {
            "asks": [["1.00", "5"], ["1.10", "10"], ["1.20", "100"]],
            "bids": [["0.99", "5"]],
        }
        fill = svc.simulate_buy(book, Decimal("10"))
        assert fill is not None
        assert fill.fully_filled is True
        assert fill.base_filled == Decimal("5") + (Decimal("5") / Decimal("1.10"))
        assert fill.quote_filled == Decimal("10")

    def test_maximize_dir_b_picks_best_usd(self):
        """Prefer a smaller top-of-book clip over dumping into worse asks."""
        svc = OrderbookService(MagicMock())
        # Best ask cheap; deeper asks much worse (like SMARS cliff).
        book = {
            "asks": [
                ["1.00", "6"],   # $6 @ $1 → 6 tokens
                ["1.50", "100"], # expensive cliff
            ],
            "bids": [],
        }
        # DEX returns $1.25 per token → ~25% on the $6 clip.
        trade = svc.maximize_dir_b_profit(
            book,
            dex_usdt_per_token=Decimal("1.25"),
            fee_token=Decimal("0"),
            fixed_fees_usd=Decimal("0.05"),
            taker_fee_bps=10,
            min_notional_usd=Decimal("1"),
            max_notional_usd=Decimal("50"),
            min_profit_usd=Decimal("0.1"),
        )
        assert trade is not None
        assert trade.net_profit_usd >= Decimal("0.1")
        # Optimal should stay near the cheap $6 level, not eat the $1.50 cliff.
        assert trade.cost_usd <= Decimal("10")
        assert trade.net_profit_pct > Decimal("10")

    def test_maximize_rejects_when_below_floor(self):
        svc = OrderbookService(MagicMock())
        book = {"asks": [["1.00", "5"]], "bids": []}
        trade = svc.maximize_dir_b_profit(
            book,
            dex_usdt_per_token=Decimal("1.01"),
            fee_token=Decimal("0"),
            fixed_fees_usd=Decimal("0.5"),
            taker_fee_bps=10,
            min_profit_usd=Decimal("0.1"),
        )
        assert trade is None


class TestFeeBreakdownPoolFeeExcluded:
    def test_pool_fee_not_in_total(self):
        fees = FeeBreakdown(
            dex_network_fee_usd=Decimal("0.5"),
            dex_pool_fee_usd=Decimal("9.99"),
            mexc_trading_fee_usd=Decimal("0.1"),
            slippage_usd=Decimal("0.2"),
        )
        assert fees.total() == Decimal("0.8")


class TestScannerOrderbookFinal:
    @pytest.mark.asyncio
    async def test_dir_b_uses_optimal_clip(self):
        from scanner.scanner import Scanner

        scanner = Scanner.__new__(Scanner)
        book = {
            "asks": [["1.00", "6"], ["2.00", "100"]],
            "bids": [],
        }
        ob = MagicMock()
        ob.fetch_book = AsyncMock(return_value=book)
        # Real maximize path.
        real = OrderbookService(MagicMock())
        ob.maximize_dir_b_profit = real.maximize_dir_b_profit
        scanner._orderbook_service = ob

        fees = FeeBreakdown(
            dex_network_fee_usd=Decimal("0.05"),
            dex_pool_fee_usd=Decimal("0.25"),
            mexc_trading_fee_usd=Decimal("0.05"),
            mexc_withdraw_fee_usd=Decimal("0"),
            slippage_usd=Decimal("0.5"),
            stable_deposit_network_fee_usd=Decimal("0"),
        )
        # Mid quote: $10 → 10 tokens → $12.5 USDT settlement (25% gross).
        result = {
            "direction": "MEXC_BUY_DEX_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("2.5"),
            "gross_profit_pct": Decimal("25"),
            "net_profit_usd": Decimal("2"),
            "net_profit_pct": Decimal("20"),
            "fees": fees,
            "settlement_out_usd": Decimal("12.5"),
            "signal": True,
        }
        updated, _new_out, ok, warnings = await scanner._apply_orderbook_final(
            result=result,
            direction="MEXC_BUY_DEX_SELL",
            mexc_symbol="SMARSUSDT",
            mexc_price_usd=Decimal("1"),
            amount_out_raw=Decimal("12500000000000000000"),
            pool={"token_decimals": 18, "mexc_withdraw_fee": "0"},
        )
        assert ok is True, warnings
        assert updated["net_profit_usd"] >= Decimal("0.1")
        assert updated["base_amount_usd"] <= Decimal("10")
        assert any("size_usd=" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_rejects_when_no_clip(self):
        from scanner.scanner import Scanner

        scanner = Scanner.__new__(Scanner)
        ob = MagicMock()
        ob.fetch_book = AsyncMock(return_value={"asks": [], "bids": []})
        ob.maximize_dir_b_profit = MagicMock(return_value=None)
        scanner._orderbook_service = ob

        result = {
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("2"),
            "fees": FeeBreakdown(dex_network_fee_usd=Decimal("0.1")),
            "settlement_out_usd": Decimal("12"),
            "net_profit_pct": Decimal("5"),
        }
        _, _, ok, warnings = await scanner._apply_orderbook_final(
            result=result,
            direction="MEXC_BUY_DEX_SELL",
            mexc_symbol="XUSDT",
            mexc_price_usd=Decimal("1"),
            amount_out_raw=Decimal("1"),
            pool={"mexc_withdraw_fee": "0"},
        )
        assert ok is False
        assert "orderbook_no_profitable_clip" in warnings


def test_build_dir_b_size_curve_no_rpc():
    from services.orderbook_service import OrderbookService

    book = {
        "asks": [["1.0", "10"], ["1.1", "20"], ["1.2", "30"]],
        "bids": [["0.9", "10"]],
    }
    svc = OrderbookService(mexc_client=None)
    curve = svc.build_dir_b_size_curve(
        book,
        dex_usdt_per_token=Decimal("1.15"),
        fee_token=Decimal("0"),
        fixed_fees_usd=Decimal("0.01"),
        taker_fee_bps=10,
        min_notional_usd=Decimal("1"),
        max_notional_usd=Decimal("50"),
    )
    assert len(curve) >= 2
    assert curve[0].size_usd <= curve[-1].size_usd
    assert any(p.net_usd > 0 for p in curve)


def test_build_dir_b_size_curve_ignores_bad_cpmm_reserves():
    """Bad reserves must not paint the whole curve as −100% net."""
    from services.orderbook_service import OrderbookService

    book = {"asks": [["1.0", "20"], ["1.05", "30"]], "bids": [["0.9", "10"]]}
    svc = OrderbookService(mexc_client=None)
    # Tiny/swapped-looking reserves that would zero settlement via CPMM.
    curve = svc.build_dir_b_size_curve(
        book,
        dex_usdt_per_token=Decimal("1.20"),
        fee_token=Decimal("0"),
        fixed_fees_usd=Decimal("0.01"),
        taker_fee_bps=10,
        min_notional_usd=Decimal("1"),
        max_notional_usd=Decimal("40"),
        reserve_token=Decimal("0.0001"),
        reserve_stable=Decimal("0.0001"),
        pool_fee_bps=25,
    )
    assert any(p.net_usd > 0 for p in curve)
    opt = max(curve, key=lambda p: p.net_usd)
    assert opt.net_usd > 0
