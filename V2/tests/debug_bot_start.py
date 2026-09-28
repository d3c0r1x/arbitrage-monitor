#!/usr/bin/env python3
"""Minimal test: replicate the bot's start() sequence and see where it hangs."""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv()

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)

import logging
from pathlib import Path

from storage.repository import StateRepository

from clients.http_client import create_http_client
from clients.mexc_client import MexcClient
from clients.rpc_client import RpcClient
from config.networks import resolve_rpc_url
from discovery.dexscreener_source import DexScreenerSource
from discovery.geckoterminal_source import GeckoTerminalSource
from discovery.onchain_factory_source import OnchainFactorySource
from discovery.source_manager import SourceManager
from metrics.health import SourceHealthTracker
from scanner.pool_refresh_task import PoolRefreshTask
from services.mexc_asset_service import MexcAssetService
from services.pool_discovery_service import PoolDiscoveryService
from services.price_service import PriceService
from services.stablecoin_registry_service import StablecoinRegistryService
from storage.backup_manager import BackupManager
from storage.database import create_connection, initialize_schema
from utils.rate_limiter import SimpleRateLimiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def rpc_factory(network):
    rpc_url = resolve_rpc_url(network)
    if not rpc_url:
        return None
    return RpcClient(rpc_url=rpc_url, rate_limiter=SimpleRateLimiter(500, 8))


async def main():
    t0 = time.time()
    print(f"[{time.time()-t0:.1f}] START", flush=True)

    project_root = Path(__file__).resolve().parent.parent
    active_db_path = project_root / "data" / "state" / "active.sqlite3"
    temp_db_path = project_root / "data" / "state" / "active.new.sqlite3"
    backup_dir = project_root / "data" / "state" / "backups"

    (project_root / "data" / "state" / "backups").mkdir(parents=True, exist_ok=True)

    conn = create_connection(active_db_path)
    initialize_schema(conn)
    conn.close()
    print(f"[{time.time()-t0:.1f}] DB schema ready", flush=True)

    http_client = await create_http_client()
    mexc_client = MexcClient(http_client)
    price_service = PriceService(mexc_client, cache_ttl_sec=10)
    mexc_asset_service = MexcAssetService(mexc_client)
    stablecoin_registry_service = StablecoinRegistryService()

    health_tracker = SourceHealthTracker(db_path=active_db_path)

    dexscreener_source = DexScreenerSource(http_client)
    geckoterminal_source = GeckoTerminalSource(http_client)

    onchain_factory = OnchainFactorySource(
        rpc_client_factory=rpc_factory,
        stablecoin_registry_service=stablecoin_registry_service,
    )

    sources = {
        "dexscreener": dexscreener_source,
        "geckoterminal": geckoterminal_source,
        "onchain_factory": onchain_factory,
    }

    source_manager = SourceManager(sources=sources, health_tracker=health_tracker)
    pool_discovery = PoolDiscoveryService(
        source_manager=source_manager,
        stablecoin_registry_service=stablecoin_registry_service,
    )

    backup_manager = BackupManager(
        active_db_path=active_db_path,
        backup_dir=backup_dir,
        max_backups=5,
    )

    state_repository = StateRepository(
        active_db_path=active_db_path,
        temp_db_path=temp_db_path,
        backup_manager=backup_manager,
    )

    refresh_task = PoolRefreshTask(
        mexc_client=mexc_client,
        price_service=price_service,
        mexc_asset_service=mexc_asset_service,
        stablecoin_registry_service=stablecoin_registry_service,
        source_manager=source_manager,
        pool_discovery_service=pool_discovery,
        state_repository=state_repository,
    )

    # Fetch assets (like initialize() does)
    print(f"[{time.time()-t0:.1f}] Fetching MEXC assets...", flush=True)
    assets = await mexc_asset_service.fetch_and_parse_assets()
    print(f"[{time.time()-t0:.1f}] Assets: {len(assets)}", flush=True)

    stablecoin_registry = stablecoin_registry_service.build_registry(assets)
    onchain_factory.set_stablecoin_registry(stablecoin_registry)

    # Now call run_refresh with pre_fetched_assets
    print(f"[{time.time()-t0:.1f}] Calling run_refresh(pre_fetched_assets=...) ...", flush=True)
    try:
        result = await refresh_task.run_refresh(pre_fetched_assets=assets)
        print(f"[{time.time()-t0:.1f}] run_refresh DONE: {result}", flush=True)
    except Exception as e:
        print(f"[{time.time()-t0:.1f}] run_refresh FAILED: {e}", flush=True)

    # Check DB
    import sqlite3
    conn = sqlite3.connect(active_db_path)
    for table in ["pools", "mexc_assets", "stablecoins", "source_health"]:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
            print(f"  {table}: {count}", flush=True)
        except Exception as e:
            print(f"  {table}: ERROR {e}", flush=True)
    conn.close()

    print(f"[{time.time()-t0:.1f}] DONE", flush=True)
    await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
