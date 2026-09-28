# Project State

## Phase: Full Fix Plan — COMPLETE

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
