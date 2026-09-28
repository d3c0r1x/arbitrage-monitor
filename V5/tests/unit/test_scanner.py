"""
Unit tests for Scanner.

Tests:
- _load_pools_json: file exists, file missing, file empty, retry logic
- refresh_pool_cache: calls load, logs counts
- run_cycle: overrun protection (sequential calls)
- _execute_cycle: full pipeline (price refresh, pool processing, quote, signal)
- process_pool: coin missing, price missing, adapter missing, quote fails, signal found
- _write_signal: creates ArbitrageSignal with correct warnings
- _write_signal: writes signal (reserves checked in scanner, no percentage filter)
"""

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.signal_models import FeeBreakdown
from scanner.scanner import Scanner


class TestScannerLoadPoolsJson:
    """Tests for Scanner._load_pools_json."""

    @pytest.fixture
    def scanner(self, tmp_path):
        s = Scanner.__new__(Scanner)
        s._pools_cache_path = tmp_path / "pools_cache.json"
        s._price_service = MagicMock()
        s._profit_calculator = MagicMock()
        s._adapter_factory = MagicMock()
        s._signal_writer = MagicMock()
        s._rpc_client_factory = MagicMock()
        s._token_security_checker = None
        s._pool_security_checker = None
        s._orderbook_service = None
        return s

    def test_loads_valid_json(self, scanner, tmp_path):
        """Returns list of pool dicts when file exists with valid JSON."""
        pools_data = [
            {"network": "ETHEREUM", "pool_address": "0xabc", "dex": "uniswap"},
            {"network": "ARBITRUM", "pool_address": "0xdef", "dex": "sushiswap"},
        ]
        scanner._pools_cache_path.write_text(json.dumps(pools_data))

        result = scanner._load_pools_json()
        assert len(result) == 2
        assert result[0]["network"] == "ETHEREUM"

    def test_returns_empty_when_file_missing(self, scanner):
        """Returns empty list when cache file does not exist."""
        # File doesn't exist yet
        result = scanner._load_pools_json(log_empty_warning=False)
        assert result == []

    def test_returns_empty_when_file_empty(self, scanner):
        """Returns empty list when JSON is empty list."""
        scanner._pools_cache_path.write_text("[]")

        result = scanner._load_pools_json(log_empty_warning=False)
        assert result == []

    def test_retries_on_empty_file(self, scanner):
        """Retries once and returns [] when cache file is empty after retry."""
        scanner._pools_cache_path.write_text("[]")

        result = scanner._load_pools_json(log_empty_warning=False)
        assert result == []

    def test_returns_empty_on_parse_error(self, scanner):
        """Returns empty list on JSON decode error after retry."""
        scanner._pools_cache_path.write_text("invalid json")

        result = scanner._load_pools_json(log_empty_warning=False)
        assert result == []


class TestScannerRefreshPoolCache:
    """Tests for Scanner.refresh_pool_cache."""

    @pytest.fixture
    def scanner(self):
        from scanner.scanner import Scanner

        s = Scanner.__new__(Scanner)
        s._pools_cache_path = MagicMock(spec=Path)
        s._price_service = MagicMock()
        s._profit_calculator = MagicMock()
        s._adapter_factory = MagicMock()
        s._signal_writer = MagicMock()
        s._rpc_client_factory = MagicMock()
        s._token_security_checker = None
        s._pool_security_checker = None
        s._orderbook_service = None
        return s

    def test_returns_pool_count(self, scanner):
        """Returns number of pools loaded from cache."""
        scanner._load_pools_json = MagicMock(return_value=[{"network": "ETH"}])
        count = scanner.refresh_pool_cache()
        assert count == 1

    def test_returns_zero_on_empty(self, scanner):
        """Returns 0 when cache is empty."""
        scanner._load_pools_json = MagicMock(return_value=[])
        count = scanner.refresh_pool_cache()
        assert count == 0


