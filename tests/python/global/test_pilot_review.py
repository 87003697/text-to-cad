from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
REVIEWER_PATH = REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-review/__main__.py"


def load_reviewer():
    spec = importlib.util.spec_from_file_location("pilot_review", REVIEWER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load pilot-review")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


class PilotReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.exp = self.root / "exp"
        self.exp.mkdir()
        self.reviewer = load_reviewer()

    def terminal_handoff(self) -> tuple[dict, dict, str]:
        graph = {
            "schema": "mesh-to-cad.step-index/1",
            "steps": [],
            "cycles": [],
            "failed_attempts": [],
            "accepted_steps": [],
            "heads": [],
            "budget": {
                "completed_cycles": 0,
                "remaining_cycles": 5,
                "total_attempts": 0,
                "tool_failures": 0,
            },
            "final_delivery": {
                "selected_step": 0,
                "accepted": True,
                "identity_sha256": "6" * 64,
                "manifest": "final/manifest.json",
            },
        }
        result = {
            "schema": "mesh-to-cad.terminal-validation/1",
            "workspace_id": "synthetic",
            "workspace_identity_sha256": "1" * 64,
            "validator_version": "mesh-to-cad.workspace-validator/1",
            "graph": graph,
            "review_graph": {
                "schema": "mesh-to-cad.review-graph/1",
                "steps": [],
                "attempts": [],
                "failed_attempts": [],
                "cycles": [],
                "final": {},
            },
            "recovery": [],
            "review_facts": {
                "step_count": 0,
                "cycle_count": 0,
                "failed_attempt_count": 0,
                "accepted_steps": [],
                "heads": [],
                "budget": graph["budget"],
                "final_delivery": graph["final_delivery"],
                "step_outcomes": [],
            },
            "evaluation_facts": {
                "accepted_step_count": 0,
                "has_accepted_step": False,
                "final_delivery_present": True,
                "final_delivery_accepted": True,
                "objective_facts": [],
            },
            "content_manifest_sha256": "2" * 64,
            "identity_sha256": "3" * 64,
        }
        bundle = {
            "schema": "mesh-to-cad.terminal-validation-bundle/1",
            "result": result,
            "manifest": {"schema": "mesh-to-cad.content-manifest/1", "files": []},
        }
        identity = "a" * 64
        write_json(
            self.exp / "run/terminal-validation-locator.json",
            {
                "schema": "mesh-to-cad.terminal-validation-locator/2",
                "handoff_layout": "external-sibling-namespace/1",
            },
        )
        write_json(
            self.root
            / ".internal-terminal-validation/exp/terminal-validation.json",
            {
                "schema": "mesh-to-cad.terminal-validation-handoff/1",
                "terminal_identity_sha256": identity,
                "bundle": bundle,
            },
        )
        return bundle, result, identity

    def verifier(self, result: dict) -> mock.Mock:
        verifier = mock.Mock()
        verifier.verify_terminal_validation.return_value = result
        return verifier

    def review_draft(self) -> dict:
        evidence = [
            {
                "scope": "experiment",
                "path": "run/terminal-validation-locator.json",
            }
        ]
        return {
            "schema": "pilot-review.draft/2",
            "semantic_verdicts": {
                "reconstruction_quality": "accepted",
                "production_runtime_integration": "not_auditable",
            },
            "protocol_assessments": [
                {
                    "check_id": check["check_id"],
                    "status": "observed",
                    "rationale": "The verified terminal result records this stage.",
                    "evidence": evidence,
                }
                for check in self.reviewer.PROTOCOL_CHECKS
            ],
            "issues": [],
            "unresolved": [],
            "evidence_gaps": [],
            "fix_playbook": [],
        }

    def test_review_verifies_external_handoff_once_without_reading_authority(self) -> None:
        bundle, result, identity = self.terminal_handoff()
        for relative in (
            "workspace.json",
            "artifact_manifest.json",
            "steps/000000/step.json",
            "cycles/000001/cycle.json",
            "attempts/000001/attempt.json",
            "final/manifest.json",
        ):
            write_json(self.exp / relative, {"must_not_be_read": True})
        verifier = self.verifier(result)
        with (
            mock.patch.object(
                self.reviewer, "_load_workspace_verifier", return_value=verifier
            ),
            mock.patch.object(
                self.reviewer,
                "_read_json",
                side_effect=AssertionError("review read Workspace Authority"),
            ),
        ):
            status, review = self.reviewer.review_workspace(self.exp)
        self.assertEqual(0, status)
        verifier.verify_terminal_validation.assert_called_once_with(
            self.exp.resolve(), bundle, identity
        )
        self.assertEqual("pass", review["verdicts"]["workspace_protocol"])
        self.assertNotIn("runner_completion", review["verdicts"])
        self.assertEqual(result["review_facts"], review["review_facts"])

    def test_review_rejects_locator_with_embedded_authority(self) -> None:
        bundle, result, identity = self.terminal_handoff()
        write_json(
            self.exp / "run/terminal-validation-locator.json",
            {
                "schema": "mesh-to-cad.terminal-validation-locator/1",
                "bundle": bundle,
                "terminal_identity_sha256": identity,
            },
        )
        verifier = self.verifier(result)
        with mock.patch.object(
            self.reviewer, "_load_workspace_verifier", return_value=verifier
        ):
            with self.assertRaises(self.reviewer.ReviewError):
                self.reviewer.review_workspace(self.exp)
        verifier.verify_terminal_validation.assert_not_called()

    def test_review_rejects_missing_or_symlinked_handoff(self) -> None:
        _bundle, result, _identity = self.terminal_handoff()
        handoff = self.root / ".internal-terminal-validation/exp/terminal-validation.json"
        body = handoff.read_bytes()
        handoff.unlink()
        verifier = self.verifier(result)
        with mock.patch.object(
            self.reviewer, "_load_workspace_verifier", return_value=verifier
        ):
            with self.assertRaises(self.reviewer.ReviewError):
                self.reviewer.review_workspace(self.exp)
            foreign = self.root / "foreign.json"
            foreign.write_bytes(body)
            handoff.symlink_to(foreign)
            with self.assertRaises(self.reviewer.ReviewError):
                self.reviewer.review_workspace(self.exp)
        verifier.verify_terminal_validation.assert_not_called()

    def test_review_rejects_verifier_failure(self) -> None:
        self.terminal_handoff()
        verifier = mock.Mock()
        verifier.verify_terminal_validation.side_effect = RuntimeError("identity mismatch")
        with mock.patch.object(
            self.reviewer, "_load_workspace_verifier", return_value=verifier
        ):
            with self.assertRaisesRegex(self.reviewer.ReviewError, "verification failed"):
                self.reviewer.review_workspace(self.exp)

    def test_prepare_writes_only_terminal_baseline_without_execution_scan(self) -> None:
        _bundle, result, _identity = self.terminal_handoff()
        write_json(
            self.exp / "attempts/000001/commands/000001/command.json",
            {"bad": True},
        )
        verifier = self.verifier(result)
        with mock.patch.object(
            self.reviewer, "_load_workspace_verifier", return_value=verifier
        ):
            status, summary = self.reviewer.prepare_target(self.exp)
        self.assertEqual(0, status)
        evidence = json.loads(
            (self.exp / "run/review/review-input.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("execution", evidence)
        self.assertNotIn("runner_completion", evidence["baseline"]["verdicts"])
        self.assertEqual(str((self.exp / "run/review").resolve()), summary["review_root"])

    def test_prepare_and_publish_preserve_semantic_verdicts(self) -> None:
        _bundle, result, _identity = self.terminal_handoff()
        verifier = self.verifier(result)
        with mock.patch.object(
            self.reviewer, "_load_workspace_verifier", return_value=verifier
        ):
            self.assertEqual(0, self.reviewer.main(["prepare", str(self.exp)]))
        review_root = self.exp / "run/review"
        write_json(review_root / "review-draft.json", self.review_draft())
        self.assertEqual(0, self.reviewer.main(["publish", str(self.exp)]))
        review = json.loads((review_root / "review.json").read_text(encoding="utf-8"))
        self.assertEqual("accepted", review["verdicts"]["reconstruction_quality"])
        self.assertEqual("pass", review["verdicts"]["workspace_protocol"])
        self.assertNotIn("execution", review)

    def test_external_review_root_does_not_mutate_workspace(self) -> None:
        _bundle, result, _identity = self.terminal_handoff()
        review_root = self.root / "review-output"
        verifier = self.verifier(result)
        with mock.patch.object(
            self.reviewer, "_load_workspace_verifier", return_value=verifier
        ):
            status, _summary = self.reviewer.prepare_target(
                self.exp, review_root=review_root
            )
        self.assertEqual(0, status)
        self.assertTrue((review_root / "review-input.json").is_file())
        self.assertFalse((self.exp / "review-input.json").exists())

    def test_cli_has_no_full_audit_or_validation_timeout_options(self) -> None:
        parser = self.reviewer._legacy_parser()
        options = {
            option
            for action in parser._actions
            for option in action.option_strings
        }
        self.assertNotIn("--full-audit", options)
        self.assertNotIn("--validation-timeout-seconds", options)


if __name__ == "__main__":
    unittest.main()
