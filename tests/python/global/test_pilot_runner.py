from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import signal
import socket
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
PILOT_ROOT = REPO_ROOT / "scripts" / "pilot"
UTILS_ROOT = REPO_ROOT / "scripts" / "utils"


def nested_gate_proof(
    *,
    job_id: str = "pilot-test-job",
    nonce: str = "a" * 32,
    artifact_sha256: str = "b" * 64,
    surface_manifest_sha256: str = "c" * 64,
) -> dict[str, object]:
    """Return the fixed successful proof accepted before Agent exec."""

    return {
        "schema": "meshshot.browser-sidecar.nested-gate-proof/1",
        "status": "succeeded",
        "jobId": job_id,
        "nonce": nonce,
        "artifactSha256": artifact_sha256,
        "surfaceManifestSha256": surface_manifest_sha256,
        "predicates": {
            "publicResidualParity": True,
            "viewerProjectionChanged": True,
            "viewerArtifactClean": True,
            "browserInventoryEmpty": True,
            "browserProcessZero": True,
        },
        "residual": {
            "pngSha256": "b498c55c68662989a3a95c4925432d61f979183d30c2cdf593e154c7b0ca9d5b",
            "mode": "RGB",
            "size": [504, 1008],
            "profileSha256": "87da3cc3f625cb9c24f51bed41dcdc70402a4d461b2af29eaa19846b1e8f7241",
            "views": ["+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso"],
        },
        "viewer": {
            "before": "Display and projection: Solid, Orthographic",
            "after": "Display and projection: Solid, Perspective",
            "bodyMentionsFixture": True,
            "bodyHasArtifactError": False,
        },
        "inventory": {
            "browserExecutables": [],
            "browserPackages": [],
            "browserCaches": [],
            "browserProcesses": [],
        },
    }


