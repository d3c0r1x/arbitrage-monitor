"""Tests: v1/v2/v3/v4 adapters are network-agnostic first-class versions."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from config.amm_versions import SUPPORTED_POOL_VERSIONS, normalize_pool_version
from dex.adapter_factory import AdapterFactory
from dex.v1_adapter import V1Adapter
from dex.v2_adapter import V2Adapter
from dex.v3_adapter import V3Adapter
from dex.v4_adapter import V4Adapter


class TestAmmVersions:
    def test_supported_set(self):
        assert SUPPORTED_POOL_VERSIONS == frozenset({"v1", "v2", "v3", "v4"})

    def test_normalize(self):
        assert normalize_pool_version("V1") == "v1"
        assert normalize_pool_version("3") == "v3"
        assert normalize_pool_version(None) is None


class TestAdapterFactoryVersionParity:
    def setup_method(self):
        self.factory = AdapterFactory(web3_factory=MagicMock())

    def test_v1_fallback_on_any_network_without_registry_match(self):
        # Unknown dex name + v1 → Solidly V1Adapter (not BASE-specific).
        ad = self.factory.get_adapter("ETHEREUM", "random_solidly_fork", pool_version="v1")
        assert isinstance(ad, V1Adapter)
        assert ad.version == "v1"

    def test_v1_on_bsc_thena(self):
        ad = self.factory.get_adapter("BSC", "thena", pool_version="v1")
        assert isinstance(ad, V1Adapter)

    def test_v1_pancake_uses_v2_style_adapter(self):
        ad = self.factory.get_adapter("BSC", "pancakeswap", pool_version="v1")
        assert isinstance(ad, V2Adapter)

    def test_v2_fallback(self):
        ad = self.factory.get_adapter("BASE", "unknown_dex", pool_version="v2")
        assert isinstance(ad, V2Adapter)

    def test_v3_uses_registry_quoter(self):
        ad = self.factory.get_adapter("BASE", "uniswap", pool_version="v3")
        assert isinstance(ad, V3Adapter)

    def test_v4_wired_not_none(self):
        ad = self.factory.get_adapter("BASE", "uniswap", pool_version="v4")
        assert isinstance(ad, V4Adapter)
        assert ad.version == "v4"

    def test_v4_on_ethereum(self):
        ad = self.factory.get_adapter("ETHEREUM", "uniswap_v4", pool_version="v4")
        assert isinstance(ad, V4Adapter)

    @pytest.mark.asyncio
    async def test_v4_soft_zero(self):
        ad = V4Adapter(web3_factory=MagicMock())
        out = await ad.quote_exact_input(
            "BASE", "0xabc", "0x1", "0x2", 10**18
        )
        assert out == Decimal("0")

    def test_each_active_style_network_has_v1_path(self):
        # Even ROBINHOOD (no Solidly registry) gets V1 via fallback.
        for net in ("ETHEREUM", "BSC", "POLYGON", "ARBITRUM", "BASE", "ROBINHOOD"):
            ad = self.factory.get_adapter(net, "solidly", pool_version="v1")
            assert isinstance(ad, V1Adapter), net
