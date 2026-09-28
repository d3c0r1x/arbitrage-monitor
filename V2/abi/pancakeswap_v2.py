"""
PancakeSwap V2 ABI fragments.

PancakeSwap V2 uses the same interface as Uniswap V2.
Reuses the Uniswap V2 ABI fragments.
"""

from abi.uniswap_v2 import UNISWAP_V2_PAIR_ABI, UNISWAP_V2_ROUTER_ABI

PANCAKESWAP_V2_ROUTER_ABI = UNISWAP_V2_ROUTER_ABI
PANCAKESWAP_V2_PAIR_ABI = UNISWAP_V2_PAIR_ABI
