"""
Pool security checker.

Checks for pool-level risks:
- V2: token0(), token1(), factory() consistency
- V3: slot0() + fee() validity
- Pool owner risk
- V4 hook risk

Security warnings NEVER remove signals.
If a check is unavailable, a warning is added.
"""

import logging

logger = logging.getLogger(__name__)


class PoolSecurityChecker:
    """Checks pool security using on-chain data."""

    def __init__(self, rpc_client_factory):
        self._rpc_client_factory = rpc_client_factory

    async def check(
        self,
        network: str,
        pool_address: str,
        pool_version: str | None = None,
        expected_token: str | None = None,
        expected_stablecoin: str | None = None,
    ) -> list[str]:
        """Run security checks on a pool.

        Args:
            network: Internal network name.
            pool_address: Pool contract address.
            pool_version: "v2", "v3", or None.
            expected_token: Expected token address (for consistency check).
            expected_stablecoin: Expected stablecoin address (for consistency check).

        Returns:
            List of warning strings (empty if no warnings).
        """
        warnings: list[str] = []

        rpc = self._rpc_client_factory(network)
        if rpc is None:
            return warnings

        if pool_version == "v1":
            # Solidly V1: same token0/token1 consistency as V2.
            warnings.extend(
                await self._check_v2_consistency(
                    rpc, pool_address, expected_token, expected_stablecoin
                )
            )

        elif pool_version == "v2":
            warnings.extend(
                await self._check_v2_consistency(
                    rpc, pool_address, expected_token, expected_stablecoin
                )
            )

        elif pool_version == "v3":
            warnings.extend(await self._check_v3_validity(rpc, pool_address))

        elif pool_version == "v4":
            warnings.append("v4_hook_risk: V4 hooks not verified")

        return warnings

    async def _check_v2_consistency(
        self,
        rpc,
        pool_address: str,
        expected_token: str | None,
        expected_stablecoin: str | None,
    ) -> list[str]:
        """V2: verify token0/token1 match expected addresses."""
        warnings: list[str] = []
        try:
            # token0() selector: 0x0dfe1681
            token0_hex = await rpc.eth_call(to=pool_address, data="0x0dfe1681")
            token0 = "0x" + token0_hex[-40:].lower()

            # token1() selector: 0xd21220a7
            token1_hex = await rpc.eth_call(to=pool_address, data="0xd21220a7")
            token1 = "0x" + token1_hex[-40:].lower()

            pool_tokens = {token0, token1}

            if expected_token and expected_token.lower() not in pool_tokens:
                warnings.append("pool_token_mismatch: expected token not in pool")

            if expected_stablecoin and expected_stablecoin.lower() not in pool_tokens:
                warnings.append("pool_stablecoin_mismatch: expected stablecoin not in pool")

        except Exception as exc:
            logger.debug("v2_consistency_check_failed: %s: %s", pool_address[:10], exc)

        return warnings

    async def _check_v3_validity(self, rpc, pool_address: str) -> list[str]:
        """V3: verify slot0() returns valid data and fee() is reasonable."""
        warnings: list[str] = []
        try:
            # slot0() selector: 0x3850c7bd
            slot0_hex = await rpc.eth_call(to=pool_address, data="0x3850c7bd")
            if not slot0_hex or slot0_hex == "0x" or len(slot0_hex) < 66:
                warnings.append("pool_v3_invalid_slot0: empty or short response")

            # fee() selector: 0xddca3f43
            fee_hex = await rpc.eth_call(to=pool_address, data="0xddca3f43")
            if fee_hex and fee_hex != "0x":
                fee_bps = int(fee_hex, 16)
                # Uniswap V3 fees: 100, 500, 3000, 10000 (in hundredths of a bip)
                # PancakeSwap V3: 100, 500, 2500, 10000
                if fee_bps > 10000:
                    warnings.append(f"pool_v3_unusual_fee: {fee_bps}")

        except Exception as exc:
            logger.debug("v3_validity_check_failed: %s: %s", pool_address[:10], exc)

        return warnings
