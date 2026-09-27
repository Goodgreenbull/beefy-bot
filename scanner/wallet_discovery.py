"""Base-first discovery from verified swaps made by tracked public wallets.

For V2/V3, require a registered pool and a matching paid Swap. For Uniswap V4,
require a direct PoolManager output, net token receipt, paid quote and a
PoolManager Swap; the V4 pool ID is not independently linked to that output.
"""

from __future__ import annotations

from typing import Any
import re

import aiohttp

from .config import BASE_QUOTES, ScannerConfig
from .feeds import TRANSFER_TOPIC, _abi_words, _address_from_topic, _address_from_word
from .models import Candidate, normalise_address, utc_now
from .state import SQLiteState


V2_SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
V3_SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
V4_SWAP_TOPIC = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"
BASE_V4_POOL_MANAGER = "0x498581ff718922c3f8e6a244956af099b2652b2b"
FACTORY_SELECTOR = "0xc45a0155"
TOKEN0_SELECTOR = "0x0dfe1681"
TOKEN1_SELECTOR = "0xd21220a7"
FEE_SELECTOR = "0xddca3f43"
GET_PAIR_SELECTOR = "0xe6a43905"
GET_POOL_SELECTOR = "0x1698ee82"


def _word_address(address: str) -> str:
    return normalise_address(address).removeprefix("0x").rjust(64, "0")


def _signed_256(value: int) -> int:
    return value - (1 << 256) if value >= (1 << 255) else value


