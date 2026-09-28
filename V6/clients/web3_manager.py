"""Web3 instance manager with multi-RPC failover.

Caches AsyncWeb3 clients per network for every configured RPC URL
(Alchemy / dRPC / Infura) and round-robins + retries across them.
Adapters that soft-return 0 on Infura 429 must be able to fall through
to the next provider — a single cached Infura URL caused quote=0.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from web3 import AsyncWeb3

from config.networks import resolve_rpc_urls

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Web3Factory:
    """Callable factory for adapters: factory(network) + factory.all(network)."""

    def __init__(self, manager: Web3Manager):
        self._manager = manager

    def __call__(self, network: str) -> AsyncWeb3 | None:
        return self._manager.get_web3(network)

    def all(self, network: str) -> list[AsyncWeb3]:
        return self._manager.all_web3(network)

    def provider_count(self, network: str) -> int:
        return self._manager.provider_count(network)


def _quote_rpc_urls(network: str) -> list[str]:
    """RPC order for on-chain quotes: prefer Alchemy/dRPC, Infura last.

    Infura was circuit-breaking under discovery load; adapters that only
    used resolve_rpc_url() (Infura-first) soft-failed every quote to 0.
    """
    urls = resolve_rpc_urls(network)
    if not urls:
        return []
    infura = [u for u in urls if "infura.io" in u.lower()]
    other = [u for u in urls if "infura.io" not in u.lower()]
    return other + infura


class Web3Manager:
    """Manages AsyncWeb3 instances with round-robin + failover."""

    def __init__(self):
        self._by_network: dict[str, list[AsyncWeb3]] = {}
        self._rr: dict[str, int] = {}

    def _ensure(self, network: str) -> list[AsyncWeb3]:
        cached = self._by_network.get(network)
        if cached is not None:
            return cached

        urls = _quote_rpc_urls(network)
        if not urls:
            logger.warning("no_rpc_url_for_network: %s", network)
            self._by_network[network] = []
            return []

        clients = [AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(url)) for url in urls]
        self._by_network[network] = clients
        self._rr[network] = 0
        logger.info(
            "web3_manager_ready: network=%s providers=%d",
            network,
            len(clients),
        )
        return clients

    def provider_count(self, network: str) -> int:
        return len(self._ensure(network))

    def get_web3(self, network: str) -> AsyncWeb3 | None:
        """Round-robin pick one provider for the network."""
        clients = self._ensure(network)
        if not clients:
            return None
        idx = self._rr.get(network, 0) % len(clients)
        self._rr[network] = idx + 1
        return clients[idx]

    def all_web3(self, network: str) -> list[AsyncWeb3]:
        """All providers (starting at current RR index) for failover retries."""
        clients = self._ensure(network)
        if not clients:
            return []
        start = self._rr.get(network, 0) % len(clients)
        # Rotate so retries don't always hit the same first URL.
        return clients[start:] + clients[:start]

    async def try_providers(
        self,
        network: str,
        op: Callable[[AsyncWeb3], Awaitable[T]],
        *,
        is_ok: Callable[[T], bool] | None = None,
    ) -> T | None:
        """Run async op(w3) across providers until is_ok(result)."""
        clients = self.all_web3(network)
        if not clients:
            return None
        check = is_ok or (lambda r: r is not None)
        last_exc: Exception | None = None
        for w3 in clients:
            try:
                result = await op(w3)
                if check(result):
                    return result
            except Exception as exc:  # noqa: BLE001 — try next RPC
                last_exc = exc
                continue
        if last_exc is not None:
            logger.debug(
                "web3_try_providers_exhausted: network=%s err=%s",
                network,
                last_exc,
            )
        return None

    def clear(self) -> None:
        """Clear the Web3 cache (e.g. on RPC URL rotation)."""
        self._by_network.clear()
        self._rr.clear()
