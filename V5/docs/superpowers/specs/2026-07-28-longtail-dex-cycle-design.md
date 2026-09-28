# 2026-07-28 Long-tail DEX cycle scan — design

## Goal
Scanner-only cycles `S → B → C → S` (2–4 hops), no CEX, no flashloan.
`S ∈ {USDT, USDC, WBNB, WETH, BNB, ETH}` (BNB/ETH → wrapped on-chain).

## Why
Major Cross DEX-DEX: ~94% `no_gross`. Edge expected on mid/long-tail pools.

## Approach
Long-tail chain universe + new-pool priority boost. Keep Cross secondary.

## Spec
1. **Starts:** extend `CHAIN_START_QUOTES` / `stable_start_addresses` to include wrapped natives mapped as start coins.
2. **Universe:** harvest/filter liq USD in `[DEX_DEX_TAIL_MIN_LIQ, DEX_DEX_TAIL_MAX_LIQ]` default `5000..200000`; boost `pairCreatedAt` young / high volume when ranking scan order.
3. **Graph/scan:** reuse `ChainEngine`; expand `INTERMEDIARIES` with S; cap chains; sim quote − gas; skip v4; keep diagnostics.
4. **Output:** existing `dex_dex_*` paths; near-miss unchanged.
5. **OOS:** CEX, flashloan, execution, inactive networks.

## Success
`chain_signals > 0` with net>0 after gas on tail, OR diag proves `no_gross` dominates on tail too.

## Defaults
- `CHAIN_START_QUOTES=USDT,USDC,WBNB,WETH,BNB,ETH`
- `DEX_DEX_TAIL_MIN_LIQ=5000` `DEX_DEX_TAIL_MAX_LIQ=200000`
- Hops 2–4, existing min profit unless env override
