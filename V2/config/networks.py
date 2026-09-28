"""
Network definitions and RPC URL resolution.

Uses Settings to build RPC URLs with Alchemy key or public fallbacks.
"""

from dataclasses import dataclass

from config.settings import settings


@dataclass(frozen=True)
class NetworkConfig:
    internal_name: str
    chain_id: int | None
    mexc_network_names: tuple[str, ...]
    native_token: str
    rpc_url_env: str
    block_explorer: str
    multicall3: str | None = None
    l2_multicall: str | None = None
    is_evm: bool = True
    notes: str = ""


NETWORKS: dict[str, NetworkConfig] = {
    "ETHEREUM": NetworkConfig(
        internal_name="ETHEREUM",
        chain_id=1,
        mexc_network_names=("ETH", "ERC20", "ETHEREUM"),
        native_token="ETH",
        rpc_url_env="ETH_RPC_URL",
        block_explorer="https://etherscan.io",
        multicall3="0xcA11bde05977b3631167028862bE2a173976CA11",
        notes="Multicall3 canonical address.",
    ),
    "BSC": NetworkConfig(
        internal_name="BSC",
        chain_id=56,
        mexc_network_names=("BSC", "BEP20", "BNB", "BNBCHAIN"),
        native_token="BNB",
        rpc_url_env="BSC_RPC_URL",
        block_explorer="https://bscscan.com",
        multicall3="0xcA11bde05977b3631167028862bE2a173976CA11",
    ),
    "POLYGON": NetworkConfig(
        internal_name="POLYGON",
        chain_id=137,
        mexc_network_names=("MATIC", "POLYGON", "POLYGONPOS"),
        native_token="POL",
        rpc_url_env="POLYGON_RPC_URL",
        block_explorer="https://polygonscan.com",
        multicall3="0xcA11bde05977b3631167028862bE2a173976CA11",
    ),
    "ARBITRUM": NetworkConfig(
        internal_name="ARBITRUM",
        chain_id=42161,
        mexc_network_names=("ARB", "ARBITRUM", "ARBITRUMONE"),
        native_token="ETH",
        rpc_url_env="ARBITRUM_RPC_URL",
        block_explorer="https://arbiscan.io",
        multicall3="0xcA11bde05977b3631167028862bE2a173976CA11",
    ),
    "BASE": NetworkConfig(
        internal_name="BASE",
        chain_id=8453,
        mexc_network_names=("BASE",),
        native_token="ETH",
        rpc_url_env="BASE_RPC_URL",
        block_explorer="https://basescan.org",
        multicall3="0xcA11bde05977b3631167028862bE2a173976CA11",
        notes="Coinbase Base L2. MEXC network name is literally BASE.",
    ),
    "ROBINHOOD": NetworkConfig(
        internal_name="ROBINHOOD",
        chain_id=4663,
        mexc_network_names=("ROBINHOOD", "ROBINHOODCHAIN"),
        native_token="ETH",
        rpc_url_env="ROBINHOOD_RPC_URL",
        block_explorer="https://robinhoodchain.blockscout.com",
        multicall3=None,
        l2_multicall="0x2cAC2D899eCC914d704FeaAE33ac1bF36277DaD1",
        notes="Robinhood Chain mainnet chain id is 4663.",
    ),
}

# Runtime whitelist: only these networks are scanned/refreshed.
# ARBITRUM replaced by BASE (more exclusive MEXC coins, cheaper gas).
# ETHEREUM/POLYGON/ROBINHOOD/ARBITRUM stay defined but inactive.
ACTIVE_NETWORKS: frozenset[str] = frozenset({"BSC", "BASE"})


def is_network_active(network: str) -> bool:
    """True if the network participates in the runtime scan/refresh cycle."""
    return bool(network) and network.upper() in ACTIVE_NETWORKS

# Approximate block times, used to convert MEXC min_confirm into a
# deposit ETA for dynamic watcher expiry (plan v3 D8).
BLOCK_TIME_SEC: dict[str, float] = {
    "ETHEREUM": 12.0,
    "BSC": 3.0,
    "POLYGON": 2.0,
    "ARBITRUM": 0.25,
    "BASE": 2.0,
    "ROBINHOOD": 2.0,
}

# Alchemy-based default RPC templates.
ALCHEMY_TEMPLATES: dict[str, str] = {
    "ETHEREUM": "https://eth-mainnet.g.alchemy.com/v2/{key}",
    "BSC": "https://bnb-mainnet.g.alchemy.com/v2/{key}",
    "POLYGON": "https://polygon-mainnet.g.alchemy.com/v2/{key}",
    "ARBITRUM": "https://arb-mainnet.g.alchemy.com/v2/{key}",
    "BASE": "https://base-mainnet.g.alchemy.com/v2/{key}",
}

# Public fallback RPC URLs.
PUBLIC_FALLBACK_RPCS: dict[str, str] = {
    "ETHEREUM": "https://eth.llamarpc.com",
    "BSC": "https://bsc-dataseed.binance.org",
    "POLYGON": "https://polygon-rpc.com",
    "ARBITRUM": "https://arb1.arbitrum.io/rpc",
    "BASE": "https://mainnet.base.org",
    "ROBINHOOD": "https://rpc.mainnet.chain.robinhood.com",
}


