"""
Pool discovery service.

Orchestrates the full discovery pipeline:
1. For each candidate token, discover pools from all available sources.
2. Filter pools to those matching MEXC token + ANY priceable quote asset
   (stablecoins, wrapped natives, MEXC-priced coins) — all arbitrage paths.
3. Deduplicate by network + pool_address.
"""

import logging

from models.pool_models import DiscoveredPool, ValidPool
from services.stablecoin_registry_service import QuoteRecord

logger = logging.getLogger(__name__)


class PoolDiscoveryService:
    """Service for discovering and filtering DEX pools."""

    def __init__(
        self,
        source_manager,
        stablecoin_registry_service,
    ):
        self._source_manager = source_manager
        self._stablecoins = stablecoin_registry_service

    async def discover_and_filter_pools(
        self,
        network: str,
        token_address: str,
        quote_records: dict[str, QuoteRecord] | set[str],
    ) -> list[ValidPool]:
        """Discover pools for a token and filter by priceable quote assets.

        Args:
            network: Internal network name.
            token_address: Lowercased token contract address.
            quote_records: {address_lower: QuoteRecord} of priceable quotes.
                A plain set of stablecoin addresses is also accepted for
                backward compatibility (treated as stable, price $1).

        Returns:
            List of ValidPool objects (empty if none found).
        """
        discovered = await self._source_manager.discover_from_all_sources(
            network=network,
            token_address=token_address,
        )

        return self._filter_pools(
            network=network,
            token_address=token_address,
            quote_records=quote_records,
            discovered_pools=discovered,
        )

    def _filter_pools(
        self,
        network: str,
        token_address: str,
        quote_records: dict[str, QuoteRecord] | set[str],
        discovered_pools: list[DiscoveredPool],
    ) -> list[ValidPool]:
        """Filter discovered pools to valid token<->quote pairs.

        A pool is valid if one of its tokens is the MEXC token address
        and the other is a known priceable quote asset (stablecoin,
        wrapped native, or any MEXC-priced coin).
        """
        token_address = token_address.lower()

        # Backward compat: plain set of stablecoin addresses.
        if isinstance(quote_records, set):
            quote_records = {
                addr.lower(): None for addr in quote_records
            }

        valid_pools: list[ValidPool] = []

        for pool in discovered_pools:
            if pool.network != network:
                continue

            token0 = pool.token0_address.lower()
            token1 = pool.token1_address.lower()

            quote_addr: str | None = None
            if token0 == token_address and token1 in quote_records:
                quote_addr = token1
            elif token1 == token_address and token0 in quote_records:
                quote_addr = token0

            if quote_addr is None:
                continue
            # Skip degenerate pools where token == quote.
            if quote_addr == token_address:
                continue

            record = quote_records.get(quote_addr)
            valid_pools.append(
                ValidPool(
                    network=network,
                    token_address=token_address,
                    stablecoin_address=quote_addr,
                    pool_address=pool.pool_address.lower(),
                    dex=pool.dex,
                    sources=pool.sources,
                    quote_coin=record.coin if record else None,
                    quote_is_stable=record.is_stable if record else True,
                    quote_price_coin=record.price_coin if record else None,
                    quote_withdraw_fee=(
                        str(record.withdraw_fee)
                        if record and record.withdraw_fee is not None
                        else None
                    ),
                )
            )

        return valid_pools
