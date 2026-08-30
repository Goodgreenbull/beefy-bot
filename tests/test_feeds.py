import types
import unittest

from scanner.config import ScannerConfig
from scanner.feeds import (
    BankrLaunchFeed,
    BaselineLaunchFeed,
    ClankerLaunchFeed,
    DexScreenerProfilesFeed,
    FactoryLaunchFeed,
    O1_FACTORIES,
    O1_LAUNCHED_TOPIC,
    RpcPairFeed,
    TokenRiskEnricher,
    platform_factory_specs,
    PAIR_CREATED_TOPIC,
)
from scanner.models import Candidate
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


class PlatformSession:
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
        if json["method"] == "eth_blockNumber":
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": hex(100)})
        if json["method"] == "eth_getLogs":
            factory = next(iter(O1_FACTORIES["base"]))
            return FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": [
                        {
                            "address": factory,
                            "topics": [
                                O1_LAUNCHED_TOPIC,
                                "0x" + TOKEN.removeprefix("0x").rjust(64, "0"),
                                "0x" + "ab" * 32,
                                "0x" + ("55" * 20).rjust(64, "0"),
                            ],
                            "data": "0x" + WETH.removeprefix("0x").rjust(64, "0"),
                            "blockNumber": hex(100),
                            "transactionHash": "0xabc",
                        }
                    ],
                }
            )
        raise AssertionError(f"Unexpected method {json['method']}")


class DirectFeedSession:
    def get(self, url, **kwargs):
        if "token-profiles/latest" in url:
            return FakeResponse(
                [
                    {
                        "chainId": "base",
                        "tokenAddress": TOKEN,
                        "url": "https://dexscreener.com/base/test",
                        "description": "A real project profile",
                        "links": [{"url": "https://example.test"}],
                    },
                    {"chainId": "solana", "tokenAddress": "ignored"},
                ]
            )
        if "clanker.world" in url:
            return FakeResponse(
                {
                    "data": [
                        {
                            "contract_address": TOKEN,
                            "name": "Real Project",
                            "symbol": "REAL",
                            "created_at": "2026-08-29T12:00:00Z",
                            "msg_sender": "0x5555555555555555555555555555555555555555",
                        }
                    ]
                }
            )
        if url.endswith("/tickers"):
            return FakeResponse(
                [{"base_currency": TOKEN, "last_price": "0.1", "liquidity_in_usd": "20000"}]
            )
        if url.endswith("/asset"):
            return FakeResponse({"asset": {"name": "Baseline Project", "symbol": "BASE"}})
        if url.endswith("/pair"):
            return FakeResponse(
                {
                    "pair": {
                        "creator": "0x5555555555555555555555555555555555555555",
                        "createdAtBlockTimestamp": 1_777_111_200,
                    }
                }
            )
        if "gopluslabs" in url:
            return FakeResponse(
                {
                    "result": {
                        TOKEN: {
                            "is_honeypot": "0",
                            "sell_tax": "0.03",
                            "is_open_source": "1",
                        }
                    }
                }
            )
        if "honeypot.is" in url:
            return FakeResponse(
                {
                    "honeypotResult": {"isHoneypot": False},
                    "simulationResult": {"sellTax": 3},
                    "summary": {"riskLevel": 10, "risk": "low"},
                    "contractCode": {"rootOpenSource": True},
                }
            )
        raise AssertionError(f"Unexpected URL {url}")


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

    async def test_o1_factory_event_is_discovered_directly(self):
        config = ScannerConfig(rpc_lookback_blocks=20, rpc_max_block_span=20)
        feed = FactoryLaunchFeed(
            "base", "https://rpc.example", platform_factory_specs(config, "base"), config
        )
        rows = await feed.discover(PlatformSession(), self.state)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].token_address, TOKEN)
        self.assertEqual(rows[0].source, "o1-b20")
        self.assertTrue(rows[0].metadata["verified_platform_event"])

    async def test_clanker_and_baseline_public_feeds_preserve_identity(self):
        session = DirectFeedSession()
        clanker = await ClankerLaunchFeed().discover(session, self.state)
        baseline = await BaselineLaunchFeed().discover(session, self.state)
        self.assertEqual(clanker[0].symbol, "REAL")
        self.assertEqual(baseline[0].symbol, "BASE")
        self.assertIn("baseline.markets", baseline[0].chart_url)

    async def test_free_contract_checks_are_combined(self):
        profile = await TokenRiskEnricher(ScannerConfig()).check(
            DirectFeedSession(),
            Candidate(chain="base", token_address=TOKEN, source="clanker"),
        )
        self.assertTrue(profile.checked)
        self.assertFalse(profile.is_honeypot)
        self.assertEqual(profile.sell_tax, 3)
        self.assertTrue(profile.open_source)

    async def test_latest_profiles_broaden_nonstandard_discovery(self):
        rows = await DexScreenerProfilesFeed().discover(DirectFeedSession(), self.state)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].token_address, TOKEN)
        self.assertEqual(rows[0].metadata["profile_social_links"], 1)
