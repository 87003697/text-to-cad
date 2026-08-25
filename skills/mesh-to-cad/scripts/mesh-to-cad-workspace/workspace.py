"""Narrow Workspace facade and provider-free terminal evidence compiler.

``workspace_core`` remains the implementation of the existing Workspace
protocol.  This module is the caller boundary and adds only an in-memory,
closed terminal evidence bundle.  Persistence and crash recovery belong to the
outer runner that owns the returned identity handoff.
"""

from __future__ import annotations

import json
import hashlib
import ctypes
import errno
import os
import platform
from pathlib import Path, PurePosixPath
import shutil
import secrets
import stat
from typing import Any, Callable, Mapping

import workspace_core as _core


# Preserve the existing public function objects and their stable error behavior
# while moving callers to this module's import boundary.
DEFAULT_COMMAND_SECONDS = _core.DEFAULT_COMMAND_SECONDS
MAX_ATTEMPTS_PER_STEP = _core.MAX_ATTEMPTS_PER_STEP
MAX_REPAIR_CYCLES = _core.MAX_REPAIR_CYCLES
MAX_TOOL_FAILURES_PER_STEP = _core.MAX_TOOL_FAILURES_PER_STEP
FAILED_ATTEMPT_RESULTS = _core.FAILED_ATTEMPT_RESULTS
WorkspaceError = _core.WorkspaceError
ValidationResult = _core.ValidationResult
begin_attempt = _core.begin_attempt
finalize_workspace = _core.finalize_workspace
initialize_workspace = _core.initialize_workspace
publish_step_zero = _core.publish_step_zero
publish_cycle = _core.publish_cycle
record_attempt = _core.record_attempt
recover_workspace = _core.recover_workspace
rebuild_index = _core.rebuild_index
run_attempt_command = _core.run_attempt_command
run_canonical_build = _core.run_canonical_build
validate_workspace = _core.validate_workspace
workspace_status = _core.workspace_status
cancel_active_commands = _core.cancel_active_commands
ExecutionScope = _core.ExecutionScope


def workspace_initialized(workspace: Path) -> bool:
    """Return whether the Workspace authority document is readable."""

    workspace = Path(workspace).resolve()
    try:
        _read_workspace_document(workspace)
    except WorkspaceError as error:
        if error.classification in {"invalid_workspace", "incomplete_transaction"}:
            return False
        raise
    return True


def _candidate_staging_path(workspace: Path, attempt: int) -> Path:
    """Return the active Attempt's ignored candidate staging directory."""

    workspace = Path(workspace).resolve()
    root, _active, _plan = _core._load_active_attempt(workspace, attempt)
    target = root / "candidate"
    if target.is_symlink():
        _fail("invalid_workspace_path", "candidate staging is a symlink", "$.candidate")
    return target


def _ingest_candidate(
    workspace: Path,
    attempt: int,
    source: Path,
) -> None:
    """Own secure candidate ingestion from one external source capability."""

    target = _candidate_staging_path(workspace, attempt)
    if target.is_symlink():
        _fail("invalid_workspace_path", "candidate staging is a symlink", "$.candidate")
    if target.exists():
        if not target.is_dir():
            _fail("invalid_workspace_path", "candidate staging is not a directory", "$.candidate")
        shutil.rmtree(target)
    try:
        _copy_agent_tree(Path(source), target)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def publish_step_zero_from_agent(
    workspace: Path,
    *,
    attempt: int,
    source: Path,
    candidate_mesh: str,
    measurement: str,
    preview: str,
) -> dict[str, Any]:
    """Ingest one external candidate and publish Step 0 through Workspace authority."""

    _ingest_candidate(workspace, attempt, source)
    authority = _core._load_active_attempt(Path(workspace).resolve(), attempt)[0] / "candidate"
    measurement_source = _agent_source_file(Path(source).resolve(), measurement)
    measurement_target = (
        Path(workspace).resolve() / "voxblame/steps/000000/summary.json"
    )
    _copy_agent_file(measurement_source, measurement_target)
    try:
        result = publish_step_zero(
            workspace,
            attempt=attempt,
            candidate=authority,
            candidate_mesh=_agent_relative(authority, candidate_mesh),
            measurement=measurement_target,
            preview=authority / _agent_relative(authority, preview),
        )
    except Exception:
        measurement_target.unlink(missing_ok=True)
        raise
    step_value = result.get("step", 0)
    if isinstance(step_value, Mapping):
        step_value = step_value.get("step", 0)
    return {"step": int(step_value)}


def publish_cycle_from_agent(
    workspace: Path,
    *,
    attempt: int,
    source: Path,
    candidate_mesh: str,
    measurement: str,
    preview: str,
    region_diff: str,
    assessment: str,
    source_changes: str,
) -> dict[str, Any]:
    """Ingest one external candidate and publish a Repair Cycle through Workspace authority."""

    _ingest_candidate(workspace, attempt, source)
    workspace = Path(workspace).resolve()
    active_root, active, _plan = _core._load_active_attempt(workspace, attempt)
    authority = active_root / "candidate"
    intended_step = int(active["intended_step"])
    measurement_source = _agent_source_file(Path(source).resolve(), measurement)
    measurement_target = workspace / "voxblame/steps" / f"{intended_step:06d}" / "summary.json"
    _copy_agent_file(measurement_source, measurement_target)
    try:
        result = publish_cycle(
            workspace,
            attempt=attempt,
            candidate=authority,
            candidate_mesh=_agent_relative(authority, candidate_mesh),
            measurement=measurement_target,
            preview=authority / _agent_relative(authority, preview),
            region_diff=authority / _agent_relative(authority, region_diff),
            assessment=authority / _agent_relative(authority, assessment),
            source_changes=authority / _agent_relative(authority, source_changes),
        )
    except Exception:
        measurement_target.unlink(missing_ok=True)
        raise
    step_value = result.get("step", 0)
    if isinstance(step_value, Mapping):
        step_value = step_value.get("step", 0)
    cycle_value = result.get("cycle", step_value)
    if isinstance(cycle_value, Mapping):
        cycle_value = cycle_value.get("cycle", 0)
    return {"step": {"step": int(step_value)}, "cycle": int(cycle_value)}