class WalletSwapDiscoveryFeed:
    """Find Base tokens acquired in verified V2/V3 or V4 swap transactions."""

    name = "wallet-swap-discovery:base"

    def __init__(self, config: ScannerConfig) -> None:
        self.config = config
        self.rpc_url = config.base_rpc_url

    def tracked_wallets(self, state: SQLiteState) -> list[str]:
        """Select configured and proven Base wallets without exposing addresses in health."""
        configured = sorted({
            normalise_address(wallet)
            for wallet in self.config.smart_wallets
            if re.fullmatch(r"0x[0-9a-f]{40}", normalise_address(wallet))
        })
        curated: set[str] = set()
        if self.config.auto_curate_smart_wallets:
            curated = state.curated_smart_wallets(
                self.config.smart_wallet_min_observations,
                self.config.smart_wallet_min_win_rate,
                self.config.smart_wallet_min_average_return,
                chain="base",
            )
        return (configured + sorted(curated - set(configured)))[:30]

    async def _rpc(self, session: aiohttp.ClientSession, method: str, params: list[Any]) -> Any:
        async with session.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        ) as response:
            if response.status >= 400:
                raise RuntimeError(f"wallet discovery RPC HTTP {response.status}")
            body = await response.json(content_type=None)
        if body.get("error"):
            raise RuntimeError(body["error"].get("message", "wallet discovery RPC error"))
        if body.get("result") is None:
            raise RuntimeError(f"wallet discovery RPC missing {method} result")
        return body["result"]

    async def _call(self, session: aiohttp.ClientSession, address: str, data: str) -> str:
        return await self._rpc(session, "eth_call", [{"to": address, "data": data}, "latest"])

    async def _verified_pool(
        self, session: aiohttp.ClientSession, pool: str, token: str, swap_topic: str
    ) -> tuple[int, int] | None:
        """Return (token index, quote index) only for an actual factory pool."""
        try:
            factory = _address_from_word(await self._call(session, pool, FACTORY_SELECTOR))
            if factory not in self.config.dex_factories.get("base", set()):
                return None
            token0 = _address_from_word(await self._call(session, pool, TOKEN0_SELECTOR))
            token1 = _address_from_word(await self._call(session, pool, TOKEN1_SELECTOR))
            if token0 == token1 or token not in {token0, token1}:
                return None
            quote = token1 if token0 == token else token0
            if quote not in self.config.quote_tokens.get("base", set()):
                return None
            if swap_topic == V3_SWAP_TOPIC:
                fee = int(await self._call(session, pool, FEE_SELECTOR), 16)
                data = GET_POOL_SELECTOR + _word_address(token0) + _word_address(token1) + f"{fee:064x}"
            else:
                data = GET_PAIR_SELECTOR + _word_address(token0) + _word_address(token1)
            registered = _address_from_word(await self._call(session, factory, data))
            if registered != pool:
                return None
            token_index = 0 if token == token0 else 1
            return token_index, 1 - token_index
        except (ValueError, TypeError, IndexError):
            return None  # A non-standard pool is not evidence of a verified buy.
        except RuntimeError as error:
            if "revert" in str(error).lower():
                return None  # Some contracts do not implement the pool ABI.
            raise  # Preserve the cursor on an RPC/provider failure.

    @staticmethod
    def _paid_swap(log: dict, token_index: int, quote_index: int) -> bool:
        topic = (log.get("topics") or [""])[0].lower()
        try:
            words = _abi_words(log.get("data"))
        except ValueError:
            return False
        if topic == V2_SWAP_TOPIC and len(words) >= 4:
            return words[quote_index] > 0 and words[2 + token_index] > 0
        if topic == V3_SWAP_TOPIC and len(words) >= 2:
            amounts = [_signed_256(value) for value in words[:2]]
            return amounts[quote_index] > 0 and amounts[token_index] < 0
        return False

    @staticmethod
    def _v4_acquisition(
        receipt: dict, token: str, wallet: str, manager: str, native_paid: bool = False
    ) -> bool:
        """Require V4 swap activity, net token inflow and a wallet-funded quote.

        V4's singleton event identifies a pool by ID, not by ERC20 address.
        This is a lower-confidence discovery, never proof of a token/pool pair.
        """
        logs = receipt.get("logs") or []
        swaps = [row for row in logs
                 if normalise_address(row.get("address")) == manager
                 and (row.get("topics") or [""])[0].lower() == V4_SWAP_TOPIC
                 and len(row.get("topics") or []) == 3]
        if not swaps:
            return False
        if not any(
            len(words := _abi_words(row.get("data"))) >= 2
            and _signed_256(words[0]) * _signed_256(words[1]) < 0
            for row in swaps
        ):
            return False
        received = sent = 0
        paid_quote = native_paid
        swap_senders = {_address_from_topic(row["topics"][2]) for row in swaps}
        for row in logs:
            topics = row.get("topics") or []
            if len(topics) != 3 or topics[0].lower() != TRANSFER_TOPIC:
                continue
            asset = normalise_address(row.get("address"))
            sender = _address_from_topic(topics[1])
            receiver = _address_from_topic(topics[2])
            try:
                amount = _abi_words(row.get("data"))
            except ValueError:
                continue
            if len(amount) != 1:
                continue
            if asset == token:
                if sender == wallet:
                    sent += amount[0]
                if receiver == wallet and sender == manager:
                    received += amount[0]
            elif (asset in BASE_QUOTES
                  and sender == wallet and receiver in {manager, *swap_senders}
                  and amount[0] > 0):
                paid_quote = True
        return received > sent and paid_quote

    async def discover(self, session: aiohttp.ClientSession, state: SQLiteState) -> list[Candidate]:
        selected_wallets = self.tracked_wallets(state)
        wallets = set(selected_wallets)
        if not wallets:
            return []
        # A bounded block window lets the free HTTP RPC catch up after a restart
        # without an unbounded log query. Never advance past unprocessed events.
        latest = max(0, int(await self._rpc(session, "eth_blockNumber", []), 16) - 2)
        key = "wallet_swap_discovery_block:base"
        stored = state.get_cursor(key)
        start = int(stored) + 1 if stored is not None else latest - 299
        start = max(0, start, latest - self.config.rpc_lookback_blocks + 1)
        end = min(latest, start + 299)
        if start > end:
            return []
        wallet_topics = ["0x" + _word_address(wallet) for wallet in selected_wallets]
        logs = await self._rpc(
            session,
            "eth_getLogs",
            [{"fromBlock": hex(start), "toBlock": hex(end),
              "topics": [TRANSFER_TOPIC, None, wallet_topics]}],
        )
        logs = sorted(logs or [], key=lambda row: (int(row["blockNumber"], 16), int(row["logIndex"], 16)))
        # Limit receipt lookups on the free tier. A full block is processed
        # together so the cursor cannot skip an event in that block.
        hashes: set[str] = set()
        selected: list[dict] = []
        for log in logs:
            tx_hash = log.get("transactionHash")
            if tx_hash not in hashes and len(hashes) >= 12 and selected:
                if log["blockNumber"] != selected[-1]["blockNumber"]:
                    end = int(selected[-1]["blockNumber"], 16)
                    break
            if tx_hash:
                hashes.add(tx_hash)
                selected.append(log)
        receipt_cache: dict[str, dict] = {}
        tx_cache: dict[str, dict] = {}
        pool_cache: dict[tuple[str, str, str], tuple[int, int] | None] = {}
        candidates: dict[str, Candidate] = {}
        observed_at = utc_now()
        for log in selected:
            if log.get("removed") or len(log.get("topics") or []) != 3:
                continue
            token = normalise_address(log.get("address"))
            if len(token) != 42 or token in self.config.quote_tokens.get("base", set()):
                continue
            pool = _address_from_topic(log["topics"][1])
            wallet = _address_from_topic(log["topics"][2])
            tx_hash = log.get("transactionHash")
            if wallet not in wallets or not tx_hash or len(pool) != 42:
                continue
            if tx_hash not in receipt_cache:
                receipt_cache[tx_hash] = await self._rpc(session, "eth_getTransactionReceipt", [tx_hash])
            receipt = receipt_cache[tx_hash]
            if (not isinstance(receipt, dict)
                    or int(receipt.get("status", "0x0"), 16) != 1
                    or normalise_address(receipt.get("from")) != wallet):
                continue
            proof = "v2-v3-registered-pool"
            if pool == BASE_V4_POOL_MANAGER:
                if not self._v4_acquisition(receipt, token, wallet, pool, native_paid=True):
                    continue
                if not self._v4_acquisition(receipt, token, wallet, pool):
                    # Native-ETH funded V4 buys have no ERC20 quote debit from
                    # the EOA. Fetch the transaction only for this V4 case.
                    if tx_hash not in tx_cache:
                        tx_cache[tx_hash] = await self._rpc(
                            session, "eth_getTransactionByHash", [tx_hash]
                        )
                    tx = tx_cache[tx_hash]
                    if (not isinstance(tx, dict)
                            or int(tx.get("value", "0x0"), 16) < 10**15
                            or not self._v4_acquisition(receipt, token, wallet, pool, native_paid=True)):
                        continue
                candidate = candidates.get(token)
                if candidate is None:
                    candidates[token] = candidate = Candidate(
                        chain="base", token_address=token, source="wallet-swap",
                        discovered_at=observed_at,
                        metadata={
                            "wallet_discovery_at": observed_at.isoformat(),
                            "wallet_discovery_block": int(log["blockNumber"], 16),
                            "wallet_discovery_tx": tx_hash,
                            "wallet_discovery_wallets": [],
                            "wallet_discovery_factory_verified": False,
                            "wallet_discovery_proof": "v4-manager-net-inflow",
                        },
                    )
                if wallet not in candidate.metadata["wallet_discovery_wallets"]:
                    candidate.metadata["wallet_discovery_wallets"].append(wallet)
                continue
            swap_logs = [
                row for row in receipt.get("logs", [])
                if normalise_address(row.get("address")) == pool
                and len(row.get("topics") or []) == 3
                and _address_from_topic(row["topics"][2]) == wallet
                and (row.get("topics") or [""])[0].lower() in {V2_SWAP_TOPIC, V3_SWAP_TOPIC}
            ]
            for swap in swap_logs:
                topic = swap["topics"][0].lower()
                cache_key = (pool, token, topic)
                if cache_key not in pool_cache:
                    pool_cache[cache_key] = await self._verified_pool(session, pool, token, topic)
                indices = pool_cache[cache_key]
                if not indices or not self._paid_swap(swap, *indices):
                    continue
                candidate = candidates.get(token)
                if candidate is None:
                    candidate = Candidate(
                        chain="base", token_address=token, pair_address=pool,
                        source="wallet-swap", discovered_at=observed_at,
                        metadata={
                            "wallet_discovery_at": observed_at.isoformat(),
                            "wallet_discovery_block": int(log["blockNumber"], 16),
                            "wallet_discovery_tx": tx_hash,
                            "wallet_discovery_wallets": [],
                            "wallet_discovery_factory_verified": True,
                            "wallet_discovery_proof": proof,
                        },
                    )
                    candidates[token] = candidate
                if wallet not in candidate.metadata["wallet_discovery_wallets"]:
                    candidate.metadata["wallet_discovery_wallets"].append(wallet)
                break
        state.set_cursor(key, str(end))
        return list(candidates.values())
