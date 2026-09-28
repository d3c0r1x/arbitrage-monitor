# Remaining Code Review Fixes — Phase 2 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all remaining ~36 findings from the senior code review (categories A11–A12, B4–B8, C1–C9, D1–D4, E2–E3, F1–F4, G1–G7) to make the MEXC × DEX Arbitrage Monitor robust, performant, and production-ready.

**Architecture:** Incremental fixes — each task addresses one review finding, tested independently. Tasks are ordered by impact: correctness → reliability → performance → test coverage → docs.

**Tech Stack:** Python 3.12, asyncio, httpx, web3.py, sqlite3, pytest, Decimal

## Global Constraints

- All monetary values use `Decimal`, never `float`
- No secrets in logs or git (API keys masked)
- Unit tests must not call real network
- Integration tests marked `@pytest.mark.integration`
- All new services must have `__init__` type hints
- Existing test suite must remain green after each task
- Windows compatibility: no Unix-specific APIs, use `asyncio.to_thread` for blocking I/O

---

## Task Sequence (ordered by impact)

### Task 1: A11/A12 — Pool version detection + adapter selection

**Files:**
- Modify: `dex/pool_detector.py` — ensure detect_version returns correct version
- Modify: `scanner/pool_refresh_task.py` — call PoolDetector after discovery, write pool_version to DB and pools_cache.json
- Modify: `dex/adapter_factory.py` — accept pool_version param in get_adapter, match by registry entry version
- Modify: `scanner/scanner.py` — pass pool_version from pool dict to adapter_factory
- Test: `tests/unit/test_pool_detector.py`

**Interfaces:**
- Consumes: `PoolDetector.detect_version(network, pool_address) -> str | None`
- Produces: `pool_dict["pool_version"] = "v2" | "v3" | ""`
- Consumes: `AdapterFactory.get_adapter(network, dex_id, pool_version=None) -> BaseDexAdapter | None`

- [ ] **Step 1: Write failing test for PoolDetector**

```python
# tests/unit/test_pool_detector.py
import pytest
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_detect_v3_by_fee_selector():
    """V3 pool responds to fee() selector 0xddca3f43."""
    mock_factory = lambda n: AsyncMock()
    detector = PoolDetector(mock_factory)
    mock_rpc = mock_factory("ETH")
    mock_rpc.eth_call = AsyncMock(return_value="0x0000000000000000000000000000000000000000000000000000000000000bb8")
    version = await detector.detect_version("ETH", "0xpool")
    assert version == "v3"

@pytest.mark.asyncio
async def test_detect_v2_by_getReserves():
    """V2 pool responds to getReserves() selector 0x0902f1ac."""
    mock_factory = lambda n: AsyncMock()
    detector = PoolDetector(mock_factory)
    mock_rpc = mock_factory("ETH")
    mock_rpc.eth_call = AsyncMock(side_effect=[
        Exception("no fee()"),
        "0x0000000000000000000000000000000000000000000000000000000000000001",
    ])
    version = await detector.detect_version("ETH", "0xpool")
    assert version == "v2"

@pytest.mark.asyncio
async def test_detect_unknown():
    """Neither selector returns data → version is None."""
    mock_factory = lambda n: AsyncMock()
    detector = PoolDetector(mock_factory)
    mock_rpc = mock_factory("ETH")
    mock_rpc.eth_call = AsyncMock(side_effect=[
        Exception("no fee()"),
        Exception("no getReserves()"),
    ])
    version = await detector.detect_version("ETH", "0xpool")
    assert version is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_pool_detector.py -v`
Expected: PASS (PoolDetector already exists)

- [ ] **Step 3: Update pool_refresh_task to detect and write pool_version**

In `scanner/pool_refresh_task.py`, after discovery in `_run_refresh_inner`:

```python
# Add pool_detector to __init__
def __init__(self, ..., pool_detector=None):
    ...
    self._pool_detector = pool_detector

# In the pools INSERT section, before writing:
pool_version = ""
if self._pool_detector:
    try:
        pool_version = await self._pool_detector.detect_version(
            network=pool.network,
            pool_address=pool.pool_address,
        )
    except Exception:
        pass

# Use pool_version in INSERT:
(pool.network, ..., pool.pool_version or pool_version or "", ..., now_ts, now_ts)
```

