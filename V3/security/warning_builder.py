"""
Warning builder.

Aggregates warnings from token and pool security checkers.
Deduplicates and returns a consolidated list.
"""

import logging

logger = logging.getLogger(__name__)


class WarningBuilder:
    """Aggregates and deduplicates security warnings."""

    @staticmethod
    def build_warnings(
        token_warnings: list[str],
        pool_warnings: list[str],
    ) -> list[str]:
        """Merge and deduplicate warnings.

        Args:
            token_warnings: Warnings from TokenSecurityChecker.
            pool_warnings: Warnings from PoolSecurityChecker.

        Returns:
            Deduplicated sorted list of warning strings.
        """
        combined = set(token_warnings) | set(pool_warnings)
        return sorted(combined)
