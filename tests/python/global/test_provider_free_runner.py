from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.pilot import deployment_authority
from scripts.pilot import provider_free_runner
from scripts.pilot.cvm_job import protocol


class ProviderFreeRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="provider-free-runner-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        (self.repo / "outputs").mkdir()
        (self.repo / ".venv/bin").mkdir(parents=True)
        (self.repo / ".venv/bin/python").write_text("", encoding="utf-8")
        for declared in deployment_authority.EXECUTION_AUTHORITY_PATHS:
            path = self.repo / declared
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{declared}\n", encoding="utf-8")
            else:
                path.mkdir(parents=True, exist_ok=True)
                (path / "authority-marker.txt").write_text(
                    f"{declared}\n", encoding="utf-8"
                )
        cadpy = self.repo / deployment_authority.CADPY_RUNTIME_PATH
        cadpy.parent.mkdir(parents=True, exist_ok=True)
        cadpy.write_text("cadpy\n", encoding="utf-8")
        self.bwrap = self.repo / "trusted/usr/bin/bwrap"
        self.bwrap.parent.mkdir(parents=True)
        self.bwrap.write_text(
            "#!/bin/sh\nprintf 'bubblewrap 1.2.3\\n'\n",
            encoding="utf-8",
        )
        self.bwrap.chmod(0o755)
        self.bwrap = self.bwrap.resolve(strict=True)
        self.browser_cache = self.repo / "trusted/ms-playwright"
        self.browser = (
            self.browser_cache
            / "chromium_headless_shell-1234/"
            "chrome-headless-shell-linux64/chrome-headless-shell"
        )
        self.browser.parent.mkdir(parents=True)
        self.browser.write_bytes(b"trusted-browser")
        self.browser.chmod(0o755)
        self.browser = self.browser.resolve(strict=True)
        self.browser_cache = self.browser_cache.resolve(strict=True)
        self.trusted_bwrap_patch = mock.patch.object(
            deployment_authority,
            "TRUSTED_BWRAP_PATH",
            self.bwrap,
        )
        self.trusted_bwrap_patch.start()
        self.addCleanup(self.trusted_bwrap_patch.stop)
        self.runtime_identity = {
            "schema": "cvm.provider-free-runtime-identity/1",
            "bwrap": {
                "path": os.fspath(self.bwrap),
                "sha256": hashlib.sha256(self.bwrap.read_bytes()).hexdigest(),
                "version": "bubblewrap 1.2.3",
            },
            "chromium": {
                "revision": "1234",
                "host_cache_path": os.fspath(self.browser_cache),
                "sandbox_cache_path": deployment_authority.SANDBOX_BROWSER_CACHE,
                "executable_path": os.fspath(self.browser),
                "sha256": hashlib.sha256(self.browser.read_bytes()).hexdigest(),
            },
            "cadpy": {
                "path": deployment_authority.CADPY_RUNTIME_PATH,
                "sha256": hashlib.sha256(cadpy.read_bytes()).hexdigest(),
            },
        }
        deployed_receipt = deployment_authority.write_receipt(
            self.repo,
            source_head="a" * 40,
            runtime_identity=self.runtime_identity,
        )
        deployment_receipt_bytes = (
            self.repo / deployment_authority.RECEIPT_PATH
        ).read_bytes()
        self.group = "20260811-210000-issue15-provider-free"
        self.exp = "20260811-210001-issue15-runtime-authority"
        self.handle = f"{self.group}/{self.exp}"
        immutable_request = {
            "job_kind": "provider-free",
            "object": "issue15-runtime-authority",
            "group": self.group,
            "exp": self.exp,
            "exp_dir": f"outputs/{self.handle}",
            "scenario": {
                "name": "issue15-runtime-authority",
                "identity": "issue15.provider-free.runtime-authority/1",
            },
            "execution_profile": {
                "schema": "cvm.provider-free-execution-profile/1",
                "id": "issue15.provider-free-bounded/1",
                "provider_access": "forbidden",
            },
            "request_authority": {
                "schema": "cvm.provider-free-request-authority/1",
                "deployment_receipt": deployment_authority.RECEIPT_PATH,
                "deployment_receipt_sha256": hashlib.sha256(
                    deployment_receipt_bytes
                ).hexdigest(),
                "deployment_receipt_canonical_sha256": hashlib.sha256(
                    json.dumps(
                        deployed_receipt, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                "deployment_source_head": deployed_receipt["source_head"],
                "deployment_tree_sha256": deployed_receipt["tree_sha256"],
                "runtime_identity": self.runtime_identity,
            },
        }
        self.environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/test",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "CVM_PROVIDER_FREE_PROFILE": "issue15.provider-free-bounded/1",
            "CVM_PROVIDER_FREE_STRIPPED_NAMES": (
                "ANTHROPIC_API_KEY,HTTPS_PROXY,OPENAI_API_KEY,VENUS_TOKEN"
            ),
            "CVM_PROVIDER_FREE_JOB": self.handle,
            "CVM_PROVIDER_FREE_REQUEST_AUTHORITY_SHA256": (
                protocol.request_authority_sha256(immutable_request)
            ),
            "CVM_PROVIDER_FREE_DEPLOYMENT_TREE_SHA256": deployed_receipt[
                "tree_sha256"
            ],
            "CVM_PROVIDER_FREE_REQUEST_JSON": json.dumps(
                immutable_request, sort_keys=True, separators=(",", ":")
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
            "cadpy_runtime": {
                "schema": "cvm.audited-cadpy-runtime/1",
                "path": deployment_authority.CADPY_RUNTIME_PATH,
                "sha256": self.runtime_identity["cadpy"]["sha256"],
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
            if list(argv) == [os.fspath(self.bwrap), "--version"]:
                return subprocess.CompletedProcess(argv, 0, stdout="bubblewrap 1.2.3\n")
            captured["argv"] = list(argv)
            captured["kwargs"] = kwargs
            self.write_success_evidence()
            return subprocess.CompletedProcess(argv, 0)

        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(self.bwrap),
            ),
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
        retained_receipt = json.loads(
            (exp_dir / "run/deployed-source-authority.json").read_text(
                encoding="utf-8"
            )
        )
        deployment_authority.verify_materialized(
            exp_dir / "run/deployed-source",
            retained_receipt,
        )
        sandbox = json.loads(
            (exp_dir / "run/sandbox-enforcement.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sandbox["network"], "isolated-loopback")
        self.assertIn("--unshare-net", sandbox["argv"])
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

    def test_path_shadow_bwrap_is_rejected_before_sandbox_start(self) -> None:
        shadow = self.repo / "bin/bwrap"
        shadow.parent.mkdir(parents=True)
        shadow.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        shadow.chmod(0o755)
        with (
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(shadow),
            ),
            mock.patch.object(provider_free_runner.subprocess, "run") as run,
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, 2)
        run.assert_not_called()

    def test_wrong_attested_chromium_revision_is_rejected_before_output(self) -> None:
        self.browser.write_bytes(b"tampered-browser")
        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(self.bwrap),
            ),
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, 2)
        self.assertFalse((self.repo / "outputs" / self.handle).exists())

    def test_output_symlink_escape_is_rejected_before_any_process_or_write(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        outputs = self.repo / "outputs"
        for mutation in ("group", "exp"):
            with self.subTest(mutation=mutation):
                group_path = outputs / self.group
                try:
                    if mutation == "group":
                        group_path.symlink_to(outside, target_is_directory=True)
                    else:
                        group_path.mkdir()
                        (group_path / self.exp).symlink_to(
                            outside,
                            target_is_directory=True,
                        )
                    with (
                        mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
                        mock.patch.object(
                            provider_free_runner.shutil,
                            "which",
                            return_value=os.fspath(self.bwrap),
                        ),
                        mock.patch.object(
                            provider_free_runner.subprocess,
                            "run",
                        ) as run,
                    ):
                        run.side_effect = (
                            lambda argv, **kwargs: subprocess.CompletedProcess(
                                argv,
                                0,
                                stdout=(
                                    "bubblewrap 1.2.3\n"
                                    if list(argv)
                                    == [os.fspath(self.bwrap), "--version"]
                                    else ""
                                ),
                            )
                        )
                        status = provider_free_runner.main(
                            ["run", "issue15-runtime-authority", self.group, self.exp],
                            environ=self.environment,
                        )

                    self.assertEqual(status, 2)
                    run.assert_not_called()
                    self.assertEqual([], list(outside.iterdir()))
                finally:
                    if group_path.is_symlink():
                        group_path.unlink()
                    elif group_path.exists():
                        for child in group_path.iterdir():
                            child.unlink()
                        group_path.rmdir()

    def test_output_path_is_revalidated_after_sandbox_returns(self) -> None:
        outside = Path(self.temporary.name) / "outside-race"
        outside.mkdir()
        exp_dir = self.repo / "outputs" / self.handle

        def fake_run(argv, **_kwargs):
            if list(argv) == [os.fspath(self.bwrap), "--version"]:
                return subprocess.CompletedProcess(
                    argv, 0, stdout="bubblewrap 1.2.3\n"
                )
            if not list(argv) or list(argv)[0] != os.fspath(self.bwrap):
                return subprocess.CompletedProcess(argv, 0)
            shutil.rmtree(exp_dir)
            exp_dir.symlink_to(outside, target_is_directory=True)
            return subprocess.CompletedProcess(argv, 0)

        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(self.bwrap),
            ),
            mock.patch.object(
                provider_free_runner.subprocess,
                "run",
                side_effect=fake_run,
            ),
        ):
            status = provider_free_runner.main(
                ["run", "issue15-runtime-authority", self.group, self.exp],
                environ=self.environment,
            )

        self.assertEqual(status, 2)
        self.assertEqual([], list(outside.iterdir()))
        self.assertTrue(exp_dir.is_symlink())
        exp_dir.unlink()
        exp_dir.parent.rmdir()

    def test_exit_zero_without_runtime_authority_receipt_fails_terminalization(self) -> None:
        def fake_run(argv, **_kwargs):
            if list(argv) == [os.fspath(self.bwrap), "--version"]:
                return subprocess.CompletedProcess(argv, 0, stdout="bubblewrap 1.2.3\n")
            return subprocess.CompletedProcess(argv, 0)

        with (
            mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
            mock.patch.object(
                provider_free_runner.shutil,
                "which",
                return_value=os.fspath(self.bwrap),
            ),
            mock.patch.object(
                provider_free_runner.subprocess,
                "run",
                side_effect=fake_run,
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

    def test_terminal_manifest_rejects_symlinks_and_special_files(self) -> None:
        for mutation in ("symlink", "fifo"):
            with self.subTest(mutation=mutation):
                exp_dir = self.repo / "outputs" / self.handle
                exp_dir.mkdir(parents=True, exist_ok=True)
                unsafe = exp_dir / f"unsafe-{mutation}"
                if mutation == "symlink":
                    outside = self.repo / "outside-secret"
                    outside.write_text("secret\n", encoding="utf-8")
                    unsafe.symlink_to(outside)
                else:
                    os.mkfifo(unsafe)
                with (
                    mock.patch.object(provider_free_runner, "REPO_ROOT", self.repo),
                    self.assertRaisesRegex(
                        provider_free_runner.ProviderFreeError,
                        "symlink|special",
                    ),
                ):
                    provider_free_runner._publish_terminal_manifest(
                        exp_dir,
                        workload_status=0,
                        final_status=0,
                    )
                unsafe.unlink()


if __name__ == "__main__":
    unittest.main()