- [ ] **Step 4: Add pool_version to pools_cache.json**

In the SQL query that builds pool_dicts:
```sql
SELECT p.network, p.token_address, p.stablecoin_address,
       p.pool_address, p.dex, ma.coin as token_coin,
       sc.coin as stablecoin_coin,
       ma.withdraw_fee,
       p.pool_version   -- ADD THIS
FROM pools p ...
```

Then in pool_dicts:
```python
pool_dicts.append({
    ...
    "pool_version": r[8] or "",
})
```

- [ ] **Step 5: Update AdapterFactory.get_adapter to accept pool_version**

In `dex/adapter_factory.py`:

```python
def get_adapter(self, network: str, dex_id: str, pool_version: str | None = None):
    key = (network, dex_id, pool_version)  # Include version in cache key
    
    if key in self._adapters:
        return self._adapters[key]
    
    network_config = DEX_REGISTRY.get(network)
    if not network_config:
        return None
    
    candidates = _DEX_ALIASES.get(dex_id, [dex_id])
    
    # If pool_version is known, filter candidates to matching version.
    if pool_version:
        candidates = [c for c in candidates if c.endswith(f"_{pool_version}")]
    
    for rid in candidates:
        adapter = self._create_adapter(network_config, network, rid)
        if adapter is not None:
            self._adapters[key] = adapter
            return adapter
    
    return None
```

- [ ] **Step 6: Update scanner to pass pool_version**

In `scanner/scanner.py`, in `process_pool()`:

```python
pool_version = pool.get("pool_version")
adapter = self._adapter_factory.get_adapter(network, dex_id, pool_version=pool_version)
```

- [ ] **Step 7: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 2: A5 — DEX pool fee: add quote_includes_pool_fee flag

**Files:**
- Modify: `dex/base_adapter.py` — add `quote_includes_pool_fee = True` class attribute
- Modify: `models/quote_models.py` or `models/fee_models.py` — document in FeeBreakdown
- Modify: `services/fee_service.py` — skip dex_pool_fee_usd when quote already includes it
- Modify: `scanner/scanner.py` — set `dex_pool_fee_included_in_quote=True` in signal
- Modify: `models/signal_models.py` — add field if needed
- Test: existing tests should pass

- [ ] **Step 1: Add quote_includes_pool_fee to BaseDexAdapter**

```python
class BaseDexAdapter(ABC):
    version: str = "base"
    quote_includes_pool_fee: bool = True  # ADD
```

- [ ] **Step 2: Update FeeService to not double-count**

In `services/fee_service.py`, in both `calculate_fees_direction_a` and `calculate_fees_direction_b`, accept a `quote_includes_pool_fee: bool = True` parameter and skip `dex_pool_fee_usd` if True.

- [ ] **Step 3: Update scanner to pass flag and record in signal**

In `scanner/scanner.py`:
```python
adapter = self._adapter_factory.get_adapter(...)
quote_includes_fee = getattr(adapter, 'quote_includes_pool_fee', True)
```

In signal:
```python
signal = ArbitrageSignal(
    ...
    dex_pool_fee_included_in_quote=quote_includes_fee,
)
```

- [ ] **Step 4: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 3: B4 — Refresh stablecoin registry in on-chain factory

**Files:**
- Modify: `scanner/pool_refresh_task.py` — pass onchain_factory and update its registry after build
- Modify: `discovery/onchain_factory_source.py` — add set_stablecoin_registry method
- Test: `tests/unit/test_onchain_factory.py` — update or verify

- [ ] **Step 1: Add set_stablecoin_registry to OnchainFactorySource**

In `discovery/onchain_factory_source.py`:
```python
def set_stablecoin_registry(self, registry: dict[str, list[StablecoinRecord]]) -> None:
    self._stablecoin_registry = registry
    # Also rebuild the per-network address sets
    self._stablecoin_addresses_by_network = {
        network: {r.address.lower() for r in records}
        for network, records in registry.items()
    }
```

