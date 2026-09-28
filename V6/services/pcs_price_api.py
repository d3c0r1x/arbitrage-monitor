"""
PancakeSwap Price API client (same endpoint as @pancakeswap/price-api-sdk).

No private keys. HTTP only:
  GET https://wallet-api.pancakeswap.com/v1/prices/list/{chainId:addr,...}?preview=1
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Iterable
from urllib.parse import quote

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://wallet-api.pancakeswap.com"
CHAIN_IDS = {
    "ETHEREUM": 1,
    "BSC": 56,
    "POLYGON": 137,
    "ARBITRUM": 42161,
    "BASE": 8453,
}


class PcsPriceApi:
    """Batch USD marks from PancakeSwap wallet price API."""

    def __init__(self, http_client, base_url: str | None = None):
        self._http = http_client
        self._base = (base_url or DEFAULT_BASE).rstrip("/")

    async def get_token_prices(
        self,
        network: str,
        addresses: Iterable[str],
    ) -> dict[str, Decimal]:
        chain_id = CHAIN_IDS.get(network.upper())
        if chain_id is None:
            return {}
        addrs = [a.lower() for a in addresses if a]
        if not addrs:
            return {}
        key = quote(",".join(f"{chain_id}:{a}" for a in addrs), safe="")
        url = f"{self._base}/v1/prices/list/{key}?preview=1"
        try:
            resp = await self._http.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("pcs_price_api_failed: %s", exc)
            return {}
        out: dict[str, Decimal] = {}
        for a in addrs:
            raw = data.get(f"{chain_id}:{a}")
            if raw is None:
                continue
            try:
                px = Decimal(str(raw))
            except Exception:
                continue
            if px > 0:
                out[a] = px
        return out
