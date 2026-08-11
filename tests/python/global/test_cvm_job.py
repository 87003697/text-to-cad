from __future__ import annotations

import json
import hashlib
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts.pilot.cvm_job import __main__ as cvm_job_cli
from scripts.pilot.cvm_job import protocol, runtime
from scripts.pilot import deployment_authority
from tests.python.support.paths import REPO_ROOT
from tests.python.support.tmp_root import temporary_directory


PILOT_ROOT = REPO_ROOT / "scripts" / "pilot"
SUBMIT_SCRIPT = PILOT_ROOT / "cvm-submit.sh"
MONITOR_SCRIPT = PILOT_ROOT / "cvm-monitor.sh"
MONITOR_SKILL = REPO_ROOT / ".claude" / "skills" / "cvm-monitor" / "SKILL.md"


class CvmJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = temporary_directory(prefix="cvm-job-")
        self.root_text = self.temporary.__enter__()
        self.workspace = Path(self.root_text)
        self.state_root = self.workspace / ".cvm-jobs"
        self.repo_root = self.workspace / "repo"
        self.repo_root.mkdir()
        (self.repo_root / "outputs").mkdir()
        for declared in deployment_authority.EXECUTION_AUTHORITY_PATHS:
            path = self.repo_root / declared
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{declared}\n", encoding="utf-8")
            else:
                path.mkdir(parents=True, exist_ok=True)
                (path / "authority-marker.txt").write_text(
                    f"{declared}\n", encoding="utf-8"
                )
        cadpy = self.repo_root / deployment_authority.CADPY_RUNTIME_PATH
        cadpy.parent.mkdir(parents=True, exist_ok=True)
        cadpy.write_text("cadpy\n", encoding="utf-8")
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
        deployment_authority.write_receipt(
            self.repo_root,
            source_head="a" * 40,
            runtime_identity=runtime_identity,
        )
        self.repo_patch = mock.patch.object(runtime, "REPO_ROOT", self.repo_root)
        self.repo_patch.start()
        self.group = "20260805-170000-audit"

    def tearDown(self) -> None:
        self.repo_patch.stop()
        self.temporary.__exit__(None, None, None)

    def submit(self) -> str:
        def fake_detach(handle, command, root):
            self.detached = (handle, list(command), root)
            return 1234

        result = runtime.submit_pilot(
            "airplane",
            self.group,
            state_root=self.state_root,
            detach=fake_detach,
        )
        return result["job"]

    def write_manifest(self, handle: str, final_status: object) -> None:
        path = self.repo_root / "outputs" / handle / "artifact_manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"final_status": final_status}), encoding="utf-8")

    def write_provider_free_terminal_evidence(
        self,
        handle: str,
        *,
        complete: bool = True,
    ) -> None:
        state = protocol.load_state(self.state_root, handle)
        exp_dir = self.repo_root / "outputs" / handle
        exp_dir.mkdir(parents=True, exist_ok=True)
        proof = {
            "schema": "cvm.provider-free-execution/1",
            "job": handle,
            "scenario": state["scenario"],
            "execution_profile": state["execution_profile"],
            "request_authority": {
                "sha256": state["request_authority_sha256"],
                "deployment_tree_sha256": state["request_authority"][
                    "deployment_tree_sha256"
                ],
                "immutable_request": protocol.request_authority_payload(state),
            },
            "sandbox": {
                "network": "isolated-loopback",
                "resource_profile": state["execution_profile"]["id"],
            },
            "provider_environment": {
                "allowlist": ["HOME", "LANG", "PATH", "PYTHONDONTWRITEBYTECODE", "TZ"],
                "stripped": [
                    "ANTHROPIC_API_KEY",
                    "HTTPS_PROXY",
                    "OPENAI_API_KEY",
                    "VENUS_TOKEN",
                ],
                "credential_values_recorded": False,
            },
            "requests": {"model_gateway": 0, "provider": 0, "tap": 0},
            "sandbox_enforcement": {
                "path": "run/sandbox-enforcement.json",
                "sha256": "",
            },
        }
        proof_path = exp_dir / "run" / "provider-free-execution.json"
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        files = []
        if complete:
            deployed_receipt_path = self.repo_root / ".cvm-deployment.json"
            deployed_receipt = json.loads(deployed_receipt_path.read_bytes())
            deployment_authority.materialize_receipt(
                self.repo_root,
                deployed_receipt,
                exp_dir / "run/deployed-source",
            )
            (exp_dir / "run/deployed-source-authority.json").write_bytes(
                deployed_receipt_path.read_bytes()
            )
            runtime_identity = deployed_receipt["runtime_identity"]
            (exp_dir / "run/sandbox-enforcement.json").write_text(
                json.dumps(
                    {
                        "schema": "cvm.provider-free-sandbox-enforcement/1",
                        "network": "isolated-loopback",
                        "argv": runtime.provider_free_sandbox_argv(
                            state["scenario"]["name"],
                            exp_dir,
                            runtime_identity,
                        ),
                        "environment_names": [
                            "HOME",
                            "LANG",
                            "PATH",
                            "PLAYWRIGHT_BROWSERS_PATH",
                            "PYTHONDONTWRITEBYTECODE",
                            "TZ",
                        ],
                        "required_environment": runtime.PROVIDER_FREE_REQUIRED_ENVIRONMENT,
                        "sandbox_profile": runtime.PROVIDER_FREE_SANDBOX_PROFILE,
                        "runtime_identity": runtime_identity,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            sandbox_bytes = (exp_dir / "run/sandbox-enforcement.json").read_bytes()
            proof["sandbox_enforcement"]["sha256"] = hashlib.sha256(
                sandbox_bytes
            ).hexdigest()
            for name, data in (
                ("run/runtime-authority-smoke.json", b"{}\n"),
                ("workspace-authority.json", b"{}\n"),
                ("workspace-authority.bundle", b"bundle"),
                ("workspace.json", b"{}\n"),
                ("final/manifest.json", b"{}\n"),
            ):
                destination = exp_dir / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                files.append(
                    {
                        "path": name,
                        "size_bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
            for path in sorted((exp_dir / "run").rglob("*")):
                relative = path.relative_to(exp_dir).as_posix()
                if path.is_file() and relative not in {
                    item["path"] for item in files
                }:
                    data = path.read_bytes()
                    files.append(
                        {
                            "path": relative,
                            "size_bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        }
                    )
        proof_path.write_text(
            json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        proof_bytes = proof_path.read_bytes()
        files.append(
            {
                "path": "run/provider-free-execution.json",
                "size_bytes": len(proof_bytes),
                "sha256": hashlib.sha256(proof_bytes).hexdigest(),
            }
        )
        manifest = {
            "schema_version": 1,
            "workload_status": 0,
            "final_status": 0,
            "files": files,
        }
        (exp_dir / "artifact_manifest.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_submit_returns_stable_handle_before_child_start(self) -> None:
        handle = self.submit()
        self.assertRegex(
            handle,
            rf"^{self.group}/\d{{8}}-\d{{6}}-airplane$",
        )
        state = protocol.load_state(self.state_root, handle)
        self.assertEqual(state["state"], "submitted")
        self.assertIsNone(state["supervisor_pid"])
        self.assertEqual(self.detached[0], handle)
        self.assertIn("supervise-pilot", self.detached[1])

    def test_provider_free_submit_binds_scenario_and_execution_profile(self) -> None:
        detached: dict[str, object] = {}

        def fake_detach(handle, command, root):
            detached.update(handle=handle, command=list(command), root=root)
            return 1234

        result = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=fake_detach,
        )

        handle = result["job"]
        state = protocol.load_state(self.state_root, handle)
        self.assertEqual(result["kind"], "provider-free")
        self.assertEqual(state["job_kind"], "provider-free")
        self.assertEqual(
            state["scenario"],
            {
                "name": "issue15-runtime-authority",
                "identity": "issue15.provider-free.runtime-authority/1",
            },
        )
        self.assertEqual(
            state["execution_profile"],
            {
                "schema": "cvm.provider-free-execution-profile/1",
                "id": "issue15.provider-free-bounded/1",
                "provider_access": "forbidden",
                "sandbox_profile": "cvm.provider-free-linux-sandbox/1",
            },
        )
        self.assertEqual(
            state["request_authority"]["deployment_tree_sha256"],
            json.loads(
                (self.repo_root / ".cvm-deployment.json").read_text(encoding="utf-8")
            )["tree_sha256"],
        )
        self.assertEqual(
            state["request_authority_sha256"],
            protocol.request_authority_sha256(state),
        )
        self.assertIn("supervise-provider-free", detached["command"])

    def test_provider_free_submit_cli_returns_compact_stable_handle(self) -> None:
        expected = {
            "job": f"{self.group}/20260811-210000-issue15-runtime-authority",
            "state": "submitted",
            "kind": "provider-free",
        }
        with (
            mock.patch.object(runtime, "submit_provider_free", return_value=expected),
            mock.patch.object(cvm_job_cli, "submit_provider_free", return_value=expected),
            mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as stdout,
        ):
            status = cvm_job_cli.main(
                [
                    "--state-root",
                    os.fspath(self.state_root),
                    "submit-provider-free",
                    "issue15-runtime-authority",
                    self.group,
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue()), expected)

    def test_provider_free_supervisor_cli_reports_terminal_state(self) -> None:
        handle = f"{self.group}/20260811-210000-issue15-runtime-authority"
        expected = {
            "job": handle,
            "state": "succeeded",
            "kind": "pilot",
            "job_kind": "provider-free",
        }
        with (
            mock.patch.object(
                cvm_job_cli,
                "supervise_provider_free",
                return_value=expected,
                create=True,
            ) as supervise,
            mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as stdout,
        ):
            status = cvm_job_cli.main(
                [
                    "--state-root",
                    os.fspath(self.state_root),
                    "supervise-provider-free",
                    "--job",
                    handle,
                ]
            )

        self.assertEqual(status, 0)
        supervise.assert_called_once_with(handle, state_root=self.state_root)
        self.assertEqual(json.loads(stdout.getvalue()), expected)

    def test_provider_free_supervisor_uses_closed_runner_and_stripped_environment(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        captured: dict[str, object] = {}

        def fake_run(root, job, command, **kwargs):
            captured["command"] = list(command)
            captured["env"] = dict(kwargs["env"])
            self.write_provider_free_terminal_evidence(handle)
            return 0, 4321

        hostile_environment = {
            "PATH": os.environ["PATH"],
            "HOME": os.fspath(self.workspace),
            "LANG": "C.UTF-8",
            "VENUS_TOKEN": "do-not-forward",
            "OPENAI_API_KEY": "do-not-forward",
            "ANTHROPIC_API_KEY": "do-not-forward",
            "HTTPS_PROXY": "http://provider-proxy.invalid",
        }
        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ=hostile_environment,
            )

        self.assertEqual(state["state"], "succeeded")
        self.assertEqual(
            captured["command"],
            [
                sys.executable,
                "-m",
                runtime.PROVIDER_FREE_RUNNER_MODULE,
                "run",
                "issue15-runtime-authority",
                self.group,
                protocol.parse_handle(handle)["exp"],
            ],
        )
        child_environment = captured["env"]
        self.assertEqual(child_environment["CVM_PROVIDER_FREE_PROFILE"], "issue15.provider-free-bounded/1")
        self.assertEqual(
            child_environment["CVM_PROVIDER_FREE_STRIPPED_NAMES"],
            "ANTHROPIC_API_KEY,HTTPS_PROXY,OPENAI_API_KEY,VENUS_TOKEN",
        )
        for forbidden in ("VENUS_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HTTPS_PROXY"):
            self.assertNotIn(forbidden, child_environment)
        self.assertEqual(
            state["no_provider_evidence"],
            f"outputs/{handle}/run/provider-free-execution.json",
        )
        with mock.patch.object(runtime, "_observe_pilot", return_value={}):
            public = runtime.status_job(handle, state_root=self.state_root)
        self.assertEqual(public["kind"], "provider-free")
        self.assertEqual(public["scenario"], state["scenario"])
        self.assertNotIn("bootstrap_diagnostic", public)

    def test_provider_free_supervisor_imports_runner_from_repo_owned_cwd(self) -> None:
        shutil.copytree(
            REPO_ROOT / "scripts",
            self.repo_root / "scripts",
            dirs_exist_ok=True,
        )
        previous_receipt = json.loads(
            (self.repo_root / deployment_authority.RECEIPT_PATH).read_text(
                encoding="utf-8"
            )
        )
        deployment_authority.write_receipt(
            self.repo_root,
            source_head=previous_receipt["source_head"],
            runtime_identity=previous_receipt["runtime_identity"],
        )
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        caller_cwd = self.workspace / "non-repository-caller"
        caller_cwd.mkdir()
        supervisor_log = protocol.log_path(self.state_root, handle)
        supervisor_log.parent.mkdir(parents=True, exist_ok=True)
        original_cwd = Path.cwd()
        try:
            os.chdir(caller_cwd)
            with supervisor_log.open("ab", buffering=0) as stream:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.pilot.cvm_job",
                        "--state-root",
                        os.fspath(self.state_root),
                        "supervise-provider-free",
                        "--job",
                        handle,
                    ],
                    cwd=self.repo_root,
                    env={
                        "HOME": os.fspath(self.workspace),
                        "PATH": os.environ["PATH"],
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
        finally:
            os.chdir(original_cwd)

        self.assertEqual(completed.returncode, 1)
        state = protocol.load_state(self.state_root, handle)
        self.assertEqual(state["state"], "failed")
        self.assertEqual(
            state["bootstrap_diagnostic"],
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "before-experiment",
                "classification": "runner-environment-allowlist-rejected",
                "process_exit_code": 2,
            },
        )
        self.assertNotIn(
            "ModuleNotFoundError",
            supervisor_log.read_text(encoding="utf-8"),
        )

    def test_provider_free_supervisor_rejects_incomplete_terminal_evidence(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        def fake_run(*_args, **_kwargs):
            self.write_provider_free_terminal_evidence(handle, complete=False)
            return 0, 4321

        with mock.patch.object(
            runtime,
            "_run_with_heartbeat",
            side_effect=fake_run,
        ):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={
                    "PATH": os.environ["PATH"],
                    "HOME": os.fspath(self.workspace),
                    "VENUS_TOKEN": "do-not-forward",
                    "OPENAI_API_KEY": "do-not-forward",
                    "ANTHROPIC_API_KEY": "do-not-forward",
                    "HTTPS_PROXY": "http://provider-proxy.invalid",
                },
            )

        self.assertEqual(state["state"], "failed")
        self.assertIn("terminal evidence", state["failure_reason"])
        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )
        self.assertNotIn("bootstrap_diagnostic", public)

    def test_provider_free_bootstrap_failure_has_bounded_public_diagnostic(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        secret = "credential-value-that-must-not-be-published"
        diagnostic_log = protocol.log_path(self.state_root, handle)
        diagnostic_log.parent.mkdir(parents=True, exist_ok=True)
        diagnostic_log.write_text(
            "attacker-controlled-output\n" * 500
            + "Traceback (most recent call last):\n"
            + "ModuleNotFoundError: missing bootstrap module\n"
            + f"OPENAI_API_KEY={secret}\n",
            encoding="utf-8",
        )

        with mock.patch.object(
            runtime,
            "_run_with_heartbeat",
            return_value=(1, 4321),
        ):
            state = runtime.supervise_provider_free(
                handle,
                state_root=self.state_root,
                environ={
                    "PATH": os.environ["PATH"],
                    "OPENAI_API_KEY": secret,
                },
            )

        expected = {
            "schema": "cvm.provider-free-bootstrap-diagnostic/1",
            "phase": "before-experiment",
            "classification": "python-import-failed",
            "process_exit_code": 1,
        }
        self.assertEqual(state["state"], "failed")
        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )
        waited, exit_code = runtime.wait_job(
            handle,
            state_root=self.state_root,
            timeout=0,
        )
        self.assertEqual(public["bootstrap_diagnostic"], expected)
        self.assertEqual(waited["bootstrap_diagnostic"], expected)
        self.assertEqual(exit_code, 1)
        serialized = json.dumps(public, sort_keys=True)
        self.assertLess(len(serialized), 1_024)
        self.assertNotIn(secret, serialized)
        self.assertNotIn("attacker-controlled-output", serialized)
        self.assertNotIn("\n", serialized)

    def test_provider_free_runner_contract_failures_have_closed_diagnostics(self) -> None:
        hostile_suffix = (
            " OPENAI_API_KEY=credential-value-that-must-not-be-published\n"
            "../../private/runtime/path\n"
            f"{'d' * 64}\n"
            "ModuleNotFoundError: attacker-controlled suffix\n"
            "provider-free-runner: trusted runtime identity is invalid: injected\n"
            "attacker-controlled-output"
        )
        cases = (
            (
                "provider-free execution profile is missing or stale",
                "runner-execution-profile-rejected",
            ),
            (
                "provider-free environment contains non-allowlisted names:",
                "runner-environment-allowlist-rejected",
            ),
            (
                "provider-free stripped-name receipt is invalid",
                "runner-stripped-name-receipt-rejected",
            ),
            (
                "provider-free CVM_PROVIDER_FREE_REQUEST_AUTHORITY_SHA256 "
                "is missing or invalid",
                "runner-request-digest-rejected",
            ),
            (
                "provider-free CVM_PROVIDER_FREE_DEPLOYMENT_TREE_SHA256 "
                "is missing or invalid",
                "runner-request-digest-rejected",
            ),
            (
                "provider-free immutable request is invalid",
                "runner-request-digest-rejected",
            ),
            (
                "provider-free immutable request digest conflicts",
                "runner-request-digest-rejected",
            ),
            (
                "PATH bwrap does not match trusted system runtime",
                "runner-bwrap-path-rejected",
            ),
            (
                "trusted runtime identity is invalid:",
                "runner-runtime-identity-rejected",
            ),
            (
                "unsafe provider-free output path:",
                "runner-output-path-rejected",
            ),
            (
                "unknown repository-owned failure:",
                "runner-contract-rejected",
            ),
            (
                "unknown repository-owned failure:"
                + "x" * (runtime.PROVIDER_FREE_BOOTSTRAP_LOG_BYTES + 1)
                + "\nprovider-free-runner: trusted runtime identity is invalid: "
                "injected",
                "runner-contract-rejected",
            ),
        )

        for index, (runner_error, classification) in enumerate(cases):
            with self.subTest(classification=classification):
                group = f"20260805-17{index:04d}-audit"
                handle = runtime.submit_provider_free(
                    "issue15-runtime-authority",
                    group,
                    state_root=self.state_root,
                    detach=lambda *args: 1234,
                )["job"]
                diagnostic_log = protocol.log_path(self.state_root, handle)
                diagnostic_log.parent.mkdir(parents=True, exist_ok=True)
                diagnostic_log.write_text(
                    "provider-free-runner: " + runner_error + hostile_suffix,
                    encoding="utf-8",
                )

                with mock.patch.object(
                    runtime,
                    "_run_with_heartbeat",
                    return_value=(2, 4321),
                ):
                    state = runtime.supervise_provider_free(
                        handle,
                        state_root=self.state_root,
                        environ={"PATH": os.environ["PATH"]},
                    )

                expected = {
                    "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                    "phase": "before-experiment",
                    "classification": classification,
                    "process_exit_code": 2,
                }
                public = runtime.status_job(
                    handle,
                    state_root=self.state_root,
                    include_observation=False,
                )
                waited, exit_code = runtime.wait_job(
                    handle,
                    state_root=self.state_root,
                    timeout=0,
                )
                self.assertEqual(state["bootstrap_diagnostic"], expected)
                self.assertEqual(public["bootstrap_diagnostic"], expected)
                self.assertEqual(waited["bootstrap_diagnostic"], expected)
                self.assertEqual(exit_code, 1)
                serialized = json.dumps(
                    {"state": state["bootstrap_diagnostic"], "public": public},
                    sort_keys=True,
                )
                for forbidden in (
                    "credential-value-that-must-not-be-published",
                    "OPENAI_API_KEY",
                    "../../private/runtime/path",
                    "d" * 64,
                    "ModuleNotFoundError",
                    "attacker-controlled-output",
                    "\n",
                ):
                    self.assertNotIn(forbidden, serialized)

    def test_public_bootstrap_diagnostic_rejects_invalid_closed_state(self) -> None:
        invalid_diagnostics = (
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/2",
                "phase": "before-experiment",
                "classification": "runner-runtime-identity-rejected",
                "process_exit_code": 2,
            },
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "during-experiment",
                "classification": "runner-runtime-identity-rejected",
                "process_exit_code": 2,
            },
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "before-experiment",
                "classification": "attacker-selected-classification",
                "process_exit_code": 2,
            },
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "before-experiment",
                "classification": "runner-runtime-identity-rejected",
                "process_exit_code": True,
            },
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "before-experiment",
                "classification": "runner-runtime-identity-rejected",
                "process_exit_code": 256,
            },
        )

        for index, diagnostic in enumerate(invalid_diagnostics):
            with self.subTest(diagnostic=diagnostic):
                group = f"20260805-18{index:04d}-audit"
                handle = runtime.submit_provider_free(
                    "issue15-runtime-authority",
                    group,
                    state_root=self.state_root,
                    detach=lambda *args: 1234,
                )["job"]
                protocol.transition(self.state_root, handle, "running")
                protocol.transition(
                    self.state_root,
                    handle,
                    "failed",
                    process_exit_code=2,
                    bootstrap_diagnostic=diagnostic,
                )

                public = runtime.status_job(
                    handle,
                    state_root=self.state_root,
                    include_observation=False,
                )

                self.assertNotIn("bootstrap_diagnostic", public)

    def test_public_bootstrap_diagnostic_ignores_unbounded_extra_fields(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        protocol.transition(self.state_root, handle, "running")
        protocol.transition(
            self.state_root,
            handle,
            "failed",
            process_exit_code=1,
            bootstrap_diagnostic={
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "before-experiment",
                "classification": "runner-exited-before-artifact-manifest",
                "process_exit_code": 1,
                "detail": "OPENAI_API_KEY=secret\n" * 500,
                "environment": {"VENUS_TOKEN": "secret"},
            },
        )

        public = runtime.status_job(
            handle,
            state_root=self.state_root,
            include_observation=False,
        )

        self.assertEqual(
            public["bootstrap_diagnostic"],
            {
                "schema": "cvm.provider-free-bootstrap-diagnostic/1",
                "phase": "before-experiment",
                "classification": "runner-exited-before-artifact-manifest",
                "process_exit_code": 1,
            },
        )
        self.assertNotIn("secret", json.dumps(public))

    def test_provider_free_supervisor_rejects_changed_sandbox_contract(self) -> None:
        for mutation in ("limit", "extra-bind", "browser-bind", "environment"):
            with self.subTest(mutation=mutation):
                handle = runtime.submit_provider_free(
                    "issue15-runtime-authority",
                    self.group,
                    state_root=self.state_root,
                    detach=lambda *args: 1234,
                )["job"]
                def fake_run(*_args, **_kwargs):
                    self.write_provider_free_terminal_evidence(handle)
                    exp_dir = self.repo_root / "outputs" / handle
                    sandbox_path = exp_dir / "run/sandbox-enforcement.json"
                    sandbox = json.loads(sandbox_path.read_text(encoding="utf-8"))
                    if mutation == "limit":
                        sandbox["sandbox_profile"]["resource_limits"][
                            "cpu_seconds"
                        ] = 1
                    elif mutation == "extra-bind":
                        sandbox["argv"][1:1] = ["--bind", "/", "/workspace/repo"]
                    elif mutation == "browser-bind":
                        sandbox["argv"].remove(
                            sandbox["runtime_identity"]["chromium"][
                                "host_cache_path"
                            ]
                        )
                    else:
                        sandbox["required_environment"][
                            "PLAYWRIGHT_BROWSERS_PATH"
                        ] = "/tmp"
                    sandbox_path.write_text(
                        json.dumps(sandbox, sort_keys=True, separators=(",", ":"))
                        + "\n",
                        encoding="utf-8",
                    )
                    proof_path = exp_dir / "run/provider-free-execution.json"
                    proof = json.loads(proof_path.read_text(encoding="utf-8"))
                    proof["sandbox_enforcement"]["sha256"] = hashlib.sha256(
                        sandbox_path.read_bytes()
                    ).hexdigest()
                    proof_path.write_text(
                        json.dumps(proof, sort_keys=True, separators=(",", ":"))
                        + "\n",
                        encoding="utf-8",
                    )
                    manifest_path = exp_dir / "artifact_manifest.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    for relative in (
                        "run/sandbox-enforcement.json",
                        "run/provider-free-execution.json",
                    ):
                        data = (exp_dir / relative).read_bytes()
                        entry = next(
                            item
                            for item in manifest["files"]
                            if item["path"] == relative
                        )
                        entry.update(
                            size_bytes=len(data),
                            sha256=hashlib.sha256(data).hexdigest(),
                        )
                    manifest_path.write_text(
                        json.dumps(manifest, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    return 0, 4321

                with mock.patch.object(
                    runtime, "_run_with_heartbeat", side_effect=fake_run
                ):
                    state = runtime.supervise_provider_free(
                        handle,
                        state_root=self.state_root,
                        environ={
                            "PATH": os.environ["PATH"],
                            "HOME": os.fspath(self.workspace),
                            "VENUS_TOKEN": "stripped",
                            "OPENAI_API_KEY": "stripped",
                            "ANTHROPIC_API_KEY": "stripped",
                            "HTTPS_PROXY": "stripped",
                        },
                    )

                self.assertEqual(state["state"], "failed")
                self.assertIn("sandbox enforcement", state["failure_reason"])

    def test_submit_launch_failure_is_terminal(self) -> None:
        def fail_detach(handle, command, root):
            raise OSError("no process")

        result = runtime.submit_pilot(
            "airplane",
            self.group,
            state_root=self.state_root,
            detach=fail_detach,
        )
        state = protocol.load_state(self.state_root, result["job"])
        self.assertEqual(state["state"], "failed")
        self.assertIn("launch failed", state["failure_reason"])

    def test_group_allocation_lock_serializes_handle_creation(self) -> None:
        fixed = datetime(2026, 8, 5, 17, 0, 0, tzinfo=timezone.utc)
        completed = threading.Event()
        result: dict[str, object] = {}

        def submit_second() -> None:
            result.update(
                runtime.submit_pilot(
                    "airplane",
                    self.group,
                    state_root=self.state_root,
                    detach=lambda *args: 1,
                )
            )
            completed.set()

        with mock.patch.object(runtime, "datetime") as clock:
            clock.now.return_value = fixed
            with runtime._allocation_lock(self.state_root, self.group):
                thread = threading.Thread(target=submit_second)
                thread.start()
                time.sleep(0.05)
                self.assertFalse(completed.is_set())
                first_exp = runtime._allocate_exp(
                    "airplane", self.group, self.state_root
                )
                protocol.publish_state(
                    self.state_root,
                    runtime._pilot_record(
                        "airplane", self.group, first_exp, self.state_root
                    ),
                )
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(
            result["job"],
            f"{self.group}/20260805-170000-airplane-2",
        )

    def test_submit_rejects_group_that_pilot_runner_cannot_use(self) -> None:
        with self.assertRaisesRegex(protocol.ProtocolError, "invalid pilot group"):
            runtime.submit_pilot(
                "airplane",
                "group",
                state_root=self.state_root,
                detach=lambda *args: 1,
            )
        self.assertFalse((self.state_root / "pilots").exists())

    def test_provider_free_allocation_rejects_symlinked_output_components(self) -> None:
        fixed = datetime(2026, 8, 11, 22, 30, 0, tzinfo=timezone.utc)
        outside = self.workspace / "outside"
        outside.mkdir()
        outputs = self.repo_root / "outputs"
        for mutation in ("group", "exp"):
            with self.subTest(mutation=mutation):
                group = f"20260805-170000-audit-{mutation}"
                group_path = outputs / group
                try:
                    if mutation == "group":
                        group_path.symlink_to(outside, target_is_directory=True)
                    else:
                        group_path.mkdir()
                        (
                            group_path
                            / "20260811-223000-issue15-runtime-authority"
                        ).symlink_to(outside, target_is_directory=True)
                    with (
                        mock.patch.object(runtime, "datetime") as clock,
                        self.assertRaisesRegex(protocol.ProtocolError, "output path"),
                    ):
                        clock.now.return_value = fixed
                        runtime.submit_provider_free(
                            "issue15-runtime-authority",
                            group,
                            state_root=self.state_root,
                            detach=lambda *args: 1234,
                        )
                    self.assertEqual([], list(outside.iterdir()))
                    self.assertFalse((self.state_root / "pilots" / group).exists())
                finally:
                    if group_path.is_symlink():
                        group_path.unlink()
                    elif group_path.exists():
                        for child in group_path.iterdir():
                            child.unlink()
                        group_path.rmdir()

    def test_provider_free_supervisor_revalidates_output_before_subprocess(self) -> None:
        handle = runtime.submit_provider_free(
            "issue15-runtime-authority",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1234,
        )["job"]
        group_path = self.repo_root / "outputs" / self.group
        outside = self.workspace / "outside-supervisor"
        outside.mkdir()
        group_path.rmdir()
        group_path.symlink_to(outside, target_is_directory=True)
        try:
            with mock.patch.object(runtime, "_run_with_heartbeat") as run:
                state = runtime.supervise_provider_free(
                    handle,
                    state_root=self.state_root,
                    environ={"PATH": os.environ["PATH"]},
                )
            run.assert_not_called()
            self.assertEqual("failed", state["state"])
            self.assertEqual([], list(outside.iterdir()))
        finally:
            group_path.unlink()

    def test_provider_free_poisoned_exact_exp_is_terminal_without_subprocess(
        self,
    ) -> None:
        for mutation in ("empty", ".git", "run", ".gitignore"):
            with self.subTest(mutation=mutation):
                group = f"20260805-170000-poison-{mutation.strip('.')}"
                handle = runtime.submit_provider_free(
                    "issue15-runtime-authority",
                    group,
                    state_root=self.state_root,
                    detach=lambda *args: 1234,
                )["job"]
                state = protocol.load_state(self.state_root, handle)
                exp_dir = self.repo_root / state["exp_dir"]
                exp_dir.mkdir()
                outside = self.workspace / f"outside-{mutation.strip('.')}"
                if mutation == ".gitignore":
                    outside.write_text("sentinel\n", encoding="utf-8")
                    (exp_dir / mutation).symlink_to(outside)
                elif mutation != "empty":
                    outside.mkdir()
                    (exp_dir / mutation).symlink_to(
                        outside,
                        target_is_directory=True,
                    )
                try:
                    with mock.patch.object(
                        runtime,
                        "_run_with_heartbeat",
                        return_value=(2, 4321),
                    ) as run:
                        terminal = runtime.supervise_provider_free(
                            handle,
                            state_root=self.state_root,
                            environ={"PATH": os.environ["PATH"]},
                        )
                    run.assert_not_called()
                    self.assertEqual("failed", terminal["state"])
                    self.assertEqual(
                        terminal,
                        protocol.load_state(self.state_root, handle),
                    )
                    with self.assertRaisesRegex(
                        protocol.ProtocolError,
                        "cannot start from failed",
                    ):
                        runtime.supervise_provider_free(
                            handle,
                            state_root=self.state_root,
                            environ={"PATH": os.environ["PATH"]},
                        )
                    if mutation == ".gitignore":
                        self.assertEqual(
                            "sentinel\n",
                            outside.read_text(encoding="utf-8"),
                        )
                    elif mutation != "empty":
                        self.assertEqual([], list(outside.iterdir()))
                finally:
                    shutil.rmtree(exp_dir)
                    exp_dir.parent.rmdir()

    def test_exit_zero_without_manifest_fails(self) -> None:
        handle = self.submit()
        state = runtime.supervise_pilot(
            handle,
            state_root=self.state_root,
            interval=0.001,
            command=[sys.executable, "-c", "pass"],
        )
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["process_exit_code"], 0)
        self.assertIn("manifest missing", state["failure_reason"])

    def test_exit_zero_and_manifest_zero_succeeds(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        state = runtime.supervise_pilot(
            handle,
            state_root=self.state_root,
            interval=0.001,
            command=[sys.executable, "-c", "pass"],
        )
        self.assertEqual(state["state"], "succeeded")
        self.assertEqual(state["runner_final_status"], 0)
        self.assertIsNotNone(state["heartbeat_at"])
        self.assertIsInstance(state["pilot_pid"], int)

    def test_default_pilot_command_uses_allocated_exp_name(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        captured: dict[str, object] = {}

        def fake_run(root, job, command, **kwargs):
            captured["command"] = list(command)
            return 0, 4321

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            state = runtime.supervise_pilot(
                handle,
                state_root=self.state_root,
            )

        parsed = protocol.parse_handle(handle)
        self.assertEqual(state["state"], "succeeded")
        self.assertEqual(
            captured["command"],
            [
                os.fspath(runtime.PILOT_SCRIPT),
                "airplane",
                self.group,
                parsed["exp"],
            ],
        )

    def test_heartbeat_updates_while_child_is_running(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        with mock.patch.object(runtime, "heartbeat", wraps=runtime.heartbeat) as beat:
            state = runtime.supervise_pilot(
                handle,
                state_root=self.state_root,
                interval=0.005,
                command=[sys.executable, "-c", "import time; time.sleep(0.04)"],
            )
        self.assertEqual(state["state"], "succeeded")
        self.assertGreaterEqual(beat.call_count, 2)

    def test_supervisor_error_terminates_started_pilot_process_group(self) -> None:
        handle = self.submit()
        pid_path = self.workspace / "pilot.pid"
        calls = 0

        def fail_heartbeat(*args, **kwargs) -> None:
            nonlocal calls
            calls += 1
            if calls < 2:
                return
            deadline = time.monotonic() + 1
            while not pid_path.exists() and time.monotonic() < deadline:
                time.sleep(0.005)
            raise OSError("state storage unavailable")

        command = [
            sys.executable,
            "-c",
            (
                "import os,time; "
                f"open({os.fspath(pid_path)!r}, 'w').write(str(os.getpid())); "
                "time.sleep(2)"
            ),
        ]
        with mock.patch.object(runtime, "heartbeat", side_effect=fail_heartbeat):
            state = runtime.supervise_pilot(
                handle,
                state_root=self.state_root,
                interval=0.001,
                command=command,
            )

        self.assertEqual(state["state"], "failed")
        self.assertIn("supervisor error", state["failure_reason"])
        pilot_pid = int(pid_path.read_text(encoding="utf-8"))
        with self.assertRaises(ProcessLookupError):
            os.kill(pilot_pid, 0)

    def test_process_group_cleanup_kills_descendants_after_leader_exits(self) -> None:
        process = mock.Mock()
        process.pid = 4321
        process.poll.return_value = 0
        process.wait.return_value = 0

        with (
            mock.patch.object(runtime.os, "killpg") as killpg,
            mock.patch.object(runtime.time, "monotonic", side_effect=(10.0, 10.0)),
            mock.patch.object(runtime.time, "sleep") as sleep,
        ):
            runtime._terminate_process_group(process, grace=0.01)

        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.01)
        process.wait.assert_not_called()
        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(4321, runtime.signal.SIGTERM),
                mock.call(4321, 0),
                mock.call(4321, 0),
                mock.call(4321, runtime.signal.SIGKILL),
            ],
        )

    def test_process_group_cleanup_reaps_real_descendant_after_leader_exit(
        self,
    ) -> None:
        pid_path = self.workspace / "descendant.pid"
        child_code = (
            "import os,time; "
            f"open({os.fspath(pid_path)!r}, 'w').write(str(os.getpid())); "
            "time.sleep(30)"
        )
        leader_code = (
            "import subprocess,sys; "
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}])"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", leader_code],
            start_new_session=True,
        )
        process.wait(timeout=2)
        deadline = time.monotonic() + 1
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(pid_path.exists())

        try:
            runtime._terminate_process_group(process, grace=0.01)
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.005)
            else:
                self.fail("descendant process group survived supervisor cleanup")
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_nonzero_process_or_manifest_fails(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        state = runtime.supervise_pilot(
            handle,
            state_root=self.state_root,
            interval=0.001,
            command=[sys.executable, "-c", "raise SystemExit(7)"],
        )
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["process_exit_code"], 7)

        handle = runtime.submit_pilot(
            "chair",
            self.group,
            state_root=self.state_root,
            detach=lambda *args: 1,
        )["job"]
        self.write_manifest(handle, 4)
        state = runtime.supervise_pilot(
            handle,
            state_root=self.state_root,
            interval=0.001,
            command=[sys.executable, "-c", "pass"],
        )
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["runner_final_status"], 4)

    def test_invalid_manifest_status_types_fail_closed(self) -> None:
        for index, final_status in enumerate((True, "0", None)):
            with self.subTest(final_status=final_status):
                handle = runtime.submit_pilot(
                    f"chair-{index}",
                    self.group,
                    state_root=self.state_root,
                    detach=lambda *args: 1,
                )["job"]
                self.write_manifest(handle, final_status)
                state = runtime.supervise_pilot(
                    handle,
                    state_root=self.state_root,
                    interval=0.001,
                    command=[sys.executable, "-c", "pass"],
                )
                self.assertEqual(state["state"], "failed")
                self.assertIsNone(state["runner_final_status"])
                self.assertIn("not an integer", state["failure_reason"])

    def test_wait_reads_observation_only_when_returning(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        calls = 0

        def sleeper(_seconds: float) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                runtime.supervise_pilot(
                    handle,
                    state_root=self.state_root,
                    interval=0.001,
                    command=[sys.executable, "-c", "pass"],
                )

        with mock.patch.object(runtime, "_observe_pilot", return_value={"tap": {"availability": "ready"}}) as observe:
            result, code = runtime.wait_job(
                handle,
                state_root=self.state_root,
                timeout=10,
                poll_interval=0,
                sleeper=sleeper,
            )
        self.assertEqual(code, 0)
        self.assertEqual(result["state"], "succeeded")
        observe.assert_called_once()

    def test_wait_stale_and_timeout_do_not_mutate_state(self) -> None:
        handle = self.submit()
        state = protocol.load_state(self.state_root, handle)
        state["heartbeat_at"] = "2000-01-01T00:00:00+00:00"
        protocol.publish_state(self.state_root, state)
        with mock.patch.object(runtime, "_observe_pilot", return_value={"tap": {"availability": "pending"}}):
            result, code = runtime.wait_job(
                handle,
                state_root=self.state_root,
                until="terminal-or-stale",
                stale_after=1,
                timeout=10,
            )
        self.assertEqual(code, 3)
        self.assertEqual(result["health"], "stale")
        self.assertEqual(protocol.load_state(self.state_root, handle)["state"], "submitted")

        ticks = iter((0.0, 2.0, 2.0))
        with mock.patch.object(runtime, "_observe_pilot", return_value={"tap": {"availability": "pending"}}):
            result, code = runtime.wait_job(
                handle,
                state_root=self.state_root,
                timeout=1,
                poll_interval=0,
                clock=lambda: next(ticks),
                sleeper=lambda _: None,
            )
        self.assertEqual(code, 4)
        self.assertEqual(result["wait"], "timeout")
        self.assertEqual(protocol.load_state(self.state_root, handle)["state"], "submitted")

    def test_tap_observer_exception_does_not_change_terminal_result(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        runtime.supervise_pilot(
            handle,
            state_root=self.state_root,
            interval=0.001,
            command=[sys.executable, "-c", "pass"],
        )
        with mock.patch.object(runtime.tap_observer, "observe_exp", side_effect=RuntimeError("bad tap")):
            result = runtime.status_job(handle, state_root=self.state_root)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["observation"]["tap"]["availability"], "degraded")

    def test_concurrent_supervisor_start_is_rejected_by_lock(self) -> None:
        handle = self.submit()
        self.write_manifest(handle, 0)
        started = threading.Event()
        release = threading.Event()
        first_result: dict[str, object] = {}

        def fake_run(*args, **kwargs):
            started.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test did not release first supervisor")
            return 0, 4321

        def run_first() -> None:
            first_result.update(
                runtime.supervise_pilot(
                    handle,
                    state_root=self.state_root,
                    command=[sys.executable, "-c", "pass"],
                )
            )

        with mock.patch.object(runtime, "_run_with_heartbeat", side_effect=fake_run):
            thread = threading.Thread(target=run_first)
            thread.start()
            self.assertTrue(started.wait(timeout=1))
            try:
                with self.assertRaisesRegex(
                    protocol.ProtocolError, "supervisor already running"
                ):
                    runtime.supervise_pilot(
                        handle,
                        state_root=self.state_root,
                        command=[sys.executable, "-c", "pass"],
                    )
            finally:
                release.set()
                thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(first_result["state"], "succeeded")

    def test_cli_rejects_missing_job_with_compact_json(self) -> None:
        env = {**os.environ, "CVM_JOB_STATE_ROOT": os.fspath(self.state_root)}
        result = subprocess.run(
            [sys.executable, "-m", "scripts.pilot.cvm_job", "status", "group/missing"],
            cwd=Path(__file__).resolve().parents[3],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])

    def test_cli_status_and_wait_preserve_terminal_exit_codes(self) -> None:
        env = {**os.environ, "CVM_JOB_STATE_ROOT": os.fspath(self.state_root)}
        cwd = Path(__file__).resolve().parents[3]
        handles = []
        for object_name, terminal in (("airplane", "succeeded"), ("chair", "failed")):
            handle = runtime.submit_pilot(
                object_name,
                self.group,
                state_root=self.state_root,
                detach=lambda *args: 1,
            )["job"]
            protocol.transition(self.state_root, handle, "running")
            protocol.transition(self.state_root, handle, terminal)
            handles.append(handle)

        status = subprocess.run(
            [sys.executable, "-m", "scripts.pilot.cvm_job", "status", handles[0]],
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        succeeded = subprocess.run(
            [sys.executable, "-m", "scripts.pilot.cvm_job", "wait", handles[0]],
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        failed = subprocess.run(
            [sys.executable, "-m", "scripts.pilot.cvm_job", "wait", handles[1]],
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(succeeded.returncode, 0, succeeded.stderr)
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertEqual(json.loads(succeeded.stdout)["state"], "succeeded")
        failed_payload = json.loads(failed.stdout)
        self.assertEqual(failed_payload["state"], "failed")
        self.assertIn("process_exit_code", failed_payload)

    def test_submit_and_monitor_are_single_ssh_wrappers(self) -> None:
        submit = SUBMIT_SCRIPT.read_text(encoding="utf-8")
        monitor = MONITOR_SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(submit.count("exec ssh"), 1)
        self.assertEqual(monitor.count("exec ssh"), 1)
        self.assertIn("ServerAliveInterval=30", monitor)
        self.assertIn("ServerAliveCountMax=6", monitor)
        self.assertIn("scripts.pilot.cvm_job status", monitor)
        self.assertIn("scripts.pilot.cvm_job wait", monitor)
        for forbidden in (" ps ", " stat ", " find ", "git log"):
            self.assertNotIn(forbidden, monitor)

    def test_monitor_contract_documents_bounded_bootstrap_diagnostic(self) -> None:
        contract = MONITOR_SKILL.read_text(encoding="utf-8")

        self.assertIn("cvm.provider-free-bootstrap-diagnostic/1", contract)
        self.assertIn("before-experiment", contract)
        self.assertIn("before-artifact-manifest", contract)
        for classification in (
            "runner-execution-profile-rejected",
            "runner-environment-allowlist-rejected",
            "runner-stripped-name-receipt-rejected",
            "runner-request-digest-rejected",
            "runner-bwrap-path-rejected",
            "runner-runtime-identity-rejected",
            "runner-output-path-rejected",
            "runner-contract-rejected",
        ):
            self.assertIn(classification, contract)
        self.assertIn("4 KiB", contract)
        self.assertIn("does not publish raw log text", contract)

    def test_submit_and_monitor_forward_one_approved_remote_cli_call(self) -> None:
        fake_bin = self.workspace / "bin"
        fake_bin.mkdir()
        command_log = self.workspace / "commands.log"
        self.write_executable(
            fake_bin / "ssh",
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$CVM_WRAPPER_LOG"
printf '%s\\n' '{"job":"group/exp","state":"submitted"}'
""",
        )
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CVM_WRAPPER_LOG": os.fspath(command_log),
        }
        submitted = subprocess.run(
            [
                os.fspath(SUBMIT_SCRIPT),
                "pilot",
                "airplane",
                "20260805-170000-audit",
            ],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        monitored = subprocess.run(
            [os.fspath(MONITOR_SCRIPT), "--wait", "--timeout", "3", "group/exp"],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        self.assertEqual(monitored.returncode, 0, monitored.stderr)
        self.assertEqual(len(submitted.stdout.splitlines()), 1)
        commands = command_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(commands), 2)
        self.assertIn("scripts.pilot.cvm_job submit-pilot", commands[0])
        self.assertIn("20260805-170000-audit", commands[0])
        self.assertIn("ServerAliveInterval=30", commands[1])
        self.assertIn("scripts.pilot.cvm_job wait", commands[1])

    def test_provider_free_submit_forwards_only_the_allowlisted_scenario(self) -> None:
        fake_bin = self.workspace / "bin"
        fake_bin.mkdir()
        command_log = self.workspace / "provider-free-commands.log"
        self.write_executable(
            fake_bin / "ssh",
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$CVM_WRAPPER_LOG"
printf '%s\\n' '{"job":"group/exp","state":"submitted"}'
""",
        )
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CVM_WRAPPER_LOG": os.fspath(command_log),
        }

        submitted = subprocess.run(
            [
                os.fspath(SUBMIT_SCRIPT),
                "provider-free",
                "issue15-runtime-authority",
                "20260811-210000-issue15-provider-free",
            ],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(submitted.returncode, 0, submitted.stderr)
        commands = command_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(commands), 1)
        self.assertIn(
            "scripts.pilot.cvm_job submit-provider-free "
            "'issue15-runtime-authority' "
            "'20260811-210000-issue15-provider-free'",
            commands[0],
        )

    def test_provider_free_submit_rejects_commands_paths_and_unsafe_groups_before_ssh(self) -> None:
        fake_bin = self.workspace / "bin"
        fake_bin.mkdir()
        marker = self.workspace / "provider-free-ssh-called"
        self.write_executable(
            fake_bin / "ssh",
            """#!/usr/bin/env bash
set -euo pipefail
touch "$CVM_SSH_MARKER"
exit 99
""",
        )
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CVM_SSH_MARKER": os.fspath(marker),
        }
        invalid = (
            ("provider-free", "../../bin/sh", "20260811-210000-safe"),
            ("provider-free", "echo${IFS}owned", "20260811-210000-safe"),
            ("provider-free", "issue15-runtime-authority", "../unsafe"),
            (
                "provider-free",
                "issue15-runtime-authority",
                "20260811-210000-safe",
                "extra-command",
            ),
        )
        for argv in invalid:
            result = subprocess.run(
                [os.fspath(SUBMIT_SCRIPT), *argv],
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 2, argv)
        self.assertFalse(marker.exists())

    def test_submit_and_monitor_reject_batch_without_ssh(self) -> None:
        fake_bin = self.workspace / "bin"
        fake_bin.mkdir()
        marker = self.workspace / "ssh-called"
        self.write_executable(
            fake_bin / "ssh",
            """#!/usr/bin/env bash
set -euo pipefail
touch "$CVM_SSH_MARKER"
exit 99
""",
        )
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CVM_SSH_MARKER": os.fspath(marker),
        }
        submitted = subprocess.run(
            [os.fspath(SUBMIT_SCRIPT), "batch", "group", "airplane"],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        monitored = subprocess.run(
            [os.fspath(MONITOR_SCRIPT), "--once", "batch/group"],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(submitted.returncode, 2)
        self.assertEqual(monitored.returncode, 2)
        self.assertFalse(marker.exists())

    def test_submit_rejects_noncanonical_group_without_ssh(self) -> None:
        fake_bin = self.workspace / "bin"
        fake_bin.mkdir()
        marker = self.workspace / "ssh-called"
        self.write_executable(
            fake_bin / "ssh",
            """#!/usr/bin/env bash
set -euo pipefail
touch "$CVM_SSH_MARKER"
exit 99
""",
        )
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CVM_SSH_MARKER": os.fspath(marker),
        }
        submitted = subprocess.run(
            [os.fspath(SUBMIT_SCRIPT), "pilot", "airplane", "group"],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(submitted.returncode, 2)
        self.assertFalse(marker.exists())

    def test_legacy_batch_entrypoint_still_calls_toys4k_pilot(self) -> None:
        repo = self.workspace / "batch-repo"
        script_dir = repo / "scripts" / "pilot"
        script_dir.mkdir(parents=True)
        batch_script = script_dir / "toys4k-batch.sh"
        shutil.copy2(PILOT_ROOT / "toys4k-batch.sh", batch_script)
        batch_script.chmod(0o755)
        command_log = self.workspace / "batch-commands.log"
        self.write_executable(
            script_dir / "toys4k-pilot.sh",
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$CVM_BATCH_TEST_LOG"
""",
        )
        secrets = self.workspace / "secrets.env"
        secrets.write_text("VENUS_TOKENS=(secret-one)\n", encoding="utf-8")
        env = {
            **os.environ,
            "TEXT_TO_CAD_SECRETS": os.fspath(secrets),
            "CVM_BATCH_TEST_LOG": os.fspath(command_log),
        }
        result = subprocess.run(
            [os.fspath(batch_script), "legacy", "airplane", "chair"],
            cwd=repo,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = command_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(commands), 2)
        self.assertEqual({line.split()[0] for line in commands}, {"airplane", "chair"})
        self.assertNotIn("secret-one", command_log.read_text(encoding="utf-8"))

    @staticmethod
    def write_executable(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
