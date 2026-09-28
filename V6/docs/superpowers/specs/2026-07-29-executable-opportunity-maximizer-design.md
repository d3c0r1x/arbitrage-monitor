# Executable Opportunity Maximizer — Design
# Date: 2026-07-29
# Status: APPROVED (user: approach 2, “decide and implement”)

## Goal
Maximize **executable** opportunities (net ≥ 1%), all channels except Alt-CEX.

## Constraints (locked)
- Paths: max **A→B→C→A** (`CHAIN_MAX_HOPS=3`); Cross-DEX remains 2-hop A→B→A across DEXes.
- Start capital A: USDT/USDC first, then WBNB/BNB, WETH/ETH, FDUSD, DAI.
- Size: ladder sweep; optimal = max net USD among sizes with net% ≥ 1% and net_usd > 0.
- Alt-CEX: **disabled**.
- Out of scope: StableSwap adapter, universe inflation, live execution key.

## Size ladder (defaults)
`$10, $25, $50, $100, $250, $500`  
Near-miss diag: `0.35% ≤ net < 1%`.  
Early-stop: 2× consecutive quote0; stop ladder on `no_gross` at a size (larger won’t help).

## Components
1. `config/settings.py` + `.env` — gates, hops, starts, ladder, `ALT_CEX_PROBE_ENABLED=0`.
2. `services/size_sweep.py` — parse ladder + pick optimal / near-miss.
3. `scanner/cross_dex_engine.py` — size sweep per route; enrich signal payload.
4. `scanner/chain_engine.py` — prioritize starts; sweep per chain; hops=3.
5. `scanner/scanner.py` — MEXC: no soft promote without book clip meeting ≥1%.

## Success check
- Logs: no `alt_cex_probe_cycle`.
- `cross_dex_diag` / `chain_diag`: `near_miss>0` and/or `signals>0` when edges exist.
- Hard signals carry `size_min_usd` / `size_max_usd` / `size_optimal_usd`.
