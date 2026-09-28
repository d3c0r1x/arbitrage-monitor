"""Persist DEX↔DEX signals + live radar (separate from MEXC paths)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SIGNALS_PATH = Path("data") / "dex_dex_signals.jsonl"
LIVE_PATH = Path("data") / "dex_dex_opportunities_live.json"
DIAG_PATH = Path("data") / "dex_dex_diagnostics.json"
FRESH_MAX_AGE_SEC = 45


class DexDexStore:
    """Append-only signals + last-cycle live opportunities."""

    def __init__(
        self,
        signals_path: str | Path = SIGNALS_PATH,
        live_path: str | Path = LIVE_PATH,
        diag_path: str | Path = DIAG_PATH,
        fresh_max_age_sec: float = FRESH_MAX_AGE_SEC,
    ):
        self._signals_path = Path(signals_path)
        self._live_path = Path(live_path)
        self._diag_path = Path(diag_path)
        self._fresh_max_age_sec = float(fresh_max_age_sec)
        self._opps: list[dict] = []
        self._diag: dict = {}

    def write_signal(self, row: dict) -> None:
        self._signals_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False, default=str)
        with self._signals_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def publish_cycle(self, opportunities: list[dict]) -> None:
        """Replace live radar with this cycle's results (quote-simulation based)."""
        now = time.time()
        items = []
        for o in opportunities:
            if not isinstance(o, dict):
                continue
            net = float(o.get("net_profit_pct") or 0)
            items.append(
                {
                    "key": o.get("key")
                    or f"{o.get('network')}:{o.get('direction')}:{o.get('token_coin')}:{o.get('buy_pool') or o.get('pool_address')}",
                    "network": o.get("network"),
                    "token_coin": o.get("token_coin") or o.get("start_coin") or "",
                    "direction": o.get("direction"),
                    "buy_dex": o.get("buy_dex") or "",
                    "sell_dex": o.get("sell_dex") or "",
                    "dex": o.get("dex")
                    or (
                        f"{o.get('buy_dex')}→{o.get('sell_dex')}"
                        if o.get("buy_dex")
                        else "multi"
                    ),
                    "pool_address": o.get("buy_pool") or o.get("pool_address") or "",
                    "sell_pool": o.get("sell_pool") or "",
                    "hops": o.get("hops") or len(o.get("chain") or []),
                    "base_amount_usd": float(o.get("base_amount_usd") or o.get("input_usd") or 0),
                    "amount_in_raw": o.get("amount_in_raw"),
                    "amount_out_raw": o.get("amount_out_raw"),
                    "net_profit_pct": net,
                    "net_profit_usd": float(o.get("net_profit_usd") or o.get("profit_usd") or 0),
                    "gross_profit_pct": float(
                        o.get("gross_profit_pct") or o.get("profit_pct") or net
                    ),
                    "gas_usd": float(o.get("gas_usd") or 0),
                    "quoted_ts": now,
                    "fresh": True,
                    "warnings": list(o.get("warnings") or []),
                }
            )
        items.sort(key=lambda x: x["net_profit_pct"], reverse=True)
        self._opps = items
        payload = {
            "exported_ts": now,
            "fresh_max_age_sec": self._fresh_max_age_sec,
            "count": len(items),
            "fresh_count": len(items),
            "opportunities": items,
        }
        try:
            self._live_path.parent.mkdir(parents=True, exist_ok=True)
            raw = json.dumps(payload, ensure_ascii=False, default=str)
            tmp = self._live_path.with_suffix(".json.tmp")
            tmp.write_text(raw, encoding="utf-8")
            tmp.replace(self._live_path)
        except OSError as exc:
            logger.debug("dex_dex_live_export_failed: %s", exc)

    def publish_diagnostics(self, payload: dict) -> None:
        """Write last-cycle reject funnel + near-miss."""
        now = time.time()
        body = {"exported_ts": now, **(payload or {})}
        self._diag = body
        try:
            self._diag_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._diag_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(body, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            tmp.replace(self._diag_path)
        except OSError as exc:
            logger.debug("dex_dex_diag_export_failed: %s", exc)
