from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.pilot import provider_free_runner


class ProviderFreeRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="provider-free-runner-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        (self.repo / "outputs").mkdir()
        (self.repo / ".venv/bin").mkdir(parents=True)
        (self.repo / ".venv/bin/python").write_text("", encoding="utf-8")
        self.group = "20260811-210000-issue15-provider-free"
        self.exp = "20260811-210001-issue15-runtime-authority"
        self.handle = f"{self.group}/{self.exp}"
        self.environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/test",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "CVM_PROVIDER_FREE_PROFILE": "issue15.provider-free-bounded/1",
            "CVM_PROVIDER_FREE_STRIPPED_NAMES": (
                "ANTHROPIC_API_KEY,HTTPS_PROXY,OPENAI_API_KEY,VENUS_TOKEN"
            ),
        }

    def write_success_evidence(self) -> None:
        exp_dir = self.repo / "outputs" / self.handle
        (exp_dir / "run").mkdir(parents=True, exist_ok=True)
        (exp_dir / "run/provider-free-commands.jsonl").write_text(
            '{"exit_code":0}\n', encoding="utf-8"
        )
        files = [{"path": "runtime-identity.json", "size_bytes": 1, "sha256": "a" * 64}]
        tree_bytes = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        artifact = {
            "role": "launcher",
            "source": {"path": "viewer/source", "sha256": "a" * 64},
            "bundle": {"path": "skills/viewer/bundle", "sha256": "b" * 64},
            "deployed": {"path": "skills/viewer/bundle", "sha256": "b" * 64},
        }
        _artifacts = [
            artifact,
            {**artifact, "role": "server"},
            {**artifact, "role": "client"},
        ]
        receipt = {
            "schema": "issue15.runtime-authority-smoke/1",
            "scenario_identity": "issue15.provider-free.runtime-authority/1",
            "workspace": {
                "path": ".",
                "schema": "mesh-to-cad.workspace/1",
                "final_delivery": {"identity_sha256": "a" * 64},
            },
            "viewer_deployment": {
                "schema": "cvm.viewer-runtime-deployment/1",
                "artifacts": _artifacts,
            },
            "viewer_fallback": {
                "schema": "issue15.viewer-fallback-smoke/1",
                "rejected_reuse": {"http_status": 400},
                "fallback": {"action": "start"},
            },
            "native_depth_eight": {
                "native_required": True,
                "backend": {"id": "meshscope.voxblame.native-sat/1"},
                "depths": list(range(1, 9)),
            },
            "shipped_tree": {
                "schema": "cvm.deployed-runtime-tree-receipt/1",
                "file_count": 1,
                "tree_sha256": hashlib.sha256(tree_bytes).hexdigest(),
                "files": files,
            },
            "commands": "run/provider-free-commands.jsonl",
        }
        (exp_dir / "run" / "runtime-authority-smoke.json").write_text(
            json.dumps(receipt), encoding="utf-8"
        )

    def write_authority(self, _exp_dir: Path) -> dict[str, object]:
        exp_dir = self.repo / "outputs" / self.handle
        (exp_dir / "workspace-authority.json").write_text("{}\n", encoding="utf-8")
        (exp_dir / "workspace-authority.bundle").write_bytes(b"bundle")
        return {"mode": "live"}

    def test_success_runs_closed_scenario_in_network_isolated_bounded_sandbox(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = list(argv)
            captured["kwargs"] = kwargs
            self.write_success_evidence()
            return subprocess.CompletedProcess(argv, 0)

        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(provider_free_runner.shutil, "which", return_value="/usr/bin/bwrap"),
            mock.patch.object(provider_free_runner.subprocess, "run", side_effect=fake_run),
            mock.patch.object(provider_free_runner.pilot_runner, "validate_workspace_delivery", return_value={"identity_sha256": "a" * 64}),
            mock.patch.object(provider_free_runner.pilot_runner, "publish_workspace_authority", side_effect=self.write_authority),
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, 0)
        argv = captured["argv"]
        self.assertIn("--unshare-net", argv)
        self.assertIn("--unshare-pid", argv)
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)
        self.assertIn("issue15-runtime-authority", argv)
        self.assertTrue(callable(captured["kwargs"]["preexec_fn"]))
        self.assertEqual(captured["kwargs"]["timeout"], 1800)

        exp_dir = self.repo / "outputs" / self.handle
        proof_path = exp_dir / "run" / "provider-free-execution.json"
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        self.assertEqual(proof["job"], self.handle)
        self.assertEqual(proof["requests"], {"model_gateway": 0, "provider": 0, "tap": 0})
        self.assertEqual(proof["sandbox"]["network"], "isolated-loopback")
        manifest = json.loads(
            (exp_dir / "artifact_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["final_status"], 0)
        proof_bytes = proof_path.read_bytes()
        self.assertIn(
            {
                "path": "run/provider-free-execution.json",
                "size_bytes": len(proof_bytes),
                "sha256": hashlib.sha256(proof_bytes).hexdigest(),
            },
            manifest["files"],
        )

    def test_unknown_scenario_is_rejected_before_sandbox_start(self) -> None:
        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(provider_free_runner.subprocess, "run") as run,
        ):
            status = provider_free_runner.main(
                ["run", "../../bin/sh", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, 2)
        run.assert_not_called()
        self.assertFalse((self.repo / "outputs" / self.handle).exists())

    def test_exit_zero_without_runtime_authority_receipt_fails_terminalization(self) -> None:
        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(provider_free_runner.shutil, "which", return_value="/usr/bin/bwrap"),
            mock.patch.object(
                provider_free_runner.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ),
            mock.patch.object(provider_free_runner.pilot_runner, "validate_workspace_delivery", return_value={"identity_sha256": "a" * 64}),
            mock.patch.object(provider_free_runner.pilot_runner, "publish_workspace_authority", return_value={"mode": "live"}),
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, provider_free_runner.pilot_runner.ARTIFACT_CONTRACT_STATUS)
        manifest = json.loads(
            (self.repo / "outputs" / self.handle / "artifact_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            manifest["final_status"],
            provider_free_runner.pilot_runner.ARTIFACT_CONTRACT_STATUS,
        )


if __name__ == "__main__":
    unittest.main()
