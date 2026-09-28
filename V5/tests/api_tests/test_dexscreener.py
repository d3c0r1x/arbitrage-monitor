#!/usr/bin/env python3
"""
Single-request test for DexScreener API endpoint.

Tests:
1. Search pools by USDC address on Ethereum
2. Search pools by USDT address on BSC
3. Search by unknown token (should return null pairs gracefully)

Usage:
    python tests/api_tests/test_dexscreener.py
"""

import sys

import httpx

BASE = "https://api.dexscreener.com/latest/dex"
TIMEOUT = 30

PASS = 0
FAIL = 0


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
print("DexScreener API Test")
print("=" * 60)

# ── 1. USDC on Ethereum ──
print("\n--- 1. USDC on Ethereum ---")
# USDC Ethereum: 0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48
usdc_eth = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"

with httpx.Client(timeout=TIMEOUT) as client:
    try:
        resp = client.get(f"{BASE}/tokens/{usdc_eth}")
        resp.raise_for_status()
        check("HTTP 200 OK", resp.status_code == 200)

        data = resp.json()
        pairs = data.get("pairs")
        check("'pairs' field present", pairs is not None)
        check("'pairs' field is list", isinstance(pairs, list), f"type={type(pairs).__name__}")

        if pairs and len(pairs) > 0:
            p = pairs[0]
            chain = p.get("chainId", "")
            dex = p.get("dexId", "")
            pair_addr = p.get("pairAddress", "")
            base = p.get("baseToken", {})
            quote = p.get("quoteToken", {})

            check("Has chainId", bool(chain), f"chain={chain}")
            check("Has dexId", bool(dex), f"dex={dex}")
            check("Has pairAddress", bool(pair_addr), f"pair={pair_addr[:30]}...")
            check("Has baseToken address", bool(base.get("address")), f"base={base.get('address','')[:20]}...")
            check("Has quoteToken address", bool(quote.get("address")), f"quote={quote.get('address','')[:20]}...")

            # Verify base token is USDC
            is_usdc = str(base.get("address", "")).lower() == usdc_eth.lower()
            check("baseToken is USDC", is_usdc, f"Found {len(pairs)} pools on {chain} via {dex}")

            # Check that price/liquidity fields are present but we ignore them
            price = p.get("priceUsd")
            liq = p.get("liquidity", {})
            if price is not None:
                check("priceUsd present (ignored)", True, f"price={price}")
            if liq:
                check("liquidity present (ignored)", True, f"liquidity_usd={liq.get('usd', '?')}")
        else:
            check("No pairs returned", False, "DexScreener returned empty pairs list for USDC")

    except httpx.HTTPStatusError as e:
        check(f"DexScreener HTTP {e.response.status_code}", False, f"body={e.response.text[:200]}")
    except Exception as e:
        check("DexScreener request failed", False, str(e))

# ── 2. USDT on BSC ──
print("\n--- 2. USDT on BSC ---")
# USDT BSC: 0x55d398326f99059ff775485246999027b3197955
usdt_bsc = "0x55d398326f99059ff775485246999027b3197955"

with httpx.Client(timeout=TIMEOUT) as client:
    try:
        resp = client.get(f"{BASE}/tokens/{usdt_bsc}")
        resp.raise_for_status()
        check("HTTP 200 OK", resp.status_code == 200)

        data = resp.json()
        pairs = data.get("pairs")
        check("'pairs' field present", pairs is not None)

        if pairs and len(pairs) > 0:
            bsc_pools = [p for p in pairs if str(p.get("chainId", "")).lower() == "bsc"]
            check("Has BSC pools", len(bsc_pools) > 0, f"{len(bsc_pools)} BSC pools")
            check("Total pools returned", len(pairs) > 0, f"{len(pairs)} pools")
        else:
            check("Pairs returned (expected >0)", False)

    except Exception as e:
        check("USDT BSC request failed", False, str(e))

# ── 3. Unknown token (should handle null pairs gracefully) ──
print("\n--- 3. Unknown token (null pairs handling) ---")
unknown = "0x0000000000000000000000000000000000000001"

with httpx.Client(timeout=TIMEOUT) as client:
    try:
        resp = client.get(f"{BASE}/tokens/{unknown}")
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs")

        check("HTTP 200 OK", resp.status_code == 200)
        # This is the critical test: DexScreener returns null, not []
        check("'pairs' is None or []", pairs is None or pairs == [],
              f"pairs type={type(pairs).__name__} value={pairs}")

        if pairs is None:
            print("     ⚠️  DexScreener returns null for unknown tokens")
            print("     → Code must handle: payload.get('pairs') or []")
        elif pairs == []:
            print("     [PASS] DexScreener returns empty array for unknown tokens")

    except Exception as e:
        check("Unknown token request failed", False, str(e))

# ── 4. Network mapping check ──
print("\n--- 4. Network mapping ---")
with httpx.Client(timeout=TIMEOUT) as client:
    try:
        resp = client.get(f"{BASE}/tokens/{usdc_eth}")
        data = resp.json()
        pairs = data.get("pairs") or []

        chain_ids = set()
        for p in pairs:
            chain_ids.add(str(p.get("chainId", "")).lower())

        mapping = {
            "ethereum": "ETHEREUM",
            "bsc": "BSC",
            "polygon": "POLYGON",
            "arbitrum": "ARBITRUM",
        }
        for chain_id, internal in mapping.items():
            if chain_id in chain_ids:
                check(f"Chain '{chain_id}' → {internal}", True)
            else:
                print(f"     ℹ️  Chain '{chain_id}' not found in USDC pairs (may not exist)")

    except Exception as e:
        check("Network mapping check failed", False, str(e))

# ────────────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"Results: {PASS} passed, {FAIL} failed")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
