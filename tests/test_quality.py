import unittest
from datetime import datetime, timedelta, timezone

from scanner.models import Candidate, MarketSnapshot, ScoreResult
from scanner.quality import apply_quality_trial
from scanner.state import SQLiteState


NOW = datetime(2026, 10, 29, 20, 30, tzinfo=timezone.utc)
TOKEN = "0x" + "3" * 40


def market(**changes):
    values = dict(chain="base", token_address=TOKEN, captured_at=NOW,
                  price_usd=0.001, liquidity_usd=12_000, market_cap_usd=80_000,
                  buys_5m=16, sells_5m=4, unique_buyers_5m=12,
                  flow_checked=True, raw={
                      "security": {"checked": True, "admin_checks_complete": True,
                                   "simulation_checked": True, "sell_simulation_success": True,
                                   "buy_tax": 0, "sell_tax": 0},
                      "verified_wallet_flow": {"buys": 1, "sells": 0,
                                               "captured_at": NOW.isoformat(),
                                               "paid_swap_verified": True,
                                               "swap_observed_at": NOW.isoformat()},
                  })
    values.update(changes)
    return MarketSnapshot(**values)


def verdict(signal="ACTION", eligible=True):
    return ScoreResult(78, "IGNITION", signal, eligible, 0, {}, [], [], "test")


def cited(snapshot, author, post_id, age_seconds=0):
    snapshot.raw.setdefault("social_evidence", []).append({
        "url": f"https://x.com/{author}/status/{post_id}", "tokenAddress": TOKEN,
        "publishedAt": (NOW - timedelta(seconds=age_seconds)).isoformat(),
    })


class QualityTrialTests(unittest.TestCase):
    def test_screenshot_patterns_stay_in_research_and_keep_baseline(self):
        for signal, buys, sells, unique, checked in (
            ("PULSE", 16, 0, 0, False), ("SCOUT", 12, 1, 5, True),
        ):
            with self.subTest(signal=signal):
                item = market(chain="robinhood", buys_5m=buys, sells_5m=sells,
                              unique_buyers_5m=unique, flow_checked=checked)
                item.raw.pop("verified_wallet_flow")
                item.raw["security"]["simulation_checked"] = False
                item.raw["security"]["sell_simulation_success"] = False
                item.raw["gmgn"] = {"attention_rank": 4, "smart_count": 1}
                result = apply_quality_trial(item, [], verdict(signal), NOW)
                self.assertFalse(result.eligible)
                self.assertTrue(item.raw["quality_trial"]["baseline_eligible"])
                self.assertEqual(result.signal, signal)

    def test_fresh_verified_wallet_can_qualify_without_waiting(self):
        self.assertTrue(apply_quality_trial(market(), [], verdict(), NOW).eligible)

    def test_plain_social_links_tags_or_overlay_counts_do_not_confirm(self):
        item = market(social_links=10, smart_wallet_buys=20)
        item.raw.pop("verified_wallet_flow")
        item.raw["gmgn"] = {"attention_rank": 1, "smart_count": 100}
        self.assertFalse(apply_quality_trial(item, [], verdict(), NOW).eligible)

    def test_pool_transfer_or_stale_paid_swap_cannot_confirm_an_entry(self):
        item = market()
        item.raw["verified_wallet_flow"]["paid_swap_verified"] = False
        self.assertFalse(apply_quality_trial(item, [], verdict(), NOW).eligible)
        item = market()
        item.raw["verified_wallet_flow"]["swap_observed_at"] = (
            NOW - timedelta(minutes=6)).isoformat()
        self.assertFalse(apply_quality_trial(item, [], verdict(), NOW).eligible)

    def test_social_promotion_requires_distinct_citations_and_persistence(self):
        item = market()
        item.raw.pop("verified_wallet_flow")
        cited(item, "builder_one", "101")
        cited(item, "builder_two", "102")
        self.assertFalse(apply_quality_trial(item, [], verdict(), NOW).eligible)
        prior = market(captured_at=NOW - timedelta(minutes=5))
        self.assertTrue(apply_quality_trial(item, [prior], verdict(), NOW).eligible)

    def test_duplicate_stale_wrong_token_and_fake_host_citations_do_not_count(self):
        item = market()
        item.raw.pop("verified_wallet_flow")
        cited(item, "same_author", "101")
        cited(item, "same_author", "102")
        cited(item, "other_author", "101")  # Same post ID cannot create another source.
        cited(item, "stale_author", "103", 301)
        cited(item, "wrong_token", "104")
        item.raw["social_evidence"][-1]["tokenAddress"] = "0x" + "4" * 40
        cited(item, "fake_host", "105")
        item.raw["social_evidence"][-1]["url"] = "https://x.com.fake.test/fake/status/105"
        prior = market(captured_at=NOW - timedelta(minutes=5))
        self.assertFalse(apply_quality_trial(item, [prior], verdict(), NOW).eligible)
        self.assertEqual(item.raw["quality_trial"]["cited_authors"], 1)

    def test_stale_market_flow_or_wallet_evidence_cannot_promote(self):
        old = (NOW - timedelta(minutes=6)).isoformat()
        for field in ("market", "flow", "wallet"):
            item = market()
            if field == "market":
                item.captured_at = NOW - timedelta(minutes=6)
            elif field == "flow":
                item.raw["flow_captured_at"] = old
            else:
                item.raw["verified_wallet_flow"]["captured_at"] = old
            with self.subTest(field=field):
                self.assertFalse(apply_quality_trial(item, [], verdict(), NOW).eligible)

    def test_unknown_tax_or_failed_simulation_cannot_promote(self):
        for field, value in (("buy_tax", None), ("sell_tax", float("nan")),
                             ("sell_simulation_success", False), ("cannot_sell", True)):
            item = market()
            item.raw["security"][field] = value
            with self.subTest(field=field):
                self.assertFalse(apply_quality_trial(item, [], verdict(), NOW).eligible)

    def test_selling_and_repeated_buy_activity_block_main_feed(self):
        item = market(unique_buyers_5m=5, buys_5m=30)
        self.assertFalse(apply_quality_trial(item, [], verdict(), NOW).eligible)
        item = market()
        item.raw["verified_wallet_flow"]["sells"] = 1
        self.assertFalse(apply_quality_trial(item, [], verdict(), NOW).eligible)

    def test_baseline_rejection_is_never_overridden(self):
        self.assertFalse(apply_quality_trial(market(), [], verdict(eligible=False), NOW).eligible)

    def test_policy_decisions_survive_sqlite_reopen_with_comparison_counts(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "research.sqlite3")
            state = SQLiteState(path)
            state.upsert_candidate(Candidate(chain="base", token_address=TOKEN, source="test"))
            now = datetime.now(timezone.utc)
            for signal in ("PULSE", "ACTION"):
                item = market(captured_at=now)
                item.raw["verified_wallet_flow"]["captured_at"] = now.isoformat()
                item.raw["verified_wallet_flow"]["swap_observed_at"] = now.isoformat()
                apply_quality_trial(item, [], verdict(signal), now)
                state.add_snapshot(f"base:{TOKEN}", item)
            state.close()
            reopened = SQLiteState(path)
            try:
                report = reopened.quality_trial_report()
                self.assertEqual(report["baseline_eligible_24h"], 2)
                self.assertEqual(report["main_eligible_24h"], 1)
                self.assertEqual(report["research_only_24h"], 1)
                self.assertEqual(len(reopened.recent_snapshots(f"base:{TOKEN}")), 2)
            finally:
                reopened.close()
