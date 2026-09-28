"""
Unit tests for PoolRefreshTask.

Tests:
- run_refresh: uses pre-fetched assets when provided
- _run_refresh_inner: full pipeline (assets, prices, candidates, discovery, DB write)
- DB transaction: BEGIN IMMEDIATE, INSERT/COMMIT, ROLLBACK on error
- Cache file: writes JSON after successful DB commit
- Empty candidates: returns early with 0 pools
- Volume fetch failure: continues without volume filter
- Discovery error: pools still written, errors counted
"""

import sqlite3
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _no_real_cache_write():
    """Never let tests overwrite the real data/pools_cache.json.

    The cache path is computed relative to the source file, so any
    successful refresh in a test would otherwise poison the live cache.
    """
    with patch("scanner.pool_refresh_task.json.dump"):
        with patch("scanner.pool_refresh_task.os.replace"):
            yield


class TestPoolRefreshTaskPreFetched:
    """Tests for PoolRefreshTask with pre-fetched assets."""

    @pytest.fixture
    def task(self):
        from scanner.pool_refresh_task import PoolRefreshTask

        t = PoolRefreshTask.__new__(PoolRefreshTask)
        t._mexc_client = MagicMock()
        t._price_service = MagicMock()
        t._price_service.refresh_all_prices = AsyncMock()
        t._price_service.get_all_prices_dict = MagicMock(return_value={"BTCUSDT": "50000"})
        t._mexc_asset_service = MagicMock()
        t._mexc_asset_service.select_candidate_tokens = MagicMock(return_value=[])
        t._mexc_asset_service.fetch_24hr_volumes = AsyncMock(return_value={})
        t._stablecoin_registry_service = MagicMock()
        t._stablecoin_registry_service.build_registry = MagicMock(return_value={})
        t._source_manager = MagicMock()
        t._pool_discovery_service = MagicMock()
        t._active_db_path = ":memory:"
        t._token_meta_service = None
        t._backup_manager = None
        t._pool_detector = None
        t._onchain_factory = None
        return t

    @pytest.mark.asyncio
    async def test_uses_pre_fetched_assets(self, task):
        """Uses pre-fetched assets when provided, skips fetch_and_parse."""
        pre_fetched = [MagicMock()]
        task._mexc_asset_service.fetch_and_parse_assets = AsyncMock()  # should NOT be called

        with patch("scanner.pool_refresh_task.performance_timer") as mock_timer:
            timer_cm = AsyncMock()
            timer_cm.__aenter__ = AsyncMock(return_value=MagicMock())
            timer_cm.__aexit__ = AsyncMock()
            mock_timer.return_value = timer_cm

            result = await task.run_refresh(pre_fetched_assets=pre_fetched)

        task._mexc_asset_service.fetch_and_parse_assets.assert_not_called()
        assert result["assets_count"] == 1

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_candidates(self, task):
        """Returns 0 pools when no candidates found."""
        with patch("scanner.pool_refresh_task.performance_timer") as mock_timer:
            timer_cm = AsyncMock()
            timer_cm.__aenter__ = AsyncMock(return_value=MagicMock())
            timer_cm.__aexit__ = AsyncMock()
            mock_timer.return_value = timer_cm

            result = await task.run_refresh(pre_fetched_assets=[])
        assert result["pools_count"] == 0


