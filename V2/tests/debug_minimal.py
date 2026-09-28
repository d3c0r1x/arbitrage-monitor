#!/usr/bin/env python3
"""Minimal: directly test PoolRefreshTask standalone."""
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from storage.repository import StateRepository

from clients.http_client import create_http_client
from clients.mexc_client import MexcClient
from clients.rpc_client import RpcClient
from config.networks import resolve_rpc_url
from discovery.dexscreener_source import DexScreenerSource
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


def rpc_factory(network):
    url = resolve_rpc_url(network)
    if not url:
        return None
    return RpcClient(rpc_url=url, rate_limiter=SimpleRateLimiter(500, 8))

async def main():
    t0 = time.time()
    root = Path(__file__).resolve().parent.parent
    active_db = root / "data" / "state" / "active.sqlite3"
    temp_db = root / "data" / "state" / "active.new.sqlite3"
    bup = root / "data" / "state" / "backups"
    bup.mkdir(parents=True, exist_ok=True)

    conn = create_connection(active_db)
    initialize_schema(conn)
    conn.close()
    print(f"[{time.time()-t0:.1f}] schema ready", flush=True)

    http = await create_http_client()
    mexc = MexcClient(http)
    ps = PriceService(mexc, cache_ttl_sec=10)
    mas = MexcAssetService(mexc)
    srs = StablecoinRegistryService()
    ht = SourceHealthTracker(db_path=str(active_db))

    ds = DexScreenerSource(http)
    of = OnchainFactorySource(rpc_client_factory=rpc_factory, stablecoin_registry_service=srs)
    sm = SourceManager(sources={"dexscreener": ds, "onchain_factory": of}, health_tracker=ht)
    pd = PoolDiscoveryService(source_manager=sm, stablecoin_registry_service=srs)

    bm = BackupManager(active_db_path=str(active_db), backup_dir=str(bup), max_backups=5)
    sr = StateRepository(active_db_path=str(active_db), temp_db_path=str(temp_db), backup_manager=bm)

    rt = PoolRefreshTask(
        mexc_client=mexc,
        price_service=ps,
        mexc_asset_service=mas,
        stablecoin_registry_service=srs,
        source_manager=sm,
        pool_discovery_service=pd,
        state_repository=sr,
    )

    print(f"[{time.time()-t0:.1f}] fetch assets...", flush=True)
    assets = await mas.fetch_and_parse_assets()
    print(f"[{time.time()-t0:.1f}] assets={len(assets)}", flush=True)

    reg = srs.build_registry(assets)
    of.set_stablecoin_registry(reg)

    print(f"[{time.time()-t0:.1f}] calling run_refresh...", flush=True)
    try:
        result = await rt.run_refresh(pre_fetched_assets=assets)
        print(f"[{time.time()-t0:.1f}] DONE: {result}", flush=True)
    except Exception as e:
        print(f"[{time.time()-t0:.1f}] CRASHED: {e}", flush=True)
        import traceback
        traceback.print_exc()

    import sqlite3
    c = sqlite3.connect(str(active_db))
    for tbl in ["pools", "mexc_assets", "stablecoins"]:
        try:
            cnt = c.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0]
            print(f"  {tbl}: {cnt}", flush=True)
        except Exception as e:
            print(f"  {tbl}: {e}", flush=True)
    c.close()

    print(f"[{time.time()-t0:.1f}] DONE", flush=True)
    await http.aclose()

asyncio.run(main())
