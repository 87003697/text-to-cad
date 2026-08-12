from __future__ import annotations

import hashlib
import io
import json
import shutil
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
                public_results = iter((begun, measured, {"ok": True}, {"ok": True}))

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
