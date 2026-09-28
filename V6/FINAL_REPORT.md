# Final Report

## Status
Completed.

## Completed Phases
- Phase 0: Scaffold
- Phase 1: Config
- Phase 2: Models
- Phase 3: Utils
- Phase 4: MEXC client
- Phase 5: Stablecoin registry
- Phase 6: Storage
- Phase 7: Pool discovery and source failover
- Phase 8: RPC and ABI
- Phase 9: DEX adapters
- Phase 10: Fees and profit
- Phase 11: Security
- Phase 12: Metrics
- Phase 13: Scanner
- Phase 14: Main
- Phase 15: Tests and docs

## Tests
- Unit tests passed (config, MEXC parsing, stablecoin registry, profit calculator, storage, decimal utils).
- Integration tests available but disabled by default (RUN_INTEGRATION=1).

## Performance
- Stage metrics implemented (performance_timer context manager).
- Metrics written to data/performance.jsonl.
- Discovery failover implemented (DexScreener → GeckoTerminal → on-chain factory).
- Source health tracking with consecutive failure counting.
- Stale pool grace period implemented.
- Adaptive concurrency controller implemented.
- Scanner cycle overrun protection via asyncio.Lock.

## Resolved Addresses
All previously placeholder addresses (marked TODO_VERIFY) have been filled with verified contract addresses:
- **Uniswap V4** (Ethereum): PoolManager `0x0000...8A90`, Quoter `0x52f0...1203`
- **SushiSwap V2** (BSC, Polygon, Arbitrum): Factory `0xc35d...74c4`
- **QuickSwap V3** (Polygon): Factory `0x411b...3A28`, QuoterV2 `0xa062...6e6E`
- **Camelot V2** (Arbitrum): Factory `0x6EcC...A652`

Plus a bugfix: **SushiSwap V2 Router on Arbitrum** corrected from `0xf261...c55` to `0x1b02...7506` (the canonical SushiSwap V2 Router address).

## How to run
```bash
python main.py
```

## Notes
- Monitoring only — no execution.
- On-chain pool discovery requires RPC access to the target network.
- DexScreener and GeckoTerminal are primary discovery sources; on-chain factory is fallback.
- Only token contract addresses are used for discovery — no symbol-based search.
- All monetary values use Decimal — no float money fields.
- Security warnings never remove signals.
