# Plan: PCS Smart Router + Price API (benchmark → integrate)

**Date:** 2026-07-29  
**Goal:** Evaluate `@pancakeswap/smart-router` (`getBestTrade`) and `@pancakeswap/price-api-sdk` against current Python Quoter/router quotes, then integrate if metrics justify it.

## Constraints

- Bot is Python; PCS packages are TypeScript → **Node sidecar** (stdio JSON RPC), no private keys.
- Primary chain: **BSC** (Aggregator HTTP API has no BSC — not used).
- Compare apples-to-apples on same token pairs / USD sizes from `pools_cache.json`.

## Phase 1 — Sidecar + bench (this session)

1. `tools/pcs_sidecar/` — Node ESM:
   - `price` → `getTokenPrices`
   - `quote` → `SmartRouter.getBestTrade` (EXACT_INPUT)
2. `tools/bench_pcs_vs_bot.py` — sample N BSC pools:
   - Current: adapter `quote_exact_input` (+ closing for WBNB)
   - PCS: sidecar quote token→USDT (or quote asset)
   - Metrics: latency p50/p95, success rate, amount_out delta %, hypothetical edges vs MEXC mid, CU/time
3. Write `docs/superpowers/plans/2026-07-29-pcs-sdk-bench-results.md`

## Phase 2 — Integrate (after bench)

- Optional price enrich via Price API (mark check).
- Optional Smart Router quote path behind flag `PCS_SMART_ROUTER_ENABLED` for Dir B settlement / cross-check.
- Keep current single-pool quote as default until bench shows clear win.

## Success criteria for integrate

- Sidecar p95 quote latency acceptable vs scan cycle budget, OR used only for hot/soft radar.
- Enough successful quotes on liquid pairs; delta vs bot not systematically worse for same pool route.
