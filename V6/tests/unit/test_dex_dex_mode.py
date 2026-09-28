"""Unit tests for scan mode + DEX-DEX store + chain gas/net from sim amounts."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from scanner.chain_engine import ChainEngine
from services.dex_dex_store import DexDexStore
from services.scan_mode import VALID, get_scan_mode, set_scan_mode


def test_set_scan_mode_roundtrip(tmp_path, monkeypatch):
    import services.scan_mode as sm

    monkeypatch.setattr(sm, "MODE_PATH", tmp_path / "scan_mode.json")
    sm._cached = None
    assert set_scan_mode("dex_dex") == "dex_dex"
    sm._cached = None
    assert get_scan_mode() == "dex_dex"
    assert set_scan_mode("mexc") == "mexc"
    with pytest.raises(ValueError):
        set_scan_mode("nope")
    assert VALID == frozenset({"mexc", "dex_dex"})


def test_dex_dex_store_publish(tmp_path):
    store = DexDexStore(
        signals_path=tmp_path / "sig.jsonl",
        live_path=tmp_path / "live.json",
    )
    store.write_signal({"token_coin": "AAA", "net_profit_pct": "1.2"})
    store.publish_cycle(
        [
            {
                "network": "BSC",
                "token_coin": "AAA",
                "direction": "DEX_DEX",
                "buy_dex": "a",
                "sell_dex": "b",
                "buy_pool": "0x1",
                "net_profit_pct": 1.5,
                "net_profit_usd": 0.15,
                "amount_in_raw": 100,
                "amount_out_raw": 101,
            }
        ]
    )
    assert (tmp_path / "sig.jsonl").read_text(encoding="utf-8").count("AAA") == 1
    live = (tmp_path / "live.json").read_text(encoding="utf-8")
    assert "AAA" in live and '"count": 1' in live


@pytest.mark.asyncio
async def test_chain_validate_uses_sim_amounts_minus_gas():
    async def quote(**kwargs):
        # Each hop multiplies by 1.02 → 2 hops ≈ +4% gross tokens.
        return Decimal(int(kwargs["amount_in"]) * 102 // 100)

    adapter = MagicMock()
    adapter.quote_exact_input = AsyncMock(side_effect=quote)
    af = MagicMock()
    af.get_adapter = MagicMock(return_value=adapter)
    price = MagicMock()
    price.get_price = MagicMock(return_value=Decimal("1"))
    fee = MagicMock()
    fee._estimate_gas_cost_usd = AsyncMock(return_value=Decimal("0.01"))

    eng = ChainEngine(af, price, fee)
    eng._decimals[("BSC", "0xusdt")] = 18
    eng._coin_map[("BSC", "0xusdt")] = "USDT"
    chain = [
        ("0xp1", "0xusdt", "0xw", "pancake", "v2"),
        ("0xp2", "0xw", "0xusdt", "sushi", "v2"),
    ]
    amount_in = 10**18  # 1 USDT
    result = await eng.validate_chain("BSC", chain, amount_in)
    assert result is not None
    assert result["amount_out_raw"] > amount_in
    assert result["gas_usd"] == Decimal("0.02")
    assert result["net_profit_usd"] < result["gross_profit_usd"]
    assert result["signal"] is True