- [ ] **Step 2: Add onchain_factory ref to PoolRefreshTask and update it**

In `pool_refresh_task.py`:
```python
def __init__(self, ..., onchain_factory=None):
    ...
    self._onchain_factory = onchain_factory

# In _run_refresh_inner, after building stablecoin_registry:
if self._onchain_factory:
    try:
        self._onchain_factory.set_stablecoin_registry(stablecoin_registry)
    except Exception as exc:
        logger.warning("onchain_registry_update_failed: %s", exc)
```

- [ ] **Step 3: Wire in main.py**

In `main.py`, pass `onchain_factory=onchain_factory` to `PoolRefreshTask`.

- [ ] **Step 4: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 4: B7 — Write refresh_log entries

**Files:**
- Modify: `scanner/pool_refresh_task.py` — INSERT/UPDATE refresh_log in _run_refresh_inner
- Test: `tests/unit/test_pool_refresh_task.py` — verify refresh_log written

- [ ] **Step 1: Add refresh_log INSERT at start of refresh**

In `_run_refresh_inner`, after `conn.execute("PRAGMA busy_timeout=15000;")`:
```python
refresh_log_id = conn.execute(
    "INSERT INTO refresh_log (started_at, status) VALUES (?, 'running')",
    (now_ts,),
).lastrowid
```

- [ ] **Step 2: Add refresh_log UPDATE at end**

Before `conn.commit()`:
```python
status = "completed"
if skip_pools_delete and old_pools_count > 0:
    status = "completed_with_stale_pools"

conn.execute(
    """UPDATE refresh_log SET finished_at=?, status=?, error=?,
       pools_count=?, mexc_assets_count=?, stablecoins_count=?
     WHERE id=?""",
    (int(time.time()), status, None, written, len(assets), total_stablecoin_records, refresh_log_id),
)
```

- [ ] **Step 3: Add refresh_log UPDATE on error**

In the `except Exception` block:
```python
if refresh_log_id is not None:
    try:
        conn.execute(
            "UPDATE refresh_log SET finished_at=?, status='failed', error=? WHERE id=?",
            (int(time.time()), str(exc)[:1000], refresh_log_id),
        )
        conn.commit()
    except Exception:
        pass
```

- [ ] **Step 4: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 5: C3 — V2 token0 cache

**Files:**
- Modify: `dex/v2_adapter.py` — add `_token0_cache` dict, cache token0() result
- Test: `tests/unit/test_v2_adapter.py` — verify cache behavior

- [ ] **Step 1: Add token0 cache to V2Adapter**

In `dex/v2_adapter.py`:
```python
class V2Adapter(BaseDexAdapter):
    def __init__(self, web3_factory, router_address: str, default_fee_bps: int):
        ...
        self._token0_cache: dict[str, str] = {}  # ADD

    async def _get_token0(self, network: str, pool_address: str) -> str | None:
        """Get token0 of a pool (cached). Immutable on-chain."""
        cached = self._token0_cache.get(pool_address)
        if cached is not None:
            return cached
        
        w3 = self._web3_factory(network)
        if w3 is None:
            return None
        try:
            pair = w3.eth.contract(
                address=w3.to_checksum_address(pool_address),
                abi=UNISWAP_V2_PAIR_ABI,
            )
            token0 = await pair.functions.token0().call()
            self._token0_cache[pool_address] = token0.lower()
            return token0.lower()
        except Exception:
            return None

    # In _quote_from_reserves, replace direct token0() call:
    token0 = await self._get_token0(network, pool_address)
```

- [ ] **Step 2: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 6: C4 — DexScreener rate limiter

**Files:**
- Modify: `discovery/dexscreener_source.py` — add SimpleRateLimiter
- Test: `tests/unit/test_dexscreener_source.py` — verify limiter called

- [ ] **Step 1: Add rate limiter to DexScreenerSource**

