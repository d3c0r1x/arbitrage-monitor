"""
Performance and health metrics models.

Float is only used for time and duration — never for money.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class StageMetrics(BaseModel):
    """Metrics collected during a processing stage."""
    stage: str
    started_at: float
    finished_at: float
    duration_ms: float
    items_total: int = 0
    items_success: int = 0
    items_failed: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    source: str | None = None
    network: str | None = None
    details: dict[str, str | int | float] = Field(default_factory=dict)


class SourceHealthEntry(BaseModel):
    """Health state of a pool discovery source."""
    source: str
    healthy: bool = True
    consecutive_failures: int = 0
    last_error: str | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    updated_at: datetime
