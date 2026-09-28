"""
MEXC Execution Module — the CEX leg of the arbitrage bundle.

Direction B (MEXC_BUY_DEX_SELL) entry leg:
  1. check USDT balance     2. market BUY token
  3. poll order FILLED      4. withdraw token to DEX wallet
  5. poll withdraw status

Direction A (DEX_BUY_MEXC_SELL) exit leg:
  1. resolve deposit address 2. poll deposit credited
  3. market SELL token

Safety:
  - EXECUTION_DRY_RUN=True (default): every step is logged, no orders or
    withdrawals are ever sent. Balance/config reads still run for realism.
  - Strict per-opportunity state machine with idempotency: a leg for the
    same opportunity key never runs twice concurrently.
  - Every step appended to data/executions.jsonl for audit.
"""

import asyncio
import json
import logging
import time
from decimal import Decimal
from pathlib import Path

from config.networks import normalize_mexc_network
from config.settings import settings

logger = logging.getLogger(__name__)

EXECUTIONS_LOG_PATH = Path("data") / "executions.jsonl"

# Poll intervals / timeouts (seconds).
ORDER_POLL_INTERVAL = 2
ORDER_POLL_TIMEOUT = 60
WITHDRAW_POLL_INTERVAL = 15
WITHDRAW_POLL_TIMEOUT = 1800
DEPOSIT_POLL_INTERVAL = 15
DEPOSIT_POLL_TIMEOUT = 1800

# MEXC withdraw record statuses: 7 = success (per API docs).
WITHDRAW_STATUS_SUCCESS = {7, "7", "SUCCESS"}
# MEXC deposit record statuses: 5 = success/credited.
DEPOSIT_STATUS_SUCCESS = {5, "5", "SUCCESS"}


