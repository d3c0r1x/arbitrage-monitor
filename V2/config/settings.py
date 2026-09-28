"""
Application settings loaded from environment variables.

All monetary values are parsed as Decimal.
Secrets are never logged.
API keys in logs show only present=True/False.
URLs with keys are masked before logging.
"""

import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()


def _str_to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _parse_decimal(value: str | None, default: Decimal) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(value.strip())
    except (InvalidOperation, ValueError):
        logger.warning("invalid decimal value: %r, using default: %s", value, default)
        return default


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except (ValueError, TypeError):
        logger.warning("invalid int value: %r, using default: %s", value, default)
        return default


def _parse_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value.strip())
    except (ValueError, TypeError):
        logger.warning("invalid float value: %r, using default: %s", value, default)
        return default


class Settings:
    """
    Application settings.

    Access via `settings = Settings()` singleton or pass explicitly.
    All attribute names are UPPER_SNAKE_CASE.
    """

    def __init__(self) -> None:
        # ── MEXC ──────────────────────────────────────────────────────────
        self.MEXC_API_KEY: str = os.environ.get("MEXC_API_KEY", "")
        self.MEXC_API_SECRET: str = os.environ.get("MEXC_API_SECRET", "")

        # ── Alchemy (dual keys for round-robin) ────────────────────────────
        self.ALCHEMY_KEY_1: str = os.environ.get(
            "ALCHEMY_KEY_1", os.environ.get("ALCHEMY_KEY", "")
        )
        self.ALCHEMY_KEY_2: str = os.environ.get("ALCHEMY_KEY_2", "")
        # Backward compat.
        self.ALCHEMY_KEY: str = self.ALCHEMY_KEY_1
        # All available keys (for round-robin RPC).
        self.ALCHEMY_KEYS: list[str] = [
            k for k in (self.ALCHEMY_KEY_1, self.ALCHEMY_KEY_2) if k
        ]

        # ── RPC URLs ──────────────────────────────────────────────────────
        self.ETH_RPC_URL: str = os.environ.get("ETH_RPC_URL", "")
        self.BSC_RPC_URL: str = os.environ.get("BSC_RPC_URL", "")
        self.POLYGON_RPC_URL: str = os.environ.get("POLYGON_RPC_URL", "")
        self.ARBITRUM_RPC_URL: str = os.environ.get("ARBITRUM_RPC_URL", "")
        self.BASE_RPC_URL: str = os.environ.get("BASE_RPC_URL", "")
        self.ROBINHOOD_RPC_URL: str = os.environ.get("ROBINHOOD_RPC_URL", "")

        # ── Monetary parameters (Decimal) ─────────────────────────────────
        self.BASE_AMOUNT_USD: Decimal = _parse_decimal(
            os.environ.get("BASE_AMOUNT_USD"), Decimal("10")
        )
        self.MIN_NET_PROFIT_PCT: Decimal = _parse_decimal(
            os.environ.get("MIN_NET_PROFIT_PCT"), Decimal("0.1")
        )
        # Absolute USD floor off by default (gate is 0.1% on clip size).
        self.MIN_NET_PROFIT_USD: Decimal = _parse_decimal(
            os.environ.get("MIN_NET_PROFIT_USD"), Decimal("0")
        )
        # Higher signal threshold for ETHEREUM: mainnet gas eats thin edges.
        self.ETH_MIN_NET_PROFIT_PCT: Decimal = _parse_decimal(
            os.environ.get("ETH_MIN_NET_PROFIT_PCT"), Decimal("0.5")
        )

        # ── Timing parameters (int seconds) ───────────────────────────────
        self.POOL_REFRESH_INTERVAL_SEC: int = _parse_int(
            os.environ.get("POOL_REFRESH_INTERVAL_SEC"), 10800
        )
        self.SCAN_INTERVAL_SEC: int = _parse_int(
            os.environ.get("SCAN_INTERVAL_SEC"), 1
        )
        self.FEE_CACHE_TTL_SEC: int = _parse_int(
            os.environ.get("FEE_CACHE_TTL_SEC"), 60
        )
        self.MEXC_PRICE_CACHE_TTL_SEC: int = _parse_int(
            os.environ.get("MEXC_PRICE_CACHE_TTL_SEC"), 10
        )

        # ── Fee parameters (bps) ──────────────────────────────────────────
        self.MEXC_TAKER_FEE_BPS: int = _parse_int(
            os.environ.get("MEXC_TAKER_FEE_BPS"), 10
        )
        self.SLIPPAGE_BUFFER_BPS: int = _parse_int(
            os.environ.get("SLIPPAGE_BUFFER_BPS"), 30
        )

        # ── Backup ────────────────────────────────────────────────────────
        self.MAX_BACKUPS: int = _parse_int(os.environ.get("MAX_BACKUPS"), 5)

        # ── Integration test toggle ───────────────────────────────────────
        self.RUN_INTEGRATION: bool = _str_to_bool(
            os.environ.get("RUN_INTEGRATION"), False
        )

        # ── Candidate pagination ─────────────────────────────────────────
        # Candidates are processed in batches per refresh cycle (round-robin).
        # No volume filter — all MEXC pairs are scanned per plan principle #4.
        self.CANDIDATES_PER_REFRESH_CYCLE: int = _parse_int(
            os.environ.get("CANDIDATES_PER_REFRESH_CYCLE"), 500
        )

        # ── Pool source priority ──────────────────────────────────────────
        # G5: Default priority derived from discovery_sources.SOURCE_PRIORITY_TABLE.
        # Users can override via .env:
        #   POOL_SOURCE_PRIORITY=dexscreener,geckoterminal,onchain_factory
        from config.discovery_sources import SOURCE_PRIORITY_TABLE

        _default_priority = ",".join(
            sc.name for sc in sorted(SOURCE_PRIORITY_TABLE, key=lambda x: x.priority)
            if sc.enabled_by_default
        )
        raw_priority: str = os.environ.get(
            "POOL_SOURCE_PRIORITY",
            _default_priority,
        )
        self.POOL_SOURCE_PRIORITY: list[str] = [
            s.strip() for s in raw_priority.split(",") if s.strip()
        ]

        self.ENABLE_OPTIONAL_POOL_SOURCES: bool = _str_to_bool(
            os.environ.get("ENABLE_OPTIONAL_POOL_SOURCES"), False
        )
        self.DISCOVERY_SOURCE_TIMEOUT_SEC: int = _parse_int(
            os.environ.get("DISCOVERY_SOURCE_TIMEOUT_SEC"), 10
        )
        self.DISCOVERY_SOURCE_MAX_FAILURES: int = _parse_int(
            os.environ.get("DISCOVERY_SOURCE_MAX_FAILURES"), 3
        )
        # Hard cap on tokens probed via on-chain factory per refresh cycle.
        # Prevents eth_call floods that stall the initial refresh.
        self.ONCHAIN_FACTORY_MAX_TOKENS_PER_CYCLE: int = _parse_int(
            os.environ.get("ONCHAIN_FACTORY_MAX_TOKENS_PER_CYCLE"), 40
        )
        self.STALE_POOL_GRACE_SEC: int = _parse_int(
            os.environ.get("STALE_POOL_GRACE_SEC"), 86400
        )

        # ── Concurrency ───────────────────────────────────────────────────
        self.RPC_MAX_CONCURRENCY: int = _parse_int(
            os.environ.get("RPC_MAX_CONCURRENCY"), 20
        )
        self.RPC_MULTICALL_BATCH_SIZE: int = _parse_int(
            os.environ.get("RPC_MULTICALL_BATCH_SIZE"), 100
        )
        self.DISCOVERY_MAX_CONCURRENCY: int = _parse_int(
            os.environ.get("DISCOVERY_MAX_CONCURRENCY"), 25
        )
        self.SCANNER_MAX_CONCURRENCY: int = _parse_int(
            os.environ.get("SCANNER_MAX_CONCURRENCY"), 50
        )

        # ── Performance log ───────────────────────────────────────────────
        self.PERFORMANCE_LOG_FILE: str = os.environ.get(
            "PERFORMANCE_LOG_FILE", "data/performance.jsonl"
        )

        # ── Optional pool source API keys ─────────────────────────────────
        self.DEFINED_API_KEY: str = os.environ.get("DEFINED_API_KEY", "")
        self.DEXTOOLS_API_KEY: str = os.environ.get("DEXTOOLS_API_KEY", "")
        self.BIRDEYE_API_KEY: str = os.environ.get("BIRDEYE_API_KEY", "")
        self.MORALIS_API_KEY: str = os.environ.get("MORALIS_API_KEY", "")
        self.COVALENT_API_KEY: str = os.environ.get("COVALENT_API_KEY", "")
        self.SUBGRAPH_URL: str = os.environ.get("SUBGRAPH_URL", "")

        # ── Execution module ──────────────────────────────────────────────
        # Executor activates ONLY when DEX_PRIVATE_KEY is set.
        self.DEX_PRIVATE_KEY: str = os.environ.get("DEX_PRIVATE_KEY", "")
        # Dry-run: simulate via eth_call but never broadcast.
        self.EXECUTION_DRY_RUN: bool = _str_to_bool(
            os.environ.get("EXECUTION_DRY_RUN"), True
        )
        # Minimum net profit % to trigger execution.
        self.EXECUTION_MIN_PROFIT_PCT: Decimal = _parse_decimal(
            os.environ.get("EXECUTION_MIN_PROFIT_PCT"), Decimal("3")
        )
        # Max gas price in gwei. Skip if network gas exceeds.
        self.EXECUTION_MAX_GAS_GWEI: int = _parse_int(
            os.environ.get("EXECUTION_MAX_GAS_GWEI"), 50
        )
        # Slippage tolerance for execution swaps (bps).
        self.EXECUTION_SLIPPAGE_BPS: int = _parse_int(
            os.environ.get("EXECUTION_SLIPPAGE_BPS"), 50
        )
        # Daily realized-loss circuit breaker (USD). Execution halts for the
        # rest of the UTC day once cumulative losses exceed this value.
        self.DAILY_LOSS_LIMIT_USD: Decimal = _parse_decimal(
            os.environ.get("DAILY_LOSS_LIMIT_USD"), Decimal("50")
        )
        # Kill-switch: if this file exists, execution is paused.
        self.KILL_SWITCH_FILE: str = os.environ.get(
            "KILL_SWITCH_FILE", "data/KILL_SWITCH"
        )
        # Skip execution when the token needs more confirmations than this
        # (long transfer ETA = high price-drift risk).
        self.EXECUTION_MAX_CONFIRMATIONS: int = _parse_int(
            os.environ.get("EXECUTION_MAX_CONFIRMATIONS"), 64
        )

        # ── Signal watcher ────────────────────────────────────────────────
        # Re-quote interval for validated signals (seconds).
        self.WATCHER_INTERVAL_SEC: int = _parse_int(
            os.environ.get("WATCHER_INTERVAL_SEC"), 10
        )
        # Min profit to keep watching (below = remove).
        self.WATCHER_MIN_PROFIT_PCT: Decimal = _parse_decimal(
            os.environ.get("WATCHER_MIN_PROFIT_PCT"), Decimal("0.5")
        )
        # Max time to watch without execution before expiry (seconds).
        self.WATCHER_EXPIRY_SEC: int = _parse_int(
            os.environ.get("WATCHER_EXPIRY_SEC"), 300
        )
        # An opportunity is "fresh" (executable now) if re-quoted within
        # this many seconds AND still above WATCHER_MIN_PROFIT_PCT.
        self.FRESH_MAX_AGE_SEC: int = _parse_int(
            os.environ.get("FRESH_MAX_AGE_SEC"), 30
        )

        # ── Hot-set scanning ───────────────────────────────────────────
        # Every cycle scans the hot-set (recently profitable pools);
        # a full-universe scan runs every N cycles.
        self.FULL_SCAN_EVERY_N_CYCLES: int = _parse_int(
            os.environ.get("FULL_SCAN_EVERY_N_CYCLES"), 6
        )
        # How long a pool stays in the hot-set after a positive result.
        self.HOT_POOL_TTL_SEC: int = _parse_int(
            os.environ.get("HOT_POOL_TTL_SEC"), 1800
        )

        # ── Multi-hop chain engine ────────────────────────────────────────
        self.CHAIN_MAX_HOPS: int = _parse_int(
            os.environ.get("CHAIN_MAX_HOPS"), 4
        )
        self.CHAIN_MIN_PROFIT_PCT: Decimal = _parse_decimal(
            os.environ.get("CHAIN_MIN_PROFIT_PCT"), Decimal("1.5")
        )
        self.CHAIN_SCAN_EVERY_N_CYCLES: int = _parse_int(
            os.environ.get("CHAIN_SCAN_EVERY_N_CYCLES"), 10
        )

        # ── Same-pair cross-DEX (DEX ↔ DEX) ───────────────────────────────
        self.CROSS_DEX_MIN_PROFIT_PCT: Decimal = _parse_decimal(
            os.environ.get("CROSS_DEX_MIN_PROFIT_PCT"), Decimal("0.3")
        )
        self.CROSS_DEX_SCAN_EVERY_N_CYCLES: int = _parse_int(
            os.environ.get("CROSS_DEX_SCAN_EVERY_N_CYCLES"), 5
        )
        self.CROSS_DEX_MAX_PAIRS_PER_CYCLE: int = _parse_int(
            os.environ.get("CROSS_DEX_MAX_PAIRS_PER_CYCLE"), 40
        )

        # ── Alt-CEX probe (Bitget/HTX/BingX; isolated from MEXC×DEX loop) ─
        self.ALT_CEX_PROBE_ENABLED: bool = _str_to_bool(
            os.environ.get("ALT_CEX_PROBE_ENABLED"), True
        )
        self.ALT_CEX_ENABLE_OKX_DEX: bool = _str_to_bool(
            os.environ.get("ALT_CEX_ENABLE_OKX_DEX"), False
        )
        self.ALT_CEX_PROBE_INTERVAL_SEC: int = _parse_int(
            os.environ.get("ALT_CEX_PROBE_INTERVAL_SEC"), 45
        )
        self.ALT_CEX_MAP_TTL_SEC: int = _parse_int(
            os.environ.get("ALT_CEX_MAP_TTL_SEC"), 3600
        )
        self.ALT_CEX_MIN_PROFIT_PCT: Decimal = _parse_decimal(
            os.environ.get("ALT_CEX_MIN_PROFIT_PCT"), Decimal("0.5")
        )
        self.ALT_CEX_ORDERBOOK_TRIGGER_PCT: Decimal = _parse_decimal(
            os.environ.get("ALT_CEX_ORDERBOOK_TRIGGER_PCT"), Decimal("1.0")
        )
        self.ALT_CEX_BASE_AMOUNT_USD: Decimal = _parse_decimal(
            os.environ.get("ALT_CEX_BASE_AMOUNT_USD"), Decimal("50")
        )
        self.ALT_CEX_TAKER_FEE_BPS: int = _parse_int(
            os.environ.get("ALT_CEX_TAKER_FEE_BPS"), 10
        )
        self.ALT_CEX_MAX_TOKENS_PER_CYCLE: int = _parse_int(
            os.environ.get("ALT_CEX_MAX_TOKENS_PER_CYCLE"), 80
        )
        # Hard cap on RPC quotes so probe never starves the main scanner.
        self.ALT_CEX_MAX_DEX_QUOTES_PER_CYCLE: int = _parse_int(
            os.environ.get("ALT_CEX_MAX_DEX_QUOTES_PER_CYCLE"), 8
        )

    @property
    def mexc_api_key_present(self) -> bool:
        return bool(self.MEXC_API_KEY)

    @property
    def mexc_api_secret_present(self) -> bool:
        return bool(self.MEXC_API_SECRET)

    @property
    def alchemy_key_present(self) -> bool:
        return bool(self.ALCHEMY_KEY)

    @property
    def dex_private_key_present(self) -> bool:
        return bool(self.DEX_PRIVATE_KEY)

    def log_key_presence(self) -> dict[str, Any]:
        """Return a dict of secret presence flags for safe logging."""
        return {
            "mexc_api_key_present": self.mexc_api_key_present,
            "mexc_api_secret_present": self.mexc_api_secret_present,
            "alchemy_key_present": self.alchemy_key_present,
            "dex_private_key_present": self.dex_private_key_present,
            "defined_api_key_present": bool(self.DEFINED_API_KEY),
            "dextools_api_key_present": bool(self.DEXTOOLS_API_KEY),
            "birdeye_api_key_present": bool(self.BIRDEYE_API_KEY),
            "moralis_api_key_present": bool(self.MORALIS_API_KEY),
            "covalent_api_key_present": bool(self.COVALENT_API_KEY),
            "subgraph_url_present": bool(self.SUBGRAPH_URL),
        }


# Module-level singleton for convenience.
settings = Settings()
