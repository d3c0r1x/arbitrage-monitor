"""Call tools/pcs_sidecar (Node) for PCS Smart Router quotes + Price API."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

SIDECAR_DIR = Path(__file__).resolve().parent / "pcs_sidecar"
SIDECAR_JS = SIDECAR_DIR / "index.js"


def _run(payload: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
    if not SIDECAR_JS.is_file():
        return {"ok": False, "error": "sidecar_missing"}
    env = os.environ.copy()
    # Ensure subgraph / price API reach the Node child even if only in .env via dotenv.
    t0 = time.perf_counter()
    proc = subprocess.run(
        ["node", str(SIDECAR_JS)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(SIDECAR_DIR),
        env=env,
    )
    wall_ms = (time.perf_counter() - t0) * 1000
    raw = (proc.stdout or "").strip().splitlines()
    if not raw:
        return {
            "ok": False,
            "error": f"empty_stdout_exit_{proc.returncode}",
            "stderr": (proc.stderr or "")[-500:],
            "wall_ms": wall_ms,
        }
    try:
        data = json.loads(raw[-1])
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "bad_json",
            "stdout": raw[-1][:300],
            "wall_ms": wall_ms,
        }
    data["wall_ms"] = wall_ms
    return data


def pcs_ping() -> dict[str, Any]:
    return _run({"cmd": "ping"}, timeout=15)


def pcs_price(chain_id: int, addresses: list[str]) -> dict[str, Any]:
    return _run(
        {
            "cmd": "price",
            "chainId": chain_id,
            "addresses": ",".join(addresses),
        },
        timeout=30,
    )


def pcs_quote(
    *,
    rpc: str,
    token_in: str,
    token_out: str,
    amount_in: int,
    chain_id: int = 56,
    decimals_in: int | None = None,
    decimals_out: int | None = None,
    max_hops: int = 2,
    max_splits: int = 2,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "cmd": "quote",
        "rpc": rpc,
        "chainId": chain_id,
        "tokenIn": token_in,
        "tokenOut": token_out,
        "amountIn": str(amount_in),
        "maxHops": max_hops,
        "maxSplits": max_splits,
    }
    if decimals_in is not None:
        payload["decimalsIn"] = decimals_in
    if decimals_out is not None:
        payload["decimalsOut"] = decimals_out
    return _run(payload, timeout=120)
