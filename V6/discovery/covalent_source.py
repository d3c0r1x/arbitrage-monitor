"""
Covalent API pool discovery source (optional).

Requires COVALENT_API_KEY env var.
"""

import logging

import httpx

from config.settings import settings
from discovery.base_source import BaseSource, SourceError
from models.pool_models import DiscoveredPool

logger = logging.getLogger(__name__)


class CovalentSource(BaseSource):
    """Optional pool discovery via Covalent API."""

    source_name: str = "covalent"

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    def is_available(self) -> bool:
        return bool(settings.COVALENT_API_KEY)

    async def fetch_pools_by_token(
        self,
        network: str,
        token_address: str,
    ) -> list[DiscoveredPool]:
        raise SourceError("Covalent source not yet implemented")
