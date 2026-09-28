"""Unit tests for services.signal_filters (plan v3 D5/D6/D14)."""

from decimal import Decimal

from services.signal_filters import post_signal_filter, pre_quote_filter


def _base_kwargs(**overrides):
    kwargs = {
        "net_profit_pct": Decimal("5"),
        "direction": "DEX_BUY_MEXC_SELL",
        "token_deposit_enable": True,
        "token_withdraw_enable": True,
        "base_amount_usd": Decimal("10"),
        "mexc_withdraw_fee_usd": Decimal("0.1"),
        "token_min_confirm": 100,
        "warnings": [],
    }
    kwargs.update(overrides)
    return kwargs


class TestPreQuoteFilter:
    def test_ok_pool(self):
        pool = {"pool_version": "v2", "token_decimals": 18, "stablecoin_decimals": 6}
        ok, reason = pre_quote_filter(pool)
        assert ok and reason == "ok"

    def test_empty_version_rejected(self):
        pool = {"pool_version": "", "token_decimals": 18, "stablecoin_decimals": 6}
        ok, reason = pre_quote_filter(pool)
        assert not ok and reason == "unknown_pool_version"

    def test_missing_decimals_rejected(self):
        pool = {"pool_version": "v3", "token_decimals": None, "stablecoin_decimals": 6}
        ok, reason = pre_quote_filter(pool)
        assert not ok and reason == "decimals_missing"


class TestPostSignalFilter:
    def test_realistic_signal_passes(self):
        ok, reason = post_signal_filter(**_base_kwargs())
        assert ok and reason == "ok"

    def test_unrealistic_profit_rejected(self):
        ok, reason = post_signal_filter(
            **_base_kwargs(net_profit_pct=Decimal("21000"))
        )
        assert not ok and reason.startswith("unrealistic_profit")

    def test_direction_a_needs_deposit(self):
        ok, reason = post_signal_filter(
            **_base_kwargs(direction="DEX_BUY_MEXC_SELL", token_deposit_enable=False)
        )
        assert not ok and reason == "mexc_deposit_closed"

    def test_direction_b_allows_deposit_closed(self):
        """ASS-like case: deposit=False but withdraw=True is valid for B."""
        ok, _ = post_signal_filter(
            **_base_kwargs(
                direction="MEXC_BUY_DEX_SELL",
                token_deposit_enable=False,
                token_withdraw_enable=True,
            )
        )
        assert ok

    def test_direction_b_needs_withdraw(self):
        ok, reason = post_signal_filter(
            **_base_kwargs(direction="MEXC_BUY_DEX_SELL", token_withdraw_enable=False)
        )
        assert not ok and reason == "mexc_withdraw_closed"

    def test_high_withdraw_fee_rejected(self):
        ok, reason = post_signal_filter(
            **_base_kwargs(mexc_withdraw_fee_usd=Decimal("5"))
        )
        assert not ok and reason.startswith("high_withdraw_fee")

    def test_high_min_confirm_rejected_direction_a(self):
        ok, reason = post_signal_filter(**_base_kwargs(token_min_confirm=5000))
        assert not ok and reason.startswith("high_min_confirm")

    def test_high_min_confirm_allowed_direction_b(self):
        # AIDOGE-style: deposit minConfirm irrelevant when buying on MEXC.
        ok, _ = post_signal_filter(
            **_base_kwargs(
                direction="MEXC_BUY_DEX_SELL",
                token_min_confirm=5000,
            )
        )
        assert ok

    def test_none_min_confirm_passes(self):
        ok, _ = post_signal_filter(**_base_kwargs(token_min_confirm=None))
        assert ok

    def test_thin_liquidity_warning_rejected(self):
        ok, reason = post_signal_filter(
            **_base_kwargs(warnings=["thin_liquidity_quote_unreliable:pool"])
        )
        assert not ok and reason.startswith("fatal_warning")
