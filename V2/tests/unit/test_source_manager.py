"""Unit tests for SourceManager discovery and on-chain fallback."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from discovery.source_manager import SourceManager


@pytest.fixture
def mock_sources():
    """Create mock discovery sources."""
    return {
        "dexscreener": MagicMock(
            is_available=MagicMock(return_value=True),
            fetch_pools_by_token=AsyncMock(return_value=[]),
        ),
        "geckoterminal": MagicMock(
            is_available=MagicMock(return_value=True),
            fetch_pools_by_token=AsyncMock(return_value=[]),
        ),
        "onchain_factory": MagicMock(
            is_available=MagicMock(return_value=True),
            fetch_pools_by_token=AsyncMock(return_value=[]),
        ),
    }


@pytest.fixture
def source_manager(mock_sources):
    """SourceManager with mock sources."""
    return SourceManager(sources=mock_sources)


class TestSourceManager:
    """Tests for SourceManager discover_from_all_sources."""

    @pytest.mark.asyncio
    async def test_primary_sources_found_pools(self, source_manager, mock_sources):
        """When primary sources find pools, onchain fallback is NOT called."""
        mock_sources["dexscreener"].fetch_pools_by_token = AsyncMock(return_value=[
            MagicMock(pool_address="0xabc", sources={"dexscreener"}),
        ])

        result = await source_manager.discover_from_all_sources(
            network="ETHEREUM", token_address="0xtoken",
        )

        assert len(result) == 1
        # Onchain should NOT have been called since primary returned pools.
        mock_sources["onchain_factory"].fetch_pools_by_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_primary_sources_empty_fallback_to_onchain(self, source_manager, mock_sources):
        """When primary sources return empty, onchain fallback IS called."""
        mock_sources["dexscreener"].fetch_pools_by_token = AsyncMock(return_value=[])
        mock_sources["geckoterminal"].fetch_pools_by_token = AsyncMock(return_value=[])
        mock_sources["onchain_factory"].fetch_pools_by_token = AsyncMock(return_value=[
            MagicMock(pool_address="0xdef", sources={"onchain_factory"}),
        ])

        result = await source_manager.discover_from_all_sources(
            network="ETHEREUM", token_address="0xtoken",
        )

        assert len(result) == 1
        mock_sources["onchain_factory"].fetch_pools_by_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_sources_return_empty(self, source_manager, mock_sources):
        """When all sources return empty, result is empty list."""
        mock_sources["dexscreener"].fetch_pools_by_token = AsyncMock(return_value=[])
        mock_sources["geckoterminal"].fetch_pools_by_token = AsyncMock(return_value=[])
        mock_sources["onchain_factory"].fetch_pools_by_token = AsyncMock(return_value=[])

        result = await source_manager.discover_from_all_sources(
            network="ETHEREUM", token_address="0xtoken",
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_onchain_unavailable_skipped(self, source_manager, mock_sources):
        """When onchain source is unavailable, it's skipped."""
        mock_sources["dexscreener"].fetch_pools_by_token = AsyncMock(return_value=[])
        mock_sources["geckoterminal"].fetch_pools_by_token = AsyncMock(return_value=[])
        mock_sources["onchain_factory"].is_available = MagicMock(return_value=False)

        result = await source_manager.discover_from_all_sources(
            network="ETHEREUM", token_address="0xtoken",
        )

        assert result == []
        mock_sources["onchain_factory"].fetch_pools_by_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_primary_failure_triggers_onchain(self, source_manager, mock_sources):
        """When primary source fails with error, onchain fallback is tried."""
        mock_sources["dexscreener"].fetch_pools_by_token = AsyncMock(
            side_effect=Exception("API timeout")
        )
        mock_sources["geckoterminal"].fetch_pools_by_token = AsyncMock(return_value=[])
        mock_sources["onchain_factory"].fetch_pools_by_token = AsyncMock(return_value=[
            MagicMock(pool_address="0xabc", sources={"onchain_factory"}),
        ])

        result = await source_manager.discover_from_all_sources(
            network="ETHEREUM", token_address="0xtoken",
        )

        assert len(result) == 1
        mock_sources["onchain_factory"].fetch_pools_by_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_both_phases_fail_returns_empty(self, source_manager, mock_sources):
        """When primary fails and onchain also fails, result is empty."""
        mock_sources["dexscreener"].fetch_pools_by_token = AsyncMock(
            side_effect=Exception("API timeout")
        )
        mock_sources["geckoterminal"].fetch_pools_by_token = AsyncMock(
            side_effect=Exception("rate limited")
        )
        mock_sources["onchain_factory"].fetch_pools_by_token = AsyncMock(
            side_effect=Exception("RPC down")
        )

        result = await source_manager.discover_from_all_sources(
            network="ETHEREUM", token_address="0xtoken",
        )

        assert result == []
