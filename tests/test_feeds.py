import types
import unittest

from scanner.config import ScannerConfig
from scanner.feeds import BankrLaunchFeed, PAIR_CREATED_TOPIC, RpcPairFeed
from scanner.state import SQLiteState


TOKEN = "0x3333333333333333333333333333333333333333"
PAIR = "0x4444444444444444444444444444444444444444"
WETH = "0x4200000000000000000000000000000000000006"


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status
        self.url = types.SimpleNamespace(host="example.test")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self.body


class BankrSession:
    def get(self, url, **kwargs):
        return FakeResponse(
            {
                "launches": [
                    {
                        "status": "deployed",
                        "chain": "base",
                        "tokenAddress": TOKEN,
                        "tokenName": "Test Token",
                        "tokenSymbol": "TEST",
                        "timestamp": 1_777_111_200_000,
                        "deployer": {"walletAddress": "0x5555555555555555555555555555555555555555"},
                    },
                    {"status": "deployed", "chain": "solana", "tokenAddress": "ignored"},
                ]
            }
        )


class RpcSession:
    def post(self, url, json):
        if isinstance(json, list):
            return FakeResponse(
                [
                    {
                        "jsonrpc": "2.0",
                        "id": item["id"],
                        "result": {"timestamp": hex(1_777_111_200)},
                    }
                    for item in json
                ]
            )
        method = json["method"]
        if method == "eth_blockNumber":
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": hex(100)})
        if method == "eth_getLogs":
            data = "0x" + PAIR.removeprefix("0x").rjust(64, "0") + hex(1).removeprefix("0x").rjust(64, "0")
            log = {
                "address": "0x6666666666666666666666666666666666666666",
                "topics": [
                    PAIR_CREATED_TOPIC,
                    "0x" + WETH.removeprefix("0x").rjust(64, "0"),
                    "0x" + TOKEN.removeprefix("0x").rjust(64, "0"),
                ],
                "data": data,
                "blockNumber": hex(100),
                "transactionHash": "0xabc",
            }
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": [log]})
        raise AssertionError(f"Unexpected method {method}")


class FeedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = SQLiteState(":memory:")

    async def asyncTearDown(self):
        self.state.close()

    async def test_bankr_filters_to_deployed_base_launches(self):
        rows = await BankrLaunchFeed(ScannerConfig()).discover(BankrSession(), self.state)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].token_address, TOKEN)
        self.assertEqual(rows[0].symbol, "TEST")

    async def test_rpc_pair_feed_selects_non_quote_token_and_advances_cursor(self):
        feed = RpcPairFeed("base", "https://rpc.example", ScannerConfig())
        rows = await feed.discover(RpcSession(), self.state)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].token_address, TOKEN)
        self.assertEqual(rows[0].pair_address, PAIR)
        self.assertEqual(self.state.get_cursor("rpc_pairs_block:base"), "100")
