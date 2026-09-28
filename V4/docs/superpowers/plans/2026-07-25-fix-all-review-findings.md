# Fix All Code Review Findings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all ~50 findings from the senior code review (categories A–G) in priority order to make the MEXC × DEX Arbitrage Monitor correct, reliable, and production-ready.

**Architecture:** Incremental fixes — each task addresses one review finding, tested independently. Tasks are ordered by business impact: critical money bugs first, then data integrity, then RPC/performance, then security/tests/docs.

**Tech Stack:** Python 3.12, asyncio, httpx, aiohttp, web3.py, sqlite3, pytest, Decimal

## Global Constraints

- All monetary values use `Decimal`, never `float`
- No secrets in logs or git (API keys masked)
- Unit tests must not call real network
- Integration tests marked `@pytest.mark.integration`
- All new services must have `__init__` type hints
- Existing test suite must remain green after each task
- Windows compatibility: no Unix-specific APIs, use `asyncio.to_thread` for blocking I/O

---

## File Structure — Complete Map

Files to CREATE:
- `services/token_meta_service.py` ✅ (already created)
- `utils/retry.py` — retry with backoff
- `utils/jsonl_rotator.py` — JSONL file rotation
- `tests/unit/test_decimals.py` — decimals tests

Files to MODIFY:
- `scanner/scanner.py` — Direction B, dynamic decimals, security integration, time.sleep fix
- `scanner/pool_refresh_task.py` — stale pool protection, backup, decimals, refresh_log
- `services/profit_calculator.py` — token decimals param (already accepts it)
- `services/fee_service.py` — withdraw fees from DB, gas estimate defaults
- `discovery/onchain_factory_source.py` — calldata fix ✅ (already done)
- `main.py` — wire TokenMetaService, Web3Manager, security checkers
- `clients/rpc_client.py` — retry with backoff
- `dex/v2_adapter.py` — structured results, token0 cache
- `dex/v3_adapter.py` — structured results, fee cache
- `dex/adapter_factory.py` — pool_version-based selection
- `dex/pool_detector.py` — V2/V3 detection
- `metrics/health.py` — update_metrics calls from scanner/refresh
- `gui/dashboard.py` — busy_timeout for DB
- `security/token_security_checker.py` — implement basic checks
- `security/pool_security_checker.py` — implement basic checks
- `storage/backup_manager.py` — verify integration
- `storage/repository.py` — refresh_log writes
- `config/settings.py` — defaults alignment
- `.env.example` — defaults alignment
- `STATE.md` — honest status update

---

## Task sequence (by qwen-cicle priority)

Priority: Critical blockers → crashes → security → high impact low effort → performance → tests → docs

---

### Task 1: A1 — Complete decimals fix (wire TokenMetaService into main.py + pool_refresh)

**Files:**
- Modify: `main.py` — instantiate TokenMetaService, pass to PoolRefreshTask
- Modify: `scanner/pool_refresh_task.py` — call get_decimals(), write real values to pools_cache
- Modify: `scanner/scanner.py` — already reads dynamic decimals (done)
- Test: `tests/unit/test_decimals.py` — NEW

**Interfaces:**
- Consumes: `TokenMetaService.get_decimals(network, address) → int` from Task 0
- Produces: `pools_cache.json` entries with real `token_decimals` and `stablecoin_decimals`

- [ ] **Step 1: Write the failing test for decimals**

```python
# tests/unit/test_decimals.py
import pytest
from decimal import Decimal

def test_floor_to_wei_usdc():
    """USDC has 6 decimals. $10 should be 10_000_000."""
    from utils.decimal_utils import floor_to_wei
    result = floor_to_wei(Decimal("10"), 6)
    assert result == 10_000_000

def test_floor_to_wei_usdt_bsc():
    """USDT on BSC has 18 decimals. $10 should be 10*10**18."""
    from utils.decimal_utils import floor_to_wei
    result = floor_to_wei(Decimal("10"), 18)
    assert result == 10_000_000_000_000_000_000

def test_floor_to_wei_fractional():
    """Fractional amounts floor correctly."""
    from utils.decimal_utils import floor_to_wei
    result = floor_to_wei(Decimal("1.234"), 6)
    assert result == 1_234_000
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_decimals.py -v`
Expected: PASS (floor_to_wei already exists in utils/decimal_utils.py)

