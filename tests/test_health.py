import unittest
from datetime import datetime, timedelta, timezone

from scanner.health import public_diagnostics


class HealthTests(unittest.TestCase):
    def test_recent_cycle_does_not_hide_wallet_failure_or_expose_credentials(self):
        now = datetime.now(timezone.utc)
        status = {"last_cycle_at": now.isoformat(), "feeds": [{
            "feed_name": "wallet-swap-discovery:base",
            "last_error": "HTTP 429 at https://provider.example/SECRET",
        }]}
        health = public_diagnostics(status, 300, now)
        self.assertTrue(health["scanner_cycle_fresh"])
        self.assertEqual(health["feed_errors"], {"wallet-swap-discovery:base": "rate_limited"})
        self.assertNotIn("SECRET", str(health))

    def test_stale_cycle_and_unverified_delivery_are_visible(self):
        now = datetime.now(timezone.utc)
        health = public_diagnostics({"last_cycle_at": (now-timedelta(hours=1)).isoformat()}, 300, now)
        self.assertFalse(health["scanner_cycle_fresh"])
        self.assertEqual(health["telegram_delivery"], "pending")

    def test_recovery_test_records_real_telegram_success(self):
        stamp = datetime.now(timezone.utc).isoformat()
        health = public_diagnostics({"feeds": [{"feed_name": "telegram-recovery", "last_success_at": stamp}]}, 300)
        self.assertEqual(health["telegram_delivery"], "healthy")
        self.assertEqual(health["telegram_last_success_at"], stamp)
