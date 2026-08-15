from __future__ import annotations

import ast
import importlib.util
import json
import os
import signal
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
PILOT_ROOT = REPO_ROOT / "scripts" / "pilot"
UTILS_ROOT = REPO_ROOT / "scripts" / "utils"


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

    def test_run_pilot_owns_sidecar_around_nested_workload(self) -> None:
        events: list[object] = []

        class FakeSidecar:
            def __init__(self, exp_dir, sandbox_exp_dir, *, job_id):
                events.append(("construct", exp_dir, sandbox_exp_dir, job_id))
                self.sandbox_authority_path = sandbox_exp_dir / "run/browser-authority.json"

            def start(self):
                events.append("sidecar-start")

            def close(self, *, workload_status):
                events.append(("sidecar-close", workload_status))
                return {
                    "cleanupErrors": [],
                    "absenceProof": {"proved": True},
                    "terminal": {"ExitCode": 0},
                    "brokerStatus": 0,
                }

        def run_supervised(exp_dir, inputs, command, environ, state, sidecar):
            events.append(("workload", sidecar.sandbox_authority_path))
            state.workload_started = True
            return 0

        def finalize(exp_dir, status, environ, *, require_rollout):
            events.append(("finalize", status, require_rollout))
            return status

        with (
            mock.patch.object(self.supervisor, "prepare_exp"),
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
            ["construct", "sidecar-start", "workload", "sidecar-close", "finalize"],
        )
        self.assertEqual(
            events[2][1],
            self.supervisor.SANDBOX_REPO_ROOT / "outputs/group/exp/run/browser-authority.json",
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
            host_home = Path(temp) / "host-home"
            playwright = host_home / ".cache" / "ms-playwright"
            installed_skills = host_home / ".codex" / "skills"
            exp_dir.mkdir(parents=True)
            skill_dir.mkdir(parents=True)
            outside_skill.mkdir()
            input_path.parent.mkdir(parents=True)
            gateway.parent.mkdir(parents=True)
            venv.mkdir()
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
                )
        triples = [argv[index : index + 3] for index in range(len(argv) - 2)]
        self.assertIn(Path("/etc/crypto-policies"), runner.SYSTEM_RO_PATHS)
        self.assertEqual(argv[-3:], ["--", "/fake/codex", "prompt with spaces"])
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
            "/workspace/repo/outputs/group/exp/run/browser-authority.json",
        )
        self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", child_env)
        self.assertEqual(
            child_env["MESHSHOT_BROWSER_AUTHORITY_FILE"],
            "/workspace/repo/outputs/group/exp/run/browser-authority.json",
        )

    def test_preflight_failure_skips_rollout_contract_and_writes_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo_root = Path(temp) / "repo"
            exp_dir = repo_root / "outputs/group/exp"
            runner = load_runner()

            class FakeSidecar:
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
