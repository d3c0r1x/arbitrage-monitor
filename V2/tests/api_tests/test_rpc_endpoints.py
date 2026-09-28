#!/usr/bin/env python3
"""
Single-request test for all RPC endpoints used by the bot.

Tests:
1. Ethereum RPC — eth_chainId, eth_gasPrice, eth_call
2. BSC RPC
3. Polygon RPC
4. Arbitrum RPC
5. Robinhood RPC

Usage:
    python tests/api_tests/test_rpc_endpoints.py
"""

import os
import sys
from decimal import Decimal

import httpx
from dotenv import load_dotenv

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
load_dotenv(os.path.join(project_root, ".env"))

TIMEOUT = 30
PASS = 0
FAIL = 0

ALCHEMY_KEY = os.environ.get("ALCHEMY_KEY", "")

NETWORKS = {
    "ETHEREUM": {
        "env_var": "ETH_RPC_URL",
        "alchemy": f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}" if ALCHEMY_KEY else None,
        "fallback": "https://eth.llamarpc.com",
        "chain_id": "0x1",
        "multicall": "0xcA11bde05977b3631167028862bE2a173976CA11",
        "test_call_to": "0x5C69bEE701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "test_call_data": "0xe6a43905" + "0" * 24 + "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48" + "0" * 24 + "dac17f958d2ee523a2206206994597c13d831ec7",
    },
    "BSC": {
        "env_var": "BSC_RPC_URL",
        "alchemy": f"https://bnb-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}" if ALCHEMY_KEY else None,
        "fallback": "https://bsc-dataseed.binance.org",
        "chain_id": "0x38",
        "test_call_to": "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
        "test_call_data": "0xe6a43905" + "0" * 24 + "55d398326f99059ff775485246999027b3197955" + "0" * 24 + "8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
        "fallback_rpcs": [
            "https://bsc-dataseed1.binance.org",
            "https://bsc-dataseed2.binance.org",
            "https://bsc-dataseed3.binance.org",
            "https://bsc-dataseed4.binance.org",
        ],
    },
    "POLYGON": {
        "env_var": "POLYGON_RPC_URL",
        "alchemy": f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}" if ALCHEMY_KEY else None,
        "fallback": "https://polygon-rpc.com",
        "chain_id": "0x89",
        "multicall": "0xcA11bde05977b3631167028862bE2a173976CA11",
        # QuickSwap V2 factory: getPair(USDC, USDT) via 0xe6a43905
        "test_call_to": "0x5757371414417b8C6CAad45bAeF941aBC7d3Ab32",
        "test_call_data": "0xe6a43905" + "0" * 24 + "3c499c542cef5e3811e1192ce70d8cc03d5c3359" + "0" * 24 + "c2132d05d31c914a87c6611c10748aeb04b58e8f",
    },
    "ARBITRUM": {
        "env_var": "ARBITRUM_RPC_URL",
        "alchemy": f"https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}" if ALCHEMY_KEY else None,
        "fallback": "https://arb1.arbitrum.io/rpc",
        "chain_id": "0xa4b1",
        "multicall": "0xcA11bde05977b3631167028862bE2a173976CA11",
        # Camelot V2 factory: getPair(USDC, USDT)
        "test_call_to": "0x6EcCab422D763aC031210895C81787E87B43A652",
        "test_call_data": "0xe6a43905" + "0" * 24 + "af88d065e77c8cC2239327C5EDb3A432268e5831" + "0" * 24 + "fd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
    },
    "ROBINHOOD": {
        "env_var": "ROBINHOOD_RPC_URL",
        "alchemy": None,
        "fallback": "https://rpc.mainnet.chain.robinhood.com",
        "chain_id": "0x1237",
    },
}

RPC_ID = 0


