"""DexScreener label → version / dex_id normalization."""

from discovery.dexscreener_source import DexScreenerSource


def test_version_from_labels_v2():
    assert DexScreenerSource._version_from_labels(["v2"]) == "v2"
    assert DexScreenerSource._version_from_labels(["clmm", "v3"]) == "v3"
    assert DexScreenerSource._version_from_labels(None) is None


def test_normalize_pancakeswap_v2():
    assert (
        DexScreenerSource._normalize_dex_id("pancakeswap", "v2")
        == "pancakeswap_v2"
    )
    assert DexScreenerSource._normalize_dex_id("uniswap_v3", "v3") == "uniswap_v3"
