"""
Multi-hop Chain Arbitrage Engine.

Finds cyclic arbitrage paths through DEX pools:
  A → B → C → B → A  (max 4 hops, max 5 nodes)
where output(A) > input(A) after fees.

Uses pools_cache.json graph. Intermediary tokens: WBNB, WETH, WBTC, WMATIC,
USDT, USDC per network. Verifies each chain on-chain via adapter quotes.
"""

import asyncio
import logging
from decimal import Decimal

from config.settings import settings

logger = logging.getLogger(__name__)

# Intermediary tokens per network (high-liquidity routing nodes).
INTERMEDIARIES: dict[str, list[str]] = {
    "ETHEREUM": ["WETH", "USDT", "USDC", "WBTC"],
    "BSC": ["WBNB", "USDT", "USDC", "ETH"],
    "POLYGON": ["WMATIC", "USDT", "USDC", "WETH"],
    "ARBITRUM": ["WETH", "USDT", "USDC"],
    "BASE": ["WETH", "USDC", "USDT"],
    "ROBINHOOD": ["WETH", "USDT"],
}


class ChainEngine:
    """Discovers and validates multi-hop cyclic arbitrage chains."""

    def __init__(self, adapter_factory, price_service):
        self._adapter_factory = adapter_factory
        self._price_service = price_service
        # Graph: network → {token_addr_lower: [(pool_addr, other_token_addr, dex, version)]}
        self._graph: dict[str, dict[str, list[tuple[str, str, str, str]]]] = {}
        # Coin lookup: (network, addr_lower) → coin symbol
        self._coin_map: dict[tuple[str, str], str] = {}
        # Decimals: (network, addr_lower) → int
        self._decimals: dict[tuple[str, str], int] = {}

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
            version = p.get("pool_version", "")
            if not net or not token or not quote or not pool_addr:
                continue
            # Fail-closed (plan v3 D1/D2): pools without version or
            # decimals cannot be quoted correctly — exclude from graph.
            if not version:
                continue
            td = p.get("token_decimals")
            qd = p.get("stablecoin_decimals")
            if td is None or qd is None:
                continue

            if net not in graph:
                graph[net] = {}
            # Bidirectional edges.
            graph[net].setdefault(token, []).append((pool_addr, quote, dex, version))
            graph[net].setdefault(quote, []).append((pool_addr, token, dex, version))

            # Coin map.
            tc = p.get("token_coin") or ""
            qc = p.get("quote_coin") or p.get("stablecoin_coin") or ""
            if tc:
                coin_map[(net, token)] = tc
            if qc:
                coin_map[(net, quote)] = qc

            # Decimals (validated non-None above).
            decimals[(net, token)] = int(td)
            decimals[(net, quote)] = int(qd)

        self._graph = graph
        self._coin_map = coin_map
        self._decimals = decimals
        total_edges = sum(
            len(edges) for net_graph in graph.values() for edges in net_graph.values()
        )
        logger.info(
            "chain_graph_built: networks=%d edges=%d", len(graph), total_edges
        )

    def find_chains(
        self,
        network: str,
        start_token: str,
        max_hops: int | None = None,
    ) -> list[list[tuple[str, str, str, str, str]]]:
        """Find cyclic chains starting and ending at start_token.

        Returns list of chains. Each chain is a list of hops:
            [(pool_addr, token_in, token_out, dex, version), ...]
        Max length = max_hops (default settings.CHAIN_MAX_HOPS = 4).
        Pattern: A-B-C-B-A uses 4 hops through intermediary tokens.
        """
        if max_hops is None:
            max_hops = settings.CHAIN_MAX_HOPS
        net_graph = self._graph.get(network)
        if not net_graph:
            return []

        start = start_token.lower()
        intermediaries = {
            addr for addr, coin in
            ((k[1], v) for k, v in self._coin_map.items() if k[0] == network)
            if coin in INTERMEDIARIES.get(network, [])
        }

        chains: list[list[tuple[str, str, str, str, str]]] = []
        # BFS/DFS with depth limit. Only route through intermediaries for
        # performance (avoids combinatorial explosion).
        visited_pools: set[str] = set()

        def dfs(current: str, path: list[tuple[str, str, str, str, str]], depth: int):
            if depth > max_hops:
                return
            if depth >= 2 and current == start:
                # Found cycle back to start (min 2 hops for meaningful arb).
                chains.append(list(path))
                return
            edges = net_graph.get(current, [])
            for pool_addr, next_token, dex, version in edges:
                if pool_addr in visited_pools:
                    continue
                # Only allow intermediaries or return-to-start.
                if depth >= 1 and next_token != start and next_token not in intermediaries:
                    continue
                visited_pools.add(pool_addr)
                path.append((pool_addr, current, next_token, dex, version))
                dfs(next_token, path, depth + 1)
                path.pop()
                visited_pools.discard(pool_addr)
                # Limit results for performance.
                if len(chains) >= 20:
                    return

        dfs(start, [], 0)
        return chains

    async def validate_chain(
        self,
        network: str,
        chain: list[tuple[str, str, str, str, str]],
        amount_in_raw: int,
    ) -> dict | None:
        """Quote a chain on-chain. Return profit dict if output > input, else None.

        Each hop: (pool_addr, token_in, token_out, dex, version).
        """
        amount = Decimal(amount_in_raw)
        hop_results = []

        for pool_addr, token_in, token_out, dex, version in chain:
            adapter = self._adapter_factory.get_adapter(
                network, dex, pool_version=version,
            )
            if adapter is None:
                return None
            try:
                amount = await adapter.quote_exact_input(
                    network=network,
                    pool_address=pool_addr,
                    token_in=token_in,
                    token_out=token_out,
                    amount_in=int(amount),
                )
            except Exception:
                return None
            if amount <= 0:
                return None
            hop_results.append({
                "pool": pool_addr,
                "token_in": token_in,
                "token_out": token_out,
                "amount_out": amount,
            })

        # Verify: output > input (gross, before gas).
        if amount <= amount_in_raw:
            return None

        # Calculate profit %.
        start_token = chain[0][1]
        dec = self._decimals.get((network, start_token), 18)
        input_human = Decimal(amount_in_raw) / Decimal(10 ** dec)
        output_human = amount / Decimal(10 ** dec)
        profit_pct = ((output_human - input_human) / input_human) * 100 if input_human > 0 else Decimal("0")

        # Get USD value for reporting.
        coin = self._coin_map.get((network, start_token), "")
        price_usd = Decimal("0")
        if coin:
            p = self._price_service.get_price(coin, "USDT")
            if p and p > 0:
                price_usd = p
        input_usd = input_human * price_usd
        profit_usd = (output_human - input_human) * price_usd

        return {
            "network": network,
            "start_token": start_token,
            "start_coin": coin,
            "chain": chain,
            "hop_results": hop_results,
            "amount_in_raw": amount_in_raw,
            "amount_out_raw": int(amount),
            "profit_pct": profit_pct,
            "profit_usd": profit_usd,
            "input_usd": input_usd,
            "signal": profit_pct > settings.CHAIN_MIN_PROFIT_PCT,
        }

    async def scan_chains(
        self,
        network: str,
        token_addresses: list[str],
        base_amount_usd: Decimal | None = None,
    ) -> list[dict]:
        """Scan multi-hop chains for a batch of tokens. Returns profitable chains."""
        if base_amount_usd is None:
            base_amount_usd = settings.BASE_AMOUNT_USD

        results: list[dict] = []
        sem = asyncio.Semaphore(settings.RPC_MAX_CONCURRENCY)

        async def check_token(token_addr: str) -> None:
            async with sem:
                chains = self.find_chains(network, token_addr)
                for chain in chains[:5]:  # Limit per token.
                    # Compute raw amount from USD.
                    dec = self._decimals.get((network, token_addr.lower()), 18)
                    coin = self._coin_map.get((network, token_addr.lower()), "")
                    price = Decimal("0")
                    if coin:
                        p = self._price_service.get_price(coin, "USDT")
                        if p and p > 0:
                            price = p
                    if price <= 0:
                        continue
                    amount_tokens = base_amount_usd / price
                    amount_raw = int(amount_tokens * Decimal(10 ** dec))
                    if amount_raw <= 0:
                        continue

                    result = await self.validate_chain(network, chain, amount_raw)
                    if result and result["signal"]:
                        results.append(result)
                        logger.info(
                            "chain_signal: %s %s hops=%d profit=%.2f%% $%.2f",
                            network, coin, len(chain),
                            float(result["profit_pct"]), float(result["profit_usd"]),
                        )

        await asyncio.gather(*(check_token(t) for t in token_addresses))
        return results
