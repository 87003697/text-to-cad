"""Python-owned attested Chromium lifecycle behind one internal runtime seam."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import fcntl
import hashlib
from importlib import metadata, resources
import json
import os
from pathlib import Path
import re
import select
import secrets
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
from typing import Any, Iterator
from urllib.parse import urlsplit


RUNTIME_SCHEMA = "meshshot.prelaunched-cdp-runtime/1"
SUPERVISOR_PROTOCOL_SCHEMA = "meshshot.browser-supervisor/1"
SUPERVISOR_RUNTIME_MODE = "provider-free-supervised-cdp/1"
SUPERVISOR_OUTER_ROOT = Path("/meshshot-supervisor")
SUPERVISOR_OUTER_SOCKET = SUPERVISOR_OUTER_ROOT / "authority.sock"
SUPERVISOR_OUTER_AUTHORITY = SUPERVISOR_OUTER_ROOT / "client-authority.json"
SUPERVISOR_OUTER_CLIENT = SUPERVISOR_OUTER_ROOT / "expected-client.json"
SUPERVISOR_OUTER_RESULT = SUPERVISOR_OUTER_ROOT / "result.json"
SUPERVISOR_NESTED_ROOT = Path("/run/meshshot-supervisor")
SUPERVISOR_NESTED_SOCKET = SUPERVISOR_NESTED_ROOT / "authority.sock"
SUPERVISOR_NESTED_AUTHORITY = SUPERVISOR_NESTED_ROOT / "client-authority.json"
SUPERVISOR_AUTHORITY_SCHEMA = "meshshot.browser-supervisor-authority/1"
SUPERVISOR_CLIENT_SCHEMA = "meshshot.browser-supervisor-client/1"
SUPERVISOR_RESULT_SCHEMA = "meshshot.browser-supervisor-result/1"
SUPERVISOR_RESULT_RECORD_CLEANUP_EXIT = 78
_SUPERVISOR_PACKET_LIMIT = 16 * 1024
BROWSER_IDENTITY_SUBSTAGES = frozenset(
    {
        "private_snapshot_launch_image_identity",
        "live_running_image_identity",
        "loopback_listener_address_ownership",
        "connected_cdp_browser_version_identity",
        "runtime_evidence_cross_binding",
    }
)
PRIVATE_SNAPSHOT_IDENTITY_PHASES = frozenset(
    {
        "source_executable_identity",
        "private_tree_materialization",
        "private_launch_image_identity",
        "playwright_package_revision_identity",
        "private_launch_version_execution",
        "private_launch_version_output_identity",
    }
)
PLAYWRIGHT_PACKAGE_REVISION_CHECKS = frozenset(
    {
        "python_distribution_metadata",
        "playwright_package_manifest",
        "browser_manifest_entry",
        "frozen_playwright_version_match",
        "frozen_browser_revision_match",
    }
)
PRIVATE_VERSION_EXECUTION_CHECKS = frozenset(
    {
        "sealed_memfd_creation_policy",
        "private_version_helper_spawn_executable_missing",
        "private_version_helper_spawn_permission",
        "private_version_helper_spawn_process_limit",
        "private_version_helper_spawn_file_limit",
        "private_version_helper_spawn_address_space",
        "private_version_helper_spawn_other",
        "private_version_handoff_setup",
        "private_version_handoff_timeout",
        "private_version_helper_exec",
        "private_version_exec_replacement",
        "private_version_probe_completion",
        "private_version_probe_timeout",
    }
)
BROWSER_CLEANUP_CHECKS_BY_SUBSTAGE = {
    "nested_attachment_close": frozenset(
        {"browser_session_close", "completion_send", "shutdown_receive", "transport_close"}
    ),
    "private_browser_process_group": frozenset(
        {
            "term_signal", "leader_term_wait", "term_group_empty",
            "kill_signal", "leader_kill_wait", "kill_group_empty",
        }
    ),
    "private_browser_profile": frozenset(
        {
            "authority_validation", "quarantine_create", "quarantine_move",
            "recursive_remove", "authority_close", "absence",
        }
    ),
    "private_browser_pinned_image": frozenset(
        {"executable_descriptor_close", "detached_mount_release"}
    ),
    "private_browser_private_tree": frozenset(
        {
            "tree_descriptor_close", "directory_thaw", "recursive_remove",
            "authority_descriptor_close", "absence",
        }
    ),
    "private_browser_handoff": frozenset(
        {
            "socket_unlink", "authority_record_unlink",
            "authority_record_descriptor_close",
            "root_descriptor_close", "transport_close",
            "pipe_descriptor_close", "process_group_cleanup",
        }
    ),
    "private_supervisor_state": frozenset(
        {
            "client_transport_close", "listener_close", "socket_unlink",
            "root_identity", "authority_record_unlink", "client_record_unlink",
            "root_descriptor_close",
        }
    ),
    "private_supervisor_record_descriptors": frozenset(
        {
            "authority_record_descriptor_close",
            "client_record_descriptor_close",
            "result_record_descriptor_close",
        }
    ),
    "outer_browser_stage": frozenset(
        {
            "tree_copy_descriptor_close", "revision_descriptor_close",
            "destination_descriptor_close", "source_descriptor_close",
        }
    ),
    "nested_public_child": frozenset(
        {"termination_signal", "completion_reap"}
    ),
    "outer_supervisor_wait": frozenset({"supervisor_wait", "supervisor_exit_status"}),
    "outer_supervisor_process_group": frozenset(
        {
            "term_signal", "leader_term_wait", "term_group_empty",
            "kill_signal", "leader_kill_wait", "kill_group_empty",
        }
    ),
    "outer_supervisor_private_state": frozenset(
        {"socket_absence", "authority_absence", "client_absence"}
    ),
}


def _private_version_execution_error(check: str) -> BrowserRuntimeError:
    return BrowserRuntimeError(
        "browser_identity",
        browser_identity_phase="private_launch_version_execution",
        browser_identity_check=check,
    )


def _private_version_helper_spawn_check(exc: OSError) -> str:
    if exc.errno in {errno.ENOENT, errno.ENOTDIR}:
        return "private_version_helper_spawn_executable_missing"
    if exc.errno in {errno.EACCES, errno.EPERM, errno.ETXTBSY}:
        return "private_version_helper_spawn_permission"
    if exc.errno == errno.EAGAIN:
        return "private_version_helper_spawn_process_limit"
    if exc.errno in {errno.EMFILE, errno.ENFILE}:
        return "private_version_helper_spawn_file_limit"
    if exc.errno == errno.ENOMEM:
        return "private_version_helper_spawn_address_space"
    return "private_version_helper_spawn_other"
_SourceIdentity = tuple[int, int, int, int]
_PROFILE_RESOURCE = "prelaunched_cdp_playwright_1_60_v1.json"
_FD_EXEC_HANDOFF = Path(__file__).with_name("fd_exec_handoff.py")
_FD_EXEC_HANDOFF_SCHEMA = "meshshot.fd-exec-handoff/1"
_BROWSER_MOUNT_HANDOFF = Path(__file__).with_name("browser_mount_handoff.py")
_BROWSER_MOUNT_SCHEMA = "meshshot.browser-mount-handoff/1"
_BROWSER_MOUNT_AUTHORITY_SCHEMA = "meshshot.browser-mount-authority/1"
_BROWSER_MOUNT_SOCKET = SUPERVISOR_OUTER_ROOT / "browser-mount.sock"
_BROWSER_MOUNT_AUTHORITY = SUPERVISOR_OUTER_ROOT / (
    "browser-mount-authority.json"
)
_BROWSER_MOUNT_TARGET = Path("/run/meshshot-browser/attested")
_BROWSER_MOUNT_EXECUTABLE = _BROWSER_MOUNT_TARGET / (
    "chrome-headless-shell-linux64/chrome-headless-shell"
)
_BROWSER_TREE_MANIFEST_ENV = "MESHSHOT_BROWSER_TREE_MANIFEST_SHA256"
_BROWSER_TREE_MANIFEST_SCHEMA = "meshshot.browser-tree-manifest/1"
_BROWSER_EXECUTION_AUTHORITY_SCHEMA = "meshshot.browser-execution-authority/1"
_TRUSTED_BWRAP = Path("/usr/bin/bwrap")
_FD_EXEC_ENVIRONMENT = frozenset(
    {"HOME", "LANG", "LC_ALL", "PATH", "TMPDIR", "TZ"}
)
_FD_EXEC_CLEANUP_TERM_SECONDS = 1.0
_FD_EXEC_CLEANUP_KILL_SECONDS = 1.0
MESHSHOT_EXECUTABLE_ROOT = Path("/meshshot-exec")
_MESHSHOT_EXECUTABLE_ROOT_ENV = "MESHSHOT_EXECUTABLE_ROOT"
_LINUX_TMPFS_MAGIC = 0x01021994
ADAPTER_PROFILE_SHA256 = "16ef68d9ee9700f10c9e92b6ca88c0430dc98c6808145258f9a6125f3acd5c04"
_DEVTOOLS_PATH = re.compile(r"^/devtools/browser/[0-9A-Za-z._-]+$")
_VERSION_OUTPUT = re.compile(
    r"^(?:Google Chrome for Testing|Chromium|Chrome|HeadlessChrome) "
    r"[0-9]+(?:\.[0-9]+){3}$"
)


class _LinuxStatFs(ctypes.Structure):
    _fields_ = [
        ("f_type", ctypes.c_long),
        ("_opaque", ctypes.c_byte * 248),
    ]


def _linux_filesystem_type(descriptor: int) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    fstatfs = libc.fstatfs
    fstatfs.argtypes = [ctypes.c_int, ctypes.POINTER(_LinuxStatFs)]
    fstatfs.restype = ctypes.c_int
    result = _LinuxStatFs()
    if fstatfs(descriptor, ctypes.byref(result)) != 0:
        raise OSError("private filesystem identity is unavailable")
    return int(result.f_type)


def _browser_execution_authority(
    tree_manifest_sha256: str, executable_sha256: str
) -> dict[str, str]:
    if (
        re.fullmatch(r"[0-9a-f]{64}", tree_manifest_sha256) is None
        or re.fullmatch(r"[0-9a-f]{64}", executable_sha256) is None
    ):
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_substage="runtime_evidence_cross_binding",
        )
    return {
        "schema": _BROWSER_EXECUTION_AUTHORITY_SCHEMA,
        "mode": "linux-detached-readonly-revision-mount/1",
        "tree_manifest_sha256": tree_manifest_sha256,
        "executable_sha256": executable_sha256,
        "mount_readonly": "passed",
        "source_detached": "passed",
    }


def _prelaunch_operation(exc: OSError) -> str:
    detail = str(exc).casefold()
    if any(value in detail for value in ("eagain", "cannot fork", "pthread_create")):
        return "browser_launch_process_limit"
    if any(value in detail for value in ("emfile", "enfile", "too many open files")):
        return "browser_launch_file_limit"
    if any(value in detail for value in ("enomem", "cannot allocate memory", "out of memory")):
        return "browser_launch_address_space"
    if any(value in detail for value in ("/dev/shm", "shared memory")):
        return "browser_launch_shared_memory"
    if any(value in detail for value in ("enoent", "no such file or directory")):
        return "browser_launch_executable_missing"
    if "error while loading shared libraries" in detail:
        return "browser_launch_executable_dependency"
    if any(value in detail for value in ("erofs", "read-only file system")):
        return "browser_launch_filesystem_permission"
    if "permission denied" in detail and any(
        value in detail for value in ("user data", "user-data-dir", "directory")
    ):
        return "browser_launch_filesystem_permission"
    if "permission denied" in detail and any(
        value in detail for value in ("zygote", "sandbox", "namespace")
    ):
        return "browser_launch_sandbox_permission"
    if any(value in detail for value in ("eacces", "eperm", "permission denied")):
        return (
            "browser_launch_executable_spawn_permission"
            if any(value in detail for value in ("spawn", "execve"))
            else "browser_launch_executable_permission"
        )
    return "browser_launch"


class BrowserRuntimeError(RuntimeError):
    """The private browser runtime rejected or failed a closed lifecycle stage."""

    def __init__(
        self,
        operation: str,
        *,
        browser_identity_substage: str | None = None,
        browser_identity_phase: str | None = None,
        browser_identity_check: str | None = None,
        browser_cleanup_substage: str | None = None,
        browser_cleanup_check: str | None = None,
        _browser_cleanup_retained: bool = False,
    ) -> None:
        super().__init__(operation)
        self.operation = operation
        self.browser_identity_substage = (
            browser_identity_substage
            if operation == "browser_identity"
            else None
        )
        if operation == "browser_identity" and self.browser_identity_substage is None:
            self.browser_identity_substage = (
                "private_snapshot_launch_image_identity"
            )
        self.browser_identity_phase = (
            browser_identity_phase
            if (
                operation == "browser_identity"
                and self.browser_identity_substage
                == "private_snapshot_launch_image_identity"
                and browser_identity_phase in PRIVATE_SNAPSHOT_IDENTITY_PHASES
            )
            else None
        )
        self.browser_identity_check = (
            browser_identity_check
            if (
                (
                    self.browser_identity_phase
                    == "playwright_package_revision_identity"
                    and browser_identity_check
                    in PLAYWRIGHT_PACKAGE_REVISION_CHECKS
                )
                or (
                    self.browser_identity_phase
                    == "private_launch_version_execution"
                    and browser_identity_check in PRIVATE_VERSION_EXECUTION_CHECKS
                )
            )
            else None
        )
        cleanup_checks = BROWSER_CLEANUP_CHECKS_BY_SUBSTAGE.get(
            browser_cleanup_substage,
            frozenset(),
        )
        self.browser_cleanup_substage = (
            browser_cleanup_substage
            if operation == "browser_cleanup" and browser_cleanup_check in cleanup_checks
            else None
        )
        self.browser_cleanup_check = (
            browser_cleanup_check
            if self.browser_cleanup_substage is not None
            else None
        )
        self._browser_cleanup_retained = bool(
            self.browser_cleanup_substage is not None
            and _browser_cleanup_retained
        )


def _is_typed_browser_cleanup(exc: BaseException) -> bool:
    return (
        isinstance(exc, BrowserRuntimeError)
        and exc.operation == "browser_cleanup"
        and exc.browser_cleanup_substage is not None
        and exc.browser_cleanup_check is not None
    )


def _is_retained_browser_cleanup(exc: BaseException) -> bool:
    return bool(
        _is_typed_browser_cleanup(exc)
        and getattr(exc, "_browser_cleanup_retained", False)
    )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _loads_json_strict(raw: bytes) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_substage="runtime_evidence_cross_binding",
        ) from exc


def _send_supervisor_packet(connection: socket.socket, payload: object) -> None:
    raw = _canonical_bytes(payload)
    if not raw or len(raw) > _SUPERVISOR_PACKET_LIMIT:
        raise BrowserRuntimeError("browser_connect")
    try:
        sent = connection.send(raw)
    except OSError as exc:
        raise BrowserRuntimeError("browser_connect") from exc
    if sent != len(raw):
        raise BrowserRuntimeError("browser_connect")


def _receive_supervisor_packet(connection: socket.socket) -> Any:
    try:
        raw = connection.recv(_SUPERVISOR_PACKET_LIMIT + 1)
    except (OSError, socket.timeout) as exc:
        raise BrowserRuntimeError("browser_connect") from exc
    if not raw or len(raw) > _SUPERVISOR_PACKET_LIMIT:
        raise BrowserRuntimeError("browser_connect")
    return _loads_json_strict(raw)


def _peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    # Linux SO_PEERCRED is stable ABI value 17. The numeric fallback keeps the
    # parser unit-testable on Darwin; production attachment is Linux-only.
    option = getattr(socket, "SO_PEERCRED", 17)
    try:
        raw = connection.getsockopt(socket.SOL_SOCKET, option, 12)
        pid, uid, gid = struct.unpack("3i", raw)
    except (OSError, struct.error, TypeError, ValueError) as exc:
        raise BrowserRuntimeError("browser_connect") from exc
    if pid <= 1 or uid < 0 or gid < 0:
        raise BrowserRuntimeError("browser_connect")
    return pid, uid, gid


def _load_profile() -> tuple[dict[str, Any], str]:
    raw = (
        resources.files("meshshot")
        .joinpath("profiles")
        .joinpath(_PROFILE_RESOURCE)
        .read_bytes()
    )
    if hashlib.sha256(raw).hexdigest() != ADAPTER_PROFILE_SHA256:
        raise BrowserRuntimeError("browser_adapter_profile")
    try:
        profile = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BrowserRuntimeError("browser_adapter_profile") from exc
    required = {
        "schema",
        "name",
        "playwright",
        "browser",
        "revision",
        "browser_version",
        "startup_timeout_ms",
        "cleanup_term_ms",
        "cleanup_kill_ms",
        "arguments",
    }
    if (
        not isinstance(profile, dict)
        or set(profile) != required
        or profile.get("schema") != "meshshot.browser-adapter-profile/1"
        or not isinstance(profile.get("arguments"), list)
        or not all(isinstance(value, str) for value in profile["arguments"])
    ):
        raise BrowserRuntimeError("browser_adapter_profile")
    return profile, ADAPTER_PROFILE_SHA256


def _playwright_revision(browser_name: str) -> str:
    try:
        import playwright

        manifest_path = (
            Path(playwright.__file__).resolve().parent
            / "driver/package/browsers.json"
        )
        manifest_text = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)
    # ValueError covers bounded UTF-8 and JSON decoding failures. Control-flow
    # BaseExceptions remain outside this data-boundary classification.
    except (
        ImportError,
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,
    ) as exc:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="playwright_package_revision_identity",
            browser_identity_check="playwright_package_manifest",
        ) from exc
    if (
        not isinstance(manifest, dict)
        or not isinstance(manifest.get("browsers"), list)
    ):
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="playwright_package_revision_identity",
            browser_identity_check="playwright_package_manifest",
        )
    entries = manifest["browsers"]
    if not all(isinstance(item, dict) for item in entries):
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="playwright_package_revision_identity",
            browser_identity_check="browser_manifest_entry",
        )
    matches = [item for item in entries if item.get("name") == browser_name]
    if (
        len(matches) != 1
        or not isinstance(matches[0].get("revision"), str)
    ):
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="playwright_package_revision_identity",
            browser_identity_check="browser_manifest_entry",
        )
    return matches[0]["revision"]


@dataclass(frozen=True)
class _SelectedExecutable:
    path: Path
    source_identity: _SourceIdentity


_LINUX_LIVE_IMAGE_PROOF = object()


@dataclass(frozen=True)
class _LiveBrowserLaunch:
    process: subprocess.Popen[bytes]
    _proof: object


def _close_browser_descriptor(
    descriptor: int,
    *,
    cleanup_substage: str,
    cleanup_check: str,
) -> None:
    try:
        os.close(descriptor)
    except OSError as exc:
        raise BrowserRuntimeError(
            "browser_cleanup",
            browser_cleanup_substage=cleanup_substage,
            browser_cleanup_check=cleanup_check,
        ) from exc


def default_executable(chromium_executable: str) -> _SelectedExecutable:
    """Resolve the exact headless-shell sibling installed by Playwright."""

    profile, _profile_sha256 = _load_profile()
    try:
        full_browser = Path(chromium_executable).resolve(strict=True)
    except OSError as exc:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="source_executable_identity",
        ) from exc
    revision_dir = next(
        (
            parent
            for parent in full_browser.parents
            if parent.name == f"chromium-{profile['revision']}"
        ),
        None,
    )
    if revision_dir is None:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="source_executable_identity",
        )
    shell_revision = revision_dir.parent / (
        f"chromium_headless_shell-{profile['revision']}"
    )
    try:
        candidates = [
            path
            for path in shell_revision.glob(
                "chrome-headless-shell-*/chrome-headless-shell"
            )
            if path.is_file() and not path.is_symlink()
        ]
    except OSError as exc:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="source_executable_identity",
        ) from exc
    if len(candidates) != 1:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="source_executable_identity",
        )
    candidate = candidates[0]
    try:
        shell_lstat = shell_revision.lstat()
        resolved_shell_revision = shell_revision.resolve(strict=True)
        candidate_lstat = candidate.lstat()
        resolved_candidate = candidate.resolve(strict=True)
        resolved_lstat = resolved_candidate.lstat()
        relative_candidate = resolved_candidate.relative_to(
            resolved_shell_revision
        )
    except (OSError, ValueError) as exc:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="source_executable_identity",
        ) from exc
    if (
        not stat.S_ISDIR(shell_lstat.st_mode)
        or shell_revision != resolved_shell_revision
        or not stat.S_ISREG(candidate_lstat.st_mode)
        or candidate_lstat.st_mode
        & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        == 0
        or candidate != resolved_candidate
        or len(relative_candidate.parts) != 2
        or relative_candidate.parts[1] != "chrome-headless-shell"
        or not relative_candidate.parts[0].startswith("chrome-headless-shell-")
        or not stat.S_ISREG(resolved_lstat.st_mode)
        or resolved_lstat.st_mode
        & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        == 0
        or (candidate_lstat.st_dev, candidate_lstat.st_ino)
        != (resolved_lstat.st_dev, resolved_lstat.st_ino)
    ):
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="source_executable_identity",
        )
    return _SelectedExecutable(
        path=resolved_candidate,
        source_identity=(
            resolved_lstat.st_dev,
            resolved_lstat.st_ino,
            resolved_lstat.st_size,
            resolved_lstat.st_mode,
        ),
    )


def _attest(executable: _PinnedExecutable, profile: dict[str, Any]) -> dict[str, str]:
    try:
        playwright_version = metadata.version("playwright")
    # Distribution discovery can fail while reading or parsing installed
    # metadata; ValueError includes its decoding failures.
    except (
        metadata.PackageNotFoundError,
        OSError,
        ValueError,
        TypeError,
    ) as exc:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="playwright_package_revision_identity",
            browser_identity_check="python_distribution_metadata",
        ) from exc
    revision = _playwright_revision(str(profile["browser"]))
    if playwright_version != profile["playwright"]:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="playwright_package_revision_identity",
            browser_identity_check="frozen_playwright_version_match",
        )
    if revision != profile["revision"]:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="playwright_package_revision_identity",
            browser_identity_check="frozen_browser_revision_match",
        )
    try:
        completed = executable.run_version(
            float(profile["startup_timeout_ms"]) / 1000
        )
    except BrowserRuntimeError as exc:
        if exc.operation != "browser_identity":
            raise
        if exc.browser_identity_phase is not None:
            raise
        raise _private_version_execution_error(
            "private_version_probe_completion"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="private_launch_version_execution",
            browser_identity_check="private_version_probe_timeout",
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise _private_version_execution_error(
            "private_version_probe_completion"
        ) from exc
    try:
        version = completed.stdout.decode("utf-8").strip()
    except (AttributeError, UnicodeDecodeError) as exc:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="private_launch_version_output_identity",
        ) from exc
    if (
        completed.returncode != 0
        or completed.stderr != b""
        or not _VERSION_OUTPUT.fullmatch(version)
        or version.rsplit(" ", 1)[-1] != profile["browser_version"]
    ):
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="private_launch_version_output_identity",
        )
    try:
        executable_sha256 = executable.sha256()
    except OSError as exc:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="private_launch_image_identity",
        ) from exc
    return {
        "playwright": playwright_version,
        "browser": str(profile["browser"]),
        "revision": revision,
        "version": version,
        "sha256": executable_sha256,
    }


def _attest_attachment(executable: Path, profile: dict[str, Any]) -> dict[str, str]:
    """Reconstruct the frozen identity without executing in the nested sandbox."""

    try:
        playwright_version = metadata.version("playwright")
    except (
        metadata.PackageNotFoundError,
        OSError,
        ValueError,
        TypeError,
    ) as exc:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="playwright_package_revision_identity",
            browser_identity_check="python_distribution_metadata",
        ) from exc
    revision = _playwright_revision(str(profile["browser"]))
    if playwright_version != profile["playwright"]:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="playwright_package_revision_identity",
            browser_identity_check="frozen_playwright_version_match",
        )
    if revision != profile["revision"]:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="playwright_package_revision_identity",
            browser_identity_check="frozen_browser_revision_match",
        )
    descriptor: int | None = None
    digest = hashlib.sha256()
    failure: BaseException | None = None
    try:
        descriptor = os.open(
            executable,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptor_info = os.fstat(descriptor)
        path_info = executable.lstat()
        if (
            not executable.is_absolute()
            or not stat.S_ISREG(descriptor_info.st_mode)
            or stat.S_ISLNK(path_info.st_mode)
            or (descriptor_info.st_dev, descriptor_info.st_ino)
            != (path_info.st_dev, path_info.st_ino)
            or descriptor_info.st_mode
            & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            == 0
        ):
            raise OSError("browser identity is not an executable regular file")
        while True:
            chunk = os.read(descriptor, 4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
        ) != (
            descriptor_info.st_dev,
            descriptor_info.st_ino,
            descriptor_info.st_size,
            descriptor_info.st_mode,
        ):
            raise OSError("browser identity changed")
    except (OSError, TypeError, ValueError) as exc:
        failure = BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="source_executable_identity",
        )
        failure.__cause__ = exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_pinned_image",
                    browser_cleanup_check="executable_descriptor_close",
                ) from exc
    if failure is not None:
        raise failure
    return {
        "playwright": playwright_version,
        "browser": str(profile["browser"]),
        "revision": revision,
        "version": f"Google Chrome for Testing {profile['browser_version']}",
        "sha256": digest.hexdigest(),
    }


@dataclass
class _OwnedPrivateDirectory:
    path: Path
    parent_fd: int | None
    directory_fd: int | None
    identity: tuple[int, int]

    def close_authority(self) -> None:
        cleanup_failed = False
        for attribute in ("directory_fd", "parent_fd"):
            descriptor = getattr(self, attribute)
            setattr(self, attribute, None)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    cleanup_failed = True
        if cleanup_failed:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_private_tree",
                browser_cleanup_check="authority_descriptor_close",
            )


def _private_directory(prefix: str) -> _OwnedPrivateDirectory:
    configured_root = os.environ.get(_MESHSHOT_EXECUTABLE_ROOT_ENV)
    if configured_root is not None:
        if (
            not sys.platform.startswith("linux")
            or Path(configured_root) != MESHSHOT_EXECUTABLE_ROOT
        ):
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            )
        root = MESHSHOT_EXECUTABLE_ROOT
        try:
            root_info = root.lstat()
        except OSError as exc:
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            ) from exc
        if (
            not root.is_absolute()
            or stat.S_ISLNK(root_info.st_mode)
            or not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.geteuid()
            or stat.S_IMODE(root_info.st_mode) & 0o022
        ):
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            )
    else:
        root = Path(tempfile.gettempdir())
        try:
            root_info = root.lstat()
        except OSError as exc:
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            ) from exc
        if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            )
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    parent_fd: int | None = None
    try:
        parent_fd = os.open(root, flags)
        opened_root = os.fstat(parent_fd)
        if (opened_root.st_dev, opened_root.st_ino, opened_root.st_mode) != (
            root_info.st_dev,
            root_info.st_ino,
            root_info.st_mode,
        ):
            raise OSError("private root identity changed")
    except OSError as exc:
        if parent_fd is not None:
            try:
                os.close(parent_fd)
            except OSError as close_error:
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_private_tree",
                    browser_cleanup_check="authority_descriptor_close",
                ) from close_error
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="private_tree_materialization",
        ) from exc
    for _attempt in range(16):
        name = f"{prefix}{secrets.token_hex(16)}"
        path = root / name
        directory_fd: int | None = None
        created_identity: tuple[int, int] | None = None
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        except OSError as exc:
            try:
                os.close(parent_fd)
            except OSError as close_error:
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_private_tree",
                    browser_cleanup_check="authority_descriptor_close",
                ) from close_error
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            ) from exc
        try:
            created = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            created_identity = (created.st_dev, created.st_ino)
            directory_fd = os.open(name, flags, dir_fd=parent_fd)
            opened = os.fstat(directory_fd)
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            os.fchmod(directory_fd, 0o700)
            after = os.fstat(directory_fd)
        except OSError as exc:
            cleanup_failed = False
            if directory_fd is not None:
                try:
                    os.close(directory_fd)
                except OSError:
                    cleanup_failed = True
            try:
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if created_identity is not None and (
                    current.st_dev,
                    current.st_ino,
                ) == created_identity:
                    os.rmdir(name, dir_fd=parent_fd)
                else:
                    cleanup_failed = True
            except OSError:
                cleanup_failed = True
            try:
                os.close(parent_fd)
            except OSError:
                cleanup_failed = True
            if cleanup_failed:
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_private_tree",
                    browser_cleanup_check="authority_descriptor_close",
                ) from exc
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            ) from exc
        if (
            not stat.S_ISDIR(created.st_mode)
            or stat.S_ISLNK(created.st_mode)
                or (opened.st_dev, opened.st_ino) != created_identity
                or (current.st_dev, current.st_ino) != created_identity
                or (after.st_dev, after.st_ino) != created_identity
                or opened.st_uid != os.geteuid()
                or current.st_uid != os.geteuid()
                or after.st_uid != os.geteuid()
                or stat.S_IMODE(current.st_mode) != 0o700
                or stat.S_IMODE(after.st_mode) != 0o700
        ):
            cleanup_failed = False
            try:
                os.close(directory_fd)
            except OSError:
                cleanup_failed = True
            try:
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == created_identity:
                    os.rmdir(name, dir_fd=parent_fd)
            except OSError:
                cleanup_failed = True
            try:
                os.close(parent_fd)
            except OSError:
                cleanup_failed = True
            if cleanup_failed:
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_private_tree",
                    browser_cleanup_check="authority_descriptor_close",
                )
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_private_tree",
                browser_cleanup_check="absence",
            )
        return _OwnedPrivateDirectory(
            path=path,
            parent_fd=parent_fd,
            directory_fd=directory_fd,
            identity=created_identity,
        )
    try:
        os.close(parent_fd)
    except OSError as exc:
        raise BrowserRuntimeError(
            "browser_cleanup",
            browser_cleanup_substage="private_browser_private_tree",
            browser_cleanup_check="authority_descriptor_close",
        ) from exc
    raise BrowserRuntimeError(
        "browser_identity",
        browser_identity_phase="private_tree_materialization",
    )


def _private_mount_root() -> _OwnedPrivateDirectory:
    """Open the kernel-created provider-free mount root as the authority."""

    if (
        not sys.platform.startswith("linux")
        or os.environ.get(_MESHSHOT_EXECUTABLE_ROOT_ENV)
        != os.fspath(MESHSHOT_EXECUTABLE_ROOT)
    ):
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="private_tree_materialization",
        )
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(MESHSHOT_EXECUTABLE_ROOT, flags)
        os.fchmod(descriptor, 0o700)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
            or _linux_filesystem_type(descriptor) != _LINUX_TMPFS_MAGIC
        ):
            raise OSError("private mount root is invalid")
    except OSError as exc:
        if descriptor is not None:
            _close_browser_descriptor(
                descriptor,
                cleanup_substage="private_browser_private_tree",
                cleanup_check="authority_descriptor_close",
            )
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="private_tree_materialization",
        ) from exc
    return _OwnedPrivateDirectory(
        path=MESHSHOT_EXECUTABLE_ROOT,
        parent_fd=None,
        directory_fd=descriptor,
        identity=(info.st_dev, info.st_ino),
    )


def _private_child_directory(
    parent: Path,
    parent_fd: int,
    prefix: str,
) -> Path:
    """Create an owned private directory on an already-authorized filesystem."""

    for _attempt in range(16):
        name = f"{prefix}{secrets.token_hex(16)}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        except OSError as exc:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_private_tree",
                browser_cleanup_check="absence",
            ) from exc
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_private_tree",
                browser_cleanup_check="absence",
            ) from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_private_tree",
                browser_cleanup_check="absence",
            )
        return parent / name
    raise BrowserRuntimeError(
        "browser_cleanup",
        browser_cleanup_substage="private_browser_private_tree",
        browser_cleanup_check="absence",
    )


class _PinnedExecutable:
    """Own one exact executable image from attestation through production exec."""

    def __init__(
        self,
        path: Path,
        expected_source_identity: _SourceIdentity | None = None,
    ) -> None:
        self.path = path
        self.fd: int | None = None
        self.launch_path: Path | None = None
        self.launch_root: Path | None = None
        self._source_identity: _SourceIdentity | None = None
        self._detached_mount_mode = bool(
            sys.platform.startswith("linux")
            and os.environ.get(_BROWSER_TREE_MANIFEST_ENV)
        )
        self._detached_filesystem_mounted = False
        self._detached_mount_parent_fd: int | None = None
        self._detached_mount_fd: int | None = None
        self._detached_underlying_fd: int | None = None
        self._detached_mount_name: str | None = None
        self._detached_underlying_identity: tuple[int, int] | None = None
        self._detached_mounted_identity: tuple[int, int] | None = None
        self.tree_manifest_sha256: str | None = None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        source_fd: int | None = None
        try:
            source_fd = os.open(path, flags)
            source_info = os.fstat(source_fd)
        except OSError as exc:
            if source_fd is not None:
                _close_browser_descriptor(
                    source_fd,
                    cleanup_substage="private_browser_pinned_image",
                    cleanup_check="executable_descriptor_close",
                )
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="source_executable_identity",
            ) from exc
        actual_source_identity = (
            source_info.st_dev,
            source_info.st_ino,
            source_info.st_size,
            source_info.st_mode,
        )
        self._source_identity = actual_source_identity
        if (
            not path.is_absolute()
            or not stat.S_ISREG(source_info.st_mode)
            or source_info.st_mode & 0o111 == 0
            or (
                expected_source_identity is not None
                and actual_source_identity != expected_source_identity
            )
        ):
            _close_browser_descriptor(
                source_fd,
                cleanup_substage="private_browser_pinned_image",
                cleanup_check="executable_descriptor_close",
            )
            source_fd = None
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="source_executable_identity",
            )
        failure: BaseException | None = None
        cleanup_error: BrowserRuntimeError | None = None
        try:
            self._materialize_private_image(source_fd, source_info)
        except BaseException as exc:
            failure = exc
            if (
                isinstance(exc, BrowserRuntimeError)
                and exc.operation == "browser_cleanup"
                and exc.browser_cleanup_substage is not None
                and exc.browser_cleanup_check is not None
            ):
                cleanup_error = exc
            try:
                self.close()
            except BrowserRuntimeError as image_cleanup:
                if cleanup_error is None:
                    cleanup_error = image_cleanup
        finally:
            if source_fd is not None:
                try:
                    _close_browser_descriptor(
                        source_fd,
                        cleanup_substage="private_browser_pinned_image",
                        cleanup_check="executable_descriptor_close",
                    )
                except BrowserRuntimeError as source_cleanup:
                    if cleanup_error is None:
                        cleanup_error = source_cleanup
                    if failure is None:
                        try:
                            self.close()
                        except BrowserRuntimeError as image_cleanup:
                            if cleanup_error is None:
                                cleanup_error = image_cleanup
        if cleanup_error is not None:
            raise cleanup_error from failure
        if failure is not None:
            raise failure

    @staticmethod
    def _tree_manifest_sha256(root: Path) -> str:
        entries: list[dict[str, object]] = []
        folded: set[str] = set()
        root_fd: int | None = None

        def visit(directory_fd: int, relative: str) -> None:
            info = os.fstat(directory_fd)
            collision = relative.casefold()
            if collision in folded or not stat.S_ISDIR(info.st_mode):
                raise OSError("browser tree contains a colliding entry")
            folded.add(collision)
            entries.append(
                {
                    "path": relative,
                    "kind": "directory",
                    "mode": stat.S_IMODE(info.st_mode),
                }
            )
            for name in sorted(os.listdir(directory_fd)):
                if not name or name in {".", ".."} or "/" in name:
                    raise OSError("browser tree contains an invalid entry")
                child_relative = name if relative == "." else f"{relative}/{name}"
                child_info = os.stat(
                    name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if stat.S_ISLNK(child_info.st_mode):
                    raise OSError("browser tree contains a symbolic link")
                if stat.S_ISDIR(child_info.st_mode):
                    child_fd = os.open(
                        name,
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory_fd,
                    )
                    try:
                        opened = os.fstat(child_fd)
                        if (opened.st_dev, opened.st_ino, opened.st_mode) != (
                            child_info.st_dev,
                            child_info.st_ino,
                            child_info.st_mode,
                        ):
                            raise OSError("browser tree identity changed")
                        visit(child_fd, child_relative)
                        current = os.stat(
                            name,
                            dir_fd=directory_fd,
                            follow_symlinks=False,
                        )
                        after = os.fstat(child_fd)
                        if (current.st_dev, current.st_ino, current.st_mode) != (
                            opened.st_dev,
                            opened.st_ino,
                            opened.st_mode,
                        ) or (after.st_dev, after.st_ino, after.st_mode) != (
                            opened.st_dev,
                            opened.st_ino,
                            opened.st_mode,
                        ):
                            raise OSError("browser tree changed while hashing")
                    finally:
                        _close_browser_descriptor(
                            child_fd,
                            cleanup_substage="private_browser_private_tree",
                            cleanup_check="tree_descriptor_close",
                        )
                    continue
                if not stat.S_ISREG(child_info.st_mode):
                    raise OSError("browser tree contains an unsupported entry")
                descriptor = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                try:
                    opened = os.fstat(descriptor)
                    if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mode) != (
                        child_info.st_dev,
                        child_info.st_ino,
                        child_info.st_size,
                        child_info.st_mode,
                    ):
                        raise OSError("browser tree identity changed")
                    digest = hashlib.sha256()
                    while True:
                        chunk = os.read(descriptor, 1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                    after = os.fstat(descriptor)
                    current = os.stat(
                        name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    if (after.st_dev, after.st_ino, after.st_size, after.st_mode) != (
                        opened.st_dev,
                        opened.st_ino,
                        opened.st_size,
                        opened.st_mode,
                    ) or (current.st_dev, current.st_ino, current.st_size, current.st_mode) != (
                        opened.st_dev,
                        opened.st_ino,
                        opened.st_size,
                        opened.st_mode,
                    ):
                        raise OSError("browser tree changed while hashing")
                finally:
                    _close_browser_descriptor(
                        descriptor,
                        cleanup_substage="private_browser_private_tree",
                        cleanup_check="tree_descriptor_close",
                    )
                collision = child_relative.casefold()
                if collision in folded:
                    raise OSError("browser tree contains a colliding entry")
                folded.add(collision)
                entries.append(
                    {
                        "path": child_relative,
                        "kind": "file",
                        "mode": stat.S_IMODE(child_info.st_mode),
                        "sha256": digest.hexdigest(),
                    }
                )

        try:
            root_info = root.lstat()
            if stat.S_ISLNK(root_info.st_mode):
                raise OSError("browser tree contains a symbolic link")
            root_fd = os.open(
                root,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            opened_root = os.fstat(root_fd)
            if (opened_root.st_dev, opened_root.st_ino, opened_root.st_mode) != (
                root_info.st_dev,
                root_info.st_ino,
                root_info.st_mode,
            ):
                raise OSError("browser tree root changed")
            visit(root_fd, ".")
        except (OSError, ValueError) as exc:
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            ) from exc
        finally:
            if root_fd is not None:
                _close_browser_descriptor(
                    root_fd,
                    cleanup_substage="private_browser_private_tree",
                    cleanup_check="tree_descriptor_close",
                )
        entries.sort(key=lambda entry: str(entry["path"]))
        raw = json.dumps(
            {"schema": _BROWSER_TREE_MANIFEST_SCHEMA, "entries": entries},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _snapshot_resource(
        source: Path,
        target: Path,
        ancestors: frozenset[tuple[int, int]] = frozenset(),
    ) -> None:
        try:
            info = source.stat()
            if stat.S_ISDIR(info.st_mode):
                identity = (info.st_dev, info.st_ino)
                if identity in ancestors:
                    raise BrowserRuntimeError(
                        "browser_identity",
                        browser_identity_phase="private_tree_materialization",
                    )
                target.mkdir(mode=0o700)
                os.chmod(target, 0o700)
                for child in source.iterdir():
                    _PinnedExecutable._snapshot_resource(
                        child,
                        target / child.name,
                        ancestors | {identity},
                    )
                return
            if not stat.S_ISREG(info.st_mode):
                raise BrowserRuntimeError(
                    "browser_identity",
                    browser_identity_phase="private_tree_materialization",
                )
            shutil.copyfile(source, target, follow_symlinks=True)
            os.chmod(target, stat.S_IMODE(info.st_mode) & ~0o222)
        except OSError as exc:
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            ) from exc

    @staticmethod
    def _snapshot_tree_exact(source: Path, target: Path) -> None:
        source_fd: int | None = None
        target_parent_fd: int | None = None
        target_fd: int | None = None

        def copy_directory(open_source: int, open_target: int) -> None:
            for name in sorted(os.listdir(open_source)):
                info = os.stat(name, dir_fd=open_source, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    raise OSError("browser tree contains a symbolic link")
                if stat.S_ISDIR(info.st_mode):
                    os.mkdir(name, mode=0o700, dir_fd=open_target)
                    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    flags |= getattr(os, "O_NOFOLLOW", 0)
                    source_child = os.open(name, flags, dir_fd=open_source)
                    target_child = os.open(name, flags, dir_fd=open_target)
                    try:
                        opened = os.fstat(source_child)
                        if (opened.st_dev, opened.st_ino, opened.st_mode) != (
                            info.st_dev,
                            info.st_ino,
                            info.st_mode,
                        ):
                            raise OSError("browser tree identity changed")
                        copy_directory(source_child, target_child)
                        current = os.stat(
                            name,
                            dir_fd=open_source,
                            follow_symlinks=False,
                        )
                        after = os.fstat(source_child)
                        if (current.st_dev, current.st_ino, current.st_mode) != (
                            opened.st_dev,
                            opened.st_ino,
                            opened.st_mode,
                        ) or (after.st_dev, after.st_ino, after.st_mode) != (
                            opened.st_dev,
                            opened.st_ino,
                            opened.st_mode,
                        ):
                            raise OSError("browser tree changed while copying")
                        os.fchmod(target_child, stat.S_IMODE(info.st_mode))
                    finally:
                        close_failed = False
                        try:
                            os.close(target_child)
                        except OSError:
                            close_failed = True
                        try:
                            os.close(source_child)
                        except OSError:
                            close_failed = True
                        if close_failed:
                            raise BrowserRuntimeError(
                                "browser_cleanup",
                                browser_cleanup_substage="private_browser_private_tree",
                                browser_cleanup_check="tree_descriptor_close",
                            )
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise OSError("browser tree contains an unsupported entry")
                source_file = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=open_source,
                )
                target_file: int | None = None
                try:
                    opened = os.fstat(source_file)
                    if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mode) != (
                        info.st_dev,
                        info.st_ino,
                        info.st_size,
                        info.st_mode,
                    ):
                        raise OSError("browser tree identity changed")
                    target_file = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=open_target,
                    )
                    while True:
                        chunk = os.read(source_file, 4 * 1024 * 1024)
                        if not chunk:
                            break
                        view = memoryview(chunk)
                        while view:
                            view = view[os.write(target_file, view) :]
                    os.fchmod(target_file, stat.S_IMODE(info.st_mode))
                    os.fsync(target_file)
                    current = os.stat(
                        name,
                        dir_fd=open_source,
                        follow_symlinks=False,
                    )
                    after = os.fstat(source_file)
                    if (current.st_dev, current.st_ino, current.st_size, current.st_mode) != (
                        opened.st_dev,
                        opened.st_ino,
                        opened.st_size,
                        opened.st_mode,
                    ) or (after.st_dev, after.st_ino, after.st_size, after.st_mode) != (
                        opened.st_dev,
                        opened.st_ino,
                        opened.st_size,
                        opened.st_mode,
                    ):
                        raise OSError("browser tree changed while copying")
                finally:
                    close_failed = False
                    if target_file is not None:
                        try:
                            os.close(target_file)
                        except OSError:
                            close_failed = True
                    try:
                        os.close(source_file)
                    except OSError:
                        close_failed = True
                    if close_failed:
                        raise BrowserRuntimeError(
                            "browser_cleanup",
                            browser_cleanup_substage="private_browser_private_tree",
                            browser_cleanup_check="tree_descriptor_close",
                        )

        try:
            source_info = source.lstat()
            if stat.S_ISLNK(source_info.st_mode) or not stat.S_ISDIR(source_info.st_mode):
                raise OSError("browser tree contains a symbolic link")
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            source_fd = os.open(source, flags)
            opened = os.fstat(source_fd)
            if (opened.st_dev, opened.st_ino, opened.st_mode) != (
                source_info.st_dev,
                source_info.st_ino,
                source_info.st_mode,
            ):
                raise OSError("browser tree identity changed")
            target_parent_fd = os.open(target.parent, flags)
            os.mkdir(target.name, mode=0o700, dir_fd=target_parent_fd)
            target_fd = os.open(target.name, flags, dir_fd=target_parent_fd)
            copy_directory(source_fd, target_fd)
            os.fchmod(target_fd, stat.S_IMODE(source_info.st_mode))
        except OSError as exc:
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            ) from exc
        finally:
            close_failed = False
            for descriptor in (target_fd, target_parent_fd, source_fd):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        close_failed = True
            if close_failed:
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_private_tree",
                    browser_cleanup_check="tree_descriptor_close",
                )

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path, flags)
        try:
            try:
                os.fsync(descriptor)
            except OSError as exc:
                if exc.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EBADF}:
                    raise
        finally:
            _close_browser_descriptor(
                descriptor,
                cleanup_substage="private_browser_private_tree",
                cleanup_check="tree_descriptor_close",
            )

    @staticmethod
    def _freeze_directories(root: Path) -> None:
        root_fd: int | None = None

        def freeze(directory_fd: int) -> None:
            for name in sorted(os.listdir(directory_fd)):
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    raise OSError("browser tree contains a symbolic link")
                if stat.S_ISDIR(info.st_mode):
                    child_fd = os.open(
                        name,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory_fd,
                    )
                    try:
                        freeze(child_fd)
                        os.fchmod(child_fd, stat.S_IMODE(info.st_mode) & ~0o222)
                    finally:
                        _close_browser_descriptor(
                            child_fd,
                            cleanup_substage="private_browser_private_tree",
                            cleanup_check="tree_descriptor_close",
                        )
                elif stat.S_ISREG(info.st_mode):
                    file_fd = os.open(
                        name,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory_fd,
                    )
                    try:
                        os.fchmod(file_fd, stat.S_IMODE(info.st_mode) & ~0o222)
                    finally:
                        _close_browser_descriptor(
                            file_fd,
                            cleanup_substage="private_browser_private_tree",
                            cleanup_check="tree_descriptor_close",
                        )
                else:
                    raise OSError("browser tree contains an unsupported entry")

        try:
            root_fd = os.open(
                root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            root_info = os.fstat(root_fd)
            freeze(root_fd)
            os.fchmod(root_fd, stat.S_IMODE(root_info.st_mode) & ~0o222)
        except OSError as exc:
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            ) from exc
        finally:
            if root_fd is not None:
                _close_browser_descriptor(
                    root_fd,
                    cleanup_substage="private_browser_private_tree",
                    cleanup_check="tree_descriptor_close",
                )

    @staticmethod
    def _thaw_directories(root: Path) -> None:
        info = root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_private_tree",
                browser_cleanup_check="directory_thaw",
            )
        os.chmod(root, 0o700)
        for current, directories, _files in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            directory = Path(current)
            info = directory.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_private_tree",
                    browser_cleanup_check="directory_thaw",
                )
            os.chmod(directory, 0o700)
            for name in directories:
                child = directory / name
                child_info = child.lstat()
                if not stat.S_ISLNK(child_info.st_mode):
                    if not stat.S_ISDIR(child_info.st_mode):
                        raise BrowserRuntimeError(
                            "browser_cleanup",
                            browser_cleanup_substage="private_browser_private_tree",
                            browser_cleanup_check="directory_thaw",
                        )
                    os.chmod(child, 0o700)

    def _materialize_private_image(
        self,
        source_fd: int,
        source_info: os.stat_result,
    ) -> None:
        if self._detached_mount_mode:
            self._materialize_detached_tree(source_info)
            return
        owned_root = _private_directory("meshshot-image-")
        root = owned_root.path
        launch = root / self.path.name
        snapshot_fd: int | None = None
        phase = "private_tree_materialization"
        try:
            digest = hashlib.sha256()
            written_total = 0
            os.lseek(source_fd, 0, os.SEEK_SET)
            output_fd = os.open(
                launch,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                while True:
                    chunk = os.read(source_fd, 4 * 1024 * 1024)
                    if not chunk:
                        break
                    written_total += len(chunk)
                    if written_total > source_info.st_size:
                        raise BrowserRuntimeError("browser_identity")
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(output_fd, view)
                        view = view[written:]
                if written_total != source_info.st_size:
                    raise BrowserRuntimeError("browser_identity")
                os.fchmod(output_fd, stat.S_IMODE(source_info.st_mode) & 0o555)
                os.fsync(output_fd)
            finally:
                _close_browser_descriptor(
                    output_fd,
                    cleanup_substage="private_browser_private_tree",
                    cleanup_check="tree_descriptor_close",
                )
            for sibling in self.path.parent.iterdir():
                if sibling.name != self.path.name:
                    self._snapshot_resource(sibling, root / sibling.name)
            self._fsync_directory(root)
            self._freeze_directories(root)
            phase = "private_launch_image_identity"
            if sys.platform.startswith("linux"):
                snapshot_fd = self._sealed_snapshot_fd(launch, source_info)
            else:
                snapshot_fd = os.open(
                    launch,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                )
            launch_info = os.fstat(snapshot_fd)
            if (
                not stat.S_ISREG(launch_info.st_mode)
                or launch_info.st_size != source_info.st_size
                or stat.S_IMODE(launch_info.st_mode) & 0o222
                or launch_info.st_mode & 0o111 == 0
                or self._sha256_fd(snapshot_fd) != digest.hexdigest()
            ):
                raise BrowserRuntimeError("browser_identity")
            identity = (
                launch_info.st_dev,
                launch_info.st_ino,
                launch_info.st_size,
                stat.S_IMODE(launch_info.st_mode),
            )
            owned_root.close_authority()
            self.fd = snapshot_fd
            snapshot_fd = None
            self.identity = identity
            self.launch_root = root
            self.launch_path = launch
        except BaseException as exc:
            cleanup_error: BrowserRuntimeError | None = (
                exc
                if (
                    isinstance(exc, BrowserRuntimeError)
                    and exc.operation == "browser_cleanup"
                    and exc.browser_cleanup_substage is not None
                    and exc.browser_cleanup_check is not None
                )
                else None
            )

            def record_cleanup(check: str, *, retained: bool = False) -> None:
                nonlocal cleanup_error
                if cleanup_error is None or retained:
                    cleanup_error = BrowserRuntimeError(
                        "browser_cleanup",
                        browser_cleanup_substage="private_browser_private_tree",
                        browser_cleanup_check=check,
                    )

            if snapshot_fd is not None:
                try:
                    os.close(snapshot_fd)
                except OSError:
                    if cleanup_error is None:
                        cleanup_error = BrowserRuntimeError(
                            "browser_cleanup",
                            browser_cleanup_substage="private_browser_pinned_image",
                            browser_cleanup_check="executable_descriptor_close",
                        )
            try:
                self._thaw_directories(root)
            except (BrowserRuntimeError, OSError):
                record_cleanup("directory_thaw")
            try:
                shutil.rmtree(root)
            except OSError:
                record_cleanup("recursive_remove")
            if os.path.lexists(root):
                record_cleanup("absence", retained=True)
            try:
                owned_root.close_authority()
            except BrowserRuntimeError:
                record_cleanup("authority_descriptor_close")
            if cleanup_error is not None:
                raise cleanup_error from exc
            if (
                isinstance(exc, BrowserRuntimeError)
                and exc.operation == "browser_cleanup"
            ):
                if (
                    exc.browser_cleanup_substage is not None
                    and exc.browser_cleanup_check is not None
                ):
                    raise
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_private_tree",
                    browser_cleanup_check="tree_descriptor_close",
                ) from exc
            if (
                isinstance(exc, BrowserRuntimeError)
                and exc.operation == "browser_identity"
                and exc.browser_identity_phase is None
            ):
                raise BrowserRuntimeError(
                    "browser_identity",
                    browser_identity_phase=phase,
                ) from exc
            if isinstance(exc, OSError):
                raise BrowserRuntimeError(
                    "browser_identity",
                    browser_identity_phase=phase,
                ) from exc
            raise

    def _materialize_detached_tree(self, source_info: os.stat_result) -> None:
        expected = os.environ.get(_BROWSER_TREE_MANIFEST_ENV)
        if (
            expected is None
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None
            or self.path.name != "chrome-headless-shell"
            or not self.path.parent.name.startswith("chrome-headless-shell-")
        ):
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            )
        source_root = self.path.parents[1]
        relative = self.path.relative_to(source_root)
        owned_root = _private_mount_root()
        root = owned_root.path
        target_root = root / "attested"
        target = target_root / relative
        descriptor: int | None = None
        phase = "private_tree_materialization"
        try:
            self._prepare_detached_mount(owned_root)
            if self._tree_manifest_sha256(source_root) != expected:
                raise BrowserRuntimeError("browser_identity")
            self._snapshot_tree_exact(source_root, target_root)
            self._fsync_directory(root)
            self._freeze_directories(root)
            if self._tree_manifest_sha256(target_root) != expected:
                raise BrowserRuntimeError("browser_identity")
            phase = "private_launch_image_identity"
            descriptor = os.open(
                target,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            target_info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(target_info.st_mode)
                or target_info.st_size != source_info.st_size
                or target_info.st_mode & 0o111 == 0
                or self._sha256_fd(descriptor) != self._sha256_fd_from_path(self.path)
            ):
                raise BrowserRuntimeError("browser_identity")
            self.fd = descriptor
            descriptor = None
            self.identity = (
                target_info.st_dev,
                target_info.st_ino,
                target_info.st_size,
                stat.S_IMODE(target_info.st_mode),
            )
            self.tree_manifest_sha256 = expected
            self.launch_root = root
            self.launch_path = target
        except BaseException as exc:
            cleanup_error: BrowserRuntimeError | None = None
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    cleanup_error = BrowserRuntimeError(
                        "browser_cleanup",
                        browser_cleanup_substage="private_browser_pinned_image",
                        browser_cleanup_check="executable_descriptor_close",
                    )
            if self._detached_filesystem_mounted:
                try:
                    self._release_detached_mount(remove_underlying=True)
                except BrowserRuntimeError:
                    if cleanup_error is None:
                        cleanup_error = BrowserRuntimeError(
                            "browser_cleanup",
                            browser_cleanup_substage="private_browser_pinned_image",
                            browser_cleanup_check="detached_mount_release",
                        )
            try:
                owned_root.close_authority()
            except BrowserRuntimeError:
                if cleanup_error is None:
                    cleanup_error = BrowserRuntimeError(
                        "browser_cleanup",
                        browser_cleanup_substage="private_browser_private_tree",
                        browser_cleanup_check="authority_descriptor_close",
                    )
            if cleanup_error is not None:
                raise cleanup_error from exc
            if (
                isinstance(exc, BrowserRuntimeError)
                and exc.operation == "browser_cleanup"
                and (
                    exc.browser_cleanup_substage is None
                    or exc.browser_cleanup_check is None
                )
            ):
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_pinned_image",
                    browser_cleanup_check="detached_mount_release",
                ) from exc
            if isinstance(exc, BrowserRuntimeError):
                if exc.operation == "browser_identity" and exc.browser_identity_phase is None:
                    raise BrowserRuntimeError(
                        "browser_identity", browser_identity_phase=phase
                    ) from exc
                raise
            raise BrowserRuntimeError(
                "browser_identity", browser_identity_phase=phase
            ) from exc

    def _prepare_detached_mount(self, owned_root: _OwnedPrivateDirectory) -> None:
        root = owned_root.path
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        mounted_fd: int | None = None
        try:
            if owned_root.directory_fd is None:
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_pinned_image",
                    browser_cleanup_check="detached_mount_release",
                )
            underlying = os.fstat(owned_root.directory_fd)
            if (
                not stat.S_ISDIR(underlying.st_mode)
                or stat.S_ISLNK(underlying.st_mode)
                or (underlying.st_dev, underlying.st_ino) != owned_root.identity
                or underlying.st_uid != os.geteuid()
                or stat.S_IMODE(underlying.st_mode) != 0o700
            ):
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_pinned_image",
                    browser_cleanup_check="detached_mount_release",
                )
            self._detached_mount_parent_fd = owned_root.parent_fd
            owned_root.parent_fd = None
            self._detached_underlying_fd = owned_root.directory_fd
            owned_root.directory_fd = None
            self._detached_mount_name = root.name
            self._detached_underlying_identity = owned_root.identity
            mount_target = Path(
                f"/proc/self/fd/{self._detached_underlying_fd}"
            )
            self._mount_private_filesystem(mount_target)
            self._detached_filesystem_mounted = True
            mounted_fd = os.open(
                mount_target,
                flags,
            )
            mounted = os.fstat(mounted_fd)
            if (
                not stat.S_ISDIR(mounted.st_mode)
                or (mounted.st_dev, mounted.st_ino)
                == self._detached_underlying_identity
            ):
                raise OSError("private mounted filesystem identity is invalid")
            self._detached_mount_fd = mounted_fd
            mounted_fd = None
            self._detached_mounted_identity = (mounted.st_dev, mounted.st_ino)
        except (BrowserRuntimeError, OSError) as exc:
            cleanup_failed = False
            if mounted_fd is not None:
                try:
                    os.close(mounted_fd)
                except OSError:
                    cleanup_failed = True
            if self._detached_filesystem_mounted:
                try:
                    self._release_detached_mount(remove_underlying=True)
                except BrowserRuntimeError:
                    cleanup_failed = True
            elif self._detached_underlying_fd is not None:
                underlying_fd = self._detached_underlying_fd
                parent_fd = self._detached_mount_parent_fd
                identity = self._detached_underlying_identity
                try:
                    opened_underlying = (
                        os.fstat(underlying_fd)
                        if underlying_fd is not None
                        else None
                    )
                    valid = (
                        opened_underlying is not None
                        and (opened_underlying.st_dev, opened_underlying.st_ino)
                        == identity
                    )
                    if not valid:
                        cleanup_failed = True
                except OSError:
                    cleanup_failed = True
                if underlying_fd is not None:
                    try:
                        os.close(underlying_fd)
                    except OSError:
                        cleanup_failed = True
                    self._detached_underlying_fd = None
                if parent_fd is not None:
                    try:
                        os.close(parent_fd)
                    except OSError:
                        cleanup_failed = True
                self._detached_mount_parent_fd = None
                self._detached_mount_name = None
                self._detached_underlying_identity = None
            try:
                owned_root.close_authority()
            except BrowserRuntimeError:
                cleanup_failed = True
            if cleanup_failed:
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_pinned_image",
                    browser_cleanup_check="detached_mount_release",
                ) from exc
            if isinstance(exc, BrowserRuntimeError):
                raise
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            ) from exc

    def _release_detached_mount(self, *, remove_underlying: bool) -> None:
        parent_fd = getattr(self, "_detached_mount_parent_fd", None)
        mounted_fd = getattr(self, "_detached_mount_fd", None)
        underlying_fd = getattr(self, "_detached_underlying_fd", None)
        name = getattr(self, "_detached_mount_name", None)
        underlying_identity = getattr(self, "_detached_underlying_identity", None)
        mounted_identity = getattr(self, "_detached_mounted_identity", None)
        if (
            underlying_fd is None
            or name is None
            or underlying_identity is None
            or not self._detached_filesystem_mounted
        ):
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_pinned_image",
                browser_cleanup_check="detached_mount_release",
            )
        cleanup_failed = False
        try:
            if (
                mounted_identity is None
            ):
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_pinned_image",
                    browser_cleanup_check="detached_mount_release",
                )
            target = (
                Path(f"/proc/self/fd/{mounted_fd}")
                if mounted_fd is not None
                else Path(f"/proc/self/fd/{underlying_fd}")
            )
            self._unmount_private_filesystem(target)
            self._detached_filesystem_mounted = False
            underlying = os.fstat(underlying_fd)
            if (
                not stat.S_ISDIR(underlying.st_mode)
                or (underlying.st_dev, underlying.st_ino) != underlying_identity
            ):
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_pinned_image",
                    browser_cleanup_check="detached_mount_release",
                )
        except (BrowserRuntimeError, OSError):
            cleanup_failed = True
        finally:
            if mounted_fd is not None:
                try:
                    os.close(mounted_fd)
                except OSError:
                    cleanup_failed = True
                self._detached_mount_fd = None
            try:
                os.close(underlying_fd)
            except OSError:
                cleanup_failed = True
            self._detached_underlying_fd = None
            if parent_fd is not None:
                try:
                    os.close(parent_fd)
                except OSError:
                    cleanup_failed = True
            self._detached_mount_parent_fd = None
            self._detached_mount_name = None
            self._detached_underlying_identity = None
            self._detached_mounted_identity = None
        if cleanup_failed:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_pinned_image",
                browser_cleanup_check="detached_mount_release",
            )

    @staticmethod
    def _mount_private_filesystem(root: Path) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        mount = libc.mount
        mount.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_ulong,
            ctypes.c_char_p,
        ]
        mount.restype = ctypes.c_int
        target = os.fsencode(root)
        if mount(b"tmpfs", target, b"tmpfs", 0x2 | 0x4, b"mode=0700") != 0:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_pinned_image",
                browser_cleanup_check="detached_mount_release",
            )

    @staticmethod
    def _unmount_private_filesystem(root: Path) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        unmount = libc.umount2
        unmount.argtypes = [ctypes.c_char_p, ctypes.c_int]
        unmount.restype = ctypes.c_int
        if unmount(os.fsencode(root), 0x2) != 0:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_pinned_image",
                browser_cleanup_check="detached_mount_release",
            )

    @staticmethod
    def _sha256_fd_from_path(path: Path) -> str:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            return _PinnedExecutable._sha256_fd(descriptor)
        finally:
            _close_browser_descriptor(
                descriptor,
                cleanup_substage="private_browser_pinned_image",
                cleanup_check="executable_descriptor_close",
            )

    def _ensure_detached_materialized(self) -> None:
        if not getattr(self, "_detached_mount_mode", False) or self.launch_root is not None:
            return
        source_fd: int | None = None
        failure: BaseException | None = None
        cleanup_error: BrowserRuntimeError | None = None
        try:
            source_fd = os.open(
                self.path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            source_info = os.fstat(source_fd)
            actual = (
                source_info.st_dev,
                source_info.st_ino,
                source_info.st_size,
                source_info.st_mode,
            )
            if actual != self._source_identity:
                raise BrowserRuntimeError(
                    "browser_identity",
                    browser_identity_phase="source_executable_identity",
                )
            if self.fd is not None:
                old_fd = self.fd
                self.fd = None
                _close_browser_descriptor(
                    old_fd,
                    cleanup_substage="private_browser_pinned_image",
                    cleanup_check="executable_descriptor_close",
                )
            self._materialize_detached_tree(source_info)
        except BaseException as exc:
            failure = exc
            if (
                isinstance(exc, BrowserRuntimeError)
                and exc.operation == "browser_cleanup"
                and exc.browser_cleanup_substage is not None
                and exc.browser_cleanup_check is not None
            ):
                cleanup_error = exc
        finally:
            if source_fd is not None:
                closing_source_fd = source_fd
                source_fd = None
                try:
                    os.close(closing_source_fd)
                except OSError:
                    if cleanup_error is None:
                        cleanup_error = BrowserRuntimeError(
                            "browser_cleanup",
                            browser_cleanup_substage="private_browser_pinned_image",
                            browser_cleanup_check="executable_descriptor_close",
                        )
        if cleanup_error is not None:
            raise cleanup_error from failure
        if failure is not None:
            raise failure

    @staticmethod
    def _sealed_snapshot_fd(
        launch: Path,
        source_info: os.stat_result,
    ) -> int:
        flags = (
            getattr(os, "MFD_EXEC", 0x0010)
            | getattr(os, "MFD_ALLOW_SEALING", 0x0002)
            | getattr(os, "MFD_CLOEXEC", 0x0001)
        )
        descriptor: int | None = None
        source: int | None = None
        result: int | None = None
        failure: BaseException | None = None
        cleanup_failed = False
        try:
            create_memfd = getattr(os, "memfd_create", None)
            if not callable(create_memfd):
                raise OSError("sealed executable memory is unavailable")
            descriptor = create_memfd("meshshot-browser", flags)
            source = os.open(
                launch,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            written_total = 0
            while True:
                chunk = os.read(source, 4 * 1024 * 1024)
                if not chunk:
                    break
                written_total += len(chunk)
                if written_total > source_info.st_size:
                    raise OSError("snapshot size changed")
                view = memoryview(chunk)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
            if written_total != source_info.st_size:
                raise OSError("snapshot size changed")
            os.fchmod(descriptor, stat.S_IMODE(source_info.st_mode) & 0o555)
            required = (
                getattr(fcntl, "F_SEAL_WRITE", 0x0008)
                | getattr(fcntl, "F_SEAL_GROW", 0x0004)
                | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
                | getattr(fcntl, "F_SEAL_SEAL", 0x0001)
            )
            add_seals = getattr(fcntl, "F_ADD_SEALS", 1033)
            get_seals = getattr(fcntl, "F_GET_SEALS", 1034)
            fcntl.fcntl(descriptor, add_seals, required)
            actual = fcntl.fcntl(descriptor, get_seals)
            if actual & required != required:
                raise OSError("snapshot sealing failed")
            result = descriptor
        except BaseException as exc:
            failure = exc
        finally:
            if source is not None:
                try:
                    os.close(source)
                except OSError:
                    cleanup_failed = True
            if (failure is not None or cleanup_failed) and descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    cleanup_failed = True
        if cleanup_failed:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_pinned_image",
                browser_cleanup_check="executable_descriptor_close",
            ) from failure
        if failure is not None:
            if isinstance(failure, OSError):
                raise BrowserRuntimeError(
                    "browser_identity",
                    browser_identity_phase="private_launch_version_execution",
                    browser_identity_check="sealed_memfd_creation_policy",
                ) from failure
            raise failure
        assert result is not None
        return result

    @staticmethod
    def _sha256_fd(descriptor: int) -> str:
        digest = hashlib.sha256()
        duplicate = os.dup(descriptor)
        try:
            os.lseek(duplicate, 0, os.SEEK_SET)
            while True:
                chunk = os.read(duplicate, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        finally:
            _close_browser_descriptor(
                duplicate,
                cleanup_substage="private_browser_pinned_image",
                cleanup_check="executable_descriptor_close",
            )
        return digest.hexdigest()

    @staticmethod
    def _sha256_fd_until(descriptor: int, deadline: float) -> str:
        digest = hashlib.sha256()
        duplicate: int | None = None
        failure: BaseException | None = None
        cleanup_failed = False
        try:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired([_FD_EXEC_HANDOFF_SCHEMA], 0)
            duplicate = os.dup(descriptor)
            os.lseek(duplicate, 0, os.SEEK_SET)
            while True:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(
                        [_FD_EXEC_HANDOFF_SCHEMA], 0
                    )
                chunk = os.read(duplicate, 1024 * 1024)
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(
                        [_FD_EXEC_HANDOFF_SCHEMA], 0
                    )
                if not chunk:
                    break
                digest.update(chunk)
        except BaseException as exc:
            failure = exc
        finally:
            if duplicate is not None:
                try:
                    os.close(duplicate)
                except OSError:
                    cleanup_failed = True
        if cleanup_failed:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_pinned_image",
                browser_cleanup_check="executable_descriptor_close",
            ) from failure
        if failure is not None:
            raise failure
        return digest.hexdigest()

    def sha256(self, path: Path | None = None) -> str:
        digest = hashlib.sha256()
        if path is not None:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        assert self.fd is not None
        return self._sha256_fd(self.fd)

    @staticmethod
    def _close_handoff_descriptors(*descriptors: int | None) -> bool:
        failed = False
        for descriptor in descriptors:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    failed = True
        return failed

    @staticmethod
    def _reap_failed_handoff(
        process: subprocess.Popen[bytes],
        *,
        process_group: bool,
        cleanup_term_timeout: float = _FD_EXEC_CLEANUP_TERM_SECONDS,
        cleanup_kill_timeout: float = _FD_EXEC_CLEANUP_KILL_SECONDS,
    ) -> bool:
        failed = False
        if process_group:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError:
                failed = True
            term_deadline = time.monotonic() + cleanup_term_timeout
            try:
                process.wait(
                    timeout=max(0.001, term_deadline - time.monotonic())
                )
            except subprocess.TimeoutExpired:
                pass
            except OSError:
                failed = True
            try:
                group_empty = _wait_group_empty(
                    process.pid,
                    max(0.0, term_deadline - time.monotonic()),
                )
            except OSError:
                failed = True
                group_empty = False
            if not group_empty:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    failed = True
                kill_deadline = time.monotonic() + cleanup_kill_timeout
                try:
                    process.wait(
                        timeout=max(0.001, kill_deadline - time.monotonic())
                    )
                except (OSError, subprocess.SubprocessError):
                    failed = True
                try:
                    group_empty = _wait_group_empty(
                        process.pid,
                        max(0.0, kill_deadline - time.monotonic()),
                    )
                except OSError:
                    failed = True
                    group_empty = False
            if not group_empty:
                failed = True
        else:
            deadline = time.monotonic() + cleanup_term_timeout
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    failed = True
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    failed = True
                kill_deadline = time.monotonic() + cleanup_kill_timeout
                try:
                    process.wait(
                        timeout=max(0.001, kill_deadline - time.monotonic())
                    )
                except (OSError, subprocess.SubprocessError):
                    failed = True
            except OSError:
                failed = True
        return failed

    def _wait_for_exec_replacement(
        self,
        process: subprocess.Popen[bytes],
        deadline: float,
        *,
        image_pid: int | None = None,
    ) -> None:
        target_pid = process.pid if image_pid is None else image_pid
        while True:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired([_FD_EXEC_HANDOFF_SCHEMA], 0)
            if image_pid is None and process.poll() is not None:
                raise BrowserRuntimeError("browser_identity")
            if image_pid is not None:
                try:
                    raw = Path(f"/proc/{target_pid}/stat").read_text(
                        encoding="utf-8"
                    )
                    tail = raw[raw.rfind(")") + 2 :].split()
                    if not tail or tail[0] == "Z":
                        raise BrowserRuntimeError("browser_identity")
                except (FileNotFoundError, ProcessLookupError) as exc:
                    raise BrowserRuntimeError("browser_identity") from exc
                except (OSError, ValueError, IndexError) as exc:
                    raise BrowserRuntimeError("browser_identity") from exc
            try:
                self._verify_running_image_until(target_pid, deadline)
                return
            except subprocess.TimeoutExpired:
                raise
            except BrowserRuntimeError as exc:
                if exc.operation == "browser_cleanup":
                    raise
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(
                        [_FD_EXEC_HANDOFF_SCHEMA], 0
                    ) from exc
                time.sleep(0.002)

    @staticmethod
    def _verify_bwrap_peer(
        process: subprocess.Popen[bytes], peer_pid: int
    ) -> None:
        current = peer_pid
        for _depth in range(16):
            try:
                raw = Path(f"/proc/{current}/stat").read_text(encoding="utf-8")
                tail = raw[raw.rfind(")") + 2 :].split()
                if (
                    current <= 1
                    or len(tail) < 4
                    or int(tail[2]) != process.pid
                    or int(tail[3]) != process.pid
                ):
                    raise BrowserRuntimeError("browser_identity")
                parent = int(tail[1])
                if parent == process.pid:
                    return
                if parent <= 1 or parent == current:
                    break
                current = parent
            except (OSError, ValueError, IndexError) as exc:
                raise BrowserRuntimeError("browser_identity") from exc
        raise BrowserRuntimeError("browser_identity")

    @staticmethod
    def _mount_packet(connection: socket.socket) -> Any:
        try:
            raw = connection.recv(_SUPERVISOR_PACKET_LIMIT + 1)
        except (OSError, socket.timeout) as exc:
            raise BrowserRuntimeError("browser_identity") from exc
        if not raw or len(raw) > _SUPERVISOR_PACKET_LIMIT:
            raise BrowserRuntimeError("browser_identity")
        return _loads_json_strict(raw)

    @staticmethod
    def _send_mount_packet(connection: socket.socket, value: object) -> None:
        raw = _canonical_bytes(value)
        if not raw or len(raw) > _SUPERVISOR_PACKET_LIMIT:
            raise BrowserRuntimeError("browser_identity")
        try:
            if connection.send(raw) != len(raw):
                raise OSError("short browser mount handoff")
        except OSError as exc:
            raise BrowserRuntimeError("browser_identity") from exc

    @staticmethod
    def _unlink_owned_handoff_entry(
        root_fd: int,
        name: str,
        identity: tuple[int, int],
        *,
        socket_entry: bool,
    ) -> None:
        """Remove only the exact private handoff inode created by this launch."""

        cleanup_check = (
            "socket_unlink" if socket_entry else "authority_record_unlink"
        )

        try:
            current = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        except OSError as exc:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_handoff",
                browser_cleanup_check=cleanup_check,
            ) from exc
        expected_type = (
            stat.S_ISSOCK(current.st_mode)
            if socket_entry
            else stat.S_ISREG(current.st_mode)
        )
        if (
            not expected_type
            or current.st_uid != os.geteuid()
            or (current.st_dev, current.st_ino) != identity
        ):
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_handoff",
                browser_cleanup_check=cleanup_check,
            )
        try:
            os.unlink(name, dir_fd=root_fd)
        except OSError as exc:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_handoff",
                browser_cleanup_check=cleanup_check,
            ) from exc
        try:
            os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_handoff",
                browser_cleanup_check=cleanup_check,
            ) from exc
        raise BrowserRuntimeError(
            "browser_cleanup",
            browser_cleanup_substage="private_browser_handoff",
            browser_cleanup_check=cleanup_check,
        )

    def _remove_detached_source(self) -> None:
        if self.launch_root is None or not self._detached_filesystem_mounted:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_pinned_image",
                browser_cleanup_check="detached_mount_release",
            )
        try:
            self._release_detached_mount(remove_underlying=True)
        except BrowserRuntimeError as exc:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_pinned_image",
                browser_cleanup_check="detached_mount_release",
            ) from exc
        self.launch_root = None
        self.launch_path = _BROWSER_MOUNT_EXECUTABLE

    def _detached_linux_popen(
        self,
        argv: list[str],
        *,
        deadline: float,
        options: dict[str, Any],
        completion: str,
    ) -> subprocess.Popen[bytes]:
        assert self.fd is not None and self.launch_root is not None
        source_root = self.launch_root / "attested"
        nonce = secrets.token_hex(32)
        listener: socket.socket | None = None
        connection: socket.socket | None = None
        process: subprocess.Popen[bytes] | None = None
        root_fd: int | None = None
        authority_fd: int | None = None
        authority_identity: tuple[int, int] | None = None
        authority_unlinked = False
        socket_identity: tuple[int, int] | None = None
        socket_unlinked = False
        failure: BaseException | None = None
        cleanup_error: BrowserRuntimeError | None = None

        def record_cleanup(check: str) -> None:
            nonlocal cleanup_error
            if cleanup_error is None:
                cleanup_error = BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_handoff",
                    browser_cleanup_check=check,
                )
        try:
            root_info = SUPERVISOR_OUTER_ROOT.lstat()
            if (
                stat.S_ISLNK(root_info.st_mode)
                or not stat.S_ISDIR(root_info.st_mode)
                or root_info.st_uid != os.geteuid()
                or stat.S_IMODE(root_info.st_mode) & 0o077
                or os.path.lexists(_BROWSER_MOUNT_SOCKET)
                or os.path.lexists(_BROWSER_MOUNT_AUTHORITY)
            ):
                raise BrowserRuntimeError("browser_identity")
            root_fd = os.open(
                SUPERVISOR_OUTER_ROOT,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            opened_root = os.fstat(root_fd)
            if (
                not stat.S_ISDIR(opened_root.st_mode)
                or (opened_root.st_dev, opened_root.st_ino)
                != (root_info.st_dev, root_info.st_ino)
            ):
                raise BrowserRuntimeError("browser_identity")
            authority_fd = os.open(
                _BROWSER_MOUNT_AUTHORITY.name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o400,
                dir_fd=root_fd,
            )
            authority_info = os.fstat(authority_fd)
            if (
                not stat.S_ISREG(authority_info.st_mode)
                or authority_info.st_uid != os.geteuid()
                or stat.S_IMODE(authority_info.st_mode) != 0o400
            ):
                raise BrowserRuntimeError("browser_identity")
            authority_identity = (
                authority_info.st_dev,
                authority_info.st_ino,
            )
            authority = _canonical_bytes(
                {"schema": _BROWSER_MOUNT_AUTHORITY_SCHEMA, "nonce": nonce}
            )
            if os.write(authority_fd, authority) != len(authority):
                raise OSError("short browser mount authority write")
            os.fsync(authority_fd)
            closing_authority_fd = authority_fd
            authority_fd = None
            _close_browser_descriptor(
                closing_authority_fd,
                cleanup_substage="private_browser_handoff",
                cleanup_check="authority_record_descriptor_close",
            )
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            listener.bind(os.fspath(_BROWSER_MOUNT_SOCKET))
            socket_info = os.stat(
                _BROWSER_MOUNT_SOCKET.name,
                dir_fd=root_fd,
                follow_symlinks=False,
            )
            if not stat.S_ISSOCK(socket_info.st_mode):
                raise BrowserRuntimeError("browser_identity")
            socket_identity = (socket_info.st_dev, socket_info.st_ino)
            os.chmod(_BROWSER_MOUNT_SOCKET, 0o600)
            socket_info = os.stat(
                _BROWSER_MOUNT_SOCKET.name,
                dir_fd=root_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISSOCK(socket_info.st_mode)
                or socket_info.st_uid != os.geteuid()
                or stat.S_IMODE(socket_info.st_mode) != 0o600
                or (socket_info.st_dev, socket_info.st_ino)
                != socket_identity
            ):
                raise BrowserRuntimeError("browser_identity")
            listener.listen(1)
            listener.settimeout(max(0.001, deadline - time.monotonic()))
            helper_argv = [
                os.fspath(_TRUSTED_BWRAP),
                "--die-with-parent",
                "--cap-drop",
                "ALL",
                "--bind",
                "/",
                "/",
                "--dir",
                os.fspath(_BROWSER_MOUNT_TARGET.parent),
                "--ro-bind",
                os.fspath(source_root),
                os.fspath(_BROWSER_MOUNT_TARGET),
                "--ro-bind",
                os.fspath(SUPERVISOR_OUTER_ROOT),
                "/run/meshshot-supervisor",
                "--tmpfs",
                os.fspath(SUPERVISOR_OUTER_ROOT),
                "--",
                sys.executable,
                "-I",
                os.fspath(_BROWSER_MOUNT_HANDOFF),
                _BROWSER_MOUNT_SCHEMA,
                completion,
            ]
            if completion == "live":
                profile_argument = next(
                    (item.split("=", 1)[1] for item in argv if item.startswith("--user-data-dir=")),
                    None,
                )
                if profile_argument is None:
                    raise BrowserRuntimeError("browser_identity")
                helper_argv.append(profile_argument)
            options["executable"] = os.fspath(_TRUSTED_BWRAP)
            options["close_fds"] = True
            options["env"] = {
                name: os.environ[name]
                for name in sorted(_FD_EXEC_ENVIRONMENT | {"PLAYWRIGHT_BROWSERS_PATH"})
                if name in os.environ
            }
            process = subprocess.Popen(helper_argv, **options)
            connection, _address = listener.accept()
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            peer_pid, peer_uid, _peer_gid = _peer_credentials(connection)
            if peer_uid != os.geteuid():
                raise BrowserRuntimeError("browser_identity")
            self._verify_bwrap_peer(process, peer_pid)
            mounted = self._mount_packet(connection)
            if mounted != {
                "schema": _BROWSER_MOUNT_SCHEMA,
                "type": "mounted",
                "nonce": nonce,
            }:
                raise BrowserRuntimeError("browser_identity")
            try:
                listener.close()
            except OSError:
                record_cleanup("transport_close")
                raise
            listener = None
            self._unlink_owned_handoff_entry(
                root_fd,
                _BROWSER_MOUNT_SOCKET.name,
                socket_identity,
                socket_entry=True,
            )
            socket_unlinked = True
            self._remove_detached_source()
            self._send_mount_packet(
                connection,
                {
                    "schema": _BROWSER_MOUNT_SCHEMA,
                    "type": "detached",
                    "nonce": nonce,
                },
            )
            executed = self._mount_packet(connection)
            if executed != {
                "schema": _BROWSER_MOUNT_SCHEMA,
                "type": "exec",
                "nonce": nonce,
            }:
                raise BrowserRuntimeError("browser_identity")
            try:
                transition = connection.recv(_SUPERVISOR_PACKET_LIMIT + 1)
            except (OSError, socket.timeout) as exc:
                raise BrowserRuntimeError("browser_identity") from exc
            if transition != b"":
                failed = _loads_json_strict(transition)
                cause = failed.get("cause") if isinstance(failed, dict) else None
                if (
                    not isinstance(failed, dict)
                    or set(failed) != {"schema", "type", "cause"}
                    or failed.get("schema") != _BROWSER_MOUNT_SCHEMA
                    or failed.get("type") != "failed"
                    or cause
                    not in {
                        "setup",
                        "permission",
                        "missing",
                        "target_missing",
                        "format",
                        "other",
                    }
                ):
                    raise BrowserRuntimeError("browser_identity")
                raise BrowserRuntimeError("browser_identity") from OSError(
                    f"closed browser mount exec cause: {cause}"
                )
            try:
                connection.close()
            except OSError:
                record_cleanup("transport_close")
                raise
            connection = None
            self._unlink_owned_handoff_entry(
                root_fd,
                _BROWSER_MOUNT_AUTHORITY.name,
                authority_identity,
                socket_entry=False,
            )
            authority_unlinked = True
            if completion == "live":
                self._wait_for_exec_replacement(
                    process, deadline, image_pid=peer_pid
                )
            else:
                setattr(process, "_meshshot_version_handoff_eof", True)
            setattr(process, "_meshshot_browser_image_pid", peer_pid)
        except BaseException as exc:
            failure = exc
            if (
                isinstance(exc, BrowserRuntimeError)
                and exc.operation == "browser_cleanup"
                and exc.browser_cleanup_substage == "private_browser_handoff"
                and exc.browser_cleanup_check is not None
            ):
                cleanup_error = exc
        finally:
            if authority_fd is not None:
                try:
                    os.close(authority_fd)
                except OSError:
                    record_cleanup("authority_record_descriptor_close")
            for endpoint in (connection, listener):
                if endpoint is not None:
                    try:
                        endpoint.close()
                    except OSError:
                        record_cleanup("transport_close")
            for name, identity, socket_entry, already_unlinked in (
                (
                    _BROWSER_MOUNT_SOCKET.name,
                    socket_identity,
                    True,
                    socket_unlinked,
                ),
                (
                    _BROWSER_MOUNT_AUTHORITY.name,
                    authority_identity,
                    False,
                    authority_unlinked,
                ),
            ):
                if identity is not None and not already_unlinked:
                    try:
                        self._unlink_owned_handoff_entry(
                            root_fd,
                            name,
                            identity,
                            socket_entry=socket_entry,
                        )
                    except BrowserRuntimeError as exc:
                        if exc.browser_cleanup_check is not None:
                            record_cleanup(exc.browser_cleanup_check)
                        else:
                            record_cleanup(
                                "socket_unlink"
                                if socket_entry
                                else "authority_record_unlink"
                            )
            if root_fd is not None:
                try:
                    os.close(root_fd)
                except OSError:
                    record_cleanup("root_descriptor_close")
            if (failure is not None or cleanup_error is not None) and process is not None:
                if self._reap_failed_handoff(
                    process,
                    process_group=bool(options.get("start_new_session")),
                ):
                    record_cleanup("process_group_cleanup")
        if cleanup_error is not None:
            raise cleanup_error from failure
        if failure is None:
            assert process is not None
            return process
        assert failure is not None
        if completion == "version" and isinstance(
            failure, (OSError, subprocess.SubprocessError)
        ):
            raise _private_version_execution_error(
                "private_version_helper_spawn_other"
            ) from failure
        raise failure

    def _linux_popen(
        self,
        argv: list[str],
        *,
        deadline: float,
        options: dict[str, Any],
        completion: str,
    ) -> subprocess.Popen[bytes]:
        assert self.fd is not None and self.launch_path is not None
        if getattr(self, "_detached_mount_mode", False):
            return self._detached_linux_popen(
                argv,
                deadline=deadline,
                options=options,
                completion=completion,
            )
        read_fd: int | None = None
        write_fd: int | None = None
        process: subprocess.Popen[bytes] | None = None
        cleanup_error: BrowserRuntimeError | None = None
        failure: BaseException | None = None
        failure_check = "private_version_handoff_setup"

        def record_cleanup(check: str) -> None:
            nonlocal cleanup_error
            if cleanup_error is None:
                cleanup_error = BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_handoff",
                    browser_cleanup_check=check,
                )
        try:
            read_fd, write_fd = os.pipe()
            os.set_inheritable(read_fd, False)
            os.set_inheritable(write_fd, False)
            launch_argv = [os.fspath(self.launch_path), *argv[1:]]
            helper_argv = [
                sys.executable,
                "-I",
                os.fspath(_FD_EXEC_HANDOFF),
                _FD_EXEC_HANDOFF_SCHEMA,
                str(self.fd),
                str(write_fd),
                *launch_argv,
            ]
            options["executable"] = sys.executable
            options["pass_fds"] = (self.fd, write_fd)
            options["close_fds"] = True
            options["env"] = {
                name: os.environ[name]
                for name in sorted(_FD_EXEC_ENVIRONMENT)
                if name in os.environ
            }
            try:
                process = subprocess.Popen(helper_argv, **options)
            except OSError as exc:
                failure_check = _private_version_helper_spawn_check(exc)
                raise
            failure_check = "private_version_handoff_setup"
            parent_write_fd = write_fd
            write_fd = None
            try:
                _close_browser_descriptor(
                    parent_write_fd,
                    cleanup_substage="private_browser_handoff",
                    cleanup_check="pipe_descriptor_close",
                )
            except BrowserRuntimeError as exc:
                failure = exc
                cleanup_error = exc
            if failure is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure_check = "private_version_handoff_timeout"
                    raise subprocess.TimeoutExpired([_FD_EXEC_HANDOFF_SCHEMA], 0)
                ready, _writable, _errors = select.select(
                    [read_fd], [], [], remaining
                )
                if not ready:
                    failure_check = "private_version_handoff_timeout"
                    raise subprocess.TimeoutExpired(
                        [_FD_EXEC_HANDOFF_SCHEMA], remaining
                    )
                status = os.read(read_fd, 1)
                if status == b"F":
                    failure_check = "private_version_helper_exec"
                    raise OSError("fd-native browser execution failed")
                if status != b"":
                    raise OSError("fd-native handoff protocol failed")
                if completion == "live":
                    self._wait_for_exec_replacement(process, deadline)
                else:
                    setattr(process, "_meshshot_version_handoff_eof", True)
        except BaseException as exc:
            failure = exc
            if (
                isinstance(exc, BrowserRuntimeError)
                and exc.operation == "browser_cleanup"
                and exc.browser_cleanup_substage == "private_browser_handoff"
                and exc.browser_cleanup_check is not None
            ):
                cleanup_error = exc
        finally:
            if self._close_handoff_descriptors(read_fd, write_fd):
                record_cleanup("pipe_descriptor_close")
            if failure is not None or cleanup_error is not None:
                if process is not None:
                    if self._reap_failed_handoff(
                        process,
                        process_group=bool(options.get("start_new_session")),
                        cleanup_term_timeout=_FD_EXEC_CLEANUP_TERM_SECONDS,
                        cleanup_kill_timeout=_FD_EXEC_CLEANUP_KILL_SECONDS,
                    ):
                        record_cleanup("process_group_cleanup")
        if cleanup_error is not None:
            raise cleanup_error from failure
        if failure is not None:
            if (
                completion == "version"
                and isinstance(failure, (OSError, subprocess.SubprocessError))
            ):
                raise _private_version_execution_error(failure_check) from failure
            raise failure
        assert process is not None
        return process

    def popen(self, argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        self._ensure_detached_materialized()
        assert self.fd is not None and self.launch_path is not None
        options = dict(kwargs)
        deadline = options.pop("_handoff_deadline", None)
        completion = options.pop("_handoff_completion", "live")
        if sys.platform.startswith("linux"):
            if not isinstance(deadline, (int, float)):
                deadline = time.monotonic() + 15.0
            if completion not in {"live", "version"}:
                raise BrowserRuntimeError("browser_identity")
            return self._linux_popen(
                argv,
                deadline=deadline,
                options=options,
                completion=completion,
            )
        else:
            options["executable"] = os.fspath(self.launch_path)
        launch_argv = [os.fspath(self.launch_path), *argv[1:]]
        return subprocess.Popen(launch_argv, **options)

    def launch_live(
        self,
        argv: list[str],
        **kwargs: Any,
    ) -> _LiveBrowserLaunch:
        """Launch one live browser with an internal authenticated-image proof."""

        if not sys.platform.startswith("linux"):
            raise BrowserRuntimeError("browser_identity")
        process = self.popen(
            argv,
            _handoff_completion="live",
            **kwargs,
        )
        return _LiveBrowserLaunch(process, _LINUX_LIVE_IMAGE_PROOF)

    def verify_running_image(self, pid: int, timeout: float) -> None:
        self._verify_running_image_until(pid, time.monotonic() + timeout)

    def _verify_running_image_until(self, pid: int, deadline: float) -> None:
        assert self.fd is not None
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired([_FD_EXEC_HANDOFF_SCHEMA], 0)
        try:
            expected = os.fstat(self.fd)
        except OSError as exc:
            raise BrowserRuntimeError("browser_identity") from exc
        if sys.platform.startswith("linux"):
            proc_exe = Path(f"/proc/{pid}/exe")
            try:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(
                        [_FD_EXEC_HANDOFF_SCHEMA], 0
                    )
                if not os.readlink(proc_exe):
                    raise BrowserRuntimeError("browser_identity")
                descriptor = os.open(proc_exe, os.O_RDONLY)
            except subprocess.TimeoutExpired:
                raise
            except OSError as exc:
                raise BrowserRuntimeError("browser_identity") from exc
            failure: BaseException | None = None
            cleanup_failed = False
            matches = False
            try:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(
                        [_FD_EXEC_HANDOFF_SCHEMA], 0
                    )
                actual = os.fstat(descriptor)
                matches = (
                    actual.st_dev == expected.st_dev
                    and actual.st_ino == expected.st_ino
                    and self._sha256_fd_until(descriptor, deadline)
                    == self._sha256_fd_until(self.fd, deadline)
                )
            except BaseException as exc:
                failure = exc
            finally:
                try:
                    os.close(descriptor)
                except OSError:
                    cleanup_failed = True
            if cleanup_failed:
                raise BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage="private_browser_pinned_image",
                    browser_cleanup_check="executable_descriptor_close",
                ) from failure
            if failure is not None:
                if isinstance(failure, OSError):
                    raise BrowserRuntimeError("browser_identity") from failure
                raise failure
            if not matches:
                raise BrowserRuntimeError("browser_identity")
            return
        if sys.platform == "darwin":
            try:
                completed = subprocess.run(
                    [
                        "/usr/sbin/lsof",
                        "-p",
                        str(pid),
                        "-a",
                        "-d",
                        "txt",
                        "-F",
                        "fDin",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=max(0.001, deadline - time.monotonic()),
                    close_fds=True,
                )
                lines = completed.stdout.decode("utf-8").splitlines()
            except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
                raise BrowserRuntimeError("browser_identity") from exc
            identities: set[tuple[int, int]] = set()
            device: int | None = None
            for line in lines:
                if line.startswith("D"):
                    try:
                        device = int(line[1:], 0)
                    except ValueError as exc:
                        raise BrowserRuntimeError("browser_identity") from exc
                elif line.startswith("i"):
                    try:
                        if device is not None:
                            identities.add((device, int(line[1:])))
                    except ValueError as exc:
                        raise BrowserRuntimeError("browser_identity") from exc
            if (
                completed.returncode != 0
                or completed.stderr != b""
                or (expected.st_dev, expected.st_ino) not in identities
            ):
                raise BrowserRuntimeError("browser_identity")
            return
        raise BrowserRuntimeError("browser_identity")

    def run_version(self, timeout: float) -> subprocess.CompletedProcess[bytes]:
        assert self.fd is not None and self.launch_path is not None
        if sys.platform.startswith("linux"):
            deadline = time.monotonic() + timeout
            process: subprocess.Popen[bytes] | None = None
            group_proven_empty = False
            authenticated_completion = False
            try:
                with _blocked_runtime_signals():
                    process = self.popen(
                        [os.fspath(self.launch_path), "--version"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True,
                        close_fds=True,
                        _handoff_deadline=deadline,
                        _handoff_completion="version",
                    )
                stdout, stderr = process.communicate(
                    timeout=max(0.001, deadline - time.monotonic())
                )
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(
                        [_FD_EXEC_HANDOFF_SCHEMA], timeout
                    )
                try:
                    version = stdout.decode("utf-8").strip()
                except UnicodeDecodeError as exc:
                    raise BrowserRuntimeError(
                        "browser_identity",
                        browser_identity_phase=(
                            "private_launch_version_output_identity"
                        ),
                    ) from exc
                authenticated_completion = (
                    _VERSION_OUTPUT.fullmatch(version) is not None
                )
                valid = (
                    authenticated_completion
                    and process.returncode == 0
                    and stderr == b""
                )
                try:
                    group_empty = _wait_group_empty(
                        process.pid,
                        max(0.0, deadline - time.monotonic()),
                    )
                except OSError as exc:
                    raise _private_version_execution_error(
                        "private_version_probe_completion"
                    ) from exc
                group_proven_empty = group_empty
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(
                        [_FD_EXEC_HANDOFF_SCHEMA], timeout
                    )
                if valid and group_empty:
                    return subprocess.CompletedProcess(
                        args=[os.fspath(self.launch_path), "--version"],
                        returncode=process.returncode,
                        stdout=stdout,
                        stderr=stderr,
                    )
                if authenticated_completion:
                    raise _private_version_execution_error(
                        "private_version_probe_completion"
                    )
                if process.returncode != 0 or not group_empty:
                    if (
                        not authenticated_completion
                        and getattr(
                            process,
                            "_meshshot_version_handoff_eof",
                            False,
                        )
                        is True
                    ):
                        raise _private_version_execution_error(
                            "private_version_exec_replacement"
                        )
                    raise _private_version_execution_error(
                        "private_version_probe_completion"
                    )
                raise BrowserRuntimeError(
                    "browser_identity",
                    browser_identity_phase=(
                        "private_launch_version_output_identity"
                    ),
                )
            except BaseException as exc:
                cleanup_failed = False
                if process is not None and not group_proven_empty:
                    cleanup_failed = self._reap_failed_handoff(
                        process,
                        process_group=True,
                        cleanup_term_timeout=_FD_EXEC_CLEANUP_TERM_SECONDS,
                        cleanup_kill_timeout=_FD_EXEC_CLEANUP_KILL_SECONDS,
                    )
                if cleanup_failed or (
                    isinstance(exc, BrowserRuntimeError)
                    and exc.operation == "browser_cleanup"
                ):
                    if (
                        isinstance(exc, BrowserRuntimeError)
                        and exc.browser_cleanup_substage is not None
                        and exc.browser_cleanup_check is not None
                    ):
                        raise
                    raise BrowserRuntimeError(
                        "browser_cleanup",
                        browser_cleanup_substage="private_browser_handoff",
                        browser_cleanup_check="process_group_cleanup",
                    ) from exc
                if isinstance(exc, subprocess.TimeoutExpired):
                    raise _private_version_execution_error(
                        "private_version_probe_timeout"
                    ) from exc
                if isinstance(exc, (OSError, subprocess.SubprocessError)):
                    raise _private_version_execution_error(
                        "private_version_probe_completion"
                    ) from exc
                raise
        options: dict[str, Any] = {
            "args": [os.fspath(self.launch_path), "--version"],
            "executable": os.fspath(self.launch_path),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "check": False,
            "timeout": timeout,
            "close_fds": True,
        }
        return subprocess.run(**options)

    def close(self) -> None:
        failure = False
        cleanup_check: str | None = None
        cleanup_retained = False
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                failure = True
                cleanup_check = "executable_descriptor_close"
            self.fd = None
        if self.launch_root is not None:
            if getattr(self, "_detached_filesystem_mounted", False):
                try:
                    self._release_detached_mount(remove_underlying=True)
                except BrowserRuntimeError:
                    failure = True
                    if cleanup_check is None:
                        cleanup_check = "detached_mount_release"
            elif getattr(self, "_detached_mount_mode", False):
                # Detached-tree cleanup is descriptor/inode-authorized only.
                # Never fall through to pathname traversal after authority loss.
                failure = True
                if cleanup_check is None:
                    cleanup_check = "detached_mount_release"
            else:
                try:
                    self._thaw_directories(self.launch_root)
                    shutil.rmtree(self.launch_root)
                except (BrowserRuntimeError, OSError):
                    failure = True
                    if cleanup_check is None:
                        cleanup_check = "detached_mount_release"
            if (
                getattr(self, "_detached_filesystem_mounted", False)
                or os.path.lexists(self.launch_root)
            ):
                failure = True
                cleanup_check = "detached_mount_release"
                cleanup_retained = True
            self.launch_root = None
        if failure:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage="private_browser_pinned_image",
                browser_cleanup_check=cleanup_check,
                _browser_cleanup_retained=cleanup_retained,
            )


def _group_empty(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _owned_process_exited(process: subprocess.Popen[bytes]) -> bool:
    image_pid = getattr(process, "_meshshot_browser_image_pid", None)
    if type(image_pid) is not int or image_pid <= 1:
        return process.poll() is not None
    try:
        raw = Path(f"/proc/{image_pid}/stat").read_text(encoding="utf-8")
        tail = raw[raw.rfind(")") + 2 :].split()
        return (
            len(tail) < 4
            or tail[0] == "Z"
            or int(tail[2]) != process.pid
            or int(tail[3]) != process.pid
        )
    except (FileNotFoundError, ProcessLookupError):
        return True
    except (OSError, ValueError, IndexError):
        return True


def _wait_group_empty(process_group: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if _group_empty(process_group):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


def _verify_listener_owner(process_group: int, port: int, timeout: float) -> None:
    if sys.platform == "darwin":
        try:
            completed = subprocess.run(
                [
                    "/usr/sbin/lsof",
                    "-nP",
                    "-a",
                    f"-iTCP:{port}",
                    "-sTCP:LISTEN",
                    "-F",
                    "pgfnPT",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
                close_fds=True,
            )
            lines = completed.stdout.decode("utf-8").splitlines()
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as exc:
            raise BrowserRuntimeError("browser_identity") from exc
        records: list[tuple[int | None, str | None, str | None]] = []
        process_active = False
        process_has_file = False
        group: int | None = None
        file_active = False
        protocol: str | None = None
        name: str | None = None
        state: str | None = None
        receive_queue: int | None = None
        send_queue: int | None = None

        def finish_file() -> None:
            nonlocal file_active, protocol, name, state
            nonlocal receive_queue, send_queue, process_has_file
            if (
                not file_active
                or protocol != "TCP"
                or name is None
                or state != "LISTEN"
                or receive_queue is None
                or send_queue is None
            ):
                raise BrowserRuntimeError("browser_identity")
            records.append((group, name, state))
            process_has_file = True
            file_active, protocol, name, state = False, None, None, None
            receive_queue, send_queue = None, None

        def finish_process() -> None:
            nonlocal process_active, process_has_file, group
            if not process_active or group is None:
                raise BrowserRuntimeError("browser_identity")
            if file_active:
                finish_file()
            if not process_has_file:
                raise BrowserRuntimeError("browser_identity")
            process_active, process_has_file, group = False, False, None

        for line in lines:
            if line.startswith("p"):
                if process_active:
                    finish_process()
                if not line[1:].isdigit():
                    raise BrowserRuntimeError("browser_identity")
                process_active = True
            elif line.startswith("f"):
                if not process_active or group is None or not line[1:].isdigit():
                    raise BrowserRuntimeError("browser_identity")
                if file_active:
                    finish_file()
                file_active = True
            elif line.startswith("g"):
                if not process_active or group is not None or file_active:
                    raise BrowserRuntimeError("browser_identity")
                try:
                    group = int(line[1:])
                except ValueError as exc:
                    raise BrowserRuntimeError("browser_identity") from exc
            elif line.startswith("n"):
                if (
                    not file_active
                    or protocol != "TCP"
                    or name is not None
                    or state is not None
                ):
                    raise BrowserRuntimeError("browser_identity")
                name = line[1:]
            elif line == "TST=LISTEN":
                if not file_active or name is None or state is not None:
                    raise BrowserRuntimeError("browser_identity")
                state = "LISTEN"
            elif line.startswith("P"):
                if (
                    not file_active
                    or protocol is not None
                    or name is not None
                    or line != "PTCP"
                ):
                    raise BrowserRuntimeError("browser_identity")
                protocol = "TCP"
            elif line.startswith("T"):
                if not file_active:
                    raise BrowserRuntimeError("browser_identity")
                if line.startswith("TQR="):
                    value = line[4:]
                    if (
                        state != "LISTEN"
                        or receive_queue is not None
                        or send_queue is not None
                        or not value.isdigit()
                    ):
                        raise BrowserRuntimeError("browser_identity")
                    receive_queue = int(value)
                elif line.startswith("TQS="):
                    value = line[4:]
                    if (
                        receive_queue is None
                        or send_queue is not None
                        or not value.isdigit()
                    ):
                        raise BrowserRuntimeError("browser_identity")
                    send_queue = int(value)
                else:
                    raise BrowserRuntimeError("browser_identity")
            else:
                raise BrowserRuntimeError("browser_identity")
        if process_active:
            finish_process()
        expected_name = f"127.0.0.1:{port}"
        if (
            completed.returncode != 0
            or completed.stderr != b""
            or not records
            or any(
                group != process_group
                or name != expected_name
                or state != "LISTEN"
                for group, name, state in records
            )
        ):
            raise BrowserRuntimeError("browser_identity")
        return
    if sys.platform.startswith("linux"):
        socket_inodes: set[str] = set()
        try:
            for table, exact_address in (
                (Path("/proc/net/tcp"), "0100007F"),
                (Path("/proc/net/tcp6"), None),
            ):
                for line in table.read_text(encoding="utf-8").splitlines()[1:]:
                    fields = line.split()
                    local_address, state = fields[1], fields[3]
                    address, port_hex = local_address.rsplit(":", 1)
                    if int(port_hex, 16) != port or state != "0A":
                        continue
                    if exact_address is None or address != exact_address:
                        raise BrowserRuntimeError("browser_identity")
                    socket_inodes.add(fields[9])
            owners: dict[str, set[int]] = {
                inode: set() for inode in socket_inodes
            }
            for process_path in Path("/proc").iterdir():
                if not process_path.name.isdigit():
                    continue
                try:
                    stat_line = (process_path / "stat").read_text(encoding="utf-8")
                    tail = stat_line[stat_line.rfind(")") + 2 :].split()
                    group = int(tail[2])
                except (FileNotFoundError, PermissionError, ProcessLookupError):
                    continue
                if group != process_group:
                    continue
                for descriptor in (process_path / "fd").iterdir():
                    try:
                        target = os.readlink(descriptor)
                    except (FileNotFoundError, PermissionError):
                        # Unrelated descriptors may disappear while /proc is
                        # enumerated or remain unreadable after capability
                        # drop. A hidden listener still fails closed below
                        # because its socket inode has no exact owner.
                        continue
                    if target.startswith("socket:["):
                        inode = target[8:-1]
                        if inode in owners:
                            owners[inode].add(group)
        except (OSError, ValueError, IndexError) as exc:
            raise BrowserRuntimeError("browser_identity") from exc
        if (
            not socket_inodes
            or any(groups != {process_group} for groups in owners.values())
        ):
            raise BrowserRuntimeError("browser_identity")
        return
    raise BrowserRuntimeError("browser_identity")


def _verify_connected_browser_version(browser: Any, expected_version: str) -> None:
    session = None
    try:
        session = browser.new_browser_cdp_session()
        response = session.send("Browser.getVersion")
    except BaseException as exc:
        raise BrowserRuntimeError("browser_identity") from exc
    finally:
        if session is not None:
            try:
                session.detach()
            except BaseException as exc:
                raise BrowserRuntimeError("browser_identity") from exc
    if not isinstance(response, dict) or "product" not in response:
        raise BrowserRuntimeError("browser_identity")
    product = response["product"]
    if (
        not isinstance(product, str)
        or "/" not in product
        or product.rsplit("/", 1)[-1] != expected_version
    ):
        raise BrowserRuntimeError("browser_identity")


@contextmanager
def _blocked_runtime_signals() -> Iterator[None]:
    runtime_signals = {signal.SIGINT, signal.SIGTERM}
    if (
        threading.current_thread() is threading.main_thread()
        and hasattr(signal, "pthread_sigmask")
    ):
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, runtime_signals)
        try:
            yield
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
    else:
        yield


class PrelaunchedCdpRuntime:
    """Deep internal adapter: attest, launch, attach, and clean one browser."""

    def __init__(self, executable: Path | _SelectedExecutable) -> None:
        if isinstance(executable, _SelectedExecutable):
            executable_path = executable.path
            expected_source_identity = executable.source_identity
        else:
            executable_path = executable
            expected_source_identity = None
        self._executable = executable_path
        self._profile, profile_sha256 = _load_profile()
        self._pinned_executable = _PinnedExecutable(
            executable_path,
            expected_source_identity=expected_source_identity,
        )
        try:
            browser_identity = _attest(self._pinned_executable, self._profile)
        except BaseException as exc:
            body_cleanup = exc if _is_typed_browser_cleanup(exc) else None
            try:
                self._pinned_executable.close()
            except BrowserRuntimeError as close_cleanup:
                if body_cleanup is None or _is_retained_browser_cleanup(
                    close_cleanup
                ):
                    raise close_cleanup from exc
            if body_cleanup is not None:
                raise body_cleanup
            raise
        self.evidence = {
            "schema": RUNTIME_SCHEMA,
            "adapter_profile": {
                "name": self._profile["name"],
                "sha256": profile_sha256,
            },
            "browser_identity": browser_identity,
            "result": "passed",
        }
        if self._pinned_executable.tree_manifest_sha256 is not None:
            self.evidence["execution_authority"] = _browser_execution_authority(
                self._pinned_executable.tree_manifest_sha256,
                browser_identity["sha256"],
            )
        self._profile_dir: Path | None = None
        self._profile_identity: tuple[int, int] | None = None
        self._profile_cleanup_forbidden = False
        self._profile_fd: int | None = None
        self._profile_parent_fd: int | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._process_group: int | None = None
        self._endpoint: str | None = None

    def _prelaunch(self) -> str:
        try:
            try:
                profile = Path(tempfile.mkdtemp(prefix="meshshot-cdp-"))
            except (OSError, TypeError, ValueError) as exc:
                raise BrowserRuntimeError("browser_profile") from exc
            self._profile_dir = profile
            try:
                directory_flags = (
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                self._profile_parent_fd = os.open(profile.parent, directory_flags)
                self._profile_fd = os.open(
                    profile.name,
                    directory_flags,
                    dir_fd=self._profile_parent_fd,
                )
                profile_info = os.fstat(self._profile_fd)
                path_info = os.stat(
                    profile.name,
                    dir_fd=self._profile_parent_fd,
                    follow_symlinks=False,
                )
                mode = profile_info.st_mode
                profile_nonempty = any(profile.iterdir())
            except OSError as exc:
                raise BrowserRuntimeError("browser_profile") from exc
            self._profile_identity = (profile_info.st_dev, profile_info.st_ino)
            if (
                stat.S_ISLNK(path_info.st_mode)
                or not stat.S_ISDIR(mode)
                or (path_info.st_dev, path_info.st_ino) != self._profile_identity
                or profile_nonempty
            ):
                self._profile_cleanup_forbidden = True
                raise BrowserRuntimeError("browser_profile")
            try:
                os.chmod(profile, 0o700)
            except OSError as exc:
                raise BrowserRuntimeError("browser_profile") from exc
            argv = [
                os.fspath(self._executable),
                *self._profile["arguments"],
                f"--user-data-dir={profile}",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                "about:blank",
            ]
            try:
                launch_options: dict[str, Any] = {}
                if sys.platform.startswith("linux"):
                    launch_options["_handoff_deadline"] = (
                        time.monotonic()
                        + float(self._profile["startup_timeout_ms"]) / 1000
                    )
                with _blocked_runtime_signals():
                    if sys.platform.startswith("linux"):
                        launch = self._pinned_executable.launch_live(
                            argv,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                            close_fds=True,
                            **launch_options,
                        )
                        process = (
                            launch.process
                            if isinstance(launch, _LiveBrowserLaunch)
                            else launch
                        )
                        process_pid = (
                            getattr(process, "pid", None)
                            if type(process) is subprocess.Popen
                            else None
                        )
                        if type(process_pid) is int and process_pid > 1:
                            self._process = process
                            self._process_group = process_pid
                        if (
                            not isinstance(launch, _LiveBrowserLaunch)
                            or self._process is None
                        ):
                            raise BrowserRuntimeError("browser_identity")
                        if launch._proof is not _LINUX_LIVE_IMAGE_PROOF:
                            raise BrowserRuntimeError("browser_identity")
                    else:
                        self._process = self._pinned_executable.popen(
                            argv,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                            close_fds=True,
                        )
                        self._process_group = self._process.pid
                        self._pinned_executable.verify_running_image(
                            self._process.pid,
                            float(self._profile["startup_timeout_ms"]) / 1000,
                        )
            except BrowserRuntimeError as exc:
                if exc.operation != "browser_identity":
                    raise
                raise BrowserRuntimeError(
                    "browser_identity",
                    browser_identity_substage="live_running_image_identity",
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise BrowserRuntimeError("browser_readiness_timeout") from exc
            except OSError as exc:
                raise BrowserRuntimeError(_prelaunch_operation(exc)) from exc
            deadline = time.monotonic() + float(
                self._profile["startup_timeout_ms"]
            ) / 1000
            readiness = profile / "DevToolsActivePort"
            while time.monotonic() < deadline:
                if _owned_process_exited(self._process):
                    raise BrowserRuntimeError("browser_prelaunch")
                try:
                    lines = readiness.read_text(encoding="utf-8").splitlines()
                except (FileNotFoundError, PermissionError):
                    time.sleep(0.02)
                    continue
                if (
                    len(lines) != 2
                    or not lines[0].isdigit()
                    or not 0 < int(lines[0]) < 65536
                    or _DEVTOOLS_PATH.fullmatch(lines[1]) is None
                ):
                    raise BrowserRuntimeError("browser_readiness")
                try:
                    _verify_listener_owner(
                        self._process_group,
                        int(lines[0]),
                        float(self._profile["startup_timeout_ms"]) / 1000,
                    )
                except BrowserRuntimeError as exc:
                    raise BrowserRuntimeError(
                        "browser_identity",
                        browser_identity_substage=(
                            "loopback_listener_address_ownership"
                        ),
                    ) from exc
                self._endpoint = f"http://127.0.0.1:{int(lines[0])}"
                return self._endpoint
            raise BrowserRuntimeError("browser_readiness_timeout")
        except BaseException as exc:
            body_cleanup = exc if _is_typed_browser_cleanup(exc) else None
            try:
                self._cleanup()
            except BrowserRuntimeError as cleanup_error:
                if body_cleanup is None or _is_retained_browser_cleanup(
                    cleanup_error
                ):
                    raise cleanup_error from exc
            if body_cleanup is not None:
                raise body_cleanup
            raise

    def _cleanup(self) -> None:
        failure = False
        cleanup_substage: str | None = None
        cleanup_check: str | None = None
        cleanup_retained = False

        def record_cleanup(
            substage: str,
            check: str,
            *,
            retained: bool = False,
        ) -> None:
            nonlocal failure, cleanup_substage, cleanup_check, cleanup_retained
            failure = True
            if cleanup_check is None or retained:
                cleanup_substage = substage
                cleanup_check = check
                cleanup_retained = retained

        process = self._process
        process_group = self._process_group
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError:
                record_cleanup("private_browser_process_group", "term_signal")
        leader_timed_out = False
        if process is not None:
            try:
                process.wait(timeout=float(self._profile["cleanup_term_ms"]) / 1000)
            except subprocess.TimeoutExpired:
                leader_timed_out = True
            except OSError:
                record_cleanup("private_browser_process_group", "leader_term_wait")
        if process_group is not None:
            group_empty = False
            group_empty_proven = False
            if not leader_timed_out:
                try:
                    group_empty = _wait_group_empty(
                        process_group,
                        float(self._profile["cleanup_term_ms"]) / 1000,
                    )
                    group_empty_proven = True
                except (BrowserRuntimeError, OSError):
                    record_cleanup(
                        "private_browser_process_group",
                        "term_group_empty",
                    )
            if leader_timed_out or not group_empty:
                try:
                    os.killpg(process_group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except (BrowserRuntimeError, OSError):
                    record_cleanup("private_browser_process_group", "kill_signal")
                if process is not None and leader_timed_out:
                    try:
                        process.wait(
                            timeout=float(self._profile["cleanup_kill_ms"]) / 1000
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        record_cleanup(
                            "private_browser_process_group",
                            "leader_kill_wait",
                        )
                try:
                    group_empty = _wait_group_empty(
                        process_group,
                        float(self._profile["cleanup_kill_ms"]) / 1000,
                    )
                    group_empty_proven = True
                except (BrowserRuntimeError, OSError):
                    group_empty = False
                    group_empty_proven = False
                    record_cleanup(
                        "private_browser_process_group",
                        "kill_group_empty",
                    )
            if group_empty_proven and not group_empty:
                record_cleanup(
                    "private_browser_process_group",
                    "kill_group_empty",
                    retained=True,
                )
        if self._profile_dir is not None:
            quarantine: Path | None = None
            if getattr(self, "_profile_cleanup_forbidden", False):
                record_cleanup(
                    "private_browser_profile",
                    "authority_validation",
                )
            else:
                quarantine_fd: int | None = None
                profile_stage = "authority_validation"
                try:
                    profile_fd = getattr(self, "_profile_fd", None)
                    parent_fd = getattr(self, "_profile_parent_fd", None)
                    if profile_fd is None or parent_fd is None:
                        raise OSError("profile identity changed")
                    profile_info = os.fstat(profile_fd)
                    if (
                        getattr(self, "_profile_identity", None) is None
                        or (profile_info.st_dev, profile_info.st_ino)
                        != self._profile_identity
                    ):
                        raise OSError("profile identity changed")
                    profile_stage = "quarantine_create"
                    quarantine = _private_child_directory(
                        self._profile_dir.parent,
                        parent_fd,
                        "meshshot-profile-cleanup-",
                    )
                    quarantine_fd = os.open(
                        quarantine,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                    )
                    profile_stage = "quarantine_move"
                    os.rename(
                        self._profile_dir.name,
                        "profile",
                        src_dir_fd=parent_fd,
                        dst_dir_fd=quarantine_fd,
                    )
                    moved_info = os.stat(
                        "profile",
                        dir_fd=quarantine_fd,
                        follow_symlinks=False,
                    )
                    if (
                        stat.S_ISLNK(moved_info.st_mode)
                        or not stat.S_ISDIR(moved_info.st_mode)
                        or (moved_info.st_dev, moved_info.st_ino)
                        != self._profile_identity
                    ):
                        os.rename(
                            "profile",
                            self._profile_dir.name,
                            src_dir_fd=quarantine_fd,
                            dst_dir_fd=parent_fd,
                        )
                        raise OSError("profile identity changed")
                    profile_stage = "recursive_remove"
                    shutil.rmtree(quarantine / "profile")
                except (BrowserRuntimeError, OSError):
                    record_cleanup("private_browser_profile", profile_stage)
                finally:
                    if quarantine_fd is not None:
                        try:
                            os.close(quarantine_fd)
                        except OSError:
                            record_cleanup(
                                "private_browser_profile",
                                "authority_close",
                            )
                    if quarantine is not None:
                        try:
                            quarantine.rmdir()
                        except OSError:
                            record_cleanup(
                                "private_browser_profile",
                                "recursive_remove",
                            )
            for attribute in ("_profile_fd", "_profile_parent_fd"):
                descriptor = getattr(self, attribute, None)
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        record_cleanup(
                            "private_browser_profile",
                            "authority_close",
                        )
                    setattr(self, attribute, None)
            if not getattr(self, "_profile_cleanup_forbidden", False):
                retained_paths = [self._profile_dir]
                if quarantine is not None:
                    retained_paths.extend((quarantine, quarantine / "profile"))
                if any(os.path.lexists(path) for path in retained_paths):
                    record_cleanup(
                        "private_browser_profile",
                        "absence",
                        retained=True,
                    )
        pinned = getattr(self, "_pinned_executable", None)
        if pinned is not None:
            try:
                pinned.close()
            except BrowserRuntimeError as exc:
                if (
                    exc.browser_cleanup_substage is not None
                    and exc.browser_cleanup_check is not None
                ):
                    record_cleanup(
                        exc.browser_cleanup_substage,
                        exc.browser_cleanup_check,
                        retained=_is_retained_browser_cleanup(exc),
                    )
                else:
                    failure = True
        if failure:
            raise BrowserRuntimeError(
                "browser_cleanup",
                browser_cleanup_substage=cleanup_substage,
                browser_cleanup_check=cleanup_check,
                _browser_cleanup_retained=cleanup_retained,
            )

    def _verify_connected_browser(self, browser: Any) -> None:
        _verify_connected_browser_version(
            browser,
            str(self._profile["browser_version"]),
        )

    def supervisor_authority(self) -> dict[str, Any]:
        """Return the fixed private authority only while the browser is live."""

        if self._endpoint is None or self._process_group is None:
            raise BrowserRuntimeError("browser_connect")
        parsed = urlsplit(self._endpoint)
        try:
            port = parsed.port
        except ValueError as exc:
            raise BrowserRuntimeError("browser_identity") from exc
        if port is None:
            raise BrowserRuntimeError("browser_identity")
        try:
            _verify_listener_owner(
                self._process_group,
                port,
                float(self._profile["startup_timeout_ms"]) / 1000,
            )
        except BrowserRuntimeError as exc:
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_substage="loopback_listener_address_ownership",
            ) from exc
        return {
            "schema": SUPERVISOR_PROTOCOL_SCHEMA,
            "type": "authority",
            "endpoint": self._endpoint,
            "process_group": self._process_group,
            "listener_reproof": "passed",
            "browser_runtime": self.evidence,
        }

    @contextmanager
    def open(self, chromium: Any) -> Iterator[Any]:
        try:
            with _runtime_signal_cleanup():
                endpoint = self._prelaunch()
                try:
                    browser = chromium.connect_over_cdp(
                        endpoint,
                        timeout=int(self._profile["startup_timeout_ms"]),
                        is_local=True,
                    )
                except BaseException as exc:
                    try:
                        self._cleanup()
                    except BrowserRuntimeError as cleanup_exc:
                        if isinstance(exc, _RuntimeSignal):
                            exc.cleanup_error = cleanup_exc
                        else:
                            raise
                    if isinstance(exc, _RuntimeSignal):
                        raise
                    raise BrowserRuntimeError("browser_connect") from exc
                try:
                    self._verify_connected_browser(browser)
                except BrowserRuntimeError as exc:
                    self._cleanup()
                    raise BrowserRuntimeError(
                        "browser_identity",
                        browser_identity_substage=(
                            "connected_cdp_browser_version_identity"
                        ),
                    ) from exc
                body_error: BaseException | None = None
                try:
                    yield browser
                except BaseException as exc:
                    body_error = exc
                finally:
                    try:
                        self._cleanup()
                    except BrowserRuntimeError as cleanup_exc:
                        if isinstance(body_error, _RuntimeSignal):
                            body_error.cleanup_error = cleanup_exc
                        elif (
                            _is_typed_browser_cleanup(body_error)
                            and not _is_retained_browser_cleanup(cleanup_exc)
                        ):
                            pass
                        else:
                            raise
                if body_error is not None:
                    raise body_error
        except _RuntimeSignal as exc:
            if exc.cleanup_error is not None:
                raise exc.cleanup_error
            raise BrowserRuntimeError("browser_signal") from exc


class SupervisedCdpAttachmentRuntime:
    """Attach to the one browser owned outside the nested exec-denial sandbox."""

    def __init__(self, executable: Path | _SelectedExecutable) -> None:
        executable_path = (
            executable.path
            if isinstance(executable, _SelectedExecutable)
            else executable
        )
        self._profile, profile_sha256 = _load_profile()
        browser_identity = _attest_attachment(executable_path, self._profile)
        self.evidence = {
            "schema": RUNTIME_SCHEMA,
            "adapter_profile": {
                "name": self._profile["name"],
                "sha256": profile_sha256,
            },
            "browser_identity": browser_identity,
            "result": "passed",
        }
        tree_manifest_sha256 = os.environ.get(_BROWSER_TREE_MANIFEST_ENV)
        if tree_manifest_sha256 is not None:
            self.evidence["execution_authority"] = _browser_execution_authority(
                tree_manifest_sha256,
                browser_identity["sha256"],
            )

    @staticmethod
    def _validate_socket_path() -> None:
        try:
            root = SUPERVISOR_NESTED_ROOT.lstat()
            endpoint = SUPERVISOR_NESTED_SOCKET.lstat()
        except OSError as exc:
            raise BrowserRuntimeError("browser_connect") from exc
        if (
            stat.S_ISLNK(root.st_mode)
            or not stat.S_ISDIR(root.st_mode)
            or root.st_uid != os.geteuid()
            or stat.S_IMODE(root.st_mode) & 0o022
            or stat.S_ISLNK(endpoint.st_mode)
            or not stat.S_ISSOCK(endpoint.st_mode)
            or endpoint.st_uid != os.geteuid()
            or stat.S_IMODE(endpoint.st_mode) & 0o077
        ):
            raise BrowserRuntimeError("browser_connect")

    @staticmethod
    def _client_authority() -> tuple[int, str]:
        descriptor: int | None = None
        try:
            descriptor = os.open(
                SUPERVISOR_NESTED_AUTHORITY,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            info = os.fstat(descriptor)
            if info.st_size <= 0 or info.st_size > _SUPERVISOR_PACKET_LIMIT:
                raise OSError("invalid private authority size")
            raw = os.read(descriptor, _SUPERVISOR_PACKET_LIMIT + 1)
        except OSError as exc:
            raise BrowserRuntimeError("browser_connect") from exc
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    raise BrowserRuntimeError(
                        "browser_cleanup",
                        browser_cleanup_substage=(
                            "private_supervisor_record_descriptors"
                        ),
                        browser_cleanup_check=(
                            "authority_record_descriptor_close"
                        ),
                    ) from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o400
            or not raw
            or len(raw) > _SUPERVISOR_PACKET_LIMIT
        ):
            raise BrowserRuntimeError("browser_connect")
        value = _loads_json_strict(raw)
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "supervisor_pid",
            "nonce",
        }:
            raise BrowserRuntimeError("browser_connect")
        supervisor_pid = value.get("supervisor_pid")
        nonce = value.get("nonce")
        if (
            value.get("schema") != SUPERVISOR_AUTHORITY_SCHEMA
            or isinstance(supervisor_pid, bool)
            or not isinstance(supervisor_pid, int)
            or supervisor_pid <= 1
            or not isinstance(nonce, str)
            or re.fullmatch(r"[0-9a-f]{64}", nonce) is None
        ):
            raise BrowserRuntimeError("browser_connect")
        return supervisor_pid, nonce

    @staticmethod
    def _validate_supervisor_peer(
        connection: socket.socket, *, expected_pid: int
    ) -> None:
        pid, uid, _gid = _peer_credentials(connection)
        if pid != expected_pid or uid != os.geteuid():
            raise BrowserRuntimeError("browser_connect")

    def _validate_authority(
        self, authority: Any, *, expected_nonce: str
    ) -> tuple[str, int]:
        if not isinstance(authority, dict) or set(authority) != {
            "schema",
            "type",
            "nonce",
            "endpoint",
            "process_group",
            "listener_reproof",
            "browser_runtime",
        }:
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_substage="runtime_evidence_cross_binding",
            )
        endpoint = authority.get("endpoint")
        process_group = authority.get("process_group")
        runtime = authority.get("browser_runtime")
        if (
            authority.get("schema") != SUPERVISOR_PROTOCOL_SCHEMA
            or authority.get("type") != "authority"
            or authority.get("nonce") != expected_nonce
            or not isinstance(endpoint, str)
            or isinstance(process_group, bool)
            or not isinstance(process_group, int)
            or process_group <= 1
            or authority.get("listener_reproof") != "passed"
            or runtime != self.evidence
        ):
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_substage="runtime_evidence_cross_binding",
            )
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_substage="loopback_listener_address_ownership",
            )
        try:
            port = parsed.port
        except ValueError as exc:
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_substage="loopback_listener_address_ownership",
            ) from exc
        if port is None or not 0 < port < 65536:
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_substage="loopback_listener_address_ownership",
            )
        return endpoint, process_group

    @contextmanager
    def open(self, chromium: Any) -> Iterator[Any]:
        self._validate_socket_path()
        supervisor_pid, nonce = self._client_authority()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        browser = None
        failure: BaseException | None = None
        cleanup_error: BrowserRuntimeError | None = None
        completion_ready = False
        completion_result = "failed"

        def record_cleanup(
            substage: str,
            check: str,
            *,
            retained: bool = False,
        ) -> None:
            nonlocal cleanup_error
            if cleanup_error is None or retained:
                cleanup_error = BrowserRuntimeError(
                    "browser_cleanup",
                    browser_cleanup_substage=substage,
                    browser_cleanup_check=check,
                    _browser_cleanup_retained=retained,
                )

        def record_exception(
            exc: BaseException,
            *,
            fallback_check: str,
        ) -> None:
            if _is_typed_browser_cleanup(exc):
                assert isinstance(exc, BrowserRuntimeError)
                assert exc.browser_cleanup_substage is not None
                assert exc.browser_cleanup_check is not None
                record_cleanup(
                    exc.browser_cleanup_substage,
                    exc.browser_cleanup_check,
                    retained=_is_retained_browser_cleanup(exc),
                )
            else:
                record_cleanup("nested_attachment_close", fallback_check)
        try:
            connection.settimeout(
                float(self._profile["startup_timeout_ms"]) / 1000
            )
            try:
                connection.connect(os.fspath(SUPERVISOR_NESTED_SOCKET))
            except (OSError, socket.timeout) as exc:
                raise BrowserRuntimeError("browser_connect") from exc
            self._validate_supervisor_peer(
                connection,
                expected_pid=supervisor_pid,
            )
            _send_supervisor_packet(
                connection,
                {
                    "schema": SUPERVISOR_PROTOCOL_SCHEMA,
                    "type": "hello",
                    "nonce": nonce,
                },
            )
            authority = _receive_supervisor_packet(connection)
            endpoint, _process_group = self._validate_authority(
                authority,
                expected_nonce=nonce,
            )
            completion_ready = True
            try:
                browser = chromium.connect_over_cdp(
                    endpoint,
                    timeout=int(self._profile["startup_timeout_ms"]),
                    is_local=True,
                )
            except BaseException as exc:
                failure = BrowserRuntimeError("browser_connect")
                failure.__cause__ = exc
            if failure is None:
                try:
                    _verify_connected_browser_version(
                        browser,
                        str(self._profile["browser_version"]),
                    )
                except BrowserRuntimeError as exc:
                    failure = BrowserRuntimeError(
                        "browser_identity",
                        browser_identity_substage=(
                            "connected_cdp_browser_version_identity"
                        ),
                    )
                    failure.__cause__ = exc
            if failure is None:
                try:
                    assert browser is not None
                    yield browser
                    completion_result = "passed"
                except BaseException as exc:
                    failure = exc
        except BaseException as exc:
            failure = exc
        finally:
            if _is_typed_browser_cleanup(failure):
                assert isinstance(failure, BrowserRuntimeError)
                cleanup_error = failure
            if browser is not None:
                try:
                    browser.close()
                except BaseException as exc:
                    record_exception(
                        exc,
                        fallback_check="browser_session_close",
                    )
            if completion_ready:
                try:
                    _send_supervisor_packet(
                        connection,
                        {
                            "schema": SUPERVISOR_PROTOCOL_SCHEMA,
                            "type": "completion",
                            "nonce": nonce,
                            "result": completion_result,
                        },
                    )
                except BaseException as exc:
                    record_exception(exc, fallback_check="completion_send")
                else:
                    try:
                        shutdown = _receive_supervisor_packet(connection)
                        if shutdown != {
                            "schema": SUPERVISOR_PROTOCOL_SCHEMA,
                            "type": "shutdown",
                            "nonce": nonce,
                        }:
                            raise BrowserRuntimeError(
                                "browser_cleanup",
                                browser_cleanup_substage="nested_attachment_close",
                                browser_cleanup_check="shutdown_receive",
                            )
                    except BaseException as exc:
                        record_exception(exc, fallback_check="shutdown_receive")
            try:
                connection.close()
            except OSError:
                record_cleanup("nested_attachment_close", "transport_close")
        if cleanup_error is not None:
            if cleanup_error is failure:
                raise cleanup_error
            raise cleanup_error from failure
        if failure is not None:
            raise failure


class _RuntimeSignal(BaseException):
    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum
        self.cleanup_error: BrowserRuntimeError | None = None


@contextmanager
def _runtime_signal_cleanup() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    watched = (signal.SIGINT, signal.SIGTERM)
    previous = {signum: signal.getsignal(signum) for signum in watched}
    installed = tuple(
        signum for signum in watched if previous[signum] is not signal.SIG_IGN
    )
    caught: _RuntimeSignal | None = None

    def interrupt(signum: int, _frame: Any) -> None:
        raise _RuntimeSignal(signum)

    try:
        for signum in installed:
            signal.signal(signum, interrupt)
        try:
            yield
        except _RuntimeSignal as exc:
            caught = exc
    finally:
        for signum in installed:
            signal.signal(signum, previous[signum])
    if caught is None:
        return
    handler = previous[caught.signum]
    if callable(handler):
        handler(caught.signum, None)
    elif handler is signal.SIG_DFL:
        os.kill(os.getpid(), caught.signum)
    raise caught
