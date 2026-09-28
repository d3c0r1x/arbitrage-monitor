"""MEXC asset service.

Responsible for:
- Selecting candidate tokens for pool discovery.
- Filtering by active networks, contract addresses, and price pair availability.
- Ranking candidates by 24h volume (no hard cutoff — pagination handles load).
"""

import logging
from decimal import Decimal

from config.networks import NETWORKS
from models.mexc_models import MexcAsset

logger = logging.getLogger(__name__)


class MexcAssetService:
    """Service for MEXC asset operations."""

    def __init__(self, mexc_client):
        self._mexc_client = mexc_client

    async def fetch_and_parse_assets(self) -> list[MexcAsset]:
        """Fetch capital config from MEXC and parse into structured assets.

        Returns:
            List of MexcAsset with parsed network details.
        """
        raw_capital_config = await self._mexc_client.get_capital_config()
        return self._mexc_client.parse_capital_config(raw_capital_config)

    async def fetch_24hr_volumes(self) -> dict[str, Decimal]:
        """Fetch 24hr quote volume for USDT/USDC pairs.

        Returns:
            Dict of symbol -> quote_volume (e.g. {"BTCUSDT": Decimal("1234567.89")}).
        """
        raw = await self._mexc_client.get_24hr_ticker()
        volumes: dict[str, Decimal] = {}
        for item in raw:
            symbol = str(item.get("symbol", "")).upper()
            if not (symbol.endswith("USDT") or symbol.endswith("USDC")):
                continue
            qv = item.get("quoteVolume")
            if qv is None:
                continue
            try:
                volumes[symbol] = Decimal(str(qv))
            except Exception:
                pass
        return volumes

    def select_candidate_tokens(
        self,
        assets: list[MexcAsset],
        prices: dict[str, Decimal],
        volumes: dict[str, Decimal] | None = None,
    ) -> list[tuple[MexcAsset, str, str, str]]:
        """Select tokens that are candidates for pool discovery.

        A token is a candidate if:
        1. It has at least one tradable network (deposit OR withdraw enabled
           — each arbitrage direction needs only one of the two).
        2. The network is in supported networks.
        3. It has a contract address.
        4. It has a MEXC price pair TOKENUSDT or TOKENUSDC.

        No volume filter is applied (plan principle #4: scan all pairs).
        Candidates are sorted by volume descending for prioritized pagination.

        Returns:
            List of (asset, network, contract_address, quote_asset) tuples.
        """
        supported_networks = set(NETWORKS.keys())
        candidates: list[tuple[MexcAsset, str, str, str, Decimal]] = []

        for asset in assets:
            coin = asset.coin.upper()

            # Check price pair availability.
            usdt_volume = Decimal("0")
            usdc_volume = Decimal("0")
            has_usdt = False
            has_usdc = False

            usdt_sym = f"{coin}USDT"
            usdc_sym = f"{coin}USDC"

            if usdt_sym in prices:
                has_usdt = True
                usdt_volume = volumes.get(usdt_sym, Decimal("0")) if volumes else Decimal("0")
            if usdc_sym in prices:
                has_usdc = True
                usdc_volume = volumes.get(usdc_sym, Decimal("0")) if volumes else Decimal("0")

            if not has_usdt and not has_usdc:
                continue

            for network_asset in asset.tradable_networks():
                network = network_asset.network_normalized

                if network not in supported_networks:
                    continue

                contract = network_asset.contract_address
                if not contract:
                    continue

                # Use the best volume for ranking.
                best_vol = max(
                    usdt_volume if has_usdt else Decimal("0"),
                    usdc_volume if has_usdc else Decimal("0"),
                )
                if has_usdt:
                    candidates.append((asset, network, contract, "USDT", best_vol))
                if has_usdc:
                    candidates.append((asset, network, contract, "USDC", best_vol))

        # Sort by volume descending (for prioritized pagination in pool_refresh_task).
        candidates.sort(key=lambda c: c[4], reverse=True)
        logger.info(
            "candidates_selected: total=%d",
            len(candidates),
        )

        # Strip the volume field for return.
        result: list[tuple[MexcAsset, str, str, str]] = [
            (a, n, c, q) for a, n, c, q, _v in candidates
        ]
        return result

    @staticmethod
    def get_candidate_summary(candidates: list) -> dict:
        """Return a summary of candidate selection for metrics."""
        networks_used: set[str] = set()
        for _, network, _, _ in candidates:
            networks_used.add(network)

        return {
            "total_candidates": len(candidates),
            "unique_tokens": len({c[0].coin for c in candidates}),
            "networks": sorted(networks_used),
        }
