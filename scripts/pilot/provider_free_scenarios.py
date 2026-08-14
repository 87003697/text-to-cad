#!/usr/bin/env python3
"""Closed provider-free scenarios dispatched by ``provider_free_runner``."""

from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import ctypes
import errno
import hashlib
import http.server
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import selectors
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Sequence
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

from scripts.pilot import deployment_authority
from scripts.pilot.cvm_job.protocol import (
    PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH,
    PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_SCHEMA,
    PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH,
    PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
    PROVIDER_FREE_BROWSER_CLEANUP_DIAGNOSTIC_PATH,
    PROVIDER_FREE_BROWSER_CLEANUP_DIAGNOSTIC_SCHEMA,
    PROVIDER_FREE_BROWSER_IDENTITY_SUBSTAGES,
    PROVIDER_FREE_BROWSER_RUNTIME_MODE,
    PROVIDER_FREE_BROWSER_SUPERVISOR_RESULT_CLEANUP_EXIT,
    PROVIDER_FREE_BROWSER_SOURCE_REVISION,
    PROVIDER_FREE_BROWSER_SUPERVISOR_NESTED_ROOT,
    PROVIDER_FREE_BROWSER_SUPERVISOR_OUTER_ROOT,
    PROVIDER_FREE_MESHSHOT_EXECUTABLE_ROOT,
    PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES,
    PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH,
    PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
    PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_EVIDENCE_PUBLICATION_OPERATION,
    PROVIDER_FREE_PREVIEW_SANDBOX_PATH,
    PROVIDER_FREE_PREVIEW_SANDBOX_SCHEMA,
    PROVIDER_FREE_STAGED_BROWSER_CACHE,
    PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
    PROVIDER_FREE_SCENARIO_FAILURE_PATH,
    PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA,
    PROVIDER_FREE_SCENARIO_FAILURE_STAGES,
    provider_free_scenario_failure_operation_allowed,
    provider_free_browser_identity_checks,
    provider_free_browser_cleanup_pair_allowed,
    provider_free_browser_runtime_allowed,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_HELPER = REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace"
MESH_COMPARE = REPO_ROOT / "skills/mesh-compare/scripts/mesh-compare"
MESH_COMPARE_ENTRYPOINT = MESH_COMPARE / "cli.py"
MESHSHOT_BROWSER_SUPERVISOR = (
    REPO_ROOT
    / "skills/mesh-compare/scripts/packages/meshshot/src/meshshot/"
    "browser_supervisor.py"
)
CAD_BUILD = REPO_ROOT / "skills/cad/scripts/canonical-build"
CAD_BUILD_ENTRYPOINT = CAD_BUILD / "__main__.py"
PREVIEW_PROFILE = (
    REPO_ROOT
    / "packages/meshshot/src/meshshot/profiles/"
    "cadena_residual_eight_view_v1.json"
)
DURABLE_MODEL_SOURCE = REPO_ROOT / "models/simple/rectangular_clamp_block.py"
DURABLE_MODEL_LIBRARY = REPO_ROOT / "models/simple/simple_model_library.py"
CADPY_SRC = REPO_ROOT / "skills/cad/scripts/packages/cadpy/src"
VIEWER_RUNTIME = REPO_ROOT / "skills/cad-viewer/scripts/viewer"
VIEWER_IDENTITY = VIEWER_RUNTIME / "runtime-identity.json"
VIEWER_LAUNCHER = VIEWER_RUNTIME / "scripts/start-agent-viewer.mjs"
SCENARIO_IDENTITY = "issue15.provider-free.runtime-authority/1"
NATIVE_BACKEND = {
    "schema": "meshscope.surface-occupancy-backend/1",
    "id": "meshscope.voxblame.native-sat/1",
    "implementation": "native",
}
COMMAND_TIMEOUT_SECONDS = 600
VIEWER_TIMEOUT_SECONDS = 60
TRUSTED_BWRAP_PATH = Path("/usr/bin/bwrap")
BROWSER_EXEC_PROBE_TIMEOUT_SECONDS = 5
_LINUX_TMPFS_MAGIC = 0x01021994
_PR_SET_DUMPABLE = 4


class _LinuxStatFs(ctypes.Structure):
    _fields_ = [
        ("f_type", ctypes.c_long),
        ("_opaque", ctypes.c_byte * 248),
    ]


class _BrowserStageMaterializationError(RuntimeError):
    """A local outer-stage primitive failed before descriptor cleanup."""


def _linux_filesystem_type(descriptor: int) -> int:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        fstatfs = libc.fstatfs
        fstatfs.argtypes = [ctypes.c_int, ctypes.POINTER(_LinuxStatFs)]
        fstatfs.restype = ctypes.c_int
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise _BrowserStageMaterializationError(
            "private browser staging filesystem unavailable"
        ) from exc
    result = _LinuxStatFs()
    if fstatfs(descriptor, ctypes.byref(result)) != 0:
        raise _BrowserStageMaterializationError(
            "private browser staging filesystem unavailable"
        )
    return int(result.f_type)


def _materialize_outer_browser_stage() -> None:
    """Copy the attested revision into the outer namespace-owned tmpfs."""

    from scripts.pilot.cvm_job import runtime as cvm_runtime

    if platform.system() != "Linux":
        raise ScenarioError(
            "private browser staging requires Linux",
            operation="preview_browser_runtime_staging",
        )
    expected = os.environ.get("MESHSHOT_BROWSER_TREE_MANIFEST_SHA256")
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ScenarioError(
            "private browser tree authority unavailable",
            operation="preview_browser_runtime_staging",
        )
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        prctl.restype = ctypes.c_int
        dumpable_result = prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise ScenarioError(
            "private browser staging isolation unavailable",
            operation="preview_browser_runtime_staging",
        ) from exc
    if dumpable_result != 0:
        raise ScenarioError(
            "private browser staging isolation unavailable",
            operation="preview_browser_runtime_staging",
        )
    source_fd: int | None = None
    destination_fd: int | None = None
    revision_fd: int | None = None
    cleanup_check: str | None = None
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(PROVIDER_FREE_BROWSER_SOURCE_REVISION, flags)
        destination_fd = os.open(PROVIDER_FREE_STAGED_BROWSER_CACHE, flags)
        if _linux_filesystem_type(destination_fd) != _LINUX_TMPFS_MAGIC:
            raise ScenarioError(
                "private browser stage is not kernel-owned tmpfs",
                operation="preview_browser_runtime_staging",
            )
        if os.listdir(destination_fd):
            raise ScenarioError(
                "private browser stage is not empty",
                operation="preview_browser_runtime_staging",
            )
        source_manifest = deployment_authority._browser_tree_manifest_from_fd(
            source_fd,
            readonly_projection=True,
        )
        if (
            deployment_authority.browser_tree_manifest_sha256(source_manifest)
            != expected
        ):
            raise ScenarioError(
                "private browser source conflicts with authority",
                operation="preview_browser_runtime_staging",
            )
        os.mkdir("attested", mode=0o700, dir_fd=destination_fd)
        revision_fd = os.open("attested", flags, dir_fd=destination_fd)
        cvm_runtime._copy_browser_tree_fd(source_fd, revision_fd)
        if (
            deployment_authority._browser_tree_manifest_from_fd(
                source_fd,
                readonly_projection=True,
            )
            != source_manifest
            or deployment_authority._browser_tree_manifest_from_fd(
                revision_fd,
                readonly_projection=False,
            )
            != source_manifest
        ):
            raise ScenarioError(
                "private browser stage conflicts with authority",
                operation="preview_browser_runtime_staging",
            )
        os.fchmod(destination_fd, 0o555)
        try:
            os.fsync(destination_fd)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EBADF}:
                raise
    except ScenarioError:
        raise
    except cvm_runtime.BrowserStageCleanupError as exc:
        raise ScenarioError(
            "private browser staging cleanup failed",
            operation="preview_browser_cleanup",
            browser_cleanup_substage="outer_browser_stage",
            browser_cleanup_check="tree_copy_descriptor_close",
        ) from exc
    except cvm_runtime.BrowserStageError as exc:
        raise ScenarioError(
            "private browser staging failed",
            operation="preview_browser_runtime_staging",
        ) from exc
    except (
        _BrowserStageMaterializationError,
        OSError,
        ValueError,
        deployment_authority.DeploymentAuthorityError,
    ) as exc:
        raise ScenarioError(
            "private browser staging failed",
            operation="preview_browser_runtime_staging",
        ) from exc
    finally:
        pending = sys.exc_info()[1]
        pending_cleanup = (
            isinstance(pending, ScenarioError)
            and pending.operation == "preview_browser_cleanup"
            and pending.browser_cleanup_substage == "outer_browser_stage"
            and pending.browser_cleanup_check is not None
        )
        if pending_cleanup:
            cleanup_check = pending.browser_cleanup_check
        for descriptor, check in (
            (revision_fd, "revision_descriptor_close"),
            (destination_fd, "destination_descriptor_close"),
            (source_fd, "source_descriptor_close"),
        ):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    if cleanup_check is None:
                        cleanup_check = check
        if cleanup_check is not None and not pending_cleanup:
            raise ScenarioError(
                "private browser staging cleanup failed",
                operation="preview_browser_cleanup",
                browser_cleanup_substage="outer_browser_stage",
                browser_cleanup_check=cleanup_check,
            )
