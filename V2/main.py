"""
MEXC × DEX Arbitrage Monitor.

Monitoring only — no execution.
Runs pool refresh and scanner concurrently.
"""

import asyncio
import logging
import signal
from decimal import Decimal
from pathlib import Path

from config.settings import settings
from utils.logging import setup_logging

logger = logging.getLogger("mexc_dex_arb")


class ArbitrageMonitor:
    """Main application class."""

    def __init__(self):
        self._shutdown_event = asyncio.Event()
        self._http_client = None
        self._alt_cex_http = None
        self._alt_cex_probe = None
        self._mexc_client = None
        self._price_service = None
        self._mexc_asset_service = None
        self._stablecoin_registry = None
        self._source_manager = None
        self._pool_discovery = None
        self._scanner = None
        self._signal_writer = None
        self._assets = []
        self._rpc_clients: dict = {}
        self._pre_fetched_assets = []

    async def initialize(self) -> None:
        """Initialize all components."""
        key_presence = settings.log_key_presence()
        logger.info(
            "app_initializing mexc=%s alchemy=%s",
            key_presence["mexc_api_key_present"],
            key_presence["alchemy_key_present"],
        )

        from clients.http_client import create_http_client
        from clients.mexc_client import MexcClient
        from dex.adapter_factory import AdapterFactory
        from metrics.health import SourceHealthTracker
        from scanner.pool_refresh_task import PoolRefreshTask
        from scanner.scanner import Scanner
        from scanner.signal_writer import SignalWriter
        from services.fee_service import FeeService
        from services.mexc_asset_service import MexcAssetService
        from services.pool_discovery_service import PoolDiscoveryService
        from services.price_service import PriceService
        from services.profit_calculator import ProfitCalculator
        from services.stablecoin_registry_service import StablecoinRegistryService
        from storage.backup_manager import BackupManager
        from storage.database import create_connection, initialize_schema

        # Data paths.
        project_root = Path(__file__).resolve().parent
        active_db_path = project_root / "data" / "state" / "active.sqlite3"
        backup_dir = project_root / "data" / "state" / "backups"
        signals_jsonl_path = project_root / "data" / "signals.jsonl"

        active_db_path.parent.mkdir(parents=True, exist_ok=True)
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Clients.
        http_client = await create_http_client()
        mexc_client = MexcClient(http_client)

        # Services.
        price_service = PriceService(
            mexc_client,
            cache_ttl_sec=settings.MEXC_PRICE_CACHE_TTL_SEC,
        )
        mexc_asset_service = MexcAssetService(mexc_client)
        stablecoin_registry_service = StablecoinRegistryService()

        # Database.
        conn = create_connection(active_db_path)
        initialize_schema(conn)
        conn.close()

        # Health tracker.
        health_tracker = SourceHealthTracker(db_path=str(active_db_path))

        from discovery.dexscreener_source import DexScreenerSource
        from discovery.geckoterminal_source import GeckoTerminalSource
        from discovery.onchain_factory_source import OnchainFactorySource
        from discovery.source_manager import SourceManager

        dexscreener_source = DexScreenerSource(http_client)
        geckoterminal_source = GeckoTerminalSource(http_client)

        def rpc_factory(network: str):
            from clients.rpc_client import RoundRobinRpcClient, RpcClient
            from config.networks import is_network_active, resolve_rpc_urls
            from config.rate_limits import RATE_LIMITS
            from utils.cu_rate_limiter import CuRateLimiter

            # Inactive networks (ETH/POLY/ROBINHOOD) never get RPC clients:
            # they burn Alchemy CU with zero real signals (plan v3 D11).
            if not is_network_active(network):
                logger.debug("rpc_factory_skip_inactive_network: %s", network)
                return None

            # Return cached client if already created for this network.
            cached = self._rpc_clients.get(network)
            if cached is not None:
                return cached

            rpc_urls = resolve_rpc_urls(network)
            if not rpc_urls:
                logger.warning("no_rpc_url_for_network: %s", network)
                return None

            rpc_limits = RATE_LIMITS["rpc"]
            cu_per_key = float(
                rpc_limits.get(
                    "cu_per_sec_per_key_effective",
                    float(rpc_limits.get("cu_per_sec_per_key", 500))
                    * float(rpc_limits.get("cu_headroom", 0.95)),
                )
            )
            # One CuRateLimiter per Alchemy key (475 CU/s @ 95% of 500).
            clients = []
            for url in rpc_urls:
                rate_limiter = CuRateLimiter(
                    cu_per_sec=cu_per_key,
                    burst_cu=cu_per_key * 2,
                )
                clients.append(RpcClient(rpc_url=url, rate_limiter=rate_limiter))

            if len(clients) == 1:
                client = clients[0]
            else:
                client = RoundRobinRpcClient(clients)
                logger.info(
                    "rpc_round_robin: network=%s keys=%d cu_per_key=%.0f",
                    network, len(clients), cu_per_key,
                )

            self._rpc_clients[network] = client
            return client

        onchain_factory = OnchainFactorySource(
            rpc_client_factory=rpc_factory,
            stablecoin_registry_service=stablecoin_registry_service,
        )

        sources = {
            "dexscreener": dexscreener_source,
            "geckoterminal": geckoterminal_source,
            "onchain_factory": onchain_factory,
        }

        source_manager = SourceManager(
            sources=sources,
            health_tracker=health_tracker,
        )

        pool_discovery = PoolDiscoveryService(
            source_manager=source_manager,
            stablecoin_registry_service=stablecoin_registry_service,
        )

        # Storage.
        backup_manager = BackupManager(
            active_db_path=str(active_db_path),
            backup_dir=str(backup_dir),
            max_backups=settings.MAX_BACKUPS,
        )

        signal_writer = SignalWriter(
            signals_jsonl_path=str(signals_jsonl_path),
            db_path=str(active_db_path),
        )

        fee_service = FeeService(
            rpc_client_factory=rpc_factory,
            price_service=price_service,
            mexc_taker_fee_bps=settings.MEXC_TAKER_FEE_BPS,
        )

        profit_calculator = ProfitCalculator(fee_service=fee_service)

        from clients.web3_manager import Web3Manager
        web3_manager = Web3Manager()
        adapter_factory = AdapterFactory(web3_manager=web3_manager)

        # Token metadata service (on-chain decimals).
        from clients.multicall_client import MulticallClient
        from services.token_meta_service import TokenMetaService
        multicall_client = MulticallClient(rpc_client_factory=rpc_factory)
        token_meta_service = TokenMetaService(
            rpc_client_factory=rpc_factory,
            multicall_client=multicall_client,
        )

        # Pool version detector.
        from dex.pool_detector import PoolDetector
        pool_detector = PoolDetector(rpc_client_factory=rpc_factory)

        # Security checkers.
        from security.pool_security_checker import PoolSecurityChecker
        from security.token_security_checker import TokenSecurityChecker
        token_security_checker = TokenSecurityChecker(rpc_client_factory=rpc_factory)
        pool_security_checker = PoolSecurityChecker(rpc_client_factory=rpc_factory)

        scanner = Scanner(
            pools_cache_path=str(project_root / "data" / "pools_cache.json"),
            price_service=price_service,
            profit_calculator=profit_calculator,
            adapter_factory=adapter_factory,
            signal_writer=signal_writer,
            rpc_client_factory=rpc_factory,
            token_security_checker=token_security_checker,
            pool_security_checker=pool_security_checker,
        )

        from services.orderbook_service import OrderbookService
        orderbook_service = OrderbookService(mexc_client)
        scanner.set_orderbook_service(orderbook_service)

        # Signal lifecycle watcher.
        from scanner.signal_watcher import SignalWatcher
        signal_watcher = SignalWatcher(
            adapter_factory=adapter_factory,
            profit_calculator=profit_calculator,
        )
        scanner.set_signal_watcher(signal_watcher)

        # Multi-hop chain engine.
        from scanner.chain_engine import ChainEngine
        chain_engine = ChainEngine(
            adapter_factory=adapter_factory,
            price_service=price_service,
        )
        scanner.set_chain_engine(chain_engine)

        # Same-pair cross-DEX engine (DEX ↔ DEX).
        from scanner.cross_dex_engine import CrossDexEngine
        cross_dex_engine = CrossDexEngine(
            adapter_factory=adapter_factory,
            price_service=price_service,
            fee_service=fee_service,
        )
        scanner.set_cross_dex_engine(cross_dex_engine)

        # Alt-CEX probe (Bitget/HTX/BingX) — sibling loop, no MEXC pipeline impact.
        self._alt_cex_probe = None
        if settings.ALT_CEX_PROBE_ENABLED:
            from clients.alt_cex_client import AltCexClient
            from services.alt_cex_probe import AltCexProbe

            alt_http = await create_http_client(timeout=45)
            self._alt_cex_http = alt_http
            alt_client = AltCexClient(
                alt_http,
                enable_okx_dex=settings.ALT_CEX_ENABLE_OKX_DEX,
            )
            self._alt_cex_probe = AltCexProbe(
                alt_client=alt_client,
                price_service=price_service,
                signal_writer=SignalWriter(
                    signals_jsonl_path=str(project_root / "data" / "alt_cex_signals.jsonl"),
                    db_path=None,
                    update_health_metrics=False,
                ),
                adapter_factory=adapter_factory,
                pools_cache_path=str(project_root / "data" / "pools_cache.json"),
                shutdown_event=self._shutdown_event,
            )
        else:
            self._alt_cex_http = None

        # Execution module (inert without DEX_PRIVATE_KEY).
        from execution.executor import Executor
        from execution.mexc_executor import MexcExecutor
        from execution.safety import ExecutionGuard
        executor = Executor(
            rpc_client_factory=rpc_factory,
            web3_manager=web3_manager,
            adapter_factory=adapter_factory,
        )
        self._executor = executor
        self._mexc_executor = MexcExecutor(mexc_client=mexc_client)
        self._execution_guard = ExecutionGuard()
        self._signal_watcher = signal_watcher

        refresh_task = PoolRefreshTask(
            mexc_client=mexc_client,
            price_service=price_service,
            mexc_asset_service=mexc_asset_service,
            stablecoin_registry_service=stablecoin_registry_service,
            source_manager=source_manager,
            pool_discovery_service=pool_discovery,
            active_db_path=str(active_db_path),
            token_meta_service=token_meta_service,
            backup_manager=backup_manager,
            pool_detector=pool_detector,
            onchain_factory=onchain_factory,
        )

        # Fetch initial assets (used for stablecoin registry + passed to refresh).
        # Soft-fail when SKIP_INITIAL_REFRESH so a transient DNS blip does not
        # kill the whole process — scanner can still use pools_cache.json.
        import os as _os
        _skip = _os.environ.get("SKIP_INITIAL_REFRESH", "").strip() in (
            "1", "true", "True", "yes", "YES",
        )
        try:
            assets = await mexc_asset_service.fetch_and_parse_assets()
        except Exception as exc:
            if not _skip:
                raise
            logger.warning(
                "initial_assets_fetch_failed_soft: %s (SKIP_INITIAL_REFRESH=1)",
                exc,
            )
            assets = []
        self._assets = assets

        # Build stablecoin registry and pass to onchain factory.
        if assets:
            stablecoin_registry = stablecoin_registry_service.build_registry(assets)
            onchain_factory.set_stablecoin_registry(stablecoin_registry)

        # Store pre-fetched assets to avoid double-fetch in start().
        self._pre_fetched_assets = assets

        self._http_client = http_client
        self._mexc_client = mexc_client
        self._price_service = price_service
        self._mexc_asset_service = mexc_asset_service
        self._scanner = scanner
        self._refresh_task = refresh_task
        self._adapter_factory = adapter_factory

        logger.info("app_initialized")

    async def start(self) -> None:
        """Start the monitor loops."""
        logger.info("app_starting")
        # Dashboard "session" filter reads this — survives dashboard restarts.
        try:
            import json
            import os
            import time as _time

            session_path = Path(__file__).resolve().parent / "data" / "bot_session.json"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                json.dumps(
                    {
                        "started_at": _time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", _time.gmtime()
                        ),
                        "pid": os.getpid(),
                    }
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("bot_session_write_failed: %s", exc)

        # Run first refresh immediately (with pre-fetched assets from initialize).
        # Wrapped in try/except to ensure scanner loop starts even if refresh fails.
        # SKIP_INITIAL_REFRESH=1 reuses existing pools_cache.json (fast debug runs).
        import os
        skip_refresh = os.environ.get("SKIP_INITIAL_REFRESH", "").strip() in (
            "1", "true", "True", "yes", "YES",
        )
        if skip_refresh and (Path(__file__).resolve().parent / "data" / "pools_cache.json").exists():
            logger.info("skipping_initial_pool_refresh: using existing pools_cache.json")
            self._scanner.refresh_pool_cache()  # type: ignore[union-attr]
        else:
            logger.info("running_initial_pool_refresh")
            try:
                refresh_result = await self._refresh_task.run_refresh(
                    pre_fetched_assets=self._pre_fetched_assets,
                )
                logger.info(
                    "initial_refresh: pools=%d assets=%d stablecoins=%d error=%s",
                    refresh_result.get("pools_count", 0),
                    refresh_result.get("assets_count", 0),
                    refresh_result.get("stablecoins_count", 0),
                    refresh_result.get("error", "none"),
                )

                # Refresh scanner's in-memory pool cache after DB write.
                # The scanner uses this cache for all cycles, bypassing DB reads.
                self._scanner.refresh_pool_cache()  # type: ignore[union-attr]
                if refresh_result.get("pools_count", 0) == 0:
                    logger.info(
                        "initial_refresh: no_pools scanner_will_retry_on_next_cycle"
                    )
            except Exception as exc:
                logger.error("initial_refresh_crashed: %s", exc, exc_info=True)
                logger.warning("initial_refresh: scanner_starts_with_empty_db")

        # Start concurrent loops.
        tasks = [
            self._run_scanner_loop(),
            self._run_refresh_loop(),
            self._run_execution_loop(),
            self._wait_shutdown(),
        ]
        if self._alt_cex_probe is not None:
            tasks.append(self._run_alt_cex_probe_loop())
        await asyncio.gather(*tasks)

    async def _run_alt_cex_probe_loop(self) -> None:
        """Secondary CEX probe — isolated from scanner cycle timing."""
        try:
            await self._alt_cex_probe.run_loop()  # type: ignore[union-attr]
        except Exception as exc:
            logger.error("alt_cex_probe_loop_crashed: %s", exc, exc_info=True)

    async def _run_scanner_loop(self) -> None:
        interval = settings.SCAN_INTERVAL_SEC

        while not self._shutdown_event.is_set():
            try:
                result = await self._scanner.run_cycle()  # type: ignore[union-attr]
                logger.debug("scanner_cycle: %s", result)
            except Exception as exc:
                logger.error("scanner_cycle_error: %s", exc)

            try:
                await asyncio.wait_for(
                    self._wait_with_shutdown(interval),
                    timeout=interval + 5,
                )
            except TimeoutError:
                continue

    async def _run_refresh_loop(self) -> None:
        interval = settings.POOL_REFRESH_INTERVAL_SEC

        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._wait_with_shutdown(interval),
                    timeout=interval + 5,
                )
            except TimeoutError:
                continue

            try:
                result = await self._refresh_task.run_refresh()
                pools_count = result.get("pools_count", 0)
                logger.info("pool_refresh_completed: pools=%d", pools_count)
                if pools_count > 0:
                    self._scanner.refresh_pool_cache()  # type: ignore[union-attr]
            except Exception as exc:
                logger.error("pool_refresh_error: %s", exc)

    async def _run_execution_loop(self) -> None:
        """Start signal watcher + execute opportunities if executor enabled."""
        # Start watcher regardless (monitors opportunities).
        try:
            await self._signal_watcher.start()
        except Exception as exc:
            logger.error("execution_loop_start_failed: %s", exc)
            return

        if not self._executor.enabled:
            logger.info("execution_loop: executor disabled (no DEX_PRIVATE_KEY)")
            # Still run watcher loop for dashboard visibility.
            while not self._shutdown_event.is_set():
                await asyncio.sleep(settings.WATCHER_INTERVAL_SEC)
            return

        logger.info(
            "execution_loop: executor ACTIVE dry_run=%s address=%s",
            self._executor._dry_run, self._executor.address,
        )
        while not self._shutdown_event.is_set():
            try:
                # Kill-switch / daily loss limit: hard stop for the whole loop.
                if self._execution_guard.kill_switch_active():
                    logger.warning("execution_paused: kill_switch_active")
                    await asyncio.sleep(settings.WATCHER_INTERVAL_SEC)
                    continue
                if self._execution_guard.daily_loss_exceeded():
                    logger.warning(
                        "execution_paused: daily_loss_limit_reached (%s USD)",
                        settings.DAILY_LOSS_LIMIT_USD,
                    )
                    await asyncio.sleep(settings.WATCHER_INTERVAL_SEC)
                    continue

                opps = self._signal_watcher.active_opportunities
                for opp in opps[:3]:  # Max 3 concurrent executions.
                    if opp.current_net_profit_pct < settings.EXECUTION_MIN_PROFIT_PCT:
                        continue
                    allowed, reason = self._execution_guard.allow_execution(opp)
                    if not allowed:
                        logger.info("execution_gated: %s %s", opp.key, reason)
                        continue

                    # Direction B entry: MEXC buy + withdraw BEFORE the DEX sell.
                    if opp.direction == "MEXC_BUY_DEX_SELL":
                        if not await self._mexc_executor.check_balance(
                            "USDT", opp.base_amount_usd
                        ):
                            continue
                        leg = await self._mexc_executor.run_entry_leg(
                            opp, self._executor.address
                        )
                        if leg.get("status") not in ("done", "dry_run_ok"):
                            logger.warning("mexc_entry_leg_failed: %s %s", opp.key, leg.get("reason"))
                            continue

                    result = await self._executor.execute_opportunity(opp)
                    if result.get("status") in ("broadcast", "dry_run_ok", "confirmed"):
                        # Direction A exit: token must land on MEXC, then sell.
                        if opp.direction == "DEX_BUY_MEXC_SELL":
                            expected = (
                                opp.base_amount_usd / opp.mexc_price_usd
                                if opp.mexc_price_usd > 0 else Decimal("0")
                            )
                            leg = await self._mexc_executor.run_exit_leg(opp, expected)
                            if leg.get("status") not in ("done", "dry_run_ok"):
                                logger.warning("mexc_exit_leg_failed: %s %s", opp.key, leg.get("reason"))
                        # Record estimated PnL for the daily loss gate (real fills
                        # refine this; dry-run records nothing).
                        if result.get("status") == "confirmed":
                            self._execution_guard.record_pnl(
                                opp.base_amount_usd
                                * opp.current_net_profit_pct / Decimal("100")
                            )
                        await self._signal_watcher.remove(opp.key, "executed")
            except Exception as exc:
                logger.error("execution_loop_error: %s", exc)
            await asyncio.sleep(settings.WATCHER_INTERVAL_SEC)

    async def _wait_with_shutdown(self, delay: float) -> None:
        try:
            await asyncio.wait_for(
                self._shutdown_event.wait(),
                timeout=delay,
            )
        except TimeoutError:
            pass

    async def _wait_shutdown(self) -> None:
        await self._shutdown_event.wait()

    async def shutdown(self) -> None:
        logger.info("app_shutting_down")
        self._shutdown_event.set()

        # Stop signal watcher.
        if hasattr(self, '_signal_watcher') and self._signal_watcher:
            await self._signal_watcher.stop()

        if self._http_client:
            await self._http_client.aclose()
        alt_http = getattr(self, "_alt_cex_http", None)
        if alt_http is not None:
            try:
                await alt_http.aclose()
            except Exception:
                pass

        # Close all tracked RPC clients to avoid Unclosed client session errors.
        for rpc in list(self._rpc_clients.values()):
            try:
                await rpc.close()
            except Exception:
                pass
        self._rpc_clients.clear()

        logger.info("app_shutdown_complete")


async def main() -> None:
    """Application entry point."""
    import os
    setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
    monitor = ArbitrageMonitor()

    def handle_signal():
        asyncio.create_task(monitor.shutdown())

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass

    try:
        await monitor.initialize()
        await monitor.start()
    except asyncio.CancelledError:
        pass
    finally:
        await monitor.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
