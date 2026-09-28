"""
MEXC API client.

Endpoints used:
  GET /api/v3/capital/config/getall — coins, networks, contracts, fees
  GET /api/v3/ticker/price          — latest prices

Contract field in capital config: "contract" (not "contractAddress").

Authentication:
  /api/v3/capital/config/getall requires API key + HMAC SHA256 signature.
  /api/v3/ticker/price is public (no auth required).
"""

import hashlib
import hmac
import logging
import time
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from config.mexc_config import MEXC_BASE_URL, MEXC_ENDPOINTS
from config.networks import normalize_mexc_network
from config.settings import settings
from models.mexc_models import MexcAsset, MexcNetworkAsset

logger = logging.getLogger(__name__)


class MexcClient:
    """HTTP client for MEXC API."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self._base_url = MEXC_BASE_URL
        self._api_key = settings.MEXC_API_KEY
        self._api_secret = settings.MEXC_API_SECRET

    def _sign_request(self, params: dict | None = None) -> dict:
        """Add timestamp and HMAC SHA256 signature to request params.

        Args:
            params: Optional existing query parameters.

        Returns:
            Dict with timestamp, signature, and original params merged.
        """
        params = dict(params or {})
        params["timestamp"] = str(int(time.time() * 1000))
        query_string = urlencode(sorted(params.items()))
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    async def _signed_get(self, path: str, params: dict | None = None) -> httpx.Response:
        """Perform a GET request with HMAC SHA256 authentication."""
        signed_params = self._sign_request(params)
        url = self._base_url + path
        headers = {"X-MEXC-APIKEY": self._api_key}
        response = await self._client.get(url, params=signed_params, headers=headers, timeout=30)
        response.raise_for_status()
        return response

    async def _signed_post(self, path: str, body: dict | None = None) -> httpx.Response:
        """Perform a POST request with HMAC SHA256 authentication."""
        signed_params = self._sign_request(body or {})
        url = self._base_url + path
        headers = {
            "X-MEXC-APIKEY": self._api_key,
            "Content-Type": "application/json",
        }
        response = await self._client.post(
            url, params=signed_params, json=body, headers=headers, timeout=30
        )
        response.raise_for_status()
        return response

    async def get_capital_config(self) -> list[dict]:
        """Fetch all coin capital config with network details.

        Requires API key authentication (SPOT_WITHDRAW_READ permission).

        Returns:
            Raw JSON list from /api/v3/capital/config/getall.
        """
        response = await self._signed_get(MEXC_ENDPOINTS["capital_config_getall"])
        return response.json()

    async def get_all_prices(self) -> list[dict]:
        """Fetch latest prices for all symbols.

        Public endpoint — no authentication required.

        Returns:
            Raw JSON list from /api/v3/ticker/price.
        """
        url = self._base_url + MEXC_ENDPOINTS["ticker_price_all"]
        response = await self._client.get(url, timeout=60)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError(f"mexc_ticker_price_not_list:{type(data).__name__}")
        return data

    async def get_24hr_ticker(self) -> list[dict]:
        """Fetch 24hr ticker stats (volume, high, low, change) for all symbols.

        Public endpoint — no authentication required.

        Returns:
            Raw JSON list from /api/v3/ticker/24hr.
            Each item has symbol, volume, quoteVolume, priceChange, etc.
        """
        url = self._base_url + MEXC_ENDPOINTS["ticker_24hr"]
        response = await self._client.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    async def get_order_book(self, symbol: str, limit: int = 100) -> dict:
        """Fetch spot order book depth for a symbol.

        Public endpoint — no authentication required.

        Args:
            symbol: Trading pair (e.g. SMARSUSDT).
            limit: Depth levels (MEXC typically supports up to 5000).

        Returns:
            Raw JSON with keys bids/asks: list of [price, qty] string pairs.
        """
        url = self._base_url + MEXC_ENDPOINTS["depth"]
        response = await self._client.get(
            url,
            params={"symbol": symbol.upper(), "limit": int(limit)},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    async def place_order(self, symbol: str, side: str, order_type: str = "MARKET", quantity: str | None = None, quoteOrderQty: str | None = None) -> dict:
        """Place a spot order on MEXC.

        Args:
            symbol: Trading pair (e.g. TOKENUSDT).
            side: BUY or SELL.
            order_type: MARKET, LIMIT, STOP_MARKET, etc.
            quantity: Order quantity in base asset.
            quoteOrderQty: Order quantity in quote asset (for market orders).

        Returns:
            Raw JSON response with orderId, status, etc.
        """
        body = {"symbol": symbol, "side": side.upper(), "type": order_type}
        if quantity:
            body["quantity"] = quantity
        if quoteOrderQty:
            body["quoteOrderQty"] = quoteOrderQty
        response = await self._signed_post(MEXC_ENDPOINTS.get("order_place", "/api/v3/order"), body)
        return response.json()

    async def get_order(self, symbol: str, order_id: str) -> dict:
        """Query a spot order status by orderId."""
        response = await self._signed_get(
            MEXC_ENDPOINTS.get("order_query", "/api/v3/order"),
            {"symbol": symbol, "orderId": order_id},
        )
        return response.json()

    async def get_account_balances(self) -> dict[str, Decimal]:
        """Fetch spot account balances (free amounts only).

        Returns:
            Dict asset -> free balance, e.g. {"USDT": Decimal("120.5")}.
        """
        response = await self._signed_get(MEXC_ENDPOINTS.get("account", "/api/v3/account"))
        data = response.json()
        balances: dict[str, Decimal] = {}
        for b in data.get("balances", []):
            free = self._parse_decimal(b.get("free"))
            if free and free > 0:
                balances[str(b.get("asset", "")).upper()] = free
        return balances

    async def withdraw(self, coin: str, address: str, amount: str, network: str, memo: str | None = None) -> dict:
        """Request a withdrawal from MEXC to an external address.

        Args:
            coin: Asset symbol (e.g. USDT).
            address: Destination wallet address.
            amount: Withdraw amount as string.
            network: MEXC raw network name (e.g. BEP20(BSC)).
            memo: Optional memo/tag.

        Returns:
            Raw JSON with withdraw id.
        """
        body = {"coin": coin, "address": address, "amount": amount, "netWork": network}
        if memo:
            body["memo"] = memo
        response = await self._signed_post(
            MEXC_ENDPOINTS.get("withdraw_apply", "/api/v3/capital/withdraw/apply"), body
        )
        return response.json()

    async def get_withdraw_history(self, coin: str | None = None) -> list[dict]:
        """Fetch recent withdraw records (status, txId)."""
        params = {"coin": coin} if coin else {}
        response = await self._signed_get(
            MEXC_ENDPOINTS.get("withdraw_history", "/api/v3/capital/withdraw/history"), params
        )
        return response.json()

    async def get_deposit_history(self, coin: str | None = None) -> list[dict]:
        """Fetch recent deposit records (status, txId, amount)."""
        params = {"coin": coin} if coin else {}
        response = await self._signed_get(
            MEXC_ENDPOINTS.get("deposit_history", "/api/v3/capital/deposit/hisrec"), params
        )
        return response.json()

    async def get_deposit_address(self, coin: str, network: str) -> list[dict]:
        """Fetch deposit address list for coin on a raw MEXC network."""
        response = await self._signed_get(
            MEXC_ENDPOINTS.get("deposit_address", "/api/v3/capital/deposit/address"),
            {"coin": coin, "network": network},
        )
        return response.json()

    def parse_capital_config(self, raw_items: list[dict]) -> list[MexcAsset]:
        """Parse raw capital config into structured MexcAsset list.

        Args:
            raw_items: Raw JSON list from get_capital_config().

        Returns:
            List of MexcAsset with parsed network details.
        """
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
