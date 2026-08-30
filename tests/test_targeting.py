import unittest

from scanner.models import Candidate, MarketSnapshot, ScoreResult
from scanner.targeting import combine_target, structural_target


class TargetingTests(unittest.TestCase):
    def test_supported_live_structure_can_produce_a_target_above_10x(self):
        candidate = Candidate(
            chain="base",
            token_address="0x3333333333333333333333333333333333333333",
            source="o1-b20",
        )
        snapshot = MarketSnapshot(
            chain="base",
            token_address=candidate.token_address,
            market_cap_usd=20_000,
            liquidity_usd=8_000,
            volume_5m_usd=10_000,
            volume_1h_usd=25_000,
            buys_5m=30,
            sells_5m=5,
            social_links=3,
            smart_wallet_buys=2,
        )
        result = ScoreResult(
            95, "IGNITION", "A+", True, 0, {}, [], [], "flow fails"
        )

        self.assertGreaterEqual(structural_target(candidate, snapshot, result), 10.0)

    def test_comparable_results_can_support_a_10x_target(self):
        historical_mfe = [850.0 + index * 10 for index in range(20)]

        target, confidence = combine_target(9.0, historical_mfe)

        self.assertGreaterEqual(target, 10.0)
        self.assertEqual(confidence, "HIGH")

    def test_missing_valuation_does_not_invent_a_large_target(self):
        candidate = Candidate(
            chain="base",
            token_address="0x4444444444444444444444444444444444444444",
            source="geckoterminal:base",
        )
        snapshot = MarketSnapshot(chain="base", token_address=candidate.token_address)
        result = ScoreResult(
            90, "IGNITION", "STRONG WATCH", True, 0, {}, [], [], "flow fails"
        )

        self.assertEqual(structural_target(candidate, snapshot, result), 1.2)


if __name__ == "__main__":
    unittest.main()
