"""Python-owned attested Chromium lifecycle behind one internal runtime seam."""

from __future__ import annotations

from contextlib import contextmanager
import errno
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
_PROFILE_RESOURCE = "prelaunched_cdp_playwright_1_60_v1.json"
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

    def __init__(self, operation: str) -> None:
        super().__init__(operation)
        self.operation = operation


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
    import playwright

    manifest_path = (
        Path(playwright.__file__).resolve().parent / "driver/package/browsers.json"
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        matches = [
            item
            for item in manifest["browsers"]
            if item.get("name") == browser_name
        ]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BrowserRuntimeError("browser_identity") from exc
    if len(matches) != 1 or not isinstance(matches[0].get("revision"), str):
        raise BrowserRuntimeError("browser_identity")
    return matches[0]["revision"]


def default_executable(chromium_executable: str) -> Path:
    """Resolve the exact headless-shell sibling installed by Playwright."""

    profile, _profile_sha256 = _load_profile()
    full_browser = Path(chromium_executable).resolve(strict=True)
    revision_dir = next(
        (
            parent
            for parent in full_browser.parents
            if parent.name == f"chromium-{profile['revision']}"
        ),
        None,
    )
    if revision_dir is None:
        raise BrowserRuntimeError("browser_identity")
    shell_revision = revision_dir.parent / (
        f"chromium_headless_shell-{profile['revision']}"
    )
    candidates = [
        path
        for path in shell_revision.glob("chrome-headless-shell-*/chrome-headless-shell")
        if path.is_file() and not path.is_symlink()
    ]
    if len(candidates) != 1:
        raise BrowserRuntimeError("browser_identity")
    return candidates[0].resolve(strict=True)


def _attest(executable: _PinnedExecutable, profile: dict[str, Any]) -> dict[str, str]:
    try:
        playwright_version = metadata.version("playwright")
    except metadata.PackageNotFoundError as exc:
        raise BrowserRuntimeError("browser_identity") from exc
    revision = _playwright_revision(str(profile["browser"]))
    if (
        playwright_version != profile["playwright"]
        or revision != profile["revision"]
    ):
        raise BrowserRuntimeError("browser_identity")
    try:
        completed = executable.run_version(
            float(profile["startup_timeout_ms"]) / 1000
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BrowserRuntimeError("browser_identity") from exc
    try:
        version = completed.stdout.decode("utf-8").strip()
    except (AttributeError, UnicodeDecodeError) as exc:
        raise BrowserRuntimeError("browser_identity") from exc
    if (
        completed.returncode != 0
        or completed.stderr != b""
        or not _VERSION_OUTPUT.fullmatch(version)
        or version.rsplit(" ", 1)[-1] != profile["browser_version"]
    ):
        raise BrowserRuntimeError("browser_identity")
    return {
        "playwright": playwright_version,
        "browser": str(profile["browser"]),
        "revision": revision,
        "version": version,
        "sha256": executable.sha256(),
    }


def _private_directory(prefix: str) -> Path:
    root = Path(tempfile.gettempdir())
    for _attempt in range(16):
        path = root / f"{prefix}{secrets.token_hex(16)}"
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            continue
        try:
            os.chmod(path, 0o700)
            info = path.lstat()
        except OSError as exc:
            shutil.rmtree(path, ignore_errors=True)
            raise BrowserRuntimeError("browser_identity") from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            shutil.rmtree(path, ignore_errors=True)
            raise BrowserRuntimeError("browser_identity")
        return path
    raise BrowserRuntimeError("browser_identity")


class _PinnedExecutable:
    """Own one exact executable image from attestation through production exec."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None
        self.launch_path: Path | None = None
        self.launch_root: Path | None = None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            source_fd = os.open(path, flags)
            source_info = os.fstat(source_fd)
        except OSError as exc:
            raise BrowserRuntimeError("browser_identity") from exc
        if (
            not path.is_absolute()
            or not stat.S_ISREG(source_info.st_mode)
            or source_info.st_mode & 0o111 == 0
        ):
            os.close(source_fd)
            raise BrowserRuntimeError("browser_identity")
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
                raise BrowserRuntimeError("browser_identity") from exc

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
                    raise BrowserRuntimeError("browser_identity")
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
                raise BrowserRuntimeError("browser_identity")
            shutil.copyfile(source, target, follow_symlinks=True)
            os.chmod(target, stat.S_IMODE(info.st_mode) & ~0o222)
        except OSError as exc:
            raise BrowserRuntimeError("browser_identity") from exc

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
                raise BrowserRuntimeError("browser_identity")
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
            if snapshot_fd is not None:
                os.close(snapshot_fd)
            cleanup_failed = False
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
            if isinstance(exc, OSError):
                raise BrowserRuntimeError("browser_identity") from exc
            raise

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
                    f"-iTCP@127.0.0.1:{port}",
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
        group: int | None = None
        name: str | None = None
        state: str | None = None
        for line in [*lines, "pEND"]:
            if line.startswith("p"):
                if name is not None or state is not None:
                    records.append((group, name, state))
                group, name, state = None, None, None
            elif line.startswith("f"):
                if name is not None or state is not None:
                    records.append((group, name, state))
                name, state = None, None
            elif line.startswith("g"):
                try:
                    group = int(line[1:])
                except ValueError as exc:
                    raise BrowserRuntimeError("browser_identity") from exc
            elif line.startswith("n"):
                name = line[1:]
            elif line == "TST=LISTEN":
                state = "LISTEN"
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

    def __init__(self, executable: Path) -> None:
        self._executable = executable
        self._profile, profile_sha256 = _load_profile()
        self._pinned_executable = _PinnedExecutable(executable)
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
                _verify_listener_owner(
                    self._process_group,
                    int(lines[0]),
                    float(self._profile["startup_timeout_ms"]) / 1000,
                )
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
                except BrowserRuntimeError:
                    self._cleanup()
                    raise
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
