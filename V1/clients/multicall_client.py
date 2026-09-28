"""
Multicall client using Multicall3 tryAggregate.

Batches multiple eth_call requests into a single RPC call using
the canonical Multicall3 contract deployed on every EVM chain.

Multicall3 address (canonical): 0xcA11bde05977b3631167028862bE2a173976CA11
Robinhood L2 Multicall: 0x2cAC2D899eCC914d704FeaAE33ac1bF36277DaD1
"""

import logging
from collections.abc import Callable

from eth_utils import to_bytes, to_hex
from web3 import Web3

logger = logging.getLogger(__name__)

# Minimal ABI for Multicall3.tryAggregate
# function tryAggregate(bool requireSuccess, (address target, bytes callData)[] calldata calls)
#   external payable returns ((bool success, bytes returnData)[]);
MULTICALL3_ABI = [
    {
        "inputs": [
            {"internalType": "bool", "name": "requireSuccess", "type": "bool"},
            {
                "components": [
                    {"internalType": "address", "name": "target", "type": "address"},
                    {"internalType": "bytes", "name": "callData", "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Call[]",
                "name": "calls",
                "type": "tuple[]",
            },
        ],
        "name": "tryAggregate",
        "outputs": [
            {
                "components": [
                    {"internalType": "bool", "name": "success", "type": "bool"},
                    {"internalType": "bytes", "name": "returnData", "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Result[]",
                "name": "returnData",
                "type": "tuple[]",
            },
        ],
        "stateMutability": "payable",
        "type": "function",
    },
]


class MulticallClient:
    """Batches eth_calls into a single RPC call via Multicall3 tryAggregate.

    One failed call does not block other calls in the batch.
    """

    def __init__(self, rpc_client_factory: Callable):
        self._rpc_factory = rpc_client_factory
        self._batch_size = 50

    async def try_aggregate(
        self,
        network: str,
        calls: list[tuple[str, str]],
        block: str = "latest",
    ) -> list[tuple[bool, str]]:
        """Execute multiple eth_calls via Multicall3 tryAggregate.

        Args:
            network: Internal network name (e.g. "ETHEREUM").
            calls: List of (to_address, calldata) tuples.
            block: Block number or tag (default "latest").

        Returns:
            List of (success, return_data_hex) tuples, one per call.
            success=False if the individual call reverted.
        """
        if not calls:
            return []

        multicall_addr = self._resolve_multicall_address(network)
        if multicall_addr is None:
            logger.warning("multicall: no address for network %s, falling back to sequential", network)
            return await self._sequential_fallback(network, calls, block)

        rpc = self._rpc_factory(network)
        if rpc is None:
            return [(False, "0x") for _ in calls]

        results: list[tuple[bool, str]] = []

        for i in range(0, len(calls), self._batch_size):
            batch = calls[i : i + self._batch_size]
            batch_results = await self._execute_batch(rpc, multicall_addr, batch, block)
            results.extend(batch_results)

        return results

    def _resolve_multicall_address(self, network: str) -> str | None:
        """Resolve the multicast address for a given network."""
        from config.networks import NETWORKS

        config = NETWORKS.get(network)
        if config is None:
            return None
        # Prefer L2 multicall for Robinhood, fall back to canonical.
        return config.l2_multicall or config.multicall3

    async def _execute_batch(
        self,
        rpc,
        multicall_addr: str,
        calls: list[tuple[str, str]],
        block: str,
    ) -> list[tuple[bool, str]]:
        """Execute one batch via a single eth_call to Multicall3.tryAggregate."""
        try:
            # Build tryAggregate calldata using Web3 contract encoding.
            w3 = Web3()
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(multicall_addr),
                abi=MULTICALL3_ABI,
            )

            # Encode calls as [(address, bytes), ...]
            encoded_calls = [
                (
                    Web3.to_checksum_address(to_addr),
                    to_bytes(hexstr=calldata),
                )
                for to_addr, calldata in calls
            ]

            # web3 v7 renamed encodeABI -> encode_abi.
            encode_fn = getattr(contract, "encode_abi", None) or getattr(
                contract, "encodeABI"
            )
            try:
                data = encode_fn(abi_element_identifier="tryAggregate", args=[False, encoded_calls])
            except TypeError:
                data = encode_fn(fn_name="tryAggregate", args=[False, encoded_calls])
            result_hex = await rpc.eth_call(to=multicall_addr, data=data, block=block)

            # Decode Result[] {bool success; bytes returnData} via eth_abi
            # (web3 v7 Contract has no decode_function_result).
            from eth_abi import decode as abi_decode

            raw_results = abi_decode(
                ["(bool,bytes)[]"],
                to_bytes(hexstr=result_hex),
            )[0]

            return [
                (success, to_hex(ret_data) if ret_data and ret_data != b"" else "0x")
                for success, ret_data in raw_results
            ]

        except Exception as exc:
            logger.warning("multicall_batch_failed: %s", exc)
            return [(False, "0x") for _ in calls]

    async def _sequential_fallback(
        self,
        network: str,
        calls: list[tuple[str, str]],
        block: str,
    ) -> list[tuple[bool, str]]:
        """Fallback: execute calls one by one when multicall is unavailable."""
        rpc = self._rpc_factory(network)
        if rpc is None:
            return [(False, "0x") for _ in calls]

        results: list[tuple[bool, str]] = []
        for to_addr, calldata in calls:
            try:
                result = await rpc.eth_call(to=to_addr, data=calldata, block=block)
                results.append((True, result))
            except Exception:
                results.append((False, "0x"))
        return results
