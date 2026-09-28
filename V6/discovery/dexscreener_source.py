"""
DexScreener pool discovery source.

Used ONLY for pool discovery — price, liquidity, volume fields are ignored.

Caching:
- In-memory cache with 60s TTL avoids redundant API calls.
- Multiple candidates hitting the same token address share one API call.
- Cache is evicted lazily when over 500 entries or on TTL expiry.
"""

import logging
import time

import httpx

from discovery.base_source import BaseSource, SourceError
from models.pool_models import DiscoveredPool
from utils.rate_limiter import SimpleRateLimiter

logger = logging.getLogger(__name__)

NETWORK_MAPPING: dict[str, str] = {
    "ethereum": "ETHEREUM",
    "bsc": "BSC",
    "polygon": "POLYGON",
    "arbitrum": "ARBITRUM",
    "base": "BASE",
    "robinhood": "ROBINHOOD",
}

CACHE_TTL_SEC = 60


class _CacheEntry:
    def __init__(self, pools: list[DiscoveredPool]):
        self.pools = pools
        self.expires_at = time.monotonic() + CACHE_TTL_SEC

    def is_expired(self) -> bool:
        return time.monotonic() >= self.expires_at


class DexScreenerSource(BaseSource):
    """Pool discovery via DexScreener API with in-memory cache."""

    source_name: str = "dexscreener"

    def __init__(self, client: httpx.AsyncClient, requests_per_minute: int = 300, burst: int = 5):
        self._client = client
        self._base_url = "https://api.dexscreener.com/latest/dex"
        self._cache: dict[str, _CacheEntry] = {}
        self._rate_limiter = SimpleRateLimiter(
            requests_per_minute=requests_per_minute,
            burst=burst,
        )

    def is_available(self) -> bool:
        return True

    async def fetch_pools_by_token(
        self,
        network: str,
        token_address: str,
    ) -> list[DiscoveredPool]:
        """Fetch pools from DexScreener by token address.

        Caches results by token_address (not network), because DexScreener
        returns all chains for a token in a single call. Results are then
        filtered by network on return.
        """
        cache_key = token_address.lower()

        # Check cache.
        entry = self._cache.get(cache_key)
        if entry is not None and not entry.is_expired():
            logger.debug("dexscreener_cache_hit: %s", token_address[:10])
            # Filter cached pools by network.
            return [p for p in entry.pools if p.network == network]

        url = f"{self._base_url}/tokens/{token_address}"

        # C4: Apply rate limiter before HTTP call.
        await self._rate_limiter.acquire()

        try:
            response = await self._client.get(url, timeout=30)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(f"DexScreener HTTP error: {exc}") from exc

        payload = response.json()
        all_pools: list[DiscoveredPool] = []

        for pair in (payload.get("pairs") or []):
            chain_id = str(pair.get("chainId", "")).lower()
            pair_address = str(pair.get("pairAddress", "")).lower()

            if not chain_id or not pair_address:
                continue

            mapped_network = self._normalize_network(chain_id)
            base_token = pair.get("baseToken", {})
            quote_token = pair.get("quoteToken", {})

            base_address = str(base_token.get("address", "")).lower()
            quote_address = str(quote_token.get("address", "")).lower()

            if not base_address or not quote_address:
                continue

            dex_raw = str(pair.get("dexId") or "").strip().lower()
            pool_version = self._version_from_labels(pair.get("labels"))
            dex_id = self._normalize_dex_id(dex_raw, pool_version)

            pool = DiscoveredPool(
                network=mapped_network,
                pool_address=pair_address,
                dex=dex_id or dex_raw or None,
                token0_address=base_address,
                token1_address=quote_address,
                sources={"dexscreener"},
                pool_version=pool_version,
            )
            all_pools.append(pool)

        # Cache ALL pools (all chains), filter by network on cache hit.
        self._set_cache(cache_key, all_pools)

        # Filter by requested network.
        network_pools = [p for p in all_pools if p.network == network]
        return network_pools

    def _set_cache(self, key: str, pools: list[DiscoveredPool]) -> None:
        self._cache[key] = _CacheEntry(pools)
        if len(self._cache) > 500:
            # First: remove expired entries.
            expired = [k for k, v in self._cache.items() if v.is_expired()]
            for k in expired:
                del self._cache[k]
            # C4: If still over limit, evict oldest by expires_at (LRU).
            if len(self._cache) > 500:
                sorted_keys = sorted(
                    self._cache.keys(),
                    key=lambda k: self._cache[k].expires_at,
                )
                to_remove = len(self._cache) - 500
                for k in sorted_keys[:to_remove]:
                    del self._cache[k]

    @staticmethod
    def _version_from_labels(labels) -> str | None:
        """DexScreener puts AMM version in labels, e.g. ['v2']."""
        if not labels:
            return None
        for lab in labels:
            s = str(lab or "").strip().lower()
            if s in ("v1", "v2", "v3", "v4"):
                return s
        return None

    @staticmethod
    def _normalize_dex_id(dex_id: str, pool_version: str | None) -> str:
        """Map bare dexId+label → registry id (pancakeswap + v2 → pancakeswap_v2)."""
        d = (dex_id or "").strip().lower()
        if not d:
            return d
        if pool_version and not d.endswith(f"_{pool_version}"):
            # Already versioned (uniswap_v3) — keep.
            tail = d.rsplit("_", 1)[-1]
            if not (tail.startswith("v") and tail[1:].isdigit()):
                d = f"{d}_{pool_version}"
        return d

    @staticmethod
    def _normalize_network(chain_id: str) -> str:
        return NETWORK_MAPPING.get(chain_id, chain_id.upper())
