from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from . import tap_observer
from .protocol import (
    ProtocolError,
    TERMINAL_STATES,
    default_state_root,
    heartbeat,
    load_state,
    log_path,
    parse_handle,
    public_state,
    publish_state,
    state_path,
    transition,
    utc_now,
    validate_component,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
PILOT_SCRIPT = REPO_ROOT / "scripts" / "pilot" / "toys4k-pilot.sh"
DEFAULT_HEARTBEAT_INTERVAL = 5.0
DEFAULT_STALE_AFTER = 60.0
DEFAULT_WAIT_TIMEOUT = 12 * 60 * 60.0
PROCESS_TERMINATION_GRACE = 5.0
_SECRET_HEADLINE = re.compile(
    r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*\S+|[A-Za-z0-9_=-]{32,}"
)
_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.-])/(?:[^\s/]+/)+[^\s]*")
_PILOT_GROUP = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$")


def _root(state_root: Path | None) -> Path:
    return Path(state_root) if state_root is not None else default_state_root(REPO_ROOT)


def _relative(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return os.fspath(path)


def _validate_pilot_group(group: str) -> str:
    group = validate_component(group, "group")
    if not _PILOT_GROUP.fullmatch(group):
        raise ProtocolError(
            f"invalid pilot group: {group!r}; expected YYYYMMDD-HHMMSS-<slug>"
        )
    return group


def _allocate_exp(object_name: str, group: str, root: Path) -> str:
    validate_component(object_name, "object")
    _validate_pilot_group(group)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{object_name}"
    exp = base
    suffix = 1
    while state_path(root, f"{group}/{exp}").exists() or (
        REPO_ROOT / "outputs" / group / exp
    ).exists():
        suffix += 1
        exp = f"{base}-{suffix}"
    return exp


def _pilot_record(
    object_name: str,
    group: str,
    exp: str,
    root: Path,
) -> dict[str, Any]:
    now = utc_now()
    handle = f"{group}/{exp}"
    return {
        "schema_version": 1,
        "kind": "pilot",
        "job": handle,
        "group": group,
        "exp": exp,
        "object": object_name,
        "exp_dir": f"outputs/{handle}",
        "state": "submitted",
        "submitted_at": now,
        "started_at": None,
        "updated_at": now,
        "heartbeat_at": now,
        "finished_at": None,
        "supervisor_pid": None,
        "pilot_pid": None,
        "process_exit_code": None,
        "runner_final_status": None,
        "artifact_manifest": None,
        "log": _relative(log_path(root, handle)),
        "failure_reason": None,
    }


def _detach(handle: str, command: Sequence[str], root: Path) -> int:
    destination = log_path(root, handle)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("ab", buffering=0) as stream:
        process = subprocess.Popen(
            list(command),
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env={**os.environ, "CVM_JOB_STATE_ROOT": os.fspath(root)},
        )
    return process.pid


def _lock_path(root: Path, handle: str) -> Path:
    parsed = parse_handle(handle)
    return root / "locks" / parsed["group"] / f"{parsed['exp']}.lock"


def _allocation_lock_path(root: Path, group: str) -> Path:
    group = validate_component(group, "group")
    return root / "locks" / group / ".submit.lock"


@contextmanager
def _allocation_lock(root: Path, group: str) -> Iterator[None]:
    destination = _allocation_lock_path(root, group)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_RDWR | os.O_CREAT, 0o600)
    stream = os.fdopen(descriptor, "a+")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


@contextmanager
def _supervisor_lock(root: Path, handle: str) -> Iterator[None]:
    destination = _lock_path(root, handle)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_RDWR | os.O_CREAT, 0o600)
    stream = os.fdopen(descriptor, "a+")
    acquired = False
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as error:
            raise ProtocolError(f"supervisor already running: {handle}") from error
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def submit_pilot(
    object_name: str,
    group: str,
    *,
    state_root: Path | None = None,
    detach: Callable[[str, Sequence[str], Path], int] = _detach,
) -> dict[str, Any]:
    root = _root(state_root)
    object_name = validate_component(object_name, "object")
    group = _validate_pilot_group(group)
    with _allocation_lock(root, group):
        exp = _allocate_exp(object_name, group, root)
        record = _pilot_record(object_name, group, exp, root)
        publish_state(root, record)
    command = [
        sys.executable,
        "-m",
        "scripts.pilot.cvm_job",
        "supervise-pilot",
        "--job",
        record["job"],
    ]
    try:
        detach(record["job"], command, root)
    except Exception as error:
        transition(
            root,
            record["job"],
            "failed",
            failure_reason=f"supervisor launch failed: {type(error).__name__}",
        )
    state = load_state(root, record["job"])
    return {"job": state["job"], "state": state["state"], "kind": "pilot"}


