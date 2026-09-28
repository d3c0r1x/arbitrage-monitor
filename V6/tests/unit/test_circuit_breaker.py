"""Unit tests for utils.circuit_breaker (plan v3, Phase 2.3)."""

import time

from utils.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    def test_closed_initially(self):
        cb = CircuitBreaker()
        assert not cb.is_open

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_sec=60)
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open

    def test_success_resets_failures(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_open

    def test_closes_after_cooldown(self, monkeypatch):
        cb = CircuitBreaker(failure_threshold=1, cooldown_sec=30)
        cb.record_failure()
        assert cb.is_open

        import utils.circuit_breaker as mod
        real_now = time.time()
        monkeypatch.setattr(mod.time, "time", lambda: real_now + 31)
        assert not cb.is_open
