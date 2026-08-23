from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from scripts.pilot import provider_free_installed_plugin as provider_free
from scripts.pilot.cvm_job import protocol, runtime
from tests.python.support.authority_fixtures import build_authority
from tests.python.support.tmp_root import temporary_directory


class ProviderFreeInstalledPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = temporary_directory(prefix="provider-free-plugin-")
        self.workspace = Path(self.temporary.__enter__())
        self.home = self.workspace / "home"
        self.repo = self.workspace / "repo"
        self.state_root = self.workspace / "state"
        self.home.mkdir()
        (self.repo / "outputs").mkdir(parents=True)
        self.fixture = build_authority(self.home, dedupe_token="provider-free")
        self.group = "20260823-210000-provider-free"

    def tearDown(self) -> None:
        self.temporary.__exit__(None, None, None)

    def submit(self) -> tuple[str, list[str]]:
        captured: list[str] = []

        def detach(_handle, command, _root):
            captured.extend(command)
            return 123

        result = runtime.submit_provider_free_installed_plugin(
            "installed-plugin",
            self.group,
            state_root=self.state_root,
            host_home=self.home,
            detach=detach,
        )
        return result["job"], captured

    def plugin_list(self, **updates: object) -> dict[str, object]:
        plugin: dict[str, object] = {
            "pluginId": "cad@text-to-cad",
            "name": "cad",
            "marketplaceName": "text-to-cad",
            "version": "0.4.21",
            "installed": True,
            "enabled": True,
            "source": {"source": "local", "path": "/opt/text-to-cad-publish-tree"},
            "marketplaceSource": {
                "sourceType": "local",
                "source": "/opt/text-to-cad-publish-tree",
            },
            "installPolicy": "AVAILABLE",
            "authPolicy": "ON_INSTALL",
        }
        plugin.update(updates)
        return {"installed": [plugin], "available": []}

    def run_success(self, record: dict[str, object]) -> tuple[list[str], dict[str, str]]:
        captured: dict[str, object] = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = list(argv)
            captured["env"] = dict(kwargs["env"])
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.plugin_list()), "")

        with mock.patch.object(
            provider_free,
            "resolve_runtime",
            return_value=(Path("/usr/bin/bwrap"), Path("/usr/bin/codex")),
        ):
            status = provider_free.run_job(
                record,
                repo_root=self.repo,
                host_home=self.home,
                environ={
                    "PATH": "/usr/bin",
                    "LANG": "C.UTF-8",
                    "VENUS_TOKEN": "secret",
                    "HTTPS_PROXY": "http://proxy",
                    "OPENAI_API_KEY": "secret",
                },
                run=fake_run,
            )
        self.assertEqual(status, 0)
        return captured["argv"], captured["env"]  # type: ignore[return-value]

    def test_submit_binds_current_authority_and_no_token(self) -> None:
        with mock.patch.dict(os.environ, {"VENUS_TOKEN_SLOT": "50"}):
            handle, command = self.submit()
        state = protocol.load_state(self.state_root, handle)
        self.assertEqual(state["scenario"], "installed-plugin")
        self.assertTrue(state["provider_free"])
        self.assertIsNone(state["token_slot"])
        self.assertEqual(
            state["plugin_authority"],
            provider_free.authority_identity(self.fixture.receipt),
        )
        self.assertIn("supervise-provider-free", command)

    def test_detached_launch_rejects_changed_authority(self) -> None:
        handle, _ = self.submit()
        build_authority(self.home, dedupe_token="new-current")
        state = runtime.supervise_provider_free_installed_plugin(
            handle,
            state_root=self.state_root,
            host_home=self.home,
            interval=0.001,
        )
        self.assertEqual(state["state"], "failed")
        self.assertIn("differs from submitted", state["failure_reason"])

    def test_detached_launch_rejects_tampered_pointer(self) -> None:
        handle, _ = self.submit()
        pointer = self.home / ".text-to-cad-codex/deployments/current.json"
        pointer.write_text("{}\n", encoding="utf-8")
        state = runtime.supervise_provider_free_installed_plugin(
            handle,
            state_root=self.state_root,
            host_home=self.home,
            interval=0.001,
        )
        self.assertEqual(state["state"], "failed")
        self.assertIn("authority", state["failure_reason"])

    def test_runner_uses_offline_bwrap_without_repo_mount_and_strict_env(self) -> None:
        handle, _ = self.submit()
        record = protocol.load_state(self.state_root, handle)
        argv, env = self.run_success(record)
        self.assertIn("--unshare-net", argv)
        self.assertNotIn(os.fspath(self.repo), argv)
        self.assertIn("/opt/text-to-cad-publish-tree", argv)
        separator = argv.index("--")
        self.assertEqual(
            argv[separator + 1 :],
            [
                "/usr/bin/codex",
                "plugin",
                "list",
                "--marketplace",
                "text-to-cad",
                "--json",
            ],
        )
        self.assertEqual(
            env,
            {
                "LANG": "C.UTF-8",
                "CODEX_HOME": "/home/pilot/.codex",
                "HOME": "/home/pilot",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "XDG_CACHE_HOME": "/tmp/cache",
            },
        )
        runner_env = provider_free.build_runner_env(
            {
                "HOME": "/host/home",
                "PATH": "/usr/bin",
                "LANG": "C.UTF-8",
                "VENUS_TOKEN": "secret",
                "OPENAI_API_KEY": "secret",
                "HTTPS_PROXY": "http://proxy",
            }
        )
        self.assertEqual(
            runner_env,
            {
                "HOME": "/host/home",
                "PATH": "/usr/bin",
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

    def test_runtime_rejects_user_writable_bwrap_and_accepts_usr_runtime(self) -> None:
        with mock.patch.object(
            provider_free.shutil,
            "which",
            side_effect=("/home/pilot/.local/bin/bwrap", "/usr/bin/codex"),
        ), self.assertRaisesRegex(
            provider_free.ProviderFreeError, "bwrap.*usr/bin/bwrap"
        ):
            provider_free.resolve_runtime(
                {"PATH": "/home/pilot/.local/bin:/usr/bin"}
            )

        with mock.patch.object(
            provider_free.shutil,
            "which",
            side_effect=("/usr/bin/bwrap", "/usr/local/bin/codex"),
        ):
            self.assertEqual(
                provider_free.resolve_runtime({"PATH": "/usr/bin"}),
                (Path("/usr/bin/bwrap"), Path("/usr/local/bin/codex")),
            )

    def test_plugin_list_rejects_malformed_missing_disabled_wrong_version_and_source(self) -> None:
        invalid = (
            None,
            {"installed": [], "available": []},
            self.plugin_list(enabled=False),
            self.plugin_list(version="9.9.9"),
            self.plugin_list(
                source={"source": "local", "path": "/host/authority"}
            ),
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(provider_free.ProviderFreeError):
                provider_free.validate_plugin_list(payload, "0.4.21")

    def test_supervisor_accepts_identity_bound_artifacts_and_monitor_shape(self) -> None:
        handle, _ = self.submit()
        record = protocol.load_state(self.state_root, handle)
        self.run_success(record)
        with mock.patch.object(
            runtime,
            "_run_with_heartbeat",
            return_value=(0, 4321),
        ), mock.patch.object(runtime, "REPO_ROOT", self.repo):
            state = runtime.supervise_provider_free_installed_plugin(
                handle,
                state_root=self.state_root,
                host_home=self.home,
            )
        self.assertEqual(state["state"], "succeeded")
        status = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(status["runner_final_status"], 0)

    def test_supervisor_rejects_evidence_or_manifest_tamper(self) -> None:
        for target in ("evidence", "manifest"):
            with self.subTest(target=target):
                root = self.workspace / target
                repo = root / "repo"
                state_root = root / "state"
                (repo / "outputs").mkdir(parents=True)
                result = runtime.submit_provider_free_installed_plugin(
                    "installed-plugin",
                    self.group,
                    state_root=state_root,
                    host_home=self.home,
                    detach=lambda *_: 1,
                )
                record = protocol.load_state(state_root, result["job"])
                old_repo = self.repo
                self.repo = repo
                try:
                    self.run_success(record)
                finally:
                    self.repo = old_repo
                _, evidence_path, manifest_path = provider_free.artifact_paths(repo, record)
                path = evidence_path if target == "evidence" else manifest_path
                path.write_text("{}\n", encoding="utf-8")
                with mock.patch.object(
                    runtime, "_run_with_heartbeat", return_value=(0, 4321)
                ), mock.patch.object(runtime, "REPO_ROOT", repo):
                    state = runtime.supervise_provider_free_installed_plugin(
                        result["job"],
                        state_root=state_root,
                        host_home=self.home,
                    )
                self.assertEqual(state["state"], "failed")
                self.assertRegex(state["failure_reason"], "evidence|artifact")


class ProviderFreeShellDispatchTests(unittest.TestCase):
    def test_shell_dispatch_is_closed(self) -> None:
        with temporary_directory(prefix="provider-free-shell-") as root_text:
            root = Path(root_text)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            log = root / "ssh.log"
            ssh = fake_bin / "ssh"
            ssh.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" > \"$CVM_WRAPPER_LOG\"\n"
                "printf '%s\\n' '{\"job\":\"group/exp\",\"state\":\"submitted\"}'\n",
                encoding="utf-8",
            )
            ssh.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "CVM_WRAPPER_LOG": os.fspath(log),
            }
            script = Path(__file__).resolve().parents[3] / "scripts/pilot/cvm-submit.sh"
            valid = subprocess.run(
                [os.fspath(script), "provider-free", "installed-plugin", "20260823-210000-provider-free"],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertIn(
                "submit-provider-free 'installed-plugin' '20260823-210000-provider-free'",
                log.read_text(encoding="utf-8"),
            )
            log.unlink()
            invalid = subprocess.run(
                [os.fspath(script), "provider-free", "command", "20260823-210000-provider-free"],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