class TestScannerRunCycle:
    """Tests for Scanner.run_cycle and _execute_cycle."""

    @pytest.fixture
    def scanner(self, tmp_path):
        s = Scanner.__new__(Scanner)
        s._pools_cache_path = tmp_path / "pools_cache.json"
        s._price_service = MagicMock()
        s._price_service.refresh_if_expired = AsyncMock(return_value=True)
        s._profit_calculator = MagicMock()
        s._adapter_factory = MagicMock()
        s._signal_writer = MagicMock()
        s._signal_writer.write_signal = AsyncMock()
        s._rpc_client_factory = MagicMock()
        s._is_running = False
        s._lock = MagicMock()
        lock_cm = MagicMock()
        lock_cm.__aenter__ = AsyncMock()
        lock_cm.__aexit__ = AsyncMock()
        s._lock = lock_cm
        s._token_security_checker = None
        s._pool_security_checker = None
        s._orderbook_service = None
        s._hot_pools = {}
        s._scan_cycle = 0
        s._signal_watcher = None
        s._chain_engine = None
        from services.pool_blacklist import PoolBlacklist
        s._pool_blacklist = PoolBlacklist()
        return s

    @pytest.mark.asyncio
    async def test_skips_when_already_running(self, scanner):
        """Returns skipped result when cycle is already running."""
        scanner._is_running = True
        result = await scanner.run_cycle()
        assert result.get("skipped") is True

    @pytest.mark.asyncio
    async def test_resets_is_running_after_cycle(self, scanner):
        """_is_running is reset to False after cycle completes."""
        scanner._load_pools_json = MagicMock(return_value=[])
        with patch("scanner.scanner.performance_timer") as mock_timer:
            timer_cm = AsyncMock()
            timer_cm.__aenter__ = AsyncMock(return_value=MagicMock())
            timer_cm.__aexit__ = AsyncMock()
            mock_timer.return_value = timer_cm
            await scanner.run_cycle()
        assert scanner._is_running is False

    @pytest.mark.asyncio
    async def test_execute_cycle_loads_pools(self, scanner):
        """_execute_cycle loads pools and returns counts."""
        scanner._load_pools_json = MagicMock(return_value=[])
        scanner._price_service.refresh_if_expired = AsyncMock(return_value=True)

        timer = MagicMock()
        result = await scanner._execute_cycle(timer)
        assert result["pools_total"] == 0
        scanner._price_service.refresh_if_expired.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_pool_missing_coin(self, scanner):
        """Pool without token_coin is counted as quote_failed."""
        pool = {
            "network": "BSC",
            "pool_address": "0xpool",
            "dex": "uniswap",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_version": "v2",
            "token_decimals": 18,
            "stablecoin_decimals": 18,
        }
        scanner._load_pools_json = MagicMock(return_value=[pool])
        scanner._price_service.refresh_if_expired = AsyncMock(return_value=True)

        timer = MagicMock()
        with patch("scanner.scanner.settings") as mock_settings:
            mock_settings.SCANNER_MAX_CONCURRENCY = 5
            mock_settings.FULL_SCAN_EVERY_N_CYCLES = 1
            mock_settings.HOT_POOL_TTL_SEC = 300
            mock_settings.CHAIN_SCAN_EVERY_N_CYCLES = 10
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            mock_settings.MIN_NET_PROFIT_PCT = Decimal("1")
            mock_settings.MIN_NET_PROFIT_USD = Decimal("0.1")
            mock_settings.MEXC_TAKER_FEE_BPS = 10
            mock_settings.ETH_MIN_NET_PROFIT_PCT = Decimal("2")
            result = await scanner._execute_cycle(timer)

        assert result["pools_total"] == 1
        assert result["quotes_failed"] == 1

    @pytest.mark.asyncio
    async def test_process_pool_no_price(self, scanner):
        """Pool with token_coin but no MEXC price is counted as quote_failed."""
        pool = {
            "network": "BSC",
            "pool_address": "0xpool",
            "dex": "uniswap",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_version": "v2",
            "token_decimals": 18,
            "stablecoin_decimals": 18,
            "token_coin": "TOKEN",
        }
        scanner._load_pools_json = MagicMock(return_value=[pool])
        scanner._price_service.refresh_if_expired = AsyncMock(return_value=True)
        scanner._price_service.get_price = MagicMock(return_value=None)

        timer = MagicMock()
        with patch("scanner.scanner.settings") as mock_settings:
            mock_settings.SCANNER_MAX_CONCURRENCY = 5
            mock_settings.FULL_SCAN_EVERY_N_CYCLES = 1
            mock_settings.HOT_POOL_TTL_SEC = 300
            mock_settings.CHAIN_SCAN_EVERY_N_CYCLES = 10
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            mock_settings.MIN_NET_PROFIT_PCT = Decimal("1")
            mock_settings.MIN_NET_PROFIT_USD = Decimal("0.1")
            mock_settings.MEXC_TAKER_FEE_BPS = 10
            mock_settings.ETH_MIN_NET_PROFIT_PCT = Decimal("2")
            result = await scanner._execute_cycle(timer)

        assert result["quotes_failed"] == 1
        assert result["has_coin"] == 1

    @pytest.mark.asyncio
    async def test_process_pool_no_adapter(self, scanner):
        """Pool with price but no adapter is counted as quote_failed."""
        pool = {
            "network": "BSC",
            "pool_address": "0xpool",
            "dex": "uniswap",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_version": "v2",
            "token_decimals": 18,
            "stablecoin_decimals": 18,
            "token_coin": "TOKEN",
        }
        scanner._load_pools_json = MagicMock(return_value=[pool])
        scanner._price_service.refresh_if_expired = AsyncMock(return_value=True)
        scanner._price_service.get_price = MagicMock(return_value=Decimal("1.50"))
        scanner._adapter_factory.get_adapter = MagicMock(return_value=None)

        timer = MagicMock()
        with patch("scanner.scanner.settings") as mock_settings:
            mock_settings.SCANNER_MAX_CONCURRENCY = 5
            mock_settings.FULL_SCAN_EVERY_N_CYCLES = 1
            mock_settings.HOT_POOL_TTL_SEC = 300
            mock_settings.CHAIN_SCAN_EVERY_N_CYCLES = 10
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            mock_settings.MIN_NET_PROFIT_PCT = Decimal("1")
            mock_settings.MIN_NET_PROFIT_USD = Decimal("0.1")
            mock_settings.MEXC_TAKER_FEE_BPS = 10
            mock_settings.ETH_MIN_NET_PROFIT_PCT = Decimal("2")
            result = await scanner._execute_cycle(timer)

        assert result["quotes_failed"] == 1
        assert result["has_adapter"] == 0

    @pytest.mark.asyncio
    async def test_process_pool_quote_fails(self, scanner):
        """Pool where quote returns 0 is counted as quote_failed."""
        pool = {
            "network": "BSC",
            "pool_address": "0xpool",
            "dex": "uniswap",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_version": "v2",
            "token_decimals": 18,
            "stablecoin_decimals": 18,
            "token_coin": "TOKEN",
        }
        scanner._load_pools_json = MagicMock(return_value=[pool])
        scanner._price_service.refresh_if_expired = AsyncMock(return_value=True)
        scanner._price_service.get_price = MagicMock(return_value=Decimal("1.50"))
        adapter = MagicMock()
        adapter.quote_exact_input = AsyncMock(return_value=0)
        scanner._adapter_factory.get_adapter = MagicMock(return_value=adapter)

        timer = MagicMock()
        with patch("scanner.scanner.settings") as mock_settings:
            mock_settings.SCANNER_MAX_CONCURRENCY = 5
            mock_settings.FULL_SCAN_EVERY_N_CYCLES = 1
            mock_settings.HOT_POOL_TTL_SEC = 300
            mock_settings.CHAIN_SCAN_EVERY_N_CYCLES = 10
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            mock_settings.MIN_NET_PROFIT_PCT = Decimal("1")
            mock_settings.MIN_NET_PROFIT_USD = Decimal("0.1")
            mock_settings.MEXC_TAKER_FEE_BPS = 10
            mock_settings.ETH_MIN_NET_PROFIT_PCT = Decimal("2")
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            result = await scanner._execute_cycle(timer)

        assert result["quotes_failed"] == 1
        assert result["has_quote"] == 0

    @pytest.mark.asyncio
    async def test_process_pool_full_signal(self, scanner):
        """Full pipeline: pool with quote and profit produces a signal."""
        pool = {
            "network": "BSC",
            "pool_address": "0xpool",
            "dex": "uniswap",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_version": "v2",
            "token_decimals": 18,
            "stablecoin_decimals": 18,
            "token_coin": "TOKEN",
        }
        scanner._load_pools_json = MagicMock(return_value=[pool])
        scanner._price_service.refresh_if_expired = AsyncMock(return_value=True)
        scanner._price_service.get_price = MagicMock(return_value=Decimal("1.50"))

        adapter = MagicMock()
        adapter.quote_exact_input = AsyncMock(return_value=Decimal("15000000000000000000"))
        # Mock reserves: 10000 stablecoin, 10000 token (enough liquidity).
        adapter.get_reserves = AsyncMock(return_value=(
            Decimal("10000000000000000000000"),  # reserve_in (10000 * 10^18)
            Decimal("10000000000000000000000"),  # reserve_out
        ))
        scanner._adapter_factory.get_adapter = MagicMock(return_value=adapter)

        scanner._profit_calculator.calculate_direction_a = AsyncMock(return_value={
            "net_profit_usd": Decimal("0.50"),
            "signal": True,
            "net_profit_pct": Decimal("5.0"),
            "direction": "DEX_BUY_MEXC_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.50"),
            "gross_profit_pct": Decimal("5.0"),
            "fees": FeeBreakdown(
                dex_network_fee_usd=Decimal("0.05"),
                mexc_trading_fee_usd=Decimal("0.01"),
            ),
        })
        # Direction B returns signal=False so it doesn't double-count.
        scanner._profit_calculator.calculate_direction_b = AsyncMock(return_value={
            "net_profit_usd": Decimal("0.00"),
            "signal": False,
            "net_profit_pct": Decimal("0.0"),
            "direction": "MEXC_BUY_DEX_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.00"),
            "gross_profit_pct": Decimal("0.0"),
            "fees": FeeBreakdown(),
        })
        scanner._signal_writer.write_signal = AsyncMock()

        timer = MagicMock()
        with patch("scanner.scanner.settings") as mock_settings:
            mock_settings.SCANNER_MAX_CONCURRENCY = 5
            mock_settings.FULL_SCAN_EVERY_N_CYCLES = 1
            mock_settings.HOT_POOL_TTL_SEC = 300
            mock_settings.CHAIN_SCAN_EVERY_N_CYCLES = 10
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            mock_settings.MIN_NET_PROFIT_PCT = Decimal("1")
            mock_settings.MIN_NET_PROFIT_USD = Decimal("0.1")
            mock_settings.MEXC_TAKER_FEE_BPS = 10
            mock_settings.ETH_MIN_NET_PROFIT_PCT = Decimal("2")
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            result = await scanner._execute_cycle(timer)

        assert result["quotes_success"] == 2  # A + B both quote successfully
        assert result["signals_found"] == 1  # Only Direction A produces signal
        assert result["signals_written"] == 1
        assert result["has_quote"] == 2  # A + B directions
        assert result["net_positive"] == 1  # Only Direction A is net positive
        scanner._signal_writer.write_signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_pool_full_signal_both_directions(self, scanner):
        """Both Direction A and Direction B produce signals."""
        pool = {
            "network": "BSC",
            "pool_address": "0xpool",
            "dex": "uniswap",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_version": "v2",
            "token_decimals": 18,
            "stablecoin_decimals": 18,
            "token_coin": "TOKEN",
        }
        scanner._load_pools_json = MagicMock(return_value=[pool])
        scanner._price_service.refresh_if_expired = AsyncMock(return_value=True)
        scanner._price_service.get_price = MagicMock(return_value=Decimal("1.50"))

        adapter = MagicMock()
        adapter.quote_exact_input = AsyncMock(return_value=Decimal("15000000000000000000"))
        # Mock reserves: 10000 stablecoin, 10000 token (enough liquidity).
        adapter.get_reserves = AsyncMock(return_value=(
            Decimal("10000000000000000000000"),  # reserve_in (10000 * 10^18)
            Decimal("10000000000000000000000"),  # reserve_out
        ))
        scanner._adapter_factory.get_adapter = MagicMock(return_value=adapter)

        scanner._profit_calculator.calculate_direction_a = AsyncMock(return_value={
            "net_profit_usd": Decimal("0.50"),
            "signal": True,
            "net_profit_pct": Decimal("5.0"),
            "direction": "DEX_BUY_MEXC_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.50"),
            "gross_profit_pct": Decimal("5.0"),
            "fees": FeeBreakdown(
                dex_network_fee_usd=Decimal("0.05"),
                mexc_trading_fee_usd=Decimal("0.01"),
                total=Decimal("0.06"),
            ),
        })
        # Direction B also returns a profitable signal.
        scanner._profit_calculator.calculate_direction_b = AsyncMock(return_value={
            "net_profit_usd": Decimal("0.30"),
            "signal": True,
            "net_profit_pct": Decimal("3.0"),
            "direction": "MEXC_BUY_DEX_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.30"),
            "gross_profit_pct": Decimal("3.0"),
            "fees": FeeBreakdown(
                dex_network_fee_usd=Decimal("0.05"),
                mexc_trading_fee_usd=Decimal("0.01"),
                total=Decimal("0.06"),
            ),
        })
        scanner._signal_writer.write_signal = AsyncMock()

        timer = MagicMock()
        with patch("scanner.scanner.settings") as mock_settings:
            mock_settings.SCANNER_MAX_CONCURRENCY = 5
            mock_settings.FULL_SCAN_EVERY_N_CYCLES = 1
            mock_settings.HOT_POOL_TTL_SEC = 300
            mock_settings.CHAIN_SCAN_EVERY_N_CYCLES = 10
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            mock_settings.MIN_NET_PROFIT_PCT = Decimal("1")
            mock_settings.MIN_NET_PROFIT_USD = Decimal("0.1")
            mock_settings.MEXC_TAKER_FEE_BPS = 10
            mock_settings.ETH_MIN_NET_PROFIT_PCT = Decimal("2")
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            result = await scanner._execute_cycle(timer)

        assert result["quotes_success"] == 2
        assert result["signals_found"] == 2  # Both A and B
        assert result["signals_written"] == 2
        assert result["has_quote"] == 2
        assert result["net_positive"] == 2  # Both A and B are net positive
        assert scanner._signal_writer.write_signal.await_count == 2

    @pytest.mark.asyncio
    async def test_process_pool_direction_b_only_signal(self, scanner):
        """Only Direction B produces a signal (A not profitable, B is)."""
        pool = {
            "network": "BSC",
            "pool_address": "0xpool",
            "dex": "uniswap",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_version": "v2",
            "token_decimals": 18,
            "stablecoin_decimals": 18,
            "token_coin": "TOKEN",
        }
        scanner._load_pools_json = MagicMock(return_value=[pool])
        scanner._price_service.refresh_if_expired = AsyncMock(return_value=True)
        scanner._price_service.get_price = MagicMock(return_value=Decimal("1.50"))

        adapter = MagicMock()
        adapter.quote_exact_input = AsyncMock(return_value=Decimal("15000000000000000000"))
        # Mock reserves: 10000 stablecoin, 10000 token (enough liquidity).
        adapter.get_reserves = AsyncMock(return_value=(
            Decimal("10000000000000000000000"),  # reserve_in (10000 * 10^18)
            Decimal("10000000000000000000000"),  # reserve_out
        ))
        scanner._adapter_factory.get_adapter = MagicMock(return_value=adapter)

        # Direction A: positive but no signal.
        scanner._profit_calculator.calculate_direction_a = AsyncMock(return_value={
            "net_profit_usd": Decimal("0.01"),
            "signal": False,
            "net_profit_pct": Decimal("0.1"),
            "direction": "DEX_BUY_MEXC_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.01"),
            "gross_profit_pct": Decimal("0.1"),
            "fees": FeeBreakdown(
                dex_network_fee_usd=Decimal("0.05"),
                mexc_trading_fee_usd=Decimal("0.01"),
                total=Decimal("0.06"),
            ),
        })
        # Direction B: has signal.
        scanner._profit_calculator.calculate_direction_b = AsyncMock(return_value={
            "net_profit_usd": Decimal("0.30"),
            "signal": True,
            "net_profit_pct": Decimal("3.0"),
            "direction": "MEXC_BUY_DEX_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.30"),
            "gross_profit_pct": Decimal("3.0"),
            "fees": FeeBreakdown(
                dex_network_fee_usd=Decimal("0.05"),
                mexc_trading_fee_usd=Decimal("0.01"),
                total=Decimal("0.06"),
            ),
        })
        scanner._signal_writer.write_signal = AsyncMock()

        timer = MagicMock()
        with patch("scanner.scanner.settings") as mock_settings:
            mock_settings.SCANNER_MAX_CONCURRENCY = 5
            mock_settings.FULL_SCAN_EVERY_N_CYCLES = 1
            mock_settings.HOT_POOL_TTL_SEC = 300
            mock_settings.CHAIN_SCAN_EVERY_N_CYCLES = 10
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            mock_settings.MIN_NET_PROFIT_PCT = Decimal("1")
            mock_settings.MIN_NET_PROFIT_USD = Decimal("0.1")
            mock_settings.MEXC_TAKER_FEE_BPS = 10
            mock_settings.ETH_MIN_NET_PROFIT_PCT = Decimal("2")
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            result = await scanner._execute_cycle(timer)

        assert result["quotes_success"] == 2
        assert result["signals_found"] == 1  # Only Direction B
        assert result["signals_written"] == 1
        assert result["net_positive"] == 2  # Both are net positive
        assert scanner._signal_writer.write_signal.await_count == 1

    @pytest.mark.asyncio
    async def test_process_pool_profit_no_signal(self, scanner):
        """Positive profit but not enough for signal (net_profit_pct <= 0)."""
        pool = {
            "network": "BSC",
            "pool_address": "0xpool",
            "dex": "uniswap",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_version": "v2",
            "token_decimals": 18,
            "stablecoin_decimals": 18,
            "token_coin": "TOKEN",
        }
        scanner._load_pools_json = MagicMock(return_value=[pool])
        scanner._price_service.refresh_if_expired = AsyncMock(return_value=True)
        scanner._price_service.get_price = MagicMock(return_value=Decimal("1.50"))

        adapter = MagicMock()
        adapter.quote_exact_input = AsyncMock(return_value=Decimal("15000000000000000000"))
        # Mock reserves: 10000 stablecoin, 10000 token (enough liquidity).
        adapter.get_reserves = AsyncMock(return_value=(
            Decimal("10000000000000000000000"),  # reserve_in (10000 * 10^18)
            Decimal("10000000000000000000000"),  # reserve_out
        ))
        scanner._adapter_factory.get_adapter = MagicMock(return_value=adapter)

        scanner._profit_calculator.calculate_direction_a = AsyncMock(return_value={
            "net_profit_usd": Decimal("0.01"),
            "signal": False,
            "net_profit_pct": Decimal("0.1"),
            "direction": "DEX_BUY_MEXC_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.01"),
            "gross_profit_pct": Decimal("0.1"),
            "fees": FeeBreakdown(
                dex_network_fee_usd=Decimal("0.05"),
                mexc_trading_fee_usd=Decimal("0.01"),
                total=Decimal("0.06"),
            ),
        })
        # Direction B returns no signal so counters stay clean.
        scanner._profit_calculator.calculate_direction_b = AsyncMock(return_value={
            "net_profit_usd": Decimal("0.00"),
            "signal": False,
            "net_profit_pct": Decimal("0.0"),
            "direction": "MEXC_BUY_DEX_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.00"),
            "gross_profit_pct": Decimal("0.0"),
            "fees": FeeBreakdown(),
        })

        timer = MagicMock()
        with patch("scanner.scanner.settings") as mock_settings:
            mock_settings.SCANNER_MAX_CONCURRENCY = 5
            mock_settings.FULL_SCAN_EVERY_N_CYCLES = 1
            mock_settings.HOT_POOL_TTL_SEC = 300
            mock_settings.CHAIN_SCAN_EVERY_N_CYCLES = 10
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            mock_settings.MIN_NET_PROFIT_PCT = Decimal("1")
            mock_settings.MIN_NET_PROFIT_USD = Decimal("0.1")
            mock_settings.MEXC_TAKER_FEE_BPS = 10
            mock_settings.ETH_MIN_NET_PROFIT_PCT = Decimal("2")
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            result = await scanner._execute_cycle(timer)

        assert result["net_positive"] == 1  # Only Direction A is net positive
        assert result["signals_found"] == 0  # Neither direction has signal
        assert scanner._signal_writer.write_signal.await_count == 0

    @pytest.mark.asyncio
    async def test_process_pool_no_net_profit(self, scanner):
        """Negative net profit is counted but not as signal."""
        pool = {
            "network": "BSC",
            "pool_address": "0xpool",
            "dex": "uniswap",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_version": "v2",
            "token_decimals": 18,
            "stablecoin_decimals": 18,
            "token_coin": "TOKEN",
        }
        scanner._load_pools_json = MagicMock(return_value=[pool])
        scanner._price_service.refresh_if_expired = AsyncMock(return_value=True)
        scanner._price_service.get_price = MagicMock(return_value=Decimal("1.50"))

        adapter = MagicMock()
        adapter.quote_exact_input = AsyncMock(return_value=Decimal("5000000000000000000"))
        scanner._adapter_factory.get_adapter = MagicMock(return_value=adapter)

        scanner._profit_calculator.calculate_direction_a = AsyncMock(return_value={
            "net_profit_usd": Decimal("-0.10"),
            "signal": False,
            "net_profit_pct": Decimal("-1.0"),
            "direction": "DEX_BUY_MEXC_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.10"),
            "gross_profit_pct": Decimal("1.0"),
            "fees": FeeBreakdown(
                dex_network_fee_usd=Decimal("0.05"),
                mexc_trading_fee_usd=Decimal("0.01"),
                total=Decimal("0.06"),
            ),
        })
        # Direction B also returns no profit.
        scanner._profit_calculator.calculate_direction_b = AsyncMock(return_value={
            "net_profit_usd": Decimal("-0.01"),
            "signal": False,
            "net_profit_pct": Decimal("-0.1"),
            "direction": "MEXC_BUY_DEX_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.00"),
            "gross_profit_pct": Decimal("0.0"),
            "fees": FeeBreakdown(),
        })

        timer = MagicMock()
        with patch("scanner.scanner.settings") as mock_settings:
            mock_settings.SCANNER_MAX_CONCURRENCY = 5
            mock_settings.FULL_SCAN_EVERY_N_CYCLES = 1
            mock_settings.HOT_POOL_TTL_SEC = 300
            mock_settings.CHAIN_SCAN_EVERY_N_CYCLES = 10
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            mock_settings.MIN_NET_PROFIT_PCT = Decimal("1")
            mock_settings.MIN_NET_PROFIT_USD = Decimal("0.1")
            mock_settings.MEXC_TAKER_FEE_BPS = 10
            mock_settings.ETH_MIN_NET_PROFIT_PCT = Decimal("2")
            mock_settings.BASE_AMOUNT_USD = Decimal("10")
            result = await scanner._execute_cycle(timer)

        assert result["net_positive"] == 0
        assert result["signals_found"] == 0


