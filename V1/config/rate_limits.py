"""
Rate limits and retry configuration for external services.

These values are used by rate limiters and retry decorators.
"""

RATE_LIMITS: dict[str, dict] = {
    "mexc": {
        "requests_per_minute": 120,
        "burst": 5,
        "retry_attempts": 3,
        "backoff_initial_sec": 1,
        "backoff_max_sec": 30,
    },
    "dexscreener": {
        "requests_per_minute": 300,
        "burst": 5,
        "retry_attempts": 3,
        "backoff_initial_sec": 2,
        "backoff_max_sec": 60,
    },
    "geckoterminal": {
        "requests_per_minute": 20,
        "burst": 1,
        "cache_ttl_sec": 60,
        "retry_attempts": 3,
        "backoff_initial_sec": 2,
        "backoff_max_sec": 60,
        "notes": "Free tier rate limits to ~30 req/min. Conservative settings.",
    },
    "rpc": {
        "requests_per_minute": 500,
        "burst": 8,
        "multicall_batch_size": 50,
        "retry_attempts": 3,
        "backoff_initial_sec": 1,
        "backoff_max_sec": 30,
    },
}
