"""
On-chain factory discovery source.

Uses DEX registry factories and RPC eth_call/multicall
to find pools by token address directly on-chain.

This is a fallback when HTTP API sources are unavailable.
"""

import logging

from config.dex_registry import DEX_REGISTRY
from config.settings import settings
from config.wrapped_natives import wrapped_natives_for_network
from discovery.base_source import BaseSource
from models.pool_models import DiscoveredPool

logger = logging.getLogger(__name__)


class OnchainFactorySource(BaseSource):
    """Pool discovery via on-chain factory contracts."""

    source_name: str = "onchain_factory"

    def __init__(self, rpc_client_factory, stablecoin_registry_service):
        self._rpc_client_factory = rpc_client_factory
        self._stablecoin_registry_service = stablecoin_registry_service
        self._stablecoin_registry: dict[str, list] = {}
        self._quote_registry: dict[str, dict] = {}
        # Per-refresh-cycle probe budget (tokens). Reset by pool_refresh_task.
        self._tokens_probed = 0

    def reset_cycle_budget(self) -> None:
        """Reset the per-cycle token probe counter (call at refresh start)."""
        if self._tokens_probed:
            logger.info(
                "onchain_factory_budget_reset: probed_last_cycle=%d",
                self._tokens_probed,
            )
        self._tokens_probed = 0

    def set_stablecoin_registry(self, registry: dict[str, list]) -> None:
        self._stablecoin_registry = registry

    def set_quote_registry(self, quote_registry) -> None:
        """Store the broad quote registry (address -> QuoteRecord per network)."""
        if isinstance(quote_registry, dict):
            self._quote_registry = quote_registry

    def is_available(self) -> bool:
        return True

    def _probe_addresses(self, network: str) -> set[str]:
        """Quote addresses to probe on-chain: stablecoins + wrapped natives.

        Bounded on purpose — this source is an RPC fallback; probing every
        priceable MEXC coin would explode eth_call volume. Stables plus
        wrapped natives cover the overwhelming majority of DEX pairs.
        """
        addrs = self._stablecoin_registry_service.stablecoin_addresses_for_network(
            registry=self._stablecoin_registry,
            network=network,
        )
        addrs = set(addrs) if addrs else set()
        addrs.update(wrapped_natives_for_network(network).keys())
        return addrs

    async def fetch_pools_by_token(
        self,
        network: str,
        token_address: str,
    ) -> list[DiscoveredPool]:
        # Enforce per-cycle probe budget: this source is a fallback, an
        # unbounded run floods the RPC with eth_call and stalls the refresh.
        budget = settings.ONCHAIN_FACTORY_MAX_TOKENS_PER_CYCLE
        if budget > 0 and self._tokens_probed >= budget:
            logger.debug(
                "onchain_budget_exhausted: %s %s", network, token_address[:10]
            )
            return []
        self._tokens_probed += 1

        quote_addresses = self._probe_addresses(network)

        if not quote_addresses:
            logger.info("onchain_no_quotes: %s", network)
            return []

        network_config = DEX_REGISTRY.get(network)
        if not network_config:
            return []

        pools: list[DiscoveredPool] = []

        for dex_config in network_config.get("dexes", []):
            dex_id = dex_config["dex_id"]
            version = dex_config.get("version")
            factory = dex_config.get("factory", "")

            if factory.startswith("TODO_"):
                logger.info("onchain_skip_todo_dex: %s on %s", dex_id, network)
                continue

            if version == "v1":
                style = str(dex_config.get("style", "solidly")).lower()
                if style in ("uniswap_v2", "v2", "pancake"):
                    # Pancake V1 etc. — classic getPair(address,address)
                    for stablecoin_addr in quote_addresses:
                        pool = await self._check_v2_factory(
                            network=network,
                            factory=factory,
                            dex_id=dex_id,
                            token_address=token_address,
                            stablecoin_address=stablecoin_addr,
                        )
                        if pool:
                            pools.append(pool)
                else:
                    # Solidly-family on ANY network: getPool/getPair(tokenA,tokenB,stable)
                    for stablecoin_addr in quote_addresses:
                        for is_stable in (False, True):
                            pool = await self._check_v1_factory(
                                network=network,
                                factory=factory,
                                dex_id=dex_id,
                                token_address=token_address,
                                stablecoin_address=stablecoin_addr,
                                stable=is_stable,
                            )
                            if pool:
                                pools.append(pool)

            elif version == "v2":
                for stablecoin_addr in quote_addresses:
                    pool = await self._check_v2_factory(
                        network=network,
                        factory=factory,
                        dex_id=dex_id,
                        token_address=token_address,
                        stablecoin_address=stablecoin_addr,
                    )
                    if pool:
                        pools.append(pool)

            elif version == "v3":
                fee_tiers = dex_config.get("fee_tiers_bps", [30])
                for stablecoin_addr in quote_addresses:
                    for fee_bps in fee_tiers:
                        pool = await self._check_v3_factory(
                            network=network,
                            factory=factory,
                            dex_id=dex_id,
                            token_address=token_address,
                            stablecoin_address=stablecoin_addr,
                            fee_bps=fee_bps,
                        )
                        if pool:
                            pools.append(pool)

            elif version == "v4":
                # V4 pools live in PoolManager (singleton); no classic
                # factory getPool by token pair alone. Discovery comes from
                # DexScreener/Gecko; onchain probe skipped intentionally.
                continue

        return pools

    async def _check_v1_factory(
        self, network, factory, dex_id, token_address, stablecoin_address, stable: bool,
    ):
        """Solidly-family factory getPool/getPair(tokenA, tokenB, stable) — any chain."""
        rpc = self._rpc_client_factory(network)
        if rpc is None:
            return None
        # Prefer getPool; fall back to getPair (same args) used by some forks.
        for selector in ("0x79bc57d5", "0x6801cc30"):
            try:
                stable_hex = ("1" if stable else "0").zfill(64)
                data = (
                    selector
                    + token_address[2:].zfill(64)
                    + stablecoin_address[2:].zfill(64)
                    + stable_hex
                )
                result = await rpc.eth_call(to=factory, data=data)
                pool_addr = "0x" + result[-40:].lower()
                if pool_addr == "0x0000000000000000000000000000000000000000":
                    continue
                return DiscoveredPool(
                    network=network,
                    pool_address=pool_addr,
                    dex=dex_id,
                    token0_address=token_address,
                    token1_address=stablecoin_address,
                    sources={"onchain_factory"},
                )
            except Exception:
                continue
        return None

    async def _check_v2_factory(self, network, factory, dex_id, token_address, stablecoin_address):
        rpc = self._rpc_client_factory(network)
        if rpc is None:
            logger.info("onchain_no_rpc_for_network: %s", network)
            return None

        try:
            data = (
                "0xe6a43905"
                + token_address[2:].zfill(64)
                + stablecoin_address[2:].zfill(64)
            )
            result = await rpc.eth_call(to=factory, data=data)
            pool_addr = "0x" + result[-40:].lower()

            if pool_addr == "0x0000000000000000000000000000000000000000":
                return None

            return DiscoveredPool(
                network=network,
                pool_address=pool_addr,
                dex=dex_id,
                token0_address=token_address,
                token1_address=stablecoin_address,
                sources={"onchain_factory"},
            )
        except Exception:
            return None

    async def _check_v3_factory(
        self, network, factory, dex_id,
        token_address, stablecoin_address, fee_bps,
    ):
        rpc = self._rpc_client_factory(network)
        if rpc is None:
            logger.info("onchain_no_rpc_for_network: %s", network)
            return None

        try:
            # uint24 requires 32-byte (64 hex char) ABI encoding
            fee_hex = format(fee_bps * 100, "x").zfill(64)
            data = (
                "0x1698ee82"
                + token_address[2:].zfill(64)
                + stablecoin_address[2:].zfill(64)
                + fee_hex
            )
            result = await rpc.eth_call(to=factory, data=data)
            pool_addr = "0x" + result[-40:].lower()

            if pool_addr == "0x0000000000000000000000000000000000000000":
                return None

            return DiscoveredPool(
                network=network,
                pool_address=pool_addr,
                dex=dex_id,
                token0_address=token_address,
                token1_address=stablecoin_address,
                sources={"onchain_factory"},
            )
        except Exception:
            return None
