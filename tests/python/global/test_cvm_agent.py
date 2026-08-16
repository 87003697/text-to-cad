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
        self.prompt = self.repo / "scripts/pilot/cvm_agent_surface_prompt.md"
        self.prompt.parent.mkdir(parents=True)
        self.prompt.write_text("fixed surface task\n", encoding="utf-8")
        (self.repo / "scripts/pilot/runner.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
        (self.repo / "tests/python/global").mkdir(parents=True)
        (self.repo / "docs/specs").mkdir(parents=True)
        (self.repo / "packages/meshshot").mkdir(parents=True)
        self.patches = [
            mock.patch.object(cvm_agent, "REPO_ROOT", self.repo),
            mock.patch.object(cvm_agent, "STATE_ROOT", self.state),
            mock.patch.object(cvm_agent, "SCRATCH_ROOT", self.scratch),
            mock.patch.object(cvm_agent, "PROMPT_PATH", self.prompt),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self.temporary.cleanup()

    def submit(self) -> dict[str, object]:
        return cvm_agent.submit(
            "surface-adaptation",
            "a" * 40,
            cvm_agent._sha256(Path(cvm_agent.__file__).resolve()),
            cvm_agent._sha256(self.prompt),
            cvm_agent._source_digest(),
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
        result = cvm_agent.submit(
            "surface-adaptation",
            "a" * 40,
            cvm_agent._sha256(Path(cvm_agent.__file__).resolve()),
            cvm_agent._sha256(self.prompt),
            cvm_agent._source_digest(),
            detach=mock.Mock(side_effect=OSError("no process")),
        )
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["errorCheck"], "supervisor-launch")

    def test_submit_rejects_stale_remote_workflow_before_state(self) -> None:
        with self.assertRaisesRegex(cvm_agent.AgentError, "module digest mismatch"):
            cvm_agent.submit(
                "surface-adaptation",
                "a" * 40,
                "0" * 64,
                cvm_agent._sha256(self.prompt),
                cvm_agent._source_digest(),
                detach=lambda _command: 1,
            )
        self.assertFalse(self.state.exists())

    def test_source_copy_excludes_state_and_rejects_symlinks(self) -> None:
        (self.repo / "outputs/private").mkdir(parents=True)
        (self.repo / "outputs/private/value").write_text("secret", encoding="utf-8")
        destination = self.root / "copy"
        cvm_agent._copy_source(destination)
        self.assertTrue((destination / "scripts/pilot/runner.py").is_file())
        self.assertFalse((destination / "outputs").exists())

        (self.repo / "escape").symlink_to(self.root)
        with self.assertRaisesRegex(cvm_agent.AgentError, "contains a symlink"):
            cvm_agent._copy_source(self.root / "rejected")

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

        class FakeProxy:
            url = "http://127.0.0.1:12345/v1"

            def __init__(
                self,
                _target,
                _audit,
                *,
                upstream_bearer_token,
                required_client_bearer_token,
            ):
                observed["proxy_token"] = upstream_bearer_token
                observed["client_token"] = required_client_bearer_token

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with (
            mock.patch.object(cvm_agent.shutil, "which", return_value="/usr/bin/codex"),
            mock.patch.object(cvm_agent, "_chown_tree"),
            mock.patch.object(cvm_agent.os, "chown"),
            mock.patch.object(cvm_agent, "_require_closed_docker_socket"),
            mock.patch.object(cvm_agent, "_run_process_group", side_effect=fake_run),
            mock.patch.object(cvm_agent, "RetryProxy", FakeProxy),
            mock.patch.dict(os.environ, {"VENUS_TOKEN": "sensitive-value"}),
        ):
            status = cvm_agent._run_codex(
                workspace, control, events, b"prompt"
            )

        self.assertEqual(status, 0)
        self.assertNotIn("sensitive-value", " ".join(observed["command"]))
        self.assertNotIn("sensitive-value", json.dumps(observed["env"]))
        self.assertEqual(observed["proxy_token"], "sensitive-value")
        self.assertNotEqual(observed["client_token"], "sensitive-value")
        config = control / "home/.codex/config.toml"
        self.assertNotIn("sensitive-value", observed["config"])
        self.assertIn(str(observed["client_token"]), observed["config"])
        self.assertFalse(config.exists())

    def test_supervisor_publishes_review_only_patch_and_usage(self) -> None:
        submitted = self.submit()
        handle = str(submitted["handle"])

        def fake_runner(workspace, control, events, _prompt):
            (workspace / "scripts/pilot/runner.py").write_text(
                "VALUE = 2\n", encoding="utf-8"
            )
            events.write_text(
                json.dumps(
                    {"type": "turn.completed", "usage": {"input_tokens": 10}}
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
        self.assertEqual(result["usage"], {"input_tokens": 10})
        value = cvm_agent._load(handle)
        exp = self.repo / "outputs" / value["group"] / value["exp"]
        self.assertTrue((exp / "candidate.patch").is_file())
        self.assertTrue((exp / "report.json").is_file())
        self.assertFalse((self.scratch / f"{handle}.private").exists())
        self.assertFalse((self.scratch / f"{handle}.worker").exists())

    def test_unsafe_candidate_path_fails_and_retains_scratch(self) -> None:
        submitted = self.submit()
        handle = str(submitted["handle"])

        def fake_runner(workspace, control, events, _prompt):
            (workspace / "AGENTS.md").write_text("changed\n", encoding="utf-8")
            events.write_text(
                '{"usage":{"input_tokens":1}}\n', encoding="utf-8"
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

    def test_handle_can_only_be_supervised_once(self) -> None:
        submitted = self.submit()
        handle = str(submitted["handle"])
        (self.state / "claims").mkdir(parents=True)
        (self.state / "claims" / handle).write_text("", encoding="utf-8")
        with self.assertRaisesRegex(cvm_agent.AgentError, "already claimed"):
            cvm_agent.supervise(handle)

    def test_skill_and_wrapper_keep_parent_review_as_terminal_gate(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        instructions = (repo_root / ".claude/skills/cvm-agent/SKILL.md").read_text(
            encoding="utf-8"
        )
        wrapper = (repo_root / "scripts/pilot/cvm-agent.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("Apply nothing until the parent reviewer", instructions)
        self.assertIn("source worktree must be clean", wrapper)
        self.assertIn("ssh -n cvm", wrapper)


if __name__ == "__main__":
    unittest.main()
