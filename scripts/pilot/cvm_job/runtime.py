from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from scripts.pilot import plugin_deployment
from scripts.pilot import runner as pilot_runner

from . import tap_observer
from .model import resolve_model, selector_for_model
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
    requested_model,
    requested_plugin_mode,
    requested_reconstruction_spec,
    requested_view_image,
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
    r"(?i)(token|secret|password|api(?:[\s_-]+)?key)\s*[=:]\s*\S+|[A-Za-z0-9_=-]{32,}"
)
_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.-])/(?:[^\s/]+(?:/[^\s/]*)*)")
_PILOT_GROUP = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$")
_DIAGNOSTIC_PREFIXES = ("pilot-runner:", "warning:")
_DIAGNOSTIC_MAX_BYTES = 256 * 1024
_DIAGNOSTIC_MAX_LINES = 12
_PUBLIC_LINE_MAX_BYTES = 160
_TRACEBACK_HEADER = "Traceback (most recent call last):"
_TRACEBACK_FILE = re.compile(
    r'^\s*File\s+"[^"]+",\s+line\s+([0-9]+),\s+in\s+([A-Za-z0-9_<>]+)\s*$'
)
_EXCEPTION_TYPE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception))(?:\s*:.*)?$"
)


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


def _sanitize_public_line(line: str) -> str:
    redacted = _SECRET_HEADLINE.sub("<redacted>", line)
    redacted = _ABSOLUTE_PATH.sub("<path>", redacted)
    return redacted.encode("utf-8")[:_PUBLIC_LINE_MAX_BYTES].decode(
        "utf-8", "ignore"
    )


def _normalize_traceback_line(line: str) -> str | None:
    if line == _TRACEBACK_HEADER:
        return line
    frame = _TRACEBACK_FILE.fullmatch(line)
    if frame:
        return f"File <path>, line {frame.group(1)}, in {frame.group(2)}"
    exception = _EXCEPTION_TYPE.fullmatch(line)
    if exception:
        return exception.group(1)
    return None


def _diagnostics_from_descriptor(
    descriptor: int, *, include_nonempty_lines: bool = False
) -> tuple[str, int, list[str]]:
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return "unavailable", 0, []
        offset = max(0, metadata.st_size - _DIAGNOSTIC_MAX_BYTES)
        os.lseek(descriptor, offset, os.SEEK_SET)
        payload = os.read(descriptor, _DIAGNOSTIC_MAX_BYTES)
    finally:
        os.close(descriptor)
    text = payload.decode("utf-8", "replace")
    if offset:
        _, _, text = text.partition("\n")
    if include_nonempty_lines:
        selected = []
        for line in text.splitlines():
            if not line:
                continue
            normalized = _normalize_traceback_line(line)
            selected.append(_sanitize_public_line(normalized or line))
    else:
        selected = []
        for line in text.splitlines():
            if line.startswith(_DIAGNOSTIC_PREFIXES):
                selected.append(_sanitize_public_line(line))
                continue
            normalized = _normalize_traceback_line(line)
            if normalized is not None:
                selected.append(_sanitize_public_line(normalized))
    diagnostics = selected[-_DIAGNOSTIC_MAX_LINES:]
    if diagnostics:
        status = "ready"
    elif metadata.st_size:
        status = "filtered"
    else:
        status = "empty"
    return status, metadata.st_size, diagnostics


def _runner_diagnostics(handle: str) -> tuple[str, int, list[str]]:
    parsed = parse_handle(handle)
    outputs = REPO_ROOT / "outputs"
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    directories: list[int] = []
    descriptor: int | None = None
    try:
        directories.append(os.open(outputs, directory_flags))
        for component in (parsed["group"], parsed["exp"], "run"):
            directories.append(
                os.open(component, directory_flags, dir_fd=directories[-1])
            )
        descriptor = os.open("stderr.log", file_flags, dir_fd=directories[-1])
    except FileNotFoundError:
        return "missing", 0, []
    except OSError:
        return "unavailable", 0, []
    finally:
        for directory in reversed(directories):
            os.close(directory)
    assert descriptor is not None
    return _diagnostics_from_descriptor(descriptor)


