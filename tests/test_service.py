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
            token_address=candidate.token_address,
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
            raw={
                "security": {
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
            },
            unique_buyers_5m=10,
            unique_buyers_15m=16,
            unique_sellers_5m=2,
            unique_sellers_15m=4,
            net_new_wallets_5m=8,
            net_new_wallets_15m=12,
            exact_ca_mentions_5m=3,
            exact_ca_mentions_15m=4,
            credible_social_mentions_5m=1,
            creator_reputation=0.7,
            narrative_score=0.7,
            flow_checked=True,
        )


class ScannerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cycle_scores_alerts_and_deduplicates_without_trading(self):
        state = SQLiteState(":memory:")
        config = ScannerConfig(active_candidate_limit=5, min_alert_score=60, warmup_cycles=0)
        alerts = []
        state.mark_feed_error("telegram-alerts", RuntimeError("previous delivery failed"))

        async def capture(candidate, market, score):
            alerts.append((candidate.key, score.signal))

        service = ScannerService(config, state, capture)
        service.feeds = [FakeFeed()]
        service.enricher = FakeEnricher()
        try:
            first = await service.run_cycle()
            second = await service.run_cycle()
            telegram_health = next(
                item for item in state.health() if item["feed_name"] == "telegram-alerts"
            )
        finally:
            await service.stop()

        self.assertEqual(first["alerts"], 1)
        self.assertEqual(second["alerts"], 0)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][0], f"base:{TOKEN}")
        self.assertIsNone(telegram_health["last_error"])

    async def test_fresh_deploy_backlog_cannot_repeat_as_a_new_launch(self):
        state = SQLiteState(":memory:")
        config = ScannerConfig(active_candidate_limit=5, min_alert_score=60, warmup_cycles=1)
        alerts = []

        async def capture(candidate, market, score):
            alerts.append(candidate.key)

        service = ScannerService(config, state, capture)
        service.feeds = [FakeFeed()]
        service.enricher = FakeEnricher()
        try:
            first = await service.run_cycle()
            second = await service.run_cycle()

            new_token = "0x9999999999999999999999999999999999999999"

            class NewFeed:
                name = "new-launch"

                async def discover(self, session, scanner_state):
                    return [
                        Candidate(
                            chain="base",
                            token_address=new_token,
                            source="bankr",
                            symbol="NEW",
                        )
                    ]

            service.feeds = [NewFeed()]
            third = await service.run_cycle()
        finally:
            await service.stop()

        self.assertEqual(first["alerts"], 0)
        self.assertEqual(second["alerts"], 0)
        self.assertEqual(third["alerts"], 1)
        self.assertEqual(alerts, [f"base:{new_token}"])