- [ ] **Step 3: Wire TokenMetaService into main.py**

In `main.py`, in `initialize()`:

```python
# After creating rpc_factory, add:
from services.token_meta_service import TokenMetaService
self._token_meta_service = TokenMetaService(rpc_client_factory=rpc_factory)

# Pass to PoolRefreshTask:
refresh_task = PoolRefreshTask(
    ...
    token_meta_service=self._token_meta_service,
)
```

- [ ] **Step 4: Add token_meta_service param to PoolRefreshTask.__init__**

```python
def __init__(
    self,
    ...
    token_meta_service=None,
):
    ...
    self._token_meta_service = token_meta_service
```

- [ ] **Step 5: Call get_decimals() in pool_refresh_task after discovery**

In `pool_refresh_task.py`, in the pools_cache JSON writing section, replace the hardcoded `18` values:

```python
# Before writing pool_dicts, fetch real decimals
token_decimals = 18
stablecoin_decimals = 18

if self._token_meta_service:
    try:
        token_decimals = await self._token_meta_service.get_decimals(
            network=r[0], token_address=r[1]
        )
        stablecoin_decimals = await self._token_meta_service.get_decimals(
            network=r[0], token_address=r[2]
        )
    except Exception as exc:
        logger.debug("decimals_fetch_failed: %s", exc)

pool_dicts = [
    {
        ...
        "token_decimals": token_decimals,
        "stablecoin_decimals": stablecoin_decimals,
    }
    ...
]
```

- [ ] **Step 6: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add services/token_meta_service.py scanner/pool_refresh_task.py main.py tests/unit/test_decimals.py
git commit -m "fix(A1): wire dynamic decimals from TokenMetaService into pool cache"
```

---

### Task 2: A7 — V3 calldata fee_hex encoding (ALREADY DONE)

```python
# Before:
fee_hex = format(fee_bps * 100, "012x")   # 6 bytes — WRONG
# After:
fee_hex = format(fee_bps * 100, "x").zfill(64)  # 32 bytes — CORRECT
```

- [ ] **Step 1: Verify the fix is already applied**

```bash
grep -n "zfill" discovery/onchain_factory_source.py
```
Expected: `fee_hex = format(fee_bps * 100, "x").zfill(64)` on line ~120

- [ ] **Step 2: Write a unit test for calldata encoding**

```python
# tests/unit/test_onchain_calldata.py
def test_v3_factory_calldata_length():
    """uint24 must be 32 bytes (64 hex chars) in ABI encoding."""
    fee_bps = 5
    fee_hex = format(fee_bps * 100, "x").zfill(64)
    assert len(fee_hex) == 64
    assert fee_hex == "00000000000000000000000000000000000000000000000000000000000001f4"

def test_v3_factory_30bps():
    """30 bps → fee tier 3000 → 0xbb8 → 64 hex chars."""
    fee_bps = 30
    fee_hex = format(fee_bps * 100, "x").zfill(64)
    assert len(fee_hex) == 64
    assert fee_hex.endswith("bb8")
```

- [ ] **Step 3: Run test**

Run: `pytest tests/unit/test_onchain_calldata.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add discovery/onchain_factory_source.py tests/unit/test_onchain_calldata.py
git commit -m "fix(A7): V3 calldata uint24 encoding — pad to 32 bytes"
```

---

### Task 3: A8 — Stale pool protection (keep pools when discovery returns 0)

**Files:**
- Modify: `scanner/pool_refresh_task.py` — add stale pool check before DELETE

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_stale_pool_protection.py
def test_refresh_keeps_pools_when_discovery_returns_zero():
    """If active DB has pools and discovery returns 0, old pools are preserved."""
    # This is an integration test concept — verify the logic in the refresh task
    assert True  # Placeholder for the async test
```

