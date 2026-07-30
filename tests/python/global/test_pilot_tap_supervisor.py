from __future__ import annotations

import ast
import importlib.util
import os
import signal
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
UTILS_ROOT = REPO_ROOT / "scripts" / "utils"


def load_supervisor():
    """Load the hyphenated executable as a module for focused unit tests."""

    path = UTILS_ROOT / "pilot-tap-supervisor.py"
    spec = importlib.util.spec_from_file_location("pilot_tap_supervisor", path)
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


class SupervisorTests(unittest.TestCase):
    """Validate mandatory tap behavior without bwrap, network, or Venus."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.supervisor = load_supervisor()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.exp_dir = Path(self.temp.name)
        self.environ = {
            "PATH": os.environ.get("PATH", ""),
            "CLAUDE_TAP_BIN": "/fake/claude-tap",
            "TAP_READY_TIMEOUT": "0.1",
            "TAP_STOP_TIMEOUT": "0.1",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_missing_default_tap_is_installed_with_uv(self) -> None:
        environ = {"PATH": "/fake/bin"}
        with (
            mock.patch.object(
                self.supervisor.shutil,
                "which",
                side_effect=[None, "/fake/bin/uv", "/fake/bin/claude-tap"],
            ),
            mock.patch.object(self.supervisor.subprocess, "run") as run,
        ):
            resolved = self.supervisor.resolve_tap(environ)
        self.assertEqual(resolved, "/fake/bin/claude-tap")
        run.assert_called_once_with(
            ["/fake/bin/uv", "tool", "install", "--quiet", "claude-tap"],
            check=True,
            env=environ,
        )

    def test_explicit_missing_tap_override_never_installs(self) -> None:
        with (
            mock.patch.object(self.supervisor.shutil, "which", return_value=None),
            mock.patch.object(self.supervisor.subprocess, "run") as run,
        ):
            with self.assertRaises(self.supervisor.TapError):
                self.supervisor.resolve_tap(
                    {"PATH": "/fake", "CLAUDE_TAP_BIN": "/missing/tap"}
                )
        run.assert_not_called()

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
                    ["/fake/workload"],
                    {**self.environ, "TAP_STOP_TIMEOUT": "nan"},
                )
        start.assert_not_called()

    def test_start_tap_is_loopback_only_and_uses_per_exp_db(self) -> None:
        process = FakeProcess()
        with mock.patch.object(
            self.supervisor.subprocess,
            "Popen",
            return_value=process,
        ) as popen:
            returned = self.supervisor.start_tap(
                "/fake/claude-tap",
                self.exp_dir,
                self.environ,
            )
        self.assertIs(returned, process)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[argv.index("--tap-host") + 1], "127.0.0.1")
        self.assertEqual(argv[argv.index("--tap-port") + 1], "0")
        self.assertIn("--tap-no-launch", argv)
        self.assertEqual(
            popen.call_args.kwargs["env"]["CLOUDTAP_DB"],
            str(self.exp_dir / "traces.sqlite3"),
        )
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_ready_port_comes_from_this_process_log(self) -> None:
        log_path = self.exp_dir / ".claude-tap.log"
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
        log_path = self.exp_dir / ".claude-tap.log"
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
        db_path = self.exp_dir / "traces.sqlite3"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "CREATE TABLE sessions "
                "(id TEXT, started_at TEXT, status TEXT, record_count INTEGER)"
            )
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                ("session-1", "2026-07-30T12:00:00Z", "complete", 3),
            )
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
            (self.exp_dir / "trace.html").read_text(encoding="utf-8"),
            "<html>trace</html>",
        )
        self.assertFalse((self.exp_dir / ".claude-tap.log.export").exists())

    def test_missing_and_active_trace_are_not_valid(self) -> None:
        with self.assertRaisesRegex(self.supervisor.TapError, "missing"):
            self.supervisor.read_trace(self.exp_dir)

        db_path = self.exp_dir / "traces.sqlite3"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "CREATE TABLE sessions "
                "(id TEXT, started_at TEXT, status TEXT, record_count INTEGER)"
            )
            connection.execute(
                "INSERT INTO sessions VALUES "
                "('session-1', '2026-07-30', 'active', 1)"
            )
        self.assertEqual(
            self.supervisor.read_trace(self.exp_dir),
            ("session-1", "active", 1),
        )

    def test_failed_export_preserves_sqlite_and_does_not_publish_html(self) -> None:
        db_path = self.exp_dir / "traces.sqlite3"
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
        self.assertFalse((self.exp_dir / "trace.html").exists())
        self.assertTrue((self.exp_dir / ".claude-tap.log.export").is_file())

    def test_success_injects_only_loopback_tap_url_and_requires_trace(self) -> None:
        tap = FakeProcess()
        workload = FakeProcess(returncode=0)
        with (
            mock.patch.object(
                self.supervisor,
                "resolve_tap",
                return_value="/fake/claude-tap",
            ),
            mock.patch.object(self.supervisor, "start_tap", return_value=tap),
            mock.patch.object(self.supervisor, "wait_ready", return_value=18888),
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
                ["/fake/bwrap", "--", "/fake/codex"],
                self.environ,
            )
        self.assertEqual(status, 0)
        self.assertEqual(
            popen.call_args.kwargs["env"]["CLAUDE_TAP_URL"],
            "http://127.0.0.1:18888/v1",
        )

    def test_successful_workload_without_captured_request_fails(self) -> None:
        tap = FakeProcess()
        workload = FakeProcess(returncode=0)
        with (
            mock.patch.object(
                self.supervisor,
                "resolve_tap",
                return_value="/fake/claude-tap",
            ),
            mock.patch.object(self.supervisor, "start_tap", return_value=tap),
            mock.patch.object(self.supervisor, "wait_ready", return_value=18888),
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
            mock.patch.object(self.supervisor, "start_tap", return_value=tap),
            mock.patch.object(self.supervisor, "wait_ready", return_value=18888),
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
                ["/fake/bwrap", "--", "/fake/codex"],
                self.environ,
            )
        self.assertEqual(status, 9)

    def test_signal_status_wins_and_workload_is_not_started(self) -> None:
        tap = FakeProcess()
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
            mock.patch.object(self.supervisor, "SignalRelay", return_value=relay),
            mock.patch.object(self.supervisor, "start_tap", return_value=tap),
            mock.patch.object(self.supervisor, "wait_ready", return_value=None),
            mock.patch.object(self.supervisor, "stop_tap"),
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
                ["/fake/workload"],
                self.environ,
            )
        self.assertEqual(status, 130)
        popen.assert_not_called()


class ProductionPathContractTests(unittest.TestCase):
    """Keep the production entrypoint mandatory-tap and status preserving."""

    def test_production_path_uses_tap_only_gateway(self) -> None:
        codex_init = (UTILS_ROOT / "codex-init.sh").read_text(encoding="utf-8")
        pilot = (UTILS_ROOT / "toys4k-pilot.sh").read_text(encoding="utf-8")
        gateway = (REPO_ROOT / "gateway" / "codex-tap-gpt56").read_text(
            encoding="utf-8"
        )
        self.assertIn("gateway/codex-tap-gpt56", codex_init)
        self.assertNotIn("gateway/codex-gpt56 ${MODEL", codex_init)
        self.assertIn("pilot-tap-supervisor.py", pilot)
        self.assertIn("CLAUDE_TAP_URL must be set", gateway)
        self.assertNotIn("v2.open.venus.oa.com", gateway)
        self.assertIn('if codex_exports="$(', pilot)

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
                    "CODEX_BIN": str(fake_codex),
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

    def test_codex_exit_propagates_workload_status(self) -> None:
        codex_exit = (UTILS_ROOT / "codex-exit.sh").read_text(encoding="utf-8")
        self.assertIn('exit "$CODEX_EXIT"', codex_exit)

    def test_codex_exit_keeps_rollout_anomaly_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exp_dir = Path(temp) / "exp"
            upper = exp_dir / ".codex-upper"
            upper.mkdir(parents=True)
            env = dict(os.environ)
            env["SANDBOX_UPPER"] = str(upper)
            result = subprocess.run(
                [str(UTILS_ROOT / "codex-exit.sh"), str(exp_dir), "7"],
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        self.assertEqual(result.returncode, 3)

    def test_codex_exit_returns_nonzero_after_collecting_rollout(self) -> None:
        for expected_status in (1, 130):
            with self.subTest(status=expected_status):
                with tempfile.TemporaryDirectory() as temp:
                    exp_dir = Path(temp) / "exp"
                    rollout = (
                        exp_dir
                        / ".codex-upper"
                        / "sessions"
                        / "a"
                        / "b"
                        / "c"
                        / "rollout-test.jsonl"
                    )
                    rollout.parent.mkdir(parents=True)
                    rollout.write_text("{}\n", encoding="utf-8")
                    env = dict(os.environ)
                    env["SANDBOX_UPPER"] = str(exp_dir / ".codex-upper")
                    result = subprocess.run(
                        [
                            str(UTILS_ROOT / "codex-exit.sh"),
                            str(exp_dir),
                            str(expected_status),
                        ],
                        env=env,
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    captured = (exp_dir / "rollout.jsonl").read_text(
                        encoding="utf-8"
                    )
                self.assertEqual(result.returncode, expected_status)
                self.assertEqual(captured, "{}\n")

    def test_codex_exit_returns_zero_after_collecting_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exp_dir = Path(temp) / "exp"
            rollout = (
                exp_dir
                / ".codex-upper"
                / "sessions"
                / "a"
                / "b"
                / "c"
                / "rollout-test.jsonl"
            )
            rollout.parent.mkdir(parents=True)
            rollout.write_text("{}\n", encoding="utf-8")
            (exp_dir / ".codex-work").mkdir()
            env = dict(os.environ)
            env["SANDBOX_UPPER"] = str(exp_dir / ".codex-upper")
            result = subprocess.run(
                [str(UTILS_ROOT / "codex-exit.sh"), str(exp_dir), "0"],
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            captured = (exp_dir / "rollout.jsonl").read_text(encoding="utf-8")
            upper_exists = (exp_dir / ".codex-upper").exists()
            work_exists = (exp_dir / ".codex-work").exists()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(captured, "{}\n")
        self.assertFalse(upper_exists)
        self.assertFalse(work_exists)

    def test_every_supervisor_class_and_function_has_a_docstring(self) -> None:
        path = UTILS_ROOT / "pilot-tap-supervisor.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        missing = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if ast.get_docstring(node) is None:
                    missing.append(node.name)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
