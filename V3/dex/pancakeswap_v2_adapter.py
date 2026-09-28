"""
PancakeSwap V2 adapter.

Extends V2Adapter with PancakeSwap-specific configuration.
Uses the same V2 ABI interface as Uniswap V2.
"""

from dex.v2_adapter import V2Adapter


class PancakeSwapV2Adapter(V2Adapter):
    """Adapter for PancakeSwap V2 pools.

    PancakeSwap V2 uses the same ABI as Uniswap V2.
    Default fee is 25 bps (0.25%).
    """

    version = "v2"
