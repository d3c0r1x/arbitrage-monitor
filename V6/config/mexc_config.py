"""
MEXC API endpoints and configuration constants.

Documentation:
  https://mexc-develop.github.io/apidocs/spot_v3_en/
"""

MEXC_BASE_URL = "https://api.mexc.com"

MEXC_ENDPOINTS: dict[str, str] = {
    # Coins, networks, contracts, deposit/withdraw, fees.
    "capital_config_getall": "/api/v3/capital/config/getall",
    # Spot symbols and trading rules.
    "exchange_info": "/api/v3/exchangeInfo",
    # Tradable symbols available for API trading.
    "default_symbols": "/api/v3/defaultSymbols",
    # Latest price for one symbol or all symbols if symbol is omitted.
    "ticker_price_all": "/api/v3/ticker/price",
    # 24hr ticker with volume, high, low, change.
    "ticker_24hr": "/api/v3/ticker/24hr",
    # Order book depth (public). bids/asks as [price, qty] string pairs.
    "depth": "/api/v3/depth",
    # Spot order placement (requires MEXC API key).
    "order_place": "/api/v3/order",
    # Query single order status (requires MEXC API key).
    "order_query": "/api/v3/order",
    # Account info with balances (requires MEXC API key).
    "account": "/api/v3/account",
    # Withdraw request (requires SPOT_WITHDRAW_WRITE permission).
    "withdraw_apply": "/api/v3/capital/withdraw/apply",
    # Withdraw history.
    "withdraw_history": "/api/v3/capital/withdraw/history",
    # Deposit history.
    "deposit_history": "/api/v3/capital/deposit/hisrec",
    # Deposit address for a coin+network.
    "deposit_address": "/api/v3/capital/deposit/address",
}

MEXC_QUOTE_ASSETS = ("USDT", "USDC")

MEXC_CAPITAL_CONFIG_REFRESH_SEC = 3600
MEXC_EXCHANGE_INFO_REFRESH_SEC = 3600
MEXC_PRICE_CACHE_TTL_SEC = 10

# Default spot taker fee if account fee is unavailable.
MEXC_DEFAULT_TAKER_FEE_BPS = 10
