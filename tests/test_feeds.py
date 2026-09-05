import time
import types
import unittest
from unittest.mock import AsyncMock, patch

from scanner.config import ScannerConfig
from scanner.feeds import (
    BankrLaunchFeed,
    BaselineLaunchFeed,
    ClankerLaunchFeed,
    DexScreenerEnricher,
    DexScreenerProfilesFeed,
    FactoryLaunchFeed,
    GMGNReadOnlyFeed,
    O1_FACTORIES,
    O1_LAUNCHED_TOPIC,
    OnchainFlowEnricher,
    PONS_V1_LAUNCHED_TOPIC,
    PONS_V2_BUY_TOPIC,
    PONS_V2_LAUNCHED_TOPIC,
    PONS_V2_SELL_TOPIC,
    PonsV1Enricher,
    PonsV2Enricher,
    PoolsFunLaunchFeed,
    ROBINHOOD_V4_POOL_MANAGER,
    RobinhoodMarketEnricher,
    RpcPairFeed,
    TokenRiskEnricher,
    TRANSFER_TOPIC,
    ZoraExploreFeed,
    _get_json,
    snapshot_from_fallback,
    platform_factory_specs,
    PAIR_CREATED_TOPIC,
)
from scanner.models import Candidate, MarketSnapshot
from scanner.state import SQLiteState


TOKEN = "0x3333333333333333333333333333333333333333"
PAIR = "0x4444444444444444444444444444444444444444"
PAIR_TWO = "0x7777777777777777777777777777777777777777"
TOKEN_TWO = "0x8888888888888888888888888888888888888888"
WETH = "0x4200000000000000000000000000000000000006"
ROBINHOOD_WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"


def abi_words(*values):
    return "0x" + "".join(f"{value:064x}" for value in values)


def abi_strings(*values):
    heads = []
    tails = []
    offset = len(values) * 32
    for value in values:
        encoded = value.encode()
        padded = encoded + b"\0" * ((32 - len(encoded) % 32) % 32)
        tail = len(encoded).to_bytes(32, "big") + padded
        heads.append(offset.to_bytes(32, "big"))
        tails.append(tail)
        offset += len(tail)
    return "0x" + (b"".join(heads + tails)).hex()


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}
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
                    {
                        "status": "deployed",
                        "chain": "robinhood",
                        "tokenAddress": TOKEN_TWO,
                        "tokenName": "Robinhood Test",
                        "tokenSymbol": "RTEST",
                        "poolId": "0xpool",
                        "launchType": "doppler",
                        "tweetUrl": "https://x.com/example/status/1",
                        "websiteUrl": "https://example.test",
                        "deployer": {"walletAddress": "0x9999999999999999999999999999999999999999"},
                    },
                    {"status": "deployed", "chain": "solana", "tokenAddress": "ignored"},
                ]
            }
        )


