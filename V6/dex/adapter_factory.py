"""
Adapter factory — version-first, network-agnostic.

Resolves v1/v2/v3/v4 adapters for ANY network in DEX_REGISTRY.
When a registry match is missing but pool_version is known, falls back to
a generic adapter that only needs the pool ABI (V1 Solidly, V2 reserves).
"""

import logging

from config.amm_versions import normalize_pool_version
from config.dex_registry import DEX_REGISTRY
from dex.v1_adapter import V1Adapter
from dex.v2_adapter import V2Adapter
from dex.v3_adapter import V3Adapter
from dex.v4_adapter import V4Adapter

logger = logging.getLogger(__name__)


_DEX_ALIASES: dict[str, list[str]] = {
    "pancakeswap": ["pancakeswap_v2", "pancakeswap_v3", "pancakeswap_v1", "pancakeswap_v4"],
    "uniswap": ["uniswap_v2", "uniswap_v3", "uniswap_v4", "uniswap_v1"],
    "sushiswap": ["sushiswap_v2", "sushiswap_v3"],
    "quickswap": ["quickswap_v3", "quickswap_v2"],
    "camelot": ["camelot_v2", "camelot_v3"],
    "biswap": ["biswap_v2", "biswap"],
    "babydogeswap": ["babydogeswap_v2", "babydogeswap"],
    "apeswap": ["apeswap_v2", "apeswap"],
    "traderjoe": ["traderjoe_v2", "traderjoe"],
    "kyberswap": ["kyberswap_v2", "kyberswap_v3", "kyberswap"],
    # Solidly-family (v1) — same adapter, any chain
    "aerodrome": ["aerodrome_v1"],
    "thena": ["thena_v1"],
    "ramses": ["ramses_v1"],
    "chronos": ["chronos_v1"],
    "pearl": ["pearl_v1"],
    "solidly": ["solidly_v1"],
    "equalizer": ["equalizer_v1"],
    "velodrome": ["velodrome_v1"],
    "baseswap": ["baseswap_v2"],
}


