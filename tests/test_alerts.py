import unittest

from scanner.alerts import format_alert
from scanner.models import Candidate, MarketSnapshot, ScoreResult


class AlertFormatTests(unittest.TestCase):
    def test_alert_is_concise_and_html_escapes_token_text(self):
        candidate = Candidate(
            chain="base",
            token_address="0x7777777777777777777777777777777777777777",
            source="bankr",
            name="A&B",
            symbol="T<ST",
            metadata={"first_detected_market_cap_usd": 40_000},
        )
        market = MarketSnapshot(
            chain="base",
            token_address=candidate.token_address,
            liquidity_usd=10_000,
            market_cap_usd=50_000,
            volume_5m_usd=5_000,
            buys_5m=10,
            sells_5m=2,
            unique_buyers_5m=8,
            unique_buyers_15m=12,
            net_new_wallets_5m=6,
            flow_checked=True,
        )
        result = ScoreResult(76, "IGNITION", "ACTION", True, 0, {}, ["fresh"], [], "flow fails")
        message = format_alert(candidate, market, result)
        self.assertIn("A&amp;B", message)
        self.assertIn("T&lt;ST", message)
        self.assertIn("Beefy ACTION", message)
        self.assertIn("model upside", message)
        self.assertIn("8 unique buyers/5m", message)
        self.assertIn("MC first $40.0k · alert $50.0k", message)
        self.assertNotIn("Strong qualifying flow", message)
        self.assertIn("<b>CA:</b>", message)
        self.assertIn("no auto-trading", message)
        self.assertLess(len(message), 1_000)

    def test_early_watch_uses_watch_verdict(self):
        candidate = Candidate(
            chain="base",
            token_address="0x9999999999999999999999999999999999999999",
            source="geckoterminal:base",
            symbol="WAIT",
        )
        market = MarketSnapshot(chain="base", token_address=candidate.token_address)
        result = ScoreResult(
            65,
            "IGNITION",
            "SCOUT",
            True,
            0,
            {},
            ["fresh"],
            [],
            "flow fails",
            upgrade_trigger="5m unique buyers reach 5 while buy share remains at least 60%",
        )
        message = format_alert(candidate, market, result)
        self.assertIn("Beefy SCOUT", message)
        self.assertIn("1.2x model upside", message)
        self.assertIn("Upgrade trigger", message)

    def test_watch_summary_changes_for_a_pullback_setup(self):
        candidate = Candidate(
            chain="base",
            token_address="0x1111111111111111111111111111111111111111",
            source="bankr",
            symbol="DIP",
        )
        market = MarketSnapshot(
            chain="base",
            token_address=candidate.token_address,
            liquidity_usd=12_000,
            market_cap_usd=100_000,
            volume_5m_usd=5_000,
            buys_5m=14,
            sells_5m=5,
            price_change_5m=28,
            social_links=2,
        )
        result = ScoreResult(77, "IGNITION", "ACTION", True, 0, {}, [], [], "flow fails")
        message = format_alert(candidate, market, result)
        self.assertIn("Beefy ACTION", message)
        self.assertIn("model upside", message)
        self.assertNotIn("conviction is not high enough", message)

    def test_alert_uses_history_aware_uncapped_target(self):
        candidate = Candidate(
            chain="base",
            token_address="0x2222222222222222222222222222222222222222",
            source="o1-b20",
            symbol="RUN",
        )
        market = MarketSnapshot(
            chain="base",
            token_address=candidate.token_address,
            liquidity_usd=20_000,
            market_cap_usd=80_000,
            volume_5m_usd=18_000,
            buys_5m=28,
            sells_5m=5,
        )
        result = ScoreResult(
            92,
            "IGNITION",
            "A+",
            True,
            0,
            {},
            ["fresh"],
            [],
            "flow fails",
            target_multiple=10.4,
            target_confidence="HIGH",
            target_basis="24 comparable 24h outcomes + live liquidity/flow structure",
        )
        message = format_alert(candidate, market, result)
        self.assertIn("10.4x model upside", message)
        self.assertIn("[HIGH]", message)
        self.assertIn("24 comparable 24h outcomes", message)