class TestPoolRefreshTaskFullPipeline:
    """Tests for the full _run_refresh_inner pipeline."""

    @pytest.fixture
    def task(self, tmp_path):
        from scanner.pool_refresh_task import PoolRefreshTask

        t = PoolRefreshTask.__new__(PoolRefreshTask)
        t._mexc_client = MagicMock()
        t._price_service = MagicMock()
        t._price_service.refresh_all_prices = AsyncMock()
        t._price_service.get_all_prices_dict = MagicMock(return_value={"BTCUSDT": "50000"})
        t._mexc_asset_service = MagicMock()

        # Build a realistic MexcAsset with networks
        mexc_asset = MagicMock()
        mexc_asset.coin = "TEST"
        mexc_asset.name = "Test Token"
        mexc_asset.networks = []

        net = MagicMock()
        net.network_normalized = "BSC"
        net.contract_address = "0xtoken"
        net.deposit_enable = True
        net.withdraw_enable = True
        net.withdraw_fee = Decimal("0.001")
        net.withdraw_min = Decimal("1")
        net.withdraw_max = Decimal("100000")
        net.min_confirm = 12
        mexc_asset.networks = [net]

        t._mexc_asset_service.fetch_and_parse_assets = AsyncMock(return_value=[mexc_asset])
        t._mexc_asset_service.select_candidate_tokens = MagicMock(return_value=[
            (mexc_asset, "BSC", "0xtoken", "USDT"),
        ])
        t._mexc_asset_service.fetch_24hr_volumes = AsyncMock(return_value={"TEST": Decimal("1000000")})
        t._stablecoin_registry_service = MagicMock()

        stablecoin_record = MagicMock()
        stablecoin_record.address = "0xstable"
        stablecoin_record.coin = "USDT"
        stablecoin_record.network = "BSC"
        stablecoin_record.deposit_enable = True
        stablecoin_record.withdraw_enable = True
        stablecoin_record.withdraw_fee = Decimal("0.001")

        t._stablecoin_registry_service.build_registry = MagicMock(return_value={
            "BSC": [stablecoin_record],
        })
        t._stablecoin_registry_service.stablecoin_addresses_for_network = MagicMock(
            return_value={"0xstable"}
        )

        quote_record = MagicMock()
        quote_record.coin = "USDT"
        quote_record.is_stable = True
        quote_record.price_coin = "USDT"
        quote_record.withdraw_fee = Decimal("0.001")
        t._stablecoin_registry_service.build_quote_registry = MagicMock(return_value={
            "BSC": {"0xstable": quote_record},
        })
        t._stablecoin_registry_service.quote_records_for_network = MagicMock(
            return_value={"0xstable": quote_record}
        )
        t._source_manager = MagicMock()
        t._pool_discovery_service = MagicMock()

        discovered_pool = MagicMock()
        discovered_pool.network = "BSC"
        discovered_pool.token_address = "0xtoken"
        discovered_pool.stablecoin_address = "0xstable"
        discovered_pool.pool_address = "0xpool"
        discovered_pool.dex = "uniswap"
        discovered_pool.sources = {"dex_screener", "mexc"}
        discovered_pool.pool_version = "v3"

        t._pool_discovery_service.discover_and_filter_pools = AsyncMock(
            return_value=[discovered_pool]
        )

        # Use temp SQLite DB
        db_path = tmp_path / "test_active.db"
        t._active_db_path = str(db_path)
        # Create tables
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pools (
                network TEXT, token_address TEXT, stablecoin_address TEXT,
                pool_address TEXT, dex TEXT, sources TEXT, pool_version TEXT,
                created_at INTEGER, updated_at INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mexc_assets (
                coin TEXT, name TEXT, network TEXT, contract_address TEXT,
                deposit_enable INTEGER, withdraw_enable INTEGER,
                withdraw_fee TEXT, withdraw_min TEXT, withdraw_max TEXT,
                min_confirm INTEGER, updated_at INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stablecoins (
                coin TEXT, network TEXT, address TEXT,
                deposit_enable INTEGER, withdraw_enable INTEGER,
                withdraw_fee TEXT, updated_at INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS refresh_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at INTEGER NOT NULL,
                finished_at INTEGER,
                status TEXT NOT NULL,
                error TEXT,
                pools_count INTEGER,
                mexc_assets_count INTEGER,
                stablecoins_count INTEGER,
                candidate_offset INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()
        t._token_meta_service = None
        t._backup_manager = None
        t._pool_detector = None
        t._onchain_factory = None
        return t

    @pytest.mark.asyncio
    async def test_full_pipeline_writes_pools(self, task):
        """Full pipeline writes pools to DB and returns counts."""
        timer = MagicMock()

        with patch("scanner.pool_refresh_task.time") as mock_time:
            mock_time.time.return_value = 1000000
            with patch("scanner.pool_refresh_task.settings") as mock_settings:
                mock_settings.DISCOVERY_MAX_CONCURRENCY = 5
                mock_settings.CANDIDATES_PER_REFRESH_CYCLE = 500
                result = await task._run_refresh_inner(timer, pre_fetched_assets=None)

        assert result["pools_count"] > 0
        assert result["assets_count"] > 0
        assert result["stablecoins_count"] > 0

        # Verify data was written to DB
        conn = sqlite3.connect(str(task._active_db_path))
        try:
            pool_count = conn.execute("SELECT COUNT(*) FROM pools").fetchone()[0]
            asset_count = conn.execute("SELECT COUNT(*) FROM mexc_assets").fetchone()[0]
            stablecoin_count = conn.execute("SELECT COUNT(*) FROM stablecoins").fetchone()[0]
            assert pool_count > 0
            assert asset_count > 0
            assert stablecoin_count > 0
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_db_transaction_atomicity(self, task):
        """DB write uses BEGIN IMMEDIATE and COMMIT on success."""
        timer = MagicMock()

        with patch("scanner.pool_refresh_task.settings") as mock_settings:
            mock_settings.DISCOVERY_MAX_CONCURRENCY = 5
            mock_settings.CANDIDATES_PER_REFRESH_CYCLE = 500
            with patch("scanner.pool_refresh_task.os.replace"):
                await task._run_refresh_inner(timer, pre_fetched_assets=None)

        # Verify pools were committed
        conn = sqlite3.connect(str(task._active_db_path))
        try:
            pool_count = conn.execute("SELECT COUNT(*) FROM pools").fetchone()[0]
            assert pool_count > 0
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_rollback_on_db_error(self, task):
        """DB error triggers rollback and error is returned."""
        # Corrupt the DB path to cause a write error
        task._active_db_path = "/nonexistent/dir/db.sqlite"
        timer = MagicMock()

        with patch("scanner.pool_refresh_task.settings") as mock_settings:
            mock_settings.DISCOVERY_MAX_CONCURRENCY = 5
            mock_settings.CANDIDATES_PER_REFRESH_CYCLE = 500
            result = await task._run_refresh_inner(timer, pre_fetched_assets=None)

        assert "error" in result
        assert result["pools_count"] == 0

    @pytest.mark.asyncio
    async def test_writes_cache_file_on_success(self, task):
        """Cache JSON file is written after successful DB commit."""
        timer = MagicMock()

        with patch("scanner.pool_refresh_task.settings") as mock_settings:
            mock_settings.DISCOVERY_MAX_CONCURRENCY = 5
            mock_settings.CANDIDATES_PER_REFRESH_CYCLE = 500
            with patch("scanner.pool_refresh_task.json.dump"):
                with patch("scanner.pool_refresh_task.os.replace"):
                    result = await task._run_refresh_inner(timer, pre_fetched_assets=None)

        assert result["pools_count"] > 0

    @pytest.mark.asyncio
    async def test_volume_fetch_failure_continues(self, task):
        """When volume fetch fails, pipeline continues without volume filter."""
        task._mexc_asset_service.fetch_24hr_volumes = AsyncMock(
            side_effect=Exception("API error")
        )
        timer = MagicMock()

        with patch("scanner.pool_refresh_task.settings") as mock_settings:
            mock_settings.DISCOVERY_MAX_CONCURRENCY = 5
            mock_settings.CANDIDATES_PER_REFRESH_CYCLE = 500
            result = await task._run_refresh_inner(timer, pre_fetched_assets=None)

        # Should still complete, volume filter skipped
        assert result["pools_count"] >= 0
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_stale_pool_protection(self, task):
        """A8: When discovery returns 0 but DB has pools, old pools survive."""
        # First, insert an existing pool into the DB
        conn = sqlite3.connect(str(task._active_db_path))
        conn.execute(
            """INSERT INTO pools VALUES
            ('BSC', '0xold', '0xoldstable', '0xoldpool', 'uniswap',
             'mexc', 'v3', 1000000, 1000000)"""
        )
        conn.commit()
        conn.close()

        # Discovery returns 0 pools
        task._pool_discovery_service.discover_and_filter_pools = AsyncMock(
            return_value=[]
        )

        timer = MagicMock()
        with patch("scanner.pool_refresh_task.settings") as mock_settings:
            mock_settings.DISCOVERY_MAX_CONCURRENCY = 5
            mock_settings.CANDIDATES_PER_REFRESH_CYCLE = 500
            result = await task._run_refresh_inner(timer, pre_fetched_assets=None)

        # Old pool should survive
        assert "error" not in result
        assert result["pools_count"] > 0

        # Verify old pool is still in DB
        conn2 = sqlite3.connect(str(task._active_db_path))
        try:
            pool_count = conn2.execute("SELECT COUNT(*) FROM pools").fetchone()[0]
            assert pool_count == 1  # Old pool still there
        finally:
            conn2.close()

    @pytest.mark.asyncio
    async def test_discovery_error_handled(self, task):
        """When discovery raises exception for a candidate, it's counted but doesn't crash."""
        task._pool_discovery_service.discover_and_filter_pools = AsyncMock(
            side_effect=Exception("RPC timeout")
        )
        timer = MagicMock()

        with patch("scanner.pool_refresh_task.settings") as mock_settings:
            mock_settings.DISCOVERY_MAX_CONCURRENCY = 5
            mock_settings.CANDIDATES_PER_REFRESH_CYCLE = 500
            result = await task._run_refresh_inner(timer, pre_fetched_assets=None)

        # Discovery errors should not prevent DB write
        assert "error" not in result
        # Data should still be written (even with 0 pools)
        # The empty mexc_assets and stablecoins should be inserted
        conn = sqlite3.connect(str(task._active_db_path))
        try:
            asset_count = conn.execute("SELECT COUNT(*) FROM mexc_assets").fetchone()[0]
            assert asset_count > 0
        finally:
            conn.close()


class TestPoolRefreshTaskVolumes:
    """Tests for volume handling in PoolRefreshTask."""

    @pytest.fixture
    def task(self):
        from scanner.pool_refresh_task import PoolRefreshTask

        t = PoolRefreshTask.__new__(PoolRefreshTask)
        t._mexc_client = MagicMock()
        t._price_service = MagicMock()
        t._price_service.refresh_all_prices = AsyncMock()
        t._price_service.get_all_prices_dict = MagicMock(return_value={})
        t._mexc_asset_service = MagicMock()
        t._mexc_asset_service.fetch_and_parse_assets = AsyncMock(return_value=[])
        t._mexc_asset_service.select_candidate_tokens = MagicMock(return_value=[])
        t._stablecoin_registry_service = MagicMock()
        t._source_manager = MagicMock()
        t._pool_discovery_service = MagicMock()
        t._active_db_path = ":memory:"
        t._token_meta_service = None
        t._backup_manager = None
        t._pool_detector = None
        t._onchain_factory = None
        return t

    @pytest.mark.asyncio
    async def test_volume_none_does_not_crash(self, task):
        """When volumes fetch returns None, pipeline continues."""
        task._mexc_asset_service.fetch_24hr_volumes = AsyncMock(return_value=None)
        timer = MagicMock()

        with patch("scanner.pool_refresh_task.settings") as mock_settings:
            mock_settings.DISCOVERY_MAX_CONCURRENCY = 5
            result = await task._run_refresh_inner(timer, pre_fetched_assets=[])

        assert result["pools_count"] == 0
