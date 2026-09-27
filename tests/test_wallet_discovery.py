import unittest
from datetime import datetime, timedelta, timezone

from scanner.alerts import format_wallet_discovery_alert
from scanner.config import ScannerConfig
from scanner.feeds import TRANSFER_TOPIC
from scanner.models import Candidate
from scanner.state import SQLiteState
from scanner.wallet_discovery import (
    V2_SWAP_TOPIC, V3_SWAP_TOPIC, WalletSwapDiscoveryFeed,
)


TOKEN = "0x3333333333333333333333333333333333333333"
POOL = "0x4444444444444444444444444444444444444444"
WALLET = "0x5555555555555555555555555555555555555555"
WETH = "0x4200000000000000000000000000000000000006"
FACTORY = "0x33128a8fc17869897dce68ed026d694621f6fdfd"
V2_FACTORY = "0x8909dc15e40173ff4699343b6eb8132c65e18ec6"
TX = "0x" + "a" * 64


def word(value):
    return f"{int(value, 16) if isinstance(value, str) else value:064x}"


def topic(address):
    return "0x" + word(address)


def transfer(tx=TX):
    return {
        "address": TOKEN, "topics": [TRANSFER_TOPIC, topic(POOL), topic(WALLET)],
        "data": "0x" + word(10**18), "blockNumber": "0x64", "logIndex": "0x1",
        "transactionHash": tx, "removed": False,
    }


def v3_swap(quote_paid=10**16, token_out=10**18, recipient=WALLET):
    return {
        "address": POOL, "topics": [V3_SWAP_TOPIC, topic(WALLET), topic(recipient)],
        "data": "0x" + word(quote_paid) + word((1 << 256) - token_out),
    }


class FakeWalletFeed(WalletSwapDiscoveryFeed):
    def __init__(self, config, logs, receipt, factory=FACTORY):
        super().__init__(config)
        self.logs = logs
        self.receipt = receipt
        self.factory = factory
        self.fail_receipt = False
        self.fail_pool_call = None

    async def _rpc(self, session, method, params):
        if method == "eth_blockNumber":
            return "0x66"  # latest confirmed block: 100
        if method == "eth_getLogs":
            return self.logs
        if method == "eth_getTransactionReceipt":
            if self.fail_receipt:
                raise RuntimeError("provider unavailable")
            return self.receipt
        if method == "eth_call":
            address, data = params[0]["to"].lower(), params[0]["data"]
            if self.fail_pool_call and address == POOL:
                raise RuntimeError(self.fail_pool_call)
            if address == POOL:
                return {
                    "0xc45a0155": topic(self.factory),
                    "0x0dfe1681": topic(WETH),
                    "0xd21220a7": topic(TOKEN),
                    "0xddca3f43": "0x" + word(3000),
                }[data]
            if address == self.factory and data.startswith(("0x1698ee82", "0xe6a43905")):
                return topic(POOL)
        raise AssertionError((method, params))


class WalletDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = SQLiteState(":memory:")
        self.config = ScannerConfig(smart_wallets=(WALLET,))

    async def asyncTearDown(self):
        self.state.close()

    async def test_confirmed_paid_pool_swap_discovers_unknown_token_once(self):
        feed = FakeWalletFeed(self.config, [transfer()], {"status": "0x1", "from": WALLET, "logs": [v3_swap()]})
        found = await feed.discover(None, self.state)
        self.assertEqual([item.token_address for item in found], [TOKEN])
        self.assertEqual(found[0].metadata["wallet_discovery_wallets"], [WALLET])
        self.assertEqual(found[0].metadata["wallet_discovery_block"], 100)
        self.assertEqual(self.state.get_cursor("wallet_swap_discovery_block:base"), "100")
        self.assertEqual(await feed.discover(None, self.state), [])

    async def test_monitored_wallet_count_uses_valid_base_addresses_only(self):
        self.config.smart_wallets = (WALLET, WALLET, "invalid")
        feed = WalletSwapDiscoveryFeed(self.config)
        self.assertEqual(feed.tracked_wallets(self.state), [WALLET])

    async def test_v2_registered_pair_with_paid_swap_is_discovered(self):
        swap = {
            "address": POOL, "topics": [V2_SWAP_TOPIC, topic(WALLET), topic(WALLET)],
            "data": "0x" + word(10**16) + word(0) + word(0) + word(10**18),
        }
        feed = FakeWalletFeed(
            self.config, [transfer()],
            {"status": "0x1", "from": WALLET, "logs": [swap]},
            factory=V2_FACTORY,
        )
        self.assertEqual([item.token_address for item in await feed.discover(None, self.state)], [TOKEN])

    async def test_transfer_without_paid_swap_is_not_a_buy(self):
        feed = FakeWalletFeed(self.config, [transfer()], {"status": "0x1", "from": WALLET, "logs": []})
        self.assertEqual(await feed.discover(None, self.state), [])

    async def test_wrong_swap_direction_is_not_a_buy(self):
        wrong_way = {
            "address": POOL, "topics": [V3_SWAP_TOPIC, topic(WALLET), topic(WALLET)],
            "data": "0x" + word((1 << 256) - 10**16) + word(10**18),
        }
        feed = FakeWalletFeed(
            self.config, [transfer()],
            {"status": "0x1", "from": WALLET, "logs": [wrong_way]},
        )
        self.assertEqual(await feed.discover(None, self.state), [])

    async def test_swap_to_another_recipient_is_not_attributed_to_wallet(self):
        feed = FakeWalletFeed(
            self.config, [transfer()],
            {"status": "0x1", "from": WALLET,
             "logs": [v3_swap(recipient="0x" + "6" * 40)]},
        )
        self.assertEqual(await feed.discover(None, self.state), [])

    async def test_provider_failure_preserves_cursor(self):
        feed = FakeWalletFeed(self.config, [transfer()], {"status": "0x1", "from": WALLET, "logs": [v3_swap()]})
        feed.fail_receipt = True
        with self.assertRaises(RuntimeError):
            await feed.discover(None, self.state)
        self.assertIsNone(self.state.get_cursor("wallet_swap_discovery_block:base"))

    async def test_nonstandard_pool_revert_is_skipped_but_provider_failure_retries(self):
        receipt = {"status": "0x1", "from": WALLET, "logs": [v3_swap()]}
        feed = FakeWalletFeed(self.config, [transfer()], receipt)
        feed.fail_pool_call = "execution reverted"
        self.assertEqual(await feed.discover(None, self.state), [])
        self.state.set_cursor("wallet_swap_discovery_block:base", "99")
        feed.fail_pool_call = "provider unavailable"
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            await feed.discover(None, self.state)
        self.assertEqual(self.state.get_cursor("wallet_swap_discovery_block:base"), "99")

    async def test_unverified_factory_is_not_a_buy(self):
        self.config.dex_factories["base"] = set()
        feed = FakeWalletFeed(self.config, [transfer()], {"status": "0x1", "from": WALLET, "logs": [v3_swap()]})
        self.assertEqual(await feed.discover(None, self.state), [])

    async def test_gifted_output_is_not_attributed_to_tracked_wallet(self):
        feed = FakeWalletFeed(
            self.config, [transfer()],
            {"status": "0x1", "from": "0x" + "6" * 40, "logs": [v3_swap()]},
        )
        self.assertEqual(await feed.discover(None, self.state), [])

    async def test_discovery_alert_does_not_block_later_trade_call(self):
        candidate = Candidate(
            chain="base", token_address=TOKEN, source="wallet-swap",
            metadata={"wallet_discovery_at": "2026-09-26T17:00:00+00:00",
                      "wallet_discovery_block": 100, "wallet_discovery_tx": TX,
                      "wallet_discovery_wallets": [WALLET]},
        )
        self.state.upsert_candidate(candidate)
        self.assertTrue(self.state.wallet_discovery_allowed(candidate.key))
        self.state.record_wallet_discovery(candidate.key, TX)
        self.assertFalse(self.state.wallet_discovery_allowed(candidate.key))
        self.assertEqual(self.state.pending_wallet_discoveries(), [])
        message = format_wallet_discovery_alert(candidate)
        self.assertIn("VERIFY FIRST", message)
        self.assertIn(TOKEN, message)
        self.assertIn(TX, message)
        self.assertIn("no entry verdict", message)

    async def test_earlier_candidate_with_fresh_wallet_swap_is_queued(self):
        earlier = datetime.now(timezone.utc) - timedelta(days=2)
        candidate = Candidate(
            chain="base", token_address=TOKEN, source="bankr", discovered_at=earlier,
        )
        self.state.upsert_candidate(candidate)
        fresh = Candidate(
            chain="base", token_address=TOKEN, source="wallet-swap",
            metadata={"wallet_discovery_at": datetime.now(timezone.utc).isoformat(),
                      "wallet_discovery_tx": TX, "wallet_discovery_wallets": [WALLET]},
        )
        self.state.upsert_candidate(fresh)
        self.assertEqual([item.key for item in self.state.pending_wallet_discoveries()], [candidate.key])


class SwapDecodeTests(unittest.TestCase):
    def test_v2_and_v3_require_quote_in_and_token_out(self):
        v2 = {"topics": [V2_SWAP_TOPIC], "data": "0x" + word(10**16) + word(0) + word(0) + word(10**18)}
        self.assertTrue(WalletSwapDiscoveryFeed._paid_swap(v2, 1, 0))
        self.assertFalse(WalletSwapDiscoveryFeed._paid_swap(v2, 0, 1))
        self.assertTrue(WalletSwapDiscoveryFeed._paid_swap(v3_swap(), 1, 0))
