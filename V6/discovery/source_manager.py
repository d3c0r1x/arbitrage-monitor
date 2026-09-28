"""
Source manager implementing discovery source failover logic.

Implements the source selection algorithm from the plan:
1. Use configured source priority.
2. Skip disabled or unhealthy sources.
3. Fall back to next source on failure.
4. Track source health.
5. Onchain factory is only used as fallback when primary sources fail.
"""

import logging
from typing import Protocol

from config.settings import settings
from discovery.base_source import BaseSource, SourceError

logger = logging.getLogger(__name__)


class SourceHealthTracker(Protocol):
    """Protocol for source health tracking (injected)."""
    async def record_success(self, source: str) -> None: ...
    async def record_failure(self, source: str, error: str) -> None: ...
    def is_healthy(self, source: str) -> bool: ...


class NullHealthTracker:
    """Health tracker that does nothing — used when no DB is available."""

    async def record_success(self, source: str) -> None:
        pass

    async def record_failure(self, source: str, error: str) -> None:
        pass

    def is_healthy(self, source: str) -> bool:
        return True


class SourceManager:
    """Manages discovery sources with failover and health tracking.

    Primary sources (dexscreener, geckoterminal) are tried first.
    Onchain factory is only used as fallback if no primary source succeeds.
    Optional sources require explicit enabling.
    """

    # G6: Only register fully implemented sources.
    _IMPLEMENTED_SOURCES = frozenset(
        {"dexscreener", "geckoterminal", "onchain_factory", "subgraph"}
    )
    def __init__(
        self,
        sources: dict[str, BaseSource],
        health_tracker: SourceHealthTracker | None = None,
    ):
        # Only keep implemented sources; log warnings for unimplemented ones.
        self._sources = {}
        for name, source in sources.items():
            if name in self._IMPLEMENTED_SOURCES:
                self._sources[name] = source
            else:
                logger.warning("optional_source_not_implemented: %s", name)
        self._health_tracker = health_tracker or NullHealthTracker()
        self._source_priority = [
            s for s in settings.POOL_SOURCE_PRIORITY
            if s in self._IMPLEMENTED_SOURCES
        ]

    def get_available_sources(self, exclude: set[str] | None = None) -> list[str]:
        exclude = exclude or set()
        available: list[str] = []

        for name in self._source_priority:
            if name in exclude:
                continue
            source = self._sources.get(name)
            if source is None:
                continue
            if not source.is_available():
                logger.debug("source_unavailable: %s", name)
                continue
            if not self._health_tracker.is_healthy(name):
                logger.debug("source_unhealthy_skipping: %s", name)
                continue
            available.append(name)

        return available

    async def discover_from_all_sources(
        self,
        network: str,
        token_address: str,
    ) -> list:
        """Discover pools from all available sources with failover."""
        from models.pool_models import DiscoveredPool

        all_pools: dict[str, DiscoveredPool] = {}
        primary_sources_available = self.get_available_sources(exclude={"onchain_factory"})
        primary_had_pools = False

        # D10: if every source (HTTP + onchain) is unavailable/unhealthy,
        # raise instead of returning []. An empty result would make the
        # refresh delete this token's cached pools; an exception marks the
        # token as failed so its previously discovered pools are kept.
        onchain = self._sources.get("onchain_factory")
        onchain_usable = onchain is not None and onchain.is_available()
        if not primary_sources_available and not onchain_usable:
            logger.warning("all_discovery_sources_unhealthy: keeping_cached_pools")
            raise SourceError("all_discovery_sources_unhealthy")

        # Phase 1: Try primary HTTP API sources.
        for source_name in primary_sources_available:
            source = self._sources[source_name]

            try:
                pools = await source.fetch_pools_by_token(
                    network=network,
                    token_address=token_address,
                )

                await self._health_tracker.record_success(source_name)

                for pool in pools:
                    key = pool.pool_address.lower()
                    if key not in all_pools:
                        all_pools[key] = pool
                    else:
                        all_pools[key].sources.update(pool.sources)

                if pools:
                    primary_had_pools = True
                    logger.debug(
                        "source_pools_found: %s count=%d",
                        source_name,
                        len(pools),
                    )

            except SourceError as exc:
                await self._health_tracker.record_failure(source_name, str(exc))
                logger.warning(
                    "source_discovery_failed: %s error=%s",
                    source_name,
                    exc,
                )
            except Exception as exc:
                await self._health_tracker.record_failure(source_name, str(exc))
                logger.error(
                    "source_unexpected_error: %s error=%s",
                    source_name,
                    exc,
                )

        # Phase 2: Always merge onchain_factory for wrapped-native + stable
        # pairs. Primary HTTP sources often return exotic quotes (CAT/BTC) or
        # miss the liquid WBNB/WETH pool under rate limits — skipping onchain
        # whenever primary_had_pools=True dropped FEG/WBNB and similar.
        if "onchain_factory" in self._sources:
            onchain_source = self._sources["onchain_factory"]
            if onchain_source.is_available():
                try:
                    pools = await onchain_source.fetch_pools_by_token(
                        network=network,
                        token_address=token_address,
                    )

                    await self._health_tracker.record_success("onchain_factory")

                    for pool in pools:
                        key = pool.pool_address.lower()
                        if key not in all_pools:
                            all_pools[key] = pool
                        else:
                            all_pools[key].sources.update(pool.sources)
                            if not all_pools[key].pool_version and pool.pool_version:
                                all_pools[key].pool_version = pool.pool_version
                            if pool.dex and (
                                not all_pools[key].dex
                                or all_pools[key].dex == "unknown"
                            ):
                                all_pools[key].dex = pool.dex

                    if pools:
                        logger.debug(
                            "onchain_factory_pools_found: count=%d supplement=%s",
                            len(pools),
                            primary_had_pools,
                        )

                except (SourceError, Exception) as exc:
                    await self._health_tracker.record_failure("onchain_factory", str(exc))
                    logger.error("onchain_factory_fallback_failed: %s", exc)

        return list(all_pools.values())
