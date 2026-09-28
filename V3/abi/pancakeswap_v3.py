"""
PancakeSwap V3 ABI fragments.

PancakeSwap V3 uses a compatible interface with Uniswap V3.
Reuses the Uniswap V3 ABI fragments.
"""

from abi.uniswap_v3 import UNISWAP_V3_POOL_ABI, UNISWAP_V3_QUOTER_V2_ABI

PANCAKESWAP_V3_QUOTER_V2_ABI = UNISWAP_V3_QUOTER_V2_ABI
PANCAKESWAP_V3_POOL_ABI = UNISWAP_V3_POOL_ABI
