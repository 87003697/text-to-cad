#!/usr/bin/env python3
"""THROWAWAY bounded workload process-group supervision for SAR-003."""

from __future__ import annotations

from dataclasses import dataclass
import os
import signal
import subprocess
import time
from typing import BinaryIO, Mapping, Protocol


@dataclass(frozen=True)
class GroupResult:
    returncode: int
    descendant_residue: bool
    group_absent: bool


class GroupAdapter(Protocol):
    def spawn(
        self, argv: tuple[str, ...], cwd: str, env: Mapping[str, str],
        stdout: BinaryIO, stderr: BinaryIO,
    ) -> tuple[object, int]: ...
    def wait(self, process: object) -> int: ...
    def group_exists(self, pgid: int) -> bool: ...
    def signal_group(self, pgid: int, signum: int) -> None: ...
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class OsGroupAdapter:
    def spawn(
        self, argv: tuple[str, ...], cwd: str, env: Mapping[str, str],
        stdout: BinaryIO, stderr: BinaryIO,
    ) -> tuple[subprocess.Popen[bytes], int]:
        process = subprocess.Popen(
            argv, cwd=cwd, env=dict(env), stdout=stdout, stderr=stderr,
            start_new_session=True,
        )
        return process, process.pid

    def wait(self, process: object) -> int:
        assert isinstance(process, subprocess.Popen)
        return process.wait()

    def group_exists(self, pgid: int) -> bool:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def signal_group(self, pgid: int, signum: int) -> None:
        try:
            os.killpg(pgid, signum)
        except ProcessLookupError:
            pass

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _await_absence(adapter: GroupAdapter, pgid: int, timeout: float) -> bool:
    deadline = adapter.monotonic() + timeout
    while adapter.group_exists(pgid):
        if adapter.monotonic() >= deadline:
            return False
        adapter.sleep(0.02)
    return True


def run_workload_group(
    argv: tuple[str, ...], *, cwd: str, env: Mapping[str, str],
    stdout: BinaryIO, stderr: BinaryIO,
    adapter: GroupAdapter | None = None,
    terminate_timeout: float = 2.0,
) -> GroupResult:
    """Run one new session; residue forces TERM/KILL and a failed result."""
    runtime = adapter or OsGroupAdapter()
    process, pgid = runtime.spawn(argv, cwd, env, stdout, stderr)
    returncode = runtime.wait(process)
    residue = runtime.group_exists(pgid)
    if residue:
        runtime.signal_group(pgid, signal.SIGTERM)
        absent = _await_absence(runtime, pgid, terminate_timeout)
        if not absent:
            runtime.signal_group(pgid, signal.SIGKILL)
            absent = _await_absence(runtime, pgid, terminate_timeout)
    else:
        absent = True
    return GroupResult(
        returncode=125 if residue else returncode,
        descendant_residue=residue,
        group_absent=absent,
    )
