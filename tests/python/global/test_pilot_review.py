from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
REVIEWER_PATH = REPO_ROOT / ".claude/skills/pilot-review/scripts/review.py"


def load_reviewer():
    spec = importlib.util.spec_from_file_location("pilot_review", REVIEWER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load pilot-review")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class PilotReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.exp = self.root / "exp"
        self.exp.mkdir()
        self.reviewer = load_reviewer()

    def helper(self, payload: dict, status: int = 0) -> Path:
        path = self.root / f"workspace-helper-{len(list(self.root.glob('workspace-helper-*')))}.py"
        path.write_text(
            "import json\n"
            "raise SystemExit((print(json.dumps(" + repr(payload) + ")) or " + str(status) + "))\n",
            encoding="utf-8",
        )
        return path

    def canonical_experiment(self) -> dict:
        write_json(
            self.exp / "workspace.json",
            {"schema": "mesh-to-cad.workspace/1", "workspace_id": "synthetic"},
        )
        write_json(
            self.exp / "input/input.json",
            {
                "schema": "voxblame.canonical-reference/1",
                "canonical_reference_sha256": "1" * 64,
            },
        )
        write_json(
            self.exp / "cycles/000001/plan.json",
            {
                "schema": "voxblame.repair-batch/1",
                "from_step": 0,
                "selected_targets": [
                    {"target_key": "missing:0", "mask_sha256": "2" * 64}
                ],
                "planned_edits": [
                    {
                        "edit_key": "add-wing",
                        "target_keys": ["missing:0"],
                        "description": "Add the missing wing.",
                    }
                ],
                "rationale": "Repair the selected residual.",
                "preview_observation": "Wing is missing.",
            },
        )
        write_json(
            self.exp / "cycles/000001/source_changes.json",
            {
                "schema": "mesh-to-cad.source-changes/1",
                "from_step": 0,
                "to_step": 1,
                "files": [
                    {
                        "path": "source/model.py",
                        "before_sha256": "3" * 64,
                        "after_sha256": "4" * 64,
                    }
                ],
            },
        )
        write_json(
            self.exp / "cycles/000001/diff.json",
            {
                "schema": "voxblame.region-diff/1",
                "from_step": 0,
                "to_step": 1,
                "identity": {"region_diff_sha256": "5" * 64},
            },
        )
        write_json(
            self.exp / "cycles/000001/assessment.json",
            {
                "schema": "mesh-to-cad.assessment/1",
                "from_step": 0,
                "to_step": 1,
                "preview_observation": "Wing now appears.",
                "summary": "The selected residual closed.",
            },
        )
        write_json(
            self.exp / "final/selection.json",
            {
                "schema": "mesh-to-cad.final-selection/1",
                "considered_steps": [0, 1],
                "selected_step": 1,
                "accepted": True,
                "stop_reason": "acceptance_satisfied",
            },
        )
        write_json(
            self.exp / "final/manifest.json",
            {
                "schema": "mesh-to-cad.final-delivery/1",
                "selected_step": 1,
                "accepted": True,
                "identity_sha256": "6" * 64,
            },
        )
        write_json(
            self.exp / "artifact_manifest.json",
            {"schema_version": 1, "workload_status": 0, "final_status": 0},
        )
        return {
            "ok": True,
            "valid": True,
            "graph": {
                "schema": "mesh-to-cad.step-index/1",
                "steps": [
                    {"step": 0, "parent_step": None, "accepted": False},
                    {"step": 1, "parent_step": 0, "accepted": True},
                ],
                "cycles": [
                    {
                        "cycle": 1,
                        "from_step": 0,
                        "to_step": 1,
                        "attempt_ids": [1, 2],
                        "plan_digest": "7" * 64,
                        "diff": "cycles/000001/diff.json",
                    }
                ],
                "failed_attempts": [
                    {
                        "attempt": 1,
                        "intended_step": 1,
                        "from_step": 0,
                        "result": "tool_failure",
                        "classification": "build_failed",
                    }
                ],
                "accepted_steps": [1],
                "budget": {
                    "completed_cycles": 1,
                    "remaining_cycles": 4,
                    "total_attempts": 2,
                    "tool_failures": 1,
                },
                "heads": [1],
                "final_delivery": {
                    "selected_step": 1,
                    "accepted": True,
                    "stop_reason": "acceptance_satisfied",
                    "route": "cad",
                    "identity_sha256": "6" * 64,
                    "manifest": "final/manifest.json",
                },
            },
            "recovery": [],
        }

    def test_reviewer_reconstructs_canonical_repair_and_delivery_chain(self) -> None:
        helper = self.helper(self.canonical_experiment())

        status = self.reviewer.main(
            [str(self.exp), "--workspace-helper", str(helper)]
        )

        self.assertEqual(status, 0)
        review = json.loads((self.exp / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(review["workspace_validation"]["classification"], "valid")
        self.assertEqual(review["verdicts"]["runner_completion"], "pass")
        self.assertEqual(review["verdicts"]["workspace_protocol"], "pass")
        node_types = {node["type"] for node in review["graph"]["nodes"]}
        self.assertTrue(
            {
                "canonical_reference",
                "measured_step",
                "repair_target",
                "repair_batch",
                "planned_edit",
                "source_change",
                "region_diff",
                "agent_assessment",
                "selection",
                "final_delivery",
            }.issubset(node_types)
        )
        edge_types = [edge["type"] for edge in review["graph"]["edges"]]
        for expected in (
            "target_selected_by_batch",
            "batch_contains_edit",
            "edit_has_source_change",
            "source_change_measured_by_diff",
            "diff_assessed_by_agent",
            "step_considered_for_selection",
            "selection_publishes_delivery",
        ):
            self.assertIn(expected, edge_types)
        self.assertTrue((self.exp / "review.md").is_file())

    def test_reviewer_classifies_legacy_without_partial_graph(self) -> None:
        (self.exp / "previews").mkdir()
        helper = self.helper(
            {
                "ok": False,
                "error": {
                    "classification": "unsupported_legacy_workspace",
                    "path": "$",
                    "detail": "legacy layout",
                },
            },
            status=2,
        )

        status = self.reviewer.main(
            [str(self.exp), "--workspace-helper", str(helper)]
        )

        self.assertEqual(status, 2)
        review = json.loads((self.exp / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(
            review["workspace_validation"]["classification"],
            "unsupported_legacy_workspace",
        )
        self.assertEqual(review["graph"], {"nodes": [], "edges": []})


if __name__ == "__main__":
    unittest.main()
