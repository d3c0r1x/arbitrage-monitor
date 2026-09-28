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

from config.networks import BLOCK_TIME_SEC
from config.settings import settings

# Buffer after the deposit credits: time to place the MEXC market sell.
_SELL_BUFFER_SEC = 120


def watcher_expiry_sec(
    token_min_confirm: int | None, network: str
) -> int:
    """Dynamic opportunity TTL (plan v3 D8).

    An opportunity must live at least as long as the token transfer
    takes to credit on MEXC (min_confirm blocks) plus a sell buffer.
    Example: AIDOGE on Arbitrum, min_confirm=5000, block=0.25s
    => 5000*0.25 + 120 = 1370s (~23 min), not the fixed 300s.
    """
    base = settings.WATCHER_EXPIRY_SEC
    if not token_min_confirm or token_min_confirm <= 0:
        return base
    block_time = BLOCK_TIME_SEC.get(network.upper(), 3.0)
    deposit_eta = int(token_min_confirm * block_time) + _SELL_BUFFER_SEC
    return max(base, deposit_eta)

OPPORTUNITIES_EXPORT_PATH = Path("data") / "opportunities.json"
OPPORTUNITIES_LIVE_PATH = Path("data") / "opportunities_live.json"
ARCHIVE_PATH = Path("data") / "signals_archive.jsonl"

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
    # Soft risk / book notes for dashboard (do not block promote).
    warnings: list = field(default_factory=list)
    # Chain hops for multi-hop: list of (pool_addr, token_in, token_out, dex, version).
    chain_hops: list = field(default_factory=list)
    # Dir B: withdraw fee in token units (already netted from amount_in_raw).
    mexc_withdraw_fee_token: Decimal = Decimal("0")
    # Last mid-price net before book (debug / UI).
    mid_net_profit_pct: Decimal | None = None
    # Frozen scan size — requote must not chase maximize()'s larger notionals
    # (base*$3 feedback → $243→$1k+ → orderbook_no_profitable_clip).
    anchor_base_usd: Decimal | None = None
    anchor_amount_in_raw: int | None = None
    # Executable size window for LIVE UI (from book curve / size ladder).
    size_min_usd: Decimal | None = None
    size_max_usd: Decimal | None = None
    size_optimal_usd: Decimal | None = None
    # PancakeSwap Price API mid (USD), optional enrich.
    pcs_mark_usd: Decimal | None = None


