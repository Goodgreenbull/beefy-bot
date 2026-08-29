import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".example", ".json", ".md", ".py", ".txt", ".yaml", ".yml"}
SKIP_DIRECTORIES = {".git", ".venv", "__pycache__", ".pytest_cache"}
SECRET_PATTERNS = {
    "Telegram bot token": re.compile(r"(?<![A-Za-z0-9_-])\d{6,12}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_-])"),
    "EVM private key": re.compile(
        r"(?i)[\"']?(?:private[_-]?key|wallet[_-]?key)[\"']?\s*[:=]\s*"
        r"[\"'](?:0x)?[A-Fa-f0-9]{64}[\"']"
    ),
}


class CurrentTreeSecretTests(unittest.TestCase):
    def test_source_tree_contains_no_token_or_wallet_key_values(self):
        findings: list[str] = []
        for path in REPO_ROOT.rglob("*"):
            if not path.is_file() or any(part in SKIP_DIRECTORIES for part in path.parts):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
            for label, pattern in SECRET_PATTERNS.items():
                if pattern.search(content):
                    findings.append(f"{label}: {path.relative_to(REPO_ROOT)}")

        self.assertEqual(findings, [], "Potential committed secrets found: " + ", ".join(findings))
