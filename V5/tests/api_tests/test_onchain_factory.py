#!/usr/bin/env python3
"""
Single-request test for on-chain factory discovery.

Tests V2 (getPair) and V3 (getPool) factory calls on multiple networks.

Usage:
    python tests/api_tests/test_onchain_factory.py
"""

import os
import sys

import httpx
from dotenv import load_dotenv

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
load_dotenv(os.path.join(project_root, ".env"))

TIMEOUT = 30
PASS = 0
FAIL = 0
RPC_ID = 0

ALCHEMY_KEY = os.environ.get("ALCHEMY_KEY", "")


def rpc_call(client, url, method, params):
    global RPC_ID
    RPC_ID += 1
    payload = {"jsonrpc": "2.0", "id": RPC_ID, "method": method, "params": params}
    resp = client.post(url, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def resolve_rpc(network):
    """Resolve RPC URL matching config/networks.py logic."""
    env_map = {
        "ETHEREUM": "ETH_RPC_URL",
        "BSC": "BSC_RPC_URL",
        "POLYGON": "POLYGON_RPC_URL",
        "ARBITRUM": "ARBITRUM_RPC_URL",
    }
    alchemy_map = {
        "ETHEREUM": f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
        "BSC": f"https://bnb-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
        "POLYGON": f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
        "ARBITRUM": f"https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}",
    }
    fallback_map = {
        "ETHEREUM": "https://eth.llamarpc.com",
        "BSC": "https://bsc-dataseed.binance.org",
        "POLYGON": "https://polygon-rpc.com",
        "ARBITRUM": "https://arb1.arbitrum.io/rpc",
    }
    env_key = env_map.get(network)
    if env_key:
        env_val = os.environ.get(env_key, "")
        if env_val and not env_val.startswith("TODO_") and env_val.strip():
            return env_val.strip()
    if ALCHEMY_KEY and network in alchemy_map:
        return alchemy_map[network]
    return fallback_map.get(network)


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


# ── V2 factory test ────────────────────────────────────────────────────
def test_v2_factory(label, rpc_url, factory, token, stablecoin, expected_exists=True):
    """Test Uniswap V2-style getPair(token, stablecoin)."""
    data = (
        "0xe6a43905"
        + token[2:].zfill(64)
        + stablecoin[2:].zfill(64)
    )
    with httpx.Client(timeout=TIMEOUT) as client:
        try:
            result = rpc_call(client, rpc_url, "eth_call",
                              [{"to": factory, "data": data}, "latest"])
            raw = result.get("result", "0x" + "0" * 40)
            pool = "0x" + raw[-40:].lower()
            exists = pool != "0x0000000000000000000000000000000000000000"

            if expected_exists:
                check(f"V2 getPair {label}", exists,
                      f"pool={pool}" if exists else "zero address (no pair)")
            else:
                check(f"V2 getPair {label}", not exists,
                      f"pool={pool}")

            return pool if exists else None
        except Exception as e:
            check(f"V2 getPair {label}", False, str(e)[:100])
            return None


# ── V3 factory test ────────────────────────────────────────────────────
def test_v3_factory(label, rpc_url, factory, token, stablecoin, fee_bps):
    """Test Uniswap V3-style getPool(token, stablecoin, fee)."""
    # fee_bps = basis points (e.g. 5 = 0.05%)
    # uint24 fee value = fee_bps * 100 (e.g. 5 bps = 500)
    # ABI-encoded as 32-byte word (64 hex chars)
    fee_hex = format(fee_bps * 100, "064x")
    data = (
        "0x1698ee82"
        + token[2:].zfill(64)
        + stablecoin[2:].zfill(64)
        + fee_hex
    )
    with httpx.Client(timeout=TIMEOUT) as client:
        try:
            result = rpc_call(client, rpc_url, "eth_call",
                              [{"to": factory, "data": data}, "latest"])
            raw = result.get("result", "0x" + "0" * 40)
            pool = "0x" + raw[-40:].lower()
            exists = pool != "0x0000000000000000000000000000000000000000"

            check(f"V3 getPool {label} fee={fee_bps}bps", exists,
                  f"pool={pool}" if exists else "zero address (no pool)")
            return pool if exists else None
        except Exception as e:
            # Reverts are expected for non-existent pools
            if "revert" in str(e).lower() or "execution reverted" in str(e).lower():
                check(f"V3 getPool {label} fee={fee_bps}bps", False,
                      "reverted (expected if pool doesn't exist)")
            else:
                check(f"V3 getPool {label} fee={fee_bps}bps", False, str(e)[:80])
            return None


# ────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("On-Chain Factory Discovery Test")
print("=" * 60)

# ── 1. Ethereum V2: Uniswap V2 + SushiSwap ──
print("\n--- 1. Ethereum V2 ---")
eth_rpc = resolve_rpc("ETHEREUM")
if eth_rpc:
    masked = eth_rpc.replace(ALCHEMY_KEY, "***") if ALCHEMY_KEY and ALCHEMY_KEY in eth_rpc else eth_rpc
    print(f"     RPC: {masked[:40]}...")

    # Uniswap V2: USDC/USDT pair SHOULD exist
    test_v2_factory(
        "UniswapV2 USDC/USDT",
        eth_rpc,
        "0x5C69bEE701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
        "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
        expected_exists=True,
    )

    # Uniswap V2: USDC/FDUSD pair
    test_v2_factory(
        "UniswapV2 USDC/FDUSD",
        eth_rpc,
        "0x5C69bEE701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "0xc5f0f7b66764f6ec8c8dff7ba8636f259e5a3bfa",  # FDUSD
        expected_exists=True,
    )

    # SushiSwap V2: USDC/USDT pair
    test_v2_factory(
        "SushiSwapV2 USDC/USDT",
        eth_rpc,
        "0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "0xdac17f958d2ee523a2206206994597c13d831ec7",
        expected_exists=True,
    )

else:
    print("     ⚠️  No Ethereum RPC URL available")

# ── 2. Ethereum V3: Uniswap V3 ──
print("\n--- 2. Ethereum V3 ---")
if eth_rpc:
    # 0.05% = 5 bps = 500 uint24 — USDC/USDT 0.05% exists on mainnet
    test_v3_factory(
        "UniswapV3 USDC/USDT",
        eth_rpc,
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "0xdac17f958d2ee523a2206206994597c13d831ec7",
        fee_bps=5,   # 0.05% = fee=500
    )
    test_v3_factory(
        "UniswapV3 USDC/USDT",
        eth_rpc,
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "0xdac17f958d2ee523a2206206994597c13d831ec7",
        fee_bps=30,  # 0.30% = fee=3000
    )
    test_v3_factory(
        "UniswapV3 USDC/USDT",
        eth_rpc,
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "0xdac17f958d2ee523a2206206994597c13d831ec7",
        fee_bps=100, # 1.00% = fee=10000
    )

# ── 3. BSC V2: PancakeSwap V2 ──
print("\n--- 3. BSC V2 ---")
bsc_rpc = resolve_rpc("BSC")
if bsc_rpc:
    masked = bsc_rpc.replace(ALCHEMY_KEY, "***") if ALCHEMY_KEY and ALCHEMY_KEY in bsc_rpc else bsc_rpc
    print(f"     RPC: {masked[:40]}...")

    # PancakeSwap V2: USDT/USDC pair
    test_v2_factory(
        "PancakeSwapV2 USDT/USDC",
        bsc_rpc,
        "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
        "0x55d398326f99059ff775485246999027b3197955",  # BSC USDT
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # BSC USDC
        expected_exists=True,
    )

    # SushiSwap V2 on BSC: USDT/USDC
    test_v2_factory(
        "SushiSwapV2 USDT/USDC (BSC)",
        bsc_rpc,
        "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",  # SushiSwap factory BSC
        "0x55d398326f99059ff775485246999027b3197955",
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
        expected_exists=True,
    )

else:
    print("     ⚠️  No BSC RPC URL available")

# ── 4. Polygon V2: QuickSwap V3 ──
print("\n--- 4. Polygon V3 ---")
poly_rpc = resolve_rpc("POLYGON")
if poly_rpc:
    masked = poly_rpc.replace(ALCHEMY_KEY, "***") if ALCHEMY_KEY and ALCHEMY_KEY in poly_rpc else poly_rpc
    print(f"     RPC: {masked[:40]}...")

    # QuickSwap V3 (Algebra-based): USDC/USDT
    test_v3_factory(
        "QuickSwapV3 USDC/USDT",
        poly_rpc,
        "0x411b0fAcC3489691f28ad58c47006AF5E3Ab3A28",
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",  # Polygon USDC
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",  # Polygon USDT
        fee_bps=5,
    )

    # Uniswap V3 on Polygon
    test_v3_factory(
        "UniswapV3 USDC/USDT (Polygon)",
        poly_rpc,
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
        fee_bps=5,
    )

else:
    print("     ⚠️  No Polygon RPC URL available")

# ── 5. Arbitrum V2: Camelot V2 + SushiSwap ──
print("\n--- 5. Arbitrum V2 ---")
arb_rpc = resolve_rpc("ARBITRUM")
if arb_rpc:
    masked = arb_rpc.replace(ALCHEMY_KEY, "***") if ALCHEMY_KEY and ALCHEMY_KEY in arb_rpc else arb_rpc
    print(f"     RPC: {masked[:40]}...")

    # Camelot V2: USDC/USDT
    test_v2_factory(
        "CamelotV2 USDC/USDT",
        arb_rpc,
        "0x6EcCab422D763aC031210895C81787E87B43A652",
        "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # Arbitrum USDC
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",  # Arbitrum USDT
        expected_exists=True,
    )

    # SushiSwap V2 on Arbitrum: USDC/USDT
    test_v2_factory(
        "SushiSwapV2 USDC/USDT (Arbitrum)",
        arb_rpc,
        "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
        "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
        expected_exists=True,
    )

else:
    print("     ⚠️  No Arbitrum RPC URL available")

# ────────────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"Results: {PASS} passed, {FAIL} failed")
print(f"{'='*60}")
sys.exit(0 if FAIL == 0 else 1)
