"""Alt-CEX public API endpoints (read-only probe, no trading)."""

BITGET_BASE_URL = "https://api.bitget.com"
BITGET_ENDPOINTS = {
    "coins": "/api/v2/spot/public/coins",
    "tickers": "/api/v2/spot/market/tickers",
    "orderbook": "/api/v2/spot/market/orderbook",
}

HTX_BASE_URL = "https://api.huobi.pro"
HTX_ENDPOINTS = {
    "currencies": "/v2/reference/currencies",
    "tickers": "/market/tickers",
    "depth": "/market/depth",
}

BINGX_BASE_URL = "https://open-api.bingx.com"
BINGX_ENDPOINTS = {
    "symbols": "/openApi/spot/v1/common/symbols",
    "tickers": "/openApi/spot/v1/ticker/24hr",
    "depth": "/openApi/spot/v1/market/depth",
}

# Optional OKX DEX aggregator (not spot). Off by default.
OKX_BASE_URL = "https://www.okx.com"
OKX_ENDPOINTS = {
    "dex_token_info": "/api/v5/dex/aggregator/get-token-info",
}

# Map exchange chain labels → our internal network names.
CHAIN_ALIASES: dict[str, str] = {
    "bsc": "BSC",
    "bep20": "BSC",
    "bnb": "BSC",
    "bnbchain": "BSC",
    "bnb smart chain": "BSC",
    "base": "BASE",
    "eth": "ETHEREUM",
    "erc20": "ETHEREUM",
    "ethereum": "ETHEREUM",
    "arb": "ARBITRUM",
    "arbitrum": "ARBITRUM",
    "arbitrum one": "ARBITRUM",
    "arc20": "ARBITRUM",
    "matic": "POLYGON",
    "polygon": "POLYGON",
    "polygon pos": "POLYGON",
    "prc20": "POLYGON",
}
