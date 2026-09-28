"""Expand DEX↔DEX coin/pool universe beyond MEXC-listed tokens.

Harvests liquid pairs from DexScreener (quote token-pairs, CoinGecko top
tokens, boosts/profiles, search) plus GeckoTerminal trending/new pools
across active EVM nets. Version/decimals mostly offline (labels + maps)
so RPC rate-limits do not wipe the harvest.
Writes data/dex_dex_pools_cache.json for the dex_dex scanner mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx

from config.amm_versions import normalize_pool_version
from config.networks import is_network_active
from config.settings import settings
from config.wrapped_natives import WRAPPED_NATIVE_QUOTES

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEX_DEX_POOLS_PATH = _PROJECT_ROOT / "data" / "dex_dex_pools_cache.json"
POOLS_CACHE_PATH = _PROJECT_ROOT / "data" / "pools_cache.json"
CG_SEED_CACHE_PATH = _PROJECT_ROOT / "data" / "dex_dex_cg_seeds.json"

# DexScreener chainId → internal network
_CHAIN_TO_NET = {
    "ethereum": "ETHEREUM",
    "bsc": "BSC",
    "arbitrum": "ARBITRUM",
    "base": "BASE",
    "polygon": "POLYGON",
}

_NET_TO_CHAIN = {v: k for k, v in _CHAIN_TO_NET.items()}

_CG_PLATFORM_TO_NET = {
    "ethereum": "ETHEREUM",
    "binance-smart-chain": "BSC",
    "arbitrum-one": "ARBITRUM",
    "base": "BASE",
    "polygon-pos": "POLYGON",
}

# GeckoTerminal network slug → internal
_GT_NET = {
    "eth": "ETHEREUM",
    "bsc": "BSC",
    "arbitrum": "ARBITRUM",
    "base": "BASE",
    "polygon_pos": "POLYGON",
}

_GT_DEX_VERSION: dict[str, str] = {
    "uniswap_v2": "v2",
    "uniswap_v3": "v3",
    "uniswap_v4": "v4",
    "pancakeswap_v2": "v2",
    "pancakeswap_v3": "v3",
    "sushiswap": "v2",
    "sushiswap_v3": "v3",
    "quickswap": "v2",
    "quickswap_v3": "v3",
    "aerodrome": "v1",
    "aerodrome-slipstream": "v3",
    "camelot": "v2",
    "camelot-v3": "v3",
    "ramses": "v1",
    "baseswap": "v2",
    "alien-base": "v2",
}

_QUOTE_SYMBOLS = frozenset({
    "USDT", "USDC", "USD1", "FDUSD", "DAI", "BUSD", "TUSD", "USDE", "PYUSD",
    "WETH", "WBNB", "ETH", "BNB", "WBTC", "BTCB", "WMATIC", "WPOL", "CBBTC",
})

# Canonical quote seeds for /token-pairs harvest (high fan-out).
_QUOTE_SEEDS: dict[str, list[tuple[str, str, int]]] = {
    # (address, symbol, decimals)
    "ETHEREUM": [
        ("0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT", 6),
        ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6),
        ("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", 18),
        ("0x6b175474e89094c44da98b954eedeac495271d0f", "DAI", 18),
    ],
    "BSC": [
        ("0x55d398326f99059ff775485246999027b3197955", "USDT", 18),
        ("0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d", "USDC", 18),
        ("0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", "WBNB", 18),
        ("0xe9e7cea3dedca5984780bafc599bd69add087d56", "BUSD", 18),
    ],
    "ARBITRUM": [
        ("0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9", "USDT", 6),
        ("0xaf88d065e77c8cc2239327c5edb3a432268e5831", "USDC", 6),
        ("0x82af49447d8a07e3bd95bd0d56f35241523fbab1", "WETH", 18),
    ],
    "BASE": [
        ("0xfde4c96c8593536e31f229ea8f37b2ada2699bb2", "USDT", 6),
        ("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "USDC", 6),
        ("0x4200000000000000000000000000000000000006", "WETH", 18),
    ],
    "POLYGON": [
        ("0xc2132d05d31c914a87c6611c10748aeb04b58e8f", "USDT", 6),
        ("0x3c499c542cef5e3811e1192ce70d8cc03d5c3359", "USDC", 6),
        ("0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270", "WMATIC", 18),
        ("0x7ceb23fd6bc0add59e62ac25578270cff1b9f619", "WETH", 18),
    ],
}

_SOLIDLY_DEX = frozenset({
    "aerodrome", "velodrome", "thena", "ramses", "equalizer", "solidly",
    "chronos", "nile", "pharaoh", "shadow",
})

_V2_DEX = frozenset({
    "sushiswap", "biswap", "apeswap", "babydogeswap", "mdex", "bakeryswap",
    "shibaswap", "fraxswap", "baseswap", "swapbased", "alien-base",
    "camelot", "traderjoe", "kyberswap", "balancer", "quickswap",
})


def _norm_dex(dex_id: str | None) -> str:
    d = (dex_id or "unknown").lower().strip()
    return d.replace(" ", "_")


def _infer_version(pair: dict) -> str | None:
    """Resolve AMM version from DexScreener labels / dexId — no RPC."""
    for lab in pair.get("labels") or []:
        ver = normalize_pool_version(str(lab))
        if ver:
            return ver
    dex = _norm_dex(pair.get("dexId"))
    for ver in ("v4", "v3", "v2", "v1"):
        if dex.endswith(f"_{ver}") or f"_{ver}_" in dex or dex.endswith(ver):
            return ver
    if any(s in dex for s in _SOLIDLY_DEX):
        return "v1"
    if dex in _V2_DEX or any(s in dex for s in _V2_DEX):
        return "v2"
    # Statistical defaults for unlabeled majors.
    if dex == "pancakeswap":
        return "v2"
    if dex == "uniswap":
        return "v3"
    if dex == "quickswap":
        return "v3"
    return None


def _build_decimals_map() -> dict[tuple[str, str], int]:
    """Known decimals: quote seeds + wrapped natives + existing caches."""
    out: dict[tuple[str, str], int] = {}
    for net, rows in _QUOTE_SEEDS.items():
        for addr, _sym, dec in rows:
            out[(net, addr.lower())] = int(dec)
    for net, mapping in WRAPPED_NATIVE_QUOTES.items():
        for addr in mapping:
            out.setdefault((net, addr.lower()), 18)
    for path in (POOLS_CACHE_PATH, DEX_DEX_POOLS_PATH):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, list):
            continue
        for p in data:
            if not isinstance(p, dict):
                continue
            net = p.get("network") or ""
            ta = str(p.get("token_address") or "").lower()
            sa = str(p.get("stablecoin_address") or "").lower()
            td = p.get("token_decimals")
            sd = p.get("stablecoin_decimals")
            if net and ta and td is not None:
                out[(net, ta)] = int(td)
            if net and sa and sd is not None:
                out[(net, sa)] = int(sd)
    return out


def _pair_to_row(pair: dict, decimals_map: dict[tuple[str, str], int]) -> dict | None:
    chain = str(pair.get("chainId") or "").lower()
    net = _CHAIN_TO_NET.get(chain)
    if not net or not is_network_active(net):
        return None
    pool_addr = str(pair.get("pairAddress") or "").lower()
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    base_addr = str(base.get("address") or "").lower()
    quote_addr = str(quote.get("address") or "").lower()
    base_sym = str(base.get("symbol") or "").upper()
    quote_sym = str(quote.get("symbol") or "").upper()
    if not pool_addr or not base_addr or not quote_addr:
        return None

    if quote_sym in _QUOTE_SYMBOLS and base_sym not in _QUOTE_SYMBOLS:
        token_addr, token_coin = base_addr, base_sym
        stable_addr, stable_coin = quote_addr, quote_sym
    elif base_sym in _QUOTE_SYMBOLS and quote_sym not in _QUOTE_SYMBOLS:
        token_addr, token_coin = quote_addr, quote_sym
        stable_addr, stable_coin = base_addr, base_sym
    elif quote_sym in _QUOTE_SYMBOLS:
        token_addr, token_coin = base_addr, base_sym or "TOK"
        stable_addr, stable_coin = quote_addr, quote_sym
    else:
        token_addr, token_coin = base_addr, base_sym or "TOK"
        stable_addr, stable_coin = quote_addr, quote_sym or "Q"

    ver = _infer_version(pair)
    td = decimals_map.get((net, token_addr))
    sd = decimals_map.get((net, stable_addr))
    # Most ERC-20s are 18; stables/WETH covered by map. Prefer keep over drop.
    if td is None:
        td = 18
    if sd is None:
        sd = 18

    quote_is_stable = stable_coin in (
        "USDT", "USDC", "USD1", "FDUSD", "DAI", "BUSD", "TUSD", "USDE", "PYUSD"
    )
    return {
        "network": net,
        "pool_address": pool_addr,
        "dex": _norm_dex(pair.get("dexId")),
        "token_address": token_addr,
        "token_coin": token_coin,
        "stablecoin_address": stable_addr,
        "stablecoin_coin": stable_coin,
        "quote_coin": stable_coin,
        "quote_is_stable": quote_is_stable,
        "quote_price_coin": stable_coin,
        "pool_version": ver or "",
        "token_decimals": int(td),
        "stablecoin_decimals": int(sd),
        "token_deposit_enable": True,
        "token_withdraw_enable": True,
        "token_min_confirm": None,
        "mexc_withdraw_fee": "0",
        "stablecoin_withdraw_fee": "0",
        "sources": "dexscreener_expand",
        "_created": int(pair.get("pairCreatedAt") or 0) or 0,
        "_volume_24h": float((pair.get("volume") or {}).get("h24") or 0) or 0.0,
    }


class DexDexUniverseBuilder:
    """Build an expanded pool/coin set for DEX↔DEX scanning."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        pool_detector=None,
        token_meta_service=None,
    ):
        self._client = http_client
        self._pool_detector = pool_detector
        self._token_meta = token_meta_service

    async def expand(self) -> dict:
        """Harvest + enrich + write cache. Returns stats."""
        t0 = time.time()
        max_tokens = int(getattr(settings, "DEX_DEX_MAX_SEED_TOKENS", 250) or 250)
        max_pools = int(getattr(settings, "DEX_DEX_MAX_POOLS", 3000) or 3000)
        min_liq = float(getattr(settings, "DEX_DEX_MIN_LIQUIDITY_USD", 5000) or 5000)
        tail_max = float(getattr(settings, "DEX_DEX_TAIL_MAX_LIQ_USD", 200000) or 200000)
        start_syms = frozenset({
            c.strip().upper()
            for c in str(
                getattr(settings, "CHAIN_START_QUOTES", "")
                or "USDT,USDC,WBNB,WETH,BNB,ETH"
            ).split(",")
            if c.strip()
        }) | frozenset({"WBNB", "WETH", "USDT", "USDC"})

        decimals_map = _build_decimals_map()

        pairs: list[dict] = []
        pairs.extend(await self._fetch_quote_token_pairs())
        pairs.extend(await self._fetch_coingecko_token_pairs(max_tokens))
        pairs.extend(await self._fetch_boost_pairs())
        pairs.extend(await self._fetch_profile_pairs())
        pairs.extend(await self._fetch_search_pairs())

        by_pool: dict[tuple[str, str], dict] = {}
        missing_ver = 0

        def _ingest_row(row: dict | None, liq: float) -> None:
            nonlocal missing_ver
            if row is None:
                return
            if liq and liq < min_liq:
                return
            if not row.get("pool_version"):
                missing_ver += 1
            key = (row["network"], row["pool_address"])
            prev = by_pool.get(key)
            if prev is None or liq > float(prev.get("_liq") or 0):
                row["_liq"] = liq
                by_pool[key] = row

        for pair in pairs:
            row = _pair_to_row(pair, decimals_map)
            liq = 0.0
            try:
                liq = float((pair.get("liquidity") or {}).get("usd") or 0)
            except (TypeError, ValueError):
                liq = 0.0
            _ingest_row(row, liq)

        # GeckoTerminal trending + new pools (extra source, rate-limited).
        for gt_row, gt_liq in await self._fetch_geckoterminal_pools(decimals_map):
            _ingest_row(gt_row, gt_liq)

        harvested = list(by_pool.values())
        now_ms = time.time() * 1000.0

        def _rank(r: dict) -> tuple:
            liq = float(r.get("_liq") or 0)
            qc = (r.get("quote_coin") or r.get("stablecoin_coin") or "").upper()
            tc = (r.get("token_coin") or "").upper()
            hub = 1 if (qc in start_syms or tc in start_syms) else 0
            created = float(r.get("_created") or 0)
            age_h = ((now_ms - created) / 3_600_000.0) if created else 9999.0
            young = 1 if age_h < 72 else 0
            in_tail = 1 if (min_liq <= liq <= tail_max) else 0
            # Prefer: hub edges, young, mid-tail band, then higher volume/liq.
            vol = float(r.get("_volume_24h") or 0)
            return (hub, young, in_tail, vol, liq)

        harvested.sort(key=_rank, reverse=True)

        seen_tok: set[tuple[str, str]] = set()
        limited: list[dict] = []
        for row in harvested:
            tkey = (row["network"], row["token_address"])
            if tkey not in seen_tok:
                if len(seen_tok) >= max_tokens:
                    continue
                seen_tok.add(tkey)
            limited.append(row)
            if len(limited) >= max_pools:
                break

        logger.info(
            "dex_dex_universe_harvested: raw_pairs=%d unique_pools=%d kept=%d "
            "tokens=%d missing_ver=%d min_liq=%.0f tail_max=%.0f",
            len(pairs), len(by_pool), len(limited), len(seen_tok), missing_ver,
            min_liq, tail_max,
        )

        # Offline-only by default: RPC version probing fights the live scanner
        # (429). Enable via DEX_DEX_RPC_VERSION_FILL=1 if needed.
        if str(getattr(settings, "DEX_DEX_RPC_VERSION_FILL", "") or os.environ.get("DEX_DEX_RPC_VERSION_FILL", "0")).strip() in (
            "1", "true", "True", "yes",
        ):
            limited = await self._fill_missing_versions(limited)
        enriched = [r for r in limited if r.get("pool_version")]

        merged = self._merge_with_cache(enriched)
        for row in merged:
            row.pop("_liq", None)
            row.pop("_created", None)
            row.pop("_volume_24h", None)

        path = DEX_DEX_POOLS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

        coins = {
            (p.get("network"), (p.get("token_coin") or "").upper())
            for p in merged
            if p.get("token_coin")
        }
        stats = {
            "pools": len(merged),
            "coins": len(coins),
            "harvested": len(enriched),
            "elapsed_sec": round(time.time() - t0, 1),
            "path": str(path),
        }
        logger.info(
            "dex_dex_universe_ready: pools=%d coins=%d harvested=%d elapsed=%.1fs",
            stats["pools"], stats["coins"], stats["harvested"], stats["elapsed_sec"],
        )
        return stats

    async def _get_json(self, url: str) -> object:
        try:
            r = await self._client.get(url, timeout=45)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.debug("dex_dex_http_failed: %s %s", url, exc)
            return None

    async def _fetch_quote_token_pairs(self) -> list[dict]:
        out: list[dict] = []
        for net, seeds in _QUOTE_SEEDS.items():
            if not is_network_active(net):
                continue
            chain = _NET_TO_CHAIN.get(net)
            if not chain:
                continue
            for addr, _sym, _dec in seeds:
                data = await self._get_json(
                    f"https://api.dexscreener.com/token-pairs/v1/{chain}/{addr}"
                )
                if isinstance(data, list):
                    out.extend(data)
                await asyncio.sleep(0.12)
        return out

    async def _fetch_coingecko_token_pairs(self, max_tokens: int) -> list[dict]:
        seeds = await self._coingecko_seeds(max_tokens)
        if not seeds:
            return []
        # Prefer tokens not already covered as pure quotes.
        addrs = []
        seen = set()
        for _net, addr, _sym in seeds:
            a = addr.lower()
            if a in seen:
                continue
            seen.add(a)
            addrs.append(a)
        return await self._pairs_for_token_addrs(addrs)

    async def _coingecko_seeds(self, max_tokens: int) -> list[tuple[str, str, str]]:
        """Top market-cap coins → EVM contract seeds (cached ~6h)."""
        now = time.time()
        if CG_SEED_CACHE_PATH.exists():
            try:
                cached = json.loads(CG_SEED_CACHE_PATH.read_text(encoding="utf-8"))
                if (
                    isinstance(cached, dict)
                    and now - float(cached.get("ts") or 0) < 6 * 3600
                    and isinstance(cached.get("seeds"), list)
                ):
                    return [
                        (s[0], s[1], s[2])
                        for s in cached["seeds"]
                        if isinstance(s, list) and len(s) >= 3
                    ]
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass

        markets = await self._get_json(
            "https://api.coingecko.com/api/v3/coins/markets"
            "?vs_currency=usd&order=market_cap_desc"
            f"&per_page={min(max(max_tokens, 100), 250)}&page=1"
        )
        if not isinstance(markets, list):
            return []
        want_ids = {str(c.get("id") or "") for c in markets if c.get("id")}

        coin_list = await self._get_json(
            "https://api.coingecko.com/api/v3/coins/list?include_platform=true"
        )
        if not isinstance(coin_list, list):
            return []

        seeds: list[tuple[str, str, str]] = []
        for c in coin_list:
            if str(c.get("id") or "") not in want_ids:
                continue
            sym = str(c.get("symbol") or "").upper()
            plats = c.get("platforms") or {}
            if not isinstance(plats, dict):
                continue
            for pk, net in _CG_PLATFORM_TO_NET.items():
                if not is_network_active(net):
                    continue
                addr = str(plats.get(pk) or "").lower()
                if addr.startswith("0x") and len(addr) >= 42:
                    seeds.append((net, addr, sym))

        try:
            CG_SEED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CG_SEED_CACHE_PATH.write_text(
                json.dumps({"ts": now, "seeds": seeds}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.debug("dex_dex_cg_cache_write_failed: %s", exc)
        return seeds

    async def _fetch_boost_pairs(self) -> list[dict]:
        data = await self._get_json("https://api.dexscreener.com/token-boosts/top/v1")
        if not isinstance(data, list):
            return []
        tokens: list[tuple[str, str]] = []
        for item in data[:100]:
            chain = str(item.get("chainId") or "").lower()
            net = _CHAIN_TO_NET.get(chain)
            addr = str(item.get("tokenAddress") or "").lower()
            if net and is_network_active(net) and addr:
                tokens.append((chain, addr))
        return await self._pairs_for_tokens(tokens)

    async def _fetch_profile_pairs(self) -> list[dict]:
        data = await self._get_json(
            "https://api.dexscreener.com/token-profiles/latest/v1"
        )
        if not isinstance(data, list):
            return []
        tokens: list[tuple[str, str]] = []
        for item in data[:100]:
            chain = str(item.get("chainId") or "").lower()
            net = _CHAIN_TO_NET.get(chain)
            addr = str(item.get("tokenAddress") or "").lower()
            if net and is_network_active(net) and addr:
                tokens.append((chain, addr))
        return await self._pairs_for_tokens(tokens)

    async def _fetch_search_pairs(self) -> list[dict]:
        out: list[dict] = []
        queries = (
            "USDT", "USDC", "WETH", "WBNB", "WMATIC", "POL",
            "PEPE", "LINK", "UNI", "AAVE", "ARB", "OP", "CAKE", "DOGE",
            "AERO", "VIRTUAL", "BRETT", "DEGEN", "QUICK", "SUSHI",
            "polygon", "base", "arbitrum",
        )
        for q in queries:
            data = await self._get_json(
                f"https://api.dexscreener.com/latest/dex/search?q={q}"
            )
            if isinstance(data, dict):
                pairs = data.get("pairs") or []
            elif isinstance(data, list):
                pairs = data
            else:
                pairs = []
            for pair in pairs:
                chain = str(pair.get("chainId") or "").lower()
                net = _CHAIN_TO_NET.get(chain)
                if net and is_network_active(net):
                    out.append(pair)
            await asyncio.sleep(0.12)
        return out

    async def _fetch_geckoterminal_pools(
        self, decimals_map: dict[tuple[str, str], int]
    ) -> list[tuple[dict, float]]:
        """Trending + new pools from GeckoTerminal for each active net."""
        out: list[tuple[dict, float]] = []
        for slug, net in _GT_NET.items():
            if not is_network_active(net):
                continue
            for kind in ("trending_pools", "new_pools"):
                data = await self._get_json(
                    f"https://api.geckoterminal.com/api/v2/networks/{slug}/{kind}"
                    "?page=1&include=base_token,quote_token,dex"
                )
                await asyncio.sleep(0.55)
                if not isinstance(data, dict):
                    continue
                included: dict[str, dict] = {}
                for inc in data.get("included") or []:
                    if not isinstance(inc, dict):
                        continue
                    iid = str(inc.get("id") or "")
                    if iid:
                        included[iid] = inc
                for item in data.get("data") or []:
                    if not isinstance(item, dict):
                        continue
                    row, liq = self._gt_pool_to_row(item, included, net, decimals_map)
                    if row is not None:
                        out.append((row, liq))
        logger.info("dex_dex_gt_pools: rows=%d", len(out))
        return out

    @staticmethod
    def _gt_pool_to_row(
        item: dict,
        included: dict[str, dict],
        net: str,
        decimals_map: dict[tuple[str, str], int],
    ) -> tuple[dict | None, float]:
        attrs = item.get("attributes") or {}
        if not isinstance(attrs, dict):
            return None, 0.0
        pool_addr = str(attrs.get("address") or "").lower()
        if not pool_addr.startswith("0x"):
            return None, 0.0
        try:
            dex_raw = str(
                ((item.get("relationships") or {}).get("dex") or {})
                .get("data", {})
                .get("id")
                or ""
            )
        except (TypeError, AttributeError):
            dex_raw = ""
        dex_slug = dex_raw.split("_", 1)[-1] if "_" in dex_raw else dex_raw
        dex_slug = _norm_dex(dex_slug or "unknown")

        def _tok(side: str) -> tuple[str, str, int]:
            rel = ((item.get("relationships") or {}).get(side) or {}).get("data") or {}
            iid = str(rel.get("id") or "")
            inc = included.get(iid) or {}
            a = (inc.get("attributes") or {}) if isinstance(inc, dict) else {}
            addr = str(a.get("address") or "").lower()
            sym = str(a.get("symbol") or "").upper()
            try:
                dec = int(a.get("decimals") if a.get("decimals") is not None else 18)
            except (TypeError, ValueError):
                dec = 18
            return addr, sym, dec

        base_addr, base_sym, base_dec = _tok("base_token")
        quote_addr, quote_sym, quote_dec = _tok("quote_token")
        if not base_addr or not quote_addr:
            return None, 0.0

        if quote_sym in _QUOTE_SYMBOLS and base_sym not in _QUOTE_SYMBOLS:
            token_addr, token_coin, td = base_addr, base_sym, base_dec
            stable_addr, stable_coin, sd = quote_addr, quote_sym, quote_dec
        elif base_sym in _QUOTE_SYMBOLS and quote_sym not in _QUOTE_SYMBOLS:
            token_addr, token_coin, td = quote_addr, quote_sym, quote_dec
            stable_addr, stable_coin, sd = base_addr, base_sym, base_dec
        else:
            token_addr, token_coin, td = base_addr, base_sym or "TOK", base_dec
            stable_addr, stable_coin, sd = quote_addr, quote_sym or "Q", quote_dec

        td = decimals_map.get((net, token_addr), td)
        sd = decimals_map.get((net, stable_addr), sd)

        ver = _GT_DEX_VERSION.get(dex_slug)
        if not ver:
            ver = _infer_version({"dexId": dex_slug, "labels": []}) or ""

        try:
            liq = float(attrs.get("reserve_in_usd") or 0)
        except (TypeError, ValueError):
            liq = 0.0
        vol = 0.0
        vol_obj = attrs.get("volume_usd")
        if isinstance(vol_obj, dict):
            try:
                vol = float(vol_obj.get("h24") or 0)
            except (TypeError, ValueError):
                vol = 0.0

        quote_is_stable = stable_coin in (
            "USDT", "USDC", "USD1", "FDUSD", "DAI", "BUSD", "TUSD", "USDE", "PYUSD"
        )
        row = {
            "network": net,
            "pool_address": pool_addr,
            "dex": dex_slug,
            "token_address": token_addr,
            "token_coin": token_coin,
            "stablecoin_address": stable_addr,
            "stablecoin_coin": stable_coin,
            "quote_coin": stable_coin,
            "quote_is_stable": quote_is_stable,
            "quote_price_coin": stable_coin,
            "pool_version": ver or "",
            "token_decimals": int(td),
            "stablecoin_decimals": int(sd),
            "token_deposit_enable": True,
            "token_withdraw_enable": True,
            "token_min_confirm": None,
            "mexc_withdraw_fee": "0",
            "stablecoin_withdraw_fee": "0",
            "sources": "geckoterminal",
            "_created": 0,
            "_volume_24h": vol,
        }
        return row, liq

    async def _pairs_for_tokens(self, tokens: list[tuple[str, str]]) -> list[dict]:
        uniq = []
        seen = set()
        for _chain, addr in tokens:
            key = addr.lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(key)
        return await self._pairs_for_token_addrs(uniq)

    async def _pairs_for_token_addrs(self, addrs: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(addrs), 20):
            batch = ",".join(addrs[i : i + 20])
            data = await self._get_json(
                f"https://api.dexscreener.com/latest/dex/tokens/{batch}"
            )
            if isinstance(data, dict):
                out.extend(data.get("pairs") or [])
            await asyncio.sleep(0.2)
        return out

    async def _fill_missing_versions(self, rows: list[dict]) -> list[dict]:
        """RPC-detect version only for rows still missing it (low concurrency)."""
        need = [r for r in rows if not r.get("pool_version")]
        if not need or self._pool_detector is None:
            return rows
        sem = asyncio.Semaphore(2)

        async def one(row: dict) -> None:
            async with sem:
                try:
                    ver = await self._pool_detector.detect_version(
                        row["network"], row["pool_address"]
                    )
                    if ver:
                        row["pool_version"] = ver
                except Exception:
                    pass
                await asyncio.sleep(0.05)

        await asyncio.gather(*(one(r) for r in need))
        filled = sum(1 for r in need if r.get("pool_version"))
        logger.info(
            "dex_dex_universe_rpc_version: needed=%d filled=%d",
            len(need), filled,
        )
        return rows

    @staticmethod
    def _merge_with_cache(new_rows: list[dict]) -> list[dict]:
        by_key: dict[tuple[str, str], dict] = {}

        def _ingest(path: Path) -> None:
            if not path.exists():
                return
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("dex_dex_merge_cache_failed: %s %s", path, exc)
                return
            if not isinstance(existing, list):
                return
            for p in existing:
                if not isinstance(p, dict):
                    continue
                net = p.get("network") or ""
                if not is_network_active(net):
                    continue
                if p.get("token_decimals") is None or p.get("stablecoin_decimals") is None:
                    continue
                if not p.get("pool_version"):
                    continue
                key = (net, str(p.get("pool_address") or "").lower())
                by_key[key] = p

        # Prefer previous expanded cache, then MEXC pools_cache.
        _ingest(DEX_DEX_POOLS_PATH)
        _ingest(POOLS_CACHE_PATH)

        for p in new_rows:
            key = (p["network"], p["pool_address"])
            if key not in by_key:
                by_key[key] = p
        return list(by_key.values())


def load_dex_dex_pools() -> list[dict]:
    """Load expanded cache, else fall back to pools_cache.json."""
    for path in (DEX_DEX_POOLS_PATH, POOLS_CACHE_PATH):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except (OSError, json.JSONDecodeError):
            continue
    return []
