"""
Multi-hop Chain Arbitrage Engine.

Finds cyclic arbitrage paths through DEX pools starting at USDT/USDC:
  USDT → … → USDT  (max CHAIN_MAX_HOPS)

PnL is based on simulated swap token amounts (adapter.quote_exact_input):
  net = (amount_out - amount_in) in start token − gas USD.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from decimal import Decimal

from config.settings import settings
from services.size_sweep import (
    attach_size_bounds,
    is_near_miss,
    pick_optimal_by_net_usd,
    size_sweep_ladder_usd,
)

logger = logging.getLogger(__name__)

_NEAR_MISS_LIMIT = 20

# Intermediary tokens per network (high-liquidity routing nodes).
INTERMEDIARIES: dict[str, list[str]] = {
    "ETHEREUM": [
        "WETH", "ETH", "USDT", "USDC", "WBTC", "DAI", "FDUSD", "USD1",
    ],
    "BSC": [
        "WBNB", "BNB", "USDT", "USDC", "ETH", "WETH", "BTCB", "DAI", "FDUSD", "USD1",
    ],
    "POLYGON": ["WMATIC", "USDT", "USDC", "WETH", "ETH", "DAI"],
    "ARBITRUM": [
        "WETH", "ETH", "USDT", "USDC", "WBTC", "DAI", "USD1",
    ],
    "BASE": ["WETH", "ETH", "USDC", "USDT", "DAI"],
    "ROBINHOOD": ["WETH", "ETH", "USDT", "USDC"],
}

# CHAIN_START_QUOTES aliases: native ↔ wrapped same start capital.
_START_COIN_ALIASES: dict[str, frozenset[str]] = {
    "BNB": frozenset({"BNB", "WBNB"}),
    "WBNB": frozenset({"BNB", "WBNB"}),
    "ETH": frozenset({"ETH", "WETH"}),
    "WETH": frozenset({"ETH", "WETH"}),
    "POL": frozenset({"POL", "MATIC", "WMATIC", "WPOL"}),
    "MATIC": frozenset({"POL", "MATIC", "WMATIC", "WPOL"}),
    "WMATIC": frozenset({"POL", "MATIC", "WMATIC", "WPOL"}),
    "WPOL": frozenset({"POL", "MATIC", "WMATIC", "WPOL"}),
}

_STABLE_COINS = frozenset({
    "USDT", "USDC", "USD", "BUSD", "DAI", "FDUSD", "USD1", "TUSD", "USDE", "PYUSD",
})

# MEXC spot symbols for pricing wrapped / native starts.
_PRICE_LOOKUP: dict[str, tuple[str, ...]] = {
    "WBNB": ("BNB", "WBNB"),
    "BNB": ("BNB", "WBNB"),
    "WETH": ("ETH", "WETH"),
    "ETH": ("ETH", "WETH"),
    "WBTC": ("BTC", "WBTC"),
    "BTCB": ("BTC", "BTCB"),
    "WMATIC": ("POL", "MATIC", "WMATIC"),
    "WPOL": ("POL", "MATIC", "WPOL"),
    "POL": ("POL", "MATIC"),
    "MATIC": ("POL", "MATIC"),
}


class ChainEngine:
    """Discovers and validates multi-hop cyclic arbitrage chains."""

    def __init__(self, adapter_factory, price_service, fee_service=None):
        self._adapter_factory = adapter_factory
        self._price_service = price_service
        self._fee_service = fee_service
        # Graph: network → {token_addr_lower: [(pool_addr, other_token_addr, dex, version)]}
        self._graph: dict[str, dict[str, list[tuple[str, str, str, str]]]] = {}
        # Coin lookup: (network, addr_lower) → coin symbol
        self._coin_map: dict[tuple[str, str], str] = {}
        # Decimals: (network, addr_lower) → int
        self._decimals: dict[tuple[str, str], int] = {}
        self._diag: dict = {"counts": {}, "near_miss": [], "chains_tried": 0}

    def last_diagnostics(self) -> dict:
        return dict(self._diag)

    def _bump(self, key: str) -> None:
        c = self._diag.setdefault("counts", {})
        c[key] = int(c.get(key) or 0) + 1

    def _add_near_miss(self, row: dict) -> None:
        nm = self._diag.setdefault("near_miss", [])
        if len(nm) >= _NEAR_MISS_LIMIT:
            return
        nm.append(row)

    def _usd_price(self, coin: str) -> Decimal:
        """Resolve USD price; WBNB/WETH map to BNB/ETH on MEXC."""
        c = (coin or "").upper()
        if not c:
            return Decimal("0")
        if c in _STABLE_COINS:
            return Decimal("1")
        bases = _PRICE_LOOKUP.get(c, (c,))
        for base in bases:
            for quote in ("USDT", "USDC"):
                p = self._price_service.get_price(base, quote)
                if p is not None and p > 0:
                    return Decimal(p)
        return Decimal("0")

    def build_graph(self, pools: list[dict]) -> None:
        """Build token graph from pools cache."""
        graph: dict[str, dict[str, list[tuple[str, str, str, str]]]] = {}
        coin_map: dict[tuple[str, str], str] = {}
        decimals: dict[tuple[str, str], int] = {}

        for p in pools:
            net = p.get("network", "")
            token = (p.get("token_address") or "").lower()
            quote = (p.get("stablecoin_address") or "").lower()
            pool_addr = (p.get("pool_address") or "").lower()
            dex = p.get("dex", "")
            version = (p.get("pool_version") or "").lower()
            if not net or not token or not quote or not pool_addr:
                continue
            if not version or version == "v4":
                continue
            td = p.get("token_decimals")
            qd = p.get("stablecoin_decimals")
            if td is None or qd is None:
                continue

            if net not in graph:
                graph[net] = {}
            graph[net].setdefault(token, []).append((pool_addr, quote, dex, version))
            graph[net].setdefault(quote, []).append((pool_addr, token, dex, version))

            tc = p.get("token_coin") or ""
            qc = p.get("quote_coin") or p.get("stablecoin_coin") or ""
            if tc:
                coin_map[(net, token)] = tc
            if qc:
                coin_map[(net, quote)] = qc

            decimals[(net, token)] = int(td)
            decimals[(net, quote)] = int(qd)

        # Ensure wrapped natives are addressable as WBNB/WETH start capitals.
        try:
            from config.wrapped_natives import WRAPPED_NATIVE_QUOTES

            for net, mapping in WRAPPED_NATIVE_QUOTES.items():
                if net not in graph:
                    continue
                for addr, price_coin in mapping.items():
                    a = addr.lower()
                    sym = {
                        "BNB": "WBNB",
                        "ETH": "WETH",
                        "BTC": "WBTC",
                        "POL": "WMATIC",
                    }.get((price_coin or "").upper(), (price_coin or "").upper())
                    if sym:
                        coin_map.setdefault((net, a), sym)
                    decimals.setdefault((net, a), 18)
        except Exception:
            pass

        self._graph = graph
        self._coin_map = coin_map
        self._decimals = decimals
        total_edges = sum(
            len(edges) for net_graph in graph.values() for edges in net_graph.values()
        )
        logger.info(
            "chain_graph_built: networks=%d edges=%d", len(graph), total_edges
        )

    def stable_start_addresses(self, network: str) -> list[str]:
        """Start capitals S present in graph (stables + wrapped natives).

        Ordered: USDT/USDC first, then natives, then other stables.
        """
        raw = {
            c.strip().upper()
            for c in str(
                settings.CHAIN_START_QUOTES
                or "USDT,USDC,WBNB,BNB,WETH,ETH,FDUSD,DAI"
            ).split(",")
            if c.strip()
        }
        wanted: set[str] = set()
        for c in raw:
            wanted |= set(_START_COIN_ALIASES.get(c, frozenset({c})))
        found: list[tuple[int, str]] = []
        seen: set[str] = set()
        prio_map = {
            "USDT": 0, "USDC": 1, "FDUSD": 4, "DAI": 5,
            "WBNB": 2, "BNB": 2, "WETH": 3, "ETH": 3,
        }
        for (net, addr), coin in self._coin_map.items():
            if net != network:
                continue
            cu = (coin or "").upper()
            if cu not in wanted:
                continue
            if addr in seen:
                continue
            if addr not in (self._graph.get(network) or {}):
                continue
            seen.add(addr)
            found.append((prio_map.get(cu, 9), addr))
        found.sort(key=lambda x: x[0])
        return [addr for _, addr in found]

    def find_chains(
        self,
        network: str,
        start_token: str,
        max_hops: int | None = None,
    ) -> list[list[tuple[str, str, str, str, str]]]:
        """Find cyclic chains starting and ending at start_token.

        Returns list of chains. Each chain is a list of hops:
            [(pool_addr, token_in, token_out, dex, version), ...]
        """
        if max_hops is None:
            max_hops = settings.CHAIN_MAX_HOPS
        net_graph = self._graph.get(network)
        if not net_graph:
            return []

        start = start_token.lower()
        chain_cap = max(5, int(settings.CHAIN_MAX_CHAINS_PER_START))
        intermediaries = {
            addr for addr, coin in
            ((k[1], v) for k, v in self._coin_map.items() if k[0] == network)
            if coin in INTERMEDIARIES.get(network, [])
        }

        chains: list[list[tuple[str, str, str, str, str]]] = []
        visited_pools: set[str] = set()

        def dfs(current: str, path: list[tuple[str, str, str, str, str]], depth: int):
            if depth > max_hops:
                return
            if depth >= 2 and current == start:
                chains.append(list(path))
                return
            edges = net_graph.get(current, [])
            for pool_addr, next_token, dex, version in edges:
                if pool_addr in visited_pools:
                    continue
                if depth >= 1 and next_token != start and next_token not in intermediaries:
                    continue
                visited_pools.add(pool_addr)
                path.append((pool_addr, current, next_token, dex, version))
                dfs(next_token, path, depth + 1)
                path.pop()
                visited_pools.discard(pool_addr)
                if len(chains) >= chain_cap:
                    return

        dfs(start, [], 0)
        return chains

    async def validate_chain(
        self,
        network: str,
        chain: list[tuple[str, str, str, str, str]],
        amount_in_raw: int,
    ) -> dict | None:
        """Quote a chain on-chain. Profit from simulated token amounts − gas.

        On hard quote failures returns ``{"_fail_reason": ..., "signal": False}``
        so callers can early-stop size ladders.
        """
        amount = Decimal(amount_in_raw)
        hop_results = []

        for pool_addr, token_in, token_out, dex, version in chain:
            adapter = self._adapter_factory.get_adapter(
                network, dex, pool_version=version,
            )
            if adapter is None:
                self._bump("no_adapter")
                return {"_fail_reason": "no_adapter", "signal": False}
            try:
                amount = await adapter.quote_exact_input(
                    network=network,
                    pool_address=pool_addr,
                    token_in=token_in,
                    token_out=token_out,
                    amount_in=int(amount),
                )
            except Exception:
                self._bump("quote_err")
                return {"_fail_reason": "quote_err", "signal": False}
            if amount <= 0:
                self._bump("quote0")
                return {"_fail_reason": "quote0", "signal": False}
            hop_results.append({
                "pool": pool_addr,
                "token_in": token_in,
                "token_out": token_out,
                "amount_out": int(amount),
                "dex": dex,
                "version": version,
            })

        # Gross edge from simulated token amounts only.
        if amount <= amount_in_raw:
            self._bump("no_gross")
            return {"_fail_reason": "no_gross", "signal": False}

        start_token = chain[0][1]
        dec = self._decimals.get((network, start_token), 18)
        input_human = Decimal(amount_in_raw) / Decimal(10 ** dec)
        output_human = amount / Decimal(10 ** dec)
        gross_token = output_human - input_human
        gross_pct = (gross_token / input_human) * 100 if input_human > 0 else Decimal("0")

        coin = self._coin_map.get((network, start_token), "")
        price_usd = self._usd_price(coin)
        if price_usd <= 0:
            self._bump("no_start_price")
            return {"_fail_reason": "no_start_price", "signal": False}

        input_usd = input_human * price_usd
        gross_usd = gross_token * price_usd

        gas_usd = Decimal("0")
        if self._fee_service is not None:
            try:
                for _pool, _tin, _tout, _dex, ver in chain:
                    gas_usd += await self._fee_service._estimate_gas_cost_usd(
                        network, ver or ""
                    )
            except Exception:
                gas_usd = Decimal("0.05") * len(chain)
        else:
            gas_usd = Decimal("0.05") * len(chain)

        net_usd = gross_usd - gas_usd
        net_pct = (net_usd / input_usd) * 100 if input_usd > 0 else Decimal("0")
        min_pct = settings.CHAIN_MIN_PROFIT_PCT
        is_signal = net_pct > min_pct and net_usd > 0
        if not is_signal:
            self._bump("below_min")
            if is_near_miss(net_pct, min_pct, settings.NEAR_MISS_MIN_PCT):
                self._add_near_miss({
                    "engine": "chain",
                    "network": network,
                    "token_coin": coin,
                    "hops": len(chain),
                    "gross_pct": float(gross_pct),
                    "net_pct": float(net_pct),
                    "gas_usd": float(gas_usd),
                    "reason": "near_miss",
                })
        else:
            self._bump("ok")

        return {
            "network": network,
            "start_token": start_token,
            "start_coin": coin,
            "token_coin": coin,
            "direction": "CHAIN_ARB",
            "chain": chain,
            "hops": len(chain),
            "hop_results": hop_results,
            "amount_in_raw": amount_in_raw,
            "amount_out_raw": int(amount),
            "gross_profit_pct": gross_pct,
            "gross_profit_usd": gross_usd,
            "profit_pct": net_pct,  # backward-compat for scanner
            "profit_usd": net_usd,
            "net_profit_pct": net_pct,
            "net_profit_usd": net_usd,
            "gas_usd": gas_usd,
            "input_usd": input_usd,
            "base_amount_usd": input_usd,
            "signal": is_signal,
            "executable": "inventory",
            "warnings": ["inventory_capital", "no_cex", "no_flashloan"],
        }

    async def scan_chains(
        self,
        network: str,
        token_addresses: list[str],
        base_amount_usd: Decimal | None = None,
    ) -> list[dict]:
        """Scan multi-hop chains for a batch of start tokens."""
        if base_amount_usd is None:
            base_amount_usd = settings.BASE_AMOUNT_USD

        results: list[dict] = []
        sem = asyncio.Semaphore(settings.RPC_MAX_CONCURRENCY)
        if "counts" not in self._diag:
            self._diag = {
                "counts": Counter(),
                "near_miss": [],
                "chains_tried": 0,
            }

        async def check_token(token_addr: str) -> None:
            async with sem:
                chains = self.find_chains(network, token_addr)
                for chain in chains[:5]:
                    self._diag["chains_tried"] = int(self._diag.get("chains_tried") or 0) + 1
                    dec = self._decimals.get((network, token_addr.lower()), 18)
                    coin = self._coin_map.get((network, token_addr.lower()), "")
                    price = self._usd_price(coin)
                    if price <= 0:
                        self._bump("no_start_price")
                        continue

                    curve: list[dict] = []
                    consecutive_quote0 = 0
                    min_pct = settings.CHAIN_MIN_PROFIT_PCT
                    near_floor = settings.NEAR_MISS_MIN_PCT
                    for size_usd in size_sweep_ladder_usd():
                        amount_tokens = size_usd / price
                        amount_raw = int(amount_tokens * Decimal(10 ** dec))
                        if amount_raw <= 0:
                            continue
                        result = await self.validate_chain(
                            network, chain, amount_raw
                        )
                        if result is None:
                            break
                        reason = result.get("_fail_reason")
                        if reason == "no_gross":
                            break
                        if reason in ("quote0", "quote_err", "no_adapter"):
                            consecutive_quote0 += 1
                            if consecutive_quote0 >= 2:
                                break
                            continue
                        if reason == "no_start_price":
                            break

                        consecutive_quote0 = 0
                        result["base_amount_usd"] = size_usd
                        net_usd = Decimal(str(result["net_profit_usd"]))
                        result["net_profit_pct"] = (
                            (net_usd / size_usd) * 100 if size_usd > 0 else Decimal("0")
                        )
                        result["profit_pct"] = result["net_profit_pct"]
                        result["signal"] = (
                            result["net_profit_pct"] > min_pct and net_usd > 0
                        )
                        curve.append(result)
                    best = pick_optimal_by_net_usd(curve)
                    if best is None:
                        continue
                    best = attach_size_bounds(best, curve)
                    results.append(best)
                    logger.info(
                        "chain_signal: %s %s hops=%d net=%.2f%% $%.2f "
                        "size=$%s (min=%s max=%s)",
                        network, coin, len(chain),
                        float(best["net_profit_pct"]),
                        float(best["net_profit_usd"]),
                        best.get("size_optimal_usd") or best["base_amount_usd"],
                        best.get("size_min_usd"),
                        best.get("size_max_usd"),
                    )

        await asyncio.gather(*(check_token(t) for t in token_addresses))
        return results

    async def scan_from_stables(
        self,
        networks: list[str] | None = None,
        base_amount_usd: Decimal | None = None,
    ) -> list[dict]:
        from config.networks import ACTIVE_NETWORKS

        nets = networks or sorted(ACTIVE_NETWORKS & set(self._graph.keys()))
        self._diag = {
            "counts": Counter(),
            "near_miss": [],
            "chains_tried": 0,
            "networks": nets,
        }
        out: list[dict] = []
        for net in nets:
            starts = self.stable_start_addresses(net)
            if not starts:
                continue
            out.extend(await self.scan_chains(net, starts, base_amount_usd))
        counts = dict(self._diag.get("counts") or {})
        self._diag["counts"] = counts
        self._diag["signals"] = len(out)
        logger.info(
            "chain_diag: tried=%d signals=%d %s near_miss=%d",
            self._diag.get("chains_tried") or 0,
            len(out),
            counts,
            len(self._diag.get("near_miss") or []),
        )
        return out
