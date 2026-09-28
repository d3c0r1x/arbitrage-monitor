#!/usr/bin/env python3
"""
Benchmark script for pool discovery performance.

Usage:
    python scripts/benchmark_discovery.py
"""

import asyncio
import sys
from pathlib import Path

# Ensure project root is in path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clients.http_client import create_http_client
from clients.mexc_client import MexcClient
from metrics.performance import performance_timer
from services.price_service import PriceService


async def main() -> int:
    print("=== Discovery Benchmark ===\n")

    http_client = await create_http_client()
    mexc_client = MexcClient(http_client)
    price_service = PriceService(mexc_client)

    # 1. Measure MEXC capital config fetch.
    async with performance_timer("benchmark_mexc_capital_config") as timer:
        raw_capital = await mexc_client.get_capital_config()
        timer.set_items_total(len(raw_capital))
        print(f"MEXC capital config: {len(raw_capital)} items in {timer.duration_ms:.0f} ms")

    # 2. Measure MEXC price fetch.
    async with performance_timer("benchmark_mexc_prices") as timer:
        raw_prices = await mexc_client.get_all_prices()
        timer.set_items_total(len(raw_prices))
        print(f"MEXC prices: {len(raw_prices)} symbols in {timer.duration_ms:.0f} ms")

    # 3. Parse capital config.
    async with performance_timer("benchmark_parse_capital") as timer:
        assets = mexc_client.parse_capital_config(raw_capital)
        timer.set_items_total(len(assets))
        print(f"Parsed assets: {len(assets)} in {timer.duration_ms:.0f} ms")

    # 4. Candidate token selection.
    async with performance_timer("benchmark_candidate_selection") as timer:
        await price_service.refresh_all_prices()
        prices = price_service.get_all_prices_dict()

        candidates = []
        for asset in assets:
            coin = asset.coin.upper()
            if f"{coin}USDT" not in prices and f"{coin}USDC" not in prices:
                continue
            for net in asset.active_networks():
                if net.contract_address:
                    candidates.append((asset.coin, net.network_normalized, net.contract_address))

        timer.set_items_total(len(candidates))
        print(f"Candidate tokens: {len(candidates)} in {timer.duration_ms:.0f} ms")

    await http_client.aclose()
    print("\n=== Benchmark Complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
