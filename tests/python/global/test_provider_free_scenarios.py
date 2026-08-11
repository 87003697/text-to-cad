from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from scripts.pilot import provider_free_scenarios


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

    def test_scenario_error_publishes_only_closed_top_level_stage(self) -> None:
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

        for stage, failing_helper in stages:
            with self.subTest(stage=stage):
                if workspace.exists():
                    import shutil

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
                                provider_free_scenarios.ScenarioError(dangerous)
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
                self.assertEqual(
                    json.loads(receipt_text),
                    {
                        "schema": "cvm.provider-free-scenario-failure/1",
                        "scenario_identity": "issue15.provider-free.runtime-authority/1",
                        "stage": stage,
                    },
                )
                for forbidden in (
                    "secret",
                    "private/path",
                    "d" * 64,
                    "argv",
                    "env",
                    dangerous,
                ):
                    self.assertNotIn(forbidden, receipt_text)


if __name__ == "__main__":
    unittest.main()
