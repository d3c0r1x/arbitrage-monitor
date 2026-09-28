"""
Signal Lifecycle Watcher.

Continuously re-quotes validated arbitrage opportunities.
When profit drops below threshold or opportunity expires, removes it.
Exposes active opportunities for the execution module.

Lifecycle:
  scanner finds signal → watcher.promote(signal_ctx)
  watcher re-quotes every WATCHER_INTERVAL_SEC
  profit < WATCHER_MIN_PROFIT_PCT or age > WATCHER_EXPIRY_SEC → remove
  execution module reads watcher.active_opportunities
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from config.settings import settings

OPPORTUNITIES_EXPORT_PATH = Path("data") / "opportunities.json"

logger = logging.getLogger(__name__)


@dataclass
class WatchedOpportunity:
    """A validated signal under continuous monitoring."""

    key: str  # unique: network:pool_address:direction
    network: str
    pool_address: str
    token_address: str
    token_coin: str
    quote_address: str
    direction: str  # DEX_BUY_MEXC_SELL or MEXC_BUY_DEX_SELL
    dex: str
    pool_version: str
    base_amount_usd: Decimal
    initial_net_profit_pct: Decimal
    current_net_profit_pct: Decimal
    last_requote_ts: float = field(default_factory=time.time)
    promoted_ts: float = field(default_factory=time.time)
    requote_count: int = 0
    # Consecutive failed requotes (RPC errors / zero quotes).
    requote_fail_count: int = 0
    # Context for re-quoting.
    token_in: str = ""
    token_out: str = ""
    amount_in_raw: int = 0
    token_decimals: int = 18
    quote_decimals: int = 18
    quote_price_usd: Decimal = Decimal("1")
    mexc_price_usd: Decimal = Decimal("0")
    # Pool fee tier for V3 execution (100/500/2500/3000/10000).
    pool_fee: int = 3000
    # MEXC quote asset for the spot symbol (USDT/USDC).
    mexc_quote_asset: str = "USDT"
    # MEXC min confirmations for token transfer (risk/ETA gate).
    token_min_confirm: int | None = None
    # Chain hops for multi-hop: list of (pool_addr, token_in, token_out, dex, version).
    chain_hops: list = field(default_factory=list)


class SignalWatcher:
    """Watches validated signals, re-quotes, removes dead opportunities."""

    def __init__(self, adapter_factory, profit_calculator):
        self._adapter_factory = adapter_factory
        self._profit_calculator = profit_calculator
        self._opportunities: dict[str, WatchedOpportunity] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._task: asyncio.Task | None = None
        # Stats.
        self.total_promoted = 0
        self.total_expired = 0
        self.total_removed_unprofitable = 0

    @property
    def active_opportunities(self) -> list[WatchedOpportunity]:
        """Return current active opportunities sorted by profit desc."""
        return sorted(
            self._opportunities.values(),
            key=lambda o: o.current_net_profit_pct,
            reverse=True,
        )

    @property
    def count(self) -> int:
        return len(self._opportunities)

    @staticmethod
    def _min_profit_for(network: str) -> Decimal:
        """Per-network freshness profit threshold (ETH gas needs more headroom)."""
        if network == "ETHEREUM":
            return settings.ETH_MIN_NET_PROFIT_PCT
        return settings.MIN_NET_PROFIT_PCT

    def export_state(self) -> None:
        """Write active opportunities with `fresh` flag to data/opportunities.json.

        fresh=true → last requote < FRESH_MAX_AGE_SEC ago AND net profit still
        above the per-network threshold, i.e. executable right now.
        """
        now = time.time()
        items = []
        for opp in self.active_opportunities:
            requote_age = now - opp.last_requote_ts
            fresh = (
                requote_age < settings.FRESH_MAX_AGE_SEC
                and opp.current_net_profit_pct > self._min_profit_for(opp.network)
            )
            items.append({
                "key": opp.key,
                "network": opp.network,
                "token_coin": opp.token_coin,
                "token_address": opp.token_address,
                "pool_address": opp.pool_address,
                "direction": opp.direction,
                "dex": opp.dex,
                "pool_version": opp.pool_version,
                "base_amount_usd": float(opp.base_amount_usd),
                "initial_net_profit_pct": float(opp.initial_net_profit_pct),
                "net_profit_pct": float(opp.current_net_profit_pct),
                "requote_count": opp.requote_count,
                "requote_age_sec": round(requote_age, 1),
                "age_sec": round(now - opp.promoted_ts, 1),
                "fresh": fresh,
            })
        payload = {
            "exported_ts": now,
            "fresh_max_age_sec": settings.FRESH_MAX_AGE_SEC,
            "count": len(items),
            "fresh_count": sum(1 for i in items if i["fresh"]),
            "opportunities": items,
        }
        try:
            OPPORTUNITIES_EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = OPPORTUNITIES_EXPORT_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(OPPORTUNITIES_EXPORT_PATH)
        except OSError as exc:
            logger.debug("watcher_export_failed: %s", exc)

    async def promote(self, opp: WatchedOpportunity) -> None:
        """Add or update a validated opportunity for watching."""
        async with self._lock:
            existing = self._opportunities.get(opp.key)
            if existing:
                # Update profit, keep original promoted_ts.
                existing.current_net_profit_pct = opp.current_net_profit_pct
                existing.last_requote_ts = time.time()
                existing.requote_count += 1
            else:
                self._opportunities[opp.key] = opp
                self.total_promoted += 1
                logger.info(
                    "watcher_promoted: %s %s %s profit=%.2f%%",
                    opp.network, opp.token_coin, opp.direction,
                    float(opp.current_net_profit_pct),
                )

    async def remove(self, key: str, reason: str) -> None:
        """Remove opportunity by key."""
        async with self._lock:
            opp = self._opportunities.pop(key, None)
            if opp:
                if reason == "expired":
                    self.total_expired += 1
                else:
                    self.total_removed_unprofitable += 1
                logger.info(
                    "watcher_removed: %s %s reason=%s profit=%.2f%% age=%.0fs",
                    opp.network, opp.token_coin, reason,
                    float(opp.current_net_profit_pct),
                    time.time() - opp.promoted_ts,
                )

    async def requote_all(self) -> None:
        """Re-quote all watched opportunities, remove dead ones."""
        now = time.time()
        to_remove: list[tuple[str, str]] = []

        opps = list(self._opportunities.values())
        if not opps:
            return

        # Re-quote concurrently with semaphore.
        sem = asyncio.Semaphore(settings.SCANNER_MAX_CONCURRENCY)

        async def requote_one(opp: WatchedOpportunity) -> None:
            async with sem:
                # Check expiry.
                age = now - opp.promoted_ts
                if age > settings.WATCHER_EXPIRY_SEC:
                    to_remove.append((opp.key, "expired"))
                    return

                try:
                    new_profit = await self._requote(opp)
                    if new_profit is None:
                        # Transient failure (RPC error / zero quote): keep the
                        # opportunity with its last known profit; drop only
                        # after several consecutive failures.
                        opp.requote_fail_count += 1
                        logger.debug(
                            "watcher_requote_transient: %s fails=%d",
                            opp.key, opp.requote_fail_count,
                        )
                        if opp.requote_fail_count >= 5:
                            to_remove.append((opp.key, "quote_failed"))
                        return
                    opp.requote_fail_count = 0
                    opp.current_net_profit_pct = new_profit
                    opp.last_requote_ts = now
                    opp.requote_count += 1

                    if new_profit < settings.WATCHER_MIN_PROFIT_PCT:
                        to_remove.append((opp.key, "unprofitable"))
                except Exception as exc:
                    logger.debug("watcher_requote_failed: %s %s", opp.key, exc)
                    # Keep on transient failure; expiry will clean up.

        await asyncio.gather(*(requote_one(o) for o in opps))

        # Remove dead.
        for key, reason in to_remove:
            await self.remove(key, reason)

        if to_remove:
            logger.info(
                "watcher_cycle: active=%d removed=%d",
                len(self._opportunities), len(to_remove),
            )

    async def _requote(self, opp: WatchedOpportunity) -> Decimal | None:
        """Re-quote a single opportunity, return new net_profit_pct.

        Returns None when the quote could not be obtained (adapter missing,
        RPC error, zero output) — the caller treats this as a transient
        failure, NOT as unprofitability.
        """
        adapter = self._adapter_factory.get_adapter(
            opp.network, opp.dex, pool_version=opp.pool_version,
        )
        if adapter is None:
            return None

        if opp.chain_hops:
            # Multi-hop: quote each hop sequentially.
            amount = Decimal(opp.amount_in_raw)
            for hop in opp.chain_hops:
                pool_addr, t_in, t_out = hop[0], hop[1], hop[2]
                hop_dex = hop[3] if len(hop) > 3 else opp.dex
                hop_ver = hop[4] if len(hop) > 4 else opp.pool_version
                hop_adapter = self._adapter_factory.get_adapter(
                    opp.network, hop_dex, pool_version=hop_ver,
                )
                if hop_adapter is None:
                    return None
                amount = await hop_adapter.quote_exact_input(
                    network=opp.network,
                    pool_address=pool_addr,
                    token_in=t_in,
                    token_out=t_out,
                    amount_in=int(amount),
                )
                if amount <= 0:
                    return None
            # Profit: output vs input in USD terms.
            if opp.base_amount_usd <= 0:
                return None
            output_usd = (amount / Decimal(10 ** opp.quote_decimals)) * opp.quote_price_usd
            profit_pct = ((output_usd - opp.base_amount_usd) / opp.base_amount_usd) * 100
            return profit_pct

        # Single-hop: standard re-quote.
        if opp.amount_in_raw <= 0:
            return None
        amount_out = await adapter.quote_exact_input(
            network=opp.network,
            pool_address=opp.pool_address,
            token_in=opp.token_in,
            token_out=opp.token_out,
            amount_in=opp.amount_in_raw,
        )
        if amount_out <= 0:
            return None

        if opp.direction == "DEX_BUY_MEXC_SELL":
            result = await self._profit_calculator.calculate_direction_a(
                network=opp.network,
                token_coin=opp.token_coin,
                mexc_price_usd=opp.mexc_price_usd,
                dex_amount_out=amount_out,
                token_decimals=opp.token_decimals,
                pool_version=opp.pool_version,
                base_amount_usd=opp.base_amount_usd,
            )
        else:
            result = await self._profit_calculator.calculate_direction_b(
                network=opp.network,
                mexc_price_usd=opp.mexc_price_usd,
                dex_amount_out=amount_out,
                stablecoin_decimals=opp.quote_decimals,
                pool_version=opp.pool_version,
                base_amount_usd=opp.base_amount_usd,
                quote_price_usd=opp.quote_price_usd,
            )
        return result["net_profit_pct"]

    async def start(self) -> None:
        """Start the watcher loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("signal_watcher_started: interval=%ds", settings.WATCHER_INTERVAL_SEC)

    async def stop(self) -> None:
        """Stop the watcher loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("signal_watcher_stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.requote_all()
                self.export_state()
            except Exception as exc:
                logger.error("watcher_loop_error: %s", exc)
            await asyncio.sleep(settings.WATCHER_INTERVAL_SEC)
