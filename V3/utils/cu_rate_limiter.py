"""
Compute-unit (CU) rate limiter for Alchemy throughput.

Budget is CU per second, not raw request count. eth_call ≈ 26 CU.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Alchemy method weights (Compute Units).
CU_COSTS: dict[str, int] = {
    "eth_call": 26,
    "eth_gasPrice": 20,
    "eth_getCode": 20,
    "eth_getStorageAt": 20,
    "eth_getBalance": 20,
    "eth_blockNumber": 10,
    "default": 26,
}


def cu_cost_for_method(method: str) -> int:
    return CU_COSTS.get(method, CU_COSTS["default"])


class CuRateLimiter:
    """Token-bucket limiter in Alchemy Compute Units per second."""

    def __init__(self, cu_per_sec: float, burst_cu: float | None = None):
        if cu_per_sec <= 0:
            raise ValueError("cu_per_sec must be > 0")
        self._rate = float(cu_per_sec)
        self._burst = float(burst_cu if burst_cu is not None else cu_per_sec * 2)
        self._tokens = self._burst
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._updated = now

    async def acquire(self, cu: int = 26) -> None:
        """Block until ``cu`` compute units are available."""
        need = max(1, int(cu))
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= need:
                    self._tokens -= need
                    return
                deficit = need - self._tokens
                wait = deficit / self._rate
            await asyncio.sleep(min(wait, 0.25))
