from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Sequence

from scripts.pilot import deployment_authority
from scripts.pilot import provider_free_output

from . import tap_observer
from .protocol import (
    PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH,
    PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH,
    PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
    PROVIDER_FREE_BROWSER_IDENTITY_SUBSTAGES,
    PROVIDER_FREE_PRIVATE_VERSION_EXECUTION_CHECKS,
    PROVIDER_FREE_PLAYWRIGHT_PACKAGE_REVISION_CHECKS,
    PROVIDER_FREE_MESHSHOT_EXECUTABLE_ROOT,
    PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES,
    PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_SCHEMA,
    PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_OPERATIONS,
    PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_EVIDENCE_PUBLICATION_OPERATION,
    PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH,
    PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
    PROVIDER_FREE_PREVIEW_SANDBOX_PATH,
    PROVIDER_FREE_SCENARIO_FAILURE_PATH,
    PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA,
    PROVIDER_FREE_SCENARIO_FAILURE_STAGES,
    provider_free_browser_identity_checks,
    PROVIDER_FREE_STAGED_BROWSER_CACHE,
    PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
    ProtocolError,
    TERMINAL_STATES,
    default_state_root,
    heartbeat,
    load_state,
    log_path,
    parse_handle,
    provider_free_browser_exec_diagnostic_allowed,
    provider_free_browser_exec_diagnostic_matches_operation,
    provider_free_browser_identity_diagnostic_allowed,
    provider_free_preview_public_wrapper_allowed,
    provider_free_preview_public_wrapper_matches_operation,
    provider_free_preview_sandbox_receipt_allowed,
    provider_free_scenario_failure_operation_allowed,
    public_state,
    publish_state,
    request_authority_payload,
    request_authority_sha256,
    state_path,
    transition,
    utc_now,
    validate_component,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
PILOT_SCRIPT = REPO_ROOT / "scripts" / "pilot" / "toys4k-pilot.sh"
PROVIDER_FREE_RUNNER_MODULE = "scripts.pilot.provider_free_runner"
DEFAULT_HEARTBEAT_INTERVAL = 5.0
DEFAULT_STALE_AFTER = 60.0
DEFAULT_WAIT_TIMEOUT = 12 * 60 * 60.0
PROCESS_TERMINATION_GRACE = 5.0
PROVIDER_FREE_BOOTSTRAP_LOG_BYTES = 4 * 1024
_PROVIDER_FREE_RUNNER_ERROR_CLASSIFICATIONS = (
    (
        b"provider-free execution profile is missing or stale",
        "runner-execution-profile-rejected",
    ),
    (
        b"provider-free environment contains non-allowlisted names:",
        "runner-environment-allowlist-rejected",
    ),
    (
        b"provider-free stripped-name receipt is invalid",
        "runner-stripped-name-receipt-rejected",
    ),
    (
        b"provider-free CVM_PROVIDER_FREE_REQUEST_AUTHORITY_SHA256 "
        b"is missing or invalid",
        "runner-request-digest-rejected",
    ),
    (
        b"provider-free CVM_PROVIDER_FREE_DEPLOYMENT_TREE_SHA256 "
        b"is missing or invalid",
        "runner-request-digest-rejected",
    ),
    (
        b"provider-free immutable request is invalid",
        "runner-request-digest-rejected",
    ),
    (
        b"provider-free immutable request digest conflicts",
        "runner-request-digest-rejected",
    ),
    (
        b"PATH bwrap does not match trusted system runtime",
        "runner-bwrap-path-rejected",
    ),
    (
        b"trusted runtime identity is invalid:",
        "runner-runtime-identity-rejected",
    ),
    (
        b"unsafe provider-free output path:",
        "runner-output-path-rejected",
    ),
)


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


_PROVIDER_FREE_RUNNER_ERROR_MARKER = b"provider-free-runner:"
_SECRET_HEADLINE = re.compile(
    r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*\S+|[A-Za-z0-9_=-]{32,}"
)
_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.-])/(?:[^\s/]+/)+[^\s]*")
_PILOT_GROUP = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$")


@dataclass(frozen=True)
class ProviderFreeScenario:
    """One repository-owned workload selectable without remote argv input."""

    name: str
    identity: str


@dataclass(frozen=True)
class AttestedBrowserMount:
    """One job-scoped host tree exposed at the stable sandbox interface."""

    host_revision: Path
    sandbox_cache: str
    tree_manifest_sha256: str = ""
    revision: str = ""
    executable_relative: str = ""
    executable_sha256: str = ""


class BrowserStageError(RuntimeError):
    """The deployment-attested browser could not be staged safely."""


