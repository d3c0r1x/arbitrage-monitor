"""
Unit tests for decimal utility functions.
"""

from decimal import Decimal


class TestDecimalUtils:
    """Tests for decimal_utils module."""

    def test_to_decimal_from_int(self):
        from utils.decimal_utils import to_decimal

        assert to_decimal(42) == Decimal("42")

    def test_to_decimal_from_str(self):
        from utils.decimal_utils import to_decimal

        assert to_decimal("42.5") == Decimal("42.5")

    def test_to_decimal_none_returns_default(self):
        from utils.decimal_utils import to_decimal

        assert to_decimal(None, Decimal("0")) == Decimal("0")

    def test_to_decimal_or_zero(self):
        from utils.decimal_utils import to_decimal_or_zero

        assert to_decimal_or_zero(None) == Decimal("0")
        assert to_decimal_or_zero("12.34") == Decimal("12.34")

    def test_quantize_money(self):
        from utils.decimal_utils import quantize_money

        assert quantize_money(Decimal("12.345")) == Decimal("12.35")
        assert quantize_money(Decimal("12.3")) == Decimal("12.30")

    def test_floor_to_wei(self):
        from utils.decimal_utils import floor_to_wei

        result = floor_to_wei(Decimal("1.23456789"), 18)
        assert isinstance(result, int)
        assert result > 0

    def test_floor_to_wei_zero(self):
        from utils.decimal_utils import floor_to_wei

        assert floor_to_wei(Decimal("0"), 18) == 0


class TestAddressUtils:
    """Tests for address_utils module."""

    def test_valid_evm_address(self):
        from utils.address_utils import is_valid_evm_address

        assert is_valid_evm_address("0x1234567890abcdef1234567890abcdef12345678")

    def test_invalid_evm_address(self):
        from utils.address_utils import is_valid_evm_address

        assert not is_valid_evm_address("")
        assert not is_valid_evm_address("not_an_address")
        assert not is_valid_evm_address("0xshort")

    def test_normalize_address(self):
        from utils.address_utils import normalize_address

        # Full 40-hex-char address.
        result = normalize_address("0x1234567890ABCDEF1234567890ABCDEF12345678")
        assert result == "0x1234567890abcdef1234567890abcdef12345678"

    def test_normalize_address_invalid(self):
        from utils.address_utils import normalize_address

        assert normalize_address(None) is None
        assert normalize_address("") is None
        assert normalize_address("0xshort") is None

    def test_is_zero_address(self):
        from utils.address_utils import is_zero_address

        assert is_zero_address("0x0000000000000000000000000000000000000000")
        assert not is_zero_address("0x1234567890abcdef1234567890abcdef12345678")
