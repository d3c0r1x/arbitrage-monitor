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
from collections import Counter, defaultdict
from decimal import Decimal
from itertools import combinations

from config.networks import is_network_active
from config.settings import settings
from services.size_sweep import (
    attach_size_bounds,
    is_near_miss,
    pick_optimal_by_net_usd,
    size_sweep_ladder_usd,
)

logger = logging.getLogger(__name__)

# Prefer liquid quote assets for the round-trip.
_QUOTE_PRIORITY = ("USDT", "USDC", "WBNB", "WETH", "BNB", "ETH")
_NEAR_MISS_LIMIT = 20


class CrossDexEngine:
    """Finds same-pair price gaps across DEXes on one network."""

    def __init__(self, adapter_factory, price_service, fee_service=None):
        self._adapter_factory = adapter_factory
        self._price_service = price_service
        self._fee_service = fee_service
        # (network, token, quote) → list[pool dict]
        self._groups: dict[tuple[str, str, str], list[dict]] = {}
        self._pool_count = 0
        self._diag: dict = {"counts": {}, "near_miss": [], "routes_tried": 0}
        self._hot_scorer = None  # optional callable(network, token) -> float

    def set_hot_scorer(self, scorer) -> None:
        self._hot_scorer = scorer

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
            ver = (p.get("pool_version") or "").lower()
            if not ver or ver == "v4":
                # v4 quotes always 0 without PoolKey — skip silently at index.
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

        # Rank groups: hot/event tokens first, then stable quotes, then more DEXes.
        def rank(item: tuple[tuple[str, str, str], list[dict]]) -> tuple:
            (net, token, _q), pools = item
            qc = (pools[0].get("quote_coin") or pools[0].get("stablecoin_coin") or "").upper()
            prio = _QUOTE_PRIORITY.index(qc) if qc in _QUOTE_PRIORITY else 99
            hot = 0.0
            if self._hot_scorer is not None:
                try:
                    hot = float(self._hot_scorer(net, token) or 0)
                except Exception:
                    hot = 0.0
            return (-hot, prio, -len({(p.get("dex") or "") for p in pools}))

        ranked = sorted(self._groups.items(), key=rank)[: max(1, int(max_pairs))]
        sem = asyncio.Semaphore(settings.RPC_MAX_CONCURRENCY)
        results: list[dict] = []
        self._diag = {
            "counts": Counter(),
            "near_miss": [],
            "routes_tried": 0,
            "groups_scanned": len(ranked),
        }

        async def check_group(key: tuple[str, str, str], pools: list[dict]) -> None:
            async with sem:
                found = await self._scan_group(key, pools, base_amount_usd, min_pct)
                if found:
                    results.extend(found)

        await asyncio.gather(*(check_group(k, v) for k, v in ranked))
        counts = dict(self._diag.get("counts") or {})
        self._diag["counts"] = counts
        self._diag["signals"] = len(results)
        logger.info(
            "cross_dex_diag: groups=%d routes=%d signals=%d %s near_miss=%d",
            self._diag.get("groups_scanned") or 0,
            self._diag.get("routes_tried") or 0,
            len(results),
            counts,
            len(self._diag.get("near_miss") or []),
        )
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

        quote_price = Decimal("1")
        if quote_coin not in ("USDT", "USDC", "USD", "BUSD", "DAI", "FDUSD", "USD1"):
            qp = self._price_service.get_price(quote_coin, "USDT")
            if qp is None or qp <= 0:
                qp = self._price_service.get_price(quote_coin, "USDC")
            if qp is None or qp <= 0:
                self._bump("no_quote_price")
                return []
            quote_price = qp

        # Size comes from ladder inside _try_route_sweep (not fixed BASE_AMOUNT).
        found: list[dict] = []
        for buy_p, sell_p in combinations(by_dex.values(), 2):
            for a, b in ((buy_p, sell_p), (sell_p, buy_p)):
                self._diag["routes_tried"] = int(self._diag.get("routes_tried") or 0) + 1
                opp = await self._try_route_sweep(
                    network=net,
                    token=token,
                    quote=quote,
                    buy_pool=a,
                    sell_pool=b,
                    quote_price_usd=quote_price,
                    token_coin=token_coin,
                    quote_coin=quote_coin,
                    quote_dec=quote_dec,
                    min_pct=min_pct,
                )
                if opp:
                    found.append(opp)
                    break  # one direction enough per unordered pair
        return found

    async def _try_route_sweep(
        self,
        *,
        network: str,
        token: str,
        quote: str,
        buy_pool: dict,
        sell_pool: dict,
        quote_price_usd: Decimal,
        token_coin: str,
        quote_coin: str,
        quote_dec: int,
        min_pct: Decimal,
    ) -> dict | None:
        """Probe ladder sizes; keep optimal executable clip (max net USD)."""
        ladder = size_sweep_ladder_usd()
        curve: list[dict] = []
        consecutive_quote0 = 0
        near_floor = settings.NEAR_MISS_MIN_PCT

        for size_usd in ladder:
            amount_quote = size_usd / quote_price_usd
            amount_in_raw = int(amount_quote * Decimal(10 ** quote_dec))
            if amount_in_raw <= 0:
                continue
            row = await self._eval_route(
                network=network,
                token=token,
                quote=quote,
                buy_pool=buy_pool,
                sell_pool=sell_pool,
                amount_in_raw=amount_in_raw,
                base_amount_usd=size_usd,
                quote_price_usd=quote_price_usd,
                token_coin=token_coin,
                quote_coin=quote_coin,
                min_pct=min_pct,
            )
            reason = row.get("_fail_reason")
            if reason in ("buy_quote0", "sell_quote0", "quote_err", "no_adapter"):
                self._bump(reason if reason != "quote_err" else "quote_err")
                consecutive_quote0 += 1
                if consecutive_quote0 >= 2:
                    break
                continue
            if reason == "no_gross":
                self._bump("no_gross")
                break
            if reason == "bad_amount":
                continue

            consecutive_quote0 = 0
            curve.append(row)
            net_pct = Decimal(str(row.get("net_profit_pct") or 0))
            if reason == "below_min" or (
                not row.get("signal")
                and is_near_miss(net_pct, min_pct, near_floor)
            ):
                if is_near_miss(net_pct, min_pct, near_floor):
                    self._add_near_miss({
                        "engine": "cross",
                        "network": network,
                        "token_coin": token_coin,
                        "buy_dex": row.get("buy_dex"),
                        "sell_dex": row.get("sell_dex"),
                        "size_usd": float(size_usd),
                        "gross_pct": float(row.get("gross_profit_pct") or 0),
                        "net_pct": float(net_pct),
                        "reason": "near_miss",
                    })
                    self._bump("below_min")

        best = pick_optimal_by_net_usd(curve)
        if best is None:
            return None
        best = attach_size_bounds(best, curve)
        self._bump("ok")
        logger.info(
            "cross_dex_signal: %s %s %s→%s net=%.3f%% $%.4f size=$%s "
            "(min=%s max=%s)",
            network,
            token_coin,
            best.get("buy_dex"),
            best.get("sell_dex"),
            float(best["net_profit_pct"]),
            float(best["net_profit_usd"]),
            best.get("size_optimal_usd") or best["base_amount_usd"],
            best.get("size_min_usd"),
            best.get("size_max_usd"),
        )
        return best

    async def _quote_or_flip(
        self,
        network: str,
        pool: dict,
        dex: str,
        version: str,
        adapter,
        *,
        token_in: str,
        token_out: str,
        amount_in: int,
        side: str,
        silent: bool = False,
    ) -> tuple[Decimal | None, str | None]:
        """Quote once; on 0, retry alternate v2↔v3. Returns (out, fail_reason)."""
        try:
            out = await adapter.quote_exact_input(
                network=network,
                pool_address=pool["pool_address"],
                token_in=token_in,
                token_out=token_out,
                amount_in=amount_in,
            )
        except Exception:
            if not silent:
                self._bump("quote_err")
            return None, "quote_err"
        if out and out > 0:
            return out, None

        alt = "v3" if (version or "").lower() == "v2" else (
            "v2" if (version or "").lower() == "v3" else None
        )
        if not alt:
            if not silent:
                self._bump(f"{side}_quote0")
            return None, f"{side}_quote0"
        alt_adapter = self._adapter_factory.get_adapter(network, dex, pool_version=alt)
        if alt_adapter is None or alt_adapter is adapter:
            if not silent:
                self._bump(f"{side}_quote0")
            return None, f"{side}_quote0"
        try:
            out2 = await alt_adapter.quote_exact_input(
                network=network,
                pool_address=pool["pool_address"],
                token_in=token_in,
                token_out=token_out,
                amount_in=amount_in,
            )
        except Exception:
            if not silent:
                self._bump("quote_err")
            return None, "quote_err"
        if out2 and out2 > 0:
            if not silent:
                self._bump("version_flip_ok")
            else:
                self._bump("version_flip_ok")
            pool["pool_version"] = alt
            return out2, None
        if not silent:
            self._bump(f"{side}_quote0")
        return None, f"{side}_quote0"

    async def _eval_route(
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
    ) -> dict:
        """Evaluate one size. Always returns a dict; may set `_fail_reason`."""
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
            return {"_fail_reason": "no_adapter", "signal": False}

        token_out, fail = await self._quote_or_flip(
            network, buy_pool, buy_dex, buy_ver, buy_adapter,
            token_in=quote, token_out=token, amount_in=amount_in_raw,
            side="buy", silent=True,
        )
        if token_out is None or token_out <= 0:
            return {"_fail_reason": fail or "buy_quote0", "signal": False}
        quote_out, fail = await self._quote_or_flip(
            network, sell_pool, sell_dex, sell_ver, sell_adapter,
            token_in=token, token_out=quote, amount_in=int(token_out),
            side="sell", silent=True,
        )
        if quote_out is None or quote_out <= 0:
            return {"_fail_reason": fail or "sell_quote0", "signal": False}
        if quote_out <= amount_in_raw:
            return {"_fail_reason": "no_gross", "signal": False}

        quote_dec = int(buy_pool["stablecoin_decimals"])
        in_human = Decimal(amount_in_raw) / Decimal(10 ** quote_dec)
        out_human = Decimal(quote_out) / Decimal(10 ** quote_dec)
        gross_pct = ((out_human - in_human) / in_human) * 100 if in_human > 0 else Decimal("0")
        gross_usd = (out_human - in_human) * quote_price_usd

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
        is_signal = net_pct >= min_pct and net_usd > 0

        chain = [
            (buy_pool["pool_address"], quote, token, buy_dex, buy_ver),
            (sell_pool["pool_address"], token, quote, sell_dex, sell_ver),
        ]
        row = {
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
            "signal": is_signal,
            "direction": "DEX_DEX",
            "executable": "inventory",
            "warnings": ["inventory_capital", "no_cex", "no_flashloan"],
        }
        if not is_signal:
            row["_fail_reason"] = "below_min"
        return row