class AdapterFactory:
    """Factory for creating and caching DEX adapters."""

    def __init__(self, web3_manager=None, web3_factory=None):
        if web3_manager is not None:
            from clients.web3_manager import Web3Factory

            self._web3_factory = Web3Factory(web3_manager)
        else:
            self._web3_factory = web3_factory
        self._adapters: dict = {}
        self._v2_reserves_cache = None

    def set_v2_reserves_cache(self, cache) -> None:
        """Attach Multicall reserves cache to all V2 adapters (existing + new)."""
        self._v2_reserves_cache = cache
        for adapter in self._adapters.values():
            if isinstance(adapter, V2Adapter) and hasattr(adapter, "set_reserves_cache"):
                adapter.set_reserves_cache(cache)

    def _attach_v2_cache(self, adapter):
        if (
            self._v2_reserves_cache is not None
            and isinstance(adapter, V2Adapter)
            and hasattr(adapter, "set_reserves_cache")
        ):
            adapter.set_reserves_cache(self._v2_reserves_cache)
        return adapter

    def get_adapter(self, network: str, dex_id: str, pool_version: str | None = None):
        """Get or create a DEX adapter for any supported version on any network."""
        pool_version = normalize_pool_version(pool_version)
        key = (network, dex_id, pool_version)

        if key in self._adapters:
            return self._adapters[key]

        network_config = DEX_REGISTRY.get(network)
        if not network_config:
            # Still allow V1 generic fallback — ABI is pool-local.
            adapter = self._version_fallback(pool_version, network_config=None)
            if adapter is not None:
                self._adapters[key] = self._attach_v2_cache(adapter)
            else:
                logger.warning("network_not_found_in_registry: %s", network)
            return adapter

        candidates = list(_DEX_ALIASES.get(dex_id, [dex_id]))
        # Also try raw dex_id if alias list didn't include it.
        if dex_id not in candidates:
            candidates.append(dex_id)

        if pool_version:
            versioned = [c for c in candidates if c.endswith(f"_{pool_version}")]
            if not versioned:
                # Any registry dex of this version on this network.
                versioned = [
                    d["dex_id"]
                    for d in network_config.get("dexes", [])
                    if d.get("version") == pool_version
                ]
            candidates = versioned or candidates

        for rid in candidates:
            adapter = self._create_adapter(network_config, network, rid)
            if adapter is not None:
                self._adapters[key] = self._attach_v2_cache(adapter)
                return adapter

        # Network-agnostic fallback by ABI version (not tied to BASE/Aerodrome).
        adapter = self._version_fallback(pool_version, network_config)
        if adapter is not None:
            self._adapters[key] = self._attach_v2_cache(adapter)
            return adapter

        return None

    def _version_fallback(self, pool_version: str | None, network_config: dict | None):
        """ABI-only adapters when registry has no matching dex_id."""
        if pool_version == "v1":
            return V1Adapter(web3_factory=self._web3_factory, default_fee_bps=30)
        if pool_version == "v2":
            # Prefer any v2 router on this network for getAmountsOut; else
            # reserves-only path inside V2Adapter still works with dummy router.
            router = self._first_router(network_config, "v2") or (
                "0x0000000000000000000000000000000000000000"
            )
            return V2Adapter(
                web3_factory=self._web3_factory,
                router_address=router,
                default_fee_bps=30,
            )
        if pool_version == "v3":
            quoter = self._first_field(network_config, "v3", "quoter_v2")
            if not quoter:
                return None
            return V3Adapter(web3_factory=self._web3_factory, quoter_v2_address=quoter)
        if pool_version == "v4":
            quoter = self._first_field(network_config, "v4", "quoter")
            manager = self._first_field(network_config, "v4", "pool_manager")
            return V4Adapter(
                web3_factory=self._web3_factory,
                quoter_address=quoter,
                pool_manager=manager,
            )
        return None

    @staticmethod
    def _first_router(network_config: dict | None, version: str) -> str | None:
        if not network_config:
            return None
        for d in network_config.get("dexes", []):
            if d.get("version") == version and d.get("router") and not str(d["router"]).startswith("TODO_"):
                return d["router"]
        return None

    @staticmethod
    def _first_field(network_config: dict | None, version: str, field: str) -> str | None:
        if not network_config:
            return None
        for d in network_config.get("dexes", []):
            if d.get("version") != version:
                continue
            val = d.get(field)
            if val and not str(val).startswith("TODO_"):
                return val
        return None

    def _create_adapter(self, network_config: dict, network: str, registry_id: str):
        for dex_config in network_config.get("dexes", []):
            if dex_config["dex_id"] != registry_id:
                continue

            version = dex_config["version"]

            if self._has_todo_addresses(dex_config):
                logger.warning(
                    "dex_has_todo_addresses_skipping: %s on %s",
                    registry_id,
                    network,
                )
                return None

            if version == "v1":
                style = str(dex_config.get("style", "solidly")).lower()
                if style in ("uniswap_v2", "v2", "pancake"):
                    return V2Adapter(
                        web3_factory=self._web3_factory,
                        router_address=dex_config["router"],
                        default_fee_bps=dex_config.get("default_fee_bps", 20),
                    )
                return V1Adapter(
                    web3_factory=self._web3_factory,
                    default_fee_bps=dex_config.get("default_fee_bps", 30),
                    router_address=dex_config.get("router"),
                )
            if version == "v2":
                return V2Adapter(
                    web3_factory=self._web3_factory,
                    router_address=dex_config["router"],
                    default_fee_bps=dex_config.get("default_fee_bps", 30),
                )
            if version == "v3":
                return V3Adapter(
                    web3_factory=self._web3_factory,
                    quoter_v2_address=dex_config["quoter_v2"],
                )
            if version == "v4":
                return V4Adapter(
                    web3_factory=self._web3_factory,
                    quoter_address=dex_config.get("quoter"),
                    pool_manager=dex_config.get("pool_manager"),
                    default_fee_bps=dex_config.get("default_fee_bps", 30),
                )

            logger.warning("unknown_dex_version: %s for %s", version, registry_id)
            return None

        return None

    @staticmethod
    def _has_todo_addresses(dex_config: dict) -> bool:
        for key, value in dex_config.items():
            if key in ("notes", "style", "dex_id", "version"):
                continue
            if isinstance(value, str) and value.startswith("TODO_"):
                return True
        return False
