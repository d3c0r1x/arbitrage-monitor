"""Circuit breaker for RPC endpoints (plan v3, Phase 2.3).

After N consecutive failures the endpoint is considered down and all
calls short-circuit for a cooldown period instead of burning retries
and rate-limit budget against a dead/throttled endpoint.
"""

import logging
import time

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Consecutive-failure breaker with timed cooldown."""

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_sec: int = 60,
        name: str = "",
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_sec = cooldown_sec
        self.name = name
        self._failures = 0
        self._open_until = 0.0

    @property
    def is_open(self) -> bool:
        if time.time() < self._open_until:
            return True
        if self._failures >= self.failure_threshold:
            self._open_until = time.time() + self.cooldown_sec
            self._failures = 0
            logger.warning(
                "circuit_breaker_open: %s cooldown=%ds",
                self.name or "unnamed", self.cooldown_sec,
            )
            try:
                from metrics.health import increment_metrics
                increment_metrics(circuit_open_total=1)
            except Exception:
                pass
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1
