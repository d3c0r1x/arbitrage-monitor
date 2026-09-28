"""
Rate limiter with semaphore-based concurrency limiting.

Supports adaptive concurrency: reduces on 429/timeout,
increases when error rate is low.
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class SimpleRateLimiter:
    """Rate limiter using a semaphore with interval-based release."""

    def __init__(self, requests_per_minute: int, burst: int = 5):
        self._rpm = requests_per_minute
        self._burst = burst
        self._semaphore = asyncio.Semaphore(burst)
        self._interval_sec = 60.0 / float(requests_per_minute) if requests_per_minute > 0 else 0.0
        # C2: Keep strong references to background tasks to prevent GC.
        self._pending_tasks: set[asyncio.Task] = set()

    async def acquire(self) -> None:
        """Acquire a permit, blocking until one is available."""
        await self._semaphore.acquire()

        if self._interval_sec > 0:
            async def release_later() -> None:
                await asyncio.sleep(self._interval_sec)
                self._semaphore.release()

            task = asyncio.create_task(release_later())
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        else:
            self._semaphore.release()


class AdaptiveConcurrencyController:
    """Adaptive concurrency control.

    Reduces concurrency on 429 or timeout.
    Gradually increases concurrency when error rate is low.
    """

    def __init__(self, initial_concurrency: int, min_concurrency: int = 1, max_concurrency: int | None = None):
        self._concurrency = initial_concurrency
        self._min_concurrency = min_concurrency
        self._max_concurrency = max_concurrency or initial_concurrency
        self._semaphore = asyncio.Semaphore(initial_concurrency)
        self._debt = 0  # C3: permits owed when reducing below available.

        self._reduced_until: float = 0.0
        self._errors: list[float] = []
        self._total_requests: list[float] = []
        self._window_sec = 300.0  # 5 minutes

    def _prune_window(self) -> None:
        now = time.time()
        cutoff = now - self._window_sec
        self._errors = [t for t in self._errors if t > cutoff]
        self._total_requests = [t for t in self._total_requests if t > cutoff]

    def record_success(self) -> None:
        self._prune_window()
        self._total_requests.append(time.time())
        self._maybe_increase()

    def record_error(self, is_rate_limit: bool = False) -> None:
        self._prune_window()
        now = time.time()
        self._errors.append(now)
        self._total_requests.append(now)

        if is_rate_limit:
            self._reduce()

    def _reduce(self) -> None:
        new_value = max(self._min_concurrency, self._concurrency // 2)
        if new_value != self._concurrency:
            # C3: Actually reduce semaphore capacity by acquiring permits.
            permits_to_remove = self._concurrency - new_value
            for _ in range(permits_to_remove):
                # Non-blocking acquire: if no permits available, track debt.
                if self._semaphore._value > 0:  # noqa: SLF001
                    self._semaphore._value -= 1  # noqa: SLF001
                else:
                    self._debt += 1
            self._concurrency = new_value
            self._reduced_until = time.time() + 60.0
            logger.warning("reduced concurrency to %d due to rate limit", self._concurrency)

    def _maybe_increase(self) -> None:
        if time.time() < self._reduced_until:
            return
        if len(self._total_requests) < 50:
            return
        error_rate = len(self._errors) / len(self._total_requests)
        if error_rate < 0.01 and self._concurrency < self._max_concurrency:
            self._concurrency += 1
            # C3: Actually increase semaphore capacity by releasing a permit.
            if self._debt > 0:
                self._debt -= 1
            else:
                self._semaphore.release()
            logger.info("increased concurrency to %d (error_rate=%.4f)", self._concurrency, error_rate)

    async def acquire(self) -> None:
        await self._semaphore.acquire()

    def release(self) -> None:
        self._semaphore.release()