class TestScannerWriteSignal:
    """Tests for Scanner._write_signal."""

    @pytest.fixture
    def scanner(self, tmp_path):
        s = Scanner.__new__(Scanner)
        s._pools_cache_path = tmp_path / "pools_cache.json"
        s._price_service = MagicMock()
        s._profit_calculator = MagicMock()
        s._adapter_factory = MagicMock()
        s._signal_writer = MagicMock()
        s._signal_writer.write_signal = AsyncMock()
        s._rpc_client_factory = MagicMock()
        s._token_security_checker = None
        s._pool_security_checker = None
        s._orderbook_service = None
        return s

    @pytest.mark.asyncio
    async def test_writes_signal_without_warnings(self, scanner):
        """Normal profit writes signal with empty warnings."""
        pool = {
            "network": "ETHEREUM",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_address": "0xpool",
            "dex": "uniswap",
        }
        fees = FeeBreakdown(
            dex_network_fee_usd=Decimal("0.05"),
            mexc_trading_fee_usd=Decimal("0.01"),
            total=Decimal("0.06"),
        )
        result = {
            "net_profit_pct": Decimal("5.0"),
            "direction": "DEX_BUY_MEXC_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.50"),
            "gross_profit_pct": Decimal("5.0"),
            "fees": fees,
            "net_profit_usd": Decimal("0.44"),
        }
        await scanner._write_signal(
            "ETHEREUM", pool, "TOKEN", "USDT", result, Decimal("1.50"), Decimal("15000000000000000000")
        )
        scanner._signal_writer.write_signal.assert_called_once()
        signal = scanner._signal_writer.write_signal.call_args[0][0]
        assert signal.warnings == []

    @pytest.mark.asyncio
    async def test_unrealistic_profit_filtered(self, scanner):
        """Signals with net profit above MAX_NET_PROFIT_PCT are rejected (D14)."""
        pool = {
            "network": "ETHEREUM",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_address": "0xpool",
            "dex": "uniswap",
        }
        fees = FeeBreakdown(
            dex_network_fee_usd=Decimal("0.05"),
            mexc_trading_fee_usd=Decimal("0.01"),
            total=Decimal("0.06"),
        )
        result = {
            "net_profit_pct": Decimal("100.0"),  # data-error territory, not arb
            "direction": "DEX_BUY_MEXC_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("10.0"),
            "gross_profit_pct": Decimal("100.0"),
            "fees": fees,
            "net_profit_usd": Decimal("9.50"),
        }
        written = await scanner._write_signal(
            "ETHEREUM", pool, "TOKEN", "USDT", result, Decimal("1.50"), Decimal("15000000000000000000")
        )
        assert written is False
        scanner._signal_writer.write_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_adds_security_warnings_from_checkers(self, scanner):
        """Security checker warnings are appended to signal.warnings."""
        scanner._token_security_checker = AsyncMock()
        scanner._token_security_checker.check = AsyncMock(return_value=["token_owner_detected"])
        scanner._pool_security_checker = AsyncMock()
        scanner._pool_security_checker.check = AsyncMock(return_value=["pool_owner_risk"])

        pool = {
            "network": "ETHEREUM",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_address": "0xpool",
            "dex": "uniswap",
        }
        fees = FeeBreakdown(
            dex_network_fee_usd=Decimal("0.05"),
            mexc_trading_fee_usd=Decimal("0.01"),
            total=Decimal("0.06"),
        )
        result = {
            "net_profit_pct": Decimal("5.0"),
            "direction": "DEX_BUY_MEXC_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.50"),
            "gross_profit_pct": Decimal("5.0"),
            "fees": fees,
            "net_profit_usd": Decimal("0.44"),
        }
        await scanner._write_signal(
            "ETHEREUM", pool, "TOKEN", "USDT", result, Decimal("1.50"), Decimal("15000000000000000000")
        )
        signal = scanner._signal_writer.write_signal.call_args[0][0]
        assert "token_owner_detected" in signal.warnings
        assert "pool_owner_risk" in signal.warnings
        assert len(signal.warnings) == 2

    @pytest.mark.asyncio
    async def test_handle_security_checker_crash(self, scanner):
        """When a security checker raises, an error warning is added."""
        scanner._token_security_checker = AsyncMock()
        scanner._token_security_checker.check = AsyncMock(side_effect=RuntimeError("RPC down"))
        scanner._pool_security_checker = AsyncMock()
        scanner._pool_security_checker.check = AsyncMock(return_value=[])

        pool = {
            "network": "ETHEREUM",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_address": "0xpool",
            "dex": "uniswap",
        }
        fees = FeeBreakdown(
            dex_network_fee_usd=Decimal("0.05"),
            mexc_trading_fee_usd=Decimal("0.01"),
            total=Decimal("0.06"),
        )
        result = {
            "net_profit_pct": Decimal("5.0"),
            "direction": "DEX_BUY_MEXC_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.50"),
            "gross_profit_pct": Decimal("5.0"),
            "fees": fees,
            "net_profit_usd": Decimal("0.44"),
        }
        await scanner._write_signal(
            "ETHEREUM", pool, "TOKEN", "USDT", result, Decimal("1.50"), Decimal("15000000000000000000")
        )
        signal = scanner._signal_writer.write_signal.call_args[0][0]
        assert "token_security_check_error" in signal.warnings

    @pytest.mark.asyncio
    async def test_signal_has_required_fields(self, scanner):
        """Signal contains all required fields."""
        pool = {
            "network": "ETHEREUM",
            "token_address": "0xtoken",
            "stablecoin_address": "0xstable",
            "pool_address": "0xpool",
            "dex": "uniswap",
        }
        fees = FeeBreakdown(
            dex_network_fee_usd=Decimal("0.05"),
            mexc_trading_fee_usd=Decimal("0.01"),
            total=Decimal("0.06"),
        )
        result = {
            "net_profit_pct": Decimal("5.0"),
            "direction": "DEX_BUY_MEXC_SELL",
            "base_amount_usd": Decimal("10"),
            "gross_profit_usd": Decimal("0.50"),
            "gross_profit_pct": Decimal("5.0"),
            "fees": fees,
            "net_profit_usd": Decimal("0.44"),
        }
        await scanner._write_signal(
            "ETHEREUM", pool, "TOKEN", "USDT", result, Decimal("1.50"), Decimal("15000000000000000000")
        )
        signal = scanner._signal_writer.write_signal.call_args[0][0]
        assert signal.network == "ETHEREUM"
        assert signal.token_coin == "TOKEN"
        assert signal.mexc_symbol == "TOKENUSDT"
        assert signal.dex == "uniswap"
        assert signal.full_cycle is True
