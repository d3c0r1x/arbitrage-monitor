"""
Unit tests for StablecoinRegistryService.

Tests:
- Whitelist filtering
- Address lowercasing
- Network grouping
- Non-whitelist coins ignored
"""

from decimal import Decimal

import pytest

from models.mexc_models import MexcAsset, MexcNetworkAsset


class TestStablecoinRegistry:
    """Tests for StablecoinRegistryService."""

    @pytest.fixture
    def service(self):
        from services.stablecoin_registry_service import StablecoinRegistryService

        return StablecoinRegistryService()

    def make_asset(self, coin: str, network: str, address: str, deposit=True, withdraw=True):
        return MexcAsset(
            coin=coin,
            name=coin,
            networks=[
                MexcNetworkAsset(
                    coin=coin,
                    name=coin,
                    network_raw=network,
                    network_normalized=network,
                    contract_address=address,
                    deposit_enable=deposit,
                    withdraw_enable=withdraw,
                    withdraw_fee=Decimal("0.1"),
                    withdraw_min=Decimal("1"),
                    withdraw_max=Decimal("1000"),
                    min_confirm=12,
                ),
            ],
        )

    def test_usdt_included(self, service):
        assets = [self.make_asset("USDT", "ETHEREUM", "0xusdt")]
        registry = service.build_registry(assets)
        assert "ETHEREUM" in registry
        assert any(r.coin == "USDT" for r in registry["ETHEREUM"])

    def test_unknown_coin_excluded(self, service):
        assets = [self.make_asset("SCR", "ETHEREUM", "0xscr")]
        registry = service.build_registry(assets)
        assert "ETHEREUM" not in registry or all(r.coin != "SCR" for r in registry.get("ETHEREUM", []))

    def test_multiple_networks(self, service):
        assets = [
            self.make_asset("USDT", "ETHEREUM", "0xusdt_eth"),
            self.make_asset("USDT", "BSC", "0xusdt_bsc"),
        ]
        registry = service.build_registry(assets)
        assert "ETHEREUM" in registry
        assert "BSC" in registry

    def test_address_lowered(self, service):
        assets = [self.make_asset("USDC", "ETHEREUM", "0xABC123")]
        registry = service.build_registry(assets)
        assert registry["ETHEREUM"][0].address == "0xabc123"

    def test_inactive_network_excluded(self, service):
        """Stablecoin with both deposit and withdraw disabled is excluded."""
        assets = [self.make_asset("USDT", "ETHEREUM", "0xusdt", deposit=False, withdraw=False)]
        registry = service.build_registry(assets)
        # Both deposit and withdraw disabled → network is inactive → excluded.
        assert "ETHEREUM" not in registry

    def test_active_network_included(self, service):
        """Stablecoin with both deposit and withdraw enabled is included."""
        assets = [self.make_asset("USDT", "ETHEREUM", "0xusdt", deposit=True, withdraw=True)]
        registry = service.build_registry(assets)
        assert registry["ETHEREUM"][0].deposit_enable is True
        assert registry["ETHEREUM"][0].withdraw_enable is True

    def test_stablecoin_addresses_for_network(self, service):
        assets = [
            self.make_asset("USDT", "ETHEREUM", "0xusdt"),
            self.make_asset("USDC", "ETHEREUM", "0xusdc"),
        ]
        registry = service.build_registry(assets)
        addresses = service.stablecoin_addresses_for_network(registry, "ETHEREUM")
        assert "0xusdt" in addresses
        assert "0xusdc" in addresses

    def test_stablecoin_addresses_empty_for_unknown_network(self, service):
        registry = {}
        addresses = service.stablecoin_addresses_for_network(registry, "UNKNOWN")
        assert addresses == set()

    def test_dai_included(self, service):
        assets = [self.make_asset("DAI", "ETHEREUM", "0xdai")]
        registry = service.build_registry(assets)
        assert any(r.coin == "DAI" for r in registry.get("ETHEREUM", []))


class TestQuoteRegistry:
    """Tests for the broad quote registry (all priceable arbitrage paths)."""

    @pytest.fixture
    def service(self):
        from services.stablecoin_registry_service import StablecoinRegistryService

        return StablecoinRegistryService()

    def make_asset(self, coin: str, network: str, address: str, deposit=True, withdraw=True):
        return MexcAsset(
            coin=coin,
            name=coin,
            networks=[
                MexcNetworkAsset(
                    coin=coin,
                    name=coin,
                    network_raw=network,
                    network_normalized=network,
                    contract_address=address,
                    deposit_enable=deposit,
                    withdraw_enable=withdraw,
                    withdraw_fee=Decimal("0.1"),
                    withdraw_min=Decimal("1"),
                    withdraw_max=Decimal("1000"),
                    min_confirm=12,
                ),
            ],
        )

    def test_stablecoin_is_quote(self, service):
        assets = [self.make_asset("USDT", "ETHEREUM", "0xusdt")]
        registry = service.build_quote_registry(assets, prices={})
        rec = registry["ETHEREUM"]["0xusdt"]
        assert rec.is_stable is True
        assert rec.coin == "USDT"

    def test_priced_coin_is_quote(self, service):
        """Non-stable MEXC coin with a USDT pair becomes a quote."""
        assets = [self.make_asset("LINK", "ETHEREUM", "0xlink")]
        registry = service.build_quote_registry(
            assets, prices={"LINKUSDT": Decimal("20")},
        )
        rec = registry["ETHEREUM"]["0xlink"]
        assert rec.is_stable is False
        assert rec.price_coin == "LINK"

    def test_unpriced_coin_not_quote(self, service):
        assets = [self.make_asset("OBSCURE", "ETHEREUM", "0xobscure")]
        registry = service.build_quote_registry(assets, prices={})
        assert "0xobscure" not in registry.get("ETHEREUM", {})

    def test_wrapped_native_added(self, service):
        """WETH is added as a quote priced via ETH even without MEXC contract."""
        registry = service.build_quote_registry(
            [], prices={"ETHUSDT": Decimal("3000")},
        )
        weth = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
        rec = registry["ETHEREUM"][weth]
        assert rec.is_stable is False
        assert rec.price_coin == "ETH"

    def test_wrapped_native_needs_price(self, service):
        """Wrapped natives without a MEXC price are NOT quotes."""
        registry = service.build_quote_registry([], prices={})
        assert "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2" not in registry.get("ETHEREUM", {})

    def test_pol_matic_alias(self, service):
        """WMATIC priced via MATIC pair when POL pair is absent."""
        registry = service.build_quote_registry(
            [], prices={"MATICUSDT": Decimal("0.5")},
        )
        wmatic = "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270"
        rec = registry["POLYGON"][wmatic]
        assert rec.price_coin == "MATIC"

    def test_quote_addresses_for_network(self, service):
        assets = [self.make_asset("USDT", "ETHEREUM", "0xusdt")]
        registry = service.build_quote_registry(assets, prices={})
        addrs = service.quote_addresses_for_network(registry, "ETHEREUM")
        assert "0xusdt" in addrs

    def test_stable_not_overwritten_by_duplicate(self, service):
        """Stablecoin record at an address is not replaced by a priced coin."""
        assets = [
            self.make_asset("USDT", "ETHEREUM", "0xsame"),
            self.make_asset("FAKE", "ETHEREUM", "0xsame"),
        ]
        registry = service.build_quote_registry(
            assets, prices={"FAKEUSDT": Decimal("2")},
        )
        assert registry["ETHEREUM"]["0xsame"].coin == "USDT"
