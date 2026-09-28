"""
Pool version detector — network-agnostic.

Detects v1 (Solidly), v2 (Uniswap-V2), v3 (Uniswap-V3) by on-chain methods.
V4 pools are not classic pair contracts (PoolManager + PoolKey); version
comes from discovery labels / registry, not eth_call on pool_address.
"""

import logging

logger = logging.getLogger(__name__)


class PoolDetector:
    """Detects pool version by on-chain inspection on any EVM network."""

    def __init__(self, rpc_client_factory):
        self._rpc_client_factory = rpc_client_factory

    async def detect_version(
        self,
        network: str,
        pool_address: str,
    ) -> str | None:
        """Detect pool version.

        Order:
        - V1 Solidly-family: stable() bool (before V2 — pairs often also
          expose getReserves).
        - V3: fee() uint24
        - V2: getReserves()

        Returns:
            "v1", "v2", "v3", or None. Never invents "v4" from address alone.
        """
        rpc = self._rpc_client_factory(network)
        if rpc is None:
            return None

        # V1 Solidly-family (any chain): stable() 0x22be3de1
        try:
            result = await rpc.eth_call(to=pool_address, data="0x22be3de1")
            if result and result != "0x" and len(result) >= 66:
                return "v1"
        except Exception:
            pass

        # V3: fee() 0xddca3f43
        try:
            result = await rpc.eth_call(to=pool_address, data="0xddca3f43")
            if result and result != "0x" and len(result) >= 66:
                return "v3"
        except Exception:
            pass

        # V2: getReserves() 0x0902f1ac
        try:
            result = await rpc.eth_call(to=pool_address, data="0x0902f1ac")
            if result and result != "0x":
                return "v2"
        except Exception:
            pass

        return None
