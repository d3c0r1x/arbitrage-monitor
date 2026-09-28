# Plan: Watcher / orderbook PnL parity (FEG-class)

**Date:** 2026-07-29  
**Goal:** LIVE and hard-gate use the same executable net%; no −100% / inflated mid% for WBNB exits.

## Problems

| # | Issue | Effect |
|---|--------|--------|
| 1 | Watcher requote = mid DEX only (no MEXC book) | LIVE shows ~2.9% while book is ~0.8% |
| 2 | `mexc_price_usd` frozen at promote | Stale spot |
| 3 | Dir B withdraw fee counted twice | Tokens already net of fee **and** `fees.total()` includes withdraw USD → hard gate falsely kills (FEG 1%+ → 0.88%) |
| 4 | Soft promote missing hop context | Fixed earlier (chain_hops); keep in place |

## Approach

1. **Single source for book sizing** — watcher calls the same `_apply_orderbook_final` as scanner (inject applier + price service).
2. **Refresh MEXC mid** on every requote via `PriceService.get_price`.
3. **No double-count withdraw (Dir B)** — when quote uses net tokens (`gross − fee_token`), pass `mexc_withdraw_fee_usd=0` into PnL math; keep fee on opportunity for book `fee_token` / display.
4. **Orderbook gate uses `optimal.net_profit_*`** — do not recompute net via `fees.total()` after maximize (that re-adds withdraw + can disagree with maximize).
5. **Promote stores** `mexc_withdraw_fee_token` on `WatchedOpportunity`; rescale `amount_in_raw` / `base_amount_usd` after book.
6. **Anchor scan size** — freeze `anchor_base_usd` / `anchor_amount_in_raw` at promote; requote always uses anchors so maximize cannot grow notional `$243→$1k+`.

## Out of scope

- Lowering `MIN_NET_PROFIT_PCT` below 1%.
- Changing soft-radar TTL rules.

## Verify

- Restart bot; FEG can hard-write when book clip ≥ 1% (no false kill from withdraw double-count).
- LIVE `net_profit_pct` follows orderbook when clip exists; size stays near promote anchor (no $214→$1k chase).
- Unit: `tests/unit/test_orderbook_service.py` passes; Dir B withdraw not in `fees.total()` after book.
