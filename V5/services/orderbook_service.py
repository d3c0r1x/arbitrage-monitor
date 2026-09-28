"""
MEXC order-book fill simulation.

Final gate after DEX+USDT profit calc: walk live depth to find the
trade size that maximizes net USD profit (not a fixed $10 clip).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BookFill:
    """Result of walking the order book for a market-style size."""

    side: str  # BUY | SELL
    fully_filled: bool
    base_filled: Decimal
    quote_filled: Decimal
    avg_price: Decimal
    best_price: Decimal
    levels_used: int
    impact_pct: Decimal  # vs best bid/ask, percent


@dataclass(frozen=True)
class OptimalBookTrade:
    """Best executable clip found by walking the book."""

    fill: BookFill
    cost_usd: Decimal
    settlement_usd: Decimal
    net_profit_usd: Decimal
    net_profit_pct: Decimal
    tokens_gross: Decimal
    tokens_net: Decimal

    # Back-compat alias used by older call sites.
    @property
    def fills(self) -> BookFill:
        return object.__getattribute__(self, "fill")


@dataclass(frozen=True)
class SizeCurvePoint:
    """One point on the size → net-profit curve (for dashboard slider)."""

    size_usd: Decimal
    net_usd: Decimal
    net_pct: Decimal
    vwap: Decimal
    impact_pct: Decimal
    tokens: Decimal


def v2_amount_out(
    amount_in: Decimal,
    reserve_in: Decimal,
    reserve_out: Decimal,
    fee_bps: int = 25,
) -> Decimal:
    """Constant-product out amount (Uniswap V2 style). No RPC."""
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return Decimal("0")
    fee_num = Decimal(10000) - Decimal(fee_bps)
    if fee_num <= 0:
        return Decimal("0")
    numerator = amount_in * fee_num * reserve_out
    denominator = reserve_in * Decimal(10000) + amount_in * fee_num
    if denominator <= 0:
        return Decimal("0")
    return numerator / denominator


def _downsample_curve(points: list[SizeCurvePoint], max_points: int = 48) -> list[SizeCurvePoint]:
    if len(points) <= max_points:
        return points
    # Keep endpoints + evenly spaced middle samples.
    out = [points[0]]
    step = (len(points) - 1) / (max_points - 1)
    for i in range(1, max_points - 1):
        idx = int(round(i * step))
        out.append(points[idx])
    out.append(points[-1])
    # De-dupe identical sizes while preserving order.
    seen: set[str] = set()
    uniq: list[SizeCurvePoint] = []
    for p in out:
        key = f"{p.size_usd:.8f}"
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def _parse_levels(raw_levels: list | None) -> list[tuple[Decimal, Decimal]]:
    levels: list[tuple[Decimal, Decimal]] = []
    for row in raw_levels or []:
        if not row or len(row) < 2:
            continue
        try:
            price = Decimal(str(row[0]))
            qty = Decimal(str(row[1]))
        except Exception:
            continue
        if price > 0 and qty > 0:
            levels.append((price, qty))
    return levels


def _fill_from_cum(
    side: str,
    best: Decimal,
    base: Decimal,
    quote: Decimal,
    levels_used: int,
    fully: bool,
) -> BookFill | None:
    if base <= 0 or quote <= 0:
        return None
    avg = quote / base
    if side == "BUY":
        impact = ((avg - best) / best) * Decimal("100") if best > 0 else Decimal("0")
    else:
        impact = ((best - avg) / best) * Decimal("100") if best > 0 else Decimal("0")
    return BookFill(
        side=side,
        fully_filled=fully,
        base_filled=base,
        quote_filled=quote,
        avg_price=avg,
        best_price=best,
        levels_used=levels_used,
        impact_pct=impact,
    )


class OrderbookService:
    """Fetch MEXC depth and simulate market fills / optimal clips."""

    def __init__(self, mexc_client, depth_limit: int = 100):
        self._mexc_client = mexc_client
        self._depth_limit = depth_limit

    async def fetch_book(self, symbol: str) -> dict | None:
        """Return raw depth JSON or None on failure."""
        try:
            data = await self._mexc_client.get_order_book(
                symbol=symbol, limit=self._depth_limit
            )
            if not isinstance(data, dict):
                return None
            return data
        except Exception as exc:
            logger.warning("orderbook_fetch_failed: %s %s", symbol, exc)
            return None

    def simulate_buy(
        self,
        book: dict,
        quote_budget: Decimal,
    ) -> BookFill | None:
        """Market-buy: spend up to quote_budget USDT walking asks."""
        if quote_budget <= 0:
            return None
        asks = sorted(_parse_levels(book.get("asks")), key=lambda x: x[0])
        if not asks:
            return None
        best = asks[0][0]
        remaining = quote_budget
        base = Decimal("0")
        quote = Decimal("0")
        levels = 0
        for price, qty in asks:
            level_quote = price * qty
            if remaining <= level_quote:
                take_base = remaining / price
                base += take_base
                quote += remaining
                levels += 1
                remaining = Decimal("0")
                break
            base += qty
            quote += level_quote
            remaining -= level_quote
            levels += 1
        return _fill_from_cum("BUY", best, base, quote, levels, remaining <= 0)

    def simulate_sell(
        self,
        book: dict,
        base_amount: Decimal,
    ) -> BookFill | None:
        """Market-sell: sell base_amount of token walking bids."""
        if base_amount <= 0:
            return None
        bids = sorted(_parse_levels(book.get("bids")), key=lambda x: x[0], reverse=True)
        if not bids:
            return None
        best = bids[0][0]
        remaining = base_amount
        base = Decimal("0")
        quote = Decimal("0")
        levels = 0
        for price, qty in bids:
            if remaining <= qty:
                base += remaining
                quote += remaining * price
                levels += 1
                remaining = Decimal("0")
                break
            base += qty
            quote += qty * price
            remaining -= qty
            levels += 1
        return _fill_from_cum("SELL", best, base, quote, levels, remaining <= 0)

    def maximize_dir_b_profit(
        self,
        book: dict,
        *,
        dex_usdt_per_token: Decimal,
        fee_token: Decimal,
        fixed_fees_usd: Decimal,
        taker_fee_bps: int,
        min_notional_usd: Decimal = Decimal("1"),
        max_notional_usd: Decimal | None = None,
        min_profit_usd: Decimal = Decimal("0"),
        min_profit_pct: Decimal = Decimal("0.1"),
    ) -> OptimalBookTrade | None:
        """Walk asks; pick the clip that maximizes net USD profit.

        Direction B: buy token on MEXC asks → sell on DEX for USDT
        (``dex_usdt_per_token`` already includes closing hop / pool fees).
        Keep clips with net% ≥ min_profit_pct (and optional USD floor).
        """
        if dex_usdt_per_token <= 0:
            return None
        asks = sorted(_parse_levels(book.get("asks")), key=lambda x: x[0])
        if not asks:
            return None
        best = asks[0][0]
        taker = Decimal(taker_fee_bps) / Decimal(10000)
        fixed = max(Decimal("0"), fixed_fees_usd)

        best_trade: OptimalBookTrade | None = None
        cum_base = Decimal("0")
        cum_quote = Decimal("0")
        levels = 0

        def _consider(tokens: Decimal, cost: Decimal, lvls: int) -> None:
            nonlocal best_trade
            if cost < min_notional_usd:
                return
            if max_notional_usd is not None and cost > max_notional_usd:
                return
            net_tokens = tokens - fee_token
            if net_tokens <= 0:
                return
            settlement = net_tokens * dex_usdt_per_token
            trading = cost * taker
            net = settlement - cost - fixed - trading
            if cost <= 0:
                return
            pct = (net / cost) * Decimal("100")
            if pct < min_profit_pct:
                return
            if net < min_profit_usd:
                return
            fill = _fill_from_cum("BUY", best, tokens, cost, lvls, True)
            if fill is None:
                return
            trade = OptimalBookTrade(
                fill=fill,
                cost_usd=cost,
                settlement_usd=settlement,
                net_profit_usd=net,
                net_profit_pct=pct,
                tokens_gross=tokens,
                tokens_net=net_tokens,
            )
            if best_trade is None or trade.net_profit_usd > best_trade.net_profit_usd:
                best_trade = trade

        for price, qty in asks:
            # Sample mid-level and full level so thin top-of-book clips
            # (e.g. ~$6 SMARS) are evaluated, not only full-level dumps.
            for frac in (Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1")):
                take = qty * frac
                if take <= 0:
                    continue
                tokens = cum_base + take
                cost = cum_quote + take * price
                _consider(tokens, cost, levels + 1)
            cum_base += qty
            cum_quote += price * qty
            levels += 1
            _consider(cum_base, cum_quote, levels)
            if max_notional_usd is not None and cum_quote >= max_notional_usd * Decimal("2"):
                break

        return best_trade

    def maximize_dir_a_profit(
        self,
        book: dict,
        *,
        full_tokens: Decimal,
        full_cost_usd: Decimal,
        fixed_fees_usd: Decimal,
        taker_fee_bps: int,
        min_notional_usd: Decimal = Decimal("1"),
        min_profit_usd: Decimal = Decimal("0"),
        min_profit_pct: Decimal = Decimal("0.1"),
    ) -> OptimalBookTrade | None:
        """Walk bids; pick the sell clip that maximizes net USD profit.

        Direction A: tokens already quoted from DEX; sell into MEXC bids.
        Scales DEX cost proportionally with tokens sold.
        """
        if full_tokens <= 0 or full_cost_usd <= 0:
            return None
        bids = sorted(_parse_levels(book.get("bids")), key=lambda x: x[0], reverse=True)
        if not bids:
            return None
        best = bids[0][0]
        taker = Decimal(taker_fee_bps) / Decimal(10000)
        fixed = max(Decimal("0"), fixed_fees_usd)

        best_trade: OptimalBookTrade | None = None
        cum_base = Decimal("0")
        cum_quote = Decimal("0")
        levels = 0

        def _consider(tokens_sold: Decimal, proceeds: Decimal, lvls: int) -> None:
            nonlocal best_trade
            if tokens_sold <= 0 or tokens_sold > full_tokens:
                return
            scale = tokens_sold / full_tokens
            cost = full_cost_usd * scale
            if cost < min_notional_usd:
                return
            trading = proceeds * taker
            net = proceeds - cost - fixed - trading
            if cost <= 0:
                return
            pct = (net / cost) * Decimal("100")
            if pct < min_profit_pct:
                return
            if net < min_profit_usd:
                return
            fill = _fill_from_cum("SELL", best, tokens_sold, proceeds, lvls, True)
            if fill is None:
                return
            trade = OptimalBookTrade(
                fill=fill,
                cost_usd=cost,
                settlement_usd=proceeds,
                net_profit_usd=net,
                net_profit_pct=pct,
                tokens_gross=tokens_sold,
                tokens_net=tokens_sold,
            )
            if best_trade is None or trade.net_profit_usd > best_trade.net_profit_usd:
                best_trade = trade

        for price, qty in bids:
            for frac in (Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1")):
                take = qty * frac
                tokens = cum_base + take
                if tokens > full_tokens:
                    rem = full_tokens - cum_base
                    if rem > 0:
                        _consider(full_tokens, cum_quote + rem * price, levels + 1)
                    break
                _consider(tokens, cum_quote + take * price, levels + 1)
            else:
                cum_base += qty
                cum_quote += price * qty
                levels += 1
                if cum_base >= full_tokens:
                    rem = full_tokens - (cum_base - qty)
                    # full fill already considered via frac loop
                    break
                _consider(cum_base, cum_quote, levels)
                continue
            break

        # Always evaluate selling the full DEX inventory.
        full_fill = self.simulate_sell(book, full_tokens)
        if full_fill is not None:
            _consider(full_fill.base_filled, full_fill.quote_filled, full_fill.levels_used)

        return best_trade

    def build_dir_b_size_curve(
        self,
        book: dict,
        *,
        dex_usdt_per_token: Decimal,
        fee_token: Decimal,
        fixed_fees_usd: Decimal,
        taker_fee_bps: int,
        min_notional_usd: Decimal = Decimal("1"),
        max_notional_usd: Decimal | None = None,
        min_profit_usd: Decimal = Decimal("0"),
        reserve_token: Decimal | None = None,
        reserve_stable: Decimal | None = None,
        pool_fee_bps: int = 25,
        max_points: int = 48,
    ) -> list[SizeCurvePoint]:
        """Build size→net curve from an already-fetched book (no RPC).

        Uses the same flat DEX USDT/token rate as maximize_dir_b_profit so the
        slider matches the signal PnL. Optional V2 reserves are only applied
        when CPMM agrees with that quoted rate (otherwise meme-pool reserves
        make every point look like −100% net).
        """
        if dex_usdt_per_token <= 0 and (
            reserve_token is None or reserve_stable is None
        ):
            return []
        asks = sorted(_parse_levels(book.get("asks")), key=lambda x: x[0])
        if not asks:
            return []
        best = asks[0][0]
        taker = Decimal(taker_fee_bps) / Decimal(10000)
        fixed = max(Decimal("0"), fixed_fees_usd)
        use_cpmm = (
            reserve_token is not None
            and reserve_stable is not None
            and reserve_token > 0
            and reserve_stable > 0
            and dex_usdt_per_token > 0
        )
        # Validate CPMM against the quoted flat rate at a small probe size.
        # Bad/swapped reserves otherwise zero-out settlement → fake −100% nets.
        if use_cpmm:
            probe = max(Decimal("1"), fee_token * 2 if fee_token > 0 else Decimal("1"))
            # Prefer a probe near ~$1 of inventory when price is known.
            probe_from_usd = Decimal("1") / dex_usdt_per_token
            if probe_from_usd > 0:
                probe = min(probe_from_usd, reserve_token * Decimal("0.001"))
                probe = max(probe, Decimal("0"))
            if probe <= 0:
                use_cpmm = False
            else:
                cpmm_p = v2_amount_out(
                    probe, reserve_token, reserve_stable, pool_fee_bps
                )
                flat_p = probe * dex_usdt_per_token
                if cpmm_p <= 0 or flat_p <= 0:
                    use_cpmm = False
                else:
                    ratio = cpmm_p / flat_p
                    if ratio < Decimal("0.85") or ratio > Decimal("1.15"):
                        use_cpmm = False

        points_by_size: dict[str, SizeCurvePoint] = {}
        cum_base = Decimal("0")
        cum_quote = Decimal("0")
        levels = 0

        def _settlement(net_tokens: Decimal) -> Decimal:
            if use_cpmm:
                return v2_amount_out(
                    net_tokens, reserve_token, reserve_stable, pool_fee_bps
                )
            return net_tokens * dex_usdt_per_token

        def _consider(tokens: Decimal, cost: Decimal, lvls: int) -> None:
            if cost < min_notional_usd:
                return
            if max_notional_usd is not None and cost > max_notional_usd:
                return
            net_tokens = tokens - fee_token
            if net_tokens <= 0 or cost <= 0:
                return
            settlement = _settlement(net_tokens)
            if settlement <= 0:
                return
            trading = cost * taker
            net = settlement - cost - fixed - trading
            pct = (net / cost) * Decimal("100")
            fill = _fill_from_cum("BUY", best, tokens, cost, lvls, True)
            if fill is None:
                return
            key = f"{cost:.6f}"
            pt = SizeCurvePoint(
                size_usd=cost,
                net_usd=net,
                net_pct=pct,
                vwap=fill.avg_price,
                impact_pct=fill.impact_pct,
                tokens=tokens,
            )
            prev = points_by_size.get(key)
            if prev is None or pt.net_usd > prev.net_usd:
                points_by_size[key] = pt

        for price, qty in asks:
            for frac in (Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1")):
                take = qty * frac
                if take <= 0:
                    continue
                _consider(cum_base + take, cum_quote + take * price, levels + 1)
            cum_base += qty
            cum_quote += price * qty
            levels += 1
            _consider(cum_base, cum_quote, levels)
            if max_notional_usd is not None and cum_quote >= max_notional_usd * Decimal("2"):
                break

        points = sorted(points_by_size.values(), key=lambda p: p.size_usd)
        # Keep only non-decreasing size path; include losing clips so slider
        # shows the full runnable range (UI can highlight profitable zone).
        return _downsample_curve(points, max_points=max_points)

    def build_dir_a_size_curve(
        self,
        book: dict,
        *,
        full_tokens: Decimal,
        full_cost_usd: Decimal,
        fixed_fees_usd: Decimal,
        taker_fee_bps: int,
        min_notional_usd: Decimal = Decimal("1"),
        max_points: int = 48,
    ) -> list[SizeCurvePoint]:
        """Size→net curve for DEX→MEXC using already-fetched bids (no RPC)."""
        if full_tokens <= 0 or full_cost_usd <= 0:
            return []
        bids = sorted(_parse_levels(book.get("bids")), key=lambda x: x[0], reverse=True)
        if not bids:
            return []
        best = bids[0][0]
        taker = Decimal(taker_fee_bps) / Decimal(10000)
        fixed = max(Decimal("0"), fixed_fees_usd)
        points_by_size: dict[str, SizeCurvePoint] = {}
        cum_base = Decimal("0")
        cum_quote = Decimal("0")
        levels = 0

        def _consider(tokens_sold: Decimal, proceeds: Decimal, lvls: int) -> None:
            if tokens_sold <= 0 or tokens_sold > full_tokens:
                return
            scale = tokens_sold / full_tokens
            cost = full_cost_usd * scale
            if cost < min_notional_usd or cost <= 0:
                return
            trading = proceeds * taker
            net = proceeds - cost - fixed - trading
            pct = (net / cost) * Decimal("100")
            fill = _fill_from_cum("SELL", best, tokens_sold, proceeds, lvls, True)
            if fill is None:
                return
            key = f"{cost:.6f}"
            pt = SizeCurvePoint(
                size_usd=cost,
                net_usd=net,
                net_pct=pct,
                vwap=fill.avg_price,
                impact_pct=fill.impact_pct,
                tokens=tokens_sold,
            )
            prev = points_by_size.get(key)
            if prev is None or pt.net_usd > prev.net_usd:
                points_by_size[key] = pt

        for price, qty in bids:
            for frac in (Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1")):
                take = qty * frac
                tokens = cum_base + take
                if tokens > full_tokens:
                    rem = full_tokens - cum_base
                    if rem > 0:
                        _consider(full_tokens, cum_quote + rem * price, levels + 1)
                    break
                _consider(tokens, cum_quote + take * price, levels + 1)
            else:
                cum_base += qty
                cum_quote += price * qty
                levels += 1
                if cum_base >= full_tokens:
                    break
                _consider(cum_base, cum_quote, levels)
                continue
            break

        full_fill = self.simulate_sell(book, full_tokens)
        if full_fill is not None:
            _consider(full_fill.base_filled, full_fill.quote_filled, full_fill.levels_used)

        points = sorted(points_by_size.values(), key=lambda p: p.size_usd)
        return _downsample_curve(points, max_points=max_points)

    async def fill_buy(self, symbol: str, quote_budget: Decimal) -> BookFill | None:
        book = await self.fetch_book(symbol)
        if book is None:
            return None
        return self.simulate_buy(book, quote_budget)

    async def fill_sell(self, symbol: str, base_amount: Decimal) -> BookFill | None:
        book = await self.fetch_book(symbol)
        if book is None:
            return None
        return self.simulate_sell(book, base_amount)

    async def optimal_dir_b(
        self,
        symbol: str,
        **kwargs,
    ) -> OptimalBookTrade | None:
        book = await self.fetch_book(symbol)
        if book is None:
            return None
        return self.maximize_dir_b_profit(book, **kwargs)

    async def optimal_dir_a(
        self,
        symbol: str,
        **kwargs,
    ) -> OptimalBookTrade | None:
        book = await self.fetch_book(symbol)
        if book is None:
            return None
        return self.maximize_dir_a_profit(book, **kwargs)
