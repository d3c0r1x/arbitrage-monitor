# Implementation plan: MEXC-first second-scan

## Tasks

1. **CU/s limiter** — `utils/cu_rate_limiter.py`; wire `RpcClient` / round-robin; `rate_limits.py` 950 CU/s @ 0.95 of 2×500.
2. **Settings** — `POOL_REFRESH_INTERVAL_SEC=10800`, `SCAN_INTERVAL_SEC=1`, `MIN_NET_PROFIT_PCT=0.1`, keep `MIN_NET_PROFIT_USD` optional/low.
3. **signal_filters** — minConfirm only for `DEX_BUY_MEXC_SELL`.
4. **Closing routes** — generalize `closing_pools` / scanner to A-B-C-A (stable start/end); WBNB/WETH + extend via graph of known stables.
5. **Orderbook / profit** — gate on 0.1% of clip size (not $0.1 floor alone).
6. **Scanner loop** — target 1s; drop mid 1% hard gate to 0.1%.
7. **LIVE/ARCHIVE** — `data/opportunities_live.json` + append archive jsonl; watcher promotes/expires.
8. **Dashboard** — LIVE panel default; ARCHIVE tab; read new files + `.run/*.log`.
9. **Tests** — CU math, AIDOGE dir-aware, close route, 0.1% gate, live/archive.
10. **Restart** bot + dashboard.

## Done when

- [x] RPC limiter CU-based, no 10 rpm.
- [x] Dir B AIDOGE not killed by minConfirm.
- [x] Signals settle USDT/USDC A-B-C-A.
- [x] Dashboard shows live now; archive keeps dead.
- [x] Settings/env: scan 1s, refresh 3h, gate 0.1%.
- [x] Tests pass; bot running. (verify on restart)
