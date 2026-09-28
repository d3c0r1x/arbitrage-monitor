"""
MEXC API response models.

All monetary fields use Decimal.
No float money fields.
"""

from decimal import Decimal

from pydantic import BaseModel, Field


class MexcNetworkAsset(BaseModel):
    coin: str
    name: str | None
    network_raw: str
    network_normalized: str
    contract_address: str
    deposit_enable: bool
    withdraw_enable: bool
    withdraw_fee: Decimal | None
    withdraw_min: Decimal | None
    withdraw_max: Decimal | None
    min_confirm: int | None


class MexcAsset(BaseModel):
    coin: str
    name: str | None
    networks: list[MexcNetworkAsset] = Field(default_factory=list)

    def active_networks(self) -> list[MexcNetworkAsset]:
        """Return networks where both deposit and withdraw are enabled."""
        return [
            network
            for network in self.networks
            if network.deposit_enable and network.withdraw_enable
        ]

    def tradable_networks(self) -> list[MexcNetworkAsset]:
        """Return networks where deposit OR withdraw is enabled.

        Direction A (DEX buy -> MEXC sell) only needs deposit;
        direction B (MEXC buy -> DEX sell) only needs withdraw.
        A token is arbitrageable if at least one path is open.
        """
        return [
            network
            for network in self.networks
            if network.deposit_enable or network.withdraw_enable
        ]


class MexcPrice(BaseModel):
    symbol: str
    base_asset: str
    quote_asset: str
    price: Decimal
    updated_at: float
