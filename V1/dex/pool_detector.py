"""
Pool version detector.

Detects DEX pool version (v2, v3) by checking on-chain methods.
"""

import logging

logger = logging.getLogger(__name__)


class PoolDetector:
    """Detects pool version by on-chain inspection."""

    def __init__(self, rpc_client_factory):
        self._rpc_client_factory = rpc_client_factory

    async def detect_version(
        self,
        network: str,
        pool_address: str,
    ) -> str | None:
        """Detect pool version by checking available methods.

        V3 pools have a 'fee()' method that returns uint24.
        V2 pools have 'getReserves()' method.

        Args:
            network: Internal network name.
            pool_address: Pool contract address.

        Returns:
            "v2" or "v3" or None if detection fails.
        """
        rpc = self._rpc_client_factory(network)
        if rpc is None:
            return None

        # Check V3: try fee() method selector 0xddca3f43
        try:
            result = await rpc.eth_call(
                to=pool_address,
                data="0xddca3f43",
            )
            if result and result != "0x":
                return "v3"
        except Exception:
            pass

        # Check V2: try getReserves() method selector 0x0902f1ac
        try:
            result = await rpc.eth_call(
                to=pool_address,
                data="0x0902f1ac",
            )
            if result and result != "0x":
                return "v2"
        except Exception:
            pass

        return None
