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


def _decision_facts_stub(step: int) -> dict:
    """Closed W1-authenticated decision-facts stub for the injected workspace API.

    The stub mirrors the projection shape but never contains a path,
    provider argv, or authority attempt identifier.  Step 0 has no
    parent and no repair-target page.  A repair step exposes bounded
    target facts plus the parent-change comparison.
    """

    if step == 0:
        return {
            "schema": "mesh-to-cad.decision-facts/1",
            "step_ordinal": 0,
            "parent_step_ordinal": None,
            "accepted": False,
            "acceptance_state": "unaccepted",
            "residual_summary": {
                "objective_facts": {
                    "global_depth_8_zero": False,
                    "out_of_frame_clear": True,
                    "no_evidence_conflict": True,
                },
                "depth_8_missing_surface_count": 2,
                "depth_8_excess_surface_count": 1,
                "depth_8_surface_error_count": 3,
                "depth_8_surface_error_rate": 0.125,
            },
            "repair_targets": {
                "total": 2,
                "returned": 2,
                "remaining": 0,
                "items": [
                    {
                        "rank": 0,
                        "kind": "interior",
                        "missing_surface_count": 2,
                        "excess_surface_count": 0,
                        "surface_error_count": 2,
                    },
                    {
                        "rank": 1,
                        "kind": "exterior",
                        "missing_surface_count": 0,
                        "excess_surface_count": 1,
                        "surface_error_count": 1,
                    },
                ],
            },
            "preview": {"identity_sha256": "a" * 64, "render_variant": "step"},
            "change_from_parent": None,
        }
    return {
        "schema": "mesh-to-cad.decision-facts/1",
        "step_ordinal": step,
        "parent_step_ordinal": step - 1,
        "accepted": True,
        "acceptance_state": "acceptance_satisfied",
        "residual_summary": {
            "objective_facts": {
                "global_depth_8_zero": True,
                "out_of_frame_clear": True,
                "no_evidence_conflict": True,
            },
            "depth_8_missing_surface_count": 0,
            "depth_8_excess_surface_count": 0,
            "depth_8_surface_error_count": 0,
            "depth_8_surface_error_rate": 0.0,
        },
        "repair_targets": None,
        "preview": {"identity_sha256": "b" * 64, "render_variant": "step"},
        "change_from_parent": {
            "no_observable_geometry_change": False,
            "parent_accepted": False,
        },
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

    def publish_step_zero_from_candidate(
        self,
        _workspace: Path,
        *,
        attempt: int,
        source: Path,
        evidence_provider,
    ) -> dict:
        self.published.append(
            {
                "kind": "step_zero",
                "attempt": attempt,
                "source": source,
                "provider": evidence_provider,
            }
        )
        return {"step": 0}

    def publish_cycle_from_candidate(
        self, _workspace: Path, *, attempt: int, source: Path, evidence_provider
    ) -> dict:
        self.completed_cycles += 1
        self.published.append(
            {
                "kind": "repair",
                "attempt": attempt,
                "source": source,
                "provider": evidence_provider,
            }
        )
        return {"step": {"step": self.completed_cycles}, "cycle": self.completed_cycles}

    def read_current_step_decision_facts(
        self, _workspace: Path, *, step: int
    ) -> dict:
        return _decision_facts_stub(step)

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

    def finalize_from_agent_selection_claim(self, _workspace: Path, **kwargs) -> dict:
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
            step_zero_evidence_provider=lambda request: None,
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
        self.assertEqual(self.sup.candidate_root / "work", observed["cwd"])
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
        for literal in (
            "work/attempts",
            "agent-finalization",
            "final/manifest.json",
            "steps/",
            "attempt-",
        ):
            self.assertNotIn(literal, source)
        self.assertIn("publish_step_zero_from_candidate", source)
        self.assertIn("publish_cycle_from_candidate", source)
        self.assertIn("seed_repair_source_from_parent_step", source)
        self.assertNotIn("publish_step_zero_from_agent", source)
        self.assertNotIn("publish_cycle_from_agent", source)
        self.assertIn("finalize_from_agent_selection_claim", source)
        self.assertNotIn("finalize_agent_submission", source)

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
        # Serially exhaust the Attempt budget through the fixed
        # /candidate/work subtree.  Each start_attempt requires the
        # prior Attempt to be retired first, matching the
        # single-active-attempt contract the fixed work tree enforces.
        # Verify the seventh and eighteenth bundles bind to their actual
        # returned Attempt identifier at the moment the Attempt is live
        # before retirement invalidates the capability.
        for index in range(18):
            started = self.sup.start_attempt(
                self.sup.workspace_handle, plan_handle, None
            )
            attempt_id = index + 1
            bundle = started["capability_bundle_handle"]
            self.assertIsNotNone(
                self.sup.registry.resolve(
                    bundle, "attempt_capabilities", attempt_id=attempt_id
                )
            )
            with self.assertRaises(SupervisorError):
                self.sup.registry.resolve(
                    bundle,
                    "attempt_capabilities",
                    attempt_id=attempt_id + 1,
                )
            self.sup.submit_step_zero(
                self.sup.workspace_handle,
                started["attempt_handle"],
                started["candidate_handle"],
            )
            # Retirement revokes the bundle so a stale handle from a
            # completed Attempt cannot cross into the next Attempt.
            with self.assertRaises(SupervisorError):
                self.sup.registry.resolve(
                    bundle, "attempt_capabilities", attempt_id=attempt_id
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

    def test_runner_terminal_publisher_recovers_handoff_without_recompiling(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from scripts.pilot import runner

        class TerminalAPI:
            def __init__(self) -> None:
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
            api = TerminalAPI()
            with mock.patch.object(runner, "_load_workspace_api", return_value=api):
                first = runner.persist_terminal_validation(workspace)
            (workspace / "run/terminal-validation-locator.json").unlink()
            with mock.patch.object(runner, "_load_workspace_api", return_value=api):
                recovered = runner.persist_terminal_validation(workspace)
            self.assertEqual(first, recovered)
            self.assertEqual(1, api.compiles)
            self.assertEqual(2, api.writes)

            locator_path = workspace / "run/terminal-validation-locator.json"
            locator_path.write_text(
                json.dumps(
                    {
                        "schema": runner.TERMINAL_LOCATOR_SCHEMA,
                        "handoff_layout": runner.TERMINAL_HANDOFF_LAYOUT,
                        "unexpected": True,
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(runner, "_load_workspace_api", return_value=api):
                with self.assertRaisesRegex(
                    runner.PilotError, "terminal_locator_conflict"
                ):
                    runner.persist_terminal_validation(workspace)
            self.assertEqual(1, api.compiles)

            concurrent_workspace = root / "concurrent-exp"
            concurrent_workspace.mkdir()
            (concurrent_workspace / "workspace.json").write_text(
                "{}", encoding="utf-8"
            )
            concurrent_api = TerminalAPI()
            original_compile = concurrent_api.compile_terminal_validation

            def slow_compile(workspace: Path) -> dict:
                time.sleep(0.05)
                return original_compile(workspace)

            concurrent_api.compile_terminal_validation = slow_compile  # type: ignore[method-assign]
            with mock.patch.object(
                runner, "_load_workspace_api", return_value=concurrent_api
            ):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(
                        pool.map(
                            runner.persist_terminal_validation,
                            [concurrent_workspace, concurrent_workspace],
                        )
                    )
            self.assertEqual(results[0], results[1])
            self.assertEqual(1, concurrent_api.compiles)
            self.assertEqual(1, concurrent_api.writes)

    def test_terminal_publication_without_fcntl_fails_before_mutation(self) -> None:
        from scripts.pilot import runner

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "exp"
            workspace.mkdir()
            marker = workspace / "workspace.json"
            marker.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(runner, "fcntl", None),
                mock.patch.object(
                    runner,
                    "_load_workspace_api",
                    side_effect=AssertionError("Workspace API loaded"),
                ),
            ):
                with self.assertRaisesRegex(
                    runner.PilotError, "terminal_publication_unavailable"
                ):
                    runner.persist_terminal_validation(workspace)
            self.assertEqual([marker], list(workspace.iterdir()))
            self.assertFalse((root / ".internal-terminal-validation").exists())

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
            self.assertFalse(list(target.parent.glob(".terminal-validation.json.tmp-*")))

    def test_terminal_locator_facade_rejects_bundle_or_identity_payloads(self) -> None:
        workspace_root = (
            Path(__file__).resolve().parents[3]
            / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace"
        )
        if str(workspace_root) not in sys.path:
            sys.path.insert(0, str(workspace_root))
        spec = importlib.util.spec_from_file_location(
            "closed_locator_facade", workspace_root / "workspace.py"
        )
        assert spec is not None and spec.loader is not None
        facade = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = facade
        spec.loader.exec_module(facade)

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "exp"
            (workspace / "run").mkdir(parents=True)
            (workspace / "workspace.json").write_text("{}", encoding="utf-8")

            # Legacy v1 locator with a self-authenticating bundle+identity.
            with self.assertRaises(facade.WorkspaceError):
                facade.write_terminal_locator(
                    workspace,
                    {
                        "schema": "mesh-to-cad.terminal-validation-locator/1",
                        "expected_identity": "a" * 64,
                        "bundle": {"schema": "b/1"},
                    },
                )
            # v2 marker augmented with a smuggled bundle: extra keys must fail.
            with self.assertRaises(facade.WorkspaceError):
                facade.write_terminal_locator(
                    workspace,
                    {
                        "schema": facade.TERMINAL_LOCATOR_SCHEMA,
                        "handoff_layout": facade.TERMINAL_HANDOFF_LAYOUT,
                        "bundle": {"schema": "b/1"},
                    },
                )
            # v2 marker with a foreign handoff layout must fail.
            with self.assertRaises(facade.WorkspaceError):
                facade.write_terminal_locator(
                    workspace,
                    {
                        "schema": facade.TERMINAL_LOCATOR_SCHEMA,
                        "handoff_layout": "attacker-controlled/1",
                    },
                )
            self.assertFalse(
                (workspace / "run/terminal-validation-locator.json").exists()
            )

            # The exact minimal marker publishes atomically.
            facade.write_terminal_locator(
                workspace,
                {
                    "schema": facade.TERMINAL_LOCATOR_SCHEMA,
                    "handoff_layout": facade.TERMINAL_HANDOFF_LAYOUT,
                },
            )
            self.assertTrue(
                (workspace / "run/terminal-validation-locator.json").is_file()
            )

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

    def test_runtime_import_failure_names_only_the_fixed_module(self) -> None:
        from scripts.pilot import candidate_runtime as runtime_module

        runtime = self.root / "diagnostic-runtime"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "bin/python").write_bytes(b"python")
        (runtime / "lib/python3.12/site-packages").mkdir(parents=True)
        secret = "/home/build-user/private/libcad.so"

        for failed in runtime_module.CAD_RUNTIME_IMPORTS:
            with self.subTest(failed=failed):
                def result(argv, **_kwargs):
                    module = argv[2].removeprefix("import ")
                    return subprocess.CompletedProcess(
                        argv,
                        1 if module == failed else 0,
                        stdout=b"",
                        stderr=f"loader error: {secret}".encode(),
                    )

                with mock.patch.object(runtime_module.subprocess, "run", side_effect=result):
                    with self.assertRaises(runtime_module.CandidateRuntimeError) as caught:
                        runtime_module.validate_candidate_runtime(runtime)
                self.assertEqual(
                    f"candidate_runtime_import_failed:{failed}", str(caught.exception)
                )
                self.assertNotIn(secret, str(caught.exception))
                self.assertNotIn("loader error", str(caught.exception))

    def test_runtime_fixed_import_validation_preserves_success(self) -> None:
        from scripts.pilot import candidate_runtime as runtime_module

        runtime = self.root / "successful-runtime"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "bin/python").write_bytes(b"python")
        (runtime / "lib/python3.12/site-packages").mkdir(parents=True)
        completed = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with mock.patch.object(
            runtime_module.subprocess, "run", return_value=completed
        ) as run:
            runtime_module.validate_candidate_runtime(runtime)
        self.assertEqual(len(runtime_module.CAD_RUNTIME_IMPORTS), run.call_count)
        self.assertEqual(
            [f"import {name}" for name in runtime_module.CAD_RUNTIME_IMPORTS],
            [call.args[0][2] for call in run.call_args_list],
        )
        failed = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"host path")
        with mock.patch.object(runtime_module.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(
                runtime_module.CandidateRuntimeError,
                "^candidate_runtime_import_failed$",
            ):
                runtime_module.validate_candidate_runtime(
                    runtime, required_imports=("custom_module",)
                )

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
            (stdlib / "os.py").write_text(
                "name = 'safe'\nsystem_prefix = '/usr'\n", encoding="utf-8"
            )
            (stdlib / "Makefile").write_text(
                "LIBDIR=/usr/lib64\nINCLUDEDIR=/usr/include\n", encoding="utf-8"
            )
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
                interpreter=fake_python,
                base_prefix=Path("/usr"),
                exec_prefix=Path("/usr"),
                libdir=Path("/usr/lib64"),
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
            self.assertIn(
                b"system_prefix = '/usr'",
                (runtime / "lib/python3.12/os.py").read_bytes(),
            )
            self.assertEqual(
                b"LIBDIR=/usr/lib64\nINCLUDEDIR=/usr/include\n",
                (runtime / "lib/python3.12/Makefile").read_bytes(),
            )
            for path in runtime.rglob("*"):
                self.assertFalse(path.is_symlink())
                if path.is_file():
                    self.assertNotIn(os.fsencode(repo), path.read_bytes())
                    self.assertNotIn(os.fsencode(venv), path.read_bytes())

            for label, leaked_path in (("repo", repo), ("venv", venv)):
                with self.subTest(leaked_path=label):
                    (stdlib / "os.py").write_text(
                        f"leaked = {os.fspath(leaked_path.resolve())!r}\n",
                        encoding="utf-8",
                    )
                    with mock.patch.object(runtime_module, "_probe", return_value=probe):
                        with self.assertRaisesRegex(
                            runner.CandidateRuntimeError,
                            "candidate_runtime_host_path_leak",
                        ):
                            runner.materialize_candidate_runtime(
                                venv,
                                root / f"candidate-runtime-{label}-leak",
                                repo_root=repo,
                            )
            (stdlib / "os.py").write_text(
                "name = 'safe'\nsystem_prefix = '/usr'\n", encoding="utf-8"
            )
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
                        (
                            runtime_module.FileRecord(
                                "cad.py", len(b"CAD = True\n"), "0" * 64
                            ),
                        ),
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
            self.assertFalse(
                any(
                    path.name.startswith(".") and ".tmp-" in path.name
                    for path in concurrent_cache.iterdir()
                )
            )

    def test_runtime_binds_stable_post_install_native_bytes(self) -> None:
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
            interpreter = root / "external/bin/python3"
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            interpreter.chmod(0o755)
            (venv / "bin/python").symlink_to(interpreter)
            (venv / "pyvenv.cfg").write_text("version = 3.12.1\n", encoding="utf-8")
            native = site / "cad_native.so"
            installed = b"stable stripped native bytes"
            wheel = b"larger unstripped wheel native bytes"
            native.write_bytes(installed)
            probe = runtime_module.RuntimeProbe(
                "3.12",
                stdlib,
                stdlib,
                site,
                site,
                None,
                distributions=(
                    runtime_module.DistributionRecord(
                        "cad-native",
                        "1",
                        site,
                        ("cad_native.so",),
                        "c" * 64,
                        (
                            runtime_module.FileRecord(
                                "cad_native.so",
                                len(wheel),
                                hashlib.sha256(wheel).hexdigest(),
                            ),
                        ),
                    ),
                ),
            )
            patches = (
                mock.patch.object(runtime_module, "_probe", return_value=probe),
                mock.patch.object(runtime_module, "validate_candidate_runtime"),
                mock.patch.object(runtime_module, "_relocate_native"),
                mock.patch.object(runtime_module, "_parse_tool_dependencies", return_value=[]),
            )
            with patches[0], patches[1], patches[2], patches[3]:
                first = runner.materialize_candidate_runtime(
                    venv, root / "cache", repo_root=repo
                )
            copied = first / "lib/python3.12/site-packages/cad_native.so"
            self.assertEqual(installed, copied.read_bytes())

            changed = b"stable stripped native bytes v2"
            native.write_bytes(changed)
            with (
                mock.patch.object(runtime_module, "_probe", return_value=probe),
                mock.patch.object(runtime_module, "validate_candidate_runtime"),
                mock.patch.object(runtime_module, "_relocate_native"),
                mock.patch.object(runtime_module, "_parse_tool_dependencies", return_value=[]),
            ):
                second = runner.materialize_candidate_runtime(
                    venv, root / "cache", repo_root=repo
                )
            self.assertNotEqual(first.identity, second.identity)
            self.assertNotEqual(
                json.loads((first / runtime_module._MARKER_NAME).read_text())["manifest_sha256"],
                json.loads((second / runtime_module._MARKER_NAME).read_text())["manifest_sha256"],
            )
            self.assertEqual(
                changed,
                (second / "lib/python3.12/site-packages/cad_native.so").read_bytes(),
            )

            original_copy = runtime_module._copy_file_stream
            mutated = False

            def mutate_after_inventory(*args, **kwargs):
                nonlocal mutated
                relative = args[1]
                if relative.as_posix() == "cad_native.so" and not mutated:
                    mutated = True
                    native.write_bytes(b"concurrent installed mutation")
                return original_copy(*args, **kwargs)

            native.write_bytes(installed)
            with (
                mock.patch.object(runtime_module, "_probe", return_value=probe),
                mock.patch.object(runtime_module, "validate_candidate_runtime"),
                mock.patch.object(runtime_module, "_relocate_native"),
                mock.patch.object(runtime_module, "_parse_tool_dependencies", return_value=[]),
                mock.patch.object(
                    runtime_module, "_copy_file_stream", side_effect=mutate_after_inventory
                ),
            ):
                with self.assertRaisesRegex(
                    runtime_module.CandidateRuntimeError,
                    "candidate_runtime_(distribution_drift|source_changed)",
                ):
                    runner.materialize_candidate_runtime(
                        venv, root / "concurrent-cache", repo_root=repo
                    )

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
        from scripts.pilot import agent_source_projection, runner
        from tests.python.support.paths import REPO_ROOT

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
            # Stage and bundle the fixed projection without binding host-wide
            # state into the runner test.
            for source_rel, _ in agent_source_projection.SOURCE_MAPPINGS:
                source_path = REPO_ROOT / source_rel
                destination = root / source_rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source_path.read_bytes())
            projection_target = (
                root / agent_source_projection.PROJECTION_ROOT_REL
            )
            agent_source_projection.bundle(root, projection_target)
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
                    )
            finally:
                bridge_socket.close()
                socket_path.unlink(missing_ok=True)
            triples = [argv[i : i + 3] for i in range(len(argv) - 2)]
            self.assertNotIn(os.fspath(exp.resolve()), argv)
            self.assertNotIn(os.fspath(input_path.resolve()), argv)
            self.assertNotIn(
                ["--ro-bind", os.fspath(venv.resolve()), "/workspace/repo/.venv"],
                triples,
            )
            self.assertIn(
                ["--bind", os.fspath(candidate.resolve()), "/candidate"],
                triples,
            )
            self.assertIn("/agent-surface/client.py", argv)
            self.assertIn("/run/mesh-to-cad-agent-surface.sock", argv)
            self.assertNotIn(
                ["--ro-bind", "/etc", "/etc"],
                triples,
            )
            # The Agent Source Projection is the ONLY skill source visible to
            # the Agent, mounted read-only at the stable sandbox path
            # /workspace/repo/skills. The full installed plugin cache, publish
            # tree, and per-skill enumeration MUST not appear in argv.
            projected_skills = agent_source_projection.projected_skills_root(
                projection_target
            ).resolve()
            self.assertIn(
                [
                    "--ro-bind",
                    os.fspath(projected_skills),
                    "/workspace/repo/skills",
                ],
                triples,
            )
            self.assertNotIn(
                os.fspath(runner.SANDBOX_PUBLISH_TREE),
                {triple[2] for triple in triples if len(triple) == 3},
            )
            for skill_id in (
                "mesh-to-cad",
                "cad",
                "mesh-compare",
                "mesh-inspect",
                "cad-viewer",
            ):
                per_skill_target = f"/workspace/repo/skills/{skill_id}"
                # No per-skill --ro-bind should exist beyond the single
                # projection bind at /workspace/repo/skills.
                for triple in triples:
                    if (
                        len(triple) == 3
                        and triple[0] == "--ro-bind"
                        and triple[2] == per_skill_target
                    ):
                        raise AssertionError(
                            f"per-skill mount leaked into isolated argv: {triple}"
                        )
            # The candidate CODEX_HOME is a fresh writable directory carrying
            # only a minimal config.toml, not the full authority codex home.
            job_codex_home = (exp / "run" / ".codex-home").resolve()
            self.assertIn(
                ["--bind", os.fspath(job_codex_home), "/home/pilot/.codex"],
                triples,
            )
            codex_home_children = sorted(
                child.name for child in job_codex_home.iterdir()
            )
            self.assertEqual(codex_home_children, ["config.toml"])
            # Absolute Workspace Authority / plugin publish tree host paths
            # must never appear in argv.
            for token in argv:
                self.assertNotIn(".text-to-cad-codex/deployments", token)
                self.assertNotIn(".plugin-publish-tree", token)
            # The Agent Surface client is mounted from the projection root,
            # not from the repository's scripts/pilot/ source path. Anything
            # else means the runner is still exposing the trusted client
            # source to the isolated Agent.
            projected_client = agent_source_projection.projected_agent_surface_client(
                projection_target
            ).resolve()
            self.assertIn(
                [
                    "--ro-bind",
                    os.fspath(projected_client),
                    "/agent-surface/client.py",
                ],
                triples,
            )
            repo_client = REPO_ROOT / "scripts/pilot/agent_surface_client.py"
            self.assertNotIn(os.fspath(repo_client), argv)
            self.assertNotIn(os.fspath(repo_client.resolve()), argv)

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
        step_zero = self.sup.submit_step_zero(
            self.sup.workspace_handle, attempt, candidate
        )
        self.assertEqual("published", step_zero["state"])
        # W1 facade owns publication and receives only the trusted candidate
        # tree plus the fixed Step 0 evidence provider; the supervisor never
        # forwarded Agent-named evidence handles.
        self.assertEqual(1, len(self.workspace.published))
        published = self.workspace.published[0]
        self.assertEqual(
            {
                "kind": "step_zero",
                "attempt": 1,
                "source": self.sup.candidate_root / "work",
            },
            {k: v for k, v in published.items() if k != "provider"},
        )
        self.assertTrue(callable(published["provider"]))
        # The Agent-authored selection.json now carries only the closed
        # semantic claim schema; trusted evidence, considered-step lists,
        # accepted facts, and preview identities are constructed inside W1
        # from the opaque Selected Step handle plus canonical workspace
        # authority.
        selection = self.sup.candidate_root / "selection.json"
        selection.write_text(
            json.dumps(
                {
                    "schema": "mesh-to-cad.agent-selection-claim/1",
                    "preview_observation": "Preview matches intent.",
                    "stop_reason": "cycle_limit",
                    "conflict": False,
                    "conflict_details": None,
                    "rationale": "Only head available under budget.",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        notes = self.sup.candidate_root / "notes.md"
        notes.write_text("## Input\n", encoding="utf-8")
        selection_handle = self.sup.register_selection(selection)
        notes_handle = self.sup.register_notes(notes)
        final = self.sup.select_and_finalize(
            self.sup.workspace_handle,
            step_zero["step_handle"],
            selection_handle,
            notes_handle,
        )
        self.assertEqual("finalized", final["state"])
        self.assertEqual(1, self.workspace.finalize_calls)

    def _prepare_finalize_inputs(self, *, claim: dict) -> tuple[str, str]:
        selection = self.sup.candidate_root / "selection.json"
        selection.write_text(json.dumps(claim, sort_keys=True) + "\n", encoding="utf-8")
        notes = self.sup.candidate_root / "notes.md"
        notes.write_text("## Input\n", encoding="utf-8")
        return (
            self.sup.register_selection(selection),
            self.sup.register_notes(notes),
        )

    def _valid_agent_claim(self) -> dict:
        return {
            "schema": "mesh-to-cad.agent-selection-claim/1",
            "preview_observation": "Preview matches intent.",
            "stop_reason": "cycle_limit",
            "conflict": False,
            "conflict_details": None,
            "rationale": "Only head available under budget.",
        }

    def test_select_and_finalize_rejects_wrong_kind_or_cross_supervisor_step_handle(
        self,
    ) -> None:
        # Only opaque supervisor-issued step handles are accepted; a
        # workspace handle in the step slot, a step handle from a foreign
        # supervisor, and a value with wrong prefix are all refused.
        attempt, candidate = self._start()
        self.sup.submit_step_zero(self.sup.workspace_handle, attempt, candidate)
        selection_handle, notes_handle = self._prepare_finalize_inputs(
            claim=self._valid_agent_claim()
        )
        with self.assertRaises(SupervisorError):
            self.sup.select_and_finalize(
                self.sup.workspace_handle,
                self.sup.workspace_handle,
                selection_handle,
                notes_handle,
            )
        with self.assertRaises(SupervisorError):
            self.sup.select_and_finalize(
                self.sup.workspace_handle,
                "h:not-a-real-handle",
                selection_handle,
                notes_handle,
            )
        other = WorkspaceSupervisor(
            self.workspace_root,
            candidate_root=self.root / "candidate-cross",
            staging_dir=self.root / "staging-cross",
            workspace_api=self.workspace,
            step_zero_evidence_provider=lambda request: None,
        )
        try:
            other_attempt, other_candidate = (
                self._start_on(other)
            )
            other.submit_step_zero(other.workspace_handle, other_attempt, other_candidate)
            foreign_step = next(
                token
                for token, record in other.registry._records.items()
                if record.kind == "step"
            )
            with self.assertRaises(SupervisorError):
                self.sup.select_and_finalize(
                    self.sup.workspace_handle,
                    foreign_step,
                    selection_handle,
                    notes_handle,
                )
        finally:
            other.close()

    def _start_on(self, supervisor: WorkspaceSupervisor) -> tuple[str, str]:
        plan = supervisor.candidate_root / "plan.json"
        plan.write_text("{}", encoding="utf-8")
        plan_handle = supervisor.register_plan(plan)
        result = supervisor.start_attempt(supervisor.workspace_handle, plan_handle, None)
        return result["attempt_handle"], result["candidate_handle"]

    def test_select_and_finalize_forwards_step_and_claim_paths_to_w1_only(
        self,
    ) -> None:
        # The supervisor never touches workspace authority; it forwards the
        # resolved Selected Step ordinal plus the Agent-authored claim and
        # notes handles unchanged, and W1 receives no numeric step from
        # the Agent surface itself.
        attempt, candidate = self._start()
        step_zero = self.sup.submit_step_zero(
            self.sup.workspace_handle, attempt, candidate
        )
        selection_handle, notes_handle = self._prepare_finalize_inputs(
            claim=self._valid_agent_claim()
        )
        observed: dict = {}
        original = self.workspace.finalize_from_agent_selection_claim

        def spy(_workspace, **kwargs):
            observed.update(kwargs)
            return original(_workspace, **kwargs)

        self.workspace.finalize_from_agent_selection_claim = spy  # type: ignore[assignment]
        result = self.sup.select_and_finalize(
            self.sup.workspace_handle,
            step_zero["step_handle"],
            selection_handle,
            notes_handle,
        )
        self.assertEqual("finalized", result["state"])
        self.assertEqual(0, observed["selected_step"])
        self.assertEqual("selection.json", observed["selection"])
        self.assertEqual("notes.md", observed["notes"])

    def test_submit_intents_return_closed_w1_decision_facts(self) -> None:
        # submit_step_zero returns the bounded decision facts the W1 facade
        # published for the newly written Measured Step.  The supervisor
        # does not parse authority documents itself.
        attempt, candidate = self._start()
        step_zero = self.sup.submit_step_zero(
            self.sup.workspace_handle, attempt, candidate
        )
        self.assertIn("decision_facts", step_zero)
        facts = step_zero["decision_facts"]
        self.assertEqual("mesh-to-cad.decision-facts/1", facts["schema"])
        self.assertEqual(0, facts["step_ordinal"])
        self.assertIsNone(facts["parent_step_ordinal"])
        self.assertIsNone(facts["change_from_parent"])
        self.assertEqual("unaccepted", facts["acceptance_state"])
        self.assertFalse(facts["accepted"])
        self.assertIsInstance(facts["repair_targets"], dict)
        self.assertLessEqual(facts["repair_targets"]["returned"], 8)
        self.assertEqual({"identity_sha256", "render_variant"}, set(facts["preview"]))

        # Simulate one already-committed cycle so the mock's synthetic
        # next-step counter advances into repair territory, then run one
        # submit_repair and verify the parent-change comparison lands on
        # the Agent-visible response.
        def seeder(_workspace, *, attempt, from_step, destination):
            (destination / "source").mkdir()

        self.workspace.seed_repair_source_from_parent_step = seeder  # type: ignore[assignment]
        self.workspace.completed_cycles = 1
        self.sup._repair_evidence_provider = lambda request: None  # type: ignore[attr-defined]
        plan = self.sup.candidate_root / "repair-plan.json"
        plan.write_text("{}", encoding="utf-8")
        plan_handle = self.sup.register_plan(plan)
        parent_handle = self.sup.registry.issue("step", 1)
        started = self.sup.start_attempt(
            self.sup.workspace_handle, plan_handle, parent_handle
        )
        repair = self.sup.submit_repair(
            self.sup.workspace_handle,
            started["attempt_handle"],
            started["candidate_handle"],
        )
        self.assertIn("decision_facts", repair)
        repair_facts = repair["decision_facts"]
        self.assertGreater(repair_facts["step_ordinal"], 0)
        self.assertEqual(
            repair_facts["step_ordinal"] - 1, repair_facts["parent_step_ordinal"]
        )
        self.assertEqual("acceptance_satisfied", repair_facts["acceptance_state"])
        self.assertTrue(repair_facts["accepted"])
        self.assertIsNone(repair_facts["repair_targets"])
        change = repair_facts["change_from_parent"]
        self.assertEqual(
            {"no_observable_geometry_change", "parent_accepted"}, set(change)
        )

        # No workspace-relative or authority-attempt tokens leak through.
        serialized = json.dumps({"step_zero": step_zero, "repair": repair})
        for literal in (
            os.fspath(self.workspace_root),
            os.fspath(self.root),
            "steps/000000",
            "voxblame",
            "measurement.json",
            "attempt-000001",
            "target_key",
            "mask_sha256",
            "observable_sha256",
            "work/attempts",
        ):
            self.assertNotIn(literal, serialized)

    def test_submit_intents_fail_closed_on_broken_decision_facts_projection(
        self,
    ) -> None:
        # If W1 raises inside the projection, the supervisor surfaces one
        # closed classification and does not silently drop decision facts.
        attempt, candidate = self._start()

        def broken(_workspace, *, step):
            raise RuntimeError("projection collapsed")

        self.workspace.read_current_step_decision_facts = broken  # type: ignore[assignment]
        with self.assertRaises(SupervisorError) as raised:
            self.sup.submit_step_zero(
                self.sup.workspace_handle, attempt, candidate
            )
        self.assertEqual(
            "decision_facts_unavailable", raised.exception.classification
        )

    def test_submit_intents_reject_stale_or_cross_attempt_handles(self) -> None:
        # start_attempt refuses a concurrent second Attempt: the current
        # work subtree is fixed, so a live Attempt must be submitted and
        # retired before another can open.
        first_attempt, first_candidate = self._start()
        with self.assertRaises(SupervisorError):
            self._start()
        # Publish Attempt 1; its handles are retired and its bytes are
        # cleared before Attempt 2's fresh work tree is created.
        self.assertEqual(
            "published",
            self.sup.submit_step_zero(
                self.sup.workspace_handle, first_attempt, first_candidate
            )["state"],
        )
        second_attempt, second_candidate = self._start()
        # Stale Attempt 1 candidate handle cannot cross into Attempt 2,
        # even though the fixed work subtree path is now Attempt 2's.
        with self.assertRaises(SupervisorError):
            self.sup.submit_step_zero(
                self.sup.workspace_handle, second_attempt, first_candidate
            )
        with self.assertRaises(SupervisorError):
            self.sup.submit_step_zero(
                self.sup.workspace_handle, first_attempt, second_candidate
            )
        # Publish Attempt 2 and verify its retired handles are also rejected.
        self.assertEqual(
            "published",
            self.sup.submit_step_zero(
                self.sup.workspace_handle, second_attempt, second_candidate
            )["state"],
        )
        with self.assertRaises(SupervisorError):
            self.sup.submit_step_zero(
                self.sup.workspace_handle, second_attempt, second_candidate
            )
        with self.assertRaises(SupervisorError):
            self.sup.submit_repair(
                self.sup.workspace_handle, second_attempt, second_candidate
            )

    def test_current_work_tree_is_fixed_and_agent_visible_without_attempt_id(self) -> None:
        # start_attempt binds the candidate handle to the fixed
        # <candidate_root>/work subtree.  The nested candidate-tool
        # sandbox binds that same host path to /candidate, so the
        # registered argv stays candidate-relative and the Agent never
        # sees an Attempt-identified directory name.
        attempt, candidate = self._start()
        work = self.sup.candidate_root / "work"
        self.assertTrue(work.is_dir())
        bound_candidate = self.sup.registry.resolve(
            candidate, "candidate", attempt_id=1
        )
        self.assertEqual(work, bound_candidate)
        # Agent authors under work/source/model.py — the fixed relative
        # argv resolves under the bound work tree.
        (work / "source").mkdir()
        (work / "source" / "model.py").write_text("pass\n", encoding="utf-8")
        operation = self.sup.register_operation(
            ["/runtime/bin/python", "source/model.py"], attempt_handle=attempt
        )
        observed: dict = {}

        def run(argv, **kwargs):
            observed["args"] = argv
            observed["cwd"] = kwargs.get("cwd")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        self.sup._command_runner = run
        self.sup.run_candidate_tool(
            self.sup.workspace_handle, attempt, candidate, operation
        )
        self.assertEqual(work, observed["cwd"])
        self.assertEqual(["/runtime/bin/python", "source/model.py"], observed["args"])

    def test_second_attempt_receives_fresh_work_tree_free_of_prior_bytes(self) -> None:
        # Attempt 1 writes into /candidate/work; after submit the tree
        # is retired.  Attempt 2 must start with a fresh empty work
        # tree — the prior Attempt's bytes must not leak across the
        # single fixed subtree.
        first_attempt, first_candidate = self._start()
        work = self.sup.candidate_root / "work"
        (work / "source").mkdir()
        (work / "source" / "model.py").write_text(
            "prior = 1\n", encoding="utf-8"
        )
        (work / "artifacts").mkdir()
        (work / "artifacts" / "stale.step").write_text(
            "stale", encoding="utf-8"
        )
        self.sup.submit_step_zero(
            self.sup.workspace_handle, first_attempt, first_candidate
        )
        self.assertFalse(work.exists())
        second_attempt, second_candidate = self._start()
        self.assertTrue(work.is_dir())
        self.assertEqual([], list(work.iterdir()))
        # The retired candidate handle from Attempt 1 must not resolve.
        with self.assertRaises(SupervisorError):
            self.sup.registry.resolve(
                first_candidate, "candidate", attempt_id=1
            )

    def test_forged_attempt_named_sibling_does_not_pose_as_current_work(self) -> None:
        # A stale or malicious directory next to /candidate/work must not
        # influence the current Attempt: the supervisor only binds the
        # fixed work path.
        sibling = self.sup.candidate_root / "attempt-000001"
        sibling.mkdir()
        (sibling / "model.py").write_text("attacker", encoding="utf-8")
        attempt, candidate = self._start()
        bound = self.sup.registry.resolve(candidate, "candidate", attempt_id=1)
        self.assertEqual(self.sup.candidate_root / "work", bound)
        self.assertNotEqual(sibling.resolve(), bound.resolve())
        # The sibling still exists on disk but plays no role in the Attempt.
        self.assertTrue(sibling.exists())
        self.assertEqual(
            "attacker",
            (sibling / "model.py").read_text(encoding="utf-8"),
        )

    def test_repair_start_attempt_invokes_w1_seed_with_fresh_external_work(self) -> None:
        # For from_step != None the supervisor calls the W1 facade's
        # seed operation with only the attempt id, from_step, and the
        # fresh external work tree destination.  The supervisor never
        # reads, forwards, or interprets a ``steps/…`` authority path.
        self.workspace.completed_cycles = 1
        seed_calls: list[dict] = []

        def seeder(_workspace, *, attempt, from_step, destination):
            self.assertIsInstance(destination, Path)
            self.assertEqual(self.sup.candidate_root / "work", destination)
            self.assertEqual([], list(destination.iterdir()))
            seed_calls.append(
                {"attempt": attempt, "from_step": from_step, "destination": destination}
            )
            (destination / "source").mkdir()
            (destination / "source" / "model.py").write_text(
                "seeded = True\n", encoding="utf-8"
            )

        self.workspace.seed_repair_source_from_parent_step = seeder
        plan = self.sup.candidate_root / "repair-plan.json"
        plan.write_text("{}", encoding="utf-8")
        plan_handle = self.sup.register_plan(plan)
        parent_handle = self.sup.registry.issue("step", 0)
        started = self.sup.start_attempt(
            self.sup.workspace_handle, plan_handle, parent_handle
        )
        self.assertEqual(1, len(seed_calls))
        self.assertEqual(0, seed_calls[0]["from_step"])
        self.assertEqual(1, seed_calls[0]["attempt"])
        work = self.sup.candidate_root / "work"
        self.assertTrue((work / "source" / "model.py").is_file())
        # Supervisor never passes authority ``steps/…`` paths to the
        # workspace API for the seed operation.
        for entry in seed_calls:
            for value in entry.values():
                text = os.fspath(value) if isinstance(value, Path) else str(value)
                self.assertNotIn("steps/", text)

    def test_repair_seed_failure_leaves_no_partial_work_tree(self) -> None:
        # A W1 seed failure aborts the Attempt, clears the work tree,
        # and never binds a candidate handle.
        self.workspace.completed_cycles = 1

        def failing_seeder(_workspace, *, attempt, from_step, destination):
            (destination / "half").mkdir()
            (destination / "half" / "bytes.txt").write_text(
                "partial", encoding="utf-8"
            )
            raise RuntimeError("simulated seed failure")

        self.workspace.seed_repair_source_from_parent_step = failing_seeder
        plan = self.sup.candidate_root / "repair-plan.json"
        plan.write_text("{}", encoding="utf-8")
        plan_handle = self.sup.register_plan(plan)
        parent_handle = self.sup.registry.issue("step", 0)
        with self.assertRaises(SupervisorError):
            self.sup.start_attempt(
                self.sup.workspace_handle, plan_handle, parent_handle
            )
        self.assertFalse((self.sup.candidate_root / "work").exists())
        # No active attempt remains; the next start_attempt may proceed.
        self.assertEqual({}, self.sup._attempts)

    def test_start_attempt_rejects_concurrent_active_attempt(self) -> None:
        # There is exactly one current-attempt subtree.  A second
        # start_attempt while an Attempt is already active must fail
        # closed rather than silently reset the live work tree.
        self._start()
        with self.assertRaises(SupervisorError):
            self._start()

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

    def test_bridge_stop_during_active_build_confirms_cancellation_before_lease_release(
        self,
    ) -> None:
        # Runner shuts down in the order bridge.stop → supervisor.close →
        # candidate_runtime_lease.release; bridge.stop must drive
        # supervisor.cancel to completion before the follower can proceed.
        from scripts.pilot.agent_surface_bridge import AgentSurfaceBridge

        attempt, candidate = self._start()
        operation = self.sup.register_operation(
            [sys.executable, "-c", "import time\ntime.sleep(60)\n"],
            attempt_handle=attempt,
        )
        popens: list[subprocess.Popen] = []
        started_process = threading.Event()

        def slow_runner(argv, **kwargs):
            process = subprocess.Popen(
                argv,
                cwd=kwargs.get("cwd"),
                env=kwargs.get("env"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=kwargs.get("start_new_session", False),
            )
            popens.append(process)
            started_process.set()
            return process

        self.sup._command_runner = slow_runner
        socket_path = self.root / "active-build.sock"
        bridge = AgentSurfaceBridge(self.sup.agent_surface(), socket_path)
        bridge.start()
        try:
            builder_error: list[BaseException | None] = [None]

            def build() -> None:
                try:
                    self.sup.run_candidate_tool(
                        self.sup.workspace_handle,
                        attempt,
                        candidate,
                        operation,
                    )
                except BaseException as exc:  # noqa: BLE001
                    builder_error[0] = exc

            builder = threading.Thread(target=build, daemon=True)
            builder.start()
            self.assertTrue(started_process.wait(5))
            deadline = time.monotonic() + 2.0
            while (
                time.monotonic() < deadline
                and self.sup._active_calls != 1
            ):
                time.sleep(0.02)
            self.assertEqual(1, self.sup._active_calls)
            self.assertFalse(self.sup.cancellation_confirmed)
            bridge.stop()
        finally:
            for process in popens:
                if process.poll() is None:
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
        # bridge.stop returned only after supervisor.cancel drained the
        # active tool call; the follower lease-release step therefore
        # observes cancellation as truthfully confirmed.
        self.assertTrue(self.sup.cancellation_confirmed)
        self.assertEqual(0, self.sup._active_calls)
        builder.join(timeout=5)
        self.assertFalse(builder.is_alive())
        self.assertEqual(1, len(popens))
        self.assertIsNotNone(popens[0].poll())

    def test_review_workspace_rejects_workspace_without_terminal_handoff(
        self,
    ) -> None:
        # The runner path publishes the terminal validation handoff only
        # after finalize_workspace succeeds and artifact_manifest is
        # written; a workspace that reached finalize without the runner
        # ever persisting the handoff must fail-closed in review rather
        # than silently mint a bundle.
        review_helper_path = (
            Path(__file__).resolve().parents[3]
            / "skills/mesh-to-cad/scripts/mesh-to-cad-review/__main__.py"
        )
        review_spec = importlib.util.spec_from_file_location(
            "_pilot_review_for_test_adversarial", review_helper_path
        )
        assert review_spec is not None and review_spec.loader is not None
        review_module = importlib.util.module_from_spec(review_spec)
        sys.modules["_pilot_review_for_test_adversarial"] = review_module
        review_spec.loader.exec_module(review_module)
        workspace = self.root / "un-persisted-workspace"
        workspace.mkdir()
        (workspace / "run").mkdir()
        # No locator marker at run/terminal-validation-locator.json and no
        # sibling .internal-terminal-validation handoff either.
        with self.assertRaises(review_module.ReviewError):
            review_module.review_workspace(workspace)

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
    def test_real_bridge_mediated_nine_call_lifecycle_repair_finalize_review(
        self,
    ) -> None:
        # Genuine Step 0 (unaccepted) → Repair (accepted) → honest
        # Finalization → Terminal → Review vertical slice.  Every intent
        # crosses the Agent Surface bridge over the Unix socket; the two
        # trusted canonical builders are the sole source of measurement
        # bytes for both Attempts; the second start_attempt is opened
        # from an opaque parent step handle returned by submit_step_zero.
        from scripts.pilot import runner
        from scripts.pilot.agent_surface_bridge import AgentSurfaceBridge

        repo_root = Path(__file__).resolve().parents[3]
        cli_path = repo_root / "tests/python/skills/mesh-to-cad/test_workspace_cli.py"
        client_path = repo_root / "scripts/pilot/agent_surface_client.py"
        review_helper_path = (
            repo_root / "skills/mesh-to-cad/scripts/mesh-to-cad-review/__main__.py"
        )
        spec = importlib.util.spec_from_file_location(
            "real_workspace_cli_fixture", cli_path
        )
        self.assertIsNotNone(spec and spec.loader)
        fixture_module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(fixture_module)
        case = fixture_module.WorkspaceCliTests(
            "test_run_command_defaults_to_thirty_minute_workspace_budget"
        )
        case.setUp()
        supervisor: WorkspaceSupervisor | None = None
        try:
            # Reference is Box(1.0, 0.01, 0.01); the returned source
            # candidate carries Box(0.8, …) — Step 0 measurement will not
            # match the reference, forcing an authentic Repair Cycle.
            prepared, source_candidate = case.canonical_cad_flow(accepted=False)
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
                repo_root / ".venv",
                case.root / "candidate-runtime",
                repo_root=repo_root,
            )
            from scripts.pilot.step_zero_evidence import (
                real_step_zero_evidence_provider,
            )
            from scripts.pilot.repair_evidence import (
                real_repair_evidence_provider,
            )

            step_zero_calls = 0
            repair_calls = 0

            def counted_step_zero(request):
                nonlocal step_zero_calls
                step_zero_calls += 1
                return real_step_zero_evidence_provider(request)

            def counted_repair(request):
                nonlocal repair_calls
                repair_calls += 1
                return real_repair_evidence_provider(request)

            supervisor = WorkspaceSupervisor(
                case.workspace,
                bind_reference=True,
                candidate_root=case.root / "agent-candidate",
                staging_dir=case.root / "staging",
                rebuild_entrypoint=runner.CAD_REBUILD_ENTRYPOINT,
                geometry_entrypoint=runner.GEOMETRY_ENTRYPOINT,
                tool_registry=registry,
                candidate_runtime=candidate_runtime,
                trusted_tools_root=repo_root,
                step_zero_evidence_provider=counted_step_zero,
                repair_evidence_provider=counted_repair,
            )
            builder_run_count = 0
            original_execute = supervisor._execute_canonical_build

            def counted_execute(context, request):
                nonlocal builder_run_count
                builder_run_count += 1
                return original_execute(context, request)

            supervisor._execute_canonical_build = counted_execute  # type: ignore[assignment]

            socket_path = case.root / "agent-surface.sock"
            bridge = AgentSurfaceBridge(supervisor.agent_surface(), socket_path)

            intents_seen: list[str] = []
            response_bodies: list[str] = []

            def call(intent: str, args: dict) -> dict:
                request = {
                    "schema": "mesh-to-cad.agent-intent/1",
                    "intent": intent,
                    "args": args,
                }
                outcome = subprocess.run(
                    [sys.executable, os.fspath(client_path)],
                    input=json.dumps(request),
                    text=True,
                    capture_output=True,
                    env={
                        **os.environ,
                        "MESH_TO_CAD_AGENT_SURFACE_SOCKET": os.fspath(socket_path),
                    },
                    check=False,
                )
                self.assertEqual(0, outcome.returncode, outcome.stderr)
                payload = json.loads(outcome.stdout)
                self.assertTrue(payload["ok"], payload)
                intents_seen.append(intent)
                response_bodies.append(outcome.stdout)
                return payload["response"]["result"]

            def rewrite_box_length(source_root: Path, length: float) -> None:
                model = source_root / "model.py"
                text = model.read_text(encoding="utf-8")
                # The fixture module emits exactly one Box literal.  The
                # simulated Agent edits the length to steer the next
                # trusted build toward matching the canonical reference.
                model.write_text(
                    re.sub(
                        r"Box\([0-9.]+, 0\.01, 0\.01",
                        f"Box({length}, 0.01, 0.01",
                        text,
                    ),
                    encoding="utf-8",
                )

            bridge.start()
            try:
                contract = supervisor.agent_bootstrap_contract()
                shutil.copy2(
                    case.initial_plan(), supervisor.candidate_root / "plan.json"
                )

                status_result = call(
                    "workspace_status",
                    {"workspace_handle": supervisor.workspace_handle},
                )
                self.assertEqual("ready", status_result["state"])

                observed = call(
                    "observe_reference",
                    {
                        "reference_handle": supervisor.reference_handle,
                        "observation": {"method": "summary", "args": {}},
                    },
                )
                self.assertEqual("summary", observed["observation"]["method"])

                started_one = call(
                    "start_attempt",
                    {
                        "workspace_handle": supervisor.workspace_handle,
                        "plan_handle": contract["plan_handle"],
                    },
                )
                self.assertEqual("started", started_one["state"])
                attempt_one_work = supervisor.candidate_root / "work"
                shutil.copytree(
                    source_candidate / "source",
                    attempt_one_work / "source",
                    dirs_exist_ok=True,
                )

                tool_result_one = call(
                    "run_candidate_tool",
                    {
                        "workspace_handle": supervisor.workspace_handle,
                        "attempt_handle": started_one["attempt_handle"],
                        "candidate_handle": started_one["candidate_handle"],
                        "operation_handle": started_one[
                            "capability_bundle_handle"
                        ],
                    },
                )
                self.assertEqual("completed", tool_result_one["state"])

                published_zero = call(
                    "submit_step_zero",
                    {
                        "workspace_handle": supervisor.workspace_handle,
                        "attempt_handle": started_one["attempt_handle"],
                        "candidate_handle": started_one["candidate_handle"],
                    },
                )
                self.assertEqual("published", published_zero["state"])
                # Step 0 is intentionally unaccepted; a Repair Cycle is
                # the only path forward.
                step_zero_facts = published_zero["decision_facts"]
                self.assertFalse(step_zero_facts["accepted"])
                self.assertEqual("unaccepted", step_zero_facts["acceptance_state"])

                # Attempt 1 handles are retired: replay must fail closed.
                replay = subprocess.run(
                    [sys.executable, os.fspath(client_path)],
                    input=json.dumps(
                        {
                            "schema": "mesh-to-cad.agent-intent/1",
                            "intent": "submit_step_zero",
                            "args": {
                                "workspace_handle": supervisor.workspace_handle,
                                "attempt_handle": started_one["attempt_handle"],
                                "candidate_handle": started_one[
                                    "candidate_handle"
                                ],
                            },
                        }
                    ),
                    text=True,
                    capture_output=True,
                    env={
                        **os.environ,
                        "MESH_TO_CAD_AGENT_SURFACE_SOCKET": os.fspath(
                            socket_path
                        ),
                    },
                    check=False,
                )
                self.assertNotEqual(0, replay.returncode)

                started_two = call(
                    "start_attempt",
                    {
                        "workspace_handle": supervisor.workspace_handle,
                        "plan_handle": contract["plan_handle"],
                        "parent_step_handle": published_zero["step_handle"],
                    },
                )
                self.assertEqual("started", started_two["state"])
                self.assertNotEqual(
                    started_one["attempt_handle"], started_two["attempt_handle"]
                )
                self.assertNotEqual(
                    started_one["candidate_handle"],
                    started_two["candidate_handle"],
                )
                self.assertNotEqual(
                    started_one["capability_bundle_handle"],
                    started_two["capability_bundle_handle"],
                )

                # W1 seeded the fixed /candidate/work with the parent
                # source (Box(0.8)); the simulated Agent authors a fix
                # using only public decision-facts to justify the edit.
                seeded_source_root = supervisor.candidate_root / "work" / "source"
                self.assertTrue((seeded_source_root / "model.py").is_file())
                self.assertIn(
                    "Box(0.8",
                    (seeded_source_root / "model.py").read_text(
                        encoding="utf-8"
                    ),
                )
                rewrite_box_length(seeded_source_root, 1.0)

                # Bounded W1-authenticated assessment claim: the Agent
                # names no path, no argv, and no attempt identifier.
                assessment = supervisor.candidate_root / "work" / "assessment.json"
                assessment.write_text(
                    json.dumps(
                        {
                            "schema": "mesh-to-cad.agent-assessment/1",
                            "parent_acceptance_state": step_zero_facts[
                                "acceptance_state"
                            ],
                            "parent_repair_target_count": step_zero_facts[
                                "repair_targets"
                            ]["returned"],
                            "rationale": (
                                "Length under-target on the primary axis; "
                                "restore to reference bounds."
                            ),
                        },
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )

                tool_result_two = call(
                    "run_candidate_tool",
                    {
                        "workspace_handle": supervisor.workspace_handle,
                        "attempt_handle": started_two["attempt_handle"],
                        "candidate_handle": started_two["candidate_handle"],
                        "operation_handle": started_two[
                            "capability_bundle_handle"
                        ],
                    },
                )
                self.assertEqual("completed", tool_result_two["state"])

                published_repair = call(
                    "submit_repair",
                    {
                        "workspace_handle": supervisor.workspace_handle,
                        "attempt_handle": started_two["attempt_handle"],
                        "candidate_handle": started_two["candidate_handle"],
                    },
                )
                self.assertEqual("published", published_repair["state"])
                repair_facts = published_repair["decision_facts"]
                self.assertTrue(repair_facts["accepted"])
                self.assertEqual(
                    "acceptance_satisfied", repair_facts["acceptance_state"]
                )

                # Attempt 2 handles are retired after submit_repair.
                stale_repair = subprocess.run(
                    [sys.executable, os.fspath(client_path)],
                    input=json.dumps(
                        {
                            "schema": "mesh-to-cad.agent-intent/1",
                            "intent": "submit_repair",
                            "args": {
                                "workspace_handle": supervisor.workspace_handle,
                                "attempt_handle": started_two[
                                    "attempt_handle"
                                ],
                                "candidate_handle": started_two[
                                    "candidate_handle"
                                ],
                            },
                        }
                    ),
                    text=True,
                    capture_output=True,
                    env={
                        **os.environ,
                        "MESH_TO_CAD_AGENT_SURFACE_SOCKET": os.fspath(
                            socket_path
                        ),
                    },
                    check=False,
                )
                self.assertNotEqual(0, stale_repair.returncode)

                selection = supervisor.candidate_root / "selection.json"
                selection.write_text(
                    json.dumps(
                        {
                            "schema": "mesh-to-cad.agent-selection-claim/1",
                            "preview_observation": (
                                "The repair preview matches the reference silhouette."
                            ),
                            "stop_reason": "acceptance_satisfied",
                            "conflict": False,
                            "conflict_details": None,
                            "rationale": (
                                "Repair Cycle satisfied acceptance."
                            ),
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
                selection_handle = supervisor.register_selection(selection)
                notes_handle = supervisor.register_notes(notes)

                final = call(
                    "select_and_finalize",
                    {
                        "workspace_handle": supervisor.workspace_handle,
                        "step_handle": published_repair["step_handle"],
                        "selection_handle": selection_handle,
                        "notes_handle": notes_handle,
                    },
                )
                self.assertEqual("finalized", final["state"])
            finally:
                bridge.stop()
            # bridge.stop → surface.cancel → supervisor.cancel drained
            # all active calls before returning; the lease-release
            # follower can only observe a confirmed cancellation.
            self.assertTrue(supervisor.cancellation_confirmed)

            # Runner-owned terminal handoff: exactly one complete
            # validate_workspace inside compile_terminal_validation.
            runner.write_artifact_manifest(case.workspace, 0, 0)
            pilot_workspace = importlib.import_module(
                "_mesh_to_cad_workspace_for_pilot"
            )
            compile_original = pilot_workspace.compile_terminal_validation
            compile_calls = 0

            def counted_compile(workspace):
                nonlocal compile_calls
                compile_calls += 1
                return compile_original(workspace)

            with mock.patch.object(
                pilot_workspace,
                "compile_terminal_validation",
                side_effect=counted_compile,
            ):
                locator = runner.persist_terminal_validation(case.workspace)
            self.assertEqual(1, compile_calls)
            self.assertTrue(
                locator is not None
                and (case.workspace / locator.sidecar_path).is_file()
            )

            # Review path authenticates the same bundle exactly once
            # through the sibling W1 verifier module.
            review_spec = importlib.util.spec_from_file_location(
                "_pilot_review_for_test", review_helper_path
            )
            assert review_spec is not None and review_spec.loader is not None
            review_module = importlib.util.module_from_spec(review_spec)
            sys.modules["_pilot_review_for_test"] = review_module
            review_spec.loader.exec_module(review_module)
            loaded_verifier = review_module._load_workspace_verifier(None)
            verify_original = loaded_verifier.verify_terminal_validation
            verify_calls = 0

            def counted_verify(workspace, bundle, identity):
                nonlocal verify_calls
                verify_calls += 1
                return verify_original(workspace, bundle, identity)

            with mock.patch.object(
                loaded_verifier,
                "verify_terminal_validation",
                side_effect=counted_verify,
            ):
                code, review = review_module.review_workspace(case.workspace)
            self.assertEqual(0, code)
            self.assertEqual(1, verify_calls)
            self.assertEqual(
                "valid", review["workspace_validation"]["classification"]
            )

            # Trusted providers were the sole source of Step 0 and
            # Repair evidence, driven exactly once each from the bridge.
            self.assertEqual(1, step_zero_calls)
            self.assertEqual(1, repair_calls)
            # Two trusted canonical builds — one per Attempt.
            self.assertEqual(2, builder_run_count)

            # Exactly nine intent calls across seven distinct names;
            # start_attempt and run_candidate_tool each fire twice.
            self.assertEqual(
                [
                    "workspace_status",
                    "observe_reference",
                    "start_attempt",
                    "run_candidate_tool",
                    "submit_step_zero",
                    "start_attempt",
                    "run_candidate_tool",
                    "submit_repair",
                    "select_and_finalize",
                ],
                intents_seen,
            )
            self.assertEqual(7, len(set(intents_seen)))

            # No host, Workspace, raw-reference, or provider argv strings
            # leaked into responses returned across the Unix socket.
            forbidden = (
                os.fspath(case.workspace),
                os.fspath(case.root),
                os.fspath(repo_root / ".venv"),
                "/runtime/bin/python",
                "source/model.py",
                "steps/000000",
                "steps/000001",
                "attempt-000001",
                "attempt-000002",
            )
            for body in response_bodies:
                for literal in forbidden:
                    self.assertNotIn(literal, body)
        finally:
            if supervisor is not None:
                supervisor.close()
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
