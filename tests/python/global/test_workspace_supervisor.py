"""Adversarial W4 tests for the trusted Agent Surface supervisor seam."""

from __future__ import annotations

import json
import hashlib
import importlib
import importlib.util
import inspect
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import socket

from scripts.pilot.workspace_supervisor import (
    SupervisorError,
    WorkspaceSupervisor,
)
class _Reference:
    def __init__(self, reference_id: str, _path: Path) -> None:
        self.reference_id = reference_id

    def handle(self, request: dict) -> dict:
        if request["method"] == "summary":
            observation = {
                "schema": "meshscope.reference-summary/1",
                "coordinate_contract": "trellis2_canonical/1",
                "stats": {
                    "vertices": 8,
                    "faces": 12,
                    "edges": 18,
                    "bounds": {"min": [-.5] * 3, "max": [.5] * 3, "size": [1.0] * 3},
                    "surface_area": 6.0,
                    "volume": 1.0,
                },
                "quality": {
                    "watertight": True,
                    "volume_valid": True,
                    "degenerate_faces": 0,
                    "euler_number": 2,
                },
                "canonical_frame": {
                    "center": [0.0] * 3,
                    "status": "ambiguous",
                    "pca_axes": None,
                    "eigenvalues": [.25] * 3,
                },
            }
        else:
            observation = {
                "schema": "meshscope.reference-components/1",
                "limit": request["args"].get("limit", 32),
                "total": 0,
                "returned": 0,
                "omitted": 0,
                "components": [],
            }
        return {
            "schema": "meshscope.reference-response/1",
            "reference_id": self.reference_id,
            "method": request["method"],
            "observation": observation,
        }


class _Workspace:
    def __init__(self, root: Path, reference_path: Path | None = None) -> None:
        self.root = root.resolve()
        self.reference_path = (
            reference_path.resolve() if reference_path is not None else None
        )
        self.completed_cycles = 0
        self.total_attempts = 0
        self.tool_failures = 0
        self.remaining_attempts = 3
        self.remaining_tool_failures = 2
        self.final_delivery_present = False
        self.finalize_calls = 0
        self.published: list[dict] = []

    def read_canonical_reference_binding(self, _workspace: Path) -> dict:
        if self.reference_path is None:
            raise AssertionError("reference binding requested without a bound reference")
        digest = hashlib.sha256(self.reference_path.read_bytes()).hexdigest()
        return {
            "path": self.reference_path,
            "reference_ply_sha256": digest,
            "canonical_reference_sha256": "a" * 64,
        }

    def workspace_status(self, _workspace: Path) -> dict:
        return {
            "completed_cycles": self.completed_cycles,
            "next_intended_step": 0 if not self.completed_cycles else self.completed_cycles + 1,
            "total_attempts": self.total_attempts,
            "tool_failures": self.tool_failures,
            "head_steps": [0] if self.completed_cycles else [],
            "final_delivery_present": self.final_delivery_present,
            "remaining_attempts": self.remaining_attempts,
            "remaining_tool_failures": self.remaining_tool_failures,
        }

    def workspace_initialized(self, _workspace: Path) -> bool:
        return True

    def candidate_staging_path(self, _workspace: Path, attempt: int) -> Path:
        target = self.root / "work" / "attempts" / f"{attempt:06d}" / "candidate"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def ingest_candidate(self, _workspace: Path, attempt: int, copy_tree) -> Path:
        target = self.candidate_staging_path(_workspace, attempt)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        copy_tree(target)
        return target

    def publish_step_zero_from_agent(self, _workspace: Path, **kwargs) -> dict:
        self.published.append(kwargs)
        return {"step": 0}

    def publish_cycle_from_agent(self, _workspace: Path, **kwargs) -> dict:
        self.completed_cycles += 1
        self.published.append(kwargs)
        return {"step": {"step": self.completed_cycles}, "cycle": self.completed_cycles}

    def finalization_staging_path(self, _workspace: Path) -> Path:
        target = self.root / "work" / "agent-finalization"
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def reset_finalization_staging(self, _workspace: Path) -> Path:
        target = self.finalization_staging_path(_workspace)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir()
        return target

    def discard_finalization_staging(self, _workspace: Path) -> None:
        target = self.finalization_staging_path(_workspace)
        if target.exists():
            shutil.rmtree(target)

    def begin_attempt(self, _workspace: Path, _plan: Path, **kwargs) -> dict:
        self.total_attempts += 1
        attempt = self.total_attempts
        (self.root / "work" / "attempts" / f"{attempt:06d}").mkdir(
            parents=True, exist_ok=False
        )
        return {"attempt": attempt, "intended_step": kwargs["intended_step"]}

    def publish_step_zero(self, _workspace: Path, **kwargs) -> dict:
        self.published.append(kwargs)
        return {"step": 0}

    def publish_cycle(self, _workspace: Path, **kwargs) -> dict:
        self.completed_cycles += 1
        self.published.append(kwargs)
        return {"step": {"step": self.completed_cycles}, "cycle": self.completed_cycles}

    def finalize_workspace(self, _workspace: Path, **kwargs) -> dict:
        self.finalize_calls += 1
        return {"identity_sha256": "f" * 64, "graph": {"final_delivery": {}}}

    def finalize_agent_submission(self, _workspace: Path, **kwargs) -> dict:
        return self.finalize_workspace(_workspace, **kwargs)

    def run_attempt_command(self, *_args, **_kwargs):
        raise AssertionError("candidate execution must use the supervisor operation port")


class WorkspaceSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace_root = self.root / "workspace"
        self.workspace_root.mkdir()
        (self.workspace_root / "input").mkdir()
        (self.workspace_root / "input/reference.ply").write_bytes(
            b"not parsed by injected reference"
        )
        self.workspace = _Workspace(
            self.workspace_root,
            reference_path=self.workspace_root / "input/reference.ply",
        )
        self.sup = WorkspaceSupervisor(
            self.workspace_root,
            bind_reference=True,
            candidate_root=self.root / "candidate-a",
            staging_dir=self.root / "staging",
            workspace_api=self.workspace,
            reference_factory=_Reference,
            rebuild_entrypoint=self.root / "rebuild",
            geometry_entrypoint=self.root / "geometry",
            tool_registry=self.root / "registry",
        )

    def tearDown(self) -> None:
        try:
            self.sup.close()
        finally:
            self.temp.cleanup()

    def _start(self) -> tuple[str, str]:
        plan = self.sup.candidate_root / "plan.json"
        plan.write_text("{}", encoding="utf-8")
        plan_handle = self.sup.register_plan(plan)
        result = self.sup.start_attempt(self.sup.workspace_handle, plan_handle, None)
        return result["attempt_handle"], result["candidate_handle"]

    def test_handles_are_run_private_and_one_shot_operations_reject_replay(self) -> None:
        other = WorkspaceSupervisor(
            self.workspace_root,
            candidate_root=self.root / "candidate-b",
            staging_dir=self.root / "staging-b",
            workspace_api=self.workspace,
        )
        try:
            self.assertNotEqual(self.sup.workspace_handle, other.workspace_handle)
            with self.assertRaises(SupervisorError):
                other.workspace_status(self.sup.workspace_handle)
        finally:
            other.close()

        attempt, candidate = self._start()
        operation = self.sup.register_operation(
            ["/bin/echo", "candidate-only"], attempt_handle=attempt
        )
        observed: dict = {}

        def run(argv, **kwargs):
            observed["args"] = argv
            observed.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, b"ok", b"")

        self.sup._command_runner = run
        first = self.sup.run_candidate_tool(
            self.sup.workspace_handle, attempt, candidate, operation
        )
        self.assertEqual("completed", first["state"])
        with self.assertRaises(SupervisorError):
            self.sup.run_candidate_tool(
                self.sup.workspace_handle, attempt, candidate, operation
            )
        self.assertEqual(self.sup.candidate_root / "attempt-000001", observed["cwd"])
        self.assertNotIn("VENUS_TOKEN", observed["env"])
        self.assertEqual(["/bin/echo", "candidate-only"], observed["args"])

    def test_status_uses_current_step_budget_and_not_final_path_presence(self) -> None:
        self.workspace.total_attempts = 8
        self.workspace.completed_cycles = 1
        self.workspace.remaining_attempts = 3
        (self.workspace_root / "final").mkdir()
        (self.workspace_root / "final" / "manifest.json").write_text(
            "{}", encoding="utf-8"
        )
        result = self.sup.workspace_status(self.sup.workspace_handle)
        self.assertEqual("preterminal", result["state"])
        self.assertEqual(3, result["budgets"]["remaining_attempts"])
        self.workspace.final_delivery_present = True
        self.assertEqual(
            "terminal",
            self.sup.workspace_status(self.sup.workspace_handle)["state"],
        )

    def test_supervisor_source_contains_no_workspace_internal_path_policy(self) -> None:
        source = inspect.getsource(WorkspaceSupervisor)
        for literal in ("work/attempts", "agent-finalization", "final/manifest.json"):
            self.assertNotIn(literal, source)
        self.assertIn("publish_step_zero_from_agent", source)
        self.assertIn("finalize_agent_submission", source)

    def test_bootstrap_preallocates_three_attempts_for_all_six_steps(self) -> None:
        contract = self.sup.agent_bootstrap_contract()
        self.assertEqual(18, contract["attempt_budget"]["maximum_attempts"])
        self.assertNotIn("attempts", contract)
        plan = self.sup.candidate_root / "dynamic-plan.json"
        plan.write_text("{}", encoding="utf-8")
        started = self.sup.start_attempt(
            self.sup.workspace_handle, self.sup.register_plan(plan), None
        )
        capability = started["capability_bundle_handle"]
        self.sup.registry.resolve(capability, "attempt_capabilities", attempt_id=1)
        with self.assertRaises(SupervisorError):
            self.sup.registry.resolve(
                capability, "attempt_capabilities", attempt_id=2
            )

    def test_dynamic_capabilities_bind_actual_attempt_seven_and_eighteen(self) -> None:
        plan = self.sup.candidate_root / "dynamic-plan.json"
        plan.write_text("{}", encoding="utf-8")
        plan_handle = self.sup.register_plan(plan)
        starts = []
        for _ in range(18):
            starts.append(
                self.sup.start_attempt(
                    self.sup.workspace_handle, plan_handle, None
                )
            )
        self.assertEqual(18, len(starts))
        bundle_seven = starts[6]["capability_bundle_handle"]
        bundle_eighteen = starts[17]["capability_bundle_handle"]
        self.sup._command_runner = lambda argv, **kwargs: __import__(
            "subprocess"
        ).CompletedProcess(argv, 0, b"", b"")
        self.assertEqual(
            "completed",
            self.sup.run_candidate_tool(
                self.sup.workspace_handle,
                starts[6]["attempt_handle"],
                starts[6]["candidate_handle"],
                bundle_seven,
            )["state"],
        )
        with self.assertRaises(SupervisorError):
            self.sup.run_candidate_tool(
                self.sup.workspace_handle,
                starts[7]["attempt_handle"],
                starts[7]["candidate_handle"],
                bundle_seven,
            )
        self.assertIsNotNone(
            self.sup.registry.resolve(
                bundle_eighteen,
                "attempt_capabilities",
                attempt_id=18,
            )
        )

    def test_traversal_and_symlink_candidate_paths_fail_closed(self) -> None:
        with self.assertRaises(SupervisorError):
            self.sup.register_candidate_path(self.sup.candidate_root / "../outside")
        link = self.sup.candidate_root / "link"
        link.symlink_to(self.root)
        with self.assertRaises(SupervisorError):
            self.sup.register_candidate_path(link)
        with self.assertRaises(SupervisorError):
            WorkspaceSupervisor(
                self.workspace_root,
                candidate_root=self.workspace_root / "candidate",
                staging_dir=self.root / "staging-inside",
                workspace_api=self.workspace,
            )

    def test_candidate_copy_rejects_deterministic_swap_before_open(self) -> None:
        from scripts.pilot import workspace_supervisor as module

        source = self.sup.candidate_root / "race.txt"
        outside = self.root / "outside.txt"
        source.write_text("candidate", encoding="utf-8")
        outside.write_text("authority", encoding="utf-8")
        destination = self.root / "copied.txt"
        original_open = module.os.open

        def racing_open(path, flags, *args, **kwargs):
            if Path(path) == source:
                source.unlink()
                source.symlink_to(outside)
            return original_open(path, flags, *args, **kwargs)

        with mock.patch.object(module.os, "open", side_effect=racing_open):
            with self.assertRaises(SupervisorError):
                module._copy_candidate_file(source, destination)
        self.assertFalse(destination.exists())

    def test_candidate_copy_rejects_fifo_and_socket_without_blocking(self) -> None:
        from scripts.pilot import workspace_supervisor as module

        fifo = self.sup.candidate_root / "candidate.fifo"
        os.mkfifo(fifo)
        with self.assertRaises(SupervisorError):
            module._copy_candidate_file(fifo, self.root / "fifo-copy")
        socket_path = self.sup.candidate_root / "candidate.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(os.fspath(socket_path))
        try:
            with self.assertRaises(SupervisorError):
                module._copy_candidate_file(socket_path, self.root / "socket-copy")
        finally:
            server.close()
        from scripts.pilot import workspace_supervisor as supervisor_module

        # The owning Workspace facade repeats the same no-follow tree policy.
        workspace_module = supervisor_module._load_workspace_api()
        nested = self.root / "nested-source"
        (nested / "child").mkdir(parents=True)
        os.mkfifo(nested / "child" / "nested.fifo")
        with self.assertRaises(workspace_module.WorkspaceError):
            workspace_module._copy_agent_tree(nested, self.root / "nested-target")

    def test_candidate_copy_rejects_hardlink_and_equal_size_in_place_mutation(self) -> None:
        from scripts.pilot import workspace_supervisor as module

        source = self.sup.candidate_root / "hardlink.txt"
        outside = self.root / "outside-hardlink.txt"
        outside.write_bytes(b"same-size")
        os.link(outside, source)
        with self.assertRaises(SupervisorError):
            module._copy_candidate_file(source, self.root / "hardlink-copy")
        source.unlink()

        source.write_bytes(b"original123456")
        original_read = module.os.read
        mutated = False

        def mutate_after_first_read(fd, size):
            nonlocal mutated
            chunk = original_read(fd, size)
            if chunk and not mutated:
                mutated = True
                source.write_bytes(b"rewriteD123456")
            return chunk

        with mock.patch.object(module.os, "read", side_effect=mutate_after_first_read):
            with self.assertRaises(SupervisorError):
                module._copy_candidate_file(source, self.root / "mutation-copy")

        source.unlink()
        source.write_bytes(b"hardlink-race")
        linked = self.root / "mid-copy-link"
        original_read = module.os.read
        linked_once = False

        def link_during_copy(fd, size):
            nonlocal linked_once
            chunk = original_read(fd, size)
            if chunk and not linked_once:
                linked_once = True
                os.link(source, linked)
            return chunk

        with mock.patch.object(module.os, "read", side_effect=link_during_copy):
            with self.assertRaises(SupervisorError):
                module._copy_candidate_file(source, self.root / "mid-copy-link-result")
        linked.unlink(missing_ok=True)

    def test_candidate_copy_rejects_growing_source_before_extra_write(self) -> None:
        from scripts.pilot import workspace_supervisor as module

        source = self.sup.candidate_root / "growing.txt"
        source.write_bytes(b"1234")
        destination = self.root / "growing-copy.txt"
        original_read = module.os.read
        grew = False

        def grow_after_open(fd, size):
            nonlocal grew
            chunk = original_read(fd, size)
            if chunk and not grew:
                grew = True
                source.write_bytes(b"12345678")
            return chunk

        with mock.patch.object(module.os, "read", side_effect=grow_after_open):
            with self.assertRaises(SupervisorError):
                module._copy_candidate_file(source, destination)
        self.assertFalse(destination.exists())

    def test_candidate_copy_does_not_remove_preexisting_destination_on_open_failure(self) -> None:
        from scripts.pilot import workspace_supervisor as module

        source = self.sup.candidate_root / "existing-source.txt"
        source.write_bytes(b"candidate")
        destination = self.root / "existing-destination.txt"
        destination.write_bytes(b"authority")
        with self.assertRaises(SupervisorError):
            module._copy_candidate_file(source, destination)
        self.assertEqual(b"authority", destination.read_bytes())

    def test_supervisor_cancel_terminates_candidate_process_group_and_children(self) -> None:
        import threading

        attempt, candidate = self._start()
        operation = self.sup.register_operation(
            ["/runtime/bin/python", "source/model.py"], attempt_handle=attempt
        )

        def launch(_argv, **kwargs):
            return subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); time.sleep(30)",
                ],
                cwd=kwargs["cwd"],
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )

        self.sup._command_runner = launch
        result: dict[str, object] = {}

        def run() -> None:
            result.update(
                self.sup.run_candidate_tool(
                    self.sup.workspace_handle,
                    attempt,
                    candidate,
                    operation,
                )
            )

        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 2
        while not self.sup._active_processes and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.sup._active_processes)
        started = time.monotonic()
        self.sup.cancel()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(self.sup._active_processes)
        self.assertTrue(self.sup.cancellation_confirmed)

    def test_execution_scopes_cancel_only_their_registered_processes(self) -> None:
        workspace_module = __import__(
            "scripts.pilot.workspace_supervisor", fromlist=["_load_workspace_api"]
        )._load_workspace_api()
        scope_a = workspace_module.ExecutionScope()
        scope_b = workspace_module.ExecutionScope()
        processes = []
        try:
            for scope in (scope_a, scope_b):
                process = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    start_new_session=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertTrue(
                    scope.register(
                        process,
                        lambda process=process: os.killpg(process.pid, __import__("signal").SIGTERM),
                    )
                )
                processes.append(process)
            self.assertTrue(scope_a.cancel())
            self.assertIsNotNone(processes[0].poll())
            self.assertIsNone(processes[1].poll())
            self.assertTrue(scope_b.cancel())
            self.assertIsNotNone(processes[1].poll())
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait()
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()

    def test_execution_scope_waits_for_terminating_spawn_token(self) -> None:
        workspace_module = __import__(
            "scripts.pilot.workspace_supervisor", fromlist=["_load_workspace_api"]
        )._load_workspace_api()
        scope = workspace_module.ExecutionScope()
        token = scope.begin_spawn()
        self.assertIsInstance(token, int)
        result: list[bool] = []
        worker = threading.Thread(target=lambda: result.append(scope.cancel()))
        worker.start()
        time.sleep(0.05)
        process = mock.Mock()
        self.assertFalse(
            scope.register_spawn(
                process,
                lambda: None,
                lambda: None,
                token=token,
            )
        )
        time.sleep(0.1)
        self.assertEqual([], result)
        scope.spawn_terminated(token, confirmed=True)
        worker.join(timeout=2)
        self.assertEqual([True], result)

    def test_runner_terminal_publisher_recovers_handoff_only_and_no_fcntl_lock(self) -> None:
        from scripts.pilot import runner

        class TerminalAPI:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace
                self.compiles = 0
                self.writes = 0

            def workspace_initialized(self, _workspace: Path) -> bool:
                return True

            def compile_terminal_validation(self, _workspace: Path) -> dict:
                self.compiles += 1
                return {
                    "bundle": {"schema": "synthetic-bundle/1", "result": {}, "manifest": {}},
                    "terminal_identity_sha256": "a" * 64,
                }

            def verify_terminal_validation(self, _workspace: Path, bundle: dict, identity: str) -> dict:
                self.assertions(bundle, identity)
                return bundle["result"]

            @staticmethod
            def assertions(bundle: dict, identity: str) -> None:
                if identity != "a" * 64 or bundle.get("schema") != "synthetic-bundle/1":
                    raise ValueError("invalid synthetic terminal bundle")

            def read_terminal_locator(self, workspace: Path) -> dict | None:
                target = workspace / "run/terminal-validation-locator.json"
                return json.loads(target.read_text()) if target.exists() else None

            def write_terminal_locator(self, workspace: Path, payload: dict) -> str:
                self.writes += 1
                target = workspace / "run/terminal-validation-locator.json"
                target.parent.mkdir(exist_ok=True)
                target.write_text(json.dumps(payload), encoding="utf-8")
                return "run/terminal-validation-locator.json"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "exp"
            workspace.mkdir()
            (workspace / "workspace.json").write_text("{}", encoding="utf-8")
            api = TerminalAPI(workspace)
            with mock.patch.object(runner, "_load_workspace_api", return_value=api), mock.patch.object(
                runner, "fcntl", None
            ):
                first = runner.persist_terminal_validation(workspace)
            (workspace / "run/terminal-validation-locator.json").unlink()
            with mock.patch.object(runner, "_load_workspace_api", return_value=api), mock.patch.object(
                runner, "fcntl", None
            ):
                recovered = runner.persist_terminal_validation(workspace)
            self.assertEqual(first, recovered)
            self.assertEqual(1, api.compiles)
            self.assertEqual(2, api.writes)
            lock_path = (
                workspace.parent
                / ".internal-terminal-validation"
                / ".exp.publish.lock"
            )
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": 99999999,
                        "host": "dead-host",
                        "boot": "dead-boot",
                        "start": "dead-start",
                        "created_ns": 0,
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(runner, "_load_workspace_api", return_value=api), mock.patch.object(
                runner, "fcntl", None
            ):
                self.assertIsNotNone(runner.persist_terminal_validation(workspace))
            self.assertFalse(lock_path.exists())

    def test_terminal_handoff_post_link_failure_removes_exact_payload(self) -> None:
        from scripts.pilot import runner

        payload = {
            "schema": "mesh-to-cad.terminal-validation-handoff/1",
            "terminal_identity_sha256": "a" * 64,
            "bundle": {"schema": "synthetic-bundle/1"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "terminal-validation.json"
            with mock.patch.object(
                runner,
                "_fsync_terminal_parent",
                side_effect=OSError("injected post-link fsync failure"),
            ):
                with self.assertRaises(runner.PilotError):
                    runner._write_terminal_handoff(target, payload)
            self.assertFalse(target.exists())
            self.assertTrue(
                list(target.parent.glob(".terminal-validation.json.quarantine-*"))
            )

    def test_quarantine_swap_preserves_foreign_replacement_and_restore_collision(self) -> None:
        from scripts.pilot import runner

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            victim = root / "terminal-validation.json"
            victim.write_bytes(b"owned")
            identity, _ = runner._terminal_file_identity_and_bytes(victim)
            original_rename = runner._terminal_atomic_rename_no_replace

            def swap_after_move(source: Path, target: Path) -> None:
                original_rename(source, target)
                if source == victim:
                    victim.write_bytes(b"foreign")

            with mock.patch.object(
                runner, "_terminal_atomic_rename_no_replace", side_effect=swap_after_move
            ):
                self.assertTrue(
                    runner._remove_exact_terminal_file(victim, identity, b"owned")
                )
            self.assertEqual(b"foreign", victim.read_bytes())

            victim.write_bytes(b"owned")
            identity, _ = runner._terminal_file_identity_and_bytes(victim)

            def collide_on_restore(source: Path, target: Path) -> None:
                original_rename(source, target)
                if source == victim:
                    victim.write_bytes(b"foreign")
                    target.write_bytes(b"foreign-quarantine")

            with mock.patch.object(
                runner,
                "_terminal_atomic_rename_no_replace",
                side_effect=collide_on_restore,
            ):
                self.assertFalse(
                    runner._remove_exact_terminal_file(victim, identity, b"owned")
                )
            self.assertEqual(b"foreign", victim.read_bytes())
            self.assertTrue(list(root.glob(".terminal-validation.json.quarantine-*")))

    def test_forced_windows_move_and_persistent_byte_lock_adapters(self) -> None:
        from scripts.pilot import runner

        calls: list[tuple[str, str, int]] = []

        def native_move(source: str, target: str, flags: int) -> int:
            calls.append((source, target, flags))
            return 1

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            with mock.patch.object(runner, "_terminal_is_windows", return_value=True):
                runner._windows_move_file_ex(source, target, native=native_move)
            self.assertEqual(0x8, calls[0][2])
            with self.assertRaises(FileExistsError):
                runner._windows_move_file_ex(
                    source,
                    target,
                    native=lambda *_args: 0,
                    last_error=lambda: 183,
                )
            with self.assertRaises(OSError):
                runner._windows_move_file_ex(
                    source,
                    target,
                    native=lambda *_args: 0,
                    last_error=lambda: 5,
                )

            class FakeByteLock:
                held = False

                def lock(self, _descriptor: int) -> None:
                    if self.held:
                        raise BlockingIOError
                    self.held = True

                def unlock(self, _descriptor: int) -> None:
                    self.held = False

            fake = FakeByteLock()
            experiment = root / "exp"
            experiment.mkdir()
            with (
                mock.patch.object(runner, "_terminal_is_windows", return_value=True),
                mock.patch.object(runner, "_windows_byte_range_lock", return_value=fake),
                mock.patch.object(runner, "_flush_terminal_file"),
            ):
                first = runner._acquire_terminal_publish_lock(experiment)
                control = first[0]
                runner._release_terminal_publish_lock(first)
                second = runner._acquire_terminal_publish_lock(experiment)
                runner._release_terminal_publish_lock(second)
            self.assertTrue(control.is_file())

    def test_forced_windows_terminal_publish_avoids_posix_directory_fsync(self) -> None:
        from scripts.pilot import runner

        workspace_root = Path(__file__).resolve().parents[3] / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace"
        if str(workspace_root) not in sys.path:
            sys.path.insert(0, str(workspace_root))
        spec = importlib.util.spec_from_file_location(
            "forced_windows_workspace_facade", workspace_root / "workspace.py"
        )
        self.assertIsNotNone(spec and spec.loader)
        facade = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(facade)

        class FakeByteLock:
            def lock(self, _descriptor: int) -> None:
                return None

            def unlock(self, _descriptor: int) -> None:
                return None

        class TerminalAPI:
            def __init__(self, workspace: Path) -> None:
                self.workspace = workspace

            def workspace_initialized(self, _workspace: Path) -> bool:
                return True

            def compile_terminal_validation(self, _workspace: Path) -> dict:
                return {
                    "bundle": {"schema": "synthetic-bundle/1", "result": {}, "manifest": {}},
                    "terminal_identity_sha256": "a" * 64,
                }

            def verify_terminal_validation(self, _workspace: Path, _bundle: dict, identity: str) -> dict:
                if identity != "a" * 64:
                    raise ValueError("wrong identity")
                return {}

            def read_terminal_locator(self, workspace: Path) -> dict | None:
                target = workspace / "run/terminal-validation-locator.json"
                return json.loads(target.read_text()) if target.exists() else None

            def write_terminal_locator(self, workspace: Path, payload: dict) -> str:
                return facade.write_terminal_locator(workspace, payload)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "exp"
            workspace.mkdir()
            (workspace / "workspace.json").write_text("{}", encoding="utf-8")
            api = TerminalAPI(workspace)
            original_open = runner.os.open

            def reject_directory_open(path, flags, *args, **kwargs):
                if flags & getattr(os, "O_DIRECTORY", 0):
                    raise AssertionError("POSIX directory fsync was attempted")
                return original_open(path, flags, *args, **kwargs)

            with (
                mock.patch.object(runner, "_load_workspace_api", return_value=api),
                mock.patch.object(runner, "_terminal_is_windows", return_value=True),
                mock.patch.object(runner, "_terminal_atomic_rename_available", return_value=True),
                mock.patch.object(runner, "_windows_move_file_ex", side_effect=lambda source, target: os.replace(source, target)),
                mock.patch.object(runner, "_windows_byte_range_lock", return_value=FakeByteLock()),
                mock.patch.object(runner, "_flush_terminal_file"),
                mock.patch.object(facade, "_locator_is_windows", return_value=True),
                mock.patch.object(facade, "_locator_atomic_rename_available", return_value=True),
                mock.patch.object(facade, "_locator_windows_move_file_ex", side_effect=lambda source, target: os.replace(source, target)),
                mock.patch.object(facade, "_locator_flush_file"),
                mock.patch.object(runner.os, "open", side_effect=reject_directory_open),
            ):
                first = runner.persist_terminal_validation(workspace)
                second = runner.persist_terminal_validation(workspace)
            self.assertEqual(first, second)
            self.assertTrue((workspace / "run/terminal-validation-locator.json").is_file())

    def test_workspace_tree_copy_rejects_growing_nested_source_and_cleans_stage(self) -> None:
        from scripts.pilot import workspace_supervisor as supervisor_module

        workspace_module = supervisor_module._load_workspace_api()
        source = self.root / "growing-tree"
        (source / "nested").mkdir(parents=True)
        member = source / "nested/member.txt"
        member.write_bytes(b"1234")
        target = self.root / "growing-tree-target"
        original_read = workspace_module.os.read
        grew = False

        def grow_nested(fd, size):
            nonlocal grew
            chunk = original_read(fd, size)
            if chunk and not grew:
                grew = True
                member.write_bytes(b"12345678")
            return chunk

        with mock.patch.object(workspace_module.os, "read", side_effect=grow_nested):
            with self.assertRaises(workspace_module.WorkspaceError):
                workspace_module._copy_agent_tree(source, target)
        self.assertFalse(target.exists())

        (source / "nested/member.txt").write_bytes(b"1234")
        (source / "second.txt").write_bytes(b"5678")
        capped_target = self.root / "capped-tree-target"
        with mock.patch.object(workspace_module, "_AGENT_MAX_TREE_BYTES", 5):
            with self.assertRaises(workspace_module.WorkspaceError):
                workspace_module._copy_agent_tree(source, capped_target)
        self.assertFalse(capped_target.exists())

    def test_candidate_sandbox_uses_narrow_runtime_allowlist(self) -> None:
        from scripts.pilot import workspace_supervisor as module

        operation = module._CandidateOperation(("/usr/bin/true",), 1, ())
        runtime = self.root / "runtime"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "bin/python").write_bytes(b"python")
        with mock.patch.object(module.shutil, "which", return_value="/fake/bwrap"):
            argv = module._candidate_sandbox_argv(
                operation, self.sup.candidate_root, runtime
            )
        triples = [argv[index : index + 3] for index in range(len(argv) - 2)]
        self.assertNotIn(["--ro-bind", "/etc", "/etc"], triples)
        self.assertNotIn("/etc", argv)
        self.assertIn(
            ["--ro-bind", "/etc/passwd", "/etc/passwd"], triples
        )
        self.assertIn(["--ro-bind", os.fspath(runtime), "/runtime"], triples)

    def test_materialized_uv_runtime_view_filters_hostile_metadata_and_symlinks(self) -> None:
        from scripts.pilot import runner
        from scripts.pilot import candidate_runtime as runtime_module

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            venv = repo / ".venv"
            external = root / "uv-cache" / "cpython-3.12"
            stdlib = external / "lib/python3.12"
            external_bin = external / "bin"
            site = venv / "lib/python3.12/site-packages"
            stdlib.mkdir(parents=True)
            external_bin.mkdir(parents=True)
            site.mkdir(parents=True)
            (stdlib / "os.py").write_text("name = 'safe'\n", encoding="utf-8")
            (stdlib / "_sysconfigdata_test.py").write_text(
                f"STDLIB = {str(stdlib)!r}\n", encoding="utf-8"
            )
            (stdlib / "lib-dynload").mkdir()
            fake_python = external_bin / "python3"
            fake_python.write_text(
                "#!/bin/sh\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            (venv / "bin").mkdir(parents=True)
            (venv / "bin/python").symlink_to(fake_python)
            (venv / "pyvenv.cfg").write_text(
                f"home = {external}\nversion = 3.12.1\n", encoding="utf-8"
            )
            (site / "cad.py").write_text("CAD = True\n", encoding="utf-8")
            (site / "playwright").mkdir()
            (site / "playwright/__init__.py").write_text("UNRELATED = True\n", encoding="utf-8")
            (site / "direct_url.json").write_text(
                json.dumps({"url": str(repo), "dir_info": {"editable": True}}),
                encoding="utf-8",
            )
            (site / "editable.pth").write_text(str(repo) + "\n", encoding="utf-8")
            dist = site / "cad-1.0.dist-info"
            dist.mkdir()
            (dist / "METADATA").write_text("Name: cad\nVersion: 1.0\n", encoding="utf-8")

            probe = runtime_module.RuntimeProbe(
                "3.12",
                stdlib,
                stdlib,
                site,
                site,
                stdlib / "lib-dynload",
                distributions=(
                    runtime_module.DistributionRecord(
                        "cad",
                        "1.0",
                        site,
                        ("cad.py",),
                        "a" * 64,
                    ),
                ),
            )
            with mock.patch.object(runtime_module, "_probe", return_value=probe):
                runtime = runner.materialize_candidate_runtime(
                    venv, root / "candidate-runtime", repo_root=repo
                )
            self.assertTrue((runtime / "bin/python").is_file())
            self.assertTrue((runtime / "lib/python3.12/os.py").is_file())
            self.assertIn(
                b"/runtime/lib/python3.12",
                (runtime / "lib/python3.12/_sysconfigdata_test.py").read_bytes(),
            )
            self.assertTrue((runtime / "lib/python3.12/site-packages/cad.py").is_file())
            self.assertFalse((runtime / "lib/python3.12/site-packages/playwright").exists())
            runner.validate_candidate_runtime(runtime, required_imports=())
            self.assertFalse((runtime / "pyvenv.cfg").exists())
            self.assertFalse((runtime / "lib/python3.12/site-packages/direct_url.json").exists())
            self.assertFalse((runtime / "lib/python3.12/site-packages/editable.pth").exists())
            for path in runtime.rglob("*"):
                self.assertFalse(path.is_symlink())
                if path.is_file():
                    self.assertNotIn(os.fsencode(repo), path.read_bytes())
                    self.assertNotIn(os.fsencode(venv), path.read_bytes())
            drift_probe = runtime_module.RuntimeProbe(
                probe.version,
                probe.stdlib,
                probe.platstdlib,
                probe.purelib,
                probe.platlib,
                probe.dynload,
                distributions=(
                    runtime_module.DistributionRecord(
                        "cad",
                        "1.0",
                        site,
                        ("cad.py",),
                        "a" * 64,
                        (runtime_module.FileRecord("cad.py", 10, "0" * 64),),
                    ),
                ),
            )
            with mock.patch.object(runtime_module, "_probe", return_value=drift_probe):
                with self.assertRaises(runner.CandidateRuntimeError):
                    runner.materialize_candidate_runtime(
                        venv, root / "candidate-runtime-drift", repo_root=repo
                    )
            from scripts.pilot import workspace_supervisor as supervisor_module

            with mock.patch.object(supervisor_module.shutil, "which", return_value="/fake/bwrap"):
                argv = supervisor_module._candidate_sandbox_argv(
                    supervisor_module._CandidateOperation(("/runtime/bin/python", "-c", "pass"), 1, ()),
                    root / "candidate",
                    runtime,
                )
            self.assertIn("/runtime/bin/python", argv)
            self.assertNotIn(os.fspath(repo), argv)

            escaped = root / "escaped.py"
            escaped.write_text("outside = True\n", encoding="utf-8")
            (site / "escape.py").symlink_to(escaped)
            escaped_probe = runtime_module.RuntimeProbe(
                probe.version,
                probe.stdlib,
                probe.platstdlib,
                probe.purelib,
                probe.platlib,
                probe.dynload,
                distributions=(
                    runtime_module.DistributionRecord(
                        "cad", "1.0", site, ("cad.py", "escape.py"), "a" * 64
                    ),
                ),
            )
            with mock.patch.object(runtime_module, "_probe", return_value=escaped_probe):
                with self.assertRaises(runner.CandidateRuntimeError):
                    runner.materialize_candidate_runtime(
                        venv, root / "candidate-runtime-escaped", repo_root=repo
                    )

            race_source = site / "race.py"
            race_source.write_text("safe = True\n", encoding="utf-8")
            race_outside = root / "race-outside.py"
            race_outside.write_text("authority = True\n", encoding="utf-8")
            race_probe = runtime_module.RuntimeProbe(
                probe.version,
                probe.stdlib,
                probe.platstdlib,
                probe.purelib,
                probe.platlib,
                probe.dynload,
                distributions=(
                    runtime_module.DistributionRecord(
                        "cad", "1.0", site, ("cad.py", "race.py"), "a" * 64
                    ),
                ),
            )
            original_open = runtime_module.os.open
            swapped = False

            def swap_before_descriptor(path, flags, *args, **kwargs):
                nonlocal swapped
                if path == "race.py" and kwargs.get("dir_fd") is not None and not swapped:
                    swapped = True
                    race_source.unlink()
                    race_source.symlink_to(race_outside)
                return original_open(path, flags, *args, **kwargs)

            with (
                mock.patch.object(runtime_module, "_probe", return_value=race_probe),
                mock.patch.object(runtime_module.os, "open", side_effect=swap_before_descriptor),
            ):
                with self.assertRaises(runner.CandidateRuntimeError):
                    runner.materialize_candidate_runtime(
                        venv, root / "candidate-runtime-race", repo_root=repo
                    )

    def test_runtime_cache_reuses_one_immutable_identity_and_serializes_builders(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from scripts.pilot import candidate_runtime as runtime_module
        from scripts.pilot import runner

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            venv = repo / ".venv"
            stdlib = root / "external/lib/python3.12"
            site = venv / "lib/python3.12/site-packages"
            (venv / "bin").mkdir(parents=True)
            stdlib.mkdir(parents=True)
            site.mkdir(parents=True)
            (stdlib / "os.py").write_text("", encoding="utf-8")
            fake = root / "external/bin/python3"
            fake.parent.mkdir(parents=True)
            fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            (venv / "bin/python").symlink_to(fake)
            (venv / "pyvenv.cfg").write_text("version = 3.12.1\n", encoding="utf-8")
            (site / "cad.py").write_text("CAD = True\n", encoding="utf-8")
            probe = runtime_module.RuntimeProbe(
                "3.12",
                stdlib,
                stdlib,
                site,
                site,
                None,
                distributions=(
                    runtime_module.DistributionRecord(
                        "cad", "1", site, ("cad.py",), "b" * 64
                    ),
                ),
            )
            cache = root / "cache"
            original_copy = runtime_module._copy_file_stream
            calls = 0

            def counted_copy(*args, **kwargs):
                nonlocal calls
                calls += 1
                return original_copy(*args, **kwargs)

            with (
                mock.patch.object(runtime_module, "_probe", return_value=probe),
                mock.patch.object(runtime_module, "_copy_file_stream", side_effect=counted_copy),
            ):
                first = runner.materialize_candidate_runtime(venv, cache, repo_root=repo)
                first_calls = calls
            with (
                mock.patch.object(runtime_module, "_probe", return_value=probe),
                mock.patch.object(
                    runtime_module,
                    "validate_candidate_runtime",
                    side_effect=AssertionError("warm cache re-imported CAD runtime"),
                ),
            ):
                second = runner.materialize_candidate_runtime(venv, cache, repo_root=repo)
            self.assertEqual(first, second)
            self.assertGreater(first_calls, 0)
            self.assertEqual(first_calls, calls)
            self.assertFalse(any(path.name.startswith(".") and ".tmp-" in path.name for path in cache.iterdir()))
            self.assertEqual(0, first.stat().st_mode & 0o222)
            receipt = first / runtime_module._RECEIPT_NAME
            receipt_value = json.loads(receipt.read_text(encoding="ascii"))
            for malformed in (
                {**receipt_value, "imports": []},
                {**receipt_value, "extra": True},
            ):
                receipt.chmod(0o644)
                receipt.write_text(json.dumps(malformed), encoding="ascii")
                receipt.chmod(0o444)
                with mock.patch.object(runtime_module, "_probe", return_value=probe):
                    repaired_receipt_runtime = runner.materialize_candidate_runtime(
                        venv, cache, repo_root=repo
                    )
                receipt = repaired_receipt_runtime / runtime_module._RECEIPT_NAME
                self.assertEqual(
                    list(runtime_module.CAD_RUNTIME_IMPORTS),
                    json.loads(receipt.read_text(encoding="ascii"))["imports"],
                )
            corrupt = first / "lib/python3.12/site-packages/cad.py"
            corrupt.chmod(0o644)
            corrupt.write_text("tampered = True\n", encoding="utf-8")
            corrupt.chmod(0o444)
            with mock.patch.object(runtime_module, "_probe", return_value=probe):
                repaired = runner.materialize_candidate_runtime(venv, cache, repo_root=repo)
            self.assertEqual(first, repaired)
            self.assertEqual("CAD = True\n", corrupt.read_text(encoding="utf-8"))
            lease_files_before = {
                path
                for path in (cache / "leases").rglob("*.json")
            }
            with (
                mock.patch.object(runtime_module, "_probe", return_value=probe),
                mock.patch.object(
                    runtime_module,
                    "_prune_cache",
                    side_effect=RuntimeError("forced prune failure"),
                ),
            ):
                with self.assertRaises(RuntimeError):
                    runner.materialize_candidate_runtime(venv, cache, repo_root=repo)
            self.assertFalse((cache / ".cache.lock").exists())
            self.assertEqual(
                lease_files_before,
                {
                    path
                    for path in (cache / "leases").rglob("*.json")
                },
            )

            with (
                mock.patch.object(runtime_module, "_probe", return_value=probe),
                mock.patch.object(runtime_module, "_copy_file_stream", side_effect=counted_copy),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                concurrent_cache = root / "concurrent-cache"
                before_concurrent = calls
                results = list(
                    pool.map(
                        lambda _item: runner.materialize_candidate_runtime(
                            venv, concurrent_cache, repo_root=repo
                        ),
                        (1, 2),
                    )
                )
            self.assertEqual([results[0], results[0]], results)
            self.assertEqual(first_calls, calls - before_concurrent)
            self.assertFalse(any(path.name.startswith(".") and ".tmp-" in path.name for path in concurrent_cache.iterdir()))

    def test_runtime_lock_reclaims_dead_reboot_and_pid_reuse_owners_but_not_live_owner(self) -> None:
        from scripts.pilot import candidate_runtime as runtime_module

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            final = root / ("a" * 64)
            lock = root / ".cache.lock"

            for owner in (
                {
                    "pid": 99999999,
                    "host": __import__("socket").gethostname(),
                    "boot": runtime_module._boot_token(),
                    "start": "dead",
                    "created_ns": 1,
                },
                {
                    **runtime_module._owner_record(),
                    "boot": "rebooted",
                },
                {
                    **runtime_module._owner_record(),
                    "start": "pid-reused",
                },
            ):
                lock.write_text(json.dumps(owner), encoding="ascii")
                descriptor = runtime_module._acquire_lock(lock, final, "a" * 64)
                self.assertGreaterEqual(descriptor, 0)
                self.assertEqual(os.getpid(), json.loads(lock.read_text())["pid"])
                runtime_module._release_lock(lock, descriptor)

            descriptor = runtime_module._acquire_lock(lock, final, "a" * 64)
            try:
                with mock.patch.object(runtime_module, "_CACHE_LOCK_SECONDS", 0.1):
                    with self.assertRaises(runtime_module.CandidateRuntimeError):
                        runtime_module._acquire_lock(lock, final, "a" * 64)
                self.assertEqual(os.getpid(), json.loads(lock.read_text())["pid"])
            finally:
                runtime_module._release_lock(lock, descriptor)

            orphan = root / ("." + "a" * 64 + ".tmp-crashed")
            orphan.mkdir()
            (orphan / "partial").write_text("partial", encoding="utf-8")
            runtime_module._cleanup_orphan_temps(root)
            self.assertFalse(orphan.exists())

            victim = root / "victim"
            victim.write_text("keep", encoding="utf-8")
            hostile_cache = root / "hostile-cache"
            hostile_cache.symlink_to(victim)
            with self.assertRaises(runtime_module.CandidateRuntimeError):
                runtime_module._reject_symlink_components(hostile_cache)
            self.assertEqual("keep", victim.read_text(encoding="utf-8"))
            hostile_lock = root / ".cache.lock"
            hostile_lock.symlink_to(victim)
            with self.assertRaises(runtime_module.CandidateRuntimeError):
                runtime_module._acquire_lock(hostile_lock, root / ("b" * 64), "b" * 64)
            self.assertEqual("keep", victim.read_text(encoding="utf-8"))

    def test_runtime_retention_respects_live_and_reclaims_dead_leases(self) -> None:
        from scripts.pilot import candidate_runtime as runtime_module
        from scripts.pilot import runner

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            venv = repo / ".venv"
            stdlib = root / "external/lib/python3.12"
            site = venv / "lib/python3.12/site-packages"
            (venv / "bin").mkdir(parents=True)
            stdlib.mkdir(parents=True)
            site.mkdir(parents=True)
            (stdlib / "os.py").write_text("", encoding="utf-8")
            fake = root / "external/bin/python3"
            fake.parent.mkdir(parents=True)
            fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            (venv / "bin/python").symlink_to(fake)
            (venv / "pyvenv.cfg").write_text("version = 3.12.1\n", encoding="utf-8")
            cad = site / "cad.py"
            cad.write_text("CAD = True\n", encoding="utf-8")

            def probe(sequence: str) -> runtime_module.RuntimeProbe:
                return runtime_module.RuntimeProbe(
                    "3.12",
                    stdlib,
                    stdlib,
                    site,
                    site,
                    None,
                    distributions=(
                        runtime_module.DistributionRecord(
                            "cad", "1", site, ("cad.py",), sequence * 64
                        ),
                    ),
                )

            cache = root / "cache"
            with mock.patch.object(
                runtime_module,
                "_probe",
                side_effect=[probe("a"), probe("b"), probe("c"), probe("d"), probe("e")],
            ):
                first = runner.materialize_candidate_runtime(venv, cache, repo_root=repo)
                first_lease = first
                second = runner.materialize_candidate_runtime(venv, cache, repo_root=repo)
                second.release()
                third = runner.materialize_candidate_runtime(venv, cache, repo_root=repo)
                self.assertTrue(first.is_dir() and third.is_dir())
                self.assertFalse(second.exists())
                first_lease.release()
                third.release()
                fourth = runner.materialize_candidate_runtime(venv, cache, repo_root=repo)
                self.assertTrue(fourth.is_dir())
                fourth.release()
                lease_dir = cache / "leases" / third.name
                lease_dir.mkdir(parents=True, exist_ok=True)
                dead = lease_dir / "dead.json"
                dead.write_text(
                    json.dumps(
                        {
                            "schema": runtime_module._CACHE_SCHEMA,
                            "identity": third.name,
                            "pid": 99999999,
                            "host": __import__("socket").gethostname(),
                            "boot": runtime_module._boot_token(),
                            "start": "dead",
                            "created_ns": 1,
                        }
                    ),
                    encoding="ascii",
                )
                fifth = runner.materialize_candidate_runtime(venv, cache, repo_root=repo)
                self.assertTrue(fifth.is_dir())
                self.assertFalse(dead.exists())
            entries = [
                path
                for path in cache.iterdir()
                if path.is_dir() and re.fullmatch(r"[0-9a-f]{64}", path.name)
            ]
            self.assertLessEqual(len(entries), 2)

    @unittest.skipUnless(
        (Path.home() / "Desktop/codes/text-to-cad/.venv/bin/python").is_file(),
        "the repository uv runtime is unavailable",
    )
    def test_actual_uv_runtime_materializes_and_launches_stdlib_without_bwrap(self) -> None:
        from scripts.pilot import runner

        source_venv = Path.home() / "Desktop/codes/text-to-cad/.venv"
        source_repo = source_venv.parent
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            def unlock_cache() -> None:
                if not cache.exists():
                    return
                for path in sorted(cache.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                    try:
                        path.chmod(0o755 if path.is_dir() else 0o644)
                    except OSError:
                        pass
            self.addCleanup(unlock_cache)
            runtime = runner.materialize_candidate_runtime(
                source_venv,
                cache,
                repo_root=source_repo,
            )
            self.addCleanup(runtime.release)
            self.assertEqual(("build123d", "trimesh", "OCP"), runner.CAD_CANDIDATE_RUNTIME_IMPORTS)
            from scripts.pilot import candidate_runtime as runtime_module
            self.assertEqual(
                (("build123d", "build123d"), ("trimesh", "trimesh"), ("OCP", "cadquery-ocp")),
                runtime_module.CAD_RUNTIME_ROOTS,
            )
            version = next(
                path.name
                for path in (runtime / "lib").glob("python*")
                if path.is_dir()
            )
            env = {
                "PATH": "/runtime/bin",
                "PYTHONHOME": os.fspath(runtime),
                "PYTHONPATH": os.fspath(runtime / "lib" / version / "site-packages"),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "LC_ALL": "C",
            }
            launched = subprocess.run(
                [
                    os.fspath(runtime / "bin/python"),
                    "-c",
                    "import os,sys,sysconfig; print(sys.version_info[:2]); print(sysconfig.get_path('stdlib')); print(os.path.basename(sys.executable))",
                ],
                env=env,
                cwd=runtime,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(0, launched.returncode, launched.stderr)
            self.assertIn("(3, 12)", launched.stdout)
            self.assertNotIn(os.fspath(source_repo), launched.stdout + launched.stderr)
            self.assertTrue(any(path.name.startswith("libpython") for path in (runtime / "lib").glob("libpython*")))
            self.assertFalse((runtime / "lib" / version / "site-packages/playwright").exists())
            source_cad = subprocess.run(
                [os.fspath(source_venv / "bin/python"), "-c", "import build123d"],
                capture_output=True,
                check=False,
                timeout=60,
            )
            if source_cad.returncode == 0:
                cad = subprocess.run(
                    [os.fspath(runtime / "bin/python"), "-c", "import build123d"],
                    env=env,
                    cwd=runtime,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                )
                self.assertEqual(0, cad.returncode, cad.stderr)

    def test_runner_candidate_mount_hides_authority_and_binds_fixed_bridge(self) -> None:
        from scripts.pilot import runner
        from tests.python.support.authority_fixtures import build_authority

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            exp = root / "outputs/group/exp"
            input_path = root / "models/toys4k/input.ply"
            gateway = root / "gateway/codex-tap-gpt56"
            venv = root / ".venv"
            home = Path(temporary) / "home"
            candidate = Path(temporary) / "candidate"
            for path in (exp, input_path.parent, gateway.parent, venv, candidate, home):
                path.mkdir(parents=True, exist_ok=True)
            input_path.write_bytes(b"ply\n")
            gateway.write_text("#!/bin/sh\n", encoding="utf-8")
            fixture = build_authority(home, dedupe_token="w4-isolated")
            socket_path = Path(temporary) / "bridge.sock"
            bridge_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            bridge_socket.bind(os.fspath(socket_path))
            bridge_socket.listen(1)
            try:
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
                        root,
                        exp,
                        [input_path],
                        ["/fake/codex"],
                        {"HOME": os.fspath(home), "PATH": "/fake", "VENUS_TOKEN": "secret"},
                        agent_candidate_dir=candidate,
                        agent_surface_socket=socket_path,
                        agent_surface_client=Path(__file__).resolve().parents[3]
                        / "scripts/pilot/agent_surface_client.py",
                    )
            finally:
                bridge_socket.close()
                socket_path.unlink(missing_ok=True)
            self.assertNotIn(os.fspath(exp.resolve()), argv)
            self.assertNotIn(os.fspath(input_path.resolve()), argv)
            self.assertNotIn(
                ["--ro-bind", os.fspath(venv.resolve()), "/workspace/repo/.venv"],
                [argv[i : i + 3] for i in range(len(argv) - 2)],
            )
            self.assertIn(
                ["--bind", os.fspath(candidate.resolve()), "/candidate"],
                [argv[i : i + 3] for i in range(len(argv) - 2)],
            )
            self.assertIn("/agent-surface/client.py", argv)
            self.assertIn("/run/mesh-to-cad-agent-surface.sock", argv)
            self.assertNotIn(
                ["--ro-bind", "/etc", "/etc"],
                [argv[i : i + 3] for i in range(len(argv) - 2)],
            )

    def test_reference_port_returns_only_w2_projection(self) -> None:
        result = self.sup.observe_reference(
            self.sup.reference_handle,
            {"method": "summary", "args": {}},
        )
        self.assertEqual(self.sup.reference_handle, result["reference_id"])
        self.assertNotIn("vertices", result["observation"])
        with self.assertRaises(SupervisorError):
            self.sup.observe_reference(
                self.sup.reference_handle,
                {"method": "raw_bytes", "args": {}},
            )

    def test_synthetic_intent_lifecycle_crosses_one_concrete_boundary(self) -> None:
        attempt, candidate = self._start()
        candidate_root = self.sup.candidate_root / "attempt-000001"
        (candidate_root / "mesh.glb").write_bytes(b"candidate")
        (candidate_root / "measurement.json").write_bytes(b"candidate")
        (candidate_root / "preview").mkdir()
        mesh = self.sup.register_candidate_path(
            candidate_root / "mesh.glb", attempt_handle=attempt
        )
        measurement = self.sup.register_candidate_path(
            candidate_root / "measurement.json", attempt_handle=attempt
        )
        preview = self.sup.register_candidate_path(
            candidate_root / "preview", attempt_handle=attempt
        )
        self.assertEqual("published", self.sup.submit_step_zero(
            self.sup.workspace_handle, attempt, candidate, mesh, measurement, preview
        )["state"])
        selection = self.sup.candidate_root / "selection.json"
        evidence = self.sup.candidate_root / "evidence.txt"
        evidence.write_text("evidence", encoding="utf-8")
        selection.write_text(
            '{"evidence":[{"kind":"preview","path":"evidence.txt","sha256":"%s"}]}\n'
            % __import__("hashlib").sha256(b"evidence").hexdigest(),
            encoding="utf-8",
        )
        notes = self.sup.candidate_root / "notes.md"
        notes.write_text("## Input\n", encoding="utf-8")
        selection_handle = self.sup.register_selection(selection)
        notes_handle = self.sup.register_notes(notes)
        final = self.sup.select_and_finalize(
            self.sup.workspace_handle, selection_handle, notes_handle
        )
        self.assertEqual("finalized", final["state"])
        self.assertEqual(1, self.workspace.finalize_calls)

    def test_w3_handler_can_dispatch_concrete_ports_without_authority_imports(self) -> None:
        surface = self.sup.agent_surface()
        request = lambda intent, args: {
            "schema": "mesh-to-cad.agent-intent/1",
            "intent": intent,
            "args": args,
        }
        status = surface.handle(request("workspace_status", {
            "workspace_handle": self.sup.workspace_handle,
        }))
        self.assertEqual("ready", status["result"]["state"])
        observed = surface.handle(request("observe_reference", {
            "reference_handle": self.sup.reference_handle,
            "observation": {"method": "summary", "args": {}},
        }))
        self.assertEqual(
            "summary", observed["result"]["observation"]["method"]
        )

    def test_unix_bridge_serves_cli_and_mcp_without_authority_paths(self) -> None:
        from scripts.pilot.agent_surface_bridge import AgentSurfaceBridge

        socket_path = self.root / "agent-surface.sock"
        bridge = AgentSurfaceBridge(self.sup.agent_surface(), socket_path)
        self.assertIs(bridge.surface.__class__, bridge._mcp.AgentSurface)
        bridge.start()
        try:
            client = Path(__file__).resolve().parents[3] / "scripts/pilot/agent_surface_client.py"
            malformed = subprocess.run(
                [sys.executable, os.fspath(client)],
                input='{"unexpected":true}',
                text=True,
                capture_output=True,
                env={**os.environ, "MESH_TO_CAD_AGENT_SURFACE_SOCKET": os.fspath(socket_path)},
                check=False,
            )
            self.assertEqual(2, malformed.returncode)
            self.assertEqual(
                "invalid_request",
                json.loads(malformed.stdout)["error"]["classification"],
            )
            request = {
                "schema": "mesh-to-cad.agent-intent/1",
                "intent": "workspace_status",
                "args": {"workspace_handle": self.sup.workspace_handle},
            }
            result = subprocess.run(
                [sys.executable, os.fspath(client)],
                input=json.dumps(request),
                text=True,
                capture_output=True,
                env={**os.environ, "MESH_TO_CAD_AGENT_SURFACE_SOCKET": os.fspath(socket_path)},
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("ready", json.loads(result.stdout)["response"]["result"]["state"])
            initialize = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
            mcp = subprocess.run(
                [sys.executable, os.fspath(client), "--mcp"],
                input=(
                    "{bad json}\n"
                    + json.dumps(initialize)
                    + "\n"
                    + json.dumps(
                        {"jsonrpc": "2.0", "method": "notifications/initialized"}
                    )
                    + "\n"
                    + json.dumps(
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
                    )
                    + "\n"
                    + json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {
                                "name": "workspace_status",
                                "arguments": {
                                    "workspace_handle": self.sup.workspace_handle
                                },
                            },
                        }
                    )
                    + "\n"
                ),
                text=True,
                capture_output=True,
                env={**os.environ, "MESH_TO_CAD_AGENT_SURFACE_SOCKET": os.fspath(socket_path)},
                check=False,
            )
            self.assertEqual(0, mcp.returncode, mcp.stderr)
            frames = [json.loads(line) for line in mcp.stdout.splitlines()]
            self.assertEqual(-32700, frames[0]["error"]["code"])
            self.assertEqual("2025-06-18", frames[1]["result"]["protocolVersion"])
            self.assertEqual(7, len(frames[2]["result"]["tools"]))
            self.assertFalse(frames[3]["result"]["isError"])
        finally:
            bridge.stop()

    def test_bridge_slowloris_connection_times_out_and_stop_closes_it(self) -> None:
        from scripts.pilot.agent_surface_bridge import AgentSurfaceBridge

        socket_path = self.root / "slow-agent-surface.sock"
        bridge = AgentSurfaceBridge(self.sup.agent_surface(), socket_path)
        bridge.start()
        slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        slow.connect(os.fspath(socket_path))
        slow.sendall(b'{"schema":"mesh-to-cad.agent-intent/1"')
        try:
            import time

            time.sleep(2.2)
            request = {
                "schema": "mesh-to-cad.agent-intent/1",
                "intent": "workspace_status",
                "args": {"workspace_handle": self.sup.workspace_handle},
            }
            result = subprocess.run(
                [
                    sys.executable,
                    os.fspath(
                        Path(__file__).resolve().parents[3]
                        / "scripts/pilot/agent_surface_client.py"
                    ),
                ],
                input=json.dumps(request),
                text=True,
                capture_output=True,
                env={**os.environ, "MESH_TO_CAD_AGENT_SURFACE_SOCKET": os.fspath(socket_path)},
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            bridge.stop()
            self.assertFalse(socket_path.exists())
        finally:
            slow.close()
            bridge.stop()

    def test_bridge_stop_cancels_blocking_handler_before_socket_teardown(self) -> None:
        from scripts.pilot.agent_surface_bridge import AgentSurfaceBridge

        class BlockingSurface:
            def __init__(self) -> None:
                self.started = __import__("threading").Event()
                self.cancelled = __import__("threading").Event()
                self.worker = None

            def handle(self, _request):
                self.worker = __import__("threading").current_thread()
                self.started.set()
                self.cancelled.wait(30)
                return {"state": "cancelled"}

            def cancel(self) -> None:
                self.cancelled.set()

        surface = BlockingSurface()
        socket_path = self.root / "blocking-agent-surface.sock"
        bridge = AgentSurfaceBridge(surface, socket_path)
        bridge.start()
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(os.fspath(socket_path))
        client.sendall(b'{"schema":"mesh-to-cad.agent-intent/1","intent":"workspace_status","args":{}}\n')
        self.assertTrue(surface.started.wait(2))
        started = __import__("time").monotonic()
        bridge.stop()
        self.assertLess(__import__("time").monotonic() - started, 5)
        self.assertIsNotNone(surface.worker)
        self.assertFalse(surface.worker.is_alive())
        client.close()

    def test_runner_child_environment_does_not_forward_provider_secret(self) -> None:
        from scripts.pilot import runner

        child = runner.build_sandbox_environment(
            {"PATH": "/host/bin", "VENUS_TOKEN": "secret", "UNRELATED": "nope"},
            "http://127.0.0.1:1/v1",
        )
        self.assertNotIn("VENUS_TOKEN", child)
        self.assertNotIn("UNRELATED", child)
        isolated = runner.build_sandbox_environment(
            {"PATH": "/host/bin", "VENUS_TOKEN": "secret"},
            "http://127.0.0.1:1/v1",
            isolated_agent=True,
        )
        self.assertNotIn("/workspace/repo/.venv", isolated["PATH"])

    def test_runner_publishes_artifact_manifest_before_terminal_handoff(self) -> None:
        from scripts.pilot import runner

        with tempfile.TemporaryDirectory() as temporary:
            exp = Path(temporary) / "exp"
            rollout = exp / "run/.codex-home/sessions/a/b/c/rollout-test.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text("{}\n", encoding="utf-8")
            (exp / "workspace.json").write_text("{}\n", encoding="utf-8")
            events: list[str] = []
            with (
                mock.patch.object(
                    runner,
                    "validate_workspace_delivery",
                    side_effect=lambda _exp: events.append("validate") or {},
                ),
                mock.patch.object(
                    runner,
                    "compact_exp_history",
                    side_effect=lambda _exp: events.append("compact"),
                ),
                mock.patch.object(
                    runner,
                    "publish_artifact_manifest",
                    side_effect=lambda *_args: events.append("manifest") or True,
                ),
                mock.patch.object(
                    runner,
                    "persist_terminal_validation",
                    side_effect=lambda _exp: events.append("handoff"),
                ),
            ):
                self.assertEqual(
                    0,
                    runner.finalize_pilot(
                        exp, 0, {"KEEP_STATE": "1"}, require_rollout=True
                    ),
                )
            self.assertLess(events.index("manifest"), events.index("handoff"))

    def test_agent_finalize_skips_duplicate_workspace_validation_hot_path(self) -> None:
        from scripts.pilot import runner

        with tempfile.TemporaryDirectory() as temporary:
            exp = Path(temporary) / "exp"
            rollout = exp / "run/.codex-home/sessions/a/b/c/rollout-test.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text("{}\n", encoding="utf-8")
            (exp / "workspace.json").write_text("{}\n", encoding="utf-8")
            with (
                mock.patch.object(
                    runner,
                    "validate_workspace_delivery",
                    side_effect=AssertionError("duplicate validation"),
                ),
                mock.patch.object(runner, "publish_artifact_manifest", return_value=True),
                mock.patch.object(runner, "persist_terminal_validation"),
            ):
                self.assertEqual(
                    0,
                    runner.finalize_pilot(
                        exp,
                        0,
                        {"KEEP_STATE": "1"},
                        require_rollout=True,
                        agent_surface=True,
                    ),
                )

    def test_legacy_finalize_compiles_once_after_cheap_workspace_preflight(self) -> None:
        from scripts.pilot import runner

        with tempfile.TemporaryDirectory() as temporary:
            exp = Path(temporary) / "exp"
            rollout = exp / "run/.codex-home/sessions/a/b/c/rollout-test.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text("{}\n", encoding="utf-8")
            (exp / "workspace.json").write_text("{}\n", encoding="utf-8")
            events: list[str] = []
            fake_workspace = mock.Mock()
            fake_workspace.workspace_initialized.return_value = True
            with (
                mock.patch.object(runner, "_load_workspace_api", return_value=fake_workspace),
                mock.patch.object(
                    runner,
                    "validate_workspace_delivery",
                    side_effect=lambda _exp: events.append("preflight") or {},
                ),
                mock.patch.object(runner, "compact_exp_history"),
                mock.patch.object(runner, "publish_artifact_manifest", return_value=True),
                mock.patch.object(
                    runner,
                    "persist_terminal_validation",
                    side_effect=lambda _exp: events.append("compile") or None,
                ) as persist,
            ):
                self.assertEqual(
                    0,
                    runner.finalize_pilot(
                        exp,
                        0,
                        {"KEEP_STATE": "1"},
                        require_rollout=True,
                        agent_surface=False,
                    ),
                )
            self.assertEqual(["preflight", "compile"], events)
            self.assertEqual(1, persist.call_count)

            with (
                mock.patch.object(runner, "_load_workspace_api", return_value=fake_workspace),
                mock.patch.object(
                    runner.subprocess,
                    "run",
                    side_effect=AssertionError("legacy preflight performed a full scan"),
                ),
            ):
                self.assertEqual({"workspace_initialized": True}, runner.validate_workspace_delivery(exp))

    @unittest.skipUnless(
        importlib.util.find_spec("trimesh") is not None,
        "real Canonical Reference preparation dependencies are unavailable",
    )
    def test_real_outer_prepare_initializes_fresh_workspace(self) -> None:
        from scripts.pilot import runner

        ply_lines = [
            "ply",
            "format ascii 1.0",
            "element vertex 8",
            "property float x",
            "property float y",
            "property float z",
            "element face 12",
            "property list uchar int vertex_indices",
            "end_header",
            "-1 -1 -1",
            "1 -1 -1",
            "1 1 -1",
            "-1 1 -1",
            "-1 -1 1",
            "1 -1 1",
            "1 1 1",
            "-1 1 1",
            "3 0 1 2",
            "3 0 2 3",
            "3 4 6 5",
            "3 4 7 6",
            "3 0 4 5",
            "3 0 5 1",
            "3 1 5 6",
            "3 1 6 2",
            "3 2 6 7",
            "3 2 7 3",
            "3 3 7 4",
            "3 3 4 0",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "empty-exp"
            raw = root / "raw.ply"
            raw.write_text("\n".join(ply_lines) + "\n", encoding="utf-8")
            runner.prepare_exp(workspace)
            reference = runner.prepare_and_initialize_workspace(workspace, raw)
            self.assertTrue(reference.is_file())
            status = subprocess.run(
                [
                    sys.executable,
                    os.fspath(runner.WORKSPACE_HELPER),
                    "status",
                    "--workspace",
                    os.fspath(workspace),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, status.returncode, status.stderr)
            payload = json.loads(status.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(0, payload["status"]["next_intended_step"])

    @unittest.skipUnless(
        all(
            importlib.util.find_spec(name) is not None
            for name in ("trimesh", "OCP", "build123d")
        )
        and shutil.which("bwrap") is not None
        # A checkout without the pinned uv/venv is not a production fixture.
        and (Path(__file__).resolve().parents[3] / ".venv/bin/python").exists(),
        "real CAD, mesh, and candidate sandbox dependencies are unavailable",
    )
    def test_real_production_supervisor_reaches_terminal_and_compiles_once(self) -> None:
        from scripts.pilot import runner

        cli_path = (
            Path(__file__).resolve().parents[3]
            / "tests/python/skills/mesh-to-cad/test_workspace_cli.py"
        )
        spec = importlib.util.spec_from_file_location("real_workspace_cli_fixture", cli_path)
        self.assertIsNotNone(spec and spec.loader)
        fixture_module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(fixture_module)
        case = fixture_module.WorkspaceCliTests("test_real_production_supervisor")
        case.setUp()
        try:
            prepared, source_candidate = case.canonical_cad_flow()
            status, _payload, stderr = case.invoke(
                "init",
                "--workspace",
                str(case.workspace),
                "--prepared",
                str(prepared),
            )
            self.assertEqual(0, status, stderr)
            authority = case.root / "trusted-tools"
            authority.mkdir()
            registry = runner.publish_tool_registry(authority)
            candidate_runtime = runner.materialize_candidate_runtime(
                runner.REPO_ROOT / ".venv",
                case.root / "candidate-runtime",
                repo_root=runner.REPO_ROOT,
            )
            supervisor = WorkspaceSupervisor(
                case.workspace,
                bind_reference=True,
                candidate_root=case.root / "agent-candidate",
                staging_dir=case.root / "staging",
                rebuild_entrypoint=runner.CAD_REBUILD_ENTRYPOINT,
                geometry_entrypoint=runner.GEOMETRY_ENTRYPOINT,
                tool_registry=registry,
                candidate_runtime=candidate_runtime,
            )
            try:
                contract = supervisor.agent_bootstrap_contract()
                shutil.copy2(
                    case.initial_plan(), supervisor.candidate_root / "plan.json"
                )
                started = supervisor.start_attempt(
                    supervisor.workspace_handle,
                    contract["plan_handle"],
                    None,
                )
                attempt = supervisor.candidate_root / "attempt-000001"
                shutil.copytree(source_candidate, attempt, dirs_exist_ok=True)
                shutil.copy2(attempt / "built/measurement.glb", attempt / "candidate.glb")
                measurement = attempt / "measurement"
                preview = attempt / "preview"
                mesh_compare = fixture_module.MESH_COMPARE_PATH
                for command in (
                    [
                        sys.executable,
                        str(mesh_compare),
                        "voxblame-measure",
                        str(attempt / "candidate.glb"),
                        "--reference",
                        str(case.workspace / "input"),
                        "--output",
                        str(measurement),
                        "--step",
                        "0",
                    ],
                    [
                        sys.executable,
                        str(mesh_compare),
                        "voxblame-preview",
                        str(attempt / "candidate.glb"),
                        "--reference",
                        str(case.workspace / "input"),
                        "--experiment",
                        str(case.workspace / "experiment.json"),
                        "--output",
                        str(preview),
                        "--variant",
                        "step",
                    ],
                ):
                    completed = subprocess.run(
                        command,
                        cwd=fixture_module.REPO_ROOT,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(0, completed.returncode, completed.stderr)
                tool_result = supervisor.run_candidate_tool(
                    supervisor.workspace_handle,
                    started["attempt_handle"],
                    started["candidate_handle"],
                    started["capability_bundle_handle"],
                )
                self.assertEqual("completed", tool_result["state"])
                published = supervisor.submit_step_zero(
                    supervisor.workspace_handle,
                    started["attempt_handle"],
                    started["candidate_handle"],
                    started["capability_bundle_handle"],
                    started["capability_bundle_handle"],
                    started["capability_bundle_handle"],
                )
                self.assertEqual("published", published["state"])
                step = json.loads(
                    (case.workspace / "steps/000000/step.json").read_text()
                )
                preview_document = json.loads(
                    (case.workspace / "steps/000000/preview/preview.json").read_text()
                )
                evidence = supervisor.candidate_root / "evidence.json"
                evidence.write_bytes(
                    (case.workspace / "steps/000000/measurement.json").read_bytes()
                )
                evidence_sha = hashlib.sha256(evidence.read_bytes()).hexdigest()
                selection = supervisor.candidate_root / "selection.json"
                selection.write_text(
                    json.dumps(
                        {
                            "schema": "mesh-to-cad.final-selection/1",
                            "considered_steps": [0],
                            "selected_step": 0,
                            "preview": {
                                "identity_sha256": preview_document[
                                    "preview_identity_sha256"
                                ],
                                "observation": "The final preview was inspected.",
                                "evidence_conflict": False,
                                "conflict_details": None,
                            },
                            "accepted": step["accepted"],
                            "stop_reason": (
                                "acceptance_satisfied"
                                if step["accepted"]
                                else "cycle_limit"
                            ),
                            "evidence": [
                                {
                                    "kind": "measurement",
                                    "path": "evidence.json",
                                    "sha256": evidence_sha,
                                }
                            ],
                        },
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                notes = supervisor.candidate_root / "notes.md"
                notes.write_text(
                    "\n".join(
                        [
                            "## Input",
                            "## Modeling Intent",
                            "## Preserved Structural Features",
                            "## Omitted Surface Details",
                            "## Repair Trajectory",
                            "## Final Selection",
                            "## Verification",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
                final = supervisor.select_and_finalize(
                    supervisor.workspace_handle,
                    supervisor.register_selection(selection),
                    supervisor.register_notes(notes),
                )
                self.assertEqual("finalized", final["state"])
                runner.write_artifact_manifest(case.workspace, 0, 0)
                workspace_module = importlib.import_module(
                    "_mesh_to_cad_workspace_for_pilot"
                )
                calls = 0
                original_compile = workspace_module.compile_terminal_validation

                def counted_compile(workspace):
                    nonlocal calls
                    calls += 1
                    return original_compile(workspace)

                with mock.patch.object(
                    workspace_module,
                    "compile_terminal_validation",
                    side_effect=counted_compile,
                ):
                    locator = runner.persist_terminal_validation(case.workspace)
                self.assertEqual(1, calls)
                self.assertTrue(
                    locator is not None
                    and (case.workspace / locator.sidecar_path).is_file()
                )
            finally:
                supervisor.close()
        finally:
            case.temporary.cleanup()

    def test_production_launcher_uses_opaque_prompt_and_agent_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            pilot = root / "scripts" / "pilot"
            model = root / "models" / "toys4k"
            pilot.mkdir(parents=True)
            model.mkdir(parents=True)
            (model / "airplane.ply").write_text("ply\n", encoding="utf-8")
            source_pilot = Path(__file__).resolve().parents[3] / "scripts/pilot"
            for name in ("toys4k-pilot.sh", "agent_surface_bridge.py"):
                target = pilot / name
                target.write_bytes((source_pilot / name).read_bytes())
                target.chmod(0o755)
            (pilot / "runner.py").write_text(
                "import json, os, pathlib, sys\n"
                "pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(sys.argv))\n",
                encoding="utf-8",
            )
            capture = root / "argv.json"
            environment = {
                **os.environ,
                "CAPTURE": os.fspath(capture),
                "HOME": os.fspath(root / "home"),
                "PYTHON_BIN": sys.executable,
                "AGENT_SURFACE_MODE": "1",
            }
            completed = subprocess.run(
                [
                    os.fspath(pilot / "toys4k-pilot.sh"),
                    "airplane",
                    "20260825-120000-w4",
                    "exp",
                    "direct",
                ],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            exp = root / "outputs/20260825-120000-w4/exp"
            prompt = (exp / "run/prompt.txt").read_text(encoding="utf-8")
            self.assertNotIn("models/toys4k/airplane.ply", prompt)
            self.assertNotIn("outputs/20260825-120000-w4/exp", prompt)
            self.assertIn("/candidate/bootstrap.json", prompt)
            self.assertIn("/agent-surface/client.py", prompt)
            for intent in (
                "workspace_status",
                "start_attempt",
                "run_candidate_tool",
                "submit_step_zero",
                "submit_repair",
                "select_and_finalize",
                "observe_reference",
            ):
                self.assertIn(intent, prompt)
            self.assertIn(
                "--agent-surface", json.loads(capture.read_text(encoding="utf-8"))
            )


if __name__ == "__main__":
    unittest.main()
