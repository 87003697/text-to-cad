from __future__ import annotations

import json
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

from scripts.pilot.cvm_job import protocol, runtime
from tests.python.support.paths import REPO_ROOT
from tests.python.support.tmp_root import temporary_directory


PILOT_ROOT = REPO_ROOT / "scripts" / "pilot"
SUBMIT_SCRIPT = PILOT_ROOT / "cvm-submit.sh"
MONITOR_SCRIPT = PILOT_ROOT / "cvm-monitor.sh"


class CvmJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = temporary_directory(prefix="cvm-job-")
        self.root_text = self.temporary.__enter__()
        self.workspace = Path(self.root_text)
        self.state_root = self.workspace / ".cvm-jobs"
        self.repo_root = self.workspace / "repo"
        self.repo_root.mkdir()
        (self.repo_root / "outputs").mkdir()
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

    def test_submit_records_nonsecret_token_slot(self) -> None:
        with mock.patch.dict(os.environ, {"VENUS_TOKEN_SLOT": "3"}):
            handle = self.submit()
        state = protocol.load_state(self.state_root, handle)
        self.assertEqual(state["token_slot"], 3)
        self.assertEqual(protocol.public_state(state, 60)["token_slot"], 3)

    def test_submit_rejects_invalid_token_slot(self) -> None:
        with (
            mock.patch.dict(os.environ, {"VENUS_TOKEN_SLOT": "50"}),
            self.assertRaisesRegex(protocol.ProtocolError, "in \\[0, 49\\]"),
        ):
            self.submit()

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

    def test_submit_token_slot_selects_remote_pool_entry_without_exposing_it(self) -> None:
        fake_bin = self.workspace / "token-bin"
        fake_bin.mkdir()
        command_log = self.workspace / "token-commands.log"
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
        result = subprocess.run(
            [
                os.fspath(SUBMIT_SCRIPT),
                "pilot",
                "airplane",
                "20260805-170000-audit",
                "--token-slot",
                "1",
            ],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        command = command_log.read_text(encoding="utf-8")
        self.assertIn("VENUS_TOKENS[1]", command)
        self.assertIn("VENUS_TOKEN_SLOT='1'", command)
        self.assertNotIn("secret-", command)

    def test_submit_rejects_noncanonical_token_slot_without_ssh(self) -> None:
        fake_bin = self.workspace / "invalid-slot-bin"
        fake_bin.mkdir()
        marker = self.workspace / "invalid-slot-ssh-called"
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
        for slot in ("010", "50", "18446744073709551616"):
            result = subprocess.run(
                [
                    os.fspath(SUBMIT_SCRIPT),
                    "pilot",
                    "airplane",
                    "20260805-170000-audit",
                    "--token-slot",
                    slot,
                ],
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 2)
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
