#!/usr/bin/env python3
"""Diagnostic: step through pool refresh and report each stage."""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "../.env")))

from clients.http_client import create_http_client
from clients.mexc_client import MexcClient
from services.mexc_asset_service import MexcAssetService
from services.price_service import PriceService
from services.stablecoin_registry_service import StablecoinRegistryService


async def main():
    http_client = await create_http_client()
    mexc_client = MexcClient(http_client)
    asset_service = MexcAssetService(mexc_client)
    price_service = PriceService(mexc_client, cache_ttl_sec=10)
    stablecoin_service = StablecoinRegistryService()

    # Step 1: Fetch MEXC assets
    t0 = time.time()
    print(f"[{time.time()-t0:.1f}s] Fetching MEXC assets...")
    assets = await asset_service.fetch_and_parse_assets()
    print(f"[{time.time()-t0:.1f}s] MEXC assets: {len(assets)} total")

    if not assets:
        print("FATAL: No MEXC assets fetched!")
        await http_client.aclose()
        return

    # Step 2: Prices
    print(f"[{time.time()-t0:.1f}s] Refreshing prices...")
    await price_service.refresh_all_prices()
    prices = price_service.get_all_prices_dict()
    print(f"[{time.time()-t0:.1f}s] Prices: {len(prices)} pairs ({len([s for s in prices if s.endswith('USDT')])} USDT, {len([s for s in prices if s.endswith('USDC')])} USDC)")

    # Step 3: Candidates
    print(f"[{time.time()-t0:.1f}s] Selecting candidates...")
    candidates = asset_service.select_candidate_tokens(assets, prices)
    print(f"[{time.time()-t0:.1f}s] Candidates: {len(candidates)} total")
    print(f"    Unique tokens: {len({c[0].coin for c in candidates})}")
    networks = {}
    for _, net, _, _ in candidates:
        networks[net] = networks.get(net, 0) + 1
    print(f"    By network: {networks}")

    if not candidates:
        print("FATAL: No candidates!")
        await http_client.aclose()
        return

    # Step 4: Stablecoin registry
    print(f"[{time.time()-t0:.1f}s] Building stablecoin registry...")
    registry = stablecoin_service.build_registry(assets)
    for net, records in registry.items():
        print(f"    {net}: {len(records)} records")
    print(f"[{time.time()-t0:.1f}s] Stablecoin registry: {len(registry)} networks")

    # Step 5: Test discovery for first 3 candidates
    print(f"\n[{time.time()-t0:.1f}s] Testing discovery for first 3 candidates...")

    from clients.rpc_client import RpcClient
    from config.networks import resolve_rpc_url
    from discovery.dexscreener_source import DexScreenerSource
    from discovery.geckoterminal_source import GeckoTerminalSource
    from discovery.onchain_factory_source import OnchainFactorySource
    from discovery.source_manager import SourceManager
    from metrics.health import SourceHealthTracker
    from services.pool_discovery_service import PoolDiscoveryService
    from utils.rate_limiter import SimpleRateLimiter

    def rpc_factory(network):
        rpc_url = resolve_rpc_url(network)
        if not rpc_url:
            return None
        return RpcClient(rpc_url=rpc_url, rate_limiter=SimpleRateLimiter(500, 8))

    dexscreener = DexScreenerSource(http_client)
    geckoterminal = GeckoTerminalSource(http_client)
    onchain = OnchainFactorySource(
        rpc_client_factory=rpc_factory,
        stablecoin_registry_service=stablecoin_service,
    )
    onchain.set_stablecoin_registry(registry)

    sources = {
        "dexscreener": dexscreener,
        "geckoterminal": geckoterminal,
        "onchain_factory": onchain,
    }

    health = SourceHealthTracker(db_path=":memory:")
    sm = SourceManager(sources=sources, health_tracker=health)
    discovery = PoolDiscoveryService(
        source_manager=sm,
        stablecoin_registry_service=stablecoin_service,
    )

    total_pools = 0
    test_count = min(3, len(candidates))
    for i in range(test_count):
        asset, network, contract_addr, quote = candidates[i]
        stablecoin_addrs = stablecoin_service.stablecoin_addresses_for_network(registry, network)
        # Filter to matching quote asset
        matching = set()
        for addr in stablecoin_addrs:
            for rec in registry.get(network, []):
                if rec.address == addr and rec.coin == quote:
                    matching.add(addr)

        print(f"[{time.time()-t0:.1f}s] Candidate {i+1}: {asset.coin}/{quote} on {network} ({contract_addr[:10]}...) with {len(matching)} stablecoins...")
        try:
            pools = await discovery.discover_and_filter_pools(
                network=network,
                token_address=contract_addr,
                quote_records=matching,
            )
            print(f"    -> {len(pools)} pools found")
            total_pools += len(pools)
        except Exception as e:
            print(f"    -> ERROR: {e}")

    print(f"\n[{time.time()-t0:.1f}s] Total pools from {test_count} candidates: {total_pools}")

    # Check source health
    print(f"\n[{time.time()-t0:.1f}s] Source health:")
    for src in ["dexscreener", "geckoterminal", "onchain_factory"]:
        print(f"    {src}: healthy={health.is_healthy(src)}")

    await http_client.aclose()
    print(f"\n[{time.time()-t0:.1f}s] Done")


if __name__ == "__main__":
    asyncio.run(main())
