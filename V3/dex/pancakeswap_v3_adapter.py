"""
PancakeSwap V3 adapter.

Extends V3Adapter with PancakeSwap-specific configuration.
Uses compatible V3 ABI interface.
"""

from dex.v3_adapter import V3Adapter


class PancakeSwapV3Adapter(V3Adapter):
    """Adapter for PancakeSwap V3 pools.

    Uses compatible V3 ABI with PancakeSwap V3 quoter.
    Fee tiers: 1, 5, 25, 100 bps.
    """

    version = "v3"
