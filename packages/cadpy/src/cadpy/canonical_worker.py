from __future__ import annotations

import os
from pathlib import Path
import platform
import resource
import signal
import stat
import subprocess
import sysconfig
import tempfile


WORKER_PROFILE = {
    "id": "cad.canonical-build-worker/1",
    "timeout_seconds": 120,
    "output_bytes": 64 * 1024,
    "cpu_seconds": 90,
    "file_bytes": 256 * 1024 * 1024,
    "open_files": 256,
    "processes": 32,
}
TRUSTED_BWRAP_PATH = Path("/usr/bin/bwrap")


def worker_resource_limits() -> None:
    limits = (
        (resource.RLIMIT_CPU, WORKER_PROFILE["cpu_seconds"]),
        (resource.RLIMIT_FSIZE, WORKER_PROFILE["file_bytes"]),
        (resource.RLIMIT_NOFILE, WORKER_PROFILE["open_files"]),
    )
    if hasattr(resource, "RLIMIT_NPROC"):
        limits = (*limits, (resource.RLIMIT_NPROC, WORKER_PROFILE["processes"]))
    for resource_id, requested in limits:
        hard_limit = resource.getrlimit(resource_id)[1]
        effective = (
            requested
            if hard_limit == resource.RLIM_INFINITY
            else min(requested, hard_limit)
        )
        resource.setrlimit(resource_id, (effective, effective))


def trusted_worker_import_paths() -> list[str]:
    cadpy_source_root = Path(__file__).resolve().parent.parent
    configured_paths = sysconfig.get_paths()
    trusted_paths = {
        cadpy_source_root,
        *(
            Path(configured_paths[name]).resolve()
            for name in ("purelib", "platlib")
        ),
    }
    return [os.fspath(path) for path in sorted(trusted_paths)]


def worker_sandbox_argv(
    *,
    worker_command: list[str],
    snapshot_root: Path,
    worker_output: Path,
) -> list[str]:
    if platform.system() != "Linux":
        return worker_command
    try:
        info = TRUSTED_BWRAP_PATH.lstat()
    except OSError as exc:
        raise RuntimeError("canonical source sandbox runtime unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RuntimeError("canonical source sandbox runtime invalid")
    return [
        os.fspath(TRUSTED_BWRAP_PATH),
        "--die-with-parent",
        "--new-session",
        "--unshare-net",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--cap-drop",
        "ALL",
        "--ro-bind",
        "/",
        "/",
        "--tmpfs",
        "/tmp",
        "--dir",
        os.fspath(snapshot_root.parent.parent),
        "--dir",
        os.fspath(snapshot_root.parent),
        "--dir",
        os.fspath(snapshot_root),
        "--ro-bind",
        os.fspath(snapshot_root),
        os.fspath(snapshot_root),
        "--bind",
        os.fspath(worker_output),
        os.fspath(worker_output),
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--chdir",
        os.fspath(snapshot_root),
        "--",
        *worker_command,
    ]


def _kill_worker_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait()


def run_worker_bounded(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
) -> str:
    with (
        tempfile.TemporaryFile() as worker_stdout,
        tempfile.TemporaryFile() as worker_stderr,
    ):
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=worker_stdout,
            stderr=worker_stderr,
            start_new_session=True,
        )
        try:
            process.wait(timeout=WORKER_PROFILE["timeout_seconds"])
        except subprocess.TimeoutExpired:
            _kill_worker_group(process)
            return "timeout"
        _kill_worker_group(process)
        output_limit = WORKER_PROFILE["output_bytes"]
        for stream in (worker_stdout, worker_stderr):
            if stream.tell() > output_limit:
                return "output-limit"
        if process.returncode != 0:
            return "rejected"
        if any(stream.tell() for stream in (worker_stdout, worker_stderr)):
            return "output"
        return "ok"


__all__ = [
    "TRUSTED_BWRAP_PATH",
    "WORKER_PROFILE",
    "run_worker_bounded",
    "trusted_worker_import_paths",
    "worker_resource_limits",
    "worker_sandbox_argv",
]