def load_runner():
    """Load the hyphenated executable as a module for focused unit tests."""

    path = PILOT_ROOT / "runner.py"
    spec = importlib.util.spec_from_file_location("pilot_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeProcess:
    """Small controllable Popen stand-in for lifecycle tests."""

    def __init__(self, returncode: int | None = None) -> None:
        self.pid = 4321
        self.returncode = returncode
        self.signals: list[int] = []
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def send_signal(self, signum: int) -> None:
        self.signals.append(signum)
        self.returncode = 0

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -signal.SIGKILL

    def wait(self, timeout=None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class EscalatingProcess(FakeProcess):
    """Timeout through SIGINT and SIGTERM, then exit after SIGKILL."""

    def __init__(self) -> None:
        super().__init__()
        self.wait_calls = 0

    def send_signal(self, signum: int) -> None:
        self.signals.append(signum)

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None) -> int:
        self.wait_calls += 1
        if self.wait_calls < 3:
            raise subprocess.TimeoutExpired("claude-tap", timeout)
        return self.returncode or 0


class FakeRetryProxy:
    """Controllable in-process retry proxy for runner lifecycle tests."""

    def __init__(self) -> None:
        self.url = "http://127.0.0.1:17777/v1"
        self.started = False
        self.stopped = False

    def start(self):
        """Record lifecycle start and return this proxy."""

        self.started = True
        return self

    def stop(self) -> None:
        """Record lifecycle stop."""

        self.stopped = True


class RunnerTests(unittest.TestCase):
    """Validate mandatory tap behavior without bwrap, network, or Venus."""

    def test_handoff_gate_digest_matches_deterministic_artifact(self) -> None:
        """The formal handoff must publish the current sealed Gate identity."""

        handoff = (
            REPO_ROOT / "docs/specs/browser-sidecar-formal-pilot-handoff.md"
        ).read_text(encoding="utf-8")
        match = re.search(
            r"Deterministic sealed Browser Gate zipapp for the current scanner source:\n"
            r"\s+`sha256:([0-9a-f]{64})`",
            handoff,
        )
        self.assertIsNotNone(match)
        with tempfile.TemporaryDirectory() as temporary:
            actual = self.supervisor._build_gate_artifact(
                REPO_ROOT, Path(temporary) / "browser-gate.pyz"
            )
        self.assertEqual(match.group(1), actual)

    @classmethod
    def setUpClass(cls) -> None:
        cls.supervisor = load_runner()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.exp_dir = Path(self.temp.name)
        self.environ = {
            "PATH": os.environ.get("PATH", ""),
            "TAP_READY_TIMEOUT": "0.1",
            "TAP_STOP_TIMEOUT": "0.1",
            "VENUS_TOKEN": "test-token",
            "UNRELATED_SECRET": "must-not-cross-sandbox",
        }

    def test_production_prompt_and_workload_have_no_monitor_control_channel(self) -> None:
        pilot_script = (PILOT_ROOT / "toys4k-pilot.sh").read_text(encoding="utf-8")
        self.assertNotIn("pilot-feedback", pilot_script)
        self.assertNotIn("CVM_JOB", pilot_script)
        prompt = pilot_script[pilot_script.index("PROMPT=$(cat") : pilot_script.index("echo \"[pilot]")]
        self.assertNotIn("monitor", prompt.lower())
        workload = pilot_script[pilot_script.index("WORKLOAD=(") : pilot_script.index("PILOT_EXIT=0")]
        self.assertNotIn("cvm", workload.lower())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_missing_tap_fails_without_installing(self) -> None:
        environ = {"PATH": "/fake/bin"}
        with (
            mock.patch.object(self.supervisor.shutil, "which", return_value=None),
            mock.patch.object(self.supervisor.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(self.supervisor.TapError, "required"):
                self.supervisor.resolve_tap(environ)
        run.assert_not_called()

    def test_tap_version_must_match_pinned_version(self) -> None:
        result = SimpleNamespace(stdout="claude-tap 0.1.141\n")
        with (
            mock.patch.object(
                self.supervisor.shutil,
                "which",
                return_value="/fake/claude-tap",
            ),
            mock.patch.object(
                self.supervisor.subprocess,
                "run",
                return_value=result,
            ),
        ):
            with self.assertRaisesRegex(self.supervisor.TapError, "0.1.141"):
                self.supervisor.resolve_tap({"PATH": "/fake"})

    def test_pinned_tap_version_is_accepted(self) -> None:
        result = SimpleNamespace(stdout="claude-tap 0.1.140\n")
        with (
            mock.patch.object(
                self.supervisor.shutil,
                "which",
                return_value="/fake/claude-tap",
            ),
            mock.patch.object(
                self.supervisor.subprocess,
                "run",
                return_value=result,
            ) as run,
        ):
            resolved = self.supervisor.resolve_tap({"PATH": "/fake"})
        self.assertEqual(resolved, "/fake/claude-tap")
        self.assertEqual(run.call_args.args[0], ["/fake/claude-tap", "--version"])

    def test_timeout_is_validated_before_child_start(self) -> None:
        with (
            mock.patch.object(
                self.supervisor,
                "resolve_tap",
                return_value="/fake/claude-tap",
            ),
            mock.patch.object(self.supervisor, "start_tap") as start,
        ):
            with self.assertRaises(self.supervisor.TapError):
                self.supervisor.run_supervised(
                    self.exp_dir,
                    [],
                    ["/fake/workload"],
                    {**self.environ, "TAP_STOP_TIMEOUT": "nan"},
                )
        start.assert_not_called()

    def test_start_tap_is_loopback_only_and_uses_per_exp_db(self) -> None:
        process = FakeProcess()
        environ = {**self.environ, "CLAUDE_TAP_TARGET": "http://attacker.invalid"}
        with mock.patch.object(
            self.supervisor.subprocess,
            "Popen",
            return_value=process,
        ) as popen:
            returned = self.supervisor.start_tap(
                "/fake/claude-tap",
                self.exp_dir,
                environ,
                "http://127.0.0.1:17777/v1",
            )
        self.assertIs(returned, process)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[argv.index("--tap-host") + 1], "127.0.0.1")
        self.assertEqual(argv[argv.index("--tap-port") + 1], "0")
        self.assertEqual(
            argv[argv.index("--tap-target") + 1],
            "http://127.0.0.1:17777/v1",
        )
        self.assertIn("--tap-no-launch", argv)
        self.assertEqual(
            popen.call_args.kwargs["env"]["CLOUDTAP_DB"],
            str(self.exp_dir / "run/traces.sqlite3"),
        )
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_ready_port_comes_from_this_process_log(self) -> None:
        log_path = self.exp_dir / "run/.claude-tap.log"
        log_path.parent.mkdir()
        log_path.write_text(
            "claude-tap listening on http://127.0.0.1:18888\n",
            encoding="utf-8",
        )
        port = self.supervisor.wait_ready(
            FakeProcess(),
            log_path,
            0.1,
            lambda: False,
        )
        self.assertEqual(port, 18888)

    def test_ready_detects_cancel_and_early_exit(self) -> None:
        log_path = self.exp_dir / "run/.claude-tap.log"
        log_path.parent.mkdir()
        log_path.write_text("", encoding="utf-8")
        self.assertIsNone(
            self.supervisor.wait_ready(
                FakeProcess(),
                log_path,
                0.1,
                lambda: True,
            )
        )
        with self.assertRaisesRegex(self.supervisor.TapError, "status=7"):
            self.supervisor.wait_ready(
                FakeProcess(returncode=7),
                log_path,
                0.1,
                lambda: False,
            )

    def test_tap_exit_during_workload_fails_closed(self) -> None:
        workload = FakeProcess()
        tap = FakeProcess(returncode=7)
        with mock.patch.object(
            self.supervisor,
            "signal_process_group",
        ) as signal_group:
            status, tap_failed = self.supervisor.wait_workload(workload, tap)
        self.assertEqual(status, 1)
        self.assertTrue(tap_failed)
        signal_group.assert_called_once_with(workload, signal.SIGTERM)

    def test_stop_escalates_sigint_then_term_then_kill(self) -> None:
        process = EscalatingProcess()
        self.supervisor.stop_tap(process, 0.1)
        self.assertEqual(process.signals, [signal.SIGINT])
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)

    def test_signal_attach_replays_launch_window_signal(self) -> None:
        relay = self.supervisor.SignalRelay()
        relay.signum = signal.SIGTERM
        workload = FakeProcess()
        with mock.patch.object(
            self.supervisor,
            "signal_process_group",
        ) as signal_group:
            relay.attach(workload)
        signal_group.assert_called_once_with(workload, signal.SIGTERM)

    def test_finalized_trace_is_read_and_html_is_published_atomically(self) -> None:
        db_path = self.exp_dir / "run/traces.sqlite3"
        db_path.parent.mkdir()
        with closing(sqlite3.connect(db_path)) as connection:
            connection.execute(
                "CREATE TABLE sessions "
                "(id TEXT, started_at TEXT, status TEXT, record_count INTEGER)"
            )
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                ("session-1", "2026-07-30T12:00:00Z", "complete", 3),
            )
            connection.commit()
        self.assertEqual(
            self.supervisor.read_trace(self.exp_dir),
            ("session-1", "complete", 3),
        )

        def fake_export(argv, **kwargs):
            output = Path(argv[argv.index("--output") + 1])
            output.write_text("<html>trace</html>", encoding="utf-8")
            return SimpleNamespace(returncode=0)

        with mock.patch.object(
            self.supervisor.subprocess,
            "run",
            side_effect=fake_export,
        ):
            self.supervisor.export_html(
                "/fake/claude-tap",
                self.exp_dir,
                "session-1",
                self.environ,
            )
        self.assertEqual(
            (self.exp_dir / "run/trace.html").read_text(encoding="utf-8"),
            "<html>trace</html>",
        )
        self.assertFalse((self.exp_dir / "run/.claude-tap.log.export").exists())

    def test_missing_and_active_trace_are_not_valid(self) -> None:
        with self.assertRaisesRegex(self.supervisor.TapError, "missing"):
            self.supervisor.read_trace(self.exp_dir)

        db_path = self.exp_dir / "run/traces.sqlite3"
        db_path.parent.mkdir()
        with closing(sqlite3.connect(db_path)) as connection:
            connection.execute(
                "CREATE TABLE sessions "
                "(id TEXT, started_at TEXT, status TEXT, record_count INTEGER)"
            )
            connection.execute(
                "INSERT INTO sessions VALUES "
                "('session-1', '2026-07-30', 'active', 1)"
            )
            connection.commit()
        self.assertEqual(
            self.supervisor.read_trace(self.exp_dir),
            ("session-1", "active", 1),
        )

    def test_failed_export_preserves_sqlite_and_does_not_publish_html(self) -> None:
        db_path = self.exp_dir / "run/traces.sqlite3"
        db_path.parent.mkdir()
        db_path.write_bytes(b"sqlite")
        with mock.patch.object(
            self.supervisor.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=2),
        ):
            self.supervisor.export_html(
                "/fake/claude-tap",
                self.exp_dir,
                "session-1",
                self.environ,
            )
        self.assertTrue(db_path.is_file())
        self.assertFalse((self.exp_dir / "run/trace.html").exists())
        self.assertTrue((self.exp_dir / "run/.claude-tap.log.export").is_file())

    def test_success_injects_only_loopback_tap_url_and_requires_trace(self) -> None:
        tap = FakeProcess()
        workload = FakeProcess(returncode=0)
        retry_proxy = FakeRetryProxy()
        with (
            mock.patch.object(
                self.supervisor,
                "resolve_tap",
                return_value="/fake/claude-tap",
            ),
            mock.patch.object(
                self.supervisor,
                "RetryProxy",
                create=True,
                return_value=retry_proxy,
            ) as retry_proxy_type,
            mock.patch.object(
                self.supervisor,
                "start_tap",
                return_value=tap,
            ) as start_tap,
            mock.patch.object(self.supervisor, "wait_ready", return_value=18888),
            mock.patch.object(
                self.supervisor,
                "build_bwrap_argv",
                return_value=["/fake/bwrap", "--", "/fake/codex"],
            ),
            mock.patch.object(
                self.supervisor.subprocess,
                "Popen",
                return_value=workload,
            ) as popen,
            mock.patch.object(
                self.supervisor,
                "wait_workload",
                return_value=(0, False),
            ),
            mock.patch.object(self.supervisor, "stop_tap"),
            mock.patch.object(
                self.supervisor,
                "read_trace",
                return_value=("session-1", "complete", 1),
            ),
            mock.patch.object(self.supervisor, "export_html"),
        ):
            status = self.supervisor.run_supervised(
                self.exp_dir,
                [],
                ["/fake/bwrap", "--", "/fake/codex"],
                self.environ,
            )
        self.assertEqual(status, 0)
        retry_proxy_type.assert_called_once_with(
            self.supervisor.TAP_TARGET,
            self.exp_dir / "run/venus-retry.jsonl",
        )
        self.assertTrue(retry_proxy.started)
        self.assertTrue(retry_proxy.stopped)
        self.assertEqual(start_tap.call_args.args[-1], retry_proxy.url)
        self.assertEqual(
            popen.call_args.kwargs["env"]["CLAUDE_TAP_URL"],
            "http://127.0.0.1:18888/v1",
        )
        self.assertEqual(popen.call_args.kwargs["env"]["HOME"], "/home/pilot")
        self.assertEqual(
            popen.call_args.kwargs["env"]["CODEX_HOME"],
            "/home/pilot/.codex",
        )
        self.assertNotIn("UNRELATED_SECRET", popen.call_args.kwargs["env"])

    def test_successful_workload_without_captured_request_fails(self) -> None:
        tap = FakeProcess()
        workload = FakeProcess(returncode=0)
        with (
            mock.patch.object(
                self.supervisor,
                "resolve_tap",
                return_value="/fake/claude-tap",
            ),
            mock.patch.object(
                self.supervisor,
                "RetryProxy",
                return_value=FakeRetryProxy(),
            ),
            mock.patch.object(self.supervisor, "start_tap", return_value=tap),
            mock.patch.object(self.supervisor, "wait_ready", return_value=18888),
            mock.patch.object(
                self.supervisor,
                "build_bwrap_argv",
                return_value=["/fake/bwrap", "--", "/fake/codex"],
            ),
            mock.patch.object(
                self.supervisor.subprocess,
                "Popen",
                return_value=workload,
            ),
            mock.patch.object(
                self.supervisor,
                "wait_workload",
                return_value=(0, False),
            ),
            mock.patch.object(self.supervisor, "stop_tap"),
            mock.patch.object(
                self.supervisor,
                "read_trace",
                return_value=("session-1", "empty", 0),
            ),
            mock.patch.object(self.supervisor, "export_html"),
        ):
            status = self.supervisor.run_supervised(
                self.exp_dir,
                [],
                ["/fake/bwrap", "--", "/fake/codex"],
                self.environ,
            )
        self.assertEqual(status, 1)

    def test_nonzero_workload_status_is_preserved_with_finalized_trace(self) -> None:
        tap = FakeProcess()
        workload = FakeProcess(returncode=9)
        with (
            mock.patch.object(
                self.supervisor,
                "resolve_tap",
                return_value="/fake/claude-tap",
            ),
            mock.patch.object(
                self.supervisor,
                "RetryProxy",
                return_value=FakeRetryProxy(),
            ),
            mock.patch.object(self.supervisor, "start_tap", return_value=tap),
            mock.patch.object(self.supervisor, "wait_ready", return_value=18888),
            mock.patch.object(
                self.supervisor,
                "build_bwrap_argv",
                return_value=["/fake/bwrap", "--", "/fake/codex"],
            ),
            mock.patch.object(
                self.supervisor.subprocess,
                "Popen",
                return_value=workload,
            ),
            mock.patch.object(
                self.supervisor,
                "wait_workload",
                return_value=(9, False),
            ),
            mock.patch.object(self.supervisor, "stop_tap"),
            mock.patch.object(
                self.supervisor,
                "read_trace",
                return_value=("session-1", "empty", 0),
            ),
            mock.patch.object(self.supervisor, "export_html"),
        ):
            status = self.supervisor.run_supervised(
                self.exp_dir,
                [],
                ["/fake/bwrap", "--", "/fake/codex"],
                self.environ,
            )
        self.assertEqual(status, 9)

    def test_signal_status_wins_and_workload_is_not_started(self) -> None:
        tap = FakeProcess()
        retry_proxy = FakeRetryProxy()
        relay = mock.MagicMock()
        relay.signum = signal.SIGINT
        relay.cancelled = True
        relay.__enter__ = mock.Mock(return_value=relay)
        relay.__exit__ = mock.Mock(return_value=False)
        with (
            mock.patch.object(
                self.supervisor,
                "resolve_tap",
                return_value="/fake/claude-tap",
            ),
            mock.patch.object(
                self.supervisor,
                "RetryProxy",
                return_value=retry_proxy,
            ),
            mock.patch.object(self.supervisor, "SignalRelay", return_value=relay),
            mock.patch.object(self.supervisor, "start_tap", return_value=tap),
            mock.patch.object(self.supervisor, "wait_ready", return_value=None),
            mock.patch.object(self.supervisor, "stop_tap"),
            mock.patch.object(
                self.supervisor,
                "build_bwrap_argv",
                return_value=["/fake/bwrap", "--", "/fake/workload"],
            ),
            mock.patch.object(
                self.supervisor,
                "read_trace",
                return_value=("session-1", "empty", 0),
            ),
            mock.patch.object(self.supervisor, "export_html"),
            mock.patch.object(self.supervisor.subprocess, "Popen") as popen,
        ):
            status = self.supervisor.run_supervised(
                self.exp_dir,
                [],
                ["/fake/workload"],
                self.environ,
            )
        self.assertEqual(status, 130)
        self.assertTrue(retry_proxy.stopped)
        popen.assert_not_called()

    def test_nested_gate_failure_never_executes_agent_workload(self) -> None:
        """Missing, malformed, duplicate, or late proof closes before Agent exec."""

        for classification in (
            "nested-gate-missing",
            "nested-gate-malformed",
            "nested-gate-duplicate",
            "nested-gate-timeout",
        ):
            with self.subTest(classification=classification):
                tap = FakeProcess()
                gate_process = FakeProcess()
                retry_proxy = FakeRetryProxy()
                state = self.supervisor.LifecycleState()

                class FakeSidecar:
                    capability_dir = self.exp_dir

                    def record_nested_gate(self, proof):
                        raise AssertionError("failed proof must not be recorded")

                class FakeGateChannel:
                    def __init__(self, capability_dir):
                        self.capability_dir = capability_dir

                    def receive(self, cancelled):
                        raise self_error

                    def release(self):
                        raise AssertionError("failed proof must not release Agent")

                    def close(self):
                        return None

                self_error = self.supervisor.PilotError(classification)
                with (
                    mock.patch.object(
                        self.supervisor,
                        "resolve_tap",
                        return_value="/fake/claude-tap",
                    ),
                    mock.patch.object(
                        self.supervisor,
                        "RetryProxy",
                        return_value=retry_proxy,
                    ),
                    mock.patch.object(self.supervisor, "start_tap", return_value=tap),
                    mock.patch.object(self.supervisor, "wait_ready", return_value=18888),
                    mock.patch.object(
                        self.supervisor,
                        "build_bwrap_argv",
                        return_value=["/fake/bwrap", "--", "/fixed/gate"],
                    ),
                    mock.patch.object(
                        self.supervisor,
                        "NestedGateChannel",
                        FakeGateChannel,
                        create=True,
                    ),
                    mock.patch.object(
                        self.supervisor.subprocess,
                        "Popen",
                        return_value=gate_process,
                    ),
                    mock.patch.object(self.supervisor, "signal_process_group"),
                    mock.patch.object(self.supervisor, "wait_workload") as wait_workload,
                    mock.patch.object(self.supervisor, "stop_tap"),
                    mock.patch.object(
                        self.supervisor,
                        "read_trace",
                        side_effect=self.supervisor.TapError("missing trace"),
                    ),
                ):
                    status = self.supervisor.run_supervised(
                        self.exp_dir,
                        [],
                        ["/fixed/agent"],
                        self.environ,
                        state,
                        FakeSidecar(),
                    )
                self.assertEqual(status, 1)
                self.assertFalse(state.workload_started)
                wait_workload.assert_not_called()

    def test_surface_proof_mismatch_withholds_exec_and_cleans_gate_process(self) -> None:
        """A different surface proof cannot release the Agent and is terminated."""

        tap = FakeProcess()
        gate_process = FakeProcess()
        state = self.supervisor.LifecycleState()
        events: list[str] = []

        class FakeSidecar:
            capability_dir = self.exp_dir

            def record_nested_gate(self, proof):
                events.append("reject-proof")
                raise self_error

        class FakeGateChannel:
            def __init__(self, capability_dir):
                events.append("open")

            def receive(self, cancelled):
                events.append("receive")
                return nested_gate_proof(surface_manifest_sha256="d" * 64)

            def release(self):
                raise AssertionError("mismatched surface must not release Agent")

            def close(self):
                events.append("close")

        self_error = self.supervisor.PilotError("nested-gate surface mismatch")
        with (
            mock.patch.object(self.supervisor, "resolve_tap", return_value="/fake/tap"),
            mock.patch.object(
                self.supervisor, "RetryProxy", return_value=FakeRetryProxy()
            ),
            mock.patch.object(self.supervisor, "start_tap", return_value=tap),
            mock.patch.object(self.supervisor, "wait_ready", return_value=18888),
            mock.patch.object(
                self.supervisor,
                "build_bwrap_argv",
                return_value=["/fake/bwrap", "--", "/fixed/gate", "--", "/fixed/agent"],
            ),
            mock.patch.object(
                self.supervisor, "NestedGateChannel", FakeGateChannel, create=True
            ),
            mock.patch.object(
                self.supervisor.subprocess, "Popen", return_value=gate_process
            ),
            mock.patch.object(self.supervisor, "signal_process_group") as terminate,
            mock.patch.object(self.supervisor, "wait_workload") as wait_workload,
            mock.patch.object(self.supervisor, "stop_tap"),
            mock.patch.object(
                self.supervisor,
                "read_trace",
                side_effect=self.supervisor.TapError("no Agent trace"),
            ),
        ):
            status = self.supervisor.run_supervised(
                self.exp_dir,
                [],
                ["/fixed/agent"],
                self.environ,
                state,
                FakeSidecar(),
            )

        self.assertEqual(status, 1)
        self.assertFalse(state.workload_started)
        self.assertEqual(events, ["open", "receive", "reject-proof", "close"])
        terminate.assert_called_once_with(gate_process, signal.SIGTERM)
        wait_workload.assert_not_called()

    def test_run_pilot_real_validator_rejects_surface_and_closes_job(self) -> None:
        """The production validator withholds Agent release and publishes failure."""

        runner = self.supervisor
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            repo_root = Path(temp) / "repo"
            exp_dir = repo_root / "outputs/group/exp"
            exp_dir.mkdir(parents=True)
            relative = exp_dir.relative_to(repo_root)
            job_id = "pilot-" + runner.hashlib.sha256(
                relative.as_posix().encode("utf-8")
            ).hexdigest()[:24]
            job = runner.BrowserSidecarJob.create(
                exp_dir,
                runner.SANDBOX_REPO_ROOT / relative,
                job_id=job_id,
            )
            job.configure_nested_gate(
                artifact_sha256="b" * 64,
                surface_manifest_sha256="c" * 64,
            )
            gate_process = FakeProcess()
            tap = FakeProcess()
            released: list[bool] = []

            class GateChannel:
                def __init__(self, capability_dir):
                    self.capability_dir = capability_dir

                def receive(self, cancelled):
                    return nested_gate_proof(
                        job_id=job.job_id,
                        nonce=job.gate_nonce,
                        artifact_sha256="b" * 64,
                        surface_manifest_sha256="d" * 64,
                    )

                def release(self):
                    released.append(True)

                def close(self):
                    return None

            with (
                mock.patch.object(runner, "REPO_ROOT", repo_root),
                mock.patch.object(runner, "prepare_exp"),
                mock.patch.object(runner, "prepare_nested_browser_gate"),
                mock.patch.object(
                    runner.BrowserSidecarJob,
                    "create",
                    return_value=job,
                ),
                mock.patch.object(job, "start"),
                mock.patch.object(job, "close", wraps=job.close) as close_job,
                mock.patch.object(runner, "resolve_tap", return_value="/fake/tap"),
                mock.patch.object(
                    runner, "RetryProxy", return_value=FakeRetryProxy()
                ),
                mock.patch.object(runner, "start_tap", return_value=tap),
                mock.patch.object(runner, "wait_ready", return_value=18888),
                mock.patch.object(
                    runner,
                    "build_bwrap_argv",
                    return_value=["/fake/bwrap", "--", "/fixed/gate"],
                ),
                mock.patch.object(runner, "NestedGateChannel", GateChannel),
                mock.patch.object(
                    runner.subprocess, "Popen", return_value=gate_process
                ),
                mock.patch.object(runner, "signal_process_group") as terminate,
                mock.patch.object(runner, "wait_workload") as agent_workload,
                mock.patch.object(runner, "stop_tap"),
                mock.patch.object(
                    runner,
                    "read_trace",
                    side_effect=runner.TapError("no Agent trace"),
                ),
                mock.patch.object(
                    runner,
                    "finalize_pilot",
                    side_effect=lambda exp, status, env, **kwargs: status,
                ),
            ):
                status = runner.run_pilot(
                    exp_dir,
                    [],
                    ["/fixed/agent"],
                    self.environ,
                )
            receipt = json.loads(
                (exp_dir / "run/browser-sidecar-receipt.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(status, 1)
        self.assertEqual(released, [])
        agent_workload.assert_not_called()
        terminate.assert_called_once_with(gate_process, signal.SIGTERM)
        close_job.assert_called_once_with(workload_status=1)
        self.assertEqual(receipt["status"], "failed")
        self.assertTrue(receipt["predicates"]["absenceProved"])
        self.assertEqual(receipt["failureCheck"], "sidecar-readiness")

    def test_nested_gate_channel_is_one_shot_exact_and_bounded(self) -> None:
        """The outer-owned channel rejects absent, malformed, duplicate, and late proof."""

        runner = self.supervisor

        def send(path: Path, payload: bytes, ack: list[bytes]) -> threading.Thread:
            def client() -> None:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.connect(os.fspath(path))
                    if payload:
                        connection.sendall(payload)
                    connection.shutdown(socket.SHUT_WR)
                    ack.append(connection.recv(2))

            thread = threading.Thread(target=client)
            thread.start()
            return thread

        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            channel = runner.NestedGateChannel(Path(temp), timeout=0.05)
            ack: list[bytes] = []
            thread = send(channel.path, b"", ack)
            with self.assertRaisesRegex(runner.PilotError, "missing"):
                channel.receive(lambda: False)
            channel.close()
            thread.join(timeout=1)
            self.assertEqual(ack, [b""])

        for label, wire in (
            ("malformed", b"{]\n"),
            (
                "duplicate",
                json.dumps(nested_gate_proof()).encode("ascii")
                + b"\n"
                + json.dumps(nested_gate_proof()).encode("ascii")
                + b"\n",
            ),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory(dir="/tmp") as temp:
                channel = runner.NestedGateChannel(Path(temp), timeout=0.2)
                ack = []
                thread = send(channel.path, wire, ack)
                with self.assertRaisesRegex(runner.PilotError, label):
                    channel.receive(lambda: False)
                channel.close()
                thread.join(timeout=1)
                self.assertEqual(ack, [b""])

        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            channel = runner.NestedGateChannel(Path(temp), timeout=0.01)
            with self.assertRaisesRegex(runner.PilotError, "timeout"):
                channel.receive(lambda: False)
            channel.close()

        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            channel = runner.NestedGateChannel(Path(temp), timeout=0.2)
            ack = []
            thread = send(
                channel.path,
                json.dumps(nested_gate_proof()).encode("ascii") + b"\n",
                ack,
            )
            proof = channel.receive(lambda: False)
            channel.release()
            channel.close()
            thread.join(timeout=1)
            self.assertEqual(proof, nested_gate_proof())
            self.assertEqual(ack, [b"\x01"])

    def test_successful_nested_gate_releases_then_executes_workload_once(self) -> None:
        """The same bwrap PID is released only after exact outer proof acceptance."""

        tap = FakeProcess()
        gate_process = FakeProcess(returncode=0)
        recorded: list[object] = []
        events: list[str] = []
        state = self.supervisor.LifecycleState()

        class FakeSidecar:
            capability_dir = self.exp_dir

            def record_nested_gate(self, proof):
                events.append("record")
                recorded.append(proof)

        class FakeGateChannel:
            def __init__(self, capability_dir):
                events.append("open")

            def receive(self, cancelled):
                events.append("receive")
                return nested_gate_proof()

            def release(self):
                events.append("release")

            def close(self):
                events.append("close")

        def wait_workload(*args):
            events.append("workload")
            return 0, False

        with (
            mock.patch.object(self.supervisor, "resolve_tap", return_value="/fake/tap"),
            mock.patch.object(
                self.supervisor,
                "RetryProxy",
                return_value=FakeRetryProxy(),
            ),
            mock.patch.object(self.supervisor, "start_tap", return_value=tap),
            mock.patch.object(self.supervisor, "wait_ready", return_value=18888),
            mock.patch.object(
                self.supervisor,
                "build_bwrap_argv",
                return_value=["/fake/bwrap", "--", "/fixed/gate", "--", "/fixed/agent"],
            ),
            mock.patch.object(
                self.supervisor,
                "NestedGateChannel",
                FakeGateChannel,
                create=True,
            ),
            mock.patch.object(
                self.supervisor.subprocess,
                "Popen",
                return_value=gate_process,
            ) as popen,
            mock.patch.object(
                self.supervisor,
                "wait_workload",
                side_effect=wait_workload,
            ),
            mock.patch.object(self.supervisor, "stop_tap"),
            mock.patch.object(
                self.supervisor,
                "read_trace",
                return_value=("session-1", "complete", 1),
            ),
            mock.patch.object(self.supervisor, "export_html"),
        ):
            status = self.supervisor.run_supervised(
                self.exp_dir,
                [],
                ["/fixed/agent"],
                self.environ,
                state,
                FakeSidecar(),
            )
        self.assertEqual(status, 0)
        self.assertEqual(recorded, [nested_gate_proof()])
        self.assertEqual(events, ["open", "receive", "record", "release", "workload", "close"])
        self.assertTrue(state.workload_started)
        popen.assert_called_once()

    def test_signal_during_nested_gate_preserves_143_without_agent_exec(self) -> None:
        """A signal while proof is pending withholds release and keeps signal status."""

        tap = FakeProcess()
        gate_process = FakeProcess()
        state = self.supervisor.LifecycleState()
        supervisor = self.supervisor

        class FakeRelay:
            signum = None
            child = None

            @property
            def cancelled(self):
                return self.signum is not None

            def attach(self, child):
                self.child = child

            def detach(self):
                self.child = None

        relay = FakeRelay()

        class FakeSidecar:
            capability_dir = self.exp_dir

            def record_nested_gate(self, proof):
                raise AssertionError("signalled proof must not be recorded")

        class FakeGateChannel:
            def __init__(self, capability_dir):
                self.released = False

            def receive(self, cancelled):
                relay.signum = signal.SIGTERM
                raise supervisor.PilotError("nested-gate interrupted")

            def release(self):
                self.released = True
                raise AssertionError("signalled gate must not release")

            def close(self):
                return None

        with (
            mock.patch.object(self.supervisor, "resolve_tap", return_value="/fake/tap"),
            mock.patch.object(
                self.supervisor,
                "RetryProxy",
                return_value=FakeRetryProxy(),
            ),
            mock.patch.object(self.supervisor, "start_tap", return_value=tap),
            mock.patch.object(self.supervisor, "wait_ready", return_value=18888),
            mock.patch.object(
                self.supervisor,
                "build_bwrap_argv",
                return_value=["/fake/bwrap", "--", "/fixed/gate"],
            ),
            mock.patch.object(
                self.supervisor,
                "NestedGateChannel",
                FakeGateChannel,
            ),
            mock.patch.object(
                self.supervisor.subprocess,
                "Popen",
                return_value=gate_process,
            ),
            mock.patch.object(self.supervisor, "signal_process_group"),
            mock.patch.object(self.supervisor, "wait_workload") as wait_workload,
            mock.patch.object(self.supervisor, "stop_tap"),
            mock.patch.object(
                self.supervisor,
                "read_trace",
                side_effect=self.supervisor.TapError("missing trace"),
            ),
        ):
            status = self.supervisor.run_supervised(
                self.exp_dir,
                [],
                ["/fixed/agent"],
                self.environ,
                state,
                FakeSidecar(),
                relay,
            )
        self.assertEqual(status, 143)
        self.assertFalse(state.workload_started)
        wait_workload.assert_not_called()

    def test_run_pilot_owns_sidecar_around_nested_workload(self) -> None:
        events: list[object] = []
        supervisor = self.supervisor

        class FakeSidecar:
            @classmethod
            def create(cls, *args, **kwargs):
                return cls(*args, **kwargs)

            def __init__(self, exp_dir, sandbox_exp_dir, *, job_id, cancelled=None):
                events.append(("construct", exp_dir, sandbox_exp_dir, job_id))
                self.sandbox_authority_path = Path("/run/meshshot-browser/authority.json")
                self.capability_dir = Path("/private/tmp/fixed-capability")
                self.cancelled = cancelled

            def start(self):
                events.append("sidecar-start")

            def close(self, *, workload_status):
                events.append(("sidecar-close", workload_status))
                return {
                    "schema": supervisor.RECEIPT_SCHEMA,
                    "status": "succeeded",
                    "imageId": supervisor.IMAGE_ID,
                    "imageSourceRevision": supervisor.IMAGE_SOURCE_REVISION,
                    "brokerImageId": supervisor.BROKER_IMAGE_ID,
                    "brokerImageSourceRevision": supervisor.BROKER_IMAGE_SOURCE_REVISION,
                    "brokerBaseImageId": supervisor.BROKER_BASE_IMAGE_ID,
                    "programs": supervisor.PROGRAMS,
                    "predicates": {
                        name: True for name in supervisor.RECEIPT_PREDICATES
                    },
                    "counts": {
                        "acceptedRequests": 2,
                        "freshContexts": 3,
                        "programCounts": {"residual": 1, "viewer": 1},
                    },
                    "failureCheck": None,
                    "retryAllowed": False,
                }

        def run_supervised(
            exp_dir, inputs, command, environ, state, sidecar, relay=None
        ):
            events.append(("workload", sidecar.sandbox_authority_path))
            state.workload_started = True
            return 0

        def finalize(exp_dir, status, environ, *, require_rollout):
            events.append(("finalize", status, require_rollout))
            return status

        with (
            mock.patch.object(self.supervisor, "prepare_exp"),
            mock.patch.object(
                self.supervisor,
                "prepare_nested_browser_gate",
                side_effect=lambda *args: events.append("gate-prepare"),
            ),
            mock.patch.object(self.supervisor, "BrowserSidecarJob", FakeSidecar),
            mock.patch.object(self.supervisor, "run_supervised", side_effect=run_supervised),
            mock.patch.object(self.supervisor, "finalize_pilot", side_effect=finalize),
            mock.patch.object(
                self.supervisor,
                "validate_exp_dir",
                return_value=self.supervisor.REPO_ROOT / "outputs/group/exp",
            ),
        ):
            status = self.supervisor.run_pilot(
                self.supervisor.REPO_ROOT / "outputs/group/exp",
                [],
                ["/fake/workload"],
                self.environ,
            )

        self.assertEqual(status, 0)
        self.assertEqual(
            [
                event if isinstance(event, str) else event[0]
                for event in events
            ],
            [
                "construct",
                "gate-prepare",
                "sidecar-start",
                "workload",
                "sidecar-close",
                "finalize",
            ],
        )
        self.assertEqual(
            events[3][1],
            Path("/run/meshshot-browser/authority.json"),
        )

    def test_run_pilot_installs_relay_before_sidecar_and_cleans_startup_signal(self) -> None:
        events: list[object] = []

        class FakeRelay:
            def __init__(self):
                self.cancelled = False
                self.signum = None

            def __enter__(self):
                events.append("relay-enter")
                return self

            def __exit__(self, *args):
                events.append("relay-exit")
                return False

        relay = FakeRelay()

        class FakeSidecar:
            @classmethod
            def create(cls, *args, **kwargs):
                return cls(*args, **kwargs)

            def __init__(self, exp_dir, sandbox_exp_dir, *, job_id, cancelled):
                self.capability_dir = Path("/private/tmp/fixed-capability")
                self.sandbox_authority_path = Path("/run/meshshot-browser/authority.json")
                self.cancelled = cancelled
                events.append("construct")

            def start(self):
                events.append("sidecar-start")
                relay.cancelled = True
                relay.signum = signal.SIGTERM

            def close(self, *, workload_status):
                events.append(("sidecar-close", workload_status))
                return {
                    "schema": "meshshot.browser-sidecar.job-receipt/2",
                    "status": "failed",
                    "cleanupErrors": [],
                    "absenceProof": {"proved": True},
                }

        with (
            mock.patch.object(self.supervisor, "prepare_exp"),
            mock.patch.object(self.supervisor, "prepare_nested_browser_gate"),
            mock.patch.object(self.supervisor, "SignalRelay", return_value=relay),
            mock.patch.object(self.supervisor, "BrowserSidecarJob", FakeSidecar),
            mock.patch.object(self.supervisor, "run_supervised") as supervised,
            mock.patch.object(
                self.supervisor,
                "finalize_pilot",
                side_effect=lambda exp, status, env, **kwargs: status,
            ),
            mock.patch.object(
                self.supervisor,
                "validate_exp_dir",
                return_value=self.supervisor.REPO_ROOT / "outputs/group/exp",
            ),
        ):
            status = self.supervisor.run_pilot(
                self.supervisor.REPO_ROOT / "outputs/group/exp",
                [],
                ["/fake/workload"],
                self.environ,
            )

        self.assertEqual(status, 128 + signal.SIGTERM)
        self.assertEqual(events[0:3], ["relay-enter", "construct", "sidecar-start"])
        self.assertIn(("sidecar-close", 128 + signal.SIGTERM), events)
        self.assertEqual(events[-1], "relay-exit")
        supervised.assert_not_called()

    def test_runner_rejects_failed_receipt_after_successful_workload(self) -> None:
        class FakeSidecar:
            @classmethod
            def create(cls, *args, **kwargs):
                return cls(*args, **kwargs)

            def __init__(self, exp_dir, sandbox_exp_dir, *, job_id, cancelled=None):
                self.capability_dir = Path("/private/tmp/fixed-capability")
                self.sandbox_authority_path = Path("/run/meshshot-browser/authority.json")

            def start(self):
                return None

            def close(self, *, workload_status):
                return {
                    "schema": "meshshot.browser-sidecar.job-receipt/2",
                    "status": "failed",
                    "cleanupErrors": [],
                    "absenceProof": {"proved": True},
                }

        with (
            mock.patch.object(self.supervisor, "prepare_exp"),
            mock.patch.object(self.supervisor, "BrowserSidecarJob", FakeSidecar),
            mock.patch.object(self.supervisor, "run_supervised", return_value=0),
            mock.patch.object(
                self.supervisor,
                "finalize_pilot",
                side_effect=lambda exp, status, env, **kwargs: status,
            ),
            mock.patch.object(
                self.supervisor,
                "validate_exp_dir",
                return_value=self.supervisor.REPO_ROOT / "outputs/group/exp",
            ),
        ):
            status = self.supervisor.run_pilot(
                self.supervisor.REPO_ROOT / "outputs/group/exp",
                [],
                ["/fake/workload"],
                self.environ,
            )
        self.assertEqual(status, 1)

    def test_runner_rejects_succeeded_receipt_with_absent_closed_predicate(self) -> None:
        self.assertFalse(
            self.supervisor.sidecar_receipt_succeeded(
                {
                    "schema": self.supervisor.RECEIPT_SCHEMA,
                    "status": "succeeded",
                }
            )
        )


class ProductionPathContractTests(unittest.TestCase):
    """Keep the production entrypoint mandatory-tap and status preserving."""

    def test_runner_direct_entrypoint_loads_retry_proxy(self) -> None:
        result = subprocess.run(
            [
                os.environ.get("PYTHON_BIN", "python3"),
                str(PILOT_ROOT / "runner.py"),
                "--help",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_path_uses_tap_only_gateway(self) -> None:
        pilot = (PILOT_ROOT / "toys4k-pilot.sh").read_text(encoding="utf-8")
        runner = (PILOT_ROOT / "runner.py").read_text(encoding="utf-8")
        gateway = (REPO_ROOT / "gateway" / "codex-tap-gpt56").read_text(
            encoding="utf-8"
        )
        self.assertIn("gateway/codex-tap-gpt56", pilot)
        self.assertIn("runner.py", pilot)
        self.assertNotIn("sandbox-run.sh", pilot)
        self.assertNotIn("eval ", pilot)
        self.assertNotIn("SANDBOX_RUN", pilot)
        self.assertNotIn("CODEX_RUN", pilot)
        self.assertNotIn('$REPO_ROOT/gateway', pilot)
        self.assertIn('run --input "$PLY"', pilot)
        self.assertNotIn("--skill", pilot)
        self.assertNotIn("PILOT_SKILLS", pilot)
        self.assertIn("build_bwrap_argv", runner)
        self.assertIn("LifecycleState", runner)
        self.assertNotIn('"--ro-bind",\n        "/",\n        "/"', runner)
        self.assertNotIn("list_skill_dirs", runner)
        self.assertIn("resolve_installed_skill_dirs", runner)
        self.assertIn('subparsers.add_parser("clean")', runner)
        self.assertIn("--skip-git-repo-check", pilot)
        self.assertIn("--disable\n    plugins", pilot)
        self.assertNotIn("--disable\n    view_image", pilot)
        self.assertIn("Do not call `view_image`", pilot)
        self.assertIn("danger-full-access", pilot)
        self.assertNotIn("workspace-write", pilot)
        self.assertTrue(gateway.startswith("#!/usr/bin/env bash\n"))
        self.assertIn('readonly CODEX_BIN="codex"', gateway)
        self.assertNotIn("/opt/homebrew", gateway)
        self.assertIn("CLAUDE_TAP_URL must be set", gateway)
        self.assertNotIn("v2.open.venus.oa.com", gateway)
        # The network-free CVM half-integration gate has passed, so no legacy
        # entrypoint or command-string lifecycle helper remains.
        for legacy_path in (
            "toys4k-pilot.sh",
            "toys4k-batch.sh",
            "pilot-tap-supervisor.py",
            "codex-init.sh",
            "codex-exit.sh",
            "sandbox-init.sh",
            "sandbox-clean.sh",
        ):
            self.assertFalse((UTILS_ROOT / legacy_path).exists())

    def test_gateway_rejects_missing_url_before_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            capture = Path(temp) / "argv"
            fake_codex = Path(temp) / "codex"
            fake_codex.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE\"\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            env = dict(os.environ)
            env.update(
                {
                    "CAPTURE": str(capture),
                    "CODEX_BIN": str(fake_codex),
                    "VENUS_TOKEN": "token",
                }
            )
            env.pop("CLAUDE_TAP_URL", None)
            result = subprocess.run(
                [str(REPO_ROOT / "gateway" / "codex-tap-gpt56"), "sol", "exec"],
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            capture_exists = capture.exists()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(capture_exists)

    def test_gateway_rejects_non_numeric_loopback_port(self) -> None:
        env = dict(os.environ)
        env.update(
            {
                "CODEX_BIN": "/does/not/matter",
                "VENUS_TOKEN": "token",
                "CLAUDE_TAP_URL": "http://127.0.0.1:not-a-port/v1",
            }
        )
        result = subprocess.run(
            [str(REPO_ROOT / "gateway" / "codex-tap-gpt56"), "sol", "exec"],
            env=env,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_gateway_passes_only_loopback_provider_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            capture = Path(temp) / "argv"
            fake_codex = Path(temp) / "codex"
            fake_codex.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE\"\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            env = dict(os.environ)
            env.update(
                {
                    "CAPTURE": str(capture),
                    "CODEX_BIN": "/must/be-ignored",
                    "PATH": f"{temp}:{env.get('PATH', '')}",
                    "VENUS_TOKEN": "token",
                    "CLAUDE_TAP_URL": "http://127.0.0.1:18888/v1",
                }
            )
            result = subprocess.run(
                [
                    str(REPO_ROOT / "gateway" / "codex-tap-gpt56"),
                    "terra",
                    "exec",
                    "prompt",
                ],
                env=env,
                check=False,
            )
            argv = capture.read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 0)
        self.assertIn('base_url="http://127.0.0.1:18888/v1"', argv)
        self.assertNotIn("v2.open.venus.oa.com", argv)

    def test_prepare_exp_creates_initial_git_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exp_dir = Path(temp) / "exp"
            load_runner().prepare_exp(exp_dir)
            head = subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"],
                cwd=exp_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            ignored = (exp_dir / ".gitignore").read_text(encoding="utf-8")
            run_exists = (exp_dir / "run").is_dir()
            name = subprocess.run(
                ["git", "config", "user.name"],
                cwd=exp_dir,
                check=True,
                capture_output=True,
                text=True,
            )
        self.assertTrue(head.stdout.strip())
        self.assertIn("run/", ignored)
        self.assertIn("artifact_manifest.json", ignored)
        self.assertEqual(name.stdout.strip(), "pilot")
        self.assertTrue(run_exists)

    def test_manifest_includes_hidden_cad_reviews_and_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exp_dir = Path(temp) / "exp"
            (exp_dir / "reviews").mkdir(parents=True)
            (exp_dir / "reviews" / "iso_20260730T120000Z.png").write_bytes(b"png")
            (exp_dir / ".part.step").mkdir()
            (exp_dir / ".part.step" / "topology.json").write_text(
                "{}\n",
                encoding="utf-8",
            )
            (exp_dir / "run/.codex-upper").mkdir(parents=True)
            (exp_dir / "run/.codex-upper/state.db").write_bytes(b"private")
            (exp_dir / "run/stderr.log").write_text("diagnostic\n", encoding="utf-8")
            (exp_dir / "run/venus-retry.jsonl").write_text(
                '{"attempt":1,"status":200,"error_code":null}\n',
                encoding="utf-8",
            )
            load_runner().write_artifact_manifest(exp_dir, 0, 0)
            manifest = json.loads(
                (exp_dir / "artifact_manifest.json").read_text(encoding="utf-8")
            )
        paths = [item["path"] for item in manifest["files"]]
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(
            paths,
            [
                ".part.step/topology.json",
                "reviews/iso_20260730T120000Z.png",
                "run/stderr.log",
                "run/venus-retry.jsonl",
            ],
        )
        self.assertNotIn("run/.codex-upper/state.db", paths)

    def test_workspace_delivery_gate_uses_public_validator_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exp_dir = Path(temp) / "exp"
            exp_dir.mkdir()
            runner = load_runner()
            completed = SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "valid": True,
                        "graph": {
                            "schema": "mesh-to-cad.step-index/1",
                            "final_delivery": {
                                "selected_step": 2,
                                "accepted": False,
                                "identity_sha256": "a" * 64,
                            },
                        },
                    }
                ),
                stderr="",
            )
            with mock.patch.object(
                runner.subprocess,
                "run",
                return_value=completed,
            ) as validate:
                delivery = runner.validate_workspace_delivery(exp_dir)
        self.assertEqual(delivery["selected_step"], 2)
        argv = validate.call_args.args[0]
        self.assertIn("validate", argv)
        self.assertEqual(argv[-1], str(exp_dir))

    def test_workspace_delivery_gate_rejects_valid_graph_without_final(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exp_dir = Path(temp) / "exp"
            exp_dir.mkdir()
            runner = load_runner()
            completed = SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "valid": True,
                        "graph": {
                            "schema": "mesh-to-cad.step-index/1",
                            "final_delivery": None,
                        },
                    }
                ),
                stderr="",
            )
            with mock.patch.object(
                runner.subprocess,
                "run",
                return_value=completed,
            ):
                with self.assertRaisesRegex(
                    runner.PilotError,
                    "complete Final Delivery",
                ):
                    runner.validate_workspace_delivery(exp_dir)

    def test_finalize_keeps_rollout_anomaly_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exp_dir = Path(temp) / "exp"
            (exp_dir / "run/.codex-upper").mkdir(parents=True)
            status = load_runner().finalize_pilot(exp_dir, 7, {})
            manifest = json.loads(
                (exp_dir / "artifact_manifest.json").read_text(encoding="utf-8")
            )
        self.assertEqual(status, 3)
        self.assertEqual(manifest["final_status"], 3)

    def test_finalize_preserves_int_term_through_postmortem_failures(self) -> None:
        """SIGINT/TERM status dominates missing rollout, collection, and publication."""

        for signum in (signal.SIGINT, signal.SIGTERM):
            expected = 128 + signum
            with self.subTest(signum=signum, failure="missing-rollout"):
                with tempfile.TemporaryDirectory() as temp:
                    exp_dir = Path(temp) / "exp"
                    (exp_dir / "run/.codex-upper").mkdir(parents=True)
                    runner = load_runner()
                    with mock.patch.object(
                        runner,
                        "publish_artifact_manifest",
                        return_value=False,
                    ):
                        status = runner.finalize_pilot(exp_dir, expected, {})
                self.assertEqual(status, expected)

            with self.subTest(signum=signum, failure="collection"):
                with tempfile.TemporaryDirectory() as temp:
                    exp_dir = Path(temp) / "exp"
                    rollout = (
                        exp_dir
                        / "run/.codex-upper/sessions/a/b/c/rollout-test.jsonl"
                    )
                    rollout.parent.mkdir(parents=True)
                    rollout.write_text("{}\n", encoding="utf-8")
                    runner = load_runner()
                    with (
                        mock.patch.object(Path, "replace", side_effect=OSError("closed")),
                        mock.patch.object(
                            runner,
                            "publish_artifact_manifest",
                            return_value=False,
                        ),
                    ):
                        status = runner.finalize_pilot(exp_dir, expected, {})
                self.assertEqual(status, expected)

    def test_finalize_preserves_nonzero_status_and_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exp_dir = Path(temp) / "exp"
            rollout = (
                exp_dir
                / "run/.codex-upper"
                / "sessions"
                / "a"
                / "b"
                / "c"
                / "rollout-test.jsonl"
            )
            rollout.parent.mkdir(parents=True)
            rollout.write_text("{}\n", encoding="utf-8")
            status = load_runner().finalize_pilot(exp_dir, 9, {})
            captured = (exp_dir / "run/rollout.jsonl").read_text(encoding="utf-8")
            upper_exists = (exp_dir / "run/.codex-upper").exists()
            manifest_exists = (exp_dir / "artifact_manifest.json").is_file()
        self.assertEqual(status, 9)
        self.assertEqual(captured, "{}\n")
        self.assertTrue(upper_exists)
        self.assertTrue(manifest_exists)

    def test_finalize_success_without_review_returns_artifact_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exp_dir = Path(temp) / "exp"
            rollout = (
                exp_dir
                / "run/.codex-upper"
                / "sessions"
                / "a"
                / "b"
                / "c"
                / "rollout-test.jsonl"
            )
            rollout.parent.mkdir(parents=True)
            rollout.write_text("{}\n", encoding="utf-8")
            status = load_runner().finalize_pilot(exp_dir, 0, {})
            upper_exists = (exp_dir / "run/.codex-upper").exists()
            manifest = json.loads(
                (exp_dir / "artifact_manifest.json").read_text(encoding="utf-8")
            )
        self.assertEqual(status, 4)
        self.assertTrue(upper_exists)
        self.assertEqual(manifest["final_status"], 4)

    def test_finalize_success_collects_and_cleans(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exp_dir = Path(temp) / "exp"
            rollout = (
                exp_dir
                / "run/.codex-upper"
                / "sessions"
                / "a"
                / "b"
                / "c"
                / "rollout-test.jsonl"
            )
            rollout.parent.mkdir(parents=True)
            rollout.write_text("{}\n", encoding="utf-8")
            runner = load_runner()
            with mock.patch.object(
                runner,
                "validate_workspace_delivery",
                return_value={"selected_step": 0, "accepted": True},
            ):
                status = runner.finalize_pilot(exp_dir, 0, {})
            captured = (exp_dir / "run/rollout.jsonl").read_text(encoding="utf-8")
            upper_exists = (exp_dir / "run/.codex-upper").exists()
            manifest_exists = (exp_dir / "artifact_manifest.json").is_file()
        self.assertEqual(status, 0)
        self.assertEqual(captured, "{}\n")
        self.assertFalse(upper_exists)
        self.assertTrue(manifest_exists)

    def test_finalize_keep_state_preserves_successful_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exp_dir = Path(temp) / "exp"
            rollout = (
                exp_dir
                / "run/.codex-upper"
                / "sessions"
                / "a"
                / "b"
                / "c"
                / "rollout-test.jsonl"
            )
            rollout.parent.mkdir(parents=True)
            rollout.write_text("{}\n", encoding="utf-8")
            runner = load_runner()
            with mock.patch.object(
                runner,
                "validate_workspace_delivery",
                return_value={"selected_step": 0, "accepted": True},
            ):
                status = runner.finalize_pilot(
                    exp_dir,
                    0,
                    {"KEEP_STATE": "1"},
                )
            upper_exists = (exp_dir / "run/.codex-upper").exists()
        self.assertEqual(status, 0)
        self.assertTrue(upper_exists)

    def test_build_bwrap_argv_exposes_only_task_and_installed_repo_files(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo_root = (Path(temp) / "repo").resolve()
            exp_dir = repo_root / "outputs" / "group" / "exp with spaces"
            skill_dir = repo_root / "skills" / "fake"
            outside_skill = Path(temp) / "outside-skill"
            input_path = repo_root / "models" / "toys4k" / "input.ply"
            other_input = repo_root / "models" / "toys4k" / "other.ply"
            other_exp = repo_root / "outputs" / "group" / "other-exp"
            gateway = repo_root / "gateway" / "codex-tap-gpt56"
            venv = repo_root / ".venv"
            gate_script = repo_root / "scripts/pilot/browser_sidecar_gate.py"
            meshshot_source = repo_root / "packages/meshshot/src/meshshot"
            host_home = Path(temp) / "host-home"
            playwright = host_home / ".cache" / "ms-playwright"
            installed_skills = host_home / ".codex" / "skills"
            exp_dir.mkdir(parents=True)
            skill_dir.mkdir(parents=True)
            outside_skill.mkdir()
            (repo_root / "browser-capability").mkdir()
            (repo_root / "browser-capability" / "browser-gate.pyz").write_bytes(
                b"sealed-gate"
            )
            (repo_root / "browser-capability" / "browser-gate.pyz").chmod(0o444)
            (repo_root / "browser-capability" / "gate-input.json").write_text(
                json.dumps({
                    "surfaceManifest": {
                        "schema": "meshshot.browser-sidecar.agent-browser-surface/1",
                        "scanRoots": sorted([
                            "/usr",
                            "/workspace/repo/.venv",
                            "/workspace/repo/gateway/codex-tap-gpt56",
                            "/workspace/repo/models/toys4k/input.ply",
                            "/workspace/repo/outputs/group/exp with spaces",
                            "/workspace/repo/skills/fake",
                            "/home/pilot/.codex",
                            "/home/pilot/.codex/skills/fake",
                        ]),
                        "browserExclusions": [],
                    }
                }) + "\n",
                encoding="utf-8",
            )
            input_path.parent.mkdir(parents=True)
            gateway.parent.mkdir(parents=True)
            venv.mkdir()
            gate_script.parent.mkdir(parents=True)
            meshshot_source.mkdir(parents=True)
            playwright.mkdir(parents=True)
            installed_skills.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# fake\n", encoding="utf-8")
            (outside_skill / "SKILL.md").write_text(
                "# unrelated\n",
                encoding="utf-8",
            )
            (installed_skills / "fake").symlink_to(
                skill_dir,
                target_is_directory=True,
            )
            (installed_skills / "unrelated").symlink_to(
                outside_skill,
                target_is_directory=True,
            )
            (installed_skills / "system-skill").mkdir()
            input_path.write_text("ply\n", encoding="utf-8")
            other_input.write_text("ply\n", encoding="utf-8")
            gateway.write_text("#!/bin/sh\n", encoding="utf-8")
            gate_script.write_text("# artifact builder only\n", encoding="utf-8")
            environ = {
                "HOME": str(host_home),
                "PATH": "/fake/bin",
                "VENUS_TOKEN": "super-secret-token",
            }
            runner = load_runner()
            with (
                mock.patch.object(
                    runner.shutil,
                    "which",
                    side_effect=lambda command, **_: {
                        "bwrap": "/fake/bwrap",
                        "codex": "/usr/bin/codex",
                    }[command],
                ),
                mock.patch.object(
                    runner,
                    "existing_system_paths",
                    return_value=[Path("/usr")],
                ),
            ):
                argv = runner.build_bwrap_argv(
                    repo_root,
                    exp_dir,
                    [input_path],
                    ["/fake/codex", "prompt with spaces"],
                    environ,
                    browser_capability_dir=repo_root / "browser-capability",
                )
        triples = [argv[index : index + 3] for index in range(len(argv) - 2)]
        self.assertIn(Path("/etc/crypto-policies"), runner.SYSTEM_RO_PATHS)
        self.assertEqual(
            argv[-6:],
            [
                "--",
                "/workspace/repo/.venv/bin/python",
                "/run/meshshot-browser/browser-gate.pyz",
                "--",
                "/fake/codex",
                "prompt with spaces",
            ],
        )
        self.assertNotIn("super-secret-token", argv)
        self.assertNotIn("--dev-bind", argv)
        self.assertIn("--unshare-pid", argv)
        self.assertIn("--unshare-ipc", argv)
        self.assertIn("--unshare-uts", argv)
        self.assertEqual(argv[argv.index("--cap-drop") + 1], "ALL")
        self.assertNotIn(["--ro-bind", "/", "/"], triples)
        self.assertEqual(argv[argv.index("--remount-ro") + 1], "/")
        self.assertNotIn(str(repo_root.resolve()), argv)
        self.assertNotIn(str(host_home.resolve()), argv)
        self.assertNotIn(str(host_home / ".codex"), argv)
        self.assertNotIn(str(playwright.resolve()), argv)
        self.assertNotIn(str(other_input.resolve()), argv)
        self.assertNotIn(str(other_exp.resolve()), argv)
        self.assertNotIn(str(outside_skill.resolve()), argv)
        self.assertIn(
            [
                "--ro-bind",
                str(input_path.resolve()),
                "/workspace/repo/models/toys4k/input.ply",
            ],
            triples,
        )
        self.assertIn(
            [
                "--ro-bind",
                str((repo_root / "browser-capability").resolve()),
                "/run/meshshot-browser",
            ],
            triples,
        )
        self.assertNotIn("/run/meshshot-gate/meshshot-src", argv)
        self.assertNotIn(str((repo_root / "packages/meshshot/src").resolve()), argv)
        self.assertNotIn(
            str((repo_root / "scripts/pilot/browser_sidecar_gate.py").resolve()),
            argv,
        )
        self.assertIn(
            [
                "--bind",
                str(exp_dir.resolve()),
                "/workspace/repo/outputs/group/exp with spaces",
            ],
            triples,
        )
        self.assertIn(
            [
                "--ro-bind",
                str(skill_dir.resolve()),
                "/workspace/repo/skills/fake",
            ],
            triples,
        )
        self.assertIn(
            [
                "--ro-bind",
                str(skill_dir.resolve()),
                "/home/pilot/.codex/skills/fake",
            ],
            triples,
        )
        self.assertNotIn("--overlay-src", argv)
        self.assertEqual(argv[argv.index("--chdir") + 1], "/workspace/repo")

        child_env = runner.build_sandbox_environment(
            environ,
            "http://127.0.0.1:18888/v1",
        )
        self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", child_env)
        self.assertNotIn("MESHSHOT_BROWSER_AUTHORITY_FILE", child_env)

    def test_mounted_surface_browser_discovery_catches_renames_distros_and_caches(self) -> None:
        """Every browser root in a mounted read-only surface becomes an exact mask."""

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "runtime"
            renamed_package = source / "lib/python/site-packages/runtime_tools"
            distro_binary = source / "bin/web-renderer"
            renamed_cache = source / "var/cache/vendor-build-1223"
            renamed_package.mkdir(parents=True)
            distro_binary.parent.mkdir(parents=True)
            renamed_cache.mkdir(parents=True)
            (renamed_package / "METADATA").write_text(
                "Metadata-Version: 2.1\nName: playwright\n", encoding="utf-8"
            )
            distro_binary.write_bytes(
                b"\x7fELF" + b"\0" * 64 + b"Chromium 148.0.7778.96 HeadlessChrome"
            )
            distro_binary.chmod(0o755)
            (renamed_cache / "browser-marker.json").write_text(
                '{"product":"chromium","revision":"1223"}\n', encoding="utf-8"
            )
            runner = load_runner()
            result = runner.discover_browser_roots(
                [(source, Path("/usr"), True)]
            )

        self.assertEqual(
            {(item["kind"], item["target"]) for item in result},
            {
                ("package", "/usr/lib/python/site-packages/runtime_tools"),
                ("executable", "/usr/bin/web-renderer"),
                ("cache", "/usr/var/cache/vendor-build-1223"),
            },
        )
        self.assertTrue(all(set(item) == {"kind", "target", "mask"} for item in result))

    def test_browser_surface_preflight_failure_precedes_sidecar_start(self) -> None:
        """An unclosed mounted surface fails before any Docker lifecycle mutation."""

        runner = load_runner()
        with tempfile.TemporaryDirectory() as temp:
            exp = (Path(temp) / "repo/outputs/group/exp").resolve()
            exp.mkdir(parents=True)
            sidecar = mock.Mock()
            sidecar_type = mock.Mock()
            sidecar_type.create.return_value = sidecar
            with (
                mock.patch.object(runner, "REPO_ROOT", (Path(temp) / "repo").resolve()),
                mock.patch.object(runner, "prepare_exp"),
                mock.patch.object(runner, "validate_exp_dir", return_value=exp),
                mock.patch.object(runner, "BrowserSidecarJob", sidecar_type),
                mock.patch.object(
                    runner,
                    "prepare_nested_browser_gate",
                    side_effect=runner.PilotError("browser surface is not closed"),
                ),
                mock.patch.object(runner, "finalize_pilot", return_value=1),
            ):
                status = runner.run_pilot(exp, [], ["/fixed/agent"], {})
        self.assertEqual(status, 1)
        sidecar.start.assert_not_called()

    def test_writable_browser_artifact_fails_actual_gate_preparation(self) -> None:
        """Writable experiment/cache discovery closes before any Sidecar resource."""

        runner = load_runner()
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp)
            repo_root = root / "repo"
            exp_dir = repo_root / "outputs/group/exp"
            input_path = repo_root / "models/toys4k/input.ply"
            gateway = repo_root / "gateway/codex-tap-gpt56"
            skill = repo_root / "skills/fake"
            installed = root / "home/.codex/skills"
            capability = root / "capability"
            (repo_root / ".venv").mkdir(parents=True)
            exp_dir.mkdir(parents=True)
            input_path.parent.mkdir(parents=True)
            input_path.write_text("ply\n", encoding="utf-8")
            gateway.parent.mkdir(parents=True)
            gateway.write_text("#!/bin/sh\n", encoding="utf-8")
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# fake\n", encoding="utf-8")
            installed.mkdir(parents=True)
            (installed / "fake").symlink_to(skill, target_is_directory=True)
            capability.mkdir()
            writable_browser = exp_dir / ".cache/ms-playwright/chrome"
            writable_browser.parent.mkdir(parents=True)
            writable_browser.write_bytes(b"\x7fELF" + b"\0" * 32)
            writable_browser.chmod(0o755)
            sidecar = mock.Mock(
                capability_dir=capability,
                job_id="formal-job-1",
                gate_nonce="1" * 16,
            )
            with mock.patch.object(
                runner, "existing_system_paths", return_value=[]
            ):
                with self.assertRaisesRegex(
                    runner.PilotError,
                    "writable Agent surface",
                ):
                    runner.prepare_nested_browser_gate(
                        repo_root,
                        exp_dir,
                        [input_path],
                        {"HOME": str(root / "home")},
                        sidecar,
                    )
            capability_entries = list(capability.iterdir())

        sidecar.start.assert_not_called()
        sidecar.configure_nested_gate.assert_not_called()
        self.assertEqual(capability_entries, [])

    def test_preflight_failure_skips_rollout_contract_and_writes_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo_root = Path(temp) / "repo"
            exp_dir = repo_root / "outputs/group/exp"
            runner = load_runner()

            class FakeSidecar:
                @classmethod
                def create(cls, *args, **kwargs):
                    return cls(*args, **kwargs)

                def __init__(self, *args, **kwargs):
                    pass

                def start(self):
                    return None

                def close(self, *, workload_status):
                    return {
                        "cleanupErrors": [],
                        "absenceProof": {"proved": True},
                    }

            with (
                mock.patch.object(runner, "REPO_ROOT", repo_root),
                mock.patch.object(runner, "BrowserSidecarJob", FakeSidecar),
                mock.patch.object(
                    runner,
                    "build_bwrap_argv",
                    side_effect=runner.PilotError("missing runtime"),
                ),
                mock.patch.object(runner, "start_tap") as start_tap,
            ):
                status = runner.run_pilot(
                    exp_dir,
                    [],
                    ["/fake/workload"],
                    {},
                )
            manifest = json.loads(
                (exp_dir / "artifact_manifest.json").read_text(encoding="utf-8")
            )
        self.assertEqual(status, 1)
        self.assertEqual(manifest["final_status"], 1)
        start_tap.assert_not_called()

    def test_cleanup_failure_rewrites_success_manifest_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exp_dir = Path(temp) / "exp"
            rollout = (
                exp_dir
                / "run/.codex-upper"
                / "sessions"
                / "a"
                / "b"
                / "c"
                / "rollout-test.jsonl"
            )
            rollout.parent.mkdir(parents=True)
            rollout.write_text("{}\n", encoding="utf-8")
            runner = load_runner()
            with (
                mock.patch.object(
                    runner,
                    "validate_workspace_delivery",
                    return_value={"selected_step": 0, "accepted": True},
                ),
                mock.patch.object(
                    runner,
                    "cleanup_sandbox",
                    side_effect=runner.PilotError("cleanup failed"),
                ),
            ):
                status = runner.finalize_pilot(exp_dir, 0, {})
            manifest = json.loads(
                (exp_dir / "artifact_manifest.json").read_text(encoding="utf-8")
            )
        self.assertEqual(status, 1)
        self.assertEqual(manifest["final_status"], 1)

    def test_clean_subcommand_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo_root = Path(temp) / "repo"
            exp_dir = repo_root / "outputs" / "group" / "exp"
            (exp_dir / "run/.codex-upper").mkdir(parents=True)
            runner = load_runner()
            with mock.patch.object(runner, "REPO_ROOT", repo_root):
                first = runner.main(["clean", str(exp_dir)])
                second = runner.main(["clean", str(exp_dir)])
        self.assertEqual((first, second), (0, 0))

    def test_clean_rejects_path_outside_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo_root = Path(temp) / "repo"
            outside = repo_root / "do-not-delete"
            (outside / "run/.codex-upper").mkdir(parents=True)
            runner = load_runner()
            with mock.patch.object(runner, "REPO_ROOT", repo_root):
                status = runner.main(["clean", str(outside)])
            upper_exists = (outside / "run/.codex-upper").exists()
        self.assertEqual(status, 1)
        self.assertTrue(upper_exists)

    def test_every_runner_class_and_function_has_a_docstring(self) -> None:
        path = PILOT_ROOT / "runner.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        missing = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if ast.get_docstring(node) is None:
                    missing.append(node.name)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
