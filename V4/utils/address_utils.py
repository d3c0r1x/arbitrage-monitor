"""
Address validation and normalization utilities.
"""

import re

_ETH_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def is_valid_evm_address(address: str) -> bool:
    """Check if a string is a valid EVM address (0x + 40 hex chars).

    Accepts both checksummed and lowercased addresses.
    """
    if not address:
        return False
    return bool(_ETH_ADDRESS_RE.match(address))


def normalize_address(address: str) -> str | None:
    """Lowercase an address and validate it.

    Returns None if the address is invalid.
    """
    if not address:
        return None
    cleaned = address.strip().lower()
    if is_valid_evm_address(cleaned):
        return cleaned
    return None


def is_zero_address(address: str) -> bool:
    """Check if an address is the zero address."""
    cleaned = address.strip().lower()
    return cleaned == "0x0000000000000000000000000000000000000000"
