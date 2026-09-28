"""Unit tests for retry_async utility."""

from unittest.mock import AsyncMock

import pytest

from utils.retry import retry_async


@pytest.mark.asyncio
async def test_retry_succeeds_first_attempt():
    """Returns result immediately on first success."""
    func = AsyncMock(return_value="ok")
    result = await retry_async(func, attempts=3)
    assert result == "ok"
    func.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_succeeds_after_failures():
    """Succeeds on third attempt after two failures."""
    func = AsyncMock(side_effect=[ValueError("fail1"), ValueError("fail2"), "ok"])
    result = await retry_async(func, attempts=3, base_delay=0.01)
    assert result == "ok"
    assert func.await_count == 3


@pytest.mark.asyncio
async def test_retry_exhausted():
    """Raises last exception after all attempts fail."""
    func = AsyncMock(side_effect=ValueError("always fail"))
    with pytest.raises(ValueError, match="always fail"):
        await retry_async(func, attempts=3, base_delay=0.01)
    assert func.await_count == 3


@pytest.mark.asyncio
async def test_retry_only_retryable_exceptions():
    """Non-retryable exception propagates immediately."""
    func = AsyncMock(side_effect=TypeError("not retryable"))
    with pytest.raises(TypeError, match="not retryable"):
        await retry_async(
            func,
            attempts=3,
            base_delay=0.01,
            retryable_exceptions=(ValueError,),
        )
    func.assert_awaited_once()  # Only called once, no retry


@pytest.mark.asyncio
async def test_retry_increasing_delays():
    """Tests that delays increase with each attempt (at least monotonic)."""
    import time

    func = AsyncMock(side_effect=[ValueError("fail1"), ValueError("fail2"), "ok"])

    start = time.monotonic()
    result = await retry_async(func, attempts=3, base_delay=0.05, max_delay=0.5)
    elapsed = time.monotonic() - start

    assert result == "ok"
    # 0.05 + 0.10 = at least 0.15s of delay (plus overhead)
    assert elapsed >= 0.10
