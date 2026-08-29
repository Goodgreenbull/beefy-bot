import unittest
from datetime import datetime, timedelta, timezone

from scanner.models import Candidate, MarketSnapshot, ScoreResult
from scanner.state import SQLiteState


TOKEN = "0x2222222222222222222222222222222222222222"


class SQLiteStateTests(unittest.TestCase):
    def setUp(self):
        self.state = SQLiteState(":memory:")
        self.candidate = Candidate(chain="base", token_address=TOKEN, source="bankr")
        self.state.upsert_candidate(self.candidate)

    def tearDown(self):
        self.state.close()

    def test_candidate_sources_merge_and_snapshot_round_trips(self):
        duplicate = Candidate(
            chain="base",
            token_address=TOKEN.upper().replace("0X", "0x"),
            source="geckoterminal:base",
            symbol="TEST",
        )
        self.state.upsert_candidate(duplicate)
        active = self.state.list_active_candidates(24, 10)
        self.assertEqual(len(active), 1)
        self.assertIn("bankr", active[0].source)
        self.assertIn("geckoterminal:base", active[0].source)

        market = MarketSnapshot(
            chain="base",
            token_address=TOKEN,
            liquidity_usd=10_000,
            volume_5m_usd=2_000,
            buys_5m=8,
            sells_5m=2,
        )
        self.state.add_snapshot(self.candidate.key, market)
        saved = self.state.recent_snapshots(self.candidate.key)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].buys_5m, 8)

    def test_alert_dedup_allows_only_material_upgrade_during_cooldown(self):
        initial = ScoreResult(72, "IGNITION", "EARLY WATCH", True, 0, {}, [], [], "test")
        same = ScoreResult(75, "IGNITION", "EARLY WATCH", True, 0, {}, [], [], "test")
        upgrade = ScoreResult(84, "IGNITION", "STRONG WATCH", True, 0, {}, [], [], "test")
        self.assertTrue(self.state.alert_allowed(self.candidate.key, initial, 45, 10))
        self.state.record_alert(self.candidate.key, initial)
        self.assertFalse(self.state.alert_allowed(self.candidate.key, same, 45, 10))
        self.assertTrue(self.state.alert_allowed(self.candidate.key, upgrade, 45, 10))

    def test_cursor_and_feed_health_persist(self):
        self.state.set_cursor("feed", "42")
        self.state.mark_feed_success("feed", 3)
        self.assertEqual(self.state.get_cursor("feed"), "42")
        self.assertEqual(self.state.health()[0]["items_seen"], 3)

    def test_active_candidates_balance_fresh_and_rotating_rechecks(self):
        self.state.connection.execute("DELETE FROM candidates")
        self.state.connection.commit()
        now = datetime.now(timezone.utc)
        keys = []
        for index in range(8):
            candidate = Candidate(
                chain="base",
                token_address=f"0x{index + 10:040x}",
                source="rpc-pairs:base:v2",
                discovered_at=now - timedelta(minutes=index + 1),
                launch_at=now - timedelta(minutes=index + 1),
            )
            self.state.upsert_candidate(candidate)
            keys.append(candidate.key)
            if index >= 4:
                self.state.add_snapshot(
                    candidate.key,
                    MarketSnapshot(
                        chain="base",
                        token_address=candidate.token_address,
                        captured_at=now - timedelta(minutes=20 - index),
                    ),
                )

        active = self.state.list_active_candidates(24, 6)
        active_keys = {candidate.key for candidate in active}
        self.assertEqual(len(active), 6)
        self.assertTrue(set(keys[:4]).issubset(active_keys))
        self.assertIn(keys[4], active_keys)
        self.assertIn(keys[5], active_keys)
