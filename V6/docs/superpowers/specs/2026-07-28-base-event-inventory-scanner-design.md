# 2026-07-28 No-CEX scanner BASE+event — design

## Goal
Scanner-only, no CEX, no flashloan. Signals must be inventory-executable: net>0 after gas/slip if wallet already holds start asset S.

## Stack
1. Enable BASE in ACTIVE_NETWORKS (Alchemy/Infura/dRPC already templated).
2. Same-chain Cross + Chain on BASE (Aerodrome/Uni) + BSC/ETH/ARB long-tail.
3. Event boost: track recent Swap/high-volume pools; scan those first each cycle (no flashloan tag).
4. Output: dex_dex_* only; `executable=inventory`.

## OOS
CEX, flashloan, live execution, Solana.