class SignalWatcher:
    """Watches validated signals, re-quotes, removes dead opportunities."""

    def __init__(
        self,
        adapter_factory,
        profit_calculator,
        price_service=None,
        orderbook_applier=None,
        pcs_price_api=None,
    ):
        self._adapter_factory = adapter_factory
        self._profit_calculator = profit_calculator
        self._price_service = price_service
        # Optional: Scanner._apply_orderbook_final bound method.
        self._orderbook_applier = orderbook_applier
        self._pcs_price_api = pcs_price_api
        self._opportunities: dict[str, WatchedOpportunity] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._task: asyncio.Task | None = None
        # Stats.
        self.total_promoted = 0
        self.total_expired = 0
        self.total_removed_unprofitable = 0

    def set_pcs_price_api(self, api) -> None:
        self._pcs_price_api = api

    def set_price_service(self, price_service) -> None:
        self._price_service = price_service

    def set_orderbook_applier(self, applier) -> None:
        """Inject Scanner._apply_orderbook_final for executable LIVE PnL."""
        self._orderbook_applier = applier

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
                "size_min_usd": (
                    float(opp.size_min_usd) if opp.size_min_usd is not None else None
                ),
                "size_max_usd": (
                    float(opp.size_max_usd) if opp.size_max_usd is not None else None
                ),
                "size_optimal_usd": (
                    float(opp.size_optimal_usd)
                    if opp.size_optimal_usd is not None
                    else float(opp.base_amount_usd)
                ),
                "initial_net_profit_pct": float(opp.initial_net_profit_pct),
                "net_profit_pct": float(opp.current_net_profit_pct),
                "mid_net_profit_pct": (
                    float(opp.mid_net_profit_pct)
                    if opp.mid_net_profit_pct is not None
                    else None
                ),
                "pcs_mark_usd": (
                    float(opp.pcs_mark_usd) if opp.pcs_mark_usd is not None else None
                ),
                "requote_count": opp.requote_count,
                "requote_age_sec": round(requote_age, 1),
                "age_sec": round(now - opp.promoted_ts, 1),
                "fresh": fresh,
                "warnings": list(opp.warnings or []),
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
            raw = json.dumps(payload, ensure_ascii=False)
            for path in (OPPORTUNITIES_EXPORT_PATH, OPPORTUNITIES_LIVE_PATH):
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(raw, encoding="utf-8")
                tmp.replace(path)
        except OSError as exc:
            logger.debug("watcher_export_failed: %s", exc)

    async def promote(self, opp: WatchedOpportunity) -> None:
        """Add or update a validated opportunity for watching."""
        async with self._lock:
            existing = self._opportunities.get(opp.key)
            if existing:
                # Update profit + requote context; keep original promoted_ts.
                existing.current_net_profit_pct = opp.current_net_profit_pct
                existing.base_amount_usd = opp.base_amount_usd
                existing.amount_in_raw = opp.amount_in_raw
                existing.quote_decimals = opp.quote_decimals
                existing.quote_price_usd = opp.quote_price_usd
                existing.mexc_price_usd = opp.mexc_price_usd
                existing.chain_hops = list(opp.chain_hops or [])
                existing.warnings = list(opp.warnings or [])
                existing.mexc_withdraw_fee_token = opp.mexc_withdraw_fee_token
                existing.mid_net_profit_pct = opp.mid_net_profit_pct
                existing.mexc_quote_asset = opp.mexc_quote_asset or existing.mexc_quote_asset
                if opp.size_min_usd is not None:
                    existing.size_min_usd = opp.size_min_usd
                if opp.size_max_usd is not None:
                    existing.size_max_usd = opp.size_max_usd
                if opp.size_optimal_usd is not None:
                    existing.size_optimal_usd = opp.size_optimal_usd
                # Refresh anchors only when scanner re-promotes a new scan size.
                if opp.anchor_base_usd is not None:
                    existing.anchor_base_usd = opp.anchor_base_usd
                if opp.anchor_amount_in_raw is not None:
                    existing.anchor_amount_in_raw = opp.anchor_amount_in_raw
                existing.last_requote_ts = time.time()
                existing.requote_count += 1
                existing.requote_fail_count = 0
            else:
                if opp.anchor_base_usd is None:
                    opp.anchor_base_usd = opp.base_amount_usd
                if opp.anchor_amount_in_raw is None:
                    opp.anchor_amount_in_raw = opp.amount_in_raw
                if opp.size_optimal_usd is None:
                    opp.size_optimal_usd = opp.base_amount_usd
                if opp.size_min_usd is None:
                    opp.size_min_usd = opp.base_amount_usd
                if opp.size_max_usd is None:
                    opp.size_max_usd = opp.base_amount_usd
                self._opportunities[opp.key] = opp
                self.total_promoted += 1
                logger.info(
                    "watcher_promoted: %s %s %s profit=%.2f%%",
                    opp.network, opp.token_coin, opp.direction,
                    float(opp.current_net_profit_pct),
                )
            self.export_state()

    async def remove(self, key: str, reason: str) -> None:
        """Remove opportunity by key; archive snapshot for dashboard history."""
        async with self._lock:
            opp = self._opportunities.pop(key, None)
            if opp:
                if reason == "expired":
                    self.total_expired += 1
                else:
                    self.total_removed_unprofitable += 1
                self._archive_opportunity(opp, reason)
                logger.info(
                    "watcher_removed: %s %s reason=%s profit=%.2f%% age=%.0fs",
                    opp.network, opp.token_coin, reason,
                    float(opp.current_net_profit_pct),
                    time.time() - opp.promoted_ts,
                )
            self.export_state()

    @staticmethod
    def _archive_opportunity(opp: "WatchedOpportunity", reason: str) -> None:
        """Append dead opportunity to archive jsonl (history, not LIVE)."""
        try:
            ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
            row = {
                "archived_ts": time.time(),
                "reason": reason,
                "key": opp.key,
                "network": opp.network,
                "token_coin": opp.token_coin,
                "token_address": opp.token_address,
                "pool_address": opp.pool_address,
                "direction": opp.direction,
                "dex": opp.dex,
                "base_amount_usd": str(opp.base_amount_usd),
                "size_min_usd": (
                    str(opp.size_min_usd) if opp.size_min_usd is not None else None
                ),
                "size_max_usd": (
                    str(opp.size_max_usd) if opp.size_max_usd is not None else None
                ),
                "size_optimal_usd": (
                    str(opp.size_optimal_usd)
                    if opp.size_optimal_usd is not None
                    else str(opp.base_amount_usd)
                ),
                "net_profit_pct": str(opp.current_net_profit_pct),
                "initial_net_profit_pct": str(opp.initial_net_profit_pct),
            }
            with ARCHIVE_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.debug("archive_write_failed: %s", exc)

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
                # Check expiry (dynamic: min_confirm deposit ETA aware, D8).
                age = now - opp.promoted_ts
                expiry = watcher_expiry_sec(opp.token_min_confirm, opp.network)
                if age > expiry:
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

                    soft = any(
                        str(w).startswith("soft:")
                        for w in (opp.warnings or [])
                    )
                    # Soft / near-miss radar rows stay until expiry even if
                    # the next quote dips — otherwise the UI stays empty.
                    if soft:
                        return
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
        """Re-quote a single opportunity, return executable net_profit_pct.

        Pipeline matches scanner hard path:
          refresh MEXC mid → DEX (+closing) quote → fee calc → orderbook size.
        Returns None on transient quote failure.
        """
        quote_asset = (opp.mexc_quote_asset or "USDT").upper()
        if self._price_service is not None:
            try:
                px = self._price_service.get_price(opp.token_coin, quote_asset)
            except Exception:
                px = None
            if px is not None and px > 0:
                opp.mexc_price_usd = px

        adapter = None
        if not opp.chain_hops:
            adapter = self._adapter_factory.get_adapter(
                opp.network, opp.dex, pool_version=opp.pool_version,
            )
            if adapter is None:
                return None

        amount_out: Decimal
        last_ver = opp.pool_version
        swap_hops = 1
        closing_applied = False

        quote_in = (
            opp.anchor_amount_in_raw
            if opp.anchor_amount_in_raw is not None
            else opp.amount_in_raw
        )
        quote_base = (
            opp.anchor_base_usd
            if opp.anchor_base_usd is not None
            else opp.base_amount_usd
        )

        if opp.chain_hops:
            amount = Decimal(quote_in)
            for hop in opp.chain_hops:
                pool_addr, t_in, t_out = hop[0], hop[1], hop[2]
                hop_dex = hop[3] if len(hop) > 3 else opp.dex
                hop_ver = hop[4] if len(hop) > 4 else opp.pool_version
                last_ver = hop_ver or last_ver
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
            amount_out = amount
            swap_hops = len(opp.chain_hops)
            closing_applied = swap_hops > 1
        else:
            if quote_in <= 0:
                return None
            amount_out = await adapter.quote_exact_input(
                network=opp.network,
                pool_address=opp.pool_address,
                token_in=opp.token_in,
                token_out=opp.token_out,
                amount_in=quote_in,
            )
            if amount_out <= 0:
                return None

        if quote_base <= 0 or opp.mexc_price_usd <= 0:
            return None

        qdec = opp.quote_decimals or 18
        qpx = opp.quote_price_usd if opp.quote_price_usd > 0 else Decimal("1")

        if opp.direction == "DEX_BUY_MEXC_SELL":
            result = await self._profit_calculator.calculate_direction_a(
                network=opp.network,
                token_coin=opp.token_coin,
                mexc_price_usd=opp.mexc_price_usd,
                dex_amount_out=amount_out,
                token_decimals=opp.token_decimals,
                pool_version=opp.pool_version,
                base_amount_usd=quote_base,
            )
        else:
            result = await self._profit_calculator.calculate_direction_b(
                network=opp.network,
                mexc_price_usd=opp.mexc_price_usd,
                dex_amount_out=amount_out,
                stablecoin_decimals=qdec,
                # quote_in is already net of withdraw fee tokens.
                mexc_withdraw_fee_usd=Decimal("0"),
                pool_version=opp.pool_version,
                base_amount_usd=quote_base,
                quote_price_usd=qpx,
                swap_hops=swap_hops,
                closing_pool_version=last_ver if closing_applied else "",
                closing_applied=closing_applied,
            )

        opp.mid_net_profit_pct = result["net_profit_pct"]

        if self._orderbook_applier is None:
            opp.base_amount_usd = quote_base
            return result["net_profit_pct"]

        pool_meta = {
            "token_decimals": opp.token_decimals,
            "mexc_withdraw_fee": str(opp.mexc_withdraw_fee_token or 0),
        }
        try:
            updated, _new_out, book_ok, book_warnings = await self._orderbook_applier(
                result=result,
                direction=opp.direction,
                mexc_symbol=f"{opp.token_coin}{quote_asset}",
                mexc_price_usd=opp.mexc_price_usd,
                amount_out_raw=amount_out,
                pool=pool_meta,
            )
        except Exception as exc:
            logger.debug("watcher_orderbook_failed: %s %s", opp.key, exc)
            opp.base_amount_usd = quote_base
            return result["net_profit_pct"]

        soft_tags = [
            w for w in (opp.warnings or []) if str(w).startswith("soft:")
        ]
        # Drop stale soft:orderbook_reject when book recovers.
        soft_tags = [w for w in soft_tags if w != "soft:orderbook_reject"]
        if not book_ok:
            soft_tags.append("soft:orderbook_reject")
        compact = [
            w for w in (book_warnings or [])
            if isinstance(w, str) and (
                w.startswith("orderbook_") or w.startswith("orderbook_vwap=")
            )
        ][:2]
        opp.warnings = soft_tags + compact

        # Display optimal clip size, but keep DEX quote anchored.
        opp.base_amount_usd = updated["base_amount_usd"]
        if updated.get("size_min_usd") is not None:
            opp.size_min_usd = Decimal(str(updated["size_min_usd"]))
        if updated.get("size_max_usd") is not None:
            opp.size_max_usd = Decimal(str(updated["size_max_usd"]))
        if updated.get("size_optimal_usd") is not None:
            opp.size_optimal_usd = Decimal(str(updated["size_optimal_usd"]))
        else:
            opp.size_optimal_usd = updated["base_amount_usd"]
        opp.amount_in_raw = quote_in
        return updated["net_profit_pct"]

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

    async def enrich_pcs_marks(self) -> None:
        """Refresh PancakeSwap Price API mids for active opportunities."""
        if self._pcs_price_api is None or not settings.PCS_PRICE_API_ENABLED:
            return
        opps = list(self._opportunities.values())
        if not opps:
            return
        by_net: dict[str, list[WatchedOpportunity]] = {}
        for o in opps:
            by_net.setdefault(o.network, []).append(o)
        for network, group in by_net.items():
            addrs = list({o.token_address.lower() for o in group if o.token_address})
            if not addrs:
                continue
            try:
                prices = await self._pcs_price_api.get_token_prices(network, addrs)
            except Exception as exc:
                logger.debug("pcs_mark_enrich_failed: %s %s", network, exc)
                continue
            for o in group:
                px = prices.get(o.token_address.lower())
                if px is not None and px > 0:
                    o.pcs_mark_usd = px

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.requote_all()
                await self.enrich_pcs_marks()
                self.export_state()
            except Exception as exc:
                logger.error("watcher_loop_error: %s", exc)
            await asyncio.sleep(settings.WATCHER_INTERVAL_SEC)
