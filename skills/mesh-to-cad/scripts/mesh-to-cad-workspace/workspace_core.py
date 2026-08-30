"""Immutable Mesh-to-CAD Workspace state and Git publication.

This module intentionally uses only the Python standard library so the skill is
self-contained when installed outside the repository checkout.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterable, Mapping
import uuid


WORKSPACE_SCHEMA = "mesh-to-cad.workspace/1"
EXPERIMENT_SCHEMA = "mesh-to-cad.experiment/1"
STEP_SCHEMA = "mesh-to-cad.measured-step/1"
CYCLE_SCHEMA = "mesh-to-cad.repair-cycle/1"
INDEX_SCHEMA = "mesh-to-cad.step-index/1"
ATTEMPT_SCHEMA = "mesh-to-cad.attempt/1"
REPAIR_EVIDENCE_FAILURE_SCHEMA = "mesh-to-cad.repair-evidence-failure/1"
REPAIR_EVIDENCE_FAILURE_SUBTYPES = (
    "provider_execution_failed",
    "voxblame_output_invalid",
    "preview_output_invalid",
    "region_diff_invalid",
    "source_changes_invalid",
)
COMMAND_SCHEMA = "mesh-to-cad.command/1"
INITIAL_PLAN_SCHEMA = "mesh-to-cad.initial-plan/1"
COORDINATE_CONTRACT = "trellis2_canonical/1"
MAX_DEPTH = 8
MAX_REPAIR_CYCLES = 5
MAX_ATTEMPTS_PER_STEP = 3
MAX_TOOL_FAILURES_PER_STEP = 2
MAX_COMMANDS_PER_ATTEMPT = 8
MAX_LOG_BYTES = 65536
DEFAULT_COMMAND_SECONDS = 1800
MAX_COMMAND_SECONDS = 1800
COMMAND_TERMINATION_GRACE_SECONDS = 2
TOOL_FAILURE_RESULT = "tool_failure"
FINAL_SELECTION_SCHEMA = "mesh-to-cad.final-selection/1"
FINAL_DELIVERY_SCHEMA = "mesh-to-cad.final-delivery/1"
VERIFICATION_SCHEMA = "voxblame.verification/1"
TOOL_REGISTRY_SCHEMA = "mesh-to-cad.tool-registry/2"
LEGACY_TOOL_REGISTRY_SCHEMA = "mesh-to-cad.tool-registry/1"
FAILED_ATTEMPT_RESULTS = (
    TOOL_FAILURE_RESULT,
    "strategy_changed",
    "no_feasible_strategy",
)


class RepairEvidenceFailure(Exception):
    def __init__(self, subtype: str) -> None:
        self.subtype = subtype


class ExecutionScope:
    """Per-run process cancellation scope with spawn/registration handshake."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active: dict[
            subprocess.Popen[Any], tuple[Callable[[], None], Callable[[], None]]
        ] = {}
        self._pending = 0
        self._next_spawn_token = 1
        self._pending_tokens: dict[int, None] = {}
        self._terminating_spawns: dict[int, None] = {}
        self._cancelled = False

    @property
    def pending_spawns(self) -> int:
        with self._condition:
            return self._pending + len(self._terminating_spawns)

    @property
    def cancelled(self) -> bool:
        with self._condition:
            return self._cancelled

    def begin_spawn(self) -> int | None:
        """Reserve a spawn slot before entering the non-atomic Popen call."""

        with self._condition:
            if self._cancelled:
                return None
            token = self._next_spawn_token
            self._next_spawn_token += 1
            self._pending_tokens[token] = None
            self._pending += 1
            return token

    def _take_spawn_token(self, token: int | None) -> int:
        if token is None:
            if len(self._pending_tokens) != 1:
                raise RuntimeError("ExecutionScope spawn token is ambiguous")
            token = next(iter(self._pending_tokens))
        if token not in self._pending_tokens:
            raise RuntimeError("ExecutionScope spawn token is unknown")
        self._pending_tokens.pop(token)
        if self._pending <= 0:
            raise RuntimeError("ExecutionScope spawn accounting underflow")
        self._pending -= 1
        return token

    def spawn_failed(self, token: int | None = None) -> None:
        """Finish a reserved spawn whose process creation raised."""

        with self._condition:
            self._take_spawn_token(token)
            self._condition.notify_all()

    def register_spawn(
        self,
        process: subprocess.Popen[Any],
        terminate: Callable[[], None],
        force: Callable[[], None] | None = None,
        token: int | None = None,
    ) -> bool:
        """Complete the reservation and register a newly-created process atomically."""

        if force is None:
            force = terminate
        with self._condition:
            token = self._take_spawn_token(token)
            cancelled = self._cancelled
            if not cancelled:
                self._active[process] = (terminate, force)
            else:
                self._terminating_spawns[token] = None
            self._condition.notify_all()
        if cancelled:
            # Cancellation may have taken its snapshot just before this
            # process became visible.  Kill this exact process immediately;
            # the caller performs the bounded pipe drain and final wait.
            for callback in (terminate, force):
                try:
                    callback()
                except OSError:
                    pass
            return False
        return True

    def spawn_terminated(self, token: int, *, confirmed: bool) -> None:
        """Close a cancelled-spawn token after the spawning thread has drained it."""

        with self._condition:
            if token not in self._terminating_spawns:
                raise RuntimeError("ExecutionScope terminating token is unknown")
            if confirmed:
                self._terminating_spawns.pop(token)
                self._condition.notify_all()

    def complete(self, process: subprocess.Popen[Any]) -> None:
        with self._condition:
            self._active.pop(process, None)
            self._condition.notify_all()

    def has_live_processes(self) -> bool:
        with self._condition:
            return (
                self._pending > 0
                or bool(self._terminating_spawns)
                or any(_process_live(process) for process in self._active)
            )

    def cancel(self) -> bool:
        """Cancel this scope, bounded across pending spawns and live process trees."""

        with self._condition:
            self._cancelled = True
            deadline = time.monotonic() + COMMAND_TERMINATION_GRACE_SECONDS
            while (
                (self._pending or self._terminating_spawns)
                and time.monotonic() < deadline
            ):
                self._condition.wait(timeout=min(0.05, deadline - time.monotonic()))
            pending_unresolved = bool(self._pending or self._terminating_spawns)
            active = tuple(self._active.items())
        for _process, (terminate, _force) in active:
            terminate()
        if self._wait_until_quiet(
            COMMAND_TERMINATION_GRACE_SECONDS,
            require_no_pending=not pending_unresolved,
        ) and not pending_unresolved:
            return True
        for _process, (_terminate, force) in active:
            force()
        return self._wait_until_quiet(
            COMMAND_TERMINATION_GRACE_SECONDS,
            require_no_pending=not pending_unresolved,
        ) and not pending_unresolved

    def _wait_until_quiet(
        self,
        seconds: float,
        *,
        require_no_pending: bool = True,
    ) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            with self._condition:
                for process in tuple(self._active):
                    if not _process_live(process):
                        self._active.pop(process, None)
                quiet = (
                    (not require_no_pending or not self._pending)
                    and not self._terminating_spawns
                    and not any(
                        _process_live(process) for process in self._active
                    )
                )
                if quiet:
                    self._condition.notify_all()
                    return True
                self._condition.wait(timeout=0.05)
        with self._condition:
            for process in tuple(self._active):
                if not _process_live(process):
                    self._active.pop(process, None)
            return (
                (not require_no_pending or not self._pending)
                and not self._terminating_spawns
                and not any(_process_live(process) for process in self._active)
            )


def _process_live(process: subprocess.Popen[Any]) -> bool:
    """Report descendants too, not merely the Popen parent."""

    if process.poll() is None:
        return True
    if os.name != "posix":
        return False
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_command_tree(process: subprocess.Popen[Any], signum: int) -> None:
    """Signal a bounded command and every descendant in its process group."""

    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def _command_tree_exists(process: subprocess.Popen[Any]) -> bool:
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _bounded_process_wait(process: subprocess.Popen[Any], seconds: float) -> bool:
    """Wait for one process without ever using an unbounded wait."""

    deadline = time.monotonic() + seconds
    while process.poll() is None and time.monotonic() < deadline:
        try:
            process.wait(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            continue
    return process.poll() is not None


def _terminate_unregistered_posix(process: subprocess.Popen[Any]) -> None:
    """Close a cancellation race for a child not yet visible to the scope."""

    _signal_command_tree(process, signal.SIGTERM)
    deadline = time.monotonic() + COMMAND_TERMINATION_GRACE_SECONDS
    while _process_live(process) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _process_live(process):
        _signal_command_tree(process, signal.SIGKILL)
    if not _bounded_process_wait(process, COMMAND_TERMINATION_GRACE_SECONDS):
        raise OSError("Workspace command termination did not complete")


def cancel_active_commands(scope: ExecutionScope) -> bool:
    """Cancel exactly one trusted run's command scope."""

    return scope.cancel()


def _run_bounded_command(
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    text: bool = False,
    posix: bool | None = None,
    environment: Mapping[str, str] | None = None,
    scope: ExecutionScope | None = None,
) -> tuple[subprocess.CompletedProcess[Any], bool]:
    """Run argv under the Workspace deadline and cancel its complete process tree.

    Both branches share the same outer contract: a successful
    ``communicate`` returns ``(CompletedProcess, False)``, a timeout
    returns ``(CompletedProcess(returncode=124), True)`` and cannot
    leave descendants running past the return, and any failure to
    launch the child (missing binary, unavailable subtree primitive on
    the host) raises ``OSError`` which ``_run_attempt_command`` maps to
    exit code 127.

    POSIX starts the child in a fresh session so a timeout can send
    ``SIGTERM``/``SIGKILL`` to the whole tree via ``killpg``. Windows
    has no process-group primitive equivalent, so we bind the child to
    a Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` --
    ``TerminateJobObject`` on timeout, and ``CloseHandle`` on scope
    exit, both terminate every process in the subtree atomically. See
    ``_WindowsProcessTree`` below for the nested-job/breakaway
    reasoning and the fail-closed setup contract.

    ``posix`` defaults to the running host but can be set explicitly by
    tests to force the Windows branch on POSIX CI. On POSIX the Windows
    branch fails closed at Job Object creation (kernel32 is
    unavailable) rather than silently degrading to parent-only
    termination.
    """

    if posix is None:
        posix = os.name == "posix"
    if posix:
        return _run_bounded_command_posix(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            text=text,
            environment=environment,
            scope=scope,
        )
    return _run_bounded_command_windows(
        argv,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        text=text,
        environment=environment,
        scope=scope,
    )


def _run_bounded_command_posix(
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    text: bool,
    environment: Mapping[str, str] | None,
    scope: ExecutionScope | None,
) -> tuple[subprocess.CompletedProcess[Any], bool]:
    spawn_token = scope.begin_spawn() if scope is not None else None
    if scope is not None and spawn_token is None:
        raise OSError("Workspace execution scope was cancelled")
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            start_new_session=True,
            env=dict(environment) if environment is not None else None,
        )
    except Exception:
        if scope is not None:
            scope.spawn_failed(spawn_token)
        raise
    registered = False
    terminate = lambda: _signal_command_tree(process, signal.SIGTERM)
    force = lambda: _signal_command_tree(process, signal.SIGKILL)
    if scope is not None:
        registered = scope.register_spawn(
            process, terminate, force, token=spawn_token
        )
        if not registered:
            confirmed = False
            try:
                _terminate_unregistered_posix(process)
                confirmed = not _process_live(process)
            finally:
                _release_pipe_readers(process)
                scope.spawn_terminated(spawn_token, confirmed=confirmed)
            raise OSError("Workspace execution scope was cancelled")
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr), False
    except subprocess.TimeoutExpired:
        _signal_command_tree(process, signal.SIGTERM)
        termination_deadline = (
            time.monotonic() + COMMAND_TERMINATION_GRACE_SECONDS
        )
        stdout = None
        stderr = None
        try:
            stdout, stderr = process.communicate(
                timeout=COMMAND_TERMINATION_GRACE_SECONDS
            )
        except subprocess.TimeoutExpired:
            pass
        while (
            _command_tree_exists(process)
            and time.monotonic() < termination_deadline
        ):
            time.sleep(0.05)
        if _command_tree_exists(process):
            _signal_command_tree(process, signal.SIGKILL)
            kill_deadline = time.monotonic() + COMMAND_TERMINATION_GRACE_SECONDS
            while _process_live(process) and time.monotonic() < kill_deadline:
                time.sleep(0.05)
        if stdout is None or stderr is None:
            try:
                stdout, stderr = process.communicate(
                    timeout=COMMAND_TERMINATION_GRACE_SECONDS
                )
            except subprocess.TimeoutExpired:
                _release_pipe_readers(process)
                stdout, stderr = _empty_stdio(text)
        if _process_live(process):
            raise OSError("Workspace command termination did not complete")
        return subprocess.CompletedProcess(argv, 124, stdout, stderr), True
    finally:
        if scope is not None and registered:
            if _process_live(process):
                _terminate_unregistered_posix(process)
            scope.complete(process)


def _run_bounded_command_windows(
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    text: bool,
    environment: Mapping[str, str] | None,
    scope: ExecutionScope | None,
) -> tuple[subprocess.CompletedProcess[Any], bool]:
    # Create the job before launching the child. If the kernel refuses
    # to build a Job Object (kernel32 unavailable on the host, out of
    # handles, ACL denial), surface OSError to the caller -- the
    # Workspace maps that to exit code 127. Refuse to run rather than
    # spawn a child we cannot cancel.
    spawn_token = scope.begin_spawn() if scope is not None else None
    if scope is not None and spawn_token is None:
        raise OSError("Workspace execution scope was cancelled")
    tree: _WindowsProcessTree | None = None
    try:
        tree = _WindowsProcessTree.create()
        process = _spawn_windows_suspended_in_tree(
            argv,
            cwd=cwd,
            text=text,
            tree=tree,
            environment=environment,
        )
        registered = False
        if scope is not None and not scope.register_spawn(
            process, tree.terminate, tree.terminate, token=spawn_token
        ):
            confirmed = False
            try:
                tree.terminate()
                confirmed = _bounded_process_wait(
                    process, COMMAND_TERMINATION_GRACE_SECONDS
                )
                if not confirmed:
                    raise OSError("Workspace command termination did not complete")
            finally:
                scope.spawn_terminated(spawn_token, confirmed=confirmed)
            raise OSError("Workspace execution scope was cancelled")
        if scope is not None:
            registered = True
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            return subprocess.CompletedProcess(
                argv, process.returncode, stdout, stderr
            ), False
        except subprocess.TimeoutExpired:
            tree.terminate()
            # ``TerminateJobObject`` marks every process in the job for
            # termination atomically. Descendants that inherited our
            # captured stdout/stderr pipe writer ends die with the job,
            # so ``communicate`` returns as soon as the pipes drain --
            # it cannot deadlock waiting for a still-live descendant.
            # We still bound the drain read so a driver-level pipe
            # hold cannot block the Workspace deadline.
            try:
                stdout, stderr = process.communicate(
                    timeout=COMMAND_TERMINATION_GRACE_SECONDS
                )
            except subprocess.TimeoutExpired:
                _release_pipe_readers(process)
                stdout, stderr = _empty_stdio(text)
            return subprocess.CompletedProcess(argv, 124, stdout, stderr), True
    finally:
        # ``kill-on-close`` guarantees the subtree cannot outlive this
        # call even on unexpected exceptions between assign, resume,
        # and terminate: the OS terminates every process in the job
        # when the final handle closes.
        if tree is not None:
            tree.close()
        if "process" in locals() and scope is not None and registered:
            scope.complete(process)
        if scope is not None and "process" not in locals():
            scope.spawn_failed(spawn_token)


# ``CreateProcessW`` flag that starts the primary thread in the
# suspended state. Value is fixed by the Win32 ABI; kept as a module
# constant so the launch sequence is grep-able and so tests can assert
# the same value is passed to ``subprocess.Popen``.
_WINDOWS_CREATE_SUSPENDED = 0x00000004


def _spawn_windows_suspended_in_tree(
    argv: list[str],
    *,
    cwd: Path,
    text: bool,
    tree: "_WindowsProcessTree",
    environment: Mapping[str, str] | None = None,
    popen_factory: Any = subprocess.Popen,
) -> subprocess.Popen[Any]:
    """Race-free Windows spawn: suspend, assign, then resume.

    The reviewer's finding was that ``subprocess.Popen`` returning a
    running child means the child can spawn descendants before
    ``AssignProcessToJobObject`` reaches it -- those descendants are
    not retroactively inherited by the new job, so they can survive
    ``TerminateJobObject``. Windows solves this with
    ``CREATE_SUSPENDED``: the child is created with its primary
    thread suspended and cannot execute a single instruction, let
    alone spawn a descendant, until ``NtResumeProcess`` runs. We
    call ``AssignProcessToJobObject`` between the two, so by the time
    any user code executes, the process (and therefore every future
    descendant it spawns while the job is alive) is inside the
    kill-on-close job.

    Error mapping preserves the ``_run_attempt_command`` contract:

    * A missing binary raises ``OSError`` from ``subprocess.Popen``
      before the child ever exists; caller maps to exit code 127.
    * Assignment failure kills the still-suspended child and re-raises
      ``OSError``; caller maps to 127. The child never ran a user
      instruction, so no descendant can exist to leak.
    * Resume failure raises ``OSError``. Falls through to
      ``tree.close()`` in the outer scope; ``KILL_ON_JOB_CLOSE``
      terminates the suspended child on handle close, so we never
      leave an unresumed process behind.

    ``popen_factory`` is exposed only so unit tests can inject a
    stand-in and assert the launch pipeline without needing a real
    Windows host.
    """

    process = popen_factory(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        creationflags=_WINDOWS_CREATE_SUSPENDED,
        env=dict(environment) if environment is not None else None,
    )
    try:
        tree.assign(process)
    except OSError:
        # The child is still suspended; TerminateProcess kills a
        # suspended process cleanly, and no descendant could exist
        # because no user code has executed.
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=COMMAND_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        raise
    try:
        tree.resume(process)
    except OSError:
        # Assignment succeeded, so the suspended child is in the job.
        # Falling through raises OSError; the outer ``tree.close()``
        # closes the job handle, which under KILL_ON_JOB_CLOSE
        # terminates the suspended child. Explicitly close the pipe
        # read ends we own so a driver-level buffer cannot outlive
        # the child.
        _release_pipe_readers(process)
        raise
    return process


