"""
DEX registry per network.

Addresses sourced from official documentation.
Addresses starting with TODO_VERIFY must be verified before production use.
DEX with TODO_ addresses are skipped at runtime.
"""

DEX_REGISTRY: dict[str, dict] = {
    "ETHEREUM": {
        "chain_id": 1,
        "dexes": [
            {
                "dex_id": "uniswap_v2",
                "version": "v2",
                "factory": "0x5C69bEE701ef814a2B6a3EDD4B1652CB9cc5aA6f",
                "router": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
                "default_fee_bps": 30,
                "verify": True,
            },
            {
                "dex_id": "sushiswap_v2",
                "version": "v2",
                "factory": "0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac",
                "router": "0xd9e1CE17f2641F24aE83637aB66a2CcA9C378B9F",
                "default_fee_bps": 30,
                "verify": True,
            },
            {
                "dex_id": "uniswap_v3",
                "version": "v3",
                "factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
                "quoter_v2": "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
                "swap_router02": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
                "fee_tiers_bps": [1, 5, 30, 100],
                "verify": True,
            },
            {
                "dex_id": "pancakeswap_v3",
                "version": "v3",
                "factory": "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
                "smart_router": "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",
                "quoter_v2": "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997",
                "nonfungible_position_manager": "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
                "fee_tiers_bps": [1, 5, 25, 100],
                "verify": True,
            },
            {
                "dex_id": "uniswap_v4",
                "version": "v4",
                "pool_manager": "0x000000000004444c5dc75cB358380D2e3dE08A90",
                "quoter": "0x52F0E24D1c21C8A0cB1e5a5dD6198556BD9E1203",
                "hooks_aware": True,
                "verify": True,
            },
        ],
    },
    "BSC": {
        "chain_id": 56,
        "dexes": [
            {
                "dex_id": "pancakeswap_v2",
                "version": "v2",
                "factory": "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
                "router": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
                "default_fee_bps": 25,
                "verify": True,
            },
            {
                "dex_id": "pancakeswap_v3",
                "version": "v3",
                "factory": "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
                "smart_router": "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",
                "quoter_v2": "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997",
                "nonfungible_position_manager": "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
                "fee_tiers_bps": [1, 5, 25, 100],
                "verify": True,
            },
            {
                "dex_id": "uniswap_v3",
                "version": "v3",
                "factory": "0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7",
                "quoter_v2": "0x78D78E420Da98ad378D7799bE8f4AF69033EB077",
                "swap_router02": "0xB971eF87ede563556b2ED4b1C0b0019111Dd85d2",
                "fee_tiers_bps": [1, 5, 30, 100],
                "verify": True,
            },
            {
                "dex_id": "sushiswap_v2",
                "version": "v2",
                "factory": "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
                "router": "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
                "default_fee_bps": 30,
                "verify": True,
            },
        ],
    },
    "POLYGON": {
        "chain_id": 137,
        "dexes": [
            {
                "dex_id": "uniswap_v3",
                "version": "v3",
                "factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
                "quoter_v2": "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
                "swap_router02": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
                "fee_tiers_bps": [1, 5, 30, 100],
                "verify": True,
            },
            {
                "dex_id": "sushiswap_v2",
                "version": "v2",
                "factory": "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
                "router": "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
                "default_fee_bps": 30,
                "verify": True,
            },
            {
                "dex_id": "quickswap_v3",
                "version": "v3",
                "factory": "0x411b0fAcC3489691f28ad58c47006AF5E3Ab3A28",
                "quoter_v2": "0xa062c2754864F67a259b346D0D7567b2ed406e6E",
                "verify": True,
            },
        ],
    },
    "ARBITRUM": {
        "chain_id": 42161,
        "dexes": [
            {
                "dex_id": "uniswap_v3",
                "version": "v3",
                "factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
                "quoter_v2": "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
                "swap_router02": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
                "fee_tiers_bps": [1, 5, 30, 100],
                "verify": True,
            },
            {
                "dex_id": "pancakeswap_v2",
                "version": "v2",
                "factory": "0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E",
                "router": "0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb",
                "default_fee_bps": 25,
                "verify": True,
            },
            {
                "dex_id": "pancakeswap_v3",
                "version": "v3",
                "factory": "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
                "smart_router": "0x32226588378236Fd0c7c4053999F88aC0e5cAc77",
                "quoter_v2": "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997",
                "nonfungible_position_manager": "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
                "fee_tiers_bps": [1, 5, 25, 100],
                "verify": True,
            },
            {
                "dex_id": "sushiswap_v2",
                "version": "v2",
                "factory": "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
                "router": "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
                "default_fee_bps": 30,
                "verify": True,
            },
            {
                "dex_id": "camelot_v2",
                "version": "v2",
                "factory": "0x6EcCab422D763aC031210895C81787E87B43A652",
                "router": "0xc873fEcbd354f5A56E00E710B90EF4201db2448d",
                "default_fee_bps": 30,
                "verify": True,
            },
        ],
    },
    "ROBINHOOD": {
        "chain_id": 4663,
        "dexes": [
            {
                "dex_id": "pancakeswap_v2",
                "version": "v2",
                "factory": "0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E",
                "router": "0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb",
                "default_fee_bps": 25,
                "verify": True,
            },
            {
                "dex_id": "pancakeswap_v3",
                "version": "v3",
                "factory": "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
                "smart_router": "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",
                "quoter_v2": "0x8553AA1615549A86882151784b329B017aA7c832",
                "nonfungible_position_manager": "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
                "fee_tiers_bps": [1, 5, 25, 100],
                "verify": True,
            },
        ],
    },
}
