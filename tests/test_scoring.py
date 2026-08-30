import unittest
from datetime import datetime, timedelta, timezone

from scanner.config import ScannerConfig
from scanner.models import Candidate, MarketSnapshot
from scanner.scoring import SignalScorer


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
TOKEN = "0x1111111111111111111111111111111111111111"
CLEAN_SECURITY = {
    "checked": True,
    "admin_checks_complete": True,
    "simulation_checked": True,
    "is_honeypot": False,
    "cannot_buy": False,
    "cannot_sell": False,
    "sell_tax": 0,
    "risk_level": 0,
    "open_source": True,
}


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
        "raw": {"security": CLEAN_SECURITY.copy()},
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

    def test_missing_price_never_alerts(self):
        candidate = Candidate(chain="base", token_address=TOKEN, source="bankr", launch_at=NOW)
        result = self.scorer.score(candidate, snapshot(price_usd=None), [], now=NOW)
        self.assertFalse(result.eligible)
        self.assertIn("price unavailable", " ".join(result.blockers))

    def test_buy_tier_requires_strong_trade_quality(self):
        candidate = Candidate(
            chain="base",
            token_address=TOKEN,
            source="bankr",
            launch_at=NOW - timedelta(minutes=10),
        )
        result = self.scorer.score(
            candidate,
            snapshot(buys_5m=6, sells_5m=0, social_velocity=8, smart_wallet_buys=5),
            [],
            now=NOW,
        )
        self.assertTrue(result.eligible)
        self.assertEqual(result.signal, "EARLY WATCH")

    def test_honeypot_never_alerts_even_with_strong_flow(self):
        candidate = Candidate(
            chain="base", token_address=TOKEN, source="clanker", launch_at=NOW
        )
        unsafe = CLEAN_SECURITY | {"is_honeypot": True, "risk_level": 100}
        result = self.scorer.score(
            candidate, snapshot(raw={"security": unsafe}), [], now=NOW
        )
        self.assertFalse(result.eligible)
        self.assertIn("honeypot simulation failed", result.blockers)

    def test_untrusted_pool_requires_sell_simulation_but_verified_launch_can_fallback(self):
        security = CLEAN_SECURITY | {"simulation_checked": False}
        generic = Candidate(
            chain="base", token_address=TOKEN, source="rpc-pairs:base:v2", launch_at=NOW
        )
        verified = Candidate(chain="base", token_address=TOKEN, source="o1-b20", launch_at=NOW)
        self.assertFalse(
            self.scorer.score(generic, snapshot(raw={"security": security}), [], now=NOW).eligible
        )
        self.assertTrue(
            self.scorer.score(verified, snapshot(raw={"security": security}), [], now=NOW).eligible
        )

    def test_high_buy_tax_and_unlocked_lp_are_rejected(self):
        candidate = Candidate(
            chain="base", token_address=TOKEN, source="rpc-pairs:base:v2", launch_at=NOW
        )
        unsafe = CLEAN_SECURITY | {"buy_tax": 25, "lp_unlocked_percent": 80}
        result = self.scorer.score(
            candidate, snapshot(raw={"security": unsafe}), [], now=NOW
        )
        self.assertFalse(result.eligible)
        self.assertIn("buy tax 25%", result.blockers)
        self.assertIn("80% of LP appears unlocked", result.blockers)

    def test_copycat_penalty_can_suppress_otherwise_eligible_alert(self):
        candidate = Candidate(
            chain="base",
            token_address=TOKEN,
            source="bankr",
            launch_at=NOW,
            metadata={
                "identity_risk": {
                    "copycat_penalty": 24,
                    "reason": "2 recent exact name/ticker duplicates",
                }
            },
        )
        result = self.scorer.score(candidate, snapshot(), [], now=NOW)
        self.assertFalse(result.eligible)
        self.assertIn("duplicates", " ".join(result.blockers))

    def test_missing_project_identity_does_not_alert_on_flow_alone(self):
        candidate = Candidate(
            chain="base", token_address=TOKEN, source="rpc-pairs:base:v2", launch_at=NOW
        )
        result = self.scorer.score(
            candidate,
            snapshot(social_links=0, smart_wallet_buys=0),
            [],
            now=NOW,
        )
        self.assertFalse(result.eligible)
        self.assertIn("no project/social", " ".join(result.blockers))