In `discovery/dexscreener_source.py`:
```python
from utils.rate_limiter import SimpleRateLimiter

class DexScreenerSource(BasePoolSource):
    def __init__(self, ..., rate_limit_config: dict | None = None):
        ...
        rpm = 300
        burst = 5
        if rate_limit_config:
            rpm = rate_limit_config.get("requests_per_minute", 300)
            burst = rate_limit_config.get("burst", 5)
        self._rate_limiter = SimpleRateLimiter(rpm, burst)
    
    async def fetch_pools_by_token(self, ...):
        await self._rate_limiter.acquire()  # ADD
        ...
```

- [ ] **Step 2: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 7: C6 — Gas estimate from V3 quoter

**Files:**
- Modify: `dex/v3_adapter.py` — return gasEstimate from quoter result[3]
- Modify: `dex/base_adapter.py` — add optional `gas_estimate` to result
- Modify: `services/fee_service.py` — use gas_estimate if available
- Test: `tests/unit/test_v3_adapter.py` — verify gas estimate extracted

- [ ] **Step 1: Return gas_estimate from V3Adapter.quote_exact_input**

In `dex/v3_adapter.py`, change quote_exact_input to return a tuple or extend the result:
```python
result = await quoter.functions.quoteExactInputSingle(params).call()
amount_out = Decimal(result[0])
self._last_gas_estimate = int(result[3]) if len(result) > 3 else None
return amount_out
```

Add a method:
```python
def get_last_gas_estimate(self) -> int | None:
    return getattr(self, '_last_gas_estimate', None)
```

- [ ] **Step 2: Update FeeService to use gas estimate**

In `services/fee_service.py`, in `_estimate_gas_cost_usd`:
```python
# Accept optional gas_units override
gas_units = gas_estimate_override or Decimal("200000")
```

- [ ] **Step 3: Update scanner to pass gas estimate**

In `scanner/scanner.py`, after quote:
```python
gas_estimate = getattr(adapter, 'get_last_gas_estimate', lambda: None)()
```

- [ ] **Step 4: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 8: D1 — PriceService TTL check + graceful refresh

**Files:**
- Modify: `services/price_service.py` — add `is_cache_expired()` and `refresh_if_expired()` methods
- Modify: `scanner/scanner.py` — use `refresh_if_expired()` instead of `refresh_all_prices()`
- Test: `tests/unit/test_price_service.py` — add TTL tests

- [ ] **Step 1: Add TTL methods to PriceService**

```python
def is_cache_expired(self) -> bool:
    return time.time() - self._updated_at > self._cache_ttl_sec

async def refresh_if_expired(self) -> bool:
    """Refresh only if cache is expired. Returns True if refreshed."""
    if self.is_cache_expired():
        await self.refresh_all_prices()
        return True
    return False
```

- [ ] **Step 2: Graceful failure in scanner**

In `scanner/scanner.py`:
```python
try:
    await self._price_service.refresh_if_expired()
except Exception as exc:
    logger.warning("price_refresh_failed_using_stale_cache: %s", exc)
    # Continue with stale cache if available
```

- [ ] **Step 3: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 9: C1 — MulticallClient with tryAggregate

**Files:**
- Create: `clients/multicall_client.py` — MulticallClient using Multicall3 tryAggregate
- Modify: `clients/rpc_client.py` — expose eth_call for multicall use
- Test: `tests/unit/test_multicall_client.py` — mock-based tests

- [ ] **Step 1: Create MulticallClient**