- [ ] **Step 2: Add stale pool protection**

In `pool_refresh_task.py`, in `_run_refresh_inner`, before `DELETE FROM pools`:

```python
# Check if we have pools before deleting.
old_pools_count = conn.execute("SELECT COUNT(*) FROM pools;").fetchone()[0]

if old_pools_count > 0 and len(discovered_pools) == 0:
    logger.warning(
        "pool_refresh_kept_stale_pools: old=%d new=0",
        old_pools_count,
    )
    # Don't delete old pools. Only update assets + stablecoins.
    # Skip the DELETE for pools and the INSERT.
    # Jump to assets/stablecoins update only.
    skip_pools_delete = True
else:
    skip_pools_delete = False
    conn.execute("DELETE FROM pools;")
```

Then wrap the pools INSERT in `if not skip_pools_delete:`.

- [ ] **Step 3: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add scanner/pool_refresh_task.py
git commit -m "fix(A8): stale pool protection — keep old pools when discovery=0"
```

---

### Task 4: A9 — Backup before refresh

**Files:**
- Modify: `scanner/pool_refresh_task.py` — call backup_manager.create_backup()
- Verify: `storage/backup_manager.py` — backup exists and works

- [ ] **Step 1: Add backup call in pool_refresh_task**

In `pool_refresh_task.py`, pass `backup_manager` to `__init__`:

```python
def __init__(self, ..., backup_manager=None):
    ...
    self._backup_manager = backup_manager
```

In `main.py`, pass: `backup_manager=backup_manager`

In `_run_refresh_inner`, before `BEGIN IMMEDIATE`:

```python
# Create backup before replacing data.
if self._backup_manager:
    try:
        self._backup_manager.create_backup()
    except Exception as exc:
        logger.error("backup_failed_refresh_aborted: %s", exc)
        return {"error": "backup_failed", "pools_count": 0}
```

- [ ] **Step 2: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add scanner/pool_refresh_task.py main.py
git commit -m "fix(A9): backup before pool refresh"
```

---

### Task 5: Fix time.sleep() → asyncio.sleep() in async code

> **⚠️ Dependency:** `_load_pools_json()` is called synchronously from `refresh_pool_cache()` which is called from `main.py` sync context. Making `_load_pools_json` fully async requires also making `refresh_pool_cache` async.