BROWSER_EXEC_PROBE_ENVIRONMENT = {
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
}
_BROWSER_SUPERVISOR_SOCKET = Path("/meshshot-supervisor/authority.sock")
_BROWSER_SUPERVISOR_AUTHORITY = Path(
    "/meshshot-supervisor/client-authority.json"
)
_BROWSER_SUPERVISOR_CLIENT = Path("/meshshot-supervisor/expected-client.json")
_BROWSER_SUPERVISOR_RESULT = Path("/meshshot-supervisor/result.json")
_BROWSER_SUPERVISOR_TIMEOUT_SECONDS = 15.0
_BROWSER_SUPERVISOR_AUTHORITY_SCHEMA = "meshshot.browser-supervisor-authority/1"
_BROWSER_SUPERVISOR_CLIENT_SCHEMA = "meshshot.browser-supervisor-client/1"
_BROWSER_SUPERVISOR_RESULT_SCHEMA = "meshshot.browser-supervisor-result/1"
_BROWSER_VERSION_OUTPUT = re.compile(
    rb"(?:Google Chrome for Testing|Chromium|Chrome|HeadlessChrome) "
    rb"[0-9]+(?:\.[0-9]+){3}\n"
)
_NODE_BROWSER_PROBE_FAILURE_KINDS = frozenset(
    {"spawn-event", "nonzero-exit", "timeout", "output-shape"}
)
_NODE_BROWSER_PROBE_RESULT_BYTES = {
    b"passed\n": None,
    **{
        f"{kind}\n".encode("ascii"): kind
        for kind in _NODE_BROWSER_PROBE_FAILURE_KINDS
    },
}
_PUBLIC_SPAWN_CLASSIFICATION = "preview_public_spawn_failed"
_PUBLIC_TIMEOUT_CLASSIFICATION = "preview_public_timeout_failed"
_PUBLIC_COMMAND_EVIDENCE_CLASSIFICATION = (
    "preview_public_command_evidence_publication_failed"
)
_PUBLIC_RESULT_SHAPE_CLASSIFICATION = "preview_public_result_shape_failed"


class ScenarioError(RuntimeError):
    """A closed scenario could not publish required auditable evidence."""

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        operation: str | None = None,
        classification: str | None = None,
        browser_identity_substage: str | None = None,
        browser_identity_phase: str | None = None,
        browser_identity_check: str | None = None,
        browser_cleanup_substage: str | None = None,
        browser_cleanup_check: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.operation = operation
        self.classification = classification
        self.browser_identity_substage = (
            browser_identity_substage
            if browser_identity_substage in PROVIDER_FREE_BROWSER_IDENTITY_SUBSTAGES
            else None
        )
        self.browser_identity_phase = (
            browser_identity_phase
            if (
                self.browser_identity_substage
                == "private_snapshot_launch_image_identity"
                and browser_identity_phase
                in PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES
            )
            else None
        )
        self.browser_identity_check = (
            browser_identity_check
            if (
                browser_identity_check
                in provider_free_browser_identity_checks(
                    self.browser_identity_phase
                )
            )
            else None
        )
        self.browser_cleanup_substage = (
            browser_cleanup_substage
            if provider_free_browser_cleanup_pair_allowed(
                browser_cleanup_substage,
                browser_cleanup_check,
            )
            else None
        )
        self.browser_cleanup_check = (
            browser_cleanup_check
            if self.browser_cleanup_substage is not None
            else None
        )


