#!/usr/bin/env python3
"""Run one complete pilot transaction through mandatory claude-tap."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import zipfile
from contextlib import closing, nullcontext
from pathlib import Path
from types import FrameType
from typing import Callable, Mapping

try:
    from scripts.pilot.venus_retry_proxy import RetryProxy
    from scripts.pilot.browser_sidecar import (
        BROKER_BASE_IMAGE_ID,
        BROKER_IMAGE_ID,
        BROKER_IMAGE_SOURCE_REVISION,
        IMAGE_ID,
        IMAGE_SOURCE_REVISION,
        PROGRAMS,
        RECEIPT_PREDICATES,
        RECEIPT_SCHEMA,
        NESTED_GATE,
        BrowserSidecarError,
        BrowserSidecarJob,
    )
    from scripts.pilot.browser_surface import BrowserSurfaceError, discover_browser_roots
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from venus_retry_proxy import RetryProxy
    from browser_sidecar import (  # type: ignore[no-redef]
        BROKER_BASE_IMAGE_ID,
        BROKER_IMAGE_ID,
        BROKER_IMAGE_SOURCE_REVISION,
        IMAGE_ID,
        IMAGE_SOURCE_REVISION,
        PROGRAMS,
        RECEIPT_PREDICATES,
        RECEIPT_SCHEMA,
        NESTED_GATE,
        BrowserSidecarError,
        BrowserSidecarJob,
    )
    from browser_surface import (  # type: ignore[no-redef]
        BrowserSurfaceError,
        discover_browser_roots,
    )


READY_PATTERN = re.compile(r"listening on http://127\.0\.0\.1:(\d+)")
FINAL_SESSION_STATUSES = {"complete", "error", "empty"}
REQUIRED_TAP_VERSION = "0.1.140"
TAP_TARGET = "http://v2.open.venus.oa.com/llmproxy/v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_REPO_ROOT = Path("/workspace/repo")
SANDBOX_HOME = Path("/home/pilot")
SANDBOX_CODEX_HOME = SANDBOX_HOME / ".codex"
ARTIFACT_CONTRACT_STATUS = 4
MANIFEST_EXCLUDED_ROOTS = {".git"}
MANIFEST_EXCLUDED_PREFIXES = {"run/.codex-upper"}
WORKSPACE_HELPER = REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace"
NESTED_GATE_TIMEOUT_SECONDS = 180.0
SYSTEM_RO_PATHS = (
    Path("/usr"),
    Path("/etc/alternatives"),
    Path("/etc/ca-certificates"),
    Path("/etc/crypto-policies"),
    Path("/etc/fonts"),
    Path("/etc/group"),
    Path("/etc/hosts"),
    Path("/etc/ld.so.cache"),
    Path("/etc/ld.so.conf"),
    Path("/etc/ld.so.conf.d"),
    Path("/etc/localtime"),
    Path("/etc/nsswitch.conf"),
    Path("/etc/os-release"),
    Path("/etc/passwd"),
    Path("/etc/pki"),
    Path("/etc/resolv.conf"),
    Path("/etc/ssl"),
    Path("/sys"),
)
SANDBOX_ENV_PASSTHROUGH = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "TZ",
)
GITIGNORE = """\
run/
artifact_manifest.json
.artifact_manifest.json.tmp
__pycache__/
*.pyc
.codex/
"""


class PilotError(RuntimeError):
    """The pilot could not prepare or finalize its local experiment state."""


class LifecycleState:
    """Track whether rollout-producing workload startup succeeded."""

    def __init__(self) -> None:
        """Initialize before any workload child exists."""

        self.workload_started = False


class TapError(RuntimeError):
    """The mandatory proxy could not satisfy its runtime contract."""


class SignalRelay:
    """Record INT/TERM and forward them to the active workload process group."""

    def __init__(self) -> None:
        """Initialize signal state without changing the caller's handlers."""

        self.signum: int | None = None
        self.child: subprocess.Popen[bytes] | None = None
        self._previous: dict[int, signal.Handlers] = {}

    @property
    def cancelled(self) -> bool:
        """Return whether the supervisor received INT or TERM."""

        return self.signum is not None

    def attach(self, child: subprocess.Popen[bytes]) -> None:
        """Attach the workload and replay any signal received during Popen."""

        self.child = child
        if self.signum is not None and child.poll() is None:
            # Popen and attach are separate Python operations. Replaying here
            # closes the narrow window in which the supervisor had no child.
            signal_process_group(child, self.signum)

    def detach(self) -> None:
        """Stop forwarding after the workload has exited."""

        self.child = None

    def _handle(self, signum: int, frame: FrameType | None) -> None:
        """Forward the first signal and make a repeated request forceful."""

        repeated = self.signum is not None
        if self.signum is None:
            self.signum = signum
        if self.child is not None and self.child.poll() is None:
            # A second Ctrl-C means the caller no longer wants graceful wait.
            signal_process_group(
                self.child,
                signal.SIGKILL if repeated else signum,
            )

    def __enter__(self) -> SignalRelay:
        """Install temporary handlers for one supervised lifecycle."""

        for signum in (signal.SIGINT, signal.SIGTERM):
            self._previous[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle)
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        """Restore the original handlers and propagate caller exceptions."""

        for signum, handler in self._previous.items():
            signal.signal(signum, handler)
        return False


