"""Hot-pool / event boost for inventory DEX↔DEX scanner (no CEX, no flashloan).

Pulls DexScreener boosts + profiles + Base/BSC search hits; ranks tokens that
moved recently so Cross/Chain scan those groups first.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from config.networks import is_network_active

logger = logging.getLogger(__name__)

HOT_PATH = Path(__file__).resolve().parent.parent / "data" / "hot_pools.json"

_CHAIN_TO_NET = {
    "ethereum": "ETHEREUM",
    "bsc": "BSC",
    "arbitrum": "ARBITRUM",
    "base": "BASE",
    "polygon": "POLYGON",
}


class HotPoolTracker:
    def __init__(self, http_client: httpx.AsyncClient):
        self._client = http_client
        self._hot: dict[tuple[str, str], float] = {}  # (net, token) -> score

    def score(self, network: str, token_address: str) -> float:
        return float(self._hot.get((network.upper(), token_address.lower()), 0.0))

    async def refresh(self) -> dict:
        scores: dict[tuple[str, str], float] = {}
        now = time.time()

        async def _get(url: str):
            try:
                r = await self._client.get(url, timeout=30)
                r.raise_for_status()
                return r.json()
            except Exception as exc:
                logger.debug("hot_pool_http_fail: %s %s", url, exc)
                return None

        for url, weight in (
            ("https://api.dexscreener.com/token-boosts/top/v1", 3.0),
            ("https://api.dexscreener.com/token-profiles/latest/v1", 2.0),
        ):
            data = await _get(url)
            if not isinstance(data, list):
                continue
            for item in data[:120]:
                chain = str(item.get("chainId") or "").lower()
                net = _CHAIN_TO_NET.get(chain)
                addr = str(item.get("tokenAddress") or "").lower()
                if not net or not is_network_active(net) or not addr:
                    continue
                key = (net, addr)
                scores[key] = max(scores.get(key, 0.0), weight)

        for q in (
            "BASE", "WETH", "AERO", "VIRTUAL", "BRETT",
            "POLYGON", "WMATIC", "QUICK", "ARB", "CAKE", "PEPE",
        ):
            data = await _get(f"https://api.dexscreener.com/latest/dex/search?q={q}")
            pairs = []
            if isinstance(data, dict):
                pairs = data.get("pairs") or []
            for pair in pairs[:50]:
                chain = str(pair.get("chainId") or "").lower()
                net = _CHAIN_TO_NET.get(chain)
                if not net or not is_network_active(net):
                    continue
                # Volume spike = event-ish.
                try:
                    vol = float((pair.get("volume") or {}).get("h1") or 0)
                    chg = abs(float((pair.get("priceChange") or {}).get("h1") or 0))
                except (TypeError, ValueError):
                    vol, chg = 0.0, 0.0
                if vol < 5000 and chg < 3:
                    continue
                for side in ("baseToken", "quoteToken"):
                    addr = str((pair.get(side) or {}).get("address") or "").lower()
                    if not addr:
                        continue
                    key = (net, addr)
                    bump = 1.0 + min(vol / 100000.0, 5.0) + min(chg / 10.0, 3.0)
                    scores[key] = max(scores.get(key, 0.0), bump)

        # GeckoTerminal trending (light; one page per active net).
        _gt = {
            "eth": "ETHEREUM",
            "bsc": "BSC",
            "arbitrum": "ARBITRUM",
            "base": "BASE",
            "polygon_pos": "POLYGON",
        }
        for slug, net in _gt.items():
            if not is_network_active(net):
                continue
            data = await _get(
                f"https://api.geckoterminal.com/api/v2/networks/{slug}/trending_pools"
                "?page=1&include=base_token,quote_token"
            )
            if not isinstance(data, dict):
                continue
            included = {
                str(i.get("id") or ""): i
                for i in (data.get("included") or [])
                if isinstance(i, dict)
            }
            for item in (data.get("data") or [])[:30]:
                if not isinstance(item, dict):
                    continue
                for side in ("base_token", "quote_token"):
                    rel = ((item.get("relationships") or {}).get(side) or {}).get("data") or {}
                    iid = str(rel.get("id") or "")
                    inc = included.get(iid) or {}
                    addr = str((inc.get("attributes") or {}).get("address") or "").lower()
                    if not addr.startswith("0x"):
                        continue
                    key = (net, addr)
                    scores[key] = max(scores.get(key, 0.0), 2.5)

        self._hot = scores
        payload = {
            "exported_ts": now,
            "count": len(scores),
            "tokens": [
                {"network": n, "token_address": a, "score": s}
                for (n, a), s in sorted(scores.items(), key=lambda x: -x[1])[:500]
            ],
        }
        try:
            HOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = HOT_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(HOT_PATH)
        except OSError as exc:
            logger.debug("hot_pool_write_fail: %s", exc)
        logger.info("hot_pools_refreshed: tokens=%d", len(scores))
        return {"tokens": len(scores)}
