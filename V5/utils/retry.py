"""Async retry with exponential backoff."""

import asyncio
import logging
from typing import Any

from utils.logging import mask_secrets

logger = logging.getLogger(__name__)


async def retry_async(
    func,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    retryable_exceptions=(Exception,),
    retry_predicate=None,
) -> Any:
    """Retry an async callable with exponential backoff.

    Args:
        func: Async callable (e.g. lambda or partial) to retry.
        attempts: Maximum number of attempts (default 3).
        base_delay: Initial delay in seconds (default 1.0).
        max_delay: Maximum delay cap in seconds (default 10.0).
        retryable_exceptions: Tuple of exception types to retry on.
        retry_predicate: Optional callable(exc) -> bool. If provided,
            only retries when predicate returns True for the exception.

    Returns:
        The result of the first successful call to func.

    Raises:
        The last exception raised by func after all attempts are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await func()
        except retryable_exceptions as exc:
            # If predicate is provided, only retry when it returns True.
            if retry_predicate is not None and not retry_predicate(exc):
                raise
            last_exc = exc
            if attempt < attempts - 1:
                delay = min(base_delay * (2**attempt), max_delay)
                logger.debug(
                    "retry_attempt %d/%d after %.1fs: %s",
                    attempt + 1, attempts, delay, mask_secrets(str(exc)),
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(
                    "retry_exhausted %d/%d: %s",
                    attempt + 1, attempts, mask_secrets(str(exc)),
                )

    raise last_exc  # type: ignore[misc]
