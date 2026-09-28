Ниже — расширенный план разработки и детальное описание готового проекта: с логикой по каждому параметру, фрагментами кода, адресами DEX, сетевыми конфигами, формулами расчета, хранением, backup, security-проверками и scanner-loop.

---

# 1. Описание проекта

## Название

MEXC × DEX Arbitrage Monitor.

## Цель

Искать арбитражные возможности между MEXC и DEX-пулами на сетях:

```text
Ethereum
BSC
Polygon
Arbitrum
Robinhood
```

Бот работает только как мониторинг:

```text
MEXC token list
  → DEX pool discovery
  → on-chain swap simulation
  → fee calculation
  → net profit calculation
  → security warnings
  → signal output
```

Автоматическое исполнение сделок не входит в базовый режим.

---

# 2. Ключевые принципы

## 1. Адреса, а не тикеры

Поиск пулов только по адресу токена из MEXC.

Правильно:

```text
MEXC contract address → DexScreener token pools
MEXC contract address → GeckoTerminal token pools
```

Неправильно:

```text
SEARCH "TOKEN/USDT"
SEARCH "TOKENUSDT" on DEX
```

## 2. MEXC — источник цены

Цена токена берется только с MEXC.

Для DEX цена не берется из API. DEX-цена получается только через on-chain swap simulation.

## 3. DexScreener и GeckoTerminal — только discovery

От них берутся только:

```text
pool address
network
token0 address
token1 address
dex id
```

Не используются:

```text
priceUsd
liquidity
volume
txns
pairCreatedAt
```

## 4. Никаких фильтров по ликвидности, объему, возрасту

Запрещено фильтровать пулы по:

```text
liquidity_usd
volume_24h
pool_age
tx_count
```

## 5. Security-проверки после расчета

Security-проверки выполняются только если пул уже показал прибыль.

Если security-проверка дает warning:

```text
signal is not removed
warning is attached
```

## 6. Полный цикл

Прибыль считается как полный round-trip:

```text
wallet → DEX → MEXC → wallet
wallet → MEXC → DEX → wallet
```

---

# 3. Выбор языка

## Язык: Python

Причины:

1. Основная нагрузка — IO:
   - HTTP;
   - RPC;
   - SQLite;
   - caching.

2. Нужно быстро итерировать:
   - добавлять DEX;
   - менять ABI;
   - менять fee logic;
   - добавлять security checks.

3. RPC rate limit — основное узкое место.
   Rust не решит проблему лимитов Alchemy или DexScreener.

Рекомендуемый стек:

```text
Python 3.12+
asyncio
httpx
web3.py
aiosqlite
pydantic
tenacity
structlog
decimal.Decimal
```

---

# 4. Архитектура проекта

```text
mexc_dex_arb/
│
├── config/
│   ├── settings.py
│   ├── networks.py
│   ├── dex_registry.py
│   ├── stablecoins.py
│   ├── mexc_config.py
│   └── rate_limits.py
│
├── abi/
│   ├── erc20.py
│   ├── uniswap_v2.py
│   ├── uniswap_v3.py
│   ├── uniswap_v4.py
│   ├── pancakeswap_v2.py
│   ├── pancakeswap_v3.py
│   └── multicall.py
│
├── clients/
│   ├── http_client.py
│   ├── mexc_client.py
│   ├── dexscreener_client.py
│   ├── geckoterminal_client.py
│   └── rpc_client.py
│
├── models/
│   ├── mexc_models.py
│   ├── pool_models.py
│   ├── quote_models.py
│   ├── fee_models.py
│   └── signal_models.py
│
├── storage/
│   ├── database.py
│   ├── repository.py
│   └── backup_manager.py
│
├── services/
│   ├── mexc_asset_service.py
│   ├── stablecoin_registry_service.py
│   ├── pool_discovery_service.py
│   ├── price_service.py
│   ├── fee_service.py
│   ├── quote_service.py
│   ├── profit_calculator.py
│   └── signal_service.py
│
├── dex/
│   ├── base_adapter.py
│   ├── pool_detector.py
│   ├── v2_adapter.py
│   ├── v3_adapter.py
│   ├── v4_adapter.py
│   ├── pancakeswap_v2_adapter.py
│   ├── pancakeswap_v3_adapter.py
│   └── adapter_factory.py
│
├── security/
│   ├── token_security_checker.py
│   ├── pool_security_checker.py
│   └── warning_builder.py
│
├── scanner/
│   ├── scanner.py
│   ├── pool_refresh_task.py
│   └── signal_writer.py
│
├── utils/
│   ├── decimal_utils.py
│   ├── address_utils.py
│   ├── time_utils.py
│   └── logging.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── main.py
└── .env
```

---

# 5. Конфигурация сетей

## 5.1. Robinhood Chain

Robinhood Chain — EVM-совместимая сеть.

Chain ID mainnet:

```text
4663
```

Public RPC:

```text
https://rpc.mainnet.chain.robinhood.com
```

Эти данные указаны в документации Robinhood Chain. [[39]]

L2 Multicall в Robinhood Chain:

```text
0x2cAC2D899eCC914d704FeaAE33ac1bF36277DaD1
```

Этот адрес указан в официальных Robinhood Chain contracts. [[81]]

---

## 5.2. networks.py

```python
# config/networks.py

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NetworkConfig:
    internal_name: str
    chain_id: int | None
    mexc_network_names: tuple[str, ...]
    native_token: str
    rpc_url_env: str
    block_explorer: str
    multicall3: str | None = None
    l2_multicall: str | None = None
    is_evm: bool = True
    notes: str = ""


NETWORKS: dict[str, NetworkConfig] = {
    "ETHEREUM": NetworkConfig(
        internal_name="ETHEREUM",
        chain_id=1,
        mexc_network_names=("ETH", "ERC20", "ETHEREUM"),
        native_token="ETH",
        rpc_url_env="ETH_RPC_URL",
        block_explorer="https://etherscan.io",
        multicall3="0xcA11bde05977b3631167028862bE2a173976CA11",
        notes="Multicall3 canonical address.",
    ),

    "BSC": NetworkConfig(
        internal_name="BSC",
        chain_id=56,
        mexc_network_names=("BSC", "BEP20", "BNB", "BNBCHAIN"),
        native_token="BNB",
        rpc_url_env="BSC_RPC_URL",
        block_explorer="https://bscscan.com",
        multicall3="0xcA11bde05977b3631167028862bE2a173976CA11",
    ),

    "POLYGON": NetworkConfig(
        internal_name="POLYGON",
        chain_id=137,
        mexc_network_names=("MATIC", "POLYGON", "POLYGONPOS"),
        native_token="POL",
        rpc_url_env="POLYGON_RPC_URL",
        block_explorer="https://polygonscan.com",
        multicall3="0xcA11bde05977b3631167028862bE2a173976CA11",
    ),

    "ARBITRUM": NetworkConfig(
        internal_name="ARBITRUM",
        chain_id=42161,
        mexc_network_names=("ARB", "ARBITRUM", "ARBITRUMONE"),
        native_token="ETH",
        rpc_url_env="ARBITRUM_RPC_URL",
        block_explorer="https://arbiscan.io",
        multicall3="0xcA11bde05977b3631167028862bE2a173976CA11",
    ),

    "ROBINHOOD": NetworkConfig(
        internal_name="ROBINHOOD",
        chain_id=4663,
        mexc_network_names=("ROBINHOOD", "ROBINHOODCHAIN"),
        native_token="ETH",
        rpc_url_env="ROBINHOOD_RPC_URL",
        block_explorer="https://robinhoodchain.blockscout.com",
        multicall3=None,
        l2_multicall="0x2cAC2D899eCC914d704FeaAE33ac1bF36277DaD1",
        notes="Robinhood Chain mainnet chain id is 4663.",
    ),
}


def normalize_mexc_network(raw_network: str) -> str | None:
    """
    MEXC may return network names in different formats.
    Example:
      ETH
      ERC20
      BSC
      BEP20
      MATIC
      POLYGON
      ARB
      ARBITRUM
      ROBINHOOD
    """

    if not raw_network:
        return None

    normalized = raw_network.strip().upper().replace("-", "").replace("_", "")

    for internal_name, network in NETWORKS.items():
        if normalized in network.mexc_network_names:
            return internal_name

    return None
```