```python
# clients/multicall_client.py
import logging
from typing import Callable

logger = logging.getLogger(__name__)

# Multicall3 ABI for tryAggregate
MULTICALL3_ABI = [...]  # Minimal ABI with tryAggregate function

class MulticallClient:
    """Aggregates multiple eth_calls into a single batch via Multicall3."""
    
    def __init__(self, rpc_client_factory: Callable):
        self._rpc_factory = rpc_client_factory
        self._multicall_addresses = {
            "ETHEREUM": "0xcA11bde05977b3631167028862bE2a173976CA11",
            "BSC": "0xcA11bde05977b3631167028862bE2a173976CA11",
            "POLYGON": "0xcA11bde05977b3631167028862bE2a173976CA11",
            "ARBITRUM": "0xcA11bde05977b3631167028862bE2a173976CA11",
            "ROBINHOOD": "0x2cAC2D899eCC914d704FeaAE33ac1bF36277DaD1",
        }
    
    async def try_aggregate(
        self,
        network: str,
        calls: list[tuple[str, str]],  # [(to_address, calldata), ...]
        batch_size: int = 50,
    ) -> list[tuple[bool, bytes]]:
        """Call tryAggregate on Multicall3.
        
        Returns list of (success, return_data) tuples.
        """
        multicall_addr = self._multicall_addresses.get(network)
        if not multicall_addr:
            return [(False, b"") for _ in calls]
        
        rpc = self._rpc_factory(network)
        if rpc is None:
            return [(False, b"") for _ in calls]
        
        results = []
        for i in range(0, len(calls), batch_size):
            batch = calls[i:i + batch_size]
            # Encode tryAggregate call
            encoded_calls = [
                (False, to_addr, calldata)
                for to_addr, calldata in batch
            ]
            # ... encode and send via eth_call
            # ... decode results
            pass
        return results
```

- [ ] **Step 2: Write test**

```python
@pytest.mark.asyncio
async def test_multicall_batches_calls():
    client = MulticallClient(lambda n: AsyncMock())
    calls = [(f"0x{i:040x}", "0x1234") for i in range(75)]
    results = await client.try_aggregate("ETHEREUM", calls, batch_size=50)
    assert len(results) == 75
```

- [ ] **Step 3: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 10: C9 — Wrap blocking SQLite calls in asyncio.to_thread

**Files:**
- Modify: `scanner/pool_refresh_task.py` — wrap heavy DB writes in to_thread
- Modify: `storage/backup_manager.py` — wrap blocking operations

- [ ] **Step 1: Wrap heavy DB writes in to_thread**

In `pool_refresh_task.py`:
```python
# Instead of direct sqlite3 calls, wrap in a sync function:
def _sync_write_to_db(...):
    conn = sqlite3.connect(active_db_path)
    conn.execute("BEGIN IMMEDIATE;")
    # ... all INSERT statements ...
    conn.commit()
    return written

# Call via:
written = await asyncio.to_thread(self._sync_write_to_db, active_db_path, ...)
```

- [ ] **Step 2: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 11: D4, G5, G6 — Minor correctness fixes

**Files:**
- Modify: `services/fee_service.py` — D4: set deposit_fee_default = 0 explicitly
- Modify: `scanner/scanner.py` — G5: handle V4 pool skip with warning + counter
- Modify: `discovery/source_manager.py` — G6: skip optional unimplemented sources

- [ ] **Step 1: D4 — Explicit deposit fee default**

In `services/fee_service.py`:
```python
mexc_deposit_fee_usd = Decimal("0")  # MEXC deposit fee is 0 by default
```

- [ ] **Step 2: G5 — V4 pool skip handling**

In `scanner/scanner.py`, in `process_pool()`:
```python
if dex_id == "uniswap_v4" or dex_id.endswith("_v4"):
    quotes_failed += 1
    logger.info("v4_pool_skipped: %s", pool_address)
    return
```

- [ ] **Step 3: G6 — Skip unimplemented sources**

In `discovery/source_manager.py`:
```python
# In __init__, only register implemented sources:
IMPLEMENTED_SOURCES = {"dexscreener", "geckoterminal", "onchain_factory"}
sources_to_register = {
    name: source for name, source in all_sources.items()
    if name in IMPLEMENTED_SOURCES
}
```

- [ ] **Step 4: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 12: G2 — Update health metrics from scanner and refresh

**Files:**
- Modify: `scanner/scanner.py` — call update_metrics after each cycle
- Modify: `scanner/pool_refresh_task.py` — call update_metrics after refresh
- Modify: `services/signal_writer.py` — call update_metrics when signal written

- [ ] **Step 1: Update metrics in scanner**

