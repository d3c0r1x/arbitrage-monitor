# PCS Subgraph Integration Plan
# Date: 2026-07-29

## Goal
Wire PancakeSwap Exchange V3 + StableSwap BSC subgraphs into pool discovery,
then run MEXC↔DEX and DEX↔DEX scanners.

## Current state
- `.env` has `GRAPH_API`, `PCS_SUBGRAPH_V3_BSC`, `PCS_SUBGRAPH_STABLE_BSC` (filled).
- Code only reads `SUBGRAPH_URL`; `SubgraphSource` raises "not yet implemented".
- `SourceManager._IMPLEMENTED_SOURCES` excludes `subgraph`.
- Graph smoke: V3 `pools` OK; Stable `pairs` OK; token filters OK.

## Steps
1. **Settings** — read `PCS_SUBGRAPH_V3_BSC`, `PCS_SUBGRAPH_STABLE_BSC`, `GRAPH_API`;
   build `subgraph_urls` list; keep `SUBGRAPH_URL` as legacy single-URL fallback;
   expose presence flags (never log key values).
2. **Implement `SubgraphSource`** — GraphQL by token for BSC only:
   - V3: `pools(where: or token0/token1)` → `dex=pancakeswap_v3`
   - Stable: `pairs(...)` → `dex=pancakeswap_stable` (discovery only; no adapter yet)
   - Cache 60s; rate-limit; fail soft with SourceError.
3. **Wire** — register in `main.py`; add `subgraph` to `_IMPLEMENTED_SOURCES`;
   update `discovery_sources` required env to accept PCS_* vars;
   add `subgraph` to `POOL_SOURCE_PRIORITY` in `.env`.
4. **Docs/.env.example** — document new vars.
5. **Smoke** — one-token fetch via source class.
6. **Run** — start bot in `mexc` then `dex_dex` (or two processes / mode toggle).

## Out of scope (later)
- StableSwap quote adapter / registry entry
- NodeReal V2 Meganode subgraph
- Non-BSC PCS subgraphs (Base/ARB/ETH)
