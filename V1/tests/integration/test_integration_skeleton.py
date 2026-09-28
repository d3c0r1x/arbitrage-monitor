"""
Integration tests skeleton.

These tests require real network calls and are disabled by default.
Run with: RUN_INTEGRATION=1 pytest -m integration -q
"""

import os

import pytest

pytestmark = [pytest.mark.integration]


@pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION", "0") not in ("1", "true", "True"),
    reason="Integration tests disabled. Set RUN_INTEGRATION=1 to enable.",
)
class TestIntegrationSkeleton:
    """Integration tests for the complete pipeline."""

    async def test_mexc_capital_config_fetch(self):
        """Integration: Fetch MEXC capital config."""
        import httpx

        from clients.mexc_client import MexcClient

        async with httpx.AsyncClient() as client:
            mexc = MexcClient(client)
            raw = await mexc.get_capital_config()
            assert len(raw) > 0

    async def test_mexc_price_fetch(self):
        """Integration: Fetch MEXC prices."""
        import httpx

        from clients.mexc_client import MexcClient

        async with httpx.AsyncClient() as client:
            mexc = MexcClient(client)
            prices = await mexc.get_all_prices()
            assert len(prices) > 0

    async def test_capital_config_parsing(self):
        """Integration: Parse MEXC capital config."""
        import httpx

        from clients.mexc_client import MexcClient

        async with httpx.AsyncClient() as client:
            mexc = MexcClient(client)
            raw = await mexc.get_capital_config()
            assets = mexc.parse_capital_config(raw)
            assert len(assets) > 0
            # Verify at least one asset has a network with non-empty contract.
            has_contract = False
            for asset in assets:
                for net in asset.networks:
                    if net.contract_address and net.contract_address != "0x":
                        has_contract = True
                        break
            assert has_contract, "No asset found with contract address"

    async def test_candidate_token_selection(self):
        """Integration: Select candidate tokens from MEXC."""
        import httpx

        from clients.mexc_client import MexcClient

        async with httpx.AsyncClient() as client:
            mexc = MexcClient(client)
            raw_assets = await mexc.get_capital_config()
            assets = mexc.parse_capital_config(raw_assets)
            raw_prices = await mexc.get_all_prices()

            prices = {}
            for item in raw_prices:
                symbol = str(item.get("symbol", "")).upper()
                price = item.get("price")
                if symbol and price:
                    from decimal import Decimal
                    prices[symbol] = Decimal(str(price))

            from services.mexc_asset_service import MexcAssetService
            service = MexcAssetService(mexc)
            candidates = service.select_candidate_tokens(assets, prices)

            assert len(candidates) > 0

    async def test_dexscreener_discovery(self):
        """Integration: Test DexScreener pool discovery for a known token."""
        import httpx

        from discovery.dexscreener_source import DexScreenerSource

        async with httpx.AsyncClient() as client:
            source = DexScreenerSource(client)
            # Use WETH address on Ethereum as a test.
            pools = await source.fetch_pools_by_token(
                network="ETHEREUM",
                token_address="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
            )
            # WETH should have pools.
            assert len(pools) > 0

    async def test_database_schema_creation(self):
        """Integration: Database schema can be created."""
        import tempfile

        from storage.database import create_connection, initialize_schema

        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
            db_path = f.name

        try:
            conn = create_connection(db_path)
            initialize_schema(conn)

            # Verify tables exist.
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [row[0] for row in cursor.fetchall()]
            conn.close()

            assert "pools" in tables
            assert "mexc_assets" in tables
            assert "stablecoins" in tables
            assert "source_health" in tables
            assert "performance_metrics" in tables
            assert "signals" in tables
            assert "refresh_log" in tables
        finally:
            os.unlink(db_path)
