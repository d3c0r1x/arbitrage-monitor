"""
Unit tests for performance metrics.

Tests:
- PerformanceLogger writes to JSONL file
- performance_timer context manager measures duration
- TimerContext counters (items, cache hits, failures)
- get_performance_logger singleton
- StageMetrics model serialization
"""

import json
import os
import tempfile
from unittest.mock import MagicMock


class TestStageMetrics:
    """Tests for StageMetrics model."""

    def test_creates_with_required_fields(self):
        """StageMetrics can be created with stage + timing fields."""
        from models.metrics_models import StageMetrics

        m = StageMetrics(
            stage="test_stage",
            started_at=1000.0,
            finished_at=1001.0,
            duration_ms=1000.0,
        )
        assert m.stage == "test_stage"
        assert m.duration_ms == 1000.0

    def test_defaults_are_zero(self):
        """Optional integer fields default to 0."""
        from models.metrics_models import StageMetrics

        m = StageMetrics(
            stage="test",
            started_at=0.0,
            finished_at=1.0,
            duration_ms=1000.0,
        )
        assert m.items_total == 0
        assert m.items_success == 0
        assert m.items_failed == 0
        assert m.cache_hits == 0
        assert m.cache_misses == 0

    def test_optional_fields(self):
        """Source and network can be None or set."""
        from models.metrics_models import StageMetrics

        m = StageMetrics(
            stage="test",
            started_at=0.0,
            finished_at=1.0,
            duration_ms=1000.0,
            source="dexscreener",
            network="BSC",
        )
        assert m.source == "dexscreener"
        assert m.network == "BSC"

    def test_details_can_hold_extra_data(self):
        """Details dict can store extra string/int/float values."""
        from models.metrics_models import StageMetrics

        m = StageMetrics(
            stage="test",
            started_at=0.0,
            finished_at=1.0,
            duration_ms=1000.0,
            details={"pools_count": 500, "dex": "pancakeswap"},
        )
        assert m.details["pools_count"] == 500
        assert m.details["dex"] == "pancakeswap"


