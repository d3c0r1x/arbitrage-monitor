"""
Price service.

Fetches prices from MEXC and caches them.
Supports USDT and USDC quote assets separately.

Never clears the live cache until a valid replacement is built — a failed
or malformed MEXC response must not leave the scanner with price=0.
"""

import logging
import time
from decimal import Decimal

from models.mexc_models import MexcPrice

logger = logging.getLogger(__name__)


class PriceService:
    """Price service that fetches and caches MEXC prices."""

    def __init__(self, mexc_client, cache_ttl_sec: int = 10):
        self._mexc_client = mexc_client
        self._cache_ttl_sec = cache_ttl_sec

        self._prices: dict[str, MexcPrice] = {}
        self._updated_at: float = 0.0

    async def refresh_all_prices(self) -> None:
        """Fetch all ticker prices from MEXC and atomically update cache."""
        raw_prices = await self._mexc_client.get_all_prices()
        if not isinstance(raw_prices, list):
            raise ValueError(
                f"mexc_prices_invalid_type:{type(raw_prices).__name__}"
            )
        if not raw_prices:
            raise ValueError("mexc_prices_empty_response")

        now = time.time()
        new_prices: dict[str, MexcPrice] = {}

        for item in raw_prices:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).upper()
            price = item.get("price")

            if not symbol or price is None:
                continue

            base_asset, quote_asset = self._split_symbol(symbol)

            if quote_asset not in {"USDT", "USDC"}:
                continue

            new_prices[symbol] = MexcPrice(
                symbol=symbol,
                base_asset=base_asset,
                quote_asset=quote_asset,
                price=Decimal(str(price)),
                updated_at=now,
            )

        if not new_prices:
            raise ValueError("mexc_prices_empty_after_parse")

        # Atomic swap — never leave callers with a wiped cache.
        self._prices = new_prices
        self._updated_at = now
        usdt_pairs = sum(1 for p in self._prices.values() if p.quote_asset == "USDT")
        usdc_pairs = sum(1 for p in self._prices.values() if p.quote_asset == "USDC")
        logger.info(
            "prices_refreshed: total=%d usdt=%d usdc=%d",
            len(self._prices),
            usdt_pairs,
            usdc_pairs,
        )

    def get_price(self, base_asset: str, quote_asset: str) -> Decimal | None:
        """Get the current price for a trading pair."""
        symbol = f"{base_asset.upper()}{quote_asset.upper()}"
        price_record = self._prices.get(symbol)

        if price_record is None:
            return None

        return price_record.price

    def is_cache_expired(self) -> bool:
        """Check if price cache has expired."""
        return time.time() - self._updated_at > self._cache_ttl_sec

    @property
    def cache_size(self) -> int:
        return len(self._prices)

    async def refresh_if_expired(self) -> bool:
        """Refresh prices only if cache is expired.

        Returns:
            True if prices were refreshed, False if cache was still valid.
        """
        if self.is_cache_expired():
            await self.refresh_all_prices()
            return True
        return False

    def get_all_prices_dict(self) -> dict[str, Decimal]:
        """Return a flat dict of symbol -> price for quick lookups."""
        return {
            record.symbol: record.price
            for record in self._prices.values()
        }

    @staticmethod
    def _split_symbol(symbol: str) -> tuple[str, str]:
        """Split a symbol into base and quote assets."""
        if symbol.endswith("USDC"):
            return symbol[:-4], "USDC"
        if symbol.endswith("USDT"):
            return symbol[:-4], "USDT"
        return symbol, ""
