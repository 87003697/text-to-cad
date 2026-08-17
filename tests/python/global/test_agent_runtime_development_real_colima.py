from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def load_module():
    path = Path("scripts/pilot/agent-runtime-development-real-colima.py").resolve()
    spec = importlib.util.spec_from_file_location("development_real_colima", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DevelopmentRealColimaLauncherTests(unittest.TestCase):
    def test_secret_env_parser_returns_only_venus_token_without_shell_evaluation(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider.env"
            path.write_text(
                "# private provider config\nOTHER=value\nexport VENUS_TOKEN='opaque-token-value'\n",
                encoding="utf-8",
            )
            self.assertEqual(module.read_venus_token(path), "opaque-token-value")
            path.write_text("VENUS_TOKEN=$(unsafe)\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "literal"):
                module.read_venus_token(path)

    def test_secret_env_parser_rejects_missing_duplicate_empty_crlf_and_symlink(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "provider.env"
            for payload in (
                "OTHER=x\n",
                "VENUS_TOKEN=\n",
                "VENUS_TOKEN=a\nVENUS_TOKEN=b\n",
                "VENUS_TOKEN=a\r\n",
            ):
                path.write_bytes(payload.encode())
                with self.assertRaises(ValueError):
                    module.read_venus_token(path)
            target = root / "target"
            target.write_text("VENUS_TOKEN=a", encoding="utf-8")
            path.unlink()
            path.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "regular"):
                module.read_venus_token(path)

    def test_public_accounting_uses_upper_bound_and_never_claims_actual_usd(self):
        module = load_module()
        rows = [
            {
                "event": "reserve", "attempt": 1, "reservedUsd": "2.450000",
                "mayHaveReachedModel": True,
            },
            {
                "event": "settle", "attempt": 1,
                "settledCostUpperBoundUsd": "0.123456",
                "actualUsd": None,
                "actualUsdUnavailableReason": "trusted_provider_dollar_telemetry_absent",
                "usage": {"inputTokens": 10, "cachedInputTokens": 2, "outputTokens": 3},
            },
            {
                "event": "reserve", "attempt": 2, "reservedUsd": "2.450000",
                "mayHaveReachedModel": True,
            },
            {
                "event": "transport-error", "attempt": 2,
                "reservationReleased": False,
            },
            {
                "event": "terminal", "attempts": 2,
                "unresolvedReservedUsd": "2.450000", "listenerAbsent": True,
            },
        ]
        summary = module.public_accounting(rows)
        self.assertEqual(summary["attemptCount"], 2)
        self.assertEqual(summary["mayHaveReachedAttemptCount"], 2)
        self.assertEqual(summary["usage"], {"inputTokens": 10, "cachedInputTokens": 2, "outputTokens": 3})
        self.assertEqual(summary["settledCostUpperBoundUsd"], "0.123456")
        self.assertEqual(summary["unresolvedReservedUsd"], "2.450000")
        self.assertIsNone(summary["actualUsd"])
        self.assertEqual(summary["actualUsdUnavailableReason"], "trusted_provider_dollar_telemetry_absent")

    def test_public_accounting_rejects_missing_terminal_or_invalid_jsonl(self):
        module = load_module()
        with self.assertRaisesRegex(ValueError, "terminal"):
            module.public_accounting([{"event": "reserve", "attempt": 1, "reservedUsd": "2.45"}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.jsonl"
            path.write_text("not-json\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSONL"):
                module.read_ledger(path)


if __name__ == "__main__":
    unittest.main()
