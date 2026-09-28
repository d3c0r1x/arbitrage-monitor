"""
Benchmark: current bot DEX quotes vs PancakeSwap Smart Router + Price API.

Usage (from repo root, with .env loaded):
  python tools/bench_pcs_vs_bot.py --limit 25 --usd 100

Outputs:
  data/bench_pcs_vs_bot.json
  docs/superpowers/plans/2026-07-29-pcs-sdk-bench-results.md (summary)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
USDT_BSC = "0x55d398326f99059ff775485246999027b3197955"
WBNB_BSC = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"


def _pctile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round((p / 100) * (len(s) - 1)))))
    return s[i]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--usd", type=float, default=100.0)
    parser.add_argument("--prefer", choices=["wbnb", "usdt", "any"], default="any")
    args = parser.parse_args()

    from config.closing_pools import get_closing_pool
    from config.networks import resolve_rpc_urls
    from dex.adapter_factory import AdapterFactory
    from clients.web3_manager import Web3Manager
    from services.price_service import PriceService
    from clients.http_client import create_http_client
    from clients.mexc_client import MexcClient
    from tools.pcs_sidecar_session import PcsSidecar

    pools = json.loads((ROOT / "data" / "pools_cache.json").read_text(encoding="utf-8"))
    bsc = [
        p
        for p in pools
        if p.get("network") == "BSC"
        and str(p.get("dex", "")).startswith("pancakeswap")
        and p.get("token_address")
        and p.get("stablecoin_address")
    ]

    # Prefer liquid quote assets; one pool per token_coin.
    def rank(p: dict) -> tuple:
        q = (p.get("stablecoin_address") or "").lower()
        prefer = 0 if q == USDT_BSC else (1 if q == WBNB_BSC else 2)
        if args.prefer == "wbnb":
            prefer = 0 if q == WBNB_BSC else 1
        elif args.prefer == "usdt":
            prefer = 0 if q == USDT_BSC else 1
        ver = 0 if str(p.get("pool_version") or "").startswith("v2") else 1
        return (prefer, ver, p.get("token_coin") or "")

    bsc.sort(key=rank)
    seen: set[str] = set()
    sample: list[dict] = []
    for p in bsc:
        coin = (p.get("token_coin") or "").upper()
        if not coin or coin in seen:
            continue
        seen.add(coin)
        sample.append(p)
        if len(sample) >= args.limit:
            break

    sidecar = PcsSidecar()
    ping = sidecar.start()
    ping2 = sidecar.ping()
    rpc_urls = resolve_rpc_urls("BSC")
    if not rpc_urls:
        raise SystemExit("no BSC RPC")

    http = await create_http_client()
    mexc = MexcClient(http)
    price_svc = PriceService(mexc)
    await price_svc.refresh_all_prices()

    w3m = Web3Manager()
    adapters = AdapterFactory(web3_manager=w3m)

    rows: list[dict] = []
    bot_ms: list[float] = []
    pcs_ms: list[float] = []
    bot_ok = pcs_ok = price_ok = 0
    bot_edge = pcs_edge = 0
    deltas: list[float] = []

    usd = Decimal(str(args.usd))

    try:
        for i, pool in enumerate(sample):
            coin = pool["token_coin"]
            token = pool["token_address"].lower()
            quote = pool["stablecoin_address"].lower()
            tdec = int(pool.get("token_decimals") or 18)
            qdec = int(pool.get("stablecoin_decimals") or 18)
            quote_is_stable = bool(pool.get("quote_is_stable", True))

            mexc_px = price_svc.get_price(coin, "USDT") or Decimal("0")
            if mexc_px <= 0:
                rows.append({"token": coin, "skip": "no_mexc_price"})
                continue

            tokens = usd / mexc_px
            amount_in = int(tokens * (Decimal(10) ** tdec))

            adapter = adapters.get_adapter(
                "BSC", pool.get("dex") or "", pool_version=pool.get("pool_version") or ""
            )

            bot_out = Decimal("0")
            bot_settle = Decimal("0")
            bot_err = None
            t0 = time.perf_counter()
            try:
                if adapter is None:
                    raise RuntimeError("no_adapter")
                bot_out = await adapter.quote_exact_input(
                    network="BSC",
                    pool_address=pool["pool_address"],
                    token_in=token,
                    token_out=quote,
                    amount_in=amount_in,
                )
                bot_settle = bot_out
                if not quote_is_stable and quote == WBNB_BSC:
                    closing = get_closing_pool("BSC", quote)
                    if closing is not None:
                        close_ad = adapters.get_adapter(
                            "BSC", closing.dex_id, pool_version=closing.pool_version
                        )
                        if close_ad is not None and bot_out > 0:
                            bot_settle = await close_ad.quote_exact_input(
                                network="BSC",
                                pool_address=closing.pool_address,
                                token_in=closing.token_in,
                                token_out=closing.token_out,
                                amount_in=int(bot_out),
                            )
                            qdec = closing.token_out_decimals
                bot_ok += 1
            except Exception as exc:
                bot_err = str(exc)
            bot_lat = (time.perf_counter() - t0) * 1000
            bot_ms.append(bot_lat)

            settle_out_addr = USDT_BSC
            settle_dec = 18
            pcs = sidecar.quote(
                rpc=rpc_urls[0],
                token_in=token,
                token_out=settle_out_addr,
                amount_in=amount_in,
                chain_id=56,
                decimals_in=tdec,
                decimals_out=settle_dec,
                max_hops=2,
                max_splits=1,
                skip_v3=True,
            )
            pcs_lat = float(pcs.get("wall_ms") or 0)
            pcs_ms.append(pcs_lat)
            pcs_out = Decimal("0")
            if pcs.get("ok") and pcs.get("amountOut"):
                pcs_out = Decimal(str(pcs["amountOut"]))
                pcs_ok += 1

            pr = sidecar.price(56, [token])
            pcs_mark = 0.0
            if pr.get("ok"):
                pcs_mark = float((pr.get("prices") or {}).get(token) or 0)
                if pcs_mark > 0:
                    price_ok += 1

            bot_usd = (
                (bot_settle / (Decimal(10) ** qdec)) if bot_settle > 0 else Decimal("0")
            )
            pcs_usd = (
                (pcs_out / (Decimal(10) ** settle_dec)) if pcs_out > 0 else Decimal("0")
            )

            bot_net_pct = ((bot_usd - usd) / usd * 100) if bot_usd > 0 else Decimal("0")
            pcs_net_pct = ((pcs_usd - usd) / usd * 100) if pcs_usd > 0 else Decimal("0")
            if bot_usd > usd * Decimal("1.005"):
                bot_edge += 1
            if pcs_usd > usd * Decimal("1.005"):
                pcs_edge += 1

            delta_pct = None
            if bot_usd > 0 and pcs_usd > 0:
                delta_pct = float((pcs_usd - bot_usd) / bot_usd * 100)
                deltas.append(delta_pct)

            row = {
                "i": i,
                "token": coin,
                "dex": pool.get("dex"),
                "pool": pool.get("pool_address"),
                "quote": pool.get("quote_coin"),
                "mexc_px": float(mexc_px),
                "pcs_mark_usd": pcs_mark,
                "mark_vs_mexc_pct": (
                    (pcs_mark / float(mexc_px) - 1) * 100 if pcs_mark and mexc_px else None
                ),
                "amount_in_tokens": float(tokens),
                "bot_settle_usd": float(bot_usd),
                "pcs_settle_usd": float(pcs_usd),
                "bot_gross_pct": float(bot_net_pct),
                "pcs_gross_pct": float(pcs_net_pct),
                "delta_pcs_vs_bot_pct": delta_pct,
                "bot_ms": round(bot_lat, 1),
                "pcs_ms": round(pcs_lat, 1),
                "pcs_engine": pcs.get("engine"),
                "pcs_latency": pcs.get("latency_ms"),
                "pcs_pools": pcs.get("pools_candidates"),
                "pcs_error": None if pcs.get("ok") else pcs.get("error"),
                "bot_error": bot_err,
            }
            rows.append(row)
            print(
                f"[{i+1}/{len(sample)}] {coin:8} bot=${float(bot_usd):7.2f} "
                f"pcs=${float(pcs_usd):7.2f} bot_ms={bot_lat:6.0f} pcs_ms={pcs_lat:6.0f} "
                f"eng={pcs.get('engine') or '-'} err={pcs.get('error') or bot_err or '-'}"
            )
    finally:
        sidecar.stop()
        await http.aclose()

    summary = {
        "sample": len(sample),
        "usd_size": float(usd),
        "sidecar_hello": ping,
        "sidecar_ping": ping2,
        "bot_ok": bot_ok,
        "pcs_ok": pcs_ok,
        "price_api_ok": price_ok,
        "bot_edge_gt_0_5pct": bot_edge,
        "pcs_edge_gt_0_5pct": pcs_edge,
        "latency_bot_ms": {
            "p50": round(_pctile(bot_ms, 50), 1),
            "p95": round(_pctile(bot_ms, 95), 1),
            "mean": round(statistics.mean(bot_ms), 1) if bot_ms else 0,
        },
        "latency_pcs_ms": {
            "p50": round(_pctile(pcs_ms, 50), 1),
            "p95": round(_pctile(pcs_ms, 95), 1),
            "mean": round(statistics.mean(pcs_ms), 1) if pcs_ms else 0,
        },
        "delta_pcs_vs_bot_pct": {
            "p50": round(_pctile(deltas, 50), 3) if deltas else None,
            "mean": round(statistics.mean(deltas), 3) if deltas else None,
            "n": len(deltas),
        },
        "notes": {
            "pcs_engine": "InfinityRouter preferred, SmartRouter fallback",
            "skip_v3": True,
            "price_api": "wallet-api.pancakeswap.com (price-api-sdk equivalent HTTP)",
        },
    }

    out = {"summary": summary, "rows": rows}
    out_path = ROOT / "data" / "bench_pcs_vs_bot.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    md = ROOT / "docs" / "superpowers" / "plans" / "2026-07-29-pcs-sdk-bench-results.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(
        "\n".join(
            [
                "# PCS Smart Router / Price API — bench results",
                "",
                f"**Sample:** {summary['sample']} BSC Pancake pools, size ${summary['usd_size']}",
                "",
                "## Summary",
                "",
                "```json",
                json.dumps(summary, indent=2),
                "```",
                "",
                "## Notes",
                "",
                "- Bot path: single-pool `quote_exact_input` + optional WBNB→USDT closing.",
                "- PCS path: `SmartRouter.getBestTrade` token→USDT (multi-hop/split).",
                "- Price API: `wallet-api.pancakeswap.com/v1/prices/list` (same as price-api-sdk).",
                "- Gross % ignores gas/MEXC fees — opportunity proxy only.",
                "",
                f"Raw: `data/bench_pcs_vs_bot.json`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print(f"wrote {out_path}")
    print(f"wrote {md}")


if __name__ == "__main__":
    asyncio.run(main())