At end of `_execute_cycle()`:
```python
from metrics.health import update_metrics
update_metrics(
    signals_total=signals_found,
    scanner_cycles=timer.get_count(),  # or similar
    pools_cached=len(pool_list),
    last_scanner_cycle_ts=time.time(),
)
```

- [ ] **Step 2: Update metrics in pool_refresh**

At end of `_run_refresh_inner()`:
```python
update_metrics(
    pools_in_db=written,
    mexc_assets=len(assets),
    stablecoins=total_stablecoin_records,
)
```

- [ ] **Step 3: Update metrics on signal**

In `signal_writer.py`:
```python
update_metrics(last_signal_ts=time.time())
```

- [ ] **Step 4: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 13: G3 — JSONL log rotation

**Files:**
- Create: `utils/jsonl_rotator.py` — RotatingFileHandler for JSONL
- Modify: `scanner/signal_writer.py` — use JsonlRotator
- Test: `tests/unit/test_jsonl_rotator.py`

- [ ] **Step 1: Create JsonlRotator**

```python
# utils/jsonl_rotator.py
import os
import glob
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class JsonlRotator:
    """Rotates JSONL files by size, keeping N most recent files."""
    
    def __init__(self, filepath: str, max_size_mb: int = 50, keep: int = 5):
        self._path = Path(filepath)
        self._max_size_bytes = max_size_mb * 1024 * 1024
        self._keep = keep
    
    def write_line(self, line: str) -> None:
        """Append a JSON line, rotating if needed."""
        if self._path.exists() and self._path.stat().st_size > self._max_size_bytes:
            self._rotate()
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    
    def _rotate(self) -> None:
        """Rename current file and clean up old files."""
        if not self._path.exists():
            return
        
        # Shift .N files up: .2 → .3, .1 → .2
        for i in range(self._keep - 1, 0, -1):
            old = self._path.with_suffix(f".{i}.jsonl")
            newer = self._path.with_suffix(f".{i + 1}.jsonl")
            if old.exists():
                os.replace(str(old), str(newer))
        
        # Rename current → .1
        first = self._path.with_suffix(".1.jsonl")
        os.replace(str(self._path), str(first))
        
        # Delete files beyond keep
        for f in sorted(self._path.parent.glob(f"{self._path.stem}.*.jsonl")):
            parts = f.suffixes  # [".N", ".jsonl"]
            if len(parts) >= 2 and parts[0].lstrip(".").isdigit():
                num = int(parts[0].lstrip("."))
                if num > self._keep:
                    f.unlink()
```

- [ ] **Step 2: Integrate into SignalWriter**

```python
from utils.jsonl_rotator import JsonlRotator

class SignalWriter:
    def __init__(self, ..., jsonl_path: str = "data/signals.jsonl"):
        ...
        self._rotator = JsonlRotator(jsonl_path)
    
    async def write_signal(self, signal):
        ...
        self._rotator.write_line(signal.model_dump_json())
```

- [ ] **Step 3: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 14: G4 — GUI busy_timeout + read-only mode

**Files:**
- Modify: `gui/dashboard.py` — add timeout=5 and read-only URI

- [ ] **Step 1: Fix DB connection in dashboard**

```python
def _db_connect(self):
    try:
        db = Path("data/state/active.sqlite3")
        if not db.exists():
            return None
        # Use read-only URI mode to avoid locking issues
        uri = f"file:{db.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        return conn
    except sqlite3.OperationalError:
        return None
```

- [ ] **Step 2: Wrap all queries in try/except**

```python
def _safe_query(self, query):
    try:
        conn = self._db_connect()
        if conn is None:
            return []
        rows = conn.execute(query).fetchall()
        conn.close()
        return rows
    except Exception:
        return []
```

- [ ] **Step 3: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 15: F1 — Fix broken unit tests

**Files:**
- Modify: `tests/unit/test_rate_limiter.py` — fix async method ordering
- Modify: various test files — fix `total=` in FeeBreakdown construction

- [ ] **Step 1: Fix test_rate_limiter.py**

