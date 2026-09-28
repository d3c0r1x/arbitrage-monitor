# MEXC × DEX Arbitrage Monitor

**Asynchronous monitor for cross-market and cross-DEX arbitrage opportunities.**

The repository documents an iterative V1 → V6 development path. The current version is **V6**.

> Default mode is monitoring only. Trade execution is opt-in and defaults to dry-run.

## What it does

- discovers token/pool opportunities across MEXC and on-chain DEXs;
- obtains on-chain swap quotes;
- accounts for fees and slippage;
- sweeps trade sizes to find the better-sized opportunity;
- produces signals and watches them over time;
- supports cross-DEX and multi-hop scans;
- exposes a web dashboard and operational status.

## Architecture

```
market/token sources
      ↓
pool discovery
      ↓
on-chain quotes
      ↓
fees + slippage + size sweep
      ↓
net profitability
      ↓
signal / watcher
      ↓
web dashboard
```

## Engineering highlights

**Multiple market models.** Supports CEX order-book data and AMM-style DEX pricing.

**Size matters.** The monitor does not treat the displayed price as the trade result; it checks different trade sizes and incorporates execution costs.

**Fail-closed behaviour.** Configuration and numeric metadata are validated before a signal is trusted.

**Operational safety.** Circuit breakers, blacklists and dry-run defaults reduce the chance that an operational problem becomes a live trade.

## Current scope

V6 covers MEXC↔DEX, DEX↔DEX, cross-DEX and multi-hop monitoring. The repository contains dedicated versions, review notes and tests so the evolution of the system can be inspected rather than inferred from a single final snapshot.

## Stack

Python 3.12 · async IO · Web3/EVM · MEXC API · Uniswap/PancakeSwap/Aerodrome · pytest · web dashboard

## Tests

The V6 snapshot contains **40 test files**. Earlier versions have their own test suites.

## Security notes

Credentials are supplied through environment variables and excluded from the repository. Historical development credentials were revoked and redacted; the current repository does not contain live keys.

## Local run

```bash
cd V6
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Limitations

This is a monitoring/research project, not a guaranteed profitable trading system. Execution depends on network conditions, liquidity, fees, latency and external APIs. Signals are estimates and can become stale.

## AI-assisted development

AI was used for implementation drafts, routine modules and test ideas. I owned the decomposition, architecture, review decisions, debugging, validation and final behaviour.