class TestPerformanceLogger:
    """Tests for PerformanceLogger."""

    def test_writes_jsonl(self):
        """write_metrics appends a JSON line to the log file."""
        from metrics.performance import PerformanceLogger
        from models.metrics_models import StageMetrics

        with tempfile.NamedTemporaryFile(mode="r+", suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            logger = PerformanceLogger(log_path=log_path)
            metrics = StageMetrics(
                stage="test_write",
                started_at=1000.0,
                finished_at=1001.0,
                duration_ms=1000.0,
            )
            logger.write_metrics(metrics)

            with open(log_path) as f:
                lines = f.readlines()
            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["stage"] == "test_write"
            assert record["duration_ms"] == 1000.0
        finally:
            os.unlink(log_path)

    def test_writes_multiple_records(self):
        """Multiple write_metrics calls append to the file."""
        from metrics.performance import PerformanceLogger
        from models.metrics_models import StageMetrics

        with tempfile.NamedTemporaryFile(mode="r+", suffix=".jsonl", delete=False) as f:
            log_path = f.name

        try:
            logger = PerformanceLogger(log_path=log_path)
            for i in range(3):
                m = StageMetrics(
                    stage=f"test_{i}",
                    started_at=float(i),
                    finished_at=float(i + 1),
                    duration_ms=1000.0 + i,
                )
                logger.write_metrics(m)

            with open(log_path) as f:
                lines = f.readlines()
            assert len(lines) == 3
            assert json.loads(lines[2])["stage"] == "test_2"
        finally:
            os.unlink(log_path)

    def test_default_log_path(self):
        """Default log path is data/performance.jsonl."""
        from metrics.performance import PerformanceLogger

        logger = PerformanceLogger()
        assert logger._log_path.name == "performance.jsonl"
        assert "data" in str(logger._log_path)


class TestPerformanceTimer:
    """Tests for performance_timer context manager."""

    def test_timer_writes_metrics(self):
        """performance_timer writes StageMetrics after context exit."""
        from metrics.performance import performance_timer

        mock_logger = MagicMock()
        import asyncio

        async def run():
            async with performance_timer("test_timer", logger_override=mock_logger) as ctx:
                ctx.set_items_total(42)

        asyncio.run(run())

        # Verify write_metrics was called with a StageMetrics
        mock_logger.write_metrics.assert_called_once()
        args, _ = mock_logger.write_metrics.call_args
        metrics = args[0]
        assert metrics.stage == "test_timer"
        assert metrics.items_total == 42
        assert metrics.duration_ms > 0

    def test_timer_records_source_and_network(self):
        """Source and network are passed through to metrics."""
        from metrics.performance import performance_timer

        mock_logger = MagicMock()
        import asyncio

        async def run():
            async with performance_timer(
                "test_source",
                source="dexscreener",
                network="BSC",
                logger_override=mock_logger,
            ) as ctx:
                ctx.set_items_total(10)

        asyncio.run(run())

        mock_logger.write_metrics.assert_called_once()
        args, _ = mock_logger.write_metrics.call_args
        metrics = args[0]
        assert metrics.source == "dexscreener"
        assert metrics.network == "BSC"


class TestTimerContext:
    """Tests for TimerContext helpers."""

    def test_set_items_total(self):
        """set_items_total updates the counter."""
        from metrics.performance import TimerContext

        ctx = TimerContext(stage="test")
        assert ctx.items_total == 0
        ctx.set_items_total(100)
        assert ctx.items_total == 100

    def test_add_success(self):
        """add_success increments by 1 (or custom count)."""
        from metrics.performance import TimerContext

        ctx = TimerContext(stage="test")
        ctx.add_success()
        assert ctx.items_success == 1
        ctx.add_success(5)
        assert ctx.items_success == 6

    def test_add_failure(self):
        """add_failure increments by 1 (or custom count)."""
        from metrics.performance import TimerContext

        ctx = TimerContext(stage="test")
        ctx.add_failure()
        assert ctx.items_failed == 1
        ctx.add_failure(3)
        assert ctx.items_failed == 4

    def test_add_cache_hit(self):
        """add_cache_hit increments by 1 (or custom count)."""
        from metrics.performance import TimerContext

        ctx = TimerContext(stage="test")
        ctx.add_cache_hit()
        assert ctx.cache_hits == 1
        ctx.add_cache_hit(10)
        assert ctx.cache_hits == 11

    def test_add_cache_miss(self):
        """add_cache_miss increments by 1 (or custom count)."""
        from metrics.performance import TimerContext

        ctx = TimerContext(stage="test")
        ctx.add_cache_miss()
        assert ctx.cache_misses == 1
        ctx.add_cache_miss(2)
        assert ctx.cache_misses == 3

    def test_details_dict(self):
        """Details dict can be written to."""
        from metrics.performance import TimerContext

        ctx = TimerContext(stage="test")
        assert ctx.details == {}
        ctx.details["pools_found"] = 150
        assert ctx.details["pools_found"] == 150


class TestGetPerformanceLogger:
    """Tests for get_performance_logger singleton."""

    def test_singleton_returns_same_instance(self):
        """Multiple calls return the same logger instance."""
        # Reset singleton
        import metrics.performance as pm
        from metrics.performance import get_performance_logger
        pm._performance_logger = None

        logger1 = get_performance_logger()
        logger2 = get_performance_logger()
        assert logger1 is logger2

    def test_logger_is_performance_logger_instance(self):
        """get_performance_logger returns PerformanceLogger."""
        import metrics.performance as pm
        from metrics.performance import (
            PerformanceLogger,
            get_performance_logger,
        )
        pm._performance_logger = None

        logger = get_performance_logger()
        assert isinstance(logger, PerformanceLogger)
