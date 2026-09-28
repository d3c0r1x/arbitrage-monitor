# PCS Smart Router / Price API — bench results

**Sample:** 8 BSC Pancake pools, size $100  
**Date:** 2026-07-29  
**Raw:** `data/bench_pcs_vs_bot.json`

## Verdict

| Component | Integrate into hot path? | Why |
|-----------|--------------------------|-----|
| **Price API** (`wallet-api.pancakeswap.com`) | **Yes** (default ON) | 8/8 ok, ~0.2s, no key |
| **Smart Router / InfinityRouter** | **No** (flag OFF) | p50 ~20s vs bot ~2.5s; 5/8 routes; Alchemy 429 |

## Summary numbers

```json
{
  "bot_ok": 8,
  "pcs_ok": 5,
  "price_api_ok": 8,
  "latency_bot_ms": { "p50": 2533.8, "p95": 5507.5, "mean": 2832.8 },
  "latency_pcs_ms": { "p50": 20093.9, "p95": 65360.8, "mean": 19140.4 },
  "bot_edge_gt_0_5pct": 0,
  "pcs_edge_gt_0_5pct": 0
}
```

Warm InfinityRouter quotes can drop to ~0.3–0.9s, but candidate-pool discovery + RPC timeouts dominate; under CU pressure quotes fail with 429 / `no_route`.

## What was shipped

1. **Sidecar** `tools/pcs_sidecar/` — `@pancakeswap/smart-router@7.5.4` + InfinityRouter; persistent `server.js`.
2. **Price API** — same HTTP as `@pancakeswap/price-api-sdk` (npm package broken: missing `@pancakeswap/utils` on registry). Wired via `services/pcs_price_api.py` → watcher LIVE field `pcs_mark_usd`.
3. **Flags**
   - `PCS_PRICE_API_ENABLED=1` (default)
   - `PCS_SMART_ROUTER_ENABLED=0` (default) — sidecar ready, not on scanner hot path
4. **Bench** `python tools/bench_pcs_vs_bot.py --limit 8 --usd 100`

## When to turn Smart Router ON

- Soft cross-check for a handful of hot tokens (not full 1600-pool cycle).
- Closing-hop discovery FEG→USDT when local closing pool missing.
- Dedicated RPC with higher CU / lower latency than shared Alchemy.

## Re-run bench

```bash
set PYTHONPATH=.
python tools/bench_pcs_vs_bot.py --limit 20 --usd 100
```
