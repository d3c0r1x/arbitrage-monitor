"""Live refresh test: run one pool refresh cycle, print pool stats.

Usage: python scripts/live_refresh_test.py
"""

import asyncio
import collections
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import ArbitrageMonitor  # noqa: E402
from utils.logging import setup_logging  # noqa: E402


async def run() -> None:
    setup_logging()
    t0 = time.time()
    monitor = ArbitrageMonitor()
    await monitor.initialize()
    print(f"[{time.time()-t0:.1f}s] initialized", flush=True)

    result = await monitor._refresh_task.run_refresh(
        pre_fetched_assets=monitor._pre_fetched_assets,
    )
    print(f"[{time.time()-t0:.1f}s] refresh_result: {result}", flush=True)

    cache_path = Path(__file__).resolve().parent.parent / "data" / "pools_cache.json"
    if cache_path.exists():
        with open(cache_path, encoding="utf-8") as f:
            pools = json.load(f)
        by_net = collections.Counter(p["network"] for p in pools)
        by_quote = collections.Counter(
            p.get("quote_coin") or p.get("stablecoin_coin") or "?" for p in pools
        )
        stable = sum(1 for p in pools if p.get("quote_is_stable", True))
        print(f"TOTAL_POOLS: {len(pools)}")
        print(f"BY_NETWORK: {dict(by_net)}")
        print(f"BY_QUOTE_TOP15: {dict(by_quote.most_common(15))}")
        print(f"STABLE_QUOTED: {stable}  NONSTABLE_QUOTED: {len(pools) - stable}")
    else:
        print("NO_CACHE_FILE")

    await monitor.shutdown()


if __name__ == "__main__":
    asyncio.run(run())
