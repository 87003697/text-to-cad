from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
REVIEWER_PATH = (
    REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-review/__main__.py"
)


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
            self.exp / "steps/000000/attempt.json",
            {
                "attempt": 0,
                "intended_step": 0,
                "from_step": None,
                "result": "measured_step_published",
            },
        )
        write_json(
            self.exp / "cycles/000001/attempt.json",
            {
                "attempt": 2,
                "intended_step": 1,
                "from_step": 0,
                "result": "repair_cycle_published",
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
            self.exp / "final/rebuild.json",
            {"schema": "canonical-build.recipe/1"},
        )
        write_json(
            self.exp / "final/verification.json",
            {
                "schema": "canonical-build.verification/1",
                "verification_sha256": "a" * 64,
            },
        )
        write_json(
            self.exp / "final/manifest.json",
            {
                "schema": "mesh-to-cad.final-delivery/1",
                "selected_step": 1,
                "accepted": True,
                "rebuild_sha256": "8" * 64,
                "verification_sha256": "9" * 64,
                "verification_identity_sha256": "a" * 64,
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
                    {
                        "step": 0,
                        "parent_step": None,
                        "accepted": False,
                        "preview": "steps/000000/preview/preview.json",
                        "measurement": "steps/000000/measurement.json",
                    },
                    {
                        "step": 1,
                        "parent_step": 0,
                        "accepted": True,
                        "preview": "steps/000001/preview/preview.json",
                        "measurement": "steps/000001/measurement.json",
                    },
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

    def review_draft(
        self,
        *,
        evidence_path: str = "workspace.json",
        omitted_check_id: str | None = None,
        extra_check_id: str | None = None,
    ) -> dict:
        assessments = [
            {
                "check_id": check["check_id"],
                "status": "observed",
                "rationale": "Synthetic canonical authority records this protocol stage.",
                "evidence": [
                    {"scope": "experiment", "path": "workspace.json"}
                ],
            }
            for check in self.reviewer.PROTOCOL_CHECKS
            if check["check_id"] != omitted_check_id
        ]
        if extra_check_id is not None:
            assessments.append(
                {
                    "check_id": extra_check_id,
                    "status": "observed",
                    "rationale": "This check was not requested by the compiler.",
                    "evidence": [
                        {"scope": "experiment", "path": "workspace.json"}
                    ],
                }
            )
        return {
            "schema": "pilot-review.draft/2",
            "semantic_verdicts": {
                "reconstruction_quality": "accepted",
                "production_runtime_integration": "not_auditable",
            },
            "protocol_assessments": assessments,
            "issues": [
                {
                    "classification": "observability-gap",
                    "detail": "Production integration evidence is incomplete.",
                    "fix_target": "pilot runner publication",
                    "evidence": [
                        {"scope": "experiment", "path": evidence_path}
                    ],
                }
            ],
            "unresolved": [],
            "evidence_gaps": ["bundle parity is not published"],
            "fix_playbook": ["Publish bundle parity evidence."],
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
                "attempt",
                "formal_preview",
                "measurement",
                "repair_target",
                "repair_batch",
                "planned_edit",
                "source_change",
                "region_diff",
                "agent_assessment",
                "selection",
                "rebuild",
                "verification",
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
            "measured_step_descends_from",
            "attempt_produces_preview",
            "preview_has_measurement",
            "measurement_publishes_step",
            "attempt_contributes_to_cycle",
            "step_considered_for_selection",
            "selection_triggers_rebuild",
            "rebuild_verified_independently",
            "verification_supports_delivery",
        ):
            self.assertIn(expected, edge_types)
        attempt_ids = {
            node["id"]
            for node in review["graph"]["nodes"]
            if node["type"] == "attempt"
        }
        self.assertEqual({"attempt:0", "attempt:1", "attempt:2"}, attempt_ids)
        final_evidence = {
            node["type"]: node["evidence"]
            for node in review["graph"]["nodes"]
            if node["type"] in {"rebuild", "verification"}
        }
        for evidence in final_evidence.values():
            self.assertTrue((self.exp / evidence).is_file(), evidence)
        cycle_contributors = {
            edge["from"]
            for edge in review["graph"]["edges"]
            if edge["type"] == "attempt_contributes_to_cycle"
        }
        self.assertEqual({"attempt:2"}, cycle_contributors)
        self.assertEqual(
            "not_auditable",
            review["verdicts"]["production_runtime_integration"],
        )
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

    def test_prepare_and_publish_group_through_two_module_seam(self) -> None:
        payload = self.canonical_experiment()
        group = self.root / "group"
        group.mkdir()
        airplane = group / "airplane"
        bicycle = group / "bicycle"
        shutil.copytree(self.exp, airplane)
        shutil.copytree(self.exp, bicycle)
        helper = self.helper(payload)
        write_json(
            bicycle / "attempts/000001/commands/000001/command.json",
            {
                "schema": "mesh-to-cad.command/1",
                "phase": "canonical-build",
                "argv": ["node", "canonical-build.mjs"],
                "duration_ms": 900001,
                "exit_code": 124,
                "timed_out": True,
                "stderr": {"path": "commands/000001/stderr.log"},
            },
        )
        stderr_path = bicycle / "attempts/000001/commands/000001/stderr.log"
        stderr_path.write_text("command timed out\n", encoding="utf-8")

        status = self.reviewer.main(
            [
                "prepare",
                str(group),
                "--workspace-helper",
                str(helper),
            ]
        )

        self.assertEqual(status, 0)
        group_input = json.loads(
            (group / "review-input.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"airplane", "bicycle"},
            {item["experiment"] for item in group_input["experiments"]},
        )
        bicycle_input = json.loads(
            (bicycle / "review-input.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [check["check_id"] for check in self.reviewer.PROTOCOL_CHECKS],
            [check["check_id"] for check in bicycle_input["protocol_checks"]],
        )
        command = bicycle_input["execution"]["commands"][0]
        self.assertEqual(124, command["exit_code"])
        self.assertTrue(command["timed_out"])
        self.assertEqual("command timed out\n", command["stderr"]["preview"])
        for experiment in (airplane, bicycle):
            write_json(experiment / "review-draft.json", self.review_draft())
        write_json(
            group / "review-summary-draft.json",
            {
                "schema": "pilot-review.group-draft/1",
                "summary": "Both experiments used the same frozen runtime.",
                "cross_experiment_findings": [
                    {
                        "classification": "tool-interface-failure",
                        "detail": "Only the bicycle command timed out.",
                        "fix_target": "canonical build exporter",
                        "evidence": [
                            {
                                "scope": "group",
                                "path": (
                                    "bicycle/attempts/000001/commands/000001/"
                                    "command.json"
                                ),
                            }
                        ],
                    }
                ],
                "fix_playbook": ["Profile the bicycle exporter path."],
            },
        )

        status = self.reviewer.main(["publish", str(group)])

        self.assertEqual(status, 0)
        for experiment in (airplane, bicycle):
            review = json.loads(
                (experiment / "review.json").read_text(encoding="utf-8")
            )
            self.assertEqual("pass", review["verdicts"]["runner_completion"])
            self.assertEqual("pass", review["verdicts"]["workspace_protocol"])
            self.assertEqual(
                "accepted", review["verdicts"]["reconstruction_quality"]
            )
            self.assertEqual(
                len(self.reviewer.PROTOCOL_CHECKS),
                len(review["protocol_assessments"]),
            )
            self.assertTrue((experiment / "review.md").is_file())
        summary = (group / "review-summary.md").read_text(encoding="utf-8")
        self.assertIn("Only the bicycle command timed out", summary)
        self.assertIn("| airplane | pass | pass | accepted |", summary)
        airplane_markdown = (airplane / "review.md").read_text(encoding="utf-8")
        self.assertIn("## Protocol assessment", airplane_markdown)
        self.assertIn("`atomic-final-delivery`: `observed`", airplane_markdown)
        self.assertLess(
            airplane_markdown.index("- workspace: `workspace.json`"),
            airplane_markdown.index("## Protocol assessment"),
        )

    def test_skill_dispatches_one_subagent_for_complete_transaction(self) -> None:
        skill = (
            REPO_ROOT / ".claude/skills/pilot-review/SKILL.md"
        ).read_text(encoding="utf-8")
        contract = (
            REPO_ROOT
            / ".claude/skills/pilot-review/references/review-agent-contract.md"
        ).read_text(encoding="utf-8")

        self.assertIn("dispatch exactly one stable sub-agent", skill)
        self.assertIn("caller performs no `prepare`", skill)
        self.assertIn("execute every Workflow step locally", skill)
        self.assertIn("Run Evidence\nCompiler `prepare`", contract)
        self.assertIn("then run Evidence Compiler\n`publish`", contract)

    def test_external_review_root_keeps_source_immutable(self) -> None:
        payload = self.canonical_experiment()
        group = self.root / "group"
        group.mkdir()
        airplane = group / "airplane"
        bicycle = group / "bicycle"
        shutil.copytree(self.exp, airplane)
        shutil.copytree(self.exp, bicycle)
        review_root = self.root / "review-output"
        helper = self.helper(payload)

        status = self.reviewer.main(
            [
                "prepare",
                str(group),
                "--review-root",
                str(review_root),
                "--workspace-helper",
                str(helper),
            ]
        )

        self.assertEqual(status, 0)
        self.assertFalse((group / "review-input.json").exists())
        for source in (airplane, bicycle):
            self.assertFalse((source / "review-input.json").exists())
            evidence = json.loads(
                (review_root / source.name / "review-input.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(str(source.resolve()), evidence["source"]["workspace"])
            self.assertEqual(str(group.resolve()), evidence["source"]["group"])
            write_json(
                review_root / source.name / "review-draft.json",
                self.review_draft(),
            )
        write_json(
            review_root / "review-summary-draft.json",
            {
                "schema": "pilot-review.group-draft/1",
                "summary": "The immutable source was reviewed externally.",
                "cross_experiment_findings": [],
                "fix_playbook": [],
            },
        )

        status = self.reviewer.main(
            ["publish", str(group), "--review-root", str(review_root)]
        )

        self.assertEqual(status, 0)
        self.assertTrue((review_root / "review-summary.md").is_file())
        for source in (airplane, bicycle):
            self.assertTrue((review_root / source.name / "review.json").is_file())
            self.assertTrue((review_root / source.name / "review.md").is_file())
            self.assertFalse((source / "review.json").exists())
            self.assertFalse((source / "review.md").exists())
        self.assertFalse((group / "review-summary.md").exists())

    def test_external_review_root_rejects_same_named_source_substitution(self) -> None:
        payload = self.canonical_experiment()
        original = self.root / "original/group"
        substitute = self.root / "substitute/group"
        original.mkdir(parents=True)
        substitute.mkdir(parents=True)
        for group in (original, substitute):
            shutil.copytree(self.exp, group / "airplane")
        review_root = self.root / "review-output"
        helper = self.helper(payload)
        self.assertEqual(
            0,
            self.reviewer.main(
                [
                    "prepare",
                    str(original),
                    "--review-root",
                    str(review_root),
                    "--workspace-helper",
                    str(helper),
                ]
            ),
        )
        write_json(
            review_root / "airplane/review-draft.json",
            self.review_draft(),
        )
        write_json(
            review_root / "review-summary-draft.json",
            {
                "schema": "pilot-review.group-draft/1",
                "summary": "This draft belongs to the original source.",
                "cross_experiment_findings": [],
                "fix_playbook": [],
            },
        )

        status = self.reviewer.main(
            ["publish", str(substitute), "--review-root", str(review_root)]
        )

        self.assertEqual(status, 1)
        self.assertFalse((review_root / "airplane/review.json").exists())
        self.assertFalse((review_root / "review-summary.md").exists())

    def test_external_review_root_rejects_source_overlap_before_writing(self) -> None:
        payload = self.canonical_experiment()
        group = self.root / "group"
        group.mkdir()
        shutil.copytree(self.exp, group / "airplane")
        helper = self.helper(payload)

        status = self.reviewer.main(
            [
                "prepare",
                str(group),
                "--review-root",
                str(group / "reviews/current"),
                "--workspace-helper",
                str(helper),
            ]
        )

        self.assertEqual(status, 1)
        self.assertFalse((group / "reviews").exists())

    def test_external_review_root_rejects_destination_symlink_to_source(self) -> None:
        payload = self.canonical_experiment()
        group = self.root / "group"
        group.mkdir()
        airplane = group / "airplane"
        shutil.copytree(self.exp, airplane)
        review_root = self.root / "review-output"
        review_root.mkdir()
        (review_root / "airplane").symlink_to(airplane, target_is_directory=True)
        helper = self.helper(payload)

        status = self.reviewer.main(
            [
                "prepare",
                str(group),
                "--review-root",
                str(review_root),
                "--workspace-helper",
                str(helper),
            ]
        )

        self.assertEqual(status, 1)
        self.assertFalse((airplane / "review-input.json").exists())

    def test_external_review_root_never_follows_fixed_temp_symlinks(self) -> None:
        payload = self.canonical_experiment()
        group = self.root / "group"
        group.mkdir()
        airplane = group / "airplane"
        shutil.copytree(self.exp, airplane)
        review_root = self.root / "review-output"
        review_exp = review_root / "airplane"
        review_exp.mkdir(parents=True)
        protected = {
            airplane / "workspace.json": (airplane / "workspace.json").read_bytes(),
            airplane / "artifact_manifest.json": (
                airplane / "artifact_manifest.json"
            ).read_bytes(),
            airplane / "input/input.json": (
                airplane / "input/input.json"
            ).read_bytes(),
        }
        (review_exp / ".review-input.json.tmp").symlink_to(
            airplane / "workspace.json"
        )
        (review_root / ".review-input.json.tmp").symlink_to(
            airplane / "artifact_manifest.json"
        )
        helper = self.helper(payload)

        self.assertEqual(
            0,
            self.reviewer.main(
                [
                    "prepare",
                    str(group),
                    "--review-root",
                    str(review_root),
                    "--workspace-helper",
                    str(helper),
                ]
            ),
        )
        write_json(review_exp / "review-draft.json", self.review_draft())
        write_json(
            review_root / "review-summary-draft.json",
            {
                "schema": "pilot-review.group-draft/1",
                "summary": "Fixed temporary symlinks are never opened.",
                "cross_experiment_findings": [],
                "fix_playbook": [],
            },
        )
        (review_exp / ".review.json.tmp").symlink_to(
            airplane / "workspace.json"
        )
        (review_exp / ".review.md.tmp").symlink_to(
            airplane / "input/input.json"
        )
        (review_root / ".review-summary.md.tmp").symlink_to(
            airplane / "input/input.json"
        )

        self.assertEqual(
            0,
            self.reviewer.main(
                ["publish", str(group), "--review-root", str(review_root)]
            ),
        )

        for path, expected in protected.items():
            self.assertEqual(expected, path.read_bytes(), path)
        self.assertTrue((review_exp / "review-input.json").is_file())
        self.assertTrue((review_exp / "review.json").is_file())
        self.assertTrue((review_exp / "review.md").is_file())
        self.assertTrue((review_root / "review-summary.md").is_file())

    def test_prepare_records_validator_timeout_without_invalidating_workspace(self) -> None:
        self.canonical_experiment()
        helper = self.root / "slow-helper.py"
        helper.write_text(
            "import time\n"
            "time.sleep(2)\n"
            "print('{}')\n",
            encoding="utf-8",
        )

        status = self.reviewer.main(
            [
                "prepare",
                str(self.exp),
                "--workspace-helper",
                str(helper),
                "--validation-timeout-seconds",
                "1",
            ]
        )

        self.assertEqual(status, 1)
        evidence = json.loads(
            (self.exp / "review-input.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "validator_timeout",
            evidence["baseline"]["workspace_validation"]["classification"],
        )
        self.assertIsNone(
            evidence["baseline"]["workspace_validation"]["valid"]
        )
        self.assertEqual(
            "not_auditable",
            evidence["baseline"]["verdicts"]["workspace_protocol"],
        )
        self.assertFalse((self.exp / "review.md").exists())
        self.assertEqual(1800, self.reviewer.DEFAULT_VALIDATION_TIMEOUT_SECONDS)

    def test_publish_rejects_missing_agent_evidence_before_writing_reports(self) -> None:
        payload = self.canonical_experiment()
        helper = self.helper(payload)
        self.assertEqual(
            0,
            self.reviewer.main(
                [
                    "prepare",
                    str(self.exp),
                    "--workspace-helper",
                    str(helper),
                ]
            ),
        )
        write_json(
            self.exp / "review-draft.json",
            self.review_draft(evidence_path="missing.json"),
        )

        status = self.reviewer.main(["publish", str(self.exp)])

        self.assertEqual(status, 1)
        self.assertFalse((self.exp / "review.json").exists())
        self.assertFalse((self.exp / "review.md").exists())

    def test_publish_rejects_incomplete_protocol_check_coverage(self) -> None:
        payload = self.canonical_experiment()
        helper = self.helper(payload)
        self.assertEqual(
            0,
            self.reviewer.main(
                ["prepare", str(self.exp), "--workspace-helper", str(helper)]
            ),
        )
        omitted = self.reviewer.PROTOCOL_CHECKS[-1]["check_id"]
        write_json(
            self.exp / "review-draft.json",
            self.review_draft(omitted_check_id=omitted),
        )

        status = self.reviewer.main(["publish", str(self.exp)])

        self.assertEqual(status, 1)
        self.assertFalse((self.exp / "review.json").exists())
        self.assertFalse((self.exp / "review.md").exists())

    def test_publish_rejects_unknown_protocol_check(self) -> None:
        payload = self.canonical_experiment()
        helper = self.helper(payload)
        self.assertEqual(
            0,
            self.reviewer.main(
                ["prepare", str(self.exp), "--workspace-helper", str(helper)]
            ),
        )
        write_json(
            self.exp / "review-draft.json",
            self.review_draft(extra_check_id="invented-check"),
        )

        status = self.reviewer.main(["publish", str(self.exp)])

        self.assertEqual(status, 1)
        self.assertFalse((self.exp / "review.json").exists())

    def test_missing_protocol_check_requires_missing_evidence_detail(self) -> None:
        payload = self.canonical_experiment()
        helper = self.helper(payload)
        self.assertEqual(
            0,
            self.reviewer.main(
                ["prepare", str(self.exp), "--workspace-helper", str(helper)]
            ),
        )
        draft = self.review_draft()
        draft["protocol_assessments"][0] = {
            "check_id": self.reviewer.PROTOCOL_CHECKS[0]["check_id"],
            "status": "missing",
            "rationale": "The expected authority is absent.",
            "evidence": [],
        }
        write_json(self.exp / "review-draft.json", draft)

        status = self.reviewer.main(["publish", str(self.exp)])

        self.assertEqual(status, 1)
        self.assertFalse((self.exp / "review.json").exists())

    def test_publish_rejects_non_string_protocol_status_cleanly(self) -> None:
        payload = self.canonical_experiment()
        helper = self.helper(payload)
        self.assertEqual(
            0,
            self.reviewer.main(
                ["prepare", str(self.exp), "--workspace-helper", str(helper)]
            ),
        )
        draft = self.review_draft()
        draft["protocol_assessments"][0]["status"] = []
        write_json(self.exp / "review-draft.json", draft)

        status = self.reviewer.main(["publish", str(self.exp)])

        self.assertEqual(status, 1)
        self.assertFalse((self.exp / "review.json").exists())

    def test_publish_rejects_non_string_assessment_evidence_scope_cleanly(self) -> None:
        payload = self.canonical_experiment()
        helper = self.helper(payload)
        self.assertEqual(
            0,
            self.reviewer.main(
                ["prepare", str(self.exp), "--workspace-helper", str(helper)]
            ),
        )
        draft = self.review_draft()
        draft["protocol_assessments"][0]["evidence"][0]["scope"] = []
        write_json(self.exp / "review-draft.json", draft)

        status = self.reviewer.main(["publish", str(self.exp)])

        self.assertEqual(status, 1)
        self.assertFalse((self.exp / "review.json").exists())

    def test_missing_protocol_check_can_publish_with_gap_detail(self) -> None:
        payload = self.canonical_experiment()
        helper = self.helper(payload)
        self.assertEqual(
            0,
            self.reviewer.main(
                ["prepare", str(self.exp), "--workspace-helper", str(helper)]
            ),
        )
        draft = self.review_draft()
        draft["protocol_assessments"][0] = {
            "check_id": self.reviewer.PROTOCOL_CHECKS[0]["check_id"],
            "status": "missing",
            "rationale": "The expected authority is absent.",
            "evidence": [],
            "missing_evidence": "canonical reference setup receipt",
        }
        write_json(self.exp / "review-draft.json", draft)

        status = self.reviewer.main(["publish", str(self.exp)])

        self.assertEqual(status, 0)
        review = json.loads(
            (self.exp / "review.json").read_text(encoding="utf-8")
        )
        self.assertEqual("missing", review["protocol_assessments"][0]["status"])

    def test_prepare_group_preserves_coverage_after_one_compiler_failure(self) -> None:
        payload = self.canonical_experiment()
        group = self.root / "group"
        group.mkdir()
        airplane = group / "airplane"
        bicycle = group / "bicycle"
        shutil.copytree(self.exp, airplane)
        shutil.copytree(self.exp, bicycle)
        helper = self.root / "selective-helper.py"
        helper.write_text(
            "import json\n"
            "import sys\n"
            f"payload = {payload!r}\n"
            "print('not-json' if sys.argv[-1].endswith('bicycle') "
            "else json.dumps(payload))\n",
            encoding="utf-8",
        )

        status = self.reviewer.main(
            [
                "prepare",
                str(group),
                "--workspace-helper",
                str(helper),
            ]
        )

        self.assertEqual(status, 1)
        airplane_input = json.loads(
            (airplane / "review-input.json").read_text(encoding="utf-8")
        )
        bicycle_input = json.loads(
            (bicycle / "review-input.json").read_text(encoding="utf-8")
        )
        self.assertEqual("valid", airplane_input["compiler_status"]["classification"])
        self.assertEqual(
            "compiler_failure",
            bicycle_input["compiler_status"]["classification"],
        )
        group_input = json.loads(
            (group / "review-input.json").read_text(encoding="utf-8")
        )
        self.assertEqual(2, len(group_input["experiments"]))

    def test_prepare_group_includes_runner_failure_without_authority_files(self) -> None:
        payload = self.canonical_experiment()
        group = self.root / "group"
        group.mkdir()
        normal = group / "normal"
        failed = group / "failed-before-authority"
        shutil.copytree(self.exp, normal)
        (failed / "run").mkdir(parents=True)
        (failed / "run/stderr.log").write_text(
            "runner exited before publication\n", encoding="utf-8"
        )
        helper = self.helper(payload)

        status = self.reviewer.main(
            ["prepare", str(group), "--workspace-helper", str(helper)]
        )

        self.assertEqual(status, 1)
        failed_input = json.loads(
            (failed / "review-input.json").read_text(encoding="utf-8")
        )
        self.assertEqual("failed-before-authority", failed_input["experiment"])
        self.assertEqual(
            "compiler_failure",
            failed_input["compiler_status"]["classification"],
        )
        group_input = json.loads(
            (group / "review-input.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"normal", "failed-before-authority"},
            {item["experiment"] for item in group_input["experiments"]},
        )

    def test_prepare_group_contains_malformed_execution_evidence_per_experiment(self) -> None:
        payload = self.canonical_experiment()
        group = self.root / "group"
        group.mkdir()
        malformed = group / "malformed"
        healthy = group / "healthy"
        shutil.copytree(self.exp, malformed)
        shutil.copytree(self.exp, healthy)
        (malformed / "artifact_manifest.json").write_text(
            "{not-json\n", encoding="utf-8"
        )
        helper = self.helper(payload)

        status = self.reviewer.main(
            ["prepare", str(group), "--workspace-helper", str(helper)]
        )

        self.assertEqual(status, 1)
        malformed_input = json.loads(
            (malformed / "review-input.json").read_text(encoding="utf-8")
        )
        healthy_input = json.loads(
            (healthy / "review-input.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "compiler_failure",
            malformed_input["compiler_status"]["classification"],
        )
        self.assertTrue(malformed_input["execution"]["compiler_errors"])
        self.assertEqual("valid", healthy_input["compiler_status"]["classification"])
        self.assertTrue((group / "review-input.json").is_file())

    def test_prepare_group_contains_escaping_command_stderr_paths(self) -> None:
        payload = self.canonical_experiment()
        group = self.root / "group"
        group.mkdir()
        relative_escape = group / "relative-escape"
        absolute_escape = group / "absolute-escape"
        healthy = group / "healthy"
        for experiment in (relative_escape, absolute_escape, healthy):
            shutil.copytree(self.exp, experiment)
        secret = self.root / "outside-secret.log"
        secret.write_text("must not enter compiled evidence\n", encoding="utf-8")
        command_relative = (
            relative_escape / "attempts/000001/commands/000001/command.json"
        )
        command_absolute = (
            absolute_escape / "attempts/000001/commands/000001/command.json"
        )
        write_json(
            command_relative,
            {"stderr": {"path": "../../../../outside-secret.log"}},
        )
        write_json(command_absolute, {"stderr": {"path": str(secret)}})
        helper = self.helper(payload)

        status = self.reviewer.main(
            ["prepare", str(group), "--workspace-helper", str(helper)]
        )

        self.assertEqual(status, 1)
        for experiment in (relative_escape, absolute_escape):
            evidence_text = (experiment / "review-input.json").read_text(
                encoding="utf-8"
            )
            evidence = json.loads(evidence_text)
            self.assertEqual(
                "compiler_failure",
                evidence["compiler_status"]["classification"],
            )
            self.assertNotIn("must not enter compiled evidence", evidence_text)
        healthy_input = json.loads(
            (healthy / "review-input.json").read_text(encoding="utf-8")
        )
        self.assertEqual("valid", healthy_input["compiler_status"]["classification"])
        self.assertEqual(
            3,
            len(
                json.loads(
                    (group / "review-input.json").read_text(encoding="utf-8")
                )["experiments"]
            ),
        )

    def test_publish_rejects_sealed_input_replayed_under_another_experiment(self) -> None:
        payload = self.canonical_experiment()
        group = self.root / "group"
        group.mkdir()
        airplane = group / "airplane"
        bicycle = group / "bicycle"
        shutil.copytree(self.exp, airplane)
        shutil.copytree(self.exp, bicycle)
        helper = self.helper(payload)
        self.assertEqual(
            0,
            self.reviewer.main(
                ["prepare", str(group), "--workspace-helper", str(helper)]
            ),
        )
        shutil.copyfile(
            airplane / "review-input.json", bicycle / "review-input.json"
        )
        for experiment in (airplane, bicycle):
            write_json(experiment / "review-draft.json", self.review_draft())
        write_json(
            group / "review-summary-draft.json",
            {
                "schema": "pilot-review.group-draft/1",
                "summary": "The experiments were reviewed together.",
                "cross_experiment_findings": [],
                "fix_playbook": [],
            },
        )

        status = self.reviewer.main(["publish", str(group)])

        self.assertEqual(status, 1)
        self.assertFalse((airplane / "review.json").exists())
        self.assertFalse((bicycle / "review.json").exists())

    def test_publish_rejects_tampered_deterministic_baseline(self) -> None:
        payload = self.canonical_experiment()
        helper = self.helper(payload)
        self.assertEqual(
            0,
            self.reviewer.main(
                [
                    "prepare",
                    str(self.exp),
                    "--workspace-helper",
                    str(helper),
                ]
            ),
        )
        evidence_path = self.exp / "review-input.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["baseline"]["verdicts"]["runner_completion"] = "fail"
        write_json(evidence_path, evidence)
        write_json(self.exp / "review-draft.json", self.review_draft())

        status = self.reviewer.main(["publish", str(self.exp)])

        self.assertEqual(status, 1)
        self.assertFalse((self.exp / "review.json").exists())


if __name__ == "__main__":
    unittest.main()
