#!/usr/bin/env python3
"""
Single-request test for GeckoTerminal API endpoint.

Tests:
1. Search pools by USDC address on eth network
2. Search pools by USDT address on bsc network
3. Rate limit behaviour

Usage:
    python tests/api_tests/test_geckoterminal.py
"""

import sys

import httpx

BASE = "https://api.geckoterminal.com/api/v2"
TIMEOUT = 30

PASS = 0
FAIL = 0

NETWORK_MAP = {
    "ETHEREUM": "eth",
    "BSC": "bsc",
    "POLYGON": "polygon_pos",
    "ARBITRUM": "arbitrum",
}


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] PASS: {name}")
        if detail:
            print(f"     {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] FAIL: {name}")
        if detail:
            print(f"     {detail}")


print("=" * 60)
print("GeckoTerminal API Test")
print("=" * 60)

# ── 1. USDC on Ethereum (eth) ──
print("\n--- 1. USDC on Ethereum (eth) ---")
usdc_eth = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"

with httpx.Client(timeout=TIMEOUT) as client:
    try:
        resp = client.get(f"{BASE}/networks/eth/tokens/{usdc_eth}/pools")
        check("HTTP 200 OK", resp.status_code == 200, f"status={resp.status_code}")

        data = resp.json()
        pools_data = data.get("data", [])
        check("Response has 'data' key", "data" in data)
        check("'data' is list", isinstance(pools_data, list), f"type={type(pools_data).__name__}")
        check("Pools found", len(pools_data) > 0, f"{len(pools_data)} pools")

        if pools_data:
            pool = pools_data[0]
            attrs = pool.get("attributes", {})
            addr = attrs.get("address", "")
            dex = attrs.get("dex_id", "")
            rel = pool.get("relationships", {})

            check("Has pool address", bool(addr), f"pool={addr[:30]}...")
            check("Has dex_id", bool(dex), f"dex={dex}")

            # Extract token addresses from relationships
            try:
                base_rel = rel.get("base_token", {}).get("data", {})
                base_id = str(base_rel.get("id", ""))
                base_addr = base_id.split("_")[-1] if "_" in base_id else base_id
                check("Has base_token address", bool(base_addr), f"base={base_addr[:20]}...")
            except Exception:
                check("Extract base_token", False)

            try:
                quote_rel = rel.get("quote_token", {}).get("data", {})
                quote_id = str(quote_rel.get("id", ""))
                quote_addr = quote_id.split("_")[-1] if "_" in quote_id else quote_id
                check("Has quote_token address", bool(quote_addr), f"quote={quote_addr[:20]}...")
            except Exception:
                check("Extract quote_token", False)

    except httpx.HTTPStatusError as e:
        check(f"GeckoTerminal HTTP {e.response.status_code}", False,
              f"body={e.response.text[:200]}")
    except Exception as e:
        check("GeckoTerminal request failed", False, str(e))

# ── 2. USDT on BSC (bsc) ──
print("\n--- 2. USDT on BSC (bsc) ---")
usdt_bsc = "0x55d398326f99059ff775485246999027b3197955"

with httpx.Client(timeout=TIMEOUT) as client:
    try:
        resp = client.get(f"{BASE}/networks/bsc/tokens/{usdt_bsc}/pools")
        check("HTTP 200 OK", resp.status_code == 200)

        data = resp.json()
        pools_data = data.get("data", [])
        check("Pools found", len(pools_data) > 0, f"{len(pools_data)} pools on BSC")

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            check("GeckoTerminal rate limited (429)", False,
                  "Rate limit hit — expected at high request volume")
        else:
            check(f"GeckoTerminal HTTP {e.response.status_code}", False,
                  f"body={e.response.text[:200]}")
    except Exception as e:
        check("USDT BSC request failed", False, str(e))

# ── 3. Network mapping ──
print("\n--- 3. Network mapping check ---")
with httpx.Client(timeout=TIMEOUT) as client:
    for internal, gecko_name in NETWORK_MAP.items():
        try:
            resp = client.get(
                f"{BASE}/networks/{gecko_name}/tokens/{usdc_eth}/pools",
                timeout=10,
            )
            if resp.status_code == 200:
                check(f"Network '{internal}' → '{gecko_name}'", True, f"status={resp.status_code}")
            elif resp.status_code == 429:
                print(f"     ⚠️  Network '{internal}' rate limited (429)")
            else:
                check(f"Network '{internal}' → '{gecko_name}'", False,
                      f"status={resp.status_code}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                print(f"     ⚠️  Network '{internal}' rate limited (429)")
            else:
                check(f"Network '{internal}' → '{gecko_name}'", False,
                      f"status={e.response.status_code}")
        except Exception as e:
            check(f"Network '{internal}' request failed", False, str(e))

# ── 4. Rate limit diagnosis ──
print("\n--- 4. Rate limit diagnosis ---")
with httpx.Client(timeout=TIMEOUT) as client:
    # Make 5 rapid requests to see rate limit behaviour
    rate_limited = 0
    succeeded = 0
    for i in range(5):
        try:
            resp = client.get(
                f"{BASE}/networks/eth/tokens/{usdc_eth}/pools",
                timeout=10,
            )
            if resp.status_code == 200:
                succeeded += 1
            elif resp.status_code == 429:
                rate_limited += 1
        except Exception:
            rate_limited += 1

    check("Rapid requests not rate limited", rate_limited == 0,
           f"{succeeded} OK, {rate_limited} rate limited (429)")
    if rate_limited > 0:
        print("     ⚠️  GeckoTerminal free tier rate limit is ~1 req/s")
        print("     → Bot should add retry with backoff for 429s")

# ────────────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"Results: {PASS} passed, {FAIL} failed")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
