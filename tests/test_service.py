import unittest

from scanner.config import ScannerConfig
from scanner.models import Candidate, MarketSnapshot
from scanner.service import ScannerService
from scanner.state import SQLiteState


TOKEN = "0x8888888888888888888888888888888888888888"


class FakeFeed:
    name = "fake-launches"

    async def discover(self, session, state):
        return [Candidate(chain="base", token_address=TOKEN, source="bankr", symbol="TEST")]


class FakeEnricher:
    async def enrich(self, session, candidate):
        return MarketSnapshot(
            chain="base",
            token_address=TOKEN,
            price_usd=0.001,
            liquidity_usd=10_000,
            market_cap_usd=75_000,
            volume_5m_usd=7_500,
            volume_1h_usd=20_000,
            buys_5m=24,
            sells_5m=6,
            price_change_5m=12,
            price_change_1h=35,
            social_links=3,
            smart_wallet_buys=2,
            source="fake-market",
        )


class ScannerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cycle_scores_alerts_and_deduplicates_without_trading(self):
        state = SQLiteState(":memory:")
        config = ScannerConfig(active_candidate_limit=5, min_alert_score=60, warmup_cycles=0)
        alerts = []

        async def capture(candidate, market, score):
            alerts.append((candidate.key, score.signal))

        service = ScannerService(config, state, capture)
        service.feeds = [FakeFeed()]
        service.enricher = FakeEnricher()
        try:
            first = await service.run_cycle()
            second = await service.run_cycle()
        finally:
            await service.stop()

        self.assertEqual(first["alerts"], 1)
        self.assertEqual(second["alerts"], 0)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][0], f"base:{TOKEN}")
