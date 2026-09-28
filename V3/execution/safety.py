"""
Execution safety gates.

Guards applied before any real-money execution step:
  1. Kill-switch file — create the file to instantly halt all executions.
  2. Daily loss limit — cumulative realized PnL per UTC day, persisted to disk.
  3. Confirmation ETA gate — skip tokens whose MEXC min_confirm is too high
     (transfer would take too long, price risk unacceptable).
"""

import json
import logging
import time
from decimal import Decimal
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

PNL_STATE_PATH = Path("data") / "state" / "execution_pnl.json"


class ExecutionGuard:
    """Pre-execution safety checks + realized PnL accounting."""

    def __init__(self):
        self._pnl_by_day: dict[str, float] = self._load_pnl()

    # ── Kill-switch ─────────────────────────────────────────────────────

    @staticmethod
    def kill_switch_active() -> bool:
        return Path(settings.KILL_SWITCH_FILE).exists()

    # ── Daily loss limit ────────────────────────────────────────────────

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def _load_pnl(self) -> dict[str, float]:
        try:
            if PNL_STATE_PATH.exists():
                return json.loads(PNL_STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def _save_pnl(self) -> None:
        try:
            PNL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            PNL_STATE_PATH.write_text(
                json.dumps(self._pnl_by_day, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.error("pnl_save_failed: %s", exc)

    def record_pnl(self, pnl_usd: Decimal | float) -> None:
        """Record realized PnL of a completed execution."""
        day = self._today()
        self._pnl_by_day[day] = self._pnl_by_day.get(day, 0.0) + float(pnl_usd)
        self._save_pnl()
        logger.info(
            "execution_pnl_recorded: today=%.2f USD (delta=%.2f)",
            self._pnl_by_day[day], float(pnl_usd),
        )

    def daily_loss_exceeded(self) -> bool:
        today_pnl = Decimal(str(self._pnl_by_day.get(self._today(), 0.0)))
        return today_pnl <= -settings.DAILY_LOSS_LIMIT_USD

    # ── Confirmation ETA gate ───────────────────────────────────────────

    @staticmethod
    def confirmations_ok(min_confirm) -> bool:
        """False if token transfer needs more confirmations than allowed."""
        if min_confirm is None:
            return True
        try:
            return int(min_confirm) <= settings.EXECUTION_MAX_CONFIRMATIONS
        except (TypeError, ValueError):
            return True

    # ── Combined gate ───────────────────────────────────────────────────

    def allow_execution(self, opp) -> tuple[bool, str]:
        """Full pre-execution check. Returns (allowed, reason)."""
        if self.kill_switch_active():
            return False, f"kill_switch:{settings.KILL_SWITCH_FILE}"
        if self.daily_loss_exceeded():
            return False, f"daily_loss_limit:{settings.DAILY_LOSS_LIMIT_USD}USD"
        min_confirm = getattr(opp, "token_min_confirm", None)
        if not self.confirmations_ok(min_confirm):
            return False, f"min_confirm_too_high:{min_confirm}"
        return True, "ok"
