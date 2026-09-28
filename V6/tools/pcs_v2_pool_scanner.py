#!/usr/bin/env python3
"""Standalone PancakeSwap V2 pool indexer (BSC) — NOT tied to the arb bot.

Pass 1 (default): allPairs + token0/1 + meta + reserves, keep liq>=$min → CSV.
Pass 2 (--pass2-risk): honeypot/risk bytecode enrich on existing CSV.

Usage:
  python tools/pcs_v2_pool_scanner.py --limit 0 --out data/pcs_v2_pools_full.csv
  python tools/pcs_v2_pool_scanner.py --pass2-risk --out data/pcs_v2_pools_full.csv

Deps: aiohttp, orjson, eth_abi
Env: DRPC_KEY, ALCHEMY_KEY / ALCHEMY_KEY_1 / ALCHEMY_KEY_2, INFURA_KEY
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import aiohttp
import orjson
from eth_abi import decode, encode

FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
PCS_V2_SWAP_FEE_BPS = 25
PCS_V2_LP_DEPOSIT_FEE_BPS = 0
PCS_V2_LP_WITHDRAW_FEE_BPS = 0

SEL_ALL_PAIRS_LENGTH = bytes.fromhex("574f2ba3")
SEL_ALL_PAIRS = bytes.fromhex("1e3dd18b")
SEL_TOKEN0 = bytes.fromhex("0dfe1681")
SEL_TOKEN1 = bytes.fromhex("d21220a7")
SEL_SYMBOL = bytes.fromhex("95d89b41")
SEL_DECIMALS = bytes.fromhex("313ce567")
SEL_GET_RESERVES = bytes.fromhex("0902f1ac")
SEL_AGGREGATE3 = bytes.fromhex("82ad56cb")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Quote tokens for USD liquidity estimate (BSC).
USDT = "0x55d398326f99059ff775485246999027b3197955"
USDC = "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"
BUSD = "0xe9e7cea3dedca5984780bafc599bd69add087d56"
DAI = "0x1af3f329e8bd44d4a0312f79e4a2c1c4b5f0f5e3"
FDUSD = "0xc5f0f7b667101a59003d75fc27c0db0f4c264c56"
USD1 = "0x8d0d000ee44948fc98c9b98a4e751417d2406d76"
TUSD = "0x40af3827f39d0eacbf4a168f8d4ee74c0276147d"
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
ETH = "0x2170ed0880ac9a755fd29b2688956bd959f933f8"
BTCB = "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c"
# Deep PCS V2 reference pools for pricing.
PAIR_WBNB_USDT = "0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae"
PAIR_ETH_WBNB = "0x74e4716e431f45807dcf19f284c7aa99f18a4fbc"
PAIR_BTCB_WBNB = "0x61eb789d75a95caa3ff50ed7e47b96c132fec082"

STABLES = frozenset({USDT, USDC, BUSD, DAI, FDUSD, USD1, TUSD})

PUBLIC_RPCS: list[str] = [
    # Official / Binance seeds
    "https://bsc-dataseed.binance.org",
    "https://bsc-dataseed1.binance.org",
    "https://bsc-dataseed2.binance.org",
    "https://bsc-dataseed3.binance.org",
    "https://bsc-dataseed4.binance.org",
    "https://bsc-dataseed.bnbchain.org",
    "https://bsc-dataseed1.bnbchain.org",
    "https://bsc-dataseed2.bnbchain.org",
    "https://bsc-dataseed3.bnbchain.org",
    "https://bsc-dataseed4.bnbchain.org",
    "https://bsc-dataseed-public.bnbchain.org",
    # Community mirrors
    "https://bsc-dataseed1.defibit.io",
    "https://bsc-dataseed2.defibit.io",
    "https://bsc-dataseed3.defibit.io",
    "https://bsc-dataseed4.defibit.io",
    "https://bsc-dataseed.defibit.io",
    "https://bsc-dataseed1.ninicoin.io",
    "https://bsc-dataseed2.ninicoin.io",
    "https://bsc-dataseed3.ninicoin.io",
    "https://bsc-dataseed4.ninicoin.io",
    "https://bsc-dataseed.ninicoin.io",
    "https://bsc-dataseed.nariox.org",
    # Aggregators / public providers
    "https://bsc-rpc.publicnode.com",
    "https://bsc.publicnode.com",
    "https://bsc.drpc.org",
    "https://binance.llamarpc.com",
    "https://1rpc.io/bnb",
    "https://public.1rpc.io/bnb",
    "https://rpc-bsc.48.club",
    "https://bsc.meowrpc.com",
    "https://endpoints.omniatech.io/v1/bsc/mainnet/public",
    "https://bsc.blockpi.network/v1/rpc/public",
    "https://bscrpc.com",
    "https://bsc.blockrazor.xyz",
    "https://bsc.merkle.io",
    "https://binance-smart-chain-public.nodies.app",
    "https://bnb-mainnet.g.alchemy.com/public",
]

CSV_FIELDS = [
    "pair_index",
    "pair_address",
    "token0_address",
    "token0_symbol",
    "token0_decimals",
    "token1_address",
    "token1_symbol",
    "token1_decimals",
    "reserve0",
    "reserve1",
    "liquidity_usd",
    "swap_fee_bps",
    "lp_deposit_fee_bps",
    "lp_withdraw_fee_bps",
    "token0_honeypot_risk",
    "token0_risk_flags",
    "token1_honeypot_risk",
    "token1_risk_flags",
    "pool_risk",
    "risk_notes",
]

log = logging.getLogger("pcs_v2_scan")


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


def _alchemy_keys() -> list[str]:
    keys: list[str] = []
    for k in (
        os.environ.get("ALCHEMY_KEY", ""),
        os.environ.get("ALCHEMY_KEY_1", ""),
        os.environ.get("ALCHEMY_KEY_2", ""),
        os.environ.get("ALCHEMY_API_KEY", ""),
    ):
        k = k.strip()
        if k and k not in keys:
            keys.append(k)
    return keys


def build_rpc_urls() -> list[tuple[str, int, int]]:
    """(url, concurrency, priority). Lower priority = preferred."""
    load_dotenv(_PROJECT_ROOT / ".env")
    urls: list[tuple[str, int, int]] = []

    drpc = os.environ.get("DRPC_KEY", "").strip() or os.environ.get("dRPC_KEY", "").strip()
    if drpc:
        urls.append((f"https://lb.drpc.live/bsc/{drpc}", 72, 0))
        log.info("rpc+: dRPC enabled (priority)")

    for k in _alchemy_keys():
        urls.append((f"https://bnb-mainnet.g.alchemy.com/v2/{k}", 40, 1))
        log.info("rpc+: Alchemy key enabled")

    infura = os.environ.get("INFURA_KEY", "").strip() or os.environ.get("INFURA_API_KEY", "").strip()
    if infura:
        urls.append((f"https://bsc-mainnet.infura.io/v3/{infura}", 24, 2))
        log.info("rpc+: Infura enabled")

    seen = {u for u, _, _ in urls}
    for u in PUBLIC_RPCS:
        if u not in seen:
            urls.append((u, 8, 10))
            seen.add(u)
    log.info("rpc_pool: endpoints=%d", len(urls))
    return urls


def _decode_address(data: bytes) -> str:
    if len(data) < 32:
        return ""
    return "0x" + data[-20:].hex()


def _decode_stringish(data: bytes) -> str:
    if not data or data == b"\x00" * len(data):
        return ""
    try:
        if len(data) >= 64:
            (s,) = decode(["string"], data)
            return str(s).strip("\x00")
    except Exception:
        pass
    try:
        return data[:32].rstrip(b"\x00").decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _decode_uint256(data: bytes) -> int | None:
    if len(data) < 32:
        return None
    try:
        return int.from_bytes(data[-32:], "big")
    except Exception:
        return None


def _decode_reserves(data: bytes) -> tuple[int, int]:
    if len(data) < 64:
        return 0, 0
    try:
        # ABI: uint112,uint112,uint32 → three 32-byte words
        r0 = int.from_bytes(data[0:32], "big")
        r1 = int.from_bytes(data[32:64], "big")
        return r0, r1
    except Exception:
        return 0, 0


@dataclass
class RpcEndpoint:
    url: str
    sem: asyncio.Semaphore
    priority: int = 10
    fails: int = 0
    ok: int = 0
    banned_until: float = 0.0


class RpcPool:
    def __init__(self, endpoints: list[tuple[str, int, int]], timeout: float = 20.0):
        self.eps = [
            RpcEndpoint(url=u, sem=asyncio.Semaphore(conc), priority=prio)
            for u, conc, prio in endpoints
        ]
        self._i = 0
        self._lock = asyncio.Lock()
        self.timeout = timeout
        self.session: aiohttp.ClientSession | None = None
        self.calls = 0
        self.errors = 0

    async def __aenter__(self) -> "RpcPool":
        timeout = aiohttp.ClientTimeout(total=self.timeout, sock_connect=6, sock_read=self.timeout)
        connector = aiohttp.TCPConnector(
            limit=400,
            limit_per_host=64,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
        )
        self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self.session:
            await self.session.close()

    async def _pick(self) -> RpcEndpoint:
        now = time.time()
        async with self._lock:
            alive = [e for e in self.eps if e.banned_until <= now]
            if not alive:
                for e in self.eps:
                    e.banned_until = 0.0
                alive = list(self.eps)
            # Spread across many healthy endpoints; slight prefer low priority/fails.
            ranked = sorted(alive, key=lambda e: (e.priority, e.fails, -e.ok))
            top_n = max(8, min(len(ranked), max(12, len(ranked) * 2 // 3)))
            top = ranked[:top_n]
            self._i = (self._i + 1) % len(top)
            return top[self._i]

    def _ban(self, ep: RpcEndpoint, sec: float = 15.0) -> None:
        ep.banned_until = time.time() + sec
        ep.fails += 1

    async def eth_call(self, to: str, data: bytes, retries: int = 5) -> bytes:
        assert self.session is not None
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"to": to, "data": "0x" + data.hex()}, "latest"],
        }
        body_bytes = orjson.dumps(payload)
        last_exc: Exception | None = None
        for attempt in range(retries):
            ep = await self._pick()
            async with ep.sem:
                try:
                    async with self.session.post(
                        ep.url,
                        data=body_bytes,
                        headers={"Content-Type": "application/json"},
                    ) as resp:
                        raw = await resp.read()
                        if resp.status in (429, 502, 503, 504):
                            self._ban(ep, 20)
                            self.errors += 1
                            last_exc = RuntimeError(f"HTTP {resp.status}")
                            await asyncio.sleep(0.03 * (attempt + 1))
                            continue
                        body = orjson.loads(raw)
                        self.calls += 1
                        if "error" in body:
                            msg = str(body["error"]).lower()
                            self.errors += 1
                            if any(x in msg for x in ("rate", "limit", "capacity", "busy", "timeout", "unauthorized")):
                                self._ban(ep, 30)
                            else:
                                ep.fails += 1
                            last_exc = RuntimeError(str(body["error"]))
                            await asyncio.sleep(0.03 * (attempt + 1))
                            continue
                        result = body.get("result") or "0x"
                        ep.ok += 1
                        if result in ("0x", "0x0", None):
                            return b""
                        return bytes.fromhex(result[2:] if result.startswith("0x") else result)
                except Exception as exc:
                    self._ban(ep, 10)
                    self.errors += 1
                    last_exc = exc
                    await asyncio.sleep(0.03 * (attempt + 1))
        raise RuntimeError(f"eth_call failed after retries: {last_exc}")

    async def eth_get_code(self, address: str, retries: int = 4) -> bytes:
        assert self.session is not None
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getCode",
            "params": [address, "latest"],
        }
        body_bytes = orjson.dumps(payload)
        last_exc: Exception | None = None
        for attempt in range(retries):
            ep = await self._pick()
            async with ep.sem:
                try:
                    async with self.session.post(
                        ep.url,
                        data=body_bytes,
                        headers={"Content-Type": "application/json"},
                    ) as resp:
                        raw = await resp.read()
                        body = orjson.loads(raw)
                        self.calls += 1
                        if "error" in body:
                            self._ban(ep, 12)
                            self.errors += 1
                            last_exc = RuntimeError(str(body["error"]))
                            continue
                        result = body.get("result") or "0x"
                        ep.ok += 1
                        return bytes.fromhex(result[2:] if result.startswith("0x") else result)
                except Exception as exc:
                    self._ban(ep, 8)
                    self.errors += 1
                    last_exc = exc
                    await asyncio.sleep(0.03 * (attempt + 1))
        raise RuntimeError(f"eth_getCode failed: {last_exc}")


def _pack_aggregate3(calls: list[tuple[str, bytes, bool]]) -> bytes:
    tuples = [(c[0], c[2], c[1]) for c in calls]
    return SEL_AGGREGATE3 + encode(["(address,bool,bytes)[]"], [tuples])


def _unpack_aggregate3(data: bytes, n: int) -> list[tuple[bool, bytes]]:
    if not data:
        return [(False, b"")] * n
    try:
        (decoded,) = decode(["(bool,bytes)[]"], data)
        out = [(bool(ok), bytes(ret)) for ok, ret in decoded]
        if len(out) < n:
            out.extend([(False, b"")] * (n - len(out)))
        return out[:n]
    except Exception:
        return [(False, b"")] * n


async def multicall(rpc: RpcPool, calls: list[tuple[str, bytes, bool]]) -> list[tuple[bool, bytes]]:
    if not calls:
        return []
    return _unpack_aggregate3(await rpc.eth_call(MULTICALL3, _pack_aggregate3(calls)), len(calls))


async def all_pairs_length(rpc: RpcPool) -> int:
    raw = await rpc.eth_call(FACTORY, SEL_ALL_PAIRS_LENGTH)
    return int.from_bytes(raw[-32:], "big") if raw else 0


@dataclass
class PriceBook:
    bnb_usd: float = 0.0
    eth_usd: float = 0.0
    btc_usd: float = 0.0


async def refresh_prices(rpc: RpcPool, book: PriceBook) -> None:
    """BNB/ETH/BTC USD from deep PCS V2 pools."""
    try:
        results = await multicall(
            rpc,
            [
                (PAIR_WBNB_USDT, SEL_GET_RESERVES, True),
                (PAIR_ETH_WBNB, SEL_GET_RESERVES, True),
                (PAIR_BTCB_WBNB, SEL_GET_RESERVES, True),
                (PAIR_WBNB_USDT, SEL_TOKEN0, True),
            ],
        )
        ok_r, raw_r = results[0]
        ok_t0, raw_t0 = results[3]
        if ok_r and raw_r:
            r0, r1 = _decode_reserves(raw_r)
            t0 = _decode_address(raw_t0) if ok_t0 else WBNB
            # WBNB/USDT pair: one side USDT(18 on BSC), other WBNB(18)
            if t0.lower() == WBNB:
                wbnb, usdt = r0, r1
            else:
                usdt, wbnb = r0, r1
            if wbnb > 0:
                book.bnb_usd = (usdt / 1e18) / (wbnb / 1e18)

        ok_e, raw_e = results[1]
        if ok_e and raw_e and book.bnb_usd > 0:
            r0, r1 = _decode_reserves(raw_e)
            # token0=ETH, token1=WBNB on PAIR_ETH_WBNB
            eth_amt, wbnb_amt = r0 / 1e18, r1 / 1e18
            if eth_amt > 0:
                book.eth_usd = (wbnb_amt / eth_amt) * book.bnb_usd

        ok_b, raw_b = results[2]
        if ok_b and raw_b and book.bnb_usd > 0:
            r0, r1 = _decode_reserves(raw_b)
            # token0=BTCB, token1=WBNB
            btc_amt, wbnb_amt = r0 / 1e18, r1 / 1e18
            if btc_amt > 0:
                book.btc_usd = (wbnb_amt / btc_amt) * book.bnb_usd

        log.info(
            "prices: BNB=$%.2f ETH=$%.2f BTC=$%.2f",
            book.bnb_usd, book.eth_usd, book.btc_usd,
        )
    except Exception as exc:
        log.warning("price_refresh_failed: %s", exc)


def estimate_liquidity_usd(
    t0: str,
    t1: str,
    r0: int,
    r1: int,
    d0: int,
    d1: int,
    book: PriceBook,
) -> float:
    if d0 < 0 or d1 < 0 or (r0 == 0 and r1 == 0):
        return 0.0
    try:
        a0 = r0 / (10 ** d0)
        a1 = r1 / (10 ** d1)
    except Exception:
        return 0.0
    t0l, t1l = t0.lower(), t1.lower()

    def px(addr: str) -> float | None:
        if addr in STABLES:
            return 1.0
        if addr == WBNB and book.bnb_usd > 0:
            return book.bnb_usd
        if addr == ETH and book.eth_usd > 0:
            return book.eth_usd
        if addr == BTCB and book.btc_usd > 0:
            return book.btc_usd
        return None

    p0, p1 = px(t0l), px(t1l)
    if p0 is not None:
        return 2.0 * a0 * p0
    if p1 is not None:
        return 2.0 * a1 * p1
    return 0.0


async def fetch_pair_addresses(rpc: RpcPool, start: int, count: int, batch: int) -> list[str]:
    out: list[str] = [""] * count
    sem = asyncio.Semaphore(96)

    async def fetch_range(offset: int, idxs: list[int], depth: int = 0) -> None:
        calls = [(FACTORY, SEL_ALL_PAIRS + encode(["uint256"], [i]), True) for i in idxs]
        try:
            async with sem:
                results = await multicall(rpc, calls)
        except Exception as exc:
            if len(idxs) > 20 and depth < 3:
                mid = len(idxs) // 2
                await asyncio.gather(
                    fetch_range(offset, idxs[:mid], depth + 1),
                    fetch_range(offset + mid, idxs[mid:], depth + 1),
                )
                return
            log.warning("allPairs fail @%d n=%d: %s", idxs[0], len(idxs), exc)
            return
        for j, (ok, ret) in enumerate(results):
            if ok and ret:
                out[offset + j] = _decode_address(ret)

    await asyncio.gather(*[
        fetch_range(offset, list(range(start + offset, start + offset + min(batch, count - offset))))
        for offset in range(0, count, batch)
    ])

    missing = [i for i, p in enumerate(out) if not p]
    if missing:
        for i in range(0, len(missing), 50):
            hole = missing[i : i + 50]
            calls = [
                (FACTORY, SEL_ALL_PAIRS + encode(["uint256"], [start + h]), True)
                for h in hole
            ]
            try:
                results = await multicall(rpc, calls)
                for j, (ok, ret) in enumerate(results):
                    if ok and ret:
                        out[hole[j]] = _decode_address(ret)
            except Exception:
                pass
    return out


async def fetch_token_sides(rpc: RpcPool, pairs: list[str], batch: int) -> list[tuple[str, str]]:
    n = len(pairs)
    out: list[tuple[str, str]] = [("", "")] * n
    sem = asyncio.Semaphore(80)

    async def one(offset: int, chunk: list[str]) -> None:
        async with sem:
            calls: list[tuple[str, bytes, bool]] = []
            for p in chunk:
                if not p:
                    continue
                calls.append((p, SEL_TOKEN0, True))
                calls.append((p, SEL_TOKEN1, True))
            if not calls:
                return
            try:
                results = await multicall(rpc, calls)
            except Exception as exc:
                log.warning("token01 fail: %s", exc)
                return
            ri = 0
            for j, p in enumerate(chunk):
                if not p:
                    continue
                ok0, r0 = results[ri]
                ok1, r1 = results[ri + 1]
                ri += 2
                out[offset + j] = (
                    _decode_address(r0) if ok0 else "",
                    _decode_address(r1) if ok1 else "",
                )

    await asyncio.gather(*[
        one(o, pairs[o : o + batch]) for o in range(0, n, batch)
    ])
    return out


async def fetch_reserves(rpc: RpcPool, pairs: list[str], batch: int) -> list[tuple[int, int]]:
    n = len(pairs)
    out: list[tuple[int, int]] = [(0, 0)] * n
    sem = asyncio.Semaphore(80)

    async def one(offset: int, chunk: list[str]) -> None:
        async with sem:
            calls = [(p, SEL_GET_RESERVES, True) for p in chunk if p]
            if not calls:
                return
            try:
                results = await multicall(rpc, calls)
            except Exception as exc:
                log.warning("reserves fail: %s", exc)
                return
            ri = 0
            for j, p in enumerate(chunk):
                if not p:
                    continue
                ok, ret = results[ri]
                ri += 1
                out[offset + j] = _decode_reserves(ret) if ok else (0, 0)

    await asyncio.gather(*[
        one(o, pairs[o : o + batch]) for o in range(0, n, batch)
    ])
    return out


async def fetch_token_meta(
    rpc: RpcPool,
    tokens: list[str],
    cache: dict[str, dict[str, Any]],
    batch: int = 100,
) -> None:
    need, seen = [], set()
    for t in tokens:
        tl = t.lower()
        if tl.startswith("0x") and len(tl) == 42 and tl not in cache and tl not in seen:
            seen.add(tl)
            need.append(tl)
    if not need:
        return
    sem = asyncio.Semaphore(64)

    async def one(chunk: list[str]) -> None:
        async with sem:
            calls: list[tuple[str, bytes, bool]] = []
            for t in chunk:
                calls.append((t, SEL_SYMBOL, True))
                calls.append((t, SEL_DECIMALS, True))
            try:
                results = await multicall(rpc, calls)
            except Exception as exc:
                log.warning("meta fail: %s", exc)
                return
            for i, t in enumerate(chunk):
                ok_s, rs = results[i * 2]
                ok_d, rd = results[i * 2 + 1]
                dec = _decode_uint256(rd) if ok_d and rd else None
                cache[t] = {
                    "symbol": (_decode_stringish(rs) if ok_s else "") or "?",
                    "decimals": dec if dec is not None else -1,
                }

    await asyncio.gather(*[one(need[i : i + batch]) for i in range(0, len(need), batch)])


def risk_from_bytecode(code: bytes) -> dict[str, Any]:
    if not code or code == b"\x00":
        return {"honeypot_risk": "high", "risk_flags": "no_code"}
    hexcode = code.hex()
    flags: list[str] = []
    if any(sig in hexcode for sig in ("9c8f9db5", "f9f92be4")):
        flags.append("blacklist_fn")
    if "8456cb59" in hexcode or "5c975abb" in hexcode:
        flags.append("pausable")
    if "a9059cbb" not in hexcode and "23b872dd" not in hexcode:
        flags.append("no_standard_transfer")
    if len(code) < 100:
        flags.append("tiny_bytecode")
    if "no_standard_transfer" in flags:
        risk = "high"
    elif flags:
        risk = "medium"
    else:
        risk = "low"
    return {"honeypot_risk": risk, "risk_flags": "|".join(flags) if flags else "none"}


async def fetch_risks(
    rpc: RpcPool,
    tokens: list[str],
    cache: dict[str, dict[str, Any]],
    concurrency: int = 80,
) -> None:
    need, seen = [], set()
    for t in tokens:
        tl = t.lower()
        if tl.startswith("0x") and tl not in cache and tl not in seen:
            seen.add(tl)
            need.append(tl)
    if not need:
        return
    sem = asyncio.Semaphore(concurrency)

    async def one(t: str) -> None:
        async with sem:
            try:
                cache[t] = risk_from_bytecode(await rpc.eth_get_code(t))
            except Exception as exc:
                cache[t] = {"honeypot_risk": "unknown", "risk_flags": "rpc_error", "notes": str(exc)[:80]}

    await asyncio.gather(*(one(t) for t in need))


class CsvAppender:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        migrate_csv_schema(path)
        new_file = not path.exists() or path.stat().st_size == 0
        self._fh: TextIO = path.open("a", newline="", encoding="utf-8")
        self._w = csv.DictWriter(self._fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if new_file:
            self._w.writeheader()
            self._fh.flush()

    def write_rows(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            self._w.writerow({k: r.get(k, "") for k in CSV_FIELDS})
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except OSError:
            pass

    def close(self) -> None:
        self._fh.close()


def migrate_csv_schema(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        old_fields = reader.fieldnames or []
        if set(CSV_FIELDS).issubset(set(old_fields)):
            return
        rows = [dict(r) for r in reader]
    log.info("csv_migrate: rewriting %d rows to new schema", len(rows))
    tmp = path.with_suffix(path.suffix + ".migrate.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})
    tmp.replace(path)


def checkpoint_path_for(out: Path) -> Path:
    return out.with_suffix(out.suffix + ".checkpoint.json")


def load_resume_index(out: Path, checkpoint: Path) -> int:
    next_idx = 0
    if checkpoint.exists():
        try:
            data = json.loads(checkpoint.read_text(encoding="utf-8"))
            next_idx = max(next_idx, int(data.get("next_index") or 0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    if out.exists() and out.stat().st_size > 0:
        try:
            last = -1
            with out.open("r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    try:
                        last = max(last, int(row.get("pair_index") or -1))
                    except (TypeError, ValueError):
                        continue
            # NOTE: with liquidity filter, CSV last index != scanned index.
            # Prefer checkpoint next_index (scanned), not CSV max.
        except OSError:
            pass
    return next_idx


def save_checkpoint(
    checkpoint: Path,
    next_index: int,
    total: int,
    rows_written: int,
    kept: int,
    skipped_liq: int,
) -> None:
    payload = {
        "next_index": next_index,
        "total_pairs": total,
        "rows_written_session": rows_written,
        "kept": kept,
        "skipped_low_liq": skipped_liq,
        "updated_ts": time.time(),
    }
    tmp = checkpoint.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(checkpoint)


def build_row(
    pair_index: int,
    pair: str,
    t0a: str,
    t1a: str,
    r0: int,
    r1: int,
    liq: float,
    meta: dict[str, dict[str, Any]],
    risks: dict[str, dict[str, Any]],
    skip_risk: bool,
) -> dict[str, Any]:
    m0 = meta.get(t0a.lower(), {})
    m1 = meta.get(t1a.lower(), {})
    rsk0 = risks.get(t0a.lower(), {})
    rsk1 = risks.get(t1a.lower(), {})
    if skip_risk:
        pool_risk = "unchecked"
    else:
        levels = {rsk0.get("honeypot_risk"), rsk1.get("honeypot_risk")}
        if "high" in levels:
            pool_risk = "high"
        elif "medium" in levels:
            pool_risk = "medium"
        elif "unknown" in levels:
            pool_risk = "unknown"
        else:
            pool_risk = "low"
    return {
        "pair_index": pair_index,
        "pair_address": pair,
        "token0_address": t0a,
        "token0_symbol": m0.get("symbol", ""),
        "token0_decimals": m0.get("decimals", ""),
        "token1_address": t1a,
        "token1_symbol": m1.get("symbol", ""),
        "token1_decimals": m1.get("decimals", ""),
        "reserve0": r0,
        "reserve1": r1,
        "liquidity_usd": round(liq, 2),
        "swap_fee_bps": PCS_V2_SWAP_FEE_BPS,
        "lp_deposit_fee_bps": PCS_V2_LP_DEPOSIT_FEE_BPS,
        "lp_withdraw_fee_bps": PCS_V2_LP_WITHDRAW_FEE_BPS,
        "token0_honeypot_risk": rsk0.get("honeypot_risk", "unchecked"),
        "token0_risk_flags": rsk0.get("risk_flags", ""),
        "token1_honeypot_risk": rsk1.get("honeypot_risk", "unchecked"),
        "token1_risk_flags": rsk1.get("risk_flags", ""),
        "pool_risk": pool_risk,
        "risk_notes": "static_bytecode;PCS_V2_fee_fixed_0.25pct;liq_filter",
    }


async def process_chunk(
    rpc: RpcPool,
    start: int,
    count: int,
    batch: int,
    meta_cache: dict[str, dict[str, Any]],
    risk_cache: dict[str, dict[str, Any]],
    skip_risk: bool,
    risk_conc: int,
    book: PriceBook,
    min_liq_usd: float,
) -> tuple[list[dict[str, Any]], int, int]:
    pairs = await fetch_pair_addresses(rpc, start, count, batch=batch)
    sides = await fetch_token_sides(rpc, pairs, batch=max(100, min(batch, 250)))
    reserves = await fetch_reserves(rpc, pairs, batch=max(100, min(batch, 250)))

    tokens: list[str] = []
    for a, b in sides:
        if a:
            tokens.append(a)
        if b:
            tokens.append(b)
    await fetch_token_meta(rpc, tokens, meta_cache)
    if not skip_risk:
        await fetch_risks(rpc, tokens, risk_cache, concurrency=risk_conc)

    rows: list[dict[str, Any]] = []
    skipped = 0
    for i, pair in enumerate(pairs):
        if not pair:
            continue
        t0a, t1a = sides[i]
        r0, r1 = reserves[i]
        d0 = int(meta_cache.get(t0a.lower(), {}).get("decimals", -1) or -1)
        d1 = int(meta_cache.get(t1a.lower(), {}).get("decimals", -1) or -1)
        liq = estimate_liquidity_usd(t0a, t1a, r0, r1, d0, d1, book)
        if liq < min_liq_usd:
            skipped += 1
            continue
        rows.append(
            build_row(start + i, pair, t0a, t1a, r0, r1, liq, meta_cache, risk_cache, skip_risk)
        )
    return rows, len(rows), skipped


async def run_pass2_risk(args: argparse.Namespace) -> None:
    t0 = time.perf_counter()
    out = Path(args.out)
    if not out.is_absolute():
        out = _PROJECT_ROOT / out
    if not out.exists():
        raise SystemExit(f"pass2: CSV not found: {out}")

    migrate_csv_schema(out)
    rows: list[dict[str, str]] = []
    with out.open("r", encoding="utf-8", newline="") as f:
        rows = [dict(r) for r in csv.DictReader(f)]
    log.info("pass2_loaded: rows=%d", len(rows))

    need_tokens: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for addr_k, risk_k in (
            ("token0_address", "token0_honeypot_risk"),
            ("token1_address", "token1_honeypot_risk"),
        ):
            addr = (row.get(addr_k) or "").lower()
            cur = (row.get(risk_k) or "").lower()
            if addr.startswith("0x") and cur in ("", "unchecked", "unknown") and addr not in seen:
                seen.add(addr)
                need_tokens.append(addr)

    log.info("pass2_tokens_need_risk: %d", len(need_tokens))
    if not need_tokens:
        log.info("pass2: nothing to enrich")
        return

    risk_cache: dict[str, dict[str, Any]] = {}
    async with RpcPool(build_rpc_urls(), timeout=args.timeout) as rpc:
        for i in range(0, len(need_tokens), 800):
            await fetch_risks(rpc, need_tokens[i : i + 800], risk_cache, concurrency=args.risk_conc)
            log.info("pass2_progress: %d/%d", len(risk_cache), len(need_tokens))

    updated = 0
    for row in rows:
        t0a = (row.get("token0_address") or "").lower()
        t1a = (row.get("token1_address") or "").lower()
        r0, r1 = risk_cache.get(t0a), risk_cache.get(t1a)
        changed = False
        if r0 and (row.get("token0_honeypot_risk") or "").lower() in ("", "unchecked", "unknown"):
            row["token0_honeypot_risk"] = str(r0.get("honeypot_risk", "unknown"))
            row["token0_risk_flags"] = str(r0.get("risk_flags", ""))
            changed = True
        if r1 and (row.get("token1_honeypot_risk") or "").lower() in ("", "unchecked", "unknown"):
            row["token1_honeypot_risk"] = str(r1.get("honeypot_risk", "unknown"))
            row["token1_risk_flags"] = str(r1.get("risk_flags", ""))
            changed = True
        if changed:
            levels = {
                (row.get("token0_honeypot_risk") or "").lower(),
                (row.get("token1_honeypot_risk") or "").lower(),
            }
            if "high" in levels:
                row["pool_risk"] = "high"
            elif "medium" in levels:
                row["pool_risk"] = "medium"
            elif "unknown" in levels or "unchecked" in levels:
                row["pool_risk"] = "unknown" if "unknown" in levels else "unchecked"
            else:
                row["pool_risk"] = "low"
            updated += 1

    tmp = out.with_suffix(out.suffix + ".pass2.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in CSV_FIELDS})
    tmp.replace(out)
    log.info("pass2_DONE: updated=%d elapsed=%.1fs", updated, time.perf_counter() - t0)


async def run_scan(args: argparse.Namespace) -> None:
    if args.pass2_risk:
        await run_pass2_risk(args)
        return

    t0 = time.perf_counter()
    out = Path(args.out)
    if not out.is_absolute():
        out = _PROJECT_ROOT / out
    ckpt = checkpoint_path_for(out)
    skip_risk = not args.with_risk
    min_liq = float(args.min_liq_usd)
    parallel = max(1, int(args.parallel_chunks))

    async with RpcPool(build_rpc_urls(), timeout=args.timeout) as rpc:
        total = await all_pairs_length(rpc)
        log.info("allPairsLength=%d", total)

        book = PriceBook()
        await refresh_prices(rpc, book)
        if book.bnb_usd <= 0:
            log.warning("BNB price missing — WBNB-quoted pools may be filtered out")

        resume = load_resume_index(out, ckpt)
        start = max(0, args.start, resume)
        end = min(total, start + args.limit) if args.limit > 0 else total
        if start >= end:
            log.info("nothing_to_do: start=%d end=%d", start, end)
            return

        log.info(
            "pass1: skip_risk=%s min_liq=$%.0f resume=%d range=[%d,%d) rem=%d "
            "batch=%d chunk=%d parallel=%d",
            skip_risk, min_liq, resume, start, end, end - start,
            args.batch, args.chunk, parallel,
        )

        writer = CsvAppender(out)
        meta_cache: dict[str, dict[str, Any]] = {}
        risk_cache: dict[str, dict[str, Any]] = {}
        rows_session = 0
        kept_session = 0
        skipped_session = 0
        cursor = start
        chunk = max(50, args.chunk)
        chunks_since_price = 0

        try:
            while cursor < end:
                jobs: list[tuple[int, int]] = []
                for _ in range(parallel):
                    if cursor >= end:
                        break
                    n = min(chunk, end - cursor)
                    jobs.append((cursor, n))
                    cursor += n

                if chunks_since_price >= 20:
                    await refresh_prices(rpc, book)
                    chunks_since_price = 0

                ct = time.perf_counter()
                results = await asyncio.gather(*[
                    process_chunk(
                        rpc, s, n, args.batch, meta_cache, risk_cache,
                        skip_risk, args.risk_conc, book, min_liq,
                    )
                    for s, n in jobs
                ])
                chunks_since_price += len(jobs)

                for rows, kept, skipped in results:
                    writer.write_rows(rows)
                    rows_session += kept
                    kept_session += kept
                    skipped_session += skipped

                save_checkpoint(
                    ckpt, cursor, total, rows_session, kept_session, skipped_session
                )
                elapsed = time.perf_counter() - t0
                scanned = cursor - start
                rate = scanned / elapsed if elapsed > 0 else 0
                eta = (end - cursor) / rate / 3600 if rate > 0 else float("inf")
                log.info(
                    "wave_done: scanned_to=%d/%d kept=%d skip_liq=%d wave_s=%.1fs "
                    "scan_rate=%.1f/s keep_rate=%.1f/s eta=%.1fh rpc=%d err=%d",
                    cursor, end, kept_session, skipped_session,
                    time.perf_counter() - ct, rate,
                    rows_session / elapsed if elapsed else 0,
                    eta, rpc.calls, rpc.errors,
                )
        finally:
            writer.close()

        log.info(
            "PASS1_DONE scanned=%d kept=%d skip_liq=%d elapsed=%.1fs out=%s",
            cursor - start, kept_session, skipped_session, time.perf_counter() - t0, out,
        )
        log.info("Next: python tools/pcs_v2_pool_scanner.py --pass2-risk --out %s", out)


def main() -> None:
    ap = argparse.ArgumentParser(description="PCS V2 BSC pool scanner (2-pass + liq filter)")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--chunk", type=int, default=1000)
    ap.add_argument("--batch", type=int, default=350)
    ap.add_argument("--parallel-chunks", type=int, default=4, help="concurrent index waves")
    ap.add_argument("--min-liq-usd", type=float, default=1000.0)
    ap.add_argument("--risk-conc", type=int, default=80)
    ap.add_argument("--with-risk", action="store_true")
    ap.add_argument("--skip-risk", action="store_true")
    ap.add_argument("--pass2-risk", action="store_true")
    ap.add_argument("--timeout", type=float, default=18.0)
    ap.add_argument("--out", type=str, default="data/pcs_v2_pools_full.csv")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(run_scan(args))


if __name__ == "__main__":
    main()