---

# 6. MEXC конфигурация

## 6.1. Endpoints

```python
# config/mexc_config.py

MEXC_BASE_URL = "https://api.mexc.com"

MEXC_ENDPOINTS = {
    # Coins, networks, contracts, deposit/withdraw, fees.
    "capital_config_getall": "/api/v3/capital/config/getall",

    # Spot symbols and trading rules.
    "exchange_info": "/api/v3/exchangeInfo",

    # Tradable symbols available for API trading.
    "default_symbols": "/api/v3/defaultSymbols",

    # Latest price for one symbol or all symbols if symbol is omitted.
    "ticker_price_all": "/api/v3/ticker/price",
}

MEXC_QUOTE_ASSETS = ("USDT", "USDC")

MEXC_CAPITAL_CONFIG_REFRESH_SEC = 3600
MEXC_EXCHANGE_INFO_REFRESH_SEC = 3600
MEXC_PRICE_CACHE_TTL_SEC = 10

# Default spot taker fee if account fee is unavailable.
MEXC_DEFAULT_TAKER_FEE_BPS = 10
```

---

## 6.2. MEXC models

```python
# models/mexc_models.py

from decimal import Decimal
from pydantic import BaseModel, Field


class MexcNetworkAsset(BaseModel):
    coin: str
    name: str | None
    network_raw: str
    network_normalized: str
    contract_address: str
    deposit_enable: bool
    withdraw_enable: bool
    withdraw_fee: Decimal | None
    withdraw_min: Decimal | None
    withdraw_max: Decimal | None
    min_confirm: int | None


class MexcAsset(BaseModel):
    coin: str
    name: str | None
    networks: list[MexcNetworkAsset] = Field(default_factory=list)

    def active_networks(self) -> list[MexcNetworkAsset]:
        return [
            network
            for network in self.networks
            if network.deposit_enable and network.withdraw_enable
        ]


class MexcPrice(BaseModel):
    symbol: str
    base_asset: str
    quote_asset: str
    price: Decimal
    updated_at: float
```

---

## 6.3. MEXC client fragment

```python
# clients/mexc_client.py

import httpx
from decimal import Decimal

from config.mexc_config import MEXC_BASE_URL, MEXC_ENDPOINTS
from models.mexc_models import MexcAsset, MexcNetworkAsset
from config.networks import normalize_mexc_network


class MexcClient:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self._base_url = MEXC_BASE_URL

    async def get_capital_config(self) -> list[dict]:
        url = self._base_url + MEXC_ENDPOINTS["capital_config_getall"]
        response = await self._client.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    async def get_all_prices(self) -> list[dict]:
        """
        Returns all latest prices if symbol is not provided.
        """
        url = self._base_url + MEXC_ENDPOINTS["ticker_price_all"]
        response = await self._client.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    def parse_capital_config(self, raw_items: list[dict]) -> list[MexcAsset]:
        assets: list[MexcAsset] = []

        for item in raw_items:
            coin = str(item.get("coin", "")).strip()
            name = item.get("name")

            if not coin:
                continue

            asset = MexcAsset(coin=coin, name=name)

            for network_item in item.get("networkList", []):
                raw_network = str(network_item.get("network", "")).strip()
                normalized_network = normalize_mexc_network(raw_network)

                if normalized_network is None:
                    continue

                contract_address = str(network_item.get("contract", "")).strip()

                # IMPORTANT:
                # MEXC uses field "contract", not "contractAddress".
                if not contract_address:
                    continue

                network_asset = MexcNetworkAsset(
                    coin=coin,
                    name=name,
                    network_raw=raw_network,
                    network_normalized=normalized_network,
                    contract_address=contract_address.lower(),
                    deposit_enable=bool(network_item.get("depositEnable", False)),
                    withdraw_enable=bool(network_item.get("withdrawEnable", False)),
                    withdraw_fee=self._parse_decimal(network_item.get("withdrawFee")),
                    withdraw_min=self._parse_decimal(network_item.get("withdrawMin")),
                    withdraw_max=self._parse_decimal(network_item.get("withdrawMax")),
                    min_confirm=network_item.get("minConfirm"),
                )

                asset.networks.append(network_asset)

            if asset.networks:
                assets.append(asset)

        return assets

    @staticmethod
    def _parse_decimal(value) -> Decimal | None:
        if value is None:
            return None

        try:
            return Decimal(str(value))
        except Exception:
            return None
```

---

# 7. Stablecoin whitelist

## 7.1. Логика

1. Берем список всех монет из MEXC.
2. Отбираем только монеты из strict whitelist.
3. Для каждой whitelist-монеты собираем contract addresses по сетям.
4. Используем только адреса.

Если монета не в whitelist — она не стейбл, даже если цена около 1 USD.

---

## 7.2. stablecoins.py

```python
# config/stablecoins.py

STABLECOIN_WHITELIST = frozenset(
    {
        "USDT",
        "USDC",
        "DAI",
        "FDUSD",
        "USDe",
        "PYUSD",
        "TUSD",
        "GUSD",
        "FRAX",
        "GHO",
        "LUSD",
        "crvUSD",
        "DOLA",
        "USDP",
        "MIM",
        "sUSD",
    }
)

# MEXC quote assets used for token pricing.
# Signals for USDT and USDC are calculated separately.
MEXC_QUOTE_ASSETS = frozenset(
    {
        "USDT",
        "USDC",
    }
)
```

---

## 7.3. Stablecoin registry builder

```python
# services/stablecoin_registry_service.py

from dataclasses import dataclass
from decimal import Decimal

from config.stablecoins import STABLECOIN_WHITELIST
from models.mexc_models import MexcAsset


@dataclass(frozen=True)
class StablecoinRecord:
    coin: str
    network: str
    address: str
    deposit_enable: bool
    withdraw_enable: bool
    withdraw_fee: Decimal | None


class StablecoinRegistryService:
    def build_registry(
        self,
        assets: list[MexcAsset],
    ) -> dict[str, list[StablecoinRecord]]:
        """
        Returns:
            {
                "ETHEREUM": [StablecoinRecord, ...],
                "BSC": [StablecoinRecord, ...],
            }
        """

        registry: dict[str, list[StablecoinRecord]] = {}

        for asset in assets:
            if asset.coin not in STABLECOIN_WHITELIST:
                continue

            for network_asset in asset.networks:
                record = StablecoinRecord(
                    coin=asset.coin,
                    network=network_asset.network_normalized,
                    address=network_asset.contract_address.lower(),
                    deposit_enable=network_asset.deposit_enable,
                    withdraw_enable=network_asset.withdraw_enable,
                    withdraw_fee=network_asset.withdraw_fee,
                )

                registry.setdefault(record.network, [])
                registry[record.network].append(record)

        return registry

    def stablecoin_addresses_for_network(
        self,
        registry: dict[str, list[StablecoinRecord]],
        network: str,
    ) -> set[str]:
        return {
            record.address.lower()
            for record in registry.get(network, [])
        }
```