def _run_with_heartbeat(
    root: Path,
    handle: str,
    command: Sequence[str],
    *,
    interval: float,
    env: dict[str, str] | None = None,
) -> tuple[int, int]:
    process = subprocess.Popen(
        list(command),
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
    try:
        heartbeat(root, handle, pilot_pid=process.pid, supervisor_pid=os.getpid())
        while True:
            status = process.poll()
            if status is not None:
                return status, process.pid
            heartbeat(root, handle, pilot_pid=process.pid, supervisor_pid=os.getpid())
            time.sleep(interval)
    except BaseException:
        _terminate_process_group(process)
        raise


def _terminate_process_group(
    process: subprocess.Popen[Any],
    *,
    grace: float = PROCESS_TERMINATION_GRACE,
) -> None:
    deadline = time.monotonic() + grace
    leader_running = process.poll() is None
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        if leader_running:
            process.wait()
        return
    if leader_running:
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait()


def _manifest_result(path: Path) -> tuple[int | None, str | None]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "artifact manifest missing"
    except (OSError, json.JSONDecodeError):
        return None, "artifact manifest invalid"
    final_status = manifest.get("final_status")
    if isinstance(final_status, bool) or not isinstance(final_status, int):
        return None, "artifact manifest final_status is not an integer"
    return final_status, None


def supervise_pilot(
    handle: str,
    *,
    state_root: Path | None = None,
    interval: float = DEFAULT_HEARTBEAT_INTERVAL,
    command: Sequence[str] | None = None,
) -> dict[str, Any]:
    root = _root(state_root)
    parsed = parse_handle(handle)
    if parsed["kind"] != "pilot":
        raise ProtocolError("supervise-pilot requires a pilot handle")
    with _supervisor_lock(root, handle):
        return _supervise_pilot_locked(
            handle,
            root=root,
            interval=interval,
            command=command,
        )


def _supervise_pilot_locked(
    handle: str,
    *,
    root: Path,
    interval: float,
    command: Sequence[str] | None,
) -> dict[str, Any]:
    record = load_state(root, handle)
    if record["state"] != "submitted":
        raise ProtocolError(f"pilot cannot start from {record['state']}")
    transition(
        root,
        handle,
        "running",
        supervisor_pid=os.getpid(),
    )
    pilot_command = list(command) if command is not None else [
        os.fspath(PILOT_SCRIPT),
        record["object"],
        record["group"],
        record["exp"],
    ]
    process_status: int | None = None
    try:
        process_status, pilot_pid = _run_with_heartbeat(
            root,
            handle,
            pilot_command,
            interval=interval,
            env=os.environ.copy(),
        )
        manifest_path = REPO_ROOT / record["exp_dir"] / "artifact_manifest.json"
        runner_status, manifest_error = _manifest_result(manifest_path)
        updates = {
            "runner_final_status": runner_status,
            "artifact_manifest": _relative(manifest_path) if manifest_path.is_file() else None,
            "process_exit_code": process_status,
        }
        if process_status == 0 and runner_status == 0 and manifest_error is None:
            return transition(root, handle, "succeeded", **updates)
        reason = manifest_error or (
            f"pilot exited {process_status}" if process_status else f"runner final_status={runner_status}"
        )
        return transition(
            root,
            handle,
            "failed",
            failure_reason=reason,
            **updates,
        )
    except Exception as error:
        current = load_state(root, handle)
        if current["state"] == "running":
            transition(
                root,
                handle,
                "failed",
                process_exit_code=process_status,
                failure_reason=f"pilot supervisor error: {type(error).__name__}",
            )
        return load_state(root, handle)


def _observe_pilot(state: dict[str, Any]) -> dict[str, Any]:
    exp_dir = REPO_ROOT / state["exp_dir"]
    try:
        result = tap_observer.observe_exp(exp_dir)
    except Exception:
        result = {"tap": {"availability": "degraded"}}
    try:
        completed = subprocess.run(
            ["git", "-C", os.fspath(exp_dir), "log", "-1", "--format=%s"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
        )
        headline = completed.stdout.strip()
        if completed.returncode == 0 and headline:
            headline = _SECRET_HEADLINE.sub("<redacted>", headline)
            headline = _ABSOLUTE_PATH.sub("<path>", headline)
            result["last_checkpoint"] = headline[:160]
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def status_job(
    handle: str,
    *,
    state_root: Path | None = None,
    stale_after: float = DEFAULT_STALE_AFTER,
    include_observation: bool = True,
) -> dict[str, Any]:
    root = _root(state_root)
    state = load_state(root, handle)
    result = public_state(state, stale_after)
    if include_observation:
        result["observation"] = _observe_pilot(state)
    return result


def wait_job(
    handle: str,
    *,
    state_root: Path | None = None,
    until: str = "terminal",
    timeout: float = DEFAULT_WAIT_TIMEOUT,
    poll_interval: float = 2.0,
    stale_after: float = DEFAULT_STALE_AFTER,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], int]:
    if until not in {"terminal", "terminal-or-stale"}:
        raise ProtocolError(f"invalid wait condition: {until}")
    if timeout < 0:
        raise ProtocolError("timeout must be non-negative")
    root = _root(state_root)
    started = clock()
    while True:
        current = status_job(
            handle,
            state_root=root,
            stale_after=stale_after,
            include_observation=False,
        )
        if current["state"] in TERMINAL_STATES:
            final = status_job(handle, state_root=root, stale_after=stale_after)
            return final, 0 if final["state"] == "succeeded" else 1
        if until == "terminal-or-stale" and current["health"] == "stale":
            return status_job(handle, state_root=root, stale_after=stale_after), 3
        if clock() - started >= timeout:
            result = status_job(handle, state_root=root, stale_after=stale_after)
            result["wait"] = "timeout"
            return result, 4
        sleeper(poll_interval)
