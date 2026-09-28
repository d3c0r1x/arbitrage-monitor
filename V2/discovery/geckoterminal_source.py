"""
GeckoTerminal pool discovery source.

Used ONLY for pool discovery — price, liquidity, volume fields are ignored.

Behavior:
- Free tier consistently returns 429 after 3-5 requests.
- This source uses SimpleRateLimiter (20 req/min, burst=1) to be polite.
- On 429: raises SourceError immediately (fail-fast) — no retry.
  The source manager falls through to DexScreener / onchain_factory.
- On timeout: retries once with 1s backoff, then raises SourceError.
- After 3 consecutive failures, source health marks it unhealthy
  and it is skipped entirely for the remainder of the refresh cycle.
- A small in-memory cache (60s TTL) avoids repeated requests.
"""

import asyncio
import logging
import time

import httpx

from discovery.base_source import BaseSource, SourceError
from models.pool_models import DiscoveredPool
from utils.rate_limiter import SimpleRateLimiter

logger = logging.getLogger(__name__)

NETWORK_MAPPING: dict[str, str] = {
    "ETHEREUM": "eth",
    "BSC": "bsc",
    "POLYGON": "polygon_pos",
    "ARBITRUM": "arbitrum",
    "BASE": "base",
    "ROBINHOOD": "robinhood",
}

# Cache TTL for pool results (seconds). Avoids re-fetching recently seen tokens.
CACHE_TTL_SEC = 60

# Max retries on 429 or transient errors
# Keep low: GeckoTerminal free tier rate-limits aggressively.
# After 2 failures we fall through to DexScreener/onchain_factory.
MAX_RETRIES = 2
# Backoff start (seconds) — doubled each retry
BACKOFF_INITIAL = 1.0


class _CacheEntry:
    """Simple cache entry with expiry."""

    def __init__(self, pools: list[DiscoveredPool], ttl: float = CACHE_TTL_SEC):
        self.pools = pools
        self.expires_at = time.monotonic() + ttl

    def is_expired(self) -> bool:
        return time.monotonic() >= self.expires_at


class GeckoTerminalSource(BaseSource):
    """Pool discovery via GeckoTerminal API with rate limiting and retry."""

    source_name: str = "geckoterminal"

    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self._base_url = "https://api.geckoterminal.com/api/v2"

        # Conservative rate limiter: 20 req/min = 3 seconds between requests
        self._rate_limiter = SimpleRateLimiter(
            requests_per_minute=20,
            burst=1,  # no burst — strictly rate limited
        )

        # In-memory cache: key = "network:token_address" → _CacheEntry
        self._cache: dict[str, _CacheEntry] = {}

    def is_available(self) -> bool:
        return True

    def _get_cache_key(self, network: str, token_address: str) -> str:
        return f"{network}:{token_address.lower()}"

    def _get_cached(self, network: str, token_address: str) -> list[DiscoveredPool] | None:
        """Return cached pools if valid, else None."""
        entry = self._cache.get(self._get_cache_key(network, token_address))
        if entry is not None and not entry.is_expired():
            logger.debug("gecko_cache_hit: %s/%s", network, token_address[:10])
            return entry.pools
        return None

    def _set_cache(self, network: str, token_address: str, pools: list[DiscoveredPool]) -> None:
        """Store pools in cache."""
        key = self._get_cache_key(network, token_address)
        self._cache[key] = _CacheEntry(pools)
        # Evict expired entries periodically (simple: when over 100 entries)
        if len(self._cache) > 100:
            expired = [k for k, v in self._cache.items() if v.is_expired()]
            for k in expired:
                del self._cache[k]

    async def fetch_pools_by_token(
        self,
        network: str,
        token_address: str,
    ) -> list[DiscoveredPool]:
        """Fetch pools from GeckoTerminal by token address.

        Implements:
        - In-memory cache (60s TTL) to avoid repeated requests.
        - Rate limiting (20 req/min, no burst).
        - Retry with exponential backoff on 429 or transient errors.
        """
        # Check cache first
        cached = self._get_cached(network, token_address)
        if cached is not None:
            return cached

        gecko_network = self._normalize_network(network)
        url = f"{self._base_url}/networks/{gecko_network}/tokens/{token_address}/pools"

        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            # Acquire rate limiter permit before each request
            await self._rate_limiter.acquire()

            try:
                response = await self._client.get(url, timeout=30)

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "5")
                    logger.warning(
                        "gecko_429 fail_fast attempt=%d/%d retry_after=%s",
                        attempt + 1, MAX_RETRIES, retry_after,
                    )
                    # Fail fast: GeckoTerminal free tier consistently rate-limits.
                    # Don't waste time retrying — let source manager fall through
                    # to DexScreener and onchain_factory.
                    raise SourceError(
                        f"GeckoTerminal rate limited (429) retry_after={retry_after}"
                    )

                response.raise_for_status()

            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning(
                    "gecko_timeout attempt=%d/%d: %s",
                    attempt + 1, MAX_RETRIES, exc,
                )
                wait = BACKOFF_INITIAL * (2 ** attempt)
                await asyncio.sleep(wait)
                continue

            except httpx.HTTPError as exc:
                # Non-429 HTTP error — raise immediately (likely 4xx client error)
                raise SourceError(f"GeckoTerminal HTTP error: {exc}") from exc

            # Success — parse response
            payload = response.json()
            pools: list[DiscoveredPool] = []

            for pool in payload.get("data", []):
                attributes = pool.get("attributes", {})
                pool_address = str(attributes.get("address", "")).lower()

                if not pool_address:
                    continue

                relationships = pool.get("relationships", {})

                base_token = self._extract_token_address(relationships, "base_token")
                quote_token = self._extract_token_address(relationships, "quote_token")

                if not base_token or not quote_token:
                    continue

                pools.append(
                    DiscoveredPool(
                        network=network,
                        pool_address=pool_address,
                        dex=attributes.get("dex_id"),
                        token0_address=base_token,
                        token1_address=quote_token,
                        sources={"geckoterminal"},
                    )
                )

            # Cache the result
            self._set_cache(network, token_address, pools)
            return pools

        # All retries exhausted
        raise SourceError(
            f"GeckoTerminal failed after {MAX_RETRIES} attempts: {last_error}"
        )

    @staticmethod
    def _extract_token_address(relationships: dict, key: str) -> str | None:
        try:
            data = relationships[key]["data"]
            token_id = str(data.get("id", ""))
            return token_id.split("_")[-1].lower()
        except (KeyError, TypeError, IndexError):
            return None

    @staticmethod
    def _normalize_network(network: str) -> str:
        return NETWORK_MAPPING.get(network, network.lower())