---

# 8. DEX registry

Ниже — стартовый реестр DEX по сетям.

Использованы официальные адреса PancakeSwap и Uniswap.

PancakeSwap V3 Smart Router для BSC, ETH и Robinhood имеет адрес:

```text
0x13f4EA83D0bd40E75C8222255bc855a974568Dd4
```

Это указано в официальных PancakeSwap V3 addresses. [[1]]

PancakeSwap V2 Router для BSC:

```text
0x10ED43C718714eb63d5aA57B78B54704E256024E
```

Это указано в официальных PancakeSwap V2 addresses. [[8]]

Uniswap V3 QuoterV2 на BNB Smart Chain:

```text
0x78D78E420Da98ad378D7799bE8f4AF69033EB077
```

Это указано в Uniswap V3 BNB deployments. [[71]]

Uniswap V3 QuoterV2 на Arbitrum:

```text
0x61fFE014bA17989E743c5F6cB21bF9697530B21e
```

Это указано в Uniswap V3 Arbitrum deployments. [[45]]

Uniswap V3 QuoterV2 на Polygon:

```text
0x61fFE014bA17989E743c5F6cB21bF9697530B21e
```

Это указано в Uniswap V3 Polygon deployments. [[56]]

---

## 8.1. dex_registry.py

```python
# config/dex_registry.py

DEX_REGISTRY = {
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
                "pool_manager": "TODO_UNISWAP_V4_POOL_MANAGER_ETH",
                "quoter": "TODO_UNISWAP_V4_QUOTER_ETH",
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
                "factory": "TODO_VERIFY_SUSHI_FACTORY_BSC",
                "router": "0x1b02da8cb0d097eb8d57a175b88c7d8b47997506",
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
                "factory": "TODO_VERIFY_SUSHI_FACTORY_POLYGON",
                "router": "0x1b02da8cb0d097eb8d57a175b88c7d8b47997506",
                "default_fee_bps": 30,
                "verify": True,
            },
            {
                "dex_id": "quickswap_v3",
                "version": "v3",
                "factory": "TODO_VERIFY_QUICKSWAP_V3_FACTORY_POLYGON",
                "quoter_v2": "TODO_VERIFY_QUICKSWAP_V3_QUOTER_POLYGON",
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
                "factory": "TODO_VERIFY_SUSHI_FACTORY_ARBITRUM",
                "router": "0xf2614A233c7C3e7f08b1F887Ba133a13f1eb2c55",
                "default_fee_bps": 30,
                "verify": True,
            },
            {
                "dex_id": "camelot_v2",
                "version": "v2",
                "factory": "TODO_VERIFY_CAMELOT_V2_FACTORY_ARBITRUM",
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
```

---

# 9. Rate limits

## 9.1. rate_limits.py

```python
# config/rate_limits.py

RATE_LIMITS = {
    "mexc": {
        "requests_per_minute": 120,
        "burst": 5,
        "retry_attempts": 3,
        "backoff_initial_sec": 1,
        "backoff_max_sec": 30,
    },

    "dexscreener": {
        "requests_per_minute": 300,
        "burst": 5,
        "retry_attempts": 3,
        "backoff_initial_sec": 2,
        "backoff_max_sec": 60,
    },

    "geckoterminal": {
        "requests_per_minute": 120,
        "burst": 5,
        "cache_ttl_sec": 60,
        "retry_attempts": 3,
        "backoff_initial_sec": 2,
        "backoff_max_sec": 60,
    },

    "rpc": {
        # If Alchemy plan is 2000 rpm, change to 2000.
        "requests_per_minute": 500,
        "burst": 8,
        "multicall_batch_size": 50,
        "retry_attempts": 3,
        "backoff_initial_sec": 1,
        "backoff_max_sec": 30,
    },
}
```

---

## 9.2. Semaphore factory

```python
# utils/rate_limiter.py

import asyncio
import time


class SimpleRateLimiter:
    def __init__(self, requests_per_minute: int, burst: int):
        self._rpm = requests_per_minute
        self._burst = burst
        self._semaphore = asyncio.Semaphore(burst)
        self._interval_sec = 60.0 / float(requests_per_minute)

    async def acquire(self):
        await self._semaphore.acquire()

        async def release_later():
            await asyncio.sleep(self._interval_sec)
            self._semaphore.release()

        asyncio.create_task(release_later())
```

---

# 10. Storage

## 10.1. Требования

1. Active DB:

```text
data/state/active.sqlite3
```

2. Temp DB:

```text
data/state/active.new.sqlite3
```

3. Backups:

```text
data/state/backups/active_YYYYMMDD_HHMMSS.sqlite3
```

4. Хранить последние 5 backup.

5. Backup старше 5 удалять.

---

## 10.2. SQLite schema

```python
# storage/database.py

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pools (
    network TEXT NOT NULL,
    token_address TEXT NOT NULL,
    stablecoin_address TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    dex TEXT NOT NULL,
    sources TEXT NOT NULL,
    pool_version TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    raw_json TEXT,
    PRIMARY KEY (network, pool_address)
);

CREATE INDEX IF NOT EXISTS idx_pools_token
ON pools (network, token_address);

CREATE INDEX IF NOT EXISTS idx_pools_stablecoin
ON pools (network, stablecoin_address);


CREATE TABLE IF NOT EXISTS mexc_assets (
    coin TEXT NOT NULL,
    name TEXT,
    network TEXT NOT NULL,
    contract_address TEXT NOT NULL,
    deposit_enable INTEGER NOT NULL,
    withdraw_enable INTEGER NOT NULL,
    withdraw_fee TEXT,
    withdraw_min TEXT,
    withdraw_max TEXT,
    min_confirm INTEGER,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (coin, network, contract_address)
);


CREATE TABLE IF NOT EXISTS stablecoins (
    coin TEXT NOT NULL,
    network TEXT NOT NULL,
    address TEXT NOT NULL,
    deposit_enable INTEGER NOT NULL,
    withdraw_enable INTEGER NOT NULL,
    withdraw_fee TEXT,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (network, address)
);


CREATE TABLE IF NOT EXISTS refresh_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    status TEXT NOT NULL,
    error TEXT,
    pools_count INTEGER,
    mexc_assets_count INTEGER,
    stablecoins_count INTEGER
);
"""
```

---

## 10.3. Backup manager

```python
# storage/backup_manager.py

import os
import shutil
import time
from pathlib import Path


class BackupManager:
    def __init__(
        self,
        active_db_path: str,
        backup_dir: str,
        max_backups: int = 5,
    ):
        self._active_db_path = Path(active_db_path)
        self._backup_dir = Path(backup_dir)
        self._max_backups = max_backups

        self._backup_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(self) -> Path | None:
        if not self._active_db_path.exists():
            return None

        timestamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        backup_path = self._backup_dir / f"active_{timestamp}.sqlite3"

        shutil.copy2(self._active_db_path, backup_path)

        self._cleanup_old_backups()

        return backup_path

    def _cleanup_old_backups(self) -> None:
        backups = sorted(
            self._backup_dir.glob("active_*.sqlite3"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

        for old_backup in backups[self._max_backups:]:
            old_backup.unlink(missing_ok=True)
```

---

## 10.4. Atomic refresh logic

