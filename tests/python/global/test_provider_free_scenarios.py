from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

from scripts.pilot import provider_free_scenarios
from scripts.pilot.cvm_job import protocol


class ProviderFreeScenarioEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="provider-free-scenario-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "repo"
        self.runtime = self.repo / "skills/cad-viewer/scripts/viewer"
        self.runtime.mkdir(parents=True)
        artifacts = []
        for role, source_path, bundle_path, content in (
            (
                "launcher",
                "viewer/scripts/start-agent-viewer.mjs",
                "skills/cad-viewer/scripts/viewer/scripts/start-agent-viewer.mjs",
                b"launcher",
            ),
            (
                "server",
                "viewer/src/server/server.mjs",
                "skills/cad-viewer/scripts/viewer/backend/server.mjs",
                b"server",
            ),
            (
                "client",
                "viewer/src/client/main.jsx",
                "skills/cad-viewer/scripts/viewer/dist/index.html",
                b"client",
            ),
        ):
            source = self.repo / source_path
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"source-" + content)
            destination = self.repo / bundle_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            artifacts.append(
                {
                    "role": role,
                    "source": {
                        "path": source_path,
                        "sha256": hashlib.sha256(b"source-" + content).hexdigest(),
                    },
                    "bundle": {"path": bundle_path, "sha256": hashlib.sha256(content).hexdigest()},
                }
            )
        (self.runtime / "runtime-identity.json").write_text(
            json.dumps(
                {
                    "schema": "cad-viewer.runtime-identity/1",
                    "viewer_version": "0.3.9",
                    "artifacts": artifacts,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_deployed_viewer_receipt_proves_source_bundle_and_deployed_digests(self) -> None:
        receipt = provider_free_scenarios.deployed_viewer_receipt(self.repo)

        self.assertEqual(receipt["schema"], "cvm.viewer-runtime-deployment/1")
        self.assertEqual(receipt["viewer_version"], "0.3.9")
        self.assertEqual([item["role"] for item in receipt["artifacts"]], ["launcher", "server", "client"])
        for artifact in receipt["artifacts"]:
            self.assertEqual(artifact["bundle"]["sha256"], artifact["deployed"]["sha256"])
            self.assertEqual(artifact["bundle"]["path"], artifact["deployed"]["path"])
            self.assertRegex(artifact["source"]["sha256"], r"^[0-9a-f]{64}$")

    def test_deployed_viewer_receipt_rejects_symlink_or_stale_bundle(self) -> None:
        identity = json.loads((self.runtime / "runtime-identity.json").read_text(encoding="utf-8"))
        stale = self.repo / identity["artifacts"][0]["bundle"]["path"]
        stale.write_bytes(b"stale")
        with self.assertRaisesRegex(provider_free_scenarios.ScenarioError, "digest"):
            provider_free_scenarios.deployed_viewer_receipt(self.repo)

        stale.write_bytes(b"launcher")
        physical = self.runtime
        moved = self.repo / "physical-viewer"
        physical.rename(moved)
        physical.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(provider_free_scenarios.ScenarioError, "physical"):
            provider_free_scenarios.deployed_viewer_receipt(self.repo)

    def test_deployed_viewer_receipt_rejects_stale_source(self) -> None:
        identity = json.loads(
            (self.runtime / "runtime-identity.json").read_text(encoding="utf-8")
        )
        source = self.repo / identity["artifacts"][0]["source"]["path"]
        source.write_bytes(b"stale-source")

        with self.assertRaisesRegex(provider_free_scenarios.ScenarioError, "source.*digest"):
            provider_free_scenarios.deployed_viewer_receipt(self.repo)

    def test_deployed_viewer_receipt_rejects_traversal_and_parent_symlinks(self) -> None:
        identity_path = self.runtime / "runtime-identity.json"
        original_text = identity_path.read_text(encoding="utf-8")
        for mutation in ("traversal", "parent-symlink"):
            with self.subTest(mutation=mutation):
                identity = json.loads(original_text)
                if mutation == "traversal":
                    outside = self.repo.parent / "outside-viewer-source"
                    outside.write_text("outside\n", encoding="utf-8")
                    identity["artifacts"][0]["source"] = {
                        "path": "../outside-viewer-source",
                        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                    }
                else:
                    source = self.repo / identity["artifacts"][0]["source"]["path"]
                    moved = self.repo / "real-viewer-source"
                    source.parent.rename(moved)
                    source.parent.symlink_to(moved, target_is_directory=True)
                identity_path.write_text(json.dumps(identity), encoding="utf-8")

                with self.assertRaisesRegex(
                    provider_free_scenarios.ScenarioError,
                    "source.*(path|physical|escape|symlink)",
                ):
                    provider_free_scenarios.deployed_viewer_receipt(self.repo)
                if mutation == "traversal":
                    identity_path.write_text(original_text, encoding="utf-8")

    def test_native_depth_eight_evidence_requires_explicit_native_identity(self) -> None:
        summary = {
            "schema": "voxblame.summary/1",
            "max_depth": 8,
            "errors_by_depth": [{"depth": depth} for depth in range(1, 9)],
            "objective_facts": {
                "global_depth_8_zero": True,
                "out_of_frame_clear": True,
                "no_evidence_conflict": True,
            },
        }
        payload = {
            "ok": True,
            "backend": {
                "schema": "meshscope.surface-occupancy-backend/1",
                "id": "meshscope.voxblame.native-sat/1",
                "implementation": "native",
            },
            "measurement": summary,
        }
        evidence = provider_free_scenarios.native_depth_eight_evidence(payload)
        self.assertEqual(evidence["backend"], payload["backend"])
        self.assertEqual(evidence["depths"], list(range(1, 9)))
        self.assertTrue(evidence["native_required"])

        payload["backend"] = {
            "schema": "meshscope.surface-occupancy-backend/1",
            "id": "meshscope.voxblame.python-sat/1",
            "implementation": "python",
        }
        with self.assertRaisesRegex(provider_free_scenarios.ScenarioError, "native"):
            provider_free_scenarios.native_depth_eight_evidence(payload)

    def test_cadpy_runtime_evidence_resolves_the_audited_skill_package(self) -> None:
        cadpy = self.repo / "skills/cad/scripts/packages/cadpy/src/cadpy/__init__.py"
        cadpy.parent.mkdir(parents=True)
        cadpy.write_text("AUDITED = True\n", encoding="utf-8")
        previous = sys.modules.pop("cadpy", None)
        self.addCleanup(
            lambda: sys.modules.__setitem__("cadpy", previous)
            if previous is not None
            else sys.modules.pop("cadpy", None)
        )
        with (
            mock.patch.object(provider_free_scenarios, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_scenarios,
                "CADPY_SRC",
                cadpy.parents[1],
            ),
        ):
            evidence = provider_free_scenarios.cadpy_runtime_evidence()

        self.assertEqual(evidence["path"], cadpy.relative_to(self.repo).as_posix())
        self.assertEqual(evidence["sha256"], hashlib.sha256(cadpy.read_bytes()).hexdigest())

    def test_top_level_failure_publishes_only_closed_stage_for_all_exceptions(
        self,
    ) -> None:
        workspace = self.repo / "outputs/group/exp"
        dangerous = (
            "OPENAI_API_KEY=secret\n../../private/path "
            + "d" * 64
            + " --argv --env"
        )
        stages = (
            ("viewer_deployment", "deployed_viewer_receipt"),
            ("shipped_tree", "deployed_runtime_tree_receipt"),
            ("cadpy_runtime", "cadpy_runtime_evidence"),
            ("viewer_fallback", "viewer_fallback_evidence"),
            ("candidate_workspace", "_prepare_candidate"),
            ("native_measurement", "_publish_measured_step"),
            ("finalization", "_finalize_workspace"),
        )

        for exception_type in (
            provider_free_scenarios.ScenarioError,
            PermissionError,
        ):
            for stage, failing_helper in stages:
                with self.subTest(
                    exception_type=exception_type.__name__, stage=stage
                ):
                    self._assert_closed_stage_failure(
                        workspace,
                        dangerous=dangerous,
                        stage=stage,
                        failing_helper=failing_helper,
                        exception_type=exception_type,
                    )

    def test_candidate_canonical_build_failure_names_closed_operation(self) -> None:
        workspace = self.repo / "outputs/group/canonical-build-failure"
        workspace.mkdir(parents=True)
        with (
            mock.patch.object(
                provider_free_scenarios,
                "deployed_viewer_receipt",
                return_value={"viewer_version": "test"},
            ),
            mock.patch.object(
                provider_free_scenarios,
                "deployed_runtime_tree_receipt",
                return_value={"files": []},
            ),
            mock.patch.object(
                provider_free_scenarios,
                "cadpy_runtime_evidence",
                return_value={"schema": "cadpy"},
            ),
            mock.patch.object(
                provider_free_scenarios,
                "viewer_fallback_evidence",
                return_value={"action": "start"},
            ),
            mock.patch.object(
                provider_free_scenarios,
                "_copy_candidate_sources",
                return_value=workspace / "work/candidate",
            ),
            mock.patch.object(
                provider_free_scenarios,
                "_build_candidate",
                side_effect=provider_free_scenarios.ScenarioError(
                    "canonical build rejected"
                ),
            ),
            mock.patch("sys.stderr", new_callable=io.StringIO),
        ):
            status = provider_free_scenarios.main(
                [
                    "run",
                    "issue15-runtime-authority",
                    "--workspace",
                    str(workspace),
                ]
            )

        self.assertEqual(status, 1)
        self.assertEqual(
            json.loads(
                (workspace / "run/scenario-failure.json").read_text(
                    encoding="utf-8"
                )
            ),
            {
                "schema": "cvm.provider-free-scenario-failure/1",
                "scenario_identity": "issue15.provider-free.runtime-authority/1",
                "stage": "candidate_workspace",
                "operation": "canonical_build",
            },
        )

    def test_candidate_failure_operations_follow_production_boundaries(self) -> None:
        operations = (
            ("fixture_availability", "_copy_candidate_sources"),
            ("canonical_build", "_build_candidate"),
            ("reference_preparation", "_prepare_reference"),
            ("workspace_init", "_initialize_workspace"),
        )
        for index, (operation, failing_helper) in enumerate(operations):
            with self.subTest(operation=operation):
                workspace = self.repo / f"outputs/group/operation-{index}"
                workspace.mkdir(parents=True)
                candidate = workspace / "work/candidate"
                defaults = {
                    "deployed_viewer_receipt": {"viewer_version": "test"},
                    "deployed_runtime_tree_receipt": {"files": []},
                    "cadpy_runtime_evidence": {"schema": "cadpy"},
                    "viewer_fallback_evidence": {"action": "start"},
                    "_copy_candidate_sources": candidate,
                    "_build_candidate": None,
                    "_prepare_reference": None,
                    "_initialize_workspace": None,
                    "_publish_measured_step": {"depths": list(range(1, 9))},
                    "_finalize_workspace": {"final": {}},
                }
                patchers = [
                    mock.patch.object(
                        provider_free_scenarios,
                        helper,
                        side_effect=(
                            PermissionError("sensitive failure")
                            if helper == failing_helper
                            else None
                        ),
                        return_value=(None if helper == failing_helper else value),
                    )
                    for helper, value in defaults.items()
                ]
                for patcher in patchers:
                    patcher.start()
                try:
                    with mock.patch("sys.stderr", new_callable=io.StringIO):
                        status = provider_free_scenarios.main(
                            [
                                "run",
                                "issue15-runtime-authority",
                                "--workspace",
                                str(workspace),
                            ]
                        )
                finally:
                    for patcher in reversed(patchers):
                        patcher.stop()

                self.assertEqual(status, 1)
                self.assertEqual(
                    json.loads(
                        (workspace / "run/scenario-failure.json").read_text(
                            encoding="utf-8"
                        )
                    )["operation"],
                    operation,
                )

    def test_wrapper_publication_root_artifact_is_closed_and_nonrecursive(
        self,
    ) -> None:
        workspace = self.repo / "outputs/group/wrapper-publication-root"
        workspace.mkdir(parents=True)
        dangerous = (
            "OPENAI_API_KEY=secret --argv http://127.0.0.1:9222 "
            "../../private/wrapper-path"
        )
        candidate = workspace / "work/candidate"
        defaults = {
            "deployed_viewer_receipt": {"viewer_version": "test"},
            "deployed_runtime_tree_receipt": {"files": []},
            "cadpy_runtime_evidence": {"schema": "cadpy"},
            "viewer_fallback_evidence": {"action": "start"},
            "_prepare_candidate": candidate,
            "_prepare_workspace": None,
            "_finalize_workspace": {"final": {}},
        }
        patchers = [
            mock.patch.object(
                provider_free_scenarios,
                helper,
                return_value=value,
            )
            for helper, value in defaults.items()
        ]
        for patcher in patchers:
            patcher.start()
        try:
            with (
                mock.patch.object(
                    provider_free_scenarios,
                    "_publish_measured_step",
                    side_effect=provider_free_scenarios.ScenarioError(
                        dangerous,
                        operation=(
                            "preview_public_wrapper_evidence_publication"
                        ),
                    ),
                ),
                mock.patch("sys.stderr", new_callable=io.StringIO) as stderr,
            ):
                status = provider_free_scenarios.main(
                    [
                        "run",
                        "issue15-runtime-authority",
                        "--workspace",
                        str(workspace),
                    ]
                )
        finally:
            for patcher in reversed(patchers):
                patcher.stop()

        self.assertEqual(status, 1)
        failure_path = workspace / "run/scenario-failure.json"
        failure_text = failure_path.read_text(encoding="utf-8")
        self.assertEqual(
            {
                "schema": "cvm.provider-free-scenario-failure/1",
                "scenario_identity": (
                    "issue15.provider-free.runtime-authority/1"
                ),
                "stage": "native_measurement",
                "operation": "preview_public_wrapper_evidence_publication",
            },
            json.loads(failure_text),
        )
        self.assertFalse(
            (
                workspace
                / protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
            ).exists()
        )
        for forbidden in (
            "OPENAI_API_KEY",
            "secret",
            "--argv",
            "127.0.0.1",
            "private/wrapper-path",
        ):
            self.assertNotIn(forbidden, failure_text)
            self.assertNotIn(forbidden, stderr.getvalue())

    def test_native_measurement_operations_follow_production_boundaries(self) -> None:
        workspace = self.repo / "outputs/group/native-operations"
        candidate = workspace / "work/candidate"
        command_log = workspace / "run/provider-free-commands.jsonl"
        workspace.mkdir(parents=True)
        begun = {"attempt": {"attempt": 1}}
        measured = {"ok": True}
        native = {"depths": list(range(1, 9))}
        operations = (
            ("attempt_begin", "write-plan"),
            ("voxblame_measure", "measure"),
            ("native_evidence", "native-evidence"),
            ("voxblame_preview", "preview"),
            ("step_publication", "publish"),
        )

        for operation, failing_boundary in operations:
            with self.subTest(operation=operation):
                public_results = iter(
                    (
                        begun,
                        measured,
                        {
                            "ok": True,
                            "preview": {
                                "browser_runtime": {
                                    "schema": protocol.PROVIDER_FREE_BROWSER_RUNTIME_SCHEMA,
                                    "adapter_profile": {
                                        "name": protocol.PROVIDER_FREE_BROWSER_ADAPTER_PROFILE,
                                        "sha256": "1" * 64,
                                    },
                                    "browser_identity": {
                                        "playwright": "1.60.0",
                                        "browser": "chromium-headless-shell",
                                        "revision": "1223",
                                        "version": "Google Chrome for Testing 148.0.7778.96",
                                        "sha256": "2" * 64,
                                    },
                                    "result": "passed",
                                }
                            },
                        },
                        {"ok": True},
                    )
                )

                def run_public(argv, **_kwargs):
                    boundary = (
                        "measure"
                        if "voxblame-measure" in argv
                        else (
                            "preview"
                            if "voxblame-preview" in argv
                            else (
                                "publish"
                                if "publish-step-zero" in argv
                                else "attempt"
                            )
                        )
                    )
                    if boundary == failing_boundary:
                        raise PermissionError("sensitive native failure")
                    return next(public_results)

                with (
                    mock.patch.object(
                        provider_free_scenarios,
                        "_write_json",
                        side_effect=(
                            PermissionError("sensitive native failure")
                            if failing_boundary == "write-plan"
                            else None
                        ),
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_run_public",
                        side_effect=run_public,
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "native_depth_eight_evidence",
                        side_effect=(
                            PermissionError("sensitive native failure")
                            if failing_boundary == "native-evidence"
                            else None
                        ),
                        return_value=native,
                    ),
                ):
                    with self.assertRaises(
                        provider_free_scenarios.ScenarioError
                    ) as raised:
                        provider_free_scenarios._publish_measured_step(
                            workspace,
                            candidate,
                            command_log,
                        )

                self.assertEqual(operation, raised.exception.operation)

    def test_failure_operations_are_closed_and_stage_compatible(self) -> None:
        for operation in (
            "attempt_begin",
            "voxblame_measure",
            "native_evidence",
            "voxblame_preview",
            "step_publication",
            "preview_runtime",
            "preview_browser_runtime_staging",
            "preview_browser_outer_exec_probe",
            "preview_browser_nested_exec_probe",
            "preview_dependency",
            "preview_browser_launch",
            "preview_browser_launch_process_limit",
            "preview_browser_launch_file_limit",
            "preview_browser_launch_address_space",
            "preview_browser_launch_shared_memory",
            "preview_browser_launch_executable",
            "preview_browser_launch_executable_missing",
            "preview_browser_launch_executable_permission",
            "preview_browser_launch_executable_spawn_permission",
            "preview_browser_launch_sandbox_permission",
            "preview_browser_launch_filesystem_permission",
            "preview_browser_launch_executable_dependency",
            "preview_browser_render",
            "preview_browser_result",
            *sorted(protocol.PROVIDER_FREE_PREVIEW_PUBLIC_FAILURE_OPERATIONS),
            "preview_public_wrapper_evidence_publication",
        ):
            with self.subTest(operation=operation):
                self.assertTrue(
                    protocol.provider_free_scenario_failure_operation_allowed(
                        "native_measurement", operation
                    )
                )
                self.assertFalse(
                    protocol.provider_free_scenario_failure_operation_allowed(
                        "candidate_workspace", operation
                    )
                )
        self.assertFalse(
            protocol.provider_free_scenario_failure_operation_allowed(
                "native_measurement", "shell"
            )
        )

    def test_preview_classification_maps_to_closed_native_operation(self) -> None:
        cases = {
            "preview_runtime_failed": "preview_runtime",
            "preview_dependency_failed": "preview_dependency",
            "preview_browser_launch_failed": "preview_browser_launch",
            "preview_browser_launch_process_limit_failed": (
                "preview_browser_launch_process_limit"
            ),
            "preview_browser_launch_file_limit_failed": (
                "preview_browser_launch_file_limit"
            ),
            "preview_browser_launch_address_space_failed": (
                "preview_browser_launch_address_space"
            ),
            "preview_browser_launch_shared_memory_failed": (
                "preview_browser_launch_shared_memory"
            ),
            "preview_browser_launch_executable_failed": (
                "preview_browser_launch_executable"
            ),
            "preview_browser_launch_executable_missing_failed": (
                "preview_browser_launch_executable_missing"
            ),
            "preview_browser_launch_executable_permission_failed": (
                "preview_browser_launch_executable_permission"
            ),
            "preview_browser_launch_executable_spawn_permission_failed": (
                "preview_browser_launch_executable_spawn_permission"
            ),
            "preview_browser_launch_sandbox_permission_failed": (
                "preview_browser_launch_sandbox_permission"
            ),
            "preview_browser_launch_filesystem_permission_failed": (
                "preview_browser_launch_filesystem_permission"
            ),
            "preview_browser_launch_executable_dependency_failed": (
                "preview_browser_launch_executable_dependency"
            ),
            "preview_browser_render_failed": "preview_browser_render",
            "preview_browser_result_failed": "preview_browser_result",
        }
        for classification, operation in cases.items():
            with self.subTest(classification=classification):
                with mock.patch.object(
                    provider_free_scenarios,
                    "_run_public",
                    side_effect=provider_free_scenarios.ScenarioError(
                        "sensitive browser failure",
                        classification=classification,
                    ),
                ):
                    with self.assertRaises(
                        provider_free_scenarios.ScenarioError
                    ) as raised:
                        provider_free_scenarios._run_voxblame_preview(
                            ["mesh-compare", "voxblame-preview"],
                            cwd=self.repo,
                            command_log=self.repo / "commands.jsonl",
                        )

                self.assertEqual(operation, raised.exception.operation)

    def test_preview_generic_fallback_is_split_into_eight_closed_operations(
        self,
    ) -> None:
        command_log = self.repo / "run/provider-free-commands.jsonl"
        wrapper_path = self.repo / "run/preview-public-wrapper-diagnostic.json"
        cases = (
            (
                "pre-public-setup",
                "preview_public_sandbox_setup",
                OSError("sensitive sandbox evidence denial"),
                None,
                None,
            ),
            (
                "spawn",
                "preview_public_spawn",
                None,
                PermissionError("sensitive subprocess spawn denial"),
                None,
            ),
            (
                "timeout",
                "preview_public_timeout",
                None,
                subprocess.TimeoutExpired(["nested-preview"], 600),
                None,
            ),
            (
                "unclassified-nonzero",
                "preview_public_unclassified_exit",
                None,
                subprocess.CompletedProcess(
                    ["nested-preview"], 9, stdout="not-json", stderr="sensitive"
                ),
                None,
            ),
            (
                "invalid-result-shape",
                "preview_public_result_shape",
                None,
                subprocess.CompletedProcess(
                    ["nested-preview"], 0, stdout="not-json", stderr=""
                ),
                None,
            ),
            (
                "command-evidence-publication",
                "preview_public_command_evidence_publication",
                None,
                subprocess.CompletedProcess(
                    ["nested-preview"], 0, stdout='{"ok":true}', stderr=""
                ),
                "command-log",
            ),
            (
                "failure-diagnostic-publication",
                "preview_public_failure_diagnostic_publication",
                None,
                subprocess.CompletedProcess(
                    ["nested-preview"], 7, stdout="not-json", stderr="sensitive"
                ),
                "browser-diagnostic",
            ),
            (
                "success-diagnostic-publication",
                "preview_public_success_diagnostic_publication",
                None,
                subprocess.CompletedProcess(
                    ["nested-preview"], 0, stdout='{"ok":true}', stderr=""
                ),
                "browser-diagnostic",
            ),
        )
        original_path_open = Path.open
        for name, operation, setup_failure, public_result, publication_failure in cases:
            with self.subTest(name=name):
                shutil.rmtree(command_log.parent, ignore_errors=True)
                command_log.parent.mkdir(parents=True)

                def guarded_open(path: Path, *args: object, **kwargs: object):
                    if publication_failure == "command-log" and path == command_log:
                        raise OSError("sensitive command evidence denial")
                    return original_path_open(path, *args, **kwargs)

                diagnostic_effect = (
                    OSError("sensitive browser diagnostic denial")
                    if publication_failure == "browser-diagnostic"
                    else None
                )
                with (
                    mock.patch.object(
                        provider_free_scenarios.platform,
                        "system",
                        return_value="Linux",
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_validate_attested_browser_runtime",
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_run_exact_browser_version_probe",
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_nested_browser_exec_probe_argv",
                        return_value=["nested-browser-probe"],
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_nested_node_browser_exec_probe_argv",
                        return_value=["nested-node-probe"],
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_run_closed_node_browser_version_probe",
                        side_effect=[None, None],
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_preview_sandbox_argv",
                        return_value=["nested-preview"],
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_publish_preview_sandbox_enforcement",
                        side_effect=setup_failure,
                    ),
                    mock.patch.object(
                        provider_free_scenarios.subprocess,
                        "run",
                        side_effect=(
                            public_result
                            if isinstance(public_result, BaseException)
                            else None
                        ),
                        return_value=(
                            public_result
                            if isinstance(public_result, subprocess.CompletedProcess)
                            else None
                        ),
                    ),
                    mock.patch.object(Path, "open", guarded_open),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_publish_browser_exec_diagnostic",
                        side_effect=diagnostic_effect,
                    ),
                    self.assertRaises(
                        provider_free_scenarios.ScenarioError
                    ) as raised,
                ):
                    provider_free_scenarios._run_failure_operation(
                        "native_measurement",
                        "voxblame_preview",
                        provider_free_scenarios._run_voxblame_preview,
                        ["mesh-compare", "voxblame-preview"],
                        cwd=self.repo,
                        command_log=command_log,
                    )

                self.assertEqual(operation, raised.exception.operation)
                self.assertNotIn("sensitive", str(raised.exception))
                self.assertEqual(
                    {
                        "schema": "cvm.provider-free-preview-public-wrapper/1",
                        "operation": operation,
                    },
                    json.loads(wrapper_path.read_text(encoding="utf-8")),
                )

    def test_preview_wrapper_publication_failure_uses_nonrecursive_root_operation(
        self,
    ) -> None:
        command_log = self.repo / "run/provider-free-commands.jsonl"
        wrapper_path = (
            self.repo / protocol.PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
        )

        def leave_partial_wrapper_then_fail(
            _command_log: Path,
            *,
            operation: str,
        ) -> None:
            wrapper_path.parent.mkdir(parents=True, exist_ok=True)
            wrapper_path.write_text(
                '{"schema":"partial","operation":'
                + json.dumps(operation),
                encoding="utf-8",
            )
            raise OSError("sensitive wrapper publication denial")

        cases = (
            (
                "after-public-failure",
                provider_free_scenarios.ScenarioError("sensitive public failure"),
            ),
            (
                "after-public-success",
                {"ok": True},
            ),
        )
        for name, public_result in cases:
            with self.subTest(name=name):
                with (
                    mock.patch.object(
                        provider_free_scenarios.platform,
                        "system",
                        return_value="Linux",
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_validate_attested_browser_runtime",
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_run_exact_browser_version_probe",
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_nested_browser_exec_probe_argv",
                        return_value=["nested-browser-probe"],
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_nested_node_browser_exec_probe_argv",
                        return_value=["nested-node-probe"],
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_run_closed_node_browser_version_probe",
                        side_effect=[None, None],
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_preview_sandbox_argv",
                        return_value=["nested-preview"],
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_publish_preview_sandbox_enforcement",
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_run_public",
                        side_effect=(
                            public_result
                            if isinstance(public_result, BaseException)
                            else None
                        ),
                        return_value=(
                            public_result
                            if isinstance(public_result, dict)
                            else None
                        ),
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_publish_browser_exec_diagnostic",
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_publish_preview_public_wrapper",
                        side_effect=leave_partial_wrapper_then_fail,
                    ),
                    self.assertRaises(
                        provider_free_scenarios.ScenarioError
                    ) as raised,
                ):
                    provider_free_scenarios._run_failure_operation(
                        "native_measurement",
                        "voxblame_preview",
                        provider_free_scenarios._run_voxblame_preview,
                        ["mesh-compare", "voxblame-preview"],
                        cwd=self.repo,
                        command_log=command_log,
                    )

                self.assertEqual(
                    "preview_public_wrapper_evidence_publication",
                    raised.exception.operation,
                )
                self.assertNotIn("sensitive", str(raised.exception))
                self.assertFalse(os.path.lexists(wrapper_path))

    def test_every_preview_wrapper_publication_call_site_uses_root_operation(
        self,
    ) -> None:
        command_log = self.repo / "run/provider-free-commands.jsonl"
        cases = (
            (
                "sandbox-setup",
                OSError("sensitive sandbox receipt denial"),
                None,
                None,
                None,
            ),
            (
                "failure-diagnostic",
                None,
                provider_free_scenarios.ScenarioError(
                    "sensitive public failure"
                ),
                None,
                OSError("sensitive diagnostic denial"),
            ),
            (
                "playwright-classification",
                None,
                provider_free_scenarios.ScenarioError(
                    "sensitive launch failure",
                    classification="preview_browser_launch_failed",
                ),
                None,
                None,
            ),
            (
                "renderer-classification",
                None,
                provider_free_scenarios.ScenarioError(
                    "sensitive renderer failure",
                    classification="preview_browser_render_failed",
                ),
                None,
                None,
            ),
            (
                "success-diagnostic",
                None,
                None,
                {"ok": True},
                OSError("sensitive diagnostic denial"),
            ),
        )
        for (
            name,
            sandbox_effect,
            public_effect,
            public_result,
            diagnostic_effect,
        ) in cases:
            with self.subTest(name=name):
                with (
                    mock.patch.object(
                        provider_free_scenarios.platform,
                        "system",
                        return_value="Linux",
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_validate_attested_browser_runtime",
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_run_exact_browser_version_probe",
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_nested_browser_exec_probe_argv",
                        return_value=["nested-browser-probe"],
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_nested_node_browser_exec_probe_argv",
                        return_value=["nested-node-probe"],
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_run_closed_node_browser_version_probe",
                        side_effect=[None, None],
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_preview_sandbox_argv",
                        return_value=["nested-preview"],
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_publish_preview_sandbox_enforcement",
                        side_effect=sandbox_effect,
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_run_public",
                        side_effect=public_effect,
                        return_value=public_result,
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_publish_browser_exec_diagnostic",
                        side_effect=diagnostic_effect,
                    ),
                    mock.patch.object(
                        provider_free_scenarios,
                        "_publish_preview_public_wrapper",
                        side_effect=OSError(
                            "sensitive wrapper publication denial"
                        ),
                    ),
                    self.assertRaises(
                        provider_free_scenarios.ScenarioError
                    ) as raised,
                ):
                    provider_free_scenarios._run_failure_operation(
                        "native_measurement",
                        "voxblame_preview",
                        provider_free_scenarios._run_voxblame_preview,
                        ["mesh-compare", "voxblame-preview"],
                        cwd=self.repo,
                        command_log=command_log,
                    )

                self.assertEqual(
                    "preview_public_wrapper_evidence_publication",
                    raised.exception.operation,
                )
                self.assertNotIn("sensitive", str(raised.exception))

    def test_preview_reports_outer_exact_browser_exec_probe_failure(self) -> None:
        command_log = self.repo / "run/provider-free-commands.jsonl"
        with (
            mock.patch.object(
                provider_free_scenarios.platform,
                "system",
                return_value="Linux",
            ),
            mock.patch.object(
                provider_free_scenarios,
                "_validate_attested_browser_runtime",
            ),
            mock.patch.object(
                provider_free_scenarios,
                "_preview_sandbox_argv",
                return_value=["nested-preview"],
            ),
            mock.patch.object(
                provider_free_scenarios,
                "_run_public",
                return_value={"ok": True},
            ) as run_public,
            mock.patch.object(
                provider_free_scenarios.subprocess,
                "run",
                side_effect=PermissionError("injected outer exec denial"),
            ) as run,
            self.assertRaises(
                provider_free_scenarios.ScenarioError
            ) as raised,
        ):
            provider_free_scenarios._run_voxblame_preview(
                ["mesh-compare", "voxblame-preview"],
                cwd=self.repo,
                command_log=command_log,
            )

        self.assertEqual(
            "preview_browser_outer_exec_probe",
            raised.exception.operation,
        )
        run.assert_called_once_with(
            [protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE, "--version"],
            cwd=self.repo,
            env={
                "HOME": "/nonexistent",
                "LANG": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
            start_new_session=True,
            close_fds=True,
        )
        run_public.assert_not_called()
        self.assertEqual(
            {
                "schema": "cvm.provider-free-browser-exec-diagnostic/5",
                "executable": protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
                "probe": "chromium-version-immediate-exit",
                "outer": "failed",
                "nested": "not-run",
                "node_attached": "not-run",
                "node_detached": "not-run",
                "node_failure_kind": "not-run",
                "prelaunched_cdp": "not-run",
            },
            json.loads(
                (self.repo / "run/browser-exec-diagnostic.json").read_text(
                    encoding="utf-8"
                )
            ),
        )

    def test_preview_reports_nested_exact_browser_exec_probe_failure(self) -> None:
        bwrap = self.repo / "bwrap"
        bwrap.write_text("#!/bin/sh\n", encoding="utf-8")
        bwrap.chmod(0o755)
        command_log = self.repo / "run/provider-free-commands.jsonl"
        outer = subprocess.CompletedProcess(
            [protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE, "--version"],
            0,
            stdout=b"Chromium 123.0.0.0\n",
            stderr=b"",
        )
        with (
            mock.patch.object(
                provider_free_scenarios.platform,
                "system",
                return_value="Linux",
            ),
            mock.patch.object(
                provider_free_scenarios,
                "TRUSTED_BWRAP_PATH",
                bwrap,
            ),
            mock.patch.object(
                provider_free_scenarios,
                "_validate_attested_browser_runtime",
            ),
            mock.patch.object(
                provider_free_scenarios,
                "_run_public",
                return_value={"ok": True},
            ) as run_public,
            mock.patch.object(
                provider_free_scenarios.subprocess,
                "run",
                side_effect=[
                    outer,
                    PermissionError("injected nested exec denial"),
                ],
            ) as run,
            self.assertRaises(
                provider_free_scenarios.ScenarioError
            ) as raised,
        ):
            provider_free_scenarios._run_voxblame_preview(
                ["mesh-compare", "voxblame-preview"],
                cwd=self.repo,
                command_log=command_log,
            )

        self.assertEqual(
            "preview_browser_nested_exec_probe",
            raised.exception.operation,
        )
        self.assertEqual(2, run.call_count)
        self.assertEqual(
            [
                str(bwrap),
                "--die-with-parent",
                "--new-session",
                "--cap-drop",
                "ALL",
                "--clearenv",
                "--setenv",
                "HOME",
                "/nonexistent",
                "--setenv",
                "LANG",
                "C.UTF-8",
                "--setenv",
                "PATH",
                "/usr/bin:/bin",
                "--bind",
                "/",
                "/",
                "--ro-bind",
                protocol.PROVIDER_FREE_STAGED_BROWSER_CACHE,
                protocol.PROVIDER_FREE_STAGED_BROWSER_CACHE,
                "--chdir",
                str(self.repo),
                "--",
                protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
                "--version",
            ],
            run.call_args_list[1].args[0],
        )
        self.assertEqual(
            {
                "HOME": "/nonexistent",
                "LANG": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
            },
            run.call_args_list[1].kwargs["env"],
        )
        run_public.assert_not_called()
        self.assertEqual(
            {
                "schema": "cvm.provider-free-browser-exec-diagnostic/5",
                "executable": protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
                "probe": "chromium-version-immediate-exit",
                "outer": "passed",
                "nested": "failed",
                "node_attached": "not-run",
                "node_detached": "not-run",
                "node_failure_kind": "not-run",
                "prelaunched_cdp": "not-run",
            },
            json.loads(
                (self.repo / "run/browser-exec-diagnostic.json").read_text(
                    encoding="utf-8"
                )
            ),
        )

    def test_preview_preserves_prelaunched_cdp_closed_failure_after_direct_probes(
        self,
    ) -> None:
        bwrap = self.repo / "bwrap"
        bwrap.write_text("#!/bin/sh\n", encoding="utf-8")
        bwrap.chmod(0o755)
        command_log = self.repo / "run/provider-free-commands.jsonl"
        passed = subprocess.CompletedProcess(
            [protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE, "--version"],
            0,
            stdout=b"Google Chrome for Testing 123.0.0.0\n",
            stderr=b"",
        )
        with (
            mock.patch.object(
                provider_free_scenarios.platform,
                "system",
                return_value="Linux",
            ),
            mock.patch.object(
                provider_free_scenarios,
                "TRUSTED_BWRAP_PATH",
                bwrap,
            ),
            mock.patch.object(
                provider_free_scenarios,
                "_validate_attested_browser_runtime",
            ),
            mock.patch.object(
                provider_free_scenarios.subprocess,
                "run",
                side_effect=[passed, passed],
            ),
            mock.patch.object(
                provider_free_scenarios,
                "_run_closed_node_browser_version_probe",
                side_effect=[None, None],
            ),
            mock.patch.object(
                provider_free_scenarios,
                "_run_public",
                side_effect=provider_free_scenarios.ScenarioError(
                    "sensitive Playwright launch detail",
                    classification=(
                        "preview_browser_launch_executable_"
                        "spawn_permission_failed"
                    ),
                ),
            ),
            self.assertRaises(
                provider_free_scenarios.ScenarioError
            ) as raised,
        ):
            provider_free_scenarios._run_voxblame_preview(
                ["mesh-compare", "voxblame-preview"],
                cwd=self.repo,
                command_log=command_log,
            )

        self.assertEqual(
            "preview_browser_launch_executable_spawn_permission",
            raised.exception.operation,
        )
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertEqual(
            {
                "schema": "cvm.provider-free-browser-exec-diagnostic/5",
                "executable": protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
                "probe": "chromium-version-immediate-exit",
                "outer": "passed",
                "nested": "passed",
                "node_attached": "not-run",
                "node_detached": "not-run",
                "node_failure_kind": "not-run",
                "prelaunched_cdp": "failed",
            },
            json.loads(
                (self.repo / "run/browser-exec-diagnostic.json").read_text(
                    encoding="utf-8"
                )
            ),
        )

    def test_preview_does_not_probe_bundled_node_before_prelaunched_cdp(
        self,
    ) -> None:
        from playwright._impl._driver import compute_driver_executable

        bwrap = self.repo / "bwrap"
        bwrap.write_text("#!/bin/sh\n", encoding="utf-8")
        bwrap.chmod(0o755)
        command_log = self.repo / "run/provider-free-commands.jsonl"
        passed = subprocess.CompletedProcess(
            [protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE, "--version"],
            0,
            stdout=b"Chromium 123.0.0.0\n",
            stderr=b"",
        )
        with (
            mock.patch.object(
                provider_free_scenarios.platform,
                "system",
                return_value="Linux",
            ),
            mock.patch.object(
                provider_free_scenarios,
                "TRUSTED_BWRAP_PATH",
                bwrap,
            ),
            mock.patch.object(
                provider_free_scenarios,
                "_validate_attested_browser_runtime",
            ),
            mock.patch.object(
                provider_free_scenarios.subprocess,
                "run",
                side_effect=[passed, passed],
            ) as run,
            mock.patch.object(
                provider_free_scenarios,
                "_run_closed_node_browser_version_probe",
                return_value="spawn-event",
            ) as node_probe,
            mock.patch.object(
                provider_free_scenarios,
                "_run_public",
                return_value={
                    "ok": True,
                    "preview": {
                        "browser_runtime": {
                            "schema": protocol.PROVIDER_FREE_BROWSER_RUNTIME_SCHEMA,
                            "adapter_profile": {
                                "name": protocol.PROVIDER_FREE_BROWSER_ADAPTER_PROFILE,
                                "sha256": "1" * 64,
                            },
                            "browser_identity": {
                                "playwright": "1.60.0",
                                "browser": "chromium-headless-shell",
                                "revision": "1223",
                                "version": "Google Chrome for Testing 148.0.7778.96",
                                "sha256": "2" * 64,
                            },
                            "result": "passed",
                        }
                    },
                },
            ) as run_public,
        ):
            result = provider_free_scenarios._run_voxblame_preview(
                ["mesh-compare", "voxblame-preview"],
                cwd=self.repo,
                command_log=command_log,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(2, run.call_count)
        node_probe.assert_not_called()
        run_public.assert_called_once()
        self.assertEqual(
            {
                "schema": "cvm.provider-free-browser-exec-diagnostic/5",
                "executable": protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
                "probe": "chromium-version-immediate-exit",
                "outer": "passed",
                "nested": "passed",
                "node_attached": "not-run",
                "node_detached": "not-run",
                "node_failure_kind": "not-run",
                "prelaunched_cdp": "passed",
            },
            json.loads(
                (self.repo / "run/browser-exec-diagnostic.json").read_text(
                    encoding="utf-8"
                )
            ),
        )

    def test_bundled_node_probe_rejects_other_executable_silently(self) -> None:
        completed = subprocess.run(
            [
                os.fspath(provider_free_scenarios._playwright_bundled_node()),
                os.fspath(
                    provider_free_scenarios.REPO_ROOT
                    / "scripts/pilot/browser_exec_probe.js"
                ),
                "attached",
                "/tmp/other-browser",
            ],
            cwd=provider_free_scenarios.REPO_ROOT,
            env=dict(provider_free_scenarios.BROWSER_EXEC_PROBE_ENVIRONMENT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
            start_new_session=True,
            close_fds=True,
        )

        self.assertEqual(2, completed.returncode)
        self.assertEqual(b"", completed.stdout)
        self.assertEqual(b"", completed.stderr)

    def test_node_probe_classifies_every_closed_result_and_timeout_cleanup(
        self,
    ) -> None:
        argv = ["nested-bwrap", "bundled-node", "probe.js", "attached"]
        cases = (
            ("passed", 0, b"passed\n", b"", None),
            ("nonzero-exit", 2, b"nonzero-exit\n", b"", "nonzero-exit"),
            ("output-shape", 2, b"output-shape\n", b"", "output-shape"),
            ("noisy-token", 2, b"spawn-event\nextra\n", b"", "output-shape"),
        )
        for name, returncode, stdout, stderr, expected in cases:
            with self.subTest(name=name):
                process = mock.Mock(pid=1234, returncode=returncode)
                process.communicate.return_value = (stdout, stderr)
                with (
                    mock.patch.object(
                        provider_free_scenarios.subprocess,
                        "Popen",
                        return_value=process,
                    ),
                    mock.patch.object(
                        provider_free_scenarios.os,
                        "killpg",
                    ) as killpg,
                ):
                    actual = provider_free_scenarios._run_closed_node_browser_version_probe(
                        argv,
                        cwd=self.repo,
                    )
                self.assertEqual(expected, actual)
                expected_cleanup = (
                    [
                        mock.call(1234, signal.SIGTERM),
                        mock.call(1234, signal.SIGKILL),
                    ]
                    if name == "noisy-token"
                    else []
                )
                self.assertEqual(expected_cleanup, killpg.call_args_list)
                self.assertEqual(
                    3 if expected_cleanup else 1,
                    process.communicate.call_count,
                )

        with (
            mock.patch.object(
                provider_free_scenarios.subprocess,
                "Popen",
                side_effect=PermissionError("sensitive spawn denial"),
            ),
            mock.patch.object(provider_free_scenarios.os, "killpg") as killpg,
        ):
            self.assertEqual(
                "spawn-event",
                provider_free_scenarios._run_closed_node_browser_version_probe(
                    argv,
                    cwd=self.repo,
                ),
            )
        killpg.assert_not_called()

        timed_out = mock.Mock(pid=5678, returncode=None)
        timed_out.communicate.side_effect = [
            subprocess.TimeoutExpired(argv, 5),
            (b"", b""),
            (b"", b""),
        ]
        with (
            mock.patch.object(
                provider_free_scenarios.subprocess,
                "Popen",
                return_value=timed_out,
            ),
            mock.patch.object(provider_free_scenarios.os, "killpg") as killpg,
        ):
            self.assertEqual(
                "timeout",
                provider_free_scenarios._run_closed_node_browser_version_probe(
                    argv,
                    cwd=self.repo,
                ),
            )
        self.assertEqual(
            [
                mock.call(5678, signal.SIGTERM),
                mock.call(5678, signal.SIGKILL),
            ],
            killpg.call_args_list,
        )
        self.assertEqual(3, timed_out.communicate.call_count)

    def test_node_probe_classifies_signaled_process_without_token_as_nonzero_exit(
        self,
    ) -> None:
        process = mock.Mock(pid=1234, returncode=-signal.SIGKILL)
        process.communicate.return_value = (b"", b"")
        with (
            mock.patch.object(
                provider_free_scenarios.subprocess,
                "Popen",
                return_value=process,
            ),
            mock.patch.object(provider_free_scenarios.os, "killpg") as killpg,
        ):
            actual = provider_free_scenarios._run_closed_node_browser_version_probe(
                ["nested-bwrap", "bundled-node", "probe.js", "attached"],
                cwd=self.repo,
            )

        self.assertEqual("nonzero-exit", actual)
        self.assertEqual(
            [
                mock.call(1234, signal.SIGTERM),
                mock.call(1234, signal.SIGKILL),
            ],
            killpg.call_args_list,
        )

    def test_node_probe_cleans_session_group_after_no_close_fallback(self) -> None:
        process = mock.Mock(pid=2468, returncode=2)
        process.communicate.side_effect = [
            (b"", b""),
            (b"", b""),
            (b"", b""),
        ]
        with (
            mock.patch.object(
                provider_free_scenarios.subprocess,
                "Popen",
                return_value=process,
            ),
            mock.patch.object(provider_free_scenarios.os, "killpg") as killpg,
        ):
            actual = provider_free_scenarios._run_closed_node_browser_version_probe(
                ["nested-bwrap", "bundled-node", "probe.js", "attached"],
                cwd=self.repo,
            )

        self.assertEqual("output-shape", actual)
        self.assertEqual(
            [
                mock.call(2468, signal.SIGTERM),
                mock.call(2468, signal.SIGKILL),
            ],
            killpg.call_args_list,
        )
        self.assertEqual(3, process.communicate.call_count)

    def test_node_probe_reaps_killed_child_before_publishing_failure_token(
        self,
    ) -> None:
        staged_root = Path("/tmp/provider-free-playwright")
        self.assertFalse(
            staged_root.exists(),
            "the subprocess lifecycle test refuses to replace a real staged runtime",
        )
        executable = (
            staged_root
            / "attested/chrome-headless-shell-linux64/chrome-headless-shell"
        )
        executable.parent.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, staged_root)
        child_pid_path = self.repo / "run/node-probe-child.pid"
        child_pid_path.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text(
            f"#!{sys.executable}\n"
            "import os\n"
            "import time\n"
            f"open({os.fspath(child_pid_path)!r}, 'w').write(str(os.getpid()))\n"
            "os.write(1, b'x' * 129)\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        process = subprocess.Popen(
            [
                os.fspath(provider_free_scenarios._playwright_bundled_node()),
                os.fspath(
                    provider_free_scenarios.REPO_ROOT
                    / "scripts/pilot/browser_exec_probe.js"
                ),
                "attached",
                protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
            ],
            cwd=provider_free_scenarios.REPO_ROOT,
            env=dict(provider_free_scenarios.BROWSER_EXEC_PROBE_ENVIRONMENT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
        child_pid: int | None = None
        try:
            self.assertIsNotNone(process.stdout)
            self.assertIsNotNone(process.stderr)
            self.assertEqual(b"output-shape\n", process.stdout.readline())
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)
            remaining_stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(2, process.returncode)
            self.assertEqual(b"", remaining_stdout)
            self.assertEqual(b"", stderr)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            if child_pid is not None:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_preview_ignores_retired_detached_node_probe(self) -> None:
        bwrap = self.repo / "bwrap"
        bwrap.write_text("#!/bin/sh\n", encoding="utf-8")
        bwrap.chmod(0o755)
        command_log = self.repo / "run/provider-free-commands.jsonl"
        python_passed = subprocess.CompletedProcess(
            [protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE, "--version"],
            0,
            stdout=b"Chromium 123.0.0.0\n",
            stderr=b"",
        )
        with (
            mock.patch.object(
                provider_free_scenarios.platform,
                "system",
                return_value="Linux",
            ),
            mock.patch.object(
                provider_free_scenarios,
                "TRUSTED_BWRAP_PATH",
                bwrap,
            ),
            mock.patch.object(
                provider_free_scenarios,
                "_validate_attested_browser_runtime",
            ),
            mock.patch.object(
                provider_free_scenarios.subprocess,
                "run",
                side_effect=[python_passed, python_passed],
            ) as run,
            mock.patch.object(
                provider_free_scenarios,
                "_run_closed_node_browser_version_probe",
                side_effect=[None, "spawn-event"],
            ) as node_probe,
            mock.patch.object(
                provider_free_scenarios,
                "_run_public",
                return_value={
                    "ok": True,
                    "preview": {
                        "browser_runtime": {
                            "schema": protocol.PROVIDER_FREE_BROWSER_RUNTIME_SCHEMA,
                            "adapter_profile": {
                                "name": protocol.PROVIDER_FREE_BROWSER_ADAPTER_PROFILE,
                                "sha256": "1" * 64,
                            },
                            "browser_identity": {
                                "playwright": "1.60.0",
                                "browser": "chromium-headless-shell",
                                "revision": "1223",
                                "version": "Google Chrome for Testing 148.0.7778.96",
                                "sha256": "2" * 64,
                            },
                            "result": "passed",
                        }
                    },
                },
            ) as run_public,
        ):
            result = provider_free_scenarios._run_voxblame_preview(
                ["mesh-compare", "voxblame-preview"],
                cwd=self.repo,
                command_log=command_log,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(2, run.call_count)
        node_probe.assert_not_called()
        run_public.assert_called_once()
        self.assertEqual(
            {
                "schema": "cvm.provider-free-browser-exec-diagnostic/5",
                "executable": protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
                "probe": "chromium-version-immediate-exit",
                "outer": "passed",
                "nested": "passed",
                "node_attached": "not-run",
                "node_detached": "not-run",
                "node_failure_kind": "not-run",
                "prelaunched_cdp": "passed",
            },
            json.loads(
                (self.repo / "run/browser-exec-diagnostic.json").read_text(
                    encoding="utf-8"
                )
            ),
        )

    def test_preview_linux_child_drops_outer_setup_capabilities(self) -> None:
        bwrap = self.repo / "bwrap"
        bwrap.write_text("#!/bin/sh\n", encoding="utf-8")
        bwrap.chmod(0o755)
        command = ["/workspace/repo/.venv/bin/python", "mesh-compare"]

        with (
            mock.patch.object(
                provider_free_scenarios, "TRUSTED_BWRAP_PATH", bwrap
            ),
            mock.patch.object(
                provider_free_scenarios.platform, "system", return_value="Linux"
            ),
        ):
            argv = provider_free_scenarios._preview_sandbox_argv(
                command,
                cwd=Path("/workspace/repo"),
            )

        self.assertEqual(
            [
                str(bwrap),
                "--die-with-parent",
                "--new-session",
                "--cap-drop",
                "ALL",
                "--bind",
                "/",
                "/",
                "--ro-bind",
                "/tmp/provider-free-playwright",
                "/tmp/provider-free-playwright",
                "--setenv",
                "PLAYWRIGHT_BROWSERS_PATH",
                "/tmp/provider-free-playwright",
                "--setenv",
                "MESHSHOT_BROWSER_EXECUTABLE",
                protocol.PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
                "--chdir",
                "/workspace/repo",
                "--",
                *command,
            ],
            argv,
        )
        group = "20260812-180000-preview"
        exp = "20260812-100000-issue15-runtime-authority"
        command_log = self.repo / "run/provider-free-commands.jsonl"
        expected = protocol.provider_free_preview_sandbox_argv(group, exp)
        provider_free_scenarios._publish_preview_sandbox_enforcement(
            command_log, expected
        )
        receipt = json.loads(
            (
                self.repo / protocol.PROVIDER_FREE_PREVIEW_SANDBOX_PATH
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(
            protocol.provider_free_preview_sandbox_receipt_allowed(
                receipt, group, exp
            )
        )

    def test_scenario_validates_pre_staged_attested_browser_without_copying_cache(
        self,
    ) -> None:
        staging = self.repo / "staged-cache"
        executable = (
            staging
            / "attested"
            / "chrome-headless-shell-linux64/chrome-headless-shell"
        )
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"trusted chromium")
        executable.chmod(0o755)
        (staging / "attested/resources.pak").write_bytes(b"resource")
        runtime_identity = {
            "chromium": {
                "revision": "1234",
                "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            }
        }
        receipt = {
            "schema": "cvm.deployed-source-authority/1",
            "contract_paths": list(
                provider_free_scenarios.deployment_authority.EXECUTION_AUTHORITY_PATHS
            ),
            "runtime_identity": runtime_identity,
        }
        (self.repo / ".cvm-deployment.json").write_text(
            json.dumps(receipt), encoding="utf-8"
        )
        with (
            mock.patch.object(provider_free_scenarios, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_scenarios.deployment_authority,
                "verify_receipt",
                return_value=receipt,
            ) as verify_receipt,
            mock.patch.object(shutil, "copytree") as copytree,
        ):
            provider_free_scenarios._validate_attested_browser_runtime(staging)

        verify_receipt.assert_called_once_with(self.repo, receipt)
        copytree.assert_not_called()

    def test_scenario_rejects_pre_staged_browser_digest_mismatch(self) -> None:
        staging = self.repo / "staged-cache"
        executable = staging / (
            "attested/chrome-headless-shell-linux64/chrome-headless-shell"
        )
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"tampered chromium")
        executable.chmod(0o755)
        receipt = {
            "schema": "cvm.deployed-source-authority/1",
            "contract_paths": list(
                provider_free_scenarios.deployment_authority.EXECUTION_AUTHORITY_PATHS
            ),
            "runtime_identity": {
                "chromium": {
                    "revision": "1234",
                    "sha256": hashlib.sha256(b"trusted chromium").hexdigest(),
                }
            },
        }
        (self.repo / ".cvm-deployment.json").write_text(
            json.dumps(receipt), encoding="utf-8"
        )

        with (
            mock.patch.object(provider_free_scenarios, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_scenarios.deployment_authority,
                "verify_receipt",
                return_value=receipt,
            ),
            self.assertRaises(provider_free_scenarios.ScenarioError) as raised,
        ):
            provider_free_scenarios._validate_attested_browser_runtime(staging)

        self.assertEqual(
            "preview_browser_runtime_staging",
            raised.exception.operation,
        )

    def _assert_closed_stage_failure(
        self,
        workspace: Path,
        *,
        dangerous: str,
        stage: str,
        failing_helper: str,
        exception_type: type[Exception],
    ) -> None:
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True)
        defaults = {
            "deployed_viewer_receipt": {"viewer_version": "test"},
            "deployed_runtime_tree_receipt": {"files": []},
            "cadpy_runtime_evidence": {"schema": "cadpy"},
            "viewer_fallback_evidence": {"action": "start"},
            "_prepare_candidate": workspace / "candidate",
            "_prepare_workspace": None,
            "_publish_measured_step": {"depths": list(range(1, 9))},
            "_finalize_workspace": {"final": {}},
        }
        patches = []
        for helper, value in defaults.items():
            patches.append(
                mock.patch.object(
                    provider_free_scenarios,
                    helper,
                    side_effect=(
                        exception_type(dangerous)
                        if helper == failing_helper
                        else None
                    ),
                    return_value=(None if helper == failing_helper else value),
                )
            )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        try:
            with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                status = provider_free_scenarios.main(
                    [
                        "run",
                        "issue15-runtime-authority",
                        "--workspace",
                        str(workspace),
                    ]
                )
        finally:
            for patcher in reversed(patches):
                patcher.stop()

        self.assertEqual(status, 1)
        receipt_path = workspace / "run/scenario-failure.json"
        receipt_text = receipt_path.read_text(encoding="utf-8")
        expected = {
            "schema": "cvm.provider-free-scenario-failure/1",
            "scenario_identity": "issue15.provider-free.runtime-authority/1",
            "stage": stage,
        }
        self.assertEqual(json.loads(receipt_text), expected)
        for forbidden in (
            "secret",
            "private/path",
            "d" * 64,
            "argv",
            "env",
            dangerous,
        ):
            self.assertNotIn(forbidden, receipt_text)
            self.assertNotIn(forbidden, stderr.getvalue())

    def test_control_flow_base_exceptions_are_not_converted_to_stage_receipts(
        self,
    ) -> None:
        workspace = self.repo / "outputs/group/control-flow"
        workspace.mkdir(parents=True)

        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception_type=exception_type.__name__):
                with (
                    mock.patch.object(
                        provider_free_scenarios,
                        "deployed_viewer_receipt",
                        side_effect=exception_type(),
                    ),
                    self.assertRaises(exception_type),
                ):
                    provider_free_scenarios.main(
                        [
                            "run",
                            "issue15-runtime-authority",
                            "--workspace",
                            str(workspace),
                        ]
                    )
                self.assertFalse(
                    (workspace / "run/scenario-failure.json").exists()
                )

    def test_final_payload_assembly_failure_is_closed_finalization(self) -> None:
        dangerous = "OPENAI_API_KEY=payload-secret\n../../payload/path"

        class ExplodingFinal(dict):
            def __getitem__(self, _key: object) -> object:
                raise PermissionError(dangerous)

        status, stderr, receipt = self._run_final_publication_failure(
            self.repo / "outputs/group/final-payload",
            finalized=ExplodingFinal(),
        )

        self.assertEqual(status, 1)
        self.assertEqual(receipt["stage"], "finalization")
        self.assertNotIn(dangerous, stderr)
        self.assertNotIn("payload-secret", json.dumps(receipt))

    def test_success_receipt_write_failure_is_closed_finalization(self) -> None:
        workspace = self.repo / "outputs/group/final-publication"
        dangerous = "OPENAI_API_KEY=write-secret\n../../write/path"
        real_write = provider_free_scenarios._write_json

        def fail_success_receipt(path: Path, payload: object) -> None:
            if path.name == "runtime-authority-smoke.json":
                raise PermissionError(dangerous)
            real_write(path, payload)

        status, stderr, receipt = self._run_final_publication_failure(
            workspace,
            finalized={"final": {}},
            write_json_side_effect=fail_success_receipt,
        )

        self.assertEqual(status, 1)
        self.assertEqual(receipt["stage"], "finalization")
        self.assertFalse(
            (workspace / "run/runtime-authority-smoke.json").exists()
        )
        self.assertNotIn(dangerous, stderr)
        self.assertNotIn("write-secret", json.dumps(receipt))

    def _run_final_publication_failure(
        self,
        workspace: Path,
        *,
        finalized: object,
        write_json_side_effect: Callable[[Path, object], None] | None = None,
    ) -> tuple[int, str, dict[str, object]]:
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True)
        defaults = {
            "deployed_viewer_receipt": {"viewer_version": "test"},
            "deployed_runtime_tree_receipt": {"files": []},
            "cadpy_runtime_evidence": {"schema": "cadpy"},
            "viewer_fallback_evidence": {"action": "start"},
            "_prepare_candidate": workspace / "candidate",
            "_prepare_workspace": None,
            "_publish_measured_step": {"depths": list(range(1, 9))},
            "_finalize_workspace": finalized,
        }
        patchers = [
            mock.patch.object(
                provider_free_scenarios,
                helper,
                return_value=value,
            )
            for helper, value in defaults.items()
        ]
        if write_json_side_effect is not None:
            patchers.append(
                mock.patch.object(
                    provider_free_scenarios,
                    "_write_json",
                    side_effect=write_json_side_effect,
                )
            )
        for patcher in patchers:
            patcher.start()
        try:
            with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                status = provider_free_scenarios.main(
                    [
                        "run",
                        "issue15-runtime-authority",
                        "--workspace",
                        str(workspace),
                    ]
                )
                stderr_text = stderr.getvalue()
        finally:
            for patcher in reversed(patchers):
                patcher.stop()
        receipt = json.loads(
            (workspace / "run/scenario-failure.json").read_text(
                encoding="utf-8"
            )
        )
        return status, stderr_text, receipt


if __name__ == "__main__":
    unittest.main()