def _release_pipe_readers(process: subprocess.Popen[Any]) -> None:
    """Force-close pipe read ends so ``communicate`` cannot block.

    Only exercised in the pathological Windows path where the pipe
    drain read still times out after ``TerminateJobObject``; the job's
    processes are already terminated by the kernel, so unread bytes
    are discarded rather than blocking the Workspace deadline.
    """

    for stream in (process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _empty_stdio(text: bool) -> tuple[Any, Any]:
    return ("", "") if text else (b"", b"")


# Windows Job Object bindings and helper. Kept in this module rather
# than a shared helper because the Workspace is the only bounded-
# command caller and self-containment is a hard requirement of the
# skill (see ``skills/mesh-to-cad/scripts/mesh-to-cad-workspace/
# workspace_core.py`` module docstring).
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9


class _WindowsProcessTree:
    """Kill-on-close Windows Job Object bound to a Popen'd child.

    Windows has no direct equivalent of POSIX ``setsid``/``killpg`` in
    the standard library. A Job Object is the native mechanism the
    kernel uses to group related processes for lifetime management. We
    create the job with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` so
    ``TerminateJobObject`` on timeout or ``CloseHandle`` on scope exit
    terminates every process associated with the job. Since Windows 8,
    processes created by a jobbed child inherit the parent's job
    (nested-job semantics) unless the caller explicitly requests
    ``JOB_OBJECT_LIMIT_BREAKAWAY_OK`` or passes
    ``CREATE_BREAKAWAY_FROM_JOB``; the tools the Workspace invokes do
    neither, so terminating this job terminates the entire subtree.

    Setup or assignment failure is fail-closed. ``create()`` surfaces
    ``OSError`` if kernel32 is not available (POSIX host with the
    Windows branch forced by tests) or the kernel refuses to build the
    job. ``assign()`` surfaces ``OSError`` if the just-spawned process
    cannot be attached, and the caller kills the parent and re-raises
    -- we do not silently degrade to parent-only termination.
    """

    __slots__ = ("_handle", "_kernel32", "_ntdll", "_ctypes")

    @classmethod
    def create(cls) -> "_WindowsProcessTree":
        if os.name != "nt":
            # The Windows branch was forced (posix=False) on a POSIX
            # host; kernel32 does not exist here, so refuse to run
            # rather than silently degrading. Matches the OSError the
            # caller expects when a subtree primitive is unavailable.
            raise OSError(
                "Windows bounded command tree requires kernel32; "
                "os.name is not 'nt'"
            )
        return cls()

    def __init__(self) -> None:
        if os.name != "nt":  # pragma: no cover - construction gated by create()
            raise OSError("Windows Job Object requires os.name == 'nt'")
        import ctypes
        from ctypes import wintypes

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        # ``NtResumeProcess`` resumes every thread in a target process by
        # handle. It is stable since NT 4, re-exported by every supported
        # Windows version, and the standard mechanism paired with
        # ``CREATE_SUSPENDED`` when the caller does not hold the main-
        # thread handle (which ``subprocess.Popen`` closes before
        # returning). A ``CREATE_SUSPENDED`` process has exactly one
        # thread until it starts running, so a single-shot resume is
        # deterministic. NTSTATUS 0 means success.
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        ntdll.NtResumeProcess.restype = ctypes.c_long
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            ok = kernel32.SetInformationJobObject(
                handle,
                _JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            kernel32.CloseHandle(handle)
            raise
        self._handle = handle
        self._kernel32 = kernel32
        self._ntdll = ntdll
        self._ctypes = ctypes

    def assign(self, process: subprocess.Popen[Any]) -> None:
        if self._handle is None:
            raise OSError("Windows Job Object is closed")
        if not self._kernel32.AssignProcessToJobObject(
            self._handle, int(process._handle)
        ):
            raise self._ctypes.WinError(self._ctypes.get_last_error())

    def resume(self, process: subprocess.Popen[Any]) -> None:
        """Resume a ``CREATE_SUSPENDED`` child previously bound to the job.

        ``subprocess.Popen`` on Windows closes the main-thread handle
        before returning, so we cannot ``ResumeThread`` from a
        Popen-visible field. ``NtResumeProcess`` resumes by process
        handle instead. A ``CREATE_SUSPENDED`` process has exactly one
        thread (the initial thread), so a single call transitions the
        child from "cannot execute any instruction" to "runs the
        requested argv" atomically with respect to job membership --
        assignment is already committed on entry.
        """

        if self._handle is None:
            raise OSError("Windows Job Object is closed")
        status = self._ntdll.NtResumeProcess(int(process._handle))
        if status != 0:
            # NTSTATUS is a signed 32-bit code; surface as OSError so
            # the caller maps to exit code 127 rather than pretending
            # the child ran.
            raise OSError(
                f"NtResumeProcess failed with NTSTATUS 0x{status & 0xFFFFFFFF:08X}"
            )

    def terminate(self) -> None:
        if self._handle is not None:
            # Non-zero exit code signals to any tool inspecting the job
            # afterwards that the tree was force-killed rather than
            # exiting cleanly.
            self._kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle is not None:
            handle = self._handle
            self._handle = None
            self._kernel32.CloseHandle(handle)

_EXPERIMENT_FIELDS = {
    "schema",
    "workspace_id",
    "coordinate_contract",
    "canonical_reference_sha256",
    "preview_profile",
}
_WORKSPACE_FIELDS = {
    "schema",
    "workspace_id",
    "coordinate_contract",
    "canonical_reference_sha256",
    "preview_profile",
    "input_identity_sha256",
    "setup_identity_sha256",
    "limits",
}
_STEP_FIELDS = {
    "schema",
    "step",
    "parent_step",
    "cycle",
    "attempt_ids",
    "canonical_reference_sha256",
    "candidate_mesh_sha256",
    "observable_sha256",
    "preview_identity_sha256",
    "preview_profile_sha256",
    "measurement_path",
    "compare_to",
    "accepted",
    "no_observable_geometry_change",
    "files",
    "identity_sha256",
}
_ACTIVE_ATTEMPT_FIELDS = {
    "schema",
    "attempt",
    "intended_cycle",
    "intended_step",
    "from_step",
    "plan_digest",
    "result",
}
_PUBLISHED_ATTEMPT_FIELDS = {
    "schema",
    "attempt",
    "intended_cycle",
    "intended_step",
    "from_step",
    "plan_digest",
    "result",
    "classification",
    "command_ids",
    "files",
    "identity_sha256",
}
_CYCLE_FIELDS = {
    "schema",
    "cycle",
    "from_step",
    "to_step",
    "attempt_ids",
    "plan_digest",
    "region_diff_sha256",
    "assessment_sha256",
    "source_changes_sha256",
    "from_observable_sha256",
    "to_observable_sha256",
    "no_observable_geometry_change",
    "files",
    "identity_sha256",
}
_SECRET_ARGUMENTS = {
    "--api-key",
    "--token",
    "--password",
    "authorization",
}
_LFS_SUFFIXES = {
    ".step",
    ".stp",
    ".glb",
    ".gltf",
    ".ply",
    ".obj",
    ".stl",
    ".vbsvo",
    ".png",
}
_FORBIDDEN_TREE_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".env",
    "credentials.json",
    "secrets.json",
    "id_rsa",
}
_LEGACY_ROOT_NAMES = {
    "compare_metrics.json",
    "iteration_state.json",
    "mesh_stats.json",
}
_STOP_REASONS = {
    "acceptance_satisfied",
    "cycle_limit",
    "no_feasible_strategy",
    "representation_limit",
    "modeling_intent_conflict",
    "repeated_ineffective_strategy",
    "tool_failure",
}
_NOTES_HEADINGS = (
    "## Input",
    "## Modeling Intent",
    "## Preserved Structural Features",
    "## Omitted Surface Details",
    "## Repair Trajectory",
    "## Final Selection",
    "## Verification",
)


_SANDBOX_REPO_ROOT = PurePosixPath("/workspace/repo")
_SANDBOX_ENTRYPOINT_ROOT = _SANDBOX_REPO_ROOT / "skills"


def _is_canonical_sandbox_absolute_path(value: str) -> bool:
    """Validate the fixed POSIX namespace emitted by the pilot runner.

    This is a serialized container path, not a host filesystem path.  It must
    therefore be parsed with ``PurePosixPath`` even when the validator itself
    runs on Windows.  Only the shipped ``/workspace/repo/skills`` subtree is
    an entrypoint namespace; callers still bind the bytes to the host-side
    executable before execution.
    """

    if not isinstance(value, str) or not value or "\0" in value:
        return False
    if not value.startswith("/workspace/repo/") or "\\" in value:
        return False
    pure = PurePosixPath(value)
    if (
        pure.as_posix() != value
        or not pure.is_absolute()
        or ".." in pure.parts
        or not pure.is_relative_to(_SANDBOX_ENTRYPOINT_ROOT)
    ):
        return False
    return pure != _SANDBOX_ENTRYPOINT_ROOT


def _is_canonical_absolute_path(value: str) -> bool:
    """Fail-closed check for a native host filesystem path.

    Serialized tool registries use ``_is_canonical_sandbox_absolute_path``
    instead; this helper is reserved for separately defined host-native
    fields.
    """

    if not isinstance(value, str) or not value or "\0" in value:
        return False
    if os.name == "nt":
        return _is_canonical_windows_absolute_path(value)
    return _is_canonical_posix_absolute_path(value)


def _is_canonical_posix_absolute_path(value: str) -> bool:
    if not value.startswith("/") or value.startswith("//"):
        return False
    if "\\" in value:
        return False
    pure = PurePosixPath(value)
    if pure.as_posix() != value:
        return False
    return ".." not in pure.parts


def _is_canonical_windows_absolute_path(value: str) -> bool:
    # ``PureWindowsPath`` normalizes ``/`` to ``\`` internally, so compare
    # against the object's rendered form to reject mixed separators and
    # non-canonical spellings like ``C:\\foo\\..\\bar``.
    if "/" in value:
        return False
    if "\0" in value:
        return False
    pure = PureWindowsPath(value)
    if not pure.is_absolute():
        return False
    if ".." in pure.parts:
        return False
    # A drive-letter absolute path always has a two-character drive (``C:``)
    # or a UNC anchor of at least ``\\server\share``; reject truncated
    # anchors that ``PureWindowsPath`` would otherwise silently accept.
    if pure.drive.startswith("\\\\"):
        anchor_parts = pure.drive.split("\\")
        # A well-formed UNC drive splits as ``['', '', server, share]`` with
        # both host and share non-empty. Anything else is a truncated anchor.
        if len(anchor_parts) != 4 or not anchor_parts[2] or not anchor_parts[3]:
            return False
    else:
        if len(pure.drive) != 2 or not pure.drive[0].isalpha() or pure.drive[1] != ":":
            return False
    return str(pure) == value


class WorkspaceError(RuntimeError):
    """Stable public Workspace failure."""

    def __init__(self, classification: str, detail: str, path: str = "$"):
        super().__init__(detail)
        self.classification = classification
        self.detail = detail
        self.path = path


@dataclass(frozen=True)
class ValidationResult:
    graph: dict[str, Any]
    recovery: list[dict[str, Any]]


@dataclass(frozen=True)
class _PreparedStepEvidence:
    candidate: Path
    preview: Path
    measurement: Path
    measurement_document: Mapping[str, Any]
    identities: dict[str, str]


def initialize_workspace(workspace: Path, prepared: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    prepared = prepared.resolve()
    _require_git_root(workspace)
    _reject_staged_changes(workspace)
    if (workspace / "workspace.json").exists():
        _fail("workspace_conflict", "Workspace is already initialized")
    for name in ("input", "setup", "experiment.json"):
        if not (prepared / name).exists():
            _fail("invalid_setup", f"prepared setup is missing {name}", f"$.{name}")
    _validate_tree_source(prepared / "input", "$.input")
    _validate_tree_source(prepared / "setup", "$.setup")
    if (prepared / "setup/route.json").exists():
        _fail("invalid_setup", "CAD routing metadata is unsupported", "$.setup.route.json")
    experiment = _read_json(prepared / "experiment.json", "$.experiment")
    _validate_experiment(experiment)
    input_manifest = _read_json(prepared / "input/input.json", "$.input.input.json")
    if input_manifest.get("canonical_reference_sha256") != experiment[
        "canonical_reference_sha256"
    ]:
        _fail(
            "identity_conflict",
            "prepared input and experiment Canonical Reference identities differ",
            "$.input.input.json.canonical_reference_sha256",
        )
    _configure_git_contract(workspace)
    workspace_document = {
        "schema": WORKSPACE_SCHEMA,
        "workspace_id": experiment["workspace_id"],
        "coordinate_contract": experiment["coordinate_contract"],
        "canonical_reference_sha256": experiment["canonical_reference_sha256"],
        "preview_profile": experiment["preview_profile"],
        "input_identity_sha256": _path_digest(prepared / "input"),
        "setup_identity_sha256": _path_digest(prepared / "setup"),
        "limits": {
            "repair_cycles": MAX_REPAIR_CYCLES,
            "attempts_per_step": MAX_ATTEMPTS_PER_STEP,
            "tool_failures_per_step": MAX_TOOL_FAILURES_PER_STEP,
        },
    }
    stage = workspace / f".tmp-setup-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        shutil.copytree(prepared / "input", stage / "input")
        shutil.copytree(prepared / "setup", stage / "setup")
        shutil.copy2(prepared / "experiment.json", stage / "experiment.json")
        _write_json(stage / "workspace.json", workspace_document)
        _write_json(
            stage / "transaction.json",
            {
                "schema": "mesh-to-cad.transaction/1",
                "kind": "setup",
                "workspace_identity_sha256": _identity(
                    WORKSPACE_SCHEMA, workspace_document
                ),
            },
        )
        _validate_staged_setup(stage, workspace_document)
        (stage / "input").rename(workspace / "input")
        (stage / "setup").rename(workspace / "setup")
        (stage / "experiment.json").rename(workspace / "experiment.json")
        (stage / "workspace.json").rename(workspace / "workspace.json")
    except Exception:
        # Keep incomplete setup evidence. Validation reports it as recoverable,
        # never as published authority.
        raise
    graph = rebuild_index(workspace, validate=False)
    paths = [
        ".gitignore",
        ".gitattributes",
        "input",
        "setup",
        "experiment.json",
        "workspace.json",
        "step_index.json",
    ]
    _commit_protocol_paths(
        workspace,
        paths,
        "setup: publish canonical Workspace",
        {
            "Workspace-Schema": WORKSPACE_SCHEMA,
            "Workspace-Id": workspace_document["workspace_id"],
            "Canonical-Reference-SHA256": workspace_document[
                "canonical_reference_sha256"
            ],
            "Workspace-SHA256": _identity(WORKSPACE_SCHEMA, workspace_document),
            "Input-SHA256": workspace_document["input_identity_sha256"],
            "Setup-SHA256": workspace_document["setup_identity_sha256"],
        },
    )
    (stage / "transaction.json").unlink()
    stage.rmdir()
    return {**workspace_document, "graph": graph}


def begin_attempt(
    workspace: Path,
    plan_path: Path,
    *,
    intended_step: int,
    from_step: int | None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    validate_workspace(workspace)
    if intended_step < 0:
        _fail("invalid_attempt", "intended step must be non-negative")
    if intended_step == 0:
        if from_step is not None:
            _fail("parent_mismatch", "Step 0 cannot declare a parent")
        intended_cycle = None
    else:
        if from_step is None or from_step < 0 or from_step >= intended_step:
            _fail(
                "parent_mismatch",
                "a repair attempt requires an explicit earlier Measured Step",
            )
        if not (workspace / "steps" / f"{from_step:06d}" / "step.json").is_file():
            _fail("parent_mismatch", f"parent Measured Step {from_step} is not published")
        intended_cycle = intended_step
    plan = _read_json(plan_path, "$.plan")
    if intended_step == 0:
        _closed_object(plan, {"schema", "summary"}, "$.plan")
        _const(plan["schema"], INITIAL_PLAN_SCHEMA, "$.plan.schema")
        _nonempty_string(plan["summary"], "$.plan.summary")
    else:
        _validate_repair_plan_boundary(plan, from_step)
    _check_attempt_budget(workspace, intended_step)
    attempt = _next_attempt_id(workspace)
    plan_bytes = _json_bytes(plan)
    plan_digest = hashlib.sha256(
        plan["schema"].encode("utf-8") + b"\0" + plan_bytes
    ).hexdigest()
    document = {
        "schema": ATTEMPT_SCHEMA,
        "attempt": attempt,
        "intended_cycle": intended_cycle,
        "intended_step": intended_step,
        "from_step": from_step,
        "plan_digest": plan_digest,
        "result": "active",
    }
    active_root = workspace / "work/attempts"
    active_root.mkdir(parents=True, exist_ok=True)
    target = active_root / f"{attempt:06d}"
    stage = active_root / f".tmp-{attempt:06d}-{uuid.uuid4().hex}"
    stage.mkdir()
    _write_json(stage / "plan.json", plan)
    _write_json(stage / "attempt.json", document)
    stage.rename(target)
    return document


def publish_step_zero(
    workspace: Path,
    *,
    attempt: int,
    candidate: Path,
    candidate_mesh: str,
    measurement: Path,
    preview: Path,
    _decision_facts_factory: Callable[
        [Mapping[str, Any], Mapping[str, Any], Mapping[str, Any] | None],
        Mapping[str, Any],
    ] | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    validate_workspace(workspace, allowed_voxblame_step=0)
    if (workspace / "steps/000000").exists():
        _fail("workspace_conflict", "Measured Step 0 is already published")
    active_root, active, plan = _load_active_attempt(workspace, attempt)
    if active["intended_step"] != 0 or active["from_step"] is not None:
        _fail("parent_mismatch", "attempt does not belong to Step 0")
    _require_successful_attempt_commands(active_root)
    evidence = _prepare_step_evidence(
        workspace,
        step=0,
        parent_step=None,
        parent_observable_sha256=None,
        candidate=candidate,
        candidate_mesh=candidate_mesh,
        measurement=measurement,
        preview=preview,
    )
    voxblame_paths = _step_zero_voxblame_paths(workspace, evidence.measurement)
    steps_root = workspace / "steps"
    steps_root.mkdir(exist_ok=True)
    transaction = workspace / "work" / (
        f".tmp-step-zero-{uuid.uuid4().hex}"
    )
    stage = transaction / "step"
    stage.mkdir(parents=True)
    try:
        _copy_step_evidence(evidence, stage)
        _validate_candidate_rebuild_recipe_if_present(stage / "candidate")
    except Exception:
        shutil.rmtree(transaction, ignore_errors=True)
        raise
    _write_json(stage / "plan.json", plan)
    successful_attempt = {**active, "result": "measured_step_published"}
    _write_json(stage / "attempt.json", successful_attempt)
    files = _inventory(stage)
    document: dict[str, Any] = {
        "schema": STEP_SCHEMA,
        "step": 0,
        "parent_step": None,
        "cycle": None,
        "attempt_ids": [attempt],
        **evidence.identities,
        "measurement_path": _workspace_relative(workspace, evidence.measurement),
        "compare_to": None,
        "accepted": _accepted(evidence.measurement_document),
        "no_observable_geometry_change": evidence.measurement_document[
            "no_observable_geometry_change"
        ],
        "files": files,
    }
    document["identity_sha256"] = _identity(STEP_SCHEMA, document)
    decision_facts = None
    _write_json(stage / "step.json", document)
    _write_json(
        transaction / "transaction.json",
        {
            "schema": "mesh-to-cad.transaction/1",
            "kind": "step_zero",
            "attempt": attempt,
            "step_identity_sha256": document["identity_sha256"],
        },
    )
    _validate_step_directory(workspace, stage, expected_step=0)
    if _decision_facts_factory is not None:
        try:
            decision_facts = _decision_facts_factory(
                document, evidence.measurement_document, None
            )
        except Exception:
            shutil.rmtree(transaction, ignore_errors=True)
            raise
        if not isinstance(decision_facts, Mapping):
            shutil.rmtree(transaction, ignore_errors=True)
            _fail("invalid_contract", "publication decision facts are invalid")
    stage.rename(workspace / "steps/000000")
    graph = rebuild_index(workspace, validate=False)
    _commit_protocol_paths(
        workspace,
        ["steps/000000", "step_index.json", *voxblame_paths],
        "step 0: publish initial Measured Step",
        {
            "Workspace-Step": "0",
            "Workspace-Attempt": str(attempt),
            "Candidate-SHA256": document["candidate_mesh_sha256"],
            "Observable-SHA256": document["observable_sha256"],
            "Preview-SHA256": document["preview_identity_sha256"],
            "Step-SHA256": document["identity_sha256"],
            "Workspace-SHA256": _workspace_identity(workspace),
        },
    )
    (transaction / "transaction.json").unlink()
    transaction.rmdir()
    shutil.rmtree(active_root)
    result = {**document, "graph": graph}
    if decision_facts is not None:
        result["_decision_facts"] = dict(decision_facts)
    return result


def _run_attempt_command(
    workspace: Path,
    *,
    attempt: int,
    phase: str,
    argv: list[str],
    timeout_seconds: int,
    canonical_output: Path | None,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    scope: ExecutionScope | None = None,
    record_failure: bool = True,
) -> dict[str, Any]:
    """Run one bounded argv command without a shell and freeze its audit log."""

    workspace = workspace.resolve()
    run_cwd = workspace if cwd is None else Path(cwd).resolve()
    if run_cwd.is_symlink() or not run_cwd.is_dir():
        _fail("invalid_command", "command working directory is unavailable")
    validate_workspace(workspace)
    active_root, _active, _plan = _load_active_attempt(workspace, attempt)
    _nonempty_string(phase, "$.phase")
    if phase == "build" and canonical_output is None:
        _fail(
            "invalid_command",
            "canonical builds must use the registered Workspace build command",
            "$.phase",
        )
    if not argv or any(not isinstance(value, str) or "\0" in value for value in argv):
        _fail("invalid_command", "command argv must contain non-empty strings")
    if timeout_seconds <= 0 or timeout_seconds > MAX_COMMAND_SECONDS:
        _fail(
            "invalid_command",
            f"timeout must be between 1 and {MAX_COMMAND_SECONDS} seconds",
        )
    commands_root = active_root / "commands"
    existing = [
        int(path.name)
        for path in commands_root.iterdir()
        if path.is_dir() and path.name.isdigit()
    ] if commands_root.is_dir() else []
    if len(existing) >= MAX_COMMANDS_PER_ATTEMPT:
        _fail("budget_violation", "attempt has exhausted its command allowance")
    command_id = max(existing, default=0) + 1
    started = time.monotonic()
    try:
        completed, timed_out = _run_bounded_command(
            argv,
            cwd=run_cwd,
            timeout_seconds=timeout_seconds,
            environment=environment,
            scope=scope,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        if timed_out:
            stderr += b"\ncommand timed out\n"
    except OSError as exc:
        if not record_failure:
            raise
        exit_code = 127
        stdout = b""
        stderr = f"command launch failed: {exc}\n".encode("utf-8", errors="replace")
        timed_out = False
    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    if canonical_output is not None and (
        timed_out
        or exit_code != 0
        or not (canonical_output / "build.json").is_file()
        or not (canonical_output / "measurement.glb").is_file()
    ):
        detail_source = stderr or stdout
        detail = _compact_process_detail(
            detail_source.decode("utf-8", errors="replace")
        )
        shutil.rmtree(canonical_output, ignore_errors=True)
        _fail(
            "build_preflight_failed",
            detail or "canonical build preflight did not publish required artifacts",
        )
    if exit_code != 0 and not record_failure:
        return {"exit_code": exit_code, "command": None}
    stored_stdout, stdout_metadata = _bounded_log(stdout)
    stored_stderr, stderr_metadata = _bounded_log(stderr)
    stdout_metadata["path"] = f"commands/{command_id:06d}/stdout.log"
    stderr_metadata["path"] = f"commands/{command_id:06d}/stderr.log"
    document = {
        "schema": COMMAND_SCHEMA,
        "command": command_id,
        "phase": phase,
        "argv": _redact_argv(argv),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "stdout": stdout_metadata,
        "stderr": stderr_metadata,
    }
    commands_root.mkdir(exist_ok=True)
    stage = commands_root / f".tmp-{command_id:06d}-{uuid.uuid4().hex}"
    stage.mkdir()
    (stage / "stdout.log").write_bytes(stored_stdout)
    (stage / "stderr.log").write_bytes(stored_stderr)
    _write_json(stage / "command.json", document)
    stage.rename(commands_root / f"{command_id:06d}")
    return document


def run_attempt_command(
    workspace: Path,
    *,
    attempt: int,
    phase: str,
    argv: list[str],
    timeout_seconds: int,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
    scope: ExecutionScope | None = None,
    record_failure: bool = True,
) -> dict[str, Any]:
    return _run_attempt_command(
        workspace,
        attempt=attempt,
        phase=phase,
        argv=argv,
        timeout_seconds=timeout_seconds,
        canonical_output=None,
        cwd=cwd,
        environment=environment,
        scope=scope,
        record_failure=record_failure,
    )


def run_canonical_build(
    workspace: Path,
    *,
    attempt: int,
    source: str,
    inputs: list[str],
    output_dir: str,
    tool_registry: Path,
    timeout_seconds: int,
    scope: ExecutionScope | None = None,
) -> dict[str, Any]:
    """Preflight and run the registered canonical builder for one Attempt."""

    workspace = workspace.resolve()
    validate_workspace(workspace)
    active_root, _active, _plan = _load_active_attempt(workspace, attempt)
    candidate_root = active_root / "candidate"
    source_path = _candidate_file(workspace, candidate_root, source, "$.source")
    input_paths = [
        _candidate_file(workspace, candidate_root, value, f"$.inputs[{index}]")
        for index, value in enumerate(inputs)
    ]
    output_path = _candidate_output(
        workspace, candidate_root, output_dir, "$.output_dir"
    )
    entrypoint = _registered_tool_entrypoint(tool_registry, "rebuild")

    argv = [
        sys.executable,
        str(entrypoint),
        "build",
        "--source",
        source_path.relative_to(workspace).as_posix(),
    ]
    for path in input_paths:
        argv.extend(("--input", path.relative_to(workspace).as_posix()))
    argv.extend(("--output-dir", output_path.relative_to(workspace).as_posix()))

    return _run_attempt_command(
        workspace,
        attempt=attempt,
        phase="build",
        argv=argv,
        timeout_seconds=timeout_seconds,
        canonical_output=output_path,
        scope=scope,
    )


def record_attempt(
    workspace: Path,
    *,
    attempt: int,
    result: str,
    classification: str,
) -> dict[str, Any]:
    """Atomically publish one failed or strategy-changed Attempt."""

    workspace = workspace.resolve()
    validate_workspace(workspace)
    active_root, active, _plan = _load_active_attempt(workspace, attempt)
    if result not in FAILED_ATTEMPT_RESULTS:
        _fail("invalid_attempt", "unsupported terminal Attempt result")
    _nonempty_string(classification, "$.classification")
    command_documents = _load_command_documents(active_root)
    has_failing_command = any(
        command["exit_code"] != 0 for command in command_documents
    )
    if has_failing_command and result != TOOL_FAILURE_RESULT:
        _fail(
            "invalid_attempt",
            "Attempt with a failing command must be recorded as tool_failure",
        )
    if result == TOOL_FAILURE_RESULT and not any(
        command["exit_code"] != 0 for command in command_documents
    ):
        _fail(
            "invalid_attempt",
            "tool_failure requires at least one recorded failing command",
        )
    if (
        result == TOOL_FAILURE_RESULT
        and _published_tool_failure_count(workspace, active["intended_step"])
        >= MAX_TOOL_FAILURES_PER_STEP
    ):
        _fail(
            "budget_violation",
            "intended step already has two published tool failures",
        )
    attempts_root = workspace / "attempts"
    attempts_root.mkdir(exist_ok=True)
    target = attempts_root / f"{attempt:06d}"
    if target.exists():
        _fail("workspace_conflict", f"Attempt {attempt} is already published")
    transaction = workspace / "work" / (
        f".tmp-attempt-{attempt:06d}-{uuid.uuid4().hex}"
    )
    stage = transaction / "attempt"
    stage.mkdir(parents=True)
    shutil.copy2(active_root / "plan.json", stage / "plan.json")
    if (active_root / "commands").exists():
        shutil.copytree(active_root / "commands", stage / "commands")
    if classification == "repair_evidence_failed":
        _copy_repair_evidence_failure(active_root, stage)
    files = _inventory(stage)
    document: dict[str, Any] = {
        **{key: active[key] for key in _ACTIVE_ATTEMPT_FIELDS - {"result"}},
        "result": result,
        "classification": classification,
        "command_ids": [item["command"] for item in command_documents],
        "files": files,
    }
    document["identity_sha256"] = _identity(ATTEMPT_SCHEMA, document)
    _write_json(stage / "attempt.json", document)
    _write_json(
        transaction / "transaction.json",
        {
            "schema": "mesh-to-cad.transaction/1",
            "kind": "attempt",
            "attempt": attempt,
            "attempt_identity_sha256": document["identity_sha256"],
        },
    )
    _validate_published_attempt(stage, expected_attempt=attempt)
    stage.rename(target)
    graph = rebuild_index(workspace, validate=False)
    _commit_protocol_paths(
        workspace,
        [f"attempts/{attempt:06d}", "step_index.json"],
        f"attempt {attempt}: {result}",
        {
            "Workspace-Attempt": str(attempt),
            "Intended-Step": str(document["intended_step"]),
            "Attempt-Result": result,
            "Plan-SHA256": document["plan_digest"],
            "Attempt-SHA256": document["identity_sha256"],
            "Workspace-SHA256": _workspace_identity(workspace),
        },
    )
    (transaction / "transaction.json").unlink()
    transaction.rmdir()
    shutil.rmtree(active_root)
    return {**document, "graph": graph}


def publish_cycle(
    workspace: Path,
    *,
    attempt: int,
    candidate: Path,
    candidate_mesh: str,
    measurement: Path,
    preview: Path,
    region_diff: Path,
    assessment: Path,
    source_changes: Path,
    _decision_facts_factory: Callable[
        [Mapping[str, Any], Mapping[str, Any], Mapping[str, Any] | None],
        Mapping[str, Any],
    ] | None = None,
    _repair_evidence_failures: bool = False,
) -> dict[str, Any]:
    """Publish one successful Repair Cycle edge and its Measured Step node."""

    workspace = workspace.resolve()
    active_root, active, plan = _load_active_attempt(workspace, attempt)
    intended_step = active["intended_step"]
    if intended_step <= 0 or active["intended_cycle"] != intended_step:
        _fail("invalid_attempt", "attempt is not a Repair Cycle attempt")
    _require_successful_attempt_commands(active_root)
    validate_workspace(workspace, allowed_voxblame_step=intended_step)
    existing_cycles = _published_numbers(workspace / "cycles")
    expected_cycle = len(existing_cycles) + 1
    if intended_step != expected_cycle:
        _fail(
            "budget_violation",
            f"next successful Repair Cycle must be {expected_cycle}",
        )
    if intended_step > MAX_REPAIR_CYCLES:
        _fail("budget_violation", "Workspace has exhausted five Repair Cycles")
    from_step = active["from_step"]
    if from_step is None:
        _fail("parent_mismatch", "Repair Cycle requires a source Measured Step")
    parent_manifest = _read_json(
        workspace / "steps" / f"{from_step:06d}" / "step.json", "$.parent_step"
    )
    region_diff = region_diff.resolve()
    assessment = assessment.resolve()
    source_changes = source_changes.resolve()
    evidence = _prepare_step_evidence(
        workspace,
        step=intended_step,
        parent_step=from_step,
        parent_observable_sha256=parent_manifest["observable_sha256"],
        candidate=candidate,
        candidate_mesh=candidate_mesh,
        measurement=measurement,
        preview=preview,
        repair_evidence=_repair_evidence_failures,
    )
    voxblame_path = _voxblame_step_path(
        workspace, evidence.measurement, intended_step
    )
    try:
        diff_document = _read_json(region_diff, "$.region_diff")
        _validate_region_diff_boundary(
            diff_document,
            plan=plan,
            plan_digest=active["plan_digest"],
            from_step=from_step,
            to_step=intended_step,
            before_observable=parent_manifest["observable_sha256"],
            after_observable=evidence.identities["observable_sha256"],
        )
    except WorkspaceError as error:
        if _repair_evidence_failures:
            raise RepairEvidenceFailure("region_diff_invalid") from error
        raise
    assessment_document = _read_json(assessment, "$.assessment")
    _validate_assessment(
        assessment_document, from_step=from_step, to_step=intended_step
    )
    try:
        source_changes_document = _read_json(source_changes, "$.source_changes")
        _validate_source_changes(
            source_changes_document, from_step=from_step, to_step=intended_step
        )
    except WorkspaceError as error:
        if _repair_evidence_failures:
            raise RepairEvidenceFailure("source_changes_invalid") from error
        raise
    attempt_ids = _cycle_attempt_ids(
        workspace,
        intended_step,
        from_step,
        active["plan_digest"],
        attempt,
    )
    transaction = workspace / "work" / (
        f".tmp-cycle-{intended_step:06d}-{uuid.uuid4().hex}"
    )
    step_stage = transaction / "step"
    cycle_stage = transaction / "cycle"
    step_stage.mkdir(parents=True)
    cycle_stage.mkdir()
    try:
        _copy_step_evidence(evidence, step_stage)
        _validate_candidate_rebuild_recipe_if_present(step_stage / "candidate")
    except Exception:
        shutil.rmtree(transaction, ignore_errors=True)
        raise
    step_files = _inventory(step_stage)
    step_document: dict[str, Any] = {
        "schema": STEP_SCHEMA,
        "step": intended_step,
        "parent_step": from_step,
        "cycle": intended_step,
        "attempt_ids": attempt_ids,
        **evidence.identities,
        "measurement_path": _workspace_relative(workspace, evidence.measurement),
        "compare_to": from_step,
        "accepted": _accepted(evidence.measurement_document),
        "no_observable_geometry_change": evidence.measurement_document[
            "no_observable_geometry_change"
        ],
        "files": step_files,
    }
    step_document["identity_sha256"] = _identity(STEP_SCHEMA, step_document)
    _write_json(step_stage / "step.json", step_document)

    _write_json(cycle_stage / "plan.json", plan)
    shutil.copy2(region_diff, cycle_stage / "diff.json")
    shutil.copy2(assessment, cycle_stage / "assessment.json")
    shutil.copy2(source_changes, cycle_stage / "source_changes.json")
    successful_attempt = {**active, "result": "repair_cycle_published"}
    _write_json(cycle_stage / "attempt.json", successful_attempt)
    if (active_root / "commands").exists():
        shutil.copytree(active_root / "commands", cycle_stage / "logs/commands")
    cycle_files = _inventory(cycle_stage)
    cycle_document: dict[str, Any] = {
        "schema": CYCLE_SCHEMA,
        "cycle": intended_step,
        "from_step": from_step,
        "to_step": intended_step,
        "attempt_ids": attempt_ids,
        "plan_digest": active["plan_digest"],
        "region_diff_sha256": diff_document["identity"]["region_diff_sha256"],
        "assessment_sha256": _file_sha256(assessment),
        "source_changes_sha256": _file_sha256(source_changes),
        "from_observable_sha256": parent_manifest["observable_sha256"],
        "to_observable_sha256": evidence.identities["observable_sha256"],
        "no_observable_geometry_change": evidence.measurement_document[
            "no_observable_geometry_change"
        ],
        "files": cycle_files,
    }
    cycle_document["identity_sha256"] = _identity(CYCLE_SCHEMA, cycle_document)
    decision_facts = None
    _write_json(cycle_stage / "cycle.json", cycle_document)
    _write_json(
        transaction / "transaction.json",
        {
            "schema": "mesh-to-cad.transaction/1",
            "kind": "repair_cycle",
            "cycle": intended_step,
            "step_identity_sha256": step_document["identity_sha256"],
            "cycle_identity_sha256": cycle_document["identity_sha256"],
        },
    )
    _validate_step_directory(workspace, step_stage, expected_step=intended_step)
    _validate_cycle_directory(
        workspace, cycle_stage, expected_cycle=intended_step, step=step_document
    )
    if _decision_facts_factory is not None:
        try:
            decision_facts = _decision_facts_factory(
                step_document, evidence.measurement_document, parent_manifest
            )
        except Exception:
            shutil.rmtree(transaction, ignore_errors=True)
            raise
        if not isinstance(decision_facts, Mapping):
            shutil.rmtree(transaction, ignore_errors=True)
            _fail("invalid_contract", "publication decision facts are invalid")
    steps_root = workspace / "steps"
    cycles_root = workspace / "cycles"
    steps_root.mkdir(exist_ok=True)
    cycles_root.mkdir(exist_ok=True)
    step_target = steps_root / f"{intended_step:06d}"
    cycle_target = cycles_root / f"{intended_step:06d}"
    if step_target.exists() or cycle_target.exists():
        _fail("workspace_conflict", "Repair Cycle target already exists")
    step_stage.rename(step_target)
    cycle_stage.rename(cycle_target)
    graph = rebuild_index(workspace, validate=False)
    _commit_protocol_paths(
        workspace,
        [
            f"steps/{intended_step:06d}",
            f"cycles/{intended_step:06d}",
            voxblame_path,
            "step_index.json",
        ],
        f"repair cycle {intended_step}: publish Measured Step {intended_step}",
        {
            "Repair-Cycle": str(intended_step),
            "Workspace-Step": str(intended_step),
            "From-Step": str(from_step),
            "Workspace-Attempt": str(attempt),
            "Plan-SHA256": active["plan_digest"],
            "Candidate-SHA256": step_document["candidate_mesh_sha256"],
            "Observable-SHA256": step_document["observable_sha256"],
            "Preview-SHA256": step_document["preview_identity_sha256"],
            "Step-SHA256": step_document["identity_sha256"],
            "Cycle-SHA256": cycle_document["identity_sha256"],
            "Workspace-SHA256": _workspace_identity(workspace),
        },
    )
    (transaction / "transaction.json").unlink()
    transaction.rmdir()
    shutil.rmtree(active_root)
    result = {**cycle_document, "step": step_document, "graph": graph}
    if decision_facts is not None:
        result["_decision_facts"] = dict(decision_facts)
    return result


def finalize_workspace(
    workspace: Path,
    *,
    selection: Path,
    notes: Path,
    rebuild_entrypoint: Path,
    geometry_entrypoint: Path,
    tool_registry: Path,
    browser_runtime_capability: Path | None = None,
    validate_after_publish: bool = True,
    scope: ExecutionScope | None = None,
) -> dict[str, Any]:
    """Rebuild the Selected Step and atomically publish Final Delivery."""

    workspace = workspace.resolve()
    validate_workspace(workspace)
    _reject_staged_changes(workspace)
    if (workspace / "final").exists():
        _fail("workspace_conflict", "Final Delivery is already published", "$.final")
    selection_document = _read_json(selection.resolve(), "$.selection")
    graph = _build_graph(workspace, validate_steps=True)
    _validate_final_selection(workspace, selection_document, graph)
    notes_bytes = _validate_final_notes(notes.resolve())
    selected_step = selection_document["selected_step"]
    selected_root = workspace / "steps" / f"{selected_step:06d}"
    selected_document = _read_json(selected_root / "step.json", "$.selected_step")
    candidate_root = selected_root / "candidate"
    recipe_path, recipe = _find_registered_recipe(candidate_root)
    rebuild_entrypoint = _external_entrypoint(
        rebuild_entrypoint, "$.rebuild_entrypoint"
    )
    geometry_entrypoint = _external_entrypoint(
        geometry_entrypoint, "$.geometry_entrypoint"
    )
    tool_registry_path = tool_registry.resolve()
    tool_registry_document = _load_tool_registry(
        tool_registry_path,
        rebuild_entrypoint=rebuild_entrypoint,
        geometry_entrypoint=geometry_entrypoint,
    )

    transaction = workspace / "work" / f".tmp-final-{uuid.uuid4().hex}"
    rebuild_root = transaction / "rebuild"
    package = transaction / "package"
    previous_notes = (
        (workspace / "notes.md").read_bytes()
        if (workspace / "notes.md").is_file()
        else None
    )
    previous_index = (workspace / "step_index.json").read_bytes()
    final_published = False
    final_committed = False
    try:
        rebuild_root.mkdir(parents=True)
        package.mkdir()
        input_records = _copy_rebuild_inputs(candidate_root, rebuild_root, recipe)
        shutil.copy2(recipe_path, rebuild_root / "rebuild.json")
        recipe_sha256 = _file_sha256(rebuild_root / "rebuild.json")
        rebuild_kwargs: dict[str, Any] = {"entrypoint": rebuild_entrypoint}
        if scope is not None:
            rebuild_kwargs["scope"] = scope
        build_root, rebuild_command = _run_registered_rebuild(
            rebuild_root,
            **rebuild_kwargs,
        )
        rebuild_command["entrypoint_sha256"] = tool_registry_document["rebuild"][
            "entrypoint_sha256"
        ]
        _verify_rebuild_inputs_unchanged(rebuild_root, input_records)
        build = _read_json(build_root / "build.json", "$.rebuild.build")
        if (build_root / "rebuild.json").read_bytes() != (
            rebuild_root / "rebuild.json"
        ).read_bytes():
            _fail(
                "build_provenance_conflict",
                "rebuilt recipe differs from the selected registered recipe",
            )
        measurement_mesh = _validate_build_provenance(
            rebuild_root,
            build_root,
            build,
            recipe=recipe,
        )

        _copy_delivery_source(rebuild_root, package / "source", input_records)
        shutil.copytree(build_root, package / "artifacts")
        shutil.copy2(build_root / "build.json", package / "build.json")
        shutil.copy2(build_root / "rebuild.json", package / "rebuild.json")
        shutil.copy2(selected_root / "measurement.json", package / "measurement.json")
        shutil.copy2(tool_registry_path, package / "tool-registry.json")
        _write_json(package / "selection.json", selection_document)

        verify_kwargs: dict[str, Any] = {
            "selected_step": selected_step,
            "output": package / "verification.json",
            "entrypoint": geometry_entrypoint,
        }
        if scope is not None:
            verify_kwargs["scope"] = scope
        _run_voxblame_verify(workspace, measurement_mesh, **verify_kwargs)
        preview_root = transaction / "preview"
        preview_kwargs: dict[str, Any] = {
            "selected_step": selected_step,
            "selected_summary": selected_root / "measurement.json",
            "output": preview_root,
            "entrypoint": geometry_entrypoint,
        }
        if scope is not None:
            preview_kwargs["scope"] = scope
        if browser_runtime_capability is not None:
            preview_kwargs["capability_path"] = browser_runtime_capability
        _run_final_preview(workspace, measurement_mesh, **preview_kwargs)
        shutil.copy2(preview_root / "preview.png", package / "preview.png")
        shutil.copy2(preview_root / "preview.json", package / "preview.json")

        verification = _read_json(
            package / "verification.json", "$.final.verification"
        )
        preview = _read_json(package / "preview.json", "$.final.preview")
        manifest: dict[str, Any] = {
            "schema": FINAL_DELIVERY_SCHEMA,
            "selected_step": selected_step,
            "accepted": selected_document["accepted"],
            "stop_reason": selection_document["stop_reason"],
            "source_identity_sha256": _path_digest(package / "source"),
            "artifacts_identity_sha256": _path_digest(package / "artifacts"),
            "build_sha256": _file_sha256(package / "build.json"),
            "rebuild_sha256": _file_sha256(package / "rebuild.json"),
            "selected_measurement_sha256": _file_sha256(
                package / "measurement.json"
            ),
            "verification_sha256": _file_sha256(package / "verification.json"),
            "verification_identity_sha256": verification["verification_sha256"],
            "preview_identity_sha256": preview["preview_identity_sha256"],
            "selection_sha256": _file_sha256(package / "selection.json"),
            "rebuild_execution": rebuild_command,
            "registered_recipe_sha256": recipe_sha256,
            "tool_registry_sha256": _file_sha256(package / "tool-registry.json"),
            "tool_registry_identity_sha256": tool_registry_document[
                "identity_sha256"
            ],
            "geometry_execution": dict(tool_registry_document["geometry"]),
            "files": _inventory(package),
        }
        manifest["identity_sha256"] = _identity(FINAL_DELIVERY_SCHEMA, manifest)
        _write_json(package / "manifest.json", manifest)
        _validate_final_directory(workspace, package, graph=graph)

        (transaction / "previous-step-index.json").write_bytes(previous_index)
        if previous_notes is not None:
            (transaction / "previous-notes.md").write_bytes(previous_notes)
        shutil.rmtree(rebuild_root)
        shutil.rmtree(preview_root)
        _write_json(
            transaction / "transaction.json",
            {
                "schema": "mesh-to-cad.transaction/1",
                "kind": "final_delivery",
                "selected_step": selected_step,
                "final_delivery_sha256": manifest["identity_sha256"],
                "previous_notes_exists": previous_notes is not None,
            },
        )
        package.rename(workspace / "final")
        final_published = True
        (transaction / "notes.md").write_bytes(notes_bytes)
        os.replace(transaction / "notes.md", workspace / "notes.md")
        graph = rebuild_index(workspace, validate=False)
        for child in list((workspace / "work").iterdir()):
            if child.resolve() == transaction.resolve():
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        _commit_protocol_paths(
            workspace,
            ["final", "notes.md", "step_index.json"],
            f"final: select step {selected_step}, accepted={str(selected_document['accepted']).lower()}",
            {
                "Final-Selected-Step": str(selected_step),
                "Final-Accepted": str(selected_document["accepted"]).lower(),
                "Final-Delivery-SHA256": manifest["identity_sha256"],
                "Observable-SHA256": selected_document["observable_sha256"],
                "Workspace-SHA256": _workspace_identity(workspace),
            },
        )
        final_committed = True
        shutil.rmtree(transaction)
        if validate_after_publish:
            validation = validate_workspace(workspace)
            graph = validation.graph
        return {**manifest, "graph": graph}
    except Exception:
        if transaction.exists():
            shutil.rmtree(transaction, ignore_errors=True)
        if final_published and not final_committed:
            _rollback_final_publication(
                workspace,
                previous_notes=previous_notes,
                previous_index=previous_index,
            )
        raise


def _validate_final_selection(
    workspace: Path,
    value: Mapping[str, Any],
    graph: Mapping[str, Any],
) -> None:
    root = _closed_object(
        value,
        {
            "schema",
            "considered_steps",
            "selected_step",
            "preview",
            "accepted",
            "stop_reason",
            "evidence",
        },
        "$.selection",
    )
    _const(root["schema"], FINAL_SELECTION_SCHEMA, "$.selection.schema")
    existing = {item["step"]: item for item in graph["steps"]}
    considered = root["considered_steps"]
    if (
        not isinstance(considered, list)
        or not considered
        or any(not isinstance(item, int) or isinstance(item, bool) for item in considered)
        or len(set(considered)) != len(considered)
        or any(item not in existing for item in considered)
    ):
        _fail("invalid_contract", "considered_steps must name unique Measured Steps", "$.selection.considered_steps")
    selected = root["selected_step"]
    if selected not in considered:
        _fail("invalid_contract", "Selected Step must be among considered_steps", "$.selection.selected_step")
    step = existing[selected]
    if root["accepted"] is not step["accepted"]:
        _fail("identity_conflict", "selection cannot change Measured Step acceptance", "$.selection.accepted")
    if root["stop_reason"] not in _STOP_REASONS:
        _fail("invalid_contract", "unsupported final stop reason", "$.selection.stop_reason")
    if root["accepted"] and root["stop_reason"] != "acceptance_satisfied":
        _fail("identity_conflict", "accepted selection requires acceptance_satisfied")
    if not root["accepted"] and root["stop_reason"] == "acceptance_satisfied":
        _fail("identity_conflict", "unaccepted selection cannot claim acceptance_satisfied")
    preview = _closed_object(
        root["preview"],
        {"identity_sha256", "observation", "evidence_conflict", "conflict_details"},
        "$.selection.preview",
    )
    _sha256(preview["identity_sha256"], "$.selection.preview.identity_sha256")
    _nonempty_string(preview["observation"], "$.selection.preview.observation")
    if not isinstance(preview["evidence_conflict"], bool):
        _fail("invalid_contract", "evidence_conflict must be boolean", "$.selection.preview.evidence_conflict")
    if preview["evidence_conflict"]:
        _fail("agent_semantic_conflict", "Agent-reported material semantic conflict blocks Final Delivery")
    if preview["conflict_details"] is not None:
        _fail("invalid_contract", "clear preview evidence requires null conflict_details")
    selected_document = _read_json(
        workspace / "steps" / f"{selected:06d}" / "step.json", "$.selected_step"
    )
    if preview["identity_sha256"] != selected_document["preview_identity_sha256"]:
        _fail("automatic_identity_conflict", "selection preview identity conflicts with Selected Step")
    evidence = root["evidence"]
    if not isinstance(evidence, list) or not evidence:
        _fail("invalid_contract", "selection evidence must not be empty", "$.selection.evidence")
    for index, raw in enumerate(evidence):
        item = _closed_object(raw, {"kind", "path", "sha256"}, f"$.selection.evidence[{index}]")
        _stable_key(item["kind"], f"$.selection.evidence[{index}].kind")
        path = _relative_member(workspace, item["path"], f"$.selection.evidence[{index}].path")
        _sha256(item["sha256"], f"$.selection.evidence[{index}].sha256")
        if _file_sha256(path) != item["sha256"]:
            _fail("automatic_identity_conflict", "selection evidence digest mismatch")


def _validate_final_notes(path: Path) -> bytes:
    try:
        body = path.read_bytes()
        text = body.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        _fail("invalid_contract", "notes must be readable UTF-8", "$.notes")
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    if tuple(headings) != _NOTES_HEADINGS:
        _fail("invalid_contract", "notes headings must match the seven-section contract", "$.notes")
    return body


def _find_registered_recipe(candidate_root: Path) -> tuple[Path, dict[str, Any]]:
    recipes = [
        path
        for path in candidate_root.rglob("rebuild.json")
        if path.is_file() and not path.is_symlink()
    ]
    if len(recipes) != 1:
        _fail("invalid_rebuild_recipe", "Selected source bundle must contain exactly one rebuild.json")
    recipe = _read_json(recipes[0], "$.rebuild_recipe")
    _validate_registered_recipe_document(recipe)
    return recipes[0], recipe


def _validate_candidate_rebuild_recipe_if_present(candidate_root: Path) -> None:
    recipes = [
        path
        for path in candidate_root.rglob("rebuild.json")
        if path.is_file() and not path.is_symlink()
    ]
    if not recipes:
        return
    if len(recipes) != 1:
        _fail("invalid_rebuild_recipe", "Candidate bundle must not contain multiple rebuild.json files")
    recipe = _read_json(recipes[0], "$.rebuild_recipe")
    _validate_registered_recipe_document(recipe)
    _validated_rebuild_inputs(candidate_root, recipe)


def _validate_registered_recipe_document(recipe: Mapping[str, Any]) -> None:
    if "route" in recipe:
        _fail(
            "invalid_rebuild_recipe",
            "CAD routing metadata is unsupported",
            "$.rebuild_recipe.route",
        )
    if recipe.get("schema") != "mesh-to-cad.rebuild-recipe/1":
        _fail("invalid_rebuild_recipe", "registered recipe schema conflicts")
    if (
        recipe.get("executable") != "cad.canonical-build/1"
        or recipe.get("workingDirectory") != "."
        or recipe.get("network") != "forbidden"
        or recipe.get("ambientInputs") != "forbidden"
    ):
        _fail("invalid_rebuild_recipe", "CAD recipe is not the registered offline contract")
    inputs = recipe.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        _fail("invalid_rebuild_recipe", "registered recipe inputs are missing")


def _copy_rebuild_inputs(
    candidate_root: Path,
    rebuild_root: Path,
    recipe: Mapping[str, Any],
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for item, source in _validated_rebuild_inputs(candidate_root, recipe):
        target = rebuild_root.joinpath(*PurePosixPath(item["path"]).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        records.append({"path": item["path"], "sha256": item["sha256"]})
    return records


def _validated_rebuild_inputs(
    candidate_root: Path,
    recipe: Mapping[str, Any],
) -> list[tuple[Mapping[str, Any], Path]]:
    validated: list[tuple[Mapping[str, Any], Path]] = []
    expected_fields = {"id", "role", "path", "sha256"}
    for index, item in enumerate(recipe["inputs"]):
        if not isinstance(item, Mapping) or set(item) != expected_fields:
            _fail("invalid_rebuild_recipe", "recipe input shape is invalid", f"$.rebuild_recipe.inputs[{index}]")
        source = _relative_member(candidate_root, item.get("path"), f"$.rebuild_recipe.inputs[{index}].path")
        _sha256(item.get("sha256"), f"$.rebuild_recipe.inputs[{index}].sha256")
        if _file_sha256(source) != item["sha256"]:
            _fail("source_mutation", "archived source digest conflicts with rebuild recipe")
        validated.append((item, source))
    return validated


def _verify_rebuild_inputs_unchanged(root: Path, records: list[dict[str, str]]) -> None:
    for item in records:
        path = _relative_member(root, item["path"], "$.rebuild.inputs")
        if _file_sha256(path) != item["sha256"]:
            _fail("source_mutation", "registered rebuild mutated archived source")


def _copy_delivery_source(
    rebuild_root: Path,
    delivery_root: Path,
    records: list[dict[str, str]],
) -> None:
    """Archive every declared input under a reproducible source-bundle root."""

    delivery_root.mkdir()
    for item in records:
        source = _relative_member(rebuild_root, item["path"], "$.rebuild.inputs")
        target = delivery_root.joinpath(*PurePosixPath(item["path"]).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _external_entrypoint(path: Path, field: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("invalid_arguments", "external tool entrypoint does not exist", field)
    if not resolved.is_file():
        _fail("invalid_arguments", "external tool entrypoint is not executable code", field)
    return resolved


def _load_tool_registry(
    path: Path,
    *,
    rebuild_entrypoint: Path,
    geometry_entrypoint: Path,
) -> dict[str, Any]:
    registry = _validate_tool_registry_document(_read_json(path, "$.tool_registry"))
    rebuild = registry["rebuild"]
    geometry = registry["geometry"]
    for entry, entrypoint, field in (
        (rebuild, rebuild_entrypoint, "$.tool_registry.rebuild.entrypoint_sha256"),
        (geometry, geometry_entrypoint, "$.tool_registry.geometry.entrypoint_sha256"),
    ):
        if _file_sha256(entrypoint) != entry["entrypoint_sha256"]:
            _fail("untrusted_tool", "tool entrypoint digest conflicts with registry", field)
    return registry


def _registered_tool_entrypoint(path: Path, tool: str) -> Path:
    registry = _validate_tool_registry_document(_read_json(path, "$.tool_registry"))
    if registry["schema"] != TOOL_REGISTRY_SCHEMA:
        _fail(
            "untrusted_tool",
            "canonical build requires a registry with an absolute entrypoint",
            "$.tool_registry.schema",
        )
    entry = registry[tool]
    entrypoint = _external_entrypoint(
        Path(entry["entrypoint"]), f"$.tool_registry.{tool}.entrypoint"
    )
    if _file_sha256(entrypoint) != entry["entrypoint_sha256"]:
        _fail(
            "untrusted_tool",
            "tool entrypoint digest conflicts with registry",
            f"$.tool_registry.{tool}.entrypoint_sha256",
        )
    return entrypoint


def _validate_tool_registry_document(value: Mapping[str, Any]) -> dict[str, Any]:
    registry = _closed_object(
        value,
        {"schema", "rebuild", "geometry", "identity_sha256"},
        "$.tool_registry",
    )
    schema = registry["schema"]
    if schema not in (LEGACY_TOOL_REGISTRY_SCHEMA, TOOL_REGISTRY_SCHEMA):
        _fail("untrusted_tool", "tool registry schema is unsupported", "$.tool_registry.schema")
    entry_fields = (
        {"id", "entrypoint_sha256"}
        if schema == LEGACY_TOOL_REGISTRY_SCHEMA
        else {"id", "entrypoint", "entrypoint_sha256"}
    )
    rebuild = _closed_object(
        registry["rebuild"],
        entry_fields,
        "$.tool_registry.rebuild",
    )
    geometry = _closed_object(
        registry["geometry"],
        entry_fields,
        "$.tool_registry.geometry",
    )
    if rebuild["id"] != "cad.canonical-build/1":
        _fail("untrusted_tool", "tool registry rebuild identity is not canonical CAD")
    if geometry["id"] != "mesh-compare.voxblame/1":
        _fail("untrusted_tool", "tool registry geometry identity is not VoxBlame")
    for entry, path_field, digest_field in (
        (
            rebuild,
            "$.tool_registry.rebuild.entrypoint",
            "$.tool_registry.rebuild.entrypoint_sha256",
        ),
        (
            geometry,
            "$.tool_registry.geometry.entrypoint",
            "$.tool_registry.geometry.entrypoint_sha256",
        ),
    ):
        if schema == TOOL_REGISTRY_SCHEMA:
            entrypoint = entry["entrypoint"]
            if not isinstance(entrypoint, str) or not _is_canonical_sandbox_absolute_path(
                entrypoint
            ):
                _fail(
                    "untrusted_tool",
                    "tool registry entrypoint is not a canonical sandbox path",
                    path_field,
                )
        _sha256(entry["entrypoint_sha256"], digest_field)
    identity_source = dict(registry)
    identity = identity_source.pop("identity_sha256")
    if identity != _identity(schema, identity_source):
        _fail("untrusted_tool", "tool registry identity digest conflicts")
    return {**dict(registry), "rebuild": dict(rebuild), "geometry": dict(geometry)}


def _run_registered_rebuild(
    rebuild_root: Path,
    *,
    entrypoint: Path,
    scope: ExecutionScope | None = None,
) -> tuple[Path, dict[str, Any]]:
    argv = [
        sys.executable,
        str(entrypoint),
        "rebuild",
        "--recipe",
        "rebuild.json",
        "--output-dir",
        "rebuilt",
    ]
    result, _timed_out = _run_bounded_command(
        argv,
        cwd=rebuild_root,
        text=True,
        timeout_seconds=MAX_COMMAND_SECONDS,
        scope=scope,
    )
    if result.returncode != 0:
        detail = _compact_process_detail(result.stderr or result.stdout)
        _fail("rebuild_failed", detail or "registered rebuild failed")
    build_root = rebuild_root / "rebuilt"
    if not build_root.is_dir():
        _fail("rebuild_failed", "registered rebuild did not publish its output")
    return build_root, {
        "registered_executable": "cad.canonical-build/1",
        "exit_code": result.returncode,
        "stdout_sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr.encode("utf-8")).hexdigest(),
    }


def _validate_build_provenance(
    rebuild_root: Path,
    build_root: Path,
    build: Mapping[str, Any],
    *,
    recipe: Mapping[str, Any],
) -> Path:
    if build.get("schema") != "mesh-to-cad.build/1":
        _fail("build_provenance_conflict", "rebuilt manifest schema conflicts")
    recipe_inputs = recipe.get("inputs")
    if not isinstance(recipe_inputs, list) or not recipe_inputs or any(
        not isinstance(item, Mapping) for item in recipe_inputs
    ):
        _fail("build_provenance_conflict", "rebuild recipe input records are missing")
    primary = build.get("primaryArtifact")
    measurement = build.get("measurementGlb")
    if not isinstance(primary, Mapping) or not isinstance(measurement, Mapping):
        _fail("build_provenance_conflict", "CAD build artifact identities are missing")
    files = build.get("files")
    if not isinstance(files, list) or any(
        not isinstance(item, Mapping) for item in files
    ):
        _fail("build_provenance_conflict", "CAD build file records are missing")
    by_id = {
        item.get("id"): item
        for item in files
        if isinstance(item.get("id"), str)
    }
    if len(by_id) != len(files):
        _fail("build_provenance_conflict", "CAD build file identities conflict")
    primary_id = primary.get("fileId")
    measurement_id = measurement.get("fileId")
    if not isinstance(primary_id, str) or not isinstance(measurement_id, str):
        _fail("build_provenance_conflict", "CAD artifact file identities are invalid")
    primary_path = _relative_member(build_root, primary.get("path"), "$.build.primaryArtifact.path")
    measurement_path = _relative_member(build_root, measurement.get("path"), "$.build.measurementGlb.path")
    if _file_sha256(primary_path) != primary.get("sha256") or _file_sha256(measurement_path) != measurement.get("sha256"):
        _fail("build_provenance_conflict", "CAD build artifact digest mismatch")
    derivation = build.get("derivation")
    if not isinstance(derivation, list):
        _fail("build_provenance_conflict", "CAD derivation is missing")
    edges = {
        (edge.get("from"), edge.get("to"))
        for edge in derivation
        if isinstance(edge, Mapping)
    }
    for index, declared in enumerate(recipe_inputs):
        input_id = declared.get("id")
        record = by_id.get(f"input:{input_id}")
        if (
            not isinstance(input_id, str)
            or not isinstance(record, Mapping)
            or record.get("path") != declared.get("path")
            or record.get("sha256") != declared.get("sha256")
            or _file_sha256(
                _relative_member(
                    rebuild_root,
                    declared.get("path"),
                    f"$.rebuild_recipe.inputs[{index}].path",
                )
            )
            != declared.get("sha256")
            or (f"input:{input_id}", primary_id) not in edges
        ):
            _fail(
                "build_provenance_conflict",
                "CAD source input is not bound to the rebuilt STEP",
            )
    if (
        by_id.get(primary_id, {}).get("sha256")
        != primary.get("sha256")
        or by_id.get(measurement_id, {}).get("sha256")
        != measurement.get("sha256")
    ):
        _fail("build_provenance_conflict", "CAD artifact records conflict")
    if (primary_id, measurement_id) not in edges:
        _fail("build_provenance_conflict", "CAD STEP-to-GLB derivation is missing")
    return measurement_path


def _run_voxblame_verify(
    workspace: Path,
    candidate: Path,
    *,
    selected_step: int,
    output: Path,
    entrypoint: Path,
    scope: ExecutionScope | None = None,
) -> None:
    argv = [
        sys.executable,
        str(entrypoint),
        "voxblame-verify",
        str(candidate),
        "--reference",
        str(workspace / "input"),
        "--workspace",
        str(workspace / "voxblame"),
        "--against-step",
        str(selected_step),
        "--output",
        str(output),
    ]
    result, _timed_out = _run_bounded_command(
        argv,
        cwd=workspace,
        text=True,
        timeout_seconds=MAX_COMMAND_SECONDS,
        scope=scope,
    )
    if result.returncode != 0 or not output.is_file():
        _fail("verification_mismatch", _compact_process_detail(result.stderr or result.stdout) or "rebuilt Observable Geometry does not match Selected Step")


def _run_final_preview(
    workspace: Path,
    candidate: Path,
    *,
    selected_step: int,
    selected_summary: Path,
    output: Path,
    entrypoint: Path,
    capability_path: Path | None = None,
    scope: ExecutionScope | None = None,
) -> None:
    argv = [
        sys.executable,
        str(entrypoint),
        "voxblame-preview",
        str(candidate),
        "--reference",
        str(workspace / "input"),
        "--output",
        str(output),
        "--experiment",
        str(workspace / "experiment.json"),
        "--variant",
        "final",
        "--selected-step",
        str(selected_step),
        "--selected-summary",
        str(selected_summary),
    ]
    if capability_path is not None:
        argv.extend(["--capability-path", str(capability_path)])
    last_detail = ""
    for _attempt in range(2):
        result, _timed_out = _run_bounded_command(
            argv,
            cwd=workspace,
            text=True,
            timeout_seconds=MAX_COMMAND_SECONDS,
            scope=scope,
        )
        if result.returncode == 0 and (output / "preview.json").is_file() and (output / "preview.png").is_file():
            return
        last_detail = _compact_process_detail(result.stderr or result.stdout)
        if output.exists():
            shutil.rmtree(output, ignore_errors=True)
    _fail("render_retries_exhausted", last_detail or "final preview failed twice")


def _compact_process_detail(value: str, limit: int = 1000) -> str:
    detail = " ".join(value.split())
    return detail if len(detail) <= limit else detail[: limit - 3] + "..."


def _validate_final_directory(
    workspace: Path,
    root: Path,
    *,
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    manifest = _read_json(root / "manifest.json", "$.final.manifest")
    fields = {
        "schema",
        "selected_step",
        "accepted",
        "stop_reason",
        "source_identity_sha256",
        "artifacts_identity_sha256",
        "build_sha256",
        "rebuild_sha256",
        "selected_measurement_sha256",
        "verification_sha256",
        "verification_identity_sha256",
        "preview_identity_sha256",
        "selection_sha256",
        "rebuild_execution",
        "registered_recipe_sha256",
        "tool_registry_sha256",
        "tool_registry_identity_sha256",
        "geometry_execution",
        "files",
        "identity_sha256",
    }
    document = _closed_object(manifest, fields, "$.final.manifest")
    _const(document["schema"], FINAL_DELIVERY_SCHEMA, "$.final.manifest.schema")
    identity_source = dict(document)
    identity = identity_source.pop("identity_sha256")
    if identity != _identity(FINAL_DELIVERY_SCHEMA, identity_source):
        _fail("corrupt_workspace", "Final Delivery identity digest mismatch")
    for key in (
        "source_identity_sha256",
        "artifacts_identity_sha256",
        "build_sha256",
        "rebuild_sha256",
        "selected_measurement_sha256",
        "verification_sha256",
        "verification_identity_sha256",
        "preview_identity_sha256",
        "selection_sha256",
        "registered_recipe_sha256",
        "tool_registry_sha256",
        "tool_registry_identity_sha256",
        "identity_sha256",
    ):
        _sha256(document[key], f"$.final.manifest.{key}")
    if _path_digest(root / "source") != document["source_identity_sha256"]:
        _fail("corrupt_workspace", "Final Delivery source identity mismatch")
    if _path_digest(root / "artifacts") != document["artifacts_identity_sha256"]:
        _fail("corrupt_workspace", "Final Delivery artifact identity mismatch")
    digest_paths = {
        "build_sha256": "build.json",
        "rebuild_sha256": "rebuild.json",
        "selected_measurement_sha256": "measurement.json",
        "verification_sha256": "verification.json",
        "selection_sha256": "selection.json",
        "tool_registry_sha256": "tool-registry.json",
    }
    for key, relative in digest_paths.items():
        if _file_sha256(root / relative) != document[key]:
            _fail("corrupt_workspace", f"Final Delivery {relative} digest mismatch")
    expected_files = document["files"]
    actual_files = _inventory(root)
    actual_files = [item for item in actual_files if item["path"] != "manifest.json"]
    if expected_files != actual_files:
        _fail("corrupt_workspace", "Final Delivery inventory does not cover its files")

    selection = _read_json(root / "selection.json", "$.final.selection")
    _validate_final_selection(workspace, selection, graph)
    selected_step = selection["selected_step"]
    if (
        document["selected_step"] != selected_step
        or document["accepted"] is not selection["accepted"]
        or document["stop_reason"] != selection["stop_reason"]
    ):
        _fail("identity_conflict", "Final Delivery selection projection conflicts")
    selected_root = workspace / "steps" / f"{selected_step:06d}"
    if (root / "measurement.json").read_bytes() != (
        selected_root / "measurement.json"
    ).read_bytes():
        _fail("corrupt_workspace", "final measurement is not the unchanged Selected Step summary")
    measurement = _read_json(root / "measurement.json", "$.final.measurement")
    if measurement.get("step") != selected_step:
        _fail("identity_conflict", "final measurement lost its original step number")

    rebuild = _read_json(root / "rebuild.json", "$.final.rebuild")
    _validate_registered_recipe_document(rebuild)
    if _file_sha256(root / "rebuild.json") != document["registered_recipe_sha256"]:
        _fail("build_provenance_conflict", "Final registered recipe digest conflicts")
    tool_registry = _validate_tool_registry_document(
        _read_json(root / "tool-registry.json", "$.final.tool_registry")
    )
    rebuild_execution = document["rebuild_execution"]
    if not isinstance(rebuild_execution, Mapping):
        _fail("untrusted_tool", "Final rebuild execution identity is invalid")
    if (
        tool_registry["identity_sha256"]
        != document["tool_registry_identity_sha256"]
        or tool_registry["geometry"] != document["geometry_execution"]
        or tool_registry["rebuild"]["id"]
        != rebuild_execution.get("registered_executable")
        or tool_registry["rebuild"]["entrypoint_sha256"]
        != rebuild_execution.get("entrypoint_sha256")
    ):
        _fail("untrusted_tool", "Final tool execution identity conflicts with registry")
    rebuilt_mesh = _validate_build_provenance(
        root / "source",
        root / "artifacts",
        _read_json(root / "build.json", "$.final.build"),
        recipe=rebuild,
    )

    verification = _closed_object(
        _read_json(root / "verification.json", "$.final.verification"),
        {
            "schema",
            "against_step",
            "canonical_reference",
            "selected_measurement",
            "rebuilt_measurement",
            "equality",
            "verified",
            "verification_sha256",
        },
        "$.final.verification",
    )
    _const(verification["schema"], VERIFICATION_SCHEMA, "$.final.verification.schema")
    verification_source = dict(verification)
    verification_identity = verification_source.pop("verification_sha256")
    if verification_identity != _identity(VERIFICATION_SCHEMA, verification_source):
        _fail("corrupt_workspace", "Final verification identity mismatch")
    equality = _closed_object(
        verification["equality"],
        {"interior", "exterior", "observable", "errors_by_depth"},
        "$.final.verification.equality",
    )
    if verification["against_step"] != selected_step or verification["verified"] is not True or any(value is not True for value in equality.values()):
        _fail("verification_mismatch", "Final verification does not prove complete Observable Geometry equality")
    if verification["selected_measurement"] != measurement["measurement"]:
        _fail("identity_conflict", "Final verification selected identity conflicts")
    if verification_identity != document["verification_identity_sha256"]:
        _fail("identity_conflict", "Final verification manifest identity conflicts")
    if (
        _file_sha256(rebuilt_mesh)
        != verification["rebuilt_measurement"].get("candidate_mesh_sha256")
    ):
        _fail(
            "build_provenance_conflict",
            "Final verification does not identify the rebuilt provenance mesh",
        )

    preview = _read_json(root / "preview.json", "$.final.preview")
    if (
        preview.get("schema") != "voxblame.preview/1"
        or preview.get("render_variant") != "final"
        or preview.get("selected_step") != selected_step
        or preview.get("selected_summary_sha256")
        != document["selected_measurement_sha256"]
        or preview.get("preview_identity_sha256")
        != document["preview_identity_sha256"]
    ):
        _fail("automatic_identity_conflict", "Final preview identity conflicts")
    preview_source = dict(preview)
    preview_identity = preview_source.pop("preview_identity_sha256")
    if preview_identity != hashlib.sha256(
        b"voxblame.preview/1\0" + _json_bytes(preview_source)
    ).hexdigest():
        _fail("corrupt_workspace", "Final preview identity digest mismatch")
    if _file_sha256(root / "preview.png") != preview.get("image", {}).get("sha256"):
        _fail("corrupt_workspace", "Final preview PNG digest mismatch")
    experiment = _load_workspace_document(workspace)
    if (
        preview.get("reference", {}).get("canonical_reference_sha256")
        != experiment["canonical_reference_sha256"]
        or preview.get("profile", {}).get("experiment_identity")
        != experiment["preview_profile"]
        or preview.get("candidate", {}).get("mesh_sha256")
        != verification["rebuilt_measurement"]["candidate_mesh_sha256"]
    ):
        _fail("automatic_identity_conflict", "Final preview depicts conflicting evidence")
    return dict(document)


def validate_workspace(
    workspace: Path, *, allowed_voxblame_step: int | None = None
) -> ValidationResult:
    workspace = workspace.resolve()
    if not (workspace / "workspace.json").is_file():
        if any((workspace / name).exists() for name in _LEGACY_ROOT_NAMES):
            _fail(
                "unsupported_legacy_workspace",
                "legacy Mesh-to-CAD layout is not a canonical Workspace",
            )
        incomplete = _find_incomplete_transactions(workspace)
        if incomplete or any(
            (workspace / name).exists()
            for name in ("input", "setup", "experiment.json")
        ):
            _fail(
                "incomplete_transaction",
                "Workspace setup publication is incomplete",
                incomplete[0]["path"] if incomplete else "$",
            )
        _fail("invalid_workspace", "workspace.json is missing")
    for name in _LEGACY_ROOT_NAMES:
        if (workspace / name).exists():
            _fail(
                "unsupported_legacy_workspace",
                f"legacy Workspace path is unsupported: {name}",
            )
    recovery = _find_incomplete_transactions(workspace)
    if allowed_voxblame_step is not None:
        allowed_path = f"voxblame/steps/{allowed_voxblame_step:06d}"
        recovery = [
            item
            for item in recovery
            if not (
                item["classification"] == "orphan_voxblame_step"
                and item["path"] == allowed_path
            )
        ]
    if recovery:
        _fail(
            "incomplete_transaction",
            "Workspace contains staged or incomplete publication state",
            recovery[0]["path"],
        )
    workspace_document = _load_workspace_document(workspace)
    experiment = _read_json(workspace / "experiment.json", "$.experiment")
    _validate_experiment(experiment)
    for key in (
        "workspace_id",
        "coordinate_contract",
        "canonical_reference_sha256",
        "preview_profile",
    ):
        if workspace_document[key] != experiment[key]:
            _fail("identity_conflict", f"workspace and experiment {key} differ")
    input_manifest = _read_json(workspace / "input/input.json", "$.input.input.json")
    if input_manifest.get("canonical_reference_sha256") != workspace_document[
        "canonical_reference_sha256"
    ]:
        _fail("identity_conflict", "input Canonical Reference identity conflicts")
    graph = _build_graph(workspace, validate_steps=True)
    index_path = workspace / "step_index.json"
    if not index_path.is_file():
        _fail("derived_index_missing", "step_index.json can be rebuilt")
    index = _read_json(index_path, "$.step_index")
    if index != graph:
        _fail("derived_index_conflict", "step_index.json does not match authority")
    _validate_git_evidence(workspace, graph)
    return ValidationResult(graph=graph, recovery=[])


def rebuild_index(workspace: Path, *, validate: bool = True) -> dict[str, Any]:
    workspace = workspace.resolve()
    _load_workspace_document(workspace)
    if validate and _find_incomplete_transactions(workspace):
        _fail("incomplete_transaction", "cannot index incomplete publication state")
    graph = _build_graph(workspace, validate_steps=validate)
    target = workspace / "step_index.json"
    stage = workspace / f".tmp-step-index-{uuid.uuid4().hex}"
    _write_json(stage, graph)
    os.replace(stage, target)
    return graph


def workspace_status(workspace: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    document = _load_workspace_document(workspace)
    graph = _build_graph(workspace, validate_steps=True)
    recovery = _find_incomplete_transactions(workspace)
    steps = graph["steps"]
    next_intended_step = graph["budget"]["completed_cycles"] + 1 if steps else 0
    attempt_ids: set[int] = set()
    tool_failures = 0
    for item in graph["failed_attempts"]:
        if item["intended_step"] == next_intended_step:
            attempt_ids.add(item["attempt"])
            if item["result"] == TOOL_FAILURE_RESULT:
                tool_failures += 1
    if next_intended_step in {item["step"] for item in steps}:
        step_document = _read_json(
            workspace / "steps" / f"{next_intended_step:06d}" / "step.json",
            "$.status.current_step",
        )
        attempt_ids.update(step_document["attempt_ids"])
    active_root = workspace / "work" / "attempts"
    if active_root.is_dir():
        for path in active_root.iterdir():
            if path.is_symlink():
                _fail("invalid_workspace_path", "active Attempt path is a symlink")
            if not path.is_dir() or not path.name.isdigit():
                continue
            attempt_path = path / "attempt.json"
            if not attempt_path.is_file():
                continue
            attempt_document = _read_json(attempt_path, "$.status.active_attempt")
            if attempt_document.get("intended_step") == next_intended_step:
                attempt_ids.add(attempt_document["attempt"])
    return {
        "schema": "mesh-to-cad.workspace-status/1",
        "workspace_id": document["workspace_id"],
        "base_step": 0 if steps else None,
        "head_steps": list(graph["heads"]),
        "completed_cycles": graph["budget"]["completed_cycles"],
        "remaining_cycles": graph["budget"]["remaining_cycles"],
        "next_intended_step": next_intended_step,
        "total_attempts": graph["budget"]["total_attempts"],
        "tool_failures": graph["budget"]["tool_failures"],
        "current_step_attempts": len(attempt_ids),
        "remaining_attempts": max(0, MAX_ATTEMPTS_PER_STEP - len(attempt_ids)),
        "current_step_tool_failures": tool_failures,
        "remaining_tool_failures": max(0, MAX_TOOL_FAILURES_PER_STEP - tool_failures),
        "final_delivery_present": graph["final_delivery"] is not None,
        "accepted_steps": graph["accepted_steps"],
        "recovery": recovery,
    }


def recover_workspace(workspace: Path) -> dict[str, Any]:
    """Finish validated marker-last cycle transactions after interruption."""

    workspace = workspace.resolve()
    setup_transactions = sorted(
        path
        for path in workspace.glob(".tmp-setup-*")
        if path.is_dir() and (path / "transaction.json").is_file()
    )
    recovered_setup = False
    for transaction in setup_transactions:
        _recover_setup_transaction(workspace, transaction)
        recovered_setup = True
    _load_workspace_document(workspace)
    _require_git_root(workspace)
    final_transactions = sorted(
        path
        for path in (workspace / "work").glob(".tmp-final-*")
        if path.is_dir() and (path / "transaction.json").is_file()
    ) if (workspace / "work").exists() else []
    step_transactions = sorted(
        path
        for path in (workspace / "work").glob(".tmp-step-zero-*")
        if path.is_dir() and (path / "transaction.json").is_file()
    ) if (workspace / "work").exists() else []
    attempt_transactions = sorted(
        path
        for path in (workspace / "work").glob(".tmp-attempt-*")
        if path.is_dir() and (path / "transaction.json").is_file()
    ) if (workspace / "work").exists() else []
    transaction_roots = sorted(
        path
        for path in (workspace / "work").glob(".tmp-cycle-*")
        if path.is_dir() and (path / "transaction.json").is_file()
    ) if (workspace / "work").exists() else []
    known = {
        path.resolve()
        for root in [
            *setup_transactions,
            *attempt_transactions,
            *step_transactions,
            *transaction_roots,
            *final_transactions,
        ]
        for path in (root, *root.rglob("*"))
    }
    unknown = [
        item
        for item in _find_incomplete_transactions(workspace)
        if not (workspace / item["path"]).resolve() in known
        and item["classification"] != "orphan_voxblame_step"
    ]
    if unknown:
        _fail(
            "unknown_staged_state",
            "recovery refuses staged state without a known transaction marker",
            unknown[0]["path"],
        )
    recovered: list[int] = []
    recovered_steps: list[int] = []
    recovered_attempts: list[int] = []
    recovered_final = [
        _recover_final_transaction(workspace, transaction)
        for transaction in final_transactions
    ]
    for transaction in attempt_transactions:
        recovered_attempts.append(
            _recover_attempt_transaction(workspace, transaction)
        )
    for transaction in step_transactions:
        recovered_steps.append(_recover_step_zero_transaction(workspace, transaction))
    for transaction in transaction_roots:
        recovered.append(_recover_cycle_transaction(workspace, transaction))
    if not recovered and not recovered_steps and not recovered_attempts and not recovered_final:
        orphans = [
            item
            for item in _find_incomplete_transactions(workspace)
            if item["classification"] == "orphan_voxblame_step"
        ]
        if orphans:
            _fail(
                "orphan_voxblame_step",
                "orphan measurement requires the original publication inputs",
                orphans[0]["path"],
            )
    result = validate_workspace(workspace)
    return {
        "recovered_setup": recovered_setup,
        "recovered_attempts": recovered_attempts,
        "recovered_steps": recovered_steps,
        "recovered_cycles": recovered,
        "recovered_final": recovered_final,
        "graph": result.graph,
    }


def _validate_experiment(value: Mapping[str, Any]) -> None:
    if "route" in value:
        detail = "implicit CAD experiment is unsupported" if value.get("route") != "cad" else "CAD routing metadata is unsupported"
        _fail("invalid_setup", detail, "$.experiment.route")
    root = _closed_object(value, _EXPERIMENT_FIELDS, "$.experiment")
    _const(root["schema"], EXPERIMENT_SCHEMA, "$.experiment.schema")
    _nonempty_string(root["workspace_id"], "$.experiment.workspace_id")
    _const(
        root["coordinate_contract"],
        COORDINATE_CONTRACT,
        "$.experiment.coordinate_contract",
    )
    _sha256(root["canonical_reference_sha256"], "$.experiment.canonical_reference_sha256")
    profile = _closed_object(root["preview_profile"], {"name", "sha256"}, "$.experiment.preview_profile")
    _nonempty_string(profile["name"], "$.experiment.preview_profile.name")
    _sha256(profile["sha256"], "$.experiment.preview_profile.sha256")


def _load_workspace_document(workspace: Path) -> dict[str, Any]:
    value = _read_json(workspace / "workspace.json", "$.workspace")
    if "route" in value:
        detail = "implicit CAD workspace is unsupported" if value.get("route") != "cad" else "CAD routing metadata is unsupported"
        _fail("invalid_setup", detail, "$.workspace.route")
    root = _closed_object(value, _WORKSPACE_FIELDS, "$.workspace")
    _const(root["schema"], WORKSPACE_SCHEMA, "$.workspace.schema")
    _const(root["coordinate_contract"], COORDINATE_CONTRACT, "$.workspace.coordinate_contract")
    _sha256(root["canonical_reference_sha256"], "$.workspace.canonical_reference_sha256")
    _sha256(root["input_identity_sha256"], "$.workspace.input_identity_sha256")
    _sha256(root["setup_identity_sha256"], "$.workspace.setup_identity_sha256")
    _closed_object(root["preview_profile"], {"name", "sha256"}, "$.workspace.preview_profile")
    limits = _closed_object(
        root["limits"],
        {"repair_cycles", "attempts_per_step", "tool_failures_per_step"},
        "$.workspace.limits",
    )
    expected = {
        "repair_cycles": MAX_REPAIR_CYCLES,
        "attempts_per_step": MAX_ATTEMPTS_PER_STEP,
        "tool_failures_per_step": MAX_TOOL_FAILURES_PER_STEP,
    }
    if limits != expected:
        _fail("budget_violation", "Workspace budget contract is unsupported")
    for name, identity_field in (
        ("input", "input_identity_sha256"),
        ("setup", "setup_identity_sha256"),
    ):
        path = workspace / name
        if not path.is_dir() or _path_digest(path) != root[identity_field]:
            _fail("corrupt_workspace", f"{name} artifact digest mismatch")
    return dict(root)


def _validate_staged_setup(stage: Path, expected: dict[str, Any]) -> None:
    actual = _read_json(stage / "workspace.json", "$.workspace")
    if actual != expected:
        _fail("invalid_setup", "staged workspace manifest changed")
    _validate_tree_source(stage / "input", "$.input")
    _validate_tree_source(stage / "setup", "$.setup")


def _prepare_step_evidence(
    workspace: Path,
    *,
    step: int,
    parent_step: int | None,
    parent_observable_sha256: str | None,
    candidate: Path,
    candidate_mesh: str,
    measurement: Path,
    preview: Path,
    repair_evidence: bool = False,
) -> _PreparedStepEvidence:
    candidate = candidate.resolve()
    preview = preview.resolve()
    measurement = measurement.resolve()
    _validate_tree_source(candidate, "$.candidate")
    _validate_candidate_rebuild_recipe_if_present(candidate)
    try:
        _validate_tree_source(preview, "$.preview")
    except WorkspaceError as error:
        if repair_evidence:
            raise RepairEvidenceFailure("preview_output_invalid") from error
        raise
    mesh_path = _relative_member(candidate, candidate_mesh, "$.candidate_mesh")
    if repair_evidence:
        try:
            measurement_document = _read_json(measurement, "$.measurement")
        except WorkspaceError as error:
            raise RepairEvidenceFailure("voxblame_output_invalid") from error
        try:
            preview_document = _read_json(preview / "preview.json", "$.preview")
        except WorkspaceError as error:
            raise RepairEvidenceFailure("preview_output_invalid") from error
    else:
        measurement_document = _read_json(measurement, "$.measurement")
        preview_document = _read_json(preview / "preview.json", "$.preview")
    identities = _validate_step_evidence(
        workspace,
        _load_workspace_document(workspace),
        step=step,
        parent_step=parent_step,
        parent_observable_sha256=parent_observable_sha256,
        mesh_path=mesh_path,
        measurement_path=measurement,
        measurement=measurement_document,
        preview_root=preview,
        preview=preview_document,
        repair_evidence=repair_evidence,
    )
    return _PreparedStepEvidence(
        candidate=candidate,
        preview=preview,
        measurement=measurement,
        measurement_document=measurement_document,
        identities=identities,
    )


def _copy_step_evidence(evidence: _PreparedStepEvidence, stage: Path) -> None:
    shutil.copytree(evidence.candidate, stage / "candidate")
    shutil.copytree(evidence.preview, stage / "preview")
    shutil.copy2(evidence.measurement, stage / "measurement.json")


def _validate_step_evidence(
    workspace: Path,
    experiment: Mapping[str, Any],
    *,
    step: int,
    parent_step: int | None,
    parent_observable_sha256: str | None,
    mesh_path: Path,
    measurement_path: Path,
    measurement: Mapping[str, Any],
    preview_root: Path,
    preview: Mapping[str, Any],
    repair_evidence: bool = False,
) -> dict[str, str]:
    try:
        _validate_measurement_boundary(measurement, step=step, compare_to=parent_step)
    except WorkspaceError as error:
        if repair_evidence:
            raise RepairEvidenceFailure("voxblame_output_invalid") from error
        raise
    try:
        _validate_preview_boundary(preview, preview_root)
    except WorkspaceError as error:
        if repair_evidence:
            raise RepairEvidenceFailure("preview_output_invalid") from error
        raise
    expected_no_op = (
        parent_observable_sha256 is not None
        and measurement["measurement"]["observable_sha256"]
        == parent_observable_sha256
    )
    if measurement["no_observable_geometry_change"] is not expected_no_op:
        if repair_evidence:
            raise RepairEvidenceFailure("voxblame_output_invalid")
        _fail(
            "identity_conflict",
            "Observable Geometry no-op fact contradicts parent identity",
        )
    candidate_sha = _file_sha256(mesh_path)
    reference_sha = experiment["canonical_reference_sha256"]
    profile = experiment["preview_profile"]
    checks = (
        (measurement["canonical_reference"]["canonical_reference_sha256"], reference_sha, "voxblame_output_invalid", "measurement reference"),
        (preview["reference"]["canonical_reference_sha256"], reference_sha, "preview_output_invalid", "preview reference"),
        (measurement["measurement"]["candidate_mesh_sha256"], candidate_sha, "voxblame_output_invalid", "measurement candidate"),
        (preview["candidate"]["mesh_sha256"], candidate_sha, "preview_output_invalid", "preview candidate"),
        (preview["profile"]["experiment_identity"], profile, "preview_output_invalid", "preview profile"),
        (preview["canonical_frame"]["coordinate_contract"], experiment["coordinate_contract"], "preview_output_invalid", "preview frame"),
    )
    for actual, expected, subtype, label in checks:
        if actual != expected:
            if repair_evidence:
                raise RepairEvidenceFailure(subtype)
            _fail("identity_conflict", f"{label} identity conflicts with the Workspace")
    try:
        _workspace_relative(workspace, measurement_path)
    except WorkspaceError as error:
        if repair_evidence:
            raise RepairEvidenceFailure("voxblame_output_invalid") from error
        raise
    return {
        "canonical_reference_sha256": reference_sha,
        "candidate_mesh_sha256": candidate_sha,
        "observable_sha256": measurement["measurement"]["observable_sha256"],
        "preview_identity_sha256": preview["preview_identity_sha256"],
        "preview_profile_sha256": profile["sha256"],
    }


def _validate_measurement_boundary(
    value: Mapping[str, Any], *, step: int, compare_to: int | None
) -> None:
    fields = {
        "schema",
        "coordinate_contract",
        "max_depth",
        "step",
        "compare_to",
        "report",
        "canonical_reference",
        "measurement",
        "errors_by_depth",
        "exterior_surface",
        "repair_targets",
        "objective_facts",
        "no_observable_geometry_change",
    }
    root = _closed_voxblame_object(value, fields, "$.measurement")
    if root["schema"] != "voxblame.summary/1":
        _fail(
            "unsupported_or_invalid_voxblame_state",
            "Measured Steps require canonical voxblame.summary/1 evidence",
        )
    if (
        root["coordinate_contract"] != COORDINATE_CONTRACT
        or root["max_depth"] != MAX_DEPTH
        or root["step"] != step
        or root["compare_to"] != compare_to
    ):
        _fail("parent_mismatch", "measurement ancestry or canonical frame conflicts")
    _relative_workspace_path(root["report"], "$.measurement.report")
    reference = _closed_voxblame_object(
        root["canonical_reference"],
        {
            "canonical_reference_sha256",
            "reference_ply_sha256",
            "triangle_set_sha256",
            "interior_tree_sha256",
        },
        "$.measurement.canonical_reference",
    )
    measurement = _closed_voxblame_object(
        root["measurement"],
        {
            "candidate_mesh_sha256",
            "interior_tree_sha256",
            "exterior_snapshot_sha256",
            "observable_sha256",
        },
        "$.measurement.measurement",
    )
    for key, digest in (*reference.items(), *measurement.items()):
        _sha256(digest, f"$.measurement.{key}")
    errors = root["errors_by_depth"]
    if not isinstance(errors, list) or len(errors) != MAX_DEPTH:
        _fail(
            "unsupported_or_invalid_voxblame_state",
            "measurement must contain ordered depths 1 through 8",
        )
    depth_fields = {
        "depth",
        "reference_surface_count",
        "candidate_surface_count",
        "missing_surface_count",
        "excess_surface_count",
        "union_surface_count",
        "surface_error_count",
        "surface_error_rate",
    }
    for expected_depth, raw in enumerate(errors, start=1):
        item = _closed_voxblame_object(
            raw, depth_fields, f"$.measurement.errors_by_depth[{expected_depth - 1}]"
        )
        counts = {
            key: item[key]
            for key in depth_fields - {"depth", "surface_error_rate"}
        }
        if item["depth"] != expected_depth or any(
            not isinstance(count, int) or isinstance(count, bool) or count < 0
            for count in counts.values()
        ):
            _fail(
                "unsupported_or_invalid_voxblame_state",
                "measurement depth counts are invalid",
            )
        if (
            item["missing_surface_count"] > item["reference_surface_count"]
            or item["excess_surface_count"] > item["candidate_surface_count"]
            or item["union_surface_count"]
            != item["reference_surface_count"] + item["excess_surface_count"]
            or item["union_surface_count"]
            != item["candidate_surface_count"] + item["missing_surface_count"]
            or item["surface_error_count"]
            != item["missing_surface_count"] + item["excess_surface_count"]
        ):
            _fail(
                "unsupported_or_invalid_voxblame_state",
                "measurement depth set-count identities conflict",
            )
        rate = item["surface_error_rate"]
        expected_rate = (
            item["surface_error_count"] / item["union_surface_count"]
            if item["union_surface_count"]
            else 0.0
        )
        if (
            not isinstance(rate, (int, float))
            or isinstance(rate, bool)
            or not math.isfinite(rate)
            or not math.isclose(rate, expected_rate, rel_tol=0.0, abs_tol=1e-15)
        ):
            _fail(
                "unsupported_or_invalid_voxblame_state",
                "measurement depth error rate conflicts with counts",
            )
    exterior = _closed_voxblame_object(
        root["exterior_surface"],
        {
            "storage_schema",
            "path",
            "logical_sha256",
            "surface_present",
            "surface_cell_count",
            "bounds_canonical",
            "centroid_canonical",
            "nearest_overrun",
            "farthest_overrun",
            "outside_directions",
            "diagnostic_grid_depth",
            "coarsened",
        },
        "$.measurement.exterior_surface",
    )
    if exterior["storage_schema"] != "voxblame.exterior-snapshot/1":
        _fail("unsupported_or_invalid_voxblame_state", "exterior schema is invalid")
    _relative_workspace_path(exterior["path"], "$.measurement.exterior_surface.path")
    _sha256(exterior["logical_sha256"], "$.measurement.exterior_surface.logical_sha256")
    if exterior["logical_sha256"] != measurement["exterior_snapshot_sha256"]:
        _fail("identity_conflict", "exterior snapshot identity conflicts")
    present = exterior["surface_present"]
    count = exterior["surface_cell_count"]
    directions = exterior["outside_directions"]
    if (
        not isinstance(present, bool)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not isinstance(directions, list)
        or not isinstance(exterior["diagnostic_grid_depth"], int)
        or not isinstance(exterior["coarsened"], bool)
    ):
        _fail("unsupported_or_invalid_voxblame_state", "exterior evidence is invalid")
    if present:
        if count == 0 or not _valid_directions(directions):
            _fail("unsupported_or_invalid_voxblame_state", "exterior presence conflicts")
        _validate_bounds(exterior["bounds_canonical"], "$.measurement.exterior_surface.bounds_canonical")
        _validate_vector3(exterior["centroid_canonical"], "$.measurement.exterior_surface.centroid_canonical")
        nearest = _finite_number(exterior["nearest_overrun"], "$.measurement.exterior_surface.nearest_overrun")
        farthest = _finite_number(exterior["farthest_overrun"], "$.measurement.exterior_surface.farthest_overrun")
        if nearest < 0 or nearest > farthest:
            _fail("unsupported_or_invalid_voxblame_state", "exterior overrun evidence is invalid")
    elif count != 0 or directions or any(
        exterior[key] is not None
        for key in (
            "bounds_canonical",
            "centroid_canonical",
            "nearest_overrun",
            "farthest_overrun",
        )
    ):
        _fail("unsupported_or_invalid_voxblame_state", "clear exterior evidence conflicts")
    targets = _closed_voxblame_object(
        root["repair_targets"],
        {"ordering_profile", "total", "returned", "remaining", "offset", "next_offset", "items"},
        "$.measurement.repair_targets",
    )
    if (
        not isinstance(targets["ordering_profile"], str)
        or not targets["ordering_profile"]
        or any(
            not isinstance(targets[key], int)
            or isinstance(targets[key], bool)
            or targets[key] < 0
            for key in ("total", "returned", "remaining", "offset")
        )
        or targets["returned"] > 8
        or not isinstance(targets["items"], list)
        or len(targets["items"]) != targets["returned"]
        or targets["returned"] + targets["remaining"]
        != max(targets["total"] - targets["offset"], 0)
        or targets["next_offset"]
        != (targets["offset"] + targets["returned"] if targets["remaining"] else None)
    ):
        _fail("unsupported_or_invalid_voxblame_state", "repair target page is invalid")
    for index, target in enumerate(targets["items"]):
        _validate_summary_target(
            target,
            step=step,
            expected_rank=targets["offset"] + index,
            path=f"$.measurement.repair_targets.items[{index}]",
        )
    facts = root["objective_facts"]
    if not isinstance(facts, Mapping) or set(facts) != {
        "global_depth_8_zero",
        "out_of_frame_clear",
        "no_evidence_conflict",
    } or any(not isinstance(facts[key], bool) for key in facts):
        _fail("unsupported_or_invalid_voxblame_state", "measurement objective facts are invalid")
    depth_eight = errors[-1]
    expected_zero = (
        depth_eight["missing_surface_count"] == 0
        and depth_eight["excess_surface_count"] == 0
    )
    if facts["global_depth_8_zero"] is not expected_zero:
        _fail("identity_conflict", "global depth-8 fact contradicts evidence")
    if facts["out_of_frame_clear"] is present:
        _fail("identity_conflict", "out-of-frame fact contradicts exterior evidence")
    if not facts["no_evidence_conflict"]:
        _fail("identity_conflict", "conflicting measurement evidence cannot publish")
    if not isinstance(root["no_observable_geometry_change"], bool):
        _fail("unsupported_or_invalid_voxblame_state", "measurement no-op fact is invalid")
    if step == 0 and root["no_observable_geometry_change"]:
        _fail("parent_mismatch", "Step 0 cannot be an Observable Geometry no-op")


def _validate_preview_boundary(value: Mapping[str, Any], root: Path) -> None:
    required = {
        "schema",
        "render_variant",
        "canonical_frame",
        "profile",
        "reference",
        "candidate",
        "image",
        "preview_identity_sha256",
    }
    if not isinstance(value, Mapping) or not required.issubset(value):
        _fail("invalid_preview", "preview metadata is incomplete")
    if value["schema"] != "voxblame.preview/1" or value["render_variant"] != "step":
        _fail("invalid_preview", "Measured Steps require a formal step preview")
    nested = {
        "canonical_frame": {"coordinate_contract"},
        "profile": {"experiment_identity"},
        "reference": {"canonical_reference_sha256"},
        "candidate": {"mesh_sha256"},
    }
    for key, required_fields in nested.items():
        item = value[key]
        if not isinstance(item, Mapping) or not required_fields.issubset(item):
            _fail("invalid_preview", f"preview {key} identity is incomplete")
    identity = value["profile"]["experiment_identity"]
    if not isinstance(identity, Mapping) or set(identity) != {"name", "sha256"}:
        _fail("invalid_preview", "preview profile experiment identity is invalid")
    _sha256(value["preview_identity_sha256"], "$.preview.preview_identity_sha256")
    identity_source = dict(value)
    identity = identity_source.pop("preview_identity_sha256")
    expected_identity = hashlib.sha256(
        b"voxblame.preview/1\0" + _json_bytes(identity_source)
    ).hexdigest()
    if identity != expected_identity:
        _fail("corrupt_workspace", "formal preview identity digest mismatch")
    image = value["image"]
    if not isinstance(image, Mapping) or image.get("path") != "preview.png":
        _fail("invalid_preview", "preview image path is invalid")
    _sha256(image.get("sha256"), "$.preview.image.sha256")
    if _file_sha256(root / "preview.png") != image["sha256"]:
        _fail("corrupt_workspace", "preview PNG digest mismatch")


def _closed_voxblame_object(
    value: Any, fields: set[str], path: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(
            "unsupported_or_invalid_voxblame_state",
            "canonical VoxBlame object fields are invalid",
            path,
        )
    return value


def _relative_workspace_path(value: Any, path: str) -> None:
    if not isinstance(value, str):
        _fail("unsupported_or_invalid_voxblame_state", "path must be relative", path)
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        _fail("unsupported_or_invalid_voxblame_state", "path must be relative", path)


def _validate_summary_target(
    value: Any, *, step: int, expected_rank: int, path: str
) -> None:
    target = _closed_voxblame_object(
        value,
        {
            "target_key",
            "source_step",
            "kind",
            "display_rank",
            "bounds_canonical",
            "error_profile",
            "mask",
            "component",
            "exterior",
        },
        path,
    )
    if (
        not isinstance(target["target_key"], str)
        or not target["target_key"].startswith(f"step-{step:06d}:")
        or target["source_step"] != step
        or target["display_rank"] != expected_rank
        or target["kind"] not in {"interior", "exterior"}
    ):
        _fail("unsupported_or_invalid_voxblame_state", "repair target identity is invalid", path)
    _validate_bounds(target["bounds_canonical"], f"{path}.bounds_canonical")
    profile = _closed_voxblame_object(
        target["error_profile"],
        {"missing_surface_count", "excess_surface_count", "surface_error_count"},
        f"{path}.error_profile",
    )
    if any(
        not isinstance(profile[key], int)
        or isinstance(profile[key], bool)
        or profile[key] < 0
        for key in profile
    ) or profile["surface_error_count"] != (
        profile["missing_surface_count"] + profile["excess_surface_count"]
    ):
        _fail("unsupported_or_invalid_voxblame_state", "repair target error profile is invalid", path)
    mask = _closed_voxblame_object(
        target["mask"],
        {"storage_schema", "logical_sha256", "region_count"},
        f"{path}.mask",
    )
    expected_mask_schema = (
        "octree_region_set/1"
        if target["kind"] == "interior"
        else "exterior_grid_region_set/1"
    )
    if (
        mask["storage_schema"] != expected_mask_schema
        or not isinstance(mask["region_count"], int)
        or isinstance(mask["region_count"], bool)
        or mask["region_count"] < 1
    ):
        _fail("unsupported_or_invalid_voxblame_state", "repair target mask is invalid", path)
    _sha256(mask["logical_sha256"], f"{path}.mask.logical_sha256")
    component = _closed_voxblame_object(
        target["component"],
        {"component_key", "split_index", "split_count", "split_reason"},
        f"{path}.component",
    )
    if (
        not isinstance(component["component_key"], str)
        or not component["component_key"]
        or not isinstance(component["split_reason"], str)
        or not component["split_reason"]
        or not isinstance(component["split_index"], int)
        or isinstance(component["split_index"], bool)
        or component["split_index"] < 0
        or not isinstance(component["split_count"], int)
        or isinstance(component["split_count"], bool)
        or component["split_count"] < 1
        or component["split_index"] >= component["split_count"]
    ):
        _fail("unsupported_or_invalid_voxblame_state", "repair target component is invalid", path)
    if target["kind"] == "interior":
        if target["exterior"] is not None:
            _fail("unsupported_or_invalid_voxblame_state", "interior target has exterior evidence", path)
        return
    exterior = _closed_voxblame_object(
        target["exterior"],
        {
            "centroid_canonical",
            "surface_cell_count",
            "nearest_overrun",
            "farthest_overrun",
            "outside_directions",
            "diagnostic_grid_depth",
            "coarsened",
        },
        f"{path}.exterior",
    )
    _validate_vector3(exterior["centroid_canonical"], f"{path}.exterior.centroid_canonical")
    if (
        not isinstance(exterior["surface_cell_count"], int)
        or isinstance(exterior["surface_cell_count"], bool)
        or exterior["surface_cell_count"] < 1
        or not _valid_directions(exterior["outside_directions"])
        or not isinstance(exterior["diagnostic_grid_depth"], int)
        or isinstance(exterior["diagnostic_grid_depth"], bool)
        or not isinstance(exterior["coarsened"], bool)
    ):
        _fail("unsupported_or_invalid_voxblame_state", "exterior target evidence is invalid", path)
    nearest = _finite_number(exterior["nearest_overrun"], f"{path}.exterior.nearest_overrun")
    farthest = _finite_number(exterior["farthest_overrun"], f"{path}.exterior.farthest_overrun")
    if nearest < 0 or nearest > farthest:
        _fail("unsupported_or_invalid_voxblame_state", "exterior overrun evidence is invalid", path)


def _validate_bounds(value: Any, path: str) -> None:
    bounds = _closed_voxblame_object(value, {"min", "max"}, path)
    minimum = _validate_vector3(bounds["min"], f"{path}.min")
    maximum = _validate_vector3(bounds["max"], f"{path}.max")
    if any(lower > upper for lower, upper in zip(minimum, maximum, strict=True)):
        _fail("unsupported_or_invalid_voxblame_state", "bounds are inverted", path)


def _validate_vector3(value: Any, path: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        _fail("unsupported_or_invalid_voxblame_state", "expected a 3-vector", path)
    return tuple(_finite_number(item, path) for item in value)  # type: ignore[return-value]


def _finite_number(value: Any, path: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        _fail("unsupported_or_invalid_voxblame_state", "expected a finite number", path)
    return float(value)


def _valid_directions(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and len(value) == len(set(value))
        and all(item in {"-x", "+x", "-y", "+y", "-z", "+z"} for item in value)
    )


def _validate_step_directory(workspace: Path, root: Path, *, expected_step: int) -> dict[str, Any]:
    value = _read_json(root / "step.json", f"$.steps[{expected_step}]")
    document = _closed_object(value, _STEP_FIELDS, f"$.steps[{expected_step}]")
    _const(document["schema"], STEP_SCHEMA, f"$.steps[{expected_step}].schema")
    _validate_candidate_rebuild_recipe_if_present(root / "candidate")
    if document["step"] != expected_step:
        _fail("parent_mismatch", "step directory and manifest number differ")
    identity_document = dict(document)
    identity = identity_document.pop("identity_sha256")
    if identity != _identity(STEP_SCHEMA, identity_document):
        _fail("corrupt_workspace", "Measured Step identity digest mismatch")
    files = document["files"]
    if not isinstance(files, list) or not files:
        _fail("corrupt_workspace", "Measured Step file inventory is empty")
    expected_paths: set[str] = set()
    for index, item in enumerate(files):
        entry = _closed_object(item, {"path", "sha256", "size_bytes"}, f"$.steps[{expected_step}].files[{index}]")
        path = _relative_member(root, entry["path"], f"$.steps[{expected_step}].files[{index}].path")
        if entry["path"] in expected_paths:
            _fail("corrupt_workspace", "duplicate Measured Step inventory path")
        expected_paths.add(entry["path"])
        if path.stat().st_size != entry["size_bytes"] or _file_sha256(path) != entry["sha256"]:
            _fail("corrupt_workspace", f"Measured Step artifact digest mismatch: {entry['path']}")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "step.json"
    }
    if expected_paths != actual_paths:
        _fail("corrupt_workspace", "Measured Step inventory does not cover its files")
    measurement = _read_json(root / "measurement.json", f"$.steps[{expected_step}].measurement")
    preview = _read_json(root / "preview/preview.json", f"$.steps[{expected_step}].preview")
    candidate_mesh_rel = next(
        (
            item["path"]
            for item in files
            if Path(item["path"]).suffix.lower() in {".glb", ".gltf"}
            and item["path"].startswith("candidate/")
            and item["sha256"] == document["candidate_mesh_sha256"]
        ),
        None,
    )
    if candidate_mesh_rel is None:
        _fail(
            "corrupt_workspace",
            "Measured Step candidate mesh identity is missing from its inventory",
        )
    experiment = _load_workspace_document(workspace)
    parent_observable = None
    if document["parent_step"] is not None:
        parent_document = _read_json(
            workspace
            / "steps"
            / f"{document['parent_step']:06d}"
            / "step.json",
            "$.step.parent",
        )
        parent_observable = parent_document.get("observable_sha256")
    identities = _validate_step_evidence(
        workspace,
        experiment,
        step=expected_step,
        parent_step=document["parent_step"],
        parent_observable_sha256=parent_observable,
        mesh_path=root / candidate_mesh_rel,
        measurement_path=workspace / document["measurement_path"],
        measurement=measurement,
        preview_root=root / "preview",
        preview=preview,
    )
    for key, expected in identities.items():
        if document[key] != expected:
            _fail("identity_conflict", f"Measured Step {key} conflicts")
    if document["compare_to"] != document["parent_step"]:
        _fail("parent_mismatch", "Measured Step compare_to and parent differ")
    if expected_step == 0:
        if document["parent_step"] is not None or document["cycle"] is not None:
            _fail("parent_mismatch", "Measured Step 0 cannot have a parent or cycle")
        successful_attempt = _read_json(root / "attempt.json", "$.step.attempt")
        successful_attempt = _closed_object(
            successful_attempt, _ACTIVE_ATTEMPT_FIELDS, "$.step.attempt"
        )
        if (
            document["attempt_ids"] != [successful_attempt["attempt"]]
            or successful_attempt["result"] != "measured_step_published"
            or successful_attempt["intended_cycle"] is not None
            or successful_attempt["intended_step"] != 0
            or successful_attempt["from_step"] is not None
        ):
            _fail("parent_mismatch", "Step 0 successful Attempt ancestry conflicts")
    elif (
        not isinstance(document["parent_step"], int)
        or document["parent_step"] < 0
        or document["parent_step"] >= expected_step
        or document["cycle"] != expected_step
    ):
        _fail("parent_mismatch", "repair Measured Step ancestry is invalid")
    if document["accepted"] != _accepted(measurement):
        _fail("identity_conflict", "Measured Step acceptance conflicts with evidence")
    if document["no_observable_geometry_change"] != measurement["no_observable_geometry_change"]:
        _fail("identity_conflict", "Measured Step no-op fact conflicts with evidence")
    source_measurement = workspace / document["measurement_path"]
    if not source_measurement.is_file() or source_measurement.read_bytes() != (root / "measurement.json").read_bytes():
        _fail("corrupt_workspace", "published VoxBlame summary conflicts with Measured Step")
    return dict(document)


def _build_graph(workspace: Path, *, validate_steps: bool) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    steps_root = workspace / "steps"
    if steps_root.exists():
        for child in sorted(steps_root.iterdir()):
            if child.name.startswith(".tmp-"):
                continue
            if not child.is_dir() or len(child.name) != 6 or not child.name.isdigit():
                _fail("corrupt_workspace", f"invalid Measured Step path: {child.name}")
            step_number = int(child.name)
            document = (
                _validate_step_directory(workspace, child, expected_step=step_number)
                if validate_steps
                else _read_json(child / "step.json", f"$.steps[{step_number}]")
            )
            steps.append(
                {
                    "step": step_number,
                    "parent_step": document["parent_step"],
                    "cycle": document["cycle"],
                    "accepted": document["accepted"],
                    "no_observable_geometry_change": document[
                        "no_observable_geometry_change"
                    ],
                    "candidate_mesh_sha256": document["candidate_mesh_sha256"],
                    "observable_sha256": document["observable_sha256"],
                    "measurement": f"steps/{step_number:06d}/measurement.json",
                    "preview": f"steps/{step_number:06d}/preview/preview.json",
                }
            )
    if steps and steps[0]["step"] != 0:
        _fail("parent_mismatch", "Workspace graph is missing Measured Step 0")
    if len(steps) > MAX_REPAIR_CYCLES + 1:
        _fail("budget_violation", "Workspace exceeds the five-cycle budget")
    cycles: list[dict[str, Any]] = []
    cycles_root = workspace / "cycles"
    if cycles_root.exists():
        for child in sorted(cycles_root.iterdir()):
            if child.name.startswith(".tmp-"):
                continue
            if not child.is_dir() or len(child.name) != 6 or not child.name.isdigit():
                _fail("corrupt_workspace", f"invalid Repair Cycle path: {child.name}")
            cycle_number = int(child.name)
            step_document = _read_json(
                workspace / "steps" / f"{cycle_number:06d}" / "step.json",
                "$.cycle.step",
            )
            document = (
                _validate_cycle_directory(
                    workspace,
                    child,
                    expected_cycle=cycle_number,
                    step=step_document,
                )
                if validate_steps
                else _read_json(child / "cycle.json", "$.cycle")
            )
            cycles.append(
                {
                    "cycle": cycle_number,
                    "from_step": document["from_step"],
                    "to_step": document["to_step"],
                    "attempt_ids": document["attempt_ids"],
                    "plan_digest": document["plan_digest"],
                    "no_observable_geometry_change": document[
                        "no_observable_geometry_change"
                    ],
                    "diff": f"cycles/{cycle_number:06d}/diff.json",
                }
            )
    if [item["cycle"] for item in cycles] != list(range(1, len(cycles) + 1)):
        _fail("parent_mismatch", "Repair Cycle numbers must be contiguous")
    nonzero_steps = [item["step"] for item in steps if item["step"] != 0]
    if nonzero_steps != [item["cycle"] for item in cycles]:
        _fail(
            "incomplete_transaction",
            "every nonzero Measured Step requires its matching Repair Cycle",
        )
    if len(cycles) > MAX_REPAIR_CYCLES:
        _fail("budget_violation", "Workspace exceeds five Repair Cycles")
    accepted_steps = [item["step"] for item in steps if item["accepted"]]
    failed_attempts: list[dict[str, Any]] = []
    attempts_root = workspace / "attempts"
    if attempts_root.exists():
        for child in sorted(attempts_root.iterdir()):
            if child.name.startswith(".tmp-"):
                continue
            if not child.is_dir() or len(child.name) != 6 or not child.name.isdigit():
                _fail("corrupt_workspace", f"invalid Attempt path: {child.name}")
            document = (
                _validate_published_attempt(child, expected_attempt=int(child.name))
                if validate_steps
                else _read_json(child / "attempt.json", "$.attempt")
            )
            failed_attempts.append(
                {
                    "attempt": document["attempt"],
                    "intended_step": document["intended_step"],
                    "from_step": document["from_step"],
                    "result": document["result"],
                    "classification": document["classification"],
                    "plan_digest": document["plan_digest"],
                }
            )
    attempt_ids_by_step: dict[int, set[int]] = {}
    tool_failure_counts: dict[int, int] = {}
    for item in failed_attempts:
        step = item["intended_step"]
        attempt_ids_by_step.setdefault(step, set()).add(item["attempt"])
        if item["result"] == TOOL_FAILURE_RESULT:
            tool_failure_counts[step] = tool_failure_counts.get(step, 0) + 1
    for item in steps:
        manifest = _read_json(
            workspace / "steps" / f"{item['step']:06d}" / "step.json", "$.step"
        )
        attempt_ids_by_step.setdefault(item["step"], set()).update(
            manifest["attempt_ids"]
        )
    if any(
        len(value) > MAX_ATTEMPTS_PER_STEP
        for value in attempt_ids_by_step.values()
    ):
        _fail("budget_violation", "an intended step exceeds three attempts")
    if any(
        value > MAX_TOOL_FAILURES_PER_STEP for value in tool_failure_counts.values()
    ):
        _fail("budget_violation", "an intended step exceeds two tool failures")
    all_attempt_ids = set().union(*attempt_ids_by_step.values()) if attempt_ids_by_step else set()
    total_attempts = len(all_attempt_ids)
    tool_failures = sum(tool_failure_counts.values())
    graph: dict[str, Any] = {
        "schema": INDEX_SCHEMA,
        "steps": steps,
        "cycles": cycles,
        "failed_attempts": failed_attempts,
        "accepted_steps": accepted_steps,
        "budget": {
            "completed_cycles": len(cycles),
            "remaining_cycles": MAX_REPAIR_CYCLES - len(cycles),
            "total_attempts": total_attempts,
            "tool_failures": tool_failures,
        },
        "heads": _heads_from_steps(steps),
        "final_delivery": None,
    }
    final_root = workspace / "final"
    if final_root.exists():
        final_document = (
            _validate_final_directory(workspace, final_root, graph=graph)
            if validate_steps
            else _read_json(final_root / "manifest.json", "$.final.manifest")
        )
        graph["final_delivery"] = {
            "selected_step": final_document["selected_step"],
            "accepted": final_document["accepted"],
            "stop_reason": final_document["stop_reason"],
            "identity_sha256": final_document["identity_sha256"],
            "manifest": "final/manifest.json",
        }
    return graph


def _validate_git_evidence(workspace: Path, graph: Mapping[str, Any]) -> None:
    _require_git_root(workspace)
    workspace_document = _load_workspace_document(workspace)
    workspace_identity = _identity(WORKSPACE_SCHEMA, workspace_document)
    _require_current_commit_trailers(
        workspace,
        "workspace.json",
        {
            "Workspace-Schema": WORKSPACE_SCHEMA,
            "Workspace-Id": workspace_document["workspace_id"],
            "Canonical-Reference-SHA256": workspace_document[
                "canonical_reference_sha256"
            ],
            "Workspace-SHA256": workspace_identity,
            "Input-SHA256": workspace_document["input_identity_sha256"],
            "Setup-SHA256": workspace_document["setup_identity_sha256"],
        },
    )
    for step in graph["steps"]:
        document = _read_json(
            workspace / "steps" / f"{step['step']:06d}" / "step.json",
            "$.git.step",
        )
        _require_current_commit_trailers(
            workspace,
            f"steps/{step['step']:06d}/step.json",
            {
                "Workspace-Step": str(step["step"]),
                "Candidate-SHA256": document["candidate_mesh_sha256"],
                "Observable-SHA256": document["observable_sha256"],
                "Preview-SHA256": document["preview_identity_sha256"],
                "Step-SHA256": document["identity_sha256"],
                "Workspace-SHA256": workspace_identity,
            },
        )
    for attempt in graph["failed_attempts"]:
        document = _read_json(
            workspace / "attempts" / f"{attempt['attempt']:06d}" / "attempt.json",
            "$.git.attempt",
        )
        _require_current_commit_trailers(
            workspace,
            f"attempts/{attempt['attempt']:06d}/attempt.json",
            {
                "Workspace-Attempt": str(attempt["attempt"]),
                "Intended-Step": str(document["intended_step"]),
                "Attempt-Result": document["result"],
                "Plan-SHA256": document["plan_digest"],
                "Attempt-SHA256": document["identity_sha256"],
                "Workspace-SHA256": workspace_identity,
            },
        )
    for cycle in graph["cycles"]:
        document = _read_json(
            workspace / "cycles" / f"{cycle['cycle']:06d}" / "cycle.json",
            "$.git.cycle",
        )
        _require_current_commit_trailers(
            workspace,
            f"cycles/{cycle['cycle']:06d}/cycle.json",
            {
                "Repair-Cycle": str(cycle["cycle"]),
                "Workspace-Step": str(document["to_step"]),
                "From-Step": str(document["from_step"]),
                "Plan-SHA256": document["plan_digest"],
                "Cycle-SHA256": document["identity_sha256"],
                "Workspace-SHA256": workspace_identity,
            },
        )
    final_delivery = graph.get("final_delivery")
    if final_delivery is not None:
        _require_current_commit_trailers(
            workspace,
            "final/manifest.json",
            {
                "Final-Selected-Step": str(final_delivery["selected_step"]),
                "Final-Accepted": str(final_delivery["accepted"]).lower(),
                "Final-Delivery-SHA256": final_delivery["identity_sha256"],
                "Workspace-SHA256": workspace_identity,
            },
        )
    tracked = _git(workspace, "ls-files", "-s").stdout
    for line in tracked.splitlines():
        path = line.split("\t", 1)[-1]
        if Path(path).suffix.lower() in _LFS_SUFFIXES:
            attr = _git(workspace, "check-attr", "filter", "--", path).stdout.strip()
            if not attr.endswith(": lfs"):
                _fail("lfs_contract_violation", f"LFS filter is not active for {path}")


def _require_current_commit_trailers(
    workspace: Path, path: str, expected: Mapping[str, str]
) -> None:
    result = _git(
        workspace,
        "log",
        "-1",
        "--format=%B",
        "--",
        path,
        check=False,
    )
    lines = set(result.stdout.splitlines()) if result.returncode == 0 else set()
    missing = [
        f"{key}: {value}"
        for key, value in expected.items()
        if f"{key}: {value}" not in lines
    ]
    if missing:
        _fail(
            "missing_git_evidence",
            f"current publishing commit for {path} is missing {missing[0]}",
        )


def _accepted(measurement: Mapping[str, Any]) -> bool:
    return all(measurement["objective_facts"].values())


def _copy_repair_evidence_failure(active_root: Path, stage: Path) -> None:
    source = active_root / "repair-evidence-failure.json"
    document = _closed_object(
        _read_json(source, "$.repair_evidence_failure"),
        {"schema", "subtype"},
        "$.repair_evidence_failure",
    )
    _const(
        document["schema"],
        REPAIR_EVIDENCE_FAILURE_SCHEMA,
        "$.repair_evidence_failure.schema",
    )
    if (
        not isinstance(document["subtype"], str)
        or document["subtype"] not in REPAIR_EVIDENCE_FAILURE_SUBTYPES
    ):
        _fail("invalid_contract", "unsupported repair evidence failure subtype", "$.repair_evidence_failure.subtype")
    shutil.copy2(source, stage / source.name)


def _inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _file_sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _step_zero_voxblame_paths(workspace: Path, measurement: Path) -> list[str]:
    relative = _workspace_relative(workspace, measurement)
    if not relative.startswith("voxblame/steps/000000/"):
        _fail("invalid_workspace_path", "Step 0 measurement must be under voxblame/steps/000000")
    ignore = workspace / "voxblame/.gitignore"
    if ignore.exists() and ignore.read_text(encoding="utf-8") != ".tmp-*\n":
        _fail("unsupported_or_invalid_voxblame_state", "invalid voxblame/.gitignore")
    if not ignore.exists():
        ignore.write_text(".tmp-*\n", encoding="utf-8")
    required = [
        "voxblame/.gitignore",
        "voxblame/session.json",
        "voxblame/reference.vbsvo",
        "voxblame/steps/000000",
    ]
    for path in required:
        if not (workspace / path).exists():
            _fail("unsupported_or_invalid_voxblame_state", f"missing {path}")
    return required


def _voxblame_step_path(workspace: Path, measurement: Path, step: int) -> str:
    relative = _workspace_relative(workspace, measurement)
    expected = f"voxblame/steps/{step:06d}/"
    if not relative.startswith(expected):
        _fail(
            "invalid_workspace_path",
            f"measurement must be under {expected.rstrip('/')}",
        )
    path = expected.rstrip("/")
    if not (workspace / path).is_dir():
        _fail("unsupported_or_invalid_voxblame_state", f"missing {path}")
    return path


def _configure_git_contract(workspace: Path) -> None:
    _git(workspace, "lfs", "version")
    _git(workspace, "lfs", "install", "--local")
    ignore = workspace / ".gitignore"
    _append_contract_lines(ignore, ["/run/", "/work/", ".tmp-*", "**/.tmp-*"])
    attributes = workspace / ".gitattributes"
    _append_contract_lines(
        attributes,
        [f"*{suffix} filter=lfs diff=lfs merge=lfs -text" for suffix in sorted(_LFS_SUFFIXES)],
    )


def _append_contract_lines(path: Path, required: Iterable[str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = list(existing)
    for line in required:
        if line not in lines:
            lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _commit_protocol_paths(
    workspace: Path,
    paths: list[str],
    subject: str,
    trailers: Mapping[str, str],
    *,
    allow_noop: bool = False,
    allow_existing_protocol_staging: bool = False,
) -> bool:
    normalized = sorted(set(paths))
    allowed_files: set[str] = set()
    for declared in normalized:
        path = workspace / declared
        if path.is_dir():
            allowed_files.update(
                child.relative_to(workspace).as_posix()
                for child in path.rglob("*")
                if child.is_file()
            )
        elif path.exists():
            allowed_files.add(declared)
    existing_staged = set(
        _git(workspace, "diff", "--cached", "--name-only").stdout.splitlines()
    )
    if existing_staged and (
        not allow_existing_protocol_staging
        or not existing_staged.issubset(allowed_files)
    ):
        _fail("git_scope_violation", "Workspace refuses pre-existing staged paths")
    _git(workspace, "add", "--", *normalized)
    staged = set(_git(workspace, "diff", "--cached", "--name-only").stdout.splitlines())
    if not staged and allow_noop:
        return False
    if not staged or not staged.issubset(allowed_files):
        _fail("git_scope_violation", "Git index contains paths outside this protocol publication")
    for path in staged:
        if Path(path).suffix.lower() in _LFS_SUFFIXES:
            attr = _git(workspace, "check-attr", "filter", "--", path).stdout.strip()
            if not attr.endswith(": lfs"):
                _fail("lfs_contract_violation", f"LFS filter is not active for {path}")
    message = subject + "\n\n" + "\n".join(f"{key}: {value}" for key, value in trailers.items())
    _git(workspace, "commit", "-m", message)
    return True


def _reject_staged_changes(workspace: Path) -> None:
    staged = _git(workspace, "diff", "--cached", "--name-only").stdout.strip()
    if staged:
        _fail("git_scope_violation", "Workspace refuses pre-existing staged paths")


def _require_git_root(workspace: Path) -> None:
    if not workspace.is_dir():
        _fail("invalid_workspace", "Workspace directory does not exist")
    result = _git(workspace, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0 or Path(result.stdout.strip()).resolve() != workspace:
        _fail("invalid_git_workspace", "Workspace must be the root of an existing Git repository")


def _git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ("git", *args),
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout).split())
        _fail("git_operation_failed", detail or f"git {' '.join(args)} failed")
    return result


def _load_active_attempt(workspace: Path, attempt: int) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root = workspace / "work/attempts" / f"{attempt:06d}"
    document = _read_json(root / "attempt.json", "$.attempt")
    _closed_object(document, _ACTIVE_ATTEMPT_FIELDS, "$.attempt")
    if document["schema"] != ATTEMPT_SCHEMA or document["attempt"] != attempt or document["result"] != "active":
        _fail("invalid_attempt", "attempt is not active")
    plan = _read_json(root / "plan.json", "$.attempt.plan")
    schema = plan.get("schema")
    if not isinstance(schema, str):
        _fail("corrupt_workspace", "active attempt plan schema is missing")
    digest = hashlib.sha256(
        schema.encode("utf-8") + b"\0" + _json_bytes(plan)
    ).hexdigest()
    if digest != document["plan_digest"]:
        _fail("corrupt_workspace", "active attempt plan digest mismatch")
    return root, document, plan


def _load_command_documents(active_root: Path) -> list[dict[str, Any]]:
    commands_root = active_root / "commands"
    if not commands_root.exists():
        return []
    documents: list[dict[str, Any]] = []
    for child in sorted(commands_root.iterdir()):
        if child.name.startswith(".tmp-"):
            _fail("incomplete_transaction", "Attempt contains a staged command")
        if not child.is_dir() or len(child.name) != 6 or not child.name.isdigit():
            _fail("corrupt_workspace", f"invalid command path: {child.name}")
        value = _read_json(child / "command.json", "$.command")
        if value.get("schema") != COMMAND_SCHEMA or value.get("command") != int(
            child.name
        ):
            _fail("corrupt_workspace", "command manifest identity mismatch")
        for stream in ("stdout", "stderr"):
            metadata = value.get(stream)
            if not isinstance(metadata, Mapping):
                _fail("corrupt_workspace", "command log metadata is invalid")
            log_path = child / f"{stream}.log"
            if (
                not log_path.is_file()
                or log_path.stat().st_size != metadata.get("stored_byte_count")
            ):
                _fail("corrupt_workspace", "command log size mismatch")
        documents.append(value)
    return documents


def _require_successful_attempt_commands(active_root: Path) -> None:
    if any(
        command["exit_code"] != 0
        for command in _load_command_documents(active_root)
    ):
        _fail(
            "invalid_attempt",
            "Attempt with a failing command must be recorded as tool_failure",
        )


def _validate_published_attempt(root: Path, *, expected_attempt: int) -> dict[str, Any]:
    value = _read_json(root / "attempt.json", "$.attempt")
    document = _closed_object(value, _PUBLISHED_ATTEMPT_FIELDS, "$.attempt")
    if document["schema"] != ATTEMPT_SCHEMA or document["attempt"] != expected_attempt:
        _fail("corrupt_workspace", "Attempt identity mismatch")
    if document["result"] not in FAILED_ATTEMPT_RESULTS:
        _fail("corrupt_workspace", "Attempt result is unsupported")
    identity_document = dict(document)
    identity = identity_document.pop("identity_sha256")
    if identity != _identity(ATTEMPT_SCHEMA, identity_document):
        _fail("corrupt_workspace", "Attempt identity digest mismatch")
    files = document["files"]
    if not isinstance(files, list) or not files:
        _fail("corrupt_workspace", "Attempt file inventory is empty")
    entries = [
        _closed_object(
            item,
            {"path", "sha256", "size_bytes"},
            f"$.attempt.files[{index}]",
        )
        for index, item in enumerate(files)
    ]
    expected_paths = {item["path"] for item in entries}
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "attempt.json"
    }
    if expected_paths != actual_paths:
        _fail("corrupt_workspace", "Attempt file inventory mismatch")
    for item in entries:
        path = _relative_member(root, item["path"], "$.attempt.files")
        if path.stat().st_size != item["size_bytes"] or _file_sha256(path) != item[
            "sha256"
        ]:
            _fail("corrupt_workspace", "Attempt artifact digest mismatch")
    commands = _load_command_documents(root)
    if document["command_ids"] != [item["command"] for item in commands]:
        _fail("corrupt_workspace", "Attempt command index mismatch")
    if document["result"] == TOOL_FAILURE_RESULT and not any(
        item["exit_code"] != 0 for item in commands
    ):
        _fail("corrupt_workspace", "tool_failure has no failing command")
    return dict(document)


def _redact_argv(argv: list[str]) -> list[str]:
    redacted: list[str] = []
    hide_next = False
    header_next = False
    for value in argv:
        lowered = value.lower()
        if header_next:
            redacted.append(
                "<redacted>" if lowered.startswith("authorization") else value
            )
            header_next = False
            continue
        if hide_next:
            redacted.append("<redacted>")
            hide_next = False
            continue
        matched_assignment = next(
            (
                secret
                for secret in _SECRET_ARGUMENTS
                if lowered.startswith(secret + "=")
            ),
            None,
        )
        if matched_assignment is not None:
            redacted.append(value.split("=", 1)[0] + "=<redacted>")
            continue
        if lowered.startswith("authorization:"):
            redacted.append("Authorization: <redacted>")
            continue
        header_assignment = next(
            (
                prefix
                for prefix in ("--header=", "-h=", "-h")
                if lowered.startswith(prefix + "authorization:")
            ),
            None,
        )
        if header_assignment is not None:
            original_prefix = value[: len(header_assignment)]
            redacted.append(original_prefix + "Authorization: <redacted>")
            continue
        redacted.append(value)
        if lowered in _SECRET_ARGUMENTS:
            hide_next = True
        elif lowered in {"--header", "-h"}:
            header_next = True
    return redacted


def _bounded_log(data: bytes) -> tuple[bytes, dict[str, Any]]:
    original = len(data)
    if original <= MAX_LOG_BYTES:
        stored = data
        truncated = False
    else:
        half = MAX_LOG_BYTES // 2
        stored = data[:half] + data[-half:]
        truncated = True
    return stored, {
        "path": "",
        "original_byte_count": original,
        "stored_byte_count": len(stored),
        "truncated": truncated,
        "truncation_policy": "head-tail-65536/1",
    }


def _check_attempt_budget(workspace: Path, intended_step: int) -> None:
    active_root = workspace / "work/attempts"
    if active_root.exists():
        for path in active_root.glob(
            "[0-9][0-9][0-9][0-9][0-9][0-9]/attempt.json"
        ):
            value = _read_json(path, "$.attempt")
            if value.get("intended_step") == intended_step:
                _fail(
                    "workspace_conflict",
                    "intended step already has an active Attempt",
                )
    if (
        _published_tool_failure_count(workspace, intended_step)
        >= MAX_TOOL_FAILURES_PER_STEP
    ):
        _fail(
            "budget_violation",
            "intended step has exhausted its tool failure allowance",
        )
    attempts = 0
    attempts_root = workspace / "attempts"
    if attempts_root.exists():
        for path in attempts_root.glob("[0-9][0-9][0-9][0-9][0-9][0-9]/attempt.json"):
            value = _read_json(path, "$.attempt")
            if value.get("intended_step") == intended_step:
                attempts += 1
    if attempts >= MAX_ATTEMPTS_PER_STEP:
        _fail("budget_violation", "intended step has exhausted its three attempts")


def _published_tool_failure_count(workspace: Path, intended_step: int) -> int:
    attempts_root = workspace / "attempts"
    if not attempts_root.exists():
        return 0
    return sum(
        1
        for path in attempts_root.glob("[0-9][0-9][0-9][0-9][0-9][0-9]/attempt.json")
        if (
            (value := _read_json(path, "$.attempt")).get("intended_step")
            == intended_step
            and value.get("result") == TOOL_FAILURE_RESULT
        )
    )


def _next_attempt_id(workspace: Path) -> int:
    ids: list[int] = []
    for base in (workspace / "attempts", workspace / "work/attempts"):
        if base.exists():
            ids.extend(int(path.name) for path in base.iterdir() if path.is_dir() and path.name.isdigit())
    step_glob = (workspace / "steps").glob("*/step.json") if (workspace / "steps").exists() else []
    for step_path in step_glob:
        ids.extend(_read_json(step_path, "$.step").get("attempt_ids", []))
    return max(ids, default=0) + 1


def _validate_repair_plan_boundary(plan: Mapping[str, Any], from_step: int | None) -> None:
    required = {
        "schema",
        "from_step",
        "selected_targets",
        "planned_edits",
        "rationale",
        "preview_observation",
    }
    _closed_object(plan, required, "$.plan")
    _const(plan["schema"], "voxblame.repair-batch/1", "$.plan.schema")
    if plan["from_step"] != from_step:
        _fail("parent_mismatch", "Repair Batch from_step conflicts with attempt")
    if not isinstance(plan["selected_targets"], list) or not plan["selected_targets"]:
        _fail("invalid_attempt", "Repair Batch must select one or more targets")
    if not isinstance(plan["planned_edits"], list) or not plan["planned_edits"]:
        _fail("invalid_attempt", "Repair Batch must contain Planned Edits")
    selected_ranks: set[int] = set()
    for index, item in enumerate(plan["selected_targets"]):
        target = _closed_object(
            item,
            {"rank", "kind", "bounds_canonical"},
            f"$.plan.selected_targets[{index}]",
        )
        rank = target["rank"]
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 0:
            _fail("invalid_attempt", "Repair Batch target ranks must be non-negative integers")
        if target["kind"] not in {"missing", "excess", "exterior"}:
            _fail("invalid_attempt", "Repair Batch target kind is invalid")
        _validate_bounds(target["bounds_canonical"], f"$.plan.selected_targets[{index}].bounds_canonical")
        if rank in selected_ranks:
            _fail("invalid_attempt", "Repair Batch target ranks must be unique")
        selected_ranks.add(rank)
    edit_keys: set[str] = set()
    mapped_targets: set[str] = set()
    for index, item in enumerate(plan["planned_edits"]):
        fields = {"edit_key", "target_ranks", "description"}
        if isinstance(item, Mapping) and "spec_region_id" in item:
            fields.add("spec_region_id")
        edit = _closed_object(
            item,
            fields,
            f"$.plan.planned_edits[{index}]",
        )
        _stable_key(edit["edit_key"], f"$.plan.planned_edits[{index}].edit_key")
        if edit["edit_key"] in edit_keys:
            _fail("invalid_attempt", "Planned Edit keys must be unique")
        edit_keys.add(edit["edit_key"])
        if (
            not isinstance(edit["target_ranks"], list)
            or not edit["target_ranks"]
            or any(not isinstance(rank, int) or isinstance(rank, bool) for rank in edit["target_ranks"])
            or len(set(edit["target_ranks"])) != len(edit["target_ranks"])
            or not set(edit["target_ranks"]).issubset(selected_ranks)
        ):
            _fail("invalid_attempt", "Planned Edit target mapping is invalid")
        mapped_targets.update(edit["target_ranks"])
        _nonempty_string(
            edit["description"], f"$.plan.planned_edits[{index}].description"
        )
        if "spec_region_id" in edit:
            _nonempty_string(
                edit["spec_region_id"],
                f"$.plan.planned_edits[{index}].spec_region_id",
            )
    if mapped_targets != selected_ranks:
        _fail("invalid_attempt", "every selected target must map to a Planned Edit")
    _nonempty_string(plan["rationale"], "$.plan.rationale")
    _nonempty_string(plan["preview_observation"], "$.plan.preview_observation")


def _find_incomplete_transactions(workspace: Path) -> list[dict[str, Any]]:
    recovery: list[dict[str, Any]] = []
    # Root-level runtime byproducts (especially run/) are telemetry. Search only
    # roots that can contain canonical authority or mutable publication state.
    staged_paths = list(workspace.glob(".tmp-*"))
    for name in (
        "input",
        "setup",
        "steps",
        "cycles",
        "attempts",
        "voxblame",
        "final",
        "work",
    ):
        root = workspace / name
        if root.exists():
            staged_paths.extend(root.rglob(".tmp-*"))
    for path in sorted(set(staged_paths)):
        recovery.append(
            {
                "classification": "staged_transaction",
                "path": path.relative_to(workspace).as_posix(),
            }
        )
    steps_root = workspace / "steps"
    if (workspace / "voxblame/steps").exists():
        published = {path.name for path in steps_root.iterdir() if path.is_dir()} if steps_root.exists() else set()
        for path in (workspace / "voxblame/steps").iterdir():
            if path.is_dir() and path.name.isdigit() and path.name not in published:
                recovery.append(
                    {
                        "classification": "orphan_voxblame_step",
                        "path": path.relative_to(workspace).as_posix(),
                    }
                )
    return sorted(recovery, key=lambda item: item["path"])


def _rollback_final_publication(
    workspace: Path,
    *,
    previous_notes: bytes | None,
    previous_index: bytes,
) -> None:
    _git(
        workspace,
        "restore",
        "--staged",
        "--",
        "final",
        "notes.md",
        "step_index.json",
        check=False,
    )
    final_root = workspace / "final"
    if final_root.is_dir() and not final_root.is_symlink():
        shutil.rmtree(final_root)
    elif final_root.exists():
        _fail("incomplete_transaction", "Final Delivery rollback target is unsafe")
    if previous_notes is None:
        if (workspace / "notes.md").is_file():
            (workspace / "notes.md").unlink()
    else:
        (workspace / "notes.md").write_bytes(previous_notes)
    (workspace / "step_index.json").write_bytes(previous_index)


def _recover_final_transaction(workspace: Path, transaction: Path) -> str:
    """Resolve an interrupted Final Delivery at the atomic Git commit boundary."""

    marker = _closed_object(
        _read_json(transaction / "transaction.json", "$.transaction"),
        {
            "schema",
            "kind",
            "selected_step",
            "final_delivery_sha256",
            "previous_notes_exists",
        },
        "$.transaction",
    )
    if (
        marker["schema"] != "mesh-to-cad.transaction/1"
        or marker["kind"] != "final_delivery"
        or not isinstance(marker["selected_step"], int)
        or isinstance(marker["selected_step"], bool)
        or not isinstance(marker["previous_notes_exists"], bool)
    ):
        _fail("incomplete_transaction", "Final Delivery transaction marker is invalid")
    _sha256(marker["final_delivery_sha256"], "$.transaction.final_delivery_sha256")
    previous_index = transaction / "previous-step-index.json"
    if not previous_index.is_file():
        _fail("incomplete_transaction", "Final Delivery recovery index is missing")
    previous_notes = transaction / "previous-notes.md"
    if marker["previous_notes_exists"] is not previous_notes.is_file():
        _fail("incomplete_transaction", "Final Delivery recovery notes conflict")

    final_root = workspace / "final"
    committed = False
    if final_root.is_dir() and not final_root.is_symlink():
        manifest = _read_json(final_root / "manifest.json", "$.final.manifest")
        if (
            manifest.get("identity_sha256") != marker["final_delivery_sha256"]
            or manifest.get("selected_step") != marker["selected_step"]
        ):
            _fail("identity_conflict", "published Final Delivery conflicts with recovery marker")
        committed = (
            _git(
                workspace,
                "ls-files",
                "--error-unmatch",
                "final/manifest.json",
                check=False,
            ).returncode
            == 0
            and _git(
                workspace,
                "diff",
                "--quiet",
                "HEAD",
                "--",
                "final",
                "notes.md",
                "step_index.json",
                check=False,
            ).returncode
            == 0
            and _git(
                workspace,
                "diff",
                "--cached",
                "--quiet",
                "--",
                "final",
                "notes.md",
                "step_index.json",
                check=False,
            ).returncode
            == 0
        )
    elif final_root.exists():
        _fail("incomplete_transaction", "Final Delivery recovery target is unsafe")

    if committed:
        _build_graph(workspace, validate_steps=True)
        shutil.rmtree(transaction)
        return "committed"

    _rollback_final_publication(
        workspace,
        previous_notes=(
            previous_notes.read_bytes() if marker["previous_notes_exists"] else None
        ),
        previous_index=previous_index.read_bytes(),
    )
    shutil.rmtree(transaction)
    return "rolled_back"


def _recover_cycle_transaction(workspace: Path, transaction: Path) -> int:
    marker = _read_json(transaction / "transaction.json", "$.transaction")
    root = _closed_object(
        marker,
        {
            "schema",
            "kind",
            "cycle",
            "step_identity_sha256",
            "cycle_identity_sha256",
        },
        "$.transaction",
    )
    if (
        root["schema"] != "mesh-to-cad.transaction/1"
        or root["kind"] != "repair_cycle"
        or not isinstance(root["cycle"], int)
        or root["cycle"] <= 0
        or root["cycle"] > MAX_REPAIR_CYCLES
    ):
        _fail("unknown_staged_state", "transaction marker is unsupported")
    cycle = root["cycle"]
    step_target = workspace / "steps" / f"{cycle:06d}"
    cycle_target = workspace / "cycles" / f"{cycle:06d}"
    step_stage = transaction / "step"
    cycle_stage = transaction / "cycle"

    if step_target.exists():
        published_step = _read_json(step_target / "step.json", "$.recovery.step")
        if published_step.get("identity_sha256") != root["step_identity_sha256"]:
            _fail("identity_conflict", "published Step conflicts with staged transaction")
        if step_stage.exists():
            staged_step = _read_json(step_stage / "step.json", "$.recovery.step")
            if staged_step.get("identity_sha256") != root["step_identity_sha256"]:
                _fail("identity_conflict", "staged Step identity conflicts")
            shutil.rmtree(step_stage)
    else:
        if not step_stage.is_dir():
            _fail("incomplete_transaction", "transaction cannot recover its Step")
        step_document = _validate_step_directory(
            workspace, step_stage, expected_step=cycle
        )
        if step_document["identity_sha256"] != root["step_identity_sha256"]:
            _fail("identity_conflict", "staged Step identity conflicts")
        step_stage.rename(step_target)

    step_document = _read_json(step_target / "step.json", "$.recovery.step")
    if cycle_target.exists():
        published_cycle = _read_json(
            cycle_target / "cycle.json", "$.recovery.cycle"
        )
        if published_cycle.get("identity_sha256") != root["cycle_identity_sha256"]:
            _fail("identity_conflict", "published Cycle conflicts with staged transaction")
        if cycle_stage.exists():
            staged_cycle = _read_json(
                cycle_stage / "cycle.json", "$.recovery.cycle"
            )
            if staged_cycle.get("identity_sha256") != root["cycle_identity_sha256"]:
                _fail("identity_conflict", "staged Cycle identity conflicts")
            shutil.rmtree(cycle_stage)
    else:
        if not cycle_stage.is_dir():
            _fail("incomplete_transaction", "transaction cannot recover its Cycle")
        cycle_document = _validate_cycle_directory(
            workspace,
            cycle_stage,
            expected_cycle=cycle,
            step=step_document,
        )
        if cycle_document["identity_sha256"] != root["cycle_identity_sha256"]:
            _fail("identity_conflict", "staged Cycle identity conflicts")
        cycle_target.parent.mkdir(exist_ok=True)
        cycle_stage.rename(cycle_target)

    graph = rebuild_index(workspace, validate=False)
    cycle_document = _read_json(cycle_target / "cycle.json", "$.recovery.cycle")
    step_document = _read_json(step_target / "step.json", "$.recovery.step")
    measurement = workspace / step_document["measurement_path"]
    _commit_protocol_paths(
        workspace,
        [
            f"steps/{cycle:06d}",
            f"cycles/{cycle:06d}",
            _voxblame_step_path(workspace, measurement, cycle),
            "step_index.json",
        ],
        f"repair cycle {cycle}: recover interrupted publication",
        {
            "Repair-Cycle": str(cycle),
            "Workspace-Step": str(cycle),
            "From-Step": str(cycle_document["from_step"]),
            "Workspace-Attempt": str(cycle_document["attempt_ids"][-1]),
            "Plan-SHA256": cycle_document["plan_digest"],
            "Candidate-SHA256": step_document["candidate_mesh_sha256"],
            "Observable-SHA256": step_document["observable_sha256"],
            "Preview-SHA256": step_document["preview_identity_sha256"],
            "Step-SHA256": step_document["identity_sha256"],
            "Cycle-SHA256": cycle_document["identity_sha256"],
            "Workspace-SHA256": _workspace_identity(workspace),
        },
        allow_noop=True,
        allow_existing_protocol_staging=True,
    )
    (transaction / "transaction.json").unlink()
    if any(transaction.iterdir()):
        _fail("unknown_staged_state", "transaction contains unknown recovery files")
    transaction.rmdir()
    active = workspace / "work/attempts" / f"{cycle_document['attempt_ids'][-1]:06d}"
    if active.exists():
        shutil.rmtree(active)
    # Ensure the graph was structurally rebuildable before final deep validation.
    if graph["budget"]["completed_cycles"] < cycle:
        _fail("incomplete_transaction", "recovered cycle is absent from graph")
    return cycle


def _recover_attempt_transaction(workspace: Path, transaction: Path) -> int:
    marker = _read_json(transaction / "transaction.json", "$.transaction")
    root = _closed_object(
        marker,
        {"schema", "kind", "attempt", "attempt_identity_sha256"},
        "$.transaction",
    )
    if (
        root["schema"] != "mesh-to-cad.transaction/1"
        or root["kind"] != "attempt"
        or not isinstance(root["attempt"], int)
        or isinstance(root["attempt"], bool)
        or root["attempt"] <= 0
    ):
        _fail("unknown_staged_state", "Attempt transaction marker is unsupported")
    _sha256(root["attempt_identity_sha256"], "$.transaction.attempt_identity_sha256")
    attempt = root["attempt"]
    target = workspace / "attempts" / f"{attempt:06d}"
    staged = transaction / "attempt"
    if target.exists():
        published = _read_json(target / "attempt.json", "$.recovery.attempt")
        if published.get("identity_sha256") != root["attempt_identity_sha256"]:
            _fail("identity_conflict", "published Attempt conflicts with transaction")
        _validate_published_attempt(target, expected_attempt=attempt)
        if staged.exists():
            staged_document = _read_json(
                staged / "attempt.json", "$.recovery.attempt"
            )
            if staged_document.get("identity_sha256") != root[
                "attempt_identity_sha256"
            ]:
                _fail("identity_conflict", "staged Attempt conflicts with transaction")
            shutil.rmtree(staged)
    else:
        if not staged.is_dir():
            _fail("incomplete_transaction", "transaction cannot recover its Attempt")
        document = _validate_published_attempt(staged, expected_attempt=attempt)
        if document["identity_sha256"] != root["attempt_identity_sha256"]:
            _fail("identity_conflict", "staged Attempt identity conflicts")
        target.parent.mkdir(exist_ok=True)
        staged.rename(target)
    graph = rebuild_index(workspace, validate=False)
    document = _read_json(target / "attempt.json", "$.recovery.attempt")
    _commit_protocol_paths(
        workspace,
        [f"attempts/{attempt:06d}", "step_index.json"],
        f"attempt {attempt}: recover {document['result']}",
        {
            "Workspace-Attempt": str(attempt),
            "Intended-Step": str(document["intended_step"]),
            "Attempt-Result": document["result"],
            "Plan-SHA256": document["plan_digest"],
            "Attempt-SHA256": document["identity_sha256"],
            "Workspace-SHA256": _workspace_identity(workspace),
        },
        allow_noop=True,
        allow_existing_protocol_staging=True,
    )
    active = workspace / "work/attempts" / f"{attempt:06d}"
    if active.exists():
        shutil.rmtree(active)
    (transaction / "transaction.json").unlink()
    if any(transaction.iterdir()):
        _fail("unknown_staged_state", "Attempt transaction contains unknown files")
    transaction.rmdir()
    if not any(item["attempt"] == attempt for item in graph["failed_attempts"]):
        _fail("incomplete_transaction", "recovered Attempt is absent from graph")
    return attempt


def _recover_setup_transaction(workspace: Path, transaction: Path) -> None:
    marker = _read_json(transaction / "transaction.json", "$.transaction")
    root = _closed_object(
        marker,
        {"schema", "kind", "workspace_identity_sha256"},
        "$.transaction",
    )
    if (
        root["schema"] != "mesh-to-cad.transaction/1"
        or root["kind"] != "setup"
    ):
        _fail("unknown_staged_state", "setup transaction marker is unsupported")
    for name in ("input", "setup", "experiment.json", "workspace.json"):
        staged = transaction / name
        target = workspace / name
        if target.exists():
            if staged.exists():
                if _path_digest(staged) != _path_digest(target):
                    _fail(
                        "identity_conflict",
                        f"published setup path conflicts with staged {name}",
                    )
                if staged.is_dir():
                    shutil.rmtree(staged)
                else:
                    staged.unlink()
        else:
            if not staged.exists():
                _fail(
                    "incomplete_transaction",
                    f"setup transaction cannot recover {name}",
                )
            staged.rename(target)
    workspace_document = _load_workspace_document(workspace)
    if (
        _identity(WORKSPACE_SCHEMA, workspace_document)
        != root["workspace_identity_sha256"]
    ):
        _fail("identity_conflict", "setup transaction identity conflicts")
    graph = rebuild_index(workspace, validate=False)
    _commit_protocol_paths(
        workspace,
        [
            ".gitignore",
            ".gitattributes",
            "input",
            "setup",
            "experiment.json",
            "workspace.json",
            "step_index.json",
        ],
        "setup: recover canonical Workspace",
        {
            "Workspace-Schema": WORKSPACE_SCHEMA,
            "Workspace-Id": workspace_document["workspace_id"],
            "Canonical-Reference-SHA256": workspace_document[
                "canonical_reference_sha256"
            ],
            "Workspace-SHA256": _identity(WORKSPACE_SCHEMA, workspace_document),
            "Input-SHA256": workspace_document["input_identity_sha256"],
            "Setup-SHA256": workspace_document["setup_identity_sha256"],
        },
        allow_noop=True,
        allow_existing_protocol_staging=True,
    )
    (transaction / "transaction.json").unlink()
    if any(transaction.iterdir()):
        _fail("unknown_staged_state", "setup transaction contains unknown files")
    transaction.rmdir()
    if graph["steps"]:
        _fail("identity_conflict", "setup recovery unexpectedly contains steps")


def _recover_step_zero_transaction(workspace: Path, transaction: Path) -> int:
    marker = _read_json(transaction / "transaction.json", "$.transaction")
    root = _closed_object(
        marker,
        {
            "schema",
            "kind",
            "attempt",
            "step_identity_sha256",
        },
        "$.transaction",
    )
    if (
        root["schema"] != "mesh-to-cad.transaction/1"
        or root["kind"] != "step_zero"
        or not isinstance(root["attempt"], int)
        or root["attempt"] <= 0
    ):
        _fail("unknown_staged_state", "Step 0 transaction marker is unsupported")
    target = workspace / "steps/000000"
    staged = transaction / "step"
    if target.exists():
        published = _read_json(target / "step.json", "$.recovery.step")
        if published.get("identity_sha256") != root["step_identity_sha256"]:
            _fail("identity_conflict", "published Step 0 conflicts")
        if staged.exists():
            staged_document = _read_json(staged / "step.json", "$.recovery.step")
            if staged_document.get("identity_sha256") != root[
                "step_identity_sha256"
            ]:
                _fail("identity_conflict", "staged Step 0 conflicts")
            shutil.rmtree(staged)
    else:
        if not staged.is_dir():
            _fail("incomplete_transaction", "transaction cannot recover Step 0")
        document = _validate_step_directory(workspace, staged, expected_step=0)
        if document["identity_sha256"] != root["step_identity_sha256"]:
            _fail("identity_conflict", "staged Step 0 identity conflicts")
        target.parent.mkdir(exist_ok=True)
        staged.rename(target)
    graph = rebuild_index(workspace, validate=False)
    document = _read_json(target / "step.json", "$.recovery.step")
    measurement = workspace / document["measurement_path"]
    _commit_protocol_paths(
        workspace,
        ["steps/000000", "step_index.json", *_step_zero_voxblame_paths(workspace, measurement)],
        "step 0: recover initial Measured Step",
        {
            "Workspace-Step": "0",
            "Workspace-Attempt": str(root["attempt"]),
            "Candidate-SHA256": document["candidate_mesh_sha256"],
            "Observable-SHA256": document["observable_sha256"],
            "Preview-SHA256": document["preview_identity_sha256"],
            "Step-SHA256": document["identity_sha256"],
            "Workspace-SHA256": _workspace_identity(workspace),
        },
        allow_noop=True,
        allow_existing_protocol_staging=True,
    )
    (transaction / "transaction.json").unlink()
    if any(transaction.iterdir()):
        _fail("unknown_staged_state", "Step 0 transaction contains unknown files")
    transaction.rmdir()
    active = workspace / "work/attempts" / f"{root['attempt']:06d}"
    if active.exists():
        shutil.rmtree(active)
    if not graph["steps"] or graph["steps"][0]["step"] != 0:
        _fail("incomplete_transaction", "recovered Step 0 is absent from graph")
    return 0


def _validate_region_diff_boundary(
    value: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    plan_digest: str,
    from_step: int,
    to_step: int,
    before_observable: str,
    after_observable: str,
) -> None:
    required = {
        "schema",
        "coordinate_contract",
        "max_depth",
        "from_step",
        "to_step",
        "repair_batch",
        "measurement_trajectory",
        "identity",
    }
    if not required.issubset(value):
        _fail("unsupported_or_invalid_voxblame_state", "Region Diff is incomplete")
    if (
        value["schema"] != "voxblame.region-diff/1"
        or value["coordinate_contract"] != COORDINATE_CONTRACT
        or value["max_depth"] != MAX_DEPTH
        or value["from_step"] != from_step
        or value["to_step"] != to_step
    ):
        _fail("parent_mismatch", "Region Diff edge conflicts with Repair Cycle")
    batch = value["repair_batch"]
    if not isinstance(batch, Mapping) or (
        batch.get("schema") != "voxblame.repair-batch/1"
        or batch.get("from_step") != from_step
        or batch.get("plan_sha256") != plan_digest
    ):
        _fail("identity_conflict", "Region Diff Repair Batch identity conflicts")
    expected_plan_digest = hashlib.sha256(
        b"voxblame.repair-batch/1\0" + _json_bytes(plan)
    ).hexdigest()
    if plan_digest != expected_plan_digest:
        _fail("identity_conflict", "frozen Repair Batch digest conflicts")
    trajectory = value["measurement_trajectory"]
    observable = trajectory.get("observable_geometry") if isinstance(trajectory, Mapping) else None
    if (
        not isinstance(trajectory, Mapping)
        or trajectory.get("steps") != [from_step, to_step]
        or not isinstance(observable, Mapping)
        or observable.get("before_sha256") != before_observable
        or observable.get("after_sha256") != after_observable
        or observable.get("changed") != (before_observable != after_observable)
    ):
        _fail("identity_conflict", "Region Diff Observable Geometry trajectory conflicts")
    identity = value["identity"]
    if not isinstance(identity, Mapping) or set(identity) != {"region_diff_sha256"}:
        _fail("unsupported_or_invalid_voxblame_state", "Region Diff identity is invalid")
    expected_document = dict(value)
    expected_document.pop("identity")
    expected_identity = hashlib.sha256(
        b"voxblame.region-diff/1\0" + _json_bytes(expected_document)
    ).hexdigest()
    if identity["region_diff_sha256"] != expected_identity:
        _fail("corrupt_workspace", "Region Diff identity digest mismatch")


def _validate_assessment(
    value: Mapping[str, Any], *, from_step: int, to_step: int
) -> None:
    root = _closed_object(
        value,
        {"schema", "from_step", "to_step", "preview_observation", "summary"},
        "$.assessment",
    )
    if (
        root["schema"] != "mesh-to-cad.assessment/1"
        or root["from_step"] != from_step
        or root["to_step"] != to_step
    ):
        _fail("parent_mismatch", "Agent assessment edge conflicts")
    _nonempty_string(root["preview_observation"], "$.assessment.preview_observation")
    _nonempty_string(root["summary"], "$.assessment.summary")


def _validate_source_changes(
    value: Mapping[str, Any], *, from_step: int, to_step: int
) -> None:
    root = _closed_object(
        value,
        {"schema", "from_step", "to_step", "files"},
        "$.source_changes",
    )
    if (
        root["schema"] != "mesh-to-cad.source-changes/1"
        or root["from_step"] != from_step
        or root["to_step"] != to_step
    ):
        _fail("parent_mismatch", "source-change evidence edge conflicts")
    if not isinstance(root["files"], list) or not root["files"]:
        _fail("invalid_contract", "source-change evidence must list files")
    seen: set[str] = set()
    for index, item in enumerate(root["files"]):
        entry = _closed_object(
            item,
            {"path", "before_sha256", "after_sha256"},
            f"$.source_changes.files[{index}]",
        )
        pure = PurePosixPath(entry["path"])
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            _fail("invalid_workspace_path", "source-change path is invalid")
        if entry["path"] in seen:
            _fail("invalid_contract", "duplicate source-change path")
        seen.add(entry["path"])
        before = entry["before_sha256"]
        after = entry["after_sha256"]
        if before is not None:
            _sha256(before, "$.source_changes.before_sha256")
        if after is not None:
            _sha256(after, "$.source_changes.after_sha256")
        if (before is None and after is None) or before == after:
            _fail("invalid_contract", "source-change digests must describe a change")


def _validate_cycle_directory(
    workspace: Path,
    root: Path,
    *,
    expected_cycle: int,
    step: Mapping[str, Any],
) -> dict[str, Any]:
    value = _read_json(root / "cycle.json", f"$.cycles[{expected_cycle}]")
    document = _closed_object(value, _CYCLE_FIELDS, f"$.cycles[{expected_cycle}]")
    attempt_ids = document["attempt_ids"]
    if (
        not isinstance(attempt_ids, list)
        or not attempt_ids
        or len(attempt_ids) > MAX_ATTEMPTS_PER_STEP
        or len(set(attempt_ids)) != len(attempt_ids)
        or any(
            not isinstance(attempt_id, int)
            or isinstance(attempt_id, bool)
            or attempt_id <= 0
            for attempt_id in attempt_ids
        )
    ):
        _fail("budget_violation", "Repair Cycle Attempt identities are invalid")
    if (
        document["schema"] != CYCLE_SCHEMA
        or document["cycle"] != expected_cycle
        or document["to_step"] != expected_cycle
        or step["step"] != expected_cycle
        or step["cycle"] != expected_cycle
        or step["parent_step"] != document["from_step"]
        or step["attempt_ids"] != document["attempt_ids"]
        or step["observable_sha256"] != document["to_observable_sha256"]
        or step["no_observable_geometry_change"]
        != document["no_observable_geometry_change"]
    ):
        _fail("parent_mismatch", "Repair Cycle and Measured Step conflict")
    parent = _read_json(
        workspace / "steps" / f"{document['from_step']:06d}" / "step.json",
        "$.cycle.parent",
    )
    if parent["observable_sha256"] != document["from_observable_sha256"]:
        _fail("identity_conflict", "Repair Cycle parent Observable Geometry conflicts")
    successful_attempt = _read_json(root / "attempt.json", "$.cycle.attempt")
    successful_attempt = _closed_object(
        successful_attempt, _ACTIVE_ATTEMPT_FIELDS, "$.cycle.attempt"
    )
    if (
        successful_attempt["result"] != "repair_cycle_published"
        or successful_attempt["attempt"] != document["attempt_ids"][-1]
        or successful_attempt["intended_cycle"] != document["cycle"]
        or successful_attempt["intended_step"] != document["to_step"]
        or successful_attempt["from_step"] != document["from_step"]
        or successful_attempt["plan_digest"] != document["plan_digest"]
    ):
        _fail("parent_mismatch", "successful Attempt ancestry conflicts with Cycle")
    for attempt_id in document["attempt_ids"][:-1]:
        attempt_document = _read_json(
            workspace / "attempts" / f"{attempt_id:06d}" / "attempt.json",
            "$.cycle.failed_attempt",
        )
        if (
            attempt_document.get("attempt") != attempt_id
            or attempt_document.get("intended_step") != document["to_step"]
            or attempt_document.get("from_step") != document["from_step"]
            or attempt_document.get("plan_digest") != document["plan_digest"]
            or attempt_document.get("result") not in FAILED_ATTEMPT_RESULTS
        ):
            _fail("parent_mismatch", "failed Attempt ancestry conflicts with Cycle")
    identity_document = dict(document)
    identity = identity_document.pop("identity_sha256")
    if identity != _identity(CYCLE_SCHEMA, identity_document):
        _fail("corrupt_workspace", "Repair Cycle identity digest mismatch")
    files = document["files"]
    expected_paths = {item["path"] for item in files if isinstance(item, Mapping)}
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "cycle.json"
    }
    if expected_paths != actual_paths:
        _fail("corrupt_workspace", "Repair Cycle file inventory mismatch")
    for item in files:
        path = _relative_member(root, item["path"], "$.cycle.files")
        if path.stat().st_size != item["size_bytes"] or _file_sha256(path) != item[
            "sha256"
        ]:
            _fail("corrupt_workspace", "Repair Cycle artifact digest mismatch")
    plan = _read_json(root / "plan.json", "$.cycle.plan")
    diff = _read_json(root / "diff.json", "$.cycle.diff")
    assessment = _read_json(root / "assessment.json", "$.cycle.assessment")
    source_changes = _read_json(
        root / "source_changes.json", "$.cycle.source_changes"
    )
    _validate_region_diff_boundary(
        diff,
        plan=plan,
        plan_digest=document["plan_digest"],
        from_step=document["from_step"],
        to_step=document["to_step"],
        before_observable=document["from_observable_sha256"],
        after_observable=document["to_observable_sha256"],
    )
    _validate_assessment(
        assessment, from_step=document["from_step"], to_step=document["to_step"]
    )
    _validate_source_changes(
        source_changes,
        from_step=document["from_step"],
        to_step=document["to_step"],
    )
    if _file_sha256(root / "assessment.json") != document["assessment_sha256"]:
        _fail("corrupt_workspace", "assessment digest mismatch")
    if (
        _file_sha256(root / "source_changes.json")
        != document["source_changes_sha256"]
    ):
        _fail("corrupt_workspace", "source-change digest mismatch")
    if (
        diff["identity"]["region_diff_sha256"]
        != document["region_diff_sha256"]
    ):
        _fail("identity_conflict", "Repair Cycle Region Diff identity conflicts")
    return dict(document)


def _cycle_attempt_ids(
    workspace: Path,
    intended_step: int,
    from_step: int,
    plan_digest: str,
    successful_attempt: int,
) -> list[int]:
    ids: list[int] = []
    attempts_root = workspace / "attempts"
    if attempts_root.exists():
        for path in sorted(attempts_root.glob("*/attempt.json")):
            value = _read_json(path, "$.attempt")
            if (
                value.get("intended_step") == intended_step
                and value.get("from_step") == from_step
                and value.get("plan_digest") == plan_digest
            ):
                ids.append(value["attempt"])
    ids.append(successful_attempt)
    if len(ids) > MAX_ATTEMPTS_PER_STEP:
        _fail("budget_violation", "Repair Cycle exceeds three attempts")
    return ids


def _published_numbers(root: Path) -> list[int]:
    if not root.exists():
        return []
    return sorted(
        int(path.name)
        for path in root.iterdir()
        if path.is_dir() and path.name.isdigit()
    )


def _path_digest(path: Path) -> str:
    if path.is_file():
        return _file_sha256(path)
    document = [
        {
            "path": child.relative_to(path).as_posix(),
            "sha256": _file_sha256(child),
        }
        for child in sorted(path.rglob("*"))
        if child.is_file()
    ]
    return hashlib.sha256(_json_bytes({"files": document})).hexdigest()


def _workspace_identity(workspace: Path) -> str:
    return _identity(WORKSPACE_SCHEMA, _load_workspace_document(workspace))


def _heads_from_steps(steps: list[dict[str, Any]]) -> list[int]:
    parents = {
        item["parent_step"] for item in steps if item["parent_step"] is not None
    }
    return [item["step"] for item in steps if item["step"] not in parents]


def _validate_tree_source(root: Path, path_label: str) -> None:
    if not root.is_dir() or root.is_symlink():
        _fail("invalid_workspace_path", "artifact source must be a real directory", path_label)
    for path in root.rglob("*"):
        if path.is_symlink():
            _fail("invalid_workspace_path", "symlinks are forbidden in formal bundles", path_label)
        if path.name in _FORBIDDEN_TREE_NAMES:
            _fail("invalid_workspace_path", f"forbidden bundle path: {path.name}", path_label)


def _relative_member(root: Path, value: str, path_label: str) -> Path:
    if not isinstance(value, str):
        _fail("invalid_workspace_path", "path must be a string", path_label)
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        _fail("invalid_workspace_path", "path must be normalized and relative", path_label)
    target = root.joinpath(*pure.parts)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        _fail("invalid_workspace_path", "path escapes its artifact root", path_label)
    if not target.is_file() or target.is_symlink():
        _fail("invalid_workspace_path", "declared artifact file is missing", path_label)
    return target


def _candidate_file(
    workspace: Path, candidate_root: Path, value: str, path_label: str
) -> Path:
    target = _relative_member(workspace, value, path_label)
    try:
        target.resolve().relative_to(candidate_root.resolve())
    except ValueError:
        _fail(
            "invalid_workspace_path",
            "canonical build inputs must belong to the active candidate",
            path_label,
        )
    return target


def _candidate_output(
    workspace: Path, candidate_root: Path, value: str, path_label: str
) -> Path:
    if not isinstance(value, str):
        _fail("invalid_workspace_path", "path must be a string", path_label)
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(
        part in {"", ".", ".."} for part in pure.parts
    ):
        _fail("invalid_workspace_path", "path must be normalized and relative", path_label)
    target = workspace.joinpath(*pure.parts)
    try:
        target.resolve().relative_to(candidate_root.resolve())
    except ValueError:
        _fail(
            "invalid_workspace_path",
            "canonical build output must belong to the active candidate",
            path_label,
        )
    if target.exists():
        _fail(
            "invalid_workspace_path",
            "canonical build output must be new",
            path_label,
        )
    return target


def _workspace_relative(workspace: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(workspace.resolve())
    except ValueError:
        _fail("invalid_workspace_path", "formal artifact path is outside the Workspace")
    if any(part in {"work", "run"} for part in relative.parts):
        _fail("invalid_workspace_path", "formal authority cannot reference mutable telemetry or work")
    return relative.as_posix()


def _closed_object(value: Any, fields: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("invalid_contract", "must be an object", path)
    if set(value) != fields:
        missing = sorted(fields - set(value))
        unknown = sorted(set(value) - fields)
        _fail("invalid_contract", f"closed object mismatch; missing={missing}, unknown={unknown}", path)
    return value


def _const(actual: Any, expected: Any, path: str) -> None:
    if actual != expected:
        _fail("invalid_contract", f"must equal {expected!r}", path)


def _nonempty_string(value: Any, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail("invalid_contract", "must be a non-empty string", path)


def _sha256(value: Any, path: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        _fail("invalid_contract", "must be a lowercase SHA-256 digest", path)


def _stable_key(value: Any, path: str) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9._:-]*", value) is None
    ):
        _fail("invalid_contract", "must be a stable lowercase key", path)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("corrupt_workspace", f"cannot read JSON artifact: {path}", label)
    if not isinstance(value, dict):
        _fail("invalid_contract", "JSON artifact must contain an object", label)
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        _fail("corrupt_workspace", f"cannot read artifact: {path}")


def _identity(schema: str, value: Mapping[str, Any]) -> str:
    return hashlib.sha256(schema.encode("utf-8") + b"\0" + _json_bytes(value)).hexdigest()


def _fail(classification: str, detail: str, path: str = "$") -> None:
    raise WorkspaceError(classification, detail, path)


__all__ = [
    "WorkspaceError",
    "begin_attempt",
    "finalize_workspace",
    "initialize_workspace",
    "publish_step_zero",
    "publish_cycle",
    "record_attempt",
    "recover_workspace",
    "rebuild_index",
    "run_attempt_command",
    "run_canonical_build",
    "validate_workspace",
    "workspace_status",
]
