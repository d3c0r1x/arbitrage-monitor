"""
Pool discovery source priority and configuration.

Defines the priority order, whether each source is enabled by default,
and what env vars are required.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceConfig:
    name: str
    priority: int
    enabled_by_default: bool
    required_env_vars: tuple[str, ...] = ()


SOURCE_PRIORITY_TABLE: list[SourceConfig] = [
    SourceConfig(name="dexscreener", priority=1, enabled_by_default=True),
    # Gecko free tier 429s stall full refresh; keep off unless explicitly in POOL_SOURCE_PRIORITY.
    SourceConfig(name="geckoterminal", priority=2, enabled_by_default=False),
    SourceConfig(name="onchain_factory", priority=3, enabled_by_default=True),
    SourceConfig(name="defined", priority=4, enabled_by_default=False, required_env_vars=("DEFINED_API_KEY",)),
    SourceConfig(name="dextools", priority=5, enabled_by_default=False, required_env_vars=("DEXTOOLS_API_KEY",)),
    SourceConfig(name="birdeye", priority=6, enabled_by_default=False, required_env_vars=("BIRDEYE_API_KEY",)),
    SourceConfig(name="moralis", priority=7, enabled_by_default=False, required_env_vars=("MORALIS_API_KEY",)),
    SourceConfig(name="covalent", priority=8, enabled_by_default=False, required_env_vars=("COVALENT_API_KEY",)),
    SourceConfig(name="subgraph", priority=9, enabled_by_default=False, required_env_vars=("SUBGRAPH_URL",)),
]


def get_source_names_by_priority() -> list[str]:
    """Return source names sorted by priority."""
    return [sc.name for sc in sorted(SOURCE_PRIORITY_TABLE, key=lambda x: x.priority)]


def is_source_enabled_by_default(name: str) -> bool:
    """Check if a source is enabled by default."""
    for sc in SOURCE_PRIORITY_TABLE:
        if sc.name == name:
            return sc.enabled_by_default
    return False
