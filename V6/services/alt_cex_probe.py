"""
Alt-CEX arbitrage probe (Bitget / HTX / BingX, optional OKX DEX).

Runs as a sibling asyncio loop — never blocks the MEXC×DEX scanner.
Writes ALT_CEX_* signals via SignalWriter; does not execute trades.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from clients.alt_cex_client import AltCexClient
from config.networks import NETWORKS, is_network_active
from config.settings import settings
from models.fee_models import FeeBreakdown
from models.signal_models import ArbitrageSignal

logger = logging.getLogger(__name__)

TAKER_BPS_DEFAULT = Decimal("10")  # ~0.10% per CEX leg estimate


class AltCexProbe:
    """Background probe comparing alt-CEX prices vs MEXC and (throttled) DEX."""

    def __init__(
        self,
        *,
        alt_client: AltCexClient,
        price_service,
        signal_writer,
        adapter_factory=None,
        pools_cache_path: str | Path,
        shutdown_event: asyncio.Event | None = None,
    ):
        self._alt = alt_client
        self._price_service = price_service
        self._signal_writer = signal_writer
        self._adapter_factory = adapter_factory
        self._pools_path = Path(pools_cache_path)
        self._shutdown = shutdown_event
        self._maps_at = 0.0
        self._cycle = 0
        self._dex_sem = asyncio.Semaphore(2)

    async def run_loop(self) -> None:
        interval = max(15, int(settings.ALT_CEX_PROBE_INTERVAL_SEC))
        logger.info(
            "alt_cex_probe_started: interval=%ss okx_dex=%s",
            interval,
            settings.ALT_CEX_ENABLE_OKX_DEX,
        )
        # Stagger so first probe doesn't collide with bot startup bursts.
        await asyncio.sleep(min(20, interval))
        while True:
            if self._shutdown and self._shutdown.is_set():
                logger.info("alt_cex_probe_stopped")
                return
            try:
                await self.run_once()
            except Exception as exc:
                logger.warning("alt_cex_probe_error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

    async def run_once(self) -> dict[str, int]:
        self._cycle += 1
        stats = {
            "listed": 0,
            "mexc_signals": 0,
            "dex_signals": 0,
            "dex_quotes": 0,
            "skipped": 0,
        }
        if not settings.ALT_CEX_PROBE_ENABLED:
            return stats

        map_ttl = max(300, int(settings.ALT_CEX_MAP_TTL_SEC))
        if (not self._alt.maps_loaded) or (time.time() - self._maps_at > map_ttl):
            await self._alt.refresh_maps()
            self._maps_at = time.time()

        try:
            await self._price_service.refresh_if_expired()
        except Exception as exc:
            logger.debug("alt_cex_mexc_price_refresh_skip: %s", exc)

        pools = self._load_pools()
        if not pools:
            return stats

        # Unique tokens: prefer contract match coverage.
        tokens = self._unique_tokens(pools)
        max_tokens = max(10, int(settings.ALT_CEX_MAX_TOKENS_PER_CYCLE))
        min_pct = Decimal(str(settings.ALT_CEX_MIN_PROFIT_PCT))
        book_trigger = Decimal(str(settings.ALT_CEX_ORDERBOOK_TRIGGER_PCT))
        base_usd = Decimal(str(settings.ALT_CEX_BASE_AMOUNT_USD))
        taker = Decimal(str(settings.ALT_CEX_TAKER_FEE_BPS)) / Decimal(10000)

        dex_budget = max(0, int(settings.ALT_CEX_MAX_DEX_QUOTES_PER_CYCLE))
        checked = 0

        for tok in tokens:
            if checked >= max_tokens:
                break
            network = tok["network"]
            if not is_network_active(network):
                continue
            listings = self._alt.resolve_listings(
                network, tok["token_address"], tok["token_coin"]
            )
            if not listings:
                continue
            checked += 1
            stats["listed"] += 1

            mexc_px = self._price_service.get_price(tok["token_coin"], "USDT")
            if mexc_px is None:
                mexc_px = self._price_service.get_price(tok["token_coin"], "USDC")

            for listing in listings:
                ex = listing["exchange"]
                coin = listing["coin"]
                alt_px = self._alt.get_usdt_price(ex, coin)
                if alt_px is None:
                    stats["skipped"] += 1
                    continue

                warnings = [
                    f"alt_cex={ex}",
                    f"match={listing['match']}",
                    "no_execution",
                ]
                if listing["match"] == "symbol":
                    warnings.append("symbol_match_not_contract")

                # ── Alt ↔ MEXC ──────────────────────────────────────────
                if mexc_px and mexc_px > 0:
                    # Symbol-only: require larger edge (name collisions).
                    eff_min = min_pct
                    if listing["match"] == "symbol":
                        eff_min = max(min_pct, Decimal("2.0"))
                    gross_pct = abs((alt_px - mexc_px) / mexc_px) * Decimal(100)
                    fee_pct = (taker * Decimal(2)) * Decimal(100)  # both legs
                    net_pct = gross_pct - fee_pct
                    if net_pct >= eff_min:
                        use_book = gross_pct >= book_trigger
                        bid = ask = mid = None
                        if use_book:
                            bid, ask, mid = await self._alt.get_orderbook_mid(ex, coin)
                            if mid and mid > 0:
                                alt_px = mid
                                warnings.append("orderbook_mid")
                                gross_pct = abs((alt_px - mexc_px) / mexc_px) * Decimal(100)
                                net_pct = gross_pct - fee_pct
                        if net_pct >= eff_min:
                            direction = (
                                "ALT_CEX_BUY_MEXC_SELL"
                                if alt_px < mexc_px
                                else "ALT_CEX_SELL_MEXC_BUY"
                            )
                            net_usd = (net_pct / Decimal(100)) * base_usd
                            await self._emit(
                                tok=tok,
                                exchange=ex,
                                coin=coin,
                                direction=direction,
                                alt_px=alt_px,
                                ref_px=mexc_px,
                                base_usd=base_usd,
                                gross_pct=gross_pct,
                                net_pct=net_pct,
                                net_usd=net_usd,
                                warnings=warnings,
                                pool_label=f"{ex}:{coin}USDT",
                            )
                            stats["mexc_signals"] += 1

                # ── Alt ↔ DEX (throttled RPC) ────────────────────────────
                # Symbol-only matches are unsafe vs on-chain pools (ticker
                # collision). Require contract map hit for DEX legs.
                if (
                    listing["match"] == "contract"
                    and dex_budget > 0
                    and self._adapter_factory is not None
                    and tok.get("pool")
                ):
                    # Only spend CU when alt already diverges from MEXC
                    # or MEXC price missing.
                    interesting = True
                    if mexc_px and mexc_px > 0:
                        interesting = (
                            abs((alt_px - mexc_px) / mexc_px) * Decimal(100)
                            >= min_pct / Decimal(2)
                        )
                    if interesting:
                        dex_px = await self._dex_implied_price(tok["pool"])
                        stats["dex_quotes"] += 1
                        dex_budget -= 1
                        if dex_px and dex_px > 0:
                            # Sanity: >100× gap almost always wrong map/decimals.
                            ratio = alt_px / dex_px if alt_px > dex_px else dex_px / alt_px
                            if ratio > Decimal("100"):
                                warnings.append("price_ratio_insanity_skip")
                                continue
                            gross_pct = abs((alt_px - dex_px) / dex_px) * Decimal(100)
                            # CEX taker + rough DEX pool+gas (~0.15%)
                            fee_pct = (taker * Decimal(100)) + Decimal("0.15")
                            net_pct = gross_pct - fee_pct
                            if net_pct >= min_pct:
                                direction = (
                                    "ALT_CEX_BUY_DEX_SELL"
                                    if alt_px < dex_px
                                    else "ALT_CEX_SELL_DEX_BUY"
                                )
                                net_usd = (net_pct / Decimal(100)) * base_usd
                                await self._emit(
                                    tok=tok,
                                    exchange=ex,
                                    coin=coin,
                                    direction=direction,
                                    alt_px=alt_px,
                                    ref_px=dex_px,
                                    base_usd=base_usd,
                                    gross_pct=gross_pct,
                                    net_pct=net_pct,
                                    net_usd=net_usd,
                                    warnings=warnings + ["vs_dex"],
                                    pool_label=tok["pool"].get("pool_address") or "",
                                    dex_name=tok["pool"].get("dex") or "dex",
                                    pool_version=tok["pool"].get("pool_version") or "",
                                )
                                stats["dex_signals"] += 1

            # Optional OKX DEX aggregator price (no CU).
            if settings.ALT_CEX_ENABLE_OKX_DEX and mexc_px:
                chain_id = getattr(NETWORKS.get(network), "chain_id", None)
                if chain_id:
                    okx_px = await self._alt.okx_dex_token_price(
                        chain_id, tok["token_address"]
                    )
                    if okx_px and okx_px > 0:
                        gross_pct = abs((okx_px - mexc_px) / mexc_px) * Decimal(100)
                        net_pct = gross_pct - Decimal("0.3")
                        if net_pct >= min_pct:
                            direction = (
                                "ALT_CEX_BUY_MEXC_SELL"
                                if okx_px < mexc_px
                                else "ALT_CEX_SELL_MEXC_BUY"
                            )
                            await self._emit(
                                tok=tok,
                                exchange="okx_dex",
                                coin=tok["token_coin"],
                                direction=direction,
                                alt_px=okx_px,
                                ref_px=mexc_px,
                                base_usd=base_usd,
                                gross_pct=gross_pct,
                                net_pct=net_pct,
                                net_usd=(net_pct / Decimal(100)) * base_usd,
                                warnings=["alt_cex=okx_dex", "dex_aggregator", "no_execution"],
                                pool_label="okx_dex",
                            )
                            stats["mexc_signals"] += 1

        logger.info(
            "alt_cex_probe_cycle: listed=%d mexc_sig=%d dex_sig=%d dex_q=%d",
            stats["listed"],
            stats["mexc_signals"],
            stats["dex_signals"],
            stats["dex_quotes"],
        )
        return stats

    def _load_pools(self) -> list[dict]:
        if not self._pools_path.exists():
            return []
        try:
            import json

            data = json.loads(self._pools_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("alt_cex_pools_load_fail: %s", exc)
            return []

    def _unique_tokens(self, pools: list[dict]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        out: list[dict[str, Any]] = []
        for p in pools:
            net = str(p.get("network") or "").upper()
            addr = str(p.get("token_address") or "").lower()
            coin = str(p.get("token_coin") or "")
            if not net or not addr or not coin:
                continue
            key = (net, addr)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "network": net,
                    "token_address": addr,
                    "token_coin": coin,
                    "pool": p,
                }
            )
        return out

    async def _dex_implied_price(self, pool: dict) -> Decimal | None:
        """One small exact-input quote → implied token USD price. Soft-fail."""
        if self._adapter_factory is None:
            return None
        async with self._dex_sem:
            try:
                network = pool["network"]
                dex = pool.get("dex") or ""
                ver = pool.get("pool_version") or ""
                adapter = self._adapter_factory.get_adapter(
                    network, dex, pool_version=ver
                )
                if adapter is None:
                    return None
                quote_dec = int(pool.get("stablecoin_decimals") or 18)
                token_dec = int(pool.get("token_decimals") or 18)
                # ~$5 notional to keep impact low.
                amount_in = int(Decimal(5) * (Decimal(10) ** quote_dec))
                if amount_in <= 0:
                    return None
                token_out = await adapter.quote_exact_input(
                    network=network,
                    pool_address=pool["pool_address"],
                    token_in=pool["stablecoin_address"],
                    token_out=pool["token_address"],
                    amount_in=amount_in,
                )
                if not token_out or token_out <= 0:
                    return None
                tokens = Decimal(token_out) / (Decimal(10) ** token_dec)
                if tokens <= 0:
                    return None
                return Decimal(5) / tokens
            except Exception as exc:
                logger.debug("alt_cex_dex_quote_fail: %s", exc)
                return None

    async def _emit(
        self,
        *,
        tok: dict,
        exchange: str,
        coin: str,
        direction: str,
        alt_px: Decimal,
        ref_px: Decimal,
        base_usd: Decimal,
        gross_pct: Decimal,
        net_pct: Decimal,
        net_usd: Decimal,
        warnings: list[str],
        pool_label: str,
        dex_name: str | None = None,
        pool_version: str = "",
    ) -> None:
        fees = FeeBreakdown(
            mexc_trading_fee_usd=base_usd
            * Decimal(str(settings.ALT_CEX_TAKER_FEE_BPS))
            / Decimal(10000)
            * Decimal(2),
        )
        signal = ArbitrageSignal(
            timestamp=datetime.now(tz=UTC),
            network=tok["network"],
            token_coin=tok["token_coin"],
            token_address=tok["token_address"],
            mexc_quote_asset="USDT",
            mexc_symbol=f"{tok['token_coin'].upper()}USDT",
            pool_stablecoin_coin="USDT",
            pool_stablecoin_address="",
            pool_address=pool_label,
            dex=dex_name or exchange,
            pool_version=pool_version,
            direction=direction,
            base_amount_usd=base_usd,
            mexc_price_usd=ref_px,  # reference price (MEXC or DEX)
            dex_amount_in=base_usd,
            dex_amount_out=alt_px,
            gross_profit_usd=(gross_pct / Decimal(100)) * base_usd,
            gross_profit_pct=gross_pct,
            fees=fees,
            net_profit_usd=net_usd,
            net_profit_pct=net_pct,
            full_cycle=False,
            warnings=warnings + [f"alt_price={alt_px}", f"ref_price={ref_px}"],
        )
        await self._signal_writer.write_signal(signal)
        logger.info(
            "alt_cex_signal: %s %s %s net=%.3f%% $%.4f",
            direction,
            tok["token_coin"],
            exchange,
            float(net_pct),
            float(net_usd),
        )
