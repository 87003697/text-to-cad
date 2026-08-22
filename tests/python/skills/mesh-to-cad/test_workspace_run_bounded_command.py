"""Cross-platform coverage for ``_run_bounded_command``.

The Workspace command runner must terminate the *entire* subprocess
tree when the deadline expires: an unbounded descendant can hang the
Workspace indefinitely and mutate on-disk state past the return. POSIX
achieves this with ``setsid`` + ``killpg``; Windows requires a Job
Object because there is no direct process-group primitive, and the
kill-on-close binding must be attached before the child can execute
any instruction (see ``_spawn_windows_suspended_in_tree``).

These tests exercise:

* The outer contract on the running host (POSIX success + the
  missing-command OSError path);
* The fail-closed setup contract on POSIX with the Windows branch
  forced (kernel32 is unavailable, so ``_run_bounded_command`` must
  refuse rather than degrade to parent-only termination);
* A deterministic launch-pipeline assertion for the Windows spawn
  helper (suspend, assign, resume, in that order, with the
  ``CREATE_SUSPENDED`` creation flag on ``subprocess.Popen``), runnable
  on any host by injecting a mock ``subprocess.Popen`` and a stub
  tree -- this fails if a regression removes suspension or reorders
  the pipeline so the child can execute before job assignment;
* The real Windows subtree kill on the Windows CI runner (spawn a
  descendant, timeout, prove it dies).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[4]
WORKSPACE_CORE_PATH = (
    REPO_ROOT
    / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace/workspace_core.py"
)


def _load_workspace_core():
    spec = importlib.util.spec_from_file_location(
        "mesh_to_cad_workspace_core_run", WORKSPACE_CORE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RunBoundedCommandContractTests(unittest.TestCase):
    """Outer contract that both branches must satisfy on any host."""

    def setUp(self) -> None:
        self.core = _load_workspace_core()

    def test_native_command_completes(self) -> None:
        completed, timed_out = self.core._run_bounded_command(
            [sys.executable, "-c", "pass"],
            cwd=Path.cwd(),
            timeout_seconds=30,
        )
        self.assertFalse(timed_out)
        self.assertEqual(0, completed.returncode)

    def test_missing_command_raises_oserror(self) -> None:
        # ``_run_attempt_command`` relies on OSError to map to exit
        # code 127. That contract must hold on both branches.
        with self.assertRaises(OSError):
            self.core._run_bounded_command(
                ["/definitely/missing/workspace-command"],
                cwd=Path.cwd(),
                timeout_seconds=5,
            )

    def test_windows_branch_fails_closed_when_kernel32_absent(self) -> None:
        # Regression: an earlier iteration of this file returned a
        # ``CompletedProcess`` from the Windows branch on POSIX by
        # spawning without a subtree primitive at all. That was the
        # parent-only fallback the reviewer flagged as P1 -- any
        # descendant would outlive the deadline. The correct
        # fail-closed behaviour is to refuse to run when the kernel-
        # level subtree primitive is unavailable; the caller in
        # ``_run_attempt_command`` maps ``OSError`` to exit code 127.
        with self.assertRaises(OSError):
            self.core._run_bounded_command(
                [sys.executable, "-c", "pass"],
                cwd=Path.cwd(),
                timeout_seconds=5,
                posix=False,
            )


class WindowsProcessTreeSetupTests(unittest.TestCase):
    """Fail-closed setup contract testable on POSIX.

    ``_WindowsProcessTree.create`` is the seam that decides whether the
    Workspace can launch a bounded Windows command at all. On POSIX
    there is no kernel32, so it must raise OSError -- silently
    degrading to parent-only termination is exactly the regression the
    reviewer identified.
    """

    def setUp(self) -> None:
        self.core = _load_workspace_core()

    def test_create_raises_oserror_on_non_windows_host(self) -> None:
        with self.assertRaises(OSError):
            self.core._WindowsProcessTree.create()

    def test_release_pipe_readers_ignores_missing_streams(self) -> None:
        class _Fake:
            stdout = None
            stderr = None

        self.core._release_pipe_readers(_Fake())

    def test_empty_stdio_matches_text_flag(self) -> None:
        self.assertEqual(("", ""), self.core._empty_stdio(True))
        self.assertEqual((b"", b""), self.core._empty_stdio(False))


class WindowsSpawnPipelineTests(unittest.TestCase):
    """Deterministic assertions for the Windows launch pipeline.

    The reviewer's concern is that ``subprocess.Popen`` returning a
    running child creates a race window: descendants spawned before
    ``AssignProcessToJobObject`` are not retroactively bound to the
    new job. The fix is to launch with ``CREATE_SUSPENDED`` and then
    assign before resuming.

    These tests inject a stand-in for ``subprocess.Popen`` and a stub
    tree so the same launch code runs on POSIX. They fail if:

    * ``CREATE_SUSPENDED`` is missing from the Popen kwargs;
    * ``assign`` is called after ``resume`` (or ``resume`` is called
      before ``assign``);
    * assignment failure is silently ignored;
    * a very fast valid command is reported as launch-failed (127).

    The sequence is the atomicity proof: while the primary thread is
    suspended it cannot execute any user code and therefore cannot
    spawn any descendant. Assignment commits before we ever call
    ``NtResumeProcess``, so at the moment the child begins executing
    it is already inside the kill-on-close job -- any process it
    creates afterwards inherits the job by nested-job semantics
    (Windows 8+).
    """

    def setUp(self) -> None:
        self.core = _load_workspace_core()

    def _stub_popen(
        self, *, poll_returncode: int | None = 0
    ) -> tuple[mock.Mock, mock.Mock]:
        fake_process = mock.Mock()
        fake_process._handle = 0x1000
        fake_process.returncode = poll_returncode
        fake_process.communicate.return_value = (b"", b"")
        fake_process.wait.return_value = poll_returncode
        popen_factory = mock.Mock(return_value=fake_process)
        return fake_process, popen_factory

    def _stub_tree(self, *, calls: list[str]) -> mock.Mock:
        tree = mock.Mock()
        tree.assign.side_effect = lambda process: calls.append("assign")
        tree.resume.side_effect = lambda process: calls.append("resume")
        return tree

    def test_spawn_launches_suspended_and_assigns_before_resume(self) -> None:
        calls: list[str] = []
        _fake, popen_factory = self._stub_popen()
        tree = self._stub_tree(calls=calls)

        self.core._spawn_windows_suspended_in_tree(
            [sys.executable, "-c", "pass"],
            cwd=Path.cwd(),
            text=False,
            tree=tree,
            popen_factory=popen_factory,
        )

        popen_factory.assert_called_once()
        kwargs = popen_factory.call_args.kwargs
        self.assertEqual(
            kwargs.get("creationflags"),
            self.core._WINDOWS_CREATE_SUSPENDED,
            "Windows spawn must pass CREATE_SUSPENDED so the child cannot "
            "execute (and therefore cannot spawn descendants) before it is "
            "assigned to the kill-on-close job.",
        )
        self.assertIs(kwargs.get("stdout"), subprocess.PIPE)
        self.assertIs(kwargs.get("stderr"), subprocess.PIPE)
        self.assertEqual(kwargs.get("text"), False)
        self.assertEqual(
            calls,
            ["assign", "resume"],
            "Job Object assignment must complete strictly before the child "
            "is resumed. Reordering these calls reopens the reviewer's "
            "launch/assignment race window.",
        )

    def test_fast_valid_command_is_not_reported_as_launch_failed(self) -> None:
        # A previous attempt to close the race by treating a
        # very-fast-exit as an assignment failure would report a
        # legitimate command as 127. Prove that a fake Popen whose
        # child is already dead by the time we assign still returns
        # its real returncode, not OSError. The child cannot in
        # practice have executed before we resume it, but the outer
        # contract still guards the code from returning a bogus 127.
        _fake, popen_factory = self._stub_popen(poll_returncode=0)
        tree = mock.Mock()  # both assign and resume succeed

        process = self.core._spawn_windows_suspended_in_tree(
            [sys.executable, "-c", "pass"],
            cwd=Path.cwd(),
            text=False,
            tree=tree,
            popen_factory=popen_factory,
        )
        self.assertEqual(process.returncode, 0)
        tree.assign.assert_called_once()
        tree.resume.assert_called_once()

    def test_assignment_failure_kills_child_and_raises(self) -> None:
        # Never silently degrade to parent-only termination: if the
        # kernel refuses AssignProcessToJobObject we must kill the
        # suspended child (safe because it has never run) and raise
        # OSError so the caller maps to 127.
        fake, popen_factory = self._stub_popen()
        tree = mock.Mock()
        tree.assign.side_effect = OSError("simulated ERROR_ACCESS_DENIED")

        with self.assertRaises(OSError):
            self.core._spawn_windows_suspended_in_tree(
                [sys.executable, "-c", "pass"],
                cwd=Path.cwd(),
                text=False,
                tree=tree,
                popen_factory=popen_factory,
            )
        fake.kill.assert_called_once()
        tree.resume.assert_not_called()

    def test_resume_failure_releases_pipes_and_raises(self) -> None:
        # If NtResumeProcess fails, the suspended child is already in
        # the job. Falling through to tree.close() at the caller kills
        # it via KILL_ON_JOB_CLOSE. We still close the pipe read ends
        # we own so a driver-level buffer cannot outlive the child.
        fake, popen_factory = self._stub_popen()
        fake.stdout = mock.Mock()
        fake.stderr = mock.Mock()
        tree = mock.Mock()
        tree.assign.side_effect = lambda process: None
        tree.resume.side_effect = OSError("simulated NTSTATUS 0xC0000022")

        with self.assertRaises(OSError):
            self.core._spawn_windows_suspended_in_tree(
                [sys.executable, "-c", "pass"],
                cwd=Path.cwd(),
                text=False,
                tree=tree,
                popen_factory=popen_factory,
            )
        fake.stdout.close.assert_called_once()
        fake.stderr.close.assert_called_once()

    def test_missing_command_propagates_oserror_before_assignment(self) -> None:
        # The missing-command signal comes from ``subprocess.Popen``
        # itself (FileNotFoundError from CreateProcessW). No child
        # exists to assign or resume, so neither call happens. The
        # caller maps OSError -> 127 unchanged.
        tree = mock.Mock()
        popen_factory = mock.Mock(side_effect=FileNotFoundError("no such file"))

        with self.assertRaises(OSError):
            self.core._spawn_windows_suspended_in_tree(
                ["/definitely/missing/cmd.exe"],
                cwd=Path.cwd(),
                text=False,
                tree=tree,
                popen_factory=popen_factory,
            )
        tree.assign.assert_not_called()
        tree.resume.assert_not_called()


@unittest.skipUnless(
    os.name == "nt",
    "Windows Job Object subtree cancellation runs on the Windows CI runner",
)
class WindowsSubtreeTerminationTests(unittest.TestCase):
    """Integration test for the Windows Job Object subtree kill.

    The parent process spawns a descendant that inherits our captured
    stdout/stderr write ends and sleeps well past the Workspace
    deadline. A parent-only termination would leave the descendant
    holding the pipe writers, so ``communicate`` would wait for the
    descendant to exit -- the reviewer's ~3 s wall-clock hang. With
    the ``CREATE_SUSPENDED``/assign/resume launch pipeline the child
    is inside the kill-on-close job before it executes a single
    instruction, so ``TerminateJobObject`` kills the entire subtree
    atomically and the deadline enforcement returns promptly.

    Runs on Windows CI only; setup and pipeline are exercised by
    ``WindowsProcessTreeSetupTests`` and ``WindowsSpawnPipelineTests``
    above.
    """

    def setUp(self) -> None:
        self.core = _load_workspace_core()

    def test_timeout_cancels_descendants_and_returns_promptly(self) -> None:
        temp = tempfile.mkdtemp(prefix="ws-bounded-tree-")
        self.addCleanup(lambda: __import__("shutil").rmtree(temp, ignore_errors=True))
        sentinel = Path(temp) / "descendant-lived.txt"
        parent_script = Path(temp) / "parent.py"
        parent_script.write_text(
            textwrap.dedent(
                f"""
                import subprocess, sys, time
                subprocess.Popen(
                    [sys.executable, "-c",
                     "import time; time.sleep(6); "
                     "open({str(sentinel)!r}, 'w', encoding='utf-8').write('mutated')"],
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                )
                time.sleep(6)
                """
            ),
            encoding="utf-8",
        )

        started = time.monotonic()
        completed, timed_out = self.core._run_bounded_command(
            [sys.executable, str(parent_script)],
            cwd=Path(temp),
            timeout_seconds=1,
        )
        elapsed = time.monotonic() - started

        self.assertTrue(timed_out)
        self.assertEqual(124, completed.returncode)
        self.assertLess(
            elapsed,
            4.0,
            f"bounded command took {elapsed:.2f}s; subtree cancellation must be prompt",
        )
        time.sleep(6.5)
        self.assertFalse(
            sentinel.exists(),
            "descendant survived timeout: Windows Job Object did not kill the subtree",
        )


if __name__ == "__main__":
    unittest.main()
