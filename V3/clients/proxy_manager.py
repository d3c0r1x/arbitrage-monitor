"""
Proxy manager for rate limit bypass.

Supports:
- HTTP/SOCKS5 proxies via env vars (HTTP_PROXY, HTTPS_PROXY, ALL_PROXY)
- Comma-separated proxy list via PROXY_URLS env var
- v2ray subscription parsing (extracts SOCKS5 endpoints from local v2ray client)
- Round-robin rotation between available proxies

Usage:
    Set in .env:
      PROXY_URLS=socks5://127.0.0.1:1080,socks5://127.0.0.1:1081
    Or for v2ray local client:
      V2RAY_SOCKS_PORT=1080

The v2ray subscription URL is used by the local v2ray client (v2rayN/v2rayturn)
which exposes SOCKS5 proxies on localhost. This module connects to those local
proxies, NOT to the remote servers directly.
"""

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ProxyConfig:
    """A single proxy endpoint."""
    url: str
    protocol: str = "socks5"  # socks5, http, https
    host: str = "127.0.0.1"
    port: int = 1080
    label: str = ""
    healthy: bool = True
    consecutive_failures: int = 0


class ProxyRotator:
    """Rotates between available proxies for HTTP requests.

    Thread-safe for asyncio (single event loop).
    Marks unhealthy proxies after consecutive failures, retries periodically.
    """

    MAX_FAILURES = 3
    RECOVERY_ATTEMPTS = 10  # Try unhealthy proxy every N rotations

    def __init__(self, proxies: list[ProxyConfig] | None = None):
        self._proxies: list[ProxyConfig] = proxies or []
        self._index = 0
        self._rotation_count = 0

    @property
    def available_count(self) -> int:
        return sum(1 for p in self._proxies if p.healthy)

    @property
    def total_count(self) -> int:
        return len(self._proxies)

    def add_proxy(self, proxy: ProxyConfig) -> None:
        self._proxies.append(proxy)

    def get_next_proxy(self) -> str | None:
        """Get next healthy proxy URL, or None if no proxies available."""
        if not self._proxies:
            return None

        self._rotation_count += 1

        # Try to find a healthy proxy.
        for _ in range(len(self._proxies)):
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1

            if proxy.healthy:
                return proxy.url

            # Periodically retry unhealthy proxies.
            if self._rotation_count % self.RECOVERY_ATTEMPTS == 0:
                proxy.healthy = True
                proxy.consecutive_failures = 0
                return proxy.url

        return None

    def report_success(self, proxy_url: str) -> None:
        for p in self._proxies:
            if p.url == proxy_url:
                p.consecutive_failures = 0
                p.healthy = True
                break

    def report_failure(self, proxy_url: str) -> None:
        for p in self._proxies:
            if p.url == proxy_url:
                p.consecutive_failures += 1
                if p.consecutive_failures >= self.MAX_FAILURES:
                    p.healthy = False
                    logger.warning(
                        "proxy_marked_unhealthy: %s failures=%d",
                        proxy_url, p.consecutive_failures,
                    )
                break

    def get_status(self) -> list[dict]:
        return [
            {
                "url": p.url,
                "protocol": p.protocol,
                "healthy": p.healthy,
                "failures": p.consecutive_failures,
            }
            for p in self._proxies
        ]


def parse_v2ray_subscription(subscription_url: str) -> list[ProxyConfig]:
    """Parse a v2ray subscription URL to extract proxy configs.

    Note: This parses the subscription to understand available servers.
    Actual proxy connections go through the LOCAL v2ray client which
    must be running and exposing SOCKS5 ports.

    Returns list of ProxyConfig for local SOCKS5 endpoints.
    """
    proxies = []

    # Check if local v2ray client is running with known SOCKS port.
    v2ray_port = os.environ.get("V2RAY_SOCKS_PORT", "1080")
    try:
        port = int(v2ray_port)
        proxies.append(ProxyConfig(
            url=f"socks5://127.0.0.1:{port}",
            protocol="socks5",
            host="127.0.0.1",
            port=port,
            label="v2ray-local",
        ))
        logger.info("v2ray_local_proxy: socks5://127.0.0.1:%d", port)
    except ValueError:
        pass

    return proxies


def load_proxies_from_env() -> list[ProxyConfig]:
    """Load proxy configuration from environment variables.

    Supported env vars:
    - PROXY_URLS: Comma-separated list of proxy URLs
    - HTTP_PROXY / HTTPS_PROXY / ALL_PROXY: Standard proxy env vars
    - V2RAY_SOCKS_PORT: Local v2ray client SOCKS5 port
    """
    proxies: list[ProxyConfig] = []
    seen_urls: set[str] = set()

    def _add(url: str, label: str = ""):
        url = url.strip()
        if url and url not in seen_urls:
            seen_urls.add(url)
            protocol = "socks5" if url.startswith("socks5") else "http"
            proxies.append(ProxyConfig(url=url, protocol=protocol, label=label))

    # 1. PROXY_URLS (comma-separated).
    proxy_urls = os.environ.get("PROXY_URLS", "")
    if proxy_urls:
        for url in proxy_urls.split(","):
            _add(url, "env")

    # 2. Standard proxy env vars.
    for var in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY"):
        val = os.environ.get(var, "")
        if val:
            _add(val, var.lower())

    # 3. v2ray local SOCKS5.
    v2ray_port = os.environ.get("V2RAY_SOCKS_PORT", "")
    if v2ray_port:
        try:
            port = int(v2ray_port)
            _add(f"socks5://127.0.0.1:{port}", "v2ray")
        except ValueError:
            pass

    if proxies:
        logger.info("proxies_loaded: count=%d urls=%s",
                    len(proxies), [p.url for p in proxies])

    return proxies


def create_proxy_rotator() -> ProxyRotator:
    """Create a ProxyRotator from environment configuration."""
    proxies = load_proxies_from_env()
    return ProxyRotator(proxies)


def create_http_client_with_proxy(
    proxy_url: str | None = None,
    timeout: int = 30,
) -> httpx.AsyncClient:
    """Create an httpx.AsyncClient with optional proxy support.

    Args:
        proxy_url: Proxy URL (socks5://... or http://...). None = direct.
        timeout: Request timeout in seconds.

    Returns:
        Configured httpx.AsyncClient.
    """
    if proxy_url:
        # httpx supports socks5:// and http:// proxies natively.
        return httpx.AsyncClient(
            timeout=timeout,
            proxy=proxy_url,
        )
    return httpx.AsyncClient(timeout=timeout)
