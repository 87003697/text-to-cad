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
import sys
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


class FakeBrowserRuntimeJob:
    """Minimal stand-in matching the BrowserRuntimeJob seam used by runner.py."""

    def __init__(
        self,
        *,
        mcp_url: str = "http://127.0.0.1:12345/mcp",
        capability_dir: Path | None = None,
    ) -> None:
        self.mcp_url = mcp_url
        self.container_name = "ttc-br-fake-runtime"
        self.network_name = "ttc-br-fake-net"
        self.capability_dir = capability_dir or Path("/tmp/fake-br-cap")
        self.started = False
        self.stopped = False

    @classmethod
    def create(cls, exp_dir, **kw):
        """Match BrowserRuntimeJob.create's classmethod signature."""

        return cls()

    def start(self):
        """Record lifecycle start without touching Docker."""

        self.started = True

    def stop(self):
        """Record lifecycle stop without touching Docker."""

        self.started = False
        self.stopped = True

    def preflight(self):
        """Match the paid-workload admission check without browser I/O."""

        return None

    def preflight_mcp(self):
        """Match the provider-free real Viewer admission check."""

        return None

    def poll_failed(self):
        """Report a healthy container so wait_workload does not tear down."""

        return False


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
        self.assertIsNone(
            re.search(r"(?<!\\)`", prompt),
            "unescaped backticks in the heredoc execute shell commands",
        )
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

    def test_run_pilot_installs_relay_before_browser_runtime_and_cleans_startup_signal(
        self,
    ) -> None:
        """SignalRelay must wrap browser runtime lifecycle; cancelled start skips workload."""

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

        class FakeRuntime:
            @classmethod
            def create(cls, *args, **kwargs):
                events.append("construct")
                return cls()

            def __init__(self):
                self.capability_dir = Path("/tmp/fake-br-cap")
                self.mcp_url = "http://127.0.0.1:12345/mcp"

            def start(self):
                events.append("runtime-start")
                relay.cancelled = True
                relay.signum = signal.SIGTERM

            def stop(self):
                events.append("runtime-stop")

            def poll_failed(self):
                return False

        with (
            mock.patch.object(self.supervisor, "prepare_exp"),
            mock.patch.object(self.supervisor, "SignalRelay", return_value=relay),
            mock.patch.object(self.supervisor, "BrowserRuntimeJob", FakeRuntime),
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
        self.assertEqual(
            events[0:3],
            ["relay-enter", "construct", "runtime-start"],
        )
        self.assertIn("runtime-stop", events)
        self.assertEqual(events[-1], "relay-exit")
        supervised.assert_not_called()

    def test_agent_surface_uses_deployed_publish_tree_for_trusted_tools(self) -> None:
        publish_tree = Path("/authority/publish-tree")
        receipt = SimpleNamespace(publish_tree=publish_tree)
        relay = mock.MagicMock(cancelled=True, signum=signal.SIGTERM)
        relay.__enter__ = mock.Mock(return_value=relay)
        relay.__exit__ = mock.Mock(return_value=False)
        runtime = FakeBrowserRuntimeJob()

        class RuntimeFactory(FakeBrowserRuntimeJob):
            @classmethod
            def create(cls, *args, **kwargs):
                return runtime

        lease = SimpleNamespace(runtime=Path("/candidate-runtime"), release=mock.Mock())
        agent_supervisor = mock.MagicMock()
        agent_supervisor.candidate_root = Path("/candidate")
        agent_supervisor.agent_bootstrap_contract.return_value = {}
        agent_supervisor.cancellation_confirmed = True

        with (
            mock.patch.object(self.supervisor, "prepare_exp"),
            mock.patch.object(self.supervisor, "prepare_and_initialize_workspace"),
            mock.patch.object(self.supervisor, "SignalRelay", return_value=relay),
            mock.patch.object(self.supervisor, "BrowserRuntimeJob", RuntimeFactory),
            mock.patch.object(self.supervisor, "publish_tool_registry"),
            mock.patch.object(
                self.supervisor,
                "materialize_candidate_runtime",
                return_value=lease,
            ),
            mock.patch.object(
                self.supervisor,
                "resolve_deployed_authority",
                return_value=receipt,
            ) as resolve,
            mock.patch.object(
                self.supervisor,
                "WorkspaceSupervisor",
                return_value=agent_supervisor,
            ) as supervisor,
            mock.patch.object(self.supervisor, "write_agent_bootstrap"),
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
                [Path("/input.ply")],
                ["/fake/workload"],
                {**self.environ, "HOME": "/home/test"},
                agent_surface=True,
            )

        self.assertEqual(128 + signal.SIGTERM, status)
        resolve.assert_called_once_with(Path("/home/test"))
        self.assertEqual(
            publish_tree,
            supervisor.call_args.kwargs["trusted_tools_root"],
        )
        self.assertEqual(
            publish_tree / "skills/mesh-compare/scripts/packages/meshscope/src",
            supervisor.call_args.kwargs["step_zero_evidence_provider"].keywords[
                "meshscope_src"
            ],
        )
        self.assertEqual(
            publish_tree / "skills/mesh-compare/scripts/packages/meshshot/src",
            supervisor.call_args.kwargs["repair_evidence_provider"].keywords[
                "meshshot_src"
            ],
        )
        self.assertNotEqual(
            self.supervisor.REPO_ROOT,
            supervisor.call_args.kwargs["trusted_tools_root"],
        )

    def test_agent_surface_preparation_uses_deployed_authority_not_stale_overlay(
        self,
    ) -> None:
        """The real outer-preparation seam must use the resolved publish tree."""

        from scripts.pilot import trusted_tools

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_overlay = root / "source-overlay"
            authority = root / "publish-tree"
            authority_used = root / "authority-used"

            def seed_tool_roots(tree: Path) -> None:
                for relative in (
                    trusted_tools.CANONICAL_BUILD_RELATIVE,
                    trusted_tools.CADGEN_RUNTIME_RELATIVE / "src/cadgen",
                    trusted_tools.MESHSCOPE_RUNTIME_RELATIVE / "src/meshscope",
                    trusted_tools.MESHSHOT_RUNTIME_RELATIVE / "src/meshshot",
                ):
                    runtime = tree / relative
                    runtime.mkdir(parents=True)
                    (runtime / "runtime.py").write_text(
                        "# fixed test runtime\n", encoding="utf-8"
                    )

            seed_tool_roots(source_overlay)
            source_meshscope_src = (
                source_overlay / trusted_tools.MESHSCOPE_RUNTIME_RELATIVE / "src"
            )
            (source_meshscope_src / "meshscope/__init__.py").write_text(
                "\n", encoding="utf-8"
            )
            (source_meshscope_src / "meshscope/voxblame").mkdir()
            (source_meshscope_src / "meshscope/voxblame/__init__.py").write_text(
                "raise RuntimeError('stale source overlay imported')\n",
                encoding="utf-8",
            )
            source_meshshot_src = (
                source_overlay / trusted_tools.MESHSHOT_RUNTIME_RELATIVE / "src"
            )
            (source_meshshot_src / "meshshot/__init__.py").write_text(
                "raise RuntimeError('stale source overlay imported')\n",
                encoding="utf-8",
            )
            source_manifest = source_overlay / trusted_tools.MANIFEST_RELATIVE
            source_manifest.parent.mkdir(parents=True)
            source_manifest.write_bytes(trusted_tools.manifest_bytes(source_overlay))
            # This file is outside the recorded source inventory, reproducing
            # the non-deleting CVM overlay that caused the production failure.
            (source_overlay / trusted_tools.CANONICAL_BUILD_RELATIVE / "stale.py").write_text(
                "stale = True\n", encoding="utf-8"
            )

            seed_tool_roots(authority)
            meshscope_src = authority / trusted_tools.MESHSCOPE_RUNTIME_RELATIVE / "src"
            (meshscope_src / "meshscope/__init__.py").write_text(
                "\n", encoding="utf-8"
            )
            (meshscope_src / "meshscope/voxblame").mkdir()
            (meshscope_src / "meshscope/voxblame/__init__.py").write_text(
                "from pathlib import Path\n"
                "class _Result:\n"
                "    manifest = {'canonical_reference_sha256': 'a' * 64}\n"
                "def prepare_reference(_input, output):\n"
                f"    Path({str(authority_used)!r}).write_text('authority', encoding='utf-8')\n"
                "    Path(output).mkdir(parents=True, exist_ok=True)\n"
                "    return _Result()\n",
                encoding="utf-8",
            )
            meshshot_src = authority / trusted_tools.MESHSHOT_RUNTIME_RELATIVE / "src"
            (meshshot_src / "meshshot/__init__.py").write_text(
                "class _Profile:\n"
                "    profile = {'name': 'test-profile'}\n"
                "    sha256 = 'b' * 64\n"
                "def load_profile():\n"
                "    return _Profile()\n",
                encoding="utf-8",
            )
            authority_manifest = authority / trusted_tools.MANIFEST_RELATIVE
            authority_manifest.parent.mkdir(parents=True)
            authority_manifest.write_bytes(trusted_tools.manifest_bytes(authority))

            raw_input = root / "input.ply"
            raw_input.write_text("ply\n", encoding="utf-8")
            exp_dir = root / "outputs/group/exp"
            receipt = SimpleNamespace(publish_tree=authority)
            relay = mock.MagicMock(cancelled=False, signum=None)
            relay.__enter__ = mock.Mock(return_value=relay)
            relay.__exit__ = mock.Mock(return_value=False)
            lease = SimpleNamespace(runtime=root / "candidate-runtime", release=mock.Mock())
            agent_supervisor = mock.MagicMock()
            agent_supervisor.candidate_root = root / "candidate"
            agent_supervisor.agent_bootstrap_contract.return_value = {}
            agent_supervisor.cancellation_confirmed = True

            provider_modules = {
                name: module
                for name, module in sys.modules.items()
                if name.split(".", 1)[0] in {"meshscope", "meshshot"}
            }
            original_sys_path = sys.path[:]
            for name in provider_modules:
                sys.modules.pop(name, None)

            def restore_provider_imports() -> None:
                for name in tuple(sys.modules):
                    if name.split(".", 1)[0] in {"meshscope", "meshshot"}:
                        sys.modules.pop(name, None)
                sys.modules.update(provider_modules)
                sys.path[:] = original_sys_path

            self.addCleanup(restore_provider_imports)

            class RuntimeFactory(FakeBrowserRuntimeJob):
                @classmethod
                def create(cls, *args, **kwargs):
                    return cls()

                def start(self):
                    relay.cancelled = True
                    relay.signum = signal.SIGTERM

            with (
                mock.patch.object(self.supervisor, "REPO_ROOT", source_overlay),
                mock.patch.object(self.supervisor, "prepare_exp"),
                mock.patch.object(
                    self.supervisor,
                    "_workspace_status_available",
                    return_value=False,
                ),
                mock.patch.object(self.supervisor, "SignalRelay", return_value=relay),
                mock.patch.object(self.supervisor, "BrowserRuntimeJob", RuntimeFactory),
                mock.patch.object(self.supervisor, "publish_tool_registry"),
                mock.patch.object(
                    self.supervisor,
                    "materialize_candidate_runtime",
                    return_value=lease,
                ),
                mock.patch.object(
                    self.supervisor,
                    "WorkspaceSupervisor",
                    return_value=agent_supervisor,
                ),
                mock.patch.object(self.supervisor, "write_agent_bootstrap"),
                mock.patch.object(
                    self.supervisor,
                    "resolve_deployed_authority",
                    return_value=receipt,
                ) as resolve,
                mock.patch.object(
                    self.supervisor.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=0),
                ),
                mock.patch.object(
                    self.supervisor,
                    "finalize_pilot",
                    side_effect=lambda exp, status, env, **kwargs: status,
                ),
                mock.patch.object(
                    self.supervisor,
                    "validate_exp_dir",
                    return_value=exp_dir,
                ),
            ):
                status = self.supervisor.run_pilot(
                    exp_dir,
                    [raw_input],
                    ["/fake/workload"],
                    {**self.environ, "HOME": os.fspath(root / "home")},
                    agent_surface=True,
                )

            self.assertEqual(128 + signal.SIGTERM, status)
            resolve.assert_called_once_with(root / "home")
            self.assertEqual("authority", authority_used.read_text(encoding="utf-8"))

    def test_run_pilot_preflights_cad_render_before_paid_workload(self) -> None:
        events: list[str] = []

        class FakeRuntime(FakeBrowserRuntimeJob):
            @classmethod
            def create(cls, *args, **kwargs):
                events.append("runtime-create")
                return cls()

            def start(self):
                events.append("runtime-start")

            def preflight(self):
                events.append("cad-render-preflight")

            def preflight_mcp(self):
                events.append("viewer-mcp-preflight")

            def stop(self):
                events.append("runtime-stop")

        with (
            mock.patch.object(self.supervisor, "prepare_exp"),
            mock.patch.object(self.supervisor, "BrowserRuntimeJob", FakeRuntime),
            mock.patch.object(
                self.supervisor,
                "run_supervised",
                side_effect=lambda *args: events.append("paid-workload") or 0,
            ),
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

        self.assertEqual(status, 0)
        self.assertLess(events.index("cad-render-preflight"), events.index("paid-workload"))
        self.assertLess(events.index("viewer-mcp-preflight"), events.index("paid-workload"))
        self.assertEqual(events[-1], "runtime-stop")

    def test_cad_render_preflight_failure_skips_paid_workload(self) -> None:
        runtime = FakeBrowserRuntimeJob()

        class FailingRuntime(FakeBrowserRuntimeJob):
            @classmethod
            def create(cls, *args, **kwargs):
                return runtime

        runtime.preflight = mock.Mock(
            side_effect=self.supervisor.BrowserRuntimeError("fixed render unavailable")
        )
        with (
            mock.patch.object(self.supervisor, "prepare_exp"),
            mock.patch.object(self.supervisor, "BrowserRuntimeJob", FailingRuntime),
            mock.patch.object(self.supervisor, "run_supervised") as paid_workload,
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
        paid_workload.assert_not_called()
        self.assertTrue(runtime.stopped)

    def test_cancellation_during_cad_preflight_skips_viewer_preflight(self) -> None:
        relay = SimpleNamespace(cancelled=False, signum=None)
        runtime = FakeBrowserRuntimeJob()

        class RelayContext:
            def __enter__(self):
                return relay

            def __exit__(self, *args):
                return False

        class RuntimeFactory(FakeBrowserRuntimeJob):
            @classmethod
            def create(cls, *args, **kwargs):
                return runtime

        def cancel_during_preflight():
            relay.cancelled = True
            relay.signum = signal.SIGTERM

        runtime.preflight = mock.Mock(side_effect=cancel_during_preflight)
        runtime.preflight_mcp = mock.Mock()
        with (
            mock.patch.object(self.supervisor, "prepare_exp"),
            mock.patch.object(self.supervisor, "SignalRelay", return_value=RelayContext()),
            mock.patch.object(self.supervisor, "BrowserRuntimeJob", RuntimeFactory),
            mock.patch.object(self.supervisor, "run_supervised") as paid_workload,
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
        runtime.preflight_mcp.assert_not_called()
        paid_workload.assert_not_called()


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
        self.assertIn('MODEL_SELECTOR="${MODEL:-gpt-5.5}"', pilot)
        self.assertIn("runner.py", pilot)
        self.assertNotIn("sandbox-run.sh", pilot)
        self.assertNotIn("eval ", pilot)
        self.assertNotIn("SANDBOX_RUN", pilot)
        self.assertNotIn("CODEX_RUN", pilot)
        self.assertNotIn('$REPO_ROOT/gateway', pilot)
        self.assertIn('run --input "$PLY"', pilot)
        self.assertIn(
            'PYTHONPATH="$REPO_ROOT/packages/browser_runtime/src${PYTHONPATH:+:$PYTHONPATH}"',
            pilot,
        )
        self.assertNotIn("--skill", pilot)
        self.assertNotIn("PILOT_SKILLS", pilot)
        self.assertIn("build_bwrap_argv", runner)
        self.assertIn("LifecycleState", runner)
        self.assertNotIn('"--ro-bind",\n        "/",\n        "/"', runner)
        self.assertNotIn("list_skill_dirs", runner)
        self.assertIn("resolve_deployed_authority", runner)
        self.assertIn('subparsers.add_parser("clean")', runner)
        self.assertIn("--skip-git-repo-check", pilot)
        self.assertIn("PLUGIN_MODE=", pilot)
        self.assertIn("direct|e2e", pilot)
        self.assertIn("WORKLOAD+=(--disable plugins)", pilot)
        self.assertIn("run/plugin-mode.txt", pilot)
        self.assertNotIn("--disable\n    view_image", pilot)
        self.assertNotIn("Do not call \\`view_image\\`", pilot)
        self.assertIn("Use \\`view_image\\`", pilot)
        self.assertIn("setup/formal preview PNGs", pilot)
        self.assertIn("parent/child comparison", pilot)
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

    def test_toys4k_plugin_modes_change_only_plugin_discovery_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            pilot_root = repo / "scripts" / "pilot"
            model_root = repo / "models" / "toys4k"
            pilot_root.mkdir(parents=True)
            model_root.mkdir(parents=True)
            (model_root / "airplane.ply").write_text("ply\n", encoding="utf-8")
            pilot = pilot_root / "toys4k-pilot.sh"
            pilot.write_bytes((PILOT_ROOT / "toys4k-pilot.sh").read_bytes())
            pilot.chmod(0o755)
            (pilot_root / "runner.py").write_text(
                """import json, os, pathlib, sys
pathlib.Path(os.environ["PILOT_CAPTURE"]).write_text(json.dumps({
    "argv": sys.argv,
    "codex_home": os.environ.get("CODEX_HOME"),
}))
""",
                encoding="utf-8",
            )

            captures: dict[str, dict[str, object]] = {}
            for mode in ("direct", "e2e"):
                capture = Path(temp) / f"{mode}.json"
                env = {
                    **os.environ,
                    "HOME": os.fspath(Path(temp) / "home"),
                    "PILOT_CAPTURE": os.fspath(capture),
                    "PYTHON_BIN": sys.executable,
                    "CODEX_HOME": "/authority/job-private-codex-home",
                }
                result = subprocess.run(
                    [
                        os.fspath(pilot),
                        "airplane",
                        "20260805-170000-audit",
                        f"exp-{mode}",
                        mode,
                    ],
                    cwd=repo,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                captures[mode] = json.loads(capture.read_text(encoding="utf-8"))
                self.assertEqual(
                    captures[mode]["codex_home"],
                    "/authority/job-private-codex-home",
                )

            direct = captures["direct"]["argv"]
            e2e = captures["e2e"]["argv"]
            self.assertIsInstance(direct, list)
            self.assertIsInstance(e2e, list)
            assert isinstance(direct, list) and isinstance(e2e, list)
            self.assertIn("--disable", direct)
            self.assertEqual(direct[direct.index("--disable") + 1], "plugins")
            self.assertNotIn("--disable", e2e)
            self.assertEqual(direct[1:4], e2e[1:4])
            self.assertEqual(
                direct[5 : direct.index("--")],
                e2e[5 : e2e.index("--")],
            )
            gateway_index = direct.index("gateway/codex-tap-gpt56")
            self.assertEqual(direct[gateway_index + 1], "gpt-5.5")
            self.assertEqual(e2e[gateway_index + 1], "gpt-5.5")
            self.assertIn("$mesh-to-cad", direct[-1])
            self.assertNotIn("$mesh-to-cad", e2e[-1])

            for mode in ("direct", "e2e"):
                run_dir = (
                    repo
                    / "outputs"
                    / "20260805-170000-audit"
                    / f"exp-{mode}"
                    / "run"
                )
                self.assertEqual(
                    (run_dir / "plugin-mode.txt").read_text(encoding="utf-8"),
                    f"{mode}\n",
                )

    def test_toys4k_reconstruction_spec_defaults_on_and_supports_opt_out(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            pilot_root = repo / "scripts" / "pilot"
            model_root = repo / "models" / "toys4k"
            pilot_root.mkdir(parents=True)
            model_root.mkdir(parents=True)
            (model_root / "airplane.ply").write_text("ply\n", encoding="utf-8")
            pilot = pilot_root / "toys4k-pilot.sh"
            pilot.write_bytes((PILOT_ROOT / "toys4k-pilot.sh").read_bytes())
            pilot.chmod(0o755)
            (pilot_root / "runner.py").write_text(
                """import json, os, pathlib, sys
pathlib.Path(os.environ["PILOT_CAPTURE"]).write_text(json.dumps({
    "argv": sys.argv,
}))
""",
                encoding="utf-8",
            )

            for mode in ("direct", "e2e"):
                for spec_option in (
                    None,
                    "--no-reconstruction-spec",
                    "--reconstruction-spec",
                ):
                    with self.subTest(mode=mode, spec_option=spec_option):
                        label = (
                            "default"
                            if spec_option is None
                            else "off"
                            if spec_option == "--no-reconstruction-spec"
                            else "on"
                        )
                        exp = f"exp-{mode}-{label}"
                        capture = Path(temp) / f"{mode}-{label}.json"
                        env = {
                            **os.environ,
                            "HOME": os.fspath(Path(temp) / "home"),
                            "PILOT_CAPTURE": os.fspath(capture),
                            "PYTHON_BIN": sys.executable,
                            "CODEX_HOME": "/authority/job-private-codex-home",
                        }
                        args = [
                            os.fspath(pilot),
                            "airplane",
                            "20260805-170000-audit",
                            exp,
                            mode,
                        ]
                        if spec_option:
                            args.append(spec_option)
                        result = subprocess.run(
                            args,
                            cwd=repo,
                            env=env,
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        capture_data = json.loads(capture.read_text(encoding="utf-8"))
                        workload = capture_data["argv"]
                        self.assertEqual(
                            (repo / "outputs" / "20260805-170000-audit" / exp / "run" / "plugin-mode.txt").read_text(
                                encoding="utf-8"
                            ),
                            f"{mode}\n",
                        )
                        prompt = (
                            repo
                            / "outputs"
                            / "20260805-170000-audit"
                            / exp
                            / "run"
                            / "prompt.txt"
                        ).read_text(encoding="utf-8")
                        if spec_option != "--no-reconstruction-spec":
                            self.assertIn("Reconstruction Spec", prompt)
                            self.assertIn("enabled for this pilot", prompt)
                            self.assertIn("create and maintain", prompt)
                            self.assertIn(
                                f"outputs/20260805-170000-audit/{exp}/run/reconstruction-spec.json",
                                prompt,
                            )
                            self.assertNotIn(
                                "Reconstruction Spec is disabled", prompt
                            )
                            self.assertNotIn(
                                "Do not create, read, or update", prompt
                            )
                        else:
                            self.assertIn(
                                "Reconstruction Spec is disabled for this run",
                                prompt,
                            )
                            self.assertIn(
                                "Do not create, read, or update",
                                prompt,
                            )
                            self.assertNotIn(
                                "Reconstruction Spec is enabled", prompt
                            )
                            self.assertNotIn("create and maintain", prompt)
                        self.assertIn("Use `view_image`", prompt)
                        self.assertIn("setup/formal preview PNGs", prompt)
                        self.assertIn("parent/child comparison", prompt)
                        if mode == "direct":
                            self.assertIn("--disable", workload)
                            self.assertEqual(
                                workload[workload.index("--disable") + 1],
                                "plugins",
                            )
                            self.assertIn("$mesh-to-cad", workload[-1])
                        else:
                            self.assertNotIn("--disable", workload)
                            self.assertNotIn("$mesh-to-cad", workload[-1])

            for flags in (
                ("--reconstruction-spec", "--reconstruction-spec"),
                ("--no-reconstruction-spec", "--no-reconstruction-spec"),
                ("--reconstruction-spec", "--no-reconstruction-spec"),
            ):
                with self.subTest(flags=flags):
                    result = subprocess.run(
                        [
                            os.fspath(pilot),
                            "airplane",
                            "20260805-170000-audit",
                            "exp-conflicting-flags",
                            "direct",
                            *flags,
                        ],
                        cwd=repo,
                        env={
                            **os.environ,
                            "HOME": os.fspath(Path(temp) / "home"),
                            "PYTHON_BIN": sys.executable,
                        },
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 2)

    def test_toys4k_view_image_treatment_control_and_reconstruction_coexist(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            pilot_root = repo / "scripts" / "pilot"
            model_root = repo / "models" / "toys4k"
            pilot_root.mkdir(parents=True)
            model_root.mkdir(parents=True)
            (model_root / "airplane.ply").write_text("ply\n", encoding="utf-8")
            pilot = pilot_root / "toys4k-pilot.sh"
            pilot.write_bytes((PILOT_ROOT / "toys4k-pilot.sh").read_bytes())
            pilot.chmod(0o755)
            (pilot_root / "runner.py").write_text(
                """import json, os, pathlib, sys
pathlib.Path(os.environ["PILOT_CAPTURE"]).write_text(json.dumps({
    "argv": sys.argv,
}))
""",
                encoding="utf-8",
            )

            captures: dict[tuple[str, str], dict[str, object]] = {}
            prompts: dict[tuple[str, str], str] = {}
            for mode in ("direct", "e2e"):
                for view_option, label in (
                    (None, "default"),
                    ("--view-image", "treatment"),
                    ("--no-view-image", "control"),
                ):
                    with self.subTest(mode=mode, view_option=view_option):
                        exp = f"exp-{mode}-{label}"
                        capture = Path(temp) / f"{mode}-{label}.json"
                        env = {
                            **os.environ,
                            "HOME": os.fspath(Path(temp) / "home"),
                            "PILOT_CAPTURE": os.fspath(capture),
                            "PYTHON_BIN": sys.executable,
                            "CODEX_HOME": "/authority/job-private-codex-home",
                        }
                        args = [
                            os.fspath(pilot),
                            "airplane",
                            "20260805-170000-audit",
                            exp,
                            mode,
                        ]
                        if view_option:
                            args.append(view_option)
                        args.append("--reconstruction-spec")
                        result = subprocess.run(
                            args,
                            cwd=repo,
                            env=env,
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        captures[(mode, label)] = json.loads(
                            capture.read_text(encoding="utf-8")
                        )
                        workload = captures[(mode, label)]["argv"]
                        assert isinstance(workload, list)
                        prompt = (
                            repo
                            / "outputs"
                            / "20260805-170000-audit"
                            / exp
                            / "run"
                            / "prompt.txt"
                        ).read_text(encoding="utf-8")
                        prompts[(mode, label)] = prompt
                        self.assertIn("Reconstruction Spec", prompt)
                        if label == "control":
                            self.assertIn("`view_image` is disabled", prompt)
                            self.assertIn("do not call `view_image`", prompt)
                        else:
                            self.assertIn("Use `view_image`", prompt)
                            self.assertNotIn("`view_image` is disabled", prompt)
                        for index, value in enumerate(workload):
                            if value == "--disable":
                                self.assertNotEqual(
                                    workload[index + 1], "view_image"
                                )

            def pilot_workload(argv: list[object]) -> list[object]:
                return argv[argv.index("--") + 1 :]

            def normalized_workload_surface(argv: list[object]) -> list[object]:
                values = pilot_workload(argv)
                normalized = [
                    re.sub(
                        r"outputs/20260805-170000-audit/exp-[^/\s]+",
                        "outputs/GROUP/EXP",
                        value,
                    )
                    if isinstance(value, str)
                    else value
                    for value in values
                ]
                return normalized[:-1]

            def common_prompt_parts(prompt: str) -> tuple[str, str]:
                normalized = re.sub(
                    r"outputs/20260805-170000-audit/exp-[^/\s]+",
                    "outputs/GROUP/EXP",
                    prompt,
                )
                before, after = normalized.split("View-image ", 1)
                _, suffix = after.split("\n\nStay under", 1)
                return before, suffix

            for mode in ("direct", "e2e"):
                surfaces = [
                    normalized_workload_surface(captures[(mode, label)]["argv"])
                    for label in ("default", "treatment", "control")
                ]
                self.assertEqual(surfaces[0], surfaces[1])
                self.assertEqual(surfaces[1], surfaces[2])
                self.assertEqual(
                    common_prompt_parts(prompts[(mode, "default")]),
                    common_prompt_parts(prompts[(mode, "treatment")]),
                )
                self.assertEqual(
                    common_prompt_parts(prompts[(mode, "treatment")]),
                    common_prompt_parts(prompts[(mode, "control")]),
                )

            off_spec_argv: dict[str, list[object]] = {}
            off_spec_prompts: dict[str, str] = {}
            for view_option, label in (
                ("--view-image", "treatment"),
                ("--no-view-image", "control"),
            ):
                exp = f"exp-direct-off-{label}"
                capture = Path(temp) / f"direct-off-{label}.json"
                result = subprocess.run(
                    [
                        os.fspath(pilot),
                        "airplane",
                        "20260805-170000-audit",
                        exp,
                        "direct",
                        view_option,
                        "--no-reconstruction-spec",
                    ],
                    cwd=repo,
                    env={
                        **os.environ,
                        "HOME": os.fspath(Path(temp) / "home"),
                        "PILOT_CAPTURE": os.fspath(capture),
                        "PYTHON_BIN": sys.executable,
                    },
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                captured = json.loads(capture.read_text(encoding="utf-8"))
                workload = captured["argv"]
                assert isinstance(workload, list)
                off_spec_argv[label] = workload
                prompt = (
                    repo
                    / "outputs"
                    / "20260805-170000-audit"
                    / exp
                    / "run"
                    / "prompt.txt"
                ).read_text(encoding="utf-8")
                off_spec_prompts[label] = prompt
                self.assertIn("Reconstruction Spec is disabled", prompt)

            self.assertEqual(
                normalized_workload_surface(off_spec_argv["treatment"]),
                normalized_workload_surface(off_spec_argv["control"]),
            )
            self.assertEqual(
                common_prompt_parts(off_spec_prompts["treatment"]),
                common_prompt_parts(off_spec_prompts["control"]),
            )

            for flags in (
                ("--view-image", "--view-image"),
                ("--no-view-image", "--no-view-image"),
                ("--view-image", "--no-view-image"),
            ):
                with self.subTest(flags=flags):
                    result = subprocess.run(
                        [
                            os.fspath(pilot),
                            "airplane",
                            "20260805-170000-audit",
                            "exp-conflicting-view-flags",
                            "direct",
                            *flags,
                        ],
                        cwd=repo,
                        env={
                            **os.environ,
                            "HOME": os.fspath(Path(temp) / "home"),
                            "PYTHON_BIN": sys.executable,
                        },
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 2)

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

    def test_gateway_accepts_exact_gpt_55_model(self) -> None:
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
                    "PATH": f"{temp}:{env.get('PATH', '')}",
                    "VENUS_TOKEN": "token",
                    "CLAUDE_TAP_URL": "http://127.0.0.1:18888/v1",
                }
            )
            result = subprocess.run(
                [
                    str(REPO_ROOT / "gateway" / "codex-tap-gpt56"),
                    "gpt-5.5",
                    "exec",
                    "prompt",
                ],
                env=env,
                check=False,
            )
            argv = capture.read_text(encoding="utf-8")
        self.assertEqual(result.returncode, 0)
        self.assertIn("-m\ngpt-5.5\n", argv)

    def test_gateway_rejects_unlisted_model(self) -> None:
        env = dict(os.environ)
        env.update(
            {
                "VENUS_TOKEN": "token",
                "CLAUDE_TAP_URL": "http://127.0.0.1:18888/v1",
            }
        )
        result = subprocess.run(
            [
                str(REPO_ROOT / "gateway" / "codex-tap-gpt56"),
                "gpt-unknown",
                "exec",
            ],
            env=env,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(result.returncode, 2)

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
            (exp_dir / "run/.codex-home").mkdir(parents=True)
            (exp_dir / "run/.codex-home/state.db").write_bytes(b"private")
            (exp_dir / "run/.plugin-publish-tree/skills/cad").mkdir(parents=True)
            (exp_dir / "run/.plugin-publish-tree/skills/cad/SKILL.md").write_text(
                "private snapshot\n", encoding="utf-8"
            )
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
        self.assertNotIn("run/.codex-home/state.db", paths)

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
            (exp_dir / "run/.codex-home").mkdir(parents=True)
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
                    (exp_dir / "run/.codex-home").mkdir(parents=True)
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
                        / "run/.codex-home/sessions/a/b/c/rollout-test.jsonl"
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
                / "run/.codex-home"
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
            upper_exists = (exp_dir / "run/.codex-home").exists()
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
                / "run/.codex-home"
                / "sessions"
                / "a"
                / "b"
                / "c"
                / "rollout-test.jsonl"
            )
            rollout.parent.mkdir(parents=True)
            rollout.write_text("{}\n", encoding="utf-8")
            status = load_runner().finalize_pilot(exp_dir, 0, {})
            upper_exists = (exp_dir / "run/.codex-home").exists()
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
                / "run/.codex-home"
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
            upper_exists = (exp_dir / "run/.codex-home").exists()
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
                / "run/.codex-home"
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
            upper_exists = (exp_dir / "run/.codex-home").exists()
        self.assertEqual(status, 0)
        self.assertTrue(upper_exists)

    def test_build_bwrap_argv_exposes_only_task_and_installed_repo_files(
        self,
    ) -> None:
        from tests.python.support.authority_fixtures import build_authority

        with tempfile.TemporaryDirectory() as temp:
            repo_root = (Path(temp) / "repo").resolve()
            exp_dir = repo_root / "outputs" / "group" / "exp with spaces"
            outside_skill = Path(temp) / "outside-skill"
            input_path = repo_root / "models" / "toys4k" / "input.ply"
            other_input = repo_root / "models" / "toys4k" / "other.ply"
            other_exp = repo_root / "outputs" / "group" / "other-exp"
            gateway = repo_root / "gateway" / "codex-tap-gpt56"
            venv = repo_root / ".venv"
            host_home = Path(temp) / "host-home"
            playwright = host_home / ".cache" / "ms-playwright"
            capability_dir = (
                exp_dir / "run" / "browser-runtime" / "0123456789abcdef"
            )
            exp_dir.mkdir(parents=True)
            outside_skill.mkdir()
            capability_dir.mkdir(parents=True)
            input_path.parent.mkdir(parents=True)
            gateway.parent.mkdir(parents=True)
            venv.mkdir()
            playwright.mkdir(parents=True)
            host_home.mkdir(parents=True, exist_ok=True)
            (outside_skill / "SKILL.md").write_text(
                "# unrelated\n",
                encoding="utf-8",
            )
            input_path.write_text("ply\n", encoding="utf-8")
            other_input.write_text("ply\n", encoding="utf-8")
            gateway.write_text("#!/bin/sh\n", encoding="utf-8")

            # Seed a real plugin-deployment authority under ``host_home`` — the
            # fixture builds a symlink-free publish tree carrying every
            # critical-runtime probe and a byte-identical installed cache, so
            # ``resolve_deployed_authority``'s manifest recompute + probe check
            # pass end-to-end without mocks.
            runner = load_runner()
            fixture = build_authority(host_home, dedupe_token="bwrap-argv")
            expected_skill_dirs = list(
                sorted(
                    (fixture.installed_path / "skills").iterdir(),
                    key=lambda p: p.name,
                )
            )
            self.assertTrue(expected_skill_dirs, "fixture must plant at least one skill")

            environ = {
                "HOME": str(host_home),
                "PATH": "/fake/bin",
                "VENUS_TOKEN": "super-secret-token",
            }
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
                    browser_capability_dir=capability_dir,
                    browser_mcp_url="http://127.0.0.1:12345/mcp",
                )
        triples = [argv[index : index + 3] for index in range(len(argv) - 2)]
        self.assertIn(Path("/etc/crypto-policies"), runner.SYSTEM_RO_PATHS)
        # After the browser-runtime refactor the sandbox execs the workload
        # directly; there is no nested gate zipapp wrapping the Agent.
        self.assertEqual(
            argv[-3:],
            [
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
                str(capability_dir.resolve()),
                runner.SANDBOX_MOUNT_ROOT,
            ],
            triples,
        )
        self.assertIn(
            [
                "--ro-bind",
                str(capability_dir.resolve()),
                (
                    "/workspace/repo/outputs/group/exp with spaces/"
                    "run/browser-runtime/0123456789abcdef"
                ),
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
        # The job-private codex home is bound writable at SANDBOX_CODEX_HOME as
        # a whole tree — the deep copy under exp_dir/run/.codex-home holds the
        # per-job marketplace source rewrite and the venus provider block.
        job_codex_home = (exp_dir / "run" / ".codex-home").resolve()
        self.assertIn(
            [
                "--bind",
                str(job_codex_home),
                str(runner.SANDBOX_CODEX_HOME),
            ],
            triples,
        )
        # A verified job-private publish-tree snapshot is bound read-only at
        # the fixed sandbox marketplace source path.
        job_publish_tree = exp_dir / "run" / ".plugin-publish-tree"
        self.assertIn(
            [
                "--ro-bind",
                str(job_publish_tree),
                str(runner.SANDBOX_PUBLISH_TREE),
            ],
            triples,
        )
        # Each installed skill directory is bound read-only at
        # SANDBOX_REPO_ROOT/skills/<name>. The legacy SANDBOX_CODEX_HOME/skills
        # mount has been dropped now that the whole codex home mounts in.
        installed_relative = fixture.installed_path.relative_to(fixture.codex_home)
        for skill_dir in expected_skill_dirs:
            job_skill_dir = job_codex_home / installed_relative / "skills" / skill_dir.name
            self.assertIn(
                [
                    "--ro-bind",
                    str(job_skill_dir),
                    f"/workspace/repo/skills/{skill_dir.name}",
                ],
                triples,
            )
            self.assertNotIn(
                [
                    "--ro-bind",
                    str(skill_dir),
                    f"/home/pilot/.codex/skills/{skill_dir.name}",
                ],
                triples,
            )
        # /opt is created inside the sandbox so the ro-bind of the publish tree
        # at /opt/text-to-cad-publish-tree has a mount point to land on.
        opt_dir_indexes = [
            index for index, token in enumerate(argv) if token == "--dir"
        ]
        opt_dir_targets = {argv[index + 1] for index in opt_dir_indexes}
        self.assertIn("/opt", opt_dir_targets)
        self.assertNotIn("--overlay-src", argv)
        self.assertEqual(argv[argv.index("--chdir") + 1], "/workspace/repo")

        child_env = runner.build_sandbox_environment(
            environ,
            "http://127.0.0.1:18888/v1",
        )
        self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", child_env)
        self.assertNotIn("MESHSHOT_BROWSER_AUTHORITY_FILE", child_env)

    def test_preflight_failure_skips_rollout_contract_and_writes_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo_root = Path(temp) / "repo"
            exp_dir = repo_root / "outputs/group/exp"
            runner = load_runner()

            with (
                mock.patch.object(runner, "REPO_ROOT", repo_root),
                mock.patch.object(
                    runner,
                    "BrowserRuntimeJob",
                    FakeBrowserRuntimeJob,
                ),
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
                / "run/.codex-home"
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
            (exp_dir / "run/.codex-home").mkdir(parents=True)
            (exp_dir / "run/.plugin-publish-tree").mkdir(parents=True)
            runner = load_runner()
            with mock.patch.object(runner, "REPO_ROOT", repo_root):
                first = runner.main(["clean", str(exp_dir)])
                second = runner.main(["clean", str(exp_dir)])
        self.assertEqual((first, second), (0, 0))

    def test_clean_rejects_path_outside_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo_root = Path(temp) / "repo"
            outside = repo_root / "do-not-delete"
            (outside / "run/.codex-home").mkdir(parents=True)
            runner = load_runner()
            with mock.patch.object(runner, "REPO_ROOT", repo_root):
                status = runner.main(["clean", str(outside)])
            upper_exists = (outside / "run/.codex-home").exists()
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
