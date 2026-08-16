from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def load_module():
    path = Path("scripts/pilot/cvm-cup-cup-033-development-mvp.py").resolve()
    spec = importlib.util.spec_from_file_location("cvm_cup_cup_033_development_mvp", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CvmCupCup033DevelopmentMvpTests(unittest.TestCase):
    def test_codex_config_is_responses_code_mode_false_and_contains_only_client_capability(self):
        module = load_module()
        config = module.codex_config("http://127.0.0.1:43123/v1", "one-shot-client")
        self.assertIn('model = "gpt-5.6-sol"', config)
        self.assertIn('wire_api = "responses"', config)
        self.assertIn("code_mode = false", config)
        self.assertIn("one-shot-client", config)
        self.assertNotIn("VENUS_TOKEN", config)

    def test_prompt_fixes_input_and_requests_all_reviewable_outputs(self):
        module = load_module()
        prompt = module.build_prompt(Path("/repo/models/toys4k/cup_cup_033.ply"), Path("/old/source.js"))
        for expected in (
            "/repo/models/toys4k/cup_cup_033.ply",
            "/old/source.js",
            "source/cup_cup_033.implicit.js",
            "artifacts/cup_cup_033.glb",
            "measurement/numeric-measurement.json",
            "review.md",
            "local tools",
        ):
            self.assertIn(expected, prompt)

    def test_validation_requires_nonempty_geometry_measurement_review_and_last_message(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            exp = Path(directory)
            required = {
                "source/cup_cup_033.implicit.js": b"export default {};\n",
                "artifacts/cup_cup_033.glb": b"glTF",
                "measurement/numeric-measurement.json": b'{"height":1}',
                "review.md": b"Development review\n",
                "run/codex-events.jsonl": b'{"type":"event"}\n',
                "run/stdout.log": b'{"type":"event"}\n',
                "run/last-message.txt": b"done\n",
            }
            for relative, payload in required.items():
                path = exp / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            (exp / "run/stderr.log").touch()
            module.validate_outputs(exp)
            (exp / "review.md").write_bytes(b"")
            with self.assertRaisesRegex(module.MvpError, "review.md"):
                module.validate_outputs(exp)

    def test_accounting_is_upper_bound_only_and_manifest_status_is_integer(self):
        module = load_module()
        rows = [
            {"event": "reserve", "attempt": 1, "reservedUsd": "2.450000", "mayHaveReachedModel": True},
            {"event": "settle", "attempt": 1, "releasedReservedUsd": "2.450000",
             "settledCostUpperBoundUsd": "0.012300", "usage": {"inputTokens": 10,
             "cachedInputTokens": 2, "outputTokens": 3}, "actualUsd": None,
             "actualUsdUnavailableReason": "trusted_provider_dollar_telemetry_absent"},
            {"event": "terminal", "attempts": 1, "unresolvedReservedUsd": "0.000000", "listenerAbsent": True},
        ]
        accounting = module.public_accounting(rows)
        self.assertEqual(accounting["attemptCount"], 1)
        self.assertEqual(accounting["settledCostUpperBoundUsd"], "0.012300")
        self.assertIsNone(accounting["actualUsd"])
        with tempfile.TemporaryDirectory() as directory:
            exp = Path(directory)
            (exp / "receipt.json").write_text("{}", encoding="utf-8")
            module.write_artifact_manifest(exp, 0)
            manifest = json.loads((exp / "artifact_manifest.json").read_text(encoding="utf-8"))
            self.assertIs(type(manifest["final_status"]), int)
            self.assertEqual(manifest["final_status"], 0)
            self.assertEqual(manifest["files"][0]["path"], "receipt.json")

    def test_run_plan_rejects_existing_output_and_wrong_fixed_input_digest(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixed = root / "models/toys4k/cup_cup_033.ply"
            fixed.parent.mkdir(parents=True)
            fixed.write_bytes(b"wrong")
            source = root / "prior.js"
            source.write_text("source", encoding="utf-8")
            with self.assertRaisesRegex(module.MvpError, "digest"):
                module.prepare_plan(root, "group", "exp", source)
            fixed.write_bytes(b"fixture")
            module.FIXED_INPUT_SHA256 = module.sha256(fixed)
            plan = module.prepare_plan(root, "group", "exp", source)
            self.assertEqual(plan.exp_dir, root / "outputs/group/exp")
            plan.exp_dir.mkdir(parents=True)
            with self.assertRaisesRegex(module.MvpError, "fresh"):
                module.prepare_plan(root, "group", "exp", source)


if __name__ == "__main__":
    unittest.main()
