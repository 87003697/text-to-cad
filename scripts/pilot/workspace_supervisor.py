"""Trusted runner boundary for the Mesh-to-CAD Agent Surface.

The Agent Surface deliberately has no knowledge of Workspace or reference
paths.  This module is the small trusted adapter supplied by the outer pilot
runner.  It owns the opaque-handle registry, executes registered candidate
operations in a private candidate tree, and keeps terminal validation out of
the Agent-visible result contract.

The adapter is dependency-injected so the isolation and lifecycle contract can
be tested without a CAD install.  In production the default dependencies are
the W1 Workspace facade and the W2 Reference Capability.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from types import ModuleType
from typing import Any, Callable, Mapping, Protocol, Sequence

try:
    from scripts.pilot.trusted_tools import (  # type: ignore[import-not-found]
        CADGEN_RUNTIME_RELATIVE,
        CANONICAL_BUILD_RELATIVE,
        TrustedToolsError,
        validate_trusted_tools,
    )
except ModuleNotFoundError as _exc:  # pragma: no cover - direct-execution fallback
    if _exc.name not in {"scripts", "scripts.pilot"}:
        raise
    from trusted_tools import (  # type: ignore[no-redef]
        CADGEN_RUNTIME_RELATIVE,
        CANONICAL_BUILD_RELATIVE,
        TrustedToolsError,
        validate_trusted_tools,
    )

try:
    from scripts.pilot.step_zero_evidence import (  # type: ignore[import-not-found]
        _MESHSCOPE_SRC,
        _ensure_shipped_package,
    )
except ModuleNotFoundError as _exc:  # pragma: no cover - direct-execution fallback
    if _exc.name not in {"scripts", "scripts.pilot"}:
        raise
    from step_zero_evidence import (  # type: ignore[no-redef]
        _MESHSCOPE_SRC,
        _ensure_shipped_package,
    )


MAX_OPERATION_OUTPUT_BYTES = 64 * 1024
MAX_OPERATION_TIMEOUT_SECONDS = 1800
MAX_CANDIDATE_FILE_BYTES = 512 * 1024 * 1024
MAX_ATTEMPT_STEP = 5
MAX_ATTEMPTS_PER_STEP = 3
MAX_CYCLES = 5
MAX_CANONICAL_BUILD_TIMEOUT_SECONDS = 1800
MAX_CANONICAL_BUILD_ATTEMPTS = 8
CANDIDATE_PUBLISHED_MEASUREMENT_NAME = "candidate.glb"
_TRUSTED_OUTPUT_PREFIX = ".trusted-out-"
_CANONICAL_OUTPUT_FILES = (
    "canonical.step",
    "measurement.glb",
    "profile.json",
    "build.json",
    "rebuild.json",
)
_CANONICAL_MEASUREMENT_NAME = "measurement.glb"
_CANONICAL_MANIFEST_NAME = "build.json"
_CANONICAL_RECIPE_NAME = "rebuild.json"
_CANONICAL_BUILD_SCHEMA = "mesh-to-cad.build/1"
_CANONICAL_RECIPE_SCHEMA = "mesh-to-cad.rebuild-recipe/1"
_CANONICAL_ADAPTER_ID = "cad.canonical-build/1"
_BUILDER_INTERNAL_PATH = PurePosixPath("/builder")
_BUILDER_TOOL_ENTRYPOINT = PurePosixPath("canonical-build")
_SOURCE_INTERNAL_ROOT = PurePosixPath("source")
_SOURCE_MODULE_NAME = "model.py"
MAX_DECLARED_SIDECARS = 32
MAX_SIDECAR_BYTES = 512 * 1024
# The Agent-visible current work subtree.  The outer Agent sandbox mounts
# the supervisor's candidate root at ``/candidate`` and the Agent authors
# under ``/candidate/work``; the nested candidate-tool bwrap binds this
# same host subtree to ``/candidate`` so the fixed operation argv remains
# candidate-relative.  The name is intentionally opaque: no Attempt
# identifier is encoded so a stale or forged Attempt-named sibling cannot
# pose as the current work tree.
_CURRENT_WORK_SUBDIR = "work"
_HANDLE_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_SAFE_ENV_KEYS = frozenset(
    {
        "LANG",
        "LC_ALL",
        "TZ",
        "PATH",
        "HOME",
        "TMPDIR",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHOME",
        "PYTHONNOUSERSITE",
    }
)
_CANDIDATE_SYSTEM_MOUNTS = (
    "/usr",
    "/etc/ca-certificates",
    "/etc/group",
    "/etc/hosts",
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ld.so.conf.d",
    "/etc/localtime",
    "/etc/nsswitch.conf",
    "/etc/passwd",
    "/etc/resolv.conf",
    "/etc/ssl",
)


class SupervisorError(RuntimeError):
    """A trusted-side lifecycle failure with a closed public classification."""

    def __init__(self, classification: str):
        self.classification = classification
        super().__init__(classification)


class WorkspaceAPI(Protocol):
    """Subset of the W1 facade used by the concrete supervisor."""

    def workspace_status(self, workspace: Path) -> Mapping[str, Any]: ...

    def workspace_initialized(self, workspace: Path) -> bool: ...

    def publish_step_zero_from_candidate(
        self,
        workspace: Path,
        *,
        attempt: int,
        source: Path,
        evidence_provider: Callable[..., Any],
    ) -> Mapping[str, Any]: ...

    def publish_cycle_from_candidate(
        self,
        workspace: Path,
        *,
        attempt: int,
        source: Path,
        evidence_provider: Callable[..., Any],
    ) -> Mapping[str, Any]: ...

    def read_current_step_decision_facts(
        self, workspace: Path, *, step: int
    ) -> Mapping[str, Any]: ...

    def finalize_from_agent_selection_claim(
        self, workspace: Path, *, scope: Any | None = None, **kwargs: Any
    ) -> Mapping[str, Any]: ...

    def run_attempt_command(
        self,
        workspace: Path,
        *,
        attempt: int,
        phase: str,
        argv: list[str],
        timeout_seconds: int,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        scope: Any | None = None,
    ) -> Mapping[str, Any]: ...

    def cancel_active_commands(self, scope: Any) -> bool: ...

    def begin_attempt(
        self,
        workspace: Path,
        plan: Path,
        *,
        intended_step: int,
        from_step: int | None,
    ) -> Mapping[str, Any]: ...

    def seed_repair_source_from_parent_step(
        self,
        workspace: Path,
        *,
        attempt: int,
        from_step: int,
        destination: Path,
    ) -> None: ...

    def publish_step_zero(self, workspace: Path, **kwargs: Any) -> Mapping[str, Any]: ...

    def publish_cycle(self, workspace: Path, **kwargs: Any) -> Mapping[str, Any]: ...

    def finalize_workspace(self, workspace: Path, **kwargs: Any) -> Mapping[str, Any]: ...

class ReferenceAPI(Protocol):
    """W2 Reference Capability's one closed operation."""

    def handle(self, request: dict[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class _HandleRecord:
    kind: str
    value: Any
    attempt_id: int | None
    reusable: bool
    consumed: bool = False


class OpaqueHandleRegistry:
    """Run-private capability registry with explicit scope and replay denial."""

    def __init__(self) -> None:
        self._records: dict[str, _HandleRecord] = {}

    def issue(
        self,
        kind: str,
        value: Any,
        *,
        attempt_id: int | None = None,
        reusable: bool = True,
    ) -> str:
        """Issue a fresh opaque handle; the handle contains no authority data."""

        while True:
            token = "h:" + "".join(secrets.choice(_HANDLE_ALPHABET) for _ in range(32))
            if token not in self._records:
                break
        self._records[token] = _HandleRecord(kind, value, attempt_id, reusable)
        return token

    def resolve(
        self,
        handle: str,
        kind: str,
        *,
        attempt_id: int | None = None,
        consume: bool = False,
    ) -> Any:
        """Resolve one handle and reject wrong kind, scope, or replay."""

        record = self._records.get(handle)
        if record is None or record.kind != kind:
            raise SupervisorError("invalid_handle")
        if attempt_id is not None:
            if record.attempt_id is not None and record.attempt_id != attempt_id:
                raise SupervisorError("stale_handle")
            if record.attempt_id is None and kind not in {"workspace", "reference"}:
                # A trusted operation may be registered before an Attempt is
                # opened; the first use binds it to that Attempt.  It can
                # never subsequently cross into another Attempt.
                record = _HandleRecord(
                    record.kind,
                    record.value,
                    attempt_id,
                    record.reusable,
                    record.consumed,
                )
                self._records[handle] = record
        if record.consumed:
            raise SupervisorError("replayed_handle")
        if consume:
            if record.reusable:
                self._records[handle] = _HandleRecord(
                    record.kind,
                    record.value,
                    record.attempt_id,
                    record.reusable,
                    True,
                )
            else:
                self._records.pop(handle, None)
        return record.value

    def revoke_attempt(self, attempt_id: int) -> None:
        """Invalidate all non-public capabilities from one completed Attempt."""

        for handle, record in list(self._records.items()):
            if record.attempt_id == attempt_id and record.kind not in {
                "workspace",
                "reference",
            }:
                self._records.pop(handle, None)

    def bind(self, handle: str, value: Any) -> None:
        """Bind a freshly issued handle to a value without exposing the record map."""

        record = self._records.get(handle)
        if record is None:
            raise SupervisorError("invalid_handle")
        self._records[handle] = _HandleRecord(
            record.kind, value, record.attempt_id, record.reusable, record.consumed
        )


@dataclass(frozen=True)
class _CandidateOperation:
    argv: tuple[str, ...]
    timeout_seconds: int
    environment: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _CanonicalBuildRequest:
    """Trusted request to run one canonical-build invocation for the Attempt.

    The request carries no argv, entrypoint, environment, tool source, or
    output-directory selection; those are all fixed by the supervisor
    itself when the request is executed.  The Attempt id is the only
    piece of scope, and it never leaves the trusted boundary.
    """

    attempt_id: int


@dataclass(frozen=True)
class _AttemptContext:
    attempt_id: int
    intended_step: int
    candidate_root: Path
    attempt_handle: str
    candidate_handle: str


@dataclass
class _AttemptCapabilities:
    """Attempt-scoped single-use canonical-build bundle with a spend budget.

    The Agent never gets an argv or environment; it gets one opaque bundle
    handle and a per-Attempt budget the supervisor decrements each time
    ``run_candidate_tool`` accepts the bundle.  The budget preserves the
    prior contract of a small bounded number of tool invocations per
    Attempt without preallocating identical, replaceable operation
    handles that would each still need argv material to be resolvable.
    """

    attempt_id: int
    intended_step: int
    remaining_invocations: int


@dataclass(frozen=True)
class CandidateSubmission:
    """Closed candidate submission binding for the W1 facade.

    A ``CandidateSubmission`` names one Attempt-scoped candidate tree the
    W1 facade must consume through its ``_from_candidate`` operations.
    The Agent never sees or constructs this value; the supervisor derives
    it from validated opaque handles and the intended step branch.
    """

    attempt_id: int
    intended_step: int
    kind: str
    candidate_root: Path


def _load_workspace_api() -> ModuleType:
    helper = (
        Path(__file__).resolve().parents[2]
        / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace"
    )
    if str(helper) not in sys.path:
        sys.path.insert(0, str(helper))
    module_name = "_mesh_to_cad_workspace_for_pilot"
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(module_name, helper / "workspace.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Workspace facade is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_reference_type() -> type[Any]:
    _ensure_shipped_package(_MESHSCOPE_SRC, "meshscope")
    from meshscope import ReferenceCapability

    return ReferenceCapability


def _load_agent_surface_type() -> type[Any]:
    source = (
        Path(__file__).resolve().parents[2]
        / "skills/mesh-to-cad/scripts/mesh-to-cad-agent-surface/handler.py"
    )
    module_name = "mesh_to_cad_agent_surface_handler"
    loaded = sys.modules.get(module_name)
    if loaded is None:
        spec = importlib.util.spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            raise RuntimeError("Agent Surface handler is unavailable")
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = loaded
        sys.modules["handler"] = loaded
        spec.loader.exec_module(loaded)
    else:
        sys.modules["handler"] = loaded
    return loaded.AgentSurface


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_relative(root: Path, value: Path) -> Path:
    """Resolve one candidate path without following a symlink component."""

    root = root.resolve()
    candidate = value if value.is_absolute() else root / value
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise SupervisorError("candidate_path_escape") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise SupervisorError("candidate_path_escape")
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                raise SupervisorError("candidate_symlink_denied")
        except OSError as exc:
            raise SupervisorError("candidate_path_unavailable") from exc
    return relative


def _open_nofollow(
    path: Path,
    *,
    directory: bool = False,
    dir_fd: int | None = None,
) -> int:
    flags = os.O_RDONLY
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        return os.open(path, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise SupervisorError("candidate_path_unavailable") from exc


def _copy_descriptor(source_fd: int, target: Path) -> None:
    """Copy one descriptor-bounded regular file and verify stable metadata."""

    target_created = False
    try:
        before = os.fstat(source_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > MAX_CANDIDATE_FILE_BYTES
        ):
            raise SupervisorError("candidate_file_invalid")
        target.parent.mkdir(parents=True, exist_ok=True)
        target_fd = os.open(
            target,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        target_created = True
        try:
            copied = 0
            first_digest = hashlib.sha256()
            while True:
                remaining = before.st_size - copied
                if remaining < 0:
                    raise SupervisorError("candidate_file_invalid")
                chunk = os.read(source_fd, min(1024 * 1024, remaining)) if remaining else b""
                if not chunk:
                    break
                if len(chunk) > remaining:
                    raise SupervisorError("candidate_file_invalid")
                copied += len(chunk)
                if copied > MAX_CANDIDATE_FILE_BYTES:
                    raise SupervisorError("candidate_file_invalid")
                first_digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(target_fd, view)
                    if written <= 0:
                        raise OSError("short candidate copy")
                    view = view[written:]
            after = os.fstat(source_fd)
            os.lseek(source_fd, 0, os.SEEK_SET)
            second_digest = hashlib.sha256()
            reread = 0
            while True:
                remaining = before.st_size - reread
                if remaining < 0:
                    raise SupervisorError("candidate_file_invalid")
                chunk = os.read(source_fd, min(1024 * 1024, remaining)) if remaining else b""
                if not chunk:
                    break
                if len(chunk) > remaining:
                    raise SupervisorError("candidate_file_invalid")
                second_digest.update(chunk)
                reread += len(chunk)
            stable = os.fstat(source_fd)
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_nlink != after.st_nlink
                or before.st_size != after.st_size
                or before.st_mode != after.st_mode
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
                or after.st_dev != stable.st_dev
                or after.st_ino != stable.st_ino
                or after.st_nlink != stable.st_nlink
                or after.st_size != stable.st_size
                or after.st_mode != stable.st_mode
                or after.st_mtime_ns != stable.st_mtime_ns
                or after.st_ctime_ns != stable.st_ctime_ns
                or copied != before.st_size
                or reread != before.st_size
                or first_digest.digest() != second_digest.digest()
            ):
                raise SupervisorError("candidate_changed_during_copy")
            target_stat = os.fstat(target_fd)
            if target_stat.st_size != copied:
                raise SupervisorError("candidate_copy_failed")
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
    except SupervisorError:
        if target_created:
            target.unlink(missing_ok=True)
        raise
    except OSError as exc:
        if target_created:
            target.unlink(missing_ok=True)
        raise SupervisorError("candidate_copy_failed") from exc


def _copy_candidate_file(source: Path, target: Path) -> None:
    """Open a candidate file once with no-follow semantics before copying."""

    descriptor = _open_nofollow(source)
    try:
        _copy_descriptor(descriptor, target)
    finally:
        os.close(descriptor)


def _bounded_process_detail(value: bytes) -> tuple[str, str]:
    digest = _sha256_bytes(value)
    if len(value) > MAX_OPERATION_OUTPUT_BYTES:
        value = value[:MAX_OPERATION_OUTPUT_BYTES]
    return digest, value.decode("utf-8", errors="replace")


def _candidate_sandbox_argv(
    operation: _CandidateOperation,
    candidate_root: Path,
    runtime_dir: Path,
    canonical_build_root: Path | None = None,
    cadgen_runtime_root: Path | None = None,
) -> list[str]:
    """Run one registered operation in a minimal Linux candidate mount."""

    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise SupervisorError("candidate_sandbox_unavailable")
    argv = [
        bwrap,
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-net",
        "--cap-drop",
        "ALL",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/candidate",
        "--ro-bind",
        os.fspath(runtime_dir),
        "/runtime",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
    ]
    if canonical_build_root is not None and cadgen_runtime_root is not None:
        argv.extend(
            (
                "--dir",
                str(_BUILDER_INTERNAL_PATH),
                "--dir",
                str(_BUILDER_INTERNAL_PATH / "packages"),
                "--ro-bind",
                os.fspath(canonical_build_root),
                str(_BUILDER_INTERNAL_PATH / "canonical-build"),
                "--ro-bind",
                os.fspath(cadgen_runtime_root),
                str(_BUILDER_INTERNAL_PATH / "packages/cadgen"),
            )
        )
    for path in _CANDIDATE_SYSTEM_MOUNTS:
        if Path(path).exists():
            argv.extend(("--ro-bind", path, path))
    argv.extend(
        (
            "--bind",
            os.fspath(candidate_root),
            "/candidate",
            "--chdir",
            "/candidate",
            "--die-with-parent",
            "--",
            *operation.argv,
        )
    )
    return argv


class WorkspaceSupervisor:
    """Concrete W3 port implementation owned by the trusted pilot runner."""

    def __init__(
        self,
        workspace: Path,
        *,
        bind_reference: bool = False,
        candidate_root: Path | None = None,
        staging_dir: Path | None = None,
        workspace_api: WorkspaceAPI | None = None,
        reference_factory: Callable[[str, Path], ReferenceAPI] | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
        rebuild_entrypoint: Path | None = None,
        geometry_entrypoint: Path | None = None,
        tool_registry: Path | None = None,
        candidate_runtime: Path | None = None,
        trusted_tools_root: Path | None = None,
        step_zero_evidence_provider: Callable[..., Any] | None = None,
        repair_evidence_provider: Callable[..., Any] | None = None,
    ) -> None:
        raw_workspace = Path(workspace)
        if raw_workspace.is_symlink():
            raise SupervisorError("invalid_workspace")
        self.workspace = raw_workspace.resolve()
        if not self.workspace.is_dir():
            raise SupervisorError("invalid_workspace")
        self.registry = OpaqueHandleRegistry()
        self._cancel_event = threading.Event()
        self._active_calls = 0
        self._active_calls_condition = threading.Condition()
        self._active_processes: set[subprocess.Popen[Any]] = set()
        self._active_processes_lock = threading.Lock()
        self._cancellation_confirmed = False
        self._closed = False
        run_token = "".join(secrets.choice(_HANDLE_ALPHABET) for _ in range(24))
        self.candidate_root = self._external_root(
            candidate_root or self.workspace.parent / f".agent-candidate-{run_token}"
        )
        self.staging_dir = self._external_root(
            staging_dir or self.workspace.parent / f".agent-staging-{run_token}"
        )
        self._staging_root = self.staging_dir / ".supervisor-staging"
        try:
            self.candidate_root.mkdir(parents=True, exist_ok=False, mode=0o700)
            self.staging_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.staging_dir.is_symlink() or not self.staging_dir.is_dir():
                raise OSError("Agent staging directory is not a directory")
            self.staging_dir.chmod(0o700)
            if self._staging_root.is_symlink():
                raise OSError("terminal staging directory is a symlink")
            if self._staging_root.exists():
                shutil.rmtree(self._staging_root)
            self._staging_root.mkdir(parents=False, exist_ok=False, mode=0o700)
        except OSError as exc:
            raise SupervisorError("supervisor_storage_unavailable") from exc

        try:
            if workspace_api is None:
                workspace_api = _load_workspace_api()  # type: ignore[assignment]
            self.workspace_api = workspace_api
            scope_factory = getattr(workspace_api, "ExecutionScope", None)
            self._execution_scope = scope_factory() if scope_factory is not None else None
            self._command_runner = command_runner or subprocess.run
            self._rebuild_entrypoint = rebuild_entrypoint
            self._geometry_entrypoint = geometry_entrypoint
            self._tool_registry = tool_registry
            self._step_zero_evidence_provider = step_zero_evidence_provider
            self._repair_evidence_provider = repair_evidence_provider
            self.candidate_runtime: Path | None = None
            if candidate_runtime is not None:
                raw_runtime = Path(candidate_runtime)
                if raw_runtime.is_symlink() or not raw_runtime.is_dir():
                    raise SupervisorError("candidate_runtime_unavailable")
                runtime = raw_runtime.resolve()
                try:
                    runtime.relative_to(self.workspace)
                except ValueError:
                    pass
                else:
                    raise SupervisorError("candidate_runtime_unavailable")
                if runtime.stat().st_mode & 0o222:
                    raise SupervisorError("candidate_runtime_unavailable")
                python_path = runtime / "bin/python"
                if not python_path.exists() or python_path.is_dir():
                    raise SupervisorError("candidate_runtime_unavailable")
                try:
                    resolved_python = python_path.resolve()
                    try:
                        resolved_python.relative_to(runtime)
                    except ValueError:
                        resolved_python.relative_to(Path("/usr"))
                except ValueError as exc:
                    raise SupervisorError("candidate_runtime_unavailable") from exc
                self.candidate_runtime = runtime
            self.canonical_build_root: Path | None = None
            self.cadgen_runtime_root: Path | None = None
            if trusted_tools_root is not None:
                tool_root = Path(trusted_tools_root).resolve()
                try:
                    validate_trusted_tools(tool_root)
                except TrustedToolsError as exc:
                    raise SupervisorError("trusted_tools_unavailable") from exc
                canonical_build_root = (tool_root / CANONICAL_BUILD_RELATIVE).resolve()
                cadgen_runtime_root = (tool_root / CADGEN_RUNTIME_RELATIVE).resolve()
                try:
                    canonical_build_root.relative_to(self.workspace)
                    raise SupervisorError("trusted_tools_unavailable")
                except ValueError:
                    pass
                self.canonical_build_root = canonical_build_root
                self.cadgen_runtime_root = cadgen_runtime_root
            self._attempts: dict[int, _AttemptContext] = {}

            self.workspace_handle = self.registry.issue("workspace", self.workspace)
            self.reference_handle: str | None = None
            self._reference: ReferenceAPI | None = None
            if bind_reference:
                binding = self._bind_workspace_canonical_reference()
                path = binding["path"]
                self.reference_handle = self.registry.issue("reference", path)
                factory = reference_factory or _load_reference_type()
                try:
                    # W2's response id is the Agent-visible opaque reference handle.
                    self._reference = factory(self.reference_handle, path)
                except Exception as exc:
                    raise SupervisorError("invalid_reference") from exc
        except Exception:
            self._discard_private_storage()
            raise

    def _bind_workspace_canonical_reference(self) -> Mapping[str, Any]:
        """Ask the trusted Workspace facade for the Canonical Reference binding.

        The facade proves that the on-disk Canonical Reference matches the
        Workspace's committed identity before returning.  Callers therefore
        cannot inject a reference path or claim a foreign identity; the
        Workspace state established during outer preparation is the only
        source of truth for both location and identity.
        """

        reader = getattr(self.workspace_api, "read_canonical_reference_binding", None)
        if reader is None:
            raise SupervisorError("invalid_reference")
        try:
            binding = reader(self.workspace)
        except Exception as exc:
            raise SupervisorError("invalid_reference") from exc
        if not isinstance(binding, Mapping):
            raise SupervisorError("invalid_reference")
        raw_path = binding.get("path")
        ply_sha256 = binding.get("reference_ply_sha256")
        canonical_sha256 = binding.get("canonical_reference_sha256")
        if (
            raw_path is None
            or not isinstance(ply_sha256, str)
            or not isinstance(canonical_sha256, str)
        ):
            raise SupervisorError("invalid_reference")
        try:
            path = Path(raw_path)
        except TypeError as exc:
            raise SupervisorError("invalid_reference") from exc
        if path.is_symlink() or not path.is_file():
            raise SupervisorError("invalid_reference")
        resolved = path.resolve()
        try:
            resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise SupervisorError("invalid_reference") from exc
        return {
            "path": resolved,
            "reference_ply_sha256": ply_sha256,
            "canonical_reference_sha256": canonical_sha256,
        }

    def _external_root(self, value: Path) -> Path:
        raw = Path(value)
        if raw.is_symlink():
            raise SupervisorError("supervisor_storage_symlink")
        root = raw.resolve()
        try:
            root.relative_to(self.workspace)
        except ValueError:
            return root
        raise SupervisorError("supervisor_storage_must_be_external")

    def close(self) -> None:
        """Cancel work, confirm termination, then remove private candidate bytes."""

        if self._closed:
            return
        self.cancel()
        self._discard_private_storage()
        self._closed = True

    @property
    def cancellation_confirmed(self) -> bool:
        """Whether all active Agent work has terminated."""

        return self._cancellation_confirmed

    def cancel(self) -> None:
        """Cancel active Workspace commands and wait for handler calls to drain."""

        self._cancel_event.set()
        self._cancel_owned_processes()
        callback = getattr(self.workspace_api, "cancel_active_commands", None)
        if callback is not None:
            try:
                cancelled = (
                    callback(self._execution_scope)
                    if self._execution_scope is not None
                    else callback()
                )
                if cancelled is False:
                    raise SupervisorError("cancellation_incomplete")
            except SupervisorError:
                raise
            except Exception as exc:
                raise SupervisorError("cancellation_incomplete") from exc
        deadline = time.monotonic() + 5.0
        with self._active_calls_condition:
            while self._active_calls and time.monotonic() < deadline:
                self._active_calls_condition.wait(timeout=0.05)
            if self._active_calls:
                raise SupervisorError("cancellation_incomplete")
        if (
            self._execution_scope is not None
            and self._execution_scope.has_live_processes()
        ):
            raise SupervisorError("cancellation_incomplete")
        self._cancellation_confirmed = True

    def _cancel_owned_processes(self) -> None:
        with self._active_processes_lock:
            processes = tuple(self._active_processes)
        for process in processes:
            if process.poll() is None and os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    raise SupervisorError("cancellation_incomplete")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and any(process.poll() is None for process in processes):
            time.sleep(0.05)
        for process in processes:
            if process.poll() is None:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except PermissionError:
                        raise SupervisorError("cancellation_incomplete")
                else:
                    process.kill()
        for process in processes:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired as exc:
                raise SupervisorError("cancellation_incomplete") from exc

    def _discard_private_storage(self) -> None:
        """Best-effort cleanup for startup or workload failure."""

        try:
            if self.candidate_root.exists() or self.candidate_root.is_symlink():
                if self.candidate_root.is_symlink():
                    self.candidate_root.unlink()
                else:
                    shutil.rmtree(self.candidate_root)
            if self._staging_root.exists() or self._staging_root.is_symlink():
                if self._staging_root.is_symlink():
                    self._staging_root.unlink()
                else:
                    shutil.rmtree(self._staging_root)
        except OSError as exc:
            raise SupervisorError("supervisor_cleanup_failed") from exc

    def bootstrap_handles(self) -> dict[str, str]:
        """Return only opaque bootstrap capabilities to the outer adapter."""

        result = {"workspace_handle": self.workspace_handle}
        if self.reference_handle is not None:
            result["reference_handle"] = self.reference_handle
        return result

    def agent_bootstrap_contract(self) -> dict[str, Any]:
        """Publish only run-level capabilities before the first Attempt exists."""

        plan = self.register_plan(self.candidate_root / "plan.json")
        attempts_per_step = int(
            getattr(self.workspace_api, "MAX_ATTEMPTS_PER_STEP", MAX_ATTEMPTS_PER_STEP)
        )
        repair_cycles = int(
            getattr(self.workspace_api, "MAX_REPAIR_CYCLES", MAX_CYCLES)
        )
        if attempts_per_step <= 0 or repair_cycles < 0:
            raise SupervisorError("workspace_contract_violation")
        return {
            "schema": "mesh-to-cad.agent-bootstrap/1",
            "workspace_handle": self.workspace_handle,
            "reference_handle": self.reference_handle,
            "plan_handle": plan,
            "attempt_budget": {
                "attempts_per_step": attempts_per_step,
                "repair_steps": repair_cycles,
                "maximum_attempts": attempts_per_step * (repair_cycles + 1),
            },
            "selection_handle": self.register_selection(
                self.candidate_root / "selection.json"
            ),
            "notes_handle": self.register_notes(self.candidate_root / "notes.md"),
        }

    def agent_surface(self) -> Any:
        """Build the W3 shared handler over this supervisor's seven ports."""

        return _load_agent_surface_type()(self)

    def register_candidate_path(
        self,
        path: Path,
        *,
        kind: str = "candidate_file",
        attempt_handle: str | None = None,
        attempt_id: int | None = None,
        reusable: bool = True,
    ) -> str:
        """Register a trusted candidate path without exposing it to the Agent."""

        if attempt_id is not None and (
            type(attempt_id) is not int or attempt_id < 1
        ):
            raise SupervisorError("invalid_attempt")
        if attempt_handle is not None:
            context = self.registry.resolve(attempt_handle, "attempt")
            if attempt_id is not None and attempt_id != context.attempt_id:
                raise SupervisorError("stale_handle")
            attempt_id = context.attempt_id
        relative = _safe_relative(self.candidate_root, Path(path))
        candidate = self.candidate_root / relative
        if candidate.is_symlink():
            raise SupervisorError("candidate_symlink_denied")
        return self.registry.issue(
            kind,
            candidate,
            attempt_id=attempt_id,
            reusable=reusable,
        )

    def register_plan(
        self,
        path: Path,
        *,
        attempt_handle: str | None = None,
    ) -> str:
        """Register an Agent-authored plan in the candidate-only tree."""

        return self.register_candidate_path(path, kind="plan", attempt_handle=attempt_handle)

    def register_selection(self, path: Path) -> str:
        """Register one Agent-authored final selection document."""

        return self.register_candidate_path(path, kind="selection")

    def register_notes(self, path: Path) -> str:
        """Register one Agent-authored final notes document."""

        return self.register_candidate_path(path, kind="notes")

    def register_operation(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: int = 1800,
        environment: Mapping[str, str] | None = None,
        attempt_handle: str | None = None,
        attempt_id: int | None = None,
    ) -> str:
        """Register one fixed candidate operation and issue a one-shot handle."""

        if not argv or any(type(item) is not str or not item or "\0" in item for item in argv):
            raise SupervisorError("invalid_operation")
        if not 1 <= timeout_seconds <= MAX_OPERATION_TIMEOUT_SECONDS:
            raise SupervisorError("invalid_operation")
        for item in argv[1:]:
            normalized = item.replace("\\", "/").lower()
            if Path(item).is_absolute() or ".." in PurePosixPath(normalized).parts:
                raise SupervisorError("invalid_operation")
        variables = dict(environment or {})
        if set(variables) - _SAFE_ENV_KEYS:
            raise SupervisorError("invalid_operation")
        if any("\0" in key or "\0" in value for key, value in variables.items()):
            raise SupervisorError("invalid_operation")
        if "PATH" in variables and variables["PATH"] != "/runtime/bin":
            raise SupervisorError("invalid_operation")
        if "PYTHONHOME" in variables and variables["PYTHONHOME"] != "/runtime":
            raise SupervisorError("invalid_operation")
        if "PYTHONNOUSERSITE" in variables and variables["PYTHONNOUSERSITE"] != "1":
            raise SupervisorError("invalid_operation")
        if set(variables) & {"HOME", "TMPDIR"}:
            raise SupervisorError("invalid_operation")
        if attempt_id is not None and (
            type(attempt_id) is not int or attempt_id < 1
        ):
            raise SupervisorError("invalid_attempt")
        if attempt_handle is not None:
            context = self.registry.resolve(attempt_handle, "attempt")
            if attempt_id is not None and attempt_id != context.attempt_id:
                raise SupervisorError("stale_handle")
            attempt_id = context.attempt_id
        operation = _CandidateOperation(
            tuple(argv), timeout_seconds, tuple(sorted(variables.items()))
        )
        handle = self.registry.issue(
            "operation",
            operation,
            attempt_id=attempt_id,
            reusable=False,
        )
        return handle

    def _workspace(self, handle: str) -> Path:
        return self.registry.resolve(handle, "workspace")

    def _attempt(
        self,
        workspace_handle: str,
        attempt_handle: str,
        candidate_handle: str,
    ) -> _AttemptContext:
        self._workspace(workspace_handle)
        context = self.registry.resolve(attempt_handle, "attempt")
        candidate = self.registry.resolve(
            candidate_handle, "candidate", attempt_id=context.attempt_id
        )
        if candidate != context.candidate_root:
            raise SupervisorError("stale_handle")
        if self._attempts.get(context.attempt_id) != context:
            raise SupervisorError("stale_handle")
        return context

    @staticmethod
    def _status_state(status: Mapping[str, Any]) -> str:
        if status.get("final_delivery_present") is True or status.get("final_delivery") is not None:
            return "terminal"
        if status.get("head_steps"):
            return "preterminal"
        return "ready"

    def workspace_status(self, workspace_handle: str) -> Mapping[str, Any]:
        workspace = self._workspace(workspace_handle)
        try:
            status = self.workspace_api.workspace_status(workspace)
        except Exception as exc:
            raise SupervisorError("workspace_unavailable") from exc
        completed = int(status.get("completed_cycles", 0))
        total_attempts = int(status.get("total_attempts", 0))
        failures = int(status.get("tool_failures", 0))
        if completed < 0 or completed > MAX_CYCLES or total_attempts < 0 or failures < 0:
            raise SupervisorError("workspace_contract_violation")
        state = self._status_state(status)
        remaining_attempts = status.get("remaining_attempts", MAX_ATTEMPTS_PER_STEP)
        remaining_tool_failures = status.get("remaining_tool_failures")
        if remaining_tool_failures is None:
            remaining_tool_failures = max(0, 2 - failures)
        if (
            type(remaining_attempts) is not int
            or remaining_attempts < 0
            or type(remaining_tool_failures) is not int
            or remaining_tool_failures < 0
        ):
            raise SupervisorError("workspace_contract_violation")
        next_intents = ["workspace_status"]
        if state != "terminal":
            next_intents.append("observe_reference")
            next_intents.append("start_attempt")
            if state == "preterminal":
                next_intents.append("select_and_finalize")
        return {
            "state": state,
            "workspace_identity": workspace_handle,
            "budgets": {
                "remaining_cycles": max(0, MAX_CYCLES - completed),
                "remaining_attempts": remaining_attempts,
                "remaining_tool_failures": remaining_tool_failures,
            },
            "permitted_next_intents": next_intents,
        }

    def start_attempt(
        self,
        workspace_handle: str,
        plan_handle: str,
        parent_step_handle: str | None,
    ) -> Mapping[str, Any]:
        workspace = self._workspace(workspace_handle)
        plan = self.registry.resolve(plan_handle, "plan")
        _safe_relative(self.candidate_root, plan)
        if parent_step_handle is None:
            from_step: int | None = None
        else:
            if type(parent_step_handle) is not str:
                raise SupervisorError("invalid_request")
            resolved = self.registry.resolve(parent_step_handle, "step")
            if type(resolved) is not int or not 0 <= resolved < MAX_ATTEMPT_STEP:
                raise SupervisorError("invalid_handle")
            from_step = resolved
        if self._attempts:
            raise SupervisorError("attempt_already_active")
        staged_plan = self._staging_root / f"plan-{secrets.token_hex(12)}.json"
        try:
            _copy_candidate_file(plan, staged_plan)
            status = self.workspace_api.workspace_status(workspace)
            raw_intended_step = status.get("next_intended_step", 0)
            if (
                type(raw_intended_step) is not int
                or raw_intended_step < 0
                or raw_intended_step > MAX_ATTEMPT_STEP
            ):
                raise SupervisorError("workspace_contract_violation")
            intended_step = raw_intended_step
            if from_step is None and intended_step != 0:
                raise SupervisorError("parent_mismatch")
            if from_step is not None and intended_step == 0:
                raise SupervisorError("parent_mismatch")
            if from_step is not None and from_step >= intended_step:
                raise SupervisorError("stale_handle")
            document = self.workspace_api.begin_attempt(
                workspace,
                staged_plan,
                intended_step=intended_step,
                from_step=from_step,
            )
            attempt_id = int(document["attempt"])
        except SupervisorError:
            raise
        except Exception as exc:
            raise SupervisorError("attempt_rejected") from exc
        finally:
            staged_plan.unlink(missing_ok=True)
        candidate = self._reset_current_work_tree()
        if from_step is not None:
            seeder = getattr(
                self.workspace_api, "seed_repair_source_from_parent_step", None
            )
            if seeder is None:
                self._discard_current_work_tree()
                raise SupervisorError("workspace_contract_violation")
            try:
                seeder(
                    workspace,
                    attempt=attempt_id,
                    from_step=from_step,
                    destination=candidate,
                )
            except Exception as exc:
                self._discard_current_work_tree()
                raise SupervisorError("repair_source_seed_failed") from exc
        attempt_handle = self.registry.issue("attempt", None, attempt_id=attempt_id)
        candidate_handle = self.registry.issue(
            "candidate", candidate, attempt_id=attempt_id
        )
        context = _AttemptContext(
            attempt_id,
            intended_step,
            candidate,
            attempt_handle,
            candidate_handle,
        )
        self.registry.bind(attempt_handle, context)
        self._attempts[attempt_id] = context
        capability_bundle_handle = self._issue_attempt_capabilities(context)
        return {
            "state": "started",
            "attempt_handle": attempt_handle,
            "candidate_handle": candidate_handle,
            "capability_bundle_handle": capability_bundle_handle,
            "permitted_next_intents": [
                "run_candidate_tool",
                "submit_step_zero" if intended_step == 0 else "submit_repair",
                "workspace_status",
            ],
        }

    def _issue_attempt_capabilities(self, context: _AttemptContext) -> str:
        """Bind one single-use canonical-build bundle to the actual Attempt.

        The bundle is issued regardless of whether a builder bundle has
        been mounted; if it has not, the trusted canonical-build path
        fails closed on the first invocation.  Only run-level capability
        is allocated here.  Evidence selection is not an Agent-visible
        capability in the seven-intent surface; the W1 facade discovers
        candidate-authored evidence from the trusted candidate tree
        during ``_from_candidate`` publication.
        """

        bundle = _AttemptCapabilities(
            context.attempt_id,
            context.intended_step,
            MAX_CANONICAL_BUILD_ATTEMPTS,
        )
        return self.registry.issue(
            "attempt_capabilities",
            bundle,
            attempt_id=context.attempt_id,
        )

    def _begin_active_call(self) -> None:
        with self._active_calls_condition:
            if self._cancel_event.is_set() or self._closed:
                raise SupervisorError("supervisor_cancelled")
            self._active_calls += 1

    def _end_active_call(self) -> None:
        with self._active_calls_condition:
            self._active_calls -= 1
            self._active_calls_condition.notify_all()

    def _execute_canonical_build(
        self,
        context: _AttemptContext,
        request: _CanonicalBuildRequest,
    ) -> Mapping[str, Any]:
        """Run one trusted canonical-build invocation for the current Attempt.

        The invocation targets the fixed candidate-relative work tree the
        Agent has authored under.  The tool's argv, entrypoint, output
        directory, and environment are all supervisor-owned; the Agent
        cannot influence them.  On success the trusted supervisor
        atomically publishes the tool-produced measurement.glb to the
        fixed ``candidate.glb`` W1 consumes.
        """

        if self.canonical_build_root is None or self.cadgen_runtime_root is None:
            raise SupervisorError("trusted_tools_unavailable")
        if self._command_runner is subprocess.run and self.candidate_runtime is None:
            raise SupervisorError("candidate_runtime_unavailable")
        work = context.candidate_root
        candidate_glb = work / CANDIDATE_PUBLISHED_MEASUREMENT_NAME
        if candidate_glb.is_symlink() or candidate_glb.exists():
            raise SupervisorError("candidate_glb_preexisting")

        source_root = work / str(_SOURCE_INTERNAL_ROOT)
        source_module = source_root / _SOURCE_MODULE_NAME
        if source_root.is_symlink() or not source_root.is_dir():
            raise SupervisorError("candidate_source_missing")
        if source_module.is_symlink() or not source_module.is_file():
            raise SupervisorError("candidate_source_missing")
        sidecars = self._collect_declared_sidecars(source_root)
        source_digests = self._snapshot_source_digests(source_root, sidecars)

        output_token = "".join(
            secrets.choice(_HANDLE_ALPHABET) for _ in range(20)
        )
        output_relative = f"{_TRUSTED_OUTPUT_PREFIX}{output_token}"
        output_dir = work / output_relative
        if output_dir.exists() or output_dir.is_symlink():
            raise SupervisorError("candidate_execution_failed")
        try:
            output_dir.mkdir(parents=False, exist_ok=False, mode=0o700)
        except OSError as exc:
            raise SupervisorError("candidate_execution_failed") from exc

        argv: list[str] = [
            "/runtime/bin/python",
            str(_BUILDER_INTERNAL_PATH / _BUILDER_TOOL_ENTRYPOINT),
            "build",
            "--source",
            (_SOURCE_INTERNAL_ROOT / _SOURCE_MODULE_NAME).as_posix(),
            "--output-dir",
            output_relative,
        ]
        for sidecar_relative in sidecars:
            argv.extend(("--input", sidecar_relative))
        operation = _CandidateOperation(
            tuple(argv),
            MAX_CANONICAL_BUILD_TIMEOUT_SECONDS,
            (),
        )

        try:
            exit_code, stdout, stderr, command_document = self._invoke_operation(
                operation, context
            )
        except SupervisorError:
            self._discard_trusted_output(output_dir)
            raise
        stdout_digest, _ = _bounded_process_detail(stdout)
        stderr_digest, _ = _bounded_process_detail(stderr)
        try:
            if exit_code != 0:
                raise SupervisorError("candidate_tool_failed")
            self._reverify_source_digests(source_root, sidecars, source_digests)
            self._validate_canonical_output(output_dir, sidecars, source_digests)
            self._publish_candidate_glb(output_dir, candidate_glb)
        except SupervisorError:
            self._discard_trusted_output(output_dir)
            try:
                if candidate_glb.is_symlink():
                    candidate_glb.unlink()
                elif candidate_glb.exists():
                    candidate_glb.unlink()
            except OSError:
                pass
            raise
        result_handle = self.registry.issue(
            "result",
            {
                "exit_code": exit_code,
                "command": command_document.get("command"),
                "stdout_sha256": stdout_digest,
                "stderr_sha256": stderr_digest,
                "adapter": _CANONICAL_ADAPTER_ID,
            },
            attempt_id=context.attempt_id,
        )
        return {
            "state": "completed",
            "candidate_handle": context.candidate_handle,
            "result_handle": result_handle,
            "permitted_next_intents": [
                "run_candidate_tool",
                "submit_step_zero"
                if context.intended_step == 0
                else "submit_repair",
                "workspace_status",
            ],
        }

    def _collect_declared_sidecars(self, source_root: Path) -> tuple[str, ...]:
        """Enumerate regular sidecar files under source/ other than model.py."""

        sidecars: list[str] = []
        for parent, dirnames, filenames in os.walk(source_root, followlinks=False):
            parent_path = Path(parent)
            dirnames.sort()
            dirnames[:] = [
                name
                for name in dirnames
                if not (parent_path / name).is_symlink()
            ]
            for name in sorted(filenames):
                path = parent_path / name
                if path.is_symlink():
                    raise SupervisorError("candidate_source_invalid")
                if path == source_root / _SOURCE_MODULE_NAME:
                    continue
                relative = (
                    PurePosixPath(_SOURCE_INTERNAL_ROOT.as_posix())
                    / path.relative_to(source_root).as_posix()
                ).as_posix()
                sidecars.append(relative)
                if len(sidecars) > MAX_DECLARED_SIDECARS:
                    raise SupervisorError("candidate_source_invalid")
                info = path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise SupervisorError("candidate_source_invalid")
                if info.st_size > MAX_SIDECAR_BYTES:
                    raise SupervisorError("candidate_source_invalid")
        return tuple(sidecars)

    def _snapshot_source_digests(
        self, source_root: Path, sidecars: tuple[str, ...]
    ) -> dict[str, str]:
        digests: dict[str, str] = {}
        module_relative = (
            _SOURCE_INTERNAL_ROOT / _SOURCE_MODULE_NAME
        ).as_posix()
        digests[module_relative] = _sha256_bytes(
            (source_root / _SOURCE_MODULE_NAME).read_bytes()
        )
        for relative in sidecars:
            interior = PurePosixPath(relative).relative_to(_SOURCE_INTERNAL_ROOT.as_posix())
            path = source_root / interior.as_posix()
            digests[relative] = _sha256_bytes(path.read_bytes())
        return digests

    def _reverify_source_digests(
        self,
        source_root: Path,
        sidecars: tuple[str, ...],
        digests: Mapping[str, str],
    ) -> None:
        module_relative = (
            _SOURCE_INTERNAL_ROOT / _SOURCE_MODULE_NAME
        ).as_posix()
        module_path = source_root / _SOURCE_MODULE_NAME
        if module_path.is_symlink() or not module_path.is_file():
            raise SupervisorError("candidate_source_mutated")
        actual = _sha256_bytes(module_path.read_bytes())
        if actual != digests.get(module_relative):
            raise SupervisorError("candidate_source_mutated")
        for relative in sidecars:
            interior = PurePosixPath(relative).relative_to(_SOURCE_INTERNAL_ROOT.as_posix())
            path = source_root / interior.as_posix()
            if path.is_symlink() or not path.is_file():
                raise SupervisorError("candidate_source_mutated")
            if _sha256_bytes(path.read_bytes()) != digests.get(relative):
                raise SupervisorError("candidate_source_mutated")

    def _validate_canonical_output(
        self,
        output_dir: Path,
        sidecars: tuple[str, ...],
        source_digests: Mapping[str, str],
    ) -> None:
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise SupervisorError("canonical_output_invalid")
        expected = set(_CANONICAL_OUTPUT_FILES)
        observed: dict[str, tuple[int, str]] = {}
        for path in sorted(output_dir.rglob("*"), key=lambda item: item.as_posix()):
            relative = path.relative_to(output_dir).as_posix()
            if path.is_symlink():
                raise SupervisorError("canonical_output_invalid")
            if path.is_dir():
                raise SupervisorError("canonical_output_invalid")
            if relative not in expected:
                raise SupervisorError("canonical_output_invalid")
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise SupervisorError("canonical_output_invalid")
            if info.st_size > MAX_CANDIDATE_FILE_BYTES:
                raise SupervisorError("canonical_output_invalid")
            body = path.read_bytes()
            if len(body) != info.st_size:
                raise SupervisorError("canonical_output_invalid")
            observed[relative] = (len(body), _sha256_bytes(body))
        if set(observed) != expected:
            raise SupervisorError("canonical_output_invalid")
        try:
            manifest = json.loads(
                (output_dir / _CANONICAL_MANIFEST_NAME).read_text(encoding="utf-8")
            )
            recipe = json.loads(
                (output_dir / _CANONICAL_RECIPE_NAME).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SupervisorError("canonical_output_invalid") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema") != _CANONICAL_BUILD_SCHEMA
        ):
            raise SupervisorError("canonical_output_invalid")
        adapter = manifest.get("adapter")
        if (
            not isinstance(adapter, dict)
            or set(adapter) != {"id", "version"}
            or type(adapter.get("id")) is not str
            or adapter["id"] != _CANONICAL_ADAPTER_ID
            or type(adapter.get("version")) is not int
            or adapter["version"] != 1
        ):
            raise SupervisorError("canonical_output_invalid")
        if not isinstance(recipe, dict) or recipe.get("schema") != _CANONICAL_RECIPE_SCHEMA:
            raise SupervisorError("canonical_output_invalid")
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            raise SupervisorError("canonical_output_invalid")
        expected_by_path = {}
        for entry in files:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("path"), str)
                or not isinstance(entry.get("sha256"), str)
            ):
                raise SupervisorError("canonical_output_invalid")
            expected_by_path[entry["path"]] = entry["sha256"]
        for name in _CANONICAL_OUTPUT_FILES:
            if name == _CANONICAL_MANIFEST_NAME:
                continue
            declared = expected_by_path.get(name)
            if declared is None or declared != observed[name][1]:
                raise SupervisorError("canonical_output_invalid")
        recipe_inputs = recipe.get("inputs")
        if not isinstance(recipe_inputs, list) or not recipe_inputs:
            raise SupervisorError("canonical_output_invalid")
        source_relative = (
            _SOURCE_INTERNAL_ROOT / _SOURCE_MODULE_NAME
        ).as_posix()
        declared_paths: list[str] = []
        for entry in recipe_inputs:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("path"), str)
                or not isinstance(entry.get("sha256"), str)
            ):
                raise SupervisorError("canonical_output_invalid")
            declared_paths.append(entry["path"])
            if entry["path"] == source_relative:
                if entry["sha256"] != source_digests.get(source_relative):
                    raise SupervisorError("canonical_output_invalid")
        if source_relative not in declared_paths:
            raise SupervisorError("canonical_output_invalid")
        for relative in sidecars:
            if relative not in declared_paths:
                raise SupervisorError("canonical_output_invalid")

    def _publish_candidate_glb(self, output_dir: Path, candidate_glb: Path) -> None:
        source = output_dir / _CANONICAL_MEASUREMENT_NAME
        if source.is_symlink() or not source.is_file():
            raise SupervisorError("canonical_output_invalid")
        source_fd = _open_nofollow(source)
        try:
            _copy_descriptor(source_fd, candidate_glb)
        finally:
            os.close(source_fd)

    def _discard_trusted_output(self, output_dir: Path) -> None:
        try:
            if output_dir.is_symlink():
                output_dir.unlink()
            elif output_dir.exists():
                shutil.rmtree(output_dir)
        except OSError:
            pass

    def _invoke_operation(
        self,
        operation: _CandidateOperation,
        context: _AttemptContext,
    ) -> tuple[int, bytes, bytes, Mapping[str, Any]]:
        """Execute one prepared operation and return (exit, stdout, stderr, doc).

        Encapsulates the sandbox/injected-runner split so ``run_candidate_tool``
        and ``_execute_canonical_build`` share the same lifecycle and
        cancel semantics without duplicating the launcher body.
        """

        env = {
            "PATH": "/runtime/bin",
            "HOME": "/candidate/.home",
            "TMPDIR": "/candidate/.tmp",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHOME": "/runtime",
            "PYTHONNOUSERSITE": "1",
        }
        env.update(dict(operation.environment))
        for key, value in env.items():
            if key not in _SAFE_ENV_KEYS or "\0" in value:
                raise SupervisorError("invalid_operation")
            if key == "PATH" and value != "/runtime/bin":
                raise SupervisorError("invalid_operation")
            if key == "PYTHONHOME" and value != "/runtime":
                raise SupervisorError("invalid_operation")
            if key == "PYTHONNOUSERSITE" and value != "1":
                raise SupervisorError("invalid_operation")
        try:
            (context.candidate_root / ".home").mkdir(exist_ok=True)
            (context.candidate_root / ".tmp").mkdir(exist_ok=True)
            if self._command_runner is subprocess.run:
                if self.candidate_runtime is None:
                    raise SupervisorError("candidate_runtime_unavailable")
                command = _candidate_sandbox_argv(
                    operation,
                    context.candidate_root,
                    self.candidate_runtime,
                    canonical_build_root=self.canonical_build_root,
                    cadgen_runtime_root=self.cadgen_runtime_root,
                )
            else:
                command = list(operation.argv)
            if self._command_runner is subprocess.run:
                command_kwargs: dict[str, Any] = {
                    "attempt": context.attempt_id,
                    "phase": "candidate",
                    "argv": command,
                    "timeout_seconds": operation.timeout_seconds,
                    "cwd": context.candidate_root,
                    "environment": env,
                }
                if self._execution_scope is not None:
                    command_kwargs["scope"] = self._execution_scope
                command_document = self.workspace_api.run_attempt_command(
                    self.workspace,
                    **command_kwargs,
                )
                return (
                    int(command_document["exit_code"]),
                    b"",
                    b"",
                    command_document,
                )
            completed = self._command_runner(
                command,
                cwd=context.candidate_root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=operation.timeout_seconds,
                check=False,
                start_new_session=(os.name == "posix"),
            )
            if isinstance(completed, subprocess.Popen):
                process = completed
                with self._active_processes_lock:
                    self._active_processes.add(process)
                try:
                    try:
                        process_stdout, process_stderr = process.communicate(
                            timeout=operation.timeout_seconds
                        )
                        completed = subprocess.CompletedProcess(
                            command,
                            process.returncode,
                            process_stdout,
                            process_stderr,
                        )
                    except subprocess.TimeoutExpired as exc:
                        if os.name == "posix":
                            os.killpg(process.pid, signal.SIGTERM)
                        else:
                            process.terminate()
                        try:
                            process_stdout, process_stderr = process.communicate(timeout=2)
                        except subprocess.TimeoutExpired:
                            if os.name == "posix":
                                os.killpg(process.pid, signal.SIGKILL)
                            else:
                                process.kill()
                            try:
                                process_stdout, process_stderr = process.communicate(
                                    timeout=2
                                )
                            except subprocess.TimeoutExpired:
                                for stream in (process.stdout, process.stderr):
                                    if stream is not None:
                                        stream.close()
                                process_stdout, process_stderr = b"", b""
                        completed = subprocess.CompletedProcess(
                            command,
                            124,
                            process_stdout if process_stdout is not None else exc.output,
                            process_stderr if process_stderr is not None else exc.stderr,
                        )
                finally:
                    with self._active_processes_lock:
                        self._active_processes.discard(process)
            stdout = completed.stdout if isinstance(completed.stdout, bytes) else b""
            stderr = completed.stderr if isinstance(completed.stderr, bytes) else b""
            return int(completed.returncode), stdout, stderr, {"command": None}
        except subprocess.TimeoutExpired as exc:
            stdout = exc.output if isinstance(exc.output, bytes) else b""
            stderr = exc.stderr if isinstance(exc.stderr, bytes) else b""
            return 124, stdout, stderr, {"command": None}
        except (OSError, subprocess.SubprocessError) as exc:
            raise SupervisorError("candidate_execution_failed") from exc

    def run_candidate_tool(
        self,
        workspace_handle: str,
        attempt_handle: str,
        candidate_handle: str,
        operation_handle: str,
    ) -> Mapping[str, Any]:
        """Run one candidate operation while participating in cancellation."""

        self._begin_active_call()
        try:
            return self._run_candidate_tool(
                workspace_handle,
                attempt_handle,
                candidate_handle,
                operation_handle,
            )
        finally:
            self._end_active_call()

    def _run_candidate_tool(
        self,
        workspace_handle: str,
        attempt_handle: str,
        candidate_handle: str,
        operation_handle: str,
    ) -> Mapping[str, Any]:
        context = self._attempt(workspace_handle, attempt_handle, candidate_handle)
        resolved = self._resolve_operation_capability(operation_handle, context)
        if isinstance(resolved, _CanonicalBuildRequest):
            return self._execute_canonical_build(context, resolved)
        operation = resolved
        if not isinstance(operation, _CandidateOperation):
            raise SupervisorError("invalid_operation")
        exit_code, stdout, stderr, command_document = self._invoke_operation(
            operation, context
        )
        stdout_digest, _ = _bounded_process_detail(stdout)
        stderr_digest, _ = _bounded_process_detail(stderr)
        result_handle = self.registry.issue(
            "result",
            {
                "exit_code": exit_code,
                "command": command_document.get("command"),
                "stdout_sha256": stdout_digest,
                "stderr_sha256": stderr_digest,
            },
            attempt_id=context.attempt_id,
        )
        return {
            "state": "completed" if exit_code == 0 else "failed",
            "candidate_handle": context.candidate_handle,
            "result_handle": result_handle,
            "permitted_next_intents": [
                "run_candidate_tool",
                "submit_step_zero"
                if context.intended_step == 0
                else "submit_repair",
                "workspace_status",
            ],
        }

    def _resolve_operation_capability(
        self, handle: str, context: _AttemptContext
    ) -> _CandidateOperation | "_CanonicalBuildRequest":
        try:
            bundle = self.registry.resolve(
                handle, "attempt_capabilities", attempt_id=context.attempt_id
            )
        except SupervisorError:
            operation = self.registry.resolve(
                handle, "operation", attempt_id=context.attempt_id, consume=True
            )
            if not isinstance(operation, _CandidateOperation):
                raise SupervisorError("invalid_operation")
            return operation
        if not isinstance(bundle, _AttemptCapabilities):
            raise SupervisorError("invalid_handle")
        if bundle.remaining_invocations <= 0:
            raise SupervisorError("budget_violation")
        bundle.remaining_invocations -= 1
        return _CanonicalBuildRequest(attempt_id=context.attempt_id)

    def _candidate_path(
        self,
        handle: str,
        kind: str,
        context: _AttemptContext,
    ) -> Path:
        path = self.registry.resolve(handle, kind, attempt_id=context.attempt_id)
        _safe_relative(context.candidate_root, path)
        if path.is_symlink() or not path.exists():
            raise SupervisorError("candidate_path_unavailable")
        return path

    def submit_step_zero(
        self,
        workspace_handle: str,
        attempt_handle: str,
        candidate_handle: str,
    ) -> Mapping[str, Any]:
        submission = self._prepare_submission(
            workspace_handle, attempt_handle, candidate_handle, kind="step_zero"
        )
        published = self._publish_submission(submission)
        step_number = int(published["step"])
        step_handle = self.registry.issue("step", step_number)
        decision_facts = self._read_decision_facts(step_number)
        self._retire_attempt(submission.attempt_id)
        return {
            "state": "published",
            "step_handle": step_handle,
            "decision_facts": decision_facts,
            "permitted_next_intents": ["start_attempt", "select_and_finalize", "workspace_status"],
        }

    def submit_repair(
        self,
        workspace_handle: str,
        attempt_handle: str,
        candidate_handle: str,
    ) -> Mapping[str, Any]:
        submission = self._prepare_submission(
            workspace_handle, attempt_handle, candidate_handle, kind="repair"
        )
        published = self._publish_submission(submission)
        step_number = int(published["step"])
        step_handle = self.registry.issue("step", step_number)
        cycle_handle = self.registry.issue("cycle", int(published["cycle"]))
        decision_facts = self._read_decision_facts(step_number)
        self._retire_attempt(submission.attempt_id)
        return {
            "state": "published",
            "step_handle": step_handle,
            "cycle_handle": cycle_handle,
            "decision_facts": decision_facts,
            "permitted_next_intents": ["start_attempt", "select_and_finalize", "workspace_status"],
        }

    def _read_decision_facts(self, step_number: int) -> Mapping[str, Any]:
        """Fetch bounded W1-authenticated decision facts for one Measured Step.

        The supervisor never parses any Workspace authority subtree,
        measurement document, preview asset, or review graph itself.
        The W1 facade owns the projection and returns semantic scalars
        only.  A malformed projection fails closed so the Agent Surface
        handler sees a supervisor error rather than an oversized or
        ill-typed decision-facts document.
        """

        try:
            facts = self.workspace_api.read_current_step_decision_facts(
                self.workspace, step=step_number
            )
        except Exception as exc:
            raise SupervisorError("decision_facts_unavailable") from exc
        if not isinstance(facts, Mapping):
            raise SupervisorError("decision_facts_unavailable")
        return facts

    def _prepare_submission(
        self,
        workspace_handle: str,
        attempt_handle: str,
        candidate_handle: str,
        *,
        kind: str,
    ) -> CandidateSubmission:
        context = self._attempt(workspace_handle, attempt_handle, candidate_handle)
        return CandidateSubmission(
            attempt_id=context.attempt_id,
            intended_step=context.intended_step,
            kind=kind,
            candidate_root=context.candidate_root,
        )

    def _publish_submission(
        self, submission: CandidateSubmission
    ) -> Mapping[str, int]:
        """Delegate one candidate submission to the W1 facade.

        The W1 facade owns candidate ingestion and authority mutation, and
        discovers evidence from the trusted candidate tree using its fixed
        internal producer filenames.  The Agent never named a path.
        """

        if submission.kind == "step_zero":
            if self._step_zero_evidence_provider is None:
                raise SupervisorError("step_zero_evidence_provider_missing")
            api_call = lambda: self.workspace_api.publish_step_zero_from_candidate(
                self.workspace,
                attempt=submission.attempt_id,
                source=submission.candidate_root,
                evidence_provider=self._step_zero_evidence_provider,
            )
        elif submission.kind == "repair":
            if self._repair_evidence_provider is None:
                raise SupervisorError("repair_evidence_provider_missing")
            api_call = lambda: self.workspace_api.publish_cycle_from_candidate(
                self.workspace,
                attempt=submission.attempt_id,
                source=submission.candidate_root,
                evidence_provider=self._repair_evidence_provider,
            )
        else:
            raise SupervisorError("invalid_request")
        try:
            document = api_call()
        except Exception as exc:
            classification = (
                "step_publication_failed"
                if submission.kind == "step_zero"
                else "cycle_publication_failed"
            )
            raise SupervisorError(classification) from exc
        step_value = document.get("step", 0)
        if isinstance(step_value, Mapping):
            step_value = step_value.get("step", 0)
        result = {"step": int(step_value)}
        if submission.kind == "repair":
            cycle_value = document.get("cycle", step_value)
            if isinstance(cycle_value, Mapping):
                cycle_value = cycle_value.get("cycle", step_value)
            result["cycle"] = int(cycle_value)
        return result

    def _retire_attempt(self, attempt_id: int) -> None:
        self.registry.revoke_attempt(attempt_id)
        self._attempts.pop(attempt_id, None)
        self._discard_current_work_tree()

    def _current_work_tree(self) -> Path:
        return self.candidate_root / _CURRENT_WORK_SUBDIR

    def _reset_current_work_tree(self) -> Path:
        """Securely reset the fixed current work subtree before an Attempt.

        The path is fixed at ``<candidate_root>/work`` so the Agent surface
        never sees or parses an Attempt identifier.  Any prior contents —
        including a forged Attempt-named sibling that a stale handle
        might attempt to reference — are irrelevant: this call clears
        ``work/`` and creates it fresh, no-follow, mode 0o700.
        """

        candidate = self._current_work_tree()
        try:
            if candidate.is_symlink():
                candidate.unlink()
            elif candidate.exists():
                shutil.rmtree(candidate)
            candidate.mkdir(parents=False, exist_ok=False, mode=0o700)
        except OSError as exc:
            raise SupervisorError("candidate_unavailable") from exc
        return candidate

    def _discard_current_work_tree(self) -> None:
        candidate = self._current_work_tree()
        try:
            if candidate.is_symlink():
                candidate.unlink()
            elif candidate.exists():
                shutil.rmtree(candidate)
        except OSError:
            pass

    def select_and_finalize(
        self,
        workspace_handle: str,
        step_handle: str,
        selection_handle: str,
        notes_handle: str,
    ) -> Mapping[str, Any]:
        """Finalize through the same drainable call scope as tool execution."""

        self._begin_active_call()
        try:
            return self._select_and_finalize(
                workspace_handle,
                step_handle,
                selection_handle,
                notes_handle,
            )
        finally:
            self._end_active_call()

    def _select_and_finalize(
        self,
        workspace_handle: str,
        step_handle: str,
        selection_handle: str,
        notes_handle: str,
    ) -> Mapping[str, Any]:
        self._workspace(workspace_handle)
        selected_step = self.registry.resolve(step_handle, "step")
        selection = self.registry.resolve(selection_handle, "selection")
        notes = self.registry.resolve(notes_handle, "notes")
        _safe_relative(self.candidate_root, selection)
        _safe_relative(self.candidate_root, notes)
        if (
            self._rebuild_entrypoint is None
            or self._geometry_entrypoint is None
            or self._tool_registry is None
        ):
            raise SupervisorError("finalization_unavailable")
        try:
            finalized = self.workspace_api.finalize_from_agent_selection_claim(
                self.workspace,
                source=self.candidate_root,
                selection=selection.relative_to(self.candidate_root).as_posix(),
                notes=notes.relative_to(self.candidate_root).as_posix(),
                selected_step=selected_step,
                rebuild_entrypoint=self._rebuild_entrypoint,
                geometry_entrypoint=self._geometry_entrypoint,
                tool_registry=self._tool_registry,
                scope=self._execution_scope,
            )
        except SupervisorError:
            raise
        except Exception as exc:
            raise SupervisorError("finalization_failed") from exc
        final_handle = self.registry.issue("final", finalized, reusable=False)
        return {
            "state": "finalized",
            "final_delivery_handle": final_handle,
            "permitted_next_intents": ["workspace_status"],
        }

    def observe_reference(
        self,
        reference_handle: str,
        observation: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if self._reference is None or self.reference_handle is None:
            raise SupervisorError("reference_unavailable")
        self.registry.resolve(reference_handle, "reference")
        if type(observation) is not dict or set(observation) != {"method", "args"}:
            raise SupervisorError("invalid_reference_request")
        method = observation.get("method")
        if method not in {"summary", "components"}:
            raise SupervisorError("unsupported_reference_operation")
        args = observation.get("args")
        if type(args) is not dict or set(args) - {"limit"}:
            raise SupervisorError("invalid_reference_request")
        if method == "summary" and args:
            raise SupervisorError("invalid_reference_request")
        if method == "components" and (
            "limit" in args
            and (type(args["limit"]) is not int or not 1 <= args["limit"] <= 32)
        ):
            raise SupervisorError("invalid_reference_request")
        request = {
            "schema": "meshscope.reference-request/1",
            "reference_id": reference_handle,
            "method": method,
            "args": dict(args),
        }
        try:
            result = self._reference.handle(request)
        except Exception as exc:
            raise SupervisorError("reference_observation_failed") from exc
        if not isinstance(result, Mapping) or result.get("reference_id") != reference_handle:
            raise SupervisorError("reference_contract_violation")
        return result

__all__ = [
    "CandidateSubmission",
    "OpaqueHandleRegistry",
    "SupervisorError",
    "WorkspaceSupervisor",
]
