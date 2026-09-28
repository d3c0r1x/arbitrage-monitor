"""Tests for Alchemy CU rate limiter."""

import asyncio
import time

import pytest

from utils.cu_rate_limiter import CuRateLimiter, cu_cost_for_method


class TestCuCosts:
    def test_eth_call_weight(self):
        assert cu_cost_for_method("eth_call") == 26

    def test_budget_math(self):
        # 2 keys × 500 × 0.95 = 950 CU/s → ~36 eth_call/s
        total = 2 * 500 * 0.95
        assert total == 950
        assert int(total / 26) == 36


class TestCuRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_consumes_tokens(self):
        lim = CuRateLimiter(cu_per_sec=260, burst_cu=260)  # 10 eth_call/s
        t0 = time.monotonic()
        for _ in range(10):
            await lim.acquire(26)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5  # burst covers first 10

    @pytest.mark.asyncio
    async def test_blocks_when_empty(self):
        lim = CuRateLimiter(cu_per_sec=26, burst_cu=26)  # 1 eth_call/s
        await lim.acquire(26)
        t0 = time.monotonic()
        await lim.acquire(26)
        assert time.monotonic() - t0 >= 0.5