class NestedGateChannel:
    """Own the one-shot proof/release socket used before Agent exec."""

    def __init__(self, capability_dir: Path, *, timeout: float = NESTED_GATE_TIMEOUT_SECONDS) -> None:
        """Bind one exact private socket before the bwrap gate starts."""

        self.path = capability_dir.resolve() / Path(NESTED_GATE["socketPath"]).name
        self.timeout = timeout
        self.listener: socket.socket | None = None
        self.connection: socket.socket | None = None
        self.identity: tuple[int, int] | None = None
        if self.path.exists() or self.path.is_symlink():
            raise PilotError("nested-gate socket path already exists")
        try:
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(os.fspath(self.path))
            self.path.chmod(0o600)
            listener.listen(1)
            metadata = self.path.lstat()
        except OSError as exc:
            listener.close() if "listener" in locals() else None
            raise PilotError("cannot create nested-gate proof channel") from exc
        if (
            not stat.S_ISSOCK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
        ):
            listener.close()
            raise PilotError("nested-gate proof channel identity is invalid")
        self.listener = listener
        self.identity = (metadata.st_dev, metadata.st_ino)

    def _unlink_owned(self) -> None:
        """Unlink only the exact socket inode this channel created."""

        if self.identity is None:
            return
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise PilotError("cannot inspect nested-gate proof channel") from exc
        if (metadata.st_dev, metadata.st_ino) != self.identity:
            raise PilotError("nested-gate proof channel identity changed")
        try:
            self.path.unlink()
        except OSError as exc:
            raise PilotError("cannot remove nested-gate proof channel") from exc

    @staticmethod
    def _decode(raw: bytes) -> Mapping[str, object]:
        """Decode exactly one bounded duplicate-free proof object."""

        if not raw:
            raise PilotError("nested-gate proof is missing")
        if not raw.endswith(b"\n"):
            raise PilotError("nested-gate proof is malformed")
        if b"\n" in raw[:-1]:
            raise PilotError("nested-gate proof is duplicate")

        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            """Reject duplicate keys in the one proof object."""

            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise PilotError("nested-gate proof is malformed")
                result[key] = value
            return result

        try:
            proof = json.loads(raw[:-1], object_pairs_hook=unique)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PilotError("nested-gate proof is malformed") from exc
        if not isinstance(proof, dict):
            raise PilotError("nested-gate proof is malformed")
        return proof

    def receive(self, cancelled: Callable[[], bool]) -> Mapping[str, object]:
        """Accept the first proof and close the listener before validation."""

        if self.listener is None:
            raise PilotError("nested-gate proof channel is unavailable")
        deadline = time.monotonic() + self.timeout
        self.listener.settimeout(min(0.1, max(self.timeout, 0.001)))
        while True:
            if cancelled():
                raise PilotError("nested-gate proof interrupted")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PilotError("nested-gate proof timeout")
            self.listener.settimeout(min(0.1, remaining))
            try:
                connection, _ = self.listener.accept()
                break
            except TimeoutError:
                continue
            except OSError as exc:
                raise PilotError("nested-gate proof accept failed") from exc
        self.connection = connection
        self.listener.close()
        self.listener = None
        self._unlink_owned()
        raw = bytearray()
        while True:
            if cancelled():
                raise PilotError("nested-gate proof interrupted")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PilotError("nested-gate proof timeout")
            connection.settimeout(min(0.1, remaining))
            try:
                chunk = connection.recv(65536)
            except TimeoutError:
                continue
            except OSError as exc:
                raise PilotError("nested-gate proof read failed") from exc
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > NESTED_GATE["maxProofBytes"]:
                raise PilotError("nested-gate proof is malformed")
        return self._decode(bytes(raw))

    def release(self) -> None:
        """Release the validated gate to exec the Agent exactly once."""

        if self.connection is None:
            raise PilotError("nested-gate proof connection is unavailable")
        try:
            self.connection.sendall(b"\x01")
            self.connection.shutdown(socket.SHUT_WR)
            self.connection.close()
            self.connection = None
        except OSError as exc:
            raise PilotError("nested-gate release failed") from exc

    def close(self) -> None:
        """Close the proof channel; never remove a replacement inode."""

        errors: list[PilotError] = []
        for connection in (self.connection, self.listener):
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    errors.append(PilotError("nested-gate channel close failed"))
        self.connection = None
        self.listener = None
        try:
            self._unlink_owned()
        except PilotError as exc:
            errors.append(exc)
        if errors:
            raise errors[0]


def signal_process_group(process: subprocess.Popen[bytes], signum: int) -> None:
    """Signal bwrap and all descendants without searching the process table."""

    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def normalize_returncode(returncode: int) -> int:
    """Translate Popen's negative signal status into shell 128+signal form."""

    return 128 + abs(returncode) if returncode < 0 else returncode


def read_timeout(
    environ: Mapping[str, str],
    name: str,
    default: str,
) -> float:
    """Read one finite, non-negative timeout before any child is started."""

    raw = environ.get(name, default)
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise TapError(f"{name} must be numeric") from exc
    if not math.isfinite(timeout) or timeout < 0:
        raise TapError(f"{name} must be finite and non-negative")
    return timeout


def resolve_tap(environ: Mapping[str, str]) -> str:
    """Find the pinned claude-tap executable without installing or upgrading."""

    path = shutil.which("claude-tap", path=environ.get("PATH"))
    if not path:
        raise TapError(
            f"claude-tap {REQUIRED_TAP_VERSION} is required; "
            f"install it explicitly before running a pilot"
        )
    try:
        result = subprocess.run(
            [path, "--version"],
            check=True,
            capture_output=True,
            text=True,
            env=dict(environ),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise TapError(f"cannot inspect claude-tap version: {exc}") from exc
    actual = result.stdout.strip()
    expected = f"claude-tap {REQUIRED_TAP_VERSION}"
    if actual != expected:
        raise TapError(f"expected {expected!r}, got {actual!r}")
    return path


def start_tap(
    tap_bin: str,
    exp_dir: Path,
    environ: Mapping[str, str],
    target_url: str,
) -> subprocess.Popen[bytes]:
    """Start one loopback-only proxy whose database belongs to EXP_DIR."""

    tap_env = dict(environ)
    # Per-EXP storage prevents concurrent pilots from sharing trace state.
    # Unbuffered output makes the ready marker observable immediately.
    run_dir = exp_dir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    tap_env["CLOUDTAP_DB"] = str(run_dir / "traces.sqlite3")
    tap_env["PYTHONUNBUFFERED"] = "1"
    argv = [
        tap_bin,
        "--tap-client",
        "codex",
        "--tap-no-launch",
        "--tap-no-open",
        "--tap-no-live",
        "--tap-host",
        "127.0.0.1",
        "--tap-port",
        # claude-tap owns bind(0), avoiding a reserve-close-rebind race.
        "0",
        "--tap-target",
        target_url,
        "--tap-allow-path",
        "/v1",
        "--tap-max-traces",
        # Each EXP has its own DB, so retention must not delete pilot evidence.
        "0",
    ]
    try:
        log_file = (run_dir / ".claude-tap.log").open("wb")
    except OSError as exc:
        raise TapError(f"cannot open claude-tap log: {exc}") from exc
    try:
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=tap_env,
            # Tap is a direct child but not part of the workload process group.
            start_new_session=True,
        )
    except OSError as exc:
        raise TapError(f"failed to start claude-tap: {exc}") from exc
    finally:
        log_file.close()


