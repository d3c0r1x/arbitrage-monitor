"""
Scanner loop.

Reads active pools from a JSON cache file that is written by the
pool refresh task after every successful refresh. This completely
avoids Windows SQLite WAL issues where reading pools from the DB
on every cycle returns 0 rows on ~50% of cycles.
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from config.networks import is_network_active
from config.closing_pools import get_closing_pool
from config.settings import settings
from metrics.health import get_metrics, increment_metrics, update_metrics
from metrics.performance import performance_timer
from models.signal_models import ArbitrageSignal
from security.token_address_registry import verify_token
from services.pool_blacklist import PoolBlacklist
from services.signal_filters import post_signal_filter, pre_quote_filter

logger = logging.getLogger(__name__)


class Scanner:
    """Scanner loop for arbitrage opportunities."""

    def __init__(
        self,
        pools_cache_path: str,
        price_service,
        profit_calculator,
        adapter_factory,
        signal_writer,
        rpc_client_factory,
        token_meta_service=None,
        token_security_checker=None,
        pool_security_checker=None,
    ):
        self._pools_cache_path = Path(pools_cache_path)
        self._price_service = price_service
        self._profit_calculator = profit_calculator
        self._adapter_factory = adapter_factory
        self._signal_writer = signal_writer
        self._rpc_client_factory = rpc_client_factory
        self._token_meta_service = token_meta_service
        self._token_security_checker = token_security_checker
        self._pool_security_checker = pool_security_checker
        self._orderbook_service = None

        self._is_running = False
        self._lock = asyncio.Lock()
        self._signal_watcher = None
        self._chain_engine = None
        self._cross_dex_engine = None
        # Dead pools (reserves repeatedly None) are skipped for a TTL.
        self._pool_blacklist = PoolBlacklist()
        # Hot-set: (network, pool_address, token_address) -> last positive ts.
        # Scanned every cycle; the full universe rotates every N cycles.
        self._hot_pools: dict[tuple, float] = {}
        self._scan_cycle = 0

    @staticmethod
    def _network_min_profit_pct(network: str) -> Decimal:
        """Per-network signal threshold: ETH mainnet gas demands more edge."""
        if network == "ETHEREUM":
            return settings.ETH_MIN_NET_PROFIT_PCT
        return settings.MIN_NET_PROFIT_PCT

    def set_signal_watcher(self, watcher) -> None:
        """Attach signal lifecycle watcher for opportunity tracking."""
        self._signal_watcher = watcher

    def set_chain_engine(self, engine) -> None:
        """Attach multi-hop chain engine."""
        self._chain_engine = engine

    def set_cross_dex_engine(self, engine) -> None:
        """Attach same-pair cross-DEX engine."""
        self._cross_dex_engine = engine

    def set_orderbook_service(self, service) -> None:
        """Attach MEXC order-book service for final fill validation."""
        self._orderbook_service = service

    def _load_pools_json(self, log_empty_warning: bool = True) -> list[dict]:
        """Load pools from JSON cache file. Called fresh every cycle.

        pool_refresh_task writes via os.replace (atomic), so the file is
        never in a partial state — no retry needed.

        Returns:
            List of pool dicts, or [] if file missing/empty/error.
        """
        try:
            if not self._pools_cache_path.exists():
                if log_empty_warning:
                    logger.warning("scanner: pools_cache file not found")
                return []

            with open(self._pools_cache_path, encoding="utf-8") as f:
                rows = json.load(f)

            if not rows:
                if log_empty_warning:
                    logger.warning("scanner: pools_cache file is empty")
                return []

            return rows

        except Exception as exc:
            logger.error("scanner: pools_cache load error: %s", exc)
            return []

    def refresh_pool_cache(self) -> int:
        """(Legacy) Kept for compatibility with main.py calls."""
        rows = self._load_pools_json()
        if rows:
            with_coin = sum(1 for p in rows if p.get("token_coin") or p.get("coin"))
            logger.info(
                "scanner_pool_cache_refreshed: pools=%d with_coin=%d",
                len(rows),
                with_coin,
            )
        return len(rows)

    async def run_cycle(self) -> dict:
        """Run a single scanner cycle."""
        async with self._lock:
            if self._is_running:
                logger.warning("scanner_cycle_overrun: previous cycle still running, skipping")
                return {"skipped": True, "reason": "overrun"}
            self._is_running = True

        try:
            async with performance_timer("scanner_cycle") as timer:
                result = await self._execute_cycle(timer)
                return result
        finally:
            self._is_running = False

    async def _execute_cycle(self, timer) -> dict:
        """Execute one scanner cycle."""
        # D1: Graceful price refresh — use stale cache on failure.
        try:
            await self._price_service.refresh_if_expired()
        except Exception as exc:
            logger.warning("price_refresh_failed_using_stale_cache: %s", exc)

        # Read pools JSON fresh every cycle (98KB file, takes <1ms).
        pool_list = self._load_pools_json()

        # Hot-set rotation: scan only recently-positive pools most cycles;
        # run the full universe every FULL_SCAN_EVERY_N_CYCLES.
        self._scan_cycle += 1
        full_every = max(1, settings.FULL_SCAN_EVERY_N_CYCLES)
        full_scan = (self._scan_cycle % full_every == 1) or not self._hot_pools
        if not full_scan:
            now_hot = time.time()
            ttl = settings.HOT_POOL_TTL_SEC
            self._hot_pools = {
                k: ts for k, ts in self._hot_pools.items()
                if now_hot - ts < ttl
            }
            hot_keys = set(self._hot_pools)
            hot_list = [
                p for p in pool_list
                if (p["network"], p["pool_address"], p["token_address"]) in hot_keys
            ]
            if hot_list:
                pool_list = hot_list
                logger.debug(
                    "scanner_hot_set_cycle: hot=%d of_total_hot_keys=%d",
                    len(hot_list), len(hot_keys),
                )
        timer.set_items_total(len(pool_list))

        signals_found = 0
        signals_written = 0
        quotes_success = 0
        quotes_failed = 0
        has_coin = 0
        has_price = 0
        has_adapter = 0
        has_quote = 0
        has_net_positive = 0
        has_signal = 0

        semaphore = asyncio.Semaphore(settings.SCANNER_MAX_CONCURRENCY)

        async def process_pool(pool):
            nonlocal signals_found, signals_written, quotes_success, quotes_failed
            nonlocal has_coin, has_price, has_adapter, has_quote, has_net_positive, has_signal

            async with semaphore:
                token_coin = pool.get("token_coin") or pool.get("coin")
                network = pool["network"]
                pool_address = pool["pool_address"]
                dex_id = pool["dex"]
                token_address = pool["token_address"]
                stablecoin_address = pool["stablecoin_address"]

                # Old caches may still hold ETH/POLY/ROBINHOOD pools (D11).
                if not is_network_active(network):
                    return

                if token_coin:
                    has_coin += 1
                else:
                    quotes_failed += 1
                    return

                # D4: reject impostor contracts carrying well-known tickers.
                ok_addr, addr_reason = verify_token(network, token_coin, token_address)
                if not ok_addr:
                    logger.warning(
                        "fake_token_rejected: %s %s %s",
                        network, token_coin, addr_reason,
                    )
                    increment_metrics(fake_tokens_rejected_total=1)
                    return

                # D5/D1: no version => no adapter choice; no decimals => no math.
                ok_pre, pre_reason = pre_quote_filter(pool)
                if not ok_pre:
                    logger.debug(
                        "pool_pre_filtered: %s %s reason=%s",
                        network, token_coin, pre_reason,
                    )
                    return

                # Dead-pool blacklist: skip pools that keep failing reserves.
                pool_key = f"{network}:{pool_address}"
                if self._pool_blacklist.is_blocked(pool_key):
                    return

                # MEXC USD price of the token — always via USDT/USDC pairs.
                # (TOKEN<quote> pairs like TOKENETH are NOT USD prices.)
                quote_asset = "USDT"
                mexc_price_usd = self._price_service.get_price(token_coin, "USDT")
                if mexc_price_usd is None:
                    quote_asset = "USDC"
                    mexc_price_usd = self._price_service.get_price(token_coin, "USDC")
                if mexc_price_usd is None or mexc_price_usd <= 0:
                    quotes_failed += 1
                    return
                has_price += 1

                # USD price of the pool's quote asset. Stablecoins = $1;
                # wrapped natives / other quotes are priced via MEXC spot.
                quote_is_stable = pool.get("quote_is_stable", True)
                quote_price_usd = Decimal("1")
                if not quote_is_stable:
                    qcoin = (
                        pool.get("quote_price_coin")
                        or pool.get("quote_coin")
                        or pool.get("stablecoin_coin")
                        or ""
                    )
                    qp = self._price_service.get_price(qcoin, "USDT")
                    if qp is None:
                        qp = self._price_service.get_price(qcoin, "USDC")
                    if qp is None or qp <= 0:
                        quotes_failed += 1
                        return
                    quote_price_usd = qp

                # V4 is first-class: adapter may soft-return 0 without PoolKey,
                # but we do not hard-skip by dex_id anymore.
                pool_version = pool.get("pool_version")
                adapter = self._adapter_factory.get_adapter(
                    network, dex_id, pool_version=pool_version,
                )
                if adapter is None:
                    quotes_failed += 1
                    return
                has_adapter += 1

                # Dynamic decimals; pre_quote_filter guarantees presence
                # (fail-closed, no silent 18 — plan v3 D1/D2).
                stablecoin_decimals = int(pool["stablecoin_decimals"])
                token_decimals = int(pool["token_decimals"])

                # Direction gating by MEXC flags: A (DEX_BUY_MEXC_SELL) needs
                # token deposit; B (MEXC_BUY_DEX_SELL) needs token withdraw.
                allow_a = pool.get("token_deposit_enable", True)
                allow_b = pool.get("token_withdraw_enable", True)
                if not allow_a and not allow_b:
                    return

                # ── Get pool reserves to calculate optimal swap size ──
                reserves_a = await adapter.get_reserves(
                    network=network,
                    pool_address=pool_address,
                    token_in=stablecoin_address,
                    token_out=token_address,
                )
                if reserves_a is None:
                    self._pool_blacklist.record_fail(pool_key)
                else:
                    self._pool_blacklist.record_success(pool_key)

                # Calculate optimal amount for Direction A.
                # Use 0.5% of reserve_in, capped at $100, minimum $10.
                # This ensures low slippage and realistic quotes.
                base_amount_usd = settings.BASE_AMOUNT_USD  # Default $10
                if reserves_a is not None:
                    reserve_in_a, reserve_out_a = reserves_a
                    # Convert reserve to USD via the quote asset's USD price.
                    reserve_in_usd = (
                        reserve_in_a / Decimal(10**stablecoin_decimals)
                    ) * quote_price_usd
                    # Optimal: 0.5% of reserve, capped at $100, min $10.
                    optimal_usd = reserve_in_usd * Decimal("0.005")
                    base_amount_usd = max(
                        settings.BASE_AMOUNT_USD,
                        min(optimal_usd, Decimal("100"))
                    )
                    # Skip if reserve too small (< $100).
                    if reserve_in_usd < Decimal("100"):
                        quotes_failed += 1
                        return

                # Convert the USD base amount into quote-token units.
                base_amount_raw = int(
                    (base_amount_usd / quote_price_usd)
                    * Decimal(10**stablecoin_decimals)
                )

                amount_out = Decimal("0")
                direction_a_ok = False
                if allow_a:
                    amount_out = await adapter.quote_exact_input(
                        network=network,
                        pool_address=pool_address,
                        token_in=stablecoin_address,
                        token_out=token_address,
                        amount_in=base_amount_raw,
                    )

                    if amount_out <= 0:
                        quotes_failed += 1
                        # Don't return — Direction B is independent.
                    else:
                        has_quote += 1
                        quotes_success += 1
                        direction_a_ok = True

                # Calculate MEXC withdraw fee in USD from raw token fee.
                mexc_withdraw_fee_usd = Decimal("0")
                raw_fee = pool.get("mexc_withdraw_fee")
                fee_token = Decimal("0")
                if raw_fee is not None and mexc_price_usd > 0:
                    try:
                        fee_token = Decimal(str(raw_fee))
                        mexc_withdraw_fee_usd = fee_token * mexc_price_usd
                    except Exception as exc:
                        logger.debug("withdraw_fee_parse_failed: %s", exc)

                # A1: Quote-asset withdraw fee for direction A (quote withdrawn at end).
                stablecoin_withdraw_fee_usd = Decimal("0")
                raw_stable_fee = pool.get("stablecoin_withdraw_fee")
                if raw_stable_fee is not None:
                    try:
                        stablecoin_withdraw_fee_usd = (
                            Decimal(str(raw_stable_fee)) * quote_price_usd
                        )
                    except Exception as exc:
                        logger.debug("stablecoin_withdraw_fee_parse_failed: %s", exc)

                pool_version = pool.get("pool_version") or ""

                if direction_a_ok:
                    result_a = await self._profit_calculator.calculate_direction_a(
                        network=network,
                        token_coin=token_coin,
                        mexc_price_usd=mexc_price_usd,
                        dex_amount_out=amount_out,
                        token_decimals=token_decimals,
                        stablecoin_withdraw_fee_usd=stablecoin_withdraw_fee_usd,
                        pool_version=pool_version,
                        base_amount_usd=base_amount_usd,
                    )

                    if result_a["net_profit_usd"] > 0:
                        has_net_positive += 1
                        self._hot_pools[(network, pool_address, token_address)] = time.time()

                    if result_a["signal"] and result_a["net_profit_pct"] > self._network_min_profit_pct(network):
                        has_signal += 1
                        signals_found += 1
                        written = await self._write_signal(
                            network, pool, token_coin, quote_asset,
                            result_a, mexc_price_usd, amount_out,
                            amount_in_raw=base_amount_raw,
                            quote_price_usd=quote_price_usd,
                        )
                        if written:
                            signals_written += 1

                # ── Direction B: MEXC_BUY_DEX_SELL ──
                # Get reserves for Direction B (token -> stablecoin).
                # Skipped when token withdraw is disabled on MEXC.
                reserves_b = None
                if allow_b:
                    reserves_b = await adapter.get_reserves(
                        network=network,
                        pool_address=pool_address,
                        token_in=token_address,
                        token_out=stablecoin_address,
                    )

                # Calculate optimal token amount for Direction B.
                token_amount = base_amount_usd / mexc_price_usd
                net_token_amount = token_amount - fee_token
                if net_token_amount <= 0:
                    pass  # Token amount too small to cover withdraw fee.
                else:
                    # Check reserves for Direction B.
                    if reserves_b is not None:
                        reserve_in_b, _ = reserves_b
                        reserve_in_tokens = reserve_in_b / Decimal(10**token_decimals)
                        # Skip if reserve too small.
                        if reserve_in_tokens < net_token_amount * Decimal("10"):
                            pass  # Not enough liquidity for Direction B.
                        else:
                            token_amount_raw = int(net_token_amount * Decimal(10**token_decimals))

                            amount_out_b = await adapter.quote_exact_input(
                                network=network,
                                pool_address=pool_address,
                                token_in=token_address,
                                token_out=stablecoin_address,
                                amount_in=token_amount_raw,
                            )

                            if amount_out_b > 0:
                                has_quote += 1
                                quotes_success += 1

                                # Close wrapped-native (WBNB/WETH) → USDT via
                                # a canonical high-liquidity pool so PnL is
                                # in real USDT, not MEXC mark-to-market.
                                settle_out = amount_out_b
                                settle_decimals = stablecoin_decimals
                                settle_price = quote_price_usd
                                swap_hops = 1
                                closing_pool_version = ""
                                closing_applied = False
                                settlement_coin = ""

                                if not quote_is_stable:
                                    closing_cfg = get_closing_pool(
                                        network, stablecoin_address
                                    )
                                    if closing_cfg is None:
                                        # No A-B-C-A path to USDT/USDC — skip.
                                        quotes_failed += 1
                                        logger.debug(
                                            "no_stable_close_route: %s %s quote=%s",
                                            network,
                                            token_coin,
                                            stablecoin_address[:10],
                                        )
                                        return
                                    closed = await self._quote_closing_to_usdt(
                                        network=network,
                                        quote_address=stablecoin_address,
                                        amount_in=int(amount_out_b),
                                    )
                                    if closed is None:
                                        quotes_failed += 1
                                        logger.debug(
                                            "closing_quote_failed: %s %s quote=%s",
                                            network,
                                            token_coin,
                                            stablecoin_address[:10],
                                        )
                                        return
                                    settle_out, closing_meta = closed
                                    settle_decimals = closing_meta["decimals"]
                                    settle_price = Decimal("1")
                                    swap_hops = 2
                                    closing_pool_version = closing_meta[
                                        "pool_version"
                                    ]
                                    closing_applied = True
                                    settlement_coin = closing_meta[
                                        "settlement_coin"
                                    ]

                                result_b = await self._profit_calculator.calculate_direction_b(
                                    network=network,
                                    mexc_price_usd=mexc_price_usd,
                                    dex_amount_out=settle_out,
                                    stablecoin_decimals=settle_decimals,
                                    mexc_withdraw_fee_usd=mexc_withdraw_fee_usd,
                                    pool_version=pool_version,
                                    base_amount_usd=base_amount_usd,
                                    quote_price_usd=settle_price,
                                    swap_hops=swap_hops,
                                    closing_pool_version=closing_pool_version,
                                    settlement_coin=settlement_coin,
                                    closing_applied=closing_applied,
                                )

                                if result_b["net_profit_usd"] > 0:
                                    has_net_positive += 1
                                    self._hot_pools[(network, pool_address, token_address)] = time.time()

                                if result_b["signal"] and result_b["net_profit_pct"] > self._network_min_profit_pct(network):
                                    has_signal += 1
                                    signals_found += 1
                                    if reserves_b is not None and str(
                                        pool.get("pool_version") or ""
                                    ).lower().startswith("v2"):
                                        rin, rout = reserves_b
                                        result_b["pool_reserve_token_human"] = rin / (
                                            Decimal(10) ** token_decimals
                                        )
                                        result_b["pool_reserve_stable_human"] = rout / (
                                            Decimal(10) ** stablecoin_decimals
                                        )
                                        # Pancake/Uniswap V2 default 0.25% → 25 bps.
                                        result_b["pool_fee_bps"] = 25
                                    written = await self._write_signal(
                                        network, pool, token_coin, quote_asset,
                                        result_b, mexc_price_usd, settle_out,
                                        amount_in_raw=token_amount_raw,
                                        quote_price_usd=settle_price,
                                    )
                                    if written:
                                        signals_written += 1

        results = await asyncio.gather(*[process_pool(p) for p in pool_list], return_exceptions=True)

        # B4: Log and count exceptions that were silently swallowed.
        for r in results:
            if isinstance(r, Exception):
                quotes_failed += 1
                logger.warning("pool_task_failed: %r", r)

        logger.info(
            "scanner_cycle: pools=%d coin=%d price=%d adapter=%d "
            "quote=%d ok=%d net_pos=%d signals=%d",
            len(pool_list),
            has_coin,
            has_price,
            has_adapter,
            has_quote,
            quotes_success,
            has_net_positive,
            signals_found,
        )

        # Multi-hop chain scanning (A-B-C-B-A).
        # Throttled: run every CHAIN_SCAN_EVERY_N_CYCLES to limit RPC load.
        chain_signals = 0
        chain_engine = getattr(self, '_chain_engine', None)
        self._cycle_count = getattr(self, '_cycle_count', 0) + 1
        chain_every = getattr(settings, 'CHAIN_SCAN_EVERY_N_CYCLES', 10)
        run_chain = chain_engine and pool_list and (self._cycle_count % chain_every == 1)
        if run_chain:
            try:
                # Rebuild graph only when pool count changed (cache refresh).
                if not hasattr(self, '_chain_pool_count') or self._chain_pool_count != len(pool_list):
                    chain_engine.build_graph(pool_list)
                    self._chain_pool_count = len(pool_list)
                # Scan a sample of tokens with MEXC prices for chain arb.
                tokens_by_net: dict[str, list[str]] = {}
                for p in pool_list:
                    if p.get("token_coin"):
                        tokens_by_net.setdefault(p["network"], []).append(p["token_address"])
                for net, addrs in tokens_by_net.items():
                    unique = list(set(addrs))[:30]
                    if unique:
                        chain_results = await chain_engine.scan_chains(net, unique)
                        for cr in chain_results:
                            chain_signals += 1
                            # Promote chain opportunity to watcher.
                            watcher = getattr(self, '_signal_watcher', None)
                            if watcher and cr.get("signal"):
                                from scanner.signal_watcher import WatchedOpportunity
                                key = f"{net}:chain:{cr['start_token']}:{id(cr)}"
                                opp = WatchedOpportunity(
                                    key=key,
                                    network=net,
                                    pool_address=cr["chain"][0][0] if cr["chain"] else "",
                                    token_address=cr["start_token"],
                                    token_coin=cr.get("start_coin", ""),
                                    quote_address="",
                                    direction="CHAIN_ARB",
                                    dex="multi",
                                    pool_version="",
                                    base_amount_usd=cr.get("input_usd", Decimal("0")),
                                    initial_net_profit_pct=cr["profit_pct"],
                                    current_net_profit_pct=cr["profit_pct"],
                                    amount_in_raw=cr["amount_in_raw"],
                                    chain_hops=cr["chain"],
                                )
                                await watcher.promote(opp)
                if chain_signals:
                    logger.info("chain_signals_found: %d", chain_signals)
            except Exception as exc:
                logger.debug("chain_scan_error: %s", exc)

        # Same-pair cross-DEX (DEX ↔ DEX). Throttled separately from chains.
        cross_signals = 0
        cross_engine = getattr(self, "_cross_dex_engine", None)
        cross_every = getattr(settings, "CROSS_DEX_SCAN_EVERY_N_CYCLES", 5)
        run_cross = cross_engine and pool_list and (self._cycle_count % cross_every == 1)
        if run_cross:
            try:
                if (
                    not hasattr(self, "_cross_pool_count")
                    or self._cross_pool_count != len(pool_list)
                ):
                    n_groups = cross_engine.build_groups(pool_list)
                    self._cross_pool_count = len(pool_list)
                    logger.info("cross_dex_groups: %d multi-DEX pairs", n_groups)
                cross_results = await cross_engine.scan()
                watcher = getattr(self, "_signal_watcher", None)
                for cr in cross_results:
                    cross_signals += 1
                    # stdout JSON for dashboard / ops visibility
                    print(
                        json.dumps(
                            {
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "network": cr["network"],
                                "token_coin": cr["token_coin"],
                                "direction": "DEX_DEX",
                                "buy_dex": cr["buy_dex"],
                                "sell_dex": cr["sell_dex"],
                                "pool_address": cr["buy_pool"],
                                "sell_pool": cr["sell_pool"],
                                "base_amount_usd": str(cr["base_amount_usd"]),
                                "net_profit_pct": str(cr["net_profit_pct"]),
                                "net_profit_usd": str(cr["net_profit_usd"]),
                                "warnings": [
                                    f"cross_dex:{cr['buy_dex']}->{cr['sell_dex']}"
                                ],
                            },
                            default=str,
                        )
                    )
                    if watcher:
                        from scanner.signal_watcher import WatchedOpportunity

                        key = (
                            f"{cr['network']}:dexdex:{cr['token_address']}:"
                            f"{cr['buy_pool']}:{cr['sell_pool']}"
                        )
                        opp = WatchedOpportunity(
                            key=key,
                            network=cr["network"],
                            pool_address=cr["buy_pool"],
                            token_address=cr["token_address"],
                            token_coin=cr["token_coin"],
                            quote_address=cr["quote_address"],
                            direction="DEX_DEX",
                            dex=f"{cr['buy_dex']}→{cr['sell_dex']}",
                            pool_version=cr.get("buy_version") or "",
                            base_amount_usd=cr["base_amount_usd"],
                            initial_net_profit_pct=cr["net_profit_pct"],
                            current_net_profit_pct=cr["net_profit_pct"],
                            amount_in_raw=cr["amount_in_raw"],
                            chain_hops=cr["chain"],
                            mexc_quote_asset=cr.get("quote_coin") or "USDT",
                            quote_decimals=int(cr.get("quote_decimals") or 18),
                            quote_price_usd=Decimal(str(cr.get("quote_price_usd") or 1)),
                        )
                        await watcher.promote(opp)
                if cross_signals:
                    logger.info("cross_dex_signals_found: %d", cross_signals)
            except Exception as exc:
                logger.warning("cross_dex_scan_error: %s", exc)

        # G2: Update health metrics.
        # signals_total is owned by signal_writer (increments per signal).
        prev_cycles = get_metrics().get("scanner_cycles", 0)
        update_metrics(
            scanner_cycles=prev_cycles + 1,
            pools_cached=len(pool_list),
            last_scanner_cycle_ts=time.time(),
        )
        if signals_written > 0:
            update_metrics(last_signal_ts=time.time())

        return {
            "pools_total": len(pool_list),
            "has_coin": has_coin,
            "has_price": has_price,
            "has_adapter": has_adapter,
            "has_quote": has_quote,
            "quotes_success": quotes_success,
            "quotes_failed": quotes_failed,
            "net_positive": has_net_positive,
            "signals_found": signals_found,
            "signals_written": signals_written,
        }

    async def _quote_closing_to_usdt(
        self,
        network: str,
        quote_address: str,
        amount_in: int,
    ) -> tuple[Decimal, dict] | None:
        """Quote wrapped-native → USDT via the canonical closing pool.

        Returns:
            (usdt_amount_raw, meta) or None if no pool / adapter / quote.
        """
        closing = get_closing_pool(network, quote_address)
        if closing is None or amount_in <= 0:
            return None

        adapter = self._adapter_factory.get_adapter(
            network,
            closing.dex_id,
            pool_version=closing.pool_version,
        )
        if adapter is None:
            logger.debug(
                "closing_adapter_missing: %s %s",
                network,
                closing.dex_id,
            )
            return None

        amount_out = await adapter.quote_exact_input(
            network=network,
            pool_address=closing.pool_address,
            token_in=closing.token_in,
            token_out=closing.token_out,
            amount_in=amount_in,
        )
        if amount_out <= 0:
            return None

        return amount_out, {
            "pool_address": closing.pool_address,
            "dex_id": closing.dex_id,
            "pool_version": closing.pool_version,
            "decimals": closing.token_out_decimals,
            "settlement_coin": closing.settlement_coin,
            "settlement_address": closing.token_out,
        }

    async def _apply_orderbook_final(
        self,
        result: dict,
        direction: str,
        mexc_symbol: str,
        mexc_price_usd: Decimal,
        amount_out_raw: Decimal,
        pool: dict,
    ) -> tuple[dict, Decimal, bool, list[str]]:
        """Final gate: size the MEXC clip for maximum USD profit on the book.

        Walks live depth; keep trade if net% ≥ MIN_NET_PROFIT_PCT on clip size.

        Returns:
            (updated_result, amount_out_raw, ok, warnings)
        """
        if getattr(self, "_orderbook_service", None) is None:
            return result, amount_out_raw, True, []

        fees = result["fees"]
        warnings: list[str] = []
        min_usd = settings.MIN_NET_PROFIT_USD
        min_pct = settings.MIN_NET_PROFIT_PCT
        taker_bps = int(settings.MEXC_TAKER_FEE_BPS)

        if mexc_price_usd <= 0:
            return result, amount_out_raw, False, ["orderbook_mexc_price_zero"]

        settlement_usd = result.get("settlement_out_usd")
        if settlement_usd is None:
            settlement_usd = result["gross_profit_usd"] + result["base_amount_usd"]

        # Fixed on-chain / withdraw costs (do not scale with clip size).
        # MEXC trading fee is recomputed on the optimal notional.
        # Slippage buffer is replaced by real book pricing.
        fixed_fees = (
            fees.dex_network_fee_usd
            + fees.stable_deposit_network_fee_usd
        )

        book = await self._orderbook_service.fetch_book(mexc_symbol)
        if book is None:
            return result, amount_out_raw, False, ["orderbook_unavailable"]

        if direction == "MEXC_BUY_DEX_SELL":
            base = result["base_amount_usd"]
            mid_tokens = base / mexc_price_usd
            raw_fee = pool.get("mexc_withdraw_fee")
            fee_token = Decimal("0")
            if raw_fee is not None:
                try:
                    fee_token = Decimal(str(raw_fee))
                except Exception:
                    fee_token = Decimal("0")
            # Quote used net tokens (gross - withdraw fee).
            net_quoted = mid_tokens - fee_token
            if net_quoted <= 0 or settlement_usd <= 0:
                return result, amount_out_raw, False, ["orderbook_empty_fill"]
            dex_usdt_per_token = settlement_usd / net_quoted

            # Withdraw fee is paid in tokens (handled via fee_token above);
            # keep USD withdraw out of fixed double-count.
            optimal = self._orderbook_service.maximize_dir_b_profit(
                book,
                dex_usdt_per_token=dex_usdt_per_token,
                fee_token=fee_token,
                fixed_fees_usd=fixed_fees,
                taker_fee_bps=taker_bps,
                min_notional_usd=Decimal("1"),
                max_notional_usd=max(base * Decimal("3"), Decimal("50")),
                min_profit_usd=min_usd,
                min_profit_pct=min_pct,
            )
            if optimal is None:
                return result, amount_out_raw, False, ["orderbook_no_profitable_clip"]

            fill = optimal.fill
            # Scale raw DEX/USDT out to the optimal net token amount.
            scale = optimal.tokens_net / net_quoted
            new_out = amount_out_raw * scale
            new_base = optimal.cost_usd
            new_settlement = optimal.settlement_usd
            book_impact = Decimal("0")  # priced in VWAP already
            trading_fee = new_base * Decimal(taker_bps) / Decimal(10000)
            # Keep withdraw fee USD proportional display for transparency.
            withdraw_usd = fee_token * mexc_price_usd
            # Curve uses the same flat DEX rate as maximize_dir_b (quoted
            # settlement / net tokens). Passing raw V2 reserves often disagrees
            # with the live quote on meme pools and paints the whole slider red.
            curve = self._orderbook_service.build_dir_b_size_curve(
                book,
                dex_usdt_per_token=dex_usdt_per_token,
                fee_token=fee_token,
                fixed_fees_usd=fixed_fees,
                taker_fee_bps=taker_bps,
                min_notional_usd=Decimal("1"),
                max_notional_usd=max(base * Decimal("5"), Decimal("100")),
                min_profit_usd=Decimal("0"),
            )

        elif direction == "DEX_BUY_MEXC_SELL":
            token_decimals = int(pool.get("token_decimals") or 18)
            full_tokens = amount_out_raw / (Decimal(10) ** token_decimals)
            full_cost = result["base_amount_usd"]
            optimal = self._orderbook_service.maximize_dir_a_profit(
                book,
                full_tokens=full_tokens,
                full_cost_usd=full_cost,
                fixed_fees_usd=fixed_fees + fees.mexc_withdraw_fee_usd,
                taker_fee_bps=taker_bps,
                min_notional_usd=Decimal("1"),
                min_profit_usd=min_usd,
                min_profit_pct=min_pct,
            )
            if optimal is None:
                return result, amount_out_raw, False, ["orderbook_no_profitable_clip"]

            fill = optimal.fill
            scale = optimal.tokens_net / full_tokens if full_tokens > 0 else Decimal("0")
            new_out = amount_out_raw * scale
            new_base = optimal.cost_usd
            new_settlement = optimal.settlement_usd
            book_impact = Decimal("0")
            trading_fee = new_settlement * Decimal(taker_bps) / Decimal(10000)
            withdraw_usd = fees.mexc_withdraw_fee_usd * scale
            curve = self._orderbook_service.build_dir_a_size_curve(
                book,
                full_tokens=full_tokens,
                full_cost_usd=full_cost,
                fixed_fees_usd=fixed_fees + fees.mexc_withdraw_fee_usd,
                taker_fee_bps=taker_bps,
                min_notional_usd=Decimal("1"),
            )
        else:
            return result, amount_out_raw, True, []

        new_fees = fees.model_copy(
            update={
                "slippage_usd": book_impact,
                "mexc_trading_fee_usd": trading_fee,
                "mexc_withdraw_fee_usd": withdraw_usd,
                "dex_pool_fee_usd": (
                    fees.dex_pool_fee_usd * (new_base / result["base_amount_usd"])
                    if result["base_amount_usd"] > 0
                    else fees.dex_pool_fee_usd
                ),
            }
        )
        new_gross = new_settlement - new_base
        new_gross_pct = (
            (new_gross / new_base) * Decimal("100") if new_base > 0 else Decimal("0")
        )
        new_net = new_settlement - new_base - new_fees.total()
        new_net_pct = (
            (new_net / new_base) * Decimal("100") if new_base > 0 else Decimal("0")
        )

        size_curve_payload = [
            {
                "size_usd": str(p.size_usd),
                "net_usd": str(p.net_usd),
                "net_pct": str(p.net_pct),
                "vwap": str(p.vwap),
                "impact_pct": str(p.impact_pct),
                "tokens": str(p.tokens),
            }
            for p in curve
        ]
        size_min = curve[0].size_usd if curve else new_base
        size_max = curve[-1].size_usd if curve else new_base

        updated = dict(result)
        updated.update(
            {
                "base_amount_usd": new_base,
                "fees": new_fees,
                "gross_profit_usd": new_gross,
                "gross_profit_pct": new_gross_pct,
                "net_profit_usd": new_net,
                "net_profit_pct": new_net_pct,
                "settlement_out_usd": new_settlement,
                "signal": new_net_pct >= min_pct,
                "orderbook_avg_price": fill.avg_price,
                "orderbook_impact_pct": fill.impact_pct,
                "orderbook_fully_filled": fill.fully_filled,
                "orderbook_optimal_cost_usd": new_base,
                "size_min_usd": size_min,
                "size_max_usd": size_max,
                "size_optimal_usd": new_base,
                "size_curve": size_curve_payload,
            }
        )
        warnings.append(
            f"orderbook_vwap={fill.avg_price:.12g}"
            f"|impact_pct={fill.impact_pct:.4f}"
            f"|size_usd={new_base:.4f}"
            f"|net_usd={new_net:.4f}"
            f"|net_pct={new_net_pct:.4f}"
            f"|gas_usd={new_fees.dex_network_fee_usd}"
            f"|pool_fee_est_usd={new_fees.dex_pool_fee_usd}"
            f"|mexc_fee_usd={new_fees.mexc_trading_fee_usd}"
        )
        logger.info(
            "orderbook_final: %s dir=%s size=$%s vwap=%s impact_pct=%s "
            "net_usd=%s net_pct=%s->%s gas=%s",
            mexc_symbol,
            direction,
            new_base,
            fill.avg_price,
            fill.impact_pct,
            new_net,
            result["net_profit_pct"],
            new_net_pct,
            new_fees.dex_network_fee_usd,
        )
        if new_net_pct < min_pct:
            return updated, new_out, False, warnings + ["orderbook_killed_edge"]
        return updated, new_out, True, warnings

    async def _write_signal(self, network: str, pool: dict, token_coin: str, quote_asset: str, result: dict, mexc_price_usd: Decimal, amount_out_raw: Decimal, amount_in_raw: int = 0, quote_price_usd: Decimal = Decimal("1")) -> bool:
        """Write a signal to the signal_writer.

        Args:
            amount_out_raw: Raw DEX quote output (in smallest unit).
            amount_in_raw: Raw input amount used for the quote (smallest unit).
            quote_price_usd: USD price of the pool's quote asset.

        Returns:
            True if signal was written, False if filtered out.
        """
        warnings: list[str] = []
        if result.get("closing_applied"):
            settle = str(result.get("settlement_coin") or "USDT").upper()
            warnings.append(f"closed_to_{settle.lower()}")

        # Security checks — only for profitable signals (RPC overhead).
        if self._token_security_checker:
            try:
                token_warnings = await self._token_security_checker.check(
                    network=network,
                    token_address=pool["token_address"],
                )
                warnings.extend(token_warnings)
            except Exception as exc:
                warnings.append("token_security_check_error")
                logger.debug("token_security_check_crashed: %s", exc)

        if self._pool_security_checker:
            try:
                pool_warnings = await self._pool_security_checker.check(
                    network=network,
                    pool_address=pool["pool_address"],
                    pool_version=pool.get("pool_version"),
                )
                warnings.extend(pool_warnings)
            except Exception as exc:
                warnings.append("pool_security_check_error")
                logger.debug("pool_security_check_crashed: %s", exc)

        # Filter out signals with security warnings (mint, pause, blacklist).
        # These tokens are dangerous — can't actually execute the arbitrage.
        dangerous_warnings = {
            "token_mint_detected", "token_pause_detected",
            "token_blacklist_detected", "token_honeypot_detected",
        }
        if any(w in dangerous_warnings for w in warnings):
            logger.debug(
                "signal_filtered_security: %s warnings=%s",
                token_coin, warnings,
            )
            return False

        # Final gate: live MEXC order book after USDT settlement calc.
        # Replaces flat slippage buffer with executable VWAP impact.
        # Gas + MEXC fees remain; pool fee stays informational on FeeBreakdown.
        mexc_symbol = f"{token_coin}{quote_asset}"
        result, amount_out_raw, book_ok, book_warnings = await self._apply_orderbook_final(
            result=result,
            direction=result["direction"],
            mexc_symbol=mexc_symbol,
            mexc_price_usd=mexc_price_usd,
            amount_out_raw=amount_out_raw,
            pool=pool,
        )
        warnings.extend(book_warnings)
        if not book_ok:
            logger.info(
                "signal_filtered: %s %s reason=orderbook_reject warnings=%s",
                network, token_coin, book_warnings,
            )
            increment_metrics(signals_filtered_total=1)
            return False

        net_profit_pct = result["net_profit_pct"]

        # D14/D6: sanity filters — unrealistic profit, direction-aware
        # MEXC deposit/withdraw availability, fee ratio, min_confirm.
        mexc_withdraw_fee_usd = None
        raw_fee = pool.get("mexc_withdraw_fee")
        if raw_fee is not None and mexc_price_usd > 0:
            try:
                mexc_withdraw_fee_usd = Decimal(str(raw_fee)) * mexc_price_usd
            except Exception:
                mexc_withdraw_fee_usd = None

        ok_post, post_reason = post_signal_filter(
            net_profit_pct=net_profit_pct,
            direction=result["direction"],
            token_deposit_enable=bool(pool.get("token_deposit_enable", True)),
            token_withdraw_enable=bool(pool.get("token_withdraw_enable", True)),
            base_amount_usd=result["base_amount_usd"],
            mexc_withdraw_fee_usd=mexc_withdraw_fee_usd,
            token_min_confirm=pool.get("token_min_confirm"),
            warnings=warnings,
            net_profit_usd=result.get("net_profit_usd"),
            min_net_profit_usd=settings.MIN_NET_PROFIT_USD,
        )
        if not ok_post:
            logger.info(
                "signal_filtered: %s %s reason=%s",
                network, token_coin, post_reason,
            )
            increment_metrics(signals_filtered_total=1)
            return False

        settlement_coin = result.get("settlement_coin") or ""
        closing_applied = bool(result.get("closing_applied"))
        pool_stable_coin = (
            settlement_coin
            if closing_applied and settlement_coin
            else (
                pool.get("quote_coin")
                or pool.get("stablecoin_coin")
                or quote_asset
            )
        )
        pool_stable_addr = pool["stablecoin_address"]
        if closing_applied:
            closing = get_closing_pool(network, pool["stablecoin_address"])
            if closing is not None:
                pool_stable_addr = closing.token_out

        signal = ArbitrageSignal(
            timestamp=datetime.now(tz=UTC),
            network=network,
            token_coin=token_coin,
            token_address=pool["token_address"],
            mexc_quote_asset=quote_asset,
            mexc_symbol=mexc_symbol,
            pool_stablecoin_coin=pool_stable_coin,
            pool_stablecoin_address=pool_stable_addr,
            pool_address=pool["pool_address"],
            dex=pool["dex"] or "",
            pool_version=pool.get("pool_version") or "",
            direction=result["direction"],
            base_amount_usd=result["base_amount_usd"],
            mexc_price_usd=mexc_price_usd,
            dex_amount_in=result["base_amount_usd"],
            dex_amount_out=amount_out_raw,
            gross_profit_usd=result["gross_profit_usd"],
            gross_profit_pct=result["gross_profit_pct"],
            fees=result["fees"],
            net_profit_usd=result["net_profit_usd"],
            net_profit_pct=net_profit_pct,
            full_cycle=True,
            warnings=warnings,
            orderbook_avg_price=result.get("orderbook_avg_price"),
            orderbook_impact_pct=result.get("orderbook_impact_pct"),
            orderbook_fully_filled=result.get("orderbook_fully_filled"),
            size_min_usd=result.get("size_min_usd"),
            size_max_usd=result.get("size_max_usd"),
            size_optimal_usd=result.get("size_optimal_usd"),
            size_curve=result.get("size_curve") or [],
        )
        await self._signal_writer.write_signal(signal)

        # Promote to watcher if clean + profitable.
        watcher = getattr(self, '_signal_watcher', None)
        if watcher and not warnings and result["signal"]:
            from scanner.signal_watcher import WatchedOpportunity
            direction = result["direction"]
            key = f"{network}:{pool['pool_address']}:{direction}"
            # Determine token_in/token_out for re-quoting.
            if direction == "DEX_BUY_MEXC_SELL":
                t_in = pool["stablecoin_address"]
                t_out = pool["token_address"]
            else:
                t_in = pool["token_address"]
                t_out = pool["stablecoin_address"]
            opp = WatchedOpportunity(
                key=key,
                network=network,
                pool_address=pool["pool_address"],
                token_address=pool["token_address"],
                token_coin=token_coin,
                quote_address=pool["stablecoin_address"],
                direction=direction,
                dex=pool.get("dex", ""),
                pool_version=pool.get("pool_version", ""),
                base_amount_usd=result["base_amount_usd"],
                initial_net_profit_pct=net_profit_pct,
                current_net_profit_pct=net_profit_pct,
                token_in=t_in,
                token_out=t_out,
                amount_in_raw=amount_in_raw,
                token_decimals=pool.get("token_decimals", 18),
                quote_decimals=pool.get("stablecoin_decimals", 18),
                quote_price_usd=quote_price_usd,
                mexc_price_usd=mexc_price_usd,
                pool_fee=self._get_pool_fee(network, pool),
                mexc_quote_asset=quote_asset or "USDT",
                token_min_confirm=pool.get("token_min_confirm"),
            )
            await watcher.promote(opp)

        return True

    def _get_pool_fee(self, network: str, pool: dict) -> int:
        """Get V3 pool fee from adapter cache (for executor)."""
        try:
            adapter = self._adapter_factory.get_adapter(
                network, pool.get("dex", ""), pool_version=pool.get("pool_version", "")
            )
            if adapter and hasattr(adapter, '_fee_cache'):
                return adapter._fee_cache.get(pool["pool_address"], 3000)
        except Exception:
            pass
        return 3000