```python
# storage/repository.py

import os
import sqlite3
from pathlib import Path


class StateRepository:
    def __init__(
        self,
        active_db_path: str,
        temp_db_path: str,
        backup_manager,
    ):
        self._active_db_path = Path(active_db_path)
        self._temp_db_path = Path(temp_db_path)
        self._backup_manager = backup_manager

    def atomic_replace(self) -> None:
        """
        Replace active DB only after temp DB validation.
        """

        if not self._temp_db_path.exists():
            raise RuntimeError("Temp database does not exist.")

        if not self._is_valid(self._temp_db_path):
            raise RuntimeError("Temp database validation failed.")

        self._backup_manager.create_backup()

        os.replace(self._temp_db_path, self._active_db_path)

    def _is_valid(self, db_path: Path) -> bool:
        try:
            connection = sqlite3.connect(db_path)
            cursor = connection.cursor()

            cursor.execute("SELECT COUNT(*) FROM pools;")
            pools_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM mexc_assets;")
            assets_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM stablecoins;")
            stablecoins_count = cursor.fetchone()[0]

            connection.close()

            return pools_count > 0 and assets_count > 0 and stablecoins_count > 0

        except Exception:
            return False
```

---

# 11. Pool discovery

## 11.1. Логика

Для каждого токена из MEXC:

```text
token_contract_address
  → DexScreener pools by token address
  → GeckoTerminal pools by token address
  → merge
  → deduplicate by network + pool_address
  → filter:
      one token == MEXC token address
      other token == stablecoin address from registry
```

---

## 11.2. Pool model

```python
# models/pool_models.py

from pydantic import BaseModel


class DiscoveredPool(BaseModel):
    network: str
    pool_address: str
    dex: str | None
    token0_address: str
    token1_address: str
    sources: set[str]

    class Config:
        arbitrary_types_allowed = True


class ValidPool(BaseModel):
    network: str
    token_address: str
    stablecoin_address: str
    pool_address: str
    dex: str | None
    sources: set[str]
    pool_version: str | None = None
```

---

## 11.3. DexScreener client fragment

```python
# clients/dexscreener_client.py

import httpx

from models.pool_models import DiscoveredPool


class DexScreenerClient:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self._base_url = "https://api.dexscreener.com/latest/dex"

    async def get_pools_by_token_address(
        self,
        network: str,
        token_address: str,
    ) -> list[DiscoveredPool]:
        """
        DexScreener is used only for pool discovery.
        Price, liquidity and volume fields must be ignored.
        """

        url = f"{self._base_url}/tokens/{token_address}"

        response = await self._client.get(url, timeout=30)
        response.raise_for_status()

        payload = response.json()

        pools: list[DiscoveredPool] = []

        for pair in payload.get("pairs", []):
            chain_id = str(pair.get("chainId", "")).lower()
            pair_address = str(pair.get("pairAddress", "")).lower()

            if not chain_id or not pair_address:
                continue

            base_token = pair.get("baseToken", {})
            quote_token = pair.get("quoteToken", {})

            base_address = str(base_token.get("address", "")).lower()
            quote_address = str(quote_token.get("address", "")).lower()

            if not base_address or not quote_address:
                continue

            pools.append(
                DiscoveredPool(
                    network=self._normalize_network(chain_id),
                    pool_address=pair_address,
                    dex=pair.get("dexId"),
                    token0_address=base_address,
                    token1_address=quote_address,
                    sources={"dexscreener"},
                )
            )

        return pools

    @staticmethod
    def _normalize_network(chain_id: str) -> str:
        mapping = {
            "ethereum": "ETHEREUM",
            "bsc": "BSC",
            "polygon": "POLYGON",
            "arbitrum": "ARBITRUM",
            "robinhood": "ROBINHOOD",
        }

        return mapping.get(chain_id, chain_id.upper())
```

---

## 11.4. GeckoTerminal client fragment

```python
# clients/geckoterminal_client.py

import httpx

from models.pool_models import DiscoveredPool


class GeckoTerminalClient:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self._base_url = "https://api.geckoterminal.com/api/v2"

    async def get_pools_by_token_address(
        self,
        network: str,
        token_address: str,
    ) -> list[DiscoveredPool]:
        gecko_network = self._normalize_network(network)

        url = f"{self._base_url}/networks/{gecko_network}/tokens/{token_address}/pools"

        response = await self._client.get(url, timeout=30)
        response.raise_for_status()

        payload = response.json()

        pools: list[DiscoveredPool] = []

        for pool in payload.get("data", []):
            attributes = pool.get("attributes", {})
            pool_address = str(attributes.get("address", "")).lower()

            if not pool_address:
                continue

            relationships = pool.get("relationships", {})

            base_token = self._extract_token_address(relationships, "base_token")
            quote_token = self._extract_token_address(relationships, "quote_token")

            if not base_token or not quote_token:
                continue

            pools.append(
                DiscoveredPool(
                    network=network,
                    pool_address=pool_address,
                    dex=attributes.get("dex_id"),
                    token0_address=base_token,
                    token1_address=quote_token,
                    sources={"geckoterminal"},
                )
            )

        return pools

    @staticmethod
    def _extract_token_address(relationships: dict, key: str) -> str | None:
        try:
            data = relationships[key]["data"]
            token_id = str(data.get("id", ""))
            return token_id.split("_")[-1].lower()
        except Exception:
            return None

    @staticmethod
    def _normalize_network(network: str) -> str:
        mapping = {
            "ETHEREUM": "eth",
            "BSC": "bsc",
            "POLYGON": "polygon_pos",
            "ARBITRUM": "arbitrum",
            "ROBINHOOD": "robinhood",
        }

        return mapping.get(network, network.lower())
```

---

## 11.5. Pool discovery service

```python
# services/pool_discovery_service.py

import asyncio

from models.pool_models import DiscoveredPool, ValidPool


class PoolDiscoveryService:
    def __init__(
        self,
        dexscreener_client,
        geckoterminal_client,
        stablecoin_registry_service,
    ):
        self._dexscreener = dexscreener_client
        self._gecko = geckoterminal_client
        self._stablecoins = stablecoin_registry_service

    async def discover_and_filter_pools(
        self,
        network: str,
        token_address: str,
        stablecoin_addresses: set[str],
    ) -> list[ValidPool]:

        dexscreener_task = self._dexscreener.get_pools_by_token_address(
            network=network,
            token_address=token_address,
        )

        gecko_task = self._gecko.get_pools_by_token_address(
            network=network,
            token_address=token_address,
        )

        results = await asyncio.gather(
            dexscreener_task,
            gecko_task,
            return_exceptions=True,
        )

        merged: dict[str, DiscoveredPool] = {}

        for result in results:
            if isinstance(result, Exception):
                continue

            for pool in result:
                key = pool.pool_address.lower()

                if key not in merged:
                    merged[key] = pool
                else:
                    merged[key].sources.update(pool.sources)

        valid_pools: list[ValidPool] = []

        for pool in merged.values():
            valid_pool = self._filter_pool(
                network=network,
                token_address=token_address,
                stablecoin_addresses=stablecoin_addresses,
                pool=pool,
            )

            if valid_pool is not None:
                valid_pools.append(valid_pool)

        return valid_pools

    def _filter_pool(
        self,
        network: str,
        token_address: str,
        stablecoin_addresses: set[str],
        pool: DiscoveredPool,
    ) -> ValidPool | None:

        token_address = token_address.lower()

        token0 = pool.token0_address.lower()
        token1 = pool.token1_address.lower()

        if token0 == token_address and token1 in stablecoin_addresses:
            return ValidPool(
                network=network,
                token_address=token_address,
                stablecoin_address=token1,
                pool_address=pool.pool_address.lower(),
                dex=pool.dex,
                sources=pool.sources,
            )

        if token1 == token_address and token0 in stablecoin_addresses:
            return ValidPool(
                network=network,
                token_address=token_address,
                stablecoin_address=token0,
                pool_address=pool.pool_address.lower(),
                dex=pool.dex,
                sources=pool.sources,
            )

        return None
```

