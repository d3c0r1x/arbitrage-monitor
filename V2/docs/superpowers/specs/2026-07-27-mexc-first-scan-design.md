# MEXC-first second-scan design

Date: 2026-07-27  
Status: approved (user)

## Goal

More real signals, honest sizing. MEXC token list drives pools. Scan ~1s. Always settle to USDT/USDC via A-B-C-A max. Final gate: net ≥ 0.1% on chosen volume. Dashboard LIVE vs ARCHIVE.

## Pipeline

### Refresh (every N hours, default 3h)

1. MEXC capital config: deposit/withdraw/fee/contract/minConfirm.
2. Discover pools only for those contracts (ACTIVE: BSC, ARBITRUM).
3. Write `data/pools_cache.json`.

### Scan (~1s)

1. MEXC mid price.
2. RPC reserves + quote_exact_input per hop.
3. If quote ≠ USDT/USDC: close route max A-B-C-A (A=USDT|USDC).
4. Size from pool depth; fees (deposit, withdraw, gas×hops, taker). Pool fee in quote.
5. If net% ≥ 0.1% → orderbook max clip still ≥ 0.1%.
6. Emit LIVE; when stale → ARCHIVE.

## RPC budget

- 2 Alchemy keys × 500 CU/s × 0.95 = **950 CU/s** total.
- Weights: eth_call=26, eth_gasPrice=20, multicall=sum.
- Replace rpm=10 with CU/s limiter.

## Filters

- `high_min_confirm` only Direction A (DEX_BUY_MEXC_SELL).
- Direction B ignore deposit minConfirm.
- Fail-closed: no decimals / no stable route / empty book / quote=0 → skip.

## Dashboard

- LIVE: currently valid opportunities.
- ARCHIVE: expired signals kept for history.
- Client filters % / $ stay.

## Out of scope

Execution broadcast / private keys.
