# Autonomous Decisions

This file contains decisions made automatically by the implementation agent.

## DECISION-002 — TODO_VERIFY Address Resolution

**Context:** The project had several DEX contract addresses marked as TODO_VERIFY (placeholder), causing those DEXs to be skipped at runtime. Additionally, the SushiSwap V2 Router on Arbitrum was set to a non-standard address.

**Decision:** Researched and filled all placeholder addresses with verified contract addresses from official documentation:

| DEX | Network | Contract | Address | Source |
|---|---|---|---|---|
| Uniswap V4 PoolManager | Ethereum | PoolManager | `0x000000000004444c5dc75cB358380D2e3dE08A90` | Uniswap official docs |
| Uniswap V4 Quoter | Ethereum | Quoter | `0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203` | Uniswap official docs |
| SushiSwap V2 Factory | BSC | Factory | `0xc35dadb65012ec5796536bd9864ed8773abc74c4` | SushiSwap official docs |
| SushiSwap V2 Factory | Polygon | Factory | `0xc35dadb65012ec5796536bd9864ed8773abc74c4` | SushiSwap official docs |
| SushiSwap V2 Factory | Arbitrum | Factory | `0xc35dadb65012ec5796536bd9864ed8773abc74c4` | SushiSwap official docs |
| QuickSwap V3 Factory | Polygon | Factory | `0x411b0fAcC3489691f28ad58c47006AF5E3Ab3A28` | QuickSwap official docs |
| QuickSwap V3 QuoterV2 | Polygon | QuoterV2 | `0xa062c2754864F67a259b346D0D7567b2ed406e6E` | QuickSwap official docs |
| Camelot V2 Factory | Arbitrum | Factory | `0x6EcCab422D763aC031210895C81787E87B43A652` | Camelot official docs |

**Bugfix:** SushiSwap V2 Router on Arbitrum corrected from `0xf2614A233c7C3e7f08b1F887Ba133a13f1eb2c55` (incorrect) to `0x1b02da8cb0d097eb8d57a175b88c7d8b47997506` (canonical SushiSwap V2 Router, same as BSC and Polygon).

**Reason:** All addresses were sourced from respective official DEX documentation. The SushiSwap V2 Router is identical across all SushiSwap V2 deployments.

**Affected files:**
- `config/dex_registry.py` — filled placeholders, fixed Arbitrum router
- `FINAL_REPORT.md` — updated known items section
- `DECISIONS.md` — this entry
