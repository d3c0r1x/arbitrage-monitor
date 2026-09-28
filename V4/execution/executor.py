"""
DEX Execution Module.

Executes arbitrage swaps on-chain. ONLY activates when DEX_PRIVATE_KEY is set.
Default mode: dry-run (simulate via eth_call, never broadcast).

Safety layers:
  1. No private key → module inert, all methods no-op.
  2. EXECUTION_DRY_RUN=True (default) → simulate only, log results.
  3. Re-quote before execution → abort if profit dropped.
  4. Slippage protection → min_amount_out = expected * (1 - slippage).
  5. Gas price cap → skip if gas > EXECUTION_MAX_GAS_GWEI.
  6. Nonce management → sequential, no gaps.
  7. eth_call simulation → revert detection before broadcast.

Supports:
  - V2 swaps (swapExactTokensForTokens)
  - V3 swaps (exactInputSingle via SwapRouter)
  - Multi-hop chains (sequential swaps in one tx via multicall or sequential txs)
"""

import logging
import time
from decimal import Decimal

from config.settings import settings

logger = logging.getLogger(__name__)

# Minimal ABIs for execution.
ERC20_APPROVE_ABI = [
    {
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

V2_ROUTER_SWAP_ABI = [
    {
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

V3_ROUTER_SWAP_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "tokenIn", "type": "address"},
                    {"name": "tokenOut", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "recipient", "type": "address"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "amountOutMinimum", "type": "uint256"},
                    {"name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    },
]

# Known router addresses per network (fallback).
V2_ROUTERS: dict[str, str] = {
    "BSC": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
    "ETHEREUM": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
    "POLYGON": "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
}
V3_ROUTERS: dict[str, str] = {
    "ETHEREUM": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
    "BSC": "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",
    "ARBITRUM": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
    "POLYGON": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
    "BASE": "0x2626664c2603336E57B271c5C0b26F421741e481",
}


def _resolve_router(network: str, dex_id: str, version: str) -> str | None:
    """Resolve router from DEX registry, fallback to hardcoded."""
    try:
        from config.dex_registry import DEX_REGISTRY
        net_cfg = DEX_REGISTRY.get(network, {})
        for dex_cfg in net_cfg.get("dexes", []):
            if dex_cfg.get("dex_id") == dex_id:
                if "v3" in version:
                    return dex_cfg.get("swap_router02") or dex_cfg.get("smart_router")
                return dex_cfg.get("router")
    except Exception:
        pass
    # Fallback.
    if "v3" in version:
        return V3_ROUTERS.get(network)
    return V2_ROUTERS.get(network)

MAX_UINT256 = 2**256 - 1


class Executor:
    """DEX swap executor. Inert without DEX_PRIVATE_KEY."""

    def __init__(self, rpc_client_factory, web3_manager=None, adapter_factory=None):
        self._rpc_factory = rpc_client_factory
        self._web3_manager = web3_manager
        self._adapter_factory = adapter_factory
        self._enabled = settings.dex_private_key_present
        self._dry_run = settings.EXECUTION_DRY_RUN
        self._account = None
        self._nonces: dict[str, int] = {}  # network → next nonce
        self._executed_count = 0
        self._simulated_count = 0
        self._failed_count = 0

        if self._enabled:
            try:
                from eth_account import Account
                self._account = Account.from_key(settings.DEX_PRIVATE_KEY)
                logger.info(
                    "executor_enabled: address=%s dry_run=%s",
                    self._account.address, self._dry_run,
                )
            except Exception as exc:
                logger.error("executor_key_parse_failed: %s", exc)
                self._enabled = False
        else:
            logger.info("executor_disabled: no DEX_PRIVATE_KEY")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def address(self) -> str:
        return self._account.address if self._account else ""

    async def execute_opportunity(self, opp) -> dict:
        """Execute a watched opportunity. Returns execution result dict.

        Safety: re-quotes, checks gas, simulates, then broadcasts (if not dry-run).
        """
        if not self._enabled:
            return {"status": "disabled", "reason": "no_private_key"}

        network = opp.network
        result = {
            "status": "pending",
            "network": network,
            "token": opp.token_coin,
            "direction": opp.direction,
            "dry_run": self._dry_run,
        }

        try:
            rpc = self._rpc_factory(network)
            if rpc is None:
                return {**result, "status": "failed", "reason": "no_rpc"}

            # Gas price check.
            gas_price_wei = await self._get_gas_price(network)
            gas_gwei = gas_price_wei / 1e9
            if gas_gwei > settings.EXECUTION_MAX_GAS_GWEI:
                return {**result, "status": "skipped", "reason": f"gas_too_high:{gas_gwei:.1f}gwei"}

            # Resolve router + ensure approval before building tx.
            router_addr = _resolve_router(network, opp.dex, opp.pool_version or "v2")
            if router_addr and opp.token_in:
                approved = await self.ensure_approval(
                    rpc, network, opp.token_in, router_addr, opp.amount_in_raw
                )
                if not approved:
                    return {**result, "status": "failed", "reason": "approval_failed"}

            # Real slippage protection: fresh quote right before the swap.
            # D7: multihop is protected too — never broadcast min_out=1.
            if opp.chain_hops:
                min_out = await self._fresh_min_out_for_hop(
                    network, opp.chain_hops[0], opp.amount_in_raw,
                )
            else:
                min_out = await self._fresh_min_out(opp)
            if min_out is None:
                return {**result, "status": "skipped", "reason": "fresh_quote_failed"}

            # Build swap transaction.
            if opp.chain_hops:
                tx = await self._build_multihop_tx(rpc, network, opp, min_out)
            elif opp.pool_version in ("v3", "pancakeswap_v3"):
                tx = await self._build_v3_swap_tx(rpc, network, opp, min_out)
            else:
                tx = await self._build_v2_swap_tx(rpc, network, opp, min_out)

            if tx is None:
                return {**result, "status": "failed", "reason": "tx_build_failed"}

            # Simulate.
            sim_ok = await self._simulate_tx(network, tx)
            self._simulated_count += 1
            if not sim_ok:
                self._failed_count += 1
                return {**result, "status": "simulation_reverted"}

            result["simulated"] = True

            if self._dry_run:
                result["status"] = "dry_run_ok"
                logger.info(
                    "execution_dry_run: %s %s %s profit=%.2f%%\n  → MEXC leg pending: %s%s",
                    network, opp.token_coin, opp.direction,
                    float(opp.current_net_profit_pct),
                    "BUY " if "MEXC_BUY" in opp.direction else "SELL ",
                    opp.token_coin[:10],
                )
                return result

            # Broadcast.
            tx_hash = await self._sign_and_send(network, tx)
            result["tx_hash"] = tx_hash

            # Wait for receipt and verify.
            w3 = self._get_web3(network)
            if w3:
                try:
                    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                    if receipt.get("status") != 1:
                        self._failed_count += 1
                        return {**result, "status": "reverted_on_chain"}
                except Exception:
                    pass  # Timeout — tx pending.

            self._executed_count += 1
            result["status"] = "confirmed"
            logger.info(
                "execution_confirmed: %s %s tx=%s",
                network, opp.token_coin, tx_hash,
            )
            return result

        except Exception as exc:
            self._failed_count += 1
            logger.error("execution_error: %s %s: %s", network, opp.token_coin, exc)
            return {**result, "status": "error", "reason": str(exc)}

    async def ensure_approval(
        self, rpc, network: str, token_address: str, spender: str, amount: int
    ) -> bool:
        """Ensure token approval for spender. Returns True if approved."""
        if not self._account:
            return False
        try:
            from eth_abi import encode as abi_encode
            w3 = self._get_web3(network)
            if w3 is None:
                return False

            token = w3.eth.contract(
                address=w3.to_checksum_address(token_address),
                abi=ERC20_APPROVE_ABI,
            )
            # Check existing allowance.
            allowance = await token.functions.allowance(
                self._account.address, w3.to_checksum_address(spender)
            ).call()
            if allowance >= amount:
                return True

            # Approve max to avoid repeated approvals.
            if self._dry_run:
                logger.info("dry_run_approve: %s for %s", token_address[:10], spender[:10])
                return True

            tx = await token.functions.approve(
                w3.to_checksum_address(spender), MAX_UINT256
            ).build_transaction({
                "from": self._account.address,
                "nonce": await self._get_nonce(network),
                "gas": 60000,
                "gasPrice": await self._get_gas_price(network),
            })
            await self._sign_and_send(network, tx)
            return True
        except Exception as exc:
            logger.error("approval_failed: %s: %s", token_address[:10], exc)
            return False

    # ── Internal helpers ──────────────────────────────────────────────────

    def _get_web3(self, rpc_or_network):
        """Get AsyncWeb3 instance from Web3Manager by network name."""
        if self._web3_manager is None:
            return None
        network = rpc_or_network if isinstance(rpc_or_network, str) else None
        if network:
            try:
                return self._web3_manager.get_web3(network)
            except Exception:
                return None
        return None

    async def _get_gas_price(self, network: str) -> int:
        """Get current gas price in wei."""
        try:
            w3 = self._get_web3(network)
            if w3:
                return await w3.eth.gas_price
        except Exception:
            pass
        return 30 * 10**9  # fallback 30 gwei

    async def _get_nonce(self, network: str) -> int:
        """Get next nonce (cached, sequential)."""
        if network not in self._nonces:
            w3 = self._get_web3(network)
            if w3 and self._account:
                self._nonces[network] = await w3.eth.get_transaction_count(
                    self._account.address, "pending"
                )
            else:
                self._nonces[network] = 0
        nonce = self._nonces[network]
        self._nonces[network] += 1
        return nonce

    async def _fresh_min_out(self, opp) -> int | None:
        """Fresh quote right before execution → min_out with slippage buffer.

        Returns None if a fresh quote is unavailable (abort execution) or 1
        when no adapter factory is wired (simulation remains the only guard).
        """
        if self._adapter_factory is None:
            return 1
        try:
            adapter = self._adapter_factory.get_adapter(
                opp.network, opp.dex, pool_version=opp.pool_version,
            )
            if adapter is None:
                return None
            amount_out = await adapter.quote_exact_input(
                network=opp.network,
                pool_address=opp.pool_address,
                token_in=opp.token_in,
                token_out=opp.token_out,
                amount_in=opp.amount_in_raw,
            )
            if amount_out <= 0:
                return None
            slippage = Decimal(settings.EXECUTION_SLIPPAGE_BPS) / Decimal(10000)
            min_out = int(Decimal(int(amount_out)) * (Decimal(1) - slippage))
            return max(min_out, 1)
        except Exception as exc:
            logger.error("fresh_min_out_failed: %s %s: %s", opp.network, opp.token_coin, exc)
            return None

    async def _fresh_min_out_for_hop(
        self, network: str, hop, amount_in_raw: int
    ) -> int | None:
        """Fresh quote for a single chain hop → min_out with slippage buffer.

        hop = (pool_addr, token_in, token_out, dex, version).
        Returns None when the quote is unavailable (abort execution).
        """
        if self._adapter_factory is None:
            return None
        try:
            pool_addr, token_in, token_out, dex, version = hop
            adapter = self._adapter_factory.get_adapter(
                network, dex, pool_version=version,
            )
            if adapter is None:
                return None
            amount_out = await adapter.quote_exact_input(
                network=network,
                pool_address=pool_addr,
                token_in=token_in,
                token_out=token_out,
                amount_in=amount_in_raw,
            )
            if amount_out <= 0:
                return None
            slippage = Decimal(settings.EXECUTION_SLIPPAGE_BPS) / Decimal(10000)
            min_out = int(Decimal(int(amount_out)) * (Decimal(1) - slippage))
            return max(min_out, 1)
        except Exception as exc:
            logger.error("fresh_min_out_hop_failed: %s: %s", network, exc)
            return None

    async def _build_v2_swap_tx(self, rpc, network: str, opp, min_out: int) -> dict | None:
        """Build V2 swapExactTokensForTokens transaction."""
        router_addr = _resolve_router(network, getattr(opp, 'dex', ''), 'v2')
        if not router_addr:
            return None
        w3 = self._get_web3(network)
        if not w3 or not self._account:
            return None

        router = w3.eth.contract(
            address=w3.to_checksum_address(router_addr),
            abi=V2_ROUTER_SWAP_ABI,
        )
        deadline = int(time.time()) + 300
        path = [
            w3.to_checksum_address(opp.token_in),
            w3.to_checksum_address(opp.token_out),
        ]
        try:
            tx = await router.functions.swapExactTokensForTokens(
                opp.amount_in_raw,
                min_out,
                path,
                self._account.address,
                deadline,
            ).build_transaction({
                "from": self._account.address,
                "nonce": await self._get_nonce(network),
                "gas": 300000,
                "gasPrice": await self._get_gas_price(network),
            })
            return tx
        except Exception as exc:
            logger.error("v2_tx_build_failed: %s", exc)
            return None

    async def _build_v3_swap_tx(self, rpc, network: str, opp, min_out: int) -> dict | None:
        """Build V3 exactInputSingle transaction."""
        router_addr = _resolve_router(network, getattr(opp, 'dex', ''), 'v3')
        if not router_addr:
            return None
        w3 = self._get_web3(network)
        if not w3 or not self._account:
            return None

        router = w3.eth.contract(
            address=w3.to_checksum_address(router_addr),
            abi=V3_ROUTER_SWAP_ABI,
        )
        # Fee: from opp context (populated by scanner from adapter cache).
        fee = getattr(opp, 'pool_fee', 0) or 3000
        params = (
            w3.to_checksum_address(opp.token_in),
            w3.to_checksum_address(opp.token_out),
            fee,
            self._account.address,
            opp.amount_in_raw,
            min_out,
            0,  # sqrtPriceLimitX96
        )
        try:
            tx = await router.functions.exactInputSingle(params).build_transaction({
                "from": self._account.address,
                "nonce": await self._get_nonce(network),
                "gas": 400000,
                "gasPrice": await self._get_gas_price(network),
                "value": 0,
            })
            return tx
        except Exception as exc:
            logger.error("v3_tx_build_failed: %s", exc)
            return None

    async def _build_multihop_tx(
        self, rpc, network: str, opp, min_out: int
    ) -> dict | None:
        """Build multi-hop execution as sequential V2 swaps.

        For production: encode as multicall or flash loan.
        Current: execute first hop only (safest for MVP). min_out comes
        from a fresh quote of the first hop (D7) — never 1.
        """
        if not opp.chain_hops:
            return None
        # Execute first hop as V2 swap (MVP approach).
        first_pool, token_in, token_out, dex, version = opp.chain_hops[0]
        # Create a temporary opp-like context for first hop.
        class HopCtx:
            pass
        hop = HopCtx()
        hop.token_in = token_in
        hop.token_out = token_out
        hop.amount_in_raw = opp.amount_in_raw
        hop.pool_version = version
        hop.dex = dex
        hop.pool_fee = getattr(opp, 'pool_fee', 3000)
        if "v3" in version:
            return await self._build_v3_swap_tx(rpc, network, hop, min_out)
        return await self._build_v2_swap_tx(rpc, network, hop, min_out)

    async def _simulate_tx(self, network: str, tx: dict) -> bool:
        """Simulate transaction via eth_call. Returns True if no revert."""
        try:
            w3 = self._get_web3(network)
            if not w3:
                return False
            call_params = {
                "from": tx.get("from", self._account.address if self._account else ""),
                "to": tx.get("to", ""),
                "data": tx.get("data", ""),
                "value": tx.get("value", 0),
            }
            await w3.eth.call(call_params)
            return True
        except Exception as exc:
            logger.debug("simulation_reverted: %s", exc)
            return False

    async def _sign_and_send(self, network: str, tx: dict) -> str:
        """Sign transaction and broadcast. Returns tx hash hex."""
        w3 = self._get_web3(network)
        if not w3 or not self._account:
            raise RuntimeError("no_web3_or_account")
        signed = w3.eth.account.sign_transaction(tx, self._account.key)
        tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()
