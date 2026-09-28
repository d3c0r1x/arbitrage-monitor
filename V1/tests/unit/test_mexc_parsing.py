"""
Unit tests for MEXC client parsing.

Tests:
- Capital config parsing
- Contract field parsing (uses "contract", not "contractAddress")
- depositEnable/withdrawEnable parsing
- Decimal conversion
"""

from decimal import Decimal

import pytest


class TestMexcClientParsing:
    """Tests for MexcClient.parse_capital_config."""

    @pytest.fixture
    def sample_capital_item(self):
        """Sample capital config item for a token."""
        return {
            "coin": "SCR",
            "name": "Scroll",
            "networkList": [
                {
                    "network": "ETH",
                    "contract": "0x1234567890abcdef1234567890abcdef12345678",
                    "depositEnable": True,
                    "withdrawEnable": True,
                    "withdrawFee": "0.5",
                    "withdrawMin": "1",
                    "withdrawMax": "100",
                    "minConfirm": 12,
                },
            ],
        }

    def test_parse_single_asset(self, sample_capital_item):
        from clients.mexc_client import MexcClient

        client = MexcClient.__new__(MexcClient)
        assets = client.parse_capital_config([sample_capital_item])
        assert len(assets) == 1
        assert assets[0].coin == "SCR"
        assert assets[0].name == "Scroll"

    def test_contract_address_lowered(self):
        from clients.mexc_client import MexcClient

        client = MexcClient.__new__(MexcClient)
        raw = [
            {
                "coin": "TEST",
                "name": None,
                "networkList": [
                    {
                        "network": "ETH",
                        "contract": "0xABC123DEF456",
                        "depositEnable": True,
                        "withdrawEnable": True,
                    },
                ],
            },
        ]
        assets = client.parse_capital_config(raw)
        assert assets[0].networks[0].contract_address == "0xabc123def456"

    def test_empty_contract_skipped(self):
        from clients.mexc_client import MexcClient

        client = MexcClient.__new__(MexcClient)
        raw = [
            {
                "coin": "TEST",
                "name": None,
                "networkList": [
                    {
                        "network": "ETH",
                        "contract": "",
                        "depositEnable": True,
                        "withdrawEnable": True,
                    },
                ],
            },
        ]
        assets = client.parse_capital_config(raw)
        assert len(assets) == 0

    def test_deposit_enable_parsing(self):
        from clients.mexc_client import MexcClient

        client = MexcClient.__new__(MexcClient)
        raw = [
            {
                "coin": "TEST",
                "name": None,
                "networkList": [
                    {
                        "network": "ETH",
                        "contract": "0xabc",
                        "depositEnable": False,
                        "withdrawEnable": True,
                    },
                ],
            },
        ]
        assets = client.parse_capital_config(raw)
        network = assets[0].networks[0]
        assert network.deposit_enable is False
        assert network.withdraw_enable is True

    def test_active_networks_filters_disabled(self):
        from clients.mexc_client import MexcClient

        client = MexcClient.__new__(MexcClient)
        raw = [
            {
                "coin": "TEST",
                "name": None,
                "networkList": [
                    {
                        "network": "ETH",
                        "contract": "0xabc",
                        "depositEnable": True,
                        "withdrawEnable": True,
                    },
                    {
                        "network": "BSC",
                        "contract": "0xdef",
                        "depositEnable": False,
                        "withdrawEnable": True,
                    },
                ],
            },
        ]
        assets = client.parse_capital_config(raw)
        active = assets[0].active_networks()
        assert len(active) == 1
        assert active[0].network_normalized == "ETHEREUM"

    def test_withdraw_fee_parsing(self):
        from clients.mexc_client import MexcClient

        client = MexcClient.__new__(MexcClient)
        raw = [
            {
                "coin": "TEST",
                "name": None,
                "networkList": [
                    {
                        "network": "ETH",
                        "contract": "0xabc",
                        "depositEnable": True,
                        "withdrawEnable": True,
                        "withdrawFee": "0.001",
                    },
                ],
            },
        ]
        assets = client.parse_capital_config(raw)
        assert assets[0].networks[0].withdraw_fee == Decimal("0.001")

    def test_invalid_withdraw_fee_returns_none(self):
        from clients.mexc_client import MexcClient

        client = MexcClient.__new__(MexcClient)
        raw = [
            {
                "coin": "TEST",
                "name": None,
                "networkList": [
                    {
                        "network": "ETH",
                        "contract": "0xabc",
                        "depositEnable": True,
                        "withdrawEnable": True,
                        "withdrawFee": None,
                    },
                ],
            },
        ]
        assets = client.parse_capital_config(raw)
        assert assets[0].networks[0].withdraw_fee is None

    def test_missing_contract_field_uses_contract(self):
        """MEXC uses 'contract' field, NOT 'contractAddress'."""
        from clients.mexc_client import MexcClient

        client = MexcClient.__new__(MexcClient)
        raw = [
            {
                "coin": "TEST",
                "name": None,
                "networkList": [
                    {
                        "network": "ETH",
                        "contractAddress": "0xabc",  # Should NOT be used.
                        "depositEnable": True,
                        "withdrawEnable": True,
                    },
                ],
            },
        ]
        assets = client.parse_capital_config(raw)
        # contractAddress is ignored, contract is empty -> skipped.
        assert len(assets) == 0

    def test_unknown_network_skipped(self):
        from clients.mexc_client import MexcClient

        client = MexcClient.__new__(MexcClient)
        raw = [
            {
                "coin": "TEST",
                "name": None,
                "networkList": [
                    {
                        "network": "UNKNOWN_CHAIN",
                        "contract": "0xabc",
                        "depositEnable": True,
                        "withdrawEnable": True,
                    },
                ],
            },
        ]
        assets = client.parse_capital_config(raw)
        assert len(assets) == 0

    def test_min_confirm_preserved(self):
        from clients.mexc_client import MexcClient

        client = MexcClient.__new__(MexcClient)
        raw = [
            {
                "coin": "TEST",
                "name": None,
                "networkList": [
                    {
                        "network": "ETH",
                        "contract": "0xabc",
                        "depositEnable": True,
                        "withdrawEnable": True,
                        "minConfirm": 12,
                    },
                ],
            },
        ]
        assets = client.parse_capital_config(raw)
        assert assets[0].networks[0].min_confirm == 12
