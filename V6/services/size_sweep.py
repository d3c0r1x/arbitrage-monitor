"""On-chain size ladder helpers for executable arb sizing."""

from __future__ import annotations

from decimal import Decimal

from config.settings import settings


def size_sweep_ladder_usd() -> list[Decimal]:
    """Return configured USD sizes (ascending, unique, positive)."""
    raw = str(getattr(settings, "SIZE_SWEEP_USD", "") or "").strip()
    if not raw:
        raw = "10,25,50,100,250,500"
    out: list[Decimal] = []
    seen: set[str] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            val = Decimal(part)
        except Exception:
            continue
        if val <= 0:
            continue
        key = format(val, "f")
        if key in seen:
            continue
        seen.add(key)
        out.append(val)
    out.sort()
    cap = getattr(settings, "SIZE_SWEEP_MAX_USD", None)
    if cap is not None and Decimal(str(cap)) > 0:
        out = [v for v in out if v <= Decimal(str(cap))]
    return out or [Decimal("50")]


def pick_optimal_by_net_usd(candidates: list[dict]) -> dict | None:
    """Among viable candidates, pick max net_profit_usd."""
    viable = [
        c for c in candidates
        if c.get("signal") and (c.get("net_profit_usd") or 0) > 0
    ]
    if not viable:
        return None
    return max(viable, key=lambda c: Decimal(str(c["net_profit_usd"])))


def attach_size_bounds(best: dict, curve: list[dict]) -> dict:
    """Add size_min/max/optimal + curve from viable points."""
    viable = [c for c in curve if c.get("signal")]
    if not viable:
        best = dict(best)
        best["size_curve"] = curve
        return best
    sizes = [Decimal(str(c["base_amount_usd"])) for c in viable]
    best = dict(best)
    best["size_min_usd"] = min(sizes)
    best["size_max_usd"] = max(sizes)
    best["size_optimal_usd"] = Decimal(str(best["base_amount_usd"]))
    best["size_curve"] = [
        {
            "size_usd": str(c["base_amount_usd"]),
            "net_pct": float(c.get("net_profit_pct") or 0),
            "net_usd": float(c.get("net_profit_usd") or 0),
            "gross_pct": float(c.get("gross_profit_pct") or 0),
            "signal": bool(c.get("signal")),
        }
        for c in curve
    ]
    return best


def is_near_miss(net_pct: Decimal, min_pct: Decimal, floor_pct: Decimal) -> bool:
    return floor_pct <= net_pct < min_pct
