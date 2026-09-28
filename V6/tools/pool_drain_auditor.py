#!/usr/bin/env python3
"""
Pool liquidity-drain RISK auditor (defensive / educational).

Loads BSC pools from:
  - data/pcs_v2_pools_full.csv
  - data/dex_dex_pools_cache.json
  - data/pools_cache.json
Keeps pools with liquidity_usd > 0 (estimates reserves when needed).

Outputs per-pool vulns relevant to third-party / privileged liquidity loss,
plus EDUCATIONAL attack_flow text (preconditions → steps → impact).

DOES NOT:
  - build/sign/send exploit transactions
  - generate working drain calldata
  - execute attacks

Usage:
  python tools/pool_drain_auditor.py --auto --limit 30
  python tools/pool_drain_auditor.py --auto --min-liq 1000 --out data/pool_liq_drain_audit.json

Env: DRPC_KEY, ALCHEMY_KEY(_1/_2), INFURA_KEY, optional GOPLUS_APP_KEY/GOPLUS_APP_SECRET
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
import orjson

log = logging.getLogger("pool_drain_audit")
_ROOT = Path(__file__).resolve().parent.parent

# ── BSC constants ────────────────────────────────────────────────────────────
USDT = "0x55d398326f99059ff775485246999027b3197955"
USDC = "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"
BUSD = "0xe9e7cea3dedca5984780bafc599bd69add087d56"
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
ETH = "0x2170ed0880ac9a755fd29b2688956bd959f933f8"
BTCB = "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c"
STABLES = frozenset({USDT, USDC, BUSD})
QUOTES = STABLES | {WBNB, ETH, BTCB}

SEL_OWNER = "8da5cb5b"
SEL_IMPL = "5c60da1b"
SEL_PAUSED = "5c975abb"
SEL_GET_RESERVES = "0902f1ac"
SEL_TOKEN0 = "0dfe1681"
SEL_TOKEN1 = "d21220a7"
EIP1967_IMPL_SLOT = (
    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
)

SELECTORS: dict[str, str] = {
    "8da5cb5b": "owner()",
    "f2fde38b": "transferOwnership(address)",
    "715018a6": "renounceOwnership()",
    "3659cfe6": "upgradeTo(address)",
    "4f1ef286": "upgradeToAndCall(address,bytes)",
    "5c60da1b": "implementation()",
    "8456cb59": "pause()",
    "3f4ba83a": "unpause()",
    "5c975abb": "paused()",
    "f9f92be4": "isBlacklisted(address)",
    "9c8f9db5": "blacklist(address)",
    "e4997dc5": "setBlacklist(address,bool)",
    "8a8c523c": "enableTrading()",
    "8f70ccf7": "setTradingEnabled(bool)",
    "c9567bf9": "openTrading()",
    "5e3532db": "setFees(...)",
    "c49b9a80": "setTaxFee(uint256)",
    "7d1db4a5": "setMaxTxPercent(uint256)",
    "74010ece": "setMaxTxAmount(uint256)",
    "51cff8d9": "withdraw(address)",
    "3ccfd60b": "withdraw()",
    "e086e5ec": "withdrawBNB()",
    "f14210a6": "withdrawToken(address,uint256)",
    "01339c21": "rescueToken(address,uint256)",
    "40c10f19": "mint(address,uint256)",
    "a0712d68": "mint(uint256)",
    "a9059cbb": "transfer(address,uint256)",
    "23b872dd": "transferFrom(address,address,uint256)",
    "095ea7b3": "approve(address,uint256)",
    "022c0d9f": "swap(uint256,uint256,address,bytes)",
    "fff6cae9": "sync()",
    "bc25cf77": "skim(address)",
    "89afcb44": "burn(address)",
    "6a627842": "mint(address)",  # pair LP mint
}

# Educational flows only — no calldata / no executable attack steps.
ATTACK_FLOWS: dict[str, dict[str, str]] = {
    "PRIV_RESCUE_WITHDRAW": {
        "name": "Privileged rescue / withdraw",
        "preconditions": "Caller holds owner/admin; contract custody holds ERC20/BNB (or LP).",
        "flow": (
            "1) Attacker obtains/compromises owner key OR is the deployer. "
            "2) Calls privileged withdraw/rescue. "
            "3) Tokens leave the contract to attacker wallet. "
            "If the contract held LP or pair inventory, pool-side value is gone."
        ),
        "impact": "Direct theft of custody balances; LP holders may be left with worthless claim.",
        "demo_safe": "On YOUR deploy: as owner call rescue on testnet and show balances before/after.",
    },
    "UPGRADEABLE_PROXY": {
        "name": "Malicious implementation upgrade",
        "preconditions": "Proxy admin/owner can upgradeTo; users approved old impl or LP sits behind proxy token.",
        "flow": (
            "1) Privileged role upgrades implementation to a contract with drain/backdoor. "
            "2) New logic uses existing allowances / storage to move funds. "
            "3) Liquidity or user wallets interacting with the token are drained."
        ),
        "impact": "Past audit of old impl is irrelevant after upgrade.",
        "demo_safe": "On YOUR proxy: upgrade to a harmless 'TransferEventOnly' impl on testnet and show codehash change.",
    },
    "MINT_DILUTION": {
        "name": "Inflation / mint-into-pool dump",
        "preconditions": "minter/owner can mint; token is one side of an AMM pool.",
        "flow": (
            "1) Mint large supply to attacker. "
            "2) Sell into the pool (swap token→quote). "
            "3) Reserves skew; LP value in quote is extracted."
        ),
        "impact": "Pool quote side drained via market sell; not a pair bug — token privilege.",
        "demo_safe": "On YOUR token: mint small amount on testnet pool and show price impact (do not target mainnet strangers).",
    },
    "HONEYPOT_SELL_BLOCK": {
        "name": "Sell block / blacklist / pause honeypot",
        "preconditions": "Trading flag, blacklist, or pause controllable; buyers already added LP/bought.",
        "flow": (
            "1) Allow buys / LP add while sells work or appear to work. "
            "2) Flip trading off / blacklist router / pause. "
            "3) Victims cannot exit; privileged party can still move value via tax wallet or unlock."
        ),
        "impact": "Liquidity trapped; economic drain via inability to exit + insider sells.",
        "demo_safe": "On YOUR token testnet: toggle trading flag and show a sell tx revert.",
    },
    "MUTABLE_TAX_SOFT_RUG": {
        "name": "Tax raised to brick sells (soft rug)",
        "preconditions": "Owner can set buy/sell tax or maxTx after LP is live.",
        "flow": (
            "1) Launch with low tax. "
            "2) Raise sell tax near 100% or maxTx to dust. "
            "3) Retail cannot sell; insider addresses excluded from tax dump into pool."
        ),
        "impact": "Effective theft of pool upside / trapped LP.",
        "demo_safe": "On YOUR token: setFee high on testnet and show sell reverts or tiny output.",
    },
    "NONSTANDARD_PAIR": {
        "name": "Non-canonical V2 pair bytecode",
        "preconditions": "Pair codehash ≠ official PCS/Uni V2 pair; custom hooks possible.",
        "flow": (
            "1) Custom pair may alter swap/mint/burn accounting. "
            "2) Attacker triggers skewed swap/skim path. "
            "3) Reserves moved contrary to x*y=k expectations."
        ),
        "impact": "Direct pool accounting drain if pair is malicious fork.",
        "demo_safe": "Compare pair codehash to official factory pair; flag divergence for manual review.",
    },
    "EXTERNAL_SECURITY_API": {
        "name": "Third-party security API flagged",
        "preconditions": "GoPlus / similar marks honeypot, high tax, or proxy risk.",
        "flow": "Treat as triage signal — combine with on-chain owner/tax/proxy checks before concluding drainability.",
        "impact": "Varies; API false positives exist.",
        "demo_safe": "Show API JSON next to live owner() and paused() results.",
    },
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def build_rpcs() -> list[str]:
    load_dotenv(_ROOT / ".env")
    urls: list[str] = []
    if os.environ.get("DRPC_KEY", "").strip():
        urls.append(f"https://lb.drpc.live/bsc/{os.environ['DRPC_KEY'].strip()}")
    for k in (
        os.environ.get("ALCHEMY_KEY", ""),
        os.environ.get("ALCHEMY_KEY_1", ""),
        os.environ.get("ALCHEMY_KEY_2", ""),
    ):
        if k.strip():
            urls.append(f"https://bnb-mainnet.g.alchemy.com/v2/{k.strip()}")
    if os.environ.get("INFURA_KEY", "").strip():
        urls.append(f"https://bsc-mainnet.infura.io/v3/{os.environ['INFURA_KEY'].strip()}")
    urls += [
        "https://bsc-dataseed.binance.org",
        "https://bsc-dataseed1.binance.org",
        "https://bsc-dataseed2.binance.org",
        "https://bsc-rpc.publicnode.com",
        "https://bsc.drpc.org",
        "https://1rpc.io/bnb",
    ]
    out, seen = [], set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


@dataclass
class Finding:
    id: str
    severity: str
    title: str
    detail: str
    evidence: list[str] = field(default_factory=list)
    attack_flow: dict[str, str] = field(default_factory=dict)
    subject: str = ""  # pair|token0|token1|impl


@dataclass
class ContractAudit:
    address: str
    role: str
    code_size: int = 0
    is_proxy: bool = False
    implementation: str = ""
    owner: str = ""
    matched_selectors: dict[str, str] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class PoolAudit:
    network: str
    pair_address: str
    token0_address: str
    token1_address: str
    token0_symbol: str
    token1_symbol: str
    liquidity_usd: float
    drain_risk: str
    risk_score: int
    vulns: list[Finding]
    contracts: list[ContractAudit]
    sources: list[str]
    educational_note: str = (
        "attack_flow fields are educational narratives for demos to stakeholders; "
        "this tool does not generate or send exploit calldata."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "network": self.network,
            "pair_address": self.pair_address,
            "token0_address": self.token0_address,
            "token1_address": self.token1_address,
            "token0_symbol": self.token0_symbol,
            "token1_symbol": self.token1_symbol,
            "liquidity_usd": self.liquidity_usd,
            "drain_risk": self.drain_risk,
            "risk_score": self.risk_score,
            "vulns": [asdict(v) for v in self.vulns],
            "contracts": [
                {
                    "address": c.address,
                    "role": c.role,
                    "code_size": c.code_size,
                    "is_proxy": c.is_proxy,
                    "implementation": c.implementation,
                    "owner": c.owner,
                    "matched_selectors": c.matched_selectors,
                    "findings": [asdict(f) for f in c.findings],
                    "notes": c.notes,
                }
                for c in self.contracts
            ],
            "sources": self.sources,
            "educational_note": self.educational_note,
        }


class RpcClient:
    def __init__(self, urls: list[str], timeout: float = 20.0):
        self.urls = urls
        self._i = 0
        self.timeout = timeout
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "RpcClient":
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            connector=aiohttp.TCPConnector(limit=80),
        )
        return self

    async def __aexit__(self, *a: Any) -> None:
        if self.session:
            await self.session.close()

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        assert self.session
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        body = orjson.dumps(payload)
        last: Exception | None = None
        for _ in range(max(6, len(self.urls))):
            url = self.urls[self._i % len(self.urls)]
            self._i += 1
            try:
                async with self.session.post(
                    url, data=body, headers={"Content-Type": "application/json"}
                ) as resp:
                    data = orjson.loads(await resp.read())
                    if "error" in data:
                        last = RuntimeError(str(data["error"]))
                        continue
                    return data.get("result")
            except Exception as exc:
                last = exc
        raise RuntimeError(f"{method} failed: {last}")

    async def eth_get_code(self, address: str) -> bytes:
        result = await self._rpc("eth_getCode", [address, "latest"]) or "0x"
        return bytes.fromhex(result[2:] if str(result).startswith("0x") else str(result))

    async def eth_call(self, to: str, data: str) -> bytes:
        result = await self._rpc("eth_call", [{"to": to, "data": data}, "latest"]) or "0x"
        s = str(result)
        if s in ("0x", "0x0"):
            return b""
        return bytes.fromhex(s[2:] if s.startswith("0x") else s)

    async def eth_get_storage(self, address: str, slot: str) -> bytes:
        result = await self._rpc("eth_getStorageAt", [address, slot, "latest"]) or "0x"
        s = str(result)
        return bytes.fromhex(s[2:] if s.startswith("0x") else s)


def _norm(addr: str) -> str:
    a = (addr or "").strip().lower()
    if a.startswith("0x") and len(a) == 42:
        return a
    return ""


def find_selectors(code_hex: str) -> dict[str, str]:
    found = {}
    for sel, name in SELECTORS.items():
        if ("63" + sel) in code_hex or sel in code_hex:
            found[sel] = name
    return found


def _flow(fid: str) -> dict[str, str]:
    return dict(ATTACK_FLOWS.get(fid, {
        "name": fid,
        "preconditions": "See evidence.",
        "flow": "Manual review required.",
        "impact": "Potential liquidity loss / lock.",
        "demo_safe": "Reproduce only on owned testnet deployments.",
    }))


def analyze_code(address: str, role: str, code: bytes) -> ContractAudit:
    ca = ContractAudit(address=address, role=role, code_size=len(code))
    if len(code) == 0:
        ca.findings.append(Finding(
            "EMPTY_CODE", "critical", "Empty bytecode",
            "EOA or destroyed contract.", ["code_size=0"],
            _flow("HONEYPOT_SELL_BLOCK"), role,
        ))
        return ca

    hx = code.hex()
    matched = find_selectors(hx)
    ca.matched_selectors = matched
    has_owner_sel = SEL_OWNER in matched

    if len(code) < 100:
        ca.findings.append(Finding(
            "TINY_BYTECODE", "high", "Tiny bytecode",
            "Likely proxy stub — follow implementation.",
            [f"size={len(code)}"], _flow("UPGRADEABLE_PROXY"), role,
        ))

    if role.startswith("token") and "a9059cbb" not in matched and "23b872dd" not in matched:
        ca.findings.append(Finding(
            "NO_ERC20_TRANSFER", "critical", "No standard ERC20 transfer",
            "Cannot behave as normal pool token.",
            ["missing transfer/transferFrom"], _flow("HONEYPOT_SELL_BLOCK"), role,
        ))

    def add(fid: str, sev: str, title: str, detail: str, sels: list[str], flow_id: str) -> None:
        hit = [s for s in sels if s in matched]
        if hit:
            ca.findings.append(Finding(
                fid, sev, title, detail,
                [f"{s}={matched[s]}" for s in hit], _flow(flow_id), role,
            ))

    add("PRIV_RESCUE_WITHDRAW", "critical", "Privileged withdraw/rescue",
        "Owner-gated pull of tokens/BNB from contract custody.",
        ["51cff8d9", "3ccfd60b", "e086e5ec", "f14210a6", "01339c21"],
        "PRIV_RESCUE_WITHDRAW")
    add("UPGRADEABLE_PROXY", "critical", "Upgradeable surface",
        "Logic can be replaced post-audit.",
        ["3659cfe6", "4f1ef286", "5c60da1b"], "UPGRADEABLE_PROXY")
    add("MINT_PRIVILEGE", "high" if has_owner_sel else "medium", "Mint capability",
        "Inflation can dump into pool (legit emission tokens also match — review context).",
        ["40c10f19", "a0712d68"], "MINT_DILUTION")
    add("TRADING_SWITCH", "high", "Trading enable switch",
        "Can brick exits after LP is taken.",
        ["8a8c523c", "8f70ccf7", "c9567bf9"], "HONEYPOT_SELL_BLOCK")
    add("BLACKLIST_PAUSE", "high", "Blacklist/pause",
        "Can freeze sellers or router.",
        ["8456cb59", "5c975abb", "f9f92be4", "9c8f9db5", "e4997dc5"],
        "HONEYPOT_SELL_BLOCK")
    add("MUTABLE_TAX", "high", "Mutable tax/maxTx",
        "Fees/limits can be raised to soft-rug sellers.",
        ["5e3532db", "c49b9a80", "7d1db4a5", "74010ece"],
        "MUTABLE_TAX_SOFT_RUG")

    if role == "pair":
        # Canonical V2 pairs have swap+sync+skim+burn+mint
        needed = {"022c0d9f", "fff6cae9", "bc25cf77"}
        if matched and not needed.issubset(matched.keys()):
            ca.findings.append(Finding(
                "NONSTANDARD_PAIR_ABI", "high", "Pair missing canonical V2 selectors",
                "Bytecode does not look like stock UniV2/PCS pair.",
                [f"have={list(matched)[:12]}"], _flow("NONSTANDARD_PAIR"), role,
            ))
        weird = [s for s in ("51cff8d9", "3ccfd60b", "40c10f19", "3659cfe6") if s in matched]
        if weird:
            ca.findings.append(Finding(
                "PAIR_PRIVILEGED_EXTRAS", "critical", "Pair has privileged extras",
                "Stock AMM pairs should not expose owner mint/withdraw/upgrade.",
                [f"{s}={matched[s]}" for s in weird], _flow("NONSTANDARD_PAIR"), role,
            ))

    return ca


async def enrich_live(rpc: RpcClient, ca: ContractAudit) -> None:
    if SEL_OWNER in ca.matched_selectors:
        try:
            raw = await rpc.eth_call(ca.address, "0x" + SEL_OWNER)
            if len(raw) >= 32:
                owner = "0x" + raw[-20:].hex()
                ca.owner = owner
                ca.notes.append(f"live_owner={owner}")
                if owner != "0x" + "00" * 20:
                    ca.findings.append(Finding(
                        "OWNER_ACTIVE", "high", "Active non-zero owner",
                        "Privileged knobs remain live.",
                        [f"owner={owner}"], _flow("PRIV_RESCUE_WITHDRAW"), ca.role,
                    ))
                else:
                    ca.notes.append("owner renounced (zero)")
        except Exception as exc:
            ca.notes.append(f"owner_fail:{exc}")

    if SEL_PAUSED in ca.matched_selectors:
        try:
            raw = await rpc.eth_call(ca.address, "0x" + SEL_PAUSED)
            if raw and int.from_bytes(raw[-32:], "big") == 1:
                ca.findings.append(Finding(
                    "CURRENTLY_PAUSED", "critical", "Contract paused()==true",
                    "Exits may already be bricked.",
                    ["paused=true"], _flow("HONEYPOT_SELL_BLOCK"), ca.role,
                ))
        except Exception:
            pass

    # Proxy resolution
    impl = ""
    if SEL_IMPL in ca.matched_selectors:
        try:
            raw = await rpc.eth_call(ca.address, "0x" + SEL_IMPL)
            if len(raw) >= 32:
                impl = "0x" + raw[-20:].hex()
        except Exception:
            pass
    if not impl or impl == "0x" + "00" * 20:
        try:
            slot = await rpc.eth_get_storage(ca.address, EIP1967_IMPL_SLOT)
            if len(slot) >= 32:
                cand = "0x" + slot[-20:].hex()
                if cand != "0x" + "00" * 20:
                    impl = cand
        except Exception:
            pass
    if impl and impl != "0x" + "00" * 20 and impl != ca.address:
        ca.is_proxy = True
        ca.implementation = impl
        ca.findings.append(Finding(
            "PROXY_DETECTED", "high", "Proxy → implementation",
            "Audit must include implementation bytecode.",
            [f"implementation={impl}"], _flow("UPGRADEABLE_PROXY"), ca.role,
        ))


async def goplus_token(session: aiohttp.ClientSession, token: str) -> dict[str, Any]:
    url = f"https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses={token}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json(content_type=None)
            result = (data or {}).get("result") or {}
            return result.get(token.lower()) or result.get(token) or {}
    except Exception as exc:
        log.debug("goplus fail %s: %s", token, exc)
        return {}


async def dexscreener_pair(session: aiohttp.ClientSession, pair: str) -> dict[str, Any]:
    url = f"https://api.dexscreener.com/latest/dex/pairs/bsc/{pair}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json(content_type=None)
            pairs = (data or {}).get("pairs") or []
            return pairs[0] if pairs else {}
    except Exception as exc:
        log.debug("dexscreener fail %s: %s", pair, exc)
        return {}


def score_pool(vulns: list[Finding]) -> tuple[int, str]:
    w = {"critical": 45, "high": 22, "medium": 10, "low": 4, "info": 1}
    # dedup by id+subject
    seen = set()
    uniq = []
    for v in vulns:
        key = (v.id, v.subject)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(v)
    score = min(100, sum(w.get(v.severity, 5) for v in uniq))
    # Only count drain-relevant toward "drain_risk"
    if any(v.severity == "critical" for v in uniq):
        level = "critical"
    elif score >= 40 or any(v.severity == "high" for v in uniq):
        level = "high"
    elif score >= 15:
        level = "medium"
    elif score > 0:
        level = "low"
    else:
        level = "none"
    return score, level


def load_pools_from_tables(min_liq: float) -> list[dict[str, Any]]:
    """Merge pool rows from bot caches + PCS scanner CSV. BSC only, liq>min."""
    by_pair: dict[str, dict[str, Any]] = {}

    def upsert(row: dict[str, Any], source: str) -> None:
        net = str(row.get("network") or "BSC").upper()
        if net and net not in ("BSC", "BNB", "BEP20", ""):
            # pcs csv has no network → treat as BSC
            if source != "pcs_v2_pools_full.csv":
                return
        pair = _norm(str(row.get("pair_address") or row.get("pool_address") or ""))
        if not pair:
            return
        t0 = _norm(str(row.get("token0_address") or row.get("token_address") or ""))
        t1 = _norm(str(row.get("token1_address") or row.get("stablecoin_address") or ""))
        try:
            liq = float(row.get("liquidity_usd") or row.get("_liq") or 0)
        except (TypeError, ValueError):
            liq = 0.0
        prev = by_pair.get(pair)
        if prev is None or liq >= float(prev.get("liquidity_usd") or 0):
            by_pair[pair] = {
                "network": "BSC",
                "pair_address": pair,
                "token0_address": t0 or (prev or {}).get("token0_address", ""),
                "token1_address": t1 or (prev or {}).get("token1_address", ""),
                "token0_symbol": row.get("token0_symbol") or row.get("token_coin") or (prev or {}).get("token0_symbol", ""),
                "token1_symbol": row.get("token1_symbol") or row.get("stablecoin_coin") or (prev or {}).get("token1_symbol", ""),
                "liquidity_usd": liq,
                "sources": list({*((prev or {}).get("sources") or []), source}),
            }

    # PCS full table
    pcs = _ROOT / "data" / "pcs_v2_pools_full.csv"
    if pcs.exists():
        with pcs.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                upsert(row, "pcs_v2_pools_full.csv")

    for name in ("dex_dex_pools_cache.json", "pools_cache.json"):
        path = _ROOT / "data" / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, list):
            continue
        for row in data:
            if not isinstance(row, dict):
                continue
            if str(row.get("network") or "").upper() not in ("BSC", "BNB", "BEP20"):
                continue
            upsert(row, name)

    pools = [p for p in by_pair.values() if float(p.get("liquidity_usd") or 0) > min_liq]
    # Also keep rows with unknown liq=0 only if min_liq==0 and we will estimate later
    if min_liq <= 0:
        extra = [p for p in by_pair.values() if p["pair_address"] not in {x["pair_address"] for x in pools}]
        pools.extend(extra)
    pools.sort(key=lambda p: -float(p.get("liquidity_usd") or 0))
    log.info("pools_loaded: unique=%d after_liq_filter(>%s)=%d", len(by_pair), min_liq, len(pools))
    return pools


async def estimate_pair_liq(rpc: RpcClient, pair: str, t0: str, t1: str) -> float:
    try:
        raw = await rpc.eth_call(pair, "0x" + SEL_GET_RESERVES)
        if len(raw) < 64:
            return 0.0
        r0 = int.from_bytes(raw[0:32], "big")
        r1 = int.from_bytes(raw[32:64], "big")
        # crude: if stable side, 2*stable; assume 18 decimals default
        for addr, reserve in ((t0, r0), (t1, r1)):
            if addr in STABLES:
                return 2.0 * (reserve / 1e18)
        if t0 == WBNB or t1 == WBNB:
            # without price book, unknown — return tiny positive if reserves exist
            return 1.0 if (r0 > 0 and r1 > 0) else 0.0
        return 1.0 if (r0 > 0 and r1 > 0) else 0.0
    except Exception:
        return 0.0


async def audit_contract(
    rpc: RpcClient, session: aiohttp.ClientSession, address: str, role: str, deep_api: bool
) -> list[ContractAudit]:
    out: list[ContractAudit] = []
    code = await rpc.eth_get_code(address)
    ca = analyze_code(address, role, code)
    await enrich_live(rpc, ca)

    if deep_api and role.startswith("token") and address not in QUOTES:
        gp = await goplus_token(session, address)
        if gp:
            flags = []
            if str(gp.get("is_honeypot", "")) in ("1", "true", "True"):
                flags.append("is_honeypot")
            if str(gp.get("is_proxy", "")) in ("1", "true", "True"):
                flags.append("is_proxy")
            try:
                sell_tax = float(gp.get("sell_tax") or 0)
            except (TypeError, ValueError):
                sell_tax = 0.0
            if sell_tax >= 0.1:
                flags.append(f"sell_tax={sell_tax}")
            if str(gp.get("cannot_sell_all", "")) in ("1", "true", "True"):
                flags.append("cannot_sell_all")
            if str(gp.get("transfer_pausable", "")) in ("1", "true", "True"):
                flags.append("transfer_pausable")
            if flags:
                ca.findings.append(Finding(
                    "GOPLUS_FLAGS", "high", "GoPlus security flags",
                    "Public API marked token risk attributes.",
                    flags, _flow("EXTERNAL_SECURITY_API"), role,
                ))
            ca.notes.append("goplus_checked")

    out.append(ca)

    if ca.implementation:
        try:
            icode = await rpc.eth_get_code(ca.implementation)
            ica = analyze_code(ca.implementation, role + "_impl", icode)
            await enrich_live(rpc, ica)
            out.append(ica)
        except Exception as exc:
            ca.notes.append(f"impl_audit_fail:{exc}")
    return out


async def audit_pool(
    rpc: RpcClient,
    session: aiohttp.ClientSession,
    pool: dict[str, Any],
    deep_api: bool,
) -> PoolAudit | None:
    pair = pool["pair_address"]
    t0 = pool.get("token0_address") or ""
    t1 = pool.get("token1_address") or ""
    liq = float(pool.get("liquidity_usd") or 0)

    # Fill missing token sides from pair
    if not t0 or not t1:
        try:
            r0 = await rpc.eth_call(pair, "0x" + SEL_TOKEN0)
            r1 = await rpc.eth_call(pair, "0x" + SEL_TOKEN1)
            if len(r0) >= 32:
                t0 = "0x" + r0[-20:].hex()
            if len(r1) >= 32:
                t1 = "0x" + r1[-20:].hex()
        except Exception:
            pass

    if liq <= 0:
        liq = await estimate_pair_liq(rpc, pair, t0, t1)
    if liq <= 0:
        return None

    contracts: list[ContractAudit] = []
    contracts.extend(await audit_contract(rpc, session, pair, "pair", deep_api=False))
    for addr, role in ((t0, "token0"), (t1, "token1")):
        if not addr:
            continue
        if addr in QUOTES:
            # light check only
            code = await rpc.eth_get_code(addr)
            contracts.append(ContractAudit(
                address=addr, role=role + "_quote", code_size=len(code),
                notes=["quote_asset_skipped_deep"],
            ))
            continue
        contracts.extend(await audit_contract(rpc, session, addr, role, deep_api=deep_api))

    if deep_api:
        ds = await dexscreener_pair(session, pair)
        if ds:
            pool["token0_symbol"] = pool.get("token0_symbol") or (ds.get("baseToken") or {}).get("symbol") or ""
            pool["token1_symbol"] = pool.get("token1_symbol") or (ds.get("quoteToken") or {}).get("symbol") or ""
            try:
                ds_liq = float((ds.get("liquidity") or {}).get("usd") or 0)
                if ds_liq > liq:
                    liq = ds_liq
            except (TypeError, ValueError):
                pass

    vulns: list[Finding] = []
    for c in contracts:
        vulns.extend(c.findings)
    # Keep only drain-relevant severities in pool summary (medium+)
    vulns = [v for v in vulns if v.severity in ("critical", "high", "medium")]
    score, level = score_pool(vulns)

    return PoolAudit(
        network="BSC",
        pair_address=pair,
        token0_address=t0,
        token1_address=t1,
        token0_symbol=str(pool.get("token0_symbol") or ""),
        token1_symbol=str(pool.get("token1_symbol") or ""),
        liquidity_usd=round(liq, 2),
        drain_risk=level,
        risk_score=score,
        vulns=vulns,
        contracts=contracts,
        sources=list(pool.get("sources") or []),
    )


async def run(args: argparse.Namespace) -> None:
    if args.auto:
        pools = load_pools_from_tables(min_liq=args.min_liq)
        if args.limit > 0:
            pools = pools[: args.limit]
    else:
        raise SystemExit("Use --auto to load from bot pool tables/caches")

    if not pools:
        raise SystemExit("No pools with liquidity > threshold")

    rpcs = build_rpcs()
    log.info("auditing pools=%d rpcs=%d deep_api=%s", len(pools), len(rpcs), not args.no_api)

    results: list[PoolAudit] = []
    sem = asyncio.Semaphore(args.concurrency)

    async with RpcClient(rpcs, timeout=args.timeout) as rpc, aiohttp.ClientSession() as session:
        async def one(p: dict[str, Any]) -> None:
            async with sem:
                try:
                    r = await audit_pool(rpc, session, p, deep_api=not args.no_api)
                    if r is None:
                        return
                    results.append(r)
                    log.info(
                        "pool %s %s/%s liq=$%.0f risk=%s vulns=%d",
                        r.pair_address[:10], r.token0_symbol, r.token1_symbol,
                        r.liquidity_usd, r.drain_risk, len(r.vulns),
                    )
                except Exception as exc:
                    log.warning("pool_fail %s: %s", p.get("pair_address"), exc)

        await asyncio.gather(*(one(p) for p in pools))

    results.sort(key=lambda r: ({"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4}.get(r.drain_risk, 5), -r.liquidity_usd))

    # Only pools with some drain-relevant signal in main list? Keep all, but highlight risky.
    payload = {
        "generated_ts": time.time(),
        "disclaimer": (
            "Defensive auditor with educational attack_flow narratives. "
            "Does not create exploit calldata or execute drains. "
            "Demo privileged behavior only on deployments you control."
        ),
        "attack_flow_catalog": ATTACK_FLOWS,
        "pools_audited": len(results),
        "pools_with_risk": sum(1 for r in results if r.drain_risk in ("critical", "high", "medium")),
        "pools": [r.to_dict() for r in results],
    }

    out = Path(args.out)
    if not out.is_absolute():
        out = _ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    csv_path = out.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "pair_address", "token0_symbol", "token1_symbol", "liquidity_usd",
                "drain_risk", "risk_score", "vuln_ids", "vuln_titles",
            ],
        )
        w.writeheader()
        for r in results:
            w.writerow({
                "pair_address": r.pair_address,
                "token0_symbol": r.token0_symbol,
                "token1_symbol": r.token1_symbol,
                "liquidity_usd": r.liquidity_usd,
                "drain_risk": r.drain_risk,
                "risk_score": r.risk_score,
                "vuln_ids": "|".join(sorted({v.id for v in r.vulns})),
                "vuln_titles": " || ".join(sorted({v.title for v in r.vulns})),
            })

    log.info(
        "DONE risky=%d/%d out=%s csv=%s",
        payload["pools_with_risk"], len(results), out, csv_path,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="BSC pool liquidity-drain risk auditor")
    ap.add_argument("--auto", action="store_true", help="load pools from bot tables/caches")
    ap.add_argument("--min-liq", type=float, default=0.0, help="min liquidity_usd (>0 recommended)")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--no-api", action="store_true", help="skip GoPlus/DexScreener")
    ap.add_argument("--out", default="data/pool_liq_drain_audit.json")
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.v else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.min_liq < 0:
        args.min_liq = 0.0
    # User asked liq > 0; default 0 means >0 filter via estimate. Use tiny epsilon in loader.
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
