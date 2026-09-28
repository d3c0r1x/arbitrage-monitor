# Project State

## Phase: Plan v3 (Qwen review, D1–D18) — implemented 2026-07-27

NOTE: IDs below (A1–G6, incl. "D1/D2" = gas fallback) belong to the OLDER
senior review. The Qwen plan v3 uses its own D1–D18 numbering (decimals,
rate limits, slippage...). Do not confuse the two.

### Plan v3 changes (2026-07-27)

| Plan ID | Fix | Files |
|---|---|---|
| D1/D2/D16 | KNOWN_DECIMALS + fail-closed decimals (no silent 18, no poison cache) | `services/token_meta_service.py`, `scanner/scanner.py`, `scanner/chain_engine.py`, `scanner/pool_refresh_task.py` |
| D3/D15 | RPC rate limit 500→10 rpm, `RATE_LIMITS["rpc"]` wired into `main.py` | `config/rate_limits.py`, `main.py` |
| D4 | Canonical token address registry, impostor tickers rejected | `security/token_address_registry.py` (new), `scanner/scanner.py` |
| D5 | Pools with empty `pool_version` not persisted / not scanned | `scanner/pool_refresh_task.py`, `services/signal_filters.py` |
| D6/D14/D17 | Direction-aware pre/post signal filters (max 50% profit, deposit/withdraw gates, min_confirm, fee ratio); NULL MEXC flags fail-closed | `services/signal_filters.py` (new), `scanner/scanner.py`, `scanner/pool_refresh_task.py` |
| D7 | Multihop min_out from fresh quote; builder `min_out=1` defaults removed | `execution/executor.py` |
| D8 | Dynamic watcher expiry from `min_confirm` × block time | `scanner/signal_watcher.py`, `config/networks.py` (BLOCK_TIME_SEC) |
| D9 | `gas_fallback_total` metric on every fallback | `services/fee_service.py`, `metrics/health.py` |
| D10 | All-sources-unhealthy → SourceError (cached pools kept) | `discovery/source_manager.py` |
| D11 | `ACTIVE_NETWORKS={BSC, ARBITRUM}`; ETH/POLY/ROBINHOOD skipped in rpc_factory, refresh, scanner | `config/networks.py`, `main.py`, `scanner/pool_refresh_task.py`, `scanner/scanner.py` |
| D12/D13/D18 | httpx/httpcore/web3 → WARNING; `mask_secrets` in retry logs; `mask_url` in circuit breaker names; key-leaking logs quarantined to `..\_quarantine_secret_logs` | `utils/logging.py`, `utils/retry.py`, `clients/rpc_client.py` |
| Ф1.4 | Dead-pool blacklist (3 fails → exponential TTL) | `services/pool_blacklist.py` (new), `scanner/scanner.py` |
| Ф2.3 | Circuit breaker per RPC endpoint + round-robin skips open circuits | `utils/circuit_breaker.py` (new), `clients/rpc_client.py` |
| Ф3.4 | MEXC capital config cache TTL 3600→300s | `execution/mexc_executor.py` |
| Ф4.1 | New counters: rpc_429, signals_filtered, fake_tokens_rejected, circuit_open, gas_fallback | `metrics/health.py` |

**Manual step still required (Ф0.1):** rotate the two leaked Alchemy keys
in the Alchemy dashboard and update `.env` (`grep ALCHEMY_KEY_1_PREFIX` now matches only `.env`).

Tests: 251 passed (30 new: signal_filters, pool_blacklist, token_address_registry,
circuit_breaker, watcher_expiry).

---

## Previous phase: Full Fix Plan (senior review A1–G6) — COMPLETE

All 24 findings from the comprehensive senior code review have been addressed.

## Completed Fixes — Full Fix Plan (Phases 1–7)

### Phase 1: Money-Critical Bugs (A1–A5, D1–D2)

| ID | Finding | Files Changed |
|---|---|---|
| ✅ **A1** | Direction A uses stablecoin withdraw fee (not token fee) | `scanner/pool_refresh_task.py`, `scanner/scanner.py`, `services/fee_service.py` |
| ✅ **A2** | `stable_deposit_network_fee_usd` added to FeeBreakdown + total() | `models/fee_models.py`, `services/fee_service.py` |
| ✅ **A3** | SLIPPAGE_BUFFER_BPS default 30 (was 0) | `config/settings.py` |
| ✅ **A4** | Signals written to SQLite (db_path in SignalWriter) | `scanner/signal_writer.py`, `main.py` |
| ✅ **A5** | Direction B quotes net token_amount (fee deducted before quote) | `scanner/scanner.py`, `services/profit_calculator.py` |
| ✅ **D1** | Gas fallback with logging + per-network defaults | `services/fee_service.py` |
| ✅ **D2** | Differentiated gas_units by pool version (v2/v3/v4) | `services/fee_service.py` |

