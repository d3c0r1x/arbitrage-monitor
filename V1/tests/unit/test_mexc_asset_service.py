"""Unit tests for MexcAssetService.

Tests:
- select_candidate_tokens filters by active networks
- select_candidate_tokens requires price pair
- select_candidate_tokens ranks by volume (no hard cutoff)
- No volume filter applied (plan principle #4)
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest


class TestMexcAssetService:
    """Tests for MexcAssetService."""

    @pytest.fixture
    def service(self, monkeypatch):
        """Create MexcAssetService with mocked settings."""
        from services.mexc_asset_service import MexcAssetService

        svc = MexcAssetService.__new__(MexcAssetService)
        svc._mexc_client = MagicMock()
        return svc

    @pytest.fixture
    def sample_asset(self):
        """Create a sample MexcAsset for testing."""
        from models.mexc_models import MexcAsset, MexcNetworkAsset

        asset = MexcAsset(coin="TEST", name="Test Token")
        asset.networks = [
            MexcNetworkAsset(
                coin="TEST", name="Test Token",
                network_raw="ETH", network_normalized="ETHEREUM",
                contract_address="0xtest123", deposit_enable=True,
                withdraw_enable=True, withdraw_fee=Decimal("0.001"),
                withdraw_min=Decimal("1"), withdraw_max=None, min_confirm=None,
            ),
        ]
        return asset

    def test_candidate_no_price_pair_skipped(self, service):
        """Token without USDT/USDC price is skipped."""
        assets = [MagicMock(coin="NO_PRICE", active_networks=MagicMock(return_value=[]))]
        prices = {}
        result = service.select_candidate_tokens(assets, prices)
        assert len(result) == 0

    def test_candidate_with_usdt_price_found(self, service, sample_asset):
        """Token with USDT price becomes a candidate."""
        prices = {"TESTUSDT": Decimal("1.50")}
        result = service.select_candidate_tokens([sample_asset], prices)
        assert len(result) == 1
        asset, network, contract, quote = result[0]
        assert asset.coin == "TEST"
        assert network == "ETHEREUM"
        assert contract == "0xtest123"
        assert quote == "USDT"

    def test_candidate_with_usdc_price_found(self, service, sample_asset):
        """Token with USDC price becomes a candidate."""
        prices = {"TESTUSDC": Decimal("1.50")}
        result = service.select_candidate_tokens([sample_asset], prices)
        assert len(result) == 1
        assert result[0][3] == "USDC"

    def test_candidate_both_usdt_and_usdc(self, service, sample_asset):
        """Token with both USDT and USDC prices gets two candidate entries."""
        prices = {"TESTUSDT": Decimal("1.50"), "TESTUSDC": Decimal("1.49")}
        result = service.select_candidate_tokens([sample_asset], prices)
        assert len(result) == 2
        quotes = {r[3] for r in result}
        assert quotes == {"USDT", "USDC"}

    def test_partial_enabled_network_kept(self, service):
        """Network with only withdraw enabled IS a candidate (direction B)."""
        from models.mexc_models import MexcAsset, MexcNetworkAsset

        asset = MexcAsset(coin="TEST", name="Test")
        asset.networks = [
            MexcNetworkAsset(
                coin="TEST", name="Test",
                network_raw="ETH", network_normalized="ETHEREUM",
                contract_address="0xeth", deposit_enable=False,
                withdraw_enable=True, withdraw_fee=None,
                withdraw_min=None, withdraw_max=None, min_confirm=None,
            ),
        ]
        prices = {"TESTUSDT": Decimal("1.00")}
        result = service.select_candidate_tokens([asset], prices)
        # tradable_networks(): deposit OR withdraw -> candidate
        assert len(result) == 1

    def test_fully_disabled_network_skipped(self, service):
        """Network with deposit AND withdraw disabled is skipped."""
        from models.mexc_models import MexcAsset, MexcNetworkAsset

        asset = MexcAsset(coin="TEST", name="Test")
        asset.networks = [
            MexcNetworkAsset(
                coin="TEST", name="Test",
                network_raw="ETH", network_normalized="ETHEREUM",
                contract_address="0xeth", deposit_enable=False,
                withdraw_enable=False, withdraw_fee=None,
                withdraw_min=None, withdraw_max=None, min_confirm=None,
            ),
        ]
        prices = {"TESTUSDT": Decimal("1.00")}
        result = service.select_candidate_tokens([asset], prices)
        assert len(result) == 0

    def test_unsupported_network_skipped(self, service):
        """Network not in NETWORKS dict is skipped."""
        from models.mexc_models import MexcAsset, MexcNetworkAsset

        asset = MexcAsset(coin="TEST", name="Test")
        asset.networks = [
            MexcNetworkAsset(
                coin="TEST", name="Test",
                network_raw="UNSUPPORTED", network_normalized="UNSUPPORTED",
                contract_address="0xabc", deposit_enable=True,
                withdraw_enable=True, withdraw_fee=None,
                withdraw_min=None, withdraw_max=None, min_confirm=None,
            ),
        ]
        prices = {"TESTUSDT": Decimal("1.00")}
        result = service.select_candidate_tokens([asset], prices)
        assert len(result) == 0

    def test_empty_contract_skipped(self, service):
        """Asset with empty contract address is skipped."""
        from models.mexc_models import MexcAsset, MexcNetworkAsset

        asset = MexcAsset(coin="TEST", name="Test")
        asset.networks = [
            MexcNetworkAsset(
                coin="TEST", name="Test",
                network_raw="ETH", network_normalized="ETHEREUM",
                contract_address="", deposit_enable=True,
                withdraw_enable=True, withdraw_fee=None,
                withdraw_min=None, withdraw_max=None, min_confirm=None,
            ),
        ]
        prices = {"TESTUSDT": Decimal("1.00")}
        result = service.select_candidate_tokens([asset], prices)
        assert len(result) == 0

    def test_volume_ranking_all_candidates_sorted(self, service, monkeypatch):
        """Candidates are sorted by 24h volume descending, all kept."""
        from models.mexc_models import MexcAsset, MexcNetworkAsset

        # Create assets with different contract addresses
        assets = []
        for _i, coin in enumerate(["HIGH", "MEDIUM", "LOW"]):
            asset = MexcAsset(coin=coin, name=coin)
            asset.networks = [
                MexcNetworkAsset(
                    coin=coin, name=coin,
                    network_raw="ETH", network_normalized="ETHEREUM",
                    contract_address=f"0x{coin.lower()}",
                    deposit_enable=True, withdraw_enable=True,
                    withdraw_fee=None, withdraw_min=None,
                    withdraw_max=None, min_confirm=None,
                ),
            ]
            assets.append(asset)

        prices = {"HIGHUSDT": Decimal("10"), "MEDIUMUSDT": Decimal("5"), "LOWUSDT": Decimal("1")}
        volumes = {"HIGHUSDT": Decimal("1000000"), "MEDIUMUSDT": Decimal("500000"), "LOWUSDT": Decimal("1000")}

        result = service.select_candidate_tokens(assets, prices, volumes)
        # All 3 candidates kept (no MAX_CANDIDATES limit)
        assert len(result) == 3
        # HIGH should be first (highest volume)
        assert result[0][0].coin == "HIGH"
        assert result[1][0].coin == "MEDIUM"
        assert result[2][0].coin == "LOW"

    def test_no_volume_filter_applied(self, service, monkeypatch):
        """Low volume tokens are NOT filtered (plan principle #4: scan all pairs)."""
        from models.mexc_models import MexcAsset, MexcNetworkAsset

        asset_high = MexcAsset(coin="HIGH", name="High Vol")
        asset_high.networks = [
            MexcNetworkAsset(
                coin="HIGH", name="High Vol",
                network_raw="ETH", network_normalized="ETHEREUM",
                contract_address="0xhigh", deposit_enable=True,
                withdraw_enable=True, withdraw_fee=None,
                withdraw_min=None, withdraw_max=None, min_confirm=None,
            ),
        ]
        asset_low = MexcAsset(coin="LOW", name="Low Vol")
        asset_low.networks = [
            MexcNetworkAsset(
                coin="LOW", name="Low Vol",
                network_raw="ETH", network_normalized="ETHEREUM",
                contract_address="0xlow", deposit_enable=True,
                withdraw_enable=True, withdraw_fee=None,
                withdraw_min=None, withdraw_max=None, min_confirm=None,
            ),
        ]

        prices = {"HIGHUSDT": Decimal("10"), "LOWUSDT": Decimal("1")}
        volumes = {"HIGHUSDT": Decimal("100000"), "LOWUSDT": Decimal("1000")}

        result = service.select_candidate_tokens([asset_high, asset_low], prices, volumes)
        # Both kept — no volume filter
        assert len(result) == 2

    def test_no_volume_dict_does_not_filter(self, service, monkeypatch):
        """Without volumes dict, all candidates with price pair are kept."""
        from models.mexc_models import MexcAsset, MexcNetworkAsset

        asset = MexcAsset(coin="LOW", name="Low Vol")
        asset.networks = [
            MexcNetworkAsset(
                coin="LOW", name="Low Vol",
                network_raw="ETH", network_normalized="ETHEREUM",
                contract_address="0xlow", deposit_enable=True,
                withdraw_enable=True, withdraw_fee=None,
                withdraw_min=None, withdraw_max=None, min_confirm=None,
            ),
        ]

        prices = {"LOWUSDT": Decimal("1")}
        # No volumes dict -> no filter
        result = service.select_candidate_tokens([asset], prices, volumes=None)
        assert len(result) == 1

    def test_get_candidate_summary(self, service, sample_asset):
        """get_candidate_summary returns correct counts."""
        prices = {"TESTUSDT": Decimal("1.50")}
        candidates = service.select_candidate_tokens([sample_asset], prices)
        summary = service.get_candidate_summary(candidates)
        assert summary["total_candidates"] == 1
        assert summary["unique_tokens"] == 1
        assert "ETHEREUM" in summary["networks"]

    def test_case_sensitive_coin_lookup(self, service, monkeypatch):
        """Coin lookup is case-insensitive (uppercased internally)."""
        from models.mexc_models import MexcAsset, MexcNetworkAsset

        asset = MexcAsset(coin="test_lower", name="lowercase")
        asset.networks = [
            MexcNetworkAsset(
                coin="test_lower", name="lowercase",
                network_raw="ETH", network_normalized="ETHEREUM",
                contract_address="0xlower", deposit_enable=True,
                withdraw_enable=True, withdraw_fee=None,
                withdraw_min=None, withdraw_max=None, min_confirm=None,
            ),
        ]
        # Price key is uppercase
        prices = {"TEST_LOWERUSDT": Decimal("1.00")}
        result = service.select_candidate_tokens([asset], prices)
        assert len(result) == 1