```python
# Move async helper to module level before it's called
async def _acquire_once(limiter):
    await limiter.acquire()

class TestRateLimiter:
    def test_acquire(self):
        limiter = SimpleRateLimiter(300, 5)
        asyncio.run(_acquire_once(limiter))  # Works because function is defined
```

- [ ] **Step 2: Fix FeeBreakdown total= parameter**

In all test files, change:
```python
FeeBreakdown(..., total=Decimal("0.06"))
```
to:
```python
fees = FeeBreakdown(...)
assert fees.total() == Decimal("0.06")
```

- [ ] **Step 3: Run tests**

Run: `pytest -m "not integration" -q`
Expected: all PASS

---

### Task 16: F2, F3 — Add missing tests

**Files:**
- Create: `tests/unit/test_token_meta.py` — decimals tests
- Modify: `tests/unit/test_scanner.py` — add Direction B test

- [ ] **Step 1: Write decimals tests**

```python
# tests/unit/test_token_meta.py
@pytest.mark.asyncio
async def test_get_decimals_cached():
    service = TokenMetaService(mock_rpc_factory)
    result = await service.get_decimals("ETH", "0xusdc")
    assert result == 6

@pytest.mark.asyncio
async def test_floor_to_wei_usdc():
    result = floor_to_wei(Decimal("10"), 6)
    assert result == 10_000_000
```

- [ ] **Step 2: Add Direction B test in test_scanner.py**

```python
@pytest.mark.asyncio
async def test_scanner_direction_b_called():
    # Mock adapter to return quote for token→stable
    # Verify signal written with direction="MEXC_BUY_DEX_SELL"
    ...
```

- [ ] **Step 3: Run tests**

Run: `pytest -m "not integration" -q`
Expected: PASS

---

### Task 17: G1, G7 — Docs and config alignment

**Files:**
- Modify: `.env.example` — align defaults with settings.py
- Modify: `STATE.md` — comprehensive status update

- [ ] **Step 1: Align .env.example with settings.py defaults**

```bash
# POOL_SOURCE_PRIORITY=dexscreener,onchain_factory
# GeckoTerminal free tier rate-limits heavily.
# POOL_SOURCE_PRIORITY=dexscreener,geckoterminal,onchain_factory

BUSY_TIMEOUT_MS=15000
```

- [ ] **Step 2: Update STATE.md**

See STATE.md update in root — track all completed and remaining tasks.

- [ ] **Step 3: Run compile check**

Run: `python -m compileall . -q`
Expected: no errors

---

### Task 18: Final test run and verification

- [ ] **Step 1: Run full unit test suite**

```bash
pytest -m "not integration" -q
```
Expected: all pass

- [ ] **Step 2: Run compile check**

```bash
python -m compileall . -q
```
Expected: no errors

- [ ] **Step 3: Summary report**

Document the final state with counts of completed vs remaining items.

---

## Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| PoolDetector RPC call slows refresh | Increased refresh time | Only called for new pools, results cached in DB |
| MulticallClient complex ABI encoding | Implementation bugs | Start with working minimal eth_call encoding, test with mocks |
| JSONL rotation breaks on Windows | File rename errors | Use os.replace for atomic renames, add Windows-specific testing |
| refresh_log added without migration | Schema mismatch | Use INSERT OR REPLACE, table already exists in schema |
| Asyncio.to_thread causes path issues | Windows path sep | Use string paths, not Path objects in thread |

## Deferred (not planned for this phase)

| Finding | Reason |
|---|---|
| C7 — Structured adapter results (DexQuoteResult) | High effort, low incremental benefit over current Decimal("0") pattern |
| E2 — Fee-on-transfer detection | Complex on-chain simulation, needs multicall |
| E3 — Structured warnings format | Low priority, string format works |
| F4 — Dry-run integration tests | Needs real RPC keys, deferred to CI setup |
| B5 — pools_cache fields | Partially done (stablecoin_coin, decimals, pool_version planned in Task 1) |
| B6 — Atomic replace vs direct commit | Current direct commit with backup is acceptable |
| B8 — Volume filter default | Already configurable, low impact |
| D3 — MEXC auth test | Integration test, needs real keys |