def _finalization_staging_path(workspace: Path) -> Path:
    """Return the ignored Workspace staging area for Agent finalization."""

    workspace = Path(workspace).resolve()
    _read_workspace_document(workspace)
    work = workspace / "work"
    if work.is_symlink():
        _fail("invalid_workspace_path", "Workspace work area is a symlink", "$.work")
    try:
        work.mkdir(exist_ok=True)
    except OSError:
        _fail("invalid_workspace_path", "Workspace work area is unavailable", "$.work")
    return work / "agent-finalization"


def _reset_finalization_staging(workspace: Path) -> Path:
    """Own creation/reset of the ignored finalization staging area."""

    target = _finalization_staging_path(workspace)
    if target.is_symlink():
        _fail("invalid_workspace_path", "finalization staging is a symlink", "$.work")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=False)
    return target


def _discard_finalization_staging(workspace: Path) -> None:
    """Own cleanup of the ignored finalization staging area."""

    target = _finalization_staging_path(workspace)
    if target.is_symlink():
        _fail("invalid_workspace_path", "finalization staging is a symlink", "$.work")
    if target.exists():
        shutil.rmtree(target)


def finalize_agent_submission(
    workspace: Path,
    *,
    source: Path,
    selection: str,
    notes: str,
    rebuild_entrypoint: Path,
    geometry_entrypoint: Path,
    tool_registry: Path,
    scope: _core.ExecutionScope | None = None,
) -> dict[str, Any]:
    """Stage Agent selection/evidence and finalize without exposing internal paths."""

    raw_source = Path(source)
    if raw_source.is_symlink() or not raw_source.is_dir():
        raise WorkspaceError("invalid_workspace_path", "Agent candidate source is unavailable")
    source = raw_source.resolve()
    staging = _reset_finalization_staging(workspace)
    try:
        selection_source = _agent_source_file(source, selection)
        notes_source = _agent_source_file(source, notes)
        _copy_agent_file(selection_source, staging / "selection.json")
        _copy_agent_file(notes_source, staging / "notes.md")
        document = json.loads((staging / "selection.json").read_text(encoding="utf-8"))
        evidence = document.get("evidence")
        if not isinstance(evidence, list):
            raise WorkspaceError("invalid_contract", "selection evidence is invalid")
        for item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise WorkspaceError("invalid_contract", "selection evidence is invalid")
            relative = PurePosixPath(item["path"])
            evidence_source = _agent_source_file(source, relative.as_posix())
            destination = staging / relative
            _copy_agent_file(evidence_source, destination)
            item["path"] = (staging.relative_to(Path(workspace).resolve()) / relative).as_posix()
        _write_json(staging / "selection.json", document)
        result = finalize_workspace(
            workspace,
            selection=staging / "selection.json",
            notes=staging / "notes.md",
            rebuild_entrypoint=rebuild_entrypoint,
            geometry_entrypoint=geometry_entrypoint,
            tool_registry=tool_registry,
            validate_after_publish=False,
            scope=scope,
        )
        return {
            "final_delivery": {
                key: result[key]
                for key in ("selected_step", "accepted", "identity_sha256")
                if key in result
            }
        }
    finally:
        _discard_finalization_staging(workspace)


def _terminal_locator_path(workspace: Path) -> Path:
    """Return the ignored transfer-sidecar path owned by the Workspace seam."""

    workspace = Path(workspace).resolve()
    _read_workspace_document(workspace)
    run = workspace / "run"
    if run.is_symlink():
        _fail("invalid_workspace_path", "Workspace run area is a symlink", "$.run")
    try:
        run.mkdir(exist_ok=True)
    except OSError:
        _fail("invalid_workspace_path", "Workspace run area is unavailable", "$.run")
    return run / "terminal-validation-locator.json"


def _locator_atomic_rename_no_replace(source: Path, target: Path) -> None:
    if source.parent != target.parent:
        raise OSError("locator quarantine paths must share a directory")
    if _locator_is_windows():
        _locator_windows_move_file_ex(source, target)
        return
    source_bytes = os.fsencode(source)
    target_bytes = os.fsencode(target)
    at_fdcwd = -2 if platform.system() == "Darwin" else -100
    if platform.system() == "Darwin":
        try:
            function = ctypes.CDLL(None, use_errno=True).renameatx_np
        except AttributeError:
            function = None
        if function is not None:
            function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            if function(at_fdcwd, source_bytes, at_fdcwd, target_bytes, 0x00000004) == 0:
                return
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FileExistsError(target)
            if error not in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
                raise OSError(error, os.strerror(error))
    try:
        function = getattr(ctypes.CDLL(None, use_errno=True), "renameat2")
    except AttributeError:
        function = None
    if function is not None:
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        if function(at_fdcwd, source_bytes, at_fdcwd, target_bytes, 1) == 0:
            return
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(target)
        if error not in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
            raise OSError(error, os.strerror(error))
    raise OSError("atomic no-replace rename is unavailable")


def _locator_is_windows() -> bool:
    return os.name == "nt" or platform.system() == "Windows"


def _locator_windows_move_file_ex(source: Path, target: Path, native=None, last_error=None) -> None:
    if native is None:
        try:
            native = ctypes.windll.kernel32.MoveFileExW
        except AttributeError as exc:  # pragma: no cover - Windows only.
            raise OSError("Windows MoveFileExW is unavailable") from exc
    native.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    native.restype = ctypes.c_int
    if native(os.fspath(source), os.fspath(target), 0x00000008):
        return
    if last_error is None:
        last_error = getattr(ctypes, "get_last_error", lambda: 1)
    error = int(last_error())
    if error in {80, 183}:
        raise FileExistsError(target)
    raise OSError(error, f"MoveFileExW failed for {source} -> {target}")


def _locator_flush_file(descriptor: int) -> None:
    if not _locator_is_windows():
        os.fsync(descriptor)
        return
    try:
        import msvcrt
        handle = msvcrt.get_osfhandle(descriptor)
        if handle == -1 or not ctypes.windll.kernel32.FlushFileBuffers(ctypes.c_void_p(handle)):
            raise OSError("FlushFileBuffers failed")
    except (AttributeError, ImportError, OSError) as exc:
        raise OSError("Windows file durability is unavailable") from exc