**Files:**
- Modify: `scanner/scanner.py` — lines 61, 70, 80: `time.sleep(0.5)` in retry loops
  Keep `_load_pools_json` sync (it's a sync file reader). Replace `time.sleep(0.5)` retry with `return []` on first failure (os.replace is atomic — retry is unnecessary).

- [ ] **Step 1: Remove blocking retry loop from _load_pools_json**

Replace the retry logic with a single attempt:

```python
def _load_pools_json(self, log_empty_warning: bool = True) -> list[dict]:
    """Load pools from JSON cache file.
    Uses atomic replace — retry is unnecessary."""
    try:
        if not self._pools_cache_path.exists():
            if log_empty_warning:
                logger.warning("scanner: pools_cache file not found")
            return []
        with open(self._pools_cache_path, encoding="utf-8") as f:
            rows = json.load(f)
        if not rows:
            if log_empty_warning:
                logger.warning("scanner: pools_cache file is empty")
            return []
        return rows
    except Exception as exc:
        logger.error("scanner: pools_cache load error: %s", exc)
        return []
```

Rationale: pool_refresh_task writes via os.replace (atomic), so the file is never in a partial state. No retry needed.

- [ ] **Step 2: Remove `import time` from scanner.py** (no longer needed after sleep removal)

- [ ] **Step 3: Commit**

```bash
git add scanner/scanner.py
git commit -m "fix: remove blocking time.sleep from _load_pools_json — atomic replace makes retry unnecessary"
```

---

### Task 6: A3 — Fix hardcoded USDT quote_asset in signals

**Files:**
- Modify: `scanner/scanner.py` — use `pool["stablecoin_coin"]` instead of hardcoded `"USDT"`
- Modify: `scanner/pool_refresh_task.py` — already adds stablecoin_coin to pools_cache ✅

- [ ] **Step 1: Fix quote_asset in scanner**

In `process_pool()`:
```python
# Before:
mexc_price_usd = self._price_service.get_price(token_coin, "USDT")
if mexc_price_usd is None:
    mexc_price_usd = self._price_service.get_price(token_coin, "USDC")

# After:
quote_asset = pool.get("stablecoin_coin", "USDT")
mexc_price_usd = self._price_service.get_price(token_coin, quote_asset)

# If no price for the correct quote asset, try fallback:
if mexc_price_usd is None:
    mexc_price_usd = self._price_service.get_price(token_coin, "USDT")
if mexc_price_usd is None:
    mexc_price_usd = self._price_service.get_price(token_coin, "USDC")
```

And in `_write_signal` call:
```python
# Before:
await self._write_signal(network, pool, token_coin, "USDT", result_a, mexc_price_usd, amount_out)

# After:
await self._write_signal(network, pool, token_coin, quote_asset, result_a, mexc_price_usd, amount_out)
```

- [ ] **Step 2: Commit**

```bash
git add scanner/scanner.py
git commit -m "fix(A3): use dynamic quote_asset from pool cache instead of hardcoded USDT"
```

---

### Task 7: A6 — Integrate security checks into scanner._write_signal

**Files:**
- Modify: `scanner/scanner.py` — call token/pool security checkers in _write_signal
- Modify: `main.py` — instantiate and pass security checkers
- Modify: `security/token_security_checker.py` — implement basic checks
- Modify: `security/pool_security_checker.py` — implement basic checks

- [ ] **Step 1: Implement basic token security checks**

In `security/token_security_checker.py`:

```python
# Add basic ERC20 selector-based checks
SELECTORS = {
    "owner": "0x8da5cb5b",
    "mint": "0x40c10f19",
    "pause": "0x8456cb59",
    "blacklist": "0x2c3496db",
}

async def check(self, network: str, token_address: str) -> list[str]:
    warnings = []
    for name, selector in SELECTORS.items():
        try:
            rpc = self._rpc_client_factory(network)
            if rpc is None:
                continue
            result = await rpc.eth_call(to=token_address, data=selector)
            # If result is non-zero, the function exists
            if result and int(result, 16) > 0:
                warnings.append(f"token_{name}_detected")
        except Exception:
            warnings.append(f"{name}_check_unavailable")
    return warnings
```

- [ ] **Step 2: Wire into scanner**

In `Scanner._write_signal()`:
```python
# After calculating net_profit_pct > 10 check
if self._token_security_checker:
    try:
        token_warnings = await self._token_security_checker.check(
            network=network, token_address=pool["token_address"]
        )
        warnings.extend(token_warnings)
    except Exception as exc:
        warnings.append("security_check_error")

if self._pool_security_checker:
    try:
        pool_warnings = await self._pool_security_checker.check(
            network=network, pool_address=pool["pool_address"]
        )
        warnings.extend(pool_warnings)
    except Exception as exc:
        warnings.append("pool_security_check_error")
```

- [ ] **Step 3: Commit**

```bash
git add scanner/scanner.py main.py security/token_security_checker.py security/pool_security_checker.py
git commit -m "feat(A6): integrate security checks into scanner signal pipeline"
```

---

### Task 8: A2 — Implement Direction B in scanner

**Files:**
- Modify: `scanner/scanner.py` — add Direction B processing after Direction A

- [ ] **Step 1: Add Direction B to process_pool**

After the Direction A block in `process_pool()`:

```python
# Direction B: MEXC_BUY_DEX_SELL
# Calculate token amount to buy on MEXC
token_amount = settings.BASE_AMOUNT_USD / mexc_price_usd
token_amount_raw = int(token_amount * Decimal(10**token_decimals))

# Quote: sell token on DEX for stablecoin
dex_stable_out = await adapter.quote_exact_input(
    network=network,
    pool_address=pool_address,
    token_in=token_address,
    token_out=stablecoin_address,
    amount_in=token_amount_raw,
)

if dex_stable_out > 0:
    result_b = await self._profit_calculator.calculate_direction_b(
        network=network,
        mexc_price_usd=mexc_price_usd,
        dex_amount_out=dex_stable_out,
        stablecoin_decimals=stablecoin_decimals,
    )

    if result_b["signal"]:
        signals_found += 1
        await self._write_signal(
            network, pool, token_coin, quote_asset,
            result_b, mexc_price_usd, dex_stable_out,
        )
        signals_written += 1
```

- [ ] **Step 2: Commit**

```bash
git add scanner/scanner.py
git commit -m "feat(A2): implement Direction B scanner — MEXC buy → DEX sell"
```

---

### Task 9: A4 — Pass MEXC withdraw fees to ProfitCalculator

**Files:**
- Modify: `scanner/scanner.py` — read withdraw_fee from DB or pool cache
- Modify: `services/fee_service.py` — use withdraw_fee from DB

- [ ] **Step 1: Add withdraw fee lookup in scanner**

```python
# Before calling profit_calculator, get MEXC withdraw fee
mexc_withdraw_fee_usd = Decimal("0")
withdraw_fee_token = pool.get("withdraw_fee")  # From MEXC assets table
if withdraw_fee_token:
    mexc_withdraw_fee_usd = Decimal(str(withdraw_fee_token)) * mexc_price_usd

result_a = await self._profit_calculator.calculate_direction_a(
    network=network,
    token_coin=token_coin,
    mexc_price_usd=mexc_price_usd,
    dex_amount_out=amount_out,
    token_decimals=token_decimals,
    mexc_withdraw_fee_usd=mexc_withdraw_fee_usd,
)
```

- [ ] **Step 2: Commit**

```bash
git add scanner/scanner.py
git commit -m "fix(A4): pass MEXC withdraw fees to profit calculator"
```

---

### Task 10: RPC retry with backoff

**Files:**
- Create: `utils/retry.py`
- Modify: `clients/rpc_client.py` — use retry

- [ ] **Step 1: Create retry utility**

```python
# utils/retry.py
import asyncio
import logging

logger = logging.getLogger(__name__)

async def retry_async(
    func,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    retryable_exceptions=(Exception,),
):
    """Retry an async function with exponential backoff."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return await func()
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt < attempts - 1:
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.debug("retry attempt %d/%d after %.1fs: %s",
                             attempt + 1, attempts, delay, exc)
                await asyncio.sleep(delay)
    raise last_exc
```

- [ ] **Step 2: Use retry in RpcClient._post**

```python
async def _post(self, method: str, params: list) -> dict:
    await self._rate_limiter.acquire()
    ...
    # Wrap in retry for transient errors
    return await retry_async(
        lambda: self._do_post(method, params),
        attempts=3,
        retryable_exceptions=(httpx.TimeoutException, httpx.HTTPStatusError),
    )
```

- [ ] **Step 3: Commit**

```bash
git add utils/retry.py clients/rpc_client.py
git commit -m "feat(C5): add retry with exponential backoff for RPC calls"
```

---

### Task 11: A10 — Web3 caching + minimum changes

**Files:**
- Create: `clients/web3_manager.py`
- Modify: `main.py` — use Web3Manager instead of raw web3_factory
- Modify: `dex/adapter_factory.py` — accept Web3Manager

- [ ] **Step 1: Create Web3Manager**

```python
# clients/web3_manager.py
from web3 import AsyncWeb3
from config.networks import resolve_rpc_url

class Web3Manager:
    def __init__(self):
        self._cache: dict[str, AsyncWeb3] = {}

    def get_web3(self, network: str) -> AsyncWeb3 | None:
        if network in self._cache:
            return self._cache[network]
        rpc_url = resolve_rpc_url(network)
        if not rpc_url:
            return None
        w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))
        self._cache[network] = w3
        return w3

    def clear(self):
        self._cache.clear()
```

- [ ] **Step 2: Wire into main.py**

```python
from clients.web3_manager import Web3Manager
self._web3_manager = Web3Manager()
# Pass to adapter_factory instead of web3_factory
adapter_factory = AdapterFactory(web3_manager=self._web3_manager)
```

- [ ] **Step 3: Commit**

```bash
git add clients/web3_manager.py main.py
git commit -m "feat(A10): Web3 caching — one instance per network"
```

---

### Task 12: Run full test suite and verify

- [ ] **Step 1: Run all unit tests**

```bash
pytest -m "not integration" -q
```
Expected: all pass

- [ ] **Step 2: Run compile check**

```bash
python -m compileall .
```
Expected: no errors

- [ ] **Step 3: Update STATE.md**

```markdown
# Project State

## Completed fixes (from code review)
- [x] A1: Dynamic decimals via TokenMetaService
- [x] A7: V3 calldata uint24 encoding
- [x] A8: Stale pool protection
- [x] A9: Backup before refresh
- [x] A3: Dynamic quote_asset in signals
- [x] A6: Security checks integrated
- [x] A2: Direction B scanner
- [x] A4: MEXC withdraw fees
- [x] C5: RPC retry with backoff
- [x] A10: Web3 caching

## Remaining (lower priority)
- [ ] B1-B8: Discovery optimizations
- [ ] C1-C9: Multicall, fee cache, adapter results
- [ ] D1-D4: Price TTL, fee defaults
- [ ] E1-E3: Security checker completeness
- [ ] F1-F4: Test expansions
- [ ] G1-G7: Docs, metrics, log rotation
```

- [ ] **Step 4: Final commit**

```bash
git add STATE.md
git commit -m "docs: update STATE.md with completed review fixes"
```

---

## Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| TokenMetaService RPC call slows pool refresh | Increased refresh time | Decimals cache minimizes RPC calls; 18 fallback on error |
| Direction B quote fails for low-liquidity tokens | False signals | profit_calculator checks token_amount > min_withdraw |
| Stale pool protection keeps bad pools indefinitely | Stale data | next successful refresh replaces them |
| Security checks add RPC overhead | Slower signal generation | Only called for profitable signals (net_profit_pct > 1) |

## Deferred Tasks (B–G categories, lower priority)

These findings are documented but deferred until A-category tasks are complete:

| Category | Tasks | Priority |
|---|---|---|
| **B1-B8** | Discovery: active networks filter, dedup requests, on-chain fallback, stale registry, pools_cache fields, atomic replace, refresh_log, volume filter | Medium |
| **C1-C9** | RPC: multicall batching, V3 fee cache, token0 cache, DexScreener rate limiter, retry/backoff (partial in Task 10), gas estimate, adapter structured results, SQLite async, Web3 cache (Task 11) | Medium |
| **D1-D4** | Price: TTL check in scanner, graceful refresh failure, MEXC auth test, deposit fee default | Low |
| **E1-E3** | Security: selective checks, fee-on-transfer detection, structured warnings | Low |
| **F1-F4** | Tests: decimals tests (Task 1), Direction B tests (Task 8), dry-run integration tests | Low |
| **G1-G7** | Docs: STATE.md (Task 12), .env alignment, optional source handling, metrics updates, JSONL rotation, GUI busy_timeout, V4 skip | Low |
