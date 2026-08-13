"""Python-owned attested Chromium lifecycle behind one internal runtime seam."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from importlib import metadata, resources
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterator


RUNTIME_SCHEMA = "meshshot.prelaunched-cdp-runtime/1"
_PROFILE_RESOURCE = "prelaunched_cdp_playwright_1_60_v1.json"
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
    return profile, hashlib.sha256(raw).hexdigest()


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


def _attest(executable: Path, profile: dict[str, Any]) -> dict[str, str]:
    try:
        mode = executable.lstat().st_mode
    except OSError as exc:
        raise BrowserRuntimeError("browser_identity") from exc
    if (
        not executable.is_absolute()
        or stat.S_ISLNK(mode)
        or not stat.S_ISREG(mode)
        or not os.access(executable, os.X_OK)
    ):
        raise BrowserRuntimeError("browser_identity")
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
        completed = subprocess.run(
            [os.fspath(executable), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=float(profile["startup_timeout_ms"]) / 1000,
            close_fds=True,
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
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
    }


def _group_empty(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


class PrelaunchedCdpRuntime:
    """Deep internal adapter: attest, launch, attach, and clean one browser."""

    def __init__(self, executable: Path) -> None:
        self._executable = executable
        self._profile, profile_sha256 = _load_profile()
        self.evidence = {
            "schema": RUNTIME_SCHEMA,
            "adapter_profile": {
                "name": self._profile["name"],
                "sha256": profile_sha256,
            },
            "browser_identity": _attest(executable, self._profile),
            "result": "passed",
        }
        self._profile_dir: Path | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._process_group: int | None = None

    def _prelaunch(self) -> str:
        profile = Path(tempfile.mkdtemp(prefix="meshshot-cdp-"))
        self._profile_dir = profile
        try:
            mode = profile.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode) or any(profile.iterdir()):
                raise BrowserRuntimeError("browser_profile")
            os.chmod(profile, 0o700)
            argv = [
                os.fspath(self._executable),
                *self._profile["arguments"],
                f"--user-data-dir={profile}",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                "about:blank",
            ]
            try:
                self._process = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
                self._process_group = os.getpgid(self._process.pid)
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
        if process is not None:
            try:
                process.wait(timeout=float(self._profile["cleanup_term_ms"]) / 1000)
            except subprocess.TimeoutExpired:
                if process_group is not None:
                    try:
                        os.killpg(process_group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except OSError:
                        failure = True
                try:
                    process.wait(timeout=float(self._profile["cleanup_kill_ms"]) / 1000)
                except (OSError, subprocess.TimeoutExpired):
                    failure = True
            except OSError:
                failure = True
        if process_group is not None:
            deadline = time.monotonic() + float(self._profile["cleanup_kill_ms"]) / 1000
            while time.monotonic() < deadline and not _group_empty(process_group):
                time.sleep(0.02)
            if not _group_empty(process_group):
                failure = True
        if self._profile_dir is not None:
            try:
                shutil.rmtree(self._profile_dir)
            except OSError:
                failure = True
            if os.path.lexists(self._profile_dir):
                failure = True
        if failure:
            raise BrowserRuntimeError("browser_cleanup")

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
                    self._cleanup()
                    if isinstance(exc, _RuntimeSignal):
                        raise
                    raise BrowserRuntimeError("browser_connect") from exc
                try:
                    yield browser
                finally:
                    self._cleanup()
        except _RuntimeSignal as exc:
            raise BrowserRuntimeError("browser_signal") from exc


class _RuntimeSignal(BaseException):
    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


@contextmanager
def _runtime_signal_cleanup() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        raise BrowserRuntimeError("browser_signal")
    watched = (signal.SIGINT, signal.SIGTERM)
    previous = {signum: signal.getsignal(signum) for signum in watched}

    def interrupt(signum: int, _frame: Any) -> None:
        for watched_signal in watched:
            signal.signal(watched_signal, signal.SIG_IGN)
        raise _RuntimeSignal(signum)

    try:
        for signum in watched:
            signal.signal(signum, interrupt)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
