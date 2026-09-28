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

from config.settings import settings
from metrics.health import get_metrics, update_metrics
from metrics.performance import performance_timer
from models.signal_models import ArbitrageSignal

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

        self._is_running = False
        self._lock = asyncio.Lock()
        self._signal_watcher = None
        self._chain_engine = None
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

                if token_coin:
                    has_coin += 1
                else:
                    quotes_failed += 1
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

                # G5: V4 pools are not supported, skip gracefully.
                if dex_id == "uniswap_v4" or dex_id.endswith("_v4"):
                    quotes_failed += 1
                    logger.debug("v4_pool_skipped: %s", pool_address)
                    return

                pool_version = pool.get("pool_version")
                adapter = self._adapter_factory.get_adapter(
                    network, dex_id, pool_version=pool_version,
                )
                if adapter is None:
                    quotes_failed += 1
                    return
                has_adapter += 1

                # Use dynamic decimals instead of hardcoded 10**18.
                stablecoin_decimals = pool.get("stablecoin_decimals", 18)
                token_decimals = pool.get("token_decimals", 18)

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

                                result_b = await self._profit_calculator.calculate_direction_b(
                                    network=network,
                                    mexc_price_usd=mexc_price_usd,
                                    dex_amount_out=amount_out_b,
                                    stablecoin_decimals=stablecoin_decimals,
                                    mexc_withdraw_fee_usd=mexc_withdraw_fee_usd,
                                    pool_version=pool_version,
                                    base_amount_usd=base_amount_usd,
                                    quote_price_usd=quote_price_usd,
                                )

                                if result_b["net_profit_usd"] > 0:
                                    has_net_positive += 1
                                    self._hot_pools[(network, pool_address, token_address)] = time.time()

                                if result_b["signal"] and result_b["net_profit_pct"] > self._network_min_profit_pct(network):
                                    has_signal += 1
                                    signals_found += 1
                                    written = await self._write_signal(
                                        network, pool, token_coin, quote_asset,
                                        result_b, mexc_price_usd, amount_out_b,
                                        amount_in_raw=token_amount_raw,
                                        quote_price_usd=quote_price_usd,
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

    async def _write_signal(self, network: str, pool: dict, token_coin: str, quote_asset: str, result: dict, mexc_price_usd: Decimal, amount_out_raw: Decimal, amount_in_raw: int = 0, quote_price_usd: Decimal = Decimal("1")) -> bool:
        """Write a signal to the signal_writer.

        Args:
            amount_out_raw: Raw DEX quote output (in smallest unit).
            amount_in_raw: Raw input amount used for the quote (smallest unit).
            quote_price_usd: USD price of the pool's quote asset.

        Returns:
            True if signal was written, False if filtered out.
        """
        net_profit_pct = result["net_profit_pct"]
        warnings: list[str] = []

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

        signal = ArbitrageSignal(
            timestamp=datetime.now(tz=UTC),
            network=network,
            token_coin=token_coin,
            token_address=pool["token_address"],
            mexc_quote_asset=quote_asset,
            mexc_symbol=f"{token_coin}{quote_asset}",
            pool_stablecoin_coin=(
                pool.get("quote_coin")
                or pool.get("stablecoin_coin")
                or quote_asset
            ),
            pool_stablecoin_address=pool["stablecoin_address"],
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
