"""Signal sanity filters (plan v3 D5/D6/D14/D17).

Two stages:
- pre_quote_filter: cheap checks before any RPC quote is made.
- post_signal_filter: direction-aware checks after profit calculation,
  right before the signal is written.

Direction semantics (D6 clarification):
- Direction A (DEX_BUY_MEXC_SELL): token must be DEPOSITABLE on MEXC.
- Direction B (MEXC_BUY_DEX_SELL): token must be WITHDRAWABLE on MEXC.
A pool with deposit=False but withdraw=True is still valid for B.
"""

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

# Net profit above this is a data error (wrong decimals, fake pool,
# stale price), not an arbitrage. Real MEXC×DEX edges are single digits.
MAX_NET_PROFIT_PCT = Decimal("50")

# Withdraw fee eating more than this fraction of the base makes the
# trade uneconomical regardless of the quoted edge.
MAX_WITHDRAW_FEE_RATIO = Decimal("0.1")

# Deposits needing more confirmations than this take too long to credit
# for the current watcher/execution horizon.
MAX_MIN_CONFIRM = 2000

# Warnings that invalidate the quote itself.
_FATAL_WARNING_SUBSTRINGS = (
    "thin_liquidity_quote_unreliable",
)


def pre_quote_filter(pool: dict) -> tuple[bool, str]:
    """Cheap pool-level checks before spending RPC on quotes."""
    if not pool.get("pool_version"):
        return False, "unknown_pool_version"

    token_dec = pool.get("token_decimals")
    stable_dec = pool.get("stablecoin_decimals")
    if token_dec is None or stable_dec is None:
        return False, "decimals_missing"

    return True, "ok"


def post_signal_filter(
    *,
    net_profit_pct: Decimal,
    direction: str,
    token_deposit_enable: bool,
    token_withdraw_enable: bool,
    base_amount_usd: Decimal,
    mexc_withdraw_fee_usd: Decimal | None = None,
    token_min_confirm: int | None = None,
    warnings: list[str] | None = None,
    net_profit_usd: Decimal | None = None,
    min_net_profit_usd: Decimal | None = None,
) -> tuple[bool, str]:
    """Direction-aware sanity checks after profit calculation.

    Returns (ok, reason). reason is machine-greppable for log analysis.
    """
    if net_profit_pct > MAX_NET_PROFIT_PCT:
        return False, f"unrealistic_profit:{net_profit_pct:.1f}%"

    # Absolute USD floor is optional (default 0). Primary gate is net %.
    if min_net_profit_usd is not None:
        try:
            floor = Decimal(str(min_net_profit_usd))
        except Exception:
            floor = Decimal("0")
        if floor > 0 and net_profit_usd is not None:
            try:
                nu = Decimal(str(net_profit_usd))
            except Exception:
                nu = None
            if nu is not None and nu < floor:
                return False, f"below_min_profit_usd:{nu:.4f}"

    if direction == "DEX_BUY_MEXC_SELL" and not token_deposit_enable:
        return False, "mexc_deposit_closed"

    if direction == "MEXC_BUY_DEX_SELL" and not token_withdraw_enable:
        return False, "mexc_withdraw_closed"

    if (
        mexc_withdraw_fee_usd is not None
        and base_amount_usd > 0
        and mexc_withdraw_fee_usd / base_amount_usd > MAX_WITHDRAW_FEE_RATIO
    ):
        return False, f"high_withdraw_fee:${mexc_withdraw_fee_usd:.2f}"

    if (
        direction == "DEX_BUY_MEXC_SELL"
        and token_min_confirm is not None
        and token_min_confirm > MAX_MIN_CONFIRM
    ):
        # minConfirm is deposit ETA on MEXC — only gates Direction A.
        return False, f"high_min_confirm:{token_min_confirm}"

    for w in warnings or []:
        for fatal in _FATAL_WARNING_SUBSTRINGS:
            if fatal in w:
                return False, f"fatal_warning:{w}"

    return True, "ok"
