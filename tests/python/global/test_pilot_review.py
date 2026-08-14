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

from scripts.pilot import deployment_authority
from scripts.pilot.cvm_job import protocol, runtime


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

    def test_reviewer_requires_runtime_authority_for_wrapper_publication_root(
        self,
    ) -> None:
        helper = self.helper(self.canonical_experiment())
        failure_path = self.exp / "run/scenario-failure.json"
        write_json(
            failure_path,
            {
                "schema": "cvm.provider-free-scenario-failure/1",
                "scenario_identity": "issue15.provider-free.runtime-authority/1",
                "stage": "native_measurement",
                "operation": "preview_public_wrapper_evidence_publication",
            },
        )
        failure_bytes = failure_path.read_bytes()
        manifest = {
            "schema_version": 1,
            "workload_status": 1,
            "final_status": 1,
            "files": [
                {
                    "path": "run/scenario-failure.json",
                    "size_bytes": len(failure_bytes),
                    "sha256": hashlib.sha256(failure_bytes).hexdigest(),
                }
            ],
        }
        write_json(self.exp / "artifact_manifest.json", manifest)

        status = self.reviewer.main(
            [str(self.exp), "--workspace-helper", str(helper)]
        )

        self.assertEqual(status, 0)
        review = json.loads(
            (self.exp / "review.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "not_auditable",
            review["verdicts"]["production_runtime_integration"],
            review["issues"],
        )
        self.assertNotIn(
            "runtime_authority_failure",
            review["contract_provenance"],
        )
        self.assertIn(
            "production runtime integration requires",
            " ".join(review["evidence_gaps"]),
        )

    def test_reviewer_binds_exec_permitted_browser_staging_profile(self) -> None:
        self.assertEqual(
            {
                "source": "deployment-attested-host-revision",
                "source_filesystem": "same-device-as-deployment-browser",
                "scope": "single-attested-revision",
                "destination": "/tmp/provider-free-playwright",
                "staged_revision": "attested",
                "staged_executable": (
                    "/tmp/provider-free-playwright/attested/"
                    "chrome-headless-shell-linux64/chrome-headless-shell"
                ),
                "destination_filesystem": (
                    "read-only-bind-of-exec-permitted-host-stage"
                ),
                "tree_validation": "regular-files-only-no-links-or-special",
                "tree_manifest": {
                    "schema": "meshshot.browser-tree-manifest/1",
                    "coverage": "complete-attested-revision-tree",
                    "binding": "canonical-sha256",
                },
                "execution_authority": {
                    "schema": "meshshot.browser-execution-authority/1",
                    "mode": "linux-detached-readonly-revision-mount/1",
                    "private_filesystem": "job-private-exec-tmpfs",
                    "handoff": (
                        "authenticated-seqpacket-mounted-detached-exec"
                    ),
                    "writable_source_after_handoff": "absent",
                    "running_image_proof": "exact-proc-image-fd-identity",
                },
                "executable_validation": {
                    "sha256": "deployment-runtime-identity",
                    "execute_bits": "required",
                },
                "exec_permission_validation": {
                    "mechanism": (
                        "kernel-execve-repository-owned-immediate-exit-probe"
                    ),
                    "network": "none",
                    "timeout_seconds": 5,
                    "expected_stdout": "cvm.browser-stage-exec-probe/1",
                },
                "sandbox_exec_diagnostics": {
                    "schema": "cvm.provider-free-browser-exec-diagnostic/5",
                    "receipt": "run/browser-exec-diagnostic.json",
                    "executable": (
                        "/tmp/provider-free-playwright/attested/"
                        "chrome-headless-shell-linux64/chrome-headless-shell"
                    ),
                    "argv_suffix": ["--version"],
                    "lifecycle": "non-rendering-immediate-exit",
                    "environment_names": ["HOME", "LANG", "PATH"],
                    "network": "none",
                    "timeout_seconds": 5,
                    "node_probe": "retired-by-python-prelaunch",
                    "result": {
                        "exit_code": 0,
                        "stdout": "single-chromium-version-line",
                        "stdout_max_bytes": 128,
                        "stderr": "empty",
                    },
                    "seams": [
                        "outer-python-direct",
                        "outer-supervised-python-prelaunch",
                        "fixed-unix-authority",
                        "nested-playwright-loopback-cdp-attach",
                    ],
                    "published": "closed-outcomes-only-no-raw-output",
                    "cleanup": "no-profile-or-persistent-process-artifacts",
                },
                "nested_mount": "read-only-exact-staged-cache",
                "launch_handoff": {
                    "environment": "MESHSHOT_BROWSER_EXECUTABLE",
                    "value": (
                        "/tmp/provider-free-playwright/attested/"
                        "chrome-headless-shell-linux64/chrome-headless-shell"
                    ),
                    "validation": "absolute-regular-non-symlink-executable",
                    "launch_owner": "outer-trusted-browser-supervisor",
                    "playwright_option": "connect_over_cdp-is-local",
                },
                "cleanup": "supervisor-context-terminal-all-exit-classes",
                "catchable_signal_cleanup": ["SIGINT", "SIGTERM"],
                "uncatchable_termination": (
                    "stale-stage-collision-fail-closed"
                ),
            },
            self.reviewer._SANDBOX_PROFILE["browser_runtime_staging"],
        )

    def test_reviewer_audits_provider_free_runtime_authority_receipt(self) -> None:
        workspace_payload = self.canonical_experiment()
        helper = self.helper(workspace_payload)
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
            "cadpy_runtime": {
                "schema": "cvm.audited-cadpy-runtime/1",
                "path": deployment_authority.CADPY_RUNTIME_PATH,
                "sha256": "6" * 64,
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
            "preview_sandbox": "run/preview-sandbox-enforcement.json",
        }
        proof = {
            "schema": "cvm.provider-free-execution/1",
            "job": "20260811-000000-test/exp-issue15-runtime-authority",
            "scenario": {
                "name": "issue15-runtime-authority",
                "identity": "issue15.provider-free.runtime-authority/1",
            },
            "execution_profile": {
                **runtime.PROVIDER_FREE_EXECUTION_PROFILE,
            },
            "sandbox": {
                "network": "isolated-loopback",
                "resource_profile": "issue15.provider-free-bounded/17",
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

        pre_verdict, _provenance, pre_issues, _gaps = (
            self.reviewer._runtime_authority_verdict(self.exp)
        )
        self.assertEqual("not_auditable", pre_verdict)
        self.assertEqual("observability-gap", pre_issues[0]["classification"])

        deployed_root = self.root / "deployed-source"
        for declared in deployment_authority.EXECUTION_AUTHORITY_PATHS:
            path = deployed_root / declared
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{declared}\n", encoding="utf-8")
            else:
                path.mkdir(parents=True, exist_ok=True)
                (path / "authority-marker.txt").write_text(
                    f"{declared}\n", encoding="utf-8"
                )
        native = (
            deployed_root
            / "skills/mesh-compare/scripts/packages/meshscope/"
            "src/meshscope/voxblame/_native.cpython.so"
        )
        native.parent.mkdir(parents=True, exist_ok=True)
        native.write_bytes(b"native")
        cadpy = deployed_root / deployment_authority.CADPY_RUNTIME_PATH
        cadpy.parent.mkdir(parents=True, exist_ok=True)
        cadpy.write_bytes(b"cadpy-runtime")
        runtime_identity = {
            "schema": "cvm.provider-free-runtime-identity/1",
            "bwrap": {
                "path": "/usr/bin/bwrap",
                "sha256": "b" * 64,
                "version": "bubblewrap 1.2.3",
            },
            "chromium": {
                "revision": "1234",
                "host_cache_path": "/home/test/.cache/ms-playwright",
                "sandbox_cache_path": deployment_authority.SANDBOX_BROWSER_CACHE,
                "executable_path": (
                    "/home/test/.cache/ms-playwright/"
                    "chromium_headless_shell-1234/"
                    "chrome-headless-shell-linux64/chrome-headless-shell"
                ),
                "sha256": "c" * 64,
            },
            "cadpy": {
                "path": deployment_authority.CADPY_RUNTIME_PATH,
                "sha256": hashlib.sha256(cadpy.read_bytes()).hexdigest(),
            },
        }
        receipt["cadpy_runtime"] = {
            "schema": "cvm.audited-cadpy-runtime/1",
            **runtime_identity["cadpy"],
        }
        viewer_root = deployed_root / "skills/cad-viewer/scripts/viewer"
        viewer_artifacts = []
        for role, token in (
            ("launcher", b"launcher"),
            ("server", b"server"),
            ("client", b"client"),
        ):
            source_path = f"skills/cad-viewer/scripts/viewer/source/{role}"
            bundle_path = f"skills/cad-viewer/scripts/viewer/bundle/{role}"
            (deployed_root / source_path).parent.mkdir(parents=True, exist_ok=True)
            (deployed_root / source_path).write_bytes(b"source-" + token)
            (deployed_root / bundle_path).parent.mkdir(parents=True, exist_ok=True)
            (deployed_root / bundle_path).write_bytes(token)
            viewer_artifacts.append(
                {
                    "role": role,
                    "source": {
                        "path": source_path,
                        "sha256": hashlib.sha256(b"source-" + token).hexdigest(),
                    },
                    "bundle": {
                        "path": bundle_path,
                        "sha256": hashlib.sha256(token).hexdigest(),
                    },
                    "deployed": {
                        "path": bundle_path,
                        "sha256": hashlib.sha256(token).hexdigest(),
                    },
                }
            )
        receipt["viewer_deployment"]["artifacts"] = viewer_artifacts
        shipped_files = []
        for path in sorted(viewer_root.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                shipped_files.append(
                    {
                        "path": path.relative_to(viewer_root).as_posix(),
                        "size_bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
        receipt["shipped_tree"] = {
            "schema": "cvm.deployed-runtime-tree-receipt/1",
            "root": "skills/cad-viewer/scripts/viewer",
            "file_count": len(shipped_files),
            "total_bytes": sum(item["size_bytes"] for item in shipped_files),
            "tree_sha256": hashlib.sha256(
                json.dumps(
                    shipped_files, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
            "files": shipped_files,
        }
        receipt["workspace"]["final_delivery"] = workspace_payload["graph"][
            "final_delivery"
        ]
        proof["job"] = f"{self.exp.parent.name}/{self.exp.name}"
        deployed_receipt = deployment_authority.build_receipt(
            deployed_root,
            source_head="a" * 40,
            runtime_identity=runtime_identity,
        )
        deployment_authority.materialize_receipt(
            deployed_root,
            deployed_receipt,
            self.exp / "run/deployed-source",
        )
        write_json(self.exp / "run/deployed-source-authority.json", deployed_receipt)
        deployed_bytes = (
            self.exp / "run/deployed-source-authority.json"
        ).read_bytes()
        immutable_request = {
            "job_kind": "provider-free",
            "object": "issue15-runtime-authority",
            "group": self.exp.parent.name,
            "exp": self.exp.name,
            "exp_dir": f"outputs/{self.exp.parent.name}/{self.exp.name}",
            "scenario": proof["scenario"],
            "execution_profile": proof["execution_profile"],
            "request_authority": {
                "schema": "cvm.provider-free-request-authority/1",
                "deployment_receipt": deployment_authority.RECEIPT_PATH,
                "deployment_receipt_sha256": hashlib.sha256(
                    deployed_bytes
                ).hexdigest(),
                "deployment_receipt_canonical_sha256": hashlib.sha256(
                    json.dumps(
                        deployed_receipt, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                "deployment_source_head": deployed_receipt["source_head"],
                "deployment_tree_sha256": deployed_receipt["tree_sha256"],
                "runtime_identity": runtime_identity,
            },
        }
        proof["request_authority"] = {
            "sha256": hashlib.sha256(
                b"cvm.provider-free-request-authority/1\0"
                + json.dumps(
                    immutable_request, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
            "deployment_tree_sha256": deployed_receipt["tree_sha256"],
            "immutable_request": immutable_request,
        }
        sandbox_exp = f"/workspace/repo/{immutable_request['exp_dir']}"
        runtime_repo = (self.root / "runtime-repo").resolve()
        runtime_exp = runtime_repo / immutable_request["exp_dir"]
        runtime_exp.mkdir(parents=True)
        tree_manifest_sha256 = "d" * 64
        sandbox_argv = runtime.provider_free_sandbox_argv(
            "issue15-runtime-authority",
            runtime_exp,
            runtime_identity,
            repo_root=runtime_repo,
        )
        staged_target = f"{protocol.PROVIDER_FREE_STAGED_BROWSER_CACHE}/attested"
        staged_index = next(
            index
            for index in range(len(sandbox_argv) - 2)
            if sandbox_argv[index] == "--ro-bind"
            and sandbox_argv[index + 2] == staged_target
        )
        sandbox_argv[staged_index + 3 : staged_index + 3] = [
            "--setenv",
            "MESHSHOT_BROWSER_TREE_MANIFEST_SHA256",
            tree_manifest_sha256,
        ]
        write_json(
            self.exp / "run/sandbox-enforcement.json",
            {
                "schema": "cvm.provider-free-sandbox-enforcement/1",
                "network": "isolated-loopback",
                "argv": sandbox_argv,
                "environment_names": [
                    "HOME",
                    "LANG",
                    "PATH",
                    "PLAYWRIGHT_BROWSERS_PATH",
                    "MESHSHOT_EXECUTABLE_ROOT",
                    "MESHSHOT_BROWSER_RUNTIME_MODE",
                    "PYTHONDONTWRITEBYTECODE",
                    "TZ",
                ],
                "required_environment": runtime.PROVIDER_FREE_REQUIRED_ENVIRONMENT,
                "sandbox_profile": runtime.PROVIDER_FREE_SANDBOX_PROFILE,
                "runtime_identity": runtime_identity,
            },
        )
        sandbox_bytes = (self.exp / "run/sandbox-enforcement.json").read_bytes()
        proof["sandbox_enforcement"] = {
            "path": "run/sandbox-enforcement.json",
            "sha256": hashlib.sha256(sandbox_bytes).hexdigest(),
        }
        write_json(
            self.exp / "run/preview-sandbox-enforcement.json",
            {
                "schema": "cvm.provider-free-preview-sandbox-enforcement/1",
                "argv": protocol.provider_free_preview_sandbox_argv(
                    immutable_request["group"], immutable_request["exp"]
                ),
                "capabilities": "drop-all",
                "mount_namespace": "inherit-outer",
            },
        )
        write_json(
            self.exp / "run/browser-exec-diagnostic.json",
            {
                "schema": "cvm.provider-free-browser-exec-diagnostic/5",
                "executable": (
                    "/tmp/provider-free-playwright/attested/"
                    "chrome-headless-shell-linux64/chrome-headless-shell"
                ),
                "probe": "chromium-version-immediate-exit",
                "outer": "passed",
                "nested": "not-run",
                "node_attached": "not-run",
                "node_detached": "not-run",
                "node_failure_kind": "not-run",
                "prelaunched_cdp": "passed",
            },
        )
        write_json(
            self.exp / "run/preview-public-wrapper-diagnostic.json",
            {
                "schema": "cvm.provider-free-preview-public-wrapper/1",
                "operation": "passed",
            },
        )
        write_json(self.exp / "run/runtime-authority-smoke.json", receipt)
        write_json(self.exp / "run/provider-free-execution.json", proof)
        runtime_evidence = {
            "schema": "meshshot.prelaunched-cdp-runtime/1",
            "adapter_profile": {
                "name": "playwright-1.60-chromium-1223-loopback-cdp/1",
                "sha256": "16ef68d9ee9700f10c9e92b6ca88c0430dc98c6808145258f9a6125f3acd5c04",
            },
            "browser_identity": {
                "playwright": "1.60.0",
                "browser": "chromium-headless-shell",
                "revision": "1223",
                "version": "Google Chrome for Testing 148.0.7778.96",
                "sha256": runtime_identity["chromium"]["sha256"],
            },
            "execution_authority": {
                "schema": "meshshot.browser-execution-authority/1",
                "mode": "linux-detached-readonly-revision-mount/1",
                "tree_manifest_sha256": tree_manifest_sha256,
                "executable_sha256": runtime_identity["chromium"]["sha256"],
                "mount_readonly": "passed",
                "source_detached": "passed",
            },
            "result": "passed",
        }
        preview_relatives = [
            "steps/000000/preview/preview.json",
            "steps/000001/preview/preview.json",
        ]
        for relative in preview_relatives:
            preview = {
                "schema": "voxblame.preview/1",
                "browser_runtime": runtime_evidence,
            }
            preview["preview_identity_sha256"] = hashlib.sha256(
                b"voxblame.preview/1\0"
                + (
                    json.dumps(
                        preview,
                        indent=2,
                        sort_keys=True,
                        separators=(",", ": "),
                    )
                    + "\n"
                ).encode()
            ).hexdigest()
            write_json(self.exp / relative, preview)
        manifest_files = []
        manifest_candidates = [
            *sorted((self.exp / "run").rglob("*")),
            *(self.exp / relative for relative in preview_relatives),
        ]
        for path in manifest_candidates:
            if path.is_file():
                data = path.read_bytes()
                manifest_files.append(
                    {
                        "path": path.relative_to(self.exp).as_posix(),
                        "size_bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
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
        self.assertEqual(
            "pass",
            review["verdicts"]["production_runtime_integration"],
            review["issues"],
        )
        self.assertEqual(
            "run/runtime-authority-smoke.json",
            review["contract_provenance"]["runtime_authority"],
        )
        self.assertNotIn("production runtime integration", " ".join(review["evidence_gaps"]))

        authoritative_proof = json.loads(json.dumps(proof))
        authoritative_manifest = json.loads(
            (self.exp / "artifact_manifest.json").read_text(encoding="utf-8")
        )
        authoritative_preview = json.loads(
            (self.exp / preview_relatives[0]).read_text(encoding="utf-8")
        )
        for field, key, value in (
            ("adapter_profile", "sha256", "1" * 64),
            ("browser_identity", "sha256", "2" * 64),
            ("execution_authority", "tree_manifest_sha256", "e" * 64),
            ("execution_authority", "executable_sha256", "e" * 64),
            ("execution_authority", "source_detached", "failed"),
        ):
            with self.subTest(browser_runtime_tamper=f"{field}.{key}"):
                candidate = json.loads(json.dumps(authoritative_preview))
                candidate["browser_runtime"][field][key] = value
                source = dict(candidate)
                source.pop("preview_identity_sha256")
                candidate["preview_identity_sha256"] = hashlib.sha256(
                    b"voxblame.preview/1\0"
                    + (
                        json.dumps(
                            source,
                            indent=2,
                            sort_keys=True,
                            separators=(",", ": "),
                        )
                        + "\n"
                    ).encode()
                ).hexdigest()
                preview_path = self.exp / preview_relatives[0]
                write_json(preview_path, candidate)
                candidate_manifest = json.loads(json.dumps(authoritative_manifest))
                entry = next(
                    item for item in candidate_manifest["files"]
                    if item["path"] == preview_relatives[0]
                )
                preview_bytes = preview_path.read_bytes()
                entry.update(
                    size_bytes=len(preview_bytes),
                    sha256=hashlib.sha256(preview_bytes).hexdigest(),
                )
                write_json(self.exp / "artifact_manifest.json", candidate_manifest)
                self.assertEqual(
                    "not_auditable",
                    self.reviewer._runtime_authority_verdict(
                        self.exp, workspace_payload
                    )[0],
                )
        write_json(self.exp / preview_relatives[0], authoritative_preview)
        write_json(self.exp / "artifact_manifest.json", authoritative_manifest)
        for field in (
            "schema",
            "deployment_receipt",
            "deployment_receipt_sha256",
            "deployment_receipt_canonical_sha256",
            "deployment_source_head",
            "deployment_tree_sha256",
            "runtime_identity",
        ):
            with self.subTest(immutable_request_field=field):
                candidate = json.loads(json.dumps(authoritative_proof))
                candidate["request_authority"]["immutable_request"][
                    "request_authority"
                ].pop(field)
                immutable = candidate["request_authority"]["immutable_request"]
                candidate["request_authority"]["sha256"] = hashlib.sha256(
                    b"cvm.provider-free-request-authority/1\0"
                    + json.dumps(
                        immutable, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
                write_json(self.exp / "run/provider-free-execution.json", candidate)
                candidate_manifest = json.loads(json.dumps(authoritative_manifest))
                proof_bytes = (
                    self.exp / "run/provider-free-execution.json"
                ).read_bytes()
                proof_entry = next(
                    item
                    for item in candidate_manifest["files"]
                    if item["path"] == "run/provider-free-execution.json"
                )
                proof_entry.update(
                    size_bytes=len(proof_bytes),
                    sha256=hashlib.sha256(proof_bytes).hexdigest(),
                )
                write_json(self.exp / "artifact_manifest.json", candidate_manifest)
                verdict = self.reviewer._runtime_authority_verdict(
                    self.exp, workspace_payload
                )[0]
                self.assertEqual("not_auditable", verdict)
        write_json(
            self.exp / "run/provider-free-execution.json",
            authoritative_proof,
        )
        write_json(self.exp / "artifact_manifest.json", authoritative_manifest)

        sandbox_path = self.exp / "run/sandbox-enforcement.json"
        authoritative_sandbox = json.loads(sandbox_path.read_text(encoding="utf-8"))
        for mutation in (
            "namespace",
            "limit",
            "cleanup",
            "chromium",
            "extra-bind",
            "browser-bind",
            "environment",
        ):
            with self.subTest(sandbox_mutation=mutation):
                candidate = json.loads(json.dumps(authoritative_sandbox))
                if mutation == "namespace":
                    candidate["sandbox_profile"]["namespaces"].remove("ipc")
                elif mutation == "limit":
                    candidate["sandbox_profile"]["resource_limits"]["cpu_seconds"] = 1
                elif mutation == "cleanup":
                    candidate["sandbox_profile"]["cleanup"]["failed_output_retained"] = False
                elif mutation == "chromium":
                    candidate["runtime_identity"]["chromium"]["revision"] = "9999"
                elif mutation == "extra-bind":
                    candidate["argv"][1:1] = ["--bind", "/", "/workspace/repo"]
                elif mutation == "browser-bind":
                    candidate["argv"].remove(
                        next(
                            value
                            for value in candidate["argv"]
                            if "/.cvm-provider-free-browser-stages/"
                            in value
                        )
                    )
                else:
                    candidate["required_environment"][
                        "PLAYWRIGHT_BROWSERS_PATH"
                    ] = "/tmp"
                write_json(sandbox_path, candidate)
                candidate_proof = json.loads(json.dumps(authoritative_proof))
                candidate_proof["sandbox_enforcement"]["sha256"] = hashlib.sha256(
                    sandbox_path.read_bytes()
                ).hexdigest()
                write_json(
                    self.exp / "run/provider-free-execution.json", candidate_proof
                )
                candidate_manifest = json.loads(json.dumps(authoritative_manifest))
                for relative in (
                    "run/sandbox-enforcement.json",
                    "run/provider-free-execution.json",
                ):
                    data = (self.exp / relative).read_bytes()
                    entry = next(
                        item
                        for item in candidate_manifest["files"]
                        if item["path"] == relative
                    )
                    entry.update(
                        size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
                    )
                write_json(self.exp / "artifact_manifest.json", candidate_manifest)
                verdict = self.reviewer._runtime_authority_verdict(
                    self.exp, workspace_payload
                )[0]
                self.assertEqual("not_auditable", verdict)
        write_json(sandbox_path, authoritative_sandbox)
        write_json(
            self.exp / "run/provider-free-execution.json",
            authoritative_proof,
        )
        write_json(self.exp / "artifact_manifest.json", authoritative_manifest)

        preview_path = self.exp / "run/preview-sandbox-enforcement.json"
        authoritative_preview = json.loads(
            preview_path.read_text(encoding="utf-8")
        )
        for mutation in ("capability", "mount", "argv"):
            with self.subTest(preview_sandbox_mutation=mutation):
                candidate = json.loads(json.dumps(authoritative_preview))
                if mutation == "capability":
                    candidate["capabilities"] = "inherit"
                elif mutation == "mount":
                    candidate["mount_namespace"] = "host"
                else:
                    candidate["argv"].remove("ALL")
                write_json(preview_path, candidate)
                candidate_manifest = json.loads(json.dumps(authoritative_manifest))
                data = preview_path.read_bytes()
                entry = next(
                    item
                    for item in candidate_manifest["files"]
                    if item["path"] == "run/preview-sandbox-enforcement.json"
                )
                entry.update(
                    size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
                )
                write_json(self.exp / "artifact_manifest.json", candidate_manifest)
                verdict = self.reviewer._runtime_authority_verdict(
                    self.exp, workspace_payload
                )[0]
                self.assertEqual("not_auditable", verdict)
        write_json(preview_path, authoritative_preview)
        write_json(self.exp / "artifact_manifest.json", authoritative_manifest)

        diagnostic_path = self.exp / "run/browser-exec-diagnostic.json"
        diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        diagnostic["stdout"] = "sensitive raw browser output"
        write_json(diagnostic_path, diagnostic)
        diagnostic_manifest = json.loads(json.dumps(authoritative_manifest))
        diagnostic_data = diagnostic_path.read_bytes()
        diagnostic_entry = next(
            item
            for item in diagnostic_manifest["files"]
            if item["path"] == "run/browser-exec-diagnostic.json"
        )
        diagnostic_entry.update(
            size_bytes=len(diagnostic_data),
            sha256=hashlib.sha256(diagnostic_data).hexdigest(),
        )
        write_json(self.exp / "artifact_manifest.json", diagnostic_manifest)
        verdict = self.reviewer._runtime_authority_verdict(
            self.exp, workspace_payload
        )[0]
        self.assertEqual("not_auditable", verdict)
        diagnostic.pop("stdout")
        write_json(diagnostic_path, diagnostic)
        write_json(self.exp / "artifact_manifest.json", authoritative_manifest)

        proof["requests"]["provider"] = 1
        write_json(self.exp / "run/provider-free-execution.json", proof)
        provider_manifest = json.loads(json.dumps(authoritative_manifest))
        proof_bytes = (self.exp / "run/provider-free-execution.json").read_bytes()
        proof_entry = next(
            item
            for item in provider_manifest["files"]
            if item["path"] == "run/provider-free-execution.json"
        )
        proof_entry.update(
            size_bytes=len(proof_bytes),
            sha256=hashlib.sha256(proof_bytes).hexdigest(),
        )
        write_json(self.exp / "artifact_manifest.json", provider_manifest)
        verdict, provenance, issues, gaps = self.reviewer._runtime_authority_verdict(
            self.exp, workspace_payload
        )
        self.assertEqual("not_auditable", verdict)
        self.assertEqual({}, provenance)
        self.assertEqual("observability-gap", issues[0]["classification"])
        self.assertTrue(gaps)

        def write_terminal_manifest(*, status: int) -> None:
            files = []
            for path in sorted((self.exp / "run").rglob("*")):
                if path.is_file():
                    data = path.read_bytes()
                    files.append(
                        {
                            "path": path.relative_to(self.exp).as_posix(),
                            "size_bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        }
                    )
            write_json(
                self.exp / "artifact_manifest.json",
                {
                    "schema_version": 1,
                    "workload_status": status,
                    "final_status": status,
                    "files": files,
                },
            )

        write_json(self.exp / "run/runtime-authority-smoke.json", receipt)
        write_json(
            self.exp / "run/provider-free-execution.json", authoritative_proof
        )
        (self.exp / "run/preview-public-wrapper-diagnostic.json").unlink()
        write_json(
            self.exp / "run/scenario-failure.json",
            {
                "schema": "cvm.provider-free-scenario-failure/1",
                "scenario_identity": "issue15.provider-free.runtime-authority/1",
                "stage": "native_measurement",
                "operation": "preview_public_wrapper_evidence_publication",
            },
        )
        write_terminal_manifest(status=1)
        verdict, provenance, issues, gaps = self.reviewer._runtime_authority_verdict(
            self.exp, workspace_payload
        )
        self.assertEqual("fail", verdict)
        self.assertEqual(
            "run/runtime-authority-smoke.json", provenance["runtime_authority"]
        )
        self.assertEqual(
            "run/scenario-failure.json", provenance["runtime_authority_failure"]
        )
        self.assertEqual("closed-runtime-failure", issues[0]["classification"])
        self.assertEqual([], gaps)

        (self.exp / "run/preview-public-wrapper-diagnostic.json").write_text(
            '{"schema":"partial"', encoding="utf-8"
        )
        write_terminal_manifest(status=1)
        verdict, provenance, issues, gaps = self.reviewer._runtime_authority_verdict(
            self.exp, workspace_payload
        )
        self.assertEqual("not_auditable", verdict)
        self.assertEqual({}, provenance)
        self.assertEqual("observability-gap", issues[0]["classification"])
        self.assertIn("must be absent", issues[0]["detail"])
        self.assertTrue(gaps)
        (self.exp / "run/preview-public-wrapper-diagnostic.json").unlink()

        browser_exec_path = self.exp / "run/browser-exec-diagnostic.json"
        browser_exec = json.loads(browser_exec_path.read_text(encoding="utf-8"))
        browser_exec["prelaunched_cdp"] = "failed"
        write_json(browser_exec_path, browser_exec)
        write_json(
            self.exp / "run/preview-public-wrapper-diagnostic.json",
            {
                "schema": "cvm.provider-free-preview-public-wrapper/1",
                "operation": "preview_browser_identity",
            },
        )
        identity_failure = {
            "schema": "cvm.provider-free-scenario-failure/1",
            "scenario_identity": "issue15.provider-free.runtime-authority/1",
            "stage": "native_measurement",
            "operation": "preview_browser_identity",
            "browser_identity_substage": "live_running_image_identity",
        }
        write_json(self.exp / "run/scenario-failure.json", identity_failure)
        failure_bytes = (self.exp / "run/scenario-failure.json").read_bytes()
        identity_diagnostic_path = self.exp / "run/browser-identity-diagnostic.json"
        identity_diagnostic = {
            "schema": "cvm.provider-free-browser-identity-diagnostic/6",
            "operation": "preview_browser_identity",
            "substage": "live_running_image_identity",
            "scenario_failure": {
                "path": "run/scenario-failure.json",
                "sha256": hashlib.sha256(failure_bytes).hexdigest(),
            },
        }
        write_json(identity_diagnostic_path, identity_diagnostic)
        write_json(self.exp / "run/runtime-authority-smoke.json", receipt)
        write_terminal_manifest(status=1)
        verdict, provenance, issues, gaps = self.reviewer._runtime_authority_verdict(
            self.exp, workspace_payload
        )
        self.assertEqual("fail", verdict, issues)
        self.assertEqual(
            "run/browser-identity-diagnostic.json",
            provenance["browser_identity_diagnostic"],
        )
        self.assertEqual("closed-runtime-failure", issues[0]["classification"])
        self.assertIn("live_running_image_identity", issues[0]["detail"])
        self.assertEqual([], gaps)

        tampered_identity = json.loads(json.dumps(identity_diagnostic))
        tampered_identity["substage"] = "connected_cdp_browser_version_identity"
        write_json(identity_diagnostic_path, tampered_identity)
        write_terminal_manifest(status=1)
        verdict, provenance, issues, gaps = self.reviewer._runtime_authority_verdict(
            self.exp, workspace_payload
        )
        self.assertEqual("not_auditable", verdict)
        self.assertEqual({}, provenance)
        self.assertEqual("observability-gap", issues[0]["classification"])
        self.assertTrue(gaps)

        private_phases = (
            "source_executable_identity",
            "private_tree_materialization",
            "private_launch_image_identity",
            "playwright_package_revision_identity",
            "private_launch_version_execution",
            "private_launch_version_output_identity",
        )
        for phase in private_phases:
            with self.subTest(private_snapshot_phase=phase):
                private_failure = {
                    **identity_failure,
                    "browser_identity_substage": (
                        "private_snapshot_launch_image_identity"
                    ),
                    "browser_identity_phase": phase,
                }
                if phase == "playwright_package_revision_identity":
                    private_failure["browser_identity_check"] = (
                        "python_distribution_metadata"
                    )
                elif phase == "private_launch_version_execution":
                    private_failure["browser_identity_check"] = (
                        "sealed_memfd_creation_policy"
                    )
                write_json(
                    self.exp / "run/scenario-failure.json",
                    private_failure,
                )
                private_failure_bytes = (
                    self.exp / "run/scenario-failure.json"
                ).read_bytes()
                private_diagnostic = {
                    "schema": "cvm.provider-free-browser-identity-diagnostic/6",
                    "operation": "preview_browser_identity",
                    "substage": "private_snapshot_launch_image_identity",
                    "phase": phase,
                    "scenario_failure": {
                        "path": "run/scenario-failure.json",
                        "sha256": hashlib.sha256(
                            private_failure_bytes
                        ).hexdigest(),
                    },
                }
                if phase in {
                    "playwright_package_revision_identity",
                    "private_launch_version_execution",
                }:
                    private_diagnostic["check"] = (
                        private_failure["browser_identity_check"]
                    )
                write_json(identity_diagnostic_path, private_diagnostic)
                write_terminal_manifest(status=1)
                verdict, _provenance, issues, gaps = (
                    self.reviewer._runtime_authority_verdict(
                        self.exp, workspace_payload
                    )
                )
                self.assertEqual("fail", verdict, issues)
                self.assertIn(phase, issues[0]["detail"])
                self.assertEqual([], gaps)

        phase = "private_launch_image_identity"
        private_failure = {
            **identity_failure,
            "browser_identity_substage": (
                "private_snapshot_launch_image_identity"
            ),
            "browser_identity_phase": phase,
        }
        write_json(self.exp / "run/scenario-failure.json", private_failure)
        private_failure_bytes = (
            self.exp / "run/scenario-failure.json"
        ).read_bytes()
        private_diagnostic = {
            "schema": "cvm.provider-free-browser-identity-diagnostic/6",
            "operation": "preview_browser_identity",
            "substage": "private_snapshot_launch_image_identity",
            "phase": phase,
            "scenario_failure": {
                "path": "run/scenario-failure.json",
                "sha256": hashlib.sha256(private_failure_bytes).hexdigest(),
            },
        }
        for mutation in (
            "missing",
            "unknown",
            "reordered",
            "unbound",
            "duplicate",
            "recomputed",
        ):
            with self.subTest(private_phase_mutation=mutation):
                write_json(
                    self.exp / "run/scenario-failure.json",
                    private_failure,
                )
                write_json(identity_diagnostic_path, private_diagnostic)
                if mutation == "missing":
                    mutated = {**private_diagnostic}
                    mutated.pop("phase")
                    write_json(identity_diagnostic_path, mutated)
                elif mutation == "unknown":
                    write_json(
                        identity_diagnostic_path,
                        {**private_diagnostic, "phase": "raw-copy-error"},
                    )
                elif mutation == "reordered":
                    write_json(
                        identity_diagnostic_path,
                        {
                            **private_diagnostic,
                            "phase": "private_tree_materialization",
                        },
                    )
                elif mutation == "unbound":
                    write_json(
                        identity_diagnostic_path,
                        {
                            **private_diagnostic,
                            "scenario_failure": {
                                "path": "run/scenario-failure.json",
                                "sha256": "0" * 64,
                            },
                        },
                    )
                elif mutation == "duplicate":
                    identity_diagnostic_path.write_text(
                        json.dumps(private_diagnostic).replace(
                            f'"phase": "{phase}"',
                            '"phase": "source_executable_identity", '
                            f'"phase": "{phase}"',
                        ),
                        encoding="utf-8",
                    )
                else:
                    failure_path = self.exp / "run/scenario-failure.json"
                    failure_path.write_text(
                        json.dumps(private_failure).replace(
                            f'"browser_identity_phase": "{phase}"',
                            '"browser_identity_phase": "source_executable_identity", '
                            f'"browser_identity_phase": "{phase}"',
                        ),
                        encoding="utf-8",
                    )
                    recomputed = {
                        **private_diagnostic,
                        "scenario_failure": {
                            "path": "run/scenario-failure.json",
                            "sha256": hashlib.sha256(
                                failure_path.read_bytes()
                            ).hexdigest(),
                        },
                    }
                    write_json(identity_diagnostic_path, recomputed)
                write_terminal_manifest(status=1)
                verdict, provenance, issues, gaps = (
                    self.reviewer._runtime_authority_verdict(
                        self.exp, workspace_payload
                    )
                )
                self.assertEqual("not_auditable", verdict)
                self.assertEqual({}, provenance)
                self.assertEqual("observability-gap", issues[0]["classification"])
                self.assertTrue(gaps)

        package_checks = (
            "python_distribution_metadata",
            "playwright_package_manifest",
            "browser_manifest_entry",
            "frozen_playwright_version_match",
            "frozen_browser_revision_match",
        )
        for check in package_checks:
            with self.subTest(playwright_package_revision_check=check):
                checked_failure = {
                    **identity_failure,
                    "browser_identity_substage": (
                        "private_snapshot_launch_image_identity"
                    ),
                    "browser_identity_phase": (
                        "playwright_package_revision_identity"
                    ),
                    "browser_identity_check": check,
                }
                checked_failure_path = self.exp / "run/scenario-failure.json"
                write_json(checked_failure_path, checked_failure)
                write_json(
                    identity_diagnostic_path,
                    {
                        "schema": "cvm.provider-free-browser-identity-diagnostic/6",
                        "operation": "preview_browser_identity",
                        "substage": "private_snapshot_launch_image_identity",
                        "phase": "playwright_package_revision_identity",
                        "check": check,
                        "scenario_failure": {
                            "path": "run/scenario-failure.json",
                            "sha256": hashlib.sha256(
                                checked_failure_path.read_bytes()
                            ).hexdigest(),
                        },
                    },
                )
                write_terminal_manifest(status=1)
                verdict, _provenance, issues, gaps = (
                    self.reviewer._runtime_authority_verdict(
                        self.exp, workspace_payload
                    )
                )
                self.assertEqual("fail", verdict, issues)
                self.assertIn(check, issues[0]["detail"])
                self.assertEqual([], gaps)

        version_checks = (
            "sealed_memfd_creation_policy",
            "private_version_helper_spawn_executable_missing",
            "private_version_helper_spawn_permission",
            "private_version_helper_spawn_process_limit",
            "private_version_helper_spawn_file_limit",
            "private_version_helper_spawn_address_space",
            "private_version_helper_spawn_other",
            "private_version_handoff_setup",
            "private_version_handoff_timeout",
            "private_version_helper_exec",
            "private_version_exec_replacement",
            "private_version_probe_completion",
            "private_version_probe_timeout",
        )
        for check in version_checks:
            with self.subTest(private_version_execution_check=check):
                checked_failure = {
                    **identity_failure,
                    "browser_identity_substage": (
                        "private_snapshot_launch_image_identity"
                    ),
                    "browser_identity_phase": (
                        "private_launch_version_execution"
                    ),
                    "browser_identity_check": check,
                }
                checked_failure_path = self.exp / "run/scenario-failure.json"
                write_json(checked_failure_path, checked_failure)
                write_json(
                    identity_diagnostic_path,
                    {
                        "schema": "cvm.provider-free-browser-identity-diagnostic/6",
                        "operation": "preview_browser_identity",
                        "substage": "private_snapshot_launch_image_identity",
                        "phase": "private_launch_version_execution",
                        "check": check,
                        "scenario_failure": {
                            "path": "run/scenario-failure.json",
                            "sha256": hashlib.sha256(
                                checked_failure_path.read_bytes()
                            ).hexdigest(),
                        },
                    },
                )
                write_terminal_manifest(status=1)
                verdict, _provenance, issues, gaps = (
                    self.reviewer._runtime_authority_verdict(
                        self.exp, workspace_payload
                    )
                )
                self.assertEqual("fail", verdict, issues)
                self.assertIn(check, issues[0]["detail"])
                self.assertEqual([], gaps)

        package_failure = {
            **identity_failure,
            "browser_identity_substage": "private_snapshot_launch_image_identity",
            "browser_identity_phase": "playwright_package_revision_identity",
            "browser_identity_check": "browser_manifest_entry",
        }
        failure_path = self.exp / "run/scenario-failure.json"
        write_json(failure_path, package_failure)
        package_diagnostic = {
            "schema": "cvm.provider-free-browser-identity-diagnostic/6",
            "operation": "preview_browser_identity",
            "substage": "private_snapshot_launch_image_identity",
            "phase": "playwright_package_revision_identity",
            "check": "browser_manifest_entry",
            "scenario_failure": {
                "path": "run/scenario-failure.json",
                "sha256": hashlib.sha256(failure_path.read_bytes()).hexdigest(),
            },
        }
        for mutation in (
            "missing",
            "duplicate",
            "unknown",
            "reordered",
            "other-phase",
            "unbound",
            "recomputed",
        ):
            with self.subTest(package_revision_check_mutation=mutation):
                write_json(failure_path, package_failure)
                write_json(identity_diagnostic_path, package_diagnostic)
                if mutation == "missing":
                    changed = {**package_diagnostic}
                    changed.pop("check")
                    write_json(identity_diagnostic_path, changed)
                elif mutation == "duplicate":
                    identity_diagnostic_path.write_text(
                        json.dumps(package_diagnostic).replace(
                            '"check": "browser_manifest_entry"',
                            '"check": "python_distribution_metadata", '
                            '"check": "browser_manifest_entry"',
                        ),
                        encoding="utf-8",
                    )
                elif mutation == "unknown":
                    write_json(
                        identity_diagnostic_path,
                        {**package_diagnostic, "check": "raw-package-error"},
                    )
                elif mutation == "reordered":
                    write_json(
                        identity_diagnostic_path,
                        {
                            **package_diagnostic,
                            "check": "python_distribution_metadata",
                        },
                    )
                elif mutation == "other-phase":
                    changed_failure = {**package_failure}
                    changed_failure["browser_identity_phase"] = (
                        "private_launch_image_identity"
                    )
                    changed_failure.pop("browser_identity_check")
                    write_json(failure_path, changed_failure)
                    changed = {**package_diagnostic}
                    changed["phase"] = "private_launch_image_identity"
                    changed["scenario_failure"] = {
                        "path": "run/scenario-failure.json",
                        "sha256": hashlib.sha256(failure_path.read_bytes()).hexdigest(),
                    }
                    write_json(identity_diagnostic_path, changed)
                elif mutation == "unbound":
                    write_json(
                        identity_diagnostic_path,
                        {
                            **package_diagnostic,
                            "scenario_failure": {
                                "path": "run/scenario-failure.json",
                                "sha256": "0" * 64,
                            },
                        },
                    )
                else:
                    failure_path.write_text(
                        json.dumps(package_failure).replace(
                            '"browser_identity_check": "browser_manifest_entry"',
                            '"browser_identity_check": "python_distribution_metadata", '
                            '"browser_identity_check": "browser_manifest_entry"',
                        ),
                        encoding="utf-8",
                    )
                    changed = {**package_diagnostic}
                    changed["scenario_failure"] = {
                        "path": "run/scenario-failure.json",
                        "sha256": hashlib.sha256(failure_path.read_bytes()).hexdigest(),
                    }
                    write_json(identity_diagnostic_path, changed)
                write_terminal_manifest(status=1)
                verdict, provenance, issues, gaps = (
                    self.reviewer._runtime_authority_verdict(
                        self.exp, workspace_payload
                    )
                )
                self.assertEqual("not_auditable", verdict)
                self.assertEqual({}, provenance)
                self.assertEqual("observability-gap", issues[0]["classification"])
                self.assertTrue(gaps)

        browser_exec["prelaunched_cdp"] = "passed"
        write_json(browser_exec_path, browser_exec)
        identity_diagnostic_path.unlink()
        (self.exp / "run/preview-public-wrapper-diagnostic.json").unlink()
        write_json(
            self.exp / "run/scenario-failure.json",
            {
                "schema": "cvm.provider-free-scenario-failure/1",
                "scenario_identity": "issue15.provider-free.runtime-authority/1",
                "stage": "native_measurement",
                "operation": "preview_public_wrapper_evidence_publication",
            },
        )
        write_terminal_manifest(status=1)

        tampered_receipt = json.loads(json.dumps(receipt))
        tampered_receipt["scenario_identity"] = "tampered"
        write_json(
            self.exp / "run/runtime-authority-smoke.json", tampered_receipt
        )
        write_terminal_manifest(status=1)
        verdict, provenance, issues, gaps = self.reviewer._runtime_authority_verdict(
            self.exp, workspace_payload
        )
        self.assertEqual("not_auditable", verdict)
        self.assertEqual({}, provenance)
        self.assertEqual("observability-gap", issues[0]["classification"])
        self.assertIn("identity conflicts", issues[0]["detail"])
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
