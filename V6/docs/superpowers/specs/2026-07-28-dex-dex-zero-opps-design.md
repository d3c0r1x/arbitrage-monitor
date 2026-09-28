# 2026-07-28 DEX-DEX zero opportunities — design

## Goal
Why `cross=0 chain=0` with 140 groups / 2218 edges, then fix so real net≥threshold signals appear.

## Decision
Phase **A** then **B** (no threshold dump).

### Phase A — funnel diagnostics
- Counters per cycle: `no_adapter`, `quote0`, `quote_err`, `no_gross`, `below_min`, `ok`
- Near-miss: `gross_pct > 0` but `net < min` (or quote path died after partial success) — top 20
- Persist `data/dex_dex_diagnostics.json`; log one INFO line per cycle
- API `GET /api/dex_dex/diagnostics` (optional thin)

### Phase B — fix top killers from counters
Likely: adapter `None`, v3 fee miss → quote0, wrong inferred version, v4 always 0 (skip/drop v4 from scan or mark unsupported).
Do **not** lower `CROSS_DEX_MIN` / `CHAIN_MIN` as primary fix.

## Success
1. Diagnostics show non-zero bucket counts each cycle.
2. After B: either `cross+chain > 0` or diagnostics prove market has no gross edge (near-miss empty + quote0 dominated by real empty books).