def wait_ready(
    process: subprocess.Popen[bytes],
    log_path: Path,
    timeout: float,
    cancelled: Callable[[], bool],
) -> int | None:
    """Return the child-advertised port, or None when startup was cancelled."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancelled():
            return None
        returncode = process.poll()
        if returncode is not None:
            raise TapError(f"claude-tap exited before ready (status={returncode})")
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise TapError(f"cannot read claude-tap log: {exc}") from exc
        match = READY_PATTERN.search(text)
        if match is not None:
            # The log is truncated for this exact child at start_tap(), so a
            # matched dynamic port cannot be stale state from another pilot.
            return int(match.group(1))
        time.sleep(0.05)
    raise TapError("claude-tap readiness timeout")


def stop_tap(process: subprocess.Popen[bytes], timeout: float) -> None:
    """Finalize tap with SIGINT, escalating only after bounded waits."""

    if process.poll() is not None:
        return
    # claude-tap 0.1.140 finalizes the SQLite writer on KeyboardInterrupt.
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        print("warning: claude-tap SIGINT timeout; sending SIGTERM", file=sys.stderr)
    process.terminate()
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        print("warning: claude-tap SIGTERM timeout; sending SIGKILL", file=sys.stderr)
    process.kill()
    process.wait(timeout=1)


def wait_workload(
    workload: subprocess.Popen[bytes],
    tap: subprocess.Popen[bytes],
    sidecar: BrowserSidecarJob | None = None,
) -> tuple[int, bool]:
    """Wait while failing closed if mandatory tap or Sidecar authority exits."""

    while True:
        workload_status = workload.poll()
        if workload_status is not None:
            return normalize_returncode(workload_status), False
        tap_status = tap.poll()
        if tap_status is not None:
            print(
                f"pilot-runner: claude-tap exited during workload "
                f"(status={tap_status})",
                file=sys.stderr,
            )
            # Without tap the workload must not continue and possibly retry a
            # direct provider path. Terminate the whole bwrap process group.
            signal_process_group(workload, signal.SIGTERM)
            try:
                workload.wait(timeout=5)
            except subprocess.TimeoutExpired:
                signal_process_group(workload, signal.SIGKILL)
                workload.wait(timeout=2)
            return 1, True
        if sidecar is not None:
            try:
                sidecar_failed = sidecar.poll_failed()
            except Exception:
                sidecar_failed = True
            if sidecar_failed:
                print(
                    "pilot-runner: Browser Sidecar exited during workload",
                    file=sys.stderr,
                )
                signal_process_group(workload, signal.SIGTERM)
                try:
                    workload.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    signal_process_group(workload, signal.SIGKILL)
                    workload.wait(timeout=2)
                return 1, True
        time.sleep(0.1)


def read_trace(exp_dir: Path) -> tuple[str, str, int]:
    """Return the latest session id, status, and captured record count."""

    db_path = exp_dir / "run/traces.sqlite3"
    if not db_path.is_file():
        raise TapError("required traces.sqlite3 is missing")
    try:
        with closing(sqlite3.connect(db_path)) as connection:
            row = connection.execute(
                "SELECT id, status, record_count "
                "FROM sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
    except sqlite3.Error as exc:
        raise TapError(f"cannot query traces.sqlite3: {exc}") from exc
    if row is None:
        raise TapError("traces.sqlite3 contains no session")
    return str(row[0]), str(row[1]), int(row[2])


def export_html(
    tap_bin: str,
    exp_dir: Path,
    session_id: str,
    environ: Mapping[str, str],
) -> None:
    """Best-effort export of an atomic HTML viewer from finalized SQLite."""

    run_dir = exp_dir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    temporary = run_dir / f".trace.html.tmp.{os.getpid()}"
    output = run_dir / "trace.html"
    export_log = run_dir / ".claude-tap.log.export"
    temporary.unlink(missing_ok=True)
    export_env = dict(environ)
    export_env["CLOUDTAP_DB"] = str(run_dir / "traces.sqlite3")
    try:
        with export_log.open("wb") as log_file:
            result = subprocess.run(
                [
                    tap_bin,
                    "export",
                    session_id,
                    "--format",
                    "html",
                    "--output",
                    str(temporary),
                ],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=export_env,
                check=False,
            )
        if result.returncode == 0 and temporary.is_file() and temporary.stat().st_size:
            # Publish only a complete viewer; a failed export never overwrites
            # an older valid trace.html.
            temporary.replace(output)
            export_log.unlink(missing_ok=True)
        else:
            print(
                "warning: trace.html export failed; SQLite and export log preserved",
                file=sys.stderr,
            )
    except OSError as exc:
        print(f"warning: trace.html export failed: {exc}", file=sys.stderr)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_sandbox(exp_dir: Path, skill_dirs: list[Path]) -> Path:
    """Create the isolated Codex home and skill mount points."""

    upper = exp_dir / "run/.codex-upper"
    try:
        upper.mkdir(parents=True, exist_ok=True)
        for skill_dir in skill_dirs:
            (upper / "skills" / skill_dir.name).mkdir(
                parents=True,
                exist_ok=True,
            )
    except OSError as exc:
        raise PilotError(f"cannot prepare sandbox state: {exc}") from exc
    return upper


def validate_exp_dir(repo_root: Path, exp_dir: Path) -> Path:
    """Require a resolved experiment child below the checkout outputs directory."""

    outputs_root = (repo_root / "outputs").resolve()
    resolved = exp_dir.resolve()
    try:
        relative = resolved.relative_to(outputs_root)
    except ValueError as exc:
        raise PilotError(f"EXP_DIR must be under {outputs_root}: {resolved}") from exc
    if not relative.parts:
        raise PilotError(f"EXP_DIR cannot be the outputs root: {resolved}")
    return resolved


def validate_input_paths(repo_root: Path, input_paths: list[Path]) -> list[Path]:
    """Resolve exact input files and require each one below checkout models."""

    models_root = (repo_root / "models").resolve()
    resolved_inputs = []
    for input_path in input_paths:
        resolved = input_path.resolve()
        try:
            resolved.relative_to(models_root)
        except ValueError as exc:
            raise PilotError(
                f"input must be under {models_root}: {resolved}"
            ) from exc
        if not resolved.is_file():
            raise PilotError(f"input file not found: {resolved}")
        resolved_inputs.append(resolved)
    if not resolved_inputs:
        raise PilotError("at least one --input is required")
    return resolved_inputs


def resolve_installed_skill_dirs(
    repo_root: Path,
    host_codex_home: Path,
) -> list[Path]:
    """Return Codex-installed skills whose links target this checkout."""

    skills_root = (repo_root / "skills").resolve()
    installed_root = host_codex_home / "skills"
    if not installed_root.is_dir():
        raise PilotError(
            "Codex skills are not installed; run "
            "scripts/install/install-skills.sh --agent codex"
        )
    skills = []
    try:
        entries = sorted(installed_root.iterdir(), key=lambda path: path.name)
        for entry in entries:
            if not entry.is_symlink():
                continue
            target = entry.resolve()
            try:
                target.relative_to(skills_root)
            except ValueError:
                continue
            if target.is_dir() and (target / "SKILL.md").is_file():
                skills.append(target)
    except OSError as exc:
        raise PilotError(f"cannot inspect installed Codex skills: {exc}") from exc
    if not skills:
        raise PilotError(
            f"no installed Codex skills target this checkout: {installed_root}"
        )
    return skills


def resolve_sandbox_codex(environ: Mapping[str, str]) -> Path:
    """Resolve Codex inside the fixed /usr runtime mounted into sandbox."""

    requested = shutil.which("codex", path=environ.get("PATH"))
    if not requested:
        raise PilotError("codex not found on Host PATH")
    resolved = Path(requested).resolve()
    try:
        resolved.relative_to(Path("/usr"))
    except ValueError as exc:
        raise PilotError(
            f"codex must resolve under audited /usr runtime: {resolved}"
        ) from exc
    return resolved


def existing_system_paths() -> list[Path]:
    """Return the fixed system-runtime allowlist entries present on this host."""

    return [path for path in SYSTEM_RO_PATHS if path.exists()]


def build_sandbox_environment(
    environ: Mapping[str, str],
    tap_url: str,
) -> dict[str, str]:
    """Return the explicit child environment allowlist for Codex."""

    child_env = {
        name: environ[name]
        for name in SANDBOX_ENV_PASSTHROUGH
        if environ.get(name)
    }
    child_env.update(
        {
            "CLAUDE_TAP_URL": tap_url,
            "CODEX_HOME": str(SANDBOX_CODEX_HOME),
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": str(SANDBOX_HOME),
            "PATH": (
                f"{SANDBOX_REPO_ROOT}/.venv/bin:"
                "/usr/local/bin:/usr/bin:/bin"
            ),
            "PYTHONDONTWRITEBYTECODE": "1",
            "UV_CACHE_DIR": "/tmp/uv-cache",
            "VENUS_TOKEN": environ.get("VENUS_TOKEN", ""),
            "XDG_CACHE_HOME": "/tmp/cache",
        }
    )
    return child_env


def _readonly_surface_mounts(
    repo_root: Path,
    exp_dir: Path,
    input_paths: list[Path],
    environ: Mapping[str, str],
) -> list[tuple[Path, Path]]:
    """Resolve the exact immutable execution surface later mounted into bwrap."""

    host_home_value = environ.get("HOME")
    if not host_home_value:
        raise PilotError("HOME must be set")
    host_codex_home = Path(
        environ.get("CODEX_HOME", str(Path(host_home_value) / ".codex"))
    ).resolve()
    inputs = validate_input_paths(repo_root, input_paths)
    skills = resolve_installed_skill_dirs(repo_root, host_codex_home)
    gateway = (repo_root / "gateway/codex-tap-gpt56").resolve()
    venv = (repo_root / ".venv").resolve()
    mounts: list[tuple[Path, Path]] = [
        (venv, SANDBOX_REPO_ROOT / ".venv"),
        (gateway, SANDBOX_REPO_ROOT / "gateway" / gateway.name),
    ]
    mounts.extend((path.resolve(), path) for path in existing_system_paths())
    mounts.extend(
        (path, SANDBOX_REPO_ROOT / path.relative_to(repo_root)) for path in inputs
    )
    for skill in skills:
        mounts.extend(
            (
                (skill, SANDBOX_REPO_ROOT / "skills" / skill.name),
                (skill, SANDBOX_CODEX_HOME / "skills" / skill.name),
            )
        )
    return mounts


def _build_gate_artifact(repo_root: Path, destination: Path) -> str:
    """Create one deterministic zipapp from reviewed gate and meshshot source."""

    entries: list[tuple[str, bytes]] = [
        (
            "__main__.py",
            (repo_root / "scripts/pilot/browser_sidecar_gate.py").read_bytes(),
        ),
        (
            "browser_surface.py",
            (repo_root / "scripts/pilot/browser_surface.py").read_bytes(),
        ),
    ]
    meshshot = repo_root / "packages/meshshot/src/meshshot"
    for path in sorted(meshshot.rglob("*")):
        if path.is_symlink():
            raise PilotError("sealed gate source contains a symlink")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            entries.append(
                ("meshshot/" + path.relative_to(meshshot).as_posix(), path.read_bytes())
            )
    temporary = destination.with_suffix(".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, payload in entries:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o444 << 16
                archive.writestr(info, payload)
        temporary.chmod(0o444)
        os.replace(temporary, destination)
        destination.chmod(0o444)
        return hashlib.sha256(destination.read_bytes()).hexdigest()
    except OSError as exc:
        raise PilotError("cannot create sealed nested Browser Gate") from exc
    finally:
        temporary.unlink(missing_ok=True)


def prepare_nested_browser_gate(
    repo_root: Path,
    exp_dir: Path,
    input_paths: list[Path],
    environ: Mapping[str, str],
    sidecar: BrowserSidecarJob,
) -> None:
    """Seal the gate and close the complete mounted Agent browser surface."""

    mounts = _readonly_surface_mounts(
        repo_root.resolve(), exp_dir.resolve(), input_paths, environ
    )
    host_home = Path(environ["HOME"])
    host_codex_home = Path(
        environ.get("CODEX_HOME", str(host_home / ".codex"))
    ).resolve()
    skills = resolve_installed_skill_dirs(repo_root.resolve(), host_codex_home)
    upper = prepare_sandbox(exp_dir.resolve(), skills)
    relative_exp = exp_dir.resolve().relative_to(repo_root.resolve())
    writable_mounts = [
        (exp_dir.resolve(), SANDBOX_REPO_ROOT / relative_exp),
        (upper.resolve(), SANDBOX_CODEX_HOME),
    ]
    try:
        exclusions = discover_browser_roots(mounts)
        writable_findings = discover_browser_roots(writable_mounts)
    except BrowserSurfaceError as exc:
        raise PilotError("cannot close mounted Agent browser surface") from exc
    if writable_findings:
        raise PilotError("writable Agent surface contains a browser artifact")
    scan_roots = sorted(
        {target.as_posix() for _, target in [*mounts, *writable_mounts]}
    )
    manifest = {
        "schema": NESTED_GATE["surfaceSchema"],
        "scanRoots": scan_roots,
        "browserExclusions": exclusions,
    }
    manifest_bytes = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    artifact = sidecar.capability_dir / Path(NESTED_GATE["artifactPath"]).name
    input_path = sidecar.capability_dir / Path(NESTED_GATE["inputPath"]).name
    created: dict[Path, tuple[int, int]] = {}
    artifact_sha256 = _build_gate_artifact(repo_root.resolve(), artifact)
    artifact_metadata = artifact.lstat()
    created[artifact] = (artifact_metadata.st_dev, artifact_metadata.st_ino)
    gate_input = {
        "schema": NESTED_GATE["inputSchema"],
        "jobId": sidecar.job_id,
        "nonce": sidecar.gate_nonce,
        "artifactSha256": artifact_sha256,
        "surfaceManifest": manifest,
    }
    temporary = input_path.with_suffix(".tmp")
    try:
        temporary.write_text(
            json.dumps(gate_input, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="ascii",
        )
        temporary.chmod(0o444)
        os.replace(temporary, input_path)
        input_path.chmod(0o444)
        input_metadata = input_path.lstat()
        created[input_path] = (input_metadata.st_dev, input_metadata.st_ino)
        sidecar.configure_nested_gate(
            artifact_sha256=artifact_sha256,
            surface_manifest_sha256=manifest_sha256,
        )
    except Exception as exc:
        cleanup_failed = False
        for path, identity in reversed(list(created.items())):
            try:
                metadata = path.lstat()
                if (metadata.st_dev, metadata.st_ino) != identity:
                    cleanup_failed = True
                else:
                    path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            raise PilotError("nested Browser Gate preparation cleanup failed") from exc
        if isinstance(exc, BrowserSidecarError):
            raise PilotError("cannot bind fixed nested Browser Gate") from exc
        raise PilotError("cannot publish fixed nested Browser Gate input") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _gate_surface_manifest(capability_dir: Path) -> Mapping[str, object]:
    """Read the outer-owned gate input used to construct exact bwrap masks."""

    path = capability_dir / Path(NESTED_GATE["inputPath"]).name
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotError("fixed nested Browser Gate input is unavailable") from exc
    manifest = value.get("surfaceManifest") if isinstance(value, dict) else None
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema", "scanRoots", "browserExclusions"
    }:
        raise PilotError("fixed nested Browser Gate surface is invalid")
    return manifest


def build_bwrap_argv(
    repo_root: Path,
    exp_dir: Path,
    input_paths: list[Path],
    workload: list[str],
    environ: Mapping[str, str],
    browser_capability_dir: Path | None = None,
) -> list[str]:
    """Build a least-visibility bwrap argv without placing secrets in it."""

    repo_root = repo_root.resolve()
    if not environ.get("VENUS_TOKEN"):
        raise PilotError(
            "VENUS_TOKEN must be set (source ~/.secrets/text-to-cad.env)"
        )
    bwrap = shutil.which("bwrap", path=environ.get("PATH"))
    if not bwrap:
        raise PilotError("bwrap not installed; run: dnf install -y bubblewrap")
    resolve_sandbox_codex(environ)
    host_home_value = environ.get("HOME")
    if not host_home_value:
        raise PilotError("HOME must be set")
    host_codex_home = Path(
        environ.get("CODEX_HOME", str(Path(host_home_value) / ".codex"))
    ).resolve()

    exp_dir = validate_exp_dir(repo_root, exp_dir)
    inputs = validate_input_paths(repo_root, input_paths)
    skill_dirs = resolve_installed_skill_dirs(
        repo_root,
        host_codex_home,
    )
    relative_exp = exp_dir.relative_to(repo_root)
    sandbox_exp = SANDBOX_REPO_ROOT / relative_exp
    gateway = repo_root / "gateway" / "codex-tap-gpt56"
    if not gateway.is_file():
        raise PilotError(f"gateway not found: {gateway}")
    venv = repo_root / ".venv"
    if not venv.is_dir():
        raise PilotError(f"pilot runtime not found: {venv}")
    upper = prepare_sandbox(exp_dir, skill_dirs)
    gate_manifest: Mapping[str, object] | None = None
    if browser_capability_dir is not None:
        browser_capability_dir = browser_capability_dir.resolve()
        if not browser_capability_dir.is_dir():
            raise PilotError("Browser Sidecar capability directory is unavailable")
        gate_artifact = browser_capability_dir / Path(NESTED_GATE["artifactPath"]).name
        if not gate_artifact.is_file() or stat.S_IMODE(gate_artifact.stat().st_mode) != 0o444:
            raise PilotError("sealed nested Browser Gate artifact is unavailable")
        gate_manifest = _gate_surface_manifest(browser_capability_dir)
        expected_scan_roots = sorted(
            {
                target.as_posix()
                for _, target in _readonly_surface_mounts(
                    repo_root, exp_dir, input_paths, environ
                )
            }
            | {sandbox_exp.as_posix(), SANDBOX_CODEX_HOME.as_posix()}
        )
        if gate_manifest.get("scanRoots") != expected_scan_roots:
            raise PilotError("nested Browser Gate mount surface changed")
    argv = [
        bwrap,
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--cap-drop",
        "ALL",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/workspace",
        "--dir",
        str(SANDBOX_REPO_ROOT),
        "--dir",
        str(SANDBOX_REPO_ROOT / "models"),
        "--dir",
        str(SANDBOX_REPO_ROOT / "outputs"),
        "--dir",
        str(sandbox_exp.parent),
        "--dir",
        str(SANDBOX_REPO_ROOT / "gateway"),
        "--dir",
        str(SANDBOX_REPO_ROOT / "skills"),
        "--dir",
        "/home",
        "--dir",
        "/run",
        "--dir",
        str(SANDBOX_HOME),
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/sbin",
        "/sbin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--ro-bind",
        str(venv),
        str(SANDBOX_REPO_ROOT / ".venv"),
        "--ro-bind",
        str(gateway),
        str(SANDBOX_REPO_ROOT / "gateway" / gateway.name),
        "--bind",
        str(exp_dir),
        str(sandbox_exp),
        "--bind",
        str(upper),
        str(SANDBOX_CODEX_HOME),
    ]
    for path in existing_system_paths():
        argv.extend(["--ro-bind", str(path), str(path)])
    for input_path in inputs:
        relative_input = input_path.relative_to(repo_root)
        argv.extend(
            [
                "--dir",
                str((SANDBOX_REPO_ROOT / relative_input).parent),
                "--ro-bind",
                str(input_path),
                str(SANDBOX_REPO_ROOT / relative_input),
            ]
        )
    for skill_dir in skill_dirs:
        argv.extend(
            [
                "--ro-bind",
                str(skill_dir),
                str(SANDBOX_REPO_ROOT / "skills" / skill_dir.name),
                "--ro-bind",
                str(skill_dir),
                str(SANDBOX_CODEX_HOME / "skills" / skill_dir.name),
            ]
        )
    if browser_capability_dir is not None:
        argv.extend(
            [
                "--ro-bind",
                str(browser_capability_dir),
                "/run/meshshot-browser",
            ]
        )
    if gate_manifest is not None:
        exclusions = gate_manifest.get("browserExclusions")
        if not isinstance(exclusions, list):
            raise PilotError("fixed nested Browser Gate exclusions are invalid")
        for exclusion in exclusions:
            if (
                not isinstance(exclusion, dict)
                or set(exclusion) != {"kind", "target", "mask"}
                or not isinstance(exclusion.get("target"), str)
            ):
                raise PilotError("fixed nested Browser Gate exclusion is invalid")
            if exclusion.get("mask") == "tmpfs":
                argv.extend(["--tmpfs", exclusion["target"]])
            elif exclusion.get("mask") == "dev-null":
                argv.extend(["--ro-bind", "/dev/null", exclusion["target"]])
            else:
                raise PilotError("fixed nested Browser Gate exclusion is invalid")
    argv.extend(
        [
            "--remount-ro",
            "/",
            "--share-net",
            "--die-with-parent",
            "--chdir",
            str(SANDBOX_REPO_ROOT),
            "--",
            os.fspath(SANDBOX_REPO_ROOT / ".venv/bin/python"),
            NESTED_GATE["artifactPath"],
            "--",
            *workload,
        ]
    )
    return argv


def run_supervised(
    exp_dir: Path,
    input_paths: list[Path],
    command: list[str],
    environ: Mapping[str, str],
    state: LifecycleState | None = None,
    sidecar: BrowserSidecarJob | None = None,
    relay: SignalRelay | None = None,
) -> int:
    """Run command behind mandatory tap and return a shell-compatible status."""

    tap_bin = resolve_tap(environ)
    # Validate timeouts before Popen so malformed cleanup configuration cannot
    # leave a proxy whose stop policy is unknown.
    ready_timeout = read_timeout(environ, "TAP_READY_TIMEOUT", "5")
    stop_timeout = read_timeout(environ, "TAP_STOP_TIMEOUT", "5")

    bwrap_argv = build_bwrap_argv(
        REPO_ROOT,
        exp_dir,
        input_paths,
        command,
        environ,
        (
            sidecar.capability_dir
            if sidecar is not None
            else None
        ),
    )
    if state is None:
        state = LifecycleState()

    child_status: int | None = None
    tap_failed = False
    tap_exited_before_stop = False
    trace_valid = False

    # Install signal handlers before tap Popen. This prevents an INT/TERM in
    # the start_tap -> relay-enter window from orphaning the new proxy.
    relay_context = nullcontext(relay) if relay is not None else SignalRelay()
    with relay_context as active_relay:
        retry_proxy = RetryProxy(
            TAP_TARGET,
            exp_dir / "run/venus-retry.jsonl",
        )
        retry_proxy.start()
        try:
            tap = start_tap(tap_bin, exp_dir, environ, retry_proxy.url)
            try:
                port = wait_ready(
                    tap,
                    exp_dir / "run/.claude-tap.log",
                    ready_timeout,
                    lambda: active_relay.cancelled,
                )
                if port is not None:
                    tap_url = f"http://127.0.0.1:{port}/v1"
                    child_env = build_sandbox_environment(
                        environ,
                        tap_url,
                    )
                    gate_channel = (
                        NestedGateChannel(sidecar.capability_dir)
                        if sidecar is not None
                        else None
                    )
                    try:
                        workload = subprocess.Popen(
                            bwrap_argv,
                            # The fixed gate inherits these redirections and then
                            # execs the Agent in the same process and namespaces.
                            stdin=None,
                            stdout=None,
                            stderr=None,
                            env=child_env,
                            start_new_session=True,
                        )
                        active_relay.attach(workload)
                        try:
                            if gate_channel is not None:
                                proof = gate_channel.receive(
                                    lambda: active_relay.cancelled
                                )
                                sidecar.record_nested_gate(proof)
                                if active_relay.cancelled:
                                    raise PilotError(
                                        "nested-gate release interrupted"
                                    )
                                gate_channel.release()
                            state.workload_started = True
                            child_status, tap_failed = wait_workload(
                                workload,
                                tap,
                                sidecar,
                            )
                        except Exception as exc:
                            print(
                                "pilot-runner: nested Browser Gate failed "
                                f"({type(exc).__name__})",
                                file=sys.stderr,
                            )
                            child_status = 1
                            tap_failed = True
                            signal_process_group(workload, signal.SIGTERM)
                            try:
                                workload.wait(timeout=2)
                            except subprocess.TimeoutExpired:
                                signal_process_group(workload, signal.SIGKILL)
                                workload.wait(timeout=2)
                        finally:
                            active_relay.detach()
                    finally:
                        if gate_channel is not None:
                            try:
                                gate_channel.close()
                            except PilotError as exc:
                                print(f"pilot-runner: {exc}", file=sys.stderr)
                                tap_failed = True
            finally:
                tap_exited_before_stop = tap.poll() is not None
                try:
                    stop_tap(tap, stop_timeout)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    print(
                        f"warning: failed to stop claude-tap: {exc}",
                        file=sys.stderr,
                    )
                    tap_failed = True
        finally:
            try:
                retry_proxy.stop()
            except OSError as exc:
                print(
                    f"warning: failed to stop Venus retry proxy: {exc}",
                    file=sys.stderr,
                )
                tap_failed = True

        try:
            session_id, session_status, record_count = read_trace(exp_dir)
        except TapError as exc:
            print(f"pilot-runner: {exc}", file=sys.stderr)
        else:
            trace_valid = session_status in FINAL_SESSION_STATUSES
            if not trace_valid:
                print(
                    f"pilot-runner: trace session remains "
                    f"{session_status!r}",
                    file=sys.stderr,
                )
            elif child_status == 0 and record_count == 0:
                # A zero-request trace cannot prove that successful Codex
                # traffic actually passed through the mandatory proxy.
                print(
                    "pilot-runner: successful Codex run captured no requests",
                    file=sys.stderr,
                )
                trace_valid = False
            export_html(tap_bin, exp_dir, session_id, environ)

        # Preserve the public priority explicitly: caller signal first, then
        # mandatory tap/trace health, then the workload's own status.
        if active_relay.signum is not None:
            return 128 + active_relay.signum
        if tap_failed or tap_exited_before_stop or not trace_valid:
            return 1
        if child_status is None:
            return 1
        return child_status


def run_git(exp_dir: Path, argv: list[str], *, check: bool = True) -> int:
    """Run one quiet Git command in EXP_DIR and return its status."""

    try:
        result = subprocess.run(
            ["git", *argv],
            cwd=exp_dir,
            check=check,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PilotError(f"git {' '.join(argv)} failed: {exc}") from exc
    return result.returncode


def prepare_exp(exp_dir: Path) -> None:
    """Create the experiment Git repository and deterministic ignore contract."""

    try:
        exp_dir.mkdir(parents=True, exist_ok=True)
        (exp_dir / "run").mkdir(exist_ok=True)
        (exp_dir / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    except OSError as exc:
        raise PilotError(f"cannot prepare EXP_DIR: {exc}") from exc
    if not (exp_dir / ".git").is_dir():
        run_git(exp_dir, ["init", "--quiet"])
    run_git(exp_dir, ["config", "user.name", "pilot"])
    run_git(exp_dir, ["config", "user.email", "pilot@localhost"])
    if run_git(exp_dir, ["rev-parse", "--verify", "HEAD"], check=False) != 0:
        run_git(exp_dir, ["add", ".gitignore"])
        run_git(
            exp_dir,
            [
                "commit",
                "--quiet",
                "-m",
                "pilot: initial commit",
            ],
        )


def validate_workspace_delivery(exp_dir: Path) -> dict[str, object]:
    """Validate canonical Workspace authority and return its Final Delivery."""

    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(WORKSPACE_HELPER),
                "validate",
                "--workspace",
                str(exp_dir),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PilotError(f"cannot validate canonical Workspace: {exc}") from exc
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PilotError("canonical Workspace validator returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise PilotError("canonical Workspace validator returned a non-object")
    if completed.returncode != 0 or payload.get("ok") is not True:
        error = payload.get("error")
        classification = (
            error.get("classification") if isinstance(error, dict) else "invalid_workspace"
        )
        detail = error.get("detail") if isinstance(error, dict) else "validation failed"
        raise PilotError(f"canonical Workspace validation failed ({classification}): {detail}")
    graph = payload.get("graph")
    if not isinstance(graph, dict):
        raise PilotError("canonical Workspace validator returned no graph")
    delivery = graph.get("final_delivery")
    if not isinstance(delivery, dict):
        raise PilotError("canonical Workspace has no complete Final Delivery")
    return delivery


def write_artifact_manifest(
    exp_dir: Path,
    workload_status: int,
    final_status: int,
) -> None:
    """Atomically inventory every persistent experiment file."""

    files = []
    try:
        for path in exp_dir.rglob("*"):
            relative = path.relative_to(exp_dir)
            if (
                not relative.parts
                or relative.parts[0] in MANIFEST_EXCLUDED_ROOTS
                or any(
                    relative.as_posix() == prefix
                    or relative.as_posix().startswith(prefix + "/")
                    for prefix in MANIFEST_EXCLUDED_PREFIXES
                )
                or relative.as_posix()
                in {"artifact_manifest.json", ".artifact_manifest.json.tmp"}
                or not path.is_file()
            ):
                continue
            files.append(
                {
                    "path": relative.as_posix(),
                    "size_bytes": path.stat().st_size,
                }
            )
        payload = {
            "schema_version": 1,
            "workload_status": workload_status,
            "final_status": final_status,
            "files": sorted(files, key=lambda item: item["path"]),
        }
        temporary = exp_dir / ".artifact_manifest.json.tmp"
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(exp_dir / "artifact_manifest.json")
    except OSError as exc:
        raise PilotError(f"cannot publish artifact manifest: {exc}") from exc


def publish_artifact_manifest(
    exp_dir: Path,
    workload_status: int,
    final_status: int,
) -> bool:
    """Publish the manifest, returning false after an operator-facing warning."""

    try:
        write_artifact_manifest(exp_dir, workload_status, final_status)
    except PilotError as exc:
        print(f"pilot-runner: {exc}", file=sys.stderr)
        return False
    return True


def cleanup_sandbox(exp_dir: Path) -> None:
    """Remove the deterministic isolated Codex home if present."""

    for name in ("run/.codex-upper",):
        path = exp_dir / name
        try:
            if path.exists():
                shutil.rmtree(path)
        except OSError as exc:
            raise PilotError(f"cannot remove {path}: {exc}") from exc


def finalize_pilot(
    exp_dir: Path,
    workload_status: int,
    environ: Mapping[str, str],
    *,
    require_rollout: bool = True,
) -> int:
    """Collect the unique rollout, apply cleanup policy, and choose final status."""

    upper = exp_dir / "run/.codex-upper"
    signal_status = workload_status in {
        128 + signal.SIGINT,
        128 + signal.SIGTERM,
    }
    if not require_rollout:
        final_status = workload_status
        if not publish_artifact_manifest(
            exp_dir,
            workload_status,
            final_status,
        ) and workload_status == 0:
            final_status = ARTIFACT_CONTRACT_STATUS
        if upper.exists():
            print(
                f"sandbox preserved at {upper} (exit={final_status})",
                file=sys.stderr,
            )
        return final_status

    rollouts = sorted(upper.glob("sessions/*/*/*/rollout-*.jsonl"))
    if len(rollouts) != 1:
        print(
            f"expected exactly 1 rollout under {upper}, found {len(rollouts)}",
            file=sys.stderr,
        )
        print(f"sandbox preserved for postmortem at {upper}", file=sys.stderr)
        final_status = workload_status if signal_status else 3
        publish_artifact_manifest(exp_dir, workload_status, final_status)
        return final_status
    try:
        rollouts[0].replace(exp_dir / "run/rollout.jsonl")
    except OSError as exc:
        print(f"cannot collect rollout: {exc}", file=sys.stderr)
        print(f"sandbox preserved for postmortem at {upper}", file=sys.stderr)
        final_status = workload_status if signal_status else 3
        publish_artifact_manifest(exp_dir, workload_status, final_status)
        return final_status

    final_status = workload_status
    if workload_status == 0:
        try:
            validate_workspace_delivery(exp_dir)
        except PilotError as exc:
            print(f"pilot-runner: {exc}", file=sys.stderr)
            final_status = ARTIFACT_CONTRACT_STATUS
    if not publish_artifact_manifest(exp_dir, workload_status, final_status):
        if workload_status == 0:
            final_status = ARTIFACT_CONTRACT_STATUS

    if final_status == 0 and not environ.get("KEEP_STATE"):
        try:
            cleanup_sandbox(exp_dir)
        except PilotError as exc:
            print(f"pilot-runner: {exc}", file=sys.stderr)
            final_status = 1
            print(
                f"sandbox cleanup incomplete at {upper}",
                file=sys.stderr,
            )
            if not publish_artifact_manifest(
                exp_dir,
                workload_status,
                final_status,
            ):
                try:
                    (exp_dir / "artifact_manifest.json").unlink(missing_ok=True)
                except OSError as unlink_exc:
                    print(
                        f"warning: cannot remove stale manifest: {unlink_exc}",
                        file=sys.stderr,
                    )
    else:
        print(
            f"sandbox preserved at {upper} (exit={final_status})",
            file=sys.stderr,
        )
        print(
            f"clean when done: {Path(__file__)} clean {str(exp_dir)!r}",
            file=sys.stderr,
        )
    return final_status


def sidecar_receipt_succeeded(receipt: object) -> bool:
    """Accept only the exact proof-only successful Sidecar receipt."""

    keys = {
        "schema",
        "status",
        "imageId",
        "imageSourceRevision",
        "brokerImageId",
        "brokerImageSourceRevision",
        "brokerBaseImageId",
        "programs",
        "predicates",
        "counts",
        "failureCheck",
        "retryAllowed",
    }
    if not isinstance(receipt, dict) or set(receipt) != keys:
        return False
    predicates = receipt.get("predicates")
    counts = receipt.get("counts")
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("status") != "succeeded"
        or receipt.get("imageId") != IMAGE_ID
        or receipt.get("imageSourceRevision") != IMAGE_SOURCE_REVISION
        or receipt.get("brokerImageId") != BROKER_IMAGE_ID
        or receipt.get("brokerImageSourceRevision") != BROKER_IMAGE_SOURCE_REVISION
        or receipt.get("brokerBaseImageId") != BROKER_BASE_IMAGE_ID
        or receipt.get("programs") != PROGRAMS
        or not isinstance(predicates, dict)
        or set(predicates) != set(RECEIPT_PREDICATES)
        or any(value is not True for value in predicates.values())
        or not isinstance(counts, dict)
        or set(counts) != {"acceptedRequests", "freshContexts", "programCounts"}
        or receipt.get("failureCheck") is not None
        or receipt.get("retryAllowed") is not False
    ):
        return False
    accepted = counts.get("acceptedRequests")
    fresh = counts.get("freshContexts")
    program_counts = counts.get("programCounts")
    return (
        isinstance(accepted, int)
        and not isinstance(accepted, bool)
        and isinstance(fresh, int)
        and not isinstance(fresh, bool)
        and isinstance(program_counts, dict)
        and set(program_counts) == {"residual", "viewer"}
        and all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 1
            for value in program_counts.values()
        )
        and accepted == sum(program_counts.values())
        and fresh == accepted + 1
    )


def run_pilot(
    exp_dir: Path,
    input_paths: list[Path],
    command: list[str],
    environ: Mapping[str, str],
) -> int:
    """Prepare, supervise, and finalize one complete pilot transaction."""

    exp_dir = validate_exp_dir(REPO_ROOT, exp_dir)
    prepare_exp(exp_dir)
    state = LifecycleState()
    relative_exp = exp_dir.relative_to(REPO_ROOT.resolve())
    sandbox_exp = SANDBOX_REPO_ROOT / relative_exp
    job_id = "pilot-" + hashlib.sha256(
        relative_exp.as_posix().encode("utf-8")
    ).hexdigest()[:24]
    workload_status = 1
    sidecar: BrowserSidecarJob | None = None
    with SignalRelay() as relay:
        try:
            sidecar = BrowserSidecarJob(
                exp_dir,
                sandbox_exp,
                job_id=job_id,
                cancelled=lambda: relay.cancelled,
            )
            prepare_nested_browser_gate(
                REPO_ROOT,
                exp_dir,
                input_paths,
                environ,
                sidecar,
            )
            if relay.cancelled:
                raise PilotError("nested Browser Gate preparation was interrupted")
            sidecar.start()
            if relay.cancelled:
                workload_status = 128 + (relay.signum or signal.SIGTERM)
            else:
                workload_status = run_supervised(
                    exp_dir,
                    input_paths,
                    command,
                    environ,
                    state,
                    sidecar,
                    relay,
                )
        except (OSError, PilotError, TapError, subprocess.SubprocessError) as exc:
            print(f"pilot-runner: {exc}", file=sys.stderr)
            workload_status = (
                128 + (relay.signum or signal.SIGTERM)
                if relay.cancelled
                else 1
            )
        except Exception as exc:
            print(
                f"pilot-runner: Browser Sidecar failed ({type(exc).__name__})",
                file=sys.stderr,
            )
            workload_status = (
                128 + (relay.signum or signal.SIGTERM)
                if relay.cancelled
                else 1
            )
        finally:
            if sidecar is None:
                sidecar_receipt: Mapping[str, object] = {
                    "schema": "meshshot.browser-sidecar.job-receipt/2",
                    "status": "failed",
                }
            else:
                try:
                    sidecar_receipt = sidecar.close(workload_status=workload_status)
                except Exception:
                    sidecar_receipt = {
                        "schema": "meshshot.browser-sidecar.job-receipt/2",
                        "status": "failed",
                    }
            if not sidecar_receipt_succeeded(sidecar_receipt):
                print(
                    "pilot-runner: Browser Sidecar terminal receipt failed",
                    file=sys.stderr,
                )
                if not relay.cancelled:
                    workload_status = 1
    return finalize_pilot(
        exp_dir,
        workload_status,
        environ,
        require_rollout=state.workload_started,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the run and postmortem-cleanup command surfaces."""

    parser = argparse.ArgumentParser(
        description="Run or clean one mandatory-tap pilot"
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--input", action="append", type=Path, required=True)
    run_parser.add_argument("exp_dir", type=Path)
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    clean_parser = subparsers.add_parser("clean")
    clean_parser.add_argument("exp_dir", type=Path)
    args = parser.parse_args(argv)
    if args.action == "run":
        if args.command and args.command[0] == "--":
            args.command = args.command[1:]
        if not args.command:
            run_parser.error("missing workload after --")
    return args


def main(argv: list[str] | None = None) -> int:
    """Convert preparation/finalization failures to an operator-facing status."""

    args = parse_args(argv)
    try:
        exp_dir = validate_exp_dir(REPO_ROOT, args.exp_dir)
        if args.action == "clean":
            cleanup_sandbox(exp_dir)
            return 0
        return run_pilot(
            exp_dir,
            args.input,
            args.command,
            dict(os.environ),
        )
    except PilotError as exc:
        print(f"pilot-runner: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