class MexcExecutor:
    """Executes the MEXC (CEX) leg of an arbitrage bundle."""

    def __init__(self, mexc_client):
        self._client = mexc_client
        self._dry_run = settings.EXECUTION_DRY_RUN
        self._active_legs: set[str] = set()
        self._capital_config_cache: list[dict] | None = None
        self._capital_config_ts: float = 0.0

    # ── Public API ──────────────────────────────────────────────────────

    async def run_entry_leg(self, opp, wallet_address: str) -> dict:
        """Direction B entry: MEXC buy → withdraw token to DEX wallet.

        Returns dict with status: done | dry_run_ok | failed | skipped.
        """
        if opp.key in self._active_legs:
            return {"status": "skipped", "reason": "leg_already_running"}
        self._active_legs.add(opp.key)
        try:
            return await self._entry_leg(opp, wallet_address)
        finally:
            self._active_legs.discard(opp.key)

    async def run_exit_leg(self, opp, expected_amount: Decimal) -> dict:
        """Direction A exit: await token deposit on MEXC → market sell."""
        key = f"{opp.key}:exit"
        if key in self._active_legs:
            return {"status": "skipped", "reason": "leg_already_running"}
        self._active_legs.add(key)
        try:
            return await self._exit_leg(opp, expected_amount)
        finally:
            self._active_legs.discard(key)

    async def check_balance(self, asset: str, required: Decimal) -> bool:
        """True if free MEXC balance of `asset` covers `required`."""
        try:
            balances = await self._client.get_account_balances()
            free = balances.get(asset.upper(), Decimal("0"))
            ok = free >= required
            if not ok:
                logger.info(
                    "mexc_balance_insufficient: %s free=%s required=%s",
                    asset, free, required,
                )
            return ok
        except Exception as exc:
            logger.error("mexc_balance_check_failed: %s", exc)
            return False

    # ── Entry leg (direction B) ─────────────────────────────────────────

    async def _entry_leg(self, opp, wallet_address: str) -> dict:
        steps: list[str] = []
        symbol = f"{opp.token_coin}{getattr(opp, 'mexc_quote_asset', 'USDT') or 'USDT'}"
        amount_usd = Decimal(opp.base_amount_usd)

        # Step 1: balance gate.
        self._log_step(opp, steps, "entry:check_balance", f"need {amount_usd} USDT")
        if not await self.check_balance("USDT", amount_usd):
            return self._finish(opp, steps, "failed", "insufficient_usdt")

        # Step 2: network/withdraw config gate.
        net_info = await self._resolve_network_info(opp.token_coin, opp.network)
        if net_info is None:
            return self._finish(opp, steps, "failed", "no_mexc_network_for_token")
        if not net_info.get("withdrawEnable", False):
            return self._finish(opp, steps, "failed", "withdraw_disabled")
        self._log_step(
            opp, steps, "entry:network_resolved",
            f"raw={net_info['network']} fee={net_info.get('withdrawFee')}",
        )

        if self._dry_run:
            self._log_step(opp, steps, "entry:DRY_RUN_buy", f"market BUY {symbol} quoteQty={amount_usd}")
            self._log_step(opp, steps, "entry:DRY_RUN_withdraw", f"{opp.token_coin} → {wallet_address[:10]}… via {net_info['network']}")
            return self._finish(opp, steps, "dry_run_ok", "simulated")

        # Step 3: market buy.
        self._log_step(opp, steps, "entry:buy", f"market BUY {symbol} quoteQty={amount_usd}")
        order = await self._client.place_order(
            symbol=symbol, side="BUY", order_type="MARKET",
            quoteOrderQty=str(amount_usd),
        )
        order_id = str(order.get("orderId", ""))
        if not order_id:
            return self._finish(opp, steps, "failed", f"order_rejected:{order}")

        # Step 4: poll order filled.
        filled_qty = await self._poll_order_filled(symbol, order_id)
        if filled_qty is None:
            return self._finish(opp, steps, "failed", "order_not_filled")
        self._log_step(opp, steps, "entry:filled", f"qty={filled_qty}")

        # Step 5: withdraw to DEX wallet.
        withdraw_min = Decimal(str(net_info.get("withdrawMin") or "0"))
        if filled_qty < withdraw_min:
            return self._finish(opp, steps, "failed", f"below_withdraw_min:{withdraw_min}")
        wd = await self._client.withdraw(
            coin=opp.token_coin, address=wallet_address,
            amount=str(filled_qty), network=net_info["network"],
        )
        wd_id = str(wd.get("id", ""))
        self._log_step(opp, steps, "entry:withdraw_requested", f"id={wd_id}")

        # Step 6: poll withdraw completion.
        ok = await self._poll_withdraw_done(opp.token_coin, wd_id)
        if not ok:
            return self._finish(opp, steps, "failed", "withdraw_timeout")
        return self._finish(opp, steps, "done", f"withdrawn qty={filled_qty}")

    # ── Exit leg (direction A) ──────────────────────────────────────────

    async def _exit_leg(self, opp, expected_amount: Decimal) -> dict:
        steps: list[str] = []
        symbol = f"{opp.token_coin}{getattr(opp, 'mexc_quote_asset', 'USDT') or 'USDT'}"

        net_info = await self._resolve_network_info(opp.token_coin, opp.network)
        if net_info is None:
            return self._finish(opp, steps, "failed", "no_mexc_network_for_token")
        if not net_info.get("depositEnable", False):
            return self._finish(opp, steps, "failed", "deposit_disabled")

        if self._dry_run:
            self._log_step(opp, steps, "exit:DRY_RUN_await_deposit", f"{expected_amount} {opp.token_coin} via {net_info['network']}")
            self._log_step(opp, steps, "exit:DRY_RUN_sell", f"market SELL {symbol} qty={expected_amount}")
            return self._finish(opp, steps, "dry_run_ok", "simulated")

        # Step 1: deposit address (informational — DEX leg must send here).
        try:
            addrs = await self._client.get_deposit_address(opp.token_coin, net_info["network"])
            addr = addrs[0].get("address") if addrs else None
            self._log_step(opp, steps, "exit:deposit_address", str(addr))
        except Exception as exc:
            return self._finish(opp, steps, "failed", f"deposit_address_error:{exc}")

        # Step 2: poll deposit credited.
        credited = await self._poll_deposit_credited(opp.token_coin, expected_amount)
        if credited is None:
            return self._finish(opp, steps, "failed", "deposit_timeout")
        self._log_step(opp, steps, "exit:deposit_credited", f"qty={credited}")

        # Step 3: market sell.
        order = await self._client.place_order(
            symbol=symbol, side="SELL", order_type="MARKET",
            quantity=str(credited),
        )
        order_id = str(order.get("orderId", ""))
        if not order_id:
            return self._finish(opp, steps, "failed", f"sell_rejected:{order}")
        filled = await self._poll_order_filled(symbol, order_id)
        if filled is None:
            return self._finish(opp, steps, "failed", "sell_not_filled")
        return self._finish(opp, steps, "done", f"sold qty={filled}")

    # ── Polling helpers ─────────────────────────────────────────────────

    async def _poll_order_filled(self, symbol: str, order_id: str) -> Decimal | None:
        deadline = time.time() + ORDER_POLL_TIMEOUT
        while time.time() < deadline:
            try:
                order = await self._client.get_order(symbol, order_id)
                status = str(order.get("status", "")).upper()
                if status == "FILLED":
                    return Decimal(str(order.get("executedQty", "0")))
                if status in ("CANCELED", "REJECTED", "EXPIRED"):
                    logger.error("mexc_order_dead: %s %s status=%s", symbol, order_id, status)
                    return None
            except Exception as exc:
                logger.debug("mexc_order_poll_error: %s", exc)
            await asyncio.sleep(ORDER_POLL_INTERVAL)
        return None

    async def _poll_withdraw_done(self, coin: str, withdraw_id: str) -> bool:
        deadline = time.time() + WITHDRAW_POLL_TIMEOUT
        while time.time() < deadline:
            try:
                records = await self._client.get_withdraw_history(coin)
                for r in records:
                    if str(r.get("id", "")) == withdraw_id:
                        if r.get("status") in WITHDRAW_STATUS_SUCCESS:
                            return True
                        break
            except Exception as exc:
                logger.debug("mexc_withdraw_poll_error: %s", exc)
            await asyncio.sleep(WITHDRAW_POLL_INTERVAL)
        return False

    async def _poll_deposit_credited(self, coin: str, expected: Decimal) -> Decimal | None:
        start_ts = time.time()
        deadline = start_ts + DEPOSIT_POLL_TIMEOUT
        # Accept deposits within 5% of expected (network fee variance).
        min_amount = expected * Decimal("0.95")
        while time.time() < deadline:
            try:
                records = await self._client.get_deposit_history(coin)
                for r in records:
                    amount = Decimal(str(r.get("amount", "0")))
                    insert_ms = float(r.get("insertTime", 0)) / 1000
                    if (
                        r.get("status") in DEPOSIT_STATUS_SUCCESS
                        and amount >= min_amount
                        and insert_ms >= start_ts - 120
                    ):
                        return amount
            except Exception as exc:
                logger.debug("mexc_deposit_poll_error: %s", exc)
            await asyncio.sleep(DEPOSIT_POLL_INTERVAL)
        return None

    # ── Network resolution ──────────────────────────────────────────────

    async def _resolve_network_info(self, coin: str, network: str) -> dict | None:
        """Find raw MEXC networkList entry for coin on internal network name."""
        try:
            now = time.time()
            if self._capital_config_cache is None or now - self._capital_config_ts > 3600:
                self._capital_config_cache = await self._client.get_capital_config()
                self._capital_config_ts = now
            for item in self._capital_config_cache:
                if str(item.get("coin", "")).upper() != coin.upper():
                    continue
                for net in item.get("networkList", []):
                    raw = str(net.get("network", ""))
                    if normalize_mexc_network(raw) == network:
                        return {
                            "network": raw,
                            "withdrawEnable": bool(net.get("withdrawEnable", False)),
                            "depositEnable": bool(net.get("depositEnable", False)),
                            "withdrawFee": net.get("withdrawFee"),
                            "withdrawMin": net.get("withdrawMin"),
                            "minConfirm": net.get("minConfirm"),
                        }
            return None
        except Exception as exc:
            logger.error("mexc_network_resolve_failed: %s %s: %s", coin, network, exc)
            return None

    # ── Step logging / audit ────────────────────────────────────────────

    def _log_step(self, opp, steps: list[str], step: str, detail: str) -> None:
        steps.append(step)
        logger.info("mexc_leg: %s %s [%s] %s", opp.token_coin, opp.network, step, detail)
        self._audit(opp, step, detail)

    def _finish(self, opp, steps: list[str], status: str, reason: str) -> dict:
        result = {
            "status": status,
            "reason": reason,
            "steps": steps,
            "key": opp.key,
            "token": opp.token_coin,
            "network": opp.network,
            "direction": opp.direction,
            "dry_run": self._dry_run,
        }
        level = logging.INFO if status in ("done", "dry_run_ok") else logging.WARNING
        logger.log(level, "mexc_leg_finished: %s %s status=%s reason=%s", opp.token_coin, opp.network, status, reason)
        self._audit(opp, f"finish:{status}", reason)
        return result

    def _audit(self, opp, step: str, detail: str) -> None:
        try:
            EXECUTIONS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(EXECUTIONS_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": time.time(),
                    "key": opp.key,
                    "token": opp.token_coin,
                    "network": opp.network,
                    "direction": opp.direction,
                    "step": step,
                    "detail": detail,
                    "dry_run": self._dry_run,
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass
