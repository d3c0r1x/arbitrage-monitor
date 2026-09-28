"""Atomic refresh progress for dashboard (data/refresh_status.json)."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

STATUS_PATH = Path(__file__).resolve().parent.parent / "data" / "refresh_status.json"

_PHASE_WEIGHTS = {
    "idle": (0, 0),
    "starting": (0, 3),
    "assets": (3, 6),
    "candidates": (6, 10),
    "discovery": (10, 72),
    "version_detection": (72, 82),
    "db_write": (82, 90),
    "decimals": (90, 98),
    "cache_write": (98, 100),
    "done": (100, 100),
    "error": (0, 0),
}


def _overall_pct(phase: str, phase_pct: float) -> float:
    lo, hi = _PHASE_WEIGHTS.get(phase, (0, 100))
    p = max(0.0, min(100.0, float(phase_pct or 0)))
    return round(lo + (hi - lo) * p / 100.0, 1)


def write_refresh_status(
    *,
    phase: str,
    phase_pct: float = 0,
    done: int = 0,
    total: int = 0,
    pools: int = 0,
    errors: int = 0,
    message: str = "",
    running: bool | None = None,
) -> None:
    """Write progress snapshot; safe to call from async refresh task."""
    if running is None:
        running = phase not in ("done", "idle", "error")
    payload = {
        "phase": phase,
        "phase_pct": round(float(phase_pct), 1),
        "pct": _overall_pct(phase, phase_pct),
        "done": int(done),
        "total": int(total),
        "pools": int(pools),
        "errors": int(errors),
        "message": message or "",
        "running": bool(running),
        "updated_at": time.time(),
        "updated_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(STATUS_PATH.parent), prefix=".refresh_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, STATUS_PATH)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        pass


def read_refresh_status() -> dict:
    if not STATUS_PATH.exists():
        return {
            "phase": "idle",
            "phase_pct": 0,
            "pct": 0,
            "done": 0,
            "total": 0,
            "pools": 0,
            "errors": 0,
            "message": "",
            "running": False,
            "updated_at": 0,
            "updated_iso": "",
        }
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "phase": "idle",
            "phase_pct": 0,
            "pct": 0,
            "done": 0,
            "total": 0,
            "pools": 0,
            "errors": 0,
            "message": "unreadable",
            "running": False,
            "updated_at": 0,
            "updated_iso": "",
        }
