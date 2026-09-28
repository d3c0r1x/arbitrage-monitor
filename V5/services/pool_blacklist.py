"""Dead-pool blacklist with exponential TTL (plan v3, Phase 1.4).

Pools whose reserves repeatedly return None (drained, self-destructed,
fake) are temporarily excluded from scanning so they stop burning RPC
budget every cycle.
"""

import logging
import time

logger = logging.getLogger(__name__)


class PoolBlacklist:
    """In-memory failure tracker: N consecutive fails => timed block."""

    def __init__(self, max_fails: int = 3, base_ttl: int = 3600):
        self._fails: dict[str, int] = {}
        self._blocked_until: dict[str, float] = {}
        self.max_fails = max_fails
        self.base_ttl = base_ttl

    def is_blocked(self, key: str) -> bool:
        until = self._blocked_until.get(key, 0)
        if time.time() < until:
            return True
        if key in self._blocked_until:
            # Block expired: give the pool a fresh start.
            del self._blocked_until[key]
            self._fails.pop(key, None)
        return False

    def record_fail(self, key: str) -> None:
        count = self._fails.get(key, 0) + 1
        self._fails[key] = count
        if count >= self.max_fails:
            # Exponential TTL: 1h, 2h, 4h... per extra failure streak.
            ttl = self.base_ttl * (2 ** (count - self.max_fails))
            self._blocked_until[key] = time.time() + ttl
            logger.warning(
                "pool_blacklisted: %s ttl=%ds fails=%d", key, ttl, count
            )

    def record_success(self, key: str) -> None:
        self._fails.pop(key, None)
        self._blocked_until.pop(key, None)

    @property
    def blocked_count(self) -> int:
        now = time.time()
        return sum(1 for t in self._blocked_until.values() if t > now)
