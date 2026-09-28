"""
Unit tests for rate_limiter.

Tests:
- SimpleRateLimiter: acquire/release lifecycle, zero RPM edge case
- AdaptiveConcurrencyController: reduce on rate limit, increase on low error rate
- AdaptiveConcurrencyController: window pruning, semaphore acquire/release
"""


import pytest


class TestSimpleRateLimiter:
    """Tests for SimpleRateLimiter."""

    def test_acquire_release_creates_task(self):
        """Acquire returns immediately and creates a scheduled release."""
        from utils.rate_limiter import SimpleRateLimiter

        limiter = SimpleRateLimiter(requests_per_minute=60, burst=5)
        # Should not block during test
        import asyncio
        asyncio.run(self._do_acquire(limiter))

    async def _do_acquire(self, limiter):
        await limiter.acquire()
        # After acquire, semaphore should have 1 less permit
        assert limiter._semaphore._value == limiter._burst - 1

    def test_zero_rpm_releases_immediately(self):
        """Zero RPM means no rate limiting — immediate release."""
        from utils.rate_limiter import SimpleRateLimiter

        limiter = SimpleRateLimiter(requests_per_minute=0, burst=5)
        import asyncio
        asyncio.run(self._do_acquire_zero(limiter))

    async def _do_acquire_zero(self, limiter):
        await limiter.acquire()
        # At 0 RPM, the code immediately releases the semaphore
        # so value should still be burst (acquire + release = no change)
        assert limiter._semaphore._value == limiter._burst

    def test_interval_calculation(self):
        """Interval is correctly calculated from RPM."""
        from utils.rate_limiter import SimpleRateLimiter

        limiter = SimpleRateLimiter(requests_per_minute=60, burst=1)
        # 60 RPM = 1 request/second = 1.0 sec interval
        assert limiter._interval_sec == 1.0

        limiter2 = SimpleRateLimiter(requests_per_minute=120, burst=1)
        # 120 RPM = 2 requests/second = 0.5 sec interval
        assert limiter2._interval_sec == 0.5

        limiter3 = SimpleRateLimiter(requests_per_minute=0, burst=1)
        # 0 RPM = 0 interval
        assert limiter3._interval_sec == 0.0


class TestAdaptiveConcurrencyController:
    """Tests for AdaptiveConcurrencyController."""

    @pytest.fixture
    def controller(self):
        from utils.rate_limiter import AdaptiveConcurrencyController

        ctrl = AdaptiveConcurrencyController(
            initial_concurrency=10,
            min_concurrency=1,
            max_concurrency=20,
        )
        return ctrl

    def test_initial_concurrency(self, controller):
        """Initial concurrency is set correctly."""
        assert controller._concurrency == 10

    def test_reduce_on_rate_limit(self, controller):
        """Rate limit reduces concurrency by half."""
        controller._reduce()
        assert controller._concurrency == 5  # 10 // 2 = 5

    def test_reduce_respects_minimum(self, controller):
        """Reduce cannot go below min_concurrency."""
        controller._concurrency = 2
        controller._reduce()
        assert controller._concurrency == 1  # max(1, 2//2=1) = 1

        controller._reduce()
        assert controller._concurrency == 1  # max(1, 1//2=0) = 1

    def test_reduce_sets_reduced_until(self, controller):
        """Reduce sets _reduced_until to now + 60s."""
        import time
        before = time.time()
        controller._reduce()
        after = time.time()
        assert before + 60 <= controller._reduced_until <= after + 60

    def test_record_error_rate_limit_reduces(self, controller):
        """record_error with is_rate_limit=True triggers reduce."""
        controller._concurrency = 10
        controller.record_error(is_rate_limit=True)
        assert controller._concurrency < 10

    def test_record_error_no_rate_limit_no_reduce(self, controller):
        """record_error without is_rate_limit=False does not reduce."""
        initial = controller._concurrency
        controller.record_error(is_rate_limit=False)
        # Should not reduce, just record the error
        assert controller._concurrency == initial

    def test_maybe_increase_on_low_error_rate(self, controller):
        """Maybe increase concurrency when error rate < 1%."""
        import time
        now = time.time()

        # Fill request window with mostly successes
        for _ in range(100):
            controller._total_requests.append(now)
        # Only 1 error out of 100 = 1%
        controller._errors = [now - 10]
        controller._total_requests.append(now - 10)

        controller._reduced_until = 0  # Not in reduced period
        controller._maybe_increase()
        assert controller._concurrency == 11  # increased by 1

    def test_maybe_increase_not_during_reduced_period(self, controller):
        """Do not increase during 60s reduced period."""
        import time
        controller._reduced_until = time.time() + 30  # Reduced for 30 more seconds
        initial = controller._concurrency
        # Fill window with low errors
        for _ in range(100):
            controller._total_requests.append(time.time())

        controller._maybe_increase()
        assert controller._concurrency == initial  # No increase

    def test_maybe_increase_not_enough_samples(self, controller):
        """Do not increase if fewer than 50 requests in window."""
        import time
        controller._reduced_until = 0
        initial = controller._concurrency
        # Only 10 requests
        for _ in range(10):
            controller._total_requests.append(time.time())

        controller._maybe_increase()
        assert controller._concurrency == initial

    def test_maybe_increase_respects_max(self, controller):
        """Do not increase beyond max_concurrency."""
        import time
        controller._concurrency = 20  # Already at max
        controller._reduced_until = 0
        for _ in range(100):
            controller._total_requests.append(time.time())

        controller._maybe_increase()
        assert controller._concurrency == 20  # Stayed at max

    def test_semaphore_acquire_release(self, controller):
        """Semaphore acquire decrements, release increments value."""
        import asyncio

        async def test():
            initial_value = controller._semaphore._value
            await controller.acquire()
            assert controller._semaphore._value == initial_value - 1
            controller.release()
            assert controller._semaphore._value == initial_value

        asyncio.run(test())

    def test_prune_window(self, controller):
        """Old records are pruned from the window."""
        import time
        old = time.time() - 1000  # More than 5 min ago
        recent = time.time() - 10

        controller._errors = [old, recent]
        controller._total_requests = [old, recent]

        controller._prune_window()

        assert old not in controller._errors
        assert recent in controller._errors
        assert old not in controller._total_requests
        assert recent in controller._total_requests

    def test_record_success_adds_to_window(self, controller):
        """record_success adds a timestamp and may increase concurrency."""
        import time
        controller._reduced_until = 0
        # Fill window with enough successes for increase check
        for _ in range(60):
            controller._total_requests.append(time.time())

        len_before = len(controller._total_requests)
        controller.record_success()
        assert len(controller._total_requests) == len_before + 1
