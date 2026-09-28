"""
Uniswap V4 ABI stubs (PoolManager / Quoter).

Full quoting needs PoolKey metadata; V4Adapter is soft-fail until then.
"""

from dex.v4_adapter import UNISWAP_V4_QUOTER_ABI

UNISWAP_V4_POOL_MANAGER_ABI: list[dict] = []

__all__ = ["UNISWAP_V4_POOL_MANAGER_ABI", "UNISWAP_V4_QUOTER_ABI"]