def _run_stage(
    stage: str,
    function: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Attach one repository-owned top-level stage to a scenario failure."""

    if stage not in PROVIDER_FREE_SCENARIO_FAILURE_STAGES:
        raise ValueError(f"unknown provider-free scenario stage: {stage!r}")
    try:
        return function(*args, **kwargs)
    except Exception as exc:
        classified_operation = (
            exc.operation if isinstance(exc, ScenarioError) else None
        )
        raise ScenarioError(
            f"provider-free scenario stage failed: {stage}",
            stage=stage,
            operation=classified_operation,
            browser_identity_substage=(
                exc.browser_identity_substage
                if isinstance(exc, ScenarioError)
                else None
            ),
            browser_identity_phase=(
                exc.browser_identity_phase
                if isinstance(exc, ScenarioError)
                else None
            ),
            browser_identity_check=(
                exc.browser_identity_check
                if isinstance(exc, ScenarioError)
                else None
            ),
            browser_cleanup_substage=(
                exc.browser_cleanup_substage
                if isinstance(exc, ScenarioError)
                else None
            ),
            browser_cleanup_check=(
                exc.browser_cleanup_check
                if isinstance(exc, ScenarioError)
                else None
            ),
        ) from exc


def _run_candidate_operation(
    operation: str,
    function: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Attach one closed candidate operation without retaining error text."""

    return _run_failure_operation(
        "candidate_workspace", operation, function, *args, **kwargs
    )


def _run_failure_operation(
    stage: str,
    operation: str,
    function: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Attach one stage-compatible closed operation without error text."""

    if not provider_free_scenario_failure_operation_allowed(stage, operation):
        raise ValueError(
            f"unknown provider-free scenario operation: {stage!r}/{operation!r}"
        )
    try:
        return function(*args, **kwargs)
    except Exception as exc:
        classified_operation = (
            exc.operation if isinstance(exc, ScenarioError) else None
        )
        if not provider_free_scenario_failure_operation_allowed(
            stage, classified_operation
        ):
            classified_operation = operation
        raise ScenarioError(
            f"provider-free scenario operation failed: {stage}/{classified_operation}",
            operation=classified_operation,
            browser_identity_substage=(
                exc.browser_identity_substage
                if isinstance(exc, ScenarioError)
                else None
            ),
            browser_identity_phase=(
                exc.browser_identity_phase
                if isinstance(exc, ScenarioError)
                else None
            ),
            browser_identity_check=(
                exc.browser_identity_check
                if isinstance(exc, ScenarioError)
                else None
            ),
            browser_cleanup_substage=(
                exc.browser_cleanup_substage
                if isinstance(exc, ScenarioError)
                else None
            ),
            browser_cleanup_check=(
                exc.browser_cleanup_check
                if isinstance(exc, ScenarioError)
                else None
            ),
        ) from exc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, separators=(",", ": "))
        + "\n"
    ).encode("utf-8")


def _reject_duplicate_json_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


def _loads_json_strict(value: str | bytes) -> Any:
    return json.loads(value, object_pairs_hook=_reject_duplicate_json_pairs)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(payload))


def _identity(schema: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(schema.encode("utf-8") + b"\0" + _json_bytes(payload)).hexdigest()


def _physical_contained_file(root: Path, relative_text: object, label: str) -> Path:
    if not isinstance(relative_text, str):
        raise ScenarioError(f"Viewer {label} path is invalid")
    pure = PurePosixPath(relative_text)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ScenarioError(f"Viewer {label} path escapes repository")
    root_resolved = root.resolve(strict=True)
    current = root_resolved
    for part in pure.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise ScenarioError(f"Viewer {label} path is missing") from exc
        if stat.S_ISLNK(mode):
            raise ScenarioError(f"Viewer {label} path contains a symlink")
    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ScenarioError(f"Viewer {label} path escapes repository") from exc
    if not resolved.is_file():
        raise ScenarioError(f"Viewer {label} artifact must be a physical file")
    return resolved


def _closed_identity_document(path: Path) -> dict[str, Any]:
    try:
        value = _loads_json_strict(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ScenarioError(f"Viewer runtime identity is missing or invalid: {exc}") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "viewer_version",
        "artifacts",
    }:
        raise ScenarioError("Viewer runtime identity is not a closed object")
    if value["schema"] != "cad-viewer.runtime-identity/1":
        raise ScenarioError("Viewer runtime identity schema is unsupported")
    artifacts = value["artifacts"]
    if not isinstance(artifacts, list) or [item.get("role") for item in artifacts] != [
        "launcher",
        "server",
        "client",
    ]:
        raise ScenarioError("Viewer runtime identity artifacts are incomplete")
    return value


def deployed_viewer_receipt(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Verify the physical deployed Viewer and bind source/bundle/deployed SHA-256."""

    runtime = repo_root / "skills/cad-viewer/scripts/viewer"
    if runtime.is_symlink() or not runtime.is_dir():
        raise ScenarioError("deployed Viewer runtime must be a physical directory")
    identity_path = runtime / "runtime-identity.json"
    identity = _closed_identity_document(identity_path)
    receipt_artifacts = []
    for artifact in identity["artifacts"]:
        if not isinstance(artifact, dict) or set(artifact) != {"role", "source", "bundle"}:
            raise ScenarioError("Viewer runtime artifact identity is invalid")
        source = artifact["source"]
        bundle = artifact["bundle"]
        if (
            not isinstance(source, dict)
            or not isinstance(bundle, dict)
            or set(source) != {"path", "sha256"}
            or set(bundle) != {"path", "sha256"}
        ):
            raise ScenarioError("Viewer source/bundle identity is invalid")
        source_path = _physical_contained_file(repo_root, source["path"], "source")
        source_sha = _sha256(source_path)
        if source_sha != source["sha256"]:
            raise ScenarioError("Viewer source artifact digest conflicts with identity")
        deployed = _physical_contained_file(repo_root, bundle["path"], "bundle")
        try:
            deployed.relative_to(runtime.resolve(strict=True))
        except ValueError as exc:
            raise ScenarioError("Viewer bundle path escapes deployed runtime") from exc
        if deployed.is_symlink() or not deployed.is_file():
            raise ScenarioError("Viewer deployed artifact must be a physical file")
        deployed_sha = _sha256(deployed)
        if deployed_sha != bundle["sha256"]:
            raise ScenarioError("Viewer deployed artifact digest conflicts with bundle")
        receipt_artifacts.append(
            {
                "role": artifact["role"],
                "source": {"path": source["path"], "sha256": source_sha},
                "bundle": dict(bundle),
                "deployed": {
                    "path": bundle["path"],
                    "sha256": deployed_sha,
                },
            }
        )
    return {
        "schema": "cvm.viewer-runtime-deployment/1",
        "viewer_version": identity["viewer_version"],
        "runtime_identity": {
            "path": identity_path.relative_to(repo_root).as_posix(),
            "sha256": _sha256(identity_path),
        },
        "artifacts": receipt_artifacts,
    }


def deployed_runtime_tree_receipt(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Digest every regular file in the physical deployed Viewer runtime."""

    runtime = repo_root / "skills/cad-viewer/scripts/viewer"
    if runtime.is_symlink() or not runtime.is_dir():
        raise ScenarioError("deployed Viewer runtime must be a physical directory")
    files: list[dict[str, Any]] = []
    for path in sorted(runtime.rglob("*")):
        relative = path.relative_to(runtime).as_posix()
        if path.is_symlink():
            raise ScenarioError(f"deployed Viewer runtime contains a symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ScenarioError(f"deployed Viewer runtime path is unsupported: {relative}")
        data = path.read_bytes()
        files.append(
            {
                "path": relative,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    if not files:
        raise ScenarioError("deployed Viewer runtime is empty")
    identity_bytes = json.dumps(files, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return {
        "schema": "cvm.deployed-runtime-tree-receipt/1",
        "root": "skills/cad-viewer/scripts/viewer",
        "file_count": len(files),
        "total_bytes": sum(item["size_bytes"] for item in files),
        "tree_sha256": hashlib.sha256(identity_bytes).hexdigest(),
        "files": files,
    }


def native_depth_eight_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    """Require the public measurement payload's explicit native depth-8 identity."""

    if payload.get("ok") is not True or payload.get("backend") != NATIVE_BACKEND:
        raise ScenarioError("native-required measurement did not use explicit native backend")
    summary = payload.get("measurement")
    if not isinstance(summary, dict) or summary.get("schema") != "voxblame.summary/1":
        raise ScenarioError("native-required measurement summary is missing")
    depths = [item.get("depth") for item in summary.get("errors_by_depth", []) if isinstance(item, dict)]
    if summary.get("max_depth") != 8 or depths != list(range(1, 9)):
        raise ScenarioError("native-required measurement lacks ordered depths 1 through 8")
    return {
        "schema": "issue15.native-depth-eight-evidence/1",
        "native_required": True,
        "backend": dict(payload["backend"]),
        "depths": depths,
        "objective_facts": summary.get("objective_facts"),
    }


def cadpy_runtime_evidence() -> dict[str, Any]:
    """Resolve cadpy through the same audited bundled package used by CAD build."""

    sys.path.insert(0, os.fspath(CADPY_SRC))
    try:
        module = importlib.import_module("cadpy")
        path = Path(str(module.__file__)).resolve(strict=True)
        path.relative_to(CADPY_SRC.resolve(strict=True))
    except (ImportError, OSError, ValueError) as exc:
        raise ScenarioError("cadpy did not resolve from audited CAD skill runtime") from exc
    finally:
        if sys.path[0] == os.fspath(CADPY_SRC):
            sys.path.pop(0)
    return {
        "schema": "cvm.audited-cadpy-runtime/1",
        "path": path.relative_to(REPO_ROOT.resolve(strict=True)).as_posix(),
        "sha256": _sha256(path),
    }


def _run_public(
    argv: Sequence[str],
    *,
    cwd: Path,
    command_log: Path,
    process_started: Any = None,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if process_started is None:
            completed = subprocess.run(
                list(argv),
                cwd=cwd,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        else:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                process_started(process)
                stdout, stderr = process.communicate(timeout=COMMAND_TIMEOUT_SECONDS)
            except BaseException:
                try:
                    process.kill()
                except BaseException as cleanup_exc:
                    raise ScenarioError(
                        "public command cleanup failed",
                        operation="preview_browser_cleanup",
                        browser_cleanup_substage="nested_public_child",
                        browser_cleanup_check="termination_signal",
                    ) from cleanup_exc
                try:
                    process.communicate(timeout=5.0)
                except BaseException as cleanup_exc:
                    raise ScenarioError(
                        "public command cleanup failed",
                        operation="preview_browser_cleanup",
                        browser_cleanup_substage="nested_public_child",
                        browser_cleanup_check="completion_reap",
                    ) from cleanup_exc
                raise
            completed = subprocess.CompletedProcess(
                list(argv), process.returncode, stdout, stderr
            )
    except subprocess.TimeoutExpired as exc:
        raise ScenarioError(
            "public command timed out",
            classification=_PUBLIC_TIMEOUT_CLASSIFICATION,
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScenarioError(
            "public command could not start",
            classification=_PUBLIC_SPAWN_CLASSIFICATION,
        ) from exc
    record = {
        "schema": "cvm.provider-free-command/1",
        "argv": list(argv),
        "cwd": os.fspath(cwd),
        "exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    try:
        command_log.parent.mkdir(parents=True, exist_ok=True)
        with command_log.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )
    except OSError as exc:
        raise ScenarioError(
            "public command evidence publication failed",
            classification=_PUBLIC_COMMAND_EVIDENCE_CLASSIFICATION,
        ) from exc
    if completed.returncode != 0:
        classification = None
        try:
            failure_payload = _loads_json_strict(completed.stdout)
        except (json.JSONDecodeError, ValueError):
            failure_payload = None
        error = (
            failure_payload.get("error")
            if isinstance(failure_payload, dict)
            else None
        )
        if (
            isinstance(error, dict)
            and isinstance(error.get("classification"), str)
        ):
            classification = error["classification"]
        diagnostic = error.get("diagnostic") if isinstance(error, dict) else None
        browser_identity_substage = None
        browser_identity_phase = None
        browser_identity_check = None
        if (
            classification == "preview_browser_identity_failed"
            and isinstance(diagnostic, dict)
            and diagnostic.get("schema") == "meshshot.browser-identity-failure/6"
            and diagnostic.get("substage")
            in PROVIDER_FREE_BROWSER_IDENTITY_SUBSTAGES
        ):
            substage = diagnostic["substage"]
            expected_keys = {"schema", "substage"}
            if substage == "private_snapshot_launch_image_identity":
                expected_keys.add("phase")
            allowed_checks = provider_free_browser_identity_checks(
                diagnostic.get("phase")
            )
            if allowed_checks:
                expected_keys.add("check")
            if set(diagnostic) == expected_keys and (
                substage != "private_snapshot_launch_image_identity"
                or diagnostic.get("phase")
                in PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES
            ):
                browser_identity_substage = substage
                browser_identity_phase = diagnostic.get("phase")
                browser_identity_check = diagnostic.get("check")
                if (
                    allowed_checks
                    and browser_identity_check not in allowed_checks
                ):
                    browser_identity_substage = None
                    browser_identity_phase = None
                    browser_identity_check = None
        detail = " ".join((completed.stderr or completed.stdout).split())[:1000]
        raise ScenarioError(
            f"public command failed ({completed.returncode}): {detail}",
            classification=classification,
            browser_identity_substage=browser_identity_substage,
            browser_identity_phase=browser_identity_phase,
            browser_identity_check=browser_identity_check,
        )
    try:
        payload = _loads_json_strict(completed.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ScenarioError(
            "public command returned invalid JSON",
            classification=_PUBLIC_RESULT_SHAPE_CLASSIFICATION,
        ) from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ScenarioError(
            "public command did not return an ok result",
            classification=_PUBLIC_RESULT_SHAPE_CLASSIFICATION,
        )
    return payload


def _preview_sandbox_argv(argv: Sequence[str], *, cwd: Path) -> list[str]:
    """Drop outer setup capabilities before starting the browser process tree."""

    command = list(argv)
    if platform.system() != "Linux":
        return command
    try:
        info = TRUSTED_BWRAP_PATH.lstat()
    except OSError as exc:
        raise ScenarioError("trusted preview sandbox runtime unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ScenarioError("trusted preview sandbox runtime invalid")
    tree_manifest_sha256 = os.environ.get(
        "MESHSHOT_BROWSER_TREE_MANIFEST_SHA256"
    )
    if (
        not isinstance(tree_manifest_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", tree_manifest_sha256) is None
    ):
        raise ScenarioError("provider-free browser tree authority unavailable")
    return [
        os.fspath(TRUSTED_BWRAP_PATH),
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--bind",
        "/",
        "/",
        "--ro-bind",
        PROVIDER_FREE_STAGED_BROWSER_CACHE,
        PROVIDER_FREE_STAGED_BROWSER_CACHE,
        "--ro-bind",
        PROVIDER_FREE_BROWSER_SUPERVISOR_OUTER_ROOT,
        PROVIDER_FREE_BROWSER_SUPERVISOR_NESTED_ROOT,
        "--tmpfs",
        PROVIDER_FREE_BROWSER_SUPERVISOR_OUTER_ROOT,
        "--setenv",
        "PLAYWRIGHT_BROWSERS_PATH",
        PROVIDER_FREE_STAGED_BROWSER_CACHE,
        "--setenv",
        "MESHSHOT_BROWSER_EXECUTABLE",
        PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
        "--setenv",
        "MESHSHOT_EXECUTABLE_ROOT",
        PROVIDER_FREE_MESHSHOT_EXECUTABLE_ROOT,
        "--setenv",
        "MESHSHOT_BROWSER_RUNTIME_MODE",
        PROVIDER_FREE_BROWSER_RUNTIME_MODE,
        "--setenv",
        "MESHSHOT_BROWSER_TREE_MANIFEST_SHA256",
        tree_manifest_sha256,
        "--chdir",
        os.fspath(cwd),
        "--",
        *command,
    ]


def _browser_supervisor_group_empty(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _cleanup_browser_supervisor(process: subprocess.Popen[bytes]) -> None:
    failure = False
    cleanup_check: str | None = None

    def record(check: str, *, retained: bool = False) -> None:
        nonlocal failure, cleanup_check
        failure = True
        if cleanup_check is None or retained:
            cleanup_check = check

    def group_empty(check: str, *, retained: bool = False) -> bool:
        try:
            return _browser_supervisor_group_empty(process.pid)
        except (ScenarioError, OSError):
            record(check, retained=retained)
            return False

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError:
        record("term_signal")
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass
    except OSError:
        record("leader_term_wait")
    deadline = time.monotonic() + 1.0
    while not group_empty("term_group_empty") and time.monotonic() < deadline:
        time.sleep(0.02)
    if not group_empty("term_group_empty"):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            record("kill_signal")
        try:
            process.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            record("leader_kill_wait")
        deadline = time.monotonic() + 1.0
        while (
            not group_empty("kill_group_empty", retained=True)
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
    if not group_empty("kill_group_empty", retained=True):
        record("kill_group_empty", retained=True)
    if failure:
        raise ScenarioError(
            "provider-free browser supervisor cleanup failed",
            operation="preview_browser_cleanup",
            browser_cleanup_substage="outer_supervisor_process_group",
            browser_cleanup_check=cleanup_check,
        )


@contextmanager
def _blocked_supervisor_signals() -> Any:
    blocked = {signal.SIGINT, signal.SIGTERM}
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "pthread_sigmask")
    ):
        yield
        return
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def _load_supervisor_authority(expected_pid: int) -> dict[str, Any]:
    try:
        info = _BROWSER_SUPERVISOR_AUTHORITY.lstat()
        raw = _BROWSER_SUPERVISOR_AUTHORITY.read_bytes()
    except OSError as exc:
        raise ScenarioError(
            "provider-free browser supervisor authority unavailable",
            operation="preview_browser_readiness",
        ) from exc
    try:
        value = _loads_json_strict(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ScenarioError(
            "provider-free browser supervisor authority invalid",
            operation="preview_browser_readiness",
        ) from exc
    nonce = value.get("nonce") if isinstance(value, dict) else None
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o400
        or not isinstance(value, dict)
        or set(value) != {"schema", "supervisor_pid", "nonce"}
        or value.get("schema") != _BROWSER_SUPERVISOR_AUTHORITY_SCHEMA
        or value.get("supervisor_pid") != expected_pid
        or not isinstance(nonce, str)
        or re.fullmatch(r"[0-9a-f]{64}", nonce) is None
    ):
        raise ScenarioError(
            "provider-free browser supervisor authority invalid",
            operation="preview_browser_readiness",
        )
    return value


def _probe_supervisor_peer(*, expected_pid: int) -> None:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        connection.settimeout(1.0)
        connection.connect(os.fspath(_BROWSER_SUPERVISOR_SOCKET))
        option = getattr(socket, "SO_PEERCRED", None)
        if option is None:
            raise OSError("peer credentials unavailable")
        peer_pid, peer_uid, _peer_gid = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, option, 12)
        )
        if peer_pid != expected_pid or peer_uid != os.geteuid():
            raise OSError("peer identity mismatch")
    except (OSError, socket.timeout, struct.error) as exc:
        raise ScenarioError(
            "provider-free browser supervisor peer invalid",
            operation="preview_browser_readiness",
        ) from exc
    finally:
        connection.close()


class _BrowserSupervisorSession:
    def __init__(self, nonce: str) -> None:
        self._nonce = nonce
        self._registered = False

    def register_client(
        self,
        process: subprocess.Popen[str],
        *,
        expected_executable: Path,
    ) -> None:
        client_pid = _resolve_nested_client_pid(
            process,
            expected_executable=expected_executable,
            timeout=_BROWSER_SUPERVISOR_TIMEOUT_SECONDS,
        )
        if self._registered or client_pid <= 1:
            raise ScenarioError(
                "provider-free browser supervisor client invalid",
                operation="preview_browser_connect",
            )
        raw = json.dumps(
            {
                "schema": _BROWSER_SUPERVISOR_CLIENT_SCHEMA,
                "client_pid": client_pid,
                "nonce": self._nonce,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor: int | None = None
        try:
            descriptor = os.open(
                _BROWSER_SUPERVISOR_CLIENT,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o400,
            )
            if os.write(descriptor, raw) != len(raw):
                raise OSError("short write")
            os.fsync(descriptor)
        except OSError as exc:
            raise ScenarioError(
                "provider-free browser supervisor client publication failed",
                operation="preview_browser_connect",
            ) from exc
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    raise ScenarioError(
                        "provider-free browser supervisor client cleanup failed",
                        operation="preview_browser_cleanup",
                        browser_cleanup_substage=(
                            "private_supervisor_record_descriptors"
                        ),
                        browser_cleanup_check="client_record_descriptor_close",
                    ) from exc
        self._registered = True


def _resolve_nested_client_pid(
    process: subprocess.Popen[str],
    *,
    expected_executable: Path,
    timeout: float,
) -> int:
    """Bind bwrap's one exact final Python child to its outer process owner."""

    try:
        expected = expected_executable.resolve(strict=True).stat()
    except OSError as exc:
        raise ScenarioError(
            "provider-free nested client executable unavailable",
            operation="preview_browser_connect",
        ) from exc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = [process.pid]
        seen: set[int] = set()
        candidates: list[int] = []
        while pending:
            pid = pending.pop()
            if pid in seen or pid <= 1:
                continue
            seen.add(pid)
            try:
                image = (Path("/proc") / str(pid) / "exe").stat()
                children = (
                    Path("/proc") / str(pid) / "task" / str(pid) / "children"
                ).read_text(encoding="ascii").split()
            except (FileNotFoundError, ProcessLookupError):
                continue
            except (OSError, ValueError) as exc:
                raise ScenarioError(
                    "provider-free nested client identity unavailable",
                    operation="preview_browser_connect",
                ) from exc
            pending.extend(int(value) for value in children)
            if (image.st_dev, image.st_ino) == (expected.st_dev, expected.st_ino):
                candidates.append(pid)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1 or process.poll() is not None:
            break
        time.sleep(0.01)
    raise ScenarioError(
        "provider-free nested client identity invalid",
        operation="preview_browser_connect",
    )


def _closed_supervisor_failure(value: Any) -> ScenarioError:
    if not isinstance(value, dict) or "schema" not in value or "operation" not in value:
        return ScenarioError(
            "provider-free browser supervisor failed",
            operation="preview_browser_prelaunch",
        )
    operation = value.get("operation")
    if operation == "browser_identity":
        substage = value.get("browser_identity_substage")
        phase = value.get("browser_identity_phase")
        check = value.get("browser_identity_check")
        expected = {"schema", "operation", "browser_identity_substage"}
        if phase is not None:
            expected.add("browser_identity_phase")
        if check is not None:
            expected.add("browser_identity_check")
        if (
            set(value) == expected
            and value.get("schema") == _BROWSER_SUPERVISOR_RESULT_SCHEMA
            and substage in PROVIDER_FREE_BROWSER_IDENTITY_SUBSTAGES
            and (
                phase is None
                or phase in PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES
            )
            and (
                check is None
                or check in provider_free_browser_identity_checks(phase)
            )
        ):
            return ScenarioError(
                "provider-free browser supervisor identity failed",
                classification="preview_browser_identity_failed",
                browser_identity_substage=substage,
                browser_identity_phase=phase,
                browser_identity_check=check,
            )
    if operation == "browser_cleanup":
        substage = value.get("browser_cleanup_substage")
        check = value.get("browser_cleanup_check")
        if (
            value.get("schema") == _BROWSER_SUPERVISOR_RESULT_SCHEMA
            and set(value)
            == {
                "schema",
                "operation",
                "browser_cleanup_substage",
                "browser_cleanup_check",
            }
            and provider_free_browser_cleanup_pair_allowed(substage, check)
        ):
            return ScenarioError(
                "provider-free browser supervisor cleanup failed",
                classification="preview_browser_cleanup_failed",
                browser_cleanup_substage=substage,
                browser_cleanup_check=check,
            )
    if (
        value.get("schema") == _BROWSER_SUPERVISOR_RESULT_SCHEMA
        and set(value) == {"schema", "operation"}
        and operation
        in {
            "browser_adapter_profile",
            "browser_launch_process_limit",
            "browser_launch_file_limit",
            "browser_launch_address_space",
            "browser_launch_shared_memory",
            "browser_launch_executable_missing",
            "browser_launch_executable_dependency",
            "browser_launch_filesystem_permission",
            "browser_launch_sandbox_permission",
            "browser_launch_executable_spawn_permission",
            "browser_launch_executable_permission",
            "browser_profile",
            "browser_prelaunch",
            "browser_readiness",
            "browser_readiness_timeout",
            "browser_connect",
            "browser_signal",
            "browser_render",
            "browser_result",
        }
    ):
        return ScenarioError(
            "provider-free browser supervisor failed",
            classification=f"preview_{operation}_failed",
        )
    return ScenarioError(
        "provider-free browser supervisor failed",
        operation="preview_browser_prelaunch",
    )


@contextmanager
def _browser_supervisor() -> Any:
    """Start one fixed outer owner and require terminal socket/process cleanup."""

    tree_manifest_sha256 = os.environ.get(
        "MESHSHOT_BROWSER_TREE_MANIFEST_SHA256"
    )
    if (
        not isinstance(tree_manifest_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", tree_manifest_sha256) is None
    ):
        raise ScenarioError(
            "provider-free browser tree authority unavailable",
            operation="preview_browser_identity",
            browser_identity_substage="runtime_evidence_cross_binding",
        )
    environment = {
        "HOME": "/home/provider-free",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/workspace/repo/.venv/bin:/usr/local/bin:/usr/bin:/bin",
        "PLAYWRIGHT_BROWSERS_PATH": PROVIDER_FREE_STAGED_BROWSER_CACHE,
        "MESHSHOT_BROWSER_EXECUTABLE": PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
        "MESHSHOT_EXECUTABLE_ROOT": PROVIDER_FREE_MESHSHOT_EXECUTABLE_ROOT,
        "MESHSHOT_BROWSER_TREE_MANIFEST_SHA256": tree_manifest_sha256,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    process: subprocess.Popen[bytes] | None = None
    body_error: BaseException | None = None
    try:
        if any(
            os.path.lexists(path)
            for path in (
                _BROWSER_SUPERVISOR_SOCKET,
                _BROWSER_SUPERVISOR_AUTHORITY,
                _BROWSER_SUPERVISOR_CLIENT,
                _BROWSER_SUPERVISOR_RESULT,
            )
        ):
            raise ScenarioError(
                "provider-free browser supervisor state was not empty",
                operation="preview_browser_profile",
            )
        with _blocked_supervisor_signals():
            process = subprocess.Popen(
                [sys.executable, os.fspath(MESHSHOT_BROWSER_SUPERVISOR)],
                cwd=REPO_ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        deadline = time.monotonic() + _BROWSER_SUPERVISOR_TIMEOUT_SECONDS
        authority: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                try:
                    result = _loads_json_strict(
                        _BROWSER_SUPERVISOR_RESULT.read_text(encoding="utf-8")
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    result = None
                if result is not None:
                    raise _closed_supervisor_failure(result)
                raise ScenarioError(
                    "provider-free browser supervisor failed before readiness",
                    operation="preview_browser_prelaunch",
                )
            try:
                info = _BROWSER_SUPERVISOR_SOCKET.lstat()
            except FileNotFoundError:
                time.sleep(0.02)
                continue
            except OSError as exc:
                raise ScenarioError(
                    "provider-free browser supervisor readiness failed",
                    operation="preview_browser_readiness",
                ) from exc
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISSOCK(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise ScenarioError(
                    "provider-free browser supervisor socket invalid",
                    operation="preview_browser_readiness",
                )
            try:
                authority = _load_supervisor_authority(process.pid)
                _probe_supervisor_peer(expected_pid=process.pid)
            except ScenarioError:
                time.sleep(0.02)
                continue
            break
        else:
            raise ScenarioError(
                "provider-free browser supervisor readiness timed out",
                operation="preview_browser_readiness_timeout",
            )
        if authority is None:
            raise ScenarioError(
                "provider-free browser supervisor authority absent",
                operation="preview_browser_readiness",
            )
        try:
            yield _BrowserSupervisorSession(str(authority["nonce"]))
        except BaseException as exc:
            body_error = exc
        try:
            process.wait(timeout=_BROWSER_SUPERVISOR_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ScenarioError(
                "provider-free browser supervisor did not terminate",
                operation="preview_browser_cleanup",
                browser_cleanup_substage="outer_supervisor_wait",
                browser_cleanup_check="supervisor_wait",
            ) from exc
        if process.returncode != 0:
            if (
                process.returncode
                == PROVIDER_FREE_BROWSER_SUPERVISOR_RESULT_CLEANUP_EXIT
            ):
                raise ScenarioError(
                    "provider-free browser supervisor result cleanup failed",
                    operation="preview_browser_cleanup",
                    browser_cleanup_substage=(
                        "private_supervisor_record_descriptors"
                    ),
                    browser_cleanup_check="result_record_descriptor_close",
                )
            try:
                result = _loads_json_strict(
                    _BROWSER_SUPERVISOR_RESULT.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                result = None
            if result is not None:
                raise _closed_supervisor_failure(result)
            raise ScenarioError(
                "provider-free browser supervisor failed",
                operation="preview_browser_cleanup",
                browser_cleanup_substage="outer_supervisor_wait",
                browser_cleanup_check="supervisor_exit_status",
            )
        if not _browser_supervisor_group_empty(process.pid):
            raise ScenarioError(
                "provider-free browser supervisor failed",
                operation="preview_browser_cleanup",
                browser_cleanup_substage="outer_supervisor_process_group",
                browser_cleanup_check="term_group_empty",
            )
        process = None
        if body_error is not None:
            raise body_error
    finally:
        pending = sys.exc_info()[1]
        cleanup_error = (
            pending
            if (
                isinstance(pending, ScenarioError)
                and pending.operation == "preview_browser_cleanup"
                and provider_free_browser_cleanup_pair_allowed(
                    pending.browser_cleanup_substage,
                    pending.browser_cleanup_check,
                )
            )
            else None
        )
        if process is not None:
            try:
                _cleanup_browser_supervisor(process)
            except ScenarioError as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        for path, check in (
            (_BROWSER_SUPERVISOR_SOCKET, "socket_absence"),
            (_BROWSER_SUPERVISOR_AUTHORITY, "authority_absence"),
            (_BROWSER_SUPERVISOR_CLIENT, "client_absence"),
        ):
            if os.path.lexists(path):
                # A positive retained-resource proof overrides an earlier
                # failure from the same outer supervisor lifecycle.
                cleanup_error = ScenarioError(
                    "provider-free browser supervisor private state remained",
                    operation="preview_browser_cleanup",
                    browser_cleanup_substage="outer_supervisor_private_state",
                    browser_cleanup_check=check,
                )
                break
        if cleanup_error is not None and cleanup_error is not pending:
            raise cleanup_error


def _publish_preview_sandbox_enforcement(
    command_log: Path,
    argv: Sequence[str],
) -> None:
    """Bind the exact capability-dropping preview boundary to this run."""

    _write_json(
        command_log.parent / Path(PROVIDER_FREE_PREVIEW_SANDBOX_PATH).name,
        {
            "schema": PROVIDER_FREE_PREVIEW_SANDBOX_SCHEMA,
            "argv": list(argv),
            "capabilities": "drop-all",
            "mount_namespace": "inherit-outer",
        },
    )


def _publish_browser_exec_diagnostic(
    command_log: Path,
    *,
    outer: str,
    nested: str,
    node_attached: str,
    node_detached: str,
    node_failure_kind: str,
    prelaunched_cdp: str,
) -> None:
    """Publish only closed outcomes for the exact staged-browser probes."""

    _write_json(
        command_log.parent
        / Path(PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH).name,
        {
            "schema": PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_SCHEMA,
            "executable": PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
            "probe": "chromium-version-immediate-exit",
            "outer": outer,
            "nested": nested,
            "node_attached": node_attached,
            "node_detached": node_detached,
            "node_failure_kind": node_failure_kind,
            "prelaunched_cdp": prelaunched_cdp,
        },
    )


def _publish_preview_public_wrapper(
    command_log: Path, *, operation: str
) -> None:
    """Publish only the closed public-wrapper operation or success state."""

    destination = (
        command_log.parent
        / Path(PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH).name
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_fd, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(temporary_fd, "wb") as stream:
            temporary_fd = -1
            stream.write(
                _json_bytes(
                    {
                        "schema": PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
                        "operation": operation,
                    }
                )
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _discard_preview_public_wrapper_residue(command_log: Path) -> None:
    """Remove only the exact failed final receipt without following links."""

    destination = (
        command_log.parent
        / Path(PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH).name
    )
    try:
        mode = destination.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(mode):
        raise OSError("preview public wrapper residue is a directory")
    destination.unlink()
    if os.path.lexists(destination):
        raise OSError("preview public wrapper residue changed during cleanup")


def _require_preview_public_wrapper(
    command_log: Path, *, operation: str
) -> None:
    """Map failure to publish wrapper evidence to its nonrecursive root."""

    try:
        _publish_preview_public_wrapper(command_log, operation=operation)
    except OSError as exc:
        try:
            _discard_preview_public_wrapper_residue(command_log)
        except OSError as cleanup_exc:
            raise ScenarioError(
                "provider-free preview public wrapper residue cleanup failed"
            ) from cleanup_exc
        raise ScenarioError(
            "provider-free preview public wrapper evidence publication failed",
            operation=(
                PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_EVIDENCE_PUBLICATION_OPERATION
            ),
        ) from exc


def _run_exact_browser_version_probe(argv: Sequence[str], *, cwd: Path) -> None:
    """Require one bounded immediate exit from the exact staged Chromium."""

    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(BROWSER_EXEC_PROBE_ENVIRONMENT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=BROWSER_EXEC_PROBE_TIMEOUT_SECONDS,
            start_new_session=True,
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScenarioError("staged browser exec probe failed") from exc
    if (
        completed.returncode != 0
        or not isinstance(completed.stdout, bytes)
        or len(completed.stdout) > 128
        or _BROWSER_VERSION_OUTPUT.fullmatch(completed.stdout) is None
        or completed.stderr != b""
    ):
        raise ScenarioError("staged browser exec probe result is invalid")


def _run_closed_node_browser_version_probe(
    argv: Sequence[str], *, cwd: Path
) -> str | None:
    """Return one closed Node failure kind or ``None`` after exact success."""

    try:
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(BROWSER_EXEC_PROBE_ENVIRONMENT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        return "spawn-event"
    try:
        stdout, stderr = process.communicate(
            timeout=BROWSER_EXEC_PROBE_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        _terminate_node_probe_process(process)
        return "timeout"
    except OSError:
        _terminate_node_probe_process(process)
        return "output-shape"
    if process.returncode is not None and process.returncode < 0:
        _terminate_node_probe_process(process)
        return "nonzero-exit"
    if (
        not isinstance(stdout, bytes)
        or not isinstance(stderr, bytes)
        or len(stdout) > 32
        or stderr != b""
    ):
        _terminate_node_probe_process(process)
        return "output-shape"
    if stdout not in _NODE_BROWSER_PROBE_RESULT_BYTES:
        _terminate_node_probe_process(process)
        return "output-shape"
    result = _NODE_BROWSER_PROBE_RESULT_BYTES[stdout]
    if process.returncode == 0:
        if result is None:
            return None
        _terminate_node_probe_process(process)
        return "output-shape"
    if result in _NODE_BROWSER_PROBE_FAILURE_KINDS:
        return result
    _terminate_node_probe_process(process)
    return "nonzero-exit"


def _terminate_node_probe_process(process: subprocess.Popen[bytes]) -> None:
    """Bound cleanup of one session-owned Node probe and its descendants."""

    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        process.communicate()
    except OSError:
        pass


def _playwright_bundled_node() -> Path:
    """Resolve only Playwright's physical bundled Node executable."""

    try:
        import playwright
        from playwright._impl._driver import compute_driver_executable

        package_root = Path(playwright.__file__).resolve(strict=True).parent
        expected = package_root / "driver/node"
        reported = Path(compute_driver_executable()[0])
        info = expected.lstat()
    except (ImportError, OSError) as exc:
        raise ScenarioError("Playwright bundled Node is unavailable") from exc
    if (
        reported != expected
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_mode & 0o111 == 0
    ):
        raise ScenarioError("Playwright bundled Node is invalid")
    return expected


def _browser_node_probe_script() -> Path:
    """Return the physical repository-owned Node probe."""

    root = REPO_ROOT.resolve(strict=True)
    path = root / "scripts/pilot/browser_exec_probe.js"
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ScenarioError("repository browser exec probe is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ScenarioError("repository browser exec probe is invalid")
    return resolved


def _nested_node_browser_exec_probe_argv(*, cwd: Path, mode: str) -> list[str]:
    """Run one closed Node spawn mode inside the exact preview sandbox."""

    if mode not in {"attached", "detached"}:
        raise ScenarioError("bundled Node browser exec probe mode is invalid")
    return _nested_browser_exec_probe_argv(
        cwd=cwd,
        command=[
            os.fspath(_playwright_bundled_node()),
            os.fspath(_browser_node_probe_script()),
            mode,
            PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
        ],
    )


def _nested_browser_exec_probe_argv(
    *, cwd: Path, command: Sequence[str] | None = None
) -> list[str]:
    """Project the exact staged Chromium through the nested preview mount."""

    try:
        info = TRUSTED_BWRAP_PATH.lstat()
    except OSError as exc:
        raise ScenarioError("trusted preview sandbox runtime unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ScenarioError("trusted preview sandbox runtime invalid")
    nested_command = list(command) if command is not None else [
        PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
        "--version",
    ]
    if not nested_command:
        raise ScenarioError("nested browser exec probe command is empty")
    return [
        os.fspath(TRUSTED_BWRAP_PATH),
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--setenv",
        "HOME",
        BROWSER_EXEC_PROBE_ENVIRONMENT["HOME"],
        "--setenv",
        "LANG",
        BROWSER_EXEC_PROBE_ENVIRONMENT["LANG"],
        "--setenv",
        "PATH",
        BROWSER_EXEC_PROBE_ENVIRONMENT["PATH"],
        "--bind",
        "/",
        "/",
        "--ro-bind",
        PROVIDER_FREE_STAGED_BROWSER_CACHE,
        PROVIDER_FREE_STAGED_BROWSER_CACHE,
        "--chdir",
        os.fspath(cwd),
        "--",
        *nested_command,
    ]


def _validate_attested_browser_runtime(
    staging_cache: Path = Path(PROVIDER_FREE_STAGED_BROWSER_CACHE),
) -> str:
    """Validate the exact host-pre-staged browser without copying a host cache."""

    try:
        receipt = _loads_json_strict(
            (REPO_ROOT / deployment_authority.RECEIPT_PATH).read_text(
                encoding="utf-8"
            )
        )
        if receipt.get("contract_paths") != list(
            deployment_authority.EXECUTION_AUTHORITY_PATHS
        ):
            raise ValueError("deployment authority contract is incomplete")
        deployment_authority.verify_receipt(REPO_ROOT, receipt)
        chromium = receipt["runtime_identity"]["chromium"]
        if (
            not str(chromium["revision"]).isdigit()
            or stat.S_ISLNK(staging_cache.lstat().st_mode)
            or not stat.S_ISDIR(staging_cache.lstat().st_mode)
        ):
            raise ValueError("invalid pre-staged browser root")
        staged_revision = staging_cache / "attested"
        if [path.name for path in staging_cache.iterdir()] != ["attested"]:
            raise ValueError("pre-staged browser root is not exact")
        for staged in (
            staged_revision,
            *sorted(staged_revision.rglob("*")),
        ):
            mode = staged.lstat().st_mode
            if stat.S_ISLNK(mode) or not (
                stat.S_ISDIR(mode) or stat.S_ISREG(mode)
            ):
                raise ValueError("pre-staged browser contains a non-regular entry")
        executable = staged_revision / (
            "chrome-headless-shell-linux64/chrome-headless-shell"
        )
        if (
            (browser_sha256 := hashlib.sha256(executable.read_bytes()).hexdigest())
            != chromium["sha256"]
            or not executable.lstat().st_mode & 0o111
        ):
            raise ValueError("pre-staged browser executable identity conflicts")
    except (
        KeyError,
        OSError,
        json.JSONDecodeError,
        ValueError,
        deployment_authority.DeploymentAuthorityError,
    ) as exc:
        raise ScenarioError(
            "provider-free browser runtime identity is unavailable",
            operation="preview_browser_runtime_staging",
        ) from exc
    return browser_sha256


class _RejectedViewerHandler(http.server.BaseHTTPRequestHandler):
    viewer_version = ""
    activation_count = 0

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/__cad/server":
            self.send_error(404)
            return
        self._send(
            200,
            {
                "schemaVersion": 1,
                "serverApiVersion": 2,
                "app": "cad-viewer",
                "viewerVersion": self.viewer_version,
                "serverMode": "serve",
                "serverFeatures": ["directory-activation"],
                "dynamicRoot": True,
                "backend": "local-fs",
                "pid": os.getpid(),
                "port": self.server.server_port,
                "url": f"http://127.0.0.1:{self.server.server_port}",
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/__cad/directory/activate":
            self.send_error(404)
            return
        type(self).activation_count += 1
        self._send(400, {"ok": False, "error": "synthetic namespace rejection"})

    def _send(self, status: int, payload: object) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, _format: str, *args: object) -> None:
        return


def _read_process_line(process: subprocess.Popen[str], timeout: float) -> str:
    if process.stdout is None:
        raise ScenarioError("Viewer launcher stdout is unavailable")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        events = selector.select(timeout)
        if not events:
            raise ScenarioError("Viewer launcher did not publish its JSON result")
        line = process.stdout.readline()
        if not line:
            raise ScenarioError("Viewer launcher exited before publishing its JSON result")
        return line
    finally:
        selector.close()


def viewer_fallback_evidence(
    workspace: Path,
    deployment: dict[str, Any],
    *,
    launcher: Path = VIEWER_LAUNCHER,
) -> dict[str, Any]:
    """Exercise HTTP-400 reuse rejection and namespace-correct deployed fallback."""

    _RejectedViewerHandler.viewer_version = str(deployment["viewer_version"])
    _RejectedViewerHandler.activation_count = 0
    fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RejectedViewerHandler)
    reuse_port = fake.server_port
    thread = threading.Thread(target=fake.serve_forever, daemon=True)
    thread.start()
    stderr_path = workspace / "run/viewer-fallback.stderr.log"
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    process: subprocess.Popen[str] | None = None
    try:
        with stderr_path.open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen(
                [
                    "node",
                    os.fspath(launcher),
                    "--viewer-start-mode",
                    "serve",
                    "--host",
                    "127.0.0.1",
                    "--dir",
                    os.fspath(workspace),
                    "--port",
                    str(reuse_port),
                    "--port-scan-limit",
                    "3",
                    "--json",
                ],
                cwd=REPO_ROOT,
                env=dict(os.environ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
                start_new_session=True,
            )
            result = json.loads(_read_process_line(process, VIEWER_TIMEOUT_SECONDS))
        if result.get("action") != "start" or result.get("port") == reuse_port:
            raise ScenarioError("deployed Viewer did not fall back after HTTP 400 reuse rejection")
        parsed_url = urlparse(str(result.get("url", "")))
        requested = parse_qs(parsed_url.query).get("dir", [])
        if requested != [os.fspath(workspace)]:
            raise ScenarioError("deployed Viewer fallback URL has the wrong directory namespace")
        info_url = (
            f"http://127.0.0.1:{int(result['port'])}/__cad/server?"
            + urlencode({"dir": os.fspath(workspace)})
        )
        with urlopen(info_url, timeout=5) as response:
            server_info = json.loads(response.read())
        if Path(server_info.get("rootPath", "")).resolve() != workspace.resolve():
            raise ScenarioError("deployed Viewer served the wrong directory namespace")
        if _RejectedViewerHandler.activation_count != 1:
            raise ScenarioError("synthetic Viewer HTTP-400 reuse rejection was not observed once")
        return {
            "schema": "issue15.viewer-fallback-smoke/1",
            "requested_directory": os.fspath(workspace),
            "rejected_reuse": {"port": reuse_port, "http_status": 400},
            "fallback": {
                "action": result["action"],
                "port": result["port"],
                "url": result["url"],
                "root_path": server_info["rootPath"],
                "server_pid": server_info["pid"],
            },
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ScenarioError(f"deployed Viewer fallback smoke failed: {exc}") from exc
    finally:
        fake.shutdown()
        fake.server_close()
        thread.join(timeout=2)
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2)


def _copy_candidate_sources(workspace: Path) -> Path:
    for source in (DURABLE_MODEL_SOURCE, DURABLE_MODEL_LIBRARY):
        if not source.is_file() or source.read_bytes().startswith(b"version https://git-lfs"):
            raise ScenarioError(f"durable model source is unavailable: {source}")
    candidate = workspace / "work/candidate"
    source_dir = candidate / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DURABLE_MODEL_SOURCE, source_dir / "model.py")
    shutil.copy2(DURABLE_MODEL_LIBRARY, source_dir / "simple_model_library.py")
    return candidate


def _build_candidate(candidate: Path, command_log: Path) -> None:
    _run_public(
        [
            sys.executable,
            os.fspath(CAD_BUILD),
            "build",
            "--source",
            "source/model.py",
            "--input",
            "source/simple_model_library.py",
            "--output-dir",
            "built",
            "--reject-source-output",
        ],
        cwd=candidate,
        command_log=command_log,
    )
    mesh = candidate / "built/measurement.glb"
    if not mesh.is_file():
        raise ScenarioError("canonical CAD build did not publish measurement.glb")


def _prepare_candidate(workspace: Path, command_log: Path) -> Path:
    candidate = _run_candidate_operation(
        "fixture_availability", _copy_candidate_sources, workspace
    )
    _run_candidate_operation(
        "canonical_build", _build_candidate, candidate, command_log
    )
    return candidate


def _prepare_reference(workspace: Path, candidate: Path, command_log: Path) -> None:
    prepared = workspace / "work/prepared"
    _run_public(
        [
            sys.executable,
            os.fspath(MESH_COMPARE),
            "voxblame-prepare-reference",
            os.fspath(candidate / "built/measurement.glb"),
            "--output",
            os.fspath(prepared / "input"),
        ],
        cwd=REPO_ROOT,
        command_log=command_log,
    )
    input_document = _loads_json_strict(
        (prepared / "input/input.json").read_text(encoding="utf-8")
    )
    reference_sha = input_document["canonical_reference_sha256"]
    profile_sha = _sha256(PREVIEW_PROFILE)
    _write_json(prepared / "setup/route.json", {"schema": "mesh-to-cad.route/1", "route": "cad"})
    _write_json(
        prepared / "experiment.json",
        {
            "schema": "mesh-to-cad.experiment/1",
            "workspace_id": f"provider-free-{workspace.name}",
            "coordinate_contract": "trellis2_canonical/1",
            "canonical_reference_sha256": reference_sha,
            "preview_profile": {
                "name": "cadena_residual_eight_view/1",
                "sha256": profile_sha,
            },
            "route": "cad",
        },
    )


def _initialize_workspace(workspace: Path, command_log: Path) -> None:
    prepared = workspace / "work/prepared"
    _run_public(
        [
            sys.executable,
            os.fspath(WORKSPACE_HELPER),
            "init",
            "--workspace",
            os.fspath(workspace),
            "--prepared",
            os.fspath(prepared),
        ],
        cwd=REPO_ROOT,
        command_log=command_log,
    )


def _prepare_workspace(workspace: Path, candidate: Path, command_log: Path) -> None:
    _run_candidate_operation(
        "reference_preparation",
        _prepare_reference,
        workspace,
        candidate,
        command_log,
    )
    _run_candidate_operation(
        "workspace_init", _initialize_workspace, workspace, command_log
    )


def _run_voxblame_preview(
    argv: Sequence[str],
    *,
    cwd: Path,
    command_log: Path,
) -> dict[str, Any]:
    """Map one closed public renderer classification to a failure operation."""

    operations = {
        "preview_runtime_failed": "preview_runtime",
        "preview_dependency_failed": "preview_dependency",
        "preview_browser_launch_failed": "preview_browser_launch",
        "preview_browser_launch_process_limit_failed": (
            "preview_browser_launch_process_limit"
        ),
        "preview_browser_launch_file_limit_failed": (
            "preview_browser_launch_file_limit"
        ),
        "preview_browser_launch_address_space_failed": (
            "preview_browser_launch_address_space"
        ),
        "preview_browser_launch_shared_memory_failed": (
            "preview_browser_launch_shared_memory"
        ),
        "preview_browser_launch_executable_failed": (
            "preview_browser_launch_executable"
        ),
        "preview_browser_launch_executable_missing_failed": (
            "preview_browser_launch_executable_missing"
        ),
        "preview_browser_launch_executable_permission_failed": (
            "preview_browser_launch_executable_permission"
        ),
        "preview_browser_launch_executable_spawn_permission_failed": (
            "preview_browser_launch_executable_spawn_permission"
        ),
        "preview_browser_launch_sandbox_permission_failed": (
            "preview_browser_launch_sandbox_permission"
        ),
        "preview_browser_launch_filesystem_permission_failed": (
            "preview_browser_launch_filesystem_permission"
        ),
        "preview_browser_launch_executable_dependency_failed": (
            "preview_browser_launch_executable_dependency"
        ),
        "preview_browser_adapter_profile_failed": "preview_browser_adapter_profile",
        "preview_browser_identity_failed": "preview_browser_identity",
        "preview_browser_profile_failed": "preview_browser_profile",
        "preview_browser_prelaunch_failed": "preview_browser_prelaunch",
        "preview_browser_readiness_failed": "preview_browser_readiness",
        "preview_browser_readiness_timeout_failed": (
            "preview_browser_readiness_timeout"
        ),
        "preview_browser_connect_failed": "preview_browser_connect",
        "preview_browser_cleanup_failed": "preview_browser_cleanup",
        "preview_browser_signal_failed": "preview_browser_signal",
        "preview_browser_render_failed": "preview_browser_render",
        "preview_browser_result_failed": "preview_browser_result",
    }
    public_failure_operations = {
        _PUBLIC_SPAWN_CLASSIFICATION: "preview_public_spawn",
        _PUBLIC_TIMEOUT_CLASSIFICATION: "preview_public_timeout",
        _PUBLIC_COMMAND_EVIDENCE_CLASSIFICATION: (
            "preview_public_command_evidence_publication"
        ),
        _PUBLIC_RESULT_SHAPE_CLASSIFICATION: "preview_public_result_shape",
    }
    is_linux = platform.system() == "Linux"
    expected_browser_sha256: str | None = None
    expected_tree_manifest_sha256 = os.environ.get(
        "MESHSHOT_BROWSER_TREE_MANIFEST_SHA256"
    )
    try:
        if is_linux:
            expected_browser_sha256 = _validate_attested_browser_runtime()
            try:
                _run_exact_browser_version_probe(
                    [
                        PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
                        "--version",
                    ],
                    cwd=cwd,
                )
            except ScenarioError as exc:
                _publish_browser_exec_diagnostic(
                    command_log,
                    outer="failed",
                    nested="not-run",
                    node_attached="not-run",
                    node_detached="not-run",
                    node_failure_kind="not-run",
                    prelaunched_cdp="not-run",
                )
                raise ScenarioError(
                    "provider-free outer browser exec probe failed",
                    operation="preview_browser_outer_exec_probe",
                ) from exc
        try:
            sandbox_argv = _preview_sandbox_argv(argv, cwd=cwd)
            _publish_preview_sandbox_enforcement(command_log, sandbox_argv)
        except Exception as exc:
            operation = "preview_public_sandbox_setup"
            _require_preview_public_wrapper(
                command_log,
                operation=operation,
            )
            raise ScenarioError(
                "provider-free preview sandbox setup failed",
                operation=operation,
            ) from exc
        try:
            supervisor = _browser_supervisor() if is_linux else nullcontext()
            with supervisor as supervisor_session:
                process_started = None
                if supervisor_session is not None:
                    process_started = lambda process: supervisor_session.register_client(
                        process,
                        expected_executable=Path(
                            sandbox_argv[sandbox_argv.index("--") + 1]
                        ),
                    )
                result = _run_public(
                    sandbox_argv,
                    cwd=cwd,
                    command_log=command_log,
                    process_started=process_started,
                )
        except ScenarioError as exc:
            if (
                exc.operation == "preview_browser_cleanup"
                and provider_free_browser_cleanup_pair_allowed(
                    exc.browser_cleanup_substage,
                    exc.browser_cleanup_check,
                )
            ):
                renderer_operation = "preview_browser_cleanup"
                public_operation = None
            else:
                renderer_operation = operations.get(exc.classification)
                public_operation = public_failure_operations.get(exc.classification)
                if renderer_operation is None and public_operation is None:
                    public_operation = "preview_public_unclassified_exit"
            if is_linux:
                try:
                    _publish_browser_exec_diagnostic(
                        command_log,
                        outer="passed",
                        nested="not-run",
                        node_attached="not-run",
                        node_detached="not-run",
                        node_failure_kind="not-run",
                        prelaunched_cdp="failed",
                    )
                except Exception as diagnostic_exc:
                    operation = "preview_public_failure_diagnostic_publication"
                    _require_preview_public_wrapper(
                        command_log,
                        operation=operation,
                    )
                    raise ScenarioError(
                        "provider-free failed-public diagnostic publication failed",
                        operation=operation,
                    ) from diagnostic_exc
            operation = renderer_operation or public_operation
            _require_preview_public_wrapper(
                command_log,
                operation=operation,
            )
            raise ScenarioError(
                "provider-free preview public wrapper failed",
                operation=operation,
                browser_identity_substage=exc.browser_identity_substage,
                browser_identity_phase=exc.browser_identity_phase,
                browser_identity_check=exc.browser_identity_check,
                browser_cleanup_substage=exc.browser_cleanup_substage,
                browser_cleanup_check=exc.browser_cleanup_check,
            ) from exc
        if is_linux:
            try:
                _publish_browser_exec_diagnostic(
                    command_log,
                    outer="passed",
                    nested="not-run",
                    node_attached="not-run",
                    node_detached="not-run",
                    node_failure_kind="not-run",
                    prelaunched_cdp="passed",
                )
            except Exception as exc:
                operation = "preview_public_success_diagnostic_publication"
                _require_preview_public_wrapper(
                    command_log,
                    operation=operation,
                )
                raise ScenarioError(
                    "provider-free successful-public diagnostic publication failed",
                    operation=operation,
                ) from exc
        _require_preview_public_wrapper(command_log, operation="passed")
        preview = result.get("preview") if isinstance(result, dict) else None
        browser_runtime = (
            preview.get("browser_runtime") if isinstance(preview, dict) else None
        )
        if not is_linux and isinstance(browser_runtime, dict):
            identity = browser_runtime.get("browser_identity")
            if isinstance(identity, dict):
                expected_browser_sha256 = identity.get("sha256")
        if (
            expected_browser_sha256 is None
            or not provider_free_browser_runtime_allowed(
                browser_runtime,
                expected_browser_sha256=expected_browser_sha256,
                expected_tree_manifest_sha256=expected_tree_manifest_sha256,
            )
        ):
            operation = "preview_browser_identity"
            _require_preview_public_wrapper(command_log, operation=operation)
            raise ScenarioError(
                "provider-free browser runtime evidence is invalid",
                operation="preview_browser_identity",
                browser_identity_substage="runtime_evidence_cross_binding",
            )
        return result
    except ScenarioError as exc:
        operation = operations.get(exc.classification)
        if operation is None:
            raise
        raise ScenarioError(
            f"provider-free preview operation failed: {operation}",
            operation=operation,
            browser_identity_substage=exc.browser_identity_substage,
        ) from exc


def _publish_measured_step(workspace: Path, candidate: Path, command_log: Path) -> dict[str, Any]:
    plan = workspace / "work/initial-plan.json"
    _run_failure_operation(
        "native_measurement",
        "attempt_begin",
        _write_json,
        plan,
        {
            "schema": "mesh-to-cad.initial-plan/1",
            "summary": "Rebuild the durable rectangular clamp fixture in canonical coordinates.",
        },
    )
    begun = _run_failure_operation(
        "native_measurement",
        "attempt_begin",
        _run_public,
        [
            sys.executable,
            os.fspath(WORKSPACE_HELPER),
            "begin-attempt",
            "--workspace",
            os.fspath(workspace),
            "--plan",
            os.fspath(plan),
            "--intended-step",
            "0",
        ],
        cwd=REPO_ROOT,
        command_log=command_log,
    )
    attempt = begun["attempt"]["attempt"]
    measured = _run_failure_operation(
        "native_measurement",
        "voxblame_measure",
        _run_public,
        [
            sys.executable,
            os.fspath(MESH_COMPARE),
            "voxblame-measure",
            os.fspath(candidate / "built/measurement.glb"),
            "--reference",
            os.fspath(workspace / "input"),
            "--output",
            os.fspath(workspace / "voxblame"),
            "--step",
            "0",
        ],
        cwd=REPO_ROOT,
        command_log=command_log,
    )
    native = _run_failure_operation(
        "native_measurement",
        "native_evidence",
        native_depth_eight_evidence,
        measured,
    )
    preview_dir = workspace / "work/preview-0"
    _run_failure_operation(
        "native_measurement",
        "voxblame_preview",
        _run_voxblame_preview,
        [
            sys.executable,
            os.fspath(MESH_COMPARE),
            "voxblame-preview",
            os.fspath(candidate / "built/measurement.glb"),
            "--reference",
            os.fspath(workspace / "input"),
            "--output",
            os.fspath(preview_dir),
            "--experiment",
            os.fspath(workspace / "experiment.json"),
            "--variant",
            "step",
        ],
        cwd=REPO_ROOT,
        command_log=command_log,
    )
    _run_failure_operation(
        "native_measurement",
        "step_publication",
        _run_public,
        [
            sys.executable,
            os.fspath(WORKSPACE_HELPER),
            "publish-step-zero",
            "--workspace",
            os.fspath(workspace),
            "--attempt",
            str(attempt),
            "--candidate",
            os.fspath(candidate),
            "--candidate-mesh",
            "built/measurement.glb",
            "--measurement",
            os.fspath(workspace / "voxblame/steps/000000/summary.json"),
            "--preview",
            os.fspath(preview_dir),
        ],
        cwd=REPO_ROOT,
        command_log=command_log,
    )
    return native


def _finalize_workspace(workspace: Path, command_log: Path) -> dict[str, Any]:
    preview = _loads_json_strict(
        (workspace / "steps/000000/preview/preview.json").read_text(
            encoding="utf-8"
        )
    )
    measurement_path = workspace / "steps/000000/measurement.json"
    selection = workspace / "work/final-selection.json"
    _write_json(
        selection,
        {
            "schema": "mesh-to-cad.final-selection/1",
            "considered_steps": [0],
            "selected_step": 0,
            "preview": {
                "identity_sha256": preview["preview_identity_sha256"],
                "observation": "The durable fixture preview was inspected by the provider-free smoke.",
                "evidence_conflict": False,
                "conflict_details": None,
            },
            "accepted": True,
            "stop_reason": "acceptance_satisfied",
            "evidence": [
                {
                    "kind": "measured_step",
                    "path": "steps/000000/measurement.json",
                    "sha256": _sha256(measurement_path),
                }
            ],
        },
    )
    notes = workspace / "work/final-notes.md"
    notes.write_text(
        "\n\n".join(
            (
                "## Input and Route\nDurable rectangular clamp CAD fixture.",
                "## Modeling Intent\nRebuild the checked-in deterministic generator.",
                "## Preserved Structural Features\nFixture geometry is preserved.",
                "## Omitted Surface Details\nNone.",
                "## Repair Trajectory\nProvider-free Step 0 smoke only.",
                "## Final Selection\nSelected Step 0.",
                "## Verification\nOffline rebuild and Observable Geometry verification are required.",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    registry = workspace / "work/tool-registry.json"
    registry_value = {
        "schema": "mesh-to-cad.tool-registry/1",
        "rebuild": {
            "id": "cad.canonical-build/1",
            "entrypoint_sha256": _sha256(CAD_BUILD_ENTRYPOINT),
        },
        "geometry": {
            "id": "mesh-compare.voxblame/1",
            "entrypoint_sha256": _sha256(MESH_COMPARE_ENTRYPOINT),
        },
    }
    registry_value["identity_sha256"] = _identity(
        "mesh-to-cad.tool-registry/1", registry_value
    )
    _write_json(registry, registry_value)
    return _run_public(
        [
            sys.executable,
            os.fspath(WORKSPACE_HELPER),
            "finalize",
            "--workspace",
            os.fspath(workspace),
            "--selection",
            os.fspath(selection),
            "--notes",
            os.fspath(notes),
            "--rebuild-entrypoint",
            os.fspath(CAD_BUILD_ENTRYPOINT),
            "--geometry-entrypoint",
            os.fspath(MESH_COMPARE_ENTRYPOINT),
            "--tool-registry",
            os.fspath(registry),
        ],
        cwd=REPO_ROOT,
        command_log=command_log,
    )


def _finalize_and_publish_runtime_authority(
    workspace: Path,
    command_log: Path,
    *,
    deployment: object,
    tree: object,
    cadpy_runtime: object,
    fallback: object,
    native: object,
) -> dict[str, Any]:
    """Finalize the Workspace and atomically publish the success receipt."""

    finalized = _finalize_workspace(workspace, command_log)
    receipt = {
        "schema": "issue15.runtime-authority-smoke/1",
        "scenario_identity": SCENARIO_IDENTITY,
        "workspace": {
            "path": ".",
            "schema": "mesh-to-cad.workspace/1",
            "final_delivery": finalized["final"],
        },
        "viewer_deployment": deployment,
        "viewer_fallback": fallback,
        "native_depth_eight": native,
        "cadpy_runtime": cadpy_runtime,
        "shipped_tree": tree,
        "commands": "run/provider-free-commands.jsonl",
        "preview_sandbox": PROVIDER_FREE_PREVIEW_SANDBOX_PATH,
    }
    _write_json(workspace / "run" / "runtime-authority-smoke.json", receipt)
    return receipt


def run_issue15_runtime_authority(workspace: Path) -> dict[str, Any]:
    command_log = workspace / "run/provider-free-commands.jsonl"
    deployment = _run_stage(
        "viewer_deployment", deployed_viewer_receipt, REPO_ROOT
    )
    tree = _run_stage("shipped_tree", deployed_runtime_tree_receipt, REPO_ROOT)
    cadpy_runtime = _run_stage("cadpy_runtime", cadpy_runtime_evidence)
    fallback = _run_stage(
        "viewer_fallback", viewer_fallback_evidence, workspace, deployment
    )
    candidate = _run_stage(
        "candidate_workspace",
        _prepare_candidate,
        workspace,
        command_log,
    )
    _run_stage(
        "candidate_workspace",
        _prepare_workspace,
        workspace,
        candidate,
        command_log,
    )
    native = _run_stage(
        "native_measurement",
        _publish_measured_step,
        workspace,
        candidate,
        command_log,
    )
    return _run_stage(
        "finalization",
        _finalize_and_publish_runtime_authority,
        workspace,
        command_log,
        deployment=deployment,
        tree=tree,
        cadpy_runtime=cadpy_runtime,
        fallback=fallback,
        native=native,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="provider-free-scenarios")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("scenario")
    run.add_argument("--workspace", type=Path, required=True)
    run_staged = subparsers.add_parser("run-staged")
    run_staged.add_argument("scenario")
    run_staged.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.scenario != "issue15-runtime-authority":
        print(f"provider-free-scenario: unknown scenario: {args.scenario!r}", file=sys.stderr)
        return 2
    workspace = args.workspace.resolve()
    try:
        if args.command == "run-staged":
            _run_stage(
                "native_measurement",
                _materialize_outer_browser_stage,
            )
        receipt = run_issue15_runtime_authority(workspace)
    except ScenarioError as exc:
        if exc.stage in PROVIDER_FREE_SCENARIO_FAILURE_STAGES:
            failure = {
                "schema": PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA,
                "scenario_identity": SCENARIO_IDENTITY,
                "stage": exc.stage,
            }
            if exc.operation is not None:
                failure["operation"] = exc.operation
            if (
                exc.operation == "preview_browser_identity"
                and exc.browser_identity_substage
                in PROVIDER_FREE_BROWSER_IDENTITY_SUBSTAGES
            ):
                failure["browser_identity_substage"] = (
                    exc.browser_identity_substage
                )
                if (
                    exc.browser_identity_substage
                    == "private_snapshot_launch_image_identity"
                    and exc.browser_identity_phase
                    in PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES
                ):
                    failure["browser_identity_phase"] = (
                        exc.browser_identity_phase
                    )
                    if (
                        exc.browser_identity_check
                        in provider_free_browser_identity_checks(
                            exc.browser_identity_phase
                        )
                    ):
                        failure["browser_identity_check"] = (
                            exc.browser_identity_check
                        )
            if (
                exc.operation == "preview_browser_cleanup"
                and provider_free_browser_cleanup_pair_allowed(
                    exc.browser_cleanup_substage,
                    exc.browser_cleanup_check,
                )
            ):
                diagnostic = {
                    "schema": PROVIDER_FREE_BROWSER_CLEANUP_DIAGNOSTIC_SCHEMA,
                    "substage": exc.browser_cleanup_substage,
                    "check": exc.browser_cleanup_check,
                }
                diagnostic_path = (
                    workspace / PROVIDER_FREE_BROWSER_CLEANUP_DIAGNOSTIC_PATH
                )
                _write_json(diagnostic_path, diagnostic)
                failure["browser_cleanup_diagnostic"] = {
                    "path": PROVIDER_FREE_BROWSER_CLEANUP_DIAGNOSTIC_PATH,
                    "sha256": hashlib.sha256(
                        diagnostic_path.read_bytes()
                    ).hexdigest(),
                }
            _write_json(
                workspace / PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                failure,
            )
            if (
                exc.operation == "preview_browser_identity"
                and exc.browser_identity_substage
                in PROVIDER_FREE_BROWSER_IDENTITY_SUBSTAGES
            ):
                phase_allowed = (
                    exc.browser_identity_substage
                    != "private_snapshot_launch_image_identity"
                    or exc.browser_identity_phase
                    in PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES
                )
                allowed_checks = provider_free_browser_identity_checks(
                    exc.browser_identity_phase
                )
                if allowed_checks:
                    phase_allowed = (
                        exc.browser_identity_check in allowed_checks
                    )
                failure_bytes = (
                    workspace / PROVIDER_FREE_SCENARIO_FAILURE_PATH
                ).read_bytes()
                if phase_allowed:
                    diagnostic = {
                        "schema": (
                            PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA
                        ),
                        "operation": "preview_browser_identity",
                        "substage": exc.browser_identity_substage,
                        "scenario_failure": {
                            "path": PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                            "sha256": hashlib.sha256(failure_bytes).hexdigest(),
                        },
                    }
                    if exc.browser_identity_phase is not None:
                        diagnostic["phase"] = exc.browser_identity_phase
                    if exc.browser_identity_check is not None:
                        diagnostic["check"] = exc.browser_identity_check
                    _write_json(
                        workspace
                        / PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH,
                        diagnostic,
                    )
        print(f"provider-free-scenario: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "scenario": receipt}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
