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
    "sell_simulation_success": True,
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
        "unique_buyers_5m": 10,
        "unique_buyers_15m": 16,
        "unique_sellers_5m": 2,
        "unique_sellers_15m": 4,
        "net_new_wallets_5m": 8,
        "net_new_wallets_15m": 12,
        "exact_ca_mentions_5m": 3,
        "exact_ca_mentions_15m": 4,
        "credible_social_mentions_5m": 1,
        "creator_reputation": 0.7,
        "narrative_score": 0.7,
        "flow_checked": True,
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
        self.assertIn(result.signal, {"ACTION", "A+"})

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
        self.assertNotEqual(result.signal, "A+")

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
        self.assertIn("dangerous tax 25%/0%", result.blockers)
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

    def test_one_exact_name_and_ticker_clone_is_not_alerted(self):
        candidate = Candidate(
            chain="base",
            token_address=TOKEN,
            source="bankr",
            launch_at=NOW,
            metadata={
                "identity_risk": {
                    "copycat_penalty": 12,
                    "reason": "1 recent exact name/ticker duplicate(s)",
                    "exact_both": 1,
                }
            },
        )
        result = self.scorer.score(candidate, snapshot(), [], now=NOW)
        self.assertFalse(result.eligible)
        self.assertIn("exact name/ticker", " ".join(result.blockers))

    def test_missing_project_identity_does_not_alert_on_flow_alone(self):
        candidate = Candidate(
            chain="base", token_address=TOKEN, source="rpc-pairs:base:v2", launch_at=NOW
        )
        result = self.scorer.score(
            candidate,
            snapshot(
                social_links=0,
                smart_wallet_buys=0,
                exact_ca_mentions_5m=0,
                exact_ca_mentions_15m=0,
                credible_social_mentions_5m=0,
                creator_reputation=0,
                narrative_score=0,
            ),
            [],
            now=NOW,
        )
        self.assertFalse(result.eligible)
        self.assertIn("no project/social", " ".join(result.blockers))

    def test_large_volume_without_buyer_or_holder_inflection_does_not_alert(self):
        candidate = Candidate(chain="base", token_address=TOKEN, source="bankr", launch_at=NOW)
        result = self.scorer.score(
            candidate,
            snapshot(
                volume_5m_usd=100_000,
                unique_buyers_5m=0,
                unique_buyers_15m=0,
                net_new_wallets_5m=0,
                flow_checked=False,
            ),
            [],
            now=NOW,
        )
        self.assertFalse(result.eligible)
        self.assertIn("inflection not confirmed", " ".join(result.blockers))

    def test_accelerating_buyers_can_upgrade_a_candidate_to_action(self):
        candidate = Candidate(chain="base", token_address=TOKEN, source="bankr", launch_at=NOW)
        history = [snapshot(buys_5m=8, sells_5m=5, unique_buyers_5m=3) for _ in range(3)]
        result = self.scorer.score(candidate, snapshot(), history, now=NOW)
        self.assertTrue(result.eligible)
        self.assertIn(result.signal, {"ACTION", "A+"})

    def test_local_two_x_and_deployer_dump_are_penalised(self):
        candidate = Candidate(chain="base", token_address=TOKEN, source="bankr", launch_at=NOW)
        history = [snapshot(price_usd=0.0004)]
        extended = self.scorer.score(candidate, snapshot(price_usd=0.001), history, now=NOW)
        dumped = self.scorer.score(candidate, snapshot(deployer_sells_15m=1), [], now=NOW)
        self.assertIn("from its measured local base", " ".join(extended.blockers))
        self.assertFalse(dumped.eligible)
        self.assertIn("deployer sold", " ".join(dumped.blockers))

    def test_a_plus_requires_two_proven_wallets(self):
        candidate = Candidate(chain="base", token_address=TOKEN, source="bankr", launch_at=NOW)
        result = self.scorer.score(candidate, snapshot(smart_wallet_buys=1), [], now=NOW)
        self.assertNotEqual(result.signal, "A+")

    def test_verified_social_launch_with_exceptional_flow_can_emit_narrow_scout(self):
        candidate = Candidate(
            chain="robinhood",
            token_address=TOKEN,
            source="bankr",
            launch_at=NOW - timedelta(minutes=5),
            deployer="0x2222222222222222222222222222222222222222",
            metadata={"verified_platform_api": True},
        )
        market = snapshot(
            chain="robinhood",
            social_links=2,
            smart_wallet_buys=0,
            exact_ca_mentions_5m=0,
            exact_ca_mentions_15m=0,
            credible_social_mentions_5m=0,
            creator_reputation=0,
            narrative_score=0,
            raw={},
            buys_5m=18,
            sells_5m=2,
            unique_buyers_5m=12,
            unique_buyers_15m=16,
            net_new_wallets_5m=10,
            net_new_wallets_15m=13,
            liquidity_usd=15_000,
        )
        result = self.scorer.score(candidate, market, [], now=NOW)
        self.assertTrue(result.eligible)
        self.assertEqual(result.signal, "SCOUT")
        self.assertGreaterEqual(result.score, 60)
        self.assertLess(result.score, 70)
        self.assertIn("independent contract screen", result.upgrade_trigger)

    def test_exceptional_platform_flow_can_scout_below_standard_floor(self):
        candidate = Candidate(
            chain="robinhood",
            token_address=TOKEN,
            source="pons-v2",
            launch_at=NOW - timedelta(minutes=5),
            deployer="0x2222222222222222222222222222222222222222",
            metadata={"verified_platform_event": True, "platform_terms_verified": True},
        )
        platform_security = {
            "checked": True,
            "admin_checks_complete": False,
            "simulation_checked": False,
            "sell_simulation_success": False,
            "is_honeypot": False,
            "cannot_buy": False,
            "cannot_sell": False,
            "open_source": True,
            "buy_tax": 1,
            "sell_tax": 1,
            "platform_template": "pons-v2",
        }
        market = snapshot(
            chain="robinhood",
            social_links=1,
            smart_wallet_buys=0,
            exact_ca_mentions_5m=0,
            exact_ca_mentions_15m=0,
            credible_social_mentions_5m=0,
            creator_reputation=0,
            narrative_score=0,
            buys_5m=8,
            sells_5m=2,
            unique_buyers_5m=7,
            unique_buyers_15m=10,
            net_new_wallets_5m=8,
            net_new_wallets_15m=10,
            liquidity_usd=7_000,
            volume_5m_usd=1_000,
            raw={"security": platform_security},
        )
        result = self.scorer.score(candidate, market, [], now=NOW)
        self.assertTrue(result.eligible)
        self.assertEqual(result.signal, "SCOUT")
        self.assertGreaterEqual(result.score, 55)
        self.assertLess(result.score, 60)

    def test_exceptional_scout_still_rejects_low_liquidity_or_weak_flow(self):
        candidate = Candidate(
            chain="robinhood",
            token_address=TOKEN,
            source="pools-fun",
            launch_at=NOW,
            deployer="0x2222222222222222222222222222222222222222",
            metadata={"verified_platform_api": True, "platform_terms_verified": True},
        )
        result = self.scorer.score(
            candidate,
            snapshot(
                chain="robinhood",
                liquidity_usd=2_500,
                social_links=1,
                smart_wallet_buys=0,
                exact_ca_mentions_5m=0,
                exact_ca_mentions_15m=0,
                credible_social_mentions_5m=0,
                creator_reputation=0,
                narrative_score=0,
                buys_5m=8,
                sells_5m=2,
                unique_buyers_5m=7,
                unique_buyers_15m=10,
                net_new_wallets_5m=5,
                raw={},
            ),
            [],
            now=NOW,
        )
        self.assertFalse(result.eligible)

    def test_provenance_alone_is_not_a_scout_when_two_upgrade_gates_are_missing(self):
        candidate = Candidate(
            chain="robinhood",
            token_address=TOKEN,
            source="bankr",
            launch_at=NOW,
            deployer="0x2222222222222222222222222222222222222222",
            metadata={"verified_platform_api": True},
        )
        market = snapshot(
            chain="robinhood",
            social_links=0,
            smart_wallet_buys=0,
            exact_ca_mentions_5m=0,
            exact_ca_mentions_15m=0,
            credible_social_mentions_5m=0,
            creator_reputation=0,
            narrative_score=0,
            raw={},
        )
        result = self.scorer.score(candidate, market, [], now=NOW)
        self.assertFalse(result.eligible)
        self.assertEqual(result.signal, "MONITOR")

    def test_platform_provenance_cannot_become_action_without_contract_screen(self):
        candidate = Candidate(
            chain="robinhood",
            token_address=TOKEN,
            source="pons-v1",
            launch_at=NOW,
            deployer="0x2222222222222222222222222222222222222222",
            metadata={"verified_platform_event": True, "platform_terms_verified": True},
        )
        result = self.scorer.score(
            candidate, snapshot(chain="robinhood", raw={}), [], now=NOW
        )
        self.assertNotIn(result.signal, {"ACTION", "A+"})
        self.assertLess(result.score, 70)

    def test_gmgn_live_attention_can_emit_a_guarded_pulse(self):
        config = ScannerConfig(pulse_alert_score=48)
        scorer = SignalScorer(config)
        candidate = Candidate(
            chain="base",
            token_address=TOKEN,
            source="gmgn",
            launch_at=NOW - timedelta(minutes=8),
            deployer="0x2222222222222222222222222222222222222222",
            metadata={"gmgn_evidence": True},
        )
        market = snapshot(
            social_links=1,
            smart_wallet_buys=0,
            exact_ca_mentions_5m=0,
            exact_ca_mentions_15m=0,
            credible_social_mentions_5m=0,
            creator_reputation=0,
            narrative_score=0,
            unique_buyers_5m=0,
            unique_buyers_15m=0,
            net_new_wallets_5m=0,
            flow_checked=False,
            buys_5m=12,
            sells_5m=6,
            price_change_5m=12,
            raw={
                "security": CLEAN_SECURITY
                | {
                    "admin_checks_complete": False,
                    "simulation_checked": False,
                    "sell_simulation_success": False,
                    "top_unlocked_eoa_percent": 20,
                },
                "gmgn": {
                    "attention_rank": 9,
                    "recent_signal_types": [13],
                },
            },
        )
        history = [
            snapshot(
                buys_5m=4,
                sells_5m=4,
                price_usd=0.001,
                raw={"security": CLEAN_SECURITY, "gmgn": {"attention_rank": 24}},
            )
        ]
        result = scorer.score(candidate, market, history, now=NOW)
        self.assertTrue(result.eligible)
        self.assertEqual(result.signal, "PULSE")
        self.assertIsNotNone(result.upgrade_trigger)

    def test_direct_market_inflection_can_pulse_during_gmgn_outage(self):
        candidate = Candidate(
            chain="base",
            token_address=TOKEN,
            source="bankr",
            launch_at=NOW - timedelta(minutes=8),
            deployer="0x2222222222222222222222222222222222222222",
        )
        market = snapshot(
            social_links=1,
            smart_wallet_buys=0,
            exact_ca_mentions_5m=0,
            exact_ca_mentions_15m=0,
            credible_social_mentions_5m=0,
            creator_reputation=0,
            narrative_score=0,
            unique_buyers_5m=5,
            unique_buyers_15m=9,
            net_new_wallets_5m=5,
            buys_5m=12,
            sells_5m=5,
            flow_checked=True,
            raw={
                "security": CLEAN_SECURITY
                | {
                    "admin_checks_complete": False,
                    "simulation_checked": False,
                    "sell_simulation_success": False,
                    "top_unlocked_eoa_percent": 20,
                }
            },
        )
        history = [
            snapshot(
                buys_5m=5,
                sells_5m=5,
                volume_5m_usd=4_000,
                price_usd=0.001,
                unique_buyers_5m=2,
                unique_buyers_15m=5,
                net_new_wallets_5m=1,
            )
        ]
        result = self.scorer.score(candidate, market, history, now=NOW)
        self.assertTrue(result.eligible)
        self.assertEqual(result.signal, "PULSE")
        self.assertEqual(result.components["live_attention"], 4.0)

    def test_pulse_never_bypasses_concentration_or_vertical_move_guards(self):
        candidate = Candidate(
            chain="base",
            token_address=TOKEN,
            source="gmgn",
            launch_at=NOW - timedelta(minutes=8),
            deployer="0x2222222222222222222222222222222222222222",
            metadata={"gmgn_evidence": True},
        )
        unsafe = snapshot(
            price_change_5m=70,
            raw={
                "security": CLEAN_SECURITY | {"top_unlocked_eoa_percent": 42},
                "gmgn": {"attention_rank": 2, "recent_signal_types": [12, 13]},
            },
        )
        result = self.scorer.score(candidate, unsafe, [], now=NOW)
        self.assertFalse(result.eligible)
        self.assertNotEqual(result.signal, "PULSE")
