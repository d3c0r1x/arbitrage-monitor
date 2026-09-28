"""
Uniswap V3 ABI fragments.

Includes:
- QuoterV2: quoteExactInputSingle
- Pool: token0, token1, fee, liquidity
"""

UNISWAP_V3_QUOTER_V2_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {
                        "internalType": "uint160",
                        "name": "sqrtPriceLimitX96",
                        "type": "uint160",
                    },
                ],
                "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            },
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
            {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

UNISWAP_V3_POOL_ABI = [
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "fee",
        "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "liquidity",
        "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function",
    },
]
