"""
Public read-only clients for Bitget / HTX / BingX (+ optional OKX DEX).

Purpose: contract/symbol → last price (+ optional depth) for alt-CEX arb probe.
Never places orders. Failures are soft (empty maps / None prices).
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from config.alt_cex_config import (
    BINGX_BASE_URL,
    BINGX_ENDPOINTS,
    BITGET_BASE_URL,
    BITGET_ENDPOINTS,
    CHAIN_ALIASES,
    HTX_BASE_URL,
    HTX_ENDPOINTS,
    OKX_BASE_URL,
    OKX_ENDPOINTS,
)

logger = logging.getLogger(__name__)

_HEX_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _norm_addr(addr: str | None) -> str:
    if not addr:
        return ""
    a = str(addr).strip().lower()
    if a.startswith("0x"):
        return a
    # HTX sometimes omits 0x for EVM contracts.
    if re.fullmatch(r"[0-9a-fA-F]{40}", a):
        return "0x" + a
    return a


def _norm_network(raw: str | None) -> str | None:
    if not raw:
        return None
    key = str(raw).strip().lower()
    if key in CHAIN_ALIASES:
        return CHAIN_ALIASES[key]
    # Loose contains.
    for alias, net in CHAIN_ALIASES.items():
        if alias in key:
            return net
    return None


def _dec(val: Any) -> Decimal | None:
    if val is None or val == "":
        return None
    try:
        d = Decimal(str(val))
        return d if d > 0 else None
    except (InvalidOperation, ValueError):
        return None


class AltCexClient:
    """Aggregates public market data from secondary CEXes."""

    def __init__(self, client: httpx.AsyncClient, *, enable_okx_dex: bool = False):
        self._client = client
        self._enable_okx_dex = enable_okx_dex
        # (network, contract) → {exchange: coin_symbol}
        self._contract_map: dict[tuple[str, str], dict[str, str]] = {}
        # exchange → set of tradable base symbols (upper)
        self._symbol_bases: dict[str, set[str]] = {
            "bitget": set(),
            "htx": set(),
            "bingx": set(),
        }
        # exchange → symbol → last price (USDT pairs)
        self._tickers: dict[str, dict[str, Decimal]] = {
            "bitget": {},
            "htx": {},
            "bingx": {},
        }
        self._maps_loaded = False

    @property
    def maps_loaded(self) -> bool:
        return self._maps_loaded

    async def _get_json(self, url: str, params: dict | None = None, retries: int = 3) -> Any:
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                resp = await self._client.get(url, params=params, timeout=45)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < retries:
                    continue
        logger.warning("alt_cex_http_fail: %s err=%s", url, last_exc)
        return None

    async def refresh_maps(self) -> dict[str, int]:
        """Refresh contract→coin maps and USDT ticker caches."""
        stats = {"bitget": 0, "htx": 0, "bingx": 0, "contracts": 0}
        self._contract_map.clear()
        for ex in self._symbol_bases:
            self._symbol_bases[ex].clear()
            self._tickers[ex].clear()

        await self._load_bitget(stats)
        await self._load_htx(stats)
        await self._load_bingx(stats)
        self._maps_loaded = True
        stats["contracts"] = len(self._contract_map)
        logger.info(
            "alt_cex_maps_refreshed: contracts=%d bitget=%d htx=%d bingx=%d",
            stats["contracts"],
            stats["bitget"],
            stats["htx"],
            stats["bingx"],
        )
        return stats

    def _remember_contract(self, network: str, contract: str, exchange: str, coin: str) -> None:
        addr = _norm_addr(contract)
        if not addr or not coin:
            return
        # Prefer EVM-looking addresses for our networks.
        if network in {"BSC", "BASE", "ETHEREUM", "ARBITRUM", "POLYGON"} and not _HEX_RE.match(addr):
            return
        key = (network, addr)
        bucket = self._contract_map.setdefault(key, {})
        bucket[exchange] = coin.upper()

    async def _load_bitget(self, stats: dict[str, int]) -> None:
        data = await self._get_json(BITGET_BASE_URL + BITGET_ENDPOINTS["coins"])
        rows = (data or {}).get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return
        for row in rows:
            coin = str(row.get("coin") or "").upper()
            if not coin:
                continue
            self._symbol_bases["bitget"].add(coin)
            for ch in row.get("chains") or []:
                net = _norm_network(ch.get("chain") or ch.get("chainName"))
                addr = ch.get("contractAddress") or ch.get("contract_address")
                if net and addr:
                    self._remember_contract(net, str(addr), "bitget", coin)
                    stats["bitget"] += 1

        tick = await self._get_json(BITGET_BASE_URL + BITGET_ENDPOINTS["tickers"])
        trows = (tick or {}).get("data") if isinstance(tick, dict) else None
        if isinstance(trows, list):
            for t in trows:
                sym = str(t.get("symbol") or "").upper()
                if not sym.endswith("USDT"):
                    continue
                px = _dec(t.get("lastPr") or t.get("close") or t.get("last"))
                if px:
                    self._tickers["bitget"][sym] = px

    async def _load_htx(self, stats: dict[str, int]) -> None:
        data = await self._get_json(HTX_BASE_URL + HTX_ENDPOINTS["currencies"])
        rows = (data or {}).get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return
        for row in rows:
            coin = str(row.get("currency") or "").upper()
            if not coin:
                continue
            self._symbol_bases["htx"].add(coin)
            for ch in row.get("chains") or []:
                net = _norm_network(
                    ch.get("baseChain")
                    or ch.get("displayName")
                    or ch.get("baseChainProtocol")
                    or ch.get("chain")
                )
                addr = ch.get("contractAddress")
                if net and addr:
                    self._remember_contract(net, str(addr), "htx", coin)
                    stats["htx"] += 1

        tick = await self._get_json(HTX_BASE_URL + HTX_ENDPOINTS["tickers"])
        trows = (tick or {}).get("data") if isinstance(tick, dict) else None
        if isinstance(trows, list):
            for t in trows:
                sym = str(t.get("symbol") or "").upper()  # btcusdt
                if not sym.endswith("USDT"):
                    continue
                px = _dec(t.get("close") or t.get("last"))
                if px:
                    # Normalize to BTCUSDT
                    self._tickers["htx"][sym] = px

    async def _load_bingx(self, stats: dict[str, int]) -> None:
        # No public contract directory — index USDT bases from symbols + tickers.
        data = await self._get_json(BINGX_BASE_URL + BINGX_ENDPOINTS["symbols"])
        payload = (data or {}).get("data") if isinstance(data, dict) else None
        symbols = []
        if isinstance(payload, dict):
            symbols = payload.get("symbols") or []
        elif isinstance(payload, list):
            symbols = payload
        for row in symbols:
            sym = str(row.get("symbol") or "").upper()  # BTC-USDT
            if not sym.endswith("-USDT") and not sym.endswith("USDT"):
                continue
            base = sym.replace("-USDT", "").replace("USDT", "")
            if base:
                self._symbol_bases["bingx"].add(base)
                stats["bingx"] += 1

        tick = await self._get_json(BINGX_BASE_URL + BINGX_ENDPOINTS["tickers"])
        trows = (tick or {}).get("data") if isinstance(tick, dict) else None
        if isinstance(trows, list):
            for t in trows:
                sym = str(t.get("symbol") or "").upper()  # BTC-USDT
                if "-USDT" not in sym and not sym.endswith("USDT"):
                    continue
                px = _dec(t.get("lastPrice") or t.get("last"))
                if px:
                    compact = sym.replace("-", "")
                    self._tickers["bingx"][compact] = px
                    self._tickers["bingx"][sym] = px

    def resolve_listings(
        self, network: str, token_address: str, token_coin: str
    ) -> list[dict[str, str]]:
        """Return [{exchange, coin, match}] for exchanges that list this token."""
        out: list[dict[str, str]] = []
        addr = _norm_addr(token_address)
        net = network.upper()
        mapped = self._contract_map.get((net, addr), {})

        for ex in ("bitget", "htx", "bingx"):
            coin = mapped.get(ex)
            match = "contract"
            if not coin:
                # BingX (and fallback): match by ticker symbol only.
                cand = (token_coin or "").upper()
                if cand and cand in self._symbol_bases.get(ex, set()):
                    coin = cand
                    match = "symbol"
            if coin:
                out.append({"exchange": ex, "coin": coin, "match": match})
        return out

    def get_usdt_price(self, exchange: str, coin: str) -> Decimal | None:
        coin_u = coin.upper()
        ticks = self._tickers.get(exchange) or {}
        for key in (f"{coin_u}USDT", f"{coin_u}-USDT", f"{coin_u.lower()}usdt"):
            if key in ticks:
                return ticks[key]
            # HTX stores lowercase btcusdt
            low = key.lower()
            if low in ticks:
                return ticks[low]
        return None

    async def get_orderbook_mid(
        self, exchange: str, coin: str, *, limit: int = 20
    ) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        """Return (bid, ask, mid) best top-of-book. Soft-fail → (None,None,None)."""
        coin_u = coin.upper()
        try:
            if exchange == "bitget":
                data = await self._get_json(
                    BITGET_BASE_URL + BITGET_ENDPOINTS["orderbook"],
                    params={"symbol": f"{coin_u}USDT", "type": "step0", "limit": str(limit)},
                )
                book = (data or {}).get("data") if isinstance(data, dict) else None
                if not isinstance(book, dict):
                    return None, None, None
                bids = book.get("bids") or []
                asks = book.get("asks") or []
            elif exchange == "htx":
                data = await self._get_json(
                    HTX_BASE_URL + HTX_ENDPOINTS["depth"],
                    params={"symbol": f"{coin_u.lower()}usdt", "type": "step0"},
                )
                book = (data or {}).get("tick") if isinstance(data, dict) else None
                if not isinstance(book, dict):
                    return None, None, None
                bids = book.get("bids") or []
                asks = book.get("asks") or []
            elif exchange == "bingx":
                data = await self._get_json(
                    BINGX_BASE_URL + BINGX_ENDPOINTS["depth"],
                    params={"symbol": f"{coin_u}-USDT", "limit": limit},
                )
                book = (data or {}).get("data") if isinstance(data, dict) else None
                if not isinstance(book, dict):
                    return None, None, None
                bids = book.get("bids") or []
                asks = book.get("asks") or []
            else:
                return None, None, None

            bid = _dec(bids[0][0]) if bids else None
            ask = _dec(asks[0][0]) if asks else None
            if bid and ask:
                return bid, ask, (bid + ask) / Decimal(2)
            return bid, ask, bid or ask
        except Exception as exc:
            logger.debug("alt_cex_depth_fail: %s %s %s", exchange, coin_u, exc)
            return None, None, None

    async def okx_dex_token_price(
        self, chain_id: int, token_address: str
    ) -> Decimal | None:
        if not self._enable_okx_dex:
            return None
        data = await self._get_json(
            OKX_BASE_URL + OKX_ENDPOINTS["dex_token_info"],
            params={
                "chainId": str(chain_id),
                "tokenContractAddress": token_address,
            },
        )
        # Response shape varies; try common fields.
        rows = (data or {}).get("data") if isinstance(data, dict) else None
        if isinstance(rows, list) and rows:
            row = rows[0]
            for key in ("price", "tokenPrice", "usdPrice", "lastPrice"):
                px = _dec(row.get(key) if isinstance(row, dict) else None)
                if px:
                    return px
        return None
