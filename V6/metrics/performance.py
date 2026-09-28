"""
Performance metrics collection.

Provides a context manager (performance_timer) for measuring stage durations.
"""

import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from models.metrics_models import StageMetrics

logger = logging.getLogger(__name__)


class PerformanceLogger:
    """Logs stage metrics to JSONL file."""

    def __init__(self, log_path: str = "data/performance.jsonl"):
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def write_metrics(self, metrics: StageMetrics) -> None:
        """Write a stage metrics record to the JSONL file."""
        record = metrics.model_dump()
        line = json.dumps(record, default=str)

        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        logger.debug(
            "stage_metric_written: %s duration_ms=%.0f",
            metrics.stage,
            metrics.duration_ms,
        )


_performance_logger: PerformanceLogger | None = None


def get_performance_logger() -> PerformanceLogger:
    global _performance_logger
    if _performance_logger is None:
        _performance_logger = PerformanceLogger()
    return _performance_logger


@asynccontextmanager
async def performance_timer(
    stage: str,
    source: str | None = None,
    network: str | None = None,
    logger_override: PerformanceLogger | None = None,
) -> AsyncIterator["TimerContext"]:
    log = logger_override or get_performance_logger()
    ctx = TimerContext(stage=stage)
    started_at = time.time()

    try:
        yield ctx
    finally:
        finished_at = time.time()
        duration_ms = (finished_at - started_at) * 1000.0

        metrics = StageMetrics(
            stage=stage,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            items_total=ctx.items_total,
            items_success=ctx.items_success,
            items_failed=ctx.items_failed,
            cache_hits=ctx.cache_hits,
            cache_misses=ctx.cache_misses,
            source=source,
            network=network,
            details=ctx.details,
        )

        log.write_metrics(metrics)


class TimerContext:
    """Context object updated during performance measurement."""

    def __init__(self, stage: str):
        self.stage = stage
        self.items_total: int = 0
        self.items_success: int = 0
        self.items_failed: int = 0
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self.details: dict[str, str | int | float] = {}

    def set_items_total(self, value: int) -> None:
        self.items_total = value

    def add_success(self, count: int = 1) -> None:
        self.items_success += count

    def add_failure(self, count: int = 1) -> None:
        self.items_failed += count

    def add_cache_hit(self, count: int = 1) -> None:
        self.cache_hits += count

    def add_cache_miss(self, count: int = 1) -> None:
        self.cache_misses += count
