"""
Time formatting and conversion utilities.
"""

import time
from datetime import UTC, datetime


def now_utc() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(tz=UTC)


def now_timestamp() -> int:
    """Return the current Unix timestamp in seconds (int)."""
    return int(time.time())


def format_timestamp(ts: int, fmt: str = "%Y%m%d_%H%M%S") -> str:
    """Format a Unix timestamp as a string."""
    return time.strftime(fmt, time.gmtime(ts))


def datetime_from_timestamp(ts: float) -> datetime:
    """Create a UTC datetime from a Unix timestamp."""
    return datetime.fromtimestamp(ts, tz=UTC)
