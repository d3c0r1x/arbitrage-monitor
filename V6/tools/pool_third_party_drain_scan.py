#!/usr/bin/env python3
"""
Third-party pool liquidity drain hunt (BSC) — defensive triage only.

Focus: can a RANDOM EOA (no owner key) move/break pool reserves via a bug,
non-canonical pair, or unprotected privileged function?

NOT: owner/tax/pause rug scoring (that's insider risk).
NOT: exploit calldata builders / live drains.

Checks:
  1) Pair runtime codehash vs official PCS V2 pair reference
  2) Pair has non-canonical privileged selectors (withdraw/upgrade/erc20-mint)
  3) eth_call AS random attacker: mint/withdraw/rescue — if call does NOT revert → public
  4) Token public mint (eth_call from attacker)
  5) Proxy admin-less weirdness (optional notes)

Usage:
  python tools/pool_third_party_drain_scan.py --min-liq 1 --out data/third_party_drain_hits.json
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
import orjson

log = logging.getLogger("tp_drain")
_ROOT = Path(__file__).resolve().parent.parent

# Known deep official-style PCS V2 pairs (reference codehash donors)
REF_PAIRS = [
    "0x58f876857a02d6762e0101bb5c46a8c1ed44dc16",  # WBNB-BUSD
    "0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae",  # WBNB-USDT
    "0x74e4716e431f45807dcf19f284c7aa99f18a4fbc",  # ETH-WBNB
]

ATTACKER = "0x0000000000000000000000000000000000000Bad"
ZERO = "0x0000000000000000000000000000000000000000"

# Dangerous if present on PAIR (stock UniV2 pair must NOT have these)
PAIR_BAD_SELS = {
    "51cff8d9": "withdraw(address)",
    "3ccfd60b": "withdraw()",
    "e086e5ec": "withdrawBNB()",
    "f14210a6": "withdrawToken(address,uint256)",
    "01339c21": "rescueToken(address,uint256)",
    "40c10f19": "mint(address,uint256)",  # ERC20 mint ≠ pair LP mint 6a627842
    "3659cfe6": "upgradeTo(address)",
    "4f1ef286": "upgradeToAndCall(address,bytes)",
    "8da5cb5b": "owner()",  # stock pair has no Ownable
}

# Canonical V2 pair should have these
PAIR_NEED_SELS = {"022c0d9f", "fff6cae9", "bc25cf77", "0dfe1681", "d21220a7", "0902f1ac"}

QUOTES = frozenset({
    "0x55d398326f99059ff775485246999027b3197955",
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
    "0xe9e7cea3dedca5984780bafc599bd69add087d56",
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
    "0x2170ed0880ac9a755fd29b2688956bd959f933f8",
    "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",
})


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


def _norm(a: str) -> str:
    a = (a or "").strip().lower()
    return a if a.startswith("0x") and len(a) == 42 else ""


def _pad_addr(addr: str) -> str:
    return addr.lower().replace("0x", "").rjust(64, "0")


def _pad_uint(n: int) -> str:
    return f"{n:064x}"


def sel_in_code(code_hex: str, sel: str) -> bool:
    return ("63" + sel) in code_hex or sel in code_hex


class Rpc:
    def __init__(self, urls: list[str], timeout: float = 20.0):
        self.urls = urls
        self._i = 0
        self.timeout = timeout
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "Rpc":
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            connector=aiohttp.TCPConnector(limit=100),
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
        for _ in range(max(8, len(self.urls))):
            url = self.urls[self._i % len(self.urls)]
            self._i += 1
            try:
                async with self.session.post(
                    url, data=body, headers={"Content-Type": "application/json"}
                ) as resp:
                    data = orjson.loads(await resp.read())
                    if "error" in data:
                        last = RuntimeError(str(data["error"]))
                        # bubble revert-like as structured
                        err = data["error"]
                        if isinstance(err, dict) and "execution reverted" in str(err).lower():
                            raise RuntimeError("REVERT:" + str(err))
                        continue
                    return data.get("result")
            except RuntimeError as exc:
                if str(exc).startswith("REVERT:"):
                    raise
                last = exc
            except Exception as exc:
                last = exc
        raise RuntimeError(f"{method} failed: {last}")

    async def code(self, addr: str) -> bytes:
        r = await self._rpc("eth_getCode", [addr, "latest"]) or "0x"
        s = str(r)
        return bytes.fromhex(s[2:] if s.startswith("0x") else s)

    async def codehash(self, addr: str) -> str:
        import hashlib
        code = await self.code(addr)
        try:
            from eth_hash.auto import keccak  # type: ignore
            return "0x" + keccak(code).hex()
        except Exception:
            return "0x" + hashlib.sha256(code).hexdigest()

    async def runtime_fingerprint(self, addr: str) -> str:
        """Fingerprint runtime bytecode (keccak if available)."""
        return await self.codehash(addr)
    async def call(
        self, to: str, data: str, from_addr: str | None = None
    ) -> tuple[bool, str]:
        """Returns (ok_no_revert, result_or_error)."""
        tx: dict[str, str] = {"to": to, "data": data}
        if from_addr:
            tx["from"] = from_addr
        assert self.session
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [tx, "latest"],
        }
        body = orjson.dumps(payload)
        last = ""
        for _ in range(max(6, len(self.urls))):
            url = self.urls[self._i % len(self.urls)]
            self._i += 1
            try:
                async with self.session.post(
                    url, data=body, headers={"Content-Type": "application/json"}
                ) as resp:
                    data_j = orjson.loads(await resp.read())
                    if "error" in data_j:
                        last = str(data_j["error"])
                        # revert = protected / failed — not publicly successful
                        return False, last
                    return True, str(data_j.get("result") or "0x")
            except Exception as exc:
                last = str(exc)
                continue
        return False, last


def load_pools(min_liq: float) -> list[dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}

    def up(row: dict[str, Any], src: str) -> None:
        pair = _norm(str(row.get("pair_address") or row.get("pool_address") or ""))
        if not pair:
            return
        net = str(row.get("network") or "BSC").upper()
        if src != "pcs" and net not in ("BSC", "BNB", "BEP20", ""):
            return
        try:
            liq = float(row.get("liquidity_usd") or 0)
        except (TypeError, ValueError):
            liq = 0.0
        t0 = _norm(str(row.get("token0_address") or row.get("token_address") or ""))
        t1 = _norm(str(row.get("token1_address") or row.get("stablecoin_address") or ""))
        prev = by.get(pair)
        if prev is None or liq >= float(prev.get("liquidity_usd") or 0):
            by[pair] = {
                "pair": pair,
                "token0": t0 or (prev or {}).get("token0", ""),
                "token1": t1 or (prev or {}).get("token1", ""),
                "s0": row.get("token0_symbol") or row.get("token_coin") or (prev or {}).get("s0", ""),
                "s1": row.get("token1_symbol") or row.get("stablecoin_coin") or (prev or {}).get("s1", ""),
                "liq": liq,
                "src": list({*((prev or {}).get("src") or []), src}),
            }

    pcs = _ROOT / "data" / "pcs_v2_pools_full.csv"
    if pcs.exists():
        with pcs.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                up(row, "pcs")
    for name in ("dex_dex_pools_cache.json", "pools_cache.json"):
        path = _ROOT / "data" / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict):
                    up(row, name)

    pools = [p for p in by.values() if float(p["liq"]) > min_liq]
    pools.sort(key=lambda x: -x["liq"])
    return pools


@dataclass
class Hit:
    pair: str
    token0: str
    token1: str
    symbol: str
    liquidity_usd: float
    severity: str
    bug_id: str
    title: str
    detail: str
    evidence: list[str] = field(default_factory=list)
    third_party_plausible: bool = True


async def scan_pool(
    rpc: Rpc,
    pool: dict[str, Any],
    ref_hashes: set[str],
) -> list[Hit]:
    hits: list[Hit] = []
    pair = pool["pair"]
    t0, t1 = pool.get("token0") or "", pool.get("token1") or ""
    sym = f"{pool.get('s0') or '?'}/{pool.get('s1') or '?'}"
    liq = float(pool.get("liq") or 0)

    def add(sev: str, bug_id: str, title: str, detail: str, evidence: list[str]) -> None:
        hits.append(Hit(
            pair=pair, token0=t0, token1=t1, symbol=sym, liquidity_usd=liq,
            severity=sev, bug_id=bug_id, title=title, detail=detail, evidence=evidence,
        ))

    # Resolve missing tokens
    if not t0 or not t1:
        ok0, r0 = await rpc.call(pair, "0x0dfe1681")
        ok1, r1 = await rpc.call(pair, "0xd21220a7")
        if ok0 and len(r0) >= 66:
            t0 = "0x" + r0[-40:]
            pool["token0"] = t0
        if ok1 and len(r1) >= 66:
            t1 = "0x" + r1[-40:]
            pool["token1"] = t1

    try:
        code = await rpc.code(pair)
    except Exception as exc:
        add("medium", "PAIR_CODE_FAIL", "Cannot fetch pair code", str(exc), [])
        return hits

    if len(code) == 0:
        add("critical", "PAIR_EOA", "Pair has no code", "Not a contract — table garbage or selfdestructed.", [])
        return hits

    hx = code.hex()
    try:
        ch = await rpc.codehash(pair)
    except Exception:
        ch = ""

    # 1) Non-canonical pair codehash
    if ref_hashes and ch and ch not in ref_hashes:
        add(
            "high",
            "NONCANONICAL_PAIR_CODEHASH",
            "Pair codehash ≠ official PCS V2 reference",
            "Custom/forked pair bytecode — third party risk if accounting is malicious or buggy.",
            [f"codehash={ch}", f"code_size={len(code)}"],
        )

    # 2) Missing canonical selectors / has bad selectors
    missing = [s for s in PAIR_NEED_SELS if not sel_in_code(hx, s)]
    if missing:
        add(
            "high",
            "PAIR_MISSING_V2_ABI",
            "Pair missing canonical V2 selectors",
            "Does not look like stock UniV2/PCS pair ABI — review before routing size.",
            [f"missing={missing}"],
        )

    bad_hit = {s: n for s, n in PAIR_BAD_SELS.items() if sel_in_code(hx, s)}
    if bad_hit:
        add(
            "critical",
            "PAIR_HAS_PRIVILEGED_ABI",
            "Pair bytecode exposes privileged ABI",
            "Stock AMM pairs must not have Ownable/withdraw/ERC20-mint/upgrade. "
            "If these entrypoints are callable by anyone, reserves can be stolen.",
            [f"{s}={n}" for s, n in bad_hit.items()],
        )

    # 3) Live: can ATTACKER call withdraw/rescue on PAIR without revert?
    probes = []
    if "3ccfd60b" in bad_hit:
        probes.append(("PAIR_PUBLIC_WITHDRAW", "0x3ccfd60b", "withdraw()"))
    if "51cff8d9" in bad_hit:
        data = "0x51cff8d9" + _pad_addr(t0 or WBNB_PLACEHOLDER())
        probes.append(("PAIR_PUBLIC_WITHDRAW_ADDR", data, "withdraw(address)"))
    if "01339c21" in bad_hit:
        data = "0x01339c21" + _pad_addr(t0 or WBNB_PLACEHOLDER()) + _pad_uint(1)
        probes.append(("PAIR_PUBLIC_RESCUE", data, "rescueToken"))
    if "40c10f19" in bad_hit:
        data = "0x40c10f19" + _pad_addr(ATTACKER) + _pad_uint(1)
        probes.append(("PAIR_PUBLIC_ERC20_MINT", data, "mint(address,uint256)"))
    if "3659cfe6" in bad_hit:
        data = "0x3659cfe6" + _pad_addr(ATTACKER)
        probes.append(("PAIR_PUBLIC_UPGRADE", data, "upgradeTo"))

    for bug_id, data, label in probes:
        ok, res = await rpc.call(pair, data if data.startswith("0x") else data, from_addr=ATTACKER)
        if ok:
            add(
                "critical",
                bug_id,
                f"Pair function callable by random EOA: {label}",
                "eth_call from attacker did NOT revert — strong signal of missing access control. "
                "Manual confirmation required before claiming funds are stealable.",
                [f"from={ATTACKER}", f"result={res[:66]}"],
            )

    # 4) Token-side public mint (non-quote)
    for tok, role in ((t0, "token0"), (t1, "token1")):
        if not tok or tok in QUOTES:
            continue
        try:
            tcode = await rpc.code(tok)
        except Exception:
            continue
        if not tcode:
            add(
                "high",
                "TOKEN_EOA",
                f"{role} has no code",
                "Token side is EOA/destroyed — pool is broken/scam surface.",
                [tok],
            )
            continue
        thx = tcode.hex()
        if sel_in_code(thx, "40c10f19"):
            data = "0x40c10f19" + _pad_addr(ATTACKER) + _pad_uint(10**18)
            ok, res = await rpc.call(tok, data, from_addr=ATTACKER)
            if ok:
                add(
                    "critical",
                    "TOKEN_PUBLIC_MINT",
                    f"Public mint on {role}",
                    "Random EOA mint() did not revert in eth_call — attacker can inflate and dump into pool.",
                    [tok, f"result={res[:66]}"],
                )
        # unprotected withdraw on token contract (custody), not pair reserves — still note
        if sel_in_code(thx, "3ccfd60b"):
            ok, res = await rpc.call(tok, "0x3ccfd60b", from_addr=ATTACKER)
            if ok:
                add(
                    "high",
                    "TOKEN_PUBLIC_WITHDRAW",
                    f"Public withdraw() on {role} contract",
                    "Unprotected withdraw on token contract (treasury/fee wallet pattern). "
                    "Does not directly empty AMM reserves unless contract holds LP/inventory.",
                    [tok],
                )

    return hits


def WBNB_PLACEHOLDER() -> str:
    return "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"


async def run(args: argparse.Namespace) -> None:
    pools = load_pools(args.min_liq)
    if args.limit > 0:
        pools = pools[: args.limit]
    if not pools:
        raise SystemExit("no pools")

    rpcs = build_rpcs()
    log.info("third_party_scan pools=%d rpcs=%d", len(pools), len(rpcs))

    all_hits: list[Hit] = []
    sem = asyncio.Semaphore(args.concurrency)

    async with Rpc(rpcs, timeout=args.timeout) as rpc:
        ref_hashes: set[str] = set()
        for ref in REF_PAIRS:
            try:
                h = await rpc.codehash(ref)
                ref_hashes.add(h)
                log.info("ref_pair %s codehash=%s", ref[:10], h)
            except Exception as exc:
                log.warning("ref_fail %s: %s", ref, exc)
        if not ref_hashes:
            log.warning("no reference codehashes — NONCANONICAL check degraded")

        async def one(p: dict[str, Any]) -> None:
            async with sem:
                try:
                    hits = await scan_pool(rpc, p, ref_hashes)
                    # Keep only third-party plausible hits (all in this scanner are)
                    if hits:
                        all_hits.extend(hits)
                        log.info(
                            "HITS %s %s liq=%.0f n=%d ids=%s",
                            p["pair"][:10],
                            f"{p.get('s0')}/{p.get('s1')}",
                            p["liq"],
                            len(hits),
                            ",".join(h.bug_id for h in hits),
                        )
                except Exception as exc:
                    log.warning("scan_fail %s: %s", p.get("pair"), exc)

        await asyncio.gather(*(one(p) for p in pools))

    # Aggregate by pair
    by_pair: dict[str, dict[str, Any]] = {}
    for h in all_hits:
        row = by_pair.setdefault(h.pair, {
            "pair_address": h.pair,
            "pair": h.symbol,
            "token0": h.token0,
            "token1": h.token1,
            "liquidity_usd": h.liquidity_usd,
            "max_severity": "info",
            "bugs": [],
        })
        row["bugs"].append({
            "bug_id": h.bug_id,
            "severity": h.severity,
            "title": h.title,
            "detail": h.detail,
            "evidence": h.evidence,
        })
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        if order.get(h.severity, 9) < order.get(row["max_severity"], 9):
            row["max_severity"] = h.severity

    rows = sorted(
        by_pair.values(),
        key=lambda r: ({"critical": 0, "high": 1, "medium": 2}.get(r["max_severity"], 9), -r["liquidity_usd"]),
    )

    # Prefer pools with LIVE public-callable evidence or pair privileged ABI
    priority_ids = {
        "PAIR_PUBLIC_WITHDRAW", "PAIR_PUBLIC_WITHDRAW_ADDR", "PAIR_PUBLIC_RESCUE",
        "PAIR_PUBLIC_ERC20_MINT", "PAIR_PUBLIC_UPGRADE", "TOKEN_PUBLIC_MINT",
        "PAIR_HAS_PRIVILEGED_ABI", "PAIR_EOA",
    }
    for r in rows:
        ids = {b["bug_id"] for b in r["bugs"]}
        r["priority_third_party"] = bool(ids & priority_ids)
        r["bug_titles"] = "; ".join(sorted({b["title"] for b in r["bugs"]}))

    out = Path(args.out)
    if not out.is_absolute():
        out = _ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_ts": time.time(),
        "scope": "third_party_random_EOA_drain_triage",
        "disclaimer": (
            "Flags non-canonical pairs and eth_call success from a random address. "
            "Success in eth_call is a strong lead, not a guaranteed mainnet drain. "
            "No exploit payloads are generated."
        ),
        "pools_scanned": len(pools),
        "pools_with_hits": len(rows),
        "priority_hits": sum(1 for r in rows if r["priority_third_party"]),
        "pools": rows,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    csv_path = out.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "priority_third_party", "max_severity", "pair", "pair_address",
                "liquidity_usd", "bug_titles", "bug_ids",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow({
                "priority_third_party": r["priority_third_party"],
                "max_severity": r["max_severity"],
                "pair": r["pair"],
                "pair_address": r["pair_address"],
                "liquidity_usd": r["liquidity_usd"],
                "bug_titles": r["bug_titles"],
                "bug_ids": "|".join(sorted({b["bug_id"] for b in r["bugs"]})),
            })

    pri = [r for r in rows if r["priority_third_party"]]
    log.info(
        "DONE scanned=%d hits_pools=%d priority=%d out=%s",
        len(pools), len(rows), len(pri), out,
    )
    for r in pri[:40]:
        log.info(
            "PRIORITY %s liq=%.0f sev=%s | %s",
            r["pair"], r["liquidity_usd"], r["max_severity"], r["bug_titles"],
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-liq", type=float, default=1.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--out", default="data/third_party_drain_hits.json")
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.v else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
