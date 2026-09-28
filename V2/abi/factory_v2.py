"""
V2 Factory ABI fragment.

Used for on-chain pool discovery:
factory.getPair(token0, token1) -> pool address
"""

FACTORY_V2_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
        ],
        "name": "getPair",
        "outputs": [
            {"internalType": "address", "name": "pair", "type": "address"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "allPairsLength",
        "outputs": [
            {"internalType": "uint256", "name": "", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]