---

# 12. RPC client

## 12.1. Требования

1. Async JSON-RPC.
2. Multicall batching.
3. Cache:
   - gas price;
   - decimals;
   - token0/token1;
   - pool fee;
   - reserves.

---

## 12.2. RPC client fragment

```python
# clients/rpc_client.py

import asyncio
import time
from decimal import Decimal

import httpx


class RpcClient:
    def __init__(
        self,
        rpc_url: str,
        rate_limiter,
    ):
        self._rpc_url = rpc_url
        self._rate_limiter = rate_limiter
        self._client = httpx.AsyncClient(timeout=30)
        self._request_id = 0

        self._gas_price_cache: dict[str, tuple[float, Decimal]] = {}
        self._gas_price_ttl_sec = 60

    async def _post(self, method: str, params: list) -> dict:
        await self._rate_limiter.acquire()

        self._request_id += 1

        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        response = await self._client.post(self._rpc_url, json=payload)
        response.raise_for_status()

        return response.json()

    async def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        result = await self._post(
            "eth_call",
            [
                {
                    "to": to,
                    "data": data,
                },
                block,
            ],
        )

        if "error" in result:
            raise RuntimeError(result["error"])

        return result["result"]

    async def get_gas_price(self, network: str) -> Decimal:
        now = time.time()

        cached = self._gas_price_cache.get(network)

        if cached is not None:
            cached_time, cached_price = cached

            if now - cached_time < self._gas_price_ttl_sec:
                return cached_price

        result = await self._post("eth_gasPrice", [])

        gas_price_wei = Decimal(int(result["result"], 16))

        self._gas_price_cache[network] = (now, gas_price_wei)

        return gas_price_wei
```

---

# 13. ABI fragments

## 13.1. ERC20

```python
# abi/erc20.py

ERC20_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]
```

---

## 13.2. Uniswap V2

```python
# abi/uniswap_v2.py

UNISWAP_V2_ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

UNISWAP_V2_PAIR_ABI = [
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
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
            {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
            {"internalType": "uint32", "name": "_blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]
```

---

## 13.3. Uniswap V3 QuoterV2

```python
# abi/uniswap_v3.py

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
            }
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
```

---

# 14. DEX adapters

## 14.1. Base adapter

```python
# dex/base_adapter.py

from abc import ABC, abstractmethod
from decimal import Decimal


class BaseDexAdapter(ABC):
    version: str = "base"

    @abstractmethod
    async def quote_exact_input(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
        amount_in: int,
    ) -> Decimal:
        """
        Returns raw amount_out from on-chain simulation.
        """

        raise NotImplementedError

    @abstractmethod
    async def get_pool_fee_bps(
        self,
        network: str,
        pool_address: str,
    ) -> Decimal | None:
        raise NotImplementedError
```

---

## 14.2. V2 adapter

```python
# dex/v2_adapter.py

from decimal import Decimal

from abi.uniswap_v2 import UNISWAP_V2_ROUTER_ABI, UNISWAP_V2_PAIR_ABI
from dex.base_adapter import BaseDexAdapter


class V2Adapter(BaseDexAdapter):
    version = "v2"

    def __init__(self, web3_factory, router_address: str, default_fee_bps: int):
        self._web3_factory = web3_factory
        self._router_address = router_address
        self._default_fee_bps = Decimal(default_fee_bps)

    async def quote_exact_input(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
        amount_in: int,
    ) -> Decimal:
        """
        Prefer router getAmountsOut.
        Fallback to reserve calculation.
        """

        w3 = self._web3_factory(network)

        router = w3.eth.contract(
            address=w3.to_checksum_address(self._router_address),
            abi=UNISWAP_V2_ROUTER_ABI,
        )

        path = [
            w3.to_checksum_address(token_in),
            w3.to_checksum_address(token_out),
        ]

        try:
            amounts = await router.functions.getAmountsOut(
                amount_in,
                path,
            ).call()

            return Decimal(amounts[-1])

        except Exception:
            return await self._quote_from_reserves(
                network=network,
                pool_address=pool_address,
                token_in=token_in,
                token_out=token_out,
                amount_in=Decimal(amount_in),
            )

    async def _quote_from_reserves(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
        amount_in: Decimal,
    ) -> Decimal:
        w3 = self._web3_factory(network)

        pair = w3.eth.contract(
            address=w3.to_checksum_address(pool_address),
            abi=UNISWAP_V2_PAIR_ABI,
        )

        token0 = await pair.functions.token0().call()
        reserves = await pair.functions.getReserves().call()

        reserve0 = Decimal(reserves[0])
        reserve1 = Decimal(reserves[1])

        if token0.lower() == token_in.lower():
            reserve_in = reserve0
            reserve_out = reserve1
        else:
            reserve_in = reserve1
            reserve_out = reserve0

        if reserve_in <= 0 or reserve_out <= 0:
            return Decimal("0")

        fee_bps = self._default_fee_bps

        amount_in_with_fee = amount_in * (Decimal(10000) - fee_bps)

        numerator = amount_in_with_fee * reserve_out
        denominator = reserve_in * Decimal(10000) + amount_in_with_fee

        return numerator / denominator

    async def get_pool_fee_bps(
        self,
        network: str,
        pool_address: str,
    ) -> Decimal | None:
        return self._default_fee_bps
```

---

## 14.3. V3 adapter

```python
# dex/v3_adapter.py

from decimal import Decimal

from abi.uniswap_v3 import UNISWAP_V3_QUOTER_V2_ABI, UNISWAP_V3_POOL_ABI
from dex.base_adapter import BaseDexAdapter


class V3Adapter(BaseDexAdapter):
    version = "v3"

    def __init__(self, web3_factory, quoter_v2_address: str):
        self._web3_factory = web3_factory
        self._quoter_v2_address = quoter_v2_address

    async def quote_exact_input(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
        amount_in: int,
    ) -> Decimal:
        w3 = self._web3_factory(network)

        pool = w3.eth.contract(
            address=w3.to_checksum_address(pool_address),
            abi=UNISWAP_V3_POOL_ABI,
        )

        fee = await pool.functions.fee().call()

        quoter = w3.eth.contract(
            address=w3.to_checksum_address(self._quoter_v2_address),
            abi=UNISWAP_V3_QUOTER_V2_ABI,
        )

        params = (
            w3.to_checksum_address(token_in),
            w3.to_checksum_address(token_out),
            int(amount_in),
            int(fee),
            0,
        )

        result = await quoter.functions.quoteExactInputSingle(params).call()

        amount_out = result[0]

        return Decimal(amount_out)

    async def get_pool_fee_bps(
        self,
        network: str,
        pool_address: str,
    ) -> Decimal | None:
        w3 = self._web3_factory(network)

        pool = w3.eth.contract(
            address=w3.to_checksum_address(pool_address),
            abi=UNISWAP_V3_POOL_ABI,
        )

        fee = await pool.functions.fee().call()

        # Uniswap V3 fee is in hundredths of a bip.
        # 3000 = 0.30% = 30 bps.
        return Decimal(fee) / Decimal(100)
```

---

## 14.4. V4 adapter placeholder

