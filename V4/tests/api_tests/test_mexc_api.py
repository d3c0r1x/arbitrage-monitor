#!/usr/bin/env python3
"""
Single-request test for MEXC API endpoints.

Tests:
1. GET /api/v3/ticker/price — public, no auth required
2. GET /api/v3/capital/config/getall — signed, requires API key

Usage:
    python tests/api_tests/test_mexc_api.py
"""

import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv

# Load .env from project root
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
load_dotenv(os.path.join(project_root, ".env"))

MEXC_BASE = "https://api.mexc.com"
API_KEY = os.environ.get("MEXC_API_KEY", "")
API_SECRET = os.environ.get("MEXC_API_SECRET", "")
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


# ────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("MEXC API Endpoint Tests")
print("=" * 60)

# ────────────────────────────────────────────────────────────────────────
# 1. Public: GET /api/v3/ticker/price
# ────────────────────────────────────────────────────────────────────────
print("\n--- 1. Ticker Price (public) ---")
with httpx.Client(timeout=TIMEOUT) as client:
    try:
        resp = client.get(f"{MEXC_BASE}/api/v3/ticker/price")
        resp.raise_for_status()
        data = resp.json()
        symbols = [s["symbol"] for s in data if isinstance(s, dict)]
        usdt_pairs = [s for s in symbols if s.endswith("USDT")]
        usdc_pairs = [s for s in symbols if s.endswith("USDC")]

        check("HTTP 200 OK", resp.status_code == 200, f"status={resp.status_code}")
        check("Response is list", isinstance(data, list), f"type={type(data).__name__}")
        check("Has USDT pairs", len(usdt_pairs) > 0, f"{len(usdt_pairs)} USDT pairs found")
        check("Has USDC pairs", len(usdc_pairs) > 0, f"{len(usdc_pairs)} USDC pairs found")
        check("Has USDT price", any(s == "USDTUSDT" for s in symbols) or len(usdt_pairs) > 0,
              f"First USDT: {usdt_pairs[0] if usdt_pairs else 'none'}")

        # Check BTCUSDT specifically
        btc = [s for s in data if isinstance(s, dict) and s.get("symbol") == "BTCUSDT"]
        if btc:
            check("BTCUSDT price exists", True, f"BTCUSDT = ${btc[0].get('price', '?')}")
        else:
            check("BTCUSDT price exists", False, "Not found in response")

    except Exception as e:
        check("Ticker price request failed", False, str(e))

# ────────────────────────────────────────────────────────────────────────
# 2. Signed: GET /api/v3/capital/config/getall
# ────────────────────────────────────────────────────────────────────────
print("\n--- 2. Capital Config (signed) ---")

if not API_KEY or not API_SECRET:
    check("MEXC_API_KEY set", False, "Missing API key — skip signed endpoint test")
    check("MEXC_API_SECRET set", False, "Missing API secret — skip signed endpoint test")
else:
    with httpx.Client(timeout=TIMEOUT) as client:
        try:
            # Build signed request
            params = {"timestamp": str(int(time.time() * 1000))}
            query = urlencode(sorted(params.items()))
            signature = hmac.new(
                API_SECRET.encode("utf-8"),
                query.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            params["signature"] = signature

            headers = {"X-MEXC-APIKEY": API_KEY}
            resp = client.get(
                f"{MEXC_BASE}/api/v3/capital/config/getall",
                params=params,
                headers=headers,
            )

            check("HTTP 200 OK", resp.status_code == 200, f"status={resp.status_code}")
            data = resp.json()
            check("Response is list", isinstance(data, list), f"type={type(data).__name__}")
            check("Has coin entries", len(data) > 0, f"{len(data)} coins returned")

            # Check for USDT
            usdt_entry = [c for c in data if c.get("coin") == "USDT"]
            check("USDT entry exists", len(usdt_entry) > 0,
                  f"USDT: {json.dumps(usdt_entry[0] if usdt_entry else {}, indent=2)[:300]}")

            if usdt_entry:
                networks = usdt_entry[0].get("networkList", [])
                check("USDT has networks", len(networks) > 0, f"{len(networks)} networks")
                eth_net = [n for n in networks if "ETH" in n.get("network", "").upper() or "ERC20" in n.get("network", "").upper()]
                check("USDT on Ethereum", len(eth_net) > 0)
                if eth_net:
                    e = eth_net[0]
                    contract = e.get("contract", "")
                    check("USDT has contract address", bool(contract),
                          f"contract={contract[:30]}..." if contract else "empty")
                    check("depositEnable exists", "depositEnable" in e, f"{e.get('depositEnable')}")
                    check("withdrawEnable exists", "withdrawEnable" in e, f"{e.get('withdrawEnable')}")

            # Check for USDC
            usdc_entry = [c for c in data if c.get("coin") == "USDC"]
            check("USDC entry exists", len(usdc_entry) > 0)

        except httpx.HTTPStatusError as e:
            check(f"Capital config HTTP {e.response.status_code}", False,
                  f"body={e.response.text[:200]}")
        except Exception as e:
            check("Capital config request failed", False, str(e))

# ────────────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"Results: {PASS} passed, {FAIL} failed")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
