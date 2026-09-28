"""
Unit tests for PriceService.

Tests:
- _split_symbol splits USDT and USDC symbols correctly
- get_price returns cached price or None
- get_all_prices_dict returns flat dict
- refresh_all_prices parses raw MEXC data correctly
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestPriceService:
    """Tests for PriceService."""

    @pytest.fixture
    def price_service(self):
        from services.price_service import PriceService

        mock_client = MagicMock()
        ps = PriceService.__new__(PriceService)
        ps._mexc_client = mock_client
        ps._cache_ttl_sec = 10
        ps._prices = {}
        ps._updated_at = 0.0
        return ps

    def test_split_symbol_usdt(self, price_service):
        """USDT suffix is correctly stripped."""
        assert price_service._split_symbol("BTCUSDT") == ("BTC", "USDT")
        assert price_service._split_symbol("ETHUSDT") == ("ETH", "USDT")
        assert price_service._split_symbol("1000PEPEUSDT") == ("1000PEPE", "USDT")

    def test_split_symbol_usdc(self, price_service):
        """USDC suffix is correctly stripped."""
        assert price_service._split_symbol("BTCUSDC") == ("BTC", "USDC")
        assert price_service._split_symbol("ETHUSDC") == ("ETH", "USDC")

    def test_split_symbol_no_match(self, price_service):
        """Symbol without USDT/USDC returns empty quote."""
        assert price_service._split_symbol("BTCUSD") == ("BTCUSD", "")
        assert price_service._split_symbol("ETHBTC") == ("ETHBTC", "")
        assert price_service._split_symbol("") == ("", "")

    def test_split_symbol_priority_usdc(self, price_service):
        """USDC check comes first (endsWith USDC before USDT)."""
        # SYMBOLUSDC should match USDC, not partial USDT inside USDC
        assert price_service._split_symbol("USDCUSDC") == ("USDC", "USDC")
        # SOLUSDC (SOL is valid, USDC is quote)
        assert price_service._split_symbol("SOLUSDC") == ("SOL", "USDC")

    def test_get_price_found(self, price_service):
        """get_price returns Decimal when symbol is cached."""
        import time

        from models.mexc_models import MexcPrice

        price_service._prices["BTCUSDT"] = MexcPrice(
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            price=Decimal("50000.00"),
            updated_at=time.time(),
        )
        result = price_service.get_price("BTC", "USDT")
        assert result == Decimal("50000.00")

    def test_get_price_not_found(self, price_service):
        """get_price returns None for unknown symbol."""
        result = price_service.get_price("UNKNOWN", "USDT")
        assert result is None

    def test_get_price_case_insensitive(self, price_service):
        """get_price uppercases the symbol."""
        import time

        from models.mexc_models import MexcPrice

        price_service._prices["BTCUSDT"] = MexcPrice(
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            price=Decimal("50000.00"),
            updated_at=time.time(),
        )
        result = price_service.get_price("btc", "usdt")
        assert result == Decimal("50000.00")

    def test_get_all_prices_dict_empty(self, price_service):
        """Empty cache returns empty dict."""
        assert price_service.get_all_prices_dict() == {}

    def test_get_all_prices_dict_returns_flat(self, price_service):
        """get_all_prices_dict returns flat symbol->price mapping."""
        import time

        from models.mexc_models import MexcPrice

        now = time.time()
        price_service._prices["BTCUSDT"] = MexcPrice(
            symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT",
            price=Decimal("50000"), updated_at=now,
        )
        price_service._prices["ETHUSDT"] = MexcPrice(
            symbol="ETHUSDT", base_asset="ETH", quote_asset="USDT",
            price=Decimal("3000"), updated_at=now,
        )
        flat = price_service.get_all_prices_dict()
        assert flat == {"BTCUSDT": Decimal("50000"), "ETHUSDT": Decimal("3000")}

    @pytest.mark.asyncio
    async def test_refresh_all_prices_parses_raw(self, price_service):
        """refresh_all_prices parses raw MEXC ticker data correctly."""
        raw_data = [
            {"symbol": "BTCUSDT", "price": "50000.00"},
            {"symbol": "ETHUSDT", "price": "3000.00"},
            {"symbol": "SOLUSDC", "price": "150.00"},
            {"symbol": "UNIBTC", "price": "0.05"},  # Not USDT/USDC quote — skipped
            {"symbol": "", "price": "1.00"},         # Empty symbol — skipped
            {"symbol": "NOPRICE", "price": None},    # No price — skipped
        ]
        mock_client = MagicMock()
        mock_client.get_all_prices = AsyncMock(return_value=raw_data)
        price_service._mexc_client = mock_client

        await price_service.refresh_all_prices()

        assert "BTCUSDT" in price_service._prices
        assert "ETHUSDT" in price_service._prices
        assert "SOLUSDC" in price_service._prices
        assert "UNIBTC" not in price_service._prices
        assert "" not in price_service._prices
        assert "NOPRICE" not in price_service._prices
        assert price_service._prices["BTCUSDT"].price == Decimal("50000.00")
        assert price_service._prices["SOLUSDC"].quote_asset == "USDC"

    @pytest.mark.asyncio
    async def test_refresh_all_prices_clears_cache(self, price_service):
        """Old prices are cleared on refresh."""
        import time

        from models.mexc_models import MexcPrice

        # Add old price
        price_service._prices["OLDUSDT"] = MexcPrice(
            symbol="OLDUSDT", base_asset="OLD", quote_asset="USDT",
            price=Decimal("1"), updated_at=time.time(),
        )
        mock_client = MagicMock()
        mock_client.get_all_prices = AsyncMock(return_value=[{"symbol": "NEWUSDT", "price": "2.00"}])
        price_service._mexc_client = mock_client

        await price_service.refresh_all_prices()

        assert "OLDUSDT" not in price_service._prices
        assert "NEWUSDT" in price_service._prices