### Phase 2: Data Integrity & Dead Code (B2–B5)

| ID | Finding | Files Changed |
|---|---|---|
| ✅ **B2** | Removed StateRepository (dead code) | `storage/repository.py` (deleted), `main.py`, `scanner/pool_refresh_task.py` |
| ✅ **B3** | Lock fix in run_cycle (TOCTOU eliminated) | `scanner/scanner.py` |
| ✅ **B4** | gather logs exceptions instead of swallowing | `scanner/scanner.py` |
| ✅ **B5** | increment_metrics for signals_total (was assignment) | `metrics/health.py`, `scanner/signal_writer.py` |

### Phase 3: MulticallClient (B1) + web3 pin

| ID | Finding | Files Changed |
|---|---|---|
| ✅ **B1** | MulticallClient wired into DI, batch decimals | `main.py`, `services/token_meta_service.py` |
| ✅ **3.1** | web3 pinned to `>=6.19,<7` | `requirements.txt` |
| ✅ **3.5** | Removed legacy RpcClient.multicall() | `clients/rpc_client.py` |

### Phase 4: Security — Bytecode Analysis (E1)

| ID | Finding | Files Changed |
|---|---|---|
| ✅ **E1** | TokenSecurityChecker rewritten: PUSH4 selector search in bytecode | `security/token_security_checker.py` |
| ✅ **4.2** | PoolSecurityChecker extended: token0/token1/factory validation | `security/pool_security_checker.py` |

### Phase 5: RPC & Performance (C1–C4)

| ID | Finding | Files Changed |
|---|---|---|
| ✅ **C1** | Retry only transient errors (4xx raises immediately) | `clients/rpc_client.py`, `utils/retry.py` |
| ✅ **C2** | Rate limiter keeps task references (GC fix) | `utils/rate_limiter.py` |
| ✅ **C3** | Adaptive concurrency actually resizes semaphore | `utils/rate_limiter.py` |
| ✅ **C4** | LRU eviction for DexScreener cache | `discovery/dexscreener_source.py` |

### Phase 6: Configuration (G1–G6)

| ID | Finding | Files Changed |
|---|---|---|
| ✅ **G1** | Removed volume filters, pagination by refresh cycles | `services/mexc_asset_service.py`, `scanner/pool_refresh_task.py`, `config/settings.py`, `storage/database.py` |
| ✅ **G2** | Robinhood address validation script | `scripts/check_project.py` |
| ✅ **G3** | DEXTOLS → DEXTOOLS typo fixed | `config/discovery_sources.py`, `discovery/dextools_source.py`, `config/settings.py` |
| ✅ **G4** | Python version synced to 3.12 | `pyproject.toml` |
| ✅ **G5** | POOL_SOURCE_PRIORITY derived from SOURCE_PRIORITY_TABLE | `config/settings.py` |
| ✅ **G6** | Repo hygiene (.gitignore, project_dump.txt deleted) | `.gitignore` |

### Phase 7: Verification

| Check | Result |
|---|---|
| Unit tests | 205 passed |
| mypy | 0 errors (73 source files) |
| ruff | 42 auto-fixed, remaining are pre-existing style (E501, E402) |
| Import check | main.py imports OK |

## Test Coverage

- Before: 189 tests
- After: 205 tests (+16 golden tests for Phase 1 money-critical fixes)
- All tests pass

## Architecture Notes

- **No volume filter**: All MEXC pairs scanned (plan principle #4). Pagination via `CANDIDATES_PER_REFRESH_CYCLE=500` with round-robin offset stored in `refresh_log.candidate_offset`.
- **Multicall3**: Batched eth_call via `MulticallClient.try_aggregate()` for decimals and security checks.
- **Security**: Bytecode analysis (PUSH4 selector search) instead of eth_call heuristics. Proxy-aware (EIP-1967).
- **Fee model**: Direction A uses stablecoin withdraw fee; Direction B uses token withdraw fee. `stable_deposit_network_fee_usd` tracked in FeeBreakdown.
