"""Runtime scan mode: mexc | dex_dex.

Persisted in data/scan_mode.json so the dashboard can toggle without restart.
Default comes from SCAN_MODE env / settings.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODE_PATH = _PROJECT_ROOT / "data" / "scan_mode.json"
VALID = frozenset({"mexc", "dex_dex"})

_lock = threading.Lock()
_cached: str | None = None


def _default_mode() -> str:
    raw = str(getattr(settings, "SCAN_MODE", "mexc") or "mexc").strip().lower()
    return raw if raw in VALID else "mexc"


def get_scan_mode() -> str:
    """Return current mode (file override → env default)."""
    global _cached
    with _lock:
        if _cached is not None:
            return _cached
        try:
            if MODE_PATH.exists():
                data = json.loads(MODE_PATH.read_text(encoding="utf-8"))
                mode = str(data.get("mode") or "").strip().lower()
                if mode in VALID:
                    _cached = mode
                    return mode
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.debug("scan_mode_read_failed: %s", exc)
        _cached = _default_mode()
        return _cached


def set_scan_mode(mode: str) -> str:
    """Persist and cache a new mode. Returns normalized mode."""
    global _cached
    mode = str(mode or "").strip().lower()
    if mode not in VALID:
        raise ValueError(f"invalid_scan_mode:{mode}")
    payload = {"mode": mode}
    with _lock:
        MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MODE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(MODE_PATH)
        _cached = mode
    logger.info("scan_mode_set: %s", mode)
    return mode


def refresh_scan_mode() -> str:
    """Drop cache and re-read from disk (call each scanner tick)."""
    global _cached
    with _lock:
        _cached = None
    return get_scan_mode()
