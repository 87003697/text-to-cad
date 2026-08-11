from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
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

    def authority_helper(self, payload: dict, status: int = 0) -> Path:
        path = self.root / f"authority-helper-{len(list(self.root.glob('authority-helper-*')))}.py"
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
        payload = {
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
        subprocess.run(["git", "init", "--quiet"], cwd=self.exp, check=True)
        subprocess.run(
            ["git", "config", "user.name", "pilot-review-test"],
            cwd=self.exp,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "pilot-review-test@localhost"],
            cwd=self.exp,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=self.exp, check=True)
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "workspace: synthetic"],
            cwd=self.exp,
            check=True,
        )
        return payload

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

    def test_reviewer_audits_provider_free_runtime_authority_receipt(self) -> None:
        helper = self.helper(self.canonical_experiment())
        shipped_files = [
            {"path": "runtime-identity.json", "size_bytes": 2, "sha256": "1" * 64}
        ]
        receipt = {
            "schema": "issue15.runtime-authority-smoke/1",
            "scenario_identity": "issue15.provider-free.runtime-authority/1",
            "workspace": {
                "path": ".",
                "schema": "mesh-to-cad.workspace/1",
                "final_delivery": {"selected_step": 0},
            },
            "viewer_deployment": {
                "schema": "cvm.viewer-runtime-deployment/1",
                "viewer_version": "test",
                "runtime_identity": {"path": "runtime-identity.json", "sha256": "2" * 64},
                "artifacts": [
                    {
                        "role": role,
                        "source": {"path": f"source/{role}", "sha256": digest * 64},
                        "bundle": {"path": f"bundle/{role}", "sha256": digest * 64},
                        "deployed": {"path": f"bundle/{role}", "sha256": digest * 64},
                    }
                    for role, digest in (("launcher", "3"), ("server", "4"), ("client", "5"))
                ],
            },
            "viewer_fallback": {
                "schema": "issue15.viewer-fallback-smoke/1",
                "rejected_reuse": {"port": 4178, "http_status": 400},
                "fallback": {"action": "start", "port": 4179},
            },
            "native_depth_eight": {
                "schema": "issue15.native-depth-eight-evidence/1",
                "native_required": True,
                "backend": {"id": "meshscope.voxblame.native-sat/1"},
                "depths": list(range(1, 9)),
            },
            "shipped_tree": {
                "schema": "cvm.deployed-runtime-tree-receipt/1",
                "root": "skills/cad-viewer/scripts/viewer",
                "file_count": 1,
                "total_bytes": 2,
                "tree_sha256": hashlib.sha256(
                    json.dumps(shipped_files, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "files": shipped_files,
            },
            "commands": "run/provider-free-commands.jsonl",
        }
        proof = {
            "schema": "cvm.provider-free-execution/1",
            "job": "20260811-000000-test/exp-issue15-runtime-authority",
            "scenario": {
                "name": "issue15-runtime-authority",
                "identity": "issue15.provider-free.runtime-authority/1",
            },
            "execution_profile": {
                "schema": "cvm.provider-free-execution-profile/1",
                "id": "issue15.provider-free-bounded/1",
                "provider_access": "forbidden",
            },
            "sandbox": {
                "network": "isolated-loopback",
                "resource_profile": "issue15.provider-free-bounded/1",
            },
            "provider_environment": {
                "allowlist": ["HOME", "LANG", "PATH", "PYTHONDONTWRITEBYTECODE", "TZ"],
                "stripped": ["ANTHROPIC_API_KEY"],
                "credential_values_recorded": False,
            },
            "requests": {"model_gateway": 0, "provider": 0, "tap": 0},
        }
        paths = {
            "run/runtime-authority-smoke.json": receipt,
            "run/provider-free-execution.json": proof,
        }
        manifest_files = []
        for relative, value in paths.items():
            write_json(self.exp / relative, value)
            data = (self.exp / relative).read_bytes()
            manifest_files.append(
                {
                    "path": relative,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        command_path = self.exp / "run/provider-free-commands.jsonl"
        command_path.write_text('{"schema":"cvm.provider-free-command/1"}\n', encoding="utf-8")
        command_data = command_path.read_bytes()
        manifest_files.append(
            {
                "path": "run/provider-free-commands.jsonl",
                "size_bytes": len(command_data),
                "sha256": hashlib.sha256(command_data).hexdigest(),
            }
        )
        write_json(
            self.exp / "artifact_manifest.json",
            {
                "schema_version": 1,
                "workload_status": 0,
                "final_status": 0,
                "files": manifest_files,
            },
        )

        status = self.reviewer.main([str(self.exp), "--workspace-helper", str(helper)])

        self.assertEqual(status, 0)
        review = json.loads((self.exp / "review.json").read_text(encoding="utf-8"))
        self.assertEqual("pass", review["verdicts"]["production_runtime_integration"])
        self.assertEqual(
            "run/runtime-authority-smoke.json",
            review["contract_provenance"]["runtime_authority"],
        )
        self.assertNotIn("production runtime integration", " ".join(review["evidence_gaps"]))

        proof["requests"]["provider"] = 1
        write_json(self.exp / "run/provider-free-execution.json", proof)
        verdict, provenance, issues, gaps = self.reviewer._runtime_authority_verdict(
            self.exp
        )
        self.assertEqual("not_auditable", verdict)
        self.assertEqual({}, provenance)
        self.assertEqual("observability-gap", issues[0]["classification"])
        self.assertTrue(gaps)

    def test_reviewer_audits_portable_authority_and_records_materialized_evidence(self) -> None:
        workspace_payload = self.canonical_experiment()
        shutil.rmtree(self.exp / ".git")
        before = {
            path.relative_to(self.exp).as_posix(): path.read_bytes()
            for path in self.exp.rglob("*")
            if path.is_file()
        }
        output = self.root / "portable-review-output"
        workspace_helper = self.helper(workspace_payload)
        authority_helper = self.authority_helper(
            {
                "ok": True,
                "authority": {
                    "mode": "materialized",
                    "evidence": [
                        "workspace-authority.json",
                        "workspace-authority.bundle",
                    ],
                    "head": "a" * 40,
                    "publication_ref": "refs/workspace-authority/portable-v1",
                    "receipt_sha256": "b" * 64,
                },
                "workspace_validation": workspace_payload,
            }
        )

        status = self.reviewer.main(
            [
                str(self.exp),
                "--workspace-helper",
                str(workspace_helper),
                "--authority-helper",
                str(authority_helper),
                "--output",
                str(output),
            ]
        )

        self.assertEqual(status, 0)
        review = json.loads((output / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(review["workspace_validation"]["authority_mode"], "materialized")
        self.assertEqual(
            review["workspace_validation"]["authority_evidence"],
            ["workspace-authority.json", "workspace-authority.bundle"],
        )
        self.assertEqual(
            review["contract_provenance"]["portable_authority"],
            "workspace-authority.json",
        )
        after = {
            path.relative_to(self.exp).as_posix(): path.read_bytes()
            for path in self.exp.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertFalse((self.exp / "review.json").exists())
        self.assertFalse((self.exp / "review.md").exists())

    def test_reviewer_classifies_legacy_without_partial_graph(self) -> None:
        (self.exp / "previews").mkdir()
        output = self.root / "legacy-review-output"
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
            [
                str(self.exp),
                "--workspace-helper",
                str(helper),
                "--authority-helper",
                str(
                    REPO_ROOT
                    / "skills/mesh-to-cad/scripts/mesh-to-cad-authority"
                ),
                "--output",
                str(output),
            ]
        )

        self.assertEqual(status, 2)
        review = json.loads((output / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(
            review["workspace_validation"]["classification"],
            "not_auditable",
        )
        self.assertEqual(
            review["workspace_validation"]["authority_classification"],
            "authority_missing",
        )
        self.assertEqual(review["graph"], {"nodes": [], "edges": []})


if __name__ == "__main__":
    unittest.main()
