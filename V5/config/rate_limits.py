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
        # Alchemy/Infura (~500 CU/s each) + optional dRPC (~100 req/s).
        # Per-URL CuRateLimiter; round-robin multiplies throughput.
        "alchemy_keys": 2,
        "cu_per_sec_per_key": 500,
        "cu_headroom": 0.95,
        "cu_per_sec_per_key_effective": 475,  # 500 * 0.95
        "eth_call_cu": 26,
        # dRPC bills primarily by request; map rps → CU budget for limiter.
        # Start conservative: advertised 100 rps often shared / bursts → 429.
        "drpc_requests_per_sec": 40,
        "drpc_headroom": 0.85,
        # Legacy rpm kept for non-CU callers; prefer CuRateLimiter.
        "requests_per_minute": 2200,
        "burst": 40,
        "multicall_batch_size": 50,
        "retry_attempts": 4,
        "backoff_initial_sec": 2,
        "backoff_max_sec": 30,
    },
}
