import unittest
from unittest.mock import AsyncMock, patch

from scanner.config import ScannerConfig
from scanner.rpc import rpc_call, rpc_payload
from scanner.state import SQLiteState
from scanner.wallet_discovery import WalletSwapDiscoveryFeed
from tests.test_feeds import FakeResponse


PRIMARY = "https://primary.example"
FALLBACK = "https://fallback.example"


class ScriptedSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, *, json):
        self.calls.append((url, json))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RpcRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = ScannerConfig(base_rpc_url=PRIMARY, base_rpc_fallback_urls=(FALLBACK,))
        self.sleep = patch("scanner.rpc.asyncio.sleep", new=AsyncMock())
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    async def test_transient_throttle_recovers_without_fallback(self):
        session = ScriptedSession([FakeResponse({}, 429), FakeResponse({"result": "0x64"})])
        result = await rpc_call(session, PRIMARY, "eth_blockNumber", [], self.config, chain="base")
        self.assertEqual(result, "0x64")
        self.assertEqual([url for url, _ in session.calls], [PRIMARY, PRIMARY])

    async def test_outage_uses_verified_fallback_and_cools_primary_across_feeds(self):
        session = ScriptedSession([
            FakeResponse({}, 429), FakeResponse({}, 429),
            FakeResponse({"result": "0x2105"}), FakeResponse({"result": "0x64"}),
            FakeResponse({"result": []}),
        ])
        self.assertEqual(await rpc_call(session, PRIMARY, "eth_blockNumber", [], self.config, chain="base"), "0x64")
        self.assertEqual(await rpc_call(session, PRIMARY, "eth_getLogs", [{}], self.config, chain="base"), [])
        self.assertEqual([url for url, _ in session.calls], [PRIMARY, PRIMARY, FALLBACK, FALLBACK, FALLBACK])
        self.assertEqual(session.calls[2][1]["method"], "eth_chainId")

    async def test_wrong_chain_fallback_is_rejected(self):
        session = ScriptedSession([
            FakeResponse({}, 503), FakeResponse({}, 503),
            FakeResponse({"result": "0x1"}), FakeResponse({"result": "0x1"}),
        ])
        with self.assertRaisesRegex(RuntimeError, "not Base mainnet"):
            await rpc_call(session, PRIMARY, "eth_blockNumber", [], self.config, chain="base")

    async def test_reverted_contract_is_not_retried_on_another_provider(self):
        session = ScriptedSession([FakeResponse({"error": {"code": -32000, "message": "execution reverted"}})])
        with self.assertRaisesRegex(RuntimeError, "execution reverted"):
            await rpc_call(session, PRIMARY, "eth_call", [], self.config, chain="base")
        self.assertEqual(len(session.calls), 1)

    async def test_partial_batch_is_a_failure(self):
        self.config.base_rpc_fallback_urls = ()
        session = ScriptedSession([FakeResponse([{"id": 1, "result": {}}]) for _ in range(2)])
        payload = [{"id": n, "method": "eth_getBlockByNumber", "params": []} for n in (1, 2)]
        with self.assertRaisesRegex(RuntimeError, "missing request IDs"):
            await rpc_payload(session, PRIMARY, payload, self.config, chain="base")

    async def test_provider_failure_preserves_live_wallet_cursor(self):
        self.config.base_rpc_fallback_urls = ()
        self.config.smart_wallets = ("0x" + "5" * 40,)
        state = SQLiteState(":memory:")
        self.addCleanup(state.close)
        state.set_cursor("wallet_swap_discovery_block:base", "99")
        session = ScriptedSession([
            FakeResponse({"result": "0x66"}), FakeResponse({}, 429), FakeResponse({}, 429),
        ])
        with self.assertRaises(RuntimeError):
            await WalletSwapDiscoveryFeed(self.config).discover(session, state)
        self.assertEqual(state.get_cursor("wallet_swap_discovery_block:base"), "99")

    async def test_write_methods_are_never_sent(self):
        session = ScriptedSession([])
        with self.assertRaises(ValueError):
            await rpc_call(session, PRIMARY, "eth_sendRawTransaction", [], self.config, chain="base")
        self.assertEqual(session.calls, [])

    async def test_log_partitions_cover_each_address_and_entire_window(self):
        self.config.base_rpc_fallback_urls = ()
        session = ScriptedSession([FakeResponse({"result": [{"part": n}]}) for n in range(6)])
        query = {"fromBlock": "0x0", "toBlock": "0x258", "address": ["first", "second"],
                 "topics": [["launch-a", "launch-b"]]}
        rows = await rpc_call(session, PRIMARY, "eth_getLogs", [query], self.config, chain="base")
        self.assertEqual(len(rows), 6)
        windows = {address: [] for address in query["address"]}
        for _, payload in session.calls:
            part = payload["params"][0]
            windows[part["address"]].append((int(part["fromBlock"], 16), int(part["toBlock"], 16)))
            self.assertEqual(part["topics"], query["topics"])
        self.assertEqual(windows, {address: [(0, 299), (300, 599), (600, 600)] for address in windows})
        self.assertEqual(query["address"], ["first", "second"])

    async def test_failed_log_partition_cannot_return_partial_success(self):
        self.config.base_rpc_fallback_urls = ()
        session = ScriptedSession([FakeResponse({"result": [{"found": True}]}), FakeResponse({}, 400)])
        with self.assertRaises(RuntimeError):
            await rpc_call(session, PRIMARY, "eth_getLogs", [{
                "fromBlock": "0x1", "toBlock": "0x2", "address": ["first", "second"],
            }], self.config, chain="base")
