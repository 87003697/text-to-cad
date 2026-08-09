"""Regression tests for repository CI dependency setup."""

from __future__ import annotations

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]


class CiSetupTests(unittest.TestCase):
    def test_setup_deps_installs_playwright_chromium_for_browser_tests(self) -> None:
        action = (
            REPO_ROOT / ".github/actions/setup-deps/action.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "python -m playwright install --with-deps chromium",
            action,
        )


if __name__ == "__main__":
    unittest.main()
