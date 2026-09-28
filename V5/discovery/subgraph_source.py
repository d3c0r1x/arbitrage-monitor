"""
PancakeSwap / The Graph pool discovery source.

Uses PCS Exchange V3 + StableSwap BSC gateway URLs (and optional legacy
SUBGRAPH_URL). Discovery only — liquidity/volume fields are ignored.
"""

from __future__ import annotations

import logging
import time

import httpx

from config.settings import settings
from discovery.base_source import BaseSource, SourceError
from models.pool_models import DiscoveredPool
from utils.rate_limiter import SimpleRateLimiter

logger = logging.getLogger(__name__)

CACHE_TTL_SEC = 60
MAX_POOLS_PER_ENDPOINT = 50

# V3 Uniswap-style schema (pools entity).
_V3_QUERY = """
query PoolsByToken($t: String!, $n: Int!) {
  as0: pools(first: $n, where: {token0: $t}) {
    id
    token0 { id }
    token1 { id }
  }
  as1: pools(first: $n, where: {token1: $t}) {
    id
    token0 { id }
    token1 { id }
  }
}
"""

# StableSwap / V2-style schema (pairs entity).
_PAIRS_QUERY = """
query PairsByToken($t: String!, $n: Int!) {
  as0: pairs(first: $n, where: {token0: $t}) {
    id
    token0 { id }
    token1 { id }
  }
  as1: pairs(first: $n, where: {token1: $t}) {
    id
    token0 { id }
    token1 { id }
  }
}
"""


class _CacheEntry:
    def __init__(self, pools: list[DiscoveredPool]):
        self.pools = pools
        self.expires_at = time.monotonic() + CACHE_TTL_SEC

    def is_expired(self) -> bool:
        return time.monotonic() >= self.expires_at


class SubgraphSource(BaseSource):
    """Pool discovery via The Graph (PCS V3 + StableSwap)."""

    source_name: str = "subgraph"

    def __init__(
        self,
        client: httpx.AsyncClient,
        requests_per_minute: int = 120,
        burst: int = 4,
    ):
        self._client = client
        self._cache: dict[str, _CacheEntry] = {}
        self._rate_limiter = SimpleRateLimiter(
            requests_per_minute=requests_per_minute,
            burst=burst,
        )

    def is_available(self) -> bool:
        return bool(settings.SUBGRAPH_ENDPOINTS)

    async def fetch_pools_by_token(
        self,
        network: str,
        token_address: str,
    ) -> list[DiscoveredPool]:
        network_u = (network or "").upper()
        token = (token_address or "").lower()
        if not token:
            return []

        endpoints = [
            ep for ep in settings.SUBGRAPH_ENDPOINTS if ep[1] == network_u
        ]
        if not endpoints:
            # Subgraph currently covers BSC only — empty is not an error.
            return []

        cache_key = f"{network_u}:{token}"
        entry = self._cache.get(cache_key)
        if entry is not None and not entry.is_expired():
            return list(entry.pools)

        merged: dict[str, DiscoveredPool] = {}
        errors: list[str] = []

        for kind, net, url in endpoints:
            try:
                pools = await self._fetch_endpoint(
                    kind=kind,
                    network=net,
                    url=url,
                    token=token,
                )
                for pool in pools:
                    key = pool.pool_address.lower()
                    if key not in merged:
                        merged[key] = pool
                    else:
                        merged[key].sources.update(pool.sources)
            except SourceError as exc:
                errors.append(f"{kind}:{exc}")
                logger.warning(
                    "subgraph_endpoint_failed: kind=%s network=%s error=%s",
                    kind,
                    net,
                    exc,
                )

        if not merged and errors:
            raise SourceError("; ".join(errors[:3]))

        pools_out = list(merged.values())
        self._set_cache(cache_key, pools_out)
        logger.debug(
            "subgraph_pools_found: network=%s token=%s count=%d",
            network_u,
            token[:10],
            len(pools_out),
        )
        return pools_out

    async def _fetch_endpoint(
        self,
        *,
        kind: str,
        network: str,
        url: str,
        token: str,
    ) -> list[DiscoveredPool]:
        if kind == "v3":
            query = _V3_QUERY
            dex = "pancakeswap_v3"
            pool_version = "v3"
            row_keys = ("as0", "as1")
        elif kind == "stable":
            query = _PAIRS_QUERY
            dex = "pancakeswap_stable"
            # No quote adapter yet — leave version unset so detector may drop.
            pool_version = None
            row_keys = ("as0", "as1")
        else:
            # Legacy SUBGRAPH_URL: try V3 schema first, then pairs.
            pools = await self._graphql_rows(
                url=url,
                query=_V3_QUERY,
                token=token,
                row_keys=("as0", "as1"),
            )
            if pools is not None:
                return [
                    self._to_pool(network, "unknown", "v3", row)
                    for row in pools
                ]
            pools = await self._graphql_rows(
                url=url,
                query=_PAIRS_QUERY,
                token=token,
                row_keys=("as0", "as1"),
            )
            if pools is None:
                raise SourceError("generic_subgraph_schema_unsupported")
            return [
                self._to_pool(network, "unknown", None, row) for row in pools
            ]

        rows = await self._graphql_rows(
            url=url,
            query=query,
            token=token,
            row_keys=row_keys,
        )
        if rows is None:
            raise SourceError(f"{kind}_query_failed")
        return [
            self._to_pool(network, dex, pool_version, row) for row in rows
        ]

    async def _graphql_rows(
        self,
        *,
        url: str,
        query: str,
        token: str,
        row_keys: tuple[str, ...],
    ) -> list[dict] | None:
        await self._rate_limiter.acquire()
        try:
            response = await self._client.post(
                url,
                json={
                    "query": query,
                    "variables": {"t": token, "n": MAX_POOLS_PER_ENDPOINT},
                },
                timeout=20,
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"http:{exc}") from exc

        if response.status_code == 403:
            raise SourceError("forbidden_or_rate_limited")
        if response.status_code >= 400:
            raise SourceError(f"http_status:{response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError("invalid_json") from exc

        if payload.get("errors"):
            msg = str(payload["errors"][0].get("message", "graphql_error"))[:160]
            # Schema mismatch → caller may try alternate query.
            if "has no field" in msg or "Cannot query field" in msg:
                return None
            raise SourceError(f"graphql:{msg}")

        data = payload.get("data") or {}
        rows: list[dict] = []
        seen: set[str] = set()
        for key in row_keys:
            for row in data.get(key) or []:
                pid = str(row.get("id") or "").lower()
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                rows.append(row)
        return rows

    @staticmethod
    def _to_pool(
        network: str,
        dex: str,
        pool_version: str | None,
        row: dict,
    ) -> DiscoveredPool:
        token0 = str((row.get("token0") or {}).get("id") or "").lower()
        token1 = str((row.get("token1") or {}).get("id") or "").lower()
        return DiscoveredPool(
            network=network.upper(),
            pool_address=str(row.get("id") or "").lower(),
            dex=dex,
            token0_address=token0,
            token1_address=token1,
            sources={"subgraph"},
            pool_version=pool_version,
        )

    def _set_cache(self, key: str, pools: list[DiscoveredPool]) -> None:
        self._cache[key] = _CacheEntry(pools)
        if len(self._cache) <= 500:
            return
        expired = [k for k, v in self._cache.items() if v.is_expired()]
        for k in expired:
            del self._cache[k]
        if len(self._cache) <= 500:
            return
        sorted_keys = sorted(
            self._cache.keys(),
            key=lambda k: self._cache[k].expires_at,
        )
        for k in sorted_keys[: len(self._cache) - 500]:
            del self._cache[k]
