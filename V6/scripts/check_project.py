"""
Project validation scripts.

G2: validate_dex_registry() — checks that all DEX addresses in the registry
actually have deployed bytecode on their respective chains.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.dex_registry import DEX_REGISTRY
from config.settings import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Map internal network name → RPC URL from settings.
_NETWORK_RPC_MAP = {
    "ETHEREUM": settings.ETH_RPC_URL,
    "BSC": settings.BSC_RPC_URL,
    "POLYGON": settings.POLYGON_RPC_URL,
    "ARBITRUM": settings.ARBITRUM_RPC_URL,
    "BASE": settings.BASE_RPC_URL,
    "ROBINHOOD": settings.ROBINHOOD_RPC_URL,
}


async def validate_dex_registry() -> dict[str, list[dict]]:
    """Validate all DEX registry addresses have deployed bytecode.

    Returns:
        Dict with 'valid' and 'invalid' lists of address records.
    """
    import httpx

    results: dict[str, list[dict]] = {"valid": [], "invalid": [], "skipped": []}

    async with httpx.AsyncClient(timeout=15) as client:
        for network_name, network_cfg in DEX_REGISTRY.items():
            rpc_url = _NETWORK_RPC_MAP.get(network_name, "")
            if not rpc_url:
                logger.warning(
                    "SKIP %s: no RPC URL configured", network_name
                )
                for dex in network_cfg.get("dexes", []):
                    results["skipped"].append({
                        "network": network_name,
                        "dex_id": dex.get("dex_id"),
                        "reason": "no_rpc_url",
                    })
                continue

            for dex in network_cfg.get("dexes", []):
                dex_id = dex.get("dex_id", "unknown")
                # Collect all address fields to validate.
                addresses_to_check: dict[str, str] = {}
                for key in ("factory", "router", "smart_router", "quoter_v2",
                            "nonfungible_position_manager"):
                    addr = dex.get(key)
                    if addr:
                        addresses_to_check[key] = addr

                for role, address in addresses_to_check.items():
                    try:
                        # eth_getCode JSON-RPC call.
                        resp = await client.post(
                            rpc_url,
                            json={
                                "jsonrpc": "2.0",
                                "method": "eth_getCode",
                                "params": [address, "latest"],
                                "id": 1,
                            },
                        )
                        data = resp.json()
                        code = data.get("result", "0x")
                        has_code = code != "0x" and len(code) > 2

                        record = {
                            "network": network_name,
                            "dex_id": dex_id,
                            "role": role,
                            "address": address,
                            "has_code": has_code,
                        }

                        if has_code:
                            results["valid"].append(record)
                            logger.info(
                                "OK %s/%s/%s %s", network_name, dex_id, role, address
                            )
                        else:
                            results["invalid"].append(record)
                            logger.warning(
                                "INVALID %s/%s/%s %s — no bytecode (TODO_VERIFY)",
                                network_name, dex_id, role, address,
                            )
                    except Exception as exc:
                        results["invalid"].append({
                            "network": network_name,
                            "dex_id": dex_id,
                            "role": role,
                            "address": address,
                            "error": str(exc),
                        })
                        logger.error(
                            "ERROR %s/%s/%s %s: %s",
                            network_name, dex_id, role, address, exc,
                        )

    # Summary.
    logger.info(
        "validate_dex_registry: valid=%d invalid=%d skipped=%d",
        len(results["valid"]),
        len(results["invalid"]),
        len(results["skipped"]),
    )
    return results


if __name__ == "__main__":
    results = asyncio.run(validate_dex_registry())
    if results["invalid"]:
        print("\n=== INVALID ADDRESSES (TODO_VERIFY) ===")
        for r in results["invalid"]:
            print(f"  {r['network']}/{r['dex_id']}/{r['role']}: {r['address']}")
        sys.exit(1)
    else:
        print("\nAll addresses valid.")
        sys.exit(0)
