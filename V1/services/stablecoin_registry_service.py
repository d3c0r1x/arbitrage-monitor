"""
Stablecoin registry service.

Builds a per-network registry of stablecoin addresses from MEXC assets.
Only whitelist coins are included.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from config.stablecoins import STABLECOIN_WHITELIST
from config.wrapped_natives import price_coin_aliases, wrapped_natives_for_network
from models.mexc_models import MexcAsset

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StablecoinRecord:
    coin: str
    network: str
    address: str
    deposit_enable: bool
    withdraw_enable: bool
    withdraw_fee: Decimal | None


@dataclass(frozen=True)
class QuoteRecord:
    """A priceable quote asset that can sit on the non-token side of a pool.

    Covers stablecoins (price = $1), wrapped-native tokens (WETH/WBNB/...),
    and any MEXC-listed coin with a USD price pair. This is what allows the
    bot to count ALL arbitrage paths, not just token<->stablecoin.
    """

    coin: str
    network: str
    address: str
    is_stable: bool
    # MEXC base symbol used to price this quote in USD (e.g. "ETH" -> ETHUSDT).
    price_coin: str
    withdraw_fee: Decimal | None = None
    deposit_enable: bool = True
    withdraw_enable: bool = True


class StablecoinRegistryService:
    """Service for building and querying stablecoin registries."""

    def build_registry(
        self,
        assets: list[MexcAsset],
    ) -> dict[str, list[StablecoinRecord]]:
        """Build a per-network registry of stablecoin addresses.

        Args:
            assets: Parsed MEXC assets.

        Returns:
            Dict of network -> list of StablecoinRecord.
        """
        registry: dict[str, list[StablecoinRecord]] = {}

        for asset in assets:
            if asset.coin not in STABLECOIN_WHITELIST:
                continue

            for network_asset in asset.active_networks():
                record = StablecoinRecord(
                    coin=asset.coin,
                    network=network_asset.network_normalized,
                    address=network_asset.contract_address.lower(),
                    deposit_enable=network_asset.deposit_enable,
                    withdraw_enable=network_asset.withdraw_enable,
                    withdraw_fee=network_asset.withdraw_fee,
                )

                registry.setdefault(record.network, [])
                registry[record.network].append(record)

        return registry

    def stablecoin_addresses_for_network(
        self,
        registry: dict[str, list[StablecoinRecord]],
        network: str,
    ) -> set[str]:
        """Get all stablecoin addresses for a given network.

        Returns:
            Set of lowercased addresses.
        """
        return {
            record.address.lower()
            for record in registry.get(network, [])
        }

    # ── Broad quote registry (all priceable arbitrage paths) ──────────────

    def build_quote_registry(
        self,
        assets: list[MexcAsset],
        prices: dict[str, Decimal] | dict[str, object],
    ) -> dict[str, dict[str, QuoteRecord]]:
        """Build a per-network registry of ALL priceable quote assets.

        A quote asset is anything that can legitimately sit on the opposite
        side of a DEX pool from a MEXC token and still be priced/settled:
          1. Stablecoins (whitelist) -> price $1.
          2. Any MEXC coin with a live USD price pair (COINUSDT / COINUSDC)
             and an on-chain contract on the network.
          3. Wrapped-native / common base tokens (WETH, WBNB, WBTC, WMATIC)
             priced via their MEXC spot coin.

        Args:
            assets: Parsed MEXC assets.
            prices: Flat dict of MEXC symbol -> price (e.g. {"ETHUSDT": ...}).

        Returns:
            Dict of network -> {address_lower: QuoteRecord}.
        """
        registry: dict[str, dict[str, QuoteRecord]] = {}

        def _has_price(coin: str) -> str | None:
            for base in price_coin_aliases(coin):
                if f"{base}USDT" in prices or f"{base}USDC" in prices:
                    return base
            return None

        for asset in assets:
            coin = asset.coin.upper()
            is_stable = asset.coin in STABLECOIN_WHITELIST
            price_coin = coin if is_stable else _has_price(coin)
            if not is_stable and price_coin is None:
                # Not a stablecoin and not priceable -> cannot be a quote.
                continue

            for network_asset in asset.active_networks():
                network = network_asset.network_normalized
                address = network_asset.contract_address.lower()
                if not address:
                    continue
                net_map = registry.setdefault(network, {})
                # Stablecoins take precedence; do not overwrite with a
                # non-stable duplicate at the same address.
                if address in net_map and net_map[address].is_stable:
                    continue
                net_map[address] = QuoteRecord(
                    coin=asset.coin,
                    network=network,
                    address=address,
                    is_stable=is_stable,
                    price_coin=price_coin or coin,
                    withdraw_fee=network_asset.withdraw_fee,
                    deposit_enable=network_asset.deposit_enable,
                    withdraw_enable=network_asset.withdraw_enable,
                )

        # Wrapped-native / base tokens (not returned as MEXC contracts).
        for network in list(registry.keys()) + [
            n for n in self._all_wrapped_networks() if n not in registry
        ]:
            wrapped = wrapped_natives_for_network(network)
            if not wrapped:
                continue
            net_map = registry.setdefault(network, {})
            for address, coin in wrapped.items():
                addr = address.lower()
                if addr in net_map:
                    continue
                price_coin = self._first_priced(coin, prices)
                if price_coin is None:
                    continue
                net_map[addr] = QuoteRecord(
                    coin=coin,
                    network=network,
                    address=addr,
                    is_stable=False,
                    price_coin=price_coin,
                    withdraw_fee=None,
                    deposit_enable=True,
                    withdraw_enable=True,
                )

        total = sum(len(v) for v in registry.values())
        logger.info(
            "quote_registry_built: networks=%d quotes=%d",
            len(registry),
            total,
        )
        return registry

    @staticmethod
    def _all_wrapped_networks() -> list[str]:
        from config.wrapped_natives import WRAPPED_NATIVE_QUOTES
        return list(WRAPPED_NATIVE_QUOTES.keys())

    @staticmethod
    def _first_priced(coin: str, prices) -> str | None:
        for base in price_coin_aliases(coin):
            if f"{base}USDT" in prices or f"{base}USDC" in prices:
                return base
        return None

    def quote_addresses_for_network(
        self,
        quote_registry: dict[str, dict[str, QuoteRecord]],
        network: str,
    ) -> set[str]:
        """Return the set of lowercased quote addresses for a network."""
        return set(quote_registry.get(network, {}).keys())

    def quote_records_for_network(
        self,
        quote_registry: dict[str, dict[str, QuoteRecord]],
        network: str,
    ) -> dict[str, QuoteRecord]:
        """Return {address_lower: QuoteRecord} for a network."""
        return quote_registry.get(network, {})