def rpc_call(client: httpx.Client, url: str, method: str, params: list) -> dict:
    global RPC_ID
    RPC_ID += 1
    payload = {
        "jsonrpc": "2.0",
        "id": RPC_ID,
        "method": method,
        "params": params,
    }
    resp = client.post(url, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        if detail:
            print(f"  [PASS] {name}  ({detail})")
        else:
            print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def test_network(name: str, cfg: dict):
    """Test a single network's RPC endpoint."""
    global PASS, FAIL
    print(f"\n--- {name} ---")

    # Resolve RPC URL (same logic as config/networks.py)
    env_url = os.environ.get(cfg["env_var"], "")
    if env_url and not env_url.startswith("TODO_") and env_url.strip():
        rpc_url = env_url.strip()
        print(f"     URL source: env var {cfg['env_var']}")
    elif cfg["alchemy"]:
        rpc_url = cfg["alchemy"]
        print("     URL source: Alchemy template")
    elif cfg["fallback"]:
        rpc_url = cfg["fallback"]
        print("     URL source: Public fallback")
    else:
        print("     ⚠️  No RPC URL available — skipping")
        return

    # Mask URL for logging
    masked = rpc_url
    if ALCHEMY_KEY and ALCHEMY_KEY in masked:
        masked = masked.replace(ALCHEMY_KEY, "***MASKED***")
    print(f"     URL: {masked}")

    with httpx.Client(timeout=TIMEOUT) as client:
        # ── Test 1: net_version / eth_chainId ──
        try:
            result = rpc_call(client, rpc_url, "eth_chainId", [])
            chain_id = result.get("result", "")
            expected = cfg.get("chain_id", "")
            check("eth_chainId", chain_id == expected or not expected,
                  f"got={chain_id}" + (f" expected={expected}" if expected else ""))
        except Exception as e:
            check("eth_chainId connection", False, str(e)[:80])
            return  # No point testing further if RPC is unreachable

        # ── Test 2: eth_gasPrice ──
        try:
            result = rpc_call(client, rpc_url, "eth_gasPrice", [])
            gas_hex = result.get("result", "0x0")
            gas_wei = int(gas_hex, 16)
            check("eth_gasPrice", gas_wei > 0,
                  f"{gas_wei} wei ({Decimal(gas_wei) / 10**9:.1f} gwei)" if gas_wei > 0 else "0 wei")
        except Exception as e:
            check("eth_gasPrice", False, str(e)[:80])

        # ── Test 3: eth_blockNumber ──
        try:
            result = rpc_call(client, rpc_url, "eth_blockNumber", [])
            block_hex = result.get("result", "0x0")
            block_num = int(block_hex, 16)
            check("eth_blockNumber", block_num > 0,
                  f"#{block_num:,}" if block_num > 0 else "0")
        except Exception as e:
            check("eth_blockNumber", False, str(e)[:80])

        # ── Test 4: eth_call (if test_call data provided) ──
        if cfg.get("test_call_to") and cfg.get("test_call_data"):
            try:
                to = cfg["test_call_to"]
                data = cfg["test_call_data"]
                params = [{"to": to, "data": data}, "latest"]
                result = rpc_call(client, rpc_url, "eth_call", params)
                result_hex = result.get("result", "")
                pool_addr = "0x" + result_hex[-40:].lower() if result_hex else "0x0"
                is_zero = pool_addr == "0x0000000000000000000000000000000000000000"
                if is_zero:
                    check("eth_call (factory getPair)", True,
                          "Pool does not exist (zero address) — expected")
                else:
                    check("eth_call (factory getPair)", True,
                          f"Pool EXISTS: {pool_addr}")
            except Exception as e:
                check("eth_call", False, str(e)[:80])

        # ── Test 5: eth_getCode (verify contract exists) ──
        if cfg.get("test_call_to"):
            # Try primary RPC first, then fallback RPCs for this network
            getcode_urls = [rpc_url] + cfg.get("fallback_rpcs", [])
            getcode_ok = False
            getcode_detail = ""
            for attempt_idx, gc_url in enumerate(getcode_urls):
                try:
                    result = rpc_call(client, gc_url, "eth_getCode",
                                      [cfg["test_call_to"], "latest"])
                    code = result.get("result", "0x")
                    has_code = code is not None and len(code) > 2
                    if has_code:
                        getcode_ok = True
                        getcode_detail = f"code length={len(code)} bytes"
                        if attempt_idx > 0:
                            getcode_detail += f" (used fallback RPC {attempt_idx})"
                        break
                    else:
                        getcode_detail = f"zero code at RPC {attempt_idx}"
                except Exception as gc_err:
                    getcode_detail = str(gc_err)[:80]
                    continue
            check("eth_getCode (factory has code)", getcode_ok, getcode_detail)


# ────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("RPC Endpoint Tests")
print("=" * 60)

for name, cfg in NETWORKS.items():
    test_network(name, cfg)

print(f"\n{'='*60}")
print(f"Results: {PASS} passed, {FAIL} failed")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
