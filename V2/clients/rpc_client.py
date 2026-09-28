"""
Async JSON-RPC client.

Supports eth_call, eth_gasPrice, multicall batching.
Caches gas price with TTL.
Never logs secret RPC URLs — uses mask_url from utils.logging.
"""

import logging
import time
from decimal import Decimal

import httpx

from config.rate_limits import RATE_LIMITS
from utils.circuit_breaker import CircuitBreaker
from utils.logging import mask_url
from utils.retry import retry_async

logger = logging.getLogger(__name__)


class RpcError(Exception):
    """Raised when an RPC call fails."""
    pass


class RpcClient:
    """Async JSON-RPC client with caching and a circuit breaker."""

    def __init__(
        self,
        rpc_url: str,
        rate_limiter,
        circuit_breaker: CircuitBreaker | None = None,
    ):
        self._rpc_url = rpc_url
        self._rate_limiter = rate_limiter
        self._client = httpx.AsyncClient(timeout=30)
        self._request_id = 0
        self._circuit_breaker = circuit_breaker or CircuitBreaker(
            name=mask_url(rpc_url),
        )

        self._gas_price_cache: dict[str, tuple[float, Decimal]] = {}
        self._gas_price_ttl_sec = 60

    async def _post(self, method: str, params: list) -> dict:
        """Make a JSON-RPC POST request with retry on transient errors.

        Retries on transport-level errors (timeout, connect, 429, 5xx).
        Does NOT retry on 4xx client errors (except 429) or JSON-RPC
        application errors (reverts) since those are deterministic.
        """
        # Circuit breaker: endpoint deemed down — fail fast without
        # burning rate-limit budget or retry cycles.
        if self._circuit_breaker.is_open:
            raise RpcError(
                f"circuit_open: {mask_url(self._rpc_url)}"
            )

        from utils.cu_rate_limiter import CuRateLimiter, cu_cost_for_method

        if isinstance(self._rate_limiter, CuRateLimiter):
            await self._rate_limiter.acquire(cu_cost_for_method(method))
        else:
            await self._rate_limiter.acquire()

        self._request_id += 1

        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        async def do_post():
            response = await self._client.post(self._rpc_url, json=payload)
            # Only retry on transient HTTP errors (429, 5xx).
            if response.status_code == 429:
                from metrics.health import increment_metrics
                increment_metrics(rpc_429_total=1)
                response.raise_for_status()
            if response.status_code >= 500:
                response.raise_for_status()
            elif response.status_code >= 400:
                # Non-retryable client error — raise immediately.
                response.raise_for_status()

            result = response.json()

            if "error" in result:
                raise RpcError(f"RPC error: {result['error']}")

            return result

        def _is_retryable(exc: Exception) -> bool:
            """Only retry on transient errors."""
            if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
                return True
            if isinstance(exc, httpx.HTTPStatusError):
                code = exc.response.status_code
                return code == 429 or code >= 500
            return False

        rpc_limits = RATE_LIMITS["rpc"]
        try:
            result = await retry_async(
                do_post,
                attempts=rpc_limits.get("retry_attempts", 4),
                base_delay=rpc_limits.get("backoff_initial_sec", 2),
                max_delay=rpc_limits.get("backoff_max_sec", 30),
                retryable_exceptions=(
                    httpx.TimeoutException,
                    httpx.ConnectError,
                    httpx.HTTPStatusError,
                ),
                retry_predicate=_is_retryable,
            )
        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.HTTPStatusError,
        ):
            # Transport-level failure: count toward opening the circuit.
            self._circuit_breaker.record_failure()
            raise
        self._circuit_breaker.record_success()
        return result

    async def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        """Execute an eth_call."""
        result = await self._post(
            "eth_call",
            [
                {"to": to, "data": data},
                block,
            ],
        )
        return result["result"]

    async def get_gas_price(self) -> Decimal:
        """Get current gas price in wei, cached for 60 seconds."""
        now = time.time()
        cache_key = self._rpc_url

        cached = self._gas_price_cache.get(cache_key)
        if cached is not None:
            cached_time, cached_price = cached
            if now - cached_time < self._gas_price_ttl_sec:
                return cached_price

        result = await self._post("eth_gasPrice", [])
        gas_price_wei = Decimal(int(result["result"], 16))

        self._gas_price_cache[cache_key] = (now, gas_price_wei)

        return gas_price_wei

    async def eth_get_code(self, address: str, block: str = "latest") -> str:
        """Get contract bytecode at address."""
        result = await self._post("eth_getCode", [address, block])
        return result["result"]

    async def eth_get_storage_at(
        self, address: str, slot: str, block: str = "latest"
    ) -> str:
        """Read a storage slot from a contract."""
        result = await self._post("eth_getStorageAt", [address, slot, block])
        return result["result"]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


class RoundRobinRpcClient:
    """Wraps multiple RpcClients, alternating requests between them.

    Doubles effective rate limit when using 2 Alchemy keys.
    Thread-safe for asyncio (single event loop).
    """

    def __init__(self, clients: list[RpcClient]):
        if not clients:
            raise ValueError("RoundRobinRpcClient needs at least 1 client")
        self._clients = clients
        self._index = 0

    def _next(self) -> RpcClient:
        # Prefer clients whose circuit breaker is closed; if all are open,
        # fall through to plain round-robin (the call fails fast anyway).
        for _ in range(len(self._clients)):
            client = self._clients[self._index % len(self._clients)]
            self._index += 1
            if not client._circuit_breaker.is_open:
                return client
        client = self._clients[self._index % len(self._clients)]
        self._index += 1
        return client

    async def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        return await self._next().eth_call(to, data, block)

    async def get_gas_price(self) -> Decimal:
        return await self._next().get_gas_price()

    async def eth_get_code(self, address: str, block: str = "latest") -> str:
        return await self._next().eth_get_code(address, block)

    async def eth_get_storage_at(
        self, address: str, slot: str, block: str = "latest"
    ) -> str:
        return await self._next().eth_get_storage_at(address, slot, block)

    async def close(self) -> None:
        for client in self._clients:
            await client.close()
