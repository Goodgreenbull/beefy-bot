import unittest
from datetime import datetime, timedelta, timezone

from scanner.config import ScannerConfig
from scanner.models import Candidate, MarketSnapshot
from scanner.scoring import SignalScorer


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
TOKEN = "0x1111111111111111111111111111111111111111"


def snapshot(**overrides):
    values = {
        "chain": "base",
        "token_address": TOKEN,
        "price_usd": 0.001,
        "liquidity_usd": 10_000,
        "market_cap_usd": 80_000,
        "volume_5m_usd": 8_000,
        "volume_1h_usd": 20_000,
        "buys_5m": 24,
        "sells_5m": 6,
        "price_change_5m": 15,
        "price_change_1h": 40,
        "social_links": 3,
        "smart_wallet_buys": 2,
    }
    values.update(overrides)
    return MarketSnapshot(**values)


class SignalScorerTests(unittest.TestCase):
    def setUp(self):
        self.config = ScannerConfig()
        self.scorer = SignalScorer(self.config)

    def test_fresh_ignition_is_alert_eligible(self):
        candidate = Candidate(
            chain="base",
            token_address=TOKEN,
            source="bankr",
            discovered_at=NOW - timedelta(minutes=10),
            launch_at=NOW - timedelta(minutes=10),
        )
        result = self.scorer.score(candidate, snapshot(), [], now=NOW)
        self.assertEqual(result.stage, "IGNITION")
        self.assertTrue(result.eligible)
        self.assertIn(result.signal, {"EARLY WATCH", "STRONG WATCH"})

    def test_extended_move_is_rejected_by_anti_late_gate(self):
        candidate = Candidate(
            chain="base",
            token_address=TOKEN,
            source="bankr",
            launch_at=NOW - timedelta(minutes=20),
        )
        result = self.scorer.score(
            candidate,
            snapshot(price_change_5m=120, price_change_1h=340, market_cap_usd=8_000_000),
            [],
            now=NOW,
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.signal, "AVOID LATE")
        self.assertGreaterEqual(result.anti_late_penalty, 35)

    def test_old_token_can_reawaken_on_new_acceleration(self):
        candidate = Candidate(
            chain="base",
            token_address=TOKEN,
            source="rpc-pairs:base:v2",
            launch_at=NOW - timedelta(days=2),
        )
        history = [snapshot(volume_5m_usd=1_000, price_usd=0.0008) for _ in range(4)]
        result = self.scorer.score(candidate, snapshot(volume_5m_usd=6_000), history, now=NOW)
        self.assertEqual(result.stage, "REAWAKENING")
        self.assertTrue(result.eligible)

    def test_low_liquidity_never_alerts(self):
        candidate = Candidate(chain="base", token_address=TOKEN, source="flaunch", launch_at=NOW)
        result = self.scorer.score(candidate, snapshot(liquidity_usd=500), [], now=NOW)
        self.assertFalse(result.eligible)
        self.assertTrue(any("liquidity" in item for item in result.blockers))

    def test_buy_tier_requires_strong_trade_quality(self):
        candidate = Candidate(
            chain="base",
            token_address=TOKEN,
            source="bankr",
            launch_at=NOW - timedelta(minutes=10),
        )
        result = self.scorer.score(
            candidate,
            snapshot(buys_5m=4, sells_5m=0, social_velocity=8, smart_wallet_buys=5),
            [],
            now=NOW,
        )
        self.assertTrue(result.eligible)
        self.assertEqual(result.signal, "EARLY WATCH")
