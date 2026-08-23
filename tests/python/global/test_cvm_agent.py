"""Provider-free contracts for the controlled native-Linux Codex worker."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.pilot import cvm_agent


class CvmAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.state = self.root / "state"
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.prompt = self.repo / "scripts/pilot/cvm_agent_surface_prompt.md"
        self.prompt.parent.mkdir(parents=True)
        self.prompt.write_text("fixed surface task\n", encoding="utf-8")
        (self.repo / "scripts/pilot/runner.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
        (self.repo / "tests/python/global").mkdir(parents=True)
        (self.repo / "tests/python/global/test_runner.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
        (self.repo / "packages/meshshot").mkdir(parents=True)
        (self.repo / "packages/meshshot/module.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
        (self.repo / "AGENTS.md").write_text("instructions\n", encoding="utf-8")
        (self.repo / "CONTRIBUTING.md").write_text("standards\n", encoding="utf-8")
        self.patches = [
            mock.patch.object(cvm_agent, "REPO_ROOT", self.repo),
            mock.patch.object(cvm_agent, "STATE_ROOT", self.state),
            mock.patch.object(cvm_agent, "SCRATCH_ROOT", self.scratch),
            mock.patch.object(cvm_agent, "PROMPT_PATH", self.prompt),
            mock.patch.object(cvm_agent, "_require_secure_scratch_root"),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self.temporary.cleanup()

    def submit(self) -> dict[str, object]:
        manifest = cvm_agent._source_manifest()
        encoded = cvm_agent.base64.urlsafe_b64encode(
            cvm_agent._manifest_bytes(manifest)
        ).decode()
        return cvm_agent.submit(
            "surface-adaptation",
            "a" * 40,
            cvm_agent._sha256(Path(cvm_agent.__file__).resolve()),
            cvm_agent._sha256(self.prompt),
            cvm_agent._source_digest(manifest),
            encoded,
            detach=lambda _command: 4321,
        )

    def test_submit_binds_exact_workflow_and_returns_opaque_handle(self) -> None:
        result = self.submit()
        self.assertRegex(str(result["handle"]), r"^cvma-[0-9a-f]{24}$")
        self.assertEqual(result["state"], "submitted")
        value = cvm_agent._load(str(result["handle"]))
        self.assertEqual(value["sourceRevision"], "a" * 40)
        self.assertFalse(value["retryAllowed"])

    def test_submit_failure_is_terminal_without_reusing_the_handle(self) -> None:
        manifest = cvm_agent._source_manifest()
        result = cvm_agent.submit(
            "surface-adaptation",
            "a" * 40,
            cvm_agent._sha256(Path(cvm_agent.__file__).resolve()),
            cvm_agent._sha256(self.prompt),
            cvm_agent._source_digest(manifest),
            cvm_agent.base64.urlsafe_b64encode(
                cvm_agent._manifest_bytes(manifest)
            ).decode(),
            detach=mock.Mock(side_effect=OSError("no process")),
        )
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["errorCheck"], "supervisor-launch")

    def test_submit_refuses_a_second_active_handle(self) -> None:
        self.submit()
        with self.assertRaisesRegex(cvm_agent.AgentError, "already active"):
            self.submit()

    def test_submit_rejects_stale_remote_workflow_before_state(self) -> None:
        manifest = cvm_agent._source_manifest()
        with self.assertRaisesRegex(cvm_agent.AgentError, "module digest mismatch"):
            cvm_agent.submit(
                "surface-adaptation",
                "a" * 40,
                "0" * 64,
                cvm_agent._sha256(self.prompt),
                cvm_agent._source_digest(manifest),
                cvm_agent.base64.urlsafe_b64encode(
                    cvm_agent._manifest_bytes(manifest)
                ).decode(),
                detach=lambda _command: 1,
            )
        self.assertFalse(self.state.exists())

    def test_source_manifest_rejects_a_lexical_escape(self) -> None:
        manifest = [
            {
                "path": "scripts/pilot/../../AGENTS.md",
                "sha256": "0" * 64,
            }
        ]
        encoded = cvm_agent.base64.urlsafe_b64encode(
            cvm_agent._manifest_bytes(manifest)
        ).decode()
        with self.assertRaisesRegex(cvm_agent.AgentError, "manifest is invalid"):
            cvm_agent._decode_manifest(encoded)

    def test_source_copy_excludes_state_and_rejects_symlinks(self) -> None:
        (self.repo / "outputs/private").mkdir(parents=True)
        (self.repo / "outputs/private/value").write_text("secret", encoding="utf-8")
        destination = self.root / "copy"
        manifest = cvm_agent._source_manifest()
        cvm_agent._copy_source(destination, manifest)
        self.assertTrue((destination / "scripts/pilot/runner.py").is_file())
        self.assertFalse((destination / "outputs").exists())

        (self.repo / "scripts/pilot/escape").symlink_to(self.root)
        with self.assertRaisesRegex(cvm_agent.AgentError, "special file"):
            cvm_agent._source_manifest()

    def test_codex_command_keeps_token_out_of_process_arguments(self) -> None:
        workspace = self.root / "workspace"
        control = self.root / "control"
        events = self.root / "events.jsonl"
        workspace.mkdir()
        control.mkdir()
        observed: dict[str, object] = {}

        def fake_run(command, **kwargs):
            observed["command"] = list(command)
            observed["env"] = dict(kwargs["environment"])
            config = Path(kwargs["environment"]["CODEX_HOME"]) / "config.toml"
            observed["config"] = config.read_text(encoding="utf-8")
            kwargs["stream"].write(b'{"usage":{"input_tokens":1}}\n')
            return 0

        def fake_materialize(codex_home, *, proxy_url, client_token):
            codex_home.mkdir(parents=True)
            (codex_home / "config.toml").write_text(
                "[marketplaces.text-to-cad]\n"
                'source = "/opt/text-to-cad-publish-tree"\n'
                '[plugins."cad@text-to-cad"]\n'
                "enabled = true\n"
                'model_provider = "venus"\n'
                "[model_providers.venus]\n"
                f"base_url = \"{proxy_url}\"\n"
                f'experimental_bearer_token = "{client_token}"\n',
                encoding="utf-8",
            )
            return mock.Mock()

        class FakeProxy:
            url = "http://127.0.0.1:12345/v1"

            def __init__(
                self,
                _target,
                _audit,
                *,
                upstream_bearer_token,
                required_client_bearer_token,
                max_upstream_attempts,
            ):
                observed["proxy_token"] = upstream_bearer_token
                observed["client_token"] = required_client_bearer_token
                observed["attempt_limit"] = max_upstream_attempts

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with (
            mock.patch.object(cvm_agent.shutil, "which", return_value="/usr/bin/codex"),
            mock.patch.object(cvm_agent, "_chown_tree"),
            mock.patch.object(cvm_agent.os, "chown") as chown,
            mock.patch.object(cvm_agent, "_require_closed_docker_socket"),
            mock.patch.object(cvm_agent, "_run_process_group", side_effect=fake_run),
            mock.patch.object(cvm_agent, "RetryProxy", FakeProxy),
            mock.patch.object(
                cvm_agent,
                "_materialize_worker_codex_home",
                side_effect=fake_materialize,
            ),
            mock.patch.dict(os.environ, {"VENUS_TOKEN": "sensitive-value"}),
        ):
            status = cvm_agent._run_codex(
                workspace, control, events, b"prompt", self.root / "audit.jsonl"
            )

        self.assertEqual(status, 0)
        self.assertNotIn("sensitive-value", " ".join(observed["command"]))
        self.assertNotIn("--approve-for-me", observed["command"])
        self.assertIn('approval_policy="never"', observed["command"])
        self.assertNotIn("sensitive-value", json.dumps(observed["env"]))
        self.assertEqual(observed["proxy_token"], "sensitive-value")
        self.assertNotEqual(observed["client_token"], "sensitive-value")
        self.assertEqual(
            observed["attempt_limit"], cvm_agent.MAX_UPSTREAM_ATTEMPTS
        )
        config = control / "home/.codex/config.toml"
        self.assertNotIn("sensitive-value", observed["config"])
        self.assertIn(str(observed["client_token"]), observed["config"])
        self.assertIn('[marketplaces.text-to-cad]', observed["config"])
        self.assertIn('[plugins."cad@text-to-cad"]', observed["config"])
        self.assertFalse(config.exists())

    def test_normal_codex_exit_still_terminates_the_process_group(self) -> None:
        process = mock.Mock(pid=321, returncode=0)
        process.communicate.return_value = (None, None)
        with (
            mock.patch.object(cvm_agent.subprocess, "Popen", return_value=process),
            mock.patch.object(
                cvm_agent.os,
                "killpg",
                side_effect=[None, ProcessLookupError()],
            ) as killpg,
        ):
            status = cvm_agent._run_process_group(
                ["codex"],
                cwd=self.repo,
                prompt=b"prompt",
                stream=mock.Mock(),
                environment={},
            )
        self.assertEqual(status, 0)
        self.assertEqual(
            killpg.call_args_list,
            [mock.call(321, cvm_agent.signal.SIGTERM), mock.call(321, 0)],
        )

    def test_stubborn_process_group_is_absent_after_sigkill(self) -> None:
        process = mock.Mock(pid=654, returncode=0)
        process.communicate.return_value = (None, None)
        with (
            mock.patch.object(cvm_agent.subprocess, "Popen", return_value=process),
            mock.patch.object(
                cvm_agent.os,
                "killpg",
                side_effect=[None, None, None, ProcessLookupError()],
            ) as killpg,
            mock.patch.object(
                cvm_agent.time,
                "monotonic",
                side_effect=[0, 0, 6, 6, 6],
            ),
            mock.patch.object(cvm_agent.time, "sleep"),
        ):
            status = cvm_agent._run_process_group(
                ["codex"],
                cwd=self.repo,
                prompt=b"prompt",
                stream=mock.Mock(),
                environment={},
            )
        self.assertEqual(status, 0)
        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(654, cvm_agent.signal.SIGTERM),
                mock.call(654, 0),
                mock.call(654, cvm_agent.signal.SIGKILL),
                mock.call(654, 0),
            ],
        )

    def test_supervisor_publishes_review_only_patch_and_usage(self) -> None:
        submitted = self.submit()
        handle = str(submitted["handle"])

        def fake_runner(workspace, control, events, _prompt, audit):
            self.assertTrue(audit.parent.name.endswith(".private"))
            self.assertNotEqual(audit.parent, workspace.parent)
            audit.write_text('{"attempt":1,"status":200}\n', encoding="utf-8")
            (workspace / "scripts/pilot/runner.py").write_text(
                "VALUE = 2\n", encoding="utf-8"
            )
            events.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 10, "output_tokens": 2},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (control / "last-message.json").write_text(
                json.dumps(
                    {
                        "summary": "candidate",
                        "diagnosis": "surface",
                        "changed_paths": ["scripts/pilot/runner.py"],
                        "tests": [],
                        "risks": [],
                        "review_request": "review patch",
                    }
                ),
                encoding="utf-8",
            )
            return 0

        result = cvm_agent.supervise(handle, runner=fake_runner)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["resultStatus"], "proposed-change")
        self.assertEqual(result["changedPaths"], ["scripts/pilot/runner.py"])
        self.assertEqual(
            result["usage"], {"input_tokens": 10, "output_tokens": 2}
        )
        value = cvm_agent._load(handle)
        exp = self.repo / "outputs" / value["group"] / value["exp"]
        self.assertTrue((exp / "candidate.patch").is_file())
        patch = (exp / "candidate.patch").read_text(encoding="utf-8")
        self.assertIn("diff --git a/scripts/pilot/runner.py b/scripts/pilot/runner.py", patch)
        self.assertTrue((exp / "report.json").is_file())
        self.assertTrue((exp / "run/venus-proxy-audit.jsonl").is_file())
        manifest = json.loads(
            (exp / "artifact_manifest.json").read_text(encoding="utf-8")
        )
        self.assertTrue(all("sha256" in item for item in manifest["files"]))
        self.assertFalse((self.scratch / f"{handle}.private").exists())
        self.assertFalse((self.scratch / f"{handle}.worker").exists())

    def test_unsafe_candidate_path_fails_and_retains_scratch(self) -> None:
        submitted = self.submit()
        handle = str(submitted["handle"])

        def fake_runner(workspace, control, events, _prompt, _audit):
            (workspace / "AGENTS.md").write_text("changed\n", encoding="utf-8")
            events.write_text(
                '{"usage":{"input_tokens":1,"output_tokens":1}}\n',
                encoding="utf-8",
            )
            (control / "last-message.json").write_text(
                json.dumps(
                    {
                        "summary": "candidate",
                        "diagnosis": "surface",
                        "changed_paths": ["AGENTS.md"],
                        "tests": [],
                        "risks": [],
                        "review_request": "review patch",
                    }
                ),
                encoding="utf-8",
            )
            return 0

        result = cvm_agent.supervise(handle, runner=fake_runner)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["errorCheck"], "candidate changed an unsafe path")
        self.assertTrue((self.scratch / f"{handle}.private").is_dir())
        self.assertTrue((self.scratch / f"{handle}.worker").is_dir())

    def test_deleting_an_unsafe_path_cannot_evade_changed_path_policy(self) -> None:
        submitted = self.submit()
        handle = str(submitted["handle"])

        def fake_runner(workspace, control, events, _prompt, _audit):
            (workspace / "scripts/pilot/runner.py").unlink()
            events.write_text(
                '{"usage":{"input_tokens":1,"output_tokens":1}}\n',
                encoding="utf-8",
            )
            (control / "last-message.json").write_text(
                json.dumps(
                    {
                        "summary": "candidate",
                        "diagnosis": "surface",
                        "changed_paths": [],
                        "tests": [],
                        "risks": [],
                        "review_request": "review patch",
                    }
                ),
                encoding="utf-8",
            )
            return 0

        with mock.patch.object(
            cvm_agent,
            "ALLOWED_CHANGED_PREFIXES",
            ("tests/python/global/",),
        ):
            result = cvm_agent.supervise(handle, runner=fake_runner)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["errorCheck"], "candidate changed an unsafe path")

    def test_handle_can_only_be_supervised_once(self) -> None:
        submitted = self.submit()
        handle = str(submitted["handle"])
        (self.state / "claims").mkdir(parents=True)
        (self.state / "claims" / handle).write_text("", encoding="utf-8")
        with self.assertRaisesRegex(cvm_agent.AgentError, "already claimed"):
            cvm_agent.supervise(handle)

    def test_supervisor_rejects_source_drift_after_submission(self) -> None:
        submitted = self.submit()
        handle = str(submitted["handle"])
        (self.repo / "scripts/pilot/runner.py").write_text(
            "VALUE = 99\n", encoding="utf-8"
        )
        result = cvm_agent.supervise(handle)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["errorCheck"], "copied source digest mismatch")

    def test_output_publication_failure_closes_the_handle(self) -> None:
        submitted = self.submit()
        handle = str(submitted["handle"])

        def fake_runner(workspace, control, events, _prompt, _audit):
            events.write_text(
                '{"usage":{"input_tokens":1,"output_tokens":1}}\n',
                encoding="utf-8",
            )
            (control / "last-message.json").write_text(
                json.dumps(
                    {
                        "summary": "diagnosis",
                        "diagnosis": "surface",
                        "changed_paths": [],
                        "tests": [],
                        "risks": [],
                        "review_request": "review diagnosis",
                    }
                ),
                encoding="utf-8",
            )
            return 0

        real_atomic_bytes = cvm_agent._atomic_bytes
        attempts = 0

        def fail_once(path, value):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("disk full")
            return real_atomic_bytes(path, value)

        with mock.patch.object(cvm_agent, "_atomic_bytes", side_effect=fail_once):
            result = cvm_agent.supervise(handle, runner=fake_runner)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["errorCheck"], "agent-output-publication")
        value = cvm_agent._load(handle)
        report = json.loads(
            (
                self.repo / "outputs" / value["group"] / value["exp"] / "report.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(report["status"], "failed")

    def test_skill_and_wrapper_keep_parent_review_as_terminal_gate(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        instructions = (repo_root / ".claude/skills/cvm-agent/SKILL.md").read_text(
            encoding="utf-8"
        )
        wrapper = (repo_root / "scripts/pilot/cvm-agent.sh").read_text(
            encoding="utf-8"
        )
        remote_wrapper = (
            repo_root / "scripts/pilot/cvm-agent-remote.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("Apply nothing until the parent reviewer", instructions)
        self.assertIn("source worktree must be clean", wrapper)
        self.assertIn("ssh -n cvm", wrapper)
        self.assertIn(".secrets/text-to-cad.env", remote_wrapper)


class MaterializeWorkerCodexHomeTests(unittest.TestCase):
    """Fail-closed contract for cvm_agent worker CODEX_HOME materialization."""

    def test_missing_authority_pointer_aborts_the_worker(self) -> None:
        with tempfile.TemporaryDirectory() as home_text:
            home = Path(home_text)
            with mock.patch.object(cvm_agent.Path, "home", return_value=home):
                with self.assertRaisesRegex(
                    cvm_agent.AgentError, "no valid plugin-authority pointer"
                ):
                    cvm_agent._materialize_worker_codex_home(
                        home / "worker-codex",
                        proxy_url="http://127.0.0.1:1/v1",
                        client_token="tok",
                    )

    def test_published_authority_materializes_and_preserves_registration(self) -> None:
        from tests.python.support import authority_fixtures

        with tempfile.TemporaryDirectory() as home_text:
            home = Path(home_text)
            fixture = authority_fixtures.build_authority(home)
            target = home / "worker-codex"
            with mock.patch.object(cvm_agent.Path, "home", return_value=home):
                observed = cvm_agent._materialize_worker_codex_home(
                    target,
                    proxy_url="http://127.0.0.1:1/v1",
                    client_token="sentinel-token",
                )
            self.assertEqual(observed.deployment_id, fixture.receipt.deployment_id)
            config = (target / "config.toml").read_text(encoding="utf-8")
            self.assertIn("[marketplaces.text-to-cad]", config)
            self.assertIn('[plugins."cad@text-to-cad"]', config)
            self.assertIn("enabled = true", config)
            self.assertIn("sentinel-token", config)
            self.assertIn("http://127.0.0.1:1/v1", config)
            # Provider TOML must NOT overwrite the marketplace source with the
            # in-sandbox path; cvm_agent runs the CLI directly (not in bwrap).
            self.assertNotIn("/opt/text-to-cad-publish-tree", config)
            self.assertTrue(
                (
                    target
                    / "plugins/cache/text-to-cad/cad"
                    / fixture.receipt.version
                    / ".codex-plugin"
                    / "plugin.json"
                ).is_file()
            )


if __name__ == "__main__":
    unittest.main()
