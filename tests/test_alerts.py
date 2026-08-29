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
        )
        market = MarketSnapshot(
            chain="base",
            token_address=candidate.token_address,
            liquidity_usd=10_000,
            market_cap_usd=50_000,
            volume_5m_usd=5_000,
            buys_5m=10,
            sells_5m=2,
        )
        result = ScoreResult(80, "IGNITION", "STRONG WATCH", True, 0, {}, ["fresh"], [], "flow fails")
        message = format_alert(candidate, market, result)
        self.assertIn("A&amp;B", message)
        self.assertIn("T&lt;ST", message)
        self.assertIn("Beefy Call: BUY", message)
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
        result = ScoreResult(72, "IGNITION", "EARLY WATCH", True, 0, {}, ["fresh"], [], "flow fails")
        message = format_alert(candidate, market, result)
        self.assertIn("Beefy Verdict: WATCH FOR NOW", message)
        self.assertNotIn("Beefy Call: BUY", message)