def _supervisor_diagnostics(root: Path, handle: str) -> tuple[str, int, list[str]]:
    try:
        descriptor = os.open(
            log_path(root, handle), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
    except FileNotFoundError:
        return "missing", 0, []
    except OSError:
        return "unavailable", 0, []
    return _diagnostics_from_descriptor(descriptor, include_nonempty_lines=True)


def diagnose_job(
    handle: str,
    *,
    state_root: Path | None = None,
) -> dict[str, Any]:
    """Return bounded, redacted runner diagnostics for one failed pilot."""

    state = load_state(_root(state_root), handle)
    if state["state"] != "failed":
        raise ProtocolError("diagnose requires a failed pilot")
    result = public_state(state, DEFAULT_STALE_AFTER)
    status, byte_count, diagnostics = _runner_diagnostics(handle)
    source = "runner_stderr"
    if status == "missing":
        status, byte_count, diagnostics = _supervisor_diagnostics(_root(state_root), handle)
        source = "supervisor_log"
    result["diagnostic_status"] = status
    result["diagnostic_source"] = source
    result["diagnostic_bytes"] = byte_count
    result["diagnostics"] = diagnostics
    return result


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
    *,
    model: str | None = None,
    plugin_mode: str = "direct",
    view_image: bool = True,
    reconstruction_spec: bool = True,
    include_model: bool = True,
    token_slot_from_environment: bool = True,
) -> dict[str, Any]:
    if not isinstance(reconstruction_spec, bool):
        raise ProtocolError("reconstruction_spec must be a boolean")
    if not isinstance(view_image, bool):
        raise ProtocolError("view_image must be a boolean")
    raw_token_slot = (
        os.environ.get("VENUS_TOKEN_SLOT") if token_slot_from_environment else None
    )
    token_slot: int | None = None
    if raw_token_slot is not None:
        if not raw_token_slot.isdigit() or int(raw_token_slot) > 49:
            raise ProtocolError("VENUS_TOKEN_SLOT must be an integer in [0, 49]")
        token_slot = int(raw_token_slot)
    now = utc_now()
    handle = f"{group}/{exp}"
    resolved_model: str | None = None
    if include_model:
        selected_model = model or os.environ.get("MODEL")
        upstream_target = (
            os.environ.get("PILOT_UPSTREAM_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("SCENEGEN_BASE_URL")
            or ""
        ).rstrip("/")
        if selected_model is None and upstream_target == "https://api5.xhub.chat/v1":
            selected_model = "sol"
        try:
            _, resolved_model = resolve_model(selected_model)
        except ValueError as error:
            raise ProtocolError(str(error)) from error
    record = {
        "schema_version": 1,
        "kind": "pilot",
        "job": handle,
        "group": group,
        "exp": exp,
        "object": object_name,
        "plugin_mode": plugin_mode,
        "token_slot": token_slot,
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
    if resolved_model is not None:
        record["model"] = resolved_model
    if include_model:
        # New provider-backed pilot records state the effective default
        # explicitly.  Provider-free records intentionally retain their
        # historical shape and do not participate in this option.
        record["view_image"] = view_image
        record["reconstruction_spec"] = reconstruction_spec
    return record


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
    model: str | None = None,
    plugin_mode: str = "direct",
    view_image: bool = True,
    reconstruction_spec: bool = True,
    state_root: Path | None = None,
    detach: Callable[[str, Sequence[str], Path], int] = _detach,
) -> dict[str, Any]:
    root = _root(state_root)
    object_name = validate_component(object_name, "object")
    group = _validate_pilot_group(group)
    with _allocation_lock(root, group):
        exp = _allocate_exp(object_name, group, root)
        record = _pilot_record(
            object_name,
            group,
            exp,
            root,
            model=model,
            plugin_mode=plugin_mode,
            view_image=view_image,
            reconstruction_spec=reconstruction_spec,
        )
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
    return {
        "job": state["job"],
        "state": state["state"],
        "kind": "pilot",
        "model": requested_model(state),
        "view_image": requested_view_image(state),
        "reconstruction_spec": requested_reconstruction_spec(state),
    }


def _provider_free_module(scenario: str):
    from scripts.pilot import provider_free_installed_plugin as installed

    if scenario == installed.SCENARIO:
        return installed
    if scenario == "agent-surface-mcp-injection":
        from scripts.pilot import provider_free_agent_surface_mcp_injection as injection

        return injection
    if scenario == "agent-surface-mcp-direct-injection":
        from scripts.pilot import provider_free_agent_surface_mcp_direct_injection as direct

        return direct
    if scenario == "agent-surface-mcp-ephemeral-differential":
        from scripts.pilot import provider_free_agent_surface_mcp_ephemeral_differential as differential

        return differential
    if scenario in {"workspace-repair-chain", "workspace-repair-chain-exhaustion"}:
        from scripts.pilot import provider_free_workspace_repair_chain as repair_chain

        return repair_chain
    raise ProtocolError(f"unsupported provider-free scenario: {scenario!r}")


def submit_provider_free_installed_plugin(
    scenario: str,
    group: str,
    *,
    state_root: Path | None = None,
    host_home: Path | None = None,
    detach: Callable[[str, Sequence[str], Path], int] = _detach,
) -> dict[str, Any]:
    """Bind the current plugin authority and launch one offline discovery job."""

    provider_free = _provider_free_module(scenario)
    root = _root(state_root)
    group = _validate_pilot_group(group)
    try:
        receipt = plugin_deployment.resolve_current_authority(
            host_home or Path.home()
        )
    except plugin_deployment.PluginAuthorityError as exc:
        raise ProtocolError(f"cannot bind plugin authority: {exc}") from exc
    with _allocation_lock(root, group):
        exp = _allocate_exp(scenario, group, root)
        record = _pilot_record(
            scenario,
            group,
            exp,
            root,
            include_model=False,
            token_slot_from_environment=False,
        )
        record.update(
            {
                "provider_free": True,
                "scenario": scenario,
                "token_slot": None,
                "plugin_authority": provider_free.authority_identity(receipt),
            }
        )
        publish_state(root, record)
    command = [
        sys.executable,
        "-m",
        "scripts.pilot.cvm_job",
        "supervise-provider-free",
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
    output_path: Path | None = None,
) -> tuple[int, int]:
    output = output_path.open("ab", buffering=0) if output_path is not None else None
    process = subprocess.Popen(
        list(command),
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
        stdout=output,
        stderr=subprocess.STDOUT if output is not None else None,
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
    finally:
        if output is not None:
            output.close()


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


def _ensure_failure_manifest(exp_dir: Path, process_status: int | None) -> None:
    """Preserve a minimal inventory when the pilot exits before finalization."""

    manifest_path = exp_dir / "artifact_manifest.json"
    if manifest_path.is_file():
        return
    exp_dir.mkdir(parents=True, exist_ok=True)
    pilot_runner.write_artifact_manifest(
        exp_dir,
        process_status if isinstance(process_status, int) else 1,
        process_status if isinstance(process_status, int) and process_status != 0 else 1,
    )


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
        requested_plugin_mode(record),
    ]
    # The shell pilot is default-on, so every provider-backed record needs an
    # explicit mode.  Historical records lack either field and the requested_*
    # helpers intentionally resolve them to false.
    if command is None:
        pilot_command.append(
            "--view-image"
            if requested_view_image(record)
            else "--no-view-image"
        )
        pilot_command.append(
            "--reconstruction-spec"
            if requested_reconstruction_spec(record)
            else "--no-reconstruction-spec"
        )
    pilot_environment = os.environ.copy()
    if command is None:
        pilot_environment["MODEL"] = selector_for_model(requested_model(record))
    process_status: int | None = None
    try:
        exp_dir = REPO_ROOT / record["exp_dir"]
        (exp_dir / "run").mkdir(parents=True, exist_ok=True)
        process_status, pilot_pid = _run_with_heartbeat(
            root,
            handle,
            pilot_command,
            interval=interval,
            env=pilot_environment,
            output_path=exp_dir / "run" / "runner.log",
        )
        manifest_path = exp_dir / "artifact_manifest.json"
        _ensure_failure_manifest(exp_dir, process_status)
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


def supervise_provider_free_installed_plugin(
    handle: str,
    *,
    state_root: Path | None = None,
    host_home: Path | None = None,
    interval: float = DEFAULT_HEARTBEAT_INTERVAL,
    command: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Supervise one provider-free runner and validate its bound evidence."""

    root = _root(state_root)
    parse_handle(handle)
    with _supervisor_lock(root, handle):
        record = load_state(root, handle)
        provider_free = _provider_free_module(record.get("scenario"))
        if record["state"] != "submitted":
            raise ProtocolError(f"pilot cannot start from {record['state']}")
        process_status: int | None = None
        try:
            provider_free.assert_current_authority(record, host_home or Path.home())
            transition(root, handle, "running", supervisor_pid=os.getpid())
            module_name = provider_free.__name__.rsplit(".", 1)[-1]
            runner_executable = sys.executable
            if command is None and record.get("scenario") in {"workspace-repair-chain", "workspace-repair-chain-exhaustion"}:
                runner_executable = os.fspath(REPO_ROOT / ".venv/bin/python")
                if not Path(runner_executable).is_file():
                    raise ProtocolError("workspace-repair-chain runner runtime unavailable")
            runner_command = list(command) if command is not None else [
                runner_executable,
                "-m",
                f"scripts.pilot.{module_name}",
                "--job",
                handle,
                "--state-root",
                os.fspath(root),
            ]
            process_status, _ = _run_with_heartbeat(
                root,
                handle,
                runner_command,
                interval=interval,
                env=provider_free.build_runner_env(os.environ),
            )
            if process_status != 0:
                return transition(
                    root,
                    handle,
                    "failed",
                    process_exit_code=process_status,
                    failure_reason=f"provider-free runner exited {process_status}",
                )
            if record.get("scenario") in {"workspace-repair-chain", "workspace-repair-chain-exhaustion"}:
                evidence_path, manifest_path = provider_free.validate_artifacts(
                    REPO_ROOT,
                    record,
                    authoring_python=provider_free.authoring_python_from_evidence(
                        REPO_ROOT, record
                    ),
                    environ=os.environ,
                )
            else:
                evidence_path, manifest_path = provider_free.validate_artifacts(
                    REPO_ROOT, record
                )
            updates = {
                "runner_final_status": 0,
                "artifact_manifest": _relative(manifest_path),
                "provider_free_evidence": _relative(evidence_path),
                "process_exit_code": process_status,
            }
            return transition(root, handle, "succeeded", **updates)
        except Exception as error:
            current = load_state(root, handle)
            if current["state"] in {"submitted", "running"}:
                transition(
                    root,
                    handle,
                    "failed",
                    process_exit_code=process_status,
                    failure_reason=f"provider-free supervisor error: {error}",
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
            result["last_checkpoint"] = _sanitize_public_line(headline)
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
