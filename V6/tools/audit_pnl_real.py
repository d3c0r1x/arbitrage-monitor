"""
Full PnL logic audit on live RPC + MEXC data.

Checks:
1. V2 local amountOut vs router.getAmountsOut (FEG/WBNB + WBNB/USDT)
2. Dir B math: net tokens, closing hop, fee.total() double-count guards
3. Dir A mid formula on a USDT V2 pool
4. Live orderbook maximize vs mid net
5. Fee composition (pool excluded, slippage, $0.50 deposit)

Usage:
  python tools/audit_pnl_real.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FEG_POOL = "0x73abb219d29a"  # prefix match
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
USDT = "0x55d398326f99059ff775485246999027b3197955"


def _pct(a: Decimal, b: Decimal) -> float:
    if b == 0:
        return 0.0
    return float((a - b) / b * 100)


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


async def main() -> None:
    from config.closing_pools import get_closing_pool
    from config.networks import resolve_rpc_urls
    from config.settings import settings
    from clients.http_client import create_http_client
    from clients.mexc_client import MexcClient
    from clients.multicall_client import MulticallClient
    from clients.rpc_client import RoundRobinRpcClient, RpcClient
    from clients.web3_manager import Web3Manager
    from dex.adapter_factory import AdapterFactory
    from models.fee_models import FeeBreakdown
    from services.fee_service import FeeService
    from services.orderbook_service import OrderbookService
    from services.price_service import PriceService
    from services.profit_calculator import ProfitCalculator
    from services.v2_reserves_cache import V2ReservesCache
    from utils.cu_rate_limiter import CuRateLimiter

    _rpc_cache: dict = {}

    def rpc_factory(network: str):
        if network in _rpc_cache:
            return _rpc_cache[network]
        urls = resolve_rpc_urls(network)
        if not urls:
            return None
        clients = [
            RpcClient(
                rpc_url=u,
                rate_limiter=CuRateLimiter(cu_per_sec=400, burst_cu=800),
            )
            for u in urls
        ]
        client = clients[0] if len(clients) == 1 else RoundRobinRpcClient(clients)
        _rpc_cache[network] = client
        return client

    pools = json.loads((ROOT / "data" / "pools_cache.json").read_text(encoding="utf-8"))
    feg = next(
        p
        for p in pools
        if (p.get("token_coin") or "").upper() == "FEG"
        and str(p.get("pool_version") or "").startswith("v2")
        and p.get("network") == "BSC"
    )
    usdt_v2 = next(
        (
            p
            for p in pools
            if p.get("network") == "BSC"
            and str(p.get("pool_version") or "").startswith("v2")
            and (p.get("quote_coin") or "").upper() == "USDT"
            and p.get("token_deposit_enable")
        ),
        None,
    )

    http = await create_http_client()
    mexc = MexcClient(http)
    price_svc = PriceService(mexc)
    await price_svc.refresh_all_prices()

    multicall = MulticallClient(rpc_client_factory=rpc_factory)
    v2_cache = V2ReservesCache(multicall_client=multicall)

    w3m = Web3Manager()
    adapters = AdapterFactory(web3_manager=w3m)
    adapters.set_v2_reserves_cache(v2_cache)

    fee_svc = FeeService(rpc_client_factory=rpc_factory, price_service=price_svc)
    calc = ProfitCalculator(fee_service=fee_svc)
    books = OrderbookService(mexc)

    findings: list[tuple[str, str, str]] = []  # sev, id, msg

    # ── 0. Static fee.total() ────────────────────────────────────────────
    section("0. STATIC: FeeBreakdown.total() excludes pool fee")
    fb = FeeBreakdown(
        dex_network_fee_usd=Decimal("0.30"),
        dex_pool_fee_usd=Decimal("9.99"),  # must NOT enter total
        mexc_trading_fee_usd=Decimal("0.10"),
        mexc_withdraw_fee_usd=Decimal("0.05"),
        slippage_usd=Decimal("0.03"),
        stable_deposit_network_fee_usd=Decimal("0.50"),
    )
    tot = fb.total()
    expected = Decimal("0.30") + Decimal("0.10") + Decimal("0.05") + Decimal("0.03") + Decimal("0.50")
    ok = tot == expected
    print(f"total={tot} expected={expected} pool_in_model={fb.dex_pool_fee_usd} PASS={ok}")
    if not ok:
        findings.append(("CRITICAL", "pool_fee_double", f"total={tot} expected={expected}"))

    print(
        f"settings: MIN_NET={settings.MIN_NET_PROFIT_PCT}% "
        f"TAKER={settings.MEXC_TAKER_FEE_BPS}bps "
        f"SLIPPAGE_BUF={settings.SLIPPAGE_BUFFER_BPS}bps "
        f"HEADROOM={settings.ORDERBOOK_MID_HEADROOM_PCT}% "
        f"BASE=${settings.BASE_AMOUNT_USD}"
    )

    # ── 1. Prefetch + local vs router ────────────────────────────────────
    section("1. LIVE V2: local amountOut vs router (FEG/WBNB + closing)")
    closing = get_closing_pool("BSC", WBNB)
    assert closing is not None
    await v2_cache.prefetch(
        "BSC",
        [feg["pool_address"], closing.pool_address],
    )
    row_feg = v2_cache.get("BSC", feg["pool_address"])
    row_close = v2_cache.get("BSC", closing.pool_address)
    print(f"FEG reserves hit={row_feg is not None} close hit={row_close is not None}")
    if row_feg:
        print(
            f"  token0={row_feg.token0[:10]}… r0={row_feg.reserve0:.0f} r1={row_feg.reserve1:.0f}"
        )

    adapter = adapters.get_adapter("BSC", feg["dex"], pool_version="v2")
    assert adapter is not None

    # Clear cache temporarily to force router path for comparison
    token = feg["token_address"]
    quote = feg["stablecoin_address"]  # WBNB
    td = int(feg["token_decimals"])
    qd = int(feg["stablecoin_decimals"])

    mexc_px = price_svc.get_price("FEG", "USDT")
    wbnb_px = price_svc.get_price("BNB", "USDT") or price_svc.get_price("WBNB", "USDT")
    print(f"MEXC FEG/USDT={mexc_px}  BNB/USDT={wbnb_px}")
    if mexc_px is None or mexc_px <= 0:
        findings.append(("CRITICAL", "no_mexc_feg", "FEG price missing"))
        print("ABORT: no FEG price")
        await http.aclose()
        return

    # Size: ~$50 of FEG (Dir B style)
    base_usd = Decimal("50")
    fee_token = Decimal(str(feg.get("mexc_withdraw_fee") or 0))
    gross_tokens = base_usd / mexc_px
    net_tokens = gross_tokens - fee_token
    print(
        f"DirB size: base=${base_usd} gross_tok={gross_tokens:.4f} "
        f"fee_tok={fee_token} net_tok={net_tokens:.4f} fee_usd={fee_token * mexc_px:.4f}"
    )
    if net_tokens <= 0:
        findings.append(
            (
                "CRITICAL",
                "withdraw_fee_kills_size",
                f"fee_token={fee_token} > gross={gross_tokens} at ${base_usd}",
            )
        )
        # Try larger size so net > 0
        for trial in (Decimal("200"), Decimal("500"), Decimal("1000"), Decimal("2000")):
            gt = trial / mexc_px
            nt = gt - fee_token
            if nt > 0:
                base_usd, gross_tokens, net_tokens = trial, gt, nt
                print(f"  bumped size to ${base_usd} net_tok={net_tokens:.4f}")
                break

    token_raw = int(net_tokens * (Decimal(10) ** td))

    # Local (cached)
    local_wbnb = await adapter.quote_exact_input(
        "BSC", feg["pool_address"], token, quote, token_raw
    )
    # Router: temporarily detach cache
    adapter.set_reserves_cache(None)
    router_wbnb = await adapter.quote_exact_input(
        "BSC", feg["pool_address"], token, quote, token_raw
    )
    adapter.set_reserves_cache(v2_cache)

    print(f"FEG->WBNB raw: local={local_wbnb} router={router_wbnb}")
    if local_wbnb > 0 and router_wbnb > 0:
        drift = abs(_pct(local_wbnb, router_wbnb))
        print(f"  drift={drift:.6f}%")
        if drift > 0.05:
            findings.append(
                ("HIGH", "v2_local_vs_router", f"drift={drift:.4f}% on FEG->WBNB")
            )
        else:
            print("  PASS local~=router (<0.05%)")
    else:
        findings.append(
            ("CRITICAL", "feg_quote_zero", f"local={local_wbnb} router={router_wbnb}")
        )

    # Closing hop WBNB->USDT
    close_in = int(local_wbnb) if local_wbnb > 0 else int(router_wbnb)
    close_ad = adapters.get_adapter("BSC", closing.dex_id, pool_version=closing.pool_version)
    local_usdt = await close_ad.quote_exact_input(
        "BSC",
        closing.pool_address,
        closing.token_in,
        closing.token_out,
        close_in,
    )
    close_ad.set_reserves_cache(None) if hasattr(close_ad, "set_reserves_cache") else None
    # re-attach for factory-shared adapters
    adapters.set_v2_reserves_cache(v2_cache)
    router_usdt = Decimal("0")
    # Force router by clearing and quoting via a fresh adapter without cache
    from dex.v2_adapter import V2Adapter

    if isinstance(close_ad, V2Adapter):
        close_ad.set_reserves_cache(None)
        router_usdt = await close_ad.quote_exact_input(
            "BSC",
            closing.pool_address,
            closing.token_in,
            closing.token_out,
            close_in,
        )
        close_ad.set_reserves_cache(v2_cache)

    print(f"WBNB->USDT raw: local={local_usdt} router={router_usdt}")
    if local_usdt > 0 and router_usdt > 0:
        drift2 = abs(_pct(local_usdt, router_usdt))
        print(f"  drift={drift2:.6f}%")
        if drift2 > 0.05:
            findings.append(("HIGH", "close_local_vs_router", f"drift={drift2:.4f}%"))
        else:
            print("  PASS local~=router")

    usdt_out = local_usdt if local_usdt > 0 else router_usdt
    usdt_human = usdt_out / (Decimal(10) ** closing.token_out_decimals)
    print(f"Settlement USDT={usdt_human:.6f} vs base=${base_usd} gross%={_pct(usdt_human, base_usd):.4f}")

    # ── 2. Dir B mid PnL + double-count probe ────────────────────────────
    section("2. Dir B mid PnL (withdraw already netted in quote)")
    # Correct: withdraw fee USD = 0 in fees.total
    res_ok = await calc.calculate_direction_b(
        network="BSC",
        mexc_price_usd=mexc_px,
        dex_amount_out=usdt_out,
        stablecoin_decimals=closing.token_out_decimals,
        mexc_withdraw_fee_usd=Decimal("0"),
        pool_version="v2",
        base_amount_usd=base_usd,
        quote_price_usd=Decimal("1"),
        swap_hops=2,
        closing_pool_version=closing.pool_version,
        settlement_coin="USDT",
        closing_applied=True,
    )
    # Wrong: also pass withdraw USD into fees (double count)
    res_bad = await calc.calculate_direction_b(
        network="BSC",
        mexc_price_usd=mexc_px,
        dex_amount_out=usdt_out,
        stablecoin_decimals=closing.token_out_decimals,
        mexc_withdraw_fee_usd=fee_token * mexc_px,
        pool_version="v2",
        base_amount_usd=base_usd,
        quote_price_usd=Decimal("1"),
        swap_hops=2,
        closing_pool_version=closing.pool_version,
        settlement_coin="USDT",
        closing_applied=True,
    )
    print(
        f"CORRECT mid: net={res_ok['net_profit_pct']:.4f}% "
        f"${res_ok['net_profit_usd']:.4f} signal={res_ok['signal']}"
    )
    print(
        f"  fees.total={res_ok['fees'].total():.4f} "
        f"gas={res_ok['fees'].dex_network_fee_usd:.4f} "
        f"taker={res_ok['fees'].mexc_trading_fee_usd:.4f} "
        f"slip={res_ok['fees'].slippage_usd:.4f} "
        f"deposit_net={res_ok['fees'].stable_deposit_network_fee_usd:.4f} "
        f"withdraw={res_ok['fees'].mexc_withdraw_fee_usd:.4f} "
        f"pool_info={res_ok['fees'].dex_pool_fee_usd:.4f}"
    )
    print(
        f"WRONG (double withdraw): net={res_bad['net_profit_pct']:.4f}% "
        f"delta={res_ok['net_profit_pct'] - res_bad['net_profit_pct']:.4f}pp"
    )
    if res_ok["net_profit_pct"] <= res_bad["net_profit_pct"]:
        findings.append(("HIGH", "double_count_guard", "wrong path not worse"))

    # Bug probe: quote with gross tokens (fee not subtracted) but fee=0 in fees
    if fee_token > 0 and net_tokens > 0:
        gross_raw = int(gross_tokens * (Decimal(10) ** td))
        adapter.set_reserves_cache(v2_cache)
        wbnb_gross = await adapter.quote_exact_input(
            "BSC", feg["pool_address"], token, quote, gross_raw
        )
        usdt_gross = await close_ad.quote_exact_input(
            "BSC",
            closing.pool_address,
            closing.token_in,
            closing.token_out,
            int(wbnb_gross),
        )
        res_gross = await calc.calculate_direction_b(
            network="BSC",
            mexc_price_usd=mexc_px,
            dex_amount_out=usdt_gross,
            stablecoin_decimals=closing.token_out_decimals,
            mexc_withdraw_fee_usd=Decimal("0"),
            pool_version="v2",
            base_amount_usd=base_usd,
            quote_price_usd=Decimal("1"),
            swap_hops=2,
            closing_pool_version=closing.pool_version,
            closing_applied=True,
        )
        inflate = res_gross["net_profit_pct"] - res_ok["net_profit_pct"]
        print(
            f"If forget subtract withdraw tokens: net={res_gross['net_profit_pct']:.4f}% "
            f"(+{inflate:.4f}pp overstated)"
        )
        if inflate > Decimal("0.05"):
            print("  PASS: scanner must subtract fee_token before quote (guard documented)")

    # Without closing - mark WBNB as $1 (the historical −100% bug)
    wbnb_human = (local_wbnb if local_wbnb > 0 else router_wbnb) / (Decimal(10) ** qd)
    res_bug = await calc.calculate_direction_b(
        network="BSC",
        mexc_price_usd=mexc_px,
        dex_amount_out=local_wbnb if local_wbnb > 0 else router_wbnb,
        stablecoin_decimals=qd,
        mexc_withdraw_fee_usd=Decimal("0"),
        pool_version="v2",
        base_amount_usd=base_usd,
        quote_price_usd=Decimal("1"),  # WRONG: treating WBNB as $1
        swap_hops=1,
        closing_applied=False,
    )
    print(
        f"BUG WBNB@$1: settlement~=${wbnb_human:.4f} net={res_bug['net_profit_pct']:.2f}% "
        f"(should be near -100% if WBNB>>1)"
    )
    if wbnb_px and wbnb_px > Decimal("10") and res_bug["net_profit_pct"] > Decimal("-50"):
        findings.append(
            ("CRITICAL", "wbnb_as_usd_not_caught", f"net={res_bug['net_profit_pct']}")
        )
    else:
        print("  PASS: WBNB@$1 path clearly destroys edge (closing hop required)")

    # ── 3. Live orderbook ────────────────────────────────────────────────
    section("3. LIVE orderbook maximize Dir B (FEGUSDT)")
    book = await books.fetch_book("FEGUSDT")
    if book is None:
        findings.append(("HIGH", "no_book", "FEGUSDT book unavailable"))
        print("FAIL: no book")
    else:
        settlement_usd = usdt_human
        dex_per = settlement_usd / net_tokens if net_tokens > 0 else Decimal("0")
        fixed = (
            res_ok["fees"].dex_network_fee_usd
            + res_ok["fees"].stable_deposit_network_fee_usd
        )
        # Mid fees include slippage; book replaces it
        print(
            f"dex_usdt_per_token={dex_per:.10f} fixed_fees=${fixed:.4f} "
            f"(gas+deposit; slip/taker recomputed)"
        )
        optimal = books.maximize_dir_b_profit(
            book,
            dex_usdt_per_token=dex_per,
            fee_token=fee_token,
            fixed_fees_usd=fixed,
            taker_fee_bps=int(settings.MEXC_TAKER_FEE_BPS),
            min_notional_usd=Decimal("1"),
            max_notional_usd=max(base_usd, Decimal("50")),
            min_profit_usd=settings.MIN_NET_PROFIT_USD,
            min_profit_pct=settings.MIN_NET_PROFIT_PCT,
        )
        if optimal is None:
            print("No profitable book clip at MIN_NET - trying min_pct=0 for diagnostic")
            opt0 = books.maximize_dir_b_profit(
                book,
                dex_usdt_per_token=dex_per,
                fee_token=fee_token,
                fixed_fees_usd=fixed,
                taker_fee_bps=int(settings.MEXC_TAKER_FEE_BPS),
                min_notional_usd=Decimal("1"),
                max_notional_usd=max(base_usd, Decimal("500")),
                min_profit_usd=Decimal("0"),
                min_profit_pct=Decimal("0"),
            )
            if opt0:
                print(
                    f"  best clip anyway: size=${opt0.cost_usd:.2f} "
                    f"net={opt0.net_profit_pct:.4f}% ${opt0.net_profit_usd:.4f} "
                    f"vwap={opt0.fill.avg_price} impact={opt0.fill.impact_pct}%"
                )
                # Recompute by hand
                hand = (
                    opt0.settlement_usd
                    - opt0.cost_usd
                    - fixed
                    - opt0.cost_usd * Decimal(settings.MEXC_TAKER_FEE_BPS) / Decimal(10000)
                )
                print(f"  hand_net=${hand:.6f} vs opt.net=${opt0.net_profit_usd:.6f}")
                if abs(hand - opt0.net_profit_usd) > Decimal("0.0001"):
                    findings.append(
                        ("CRITICAL", "book_net_mismatch", f"hand={hand} opt={opt0.net_profit_usd}")
                    )
                else:
                    print("  PASS: maximize formula == hand recalc")
            else:
                print("  empty/unusable book for any clip")
                findings.append(("MED", "book_no_clip", "no clip even at 0%"))
        else:
            print(
                f"PASS hard clip: size=${optimal.cost_usd:.2f} "
                f"net={optimal.net_profit_pct:.4f}% ${optimal.net_profit_usd:.4f} "
                f"vwap={optimal.fills.avg_price} impact={optimal.fills.impact_pct}%"
            )
            hand = (
                optimal.settlement_usd
                - optimal.cost_usd
                - fixed
                - optimal.cost_usd
                * Decimal(settings.MEXC_TAKER_FEE_BPS)
                / Decimal(10000)
            )
            if abs(hand - optimal.net_profit_usd) > Decimal("0.0001"):
                findings.append(
                    ("CRITICAL", "book_net_mismatch", f"hand={hand} opt={optimal.net_profit_usd}")
                )
            else:
                print("  PASS: maximize == hand")

            # Headroom gate simulation
            mid = res_ok["net_profit_pct"]
            need = settings.MIN_NET_PROFIT_PCT + settings.ORDERBOOK_MID_HEADROOM_PCT
            print(f"Headroom gate: mid={mid:.4f}% need>={need:.4f}% -> book_fetch={mid >= need}")

    # ── 4. Dir A on USDT V2 if available ─────────────────────────────────
    section("4. Dir A mid on BSC USDT V2 pool (if any)")
    if usdt_v2 is None:
        print("SKIP: no BSC USDT V2 in cache")
    else:
        print(
            f"pool {usdt_v2['token_coin']}/USDT {usdt_v2['pool_address'][:12]}… "
            f"dex={usdt_v2['dex']}"
        )
        await v2_cache.prefetch("BSC", [usdt_v2["pool_address"]])
        adapters.set_v2_reserves_cache(v2_cache)
        ad_a = adapters.get_adapter("BSC", usdt_v2["dex"], pool_version="v2")
        coin = usdt_v2["token_coin"]
        px = price_svc.get_price(coin, "USDT")
        print(f"MEXC {coin}/USDT={px}")
        if px and px > 0 and ad_a:
            base_a = Decimal("50")
            raw_in = int(base_a * (Decimal(10) ** int(usdt_v2["stablecoin_decimals"])))
            out_local = await ad_a.quote_exact_input(
                "BSC",
                usdt_v2["pool_address"],
                usdt_v2["stablecoin_address"],
                usdt_v2["token_address"],
                raw_in,
            )
            ad_a.set_reserves_cache(None)
            out_router = await ad_a.quote_exact_input(
                "BSC",
                usdt_v2["pool_address"],
                usdt_v2["stablecoin_address"],
                usdt_v2["token_address"],
                raw_in,
            )
            ad_a.set_reserves_cache(v2_cache)
            print(f"USDT->{coin}: local={out_local} router={out_router}")
            if out_local > 0 and out_router > 0:
                d = abs(_pct(out_local, out_router))
                print(f"  drift={d:.6f}%")
                if d > 0.05:
                    findings.append(("HIGH", "dir_a_v2_drift", f"{coin} drift={d:.4f}%"))
            wd_stable = Decimal("0")
            if usdt_v2.get("stablecoin_withdraw_fee") is not None:
                wd_stable = Decimal(str(usdt_v2["stablecoin_withdraw_fee"]))
            res_a = await calc.calculate_direction_a(
                network="BSC",
                token_coin=coin,
                mexc_price_usd=px,
                dex_amount_out=out_local if out_local > 0 else out_router,
                token_decimals=int(usdt_v2["token_decimals"]),
                stablecoin_withdraw_fee_usd=wd_stable,
                pool_version="v2",
                base_amount_usd=base_a,
            )
            print(
                f"DirA mid: gross={res_a['gross_profit_pct']:.4f}% "
                f"net={res_a['net_profit_pct']:.4f}% signal={res_a['signal']} "
                f"fees.total={res_a['fees'].total():.4f}"
            )
            # Manual net
            tok = (out_local if out_local > 0 else out_router) / (
                Decimal(10) ** int(usdt_v2["token_decimals"])
            )
            gross_val = tok * px
            hand_net = gross_val - base_a - res_a["fees"].total()
            print(f"  hand_net=${hand_net:.6f} vs calc=${res_a['net_profit_usd']:.6f}")
            if abs(hand_net - res_a["net_profit_usd"]) > Decimal("0.0001"):
                findings.append(
                    (
                        "CRITICAL",
                        "dir_a_formula",
                        f"hand={hand_net} calc={res_a['net_profit_usd']}",
                    )
                )
            else:
                print("  PASS Dir A formula")

            book_a = await books.fetch_book(f"{coin}USDT")
            if book_a and out_local > 0:
                full_tok = out_local / (Decimal(10) ** int(usdt_v2["token_decimals"]))
                fixed_a = (
                    res_a["fees"].dex_network_fee_usd
                    + res_a["fees"].mexc_withdraw_fee_usd
                )
                opt_a = books.maximize_dir_a_profit(
                    book_a,
                    full_tokens=full_tok,
                    full_cost_usd=base_a,
                    fixed_fees_usd=fixed_a,
                    taker_fee_bps=int(settings.MEXC_TAKER_FEE_BPS),
                    min_notional_usd=Decimal("1"),
                    min_profit_usd=Decimal("0"),
                    min_profit_pct=Decimal("0"),
                )
                if opt_a:
                    print(
                        f"  book best: size=${opt_a.cost_usd:.2f} "
                        f"net={opt_a.net_profit_pct:.4f}% impact={opt_a.fills.impact_pct}%"
                    )

    # ── 5. Fee composition realism ───────────────────────────────────────
    section("5. Fee drag at common sizes (BSC v2, 2 hops)")
    for sz in (Decimal("10"), Decimal("50"), Decimal("100"), Decimal("500")):
        fees = await fee_svc.calculate_fees_direction_b(
            network="BSC",
            base_amount_usd=sz,
            mexc_withdraw_fee_usd=Decimal("0"),
            mexc_deposit_fee_usd=Decimal("0"),
            stable_deposit_network_fee_usd=Decimal("0.5"),
            pool_version="v2",
            swap_hops=2,
            closing_pool_version="v2",
        )
        drag = (fees.total() / sz) * Decimal("100") if sz else Decimal("0")
        print(
            f"  ${sz}: total=${fees.total():.4f} ({drag:.3f}%) "
            f"gas={fees.dex_network_fee_usd:.4f} "
            f"deposit={fees.stable_deposit_network_fee_usd:.4f} "
            f"taker={fees.mexc_trading_fee_usd:.4f} "
            f"slip={fees.slippage_usd:.4f}"
        )
    if Decimal("0.5") / Decimal("10") * 100 >= Decimal("5"):
        findings.append(
            (
                "MED",
                "flat_deposit_fee",
                "stable_deposit_network_fee_usd=$0.50 is 5% of $10 base - "
                "kills thin mid edges; book path keeps it in fixed_fees too",
            )
        )

    # ── Summary ──────────────────────────────────────────────────────────
    section("SUMMARY")
    crit = [f for f in findings if f[0] == "CRITICAL"]
    high = [f for f in findings if f[0] == "HIGH"]
    med = [f for f in findings if f[0] == "MED"]
    print(f"findings: CRITICAL={len(crit)} HIGH={len(high)} MED={len(med)}")
    for sev, fid, msg in findings:
        print(f"  [{sev}] {fid}: {msg}")
    if not crit and not high:
        print("VERDICT: core math OK on live FEG path (local~=router, fee guards hold)")
    elif crit:
        print("VERDICT: CRITICAL math issues - see above")
    else:
        print("VERDICT: no CRITICAL; HIGH items need attention")

    out = ROOT / "data" / "audit_pnl_real.json"
    payload = {
        "feg_pool": feg["pool_address"],
        "mexc_feg": str(mexc_px),
        "base_usd": str(base_usd),
        "fee_token": str(fee_token),
        "mid_net_pct": str(res_ok["net_profit_pct"]),
        "mid_fees_total": str(res_ok["fees"].total()),
        "findings": [{"sev": a, "id": b, "msg": c} for a, b, c in findings],
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {out}")

    await http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
