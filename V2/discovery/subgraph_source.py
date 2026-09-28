"""
Subgraph GraphQL pool discovery source (optional).

Requires SUBGRAPH_URL env var.
"""

import logging

import httpx

from config.settings import settings
from discovery.base_source import BaseSource, SourceError
from models.pool_models import DiscoveredPool

logger = logging.getLogger(__name__)


class SubgraphSource(BaseSource):
    """Optional pool discovery via GraphQL subgraph."""

    source_name: str = "subgraph"

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    def is_available(self) -> bool:
        return bool(settings.SUBGRAPH_URL)

    async def fetch_pools_by_token(
        self,
        network: str,
        token_address: str,
    ) -> list[DiscoveredPool]:
        raise SourceError("Subgraph source not yet implemented")
