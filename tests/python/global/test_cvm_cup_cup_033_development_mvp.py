from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr


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
        prompt = module.build_prompt(
            Path("/repo/models/toys4k/cup_cup_033.ply"),
            Path("/exp/source/cup_cup_033.implicit.js"),
        )
        for expected in (
            "/repo/models/toys4k/cup_cup_033.ply",
            "/exp/source/cup_cup_033.implicit.js",
            "source/cup_cup_033.implicit.js",
            "artifacts/cup_cup_033.glb",
            "measurement/numeric-measurement.json",
            "review.md",
            "local tools",
            "meshscope",
            "trimesh",
        ):
            self.assertIn(expected, prompt)
        for forbidden in (
            "Do not run git",
            "Do not run find",
            "Do not use head",
            "Do not use snapshot",
            "Do not use a browser",
            "Do not use matplotlib",
            "Do not run canonical-build",
            "Do not read binary files as text",
        ):
            self.assertIn(forbidden, prompt)

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
            module.write_artifact_manifest(exp, 0, "a" * 40)
            manifest = json.loads((exp / "artifact_manifest.json").read_text(encoding="utf-8"))
            self.assertIs(type(manifest["final_status"]), int)
            self.assertEqual(manifest["final_status"], 0)
            self.assertEqual(manifest["source_revision"], "a" * 40)
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
                module.prepare_plan(root, "group", "exp", source, "a" * 40)
            fixed.write_bytes(b"fixture")
            module.FIXED_INPUT_SHA256 = module.sha256(fixed)
            plan = module.prepare_plan(root, "group", "exp", source, "a" * 40)
            self.assertEqual(plan.exp_dir, root / "outputs/group/exp")
            self.assertEqual(plan.source_revision, "a" * 40)
            plan.exp_dir.mkdir(parents=True)
            with self.assertRaisesRegex(module.MvpError, "fresh"):
                module.prepare_plan(root, "group", "exp", source, "a" * 40)

    def test_source_revision_is_explicit_and_does_not_depend_on_git_metadata(self):
        module = load_module()
        parser = module.argument_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([
                "--group", "g", "--exp", "e", "--initial-source", "/source.js",
            ])
        args = parser.parse_args([
            "--group", "g", "--exp", "e", "--initial-source", "/source.js",
            "--source-revision", "b" * 40,
        ])
        self.assertEqual(args.source_revision, "b" * 40)
        self.assertFalse(hasattr(module, "_git_sha"))

    def test_runner_seeds_one_fixed_working_source_copy(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixed = root / "models/toys4k/cup_cup_033.ply"
            fixed.parent.mkdir(parents=True)
            fixed.write_bytes(b"fixture")
            module.FIXED_INPUT_SHA256 = module.sha256(fixed)
            initial = root / "prior.implicit.js"
            initial.write_bytes(b"fixed source bytes\n")
            plan = module.prepare_plan(root, "g", "e", initial, "c" * 40)
            plan.exp_dir.mkdir(parents=True)
            working = module.seed_working_source(plan)
            self.assertEqual(working, plan.exp_dir / "source/cup_cup_033.implicit.js")
            self.assertEqual(working.read_bytes(), b"fixed source bytes\n")
            self.assertEqual(initial.read_bytes(), b"fixed source bytes\n")


if __name__ == "__main__":
    unittest.main()