```python
# dex/v4_adapter.py

from decimal import Decimal

from dex.base_adapter import BaseDexAdapter


class V4Adapter(BaseDexAdapter):
    version = "v4"

    async def quote_exact_input(
        self,
        network: str,
        pool_address: str,
        token_in: str,
        token_out: str,
        amount_in: int,
    ) -> Decimal:
        """
        Uniswap V4 requires PoolManager and hook-aware simulation.

        Implementation requirements:
        1. Use official V4 Quoter if available.
        2. If hook changes output, use static simulation through PoolManager.
        3. If quote cannot be safely simulated, return 0 and add warning.
        """

        raise NotImplementedError("Uniswap V4 adapter must be implemented separately.")

    async def get_pool_fee_bps(
        self,
        network: str,
        pool_address: str,
    ) -> Decimal | None:
        raise NotImplementedError("Uniswap V4 adapter must be implemented separately.")
```

---

## 14.5. Adapter factory

```python
# dex/adapter_factory.py

from config.dex_registry import DEX_REGISTRY
from dex.v2_adapter import V2Adapter
from dex.v3_adapter import V3Adapter
from dex.v4_adapter import V4Adapter


class AdapterFactory:
    def __init__(self, web3_factory):
        self._web3_factory = web3_factory
        self._adapters = {}

    def get_adapter(self, network: str, dex_id: str):
        key = (network, dex_id)

        if key in self._adapters:
            return self._adapters[key]

        network_config = DEX_REGISTRY.get(network, {})

        for dex_config in network_config.get("dexes", []):
            if dex_config["dex_id"] != dex_id:
                continue

            version = dex_config["version"]

            if version == "v2":
                adapter = V2Adapter(
                    web3_factory=self._web3_factory,
                    router_address=dex_config["router"],
                    default_fee_bps=dex_config.get("default_fee_bps", 30),
                )

            elif version == "v3":
                adapter = V3Adapter(
                    web3_factory=self._web3_factory,
                    quoter_v2_address=dex_config["quoter_v2"],
                )

            elif version == "v4":
                adapter = V4Adapter()

            else:
                adapter = None

            self._adapters[key] = adapter

            return adapter

        return None
```

---

# 15. Price service

## 15.1. Логика

1. Загружаем все цены MEXC.
2. Для токена ищем пары:

```text
TOKENUSDT
TOKENUSDC
```

3. Если есть обе — считаем обе отдельно.

4. Цена кэшируется.

---

## 15.2. Price service fragment

```python
# services/price_service.py

import time
from decimal import Decimal

from models.mexc_models import MexcPrice


class PriceService:
    def __init__(self, mexc_client, cache_ttl_sec: int = 10):
        self._mexc_client = mexc_client
        self._cache_ttl_sec = cache_ttl_sec

        self._prices: dict[str, MexcPrice] = {}
        self._updated_at = 0.0

    async def refresh_all_prices(self) -> None:
        raw_prices = await self._mexc_client.get_all_prices()

        now = time.time()

        self._prices.clear()

        for item in raw_prices:
            symbol = str(item.get("symbol", "")).upper()
            price = item.get("price")

            if not symbol or price is None:
                continue

            base_asset, quote_asset = self._split_symbol(symbol)

            if quote_asset not in {"USDT", "USDC"}:
                continue

            self._prices[symbol] = MexcPrice(
                symbol=symbol,
                base_asset=base_asset,
                quote_asset=quote_asset,
                price=Decimal(str(price)),
                updated_at=now,
            )

        self._updated_at = now

    def get_price(
        self,
        base_asset: str,
        quote_asset: str,
    ) -> Decimal | None:
        symbol = f"{base_asset.upper()}{quote_asset.upper()}"

        price_record = self._prices.get(symbol)

        if price_record is None:
            return None

        return price_record.price

    @staticmethod
    def _split_symbol(symbol: str) -> tuple[str, str]:
        if symbol.endswith("USDT"):
            return symbol[:-4], "USDT"

        if symbol.endswith("USDC"):
            return symbol[:-4], "USDC"

        return symbol, ""
```

---

# 16. Fee service

## 16.1. Компоненты комиссий

```text
DEX network fee
DEX pool fee
MEXC deposit fee
MEXC withdraw fee
MEXC trading fee
Token transfer tax
Slippage cost
```

---

## 16.2. Fee model

```python
# models/fee_models.py

from decimal import Decimal

from pydantic import BaseModel


class FeeBreakdown(BaseModel):
    dex_network_fee_usd: Decimal = Decimal("0")
    dex_pool_fee_usd: Decimal = Decimal("0")
    mexc_deposit_fee_usd: Decimal = Decimal("0")
    mexc_withdraw_fee_usd: Decimal = Decimal("0")
    mexc_trading_fee_usd: Decimal = Decimal("0")
    token_transfer_tax_usd: Decimal = Decimal("0")
    slippage_usd: Decimal = Decimal("0")

    def total(self) -> Decimal:
        return (
            self.dex_network_fee_usd
            + self.dex_pool_fee_usd
            + self.mexc_deposit_fee_usd
            + self.mexc_withdraw_fee_usd
            + self.mexc_trading_fee_usd
            + self.token_transfer_tax_usd
            + self.slippage_usd
        )
```

---

## 16.3. Fee service fragment

```python
# services/fee_service.py

from decimal import Decimal

from models.fee_models import FeeBreakdown


class FeeService:
    def __init__(
        self,
        rpc_client_factory,
        price_service,
        mexc_taker_fee_bps: int = 10,
    ):
        self._rpc_client_factory = rpc_client_factory
        self._price_service = price_service
        self._mexc_taker_fee_bps = Decimal(mexc_taker_fee_bps)

    async def calculate_dex_network_fee_usd(
        self,
        network: str,
        gas_estimate: Decimal,
    ) -> Decimal:
        rpc_client = self._rpc_client_factory(network)

        gas_price_wei = await rpc_client.get_gas_price(network)

        native_token = self._native_token(network)

        native_price_usd = self._price_service.get_price(native_token, "USDT")

        if native_price_usd is None:
            return Decimal("0")

        fee_native = gas_estimate * gas_price_wei / Decimal(10**18)

        return fee_native * native_price_usd

    def calculate_mexc_trading_fee_usd(
        self,
        trade_value_usd: Decimal,
    ) -> Decimal:
        return trade_value_usd * self._mexc_taker_fee_bps / Decimal(10000)

    @staticmethod
    def _native_token(network: str) -> str:
        mapping = {
            "ETHEREUM": "ETH",
            "BSC": "BNB",
            "POLYGON": "POL",
            "ARBITRUM": "ETH",
            "ROBINHOOD": "ETH",
        }

        return mapping[network]
```

---

# 17. Profit calculator

## 17.1. Direction A

```text
Wallet stable
  → buy token on DEX
  → transfer token to MEXC
  → sell token on MEXC
  → withdraw stable from MEXC
```

---

## 17.2. Direction B

```text
Wallet stable
  → deposit stable to MEXC
  → buy token on MEXC
  → withdraw token to wallet
  → sell token on DEX
```

---

## 17.3. Profit calculator fragment

