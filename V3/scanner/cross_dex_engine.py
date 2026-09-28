"""
Same-pair cross-DEX arbitrage (DEX ↔ DEX).

For each (network, token, quote) with ≥2 distinct DEX pools, try both
directions:

  quote → buy token on pool A → sell token on pool B → quote

Profit if quote_out > quote_in after gas. No MEXC leg.

Throttled by the scanner (CROSS_DEX_SCAN_EVERY_N_CYCLES) to protect CU budget.
Default networks: BSC + BASE (ACTIVE set).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from decimal import Decimal
from itertools import combinations

from config.networks import is_network_active
from config.settings import settings

logger = logging.getLogger(__name__)

# Prefer liquid quote assets for the round-trip.
_QUOTE_PRIORITY = ("USDT", "USDC", "WBNB", "WETH", "BNB", "ETH")


class CrossDexEngine:
    """Finds same-pair price gaps across DEXes on one network."""

    def __init__(self, adapter_factory, price_service, fee_service=None):
        self._adapter_factory = adapter_factory
        self._price_service = price_service
        self._fee_service = fee_service
        # (network, token, quote) → list[pool dict]
        self._groups: dict[tuple[str, str, str], list[dict]] = {}
        self._pool_count = 0

    def build_groups(self, pools: list[dict]) -> int:
        """Index pools by same-pair key. Returns number of multi-DEX groups."""
        groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for p in pools:
            net = (p.get("network") or "").upper()
            if not is_network_active(net):
                continue
            token = (p.get("token_address") or "").lower()
            quote = (p.get("stablecoin_address") or p.get("quote_address") or "").lower()
            if not token or not quote or token == quote:
                continue
            if not p.get("pool_version"):
                continue
            if p.get("token_decimals") is None or p.get("stablecoin_decimals") is None:
                continue
            groups[(net, token, quote)].append(p)

        # Keep only groups with ≥2 different DEX ids.
        multi = {
            k: v
            for k, v in groups.items()
            if len({(x.get("dex") or "").lower() for x in v}) >= 2
        }
        self._groups = multi
        self._pool_count = len(pools)
        return len(multi)

    async def scan(
        self,
        base_amount_usd: Decimal | None = None,
        max_pairs: int | None = None,
    ) -> list[dict]:
        """Scan multi-DEX same-pair groups. Returns profitable opportunities."""
        if not self._groups:
            return []
        if base_amount_usd is None:
            base_amount_usd = settings.BASE_AMOUNT_USD
        if max_pairs is None:
            max_pairs = settings.CROSS_DEX_MAX_PAIRS_PER_CYCLE
        min_pct = settings.CROSS_DEX_MIN_PROFIT_PCT

        # Rank groups: prefer stable quotes, then more DEXes.
        def rank(item: tuple[tuple[str, str, str], list[dict]]) -> tuple:
            (_net, _t, _q), pools = item
            qc = (pools[0].get("quote_coin") or pools[0].get("stablecoin_coin") or "").upper()
            prio = _QUOTE_PRIORITY.index(qc) if qc in _QUOTE_PRIORITY else 99
            return (prio, -len({(p.get("dex") or "") for p in pools}))

        ranked = sorted(self._groups.items(), key=rank)[: max(1, int(max_pairs))]
        sem = asyncio.Semaphore(settings.RPC_MAX_CONCURRENCY)
        results: list[dict] = []

        async def check_group(key: tuple[str, str, str], pools: list[dict]) -> None:
            async with sem:
                found = await self._scan_group(key, pools, base_amount_usd, min_pct)
                if found:
                    results.extend(found)

        await asyncio.gather(*(check_group(k, v) for k, v in ranked))
        return results

    async def _scan_group(
        self,
        key: tuple[str, str, str],
        pools: list[dict],
        base_amount_usd: Decimal,
        min_pct: Decimal,
    ) -> list[dict]:
        net, token, quote = key
        # One representative pool per DEX (prefer higher version label stability).
        by_dex: dict[str, dict] = {}
        for p in pools:
            dex = (p.get("dex") or "unknown").lower()
            if dex not in by_dex:
                by_dex[dex] = p
        if len(by_dex) < 2:
            return []

        quote_dec = int(pools[0]["stablecoin_decimals"])
        token_coin = pools[0].get("token_coin") or ""
        quote_coin = (
            pools[0].get("quote_coin")
            or pools[0].get("stablecoin_coin")
            or "USDT"
        ).upper()

        # Size in quote-asset units (assume ~$1 for stables; else MEXC price).
        quote_price = Decimal("1")
        if quote_coin not in ("USDT", "USDC", "USD", "BUSD", "DAI"):
            qp = self._price_service.get_price(quote_coin, "USDT")
            if qp is None or qp <= 0:
                qp = self._price_service.get_price(quote_coin, "USDC")
            if qp is None or qp <= 0:
                return []
            quote_price = qp
        amount_quote = base_amount_usd / quote_price
        amount_in_raw = int(amount_quote * Decimal(10 ** quote_dec))
        if amount_in_raw <= 0:
            return []

        found: list[dict] = []
        for buy_p, sell_p in combinations(by_dex.values(), 2):
            for a, b in ((buy_p, sell_p), (sell_p, buy_p)):
                opp = await self._try_route(
                    network=net,
                    token=token,
                    quote=quote,
                    buy_pool=a,
                    sell_pool=b,
                    amount_in_raw=amount_in_raw,
                    base_amount_usd=base_amount_usd,
                    quote_price_usd=quote_price,
                    token_coin=token_coin,
                    quote_coin=quote_coin,
                    min_pct=min_pct,
                )
                if opp:
                    found.append(opp)
                    break  # one direction enough per unordered pair
        return found

    async def _try_route(
        self,
        *,
        network: str,
        token: str,
        quote: str,
        buy_pool: dict,
        sell_pool: dict,
        amount_in_raw: int,
        base_amount_usd: Decimal,
        quote_price_usd: Decimal,
        token_coin: str,
        quote_coin: str,
        min_pct: Decimal,
    ) -> dict | None:
        buy_dex = buy_pool.get("dex") or ""
        sell_dex = sell_pool.get("dex") or ""
        buy_ver = buy_pool.get("pool_version") or ""
        sell_ver = sell_pool.get("pool_version") or ""
        buy_adapter = self._adapter_factory.get_adapter(
            network, buy_dex, pool_version=buy_ver
        )
        sell_adapter = self._adapter_factory.get_adapter(
            network, sell_dex, pool_version=sell_ver
        )
        if buy_adapter is None or sell_adapter is None:
            return None

        try:
            token_out = await buy_adapter.quote_exact_input(
                network=network,
                pool_address=buy_pool["pool_address"],
                token_in=quote,
                token_out=token,
                amount_in=amount_in_raw,
            )
            if token_out <= 0:
                return None
            quote_out = await sell_adapter.quote_exact_input(
                network=network,
                pool_address=sell_pool["pool_address"],
                token_in=token,
                token_out=quote,
                amount_in=int(token_out),
            )
        except Exception:
            return None

        if quote_out <= 0 or quote_out <= amount_in_raw:
            return None

        quote_dec = int(buy_pool["stablecoin_decimals"])
        in_human = Decimal(amount_in_raw) / Decimal(10 ** quote_dec)
        out_human = Decimal(quote_out) / Decimal(10 ** quote_dec)
        gross_pct = ((out_human - in_human) / in_human) * 100 if in_human > 0 else Decimal("0")
        gross_usd = (out_human - in_human) * quote_price_usd

        # Gas for 2 hops (buy + sell).
        gas_usd = Decimal("0")
        if self._fee_service is not None:
            try:
                gas_usd = await self._fee_service._estimate_gas_cost_usd(network, buy_ver)
                gas_usd += await self._fee_service._estimate_gas_cost_usd(network, sell_ver)
            except Exception:
                gas_usd = Decimal("0.1")
        else:
            gas_usd = Decimal("0.1")

        net_usd = gross_usd - gas_usd
        net_pct = (net_usd / base_amount_usd) * 100 if base_amount_usd > 0 else Decimal("0")
        if net_pct < min_pct or net_usd <= 0:
            return None

        chain = [
            (
                buy_pool["pool_address"],
                quote,
                token,
                buy_dex,
                buy_ver,
            ),
            (
                sell_pool["pool_address"],
                token,
                quote,
                sell_dex,
                sell_ver,
            ),
        ]
        logger.info(
            "cross_dex_signal: %s %s %s→%s net=%.3f%% $%.4f size=$%s",
            network,
            token_coin,
            buy_dex,
            sell_dex,
            float(net_pct),
            float(net_usd),
            base_amount_usd,
        )
        return {
            "network": network,
            "token_address": token,
            "quote_address": quote,
            "token_coin": token_coin,
            "quote_coin": quote_coin,
            "quote_decimals": quote_dec,
            "quote_price_usd": quote_price_usd,
            "buy_dex": buy_dex,
            "sell_dex": sell_dex,
            "buy_pool": buy_pool["pool_address"],
            "sell_pool": sell_pool["pool_address"],
            "buy_version": buy_ver,
            "sell_version": sell_ver,
            "chain": chain,
            "amount_in_raw": amount_in_raw,
            "amount_out_raw": int(quote_out),
            "base_amount_usd": base_amount_usd,
            "gross_profit_pct": gross_pct,
            "gross_profit_usd": gross_usd,
            "net_profit_pct": net_pct,
            "net_profit_usd": net_usd,
            "gas_usd": gas_usd,
            "signal": True,
            "direction": "DEX_DEX",
        }
