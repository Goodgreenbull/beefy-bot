import os
import unittest
from unittest.mock import patch

from scanner.config import ScannerConfig


class ScannerConfigTests(unittest.TestCase):
    def test_free_profile_defaults_to_five_minutes_and_existing_admin_chat(self):
        with patch.dict(os.environ, {"ADMIN_CHAT_ID": "12345"}, clear=True):
            config = ScannerConfig.from_env()
        self.assertTrue(config.enabled)
        self.assertEqual(config.interval_seconds, 300)
        self.assertEqual(config.active_candidate_limit, 50)
        self.assertEqual(config.alert_chat_id, "12345")
        self.assertEqual(config.max_alerts_per_cycle, 3)
        self.assertEqual(config.warmup_cycles, 1)

    def test_explicit_signal_chat_wins_without_exposing_credentials(self):
        values = {
            "SIGNAL_TELEGRAM_CHAT_ID": "signal-chat",
            "TELEGRAM_GROUP_ID": "group-chat",
            "ADMIN_CHAT_ID": "admin-chat",
            "SCANNER_INTERVAL_SECONDS": "9000",
        }
        with patch.dict(os.environ, values, clear=True):
            config = ScannerConfig.from_env()
        self.assertEqual(config.alert_chat_id, "signal-chat")
        self.assertEqual(config.interval_seconds, 600)

    def test_private_admin_chat_wins_over_group_for_alerts(self):
        values = {"TELEGRAM_GROUP_ID": "group-chat", "ADMIN_CHAT_ID": "admin-chat"}
        with patch.dict(os.environ, values, clear=True):
            config = ScannerConfig.from_env()
        self.assertEqual(config.alert_chat_id, "admin-chat")