class PlatformDiscoverySession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params") or {}))
        if "api.bankr.bot" in url:
            return FakeResponse(
                {
                    "results": [
                        {
                            "chain": "robinhood",
                            "platform": "poolsfun",
                            "tokenAddress": TOKEN,
                            "poolId": PAIR,
                            "name": "Pools Project",
                            "symbol": "POOL",
                            "deployerAddress": "0x5555555555555555555555555555555555555555",
                            "deployerXUsername": "builder",
                            "websiteUrl": "https://project.test",
                            "deployedAt": "2026-08-31T12:00:00Z",
                            "marketCapUsd": 12_000,
                            "vol5m": 2_000,
                            "txCount24h": 25,
                        }
                    ]
                }
            )
        if "api-sdk.zora.engineering" in url:
            return FakeResponse(
                {
                    "exploreList": {
                        "edges": [
                            {
                                "node": {
                                    "chainId": 8453,
                                    "platformBlocked": False,
                                    "address": TOKEN_TWO,
                                    "name": "Base App Project",
                                    "symbol": "BAP",
                                    "coinType": "CONTENT",
                                    "createdAt": "2026-08-31T12:01:00Z",
                                    "creatorAddress": "0x9999999999999999999999999999999999999999",
                                    "creatorProfile": {
                                        "handle": "realbuilder",
                                        "platformBlocked": False,
                                        "socialAccounts": {
                                            "twitter": {"username": "realbuilder"},
                                            "farcaster": None,
                                        },
                                    },
                                    "uniqueHolders": 12,
                                }
                            },
                            {
                                "node": {
                                    "chainId": 8453,
                                    "platformBlocked": True,
                                    "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                                }
                            },
                        ]
                    }
                }
            )
        raise AssertionError(f"Unexpected URL {url}")


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


class PonsPlatformSession:
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
            deployer = "0x5555555555555555555555555555555555555555"
            v1_factory = "0xa5aab3f0c6eeadf30ef1d3eb997108e976351feb"
            v2_factory = "0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e"
            rows = [
                {
                    "address": v1_factory,
                    "topics": [
                        PONS_V1_LAUNCHED_TOPIC,
                        "0x" + TOKEN.removeprefix("0x").rjust(64, "0"),
                        "0x" + deployer.removeprefix("0x").rjust(64, "0"),
                        "0x" + ("66" * 20).rjust(64, "0"),
                    ],
                    "data": abi_words(int(ROBINHOOD_WETH, 16), int(PAIR, 16), 1, 2),
                    "blockNumber": hex(99),
                    "transactionHash": "0xpons1",
                },
                {
                    "address": v2_factory,
                    "topics": [
                        PONS_V2_LAUNCHED_TOPIC,
                        "0x" + TOKEN_TWO.removeprefix("0x").rjust(64, "0"),
                        "0x" + PAIR_TWO.removeprefix("0x").rjust(64, "0"),
                        "0x" + deployer.removeprefix("0x").rjust(64, "0"),
                    ],
                    "data": abi_words(int(ROBINHOOD_WETH, 16)),
                    "blockNumber": hex(100),
                    "transactionHash": "0xpons2",
                },
            ]
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": rows})
        raise AssertionError(f"Unexpected method {json['method']}")


class PonsMarketSession:
    def get(self, url, **kwargs):
        if "coins.llama.fi" in url:
            return FakeResponse({"coins": {"coingecko:ethereum": {"price": 2_500}}})
        raise AssertionError(f"Unexpected URL {url}")

    def post(self, url, json):
        if not isinstance(json, list):
            raise AssertionError(f"Unexpected RPC payload {json}")
        sqrt_price = int((1000 ** 0.5) * (2 ** 96))
        by_selector = {
            "0x3850c7bd": abi_words(sqrt_price, 0, 0, 0, 0, 0, 1),
            "0x70a08231": abi_words(2 * 10**18),
            "0x18160ddd": abi_words(1_000_000_000 * 10**18),
            "0x313ce567": abi_words(18),
            "0x06fdde03": abi_strings("Pons Project"),
            "0x95d89b41": abi_strings("PONSX"),
            "0x53cd512a": abi_strings("https://x.com/ponsx", "", "", "https://pons.test", ""),
        }
        rows = []
        for item in json:
            selector = item["params"][0]["data"][:10]
            rows.append(
                {"jsonrpc": "2.0", "id": item["id"], "result": by_selector[selector]}
            )
        return FakeResponse(rows)


class PonsV2MarketSession:
    def get(self, url, **kwargs):
        if "coins.llama.fi" in url:
            return FakeResponse({"coins": {"coingecko:ethereum": {"price": 2_500}}})
        raise AssertionError(f"Unexpected URL {url}")

    def post(self, url, json):
        if not isinstance(json, list):
            raise AssertionError(f"Unexpected RPC payload {json}")
        if any(item["method"] == "eth_call" for item in json):
            by_selector = {
                "0x0902f1ac": abi_words(2 * 10**18, 1_000_000_000 * 10**18),
                "0x4f1f58fd": abi_words(1 * 10**18),
                "0x24a9d853": abi_words(100),
                "0xc1bb8901": abi_words(100),
                "0x18160ddd": abi_words(1_000_000_000 * 10**18),
                "0x313ce567": abi_words(18),
                "0x06fdde03": abi_strings("Curve Project"),
                "0x95d89b41": abi_strings("CURVE"),
                "0x53cd512a": abi_strings("https://x.com/curve", "", "", "https://curve.test", ""),
            }
            rows = []
            for item in json:
                result = (
                    hex(1_000)
                    if item["method"] == "eth_blockNumber"
                    else by_selector[item["params"][0]["data"][:10]]
                )
                rows.append({"jsonrpc": "2.0", "id": item["id"], "result": result})
            return FakeResponse(rows)

        wallet = "0x9999999999999999999999999999999999999999"
        curve_topic = "0x" + PAIR_TWO.removeprefix("0x").rjust(64, "0")
        wallet_topic = "0x" + wallet.removeprefix("0x").rjust(64, "0")
        zero_topic = "0x" + "0" * 64
        responses = []
        for item in json:
            topic = item["params"][0]["topics"][0]
            if topic == PONS_V2_BUY_TOPIC:
                rows = [
                    {
                        "topics": [PONS_V2_BUY_TOPIC, wallet_topic],
                        "data": abi_words(10**18, 500 * 10**18),
                        "blockNumber": hex(990),
                    }
                ]
            elif topic == PONS_V2_SELL_TOPIC:
                rows = [
                    {
                        "topics": [PONS_V2_SELL_TOPIC, wallet_topic],
                        "data": abi_words(100 * 10**18, 5 * 10**17),
                        "blockNumber": hex(995),
                    }
                ]
            else:
                rows = [
                    {
                        "topics": [TRANSFER_TOPIC, zero_topic, curve_topic],
                        "data": hex(1_000_000_000 * 10**18),
                        "blockNumber": hex(800),
                    },
                    {
                        "topics": [TRANSFER_TOPIC, curve_topic, wallet_topic],
                        "data": hex(500 * 10**18),
                        "blockNumber": hex(990),
                    },
                ]
            responses.append({"jsonrpc": "2.0", "id": item["id"], "result": rows})
        return FakeResponse(responses)


class HooderSession:
    def get(self, url, **kwargs):
        return FakeResponse(
            {
                "success": True,
                "data": {
                    "name": "Indexed Robinhood Token",
                    "symbol": "IRT",
                    "priceUsd": 0.002,
                    "liquidityUsd": 12_000,
                    "volume24hUsd": 30_000,
                    "priceChange24h": 20,
                    "isStale": False,
                    "sources": ["DexScreener"],
                },
            }
        )

    def post(self, url, json):
        return FakeResponse(
            [
                {"jsonrpc": "2.0", "id": 0, "result": hex(1_000_000 * 10**18)},
                {"jsonrpc": "2.0", "id": 1, "result": hex(18)},
            ]
        )


class FlowSession:
    def post(self, url, json):
        if isinstance(json, list):
            return FakeResponse(
                [
                    {
                        "jsonrpc": "2.0",
                        "id": item["id"],
                        "result": {"from": f"0x{item['id'] + 100:040x}"},
                    }
                    for item in json
                ]
            )
        if json["method"] == "eth_blockNumber":
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": hex(1_000)})
        if json["method"] == "eth_getLogs":
            topics = json["params"][0]["topics"]
            pair_topic = "0x" + PAIR.removeprefix("0x").rjust(64, "0")
            if len(topics) == 2:
                rows = [
                    {
                        "address": TOKEN,
                        "topics": [
                            TRANSFER_TOPIC,
                            pair_topic,
                            "0x" + f"{index + 200:040x}".rjust(64, "0"),
                        ],
                        "blockNumber": hex(900 + index * 20),
                        "transactionHash": f"0xbuy{index}",
                    }
                    for index in range(3)
                ]
            else:
                deployer = "0x5555555555555555555555555555555555555555"
                rows = [
                    {
                        "address": TOKEN,
                        "topics": [
                            TRANSFER_TOPIC,
                            "0x" + deployer.removeprefix("0x").rjust(64, "0"),
                            pair_topic,
                        ],
                        "blockNumber": hex(940),
                        "transactionHash": "0xsell0",
                    }
                ]
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": rows})
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
                            "cannot_buy": "0",
                            "cannot_sell": "0",
                            "owner_change_balance": "0",
                            "transfer_pausable": "0",
                            "is_blacklisted": "0",
                            "is_mintable": "0",
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


class GMGNSession:
    def __init__(self):
        self.calls = []
        self.now = int(time.time())

    @staticmethod
    def _wrapped(data):
        return {"code": 0, "data": {"code": 0, "data": data}}

    def _quality_row(self, chain, address, launchpad):
        return {
            "chain": chain,
            "address": address,
            "name": "Useful Project",
            "symbol": "USEFUL",
            "price": 0.001,
            "market_cap": 100_000,
            "liquidity": 20_000,
            "volume": 8_000,
            "swaps": 40,
            "buys": 28,
            "sells": 12,
            "holder_count": 120,
            "smart_degen_count": 4,
            "renowned_count": 2,
            "top_10_holder_rate": 0.20,
            "rug_ratio": 0,
            "is_honeypot": 0,
            "is_wash_trading": False,
            "buy_tax": "0",
            "sell_tax": "0",
            "is_open_source": 1,
            "is_renounced": 1,
            "creation_timestamp": self.now - 300,
            "launchpad_platform": launchpad,
            "creator": "0x5555555555555555555555555555555555555555",
            "twitter_username": "https://x.com/useful",
            "website": "https://useful.example",
        }

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        chain = kwargs["params"]["chain"]
        rows = [
            self._quality_row(
                chain,
                TOKEN if chain == "base" else TOKEN_TWO,
                "bankr" if chain == "base" else "pons_v2",
            )
        ]
        if chain == "robinhood":
            rows.extend(
                [
                    {
                        **self._quality_row(
                            chain,
                            "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                            "pool_robinhood_stock_amm",
                        ),
                        "name": "Tesla",
                        "symbol": "TSLA",
                    },
                    {
                        **self._quality_row(
                            chain,
                            "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                            "pons_v2",
                        ),
                        "is_honeypot": 1,
                    },
                ]
            )
        return FakeResponse(self._wrapped({"rank": rows}))

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/v1/market/token_signal"):
            return FakeResponse(
                self._wrapped(
                    {
                        "signals": [
                            {
                                "token_address": TOKEN_TWO,
                                "signal_type": 12,
                                "trigger_at": self.now,
                                "cur_data": {
                                    **self._quality_row(
                                        "robinhood", TOKEN_TWO, "pons_v2"
                                    ),
                                    # Older signal payloads can carry a higher
                                    # value than the current rank response.
                                    "market_cap": 250_000,
                                },
                            }
                        ]
                    }
                )
            )
        chain = kwargs["params"]["chain"]
        row = self._quality_row(
            chain,
            TOKEN if chain == "base" else TOKEN_TWO,
            "bankr" if chain == "base" else "pons_v2",
        )
        return FakeResponse(self._wrapped({"new_creation": {"list": [row]}}))


class GMGNCooldownSession:
    def __init__(self):
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return FakeResponse({}, status=403)

    def post(self, url, **kwargs):
        raise AssertionError("GMGN should stop the cycle after the first access rejection")


class FeedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = SQLiteState(":memory:")

    async def asyncTearDown(self):
        self.state.close()

    async def test_bankr_keeps_deployed_base_and_robinhood_launches(self):
        rows = await BankrLaunchFeed(ScannerConfig()).discover(BankrSession(), self.state)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].token_address, TOKEN)
        self.assertEqual(rows[0].symbol, "TEST")
        self.assertEqual(rows[1].chain, "robinhood")
        self.assertEqual(rows[1].pair_address, ROBINHOOD_V4_POOL_MANAGER)
        self.assertEqual(rows[1].metadata["profile_social_links"], 2)
        self.assertTrue(rows[1].metadata["verified_platform_api"])

    async def test_public_json_feed_retries_a_rate_limit(self):
        class RateLimitSession:
            def __init__(self):
                self.calls = 0

            def get(self, url, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return FakeResponse({}, status=429, headers={"Retry-After": "0"})
                return FakeResponse({"ok": True})

        session = RateLimitSession()
        with patch("scanner.feeds.asyncio.sleep", new=AsyncMock()) as sleep:
            payload = await _get_json(session, "https://example.test/feed")

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(session.calls, 2)
        sleep.assert_awaited_once()

    async def test_gmgn_feed_is_read_only_filters_junk_and_preserves_evidence(self):
        session = GMGNSession()
        feed = GMGNReadOnlyFeed(ScannerConfig(gmgn_candidate_limit=20))
        with patch("scanner.feeds.asyncio.sleep", new=AsyncMock()):
            rows = await feed.discover(session, self.state)

        by_address = {row.token_address: row for row in rows}
        self.assertEqual(set(by_address), {TOKEN, TOKEN_TWO})
        self.assertTrue(by_address[TOKEN].metadata["gmgn_evidence"])
        self.assertEqual(by_address[TOKEN].metadata["gmgn_launchpad"], "bankr")
        self.assertEqual(by_address[TOKEN].metadata["gmgn_attention_rank"], 1)
        self.assertEqual(by_address[TOKEN].metadata["gmgn_attention_source"], "1m-activity")
        self.assertEqual(by_address[TOKEN_TWO].metadata["gmgn_recent_smart_signals"], 1)
        self.assertEqual(
            by_address[TOKEN_TWO].metadata["gmgn_market"]["market_cap_usd"],
            100_000,
        )
        self.assertEqual(len(session.calls), 7)
        for _, url, kwargs in session.calls:
            self.assertIn(url.removeprefix(feed.host), feed.allowed_paths)
            serialised = str(kwargs).lower()
            self.assertNotIn("wallet_address", serialised)
            self.assertNotIn("from_address", serialised)
            self.assertNotIn("private", serialised)

        snapshot = snapshot_from_fallback(by_address[TOKEN])
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.market_cap_usd, 100_000)
        self.assertEqual(snapshot.holder_count, 120)
        self.assertEqual(snapshot.raw["security"]["providers"], ["gmgn"])

    async def test_gmgn_access_rejection_stops_the_cycle_and_honours_cooldown(self):
        session = GMGNCooldownSession()
        feed = GMGNReadOnlyFeed(ScannerConfig())

        with self.assertRaisesRegex(RuntimeError, "temporarily unavailable"):
            await feed.discover(session, self.state)
        self.assertEqual(session.calls, 1)

        with self.assertRaisesRegex(RuntimeError, "rate limited"):
            await feed.discover(session, self.state)
        self.assertEqual(session.calls, 1)

    async def test_pools_fun_and_zora_official_feeds_preserve_provenance(self):
        session = PlatformDiscoverySession()
        pools_rows = await PoolsFunLaunchFeed().discover(session, self.state)
        zora_feed = ZoraExploreFeed()
        zora_rows = await zora_feed.discover(session, self.state)
        self.assertEqual(len(pools_rows), 1)
        self.assertEqual(pools_rows[0].source, "pools-fun")
        self.assertEqual(pools_rows[0].pair_address, PAIR)
        self.assertEqual(pools_rows[0].metadata["profile_social_links"], 2)
        self.assertEqual(len(zora_rows), 1)
        self.assertEqual(zora_rows[0].source, "zora")
        self.assertEqual(zora_rows[0].deployer, "0x9999999999999999999999999999999999999999")
        self.assertEqual(zora_rows[0].metadata["profile_social_links"], 1)
        self.assertEqual(self.state.get_cursor("zora:explore_lane"), "1")
        self.assertEqual(session.calls[-1][1]["listType"], "NEW")

    async def test_rpc_pair_feed_selects_non_quote_token_and_advances_cursor(self):
        config = ScannerConfig(
            dex_factories={"base": {"0x6666666666666666666666666666666666666666"}}
        )
        feed = RpcPairFeed("base", "https://rpc.example", config)
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

    async def test_pons_v1_and_v2_factory_events_preserve_pool_and_creator(self):
        config = ScannerConfig(rpc_lookback_blocks=20, rpc_max_block_span=20)
        feed = FactoryLaunchFeed(
            "robinhood",
            "https://rpc.example",
            platform_factory_specs(config, "robinhood"),
            config,
        )
        rows = await feed.discover(PonsPlatformSession(), self.state)
        by_source = {row.source: row for row in rows}
        self.assertEqual(by_source["pons-v1"].pair_address, PAIR)
        self.assertEqual(by_source["pons-v1"].metadata["quote_token"], ROBINHOOD_WETH)
        self.assertEqual(by_source["pons-v2"].pair_address, PAIR_TWO)
        self.assertEqual(
            by_source["pons-v2"].deployer,
            "0x5555555555555555555555555555555555555555",
        )
        self.assertTrue(by_source["pons-v2"].metadata["platform_terms_verified"])

    async def test_pons_v1_market_reads_price_liquidity_and_socials_onchain(self):
        candidate = Candidate(
            chain="robinhood",
            token_address=TOKEN,
            pair_address=PAIR,
            source="pons-v1",
            deployer="0x5555555555555555555555555555555555555555",
            metadata={"quote_token": ROBINHOOD_WETH},
        )
        market = await PonsV1Enricher(ScannerConfig()).enrich(
            PonsMarketSession(), candidate
        )
        self.assertIsNotNone(market)
        self.assertAlmostEqual(market.price_usd, 2.5, places=6)
        self.assertAlmostEqual(market.liquidity_usd, 10_000, places=2)
        self.assertEqual(market.social_links, 2)
        self.assertEqual(market.raw["symbol"], "PONSX")

    async def test_pons_v2_native_curve_reads_flow_and_safety_onchain(self):
        candidate = Candidate(
            chain="robinhood",
            token_address=TOKEN_TWO,
            pair_address=PAIR_TWO,
            source="pons-v2",
            deployer="0x5555555555555555555555555555555555555555",
            metadata={
                "quote_token": "0x0000000000000000000000000000000000000000",
                "block_number": 800,
                "factory": "0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e",
            },
        )
        market = await PonsV2Enricher(ScannerConfig()).enrich(
            PonsV2MarketSession(), candidate
        )
        self.assertIsNotNone(market)
        self.assertAlmostEqual(market.price_usd, 0.000005, places=9)
        self.assertAlmostEqual(market.market_cap_usd, 5_000, places=2)
        self.assertAlmostEqual(market.volume_5m_usd, 3_750, places=2)
        self.assertEqual(market.unique_buyers_5m, 1)
        self.assertEqual(market.unique_sellers_5m, 1)
        self.assertEqual(market.social_links, 2)
        self.assertEqual(market.raw["security"]["buy_tax"], 2)
        self.assertFalse(market.raw["security"]["admin_checks_complete"])

    async def test_hooderscan_fallback_adds_robinhood_market_cap(self):
        candidate = Candidate(
            chain="robinhood", token_address=TOKEN_TWO, source="bankr"
        )
        market = await RobinhoodMarketEnricher(ScannerConfig()).enrich(
            HooderSession(), candidate
        )
        self.assertIsNotNone(market)
        self.assertEqual(market.liquidity_usd, 12_000)
        self.assertEqual(market.market_cap_usd, 2_000)

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
        self.assertTrue(profile.admin_checks_complete)
        self.assertTrue(profile.simulation_checked)
        self.assertFalse(profile.is_honeypot)
        self.assertEqual(profile.sell_tax, 3)
        self.assertTrue(profile.open_source)

    async def test_onchain_flow_measures_unique_wallets_and_deployer_selling(self):
        config = ScannerConfig(flow_5m_blocks=150, flow_15m_blocks=450)
        candidate = Candidate(
            chain="base",
            token_address=TOKEN,
            pair_address=PAIR,
            source="bankr",
            deployer="0x5555555555555555555555555555555555555555",
        )
        result = await OnchainFlowEnricher(config).enrich(
            FlowSession(),
            candidate,
            MarketSnapshot(chain="base", token_address=TOKEN, pair_address=PAIR),
        )
        self.assertTrue(result["flow_checked"])
        self.assertEqual(result["unique_buyers_5m"], 3)
        self.assertEqual(result["unique_sellers_5m"], 1)
        self.assertEqual(result["deployer_sells_15m"], 1)

    async def test_malformed_security_responses_are_not_treated_as_clean(self):
        class ErrorSession:
            def get(self, url, **kwargs):
                return FakeResponse({"error": "provider unavailable"})

        profile = await TokenRiskEnricher(ScannerConfig()).check(
            ErrorSession(),
            Candidate(chain="base", token_address=TOKEN, source="clanker"),
        )
        self.assertFalse(profile.checked)
        self.assertFalse(profile.admin_checks_complete)
        self.assertFalse(profile.simulation_checked)

    async def test_dex_enrichment_rejects_quote_side_price_for_candidate(self):
        class QuoteOnlySession:
            def get(self, url, **kwargs):
                return FakeResponse(
                    {
                        "pairs": [
                            {
                                "chainId": "base",
                                "pairAddress": PAIR,
                                "baseToken": {
                                    "address": WETH,
                                    "name": "Wrapped Ether",
                                    "symbol": "WETH",
                                },
                                "quoteToken": {"address": TOKEN},
                                "priceUsd": "4500",
                                "liquidity": {"usd": 1000000},
                            }
                        ]
                    }
                )

        market = await DexScreenerEnricher(ScannerConfig()).enrich(
            QuoteOnlySession(),
            Candidate(chain="base", token_address=TOKEN, source="rpc-pairs:base:v2"),
        )
        self.assertIsNone(market)

    async def test_latest_profiles_broaden_nonstandard_discovery(self):
        rows = await DexScreenerProfilesFeed().discover(DirectFeedSession(), self.state)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].token_address, TOKEN)
        self.assertEqual(rows[0].metadata["profile_social_links"], 1)