def _locator_atomic_rename_available() -> bool:
    if _locator_is_windows():
        kernel = getattr(getattr(ctypes, "windll", None), "kernel32", None)
        try:
            import msvcrt  # noqa: F401
        except ImportError:
            return False
        return kernel is not None and hasattr(kernel, "MoveFileExW") and hasattr(
            kernel, "FlushFileBuffers"
        )
    if platform.system() == "Darwin":
        return hasattr(ctypes.CDLL(None), "renameatx_np")
    return hasattr(ctypes.CDLL(None), "renameat2")


def _locator_identity_and_bytes(path: Path) -> tuple[tuple[int, int], bytes]:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode):
            raise WorkspaceError("invalid_workspace_path", "terminal locator is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return (value.st_dev, value.st_ino), b"".join(chunks)
    finally:
        os.close(descriptor)


def _locator_fsync_parent(path: Path) -> None:
    if _locator_is_windows():
        return
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _locator_quarantine_delete_exact(
    path: Path,
    identity: tuple[int, int],
    expected: bytes,
) -> bool:
    if sum(1 for _ in path.parent.glob(f".{path.name}.quarantine-*")) >= 32:
        raise WorkspaceError("terminal_locator_quarantine_limit", "locator tombstone limit reached")
    slot = path.with_name(
        f".{path.name}.quarantine-{os.getpid()}-{secrets.token_hex(8)}"
    )
    try:
        _locator_atomic_rename_no_replace(path, slot)
    except (FileNotFoundError, FileExistsError, OSError):
        return False
    try:
        moved, value = _locator_identity_and_bytes(slot)
        if moved != identity or value != expected:
            try:
                _locator_atomic_rename_no_replace(slot, path)
            except FileExistsError:
                pass
            return False
        try:
            _locator_fsync_parent(path.parent)
        except OSError:
            return False
        return True
    except (OSError, WorkspaceError):
        return False


def write_terminal_locator(
    workspace: Path,
    payload: Mapping[str, Any],
) -> str:
    """Own atomic publication of the ignored terminal transfer sidecar."""

    target = _terminal_locator_path(workspace)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        _fail("invalid_workspace_path", "terminal locator is not a regular file", "$.run")
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            _fail("terminal_locator_conflict", "existing terminal locator is unreadable", "$.run")
        if existing == dict(payload):
            return "run/terminal-validation-locator.json"
        _fail("terminal_locator_conflict", "existing terminal locator belongs to another handoff", "$.run")
    if not _locator_atomic_rename_available():
        _fail("terminal_locator_unavailable", "atomic locator publication is unavailable", "$.run")
    if sum(1 for _ in target.parent.glob(f".{target.name}.quarantine-*")) >= 32:
        _fail("terminal_locator_quarantine_limit", "locator tombstone limit reached", "$.run")
    temporary = target.with_name(
        f".{target.name}.tmp-{os.getpid()}-{hashlib.sha256(os.urandom(16)).hexdigest()[:12]}"
    )
    encoded = (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    descriptor: int | None = None
    temporary_identity: tuple[int, int] | None = None
    target_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short terminal locator write")
            view = view[written:]
        value = os.fstat(descriptor)
        temporary_identity = value.st_dev, value.st_ino
        _locator_flush_file(descriptor)
        os.close(descriptor)
        descriptor = None
        _, temporary_bytes = _locator_identity_and_bytes(temporary)
        if temporary_bytes != encoded:
            raise OSError("terminal locator bytes changed before publication")
        _locator_atomic_rename_no_replace(temporary, target)
        target_identity, target_bytes = _locator_identity_and_bytes(target)
        if target_bytes != encoded:
            raise OSError("terminal locator bytes changed after publication")
        _locator_fsync_parent(target.parent)
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        if target_identity is not None:
            _locator_quarantine_delete_exact(target, target_identity, encoded)
        if temporary_identity is not None:
            _locator_quarantine_delete_exact(temporary, temporary_identity, encoded)
        raise
    return "run/terminal-validation-locator.json"


def read_terminal_locator(workspace: Path) -> dict[str, Any] | None:
    """Read the ignored locator through the Workspace-owned sidecar seam."""

    target = _terminal_locator_path(workspace)
    if target.is_symlink():
        _fail("invalid_workspace_path", "terminal locator is a symlink", "$.run")
    if not target.exists():
        return None
    if not target.is_file():
        _fail("invalid_workspace_path", "terminal locator is not a regular file", "$.run")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail("invalid_contract", "terminal locator is not valid JSON", "$.run")
    if not isinstance(value, dict):
        _fail("invalid_contract", "terminal locator is not an object", "$.run")
    return value


TERMINAL_VALIDATION_SCHEMA = "mesh-to-cad.terminal-validation/1"
CONTENT_MANIFEST_SCHEMA = "mesh-to-cad.content-manifest/1"
TERMINAL_BUNDLE_SCHEMA = "mesh-to-cad.terminal-validation-bundle/1"
TERMINAL_IDENTITY_SCHEMA = "mesh-to-cad.terminal-validation-handoff/1"
VALIDATOR_VERSION = "mesh-to-cad.workspace-validator/1"

_TERMINAL_FIELDS = {
    "schema",
    "workspace_id",
    "workspace_identity_sha256",
    "validator_version",
    "graph",
    "review_graph",
    "recovery",
    "review_facts",
    "evaluation_facts",
    "content_manifest_sha256",
    "identity_sha256",
}
_BUNDLE_FIELDS = {"schema", "result", "manifest"}
_MANIFEST_FIELDS = {
    "schema",
    "workspace_id",
    "workspace_identity_sha256",
    "files",
    "identity_sha256",
}
_MANIFEST_ENTRY_FIELDS = {"path", "sha256", "size_bytes"}
_REVIEW_FACT_FIELDS = {
    "step_count",
    "cycle_count",
    "failed_attempt_count",
    "accepted_steps",
    "heads",
    "budget",
    "final_delivery",
    "step_outcomes",
}
_STEP_OUTCOME_FIELDS = {
    "step",
    "parent_step",
    "cycle",
    "accepted",
    "no_observable_geometry_change",
    "candidate_mesh_sha256",
    "observable_sha256",
}
_EVALUATION_FACT_FIELDS = {
    "accepted_step_count",
    "has_accepted_step",
    "final_delivery_present",
    "final_delivery_accepted",
    "objective_facts",
}
_OBJECTIVE_FACT_FIELDS = {
    "global_depth_8_zero",
    "out_of_frame_clear",
    "no_evidence_conflict",
}
_MANIFEST_EXCLUDED_ROOTS = frozenset({".git", "run", "work"})
_AGENT_MAX_FILE_BYTES = 512 * 1024 * 1024
_AGENT_MAX_TREE_BYTES = 1024 * 1024 * 1024


def _agent_source_file(root: Path, value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(
        part in {"", ".", ".."} for part in pure.parts
    ):
        raise WorkspaceError("invalid_workspace_path", "Agent path is not normalized")
    target = root.joinpath(*pure.parts)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise WorkspaceError("invalid_workspace_path", "Agent path escapes candidate") from error
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise WorkspaceError("invalid_workspace_path", "Agent path contains symlink")
    return target


def _agent_relative(root: Path, value: str) -> str:
    return _agent_source_file(root, value).relative_to(root).as_posix()


def _agent_open(path: Path, *, directory: bool = False, dir_fd: int | None = None) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        return os.open(path, flags, dir_fd=dir_fd)
    except OSError as error:
        raise WorkspaceError("invalid_workspace_path", "Agent artifact cannot be opened") from error


def _copy_agent_file(source: Path, target: Path) -> None:
    """Copy one external Agent file through the shared descriptor guard."""

    descriptor = _agent_open(source)
    try:
        _copy_agent_file_from_descriptor(descriptor, target)
    finally:
        os.close(descriptor)


def _copy_agent_tree(source: Path, target: Path) -> None:
    source_fd = _agent_open(source, directory=True)
    total = 0

    def visit(directory_fd: int, relative: PurePosixPath) -> None:
        nonlocal total
        with os.scandir(directory_fd) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                if entry.is_symlink() or entry.name in {"", ".", ".."}:
                    raise WorkspaceError("invalid_workspace_path", "Agent tree contains symlink")
                if not relative.parts and entry.name == "bootstrap.json":
                    continue
                child_relative = relative / entry.name
                if entry.is_dir(follow_symlinks=False):
                    child_fd = _agent_open(Path(entry.name), directory=True, dir_fd=directory_fd)
                    try:
                        (target / child_relative).mkdir(parents=True, exist_ok=True)
                        visit(child_fd, child_relative)
                    finally:
                        os.close(child_fd)
                    continue
                child_fd = os.open(
                    entry.name,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=directory_fd,
                )
                try:
                    metadata = os.fstat(child_fd)
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                        raise WorkspaceError("invalid_workspace_path", "Agent tree contains special file")
                    if total + metadata.st_size > _AGENT_MAX_TREE_BYTES:
                        raise WorkspaceError("invalid_workspace_path", "Agent tree is too large")
                    copied = _copy_agent_file_from_descriptor(
                        child_fd,
                        target / child_relative,
                        max_bytes=_AGENT_MAX_TREE_BYTES - total,
                    )
                    total += copied
                    if total > _AGENT_MAX_TREE_BYTES:
                        raise WorkspaceError("invalid_workspace_path", "Agent tree is too large")
                finally:
                    os.close(child_fd)

    target_created = False
    try:
        target.mkdir(parents=True, exist_ok=False)
        target_created = True
        visit(source_fd, PurePosixPath())
    except Exception:
        if target_created:
            shutil.rmtree(target, ignore_errors=True)
        raise
    finally:
        os.close(source_fd)


def _copy_agent_file_from_descriptor(
    descriptor: int,
    target: Path,
    *,
    max_bytes: int = _AGENT_MAX_FILE_BYTES,
) -> int:
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size > _AGENT_MAX_FILE_BYTES
        or before.st_size > max_bytes
    ):
        raise WorkspaceError("invalid_workspace_path", "Agent artifact is not a regular file")
    target.parent.mkdir(parents=True, exist_ok=True)
    target_created = False
    try:
        target_fd = os.open(
            target,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as error:
        raise WorkspaceError("invalid_workspace_path", "Agent artifact target is unavailable") from error
    target_created = True
    try:
        digest = hashlib.sha256()
        copied = 0
        while True:
            remaining = before.st_size - copied
            if remaining < 0 or copied > max_bytes:
                raise WorkspaceError("invalid_workspace_path", "Agent artifact exceeds its validated size")
            chunk = os.read(descriptor, min(1024 * 1024, remaining)) if remaining else b""
            if not chunk:
                break
            if len(chunk) > remaining or copied + len(chunk) > max_bytes:
                raise WorkspaceError("invalid_workspace_path", "Agent artifact exceeds its validated size")
            digest.update(chunk)
            copied += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(target_fd, view)
                if written <= 0:
                    raise OSError("short Agent artifact write")
                view = view[written:]
        copied_metadata = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        reread = hashlib.sha256()
        reread_bytes = 0
        while True:
            remaining = before.st_size - reread_bytes
            if remaining < 0:
                raise WorkspaceError("invalid_workspace_path", "Agent artifact exceeds its validated size")
            chunk = os.read(descriptor, min(1024 * 1024, remaining)) if remaining else b""
            if not chunk:
                break
            if len(chunk) > remaining:
                raise WorkspaceError("invalid_workspace_path", "Agent artifact exceeds its validated size")
            reread.update(chunk)
            reread_bytes += len(chunk)
        after = os.fstat(descriptor)
        if (
            copied != before.st_size
            or reread_bytes != before.st_size
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_nlink != after.st_nlink
            or before.st_size != after.st_size
            or before.st_mode != after.st_mode
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or copied_metadata.st_dev != after.st_dev
            or copied_metadata.st_ino != after.st_ino
            or copied_metadata.st_nlink != after.st_nlink
            or copied_metadata.st_size != after.st_size
            or copied_metadata.st_mode != after.st_mode
            or copied_metadata.st_mtime_ns != after.st_mtime_ns
            or copied_metadata.st_ctime_ns != after.st_ctime_ns
            or digest.digest() != reread.digest()
        ):
            raise WorkspaceError("invalid_workspace_path", "Agent artifact changed during copy")
        target_stat = os.fstat(target_fd)
        if target_stat.st_size != copied:
            raise WorkspaceError("invalid_workspace_path", "Agent artifact target size is invalid")
        os.fsync(target_fd)
    except Exception:
        if target_created:
            target.unlink(missing_ok=True)
        raise
    finally:
        os.close(target_fd)
    return copied


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compile_terminal_validation(workspace: Path) -> dict[str, Any]:
    """Compile one terminal evidence bundle without mutating the Workspace."""

    workspace = Path(workspace).resolve()
    before = _content_state(workspace)
    validation = validate_workspace(workspace)
    _require_terminal_state(validation.graph)
    after = _content_state(workspace)
    _require_stable_snapshot(before, after)

    workspace_document = _read_workspace_document(workspace)
    manifest = _build_manifest(workspace_document, after)
    result = _build_result(workspace, workspace_document, validation, manifest)
    _validate_manifest(manifest, workspace)
    _validate_result(result, manifest, workspace)
    _require_stable_snapshot(after, _content_state(workspace))

    bundle = {
        "schema": TERMINAL_BUNDLE_SCHEMA,
        "result": result,
        "manifest": manifest,
    }
    return {
        "bundle": bundle,
        "terminal_identity_sha256": _identity(TERMINAL_IDENTITY_SCHEMA, bundle),
    }


def verify_terminal_validation(
    workspace: Path,
    bundle: Mapping[str, Any],
    expected_identity: str | None = None,
) -> dict[str, Any]:
    """Verify a caller-supplied terminal bundle without full validation or Git."""

    if expected_identity is None:
        _fail(
            "terminal_identity_required",
            "caller must supply the expected Terminal Validation identity",
            "$.expected_terminal_identity",
        )
    _sha256(expected_identity, "$.expected_terminal_identity")
    workspace = Path(workspace).resolve()
    _closed(bundle, _BUNDLE_FIELDS, "$.terminal_bundle")
    _const(bundle["schema"], TERMINAL_BUNDLE_SCHEMA, "$.terminal_bundle.schema")
    actual_identity = _identity(TERMINAL_IDENTITY_SCHEMA, bundle)
    if actual_identity != expected_identity:
        _fail(
            "terminal_identity_mismatch",
            "terminal bundle does not match the expected identity",
            "$.expected_terminal_identity",
        )
    manifest = bundle["manifest"]
    result = bundle["result"]
    _validate_manifest(manifest, workspace)
    _validate_result(result, manifest, workspace)
    return dict(result)


def _build_manifest(
    workspace_document: Mapping[str, Any],
    inventory: tuple[tuple[str, int, str], ...],
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema": CONTENT_MANIFEST_SCHEMA,
        "workspace_id": workspace_document["workspace_id"],
        "workspace_identity_sha256": _workspace_identity(workspace_document),
        "files": [
            {"path": path, "size_bytes": size, "sha256": digest}
            for path, size, digest in inventory
        ],
    }
    manifest["identity_sha256"] = _identity(CONTENT_MANIFEST_SCHEMA, manifest)
    return manifest


def _build_result(
    workspace: Path,
    workspace_document: Mapping[str, Any],
    validation: ValidationResult,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": TERMINAL_VALIDATION_SCHEMA,
        "workspace_id": workspace_document["workspace_id"],
        "workspace_identity_sha256": _workspace_identity(workspace_document),
        "validator_version": VALIDATOR_VERSION,
        "graph": validation.graph,
        "review_graph": _build_review_graph(workspace, validation.graph),
        "recovery": list(validation.recovery),
        "review_facts": _review_facts(validation.graph),
        "evaluation_facts": _evaluation_facts(workspace, validation.graph),
        "content_manifest_sha256": manifest["identity_sha256"],
    }
    result["identity_sha256"] = _identity(TERMINAL_VALIDATION_SCHEMA, result)
    return result


def _build_review_graph(
    workspace: Path,
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze the extra closed facts needed by review at W1 compile time."""

    steps: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    recorded_attempts: set[int] = set()
    for item in graph["steps"]:
        step_number = int(item["step"])
        step_path = workspace / "steps" / f"{step_number:06d}" / "step.json"
        step_document = _core._read_json(step_path, "$.review_graph.step")
        attempt_ids = list(step_document.get("attempt_ids", []))
        steps.append({**dict(item), "attempt_ids": attempt_ids})
        for attempt_number in attempt_ids:
            if not isinstance(attempt_number, int) or attempt_number in recorded_attempts:
                continue
            candidates = [
                workspace / "attempts" / f"{attempt_number:06d}" / "attempt.json",
                workspace / "steps" / f"{step_number:06d}" / "attempt.json",
                workspace / "cycles" / f"{step_number:06d}" / "attempt.json",
            ]
            attempt_path = next((path for path in candidates if path.is_file()), None)
            if attempt_path is None:
                raise _core.WorkspaceError(
                    "corrupt_workspace",
                    "review graph references a missing Attempt",
                    "$.review_graph.attempts",
                )
            attempts.append(
                {
                    "attempt": _core._read_json(attempt_path, "$.review_graph.attempt"),
                    "path": attempt_path.relative_to(workspace).as_posix(),
                }
            )
            recorded_attempts.add(attempt_number)
    for item in graph["failed_attempts"]:
        attempt_number = item.get("attempt")
        if not isinstance(attempt_number, int) or attempt_number in recorded_attempts:
            continue
        attempt_path = workspace / "attempts" / f"{attempt_number:06d}" / "attempt.json"
        if attempt_path.is_file():
            attempts.append(
                {
                    "attempt": _core._read_json(attempt_path, "$.review_graph.attempt"),
                    "path": attempt_path.relative_to(workspace).as_posix(),
                }
            )
            recorded_attempts.add(attempt_number)
    cycles: list[dict[str, Any]] = []
    for item in graph["cycles"]:
        number = int(item["cycle"])
        root = workspace / "cycles" / f"{number:06d}"
        cycles.append(
            {
                **dict(item),
                "plan": _core._read_json(root / "plan.json", "$.review_graph.plan"),
                "source_changes": _core._read_json(
                    root / "source_changes.json", "$.review_graph.source_changes"
                ),
                "diff_document": _core._read_json(
                    root / "diff.json", "$.review_graph.diff"
                ),
                "assessment": _core._read_json(
                    root / "assessment.json", "$.review_graph.assessment"
                ),
            }
        )
    delivery = graph.get("final_delivery")
    final: dict[str, Any] | None = None
    if isinstance(delivery, Mapping):
        def optional_final(name: str) -> dict[str, Any] | None:
            path = workspace / "final" / f"{name}.json"
            if not path.is_file():
                return None
            return _core._read_json(path, f"$.review_graph.{name}")

        final = {
            "selection": optional_final("selection"),
            "manifest": optional_final("manifest"),
            "rebuild": optional_final("rebuild"),
            "verification": optional_final("verification"),
        }
    return {
        "schema": "mesh-to-cad.review-graph/1",
        "steps": steps,
        "attempts": attempts,
        "failed_attempts": list(graph["failed_attempts"]),
        "cycles": cycles,
        "final": final,
    }


def _require_terminal_state(graph: Mapping[str, Any]) -> None:
    if graph.get("final_delivery") is None:
        _fail(
            "terminal_state_required",
            "Terminal Validation requires a complete Final Delivery",
            "$.graph.final_delivery",
        )


def _content_state(workspace: Path) -> tuple[tuple[str, int, str], ...]:
    state: list[tuple[str, int, str]] = []
    for path in _content_files(workspace):
        relative = _safe_relative(workspace, path)
        try:
            size = path.stat().st_size
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            _inventory_error(relative)
        state.append((relative, size, digest))
    return tuple(state)


def _require_stable_snapshot(
    before: tuple[tuple[str, int, str], ...],
    after: tuple[tuple[str, int, str], ...],
) -> None:
    if before != after:
        before_paths = {entry[0] for entry in before}
        after_paths = {entry[0] for entry in after}
        changed = next(
            (left[0] for left, right in zip(before, after) if left != right),
            next(
                (item[0] for item in after if item[0] not in before_paths),
                next(
                    (item[0] for item in before if item[0] not in after_paths),
                    "Workspace content",
                ),
            ),
        )
        _fail(
            "workspace_changed_during_validation",
            "Workspace authority changed during Terminal Validation",
            f"$.{changed}",
        )


def _content_files(workspace: Path) -> list[Path]:
    try:
        is_directory = workspace.is_dir()
        is_link = workspace.is_symlink()
    except OSError:
        _inventory_error("$")
    if not is_directory or is_link:
        _fail("invalid_workspace", "Workspace directory does not exist")
    files: list[Path] = []
    try:
        children = sorted(workspace.iterdir(), key=lambda path: path.name)
    except OSError:
        _inventory_error("$")
    for child in children:
        if child.name in _MANIFEST_EXCLUDED_ROOTS:
            continue
        files.extend(_walk_content(workspace, child))
    return sorted(files, key=lambda path: path.relative_to(workspace).as_posix())


def _walk_content(workspace: Path, path: Path) -> list[Path]:
    try:
        is_link = path.is_symlink()
        is_file = path.is_file()
        is_directory = path.is_dir()
    except OSError:
        _inventory_error(_safe_relative(workspace, path))
    if is_link:
        _fail(
            "invalid_workspace_path",
            "Terminal Validation content cannot contain symlinks",
            f"$.{path.name}",
        )
    if is_file:
        return [path]
    if not is_directory:
        _fail("corrupt_workspace", "Terminal Validation content is not a file or directory")
    files: list[Path] = []
    try:
        children = sorted(path.iterdir(), key=lambda item: item.name)
    except OSError:
        _inventory_error(path.name)
    for child in children:
        files.extend(_walk_content(workspace, child))
    return files


def _safe_relative(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return "Workspace content"


def _inventory_error(relative: str) -> None:
    _fail(
        "corrupt_workspace",
        "cannot read Workspace authority content",
        f"$.{relative}" if relative != "$" else "$",
    )


def _validate_manifest(manifest: Mapping[str, Any], workspace: Path) -> None:
    _closed(manifest, _MANIFEST_FIELDS, "$.content_manifest")
    _const(manifest["schema"], CONTENT_MANIFEST_SCHEMA, "$.content_manifest.schema")
    _nonempty_string(manifest["workspace_id"], "$.content_manifest.workspace_id")
    _sha256(manifest["workspace_identity_sha256"], "$.content_manifest.workspace_identity_sha256")
    files = manifest["files"]
    if not isinstance(files, list):
        _fail("invalid_contract", "must be an array", "$.content_manifest.files")
    previous = ""
    seen: set[str] = set()
    for index, item in enumerate(files):
        path = f"$.content_manifest.files[{index}]"
        _closed(item, _MANIFEST_ENTRY_FIELDS, path)
        name = item["path"]
        if not isinstance(name, str):
            _fail("invalid_contract", "path must be a string", f"{path}.path")
        pure = PurePosixPath(name)
        if (
            not pure.parts
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or name != pure.as_posix()
            or pure.parts[0] in _MANIFEST_EXCLUDED_ROOTS
        ):
            _fail("invalid_workspace_path", "manifest path is not canonical content", f"{path}.path")
        if name in seen or name <= previous:
            _fail("invalid_contract", "manifest paths must be unique and sorted", f"{path}.path")
        seen.add(name)
        previous = name
        _sha256(item["sha256"], f"{path}.sha256")
        size = item["size_bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            _fail("invalid_contract", "size_bytes must be a non-negative integer", f"{path}.size_bytes")
    expected_identity = _identity(
        CONTENT_MANIFEST_SCHEMA,
        {key: manifest[key] for key in _MANIFEST_FIELDS if key != "identity_sha256"},
    )
    if manifest["identity_sha256"] != expected_identity:
        _fail("corrupt_workspace", "content manifest identity mismatch", "$.content_manifest.identity_sha256")
    expected_files = {
        path.relative_to(workspace).as_posix(): path for path in _content_files(workspace)
    }
    if seen != set(expected_files):
        _fail("corrupt_workspace", "content manifest file set mismatch", "$.content_manifest.files")
    for item in files:
        path = expected_files[item["path"]]
        relative = item["path"]
        try:
            actual_size = path.stat().st_size
            actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            _inventory_error(relative)
        if actual_size != item["size_bytes"] or actual_digest != item["sha256"]:
            _fail("corrupt_workspace", "content manifest file digest mismatch", "$.content_manifest.files")


def _validate_result(
    result: Mapping[str, Any], manifest: Mapping[str, Any], workspace: Path
) -> None:
    _closed(result, _TERMINAL_FIELDS, "$.terminal_validation")
    _const(result["schema"], TERMINAL_VALIDATION_SCHEMA, "$.terminal_validation.schema")
    _nonempty_string(result["workspace_id"], "$.terminal_validation.workspace_id")
    _sha256(result["workspace_identity_sha256"], "$.terminal_validation.workspace_identity_sha256")
    _const(result["validator_version"], VALIDATOR_VERSION, "$.terminal_validation.validator_version")
    graph = result["graph"]
    if not isinstance(graph, dict) or graph.get("schema") != _core.INDEX_SCHEMA:
        _fail("invalid_contract", "graph schema is unsupported", "$.terminal_validation.graph")
    _validate_graph_shape(graph)
    review_graph = result["review_graph"]
    if not isinstance(review_graph, dict) or review_graph.get("schema") != "mesh-to-cad.review-graph/1":
        _fail("invalid_contract", "review graph schema is unsupported", "$.terminal_validation.review_graph")
    if not isinstance(result["recovery"], list):
        _fail("invalid_contract", "recovery must be an array", "$.terminal_validation.recovery")
    _validate_review_facts(result["review_facts"])
    _validate_evaluation_facts(result["evaluation_facts"])
    try:
        expected_review = _review_facts(graph)
        expected_evaluation = _evaluation_facts(workspace, graph)
        expected_review_graph = _build_review_graph(workspace, graph)
    except (KeyError, TypeError, ValueError):
        _fail("invalid_contract", "graph facts are structurally incomplete", "$.terminal_validation.graph")
    if result["review_facts"] != expected_review:
        _fail("corrupt_workspace", "review facts are not deterministic", "$.terminal_validation.review_facts")
    if review_graph != expected_review_graph:
        _fail("corrupt_workspace", "review graph is not deterministic", "$.terminal_validation.review_graph")
    if result["evaluation_facts"] != expected_evaluation:
        _fail("corrupt_workspace", "evaluation facts are not deterministic", "$.terminal_validation.evaluation_facts")
    _sha256(result["content_manifest_sha256"], "$.terminal_validation.content_manifest_sha256")
    if result["content_manifest_sha256"] != manifest["identity_sha256"]:
        _fail("corrupt_workspace", "result is bound to another content manifest", "$.terminal_validation.content_manifest_sha256")
    expected_identity = _identity(
        TERMINAL_VALIDATION_SCHEMA,
        {key: result[key] for key in _TERMINAL_FIELDS if key != "identity_sha256"},
    )
    if result["identity_sha256"] != expected_identity:
        _fail("corrupt_workspace", "Terminal Validation result identity mismatch", "$.terminal_validation.identity_sha256")
    if (
        result["workspace_id"] != manifest["workspace_id"]
        or result["workspace_identity_sha256"] != manifest["workspace_identity_sha256"]
    ):
        _fail("corrupt_workspace", "result and manifest identities conflict", "$.terminal_validation.workspace_identity_sha256")


def _validate_graph_shape(graph: Mapping[str, Any]) -> None:
    required = {
        "schema",
        "steps",
        "cycles",
        "failed_attempts",
        "accepted_steps",
        "budget",
        "heads",
        "final_delivery",
    }
    missing = required - set(graph)
    if missing:
        _fail("invalid_contract", f"graph is missing {sorted(missing)}", "$.terminal_validation.graph")
    for key in ("steps", "cycles", "failed_attempts", "accepted_steps", "heads"):
        if not isinstance(graph[key], list):
            _fail("invalid_contract", f"graph.{key} must be an array", f"$.terminal_validation.graph.{key}")
    if not isinstance(graph["budget"], dict):
        _fail("invalid_contract", "graph.budget must be an object", "$.terminal_validation.graph.budget")
    if graph["final_delivery"] is not None and not isinstance(graph["final_delivery"], dict):
        _fail("invalid_contract", "graph.final_delivery must be an object or null", "$.terminal_validation.graph.final_delivery")


def _review_facts(graph: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "step_count": len(graph["steps"]),
        "cycle_count": len(graph["cycles"]),
        "failed_attempt_count": len(graph["failed_attempts"]),
        "accepted_steps": list(graph["accepted_steps"]),
        "heads": list(graph["heads"]),
        "budget": dict(graph["budget"]),
        "final_delivery": graph["final_delivery"],
        "step_outcomes": [
            {
                "step": item["step"],
                "parent_step": item["parent_step"],
                "cycle": item["cycle"],
                "accepted": item["accepted"],
                "no_observable_geometry_change": item["no_observable_geometry_change"],
                "candidate_mesh_sha256": item["candidate_mesh_sha256"],
                "observable_sha256": item["observable_sha256"],
            }
            for item in graph["steps"]
        ],
    }


def _evaluation_facts(workspace: Path, graph: Mapping[str, Any]) -> dict[str, Any]:
    delivery = graph["final_delivery"]
    return {
        "accepted_step_count": len(graph["accepted_steps"]),
        "has_accepted_step": bool(graph["accepted_steps"]),
        "final_delivery_present": delivery is not None,
        "final_delivery_accepted": bool(delivery["accepted"]) if delivery is not None else None,
        "objective_facts": [
            {"step": item["step"], "facts": _measurement_objective_facts(workspace, item)}
            for item in graph["steps"]
        ],
    }


def _measurement_objective_facts(workspace: Path, step: Mapping[str, Any]) -> dict[str, bool]:
    path = (workspace / step["measurement"]).resolve()
    try:
        path.relative_to(workspace.resolve())
    except ValueError:
        _fail("invalid_workspace_path", "measurement path escapes the Workspace")
    measurement = _read_authority_json(workspace, path, "$.terminal_validation.measurement")
    facts = measurement.get("objective_facts")
    _closed(facts, _OBJECTIVE_FACT_FIELDS, "$.terminal_validation.objective_facts")
    if any(not isinstance(facts[key], bool) for key in _OBJECTIVE_FACT_FIELDS):
        _fail("invalid_contract", "objective facts must be boolean", "$.terminal_validation.objective_facts")
    return {key: facts[key] for key in sorted(_OBJECTIVE_FACT_FIELDS)}


def _validate_review_facts(value: Any) -> None:
    _closed(value, _REVIEW_FACT_FIELDS, "$.terminal_validation.review_facts")
    for key in ("step_count", "cycle_count", "failed_attempt_count"):
        if not isinstance(value[key], int) or isinstance(value[key], bool) or value[key] < 0:
            _fail("invalid_contract", f"{key} must be a non-negative integer", f"$.terminal_validation.review_facts.{key}")
    for key in ("accepted_steps", "heads"):
        if not isinstance(value[key], list) or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in value[key]):
            _fail("invalid_contract", f"{key} must contain non-negative integers", f"$.terminal_validation.review_facts.{key}")
    if not isinstance(value["budget"], dict) or set(value["budget"]) != {"completed_cycles", "remaining_cycles", "total_attempts", "tool_failures"}:
        _fail("invalid_contract", "budget facts are not closed", "$.terminal_validation.review_facts.budget")
    if not isinstance(value["step_outcomes"], list):
        _fail("invalid_contract", "step_outcomes must be an array", "$.terminal_validation.review_facts.step_outcomes")
    for index, item in enumerate(value["step_outcomes"]):
        path = f"$.terminal_validation.review_facts.step_outcomes[{index}]"
        _closed(item, _STEP_OUTCOME_FIELDS, path)
        for key in ("candidate_mesh_sha256", "observable_sha256"):
            _sha256(item[key], f"{path}.{key}")
        for key in ("accepted", "no_observable_geometry_change"):
            if not isinstance(item[key], bool):
                _fail("invalid_contract", f"{key} must be boolean", f"{path}.{key}")


def _validate_evaluation_facts(value: Any) -> None:
    _closed(value, _EVALUATION_FACT_FIELDS, "$.terminal_validation.evaluation_facts")
    if not isinstance(value["accepted_step_count"], int) or isinstance(value["accepted_step_count"], bool) or value["accepted_step_count"] < 0:
        _fail("invalid_contract", "accepted_step_count must be non-negative", "$.terminal_validation.evaluation_facts.accepted_step_count")
    for key in ("has_accepted_step", "final_delivery_present"):
        if not isinstance(value[key], bool):
            _fail("invalid_contract", f"{key} must be boolean", f"$.terminal_validation.evaluation_facts.{key}")
    if value["final_delivery_accepted"] is not None and not isinstance(value["final_delivery_accepted"], bool):
        _fail("invalid_contract", "final_delivery_accepted must be boolean or null", "$.terminal_validation.evaluation_facts.final_delivery_accepted")
    if not isinstance(value["objective_facts"], list):
        _fail("invalid_contract", "objective_facts must be an array", "$.terminal_validation.evaluation_facts.objective_facts")
    for index, item in enumerate(value["objective_facts"]):
        path = f"$.terminal_validation.evaluation_facts.objective_facts[{index}]"
        _closed(item, {"step", "facts"}, path)
        if not isinstance(item["step"], int) or isinstance(item["step"], bool) or item["step"] < 0:
            _fail("invalid_contract", "objective fact step must be non-negative", f"{path}.step")
        _closed(item["facts"], _OBJECTIVE_FACT_FIELDS, f"{path}.facts")
        if any(not isinstance(item["facts"][key], bool) for key in _OBJECTIVE_FACT_FIELDS):
            _fail("invalid_contract", "objective facts must be boolean", f"{path}.facts")


def _read_workspace_document(workspace: Path) -> dict[str, Any]:
    return _read_authority_json(workspace, workspace / "workspace.json", "$.workspace")


def _read_authority_json(workspace: Path, path: Path, label: str) -> dict[str, Any]:
    relative = _safe_relative(workspace, path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _inventory_error(relative)
    if not isinstance(value, dict):
        _fail("invalid_contract", "JSON artifact must contain an object", label)
    return value


def _workspace_identity(value: Mapping[str, Any]) -> str:
    return _core._identity(_core.WORKSPACE_SCHEMA, value)


_closed = _core._closed_object
_const = _core._const
_nonempty_string = _core._nonempty_string
_sha256 = _core._sha256
_identity = _core._identity


def _fail(classification: str, detail: str, path: str = "$") -> None:
    raise WorkspaceError(classification, detail, path)


__all__ = [
    "CONTENT_MANIFEST_SCHEMA",
    "DEFAULT_COMMAND_SECONDS",
    "ExecutionScope",
    "FAILED_ATTEMPT_RESULTS",
    "MAX_ATTEMPTS_PER_STEP",
    "MAX_REPAIR_CYCLES",
    "MAX_TOOL_FAILURES_PER_STEP",
    "TERMINAL_BUNDLE_SCHEMA",
    "TERMINAL_IDENTITY_SCHEMA",
    "TERMINAL_VALIDATION_SCHEMA",
    "VALIDATOR_VERSION",
    "WorkspaceError",
    "ValidationResult",
    "begin_attempt",
    "cancel_active_commands",
    "compile_terminal_validation",
    "finalize_workspace",
    "finalize_agent_submission",
    "initialize_workspace",
    "publish_cycle",
    "publish_cycle_from_agent",
    "publish_step_zero",
    "publish_step_zero_from_agent",
    "read_terminal_locator",
    "record_attempt",
    "recover_workspace",
    "rebuild_index",
    "run_attempt_command",
    "run_canonical_build",
    "validate_workspace",
    "verify_terminal_validation",
    "write_terminal_locator",
    "workspace_initialized",
    "workspace_status",
]
