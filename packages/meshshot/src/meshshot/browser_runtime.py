"""Python-owned attested Chromium lifecycle behind one internal runtime seam."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
import hashlib
from importlib import metadata, resources
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterator


RUNTIME_SCHEMA = "meshshot.prelaunched-cdp-runtime/1"
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
_SourceIdentity = tuple[int, int, int, int]
_PROFILE_RESOURCE = "prelaunched_cdp_playwright_1_60_v1.json"
MESHSHOT_EXECUTABLE_ROOT = Path("/meshshot-exec")
_MESHSHOT_EXECUTABLE_ROOT_ENV = "MESHSHOT_EXECUTABLE_ROOT"
ADAPTER_PROFILE_SHA256 = "16ef68d9ee9700f10c9e92b6ca88c0430dc98c6808145258f9a6125f3acd5c04"
_DEVTOOLS_PATH = re.compile(r"^/devtools/browser/[0-9A-Za-z._-]+$")
_VERSION_OUTPUT = re.compile(
    r"^(?:Google Chrome for Testing|Chromium|Chrome|HeadlessChrome) "
    r"[0-9]+(?:\.[0-9]+){3}$"
)


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
                self.browser_identity_phase
                == "playwright_package_revision_identity"
                and browser_identity_check in PLAYWRIGHT_PACKAGE_REVISION_CHECKS
            )
            else None
        )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
    except (OSError, subprocess.SubprocessError) as exc:
        raise BrowserRuntimeError(
            "browser_identity",
            browser_identity_phase="private_launch_version_execution",
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


def _private_directory(prefix: str) -> Path:
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
    for _attempt in range(16):
        path = root / f"{prefix}{secrets.token_hex(16)}"
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            continue
        except OSError as exc:
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            ) from exc
        try:
            os.chmod(path, 0o700)
            info = path.lstat()
        except OSError as exc:
            shutil.rmtree(path, ignore_errors=True)
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            ) from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            shutil.rmtree(path, ignore_errors=True)
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="private_tree_materialization",
            )
        return path
    raise BrowserRuntimeError(
        "browser_identity",
        browser_identity_phase="private_tree_materialization",
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
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            source_fd = os.open(path, flags)
            source_info = os.fstat(source_fd)
        except OSError as exc:
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
        if (
            not path.is_absolute()
            or not stat.S_ISREG(source_info.st_mode)
            or source_info.st_mode & 0o111 == 0
            or (
                expected_source_identity is not None
                and actual_source_identity != expected_source_identity
            )
        ):
            os.close(source_fd)
            raise BrowserRuntimeError(
                "browser_identity",
                browser_identity_phase="source_executable_identity",
            )
        try:
            self._materialize_private_image(source_fd, source_info)
        except BaseException:
            self.close()
            raise
        finally:
            try:
                os.close(source_fd)
            except OSError as exc:
                self.close()
                raise BrowserRuntimeError(
                    "browser_identity",
                    browser_identity_phase="source_executable_identity",
                ) from exc

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
            os.close(descriptor)

    @staticmethod
    def _freeze_directories(root: Path) -> None:
        for current, _directories, _files in os.walk(
            root,
            topdown=False,
            followlinks=False,
        ):
            directory = Path(current)
            info = directory.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise BrowserRuntimeError(
                    "browser_identity",
                    browser_identity_phase="private_tree_materialization",
                )
            os.chmod(directory, 0o555)

    @staticmethod
    def _thaw_directories(root: Path) -> None:
        info = root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise BrowserRuntimeError("browser_cleanup")
        os.chmod(root, 0o700)
        for current, directories, _files in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            directory = Path(current)
            info = directory.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise BrowserRuntimeError("browser_cleanup")
            os.chmod(directory, 0o700)
            for name in directories:
                child = directory / name
                child_info = child.lstat()
                if not stat.S_ISLNK(child_info.st_mode):
                    if not stat.S_ISDIR(child_info.st_mode):
                        raise BrowserRuntimeError("browser_cleanup")
                    os.chmod(child, 0o700)

    def _materialize_private_image(
        self,
        source_fd: int,
        source_info: os.stat_result,
    ) -> None:
        root = _private_directory("meshshot-image-")
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
                os.close(output_fd)
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
            self.fd = snapshot_fd
            snapshot_fd = None
            self.identity = (
                launch_info.st_dev,
                launch_info.st_ino,
                launch_info.st_size,
                stat.S_IMODE(launch_info.st_mode),
            )
            self.launch_root = root
            self.launch_path = launch
        except BaseException as exc:
            cleanup_failed = False
            if snapshot_fd is not None:
                try:
                    os.close(snapshot_fd)
                except OSError:
                    cleanup_failed = True
            try:
                self._thaw_directories(root)
            except (BrowserRuntimeError, OSError):
                cleanup_failed = True
            try:
                shutil.rmtree(root)
            except OSError:
                cleanup_failed = True
            if os.path.lexists(root):
                cleanup_failed = True
            if cleanup_failed:
                raise BrowserRuntimeError("browser_cleanup") from exc
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
            raise BrowserRuntimeError("browser_cleanup") from failure
        if failure is not None:
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
            os.close(duplicate)
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

    def popen(self, argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        assert self.fd is not None and self.launch_path is not None
        options = dict(kwargs)
        if sys.platform.startswith("linux"):
            options["executable"] = f"/proc/self/fd/{self.fd}"
            options["pass_fds"] = (self.fd,)
            options["close_fds"] = True
        else:
            options["executable"] = os.fspath(self.launch_path)
        launch_argv = [os.fspath(self.launch_path), *argv[1:]]
        return subprocess.Popen(launch_argv, **options)

    def verify_running_image(self, pid: int, timeout: float) -> None:
        assert self.fd is not None
        expected = os.fstat(self.fd)
        if sys.platform.startswith("linux"):
            proc_exe = Path(f"/proc/{pid}/exe")
            try:
                if not os.readlink(proc_exe):
                    raise BrowserRuntimeError("browser_identity")
                descriptor = os.open(proc_exe, os.O_RDONLY)
            except OSError as exc:
                raise BrowserRuntimeError("browser_identity") from exc
            try:
                actual = os.fstat(descriptor)
                matches = (
                    actual.st_dev == expected.st_dev
                    and actual.st_ino == expected.st_ino
                    and self._sha256_fd(descriptor) == self._sha256_fd(self.fd)
                )
            finally:
                os.close(descriptor)
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
                    timeout=timeout,
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
        if sys.platform.startswith("linux"):
            options["executable"] = f"/proc/self/fd/{self.fd}"
            options["pass_fds"] = (self.fd,)
        return subprocess.run(**options)

    def close(self) -> None:
        failure = False
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                failure = True
            self.fd = None
        if self.launch_root is not None:
            try:
                self._thaw_directories(self.launch_root)
                shutil.rmtree(self.launch_root)
            except (BrowserRuntimeError, OSError):
                failure = True
            if os.path.lexists(self.launch_root):
                failure = True
            self.launch_root = None
        if failure:
            raise BrowserRuntimeError("browser_cleanup")


def _group_empty(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


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
                stat_line = (process_path / "stat").read_text(encoding="utf-8")
                tail = stat_line[stat_line.rfind(")") + 2 :].split()
                group = int(tail[2])
                for descriptor in (process_path / "fd").iterdir():
                    target = os.readlink(descriptor)
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
        except BaseException:
            self._pinned_executable.close()
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
        self._profile_dir: Path | None = None
        self._profile_identity: tuple[int, int] | None = None
        self._profile_cleanup_forbidden = False
        self._profile_fd: int | None = None
        self._profile_parent_fd: int | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._process_group: int | None = None

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
                with _blocked_runtime_signals():
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
            except OSError as exc:
                raise BrowserRuntimeError(_prelaunch_operation(exc)) from exc
            deadline = time.monotonic() + float(self._profile["startup_timeout_ms"]) / 1000
            readiness = profile / "DevToolsActivePort"
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
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
                return f"http://127.0.0.1:{int(lines[0])}"
            raise BrowserRuntimeError("browser_readiness_timeout")
        except BaseException:
            self._cleanup()
            raise

    def _cleanup(self) -> None:
        failure = False
        process = self._process
        process_group = self._process_group
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError:
                failure = True
        leader_timed_out = False
        if process is not None:
            try:
                process.wait(timeout=float(self._profile["cleanup_term_ms"]) / 1000)
            except subprocess.TimeoutExpired:
                leader_timed_out = True
            except OSError:
                failure = True
        if process_group is not None:
            group_empty = False if leader_timed_out else _wait_group_empty(
                process_group,
                float(self._profile["cleanup_term_ms"]) / 1000,
            )
            if leader_timed_out or not group_empty:
                try:
                    os.killpg(process_group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except (BrowserRuntimeError, OSError):
                    failure = True
                if process is not None and leader_timed_out:
                    try:
                        process.wait(
                            timeout=float(self._profile["cleanup_kill_ms"]) / 1000
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        failure = True
                group_empty = _wait_group_empty(
                    process_group,
                    float(self._profile["cleanup_kill_ms"]) / 1000,
                )
            if not group_empty:
                failure = True
        if self._profile_dir is not None:
            if getattr(self, "_profile_cleanup_forbidden", False):
                failure = True
            else:
                quarantine: Path | None = None
                quarantine_fd: int | None = None
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
                    quarantine = _private_directory("meshshot-profile-cleanup-")
                    quarantine_fd = os.open(
                        quarantine,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                    )
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
                    shutil.rmtree(quarantine / "profile")
                except (BrowserRuntimeError, OSError):
                    failure = True
                finally:
                    if quarantine_fd is not None:
                        try:
                            os.close(quarantine_fd)
                        except OSError:
                            failure = True
                    if quarantine is not None:
                        try:
                            quarantine.rmdir()
                        except OSError:
                            failure = True
            for attribute in ("_profile_fd", "_profile_parent_fd"):
                descriptor = getattr(self, attribute, None)
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        failure = True
                    setattr(self, attribute, None)
            if not getattr(self, "_profile_cleanup_forbidden", False):
                if os.path.lexists(self._profile_dir):
                    failure = True
        pinned = getattr(self, "_pinned_executable", None)
        if pinned is not None:
            try:
                pinned.close()
            except BrowserRuntimeError:
                failure = True
        if failure:
            raise BrowserRuntimeError("browser_cleanup")

    def _verify_connected_browser(self, browser: Any) -> None:
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
            or product.rsplit("/", 1)[-1] != self._profile["browser_version"]
        ):
            raise BrowserRuntimeError("browser_identity")

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
                interrupted: _RuntimeSignal | None = None
                try:
                    yield browser
                except _RuntimeSignal as exc:
                    interrupted = exc
                finally:
                    try:
                        self._cleanup()
                    except BrowserRuntimeError as cleanup_exc:
                        if interrupted is None:
                            raise
                        interrupted.cleanup_error = cleanup_exc
                if interrupted is not None:
                    raise interrupted
        except _RuntimeSignal as exc:
            if exc.cleanup_error is not None:
                raise exc.cleanup_error
            raise BrowserRuntimeError("browser_signal") from exc


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
