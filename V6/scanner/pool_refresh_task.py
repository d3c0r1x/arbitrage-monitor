"""
Pool refresh task.

Writes discovered pools DIRECTLY to the active DB within a SQLite transaction.
No temp DB, no atomic_replace, no ATTACH DATABASE — avoids all Windows file-locking issues.

The transaction ensures atomicity: readers (scanner) see either old data or new data,
never partial state. On failure, automatic rollback preserves old data.

Source health table is NOT touched (preserves records written during discovery).
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

from config.settings import settings
from metrics.performance import performance_timer
from services.refresh_status import write_refresh_status

logger = logging.getLogger(__name__)


class PoolRefreshTask:
    """Background task that refreshes pool data."""

    def __init__(
        self,
        mexc_client,
        price_service,
        mexc_asset_service,
        stablecoin_registry_service,
        source_manager,
        pool_discovery_service,
        active_db_path: str,
        token_meta_service=None,
        backup_manager=None,
        pool_detector=None,
        onchain_factory=None,
    ):
        self._mexc_client = mexc_client
        self._price_service = price_service
        self._mexc_asset_service = mexc_asset_service
        self._stablecoin_registry_service = stablecoin_registry_service
        self._source_manager = source_manager
        self._pool_discovery_service = pool_discovery_service
        self._active_db_path = active_db_path
        self._token_meta_service = token_meta_service
        self._backup_manager = backup_manager
        self._pool_detector = pool_detector
        self._onchain_factory = onchain_factory

    async def run_refresh(self, pre_fetched_assets=None) -> dict:
        """Execute a full pool refresh cycle with performance tracking.

        Args:
            pre_fetched_assets: Optional list of already-fetched MexcAsset.
                If provided, skips the redundant MEXC API call.
        """
        async with performance_timer("pool_refresh") as timer:
            return await self._run_refresh_inner(timer, pre_fetched_assets)

    async def _run_refresh_inner(self, timer, pre_fetched_assets=None) -> dict:
        """Inner refresh logic. Writes directly to active DB via transaction."""
        logger.info("pool_refresh_started")
        write_refresh_status(phase="starting", phase_pct=0, message="pool_refresh_started")

        # Step 1: Fetch MEXC assets (or use pre-fetched from initialize).
        write_refresh_status(phase="assets", phase_pct=10, message="fetching_mexc_assets")
        if pre_fetched_assets is not None:
            assets = pre_fetched_assets
            logger.info("pool_refresh: using pre_fetched_assets=%d", len(assets))
        else:
            assets = await self._mexc_asset_service.fetch_and_parse_assets()
        logger.info("pool_refresh: mexc_assets=%d", len(assets))
        write_refresh_status(
            phase="assets", phase_pct=100, done=len(assets), total=len(assets),
            message=f"mexc_assets={len(assets)}",
        )

        if not assets:
            logger.warning("pool_refresh: no_mexc_assets_fetched")
            write_refresh_status(phase="error", message="no_mexc_assets", running=False)
            return {"pools_count": 0, "assets_count": 0, "stablecoins_count": 0}

        # Step 2: Refresh prices.
        await self._price_service.refresh_all_prices()
        prices = self._price_service.get_all_prices_dict()
        logger.info("pool_refresh: prices=%d", len(prices))

        # Step 3: Select candidate tokens.
        volumes: dict[str, Decimal] | None = None
        try:
            volumes = await self._mexc_asset_service.fetch_24hr_volumes()
            logger.info("pool_refresh: 24hr_volumes=%d", len(volumes))
        except Exception as exc:
            logger.warning("pool_refresh: 24hr_volumes_failed (proceeding without volume filter): %s", exc)

        candidates = self._mexc_asset_service.select_candidate_tokens(
            assets, prices, volumes=volumes,
        )

        # D11: drop candidates on inactive networks (ETH/POLY/ROBINHOOD)
        # before any discovery/RPC spend.
        from config.networks import is_network_active
        before_net_filter = len(candidates)
        candidates = [c for c in candidates if is_network_active(c[1])]
        if before_net_filter != len(candidates):
            logger.info(
                "pool_refresh: inactive_networks_filtered %d -> %d",
                before_net_filter, len(candidates),
            )

        logger.info(
            "pool_refresh: candidates=%d unique_tokens=%d",
            len(candidates),
            len({c[0].coin for c in candidates}),
        )

        if not candidates:
            logger.warning("pool_refresh: no_candidate_tokens_found")
            return {"pools_count": 0, "assets_count": len(assets), "stablecoins_count": 0}

        # G1: Pagination — process candidates in batches per refresh cycle.
        batch_size = settings.CANDIDATES_PER_REFRESH_CYCLE
        total_candidates = len(candidates)
        candidate_offset = 0

        if total_candidates > batch_size:
            # Read last offset from previous refresh cycle.
            try:
                _conn_tmp = sqlite3.connect(self._active_db_path)
                row = _conn_tmp.execute(
                    "SELECT candidate_offset FROM refresh_log "
                    "WHERE status LIKE 'completed%' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                _conn_tmp.close()
                if row and row[0] is not None:
                    candidate_offset = row[0] % total_candidates
            except Exception as exc:
                logger.debug("pagination_offset_read_failed: %s", exc)

            # Slice batch with wrap-around.
            if candidate_offset + batch_size <= total_candidates:
                candidates = candidates[candidate_offset:candidate_offset + batch_size]
            else:
                # Wrap around the end.
                candidates = candidates[candidate_offset:] + candidates[:batch_size - (total_candidates - candidate_offset)]

            logger.info(
                "pool_refresh: paginated offset=%d batch=%d total=%d",
                candidate_offset, len(candidates), total_candidates,
            )

        # Compute next offset for storage in refresh_log.
        next_offset = (candidate_offset + len(candidates)) % max(total_candidates, 1)

        # Step 4: Build stablecoin registry + broad quote registry.
        stablecoin_registry = self._stablecoin_registry_service.build_registry(assets)
        networks_with_stablecoins = list(stablecoin_registry.keys())
        total_stablecoin_records = sum(len(v) for v in stablecoin_registry.values())

        # Quote registry: ALL priceable quote assets (stables, wrapped
        # natives, MEXC-priced coins) -> enables all arbitrage paths.
        quote_registry = self._stablecoin_registry_service.build_quote_registry(
            assets, prices,
        )
        if isinstance(quote_registry, dict):
            total_quote_records = sum(len(v) for v in quote_registry.values())
            logger.info(
                "pool_refresh: quote_registry networks=%d records=%d",
                len(quote_registry),
                total_quote_records,
            )

        # B4: Update on-chain factory's registry so it uses current stablecoin data.
        if self._onchain_factory:
            try:
                self._onchain_factory.set_stablecoin_registry(stablecoin_registry)
                if hasattr(self._onchain_factory, "set_quote_registry"):
                    self._onchain_factory.set_quote_registry(quote_registry)
                # Reset per-cycle eth_call probe budget.
                if hasattr(self._onchain_factory, "reset_cycle_budget"):
                    self._onchain_factory.reset_cycle_budget()
            except Exception as exc:
                logger.warning("onchain_registry_update_failed: %s", exc)
        logger.info(
            "pool_refresh: stablecoin_registry networks=%d records=%d",
            len(networks_with_stablecoins),
            total_stablecoin_records,
        )

        # Step 5: Discover pools (deduplicated by network + token_address).
        semaphore = asyncio.Semaphore(settings.DISCOVERY_MAX_CONCURRENCY)

        # Group candidates by (network, contract_address) to avoid duplicate discovery.
        # Same token on same network can appear with multiple quote assets (USDT, USDC).
        discovery_groups: dict[tuple[str, str], list[str]] = {}
        for token_info in candidates:
            _asset, network, contract_address, quote_asset = token_info
            key = (network, contract_address)
            if key not in discovery_groups:
                discovery_groups[key] = []
            discovery_groups[key].append(quote_asset)

        logger.info(
            "pool_refresh: candidates=%d unique_tokens=%d",
            len(candidates),
            len(discovery_groups),
        )

        async def discover_token(network: str, token_address: str):
            async with semaphore:
                quote_records = (
                    self._stablecoin_registry_service
                    .quote_records_for_network(quote_registry, network)
                )
                if not quote_records:
                    # Fallback: plain stablecoin address set.
                    quote_records = (
                        self._stablecoin_registry_service
                        .stablecoin_addresses_for_network(stablecoin_registry, network)
                    )
                if not quote_records:
                    return []

                return await self._pool_discovery_service.discover_and_filter_pools(
                    network=network,
                    token_address=token_address,
                    quote_records=quote_records,
                )

        # Discover once per unique (network, token_address) with live %.
        discovery_keys = list(discovery_groups.keys())
        total_keys = len(discovery_keys)
        write_refresh_status(
            phase="discovery", phase_pct=0, done=0, total=total_keys,
            message=f"discovering unique_tokens={total_keys}",
        )

        async def _discover_one(key: tuple[str, str]):
            try:
                pools = await discover_token(key[0], key[1])
                return key, pools, None
            except Exception as exc:  # noqa: BLE001 — gather-equivalent
                return key, [], exc

        now_ts = int(time.time())
        pool_count = 0
        discovery_errors = 0
        discovered_pools: list = []
        seen_pool_keys: set = set()
        failed_keys: set = set()
        done_n = 0
        last_log_n = 0

        tasks = [asyncio.create_task(_discover_one(k)) for k in discovery_keys]
        for fut in asyncio.as_completed(tasks):
            key, result, err = await fut
            done_n += 1
            if err is not None:
                discovery_errors += 1
                failed_keys.add(key)
                logger.warning("discovery_failed %s/%s: %s", key[0], key[1][:10], err)
            else:
                for pool in result:
                    dedup_key = (pool.network, pool.pool_address, pool.token_address)
                    if dedup_key in seen_pool_keys:
                        continue
                    seen_pool_keys.add(dedup_key)
                    discovered_pools.append(pool)
                pool_count += len(result)

            phase_pct = (100.0 * done_n / total_keys) if total_keys else 100.0
            if done_n == total_keys or done_n - last_log_n >= 25 or done_n <= 3:
                last_log_n = done_n
                write_refresh_status(
                    phase="discovery",
                    phase_pct=phase_pct,
                    done=done_n,
                    total=total_keys,
                    pools=pool_count,
                    errors=discovery_errors,
                    message=f"discovery {done_n}/{total_keys} ({phase_pct:.0f}%)",
                )
                if done_n == total_keys or done_n % 100 == 0:
                    logger.info(
                        "pool_refresh: discovery_progress %d/%d (%.1f%%) pools=%d errors=%d",
                        done_n,
                        total_keys,
                        phase_pct,
                        pool_count,
                        discovery_errors,
                    )

        logger.info(
            "pool_refresh: discovery_complete pools=%d errors=%d candidates=%d unique=%d",
            pool_count,
            discovery_errors,
            len(candidates),
            len(discovery_keys),
        )
        write_refresh_status(
            phase="discovery", phase_pct=100, done=total_keys, total=total_keys,
            pools=pool_count, errors=discovery_errors, message="discovery_complete",
        )

        # Step 5b: Detect pool versions in parallel BEFORE the DB transaction.
        # Sequential detection inside BEGIN IMMEDIATE stalled refresh 30+ min
        # at >1000 pools and blocked all other DB writers.
        if self._pool_detector and discovered_pools:
            det_limit = settings.RPC_MAX_CONCURRENCY
            det_sem = asyncio.Semaphore(det_limit if isinstance(det_limit, int) else 20)
            det_cache: dict[tuple[str, str], str] = {}
            det_total = len(discovered_pools)
            det_done = 0
            write_refresh_status(
                phase="version_detection", phase_pct=0, done=0, total=det_total,
                pools=pool_count, errors=discovery_errors,
                message="detecting_pool_versions",
            )

            async def detect_pool_version(pool) -> None:
                nonlocal det_done
                if pool.pool_version:
                    det_done += 1
                    return
                cache_key = (pool.network, pool.pool_address)
                if cache_key in det_cache:
                    pool.pool_version = det_cache[cache_key]
                    det_done += 1
                    return
                async with det_sem:
                    try:
                        detected = await self._pool_detector.detect_version(
                            network=pool.network,
                            pool_address=pool.pool_address,
                        )
                    except Exception as exc:
                        logger.debug("pool_version_detect_failed: %s", exc)
                        det_done += 1
                        return
                    if detected:
                        det_cache[cache_key] = detected
                        pool.pool_version = detected
                det_done += 1
                if det_done % 50 == 0 or det_done == det_total:
                    write_refresh_status(
                        phase="version_detection",
                        phase_pct=(100.0 * det_done / det_total) if det_total else 100,
                        done=det_done,
                        total=det_total,
                        pools=pool_count,
                        errors=discovery_errors,
                    )

            det_t0 = time.time()
            await asyncio.gather(
                *(detect_pool_version(p) for p in discovered_pools)
            )
            logger.info(
                "pool_refresh: version_detection pools=%d sec=%.1f",
                len(discovered_pools),
                time.time() - det_t0,
            )
            write_refresh_status(
                phase="version_detection", phase_pct=100, done=det_total, total=det_total,
                pools=pool_count, errors=discovery_errors,
            )

        # D5: refuse to persist pools with unknown version — the scanner
        # cannot pick a correct adapter for them and produces garbage quotes.
        before_ver_filter = len(discovered_pools)
        discovered_pools = [p for p in discovered_pools if p.pool_version]
        if before_ver_filter != len(discovered_pools):
            logger.warning(
                "pool_refresh: empty_pool_version_dropped %d of %d",
                before_ver_filter - len(discovered_pools), before_ver_filter,
            )

        # Step 6: Write everything to active DB in a single transaction.
        active_db_path = self._active_db_path
        conn = None
        refresh_log_id = None
        write_refresh_status(
            phase="db_write", phase_pct=0, pools=len(discovered_pools),
            errors=discovery_errors, message="writing_db",
        )
        try:
            conn = sqlite3.connect(active_db_path)
            conn.execute("PRAGMA busy_timeout=15000;")

            # A9: Create backup before modifying data (file copy, no DB txn).
            if self._backup_manager:
                try:
                    self._backup_manager.create_backup()
                except Exception as exc:
                    logger.error("backup_failed_aborting_refresh: %s", exc)
                    write_refresh_status(
                        phase="error", message=f"backup_failed: {exc}", running=False,
                    )
                    return {
                        "error": f"backup_failed: {exc}",
                        "pools_count": 0,
                        "assets_count": len(assets),
                        "stablecoins_count": total_stablecoin_records,
                    }

            # Start transaction. All subsequent DB ops are inside it.
            conn.execute("BEGIN IMMEDIATE;")

            # B7: Record refresh start (inside transaction).
            refresh_log_id = conn.execute(
                "INSERT INTO refresh_log (started_at, status, candidate_offset) VALUES (?, 'running', ?)",
                (now_ts, next_offset),
            ).lastrowid

            # A8: Stale pool protection.
            # If discovery returned 0 pools but DB has pools, keep the old ones.
            skip_pools_delete = False
            old_pools_count = conn.execute(
                "SELECT COUNT(*) FROM pools;"
            ).fetchone()[0]
            if old_pools_count > 0 and len(discovered_pools) == 0:
                logger.warning(
                    "pool_refresh: no_pools_found_keeping_%d_stale_pools",
                    old_pools_count,
                )
                skip_pools_delete = True

            # Merge semantics (pagination-safe): refresh only tokens from the
            # current batch, keep pools discovered in previous cycles, drop
            # rows older than the stale grace period. A full "DELETE FROM
            # pools" here wiped pools of all other paginated batches.
            if not skip_pools_delete:
                for key_d in discovery_keys:
                    if key_d in failed_keys:
                        continue  # Discovery failed — keep this token's old pools.
                    conn.execute(
                        "DELETE FROM pools WHERE network=? AND LOWER(token_address)=?",
                        (key_d[0], key_d[1].lower()),
                    )
                grace_sec = settings.STALE_POOL_GRACE_SEC
                if isinstance(grace_sec, int):
                    conn.execute(
                        "DELETE FROM pools WHERE updated_at < ?",
                        (now_ts - grace_sec,),
                    )
            conn.execute("DELETE FROM mexc_assets;")
            conn.execute("DELETE FROM stablecoins;")

            # Insert discovered pools (skip if stale pools kept).
            if not skip_pools_delete:
                for pool in discovered_pools:
                    conn.execute(
                    """INSERT OR REPLACE INTO pools
                    (network, token_address, stablecoin_address, pool_address,
                     dex, sources, pool_version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        pool.network,
                        pool.token_address,
                        pool.stablecoin_address,
                        pool.pool_address,
                        pool.dex or "",
                        ",".join(sorted(pool.sources)),
                        pool.pool_version or "",
                        now_ts,
                        now_ts,
                    ),
                )

            # Insert MEXC assets.
            for asset in assets:
                for net in asset.networks:
                    conn.execute(
                        """INSERT OR REPLACE INTO mexc_assets
                        (coin, name, network, contract_address, deposit_enable,
                         withdraw_enable, withdraw_fee, withdraw_min,
                         withdraw_max, min_confirm, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            asset.coin,
                            asset.name,
                            net.network_normalized,
                            net.contract_address,
                            1 if net.deposit_enable else 0,
                            1 if net.withdraw_enable else 0,
                            str(net.withdraw_fee) if net.withdraw_fee else None,
                            str(net.withdraw_min) if net.withdraw_min else None,
                            str(net.withdraw_max) if net.withdraw_max else None,
                            net.min_confirm,
                            now_ts,
                        ),
                    )

            # Insert stablecoins.
            for _network_name, records in stablecoin_registry.items():
                for record in records:
                    conn.execute(
                        """INSERT OR REPLACE INTO stablecoins
                        (coin, network, address, deposit_enable, withdraw_enable,
                         withdraw_fee, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            record.coin,
                            record.network,
                            record.address,
                            1 if record.deposit_enable else 0,
                            1 if record.withdraw_enable else 0,
                            str(record.withdraw_fee) if record.withdraw_fee else None,
                            now_ts,
                        ),
                    )

            # Verify pools were written.
            written = conn.execute("SELECT COUNT(*) FROM pools;").fetchone()[0]

            # B7: Update refresh_log with completion status.
            status = "completed"
            if skip_pools_delete and old_pools_count > 0:
                status = "completed_with_stale_pools"
            conn.execute(
                """UPDATE refresh_log SET finished_at=?, status=?, error=?,
                   pools_count=?, mexc_assets_count=?, stablecoins_count=?
                 WHERE id=?""",
                (int(time.time()), status, None, written, len(assets),
                 total_stablecoin_records, refresh_log_id),
            )

            conn.commit()

            # Write pool list to JSON cache file for the scanner.
            if written > 0:
                pool_rows = conn.execute(
                    """SELECT p.network, p.token_address, p.stablecoin_address,
                              p.pool_address, p.dex, ma.coin as token_coin,
                              sc.coin as stablecoin_coin,
                              ma.withdraw_fee,
                              p.pool_version,
                              sc.withdraw_fee as stablecoin_withdraw_fee,
                              ma.deposit_enable, ma.withdraw_enable,
                              ma.min_confirm
                       FROM pools p
                       LEFT JOIN mexc_assets ma
                           ON ma.contract_address = p.token_address
                           AND ma.network = p.network
                       LEFT JOIN stablecoins sc
                           ON sc.address = p.stablecoin_address
                           AND sc.network = p.network"""
                ).fetchall()

                # Batch decimals fetch via Multicall3 (10-50x faster than sequential).
                # Group unique addresses by network, fetch all in one batch per network.
                decimals_map: dict[tuple[str, str], int] = {}  # (network, addr) -> decimals
                write_refresh_status(
                    phase="decimals", phase_pct=0, pools=written,
                    message="fetching_decimals",
                )
                if self._token_meta_service:
                    # Collect unique addresses per network.
                    addrs_by_network: dict[str, set[str]] = {}
                    for r in pool_rows:
                        network, token_addr, stable_addr = r[0], r[1], r[2]
                        if network not in addrs_by_network:
                            addrs_by_network[network] = set()
                        addrs_by_network[network].add(token_addr.lower())
                        addrs_by_network[network].add(stable_addr.lower())

                    # Batch fetch per network (one multicall per network).
                    for network, addrs in addrs_by_network.items():
                        try:
                            batch_result = await self._token_meta_service.get_decimals_batch(
                                network, list(addrs)
                            )
                            for addr_lower, dec in batch_result.items():
                                decimals_map[(network, addr_lower)] = dec
                        except Exception as exc:
                            logger.warning("batch_decimals_failed %s: %s", network, exc)

                def _get_decimals(network: str, addr: str) -> int | None:
                    # Fail-closed (D1/D2/D16): no silent default 18 — a pool
                    # with unknown decimals is skipped instead of producing
                    # wrong-by-10^12 quotes.
                    return decimals_map.get((network, addr.lower()))

                pool_dicts = []
                skipped_no_decimals = 0
                for r in pool_rows:
                    network_r = r[0]
                    token_dec = _get_decimals(r[0], r[1])
                    stable_dec = _get_decimals(r[0], r[2])
                    if token_dec is None or stable_dec is None:
                        skipped_no_decimals += 1
                        continue
                    quote_addr = (r[2] or "").lower()
                    # Quote metadata from the broad quote registry (works for
                    # both fresh and stale pools; wrapped natives are not in
                    # the stablecoins table so the SQL join yields NULL).
                    qrec = None
                    if isinstance(quote_registry, dict):
                        qrec = (quote_registry.get(network_r) or {}).get(quote_addr)
                    quote_coin = qrec.coin if qrec else (r[6] or "")
                    pool_dicts.append({
                        "network": network_r,
                        "token_address": r[1],
                        "stablecoin_address": r[2],
                        "pool_address": r[3],
                        "dex": r[4] or "",
                        "token_coin": r[5] or "",
                        "stablecoin_coin": r[6] or quote_coin,
                        "mexc_withdraw_fee": r[7] if len(r) > 7 and r[7] else None,
                        "pool_version": r[8] if len(r) > 8 else "",
                        "stablecoin_withdraw_fee": (
                            r[9] if len(r) > 9 and r[9] else (
                                str(qrec.withdraw_fee)
                                if qrec and qrec.withdraw_fee is not None
                                else None
                            )
                        ),
                        "quote_coin": quote_coin,
                        "quote_is_stable": qrec.is_stable if qrec else True,
                        "quote_price_coin": qrec.price_coin if qrec else None,
                        # Direction gating: A needs token deposit, B needs
                        # token withdraw. NULL join (unknown MEXC status) is
                        # fail-closed (D17): don't trade what we can't verify.
                        "token_deposit_enable": (
                            bool(r[10]) if len(r) > 10 and r[10] is not None else False
                        ),
                        "token_withdraw_enable": (
                            bool(r[11]) if len(r) > 11 and r[11] is not None else False
                        ),
                        # Confirmations required for MEXC deposit credit
                        # (transfer-ETA risk gate for the executor).
                        "token_min_confirm": (
                            int(r[12]) if len(r) > 12 and r[12] is not None else None
                        ),
                        "token_decimals": token_dec,
                        "stablecoin_decimals": stable_dec,
                    })
                if skipped_no_decimals:
                    logger.warning(
                        "pools_cache: decimals_missing_skipped=%d",
                        skipped_no_decimals,
                    )
                cache_path = Path(__file__).resolve().parent.parent / "data" / "pools_cache.json"
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                # Write to tmp file first, then atomically rename.
                # This avoids Windows race conditions where the scanner
                # reads the file while it's being truncated by open("w").
                tmp_path = cache_path.with_suffix(".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(pool_dicts, f, default=str)
                os.replace(str(tmp_path), str(cache_path))
                logger.info(
                    "pools_cache_written: path=%s count=%d",
                    cache_path,
                    len(pool_dicts),
                )
                write_refresh_status(
                    phase="cache_write", phase_pct=100, pools=len(pool_dicts),
                    done=len(pool_dicts), total=len(pool_dicts),
                    message="pools_cache_written",
                )

            conn.close()
            conn = None

            logger.info(
                "pool_refresh_direct_commit: pools=%d assets=%d stablecoins=%d",
                written,
                len(assets),
                total_stablecoin_records,
            )
            timer.set_items_total(written)
            timer.add_success(written)
            write_refresh_status(
                phase="done", phase_pct=100, pools=written,
                done=written, total=written,
                message=f"completed pools={written}",
                running=False,
            )

            return {
                "pools_count": written,
                "assets_count": len(assets),
                "stablecoins_count": total_stablecoin_records,
            }

        except Exception as exc:
            if conn:
                try:
                    # B7: Record failure in refresh_log if we have an id.
                    if refresh_log_id is not None:
                        conn.execute(
                            """UPDATE refresh_log SET finished_at=?, status='failed', error=?
                             WHERE id=?""",
                            (int(time.time()), str(exc)[:1000], refresh_log_id),
                        )
                        conn.commit()
                except Exception:
                    pass
                try:
                    conn.rollback()
                except Exception:
                    pass
                conn.close()
            logger.error("pool_refresh_direct_failed: %s", exc, exc_info=True)
            write_refresh_status(
                phase="error", message=str(exc)[:200], running=False,
            )
            timer.add_failure(1)
            return {
                "pools_count": 0,
                "assets_count": len(assets),
                "stablecoins_count": total_stablecoin_records,
                "error": str(exc),
            }
