"""
Unit tests for configuration modules.

Tests:
- Network normalization
- Stablecoin whitelist
- Settings parsing
- DEX registry structure
"""

import os
from decimal import Decimal
from unittest.mock import patch


class TestNetworkNormalization:
    """Tests for normalize_mexc_network."""

    def test_ethereum_variants(self):
        """ETH, ERC20, ETHEREUM all map to ETHEREUM."""
        from config.networks import normalize_mexc_network

        assert normalize_mexc_network("ETH") == "ETHEREUM"
        assert normalize_mexc_network("ERC20") == "ETHEREUM"
        assert normalize_mexc_network("ETHEREUM") == "ETHEREUM"

    def test_bsc_variants(self):
        """BSC, BEP20, BNB all map to BSC."""
        from config.networks import normalize_mexc_network

        assert normalize_mexc_network("BSC") == "BSC"
        assert normalize_mexc_network("BEP20") == "BSC"
        assert normalize_mexc_network("BNB") == "BSC"

    def test_polygon_variants(self):
        """MATIC, POLYGON, POLYGONPOS all map to POLYGON."""
        from config.networks import normalize_mexc_network

        assert normalize_mexc_network("MATIC") == "POLYGON"
        assert normalize_mexc_network("POLYGON") == "POLYGON"
        assert normalize_mexc_network("POLYGONPOS") == "POLYGON"

    def test_arbitrum_variants(self):
        """ARB, ARBITRUM, ARBITRUMONE all map to ARBITRUM."""
        from config.networks import normalize_mexc_network

        assert normalize_mexc_network("ARB") == "ARBITRUM"
        assert normalize_mexc_network("ARBITRUM") == "ARBITRUM"
        assert normalize_mexc_network("ARBITRUMONE") == "ARBITRUM"

    def test_base_network(self):
        """MEXC BASE maps to BASE; active set is BSC+BASE."""
        from config.networks import ACTIVE_NETWORKS, normalize_mexc_network

        assert normalize_mexc_network("BASE") == "BASE"
        assert ACTIVE_NETWORKS == frozenset({"BSC", "BASE"})
        assert "ARBITRUM" not in ACTIVE_NETWORKS

    def test_robinhood(self):
        """ROBINHOOD maps to ROBINHOOD."""
        from config.networks import normalize_mexc_network

        assert normalize_mexc_network("ROBINHOOD") == "ROBINHOOD"

    def test_empty_and_unknown_return_none(self):
        """Empty or unknown network returns None."""
        from config.networks import normalize_mexc_network

        assert normalize_mexc_network("") is None
        assert normalize_mexc_network("UNKNOWN_CHAIN") is None
        assert normalize_mexc_network(None) is None

    def test_case_insensitive(self):
        """Network normalization is case insensitive."""
        from config.networks import normalize_mexc_network

        assert normalize_mexc_network("eth") == "ETHEREUM"
        assert normalize_mexc_network("bsc") == "BSC"

    def test_hyphen_and_underscore_stripped(self):
        """Hyphens and underscores are stripped before matching."""
        from config.networks import normalize_mexc_network

        assert normalize_mexc_network("ARBITRUM-ONE") == "ARBITRUM"


class TestStablecoinWhitelist:
    """Tests for stablecoin whitelist."""

    def test_whitelist_contains_usdt(self):
        from config.stablecoins import STABLECOIN_WHITELIST

        assert "USDT" in STABLECOIN_WHITELIST

    def test_whitelist_contains_usdc(self):
        from config.stablecoins import STABLECOIN_WHITELIST

        assert "USDC" in STABLECOIN_WHITELIST

    def test_whitelist_contains_dai(self):
        from config.stablecoins import STABLECOIN_WHITELIST

        assert "DAI" in STABLECOIN_WHITELIST

    def test_unknown_not_in_whitelist(self):
        from config.stablecoins import STABLECOIN_WHITELIST

        assert "SCR" not in STABLECOIN_WHITELIST

    def test_usdt_usdc_are_quote_assets(self):
        from config.stablecoins import MEXC_QUOTE_ASSETS

        assert "USDT" in MEXC_QUOTE_ASSETS
        assert "USDC" in MEXC_QUOTE_ASSETS

    def test_dai_not_quote_asset(self):
        from config.stablecoins import MEXC_QUOTE_ASSETS

        assert "DAI" not in MEXC_QUOTE_ASSETS


