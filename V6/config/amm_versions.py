"""Supported AMM pool versions — first-class across every network.

Version is a property of the *pool ABI*, not of a chain. Any active
network may discover v1/v2/v3/v4 pools; adapters must resolve by version
(+ optional DEX style), with network-agnostic fallbacks when the registry
has no matching dex_id.
"""

from __future__ import annotations

SUPPORTED_POOL_VERSIONS: frozenset[str] = frozenset({"v1", "v2", "v3", "v4"})

# How a v1 pool quotes on-chain.
# - solidly: pair.getAmountOut(amountIn, tokenIn)  (Aerodrome/Thena/Ramses/…)
# - uniswap_v2: Uniswap-V2 clone (Pancake V1) → router/reserves via V2Adapter
V1_STYLES: frozenset[str] = frozenset({"solidly", "uniswap_v2", "v2", "pancake"})


def normalize_pool_version(raw: str | None) -> str | None:
    """Return canonical version string or None."""
    if not raw:
        return None
    v = str(raw).strip().lower()
    if v.startswith("v") and v in SUPPORTED_POOL_VERSIONS:
        return v
    # DexScreener sometimes sends "1"/"2"/"3"/"4"
    if v in ("1", "2", "3", "4"):
        return f"v{v}"
    return None
