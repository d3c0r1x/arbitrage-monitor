"""Persistent PCS sidecar client (line-delimited JSON over stdio)."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

SIDECAR_DIR = Path(__file__).resolve().parent / "pcs_sidecar"
SERVER_JS = SIDECAR_DIR / "server.js"


class PcsSidecar:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self) -> dict[str, Any]:
        if self._proc and self._proc.poll() is None:
            return {"ok": True, "already": True}
        env = os.environ.copy()
        self._proc = subprocess.Popen(
            ["node", str(SERVER_JS)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(SIDECAR_DIR),
            env=env,
        )
        hello = self._readline(timeout=30)
        return hello or {"ok": False, "error": "no_hello"}

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
        self._proc = None

    def _readline(self, timeout: float = 180.0) -> dict[str, Any] | None:
        assert self._proc and self._proc.stdout
        result: dict[str, Any] = {}
        error: list[BaseException] = []

        def _read() -> None:
            try:
                line = self._proc.stdout.readline()
                if not line:
                    result["ok"] = False
                    result["error"] = "eof"
                    return
                result.update(json.loads(line))
            except BaseException as exc:  # noqa: BLE001
                error.append(exc)

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            return {"ok": False, "error": "timeout"}
        if error:
            return {"ok": False, "error": str(error[0])}
        return result or None

    def call(self, payload: dict[str, Any], timeout: float = 180.0) -> dict[str, Any]:
        with self._lock:
            if not self._proc or self._proc.poll() is not None:
                self.start()
            assert self._proc and self._proc.stdin
            t0 = time.perf_counter()
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
            data = self._readline(timeout=timeout) or {"ok": False, "error": "no_response"}
            data["wall_ms"] = (time.perf_counter() - t0) * 1000
            return data

    def ping(self) -> dict[str, Any]:
        return self.call({"cmd": "ping"}, timeout=15)

    def price(self, chain_id: int, addresses: list[str]) -> dict[str, Any]:
        return self.call(
            {
                "cmd": "price",
                "chainId": chain_id,
                "addresses": ",".join(addresses),
            },
            timeout=30,
        )

    def quote(self, **kwargs: Any) -> dict[str, Any]:
        payload = {"cmd": "quote", **kwargs}
        # normalize keys for node
        if "token_in" in payload:
            payload["tokenIn"] = payload.pop("token_in")
        if "token_out" in payload:
            payload["tokenOut"] = payload.pop("token_out")
        if "amount_in" in payload:
            payload["amountIn"] = str(payload.pop("amount_in"))
        if "chain_id" in payload:
            payload["chainId"] = payload.pop("chain_id")
        if "decimals_in" in payload:
            payload["decimalsIn"] = payload.pop("decimals_in")
        if "decimals_out" in payload:
            payload["decimalsOut"] = payload.pop("decimals_out")
        if "max_hops" in payload:
            payload["maxHops"] = payload.pop("max_hops")
        if "max_splits" in payload:
            payload["maxSplits"] = payload.pop("max_splits")
        if "skip_v3" in payload:
            payload["skipV3"] = "1" if payload.pop("skip_v3") else "0"
        return self.call(payload, timeout=180)
