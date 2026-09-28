"""
Decimal conversion and formatting utilities.

All monetary values in this project use Decimal — never float.
"""

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


def to_decimal(value: Any, default: Decimal | None = None) -> Decimal | None:
    """Safely convert a value to Decimal.

    Args:
        value: Input value (int, float, str, Decimal).
        default: Returned if conversion fails.

    Returns:
        Decimal value or default if conversion fails.
    """
    if isinstance(value, Decimal):
        return value
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def to_decimal_or_zero(value: Any) -> Decimal:
    """Convert to Decimal or return Decimal('0')."""
    result = to_decimal(value, Decimal("0"))
    if result is None:
        return Decimal("0")
    return result


def quantize_money(value: Decimal) -> Decimal:
    """Round to 2 decimal places with HALF_UP rounding.

    Suitable for display in USD.
    """
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def quantize_price(value: Decimal, precision: int = 8) -> Decimal:
    """Round to the given number of decimal places.

    Suitable for token prices which may need more precision than 2 dp.
    """
    fmt = f"0.{'0' * precision}"
    return value.quantize(Decimal(fmt), rounding=ROUND_HALF_UP)


def floor_to_wei(value: Decimal, decimals: int = 18) -> int:
    """Floor a Decimal token amount to an integer in the smallest unit.

    Example:
        floor_to_wei(Decimal('1.234'), 18) -> 1234000000000000000
    """
    factor = Decimal(10) ** decimals
    scaled = value * factor
    return int(scaled.to_integral_value(rounding=ROUND_DOWN))
