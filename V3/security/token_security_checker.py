"""
Token security checker — bytecode analysis.

Detects risky functions by searching for their 4-byte selectors in
the contract's deployed bytecode (PUSH4 opcode = 0x63 + selector).

This avoids false positives from eth_call probing (most contracts
return "0x" for unknown selectors without reverting).

Checks:
- mint(address,uint256) — arbitrary minting
- pause() / unpause() — transfer freezing
- blacklist(address) / setBlacklist — address blocking
- setFee / setTax — hidden tax changes

For EIP-1967 proxies, reads the implementation address from the
canonical slot and analyzes the implementation bytecode.

Security warnings NEVER remove signals.
The scanner only calls this for profitable signals (>1% net profit).
"""

import logging
import time

logger = logging.getLogger(__name__)

# EIP-1967 implementation slot:
# keccak256("eip1967.proxy.implementation") - 1
_EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"

# Risk selectors to search for in bytecode.
# Format: 4-byte hex (without 0x prefix) → warning name
_RISK_SELECTORS: dict[str, str] = {
    "40c10f19": "mint",           # mint(address,uint256)
    "8456cb59": "pause",          # pause()
    "3f4ba83a": "unpause",        # unpause()
    "2c3496db": "blacklist",      # blacklist(address)
    "a7ee80e0": "set_blacklist",  # setBlacklist(address,bool)
    "69fe0e2d": "set_fee",        # setFee(uint256)
    "8c0b5e22": "set_max_tx",    # setMaxTx(uint256)
}

# Cache: (network, address) → (timestamp, warnings)
_CODE_CACHE: dict[tuple[str, str], tuple[float, list[str]]] = {}
_CACHE_TTL_SEC = 300


class TokenSecurityChecker:
    """Checks token security via bytecode selector analysis."""

    def __init__(self, rpc_client_factory, multicall_client=None):
        self._rpc_client_factory = rpc_client_factory
        self._multicall_client = multicall_client

    async def check(
        self,
        network: str,
        token_address: str,
    ) -> list[str]:
        """Run security checks on a token via bytecode analysis.

        Args:
            network: Internal network name.
            token_address: Token contract address.

        Returns:
            List of warning strings (empty if no risks detected).
        """
        key = (network, token_address.lower())

        # Check cache.
        cached = _CODE_CACHE.get(key)
        if cached is not None:
            ts, warnings = cached
            if time.time() - ts < _CACHE_TTL_SEC:
                return warnings

        rpc = self._rpc_client_factory(network)
        if rpc is None:
            return ["security_check_unavailable: no rpc for network"]

        try:
            bytecode_hex = await rpc.eth_get_code(token_address)
        except Exception as exc:
            logger.debug("token_get_code_failed: %s %s: %s", network, token_address[:10], exc)
            return ["security_check_unavailable: eth_getCode failed"]

        if not bytecode_hex or bytecode_hex == "0x" or bytecode_hex == "0x0":
            # EOA or self-destructed contract — no code to analyze.
            warnings = ["token_no_code: EOA or destroyed contract"]
            _CODE_CACHE[key] = (time.time(), warnings)
            return warnings

        # Check if this is an EIP-1967 proxy.
        bytecode = bytecode_hex[2:] if bytecode_hex.startswith("0x") else bytecode_hex
        impl_warnings = await self._check_proxy(network, token_address, rpc, bytecode)

        # Analyze the main bytecode.
        warnings = self._scan_bytecode(bytecode)

        # Merge proxy implementation warnings.
        if impl_warnings:
            warnings = list(set(warnings + impl_warnings))

        _CODE_CACHE[key] = (time.time(), warnings)
        return warnings

    async def _check_proxy(
        self, network: str, token_address: str, rpc, bytecode: str
    ) -> list[str]:
        """If contract is an EIP-1967 proxy, analyze implementation bytecode."""
        try:
            slot_value = await rpc.eth_get_storage_at(token_address, _EIP1967_IMPL_SLOT)
            if not slot_value or slot_value == "0x" + "0" * 64:
                return []

            # Extract address from 32-byte slot (last 20 bytes).
            impl_addr = "0x" + slot_value[-40:]
            if impl_addr == "0x" + "0" * 40:
                return []

            # Fetch implementation bytecode.
            impl_code_hex = await rpc.eth_get_code(impl_addr)
            if not impl_code_hex or impl_code_hex == "0x":
                return []

            impl_bytecode = impl_code_hex[2:] if impl_code_hex.startswith("0x") else impl_code_hex
            return self._scan_bytecode(impl_bytecode)

        except Exception as exc:
            logger.debug("proxy_check_failed: %s: %s", token_address[:10], exc)
            return []

    @staticmethod
    def _scan_bytecode(bytecode: str) -> list[str]:
        """Search bytecode for PUSH4 (0x63) + known risk selectors.

        Returns list of warning strings for detected risks.
        """
        warnings: list[str] = []
        bytecode_lower = bytecode.lower()

        for selector_hex, name in _RISK_SELECTORS.items():
            # PUSH4 opcode is 0x63, followed by 4 bytes of the selector.
            pattern = "63" + selector_hex
            if pattern in bytecode_lower:
                warnings.append(f"token_{name}_detected")

        return warnings

    def clear_cache(self) -> None:
        """Clear the bytecode analysis cache."""
        _CODE_CACHE.clear()