```python
# services/profit_calculator.py

from decimal import Decimal

from models.fee_models import FeeBreakdown


class ProfitCalculator:
    def __init__(self, base_amount_usd: Decimal = Decimal("10")):
        self._base_amount_usd = base_amount_usd

    def direction_a_net_profit(
        self,
        token_received_from_dex: Decimal,
        mexc_price_usd: Decimal,
        fees: FeeBreakdown,
    ) -> tuple[Decimal, Decimal]:
        """
        DEX_BUY_MEXC_SELL
        """

        gross_value_usd = token_received_from_dex * mexc_price_usd

        gross_profit_usd = gross_value_usd - self._base_amount_usd

        net_profit_usd = gross_profit_usd - fees.total()

        net_profit_pct = net_profit_usd / self._base_amount_usd * Decimal(100)

        return net_profit_usd, net_profit_pct

    def direction_b_net_profit(
        self,
        stable_received_from_dex: Decimal,
        fees: FeeBreakdown,
    ) -> tuple[Decimal, Decimal]:
        """
        MEXC_BUY_DEX_SELL
        """

        gross_profit_usd = stable_received_from_dex - self._base_amount_usd

        net_profit_usd = gross_profit_usd - fees.total()

        net_profit_pct = net_profit_usd / self._base_amount_usd * Decimal(100)

        return net_profit_usd, net_profit_pct
```

---

# 18. Signal model

```python
# models/signal_models.py

from decimal import Decimal

from pydantic import BaseModel

from models.fee_models import FeeBreakdown


class SecurityWarning(BaseModel):
    type: str
    severity: str
    message: str


class ArbitrageSignal(BaseModel):
    timestamp: str
    network: str
    token_coin: str
    token_address: str

    mexc_quote_asset: str
    mexc_symbol: str

    pool_stablecoin_coin: str
    pool_stablecoin_address: str

    pool_address: str
    dex: str | None
    pool_version: str | None

    direction: str

    base_amount_usd: Decimal

    mexc_price_usd: Decimal

    dex_amount_in: Decimal
    dex_amount_out: Decimal

    gross_profit_usd: Decimal
    gross_profit_pct: Decimal

    fees: FeeBreakdown

    net_profit_usd: Decimal
    net_profit_pct: Decimal

    full_cycle: bool

    warnings: list[SecurityWarning]
```

---

# 19. Security checks

## 19.1. Логика

Security checks запускаются только если:

```text
net_profit_pct > min_net_profit_pct
```

Если проверка дает warning:

```text
signal remains
warning is attached
```

---

## 19.2. Token security checker fragment

```python
# security/token_security_checker.py

from models.signal_models import SecurityWarning


class TokenSecurityChecker:
    def __init__(self, web3_factory):
        self._web3_factory = web3_factory

    async def check_token(
        self,
        network: str,
        token_address: str,
    ) -> list[SecurityWarning]:
        warnings: list[SecurityWarning] = []

        w3 = self._web3_factory(network)

        checksum_address = w3.to_checksum_address(token_address)

        code = await w3.eth.get_code(checksum_address)

        if code == b"":
            warnings.append(
                SecurityWarning(
                    type="not_a_contract",
                    severity="critical",
                    message="Token address has no contract code.",
                )
            )

            return warnings

        if await self._has_owner_function(w3, checksum_address):
            warnings.append(
                SecurityWarning(
                    type="ownable",
                    severity="medium",
                    message="Token has owner function.",
                )
            )

        if await self._has_mint_function(w3, checksum_address):
            warnings.append(
                SecurityWarning(
                    type="mintable",
                    severity="high",
                    message="Token may have mint function.",
                )
            )

        if await self._has_blacklist_function(w3, checksum_address):
            warnings.append(
                SecurityWarning(
                    type="blacklist",
                    severity="high",
                    message="Token may have blacklist function.",
                )
            )

        if await self._has_pause_function(w3, checksum_address):
            warnings.append(
                SecurityWarning(
                    type="pausable",
                    severity="medium",
                    message="Token may be pausable.",
                )
            )

        return warnings

    async def _has_owner_function(self, w3, address: str) -> bool:
        try:
            abi = [
                {
                    "inputs": [],
                    "name": "owner",
                    "outputs": [{"internalType": "address", "name": "", "type": "address"}],
                    "stateMutability": "view",
                    "type": "function",
                }
            ]

            contract = w3.eth.contract(address=address, abi=abi)

            await contract.functions.owner().call()

            return True

        except Exception:
            return False

    async def _has_mint_function(self, w3, address: str) -> bool:
        # Simplified selector-based check.
        # In production use full ABI or 4byte selector database.
        return False

    async def _has_blacklist_function(self, w3, address: str) -> bool:
        return False

    async def _has_pause_function(self, w3, address: str) -> bool:
        return False
```

---

# 20. Scanner

## 20.1. Main scanner loop

```python
# scanner/scanner.py

import asyncio
from decimal import Decimal


class Scanner:
    def __init__(
        self,
        storage,
        adapter_factory,
        price_service,
        fee_service,
        profit_calculator,
        token_security_checker,
        min_net_profit_pct: Decimal = Decimal("1"),
    ):
        self._storage = storage
        self._adapter_factory = adapter_factory
        self._price_service = price_service
        self._fee_service = fee_service
        self._profit_calculator = profit_calculator
        self._token_security_checker = token_security_checker
        self._min_net_profit_pct = min_net_profit_pct

    async def run_once(self) -> None:
        pools = await self._storage.get_active_pools()

        tasks = [
            self._scan_pool(pool)
            for pool in pools
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _scan_pool(self, pool) -> None:
        for quote_asset in ("USDT", "USDC"):
            await self._scan_pool_with_quote_asset(pool, quote_asset)

    async def _scan_pool_with_quote_asset(self, pool, quote_asset: str) -> None:
        mexc_price = self._price_service.get_price(
            base_asset=pool.token_coin,
            quote_asset=quote_asset,
        )

        if mexc_price is None:
            return

        direction_a_signal = await self._scan_direction_a(
            pool=pool,
            quote_asset=quote_asset,
            mexc_price=mexc_price,
        )

        direction_b_signal = await self._scan_direction_b(
            pool=pool,
            quote_asset=quote_asset,
            mexc_price=mexc_price,
        )

        if direction_a_signal is not None:
            await self._emit_signal(direction_a_signal)

        if direction_b_signal is not None:
            await self._emit_signal(direction_b_signal)

    async def _emit_signal(self, signal) -> None:
        if signal.net_profit_pct > self._min_net_profit_pct:
            signal.warnings = await self._token_security_checker.check_token(
                network=signal.network,
                token_address=signal.token_address,
            )

            await self._storage.save_signal(signal)

    async def _scan_direction_a(self, pool, quote_asset: str, mexc_price):
        # Implementation:
        # 1. quote stable -> token on DEX
        # 2. calculate gross
        # 3. calculate fees
        # 4. calculate net
        raise NotImplementedError

    async def _scan_direction_b(self, pool, quote_asset: str, mexc_price):
        # Implementation:
        # 1. token_amount = 10 / mexc_price
        # 2. quote token -> stable on DEX
        # 3. calculate gross
        # 4. calculate fees
        # 5. calculate net
        raise NotImplementedError
```

---

# 21. Pool refresh task

```python
# scanner/pool_refresh_task.py

import asyncio


class PoolRefreshTask:
    def __init__(
        self,
        refresh_interval_sec: int,
        refresh_service,
        state_repository,
    ):
        self._refresh_interval_sec = refresh_interval_sec
        self._refresh_service = refresh_service
        self._state_repository = state_repository

    async def run_forever(self) -> None:
        while True:
            try:
                await self._refresh_service.refresh_all()

                self._state_repository.atomic_replace()

            except Exception as error:
                # Keep old active DB.
                # Log error.
                print(f"Pool refresh failed: {error}")

            await asyncio.sleep(self._refresh_interval_sec)
```

---

# 22. Main entrypoint

