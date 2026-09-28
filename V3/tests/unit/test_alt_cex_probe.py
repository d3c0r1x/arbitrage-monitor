"""Unit tests for alt-CEX client map + probe (no network)."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from clients.alt_cex_client import AltCexClient, _norm_addr, _norm_network
from services.alt_cex_probe import AltCexProbe


class TestNormHelpers:
    def test_norm_addr_adds_0x(self):
        assert _norm_addr("55d398326f99059ff775485246999027b3197955").startswith("0x")

    def test_norm_network_bsc(self):
        assert _norm_network("BEP20") == "BSC"
        assert _norm_network("BNB Smart Chain") == "BSC"
        assert _norm_network("BASE") == "BASE"


class TestAltCexClientMaps:
    @pytest.mark.asyncio
    async def test_resolve_contract_and_symbol_fallback(self):
        http = MagicMock()
        client = AltCexClient(http)

        # Inject maps without HTTP.
        client._maps_loaded = True
        client._contract_map[("BSC", "0xabc")] = {"bitget": "FOO", "htx": "FOO"}
        client._symbol_bases["bingx"].add("FOO")
        client._tickers["bitget"]["FOOUSDT"] = Decimal("1.5")
        client._tickers["htx"]["foousdt"] = Decimal("1.6")
        client._tickers["bingx"]["FOOUSDT"] = Decimal("1.4")

        listings = client.resolve_listings("BSC", "0xabc", "FOO")
        by_ex = {x["exchange"]: x for x in listings}
        assert by_ex["bitget"]["match"] == "contract"
        assert by_ex["htx"]["match"] == "contract"
        assert by_ex["bingx"]["match"] == "symbol"
        assert client.get_usdt_price("bitget", "FOO") == Decimal("1.5")
        assert client.get_usdt_price("htx", "FOO") == Decimal("1.6")


class TestAltCexProbe:
    @pytest.mark.asyncio
    async def test_emits_mexc_direction_signal(self, tmp_path):
        pools = [
            {
                "network": "BSC",
                "token_address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "token_coin": "TOK",
                "stablecoin_address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "pool_address": "0xcccccccccccccccccccccccccccccccccccccccc",
                "dex": "pancakeswap",
                "pool_version": "v2",
                "token_decimals": 18,
                "stablecoin_decimals": 18,
            }
        ]
        cache = tmp_path / "pools.json"
        import json

        cache.write_text(json.dumps(pools), encoding="utf-8")

        alt = MagicMock()
        alt.maps_loaded = True
        alt.refresh_maps = AsyncMock(return_value={})
        alt.resolve_listings = MagicMock(
            return_value=[{"exchange": "bitget", "coin": "TOK", "match": "contract"}]
        )
        alt.get_usdt_price = MagicMock(return_value=Decimal("0.90"))
        alt.get_orderbook_mid = AsyncMock(return_value=(None, None, None))

        price = MagicMock()
        price.refresh_if_expired = AsyncMock(return_value=False)
        price.get_price = MagicMock(return_value=Decimal("1.00"))

        writer = MagicMock()
        writer.write_signal = AsyncMock()

        probe = AltCexProbe(
            alt_client=alt,
            price_service=price,
            signal_writer=writer,
            adapter_factory=None,
            pools_cache_path=cache,
        )
        # Disable DEX quotes + OKX for this unit test.
        from config import settings as settings_mod

        old_dex = settings_mod.settings.ALT_CEX_MAX_DEX_QUOTES_PER_CYCLE
        old_okx = settings_mod.settings.ALT_CEX_ENABLE_OKX_DEX
        old_en = settings_mod.settings.ALT_CEX_PROBE_ENABLED
        settings_mod.settings.ALT_CEX_MAX_DEX_QUOTES_PER_CYCLE = 0
        settings_mod.settings.ALT_CEX_ENABLE_OKX_DEX = False
        settings_mod.settings.ALT_CEX_PROBE_ENABLED = True
        settings_mod.settings.ALT_CEX_MIN_PROFIT_PCT = Decimal("0.5")
        try:
            stats = await probe.run_once()
        finally:
            settings_mod.settings.ALT_CEX_MAX_DEX_QUOTES_PER_CYCLE = old_dex
            settings_mod.settings.ALT_CEX_ENABLE_OKX_DEX = old_okx
            settings_mod.settings.ALT_CEX_PROBE_ENABLED = old_en

        assert stats["mexc_signals"] >= 1
        writer.write_signal.assert_awaited()
        sig = writer.write_signal.await_args.args[0]
        assert sig.direction == "ALT_CEX_BUY_MEXC_SELL"
        assert "no_execution" in sig.warnings