def resolve_rpc_url(network_name: str) -> str | None:
    """
    Resolve RPC URL for a network.

    Priority:
    1. Explicit env var (e.g. ETH_RPC_URL)
    2. Alchemy template (if ALCHEMY_KEY is set)
    3. Public fallback RPC
    """
    network_config = NETWORKS.get(network_name)
    if network_config is None:
        return None

    # 1. Check explicit env var.
    env_value = getattr(settings, network_config.rpc_url_env, None)
    if env_value and isinstance(env_value, str) and env_value.strip():
        return env_value.strip()

    # 2. Try Alchemy template.
    if settings.alchemy_key_present and network_name in ALCHEMY_TEMPLATES:
        return ALCHEMY_TEMPLATES[network_name].format(key=settings.ALCHEMY_KEY)

    # 3. Public fallback.
    return PUBLIC_FALLBACK_RPCS.get(network_name)


def resolve_rpc_urls(network_name: str) -> list[str]:
    """
    Resolve ALL available RPC URLs for a network (one per Alchemy key).

    Returns list of URLs for round-robin load balancing.
    """
    network_config = NETWORKS.get(network_name)
    if network_config is None:
        return []

    # 1. Check explicit env var (single URL).
    env_value = getattr(settings, network_config.rpc_url_env, None)
    if env_value and isinstance(env_value, str) and env_value.strip():
        return [env_value.strip()]

    # 2. Alchemy templates with ALL keys.
    if network_name in ALCHEMY_TEMPLATES and settings.ALCHEMY_KEYS:
        return [
            ALCHEMY_TEMPLATES[network_name].format(key=k)
            for k in settings.ALCHEMY_KEYS
        ]

    # 3. Public fallback.
    fallback = PUBLIC_FALLBACK_RPCS.get(network_name)
    return [fallback] if fallback else []


def get_native_token_price(network: str) -> str:
    """
    Return the MEXC symbol for native token price.
    """
    config = NETWORKS.get(network)
    if config is None:
        return ""
    return f"{config.native_token}USDT"


def _extract_mexc_network_tokens(raw_network: str) -> list[str]:
    """
    Extract candidate tokens from a raw MEXC network name.

    MEXC returns network names in various formats:
      "ETH", "ERC20", "BSC", "BEP20" — direct names
      "Ethereum(ERC20)" — human name with tech name in parentheses
      "BNB Smart Chain(BEP20)" — multi-word with tech name in parentheses
      "Polygon(MATIC)" — human name with tech name
      "Arbitrum(ARB)" — human name with tech name
      "ARBITRUMONE" — direct name

    Returns:
        List of normalized candidate strings to match against.
    """
    if not raw_network:
        return []

    stripped = raw_network.strip()
    candidates: list[str] = []

    # 1. Normalize the full string: uppercase, remove spaces, dashes, underscores.
    full = stripped.upper().replace("-", "").replace("_", "").replace(" ", "")
    candidates.append(full)

    # 2. Remove common non-essential words for matching.
    for word in ("SMARTCHAIN", "CHAIN", "NETWORK", "MAINNET"):
        cleaned = full.replace(word, "")
        if cleaned != full and cleaned:
            candidates.append(cleaned)

    # 3. Extract text inside parentheses (e.g. "ERC20" from "Ethereum(ERC20)").
    if "(" in stripped and stripped.endswith(")"):
        paren_content = stripped[stripped.index("(") + 1 : -1]
        if paren_content:
            paren_normalized = paren_content.strip().upper()
            candidates.append(paren_normalized)
            # Also try without spaces.
            candidates.append(paren_normalized.replace(" ", ""))

        # 4. Also try the text before parentheses (e.g. "ETHEREUM").
        before_paren = stripped[: stripped.index("(")].strip().upper()
        if before_paren:
            candidates.append(before_paren.replace("-", "").replace("_", "").replace(" ", ""))

    # 5. Handle special case: "BNB Smart Chain" -> "BNB"
    for token in ("BNB", "ERC", "BEP", "MATIC", "ARB", "POLYGON", "ARBITRUM", "BASE"):
        if token in full:
            candidates.append(token)

    return candidates


def normalize_mexc_network(raw_network: str) -> str | None:
    """
    Normalize MEXC network name to internal network name.

    Handles MEXC formats like "Ethereum(ERC20)", "BNB Smart Chain(BEP20)",
    "Polygon(MATIC)", "Arbitrum(ARB)", and direct names like "ETH", "BSC".

    Example:
      ETH -> ETHEREUM
      ERC20 -> ETHEREUM
      Ethereum(ERC20) -> ETHEREUM
      BSC -> BSC
      BEP20 -> BSC
      BNB Smart Chain(BEP20) -> BSC
      MATIC -> POLYGON
      Polygon(MATIC) -> POLYGON
      ARB -> ARBITRUM
      Arbitrum(ARB) -> ARBITRUM
      ROBINHOOD -> ROBINHOOD
      Solana(SOL) -> None (not supported)
    """
    if not raw_network:
        return None

    candidates = _extract_mexc_network_tokens(raw_network)

    for candidate in candidates:
        for internal_name, network in NETWORKS.items():
            if candidate in network.mexc_network_names:
                return internal_name

    return None