class _BrowserStageSignal(BaseException):
    """One catchable process signal that must unwind browser staging."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


_BROWSER_STAGE_SIGNALS = (signal.SIGINT, signal.SIGTERM)
_EXEC_PROBE_BYTES = (
    b"#!/bin/sh\n"
    b"printf 'cvm.browser-stage-exec-probe/1\\n'\n"
)
_EXEC_PROBE_STDOUT = b"cvm.browser-stage-exec-probe/1\n"
_EXEC_PROBE_NAME = ".cvm-browser-stage-exec-probe"
_EXEC_PROBE_TIMEOUT_SECONDS = 5
_PRODUCTION_SUBPROCESS_RUN = subprocess.run


@contextmanager
def _browser_stage_signal_cleanup() -> Iterator[None]:
    """Turn catchable termination into an unwind, then re-emit it exactly."""

    if threading.current_thread() is not threading.main_thread():
        raise BrowserStageError(
            "browser staging signal cleanup requires the main thread"
        )
    previous = {
        signum: signal.getsignal(signum)
        for signum in _BROWSER_STAGE_SIGNALS
    }
    interrupted: _BrowserStageSignal | None = None

    def interrupt(signum: int, _frame: Any) -> None:
        for catchable in _BROWSER_STAGE_SIGNALS:
            signal.signal(catchable, signal.SIG_IGN)
        raise _BrowserStageSignal(signum)

    try:
        for signum in _BROWSER_STAGE_SIGNALS:
            signal.signal(signum, interrupt)
        try:
            yield
        except _BrowserStageSignal as exc:
            interrupted = exc
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if interrupted is not None:
        signal.signal(interrupted.signum, signal.SIG_DFL)
        os.kill(os.getpid(), interrupted.signum)
        raise BrowserStageError("failed to re-emit browser staging signal")


def _run_browser_stage_exec_probe(
    probe: Path,
    stage_root: Path,
) -> subprocess.CompletedProcess[bytes]:
    """Use the real production exec boundary behind one private test seam."""

    return _PRODUCTION_SUBPROCESS_RUN(
        [os.fspath(probe)],
        cwd=stage_root,
        env={},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=_EXEC_PROBE_TIMEOUT_SECONDS,
        start_new_session=True,
        close_fds=True,
    )


def _probe_browser_stage_exec(stage_root: Path) -> None:
    """Execute one bounded repository-owned probe on the staged filesystem."""

    probe = stage_root / _EXEC_PROBE_NAME
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    probe_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(probe, flags, 0o700)
        os.write(descriptor, _EXEC_PROBE_BYTES)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        probe.chmod(0o700)
        probe_info = probe.lstat()
        probe_identity = (probe_info.st_dev, probe_info.st_ino)
        completed = _run_browser_stage_exec_probe(probe, stage_root)
        if (
            completed.returncode != 0
            or completed.stdout != _EXEC_PROBE_STDOUT
            or completed.stderr != b""
        ):
            raise BrowserStageError(
                "browser stage is not exec-permitted"
            )
    except BrowserStageError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise BrowserStageError(
            "browser stage is not exec-permitted"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if probe_identity is not None:
            try:
                probe_info = probe.lstat()
                if (
                    stat.S_ISLNK(probe_info.st_mode)
                    or not stat.S_ISREG(probe_info.st_mode)
                    or (probe_info.st_dev, probe_info.st_ino)
                    != probe_identity
                    or stat.S_IMODE(probe_info.st_mode) != 0o700
                    or probe.read_bytes() != _EXEC_PROBE_BYTES
                ):
                    raise BrowserStageError(
                        "browser stage exec probe identity changed"
                    )
                probe.unlink()
            except BrowserStageError:
                raise
            except OSError as exc:
                raise BrowserStageError(
                    "browser stage exec probe cleanup failed"
                ) from exc


def _browser_mount(
    chromium: dict[str, Any],
    handle: str,
) -> AttestedBrowserMount:
    parsed = parse_handle(handle)
    return AttestedBrowserMount(
        host_revision=(
            Path(chromium["host_cache_path"])
            / ".cvm-provider-free-browser-stages"
            / f"{parsed['group']}.{parsed['exp']}"
            / "attested"
        ),
        sandbox_cache=PROVIDER_FREE_STAGED_BROWSER_CACHE,
    )


def _browser_tree_manifest(root: Path) -> dict[str, tuple[str, int, str | None]]:
    """Return the closed regular-file tree identity used around one copy."""
    try:
        return deployment_authority.browser_tree_manifest(
            root,
            readonly_projection=False,
        )
    except deployment_authority.DeploymentAuthorityError as exc:
        raise BrowserStageError("browser revision tree is invalid") from exc


def _browser_tree_manifest_sha256(
    manifest: dict[str, tuple[str, int, str | None]],
) -> str:
    try:
        return deployment_authority.browser_tree_manifest_sha256(manifest)
    except deployment_authority.DeploymentAuthorityError as exc:
        raise BrowserStageError("browser revision manifest is invalid") from exc


def _copy_browser_tree_fd(
    source_fd: int,
    target_fd: int,
) -> None:
    """Copy and freeze a browser tree using only already-authorized dirfds."""

    source_root = os.fstat(source_fd)
    if not stat.S_ISDIR(source_root.st_mode):
        raise BrowserStageError("browser revision root is invalid")

    def copy_directory(open_source: int, open_target: int) -> None:
        for name in sorted(os.listdir(open_source)):
            if not name or name in {".", ".."} or "/" in name:
                raise BrowserStageError("browser revision entry is invalid")
            info = os.stat(name, dir_fd=open_source, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise BrowserStageError("browser revision contains a link")
            if stat.S_ISDIR(info.st_mode):
                os.mkdir(name, mode=0o700, dir_fd=open_target)
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                source_child: int | None = None
                target_child: int | None = None
                try:
                    source_child = os.open(name, flags, dir_fd=open_source)
                    target_child = os.open(name, flags, dir_fd=open_target)
                    opened = os.fstat(source_child)
                    if (opened.st_dev, opened.st_ino, opened.st_mode) != (
                        info.st_dev,
                        info.st_ino,
                        info.st_mode,
                    ):
                        raise BrowserStageError("browser revision changed")
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
                        raise BrowserStageError("browser revision changed")
                    os.fchmod(target_child, stat.S_IMODE(info.st_mode) & ~0o222)
                    try:
                        os.fsync(target_child)
                    except OSError as exc:
                        if exc.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EBADF}:
                            raise
                finally:
                    close_failed = False
                    if target_child is not None:
                        try:
                            os.close(target_child)
                        except OSError:
                            close_failed = True
                    if source_child is not None:
                        try:
                            os.close(source_child)
                        except OSError:
                            close_failed = True
                    if close_failed:
                        raise BrowserStageError(
                            "browser stage descriptor cleanup failed"
                        )
                continue
            if not stat.S_ISREG(info.st_mode):
                raise BrowserStageError("browser revision contains a special entry")
            source_file: int | None = None
            target_file: int | None = None
            try:
                source_file = os.open(
                    name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=open_source,
                )
                opened = os.fstat(source_file)
                if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mode) != (
                    info.st_dev,
                    info.st_ino,
                    info.st_size,
                    info.st_mode,
                ):
                    raise BrowserStageError("browser revision changed")
                target_file = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
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
                os.fchmod(target_file, stat.S_IMODE(info.st_mode) & ~0o222)
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
                    raise BrowserStageError("browser revision changed")
            finally:
                close_failed = False
                if target_file is not None:
                    try:
                        os.close(target_file)
                    except OSError:
                        close_failed = True
                if source_file is not None:
                    try:
                        os.close(source_file)
                    except OSError:
                        close_failed = True
                if close_failed:
                    raise BrowserStageError(
                        "browser stage descriptor cleanup failed"
                    )

    copy_directory(source_fd, target_fd)
    os.fchmod(target_fd, stat.S_IMODE(source_root.st_mode) & ~0o222)


def _remove_browser_tree_owned(
    parent_fd: int,
    name: str,
    identity: tuple[int, int],
) -> None:
    """Remove exactly one owned tree without following replacement paths."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    root_fd: int | None = None
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(current.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or (current.st_dev, current.st_ino) != identity
        ):
            raise BrowserStageError("browser stage identity changed before cleanup")
        root_fd = os.open(name, flags, dir_fd=parent_fd)
        opened = os.fstat(root_fd)
        if (opened.st_dev, opened.st_ino) != identity:
            raise BrowserStageError("browser stage identity changed before cleanup")

        def remove_contents(directory_fd: int) -> None:
            os.fchmod(directory_fd, 0o700)
            for child in sorted(os.listdir(directory_fd)):
                info = os.stat(child, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    raise BrowserStageError("browser stage contains a link")
                if stat.S_ISDIR(info.st_mode):
                    child_fd = os.open(child, flags, dir_fd=directory_fd)
                    try:
                        child_opened = os.fstat(child_fd)
                        if (child_opened.st_dev, child_opened.st_ino) != (
                            info.st_dev,
                            info.st_ino,
                        ):
                            raise BrowserStageError("browser stage changed during cleanup")
                        remove_contents(child_fd)
                    finally:
                        os.close(child_fd)
                    current_child = os.stat(
                        child,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    if (current_child.st_dev, current_child.st_ino) != (
                        info.st_dev,
                        info.st_ino,
                    ):
                        raise BrowserStageError("browser stage changed during cleanup")
                    os.rmdir(child, dir_fd=directory_fd)
                elif stat.S_ISREG(info.st_mode):
                    os.unlink(child, dir_fd=directory_fd)
                else:
                    raise BrowserStageError("browser stage contains a special entry")

        remove_contents(root_fd)
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity:
            raise BrowserStageError("browser stage identity changed during cleanup")
        os.rmdir(name, dir_fd=parent_fd)
    except OSError as exc:
        raise BrowserStageError("browser stage cleanup failed") from exc
    finally:
        if root_fd is not None:
            try:
                os.close(root_fd)
            except OSError as exc:
                raise BrowserStageError("browser stage cleanup failed") from exc


@contextmanager
def staged_attested_browser(
    chromium: dict[str, Any],
    handle: str,
    *,
    repo_root: Path | None = None,
) -> Iterator[AttestedBrowserMount]:
    """Copy one attested browser revision into a private host-side stage."""

    with _browser_stage_signal_cleanup():
        with _staged_attested_browser(
            chromium,
            handle,
            repo_root=repo_root,
        ) as mount:
            yield mount


@contextmanager
def _staged_attested_browser(
    chromium: dict[str, Any],
    handle: str,
    *,
    repo_root: Path | None,
) -> Iterator[AttestedBrowserMount]:
    """Implement browser staging behind the signal-safe public interface."""

    source_fd: int | None = None
    stage_parent_fd: int | None = None
    stage_root_fd: int | None = None
    staged_revision_fd: int | None = None
    try:
        parse_handle(handle)
        source_cache = Path(str(chromium["host_cache_path"])).resolve(strict=True)
        source_revision_link = (
            source_cache / f"chromium_headless_shell-{chromium['revision']}"
        )
        if stat.S_ISLNK(source_revision_link.lstat().st_mode):
            raise BrowserStageError("browser revision must not be a symlink")
        source_revision = source_revision_link.resolve(strict=True)
        try:
            source_revision.relative_to(source_cache)
        except ValueError as exc:
            raise BrowserStageError(
                "browser revision escapes its attested cache"
            ) from exc
        trusted_tree_manifest = chromium.get("tree_manifest_sha256")
        if (
            not isinstance(trusted_tree_manifest, str)
            or re.fullmatch(r"[0-9a-f]{64}", trusted_tree_manifest) is None
        ):
            raise BrowserStageError("deployment browser tree identity is invalid")
        source_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        source_flags |= getattr(os, "O_NOFOLLOW", 0)
        source_fd = os.open(source_revision, source_flags)
        source_opened = os.fstat(source_fd)
        source_current = source_revision.lstat()
        if (
            not stat.S_ISDIR(source_opened.st_mode)
            or stat.S_ISLNK(source_current.st_mode)
            or (source_opened.st_dev, source_opened.st_ino, source_opened.st_mode)
            != (source_current.st_dev, source_current.st_ino, source_current.st_mode)
        ):
            raise BrowserStageError("deployment browser tree root changed")
        source_manifest = deployment_authority._browser_tree_manifest_from_fd(
            source_fd,
            readonly_projection=True,
        )
        tree_manifest_sha256 = _browser_tree_manifest_sha256(source_manifest)
        if tree_manifest_sha256 != trusted_tree_manifest:
            raise BrowserStageError(
                "browser revision conflicts with deployment tree identity"
            )
        executable_relative = Path(
            "chrome-headless-shell-linux64/chrome-headless-shell"
        )
        source_executable = source_revision / executable_relative
        if (
            os.fspath(source_executable) != chromium.get("executable_path")
            or not source_executable.lstat().st_mode & 0o111
        ):
            raise BrowserStageError(
                "browser executable path or mode conflicts with deployment identity"
            )
        requested_root = REPO_ROOT if repo_root is None else Path(repo_root)
        repository = requested_root.resolve(strict=True)
        stage_parent = source_cache / ".cvm-provider-free-browser-stages"
        mount_path = _browser_mount(chromium, handle)
        mount = AttestedBrowserMount(
            host_revision=mount_path.host_revision,
            sandbox_cache=mount_path.sandbox_cache,
            tree_manifest_sha256=tree_manifest_sha256,
            revision=str(chromium["revision"]),
            executable_relative=executable_relative.as_posix(),
            executable_sha256=str(chromium["sha256"]),
        )
        stage_root = mount.host_revision.parent
        try:
            stage_parent.resolve(strict=False).relative_to(repository)
        except ValueError:
            pass
        else:
            raise BrowserStageError("browser stage must be outside the repository")
    except BaseException as exc:
        failure: BaseException = exc
        if isinstance(
            exc,
            (
                KeyError,
                OSError,
                ProtocolError,
                ValueError,
                deployment_authority.DeploymentAuthorityError,
            ),
        ):
            failure = BrowserStageError("deployment browser identity is invalid")
            failure.__cause__ = exc
        if source_fd is not None:
            descriptor = source_fd
            source_fd = None
            try:
                os.close(descriptor)
            except OSError as close_error:
                raise BrowserStageError(
                    "browser stage descriptor cleanup failed"
                ) from close_error
        raise failure
    created = False
    try:
        try:
            stage_parent.mkdir(mode=0o700, parents=False, exist_ok=True)
            stage_parent_mode = stage_parent.lstat().st_mode
            if (
                stat.S_ISLNK(stage_parent_mode)
                or not stat.S_ISDIR(stage_parent_mode)
                or stage_parent.resolve(strict=True) != stage_parent
                or stage_parent.stat().st_uid != os.getuid()
                or stat.S_IMODE(stage_parent_mode) & 0o077
            ):
                raise BrowserStageError("browser stage parent is not private")
            if stage_parent.stat().st_dev != source_revision.stat().st_dev:
                raise BrowserStageError(
                    "browser stage is not on the deployment browser filesystem"
                )
            stage_parent_fd = os.open(stage_parent, source_flags)
            opened_parent = os.fstat(stage_parent_fd)
            current_parent = stage_parent.lstat()
            if (opened_parent.st_dev, opened_parent.st_ino) != (
                current_parent.st_dev,
                current_parent.st_ino,
            ):
                raise BrowserStageError("browser stage parent identity changed")
            os.mkdir(stage_root.name, mode=0o700, dir_fd=stage_parent_fd)
            created = True
            stage_root_fd = os.open(stage_root.name, source_flags, dir_fd=stage_parent_fd)
            stage_info = os.fstat(stage_root_fd)
            stage_identity = (stage_info.st_dev, stage_info.st_ino)
            staged_revision = stage_root / "attested"
            os.mkdir("attested", mode=0o700, dir_fd=stage_root_fd)
            staged_revision_fd = os.open(
                "attested",
                source_flags,
                dir_fd=stage_root_fd,
            )
            _copy_browser_tree_fd(source_fd, staged_revision_fd)
            if (
                deployment_authority._browser_tree_manifest_from_fd(
                    source_fd,
                    readonly_projection=True,
                )
                != source_manifest
                or deployment_authority._browser_tree_manifest_from_fd(
                    staged_revision_fd,
                    readonly_projection=False,
                )
                != source_manifest
            ):
                raise BrowserStageError(
                    "staged browser tree conflicts with deployment revision"
                )
            staged_executable = staged_revision / executable_relative
            if (
                hashlib.sha256(staged_executable.read_bytes()).hexdigest()
                != chromium["sha256"]
                or not staged_executable.lstat().st_mode & 0o111
            ):
                raise BrowserStageError(
                    "staged browser executable identity conflicts with deployment identity"
                )
            _probe_browser_stage_exec(stage_root)
        except BrowserStageError:
            raise
        except (KeyError, OSError, shutil.Error, ValueError) as exc:
            raise BrowserStageError("browser stage setup failed") from exc
        yield mount
    finally:
        cleanup_failed = False
        if staged_revision_fd is not None:
            descriptor = staged_revision_fd
            staged_revision_fd = None
            try:
                os.close(descriptor)
            except OSError:
                cleanup_failed = True
        if stage_root_fd is not None:
            descriptor = stage_root_fd
            stage_root_fd = None
            try:
                os.close(descriptor)
            except OSError:
                cleanup_failed = True
        if created:
            try:
                if stage_parent_fd is None:
                    raise BrowserStageError("browser stage cleanup authority missing")
                _remove_browser_tree_owned(
                    stage_parent_fd,
                    stage_root.name,
                    stage_identity,
                )
            except (BrowserStageError, OSError):
                cleanup_failed = True
            if stage_parent_fd is not None:
                descriptor = stage_parent_fd
                stage_parent_fd = None
                try:
                    os.close(descriptor)
                except OSError:
                    cleanup_failed = True
            try:
                stage_parent.rmdir()
            except OSError:
                pass
        if stage_parent_fd is not None:
            descriptor = stage_parent_fd
            stage_parent_fd = None
            try:
                os.close(descriptor)
            except OSError:
                cleanup_failed = True
        if source_fd is not None:
            descriptor = source_fd
            source_fd = None
            try:
                os.close(descriptor)
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            raise BrowserStageError("browser stage cleanup failed")


@dataclass(frozen=True)
class _ProviderFreeTerminalManifest:
    """One parsed and closed terminal manifest shared by all validators."""

    workload_status: int
    final_status: int
    by_path: dict[str, dict[str, Any]]


PROVIDER_FREE_NAMESPACES = (
    ("user", "--unshare-user"),
    ("network", "--unshare-net"),
    ("pid", "--unshare-pid"),
    ("ipc", "--unshare-ipc"),
    ("uts", "--unshare-uts"),
)
PROVIDER_FREE_SETUP_CAPABILITIES = (
    "CAP_SYS_ADMIN",
    "CAP_SYS_CHROOT",
    "CAP_NET_ADMIN",
    "CAP_SETUID",
    "CAP_SETGID",
    "CAP_SETFCAP",
)
PROVIDER_FREE_EXECUTION_PROFILE = {
    "schema": "cvm.provider-free-execution-profile/1",
    "id": "issue15.provider-free-bounded/17",
    "provider_access": "forbidden",
    "sandbox_profile": "cvm.provider-free-linux-sandbox/17",
}
PROVIDER_FREE_SANDBOX_PROFILE = {
    "schema": "cvm.provider-free-linux-sandbox/17",
    "namespaces": [name for name, _flag in PROVIDER_FREE_NAMESPACES],
    "capabilities": {
        "baseline": "drop-all",
        "retained": list(PROVIDER_FREE_SETUP_CAPABILITIES),
        "scope": "outer-user-namespace",
        "purpose": "nested-bwrap-setup",
    },
    "die_with_parent": True,
    "new_session": True,
    "temporary_filesystem": "/tmp",
    "private_browser_image_filesystem": {
        "root": PROVIDER_FREE_MESHSHOT_EXECUTABLE_ROOT,
        "mount": "repository-owned-exec-permitted-tmpfs",
        "scope": "single-preview-runtime",
        "cleanup": "python-runtime-terminal-all-exit-classes",
    },
    "browser_supervisor": {
        "schema": "meshshot.browser-supervisor/1",
        "runtime_mode": "provider-free-supervised-cdp/1",
        "outer_root": "/meshshot-supervisor",
        "nested_root": "/run/meshshot-supervisor",
        "socket": "authority.sock",
        "transport": "AF_UNIX-SOCK_SEQPACKET-one-shot",
        "nested_mount": "read-only-exact",
        "outer_root_visibility": "nested-hidden-after-read-only-bind",
        "peer_identity": "SO_PEERCRED-exact-supervisor-and-client-pid",
        "attempt_nonce": "fresh-256-bit-every-packet",
        "listener_cleanup": "close-and-inode-bound-unlink-after-authenticated-accept",
        "failure_projection": "private-closed-result-only",
        "ambient_file_descriptors": "forbidden",
        "launch_owner": "outer-trusted-supervisor",
    },
    "repository_mount": "read-only",
    "output_mount": "read-write-exact-experiment",
    "browser_cache_mount": "read-only-job-scoped-attested-revision",
    "browser_runtime_staging": {
        "source": "deployment-attested-host-revision",
        "source_filesystem": "same-device-as-deployment-browser",
        "scope": "single-attested-revision",
        "destination": PROVIDER_FREE_STAGED_BROWSER_CACHE,
        "staged_revision": "attested",
        "staged_executable": PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
        "destination_filesystem": "read-only-bind-of-exec-permitted-host-stage",
        "tree_validation": "regular-files-only-no-links-or-special",
        "tree_manifest": {
            "schema": "meshshot.browser-tree-manifest/1",
            "coverage": "complete-attested-revision-tree",
            "binding": "canonical-sha256",
        },
        "execution_authority": {
            "schema": "meshshot.browser-execution-authority/1",
            "mode": "linux-detached-readonly-revision-mount/1",
            "private_filesystem": "job-private-exec-tmpfs",
            "handoff": "authenticated-seqpacket-mounted-detached-exec",
            "writable_source_after_handoff": "absent",
            "running_image_proof": "exact-proc-image-fd-identity",
        },
        "executable_validation": {
            "sha256": "deployment-runtime-identity",
            "execute_bits": "required",
        },
        "exec_permission_validation": {
            "mechanism": "kernel-execve-repository-owned-immediate-exit-probe",
            "network": "none",
            "timeout_seconds": _EXEC_PROBE_TIMEOUT_SECONDS,
            "expected_stdout": "cvm.browser-stage-exec-probe/1",
        },
        "sandbox_exec_diagnostics": {
            "schema": PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_SCHEMA,
            "receipt": PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH,
            "executable": PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
            "argv_suffix": ["--version"],
            "lifecycle": "non-rendering-immediate-exit",
            "environment_names": ["HOME", "LANG", "PATH"],
            "network": "none",
            "timeout_seconds": 5,
            "node_probe": "retired-by-python-prelaunch",
            "result": {
                "exit_code": 0,
                "stdout": "single-chromium-version-line",
                "stdout_max_bytes": 128,
                "stderr": "empty",
            },
            "seams": [
                "outer-python-direct",
                "outer-supervised-python-prelaunch",
                "fixed-unix-authority",
                "nested-playwright-loopback-cdp-attach",
            ],
            "published": "closed-outcomes-only-no-raw-output",
            "cleanup": "no-profile-or-persistent-process-artifacts",
        },
        "nested_mount": "read-only-exact-staged-cache",
        "launch_handoff": {
            "environment": "MESHSHOT_BROWSER_EXECUTABLE",
            "value": PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
            "validation": "absolute-regular-non-symlink-executable",
            "launch_owner": "outer-trusted-browser-supervisor",
            "playwright_option": "connect_over_cdp-is-local",
        },
        "cleanup": "supervisor-context-terminal-all-exit-classes",
        "catchable_signal_cleanup": ["SIGINT", "SIGTERM"],
        "uncatchable_termination": "stale-stage-collision-fail-closed",
    },
    "preview_process": {
        "capabilities": "drop-all",
        "mount_namespace": "inherit-outer",
        "receipt": PROVIDER_FREE_PREVIEW_SANDBOX_PATH,
        "browser_identity_diagnostic": {
            "schema": PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
            "receipt": PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH,
            "operation": "preview_browser_identity",
            "substages": [
                "private_snapshot_launch_image_identity",
                "live_running_image_identity",
                "loopback_listener_address_ownership",
                "connected_cdp_browser_version_identity",
                "runtime_evidence_cross_binding",
            ],
            "private_snapshot_phases": sorted(
                PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES
            ),
            "playwright_package_revision_checks": sorted(
                PROVIDER_FREE_PLAYWRIGHT_PACKAGE_REVISION_CHECKS
            ),
            "private_version_execution_checks": sorted(
                PROVIDER_FREE_PRIVATE_VERSION_EXECUTION_CHECKS
            ),
            "binding": [
                PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                "artifact_manifest.json",
            ],
            "published": "first-failing-closed-substage-only",
        },
        "public_wrapper": {
            "schema": PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA,
            "receipt": PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH,
            "operations": sorted(PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_OPERATIONS),
            "published": "closed-operation-only-no-process-data",
            "publication_failure": {
                "operation": (
                    PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_EVIDENCE_PUBLICATION_OPERATION
                ),
                "scenario_failure": PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                "terminal_manifest": "artifact_manifest.json",
                "wrapper_receipt": "absent",
            },
        },
    },
    "untrusted_canonical_worker": {
        "profile": "cad.canonical-build-worker/2",
        "address_space": {
            "platform": "linux",
            "soft_bytes": 16 * 1024**3,
            "hard_bytes": 16 * 1024**3,
        },
    },
    "resource_limits": {
        "wall_seconds": 1800,
        "cpu_seconds": 1800,
        "address_space_bytes": 128 * 1024**3,
        "file_size_bytes": 4 * 1024**3,
        "open_files": 512,
        "processes": 256,
    },
    "cleanup": {
        "timeout_exit_code": 124,
        "terminal_manifest_rejects_links_and_special_files": True,
        "failed_output_retained": True,
    },
}
PROVIDER_FREE_SANDBOX_REPO_ROOT = Path("/workspace/repo")
PROVIDER_FREE_REQUIRED_ENVIRONMENT = {
    "HOME": "/home/provider-free",
    "PATH": "/workspace/repo/.venv/bin:/usr/local/bin:/usr/bin:/bin",
    "PLAYWRIGHT_BROWSERS_PATH": PROVIDER_FREE_STAGED_BROWSER_CACHE,
    "MESHSHOT_EXECUTABLE_ROOT": PROVIDER_FREE_MESHSHOT_EXECUTABLE_ROOT,
    "MESHSHOT_BROWSER_RUNTIME_MODE": "provider-free-supervised-cdp/1",
    "PYTHONDONTWRITEBYTECODE": "1",
}
PROVIDER_FREE_SYSTEM_RO_PATHS = tuple(
    Path(value)
    for value in (
        "/usr",
        "/etc/alternatives",
        "/etc/ca-certificates",
        "/etc/crypto-policies",
        "/etc/fonts",
        "/etc/group",
        "/etc/hosts",
        "/etc/ld.so.cache",
        "/etc/ld.so.conf",
        "/etc/ld.so.conf.d",
        "/etc/localtime",
        "/etc/nsswitch.conf",
        "/etc/os-release",
        "/etc/passwd",
        "/etc/pki",
        "/etc/resolv.conf",
        "/etc/ssl",
        "/sys",
    )
)
PROVIDER_FREE_SCENARIOS = {
    "issue15-runtime-authority": ProviderFreeScenario(
        name="issue15-runtime-authority",
        identity="issue15.provider-free.runtime-authority/1",
    ),
}


def provider_free_sandbox_argv(
    scenario_name: str,
    exp_dir: Path,
    runtime_identity: dict[str, Any],
    *,
    repo_root: Path | None = None,
    browser_mount: AttestedBrowserMount | None = None,
) -> list[str]:
    """Build the one exact versioned provider-free bubblewrap launch contract."""

    requested_root = REPO_ROOT if repo_root is None else repo_root
    source_root = Path(requested_root).resolve(strict=True)
    try:
        exp_dir = provider_free_output.revalidate_exp_path(source_root, exp_dir)
    except provider_free_output.OutputPathError as exc:
        raise ProtocolError(f"unsafe provider-free output path: {exc}") from exc
    relative_exp = exp_dir.relative_to(source_root)
    if len(relative_exp.parts) != 3 or relative_exp.parts[0] != "outputs":
        raise ProtocolError("provider-free output is not experiment-bound")
    handle = f"{relative_exp.parts[1]}/{relative_exp.parts[2]}"
    expected_mount = _browser_mount(runtime_identity["chromium"], handle)
    mount = expected_mount if browser_mount is None else browser_mount
    if (
        mount.host_revision != expected_mount.host_revision
        or mount.sandbox_cache != expected_mount.sandbox_cache
        or (
            browser_mount is not None
            and (
                re.fullmatch(r"[0-9a-f]{64}", mount.tree_manifest_sha256)
                is None
                or mount.revision
                != str(runtime_identity["chromium"]["revision"])
                or mount.executable_relative
                != "chrome-headless-shell-linux64/chrome-headless-shell"
                or mount.executable_sha256
                != runtime_identity["chromium"]["sha256"]
                or mount.tree_manifest_sha256
                != runtime_identity["chromium"]["tree_manifest_sha256"]
            )
        )
    ):
        raise ProtocolError("provider-free browser mount conflicts with job authority")
    if browser_mount is not None:
        try:
            actual_manifest_sha256 = _browser_tree_manifest_sha256(
                _browser_tree_manifest(mount.host_revision)
            )
        except (BrowserStageError, OSError) as exc:
            raise ProtocolError(
                "provider-free browser mount identity is unavailable"
            ) from exc
        if actual_manifest_sha256 != mount.tree_manifest_sha256:
            raise ProtocolError(
                "provider-free browser mount conflicts with tree authority"
            )
    sandbox_exp = PROVIDER_FREE_SANDBOX_REPO_ROOT / relative_exp
    bwrap = runtime_identity["bwrap"]["path"]
    chromium = runtime_identity["chromium"]
    argv = [
        bwrap,
        *(flag for _name, flag in PROVIDER_FREE_NAMESPACES),
        "--cap-drop",
        "ALL",
    ]
    for capability in PROVIDER_FREE_SETUP_CAPABILITIES:
        argv.extend(("--cap-add", capability))
    argv.extend((
        "--die-with-parent",
        "--new-session",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        PROVIDER_FREE_MESHSHOT_EXECUTABLE_ROOT,
        "--tmpfs",
        "/meshshot-supervisor",
        "--dir",
        "/workspace",
        "--ro-bind",
        os.fspath(source_root),
        os.fspath(PROVIDER_FREE_SANDBOX_REPO_ROOT),
        "--bind",
        os.fspath(exp_dir),
        os.fspath(sandbox_exp),
        "--dir",
        "/home",
        "--dir",
        "/home/provider-free",
        "--dir",
        "/home/provider-free/.cache",
        "--dir",
        PROVIDER_FREE_STAGED_BROWSER_CACHE,
        "--ro-bind",
        os.fspath(mount.host_revision),
        f"{mount.sandbox_cache}/attested",
    ))
    if browser_mount is not None:
        argv.extend(
            (
                "--setenv",
                "MESHSHOT_BROWSER_TREE_MANIFEST_SHA256",
                mount.tree_manifest_sha256,
            )
        )
    for source, target in (
        ("usr/bin", "/bin"),
        ("usr/sbin", "/sbin"),
        ("usr/lib", "/lib"),
        ("usr/lib64", "/lib64"),
    ):
        argv.extend(("--symlink", source, target))
    for path in PROVIDER_FREE_SYSTEM_RO_PATHS:
        if path.exists():
            argv.extend(("--ro-bind", os.fspath(path), os.fspath(path)))
    argv.extend(
        (
            "--chdir",
            os.fspath(PROVIDER_FREE_SANDBOX_REPO_ROOT),
            "--",
            os.fspath(PROVIDER_FREE_SANDBOX_REPO_ROOT / ".venv/bin/python"),
            "-m",
            "scripts.pilot.provider_free_scenarios",
            "run",
            scenario_name,
            "--workspace",
            os.fspath(sandbox_exp),
        )
    )
    return argv


PROVIDER_FREE_ENV_ALLOWLIST = (
    "HOME",
    "LANG",
    "PATH",
    "PYTHONDONTWRITEBYTECODE",
    "TZ",
)
PROVIDER_FREE_SUPERVISOR_LOCALE = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
PROVIDER_FREE_PROOF = "run/provider-free-execution.json"


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


def _allocate_provider_free_exp(object_name: str, group: str, root: Path) -> str:
    validate_component(object_name, "object")
    _validate_pilot_group(group)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{object_name}"
    exp = base
    suffix = 1
    while True:
        try:
            _, output_exists = provider_free_output.physical_exp_path(
                REPO_ROOT,
                group,
                exp,
                create_exp=False,
            )
        except provider_free_output.OutputPathError as exc:
            raise ProtocolError(f"unsafe provider-free output path: {exc}") from exc
        if not state_path(root, f"{group}/{exp}").exists() and not output_exists:
            return exp
        suffix += 1
        exp = f"{base}-{suffix}"


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
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        stream = os.fdopen(descriptor, "wb", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
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


def submit_provider_free(
    scenario_name: str,
    group: str,
    *,
    state_root: Path | None = None,
    detach: Callable[[str, Sequence[str], Path], int] = _detach,
) -> dict[str, Any]:
    """Submit one closed-registry provider-free scenario."""

    root = _root(state_root)
    scenario = PROVIDER_FREE_SCENARIOS.get(scenario_name)
    if scenario is None:
        raise ProtocolError(f"unknown provider-free scenario: {scenario_name!r}")
    group = _validate_pilot_group(group)
    receipt_path = REPO_ROOT / deployment_authority.RECEIPT_PATH
    try:
        receipt_bytes = receipt_path.read_bytes()
        deployment_receipt = _loads_json_strict(receipt_bytes)
        deployment_authority.verify_receipt(REPO_ROOT, deployment_receipt)
        deployment_authority.validate_runtime_identity(
            REPO_ROOT,
            deployment_receipt.get("runtime_identity"),
            verify_external=False,
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        deployment_authority.DeploymentAuthorityError,
    ) as exc:
        raise ProtocolError("deployed source authority is missing or invalid") from exc
    if deployment_receipt.get("contract_paths") != list(
        deployment_authority.EXECUTION_AUTHORITY_PATHS
    ):
        raise ProtocolError("deployed source authority contract is incomplete")
    with _allocation_lock(root, group):
        exp = _allocate_provider_free_exp(scenario.name, group, root)
        record = _pilot_record(scenario.name, group, exp, root)
        record.update(
            {
                "job_kind": "provider-free",
                "scenario": {
                    "name": scenario.name,
                    "identity": scenario.identity,
                },
                "execution_profile": dict(PROVIDER_FREE_EXECUTION_PROFILE),
                "request_authority": {
                    "schema": "cvm.provider-free-request-authority/1",
                    "deployment_receipt": deployment_authority.RECEIPT_PATH,
                    "deployment_receipt_sha256": hashlib.sha256(
                        receipt_bytes
                    ).hexdigest(),
                    "deployment_receipt_canonical_sha256": hashlib.sha256(
                        json.dumps(
                            deployment_receipt,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest(),
                    "deployment_source_head": deployment_receipt["source_head"],
                    "deployment_tree_sha256": deployment_receipt["tree_sha256"],
                    "runtime_identity": deployment_receipt["runtime_identity"],
                },
            }
        )
        record["request_authority_sha256"] = request_authority_sha256(record)
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
    return {
        "job": state["job"],
        "state": state["state"],
        "kind": "provider-free",
    }


def _provider_free_environment(
    environ: dict[str, str],
    *,
    profile_id: str,
    handle: str,
    request_authority_sha: str,
    deployment_tree_sha: str,
    immutable_request: dict[str, Any],
) -> dict[str, str]:
    """Build the closed runner environment without credential values."""

    child = {
        name: environ[name]
        for name in PROVIDER_FREE_ENV_ALLOWLIST
        if environ.get(name)
    }
    child.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    child.update(PROVIDER_FREE_SUPERVISOR_LOCALE)
    removed = sorted(set(environ).difference(PROVIDER_FREE_ENV_ALLOWLIST))
    child.update(
        {
            "CVM_PROVIDER_FREE_PROFILE": profile_id,
            "CVM_PROVIDER_FREE_STRIPPED_NAMES": ",".join(removed),
            "CVM_PROVIDER_FREE_JOB": handle,
            "CVM_PROVIDER_FREE_REQUEST_AUTHORITY_SHA256": request_authority_sha,
            "CVM_PROVIDER_FREE_DEPLOYMENT_TREE_SHA256": deployment_tree_sha,
            "CVM_PROVIDER_FREE_REQUEST_JSON": json.dumps(
                immutable_request, sort_keys=True, separators=(",", ":")
            ),
        }
    )
    return child


def _provider_free_common_evidence_result(
    exp_dir: Path,
    *,
    handle: str,
    record: dict[str, Any],
    expected_stripped: list[str],
    manifest: _ProviderFreeTerminalManifest,
) -> tuple[str | None, str | None]:
    """Validate evidence shared by successful and failed workload terminals."""

    proof_path = exp_dir / PROVIDER_FREE_PROOF
    try:
        proof_bytes = proof_path.read_bytes()
        proof = _loads_json_strict(proof_bytes)
    except FileNotFoundError:
        return None, "provider-free execution evidence missing"
    except (OSError, ValueError, json.JSONDecodeError):
        return None, "provider-free execution evidence invalid"
    try:
        sandbox_bytes = (exp_dir / "run/sandbox-enforcement.json").read_bytes()
    except OSError:
        return None, (
            "provider-free terminal evidence missing: "
            "run/sandbox-enforcement.json"
        )
    expected_proof = {
        "schema": "cvm.provider-free-execution/1",
        "job": handle,
        "scenario": record["scenario"],
        "execution_profile": record["execution_profile"],
        "request_authority": {
            "sha256": record["request_authority_sha256"],
            "deployment_tree_sha256": record["request_authority"][
                "deployment_tree_sha256"
            ],
            "immutable_request": request_authority_payload(record),
        },
        "sandbox": {
            "network": "isolated-loopback",
            "resource_profile": record["execution_profile"]["id"],
        },
        "provider_environment": {
            "allowlist": list(PROVIDER_FREE_ENV_ALLOWLIST),
            "stripped": expected_stripped,
            "credential_values_recorded": False,
        },
        "requests": {"model_gateway": 0, "provider": 0, "tap": 0},
        "sandbox_enforcement": {
            "path": "run/sandbox-enforcement.json",
            "sha256": hashlib.sha256(sandbox_bytes).hexdigest(),
        },
    }
    if proof != expected_proof:
        return None, (
            "provider-free execution evidence does not match job authority"
        )
    by_path = manifest.by_path
    try:
        retained_receipt_bytes = (
            exp_dir / "run/deployed-source-authority.json"
        ).read_bytes()
        retained_receipt = _loads_json_strict(retained_receipt_bytes)
        deployment_authority.verify_materialized(
            exp_dir / "run/deployed-source",
            retained_receipt,
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        deployment_authority.DeploymentAuthorityError,
    ):
        return None, (
            "provider-free terminal evidence has invalid retained deployed "
            "source authority"
        )
    if (
        retained_receipt.get("source_head")
        != record["request_authority"]["deployment_source_head"]
        or retained_receipt.get("tree_sha256")
        != record["request_authority"]["deployment_tree_sha256"]
        or retained_receipt.get("contract_paths")
        != list(deployment_authority.EXECUTION_AUTHORITY_PATHS)
        or hashlib.sha256(retained_receipt_bytes).hexdigest()
        != record["request_authority"]["deployment_receipt_sha256"]
        or hashlib.sha256(
            json.dumps(
                retained_receipt, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        != record["request_authority"]["deployment_receipt_canonical_sha256"]
        or retained_receipt.get("runtime_identity")
        != record["request_authority"]["runtime_identity"]
    ):
        return None, (
            "provider-free retained deployment authority conflicts with job"
        )
    try:
        sandbox = _loads_json_strict(
            (exp_dir / "run/sandbox-enforcement.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return None, "provider-free sandbox enforcement evidence is invalid"
    argv = sandbox.get("argv") if isinstance(sandbox, dict) else None
    environment_names = (
        sandbox.get("environment_names") if isinstance(sandbox, dict) else None
    )
    tree_manifest_values = (
        [
            argv[index + 2]
            for index in range(len(argv) - 2)
            if argv[index : index + 2]
            == ["--setenv", "MESHSHOT_BROWSER_TREE_MANIFEST_SHA256"]
        ]
        if isinstance(argv, list)
        else []
    )
    tree_manifest_sha256 = (
        tree_manifest_values[0] if len(tree_manifest_values) == 1 else None
    )
    runtime_identity = record["request_authority"]["runtime_identity"]
    expected_argv = provider_free_sandbox_argv(
        record["scenario"]["name"],
        exp_dir,
        runtime_identity,
    )
    if (
        re.fullmatch(r"[0-9a-f]{64}", tree_manifest_sha256 or "") is None
        or tree_manifest_sha256
        != runtime_identity["chromium"]["tree_manifest_sha256"]
    ):
        expected_argv = []
    else:
        staged_target = f"{PROVIDER_FREE_STAGED_BROWSER_CACHE}/attested"
        try:
            staged_index = next(
                index
                for index in range(len(expected_argv) - 2)
                if expected_argv[index] == "--ro-bind"
                and expected_argv[index + 2] == staged_target
            )
        except StopIteration:
            expected_argv = []
        else:
            expected_argv[staged_index + 3 : staged_index + 3] = [
                "--setenv",
                "MESHSHOT_BROWSER_TREE_MANIFEST_SHA256",
                tree_manifest_sha256,
            ]
    if (
        not isinstance(sandbox, dict)
        or sandbox.get("schema") != "cvm.provider-free-sandbox-enforcement/1"
        or set(sandbox)
        != {
            "schema",
            "network",
            "argv",
            "environment_names",
            "required_environment",
            "sandbox_profile",
            "runtime_identity",
        }
        or sandbox.get("network") != "isolated-loopback"
        or sandbox.get("sandbox_profile") != PROVIDER_FREE_SANDBOX_PROFILE
        or sandbox.get("runtime_identity")
        != record["request_authority"]["runtime_identity"]
        or argv != expected_argv
        or not isinstance(environment_names, list)
        or not set(("HOME", "PATH", "PYTHONDONTWRITEBYTECODE")).issubset(
            environment_names
        )
        or not set(environment_names).issubset(
            {
                *PROVIDER_FREE_ENV_ALLOWLIST,
                "PLAYWRIGHT_BROWSERS_PATH",
                "MESHSHOT_EXECUTABLE_ROOT",
                "MESHSHOT_BROWSER_RUNTIME_MODE",
            }
        )
        or not {
            "PLAYWRIGHT_BROWSERS_PATH",
            "MESHSHOT_EXECUTABLE_ROOT",
            "MESHSHOT_BROWSER_RUNTIME_MODE",
        }.issubset(environment_names)
        or sandbox.get("required_environment")
        != PROVIDER_FREE_REQUIRED_ENVIRONMENT
    ):
        return None, "provider-free sandbox enforcement evidence is incomplete"
    for relative in (
        PROVIDER_FREE_PROOF,
        "run/deployed-source-authority.json",
        "run/sandbox-enforcement.json",
    ):
        path = exp_dir / relative
        try:
            data = path.read_bytes()
        except OSError:
            return None, (
                f"provider-free terminal evidence missing: {relative}"
            )
        expected_entry = {
            "path": relative,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if by_path.get(relative) != expected_entry:
            return None, (
                f"provider-free terminal evidence is not bound: {relative}"
            )
    for item in retained_receipt["files"]:
        relative = f"run/deployed-source/{item['path']}"
        if by_path.get(relative) != {**item, "path": relative}:
            return None, (
                f"provider-free terminal evidence is not bound: {relative}"
            )
    return _relative(proof_path), None


def _provider_free_evidence_result(
    exp_dir: Path,
    *,
    handle: str,
    record: dict[str, Any],
    expected_stripped: list[str],
    manifest: _ProviderFreeTerminalManifest,
) -> tuple[str | None, str | None]:
    """Validate complete successful execution evidence and manifest bindings."""

    proof_path, error = _provider_free_common_evidence_result(
        exp_dir,
        handle=handle,
        record=record,
        expected_stripped=expected_stripped,
        manifest=manifest,
    )
    if error is not None:
        return None, error
    by_path = manifest.by_path
    try:
        preview_sandbox_bytes = (
            exp_dir / PROVIDER_FREE_PREVIEW_SANDBOX_PATH
        ).read_bytes()
        preview_sandbox = _loads_json_strict(preview_sandbox_bytes)
    except (OSError, ValueError, json.JSONDecodeError):
        return None, "provider-free preview sandbox evidence is invalid"
    if not provider_free_preview_sandbox_receipt_allowed(
        preview_sandbox,
        exp_dir.parent.name,
        exp_dir.name,
    ):
        return None, "provider-free preview sandbox evidence conflicts"
    preview_entry = {
        "path": PROVIDER_FREE_PREVIEW_SANDBOX_PATH,
        "size_bytes": len(preview_sandbox_bytes),
        "sha256": hashlib.sha256(preview_sandbox_bytes).hexdigest(),
    }
    if by_path.get(PROVIDER_FREE_PREVIEW_SANDBOX_PATH) != preview_entry:
        return None, "provider-free preview sandbox evidence is not bound"
    try:
        diagnostic_bytes = (
            exp_dir / PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH
        ).read_bytes()
        diagnostic = _loads_json_strict(diagnostic_bytes)
    except (OSError, ValueError, json.JSONDecodeError):
        return None, "provider-free browser exec diagnostic evidence is invalid"
    if (
        not provider_free_browser_exec_diagnostic_allowed(diagnostic)
        or diagnostic["prelaunched_cdp"] != "passed"
    ):
        return None, "provider-free browser exec diagnostic evidence conflicts"
    diagnostic_entry = {
        "path": PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH,
        "size_bytes": len(diagnostic_bytes),
        "sha256": hashlib.sha256(diagnostic_bytes).hexdigest(),
    }
    if by_path.get(PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH) != diagnostic_entry:
        return None, "provider-free browser exec diagnostic evidence is not bound"
    try:
        public_wrapper_bytes = (
            exp_dir / PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
        ).read_bytes()
        public_wrapper = _loads_json_strict(public_wrapper_bytes)
    except (OSError, ValueError, json.JSONDecodeError):
        return None, "provider-free preview public wrapper evidence is invalid"
    if (
        not provider_free_preview_public_wrapper_allowed(public_wrapper)
        or public_wrapper["operation"] != "passed"
    ):
        return None, "provider-free preview public wrapper evidence conflicts"
    public_wrapper_entry = {
        "path": PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH,
        "size_bytes": len(public_wrapper_bytes),
        "sha256": hashlib.sha256(public_wrapper_bytes).hexdigest(),
    }
    if by_path.get(PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH) != public_wrapper_entry:
        return None, "provider-free preview public wrapper evidence is not bound"
    for relative in (
        "run/runtime-authority-smoke.json",
        "workspace-authority.json",
        "workspace-authority.bundle",
        "workspace.json",
        "final/manifest.json",
    ):
        try:
            data = (exp_dir / relative).read_bytes()
        except OSError:
            return None, f"provider-free terminal evidence missing: {relative}"
        expected_entry = {
            "path": relative,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if by_path.get(relative) != expected_entry:
            return None, f"provider-free terminal evidence is not bound: {relative}"
    return proof_path, None


def _provider_free_failure_evidence_result(
    exp_dir: Path,
    *,
    handle: str,
    record: dict[str, Any],
    expected_stripped: list[str],
    manifest: _ProviderFreeTerminalManifest,
) -> tuple[
    str | None,
    dict[str, str] | None,
    dict[str, str] | None,
    str | None,
]:
    """Validate the failure-only evidence path without requiring success files."""

    by_path = manifest.by_path
    failure_path = exp_dir / PROVIDER_FREE_SCENARIO_FAILURE_PATH
    try:
        failure_bytes = failure_path.read_bytes()
        failure = _loads_json_strict(failure_bytes)
    except (OSError, ValueError, json.JSONDecodeError):
        return None, None, None, "provider-free scenario failure evidence is invalid"
    failure_keys = set(failure) if isinstance(failure, dict) else set()
    operation = failure.get("operation") if isinstance(failure, dict) else None
    browser_identity_substage = (
        failure.get("browser_identity_substage")
        if isinstance(failure, dict)
        else None
    )
    browser_identity_phase = (
        failure.get("browser_identity_phase")
        if isinstance(failure, dict)
        else None
    )
    browser_identity_check = (
        failure.get("browser_identity_check")
        if isinstance(failure, dict)
        else None
    )
    expected_failure_keys = {"schema", "scenario_identity", "stage"}
    if operation is not None:
        expected_failure_keys.add("operation")
    if operation == "preview_browser_identity":
        expected_failure_keys.add("browser_identity_substage")
        if browser_identity_substage == "private_snapshot_launch_image_identity":
            expected_failure_keys.add("browser_identity_phase")
        if provider_free_browser_identity_checks(browser_identity_phase):
            expected_failure_keys.add("browser_identity_check")
    if (
        not isinstance(failure, dict)
        or failure_keys != expected_failure_keys
        or failure.get("schema") != PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA
        or failure.get("scenario_identity") != record["scenario"]["identity"]
        or failure.get("stage") not in PROVIDER_FREE_SCENARIO_FAILURE_STAGES
        or (
            operation is not None
            and not provider_free_scenario_failure_operation_allowed(
                failure.get("stage"), operation
            )
        )
        or (
            (browser_identity_substage is not None)
            != (operation == "preview_browser_identity")
        )
        or (
            operation == "preview_browser_identity"
            and browser_identity_substage
            not in PROVIDER_FREE_BROWSER_IDENTITY_SUBSTAGES
        )
        or (
            browser_identity_substage
            == "private_snapshot_launch_image_identity"
            and browser_identity_phase
            not in PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES
        )
        or (
            browser_identity_substage
            != "private_snapshot_launch_image_identity"
            and browser_identity_phase is not None
        )
        or (
            provider_free_browser_identity_checks(browser_identity_phase)
            and browser_identity_check
            not in provider_free_browser_identity_checks(browser_identity_phase)
        )
        or (
            not provider_free_browser_identity_checks(browser_identity_phase)
            and browser_identity_check is not None
        )
    ):
        return None, None, None, "provider-free scenario failure identity conflicts"
    expected_entry = {
        "path": PROVIDER_FREE_SCENARIO_FAILURE_PATH,
        "size_bytes": len(failure_bytes),
        "sha256": hashlib.sha256(failure_bytes).hexdigest(),
    }
    if by_path.get(PROVIDER_FREE_SCENARIO_FAILURE_PATH) != expected_entry:
        return None, None, None, "provider-free scenario failure evidence is not bound"
    public_identity_diagnostic: dict[str, str] | None = None
    identity_diagnostic_path = (
        exp_dir / PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH
    )
    if operation == "preview_browser_identity":
        try:
            identity_diagnostic_bytes = identity_diagnostic_path.read_bytes()
            identity_diagnostic = _loads_json_strict(identity_diagnostic_bytes)
        except (OSError, ValueError, json.JSONDecodeError):
            return (
                None,
                None,
                None,
                "provider-free browser identity diagnostic evidence is invalid",
            )
        if not provider_free_browser_identity_diagnostic_allowed(
            identity_diagnostic,
            expected_failure_sha256=hashlib.sha256(failure_bytes).hexdigest(),
            expected_substage=browser_identity_substage,
            expected_phase=browser_identity_phase,
            expected_check=browser_identity_check,
        ):
            return (
                None,
                None,
                None,
                "provider-free browser identity diagnostic evidence conflicts",
            )
        identity_entry = {
            "path": PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH,
            "size_bytes": len(identity_diagnostic_bytes),
            "sha256": hashlib.sha256(identity_diagnostic_bytes).hexdigest(),
        }
        if by_path.get(PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH) != identity_entry:
            return (
                None,
                None,
                None,
                "provider-free browser identity diagnostic evidence is not bound",
            )
        public_identity_diagnostic = {
            "schema": PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
            "substage": browser_identity_substage,
        }
        if browser_identity_phase is not None:
            public_identity_diagnostic["phase"] = browser_identity_phase
        if browser_identity_check is not None:
            public_identity_diagnostic["check"] = browser_identity_check
    elif (
        os.path.lexists(identity_diagnostic_path)
        or PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH in by_path
    ):
        return (
            None,
            None,
            None,
            "provider-free browser identity diagnostic evidence is inconsistent",
        )
    diagnostic_operations = {
        "preview_browser_outer_exec_probe",
        "preview_browser_nested_exec_probe",
    }
    if operation in diagnostic_operations:
        diagnostic_path = exp_dir / PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH
        try:
            diagnostic_bytes = diagnostic_path.read_bytes()
            diagnostic = _loads_json_strict(diagnostic_bytes)
        except (OSError, ValueError, json.JSONDecodeError):
            return (
                None,
                None,
                None,
                "provider-free browser exec diagnostic evidence is invalid",
            )
        if not provider_free_browser_exec_diagnostic_matches_operation(
            diagnostic,
            operation,
        ):
            return (
                None,
                None,
                None,
                "provider-free browser exec diagnostic evidence conflicts",
            )
        diagnostic_entry = {
            "path": PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH,
            "size_bytes": len(diagnostic_bytes),
            "sha256": hashlib.sha256(diagnostic_bytes).hexdigest(),
        }
        if by_path.get(PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH) != diagnostic_entry:
            return (
                None,
                None,
                None,
                "provider-free browser exec diagnostic evidence is not bound",
            )
    if operation in PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_OPERATIONS:
        public_wrapper_path = exp_dir / PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
        try:
            public_wrapper_bytes = public_wrapper_path.read_bytes()
            public_wrapper = _loads_json_strict(public_wrapper_bytes)
        except (OSError, ValueError, json.JSONDecodeError):
            return (
                None,
                None,
                None,
                "provider-free preview public wrapper evidence is invalid",
            )
        if not provider_free_preview_public_wrapper_matches_operation(
            public_wrapper,
            operation,
        ):
            return (
                None,
                None,
                None,
                "provider-free preview public wrapper evidence conflicts",
            )
        public_wrapper_entry = {
            "path": PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH,
            "size_bytes": len(public_wrapper_bytes),
            "sha256": hashlib.sha256(public_wrapper_bytes).hexdigest(),
        }
        if by_path.get(PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH) != public_wrapper_entry:
            return (
                None,
                None,
                None,
                "provider-free preview public wrapper evidence is not bound",
            )
    elif operation == (
        PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_EVIDENCE_PUBLICATION_OPERATION
    ) and (
        os.path.lexists(exp_dir / PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH)
        or PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH in by_path
    ):
        return (
            None,
            None,
            None,
            "provider-free preview public wrapper must be absent for publication failure",
        )
    proof_path, error = _provider_free_common_evidence_result(
        exp_dir,
        handle=handle,
        record=record,
        expected_stripped=expected_stripped,
        manifest=manifest,
    )
    if error is not None:
        return None, None, None, error
    return proof_path, failure, public_identity_diagnostic, None


def supervise_provider_free(
    handle: str,
    *,
    state_root: Path | None = None,
    interval: float = DEFAULT_HEARTBEAT_INTERVAL,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one immutable provider-free scenario through its fixed runner."""

    root = _root(state_root)
    parsed = parse_handle(handle)
    with _supervisor_lock(root, handle):
        record = load_state(root, handle)
        if record.get("job_kind") != "provider-free":
            raise ProtocolError("supervise-provider-free requires a provider-free job")
        if record["state"] != "submitted":
            raise ProtocolError(
                f"provider-free job cannot start from {record['state']}"
            )
        scenario = PROVIDER_FREE_SCENARIOS.get(record.get("scenario", {}).get("name"))
        if scenario is None or record["scenario"].get("identity") != scenario.identity:
            raise ProtocolError("provider-free job scenario is not in the closed registry")
        if record.get("execution_profile") != PROVIDER_FREE_EXECUTION_PROFILE:
            raise ProtocolError("provider-free execution profile does not match registry")
        receipt_path = REPO_ROOT / deployment_authority.RECEIPT_PATH
        try:
            receipt_bytes = receipt_path.read_bytes()
            live_receipt = _loads_json_strict(receipt_bytes)
            deployment_authority.verify_receipt(REPO_ROOT, live_receipt)
            deployment_authority.validate_runtime_identity(
                REPO_ROOT,
                live_receipt.get("runtime_identity"),
                verify_external=False,
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            deployment_authority.DeploymentAuthorityError,
        ) as exc:
            raise ProtocolError("deployed source authority changed before execution") from exc
        if (
            hashlib.sha256(receipt_bytes).hexdigest()
            != record["request_authority"]["deployment_receipt_sha256"]
            or live_receipt.get("source_head")
            != record["request_authority"]["deployment_source_head"]
            or live_receipt.get("tree_sha256")
            != record["request_authority"]["deployment_tree_sha256"]
            or live_receipt.get("runtime_identity")
            != record["request_authority"]["runtime_identity"]
        ):
            raise ProtocolError("deployed source identity changed before execution")
        transition(root, handle, "running", supervisor_pid=os.getpid())
        source_environment = dict(os.environ if environ is None else environ)
        child_environment = _provider_free_environment(
            source_environment,
            profile_id=PROVIDER_FREE_EXECUTION_PROFILE["id"],
            handle=handle,
            request_authority_sha=record["request_authority_sha256"],
            deployment_tree_sha=record["request_authority"][
                "deployment_tree_sha256"
            ],
            immutable_request=request_authority_payload(record),
        )
        stripped = child_environment["CVM_PROVIDER_FREE_STRIPPED_NAMES"].split(",")
        if stripped == [""]:
            stripped = []
        command = [
            sys.executable,
            "-m",
            PROVIDER_FREE_RUNNER_MODULE,
            "run",
            scenario.name,
            record["group"],
            parsed["exp"],
        ]
        process_status: int | None = None
        try:
            _exp_dir, output_exists = provider_free_output.physical_exp_path(
                REPO_ROOT,
                record["group"],
                parsed["exp"],
                create_exp=False,
            )
            if output_exists:
                raise provider_free_output.OutputPathError(
                    f"provider-free experiment must be new: {_exp_dir}"
                )
            process_status, _pid = _run_with_heartbeat(
                root,
                handle,
                command,
                interval=interval,
                env=child_environment,
            )
            exp_dir, _ = provider_free_output.physical_exp_path(
                REPO_ROOT,
                record["group"],
                parsed["exp"],
                create_exp=False,
            )
            manifest_path = exp_dir / "artifact_manifest.json"
            if not manifest_path.is_file():
                diagnostic = _provider_free_bootstrap_diagnostic(
                    root,
                    handle,
                    process_status=process_status,
                    output_exists=exp_dir.is_dir(),
                )
                return transition(
                    root,
                    handle,
                    "failed",
                    runner_final_status=None,
                    artifact_manifest=None,
                    process_exit_code=process_status,
                    no_provider_evidence=None,
                    bootstrap_diagnostic=diagnostic,
                    failure_reason="provider-free runner produced no artifact manifest",
                )
            terminal_manifest, manifest_error = _provider_free_manifest_result(
                manifest_path
            )
            runner_status = (
                terminal_manifest.final_status
                if terminal_manifest is not None
                else None
            )
            updates = {
                "runner_final_status": runner_status,
                "artifact_manifest": (
                    _relative(manifest_path) if manifest_path.is_file() else None
                ),
                "process_exit_code": process_status,
            }
            if manifest_error is not None:
                return transition(
                    root,
                    handle,
                    "failed",
                    failure_reason=manifest_error,
                    no_provider_evidence=None,
                    **updates,
                )
            if terminal_manifest is None:
                raise AssertionError("validated terminal manifest is missing")
            workload_status = terminal_manifest.workload_status
            if workload_status != 0:
                (
                    proof_path,
                    scenario_failure,
                    browser_identity_diagnostic,
                    failure_error,
                ) = (
                    _provider_free_failure_evidence_result(
                        exp_dir,
                        handle=handle,
                        record=record,
                        expected_stripped=stripped,
                        manifest=terminal_manifest,
                    )
                )
                updates["no_provider_evidence"] = proof_path
                if failure_error is not None:
                    return transition(
                        root,
                        handle,
                        "failed",
                        failure_reason=failure_error,
                        **updates,
                    )
                updates["scenario_failure"] = scenario_failure
                if browser_identity_diagnostic is not None:
                    updates["browser_identity_diagnostic"] = (
                        browser_identity_diagnostic
                    )
                if runner_status != workload_status:
                    reason = "provider-free scenario final status conflicts"
                elif process_status != runner_status:
                    reason = "provider-free runner exit status conflicts"
                else:
                    reason = (
                        "provider-free scenario failed at "
                        f"{scenario_failure['stage']}"
                    )
                return transition(
                    root,
                    handle,
                    "failed",
                    failure_reason=reason,
                    **updates,
                )
            proof_path, proof_error = _provider_free_evidence_result(
                exp_dir,
                handle=handle,
                record=record,
                expected_stripped=stripped,
                manifest=terminal_manifest,
            )
            updates["no_provider_evidence"] = proof_path
            if (
                process_status == 0
                and runner_status == 0
                and proof_error is None
            ):
                return transition(root, handle, "succeeded", **updates)
            reason = proof_error or (
                f"provider-free runner exited {process_status}"
                if process_status
                else f"runner final_status={runner_status}"
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
                    failure_reason=(
                        "provider-free supervisor error: "
                        f"{type(error).__name__}"
                    ),
                )
            return load_state(root, handle)


def _provider_free_bootstrap_diagnostic(
    root: Path,
    handle: str,
    *,
    process_status: int,
    output_exists: bool,
) -> dict[str, object]:
    """Classify a bounded log tail without publishing attacker-controlled text."""

    if process_status == 0:
        classification = "runner-completed-without-artifact-manifest"
    elif process_status < 0:
        classification = "runner-terminated-before-artifact-manifest"
    elif output_exists:
        classification = "runner-exited-before-artifact-manifest"
    else:
        classification = "runner-exited-before-artifact-manifest"
        diagnostic_tail_truncated = False
        try:
            with log_path(root, handle).open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                tail_start = max(0, size - PROVIDER_FREE_BOOTSTRAP_LOG_BYTES)
                diagnostic_tail_truncated = tail_start > 0
                stream.seek(tail_start)
                diagnostic_tail = stream.read(PROVIDER_FREE_BOOTSTRAP_LOG_BYTES)
        except OSError:
            diagnostic_tail = b""
        runner_marker = diagnostic_tail.find(_PROVIDER_FREE_RUNNER_ERROR_MARKER)
        if runner_marker >= 0:
            classification = "runner-contract-rejected"
            runner_error = diagnostic_tail[
                runner_marker + len(_PROVIDER_FREE_RUNNER_ERROR_MARKER) :
            ]
            if runner_error.startswith(b" "):
                runner_error = runner_error[1:]
            if not diagnostic_tail_truncated:
                classification = next(
                    (
                        closed_classification
                        for prefix, closed_classification in (
                            _PROVIDER_FREE_RUNNER_ERROR_CLASSIFICATIONS
                        )
                        if runner_error.startswith(prefix)
                    ),
                    classification,
                )
        elif any(
            marker in diagnostic_tail
            for marker in (
                b"ModuleNotFoundError",
                b"ImportError:",
                b"cannot import name",
            )
        ):
            classification = "python-import-failed"
        elif b"can't open file" in diagnostic_tail:
            classification = "runner-entrypoint-unavailable"
    return {
        "schema": "cvm.provider-free-bootstrap-diagnostic/1",
        "phase": (
            "before-artifact-manifest" if output_exists else "before-experiment"
        ),
        "classification": classification,
        "process_exit_code": process_status,
    }


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
        manifest = _loads_json_strict(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "artifact manifest missing"
    except (OSError, ValueError, json.JSONDecodeError):
        return None, "artifact manifest invalid"
    final_status = manifest.get("final_status")
    if isinstance(final_status, bool) or not isinstance(final_status, int):
        return None, "artifact manifest final_status is not an integer"
    return final_status, None


def _provider_free_manifest_result(
    path: Path,
) -> tuple[_ProviderFreeTerminalManifest | None, str | None]:
    """Parse and validate one closed provider-free terminal manifest once."""

    try:
        manifest = _loads_json_strict(path.read_bytes())
    except FileNotFoundError:
        return None, "artifact manifest missing"
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None, "artifact manifest invalid"
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {"schema_version", "workload_status", "final_status", "files"}
        or manifest.get("schema_version") != 1
    ):
        return None, "artifact manifest schema is unsupported"
    statuses: dict[str, int] = {}
    for field in ("workload_status", "final_status"):
        value = manifest.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            return None, f"artifact manifest {field} is not an integer"
        statuses[field] = value
    entries = manifest.get("files")
    if not isinstance(entries, list):
        return None, "artifact manifest files are invalid"
    by_path: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "size_bytes",
            "sha256",
        }:
            return None, "artifact manifest entry is invalid"
        relative = entry.get("path")
        size_bytes = entry.get("size_bytes")
        sha256 = entry.get("sha256")
        pure = PurePosixPath(relative) if isinstance(relative, str) else None
        if (
            pure is None
            or pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
            or pure.as_posix() != relative
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            return None, "artifact manifest entry is invalid"
        if relative in by_path:
            return None, "artifact manifest has duplicate paths"
        by_path[relative] = entry
    return (
        _ProviderFreeTerminalManifest(
            workload_status=statuses["workload_status"],
            final_status=statuses["final_status"],
            by_path=by_path,
        ),
        None,
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
