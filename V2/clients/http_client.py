"""
Shared async HTTP client factory.
Provides httpx.AsyncClient instances with sensible defaults.
Supports proxy rotation for rate limit bypass on discovery sources.
"""

import logging

import httpx

from clients.proxy_manager import ProxyRotator, create_proxy_rotator

logger = logging.getLogger(__name__)

# Global proxy rotator (initialized once).
_proxy_rotator: ProxyRotator | None = None


def get_proxy_rotator() -> ProxyRotator:
    """Get or create the global proxy rotator."""
    global _proxy_rotator
    if _proxy_rotator is None:
        _proxy_rotator = create_proxy_rotator()
    return _proxy_rotator


async def create_http_client(timeout: int = 30) -> httpx.AsyncClient:
    """Create a shared async HTTP client (direct, no proxy).

    Used for MEXC API and other non-rate-limited endpoints.
    """
    return httpx.AsyncClient(timeout=timeout)


async def create_discovery_http_client(timeout: int = 30) -> httpx.AsyncClient:
    """Create an HTTP client for discovery sources with proxy support.

    Uses proxy rotation if proxies are configured.
    Falls back to direct connection if no proxies available.
    """
    rotator = get_proxy_rotator()
    proxy_url = rotator.get_next_proxy()

    if proxy_url:
        logger.info("discovery_client_with_proxy: %s", proxy_url)
        return httpx.AsyncClient(timeout=timeout, proxy=proxy_url)

    return httpx.AsyncClient(timeout=timeout)


async def close_http_client(client: httpx.AsyncClient) -> None:
    """Close the HTTP client gracefully."""
    await client.aclose()
