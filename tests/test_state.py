import unittest
from datetime import datetime, timedelta, timezone

from scanner.models import Candidate, MarketSnapshot, ScoreResult
from scanner.config import ScannerConfig
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

    def test_alert_dedup_blocks_same_token_for_24h_then_allows_reawakening(self):
        initial = ScoreResult(72, "IGNITION", "EARLY WATCH", True, 0, {}, [], [], "test")
        same = ScoreResult(75, "IGNITION", "EARLY WATCH", True, 0, {}, [], [], "test")
        upgrade = ScoreResult(84, "IGNITION", "STRONG WATCH", True, 0, {}, [], [], "test")
        self.assertTrue(self.state.alert_allowed(self.candidate.key, initial, 45, 10))
        self.state.record_alert(self.candidate.key, initial)
        self.assertFalse(self.state.alert_allowed(self.candidate.key, same, 45, 10))
        self.assertFalse(self.state.alert_allowed(self.candidate.key, upgrade, 45, 10))
        self.state.connection.execute(
            "UPDATE alerts SET sent_at = ?",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),),
        )
        self.state.connection.commit()
        self.assertFalse(self.state.alert_allowed(self.candidate.key, same, 45, 10))
        reawakening = ScoreResult(75, "REAWAKENING", "EARLY WATCH", True, 0, {}, [], [], "test")
        self.assertFalse(self.state.alert_allowed(self.candidate.key, reawakening, 45, 10))
        self.state.connection.execute(
            "UPDATE alerts SET sent_at = ?",
            ((datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),),
        )
        self.state.connection.commit()
        self.assertTrue(self.state.alert_allowed(self.candidate.key, reawakening, 45, 10))

    def test_cursor_and_feed_health_persist(self):
        self.state.set_cursor("feed", "42")
        self.state.mark_feed_success("feed", 3)
        self.assertEqual(self.state.get_cursor("feed"), "42")
        self.assertEqual(self.state.health()[0]["items_seen"], 3)

    def test_operator_rejected_spacex_usd_and_oil_themes_are_hard_blocked(self):
        rows = [
            Candidate(chain="base", token_address=f"0x{index + 300:040x}", source="bankr", name=name, symbol=symbol)
            for index, (name, symbol) in enumerate(
                (("Space X Official", "SPACEX"), ("United States Dollar", "USD"), ("US Crude Oil", "WTI"))
            )
        ]
        for candidate in rows:
            risk = self.state.identity_risk(candidate)
            self.assertTrue(risk["blocked_theme"])
            self.assertGreaterEqual(risk["copycat_penalty"], 60)

    def test_near_misses_persist_the_real_quality_blocker(self):
        result = ScoreResult(
            64,
            "IGNITION",
            "MONITOR",
            False,
            0,
            {"buyer_velocity": 12},
            ["buyer velocity"],
            ["contract safety not confirmed yet"],
            "test",
        )
        self.state.update_score(self.candidate.key, result)
        rows = self.state.near_misses()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["score"], 64)
        self.assertEqual(rows[0]["blockers"][0], "contract safety not confirmed yet")

    def test_alert_outcomes_track_all_horizons_mfe_mae_and_wallet_reputation(self):
        score = ScoreResult(84, "IGNITION", "STRONG WATCH", True, 0, {}, [], [], "test")
        entry = MarketSnapshot(
            chain="base",
            token_address=TOKEN,
            price_usd=1.0,
            liquidity_usd=10_000,
            market_cap_usd=100_000,
        )
        alert_ids = [self.state.record_alert(self.candidate.key, score, entry) for _ in range(3)]
        wallet = "0x7777777777777777777777777777777777777777"
        sent_at = datetime.now(timezone.utc) - timedelta(hours=24)
        for alert_id in alert_ids:
            self.state.connection.execute(
                "UPDATE alerts SET sent_at = ? WHERE id = ?", (sent_at.isoformat(), alert_id)
            )
            self.state.record_alert_wallets(alert_id, "base", {wallet})
        self.state.connection.commit()

        prices = ((15, 1.10), (60, 0.80), (360, 1.50), (1440, 1.20))
        for minutes, price in prices:
            self.state.update_alert_outcomes(
                self.candidate.key,
                MarketSnapshot(
                    chain="base",
                    token_address=TOKEN,
                    captured_at=sent_at + timedelta(minutes=minutes),
                    price_usd=price,
                    liquidity_usd=10_000,
                    market_cap_usd=100_000 * price,
                ),
            )

        report = self.state.outcome_report()
        self.assertEqual(report["outcome_counts"], {15: 3, 60: 3, 360: 3, 1440: 3})
        alert = self.state.connection.execute(
            "SELECT mfe_pct, mae_pct FROM alerts WHERE id = ?", (alert_ids[0],)
        ).fetchone()
        self.assertAlmostEqual(alert["mfe_pct"], 50.0)
        self.assertAlmostEqual(alert["mae_pct"], -20.0)
        horizon_rows = self.state.connection.execute(
            """
            SELECT horizon_minutes, mfe_pct, mae_pct
            FROM alert_outcomes WHERE alert_id = ? ORDER BY horizon_minutes
            """,
            (alert_ids[0],),
        ).fetchall()
        expected_horizons = [(15, 10.0, 0.0), (60, 10.0, -20.0), (360, 50.0, -20.0), (1440, 50.0, -20.0)]
        for row, expected in zip(horizon_rows, expected_horizons):
            self.assertEqual(row["horizon_minutes"], expected[0])
            self.assertAlmostEqual(row["mfe_pct"], expected[1])
            self.assertAlmostEqual(row["mae_pct"], expected[2])
        # Repeat alerts for the same token count as one wallet observation.
        self.assertNotIn(wallet, self.state.curated_smart_wallets())

        for index in range(2):
            token = f"0x{index + 900:040x}"
            candidate = Candidate(chain="base", token_address=token, source="bankr")
            self.state.upsert_candidate(candidate)
            alert_id = self.state.record_alert(
                candidate.key,
                score,
                MarketSnapshot(chain="base", token_address=token, price_usd=1.0),
            )
            self.state.connection.execute(
                "UPDATE alerts SET sent_at = ? WHERE id = ?", (sent_at.isoformat(), alert_id)
            )
            self.state.record_alert_wallets(alert_id, "base", {wallet})
            self.state.update_alert_outcomes(
                candidate.key,
                MarketSnapshot(
                    chain="base",
                    token_address=token,
                    captured_at=sent_at + timedelta(hours=24),
                    price_usd=1.3,
                ),
            )

        self.assertIn(wallet, self.state.curated_smart_wallets())
        reputation = self.state.connection.execute(
            "SELECT evaluated_alerts FROM wallet_reputation WHERE wallet = ?", (wallet,)
        ).fetchone()
        self.assertEqual(reputation["evaluated_alerts"], 3)

    def test_confirmed_market_disappearance_records_terminal_loss(self):
        score = ScoreResult(84, "IGNITION", "STRONG WATCH", True, 0, {}, [], [], "test")
        sent_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        alert_id = self.state.record_alert(
            self.candidate.key,
            score,
            MarketSnapshot(chain="base", token_address=TOKEN, price_usd=1.0),
        )
        self.state.connection.execute(
            "UPDATE alerts SET sent_at = ? WHERE id = ?", (sent_at.isoformat(), alert_id)
        )
        self.state.connection.commit()

        self.assertEqual(self.state.record_missing_market(self.candidate.key, sent_at + timedelta(minutes=16)), 0)
        self.assertEqual(self.state.record_missing_market(self.candidate.key, sent_at + timedelta(minutes=17)), 0)
        inserted = self.state.record_missing_market(
            self.candidate.key, sent_at + timedelta(minutes=18)
        )
        self.assertEqual(inserted, 1)
        outcome = self.state.connection.execute(
            "SELECT return_pct FROM alert_outcomes WHERE alert_id = ? AND horizon_minutes = 15",
            (alert_id,),
        ).fetchone()
        self.assertEqual(outcome["return_pct"], -100.0)

    def test_market_cap_audit_separates_detection_alert_current_and_peak(self):
        detected = MarketSnapshot(
            chain="base", token_address=TOKEN, price_usd=0.5, market_cap_usd=50_000
        )
        self.state.add_snapshot(self.candidate.key, detected)
        result = ScoreResult(75, "IGNITION", "ACTION", True, 0, {}, [], [], "test")
        alert_id = self.state.record_alert(
            self.candidate.key,
            result,
            MarketSnapshot(
                chain="base", token_address=TOKEN, price_usd=1.0, market_cap_usd=80_000
            ),
        )
        sent_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        self.state.connection.execute(
            "UPDATE alerts SET sent_at = ? WHERE id = ?", (sent_at.isoformat(), alert_id)
        )
        self.state.connection.commit()
        self.state.update_alert_outcomes(
            self.candidate.key,
            MarketSnapshot(
                chain="base",
                token_address=TOKEN,
                captured_at=sent_at + timedelta(minutes=15),
                price_usd=1.25,
                market_cap_usd=100_000,
            ),
        )
        self.state.update_alert_outcomes(
            self.candidate.key,
            MarketSnapshot(
                chain="base",
                token_address=TOKEN,
                captured_at=sent_at + timedelta(minutes=16),
                price_usd=0.875,
                market_cap_usd=70_000,
            ),
        )
        row = self.state.connection.execute(
            """
            SELECT first_detected_market_cap_usd, alert_market_cap_usd,
                   current_market_cap_usd, peak_after_alert_market_cap_usd
            FROM alerts WHERE id = ?
            """,
            (alert_id,),
        ).fetchone()
        self.assertEqual(tuple(row), (50_000, 80_000, 70_000, 100_000))

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

    def test_threshold_calibration_waits_for_a_real_sample_then_raises_quality_bar(self):
        config = ScannerConfig(calibration_min_samples=30)
        before = self.state.calibrated_thresholds(config)
        self.assertFalse(before["calibrated"])

        sent_at = datetime.now(timezone.utc) - timedelta(hours=24)
        for index in range(40):
            score_value = 74 + (index % 6) if index < 21 else 80 + (index % 10)
            result = ScoreResult(
                score_value, "IGNITION", "EARLY WATCH", True, 0, {}, [], [], "test"
            )
            alert_id = self.state.record_alert(
                self.candidate.key,
                result,
                MarketSnapshot(chain="base", token_address=TOKEN, price_usd=1.0),
            )
            realised = 30.0 if score_value >= 80 else -25.0
            self.state.connection.execute(
                "UPDATE alerts SET sent_at = ?, mfe_pct = 45, mae_pct = -12 WHERE id = ?",
                (sent_at.isoformat(), alert_id),
            )
            self.state.connection.execute(
                """
                INSERT INTO alert_outcomes(
                    alert_id, horizon_minutes, due_at, captured_at,
                    price_usd, market_cap_usd, liquidity_usd, return_pct,
                    capture_lag_minutes, mfe_pct, mae_pct
                ) VALUES (?, 1440, ?, ?, ?, 100000, 10000, ?, 0, 45, -12)
                """,
                (
                    alert_id,
                    (sent_at + timedelta(hours=24)).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    1 + realised / 100,
                    realised,
                ),
            )
        self.state.connection.commit()

        calibrated = self.state.calibrated_thresholds(config)
        self.assertTrue(calibrated["calibrated"])
        self.assertEqual(calibrated["samples"], 40)
        self.assertEqual(calibrated["scout"], 60)
        self.assertEqual(calibrated["watch"], 70)
        self.assertEqual(calibrated["buy"], 80)

    def test_target_estimate_uses_distinct_comparable_24h_outcomes(self):
        sent_at = datetime.now(timezone.utc) - timedelta(hours=25)
        result = ScoreResult(
            88, "IGNITION", "STRONG WATCH", True, 0, {}, [], [], "test"
        )
        for index in range(5):
            token = f"0x{index + 600:040x}"
            candidate = Candidate(chain="base", token_address=token, source="bankr")
            self.state.upsert_candidate(candidate)
            alert_id = self.state.record_alert(
                candidate.key,
                result,
                MarketSnapshot(chain="base", token_address=token, price_usd=1.0),
            )
            self.state.connection.execute(
                "UPDATE alerts SET sent_at = ? WHERE id = ?",
                (sent_at.isoformat(), alert_id),
            )
            self.state.connection.execute(
                """
                INSERT INTO alert_outcomes(
                    alert_id, horizon_minutes, due_at, captured_at,
                    price_usd, market_cap_usd, liquidity_usd, return_pct,
                    capture_lag_minutes, mfe_pct, mae_pct
                ) VALUES (?, 1440, ?, ?, 10, 1000000, 20000, 900, 0, ?, -20)
                """,
                (
                    alert_id,
                    (sent_at + timedelta(hours=24)).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    900.0 + index * 10,
                ),
            )
        self.state.connection.commit()

        current = ScoreResult(
            90, "IGNITION", "STRONG WATCH", True, 0, {}, [], [], "test"
        )
        snapshot = MarketSnapshot(
            chain="base",
            token_address=TOKEN,
            market_cap_usd=100_000,
            liquidity_usd=20_000,
            volume_5m_usd=8_000,
            volume_1h_usd=20_000,
            buys_5m=20,
            sells_5m=5,
        )
        self.state.apply_target_estimate(self.candidate, snapshot, current)

        self.assertGreater(current.target_multiple or 0, 5.0)
        self.assertEqual(current.target_confidence, "LOW")
        self.assertIn("5 comparable 24h outcomes", current.target_basis)
