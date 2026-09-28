"""
V3 Factory ABI fragment.

Used for on-chain pool discovery:
factory.getPool(tokenA, tokenB, fee) -> pool address
"""

FACTORY_V3_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [
            {"internalType": "address", "name": "pool", "type": "address"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]
