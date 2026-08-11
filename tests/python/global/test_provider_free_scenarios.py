from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