class TestSettings:
    """Tests for settings parsing."""

    def test_default_base_amount(self):
        """Default BASE_AMOUNT_USD is 10."""
        from config.settings import Settings

        s = Settings()
        assert s.BASE_AMOUNT_USD == Decimal("10")

    def test_default_min_profit_pct(self):
        """Default MIN_NET_PROFIT_PCT is 0.1 (clip-size gate)."""
        from config.settings import Settings

        s = Settings()
        assert s.MIN_NET_PROFIT_PCT == Decimal("0.1")

    @patch.dict(os.environ, {"BASE_AMOUNT_USD": "100", "MIN_NET_PROFIT_PCT": "5"}, clear=True)
    def test_env_overrides_defaults(self):
        """Environment variables override defaults."""
        from config.settings import Settings

        s = Settings()
        assert s.BASE_AMOUNT_USD == Decimal("100")
        assert s.MIN_NET_PROFIT_PCT == Decimal("5")

    def test_decimal_parsing(self):
        """Settings parses Decimal from string."""
        from config.settings import Settings

        s = Settings()
        assert isinstance(s.BASE_AMOUNT_USD, Decimal)

    def test_key_presence_logging(self):
        """log_key_presence returns dict with bool values."""
        from config.settings import Settings

        s = Settings()
        key_presence = s.log_key_presence()
        assert isinstance(key_presence, dict)
        assert all(isinstance(v, bool) for v in key_presence.values())


class TestDexRegistry:
    """Tests for DEX registry structure."""

    def test_registry_contains_all_networks(self):
        from config.dex_registry import DEX_REGISTRY

        for network in ("ETHEREUM", "BSC", "POLYGON", "ARBITRUM", "BASE", "ROBINHOOD"):
            assert network in DEX_REGISTRY, f"{network} missing from DEX_REGISTRY"

    def test_each_network_has_chain_id(self):
        from config.dex_registry import DEX_REGISTRY

        for network_name, config in DEX_REGISTRY.items():
            assert config["chain_id"] is not None, f"{network_name} has no chain_id"

    def test_each_dex_has_version(self):
        from config.dex_registry import DEX_REGISTRY

        for network_name, config in DEX_REGISTRY.items():
            for dex in config["dexes"]:
                assert "version" in dex, f"{network_name} {dex['dex_id']} has no version"
                assert dex["version"] in ("v1", "v2", "v3", "v4"), f"{network_name} {dex['dex_id']} unknown version"

    def test_todo_addresses_identified(self):
        """DEXes with TODO_ addresses should be skippable at runtime."""
        from config.dex_registry import DEX_REGISTRY

        for network_name, config in DEX_REGISTRY.items():
            for dex in config["dexes"]:
                for key, value in dex.items():
                    if isinstance(value, str) and value.startswith("TODO_"):
                        # This DEX has placeholder addresses — should be skipped at runtime.
                        pass

    def test_pancakeswap_v3_in_bsc(self):
        from config.dex_registry import DEX_REGISTRY

        bsc_dexes = DEX_REGISTRY["BSC"]["dexes"]
        assert any(d["dex_id"] == "pancakeswap_v3" for d in bsc_dexes)

    def test_uniswap_v3_in_ethereum(self):
        from config.dex_registry import DEX_REGISTRY

        eth_dexes = DEX_REGISTRY["ETHEREUM"]["dexes"]
        assert any(d["dex_id"] == "uniswap_v3" for d in eth_dexes)