```python
# main.py

import asyncio

from scanner.scanner import Scanner
from scanner.pool_refresh_task import PoolRefreshTask


async def main():
    # Initialize:
    # - http clients
    # - rpc clients
    # - storage
    # - services
    # - adapters
    # - scanner
    # - refresh task

    scanner: Scanner = ...
    refresh_task: PoolRefreshTask = ...

    await asyncio.gather(
        refresh_task.run_forever(),
        scanner.run_forever(),
    )


if __name__ == "__main__":
    asyncio.run(main())
```

---

# 23. Детальная логика по каждому параметру

## 23.1. `base_amount_usd`

Значение:

```python
base_amount_usd = Decimal("10")
```

Используется как базовая сумма расчета.

Для stablecoin amount:

```python
stable_amount = base_amount_usd / stablecoin_price_usd
```

Если stablecoin = USDT или USDC:

```python
stablecoin_price_usd = Decimal("1")
```

Если stablecoin = DAI, FDUSD, USDe и т.д.:

```python
stablecoin_price_usd = MEXC price of stablecoin against USDT/USDC
```

Если цены стейблкоина нет:

```python
skip pool
```

или:

```python
assume 1 USD + warning
```

Рекомендация:

```text
assume 1 USD only for USDT and USDC
for other stablecoins require MEXC price
```

---

## 23.2. `min_net_profit_pct`

Значение:

```python
min_net_profit_pct = Decimal("1")
```

Сигнал выводится только если:

```python
net_profit_pct > min_net_profit_pct
```

Не `>=`, а строго `>`.

---

## 23.3. `pool_refresh_interval_sec`

Значение:

```python
pool_refresh_interval_sec = 3600
```

Каждый час:

```text
1. Запрос MEXC capital config.
2. Обновление stablecoin registry.
3. Запрос DexScreener/GeckoTerminal по token addresses.
4. Сборка нового SQLite.
5. Валидация.
6. Backup.
7. Atomic replace.
```

Во время обновления scanner использует старую active DB.

---

## 23.4. `fee_cache_ttl_sec`

Значение:

```python
fee_cache_ttl_sec = 60
```

Кэшировать:

```text
gas price
pool fee
MEXC withdraw fee
MEXC deposit fee
```

Не кэшировать:

```text
DEX quote
```

DEX quote должен быть свежим для каждого расчета.

---

## 23.5. `mexc_price_cache_ttl_sec`

Значение:

```python
mexc_price_cache_ttl_sec = 10
```

Каждые 10 секунд можно обновлять:

```text
GET /api/v3/ticker/price
```

Если нагрузка большая, можно увеличить до 15-30 секунд.

---

## 23.6. `max_backups`

Значение:

```python
max_backups = 5
```

После каждого успешного refresh:

```text
active.sqlite3 → backups/active_TIMESTAMP.sqlite3
```

Затем:

```text
keep newest 5
delete older
```

---

## 23.7. `multicall_batch_size`

Значение:

```python
multicall_batch_size = 50
```

Один multicall может содержать до 50 view-запросов.

Пример батча:

```text
token.decimals()
token.symbol()
pool.token0()
pool.token1()
pool.fee()
pool.getReserves()
```

Это экономит RPC лимит.

---

## 23.8. `mexc_taker_fee_bps`

Default:

```python
mexc_taker_fee_bps = 10
```

То есть:

```text
0.10%
```

Если есть authenticated MEXC API и можно получить реальный fee — использовать реальный.

---

## 23.9. `full_cycle`

Значение:

```python
full_cycle = True
```

Если `True`, считать полный цикл.

Direction A:

```text
DEX buy
token transfer to MEXC
MEXC sell
MEXC withdraw stable
```

Direction B:

```text
stable deposit to MEXC
MEXC buy
token withdraw from MEXC
DEX sell
```

Если `False`, считать упрощенно:

```text
DEX buy + MEXC sell
```

или:

```text
MEXC buy + DEX sell
```

Но по твоему требованию default:

```text
True
```

---

# 24. Описание готового поведения системы

## 24.1. При запуске

Бот:

1. Читает конфиг.
2. Подключается к RPC.
3. Загружает active SQLite.
4. Запускает background refresh task.
5. Запускает scanner loop.

---

## 24.2. Каждый цикл scanner

Scanner:

1. Берет список активных пулов из SQLite.
2. Для каждого пула:
   - определяет DEX adapter;
   - определяет token decimals;
   - определяет stablecoin decimals;
   - получает MEXC price для USDT;
   - получает MEXC price для USDC;
   - делает on-chain quote stable → token;
   - делает on-chain quote token → stable;
   - считает gross profit;
   - считает fees;
   - считает net profit.

3. Если net profit > 1%:
   - запускает security checks;
   - добавляет warnings;
   - сохраняет сигнал.

---

## 24.3. Каждый час

Refresh task:

1. Создает temp DB.
2. Загружает MEXC capital config.
3. Строит MEXC asset registry.
4. Строит stablecoin registry.
5. Для каждого токена:
   - запрашивает DexScreener;
   - запрашивает GeckoTerminal;
   - merge;
   - filter;
   - writes to temp DB.

6. Валидирует temp DB.
7. Делает backup active DB.
8. Заменяет active DB.
9. Удаляет backup старше 5.

---

# 25. Тестирование

## 25.1. Unit tests

Проверить:

```text
normalize_mexc_network
parse MEXC capital config
stablecoin whitelist filtering
Decimal conversions
V2 reserve quote formula
V3 fee conversion
profit calculator
backup cleanup
```

---

## 25.2. Integration tests

Проверить:

```text
MEXC getall parsing
MEXC ticker price parsing
DexScreener pool parsing
GeckoTerminal pool parsing
SQLite atomic replace
backup retention
RPC eth_call
V2 quote on real pool
V3 quote on real pool
```

---

## 25.3. Dry-run tests

Для известных пар:

```text
ETH/USDC on Uniswap V3 Ethereum
BNB/USDT on PancakeSwap V2 BSC
MATIC/USDC on Uniswap V3 Polygon
ARB/USDC on Uniswap V3 Arbitrum
```

Проверить:

```text
quote does not revert
decimals correct
fee calculation sane
signal format valid
```

---

# 26. Production checklist

Перед production нужно проверить:

```text
[ ] Все DEX адреса сверены.
[ ] ABI соответствуют verified contracts.
[ ] Robinhood RPC стабилен.
[ ] MEXC network names правильно маппятся.
[ ] MEXC withdrawFee не дублируется с RPC network fee.
[ ] Pool fee не вычитается дважды, если quote уже включает fee.
[ ] Fee-on-transfer токены дают warning или conservative haircut.
[ ] V4 hooks не ломают quote.
[ ] SQLite backup работает.
[ ] Rate limiter не превышает Alchemy лимит.
[ ] DexScreener rate limiter не превышает 300 rpm.
[ ] Scanner не падает из-за одного битого пула.
[ ] Refresh не блокирует scanner.
[ ] При ошибке refresh старый active DB остается.
```

---

# 27. Итоговый проект в одном абзаце

Готовый проект — это async Python-бот, который раз в час обновляет список токенов и стейблкоинов из MEXC, раз в час пересобирает список DEX-пулов через DexScreener и GeckoTerminal строго по контрактным адресам, хранит состояние в SQLite с atomic replace и backup последних 5 итераций, непрерывно сканирует активные пулы, симулирует свопы через on-chain `eth_call` для V2/V3/V4 пулов, берет цену только с MEXC для пар TOKEN/USDT и TOKEN/USDC отдельно, считает полный цикл комиссии wallet → DEX → MEXC → wallet и wallet → MEXC → DEX → wallet, выводит сигналы только при чистой прибыли выше 1%, а security-проверки выполняет после расчета прибыли, не удаляя сигнал, а добавляя warnings.