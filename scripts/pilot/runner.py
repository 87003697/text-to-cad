#!/usr/bin/env python3
"""Run one complete pilot transaction through mandatory claude-tap."""

from __future__ import annotations

import argparse
from collections import namedtuple
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import signal
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
    import fcntl
except ImportError:  # pragma: no cover - unsupported publication platform.
    fcntl = None


TerminalValidationLocator = namedtuple(
    "TerminalValidationLocator", "bundle_path expected_identity sidecar_path"
)

PILOT_SCRIPT_DIR = Path(__file__).resolve().parent
if str(PILOT_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(PILOT_SCRIPT_DIR))

try:
    from scripts.pilot.venus_retry_proxy import RetryProxy
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from venus_retry_proxy import RetryProxy

try:
    from scripts.pilot import plugin_deployment
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    import plugin_deployment  # type: ignore[no-redef]

try:
    from scripts.pilot.workspace_supervisor import (
        SupervisorError,
        WorkspaceSupervisor,
        _load_workspace_api,
    )
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from workspace_supervisor import (
        SupervisorError,
        WorkspaceSupervisor,
        _load_workspace_api,
    )

try:
    from scripts.pilot.step_zero_evidence import (
        _MESHSCOPE_SRC,
        _MESHSHOT_SRC,
        _ensure_shipped_package,
        real_step_zero_evidence_provider,
    )
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from step_zero_evidence import (  # type: ignore[no-redef]
        _MESHSCOPE_SRC,
        _MESHSHOT_SRC,
        _ensure_shipped_package,
        real_step_zero_evidence_provider,
    )

try:
    from scripts.pilot.trusted_tools import TrustedToolsError, validate_trusted_tools
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from trusted_tools import TrustedToolsError, validate_trusted_tools  # type: ignore[no-redef]

try:
    from scripts.pilot.repair_evidence import real_repair_evidence_provider
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from repair_evidence import real_repair_evidence_provider

try:
    from scripts.pilot.agent_surface_bridge import (
        AgentSurfaceBridge,
        SOCKET_TARGET as AGENT_SURFACE_SOCKET_TARGET,
    )
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from agent_surface_bridge import (
        AgentSurfaceBridge,
        SOCKET_TARGET as AGENT_SURFACE_SOCKET_TARGET,
    )

try:
    from scripts.pilot import agent_source_projection
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    import agent_source_projection  # type: ignore[no-redef]

try:
    from scripts.pilot.candidate_runtime import (
        CAD_RUNTIME_IMPORTS,
        CandidateRuntimeLease,
        CandidateRuntimeError,
        materialize_candidate_runtime,
        validate_candidate_runtime,
    )
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from candidate_runtime import (  # type: ignore[no-redef]
        CAD_RUNTIME_IMPORTS,
        CandidateRuntimeLease,
        CandidateRuntimeError,
        materialize_candidate_runtime,
        validate_candidate_runtime,
    )

sys.path.insert(
    0,
    os.fspath(
        Path(__file__).resolve().parents[2] / "packages/browser_runtime/src"
    ),
)
from browser_runtime import (
    BROWSER_RUNTIME_CONTRACT,
    HOST_IMAGE_LOCK_PATH,
    SANDBOX_CODEX_CONFIG_NAME,
    SANDBOX_CODEX_CONFIG_PATH,
    SANDBOX_MOUNT_ROOT,
    BrowserRuntimeError,
    BrowserRuntimeJob,
    render_mcp_config,
)


READY_PATTERN = re.compile(r"listening on http://127\.0\.0\.1:(\d+)")
FINAL_SESSION_STATUSES = {"complete", "error", "empty"}
REQUIRED_TAP_VERSION = "0.1.140"
TAP_TARGET = "http://v2.open.venus.oa.com/llmproxy/v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_REPO_ROOT = Path("/workspace/repo")
SANDBOX_HOME = Path("/home/pilot")
SANDBOX_CODEX_HOME = SANDBOX_HOME / ".codex"
SANDBOX_PUBLISH_TREE = Path(plugin_deployment.SANDBOX_MARKETPLACE_SOURCE)
JOB_CODEX_HOME_REL = "run/.codex-home"
JOB_PUBLISH_TREE_REL = "run/.plugin-publish-tree"
ARTIFACT_CONTRACT_STATUS = 4
MANIFEST_EXCLUDED_ROOTS = {".git"}
MANIFEST_EXCLUDED_PREFIXES = {JOB_CODEX_HOME_REL, JOB_PUBLISH_TREE_REL}
TERMINAL_LOCATOR_RELATIVE = "run/terminal-validation-locator.json"
TERMINAL_PUBLISH_LOCK_SECONDS = 120.0
WORKSPACE_HELPER = REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace"
CAD_REBUILD_ENTRYPOINT = REPO_ROOT / "skills/cad/scripts/canonical-build/__main__.py"
GEOMETRY_ENTRYPOINT = REPO_ROOT / "skills/mesh-compare/scripts/mesh-compare/__main__.py"
SANDBOX_CAD_REBUILD_ENTRYPOINT = (
    SANDBOX_REPO_ROOT / "skills/cad/scripts/canonical-build/__main__.py"
)
SANDBOX_GEOMETRY_ENTRYPOINT = (
    SANDBOX_REPO_ROOT / "skills/mesh-compare/scripts/mesh-compare/__main__.py"
)
VIEWER_RUNTIME_DIR = REPO_ROOT / "skills/cad-viewer/scripts/viewer"
TRUSTED_TOOL_REGISTRY_NAME = "trusted-tool-registry.json"
CAD_CANDIDATE_RUNTIME_IMPORTS = CAD_RUNTIME_IMPORTS
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


def _workspace_json_bytes(value: Mapping[str, object]) -> bytes:
    """Encode one Workspace-compatible canonical JSON document."""

    return (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


def publish_tool_registry(authority_dir: Path) -> Path:
    """Publish the runner-owned finalization registry beside runtime authority."""

    for path in (CAD_REBUILD_ENTRYPOINT, GEOMETRY_ENTRYPOINT):
        if not path.is_file() or path.is_symlink():
            raise PilotError("trusted tool entrypoint is unavailable")
    value: dict[str, object] = {
        "schema": "mesh-to-cad.tool-registry/2",
        "rebuild": {
            "id": "cad.canonical-build/1",
            "entrypoint": str(SANDBOX_CAD_REBUILD_ENTRYPOINT),
            "entrypoint_sha256": hashlib.sha256(
                CAD_REBUILD_ENTRYPOINT.read_bytes()
            ).hexdigest(),
        },
        "geometry": {
            "id": "mesh-compare.voxblame/1",
            "entrypoint": str(SANDBOX_GEOMETRY_ENTRYPOINT),
            "entrypoint_sha256": hashlib.sha256(
                GEOMETRY_ENTRYPOINT.read_bytes()
            ).hexdigest(),
        },
    }
    value["identity_sha256"] = hashlib.sha256(
        b"mesh-to-cad.tool-registry/2\0" + _workspace_json_bytes(value)
    ).hexdigest()
    target = Path(authority_dir) / TRUSTED_TOOL_REGISTRY_NAME
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o400,
        )
        try:
            payload = memoryview(_workspace_json_bytes(value))
            while payload:
                written = os.write(descriptor, payload)
                if written <= 0:
                    raise OSError("short trusted tool registry write")
                payload = payload[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(temporary, 0o444)
        os.replace(temporary, target)
        return target
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise PilotError("cannot publish trusted tool registry") from exc


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
    sidecar: BrowserRuntimeJob | None = None,
) -> tuple[int, bool]:
    """Wait while failing closed if mandatory tap or browser runtime exits."""

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
                    "pilot-runner: browser runtime container exited during workload",
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


def prepare_job_codex_home(
    exp_dir: Path,
    receipt: plugin_deployment.DeploymentReceipt,
    *,
    browser_mcp_url: str | None = None,
) -> Path:
    """Materialize a job-private writable CODEX_HOME from the plugin authority.

    Deep-copies the verified authority ``codex_home`` (config.toml with the
    marketplace + plugin registration and the installed plugin cache) into a
    per-experiment directory, rewrites the marketplace source to the stable
    in-sandbox path, and merges the browser MCP fragment when supplied. The
    materialization is manifest-verified against the authority receipt so a
    torn copy cannot silently degrade the pilot.
    """

    target = exp_dir / JOB_CODEX_HOME_REL
    try:
        if target.exists():
            shutil.rmtree(target)
        (target.parent).mkdir(parents=True, exist_ok=True)
        extra_toml = (
            render_mcp_config(browser_mcp_url) if browser_mcp_url is not None else None
        )
        plugin_deployment.materialize_job_codex_home(
            receipt,
            target,
            extra_toml=extra_toml,
        )
    except plugin_deployment.PluginAuthorityError as exc:
        raise PilotError(
            f"cannot materialize job CODEX_HOME: {exc}"
        ) from exc
    except OSError as exc:
        raise PilotError(f"cannot prepare sandbox state: {exc}") from exc
    return target


def prepare_job_publish_tree(
    exp_dir: Path,
    receipt: plugin_deployment.DeploymentReceipt,
) -> Path:
    """Rebuild the verified job-private publish snapshot for one pilot run."""

    target = exp_dir / JOB_PUBLISH_TREE_REL
    try:
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
        return plugin_deployment.materialize_job_publish_tree(receipt, target)
    except plugin_deployment.PluginAuthorityError as exc:
        raise PilotError(f"cannot materialize job publish tree: {exc}") from exc
    except OSError as exc:
        raise PilotError(f"cannot prepare job publish tree: {exc}") from exc


def prepare_isolated_job_codex_home(exp_dir: Path) -> Path:
    """Materialize a minimal writable CODEX_HOME for a candidate-only Agent.

    A candidate-only Agent runs Codex with ``--disable plugins``; provider
    config is injected via the gateway's ``-c`` flags. The Codex CLI still
    requires a writable ``CODEX_HOME`` (for rollouts, sessions, and the
    conversation log), so we allocate an empty per-job directory with a
    minimal ``config.toml``. No plugin authority, marketplace, or installed
    skill cache is materialized here — the Agent Source Projection is the
    only skill source visible to this Agent Execution.
    """

    target = exp_dir / JOB_CODEX_HOME_REL
    try:
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir(mode=0o700)
        config_path = target / plugin_deployment.CONFIG_TOML_NAME
        config_path.write_text(
            "# Candidate-only Agent Execution CODEX_HOME.\n"
            "# The gateway supplies provider config via -c flags; no\n"
            "# marketplaces or plugins are registered here.\n",
            encoding="utf-8",
        )
        config_path.chmod(0o600)
    except OSError as exc:
        raise PilotError(f"cannot prepare candidate CODEX_HOME: {exc}") from exc
    return target


def prepare_agent_source_projection(repo_root: Path) -> Path:
    """Return the verified Agent Source Projection root under ``repo_root``.

    The projection is materialized offline by ``scripts/bundle/bundle.sh``.
    The runner never regenerates it; it only verifies the checked-in tree
    matches the canonical manifest embedded in the projection and the source
    used to build it. Any drift fails closed here so an isolated Agent
    Execution cannot start against a stale, torn, or tampered projection.
    """

    target = Path(repo_root) / agent_source_projection.PROJECTION_ROOT_REL
    try:
        agent_source_projection.verify_matches_source(repo_root, target)
    except agent_source_projection.ProjectionError as exc:
        raise PilotError(
            f"agent source projection is unavailable: {exc}"
        ) from exc
    return target


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


def resolve_deployed_authority(host_home: Path) -> plugin_deployment.DeploymentReceipt:
    """Return the current plugin-authority receipt or fail closed.

    Reads ``current.json`` under ``<host_home>/.text-to-cad-codex/deployments/``
    through :mod:`plugin_deployment` so every pilot consumes the exact bytes
    that the shipped ``cvm_install_plugin.py`` installed and verified through
    the real Codex plugin CLI. The receipt drives both the sandbox mount
    layout (whole authority ``codex-home`` at ``SANDBOX_CODEX_HOME`` + publish
    tree at the marketplace path) and the legacy per-skill script mounts under
    ``SANDBOX_REPO_ROOT/skills``. Any missing authority, digest mismatch, or
    path escape raises :class:`PilotError` — consumers must not fall back to
    legacy ``~/.codex/skills`` symlinks.
    """

    try:
        return plugin_deployment.resolve_current_authority(host_home)
    except plugin_deployment.PluginAuthorityError as exc:
        raise PilotError(
            "no valid plugin-authority pointer for CVM Codex; "
            "publish one via scripts/pilot/cvm-push.sh before running a pilot: "
            f"{exc}"
        ) from exc


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
    *,
    isolated_agent: bool = False,
) -> dict[str, str]:
    """Return the explicit child environment allowlist for Codex.

    Provider credentials stay in the host-side tap/retry processes; the
    Agent child receives no bearer token or other secret environment value.
    """

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
                "/usr/local/bin:/usr/bin:/bin"
                if isolated_agent
                else f"{SANDBOX_REPO_ROOT}/.venv/bin:/usr/local/bin:/usr/bin:/bin"
            ),
            "PYTHONDONTWRITEBYTECODE": "1",
            "UV_CACHE_DIR": "/tmp/uv-cache",
            "XDG_CACHE_HOME": "/tmp/cache",
        }
    )
    return child_env



def build_bwrap_argv(
    repo_root: Path,
    exp_dir: Path,
    input_paths: list[Path],
    workload: list[str],
    environ: Mapping[str, str],
    browser_capability_dir: Path | None = None,
    browser_mcp_url: str | None = None,
    agent_candidate_dir: Path | None = None,
    agent_surface_socket: Path | None = None,
) -> list[str]:
    """Build a least-visibility bwrap argv without placing secrets in it.

    ``agent_candidate_dir`` selects the W4 candidate-only seam.  In that mode
    the workload receives a fixed ``/candidate`` mount, the Agent Source
    Projection at ``/workspace/repo/skills``, the projected Agent Surface
    client at ``/agent-surface/client.py``, and no experiment, input, or
    output bind.  The default remains the historical pilot mount until the
    Agent Surface workflow is enabled by the outer runner.
    """

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
    host_home = Path(host_home_value)

    exp_dir = validate_exp_dir(repo_root, exp_dir)
    inputs = validate_input_paths(repo_root, input_paths)
    isolated_agent = agent_candidate_dir is not None
    if isolated_agent:
        raw_candidate_dir = Path(agent_candidate_dir)
        if raw_candidate_dir.is_symlink():
            raise PilotError("agent candidate directory is unavailable")
        agent_candidate_dir = raw_candidate_dir.resolve()
        if not agent_candidate_dir.is_dir():
            raise PilotError("agent candidate directory is unavailable")
        try:
            agent_candidate_dir.relative_to(exp_dir)
        except ValueError:
            pass
        else:
            raise PilotError("agent candidate directory must be outside EXP_DIR")
        if agent_surface_socket is None:
            raise PilotError("Agent Surface bridge is unavailable")
        if (
            not Path(agent_surface_socket).is_socket()
            or Path(agent_surface_socket).is_symlink()
        ):
            raise PilotError("Agent Surface bridge is unavailable")
    elif agent_surface_socket is not None:
        raise PilotError("Agent Surface requires candidate-only isolation")
    relative_exp = exp_dir.relative_to(repo_root)
    sandbox_exp = SANDBOX_REPO_ROOT / relative_exp
    gateway = repo_root / "gateway" / "codex-tap-gpt56"
    if not gateway.is_file():
        raise PilotError(f"gateway not found: {gateway}")
    venv = repo_root / ".venv"
    if not venv.is_dir():
        raise PilotError(f"pilot runtime not found: {venv}")

    if isolated_agent:
        # Candidate-only Agent Executions see only the Agent Source Projection
        # under /workspace/repo/skills, the fixed Agent Surface client (also
        # sourced from the projection) at /agent-surface/client.py, the
        # Agent Surface socket, /candidate, and a minimal writable CODEX_HOME.
        # No full installed plugin cache, publish tree, per-skill
        # enumeration, Workspace Authority path, or repository client source
        # is bound into the sandbox.
        projection_root = prepare_agent_source_projection(repo_root)
        projection_skills = agent_source_projection.projected_skills_root(
            projection_root
        )
        projected_client = agent_source_projection.projected_agent_surface_client(
            projection_root
        )
        if not projected_client.is_file() or projected_client.is_symlink():
            raise PilotError("Agent Surface client is unavailable")
        job_codex_home = prepare_isolated_job_codex_home(exp_dir)
        job_publish_tree = None
        skill_dirs: tuple[Path, ...] = ()
    else:
        receipt = resolve_deployed_authority(host_home)
        job_codex_home = prepare_job_codex_home(
            exp_dir, receipt, browser_mcp_url=browser_mcp_url
        )
        job_publish_tree = prepare_job_publish_tree(exp_dir, receipt)
        installed_relative = Path(receipt.installed_path).relative_to(
            receipt.codex_home
        )
        skill_dirs = tuple(
            plugin_deployment.skill_directories_under_installed(
                job_codex_home / installed_relative
            )
        )
        projection_skills = None

    if browser_capability_dir is not None:
        browser_capability_dir = browser_capability_dir.resolve()
        if not browser_capability_dir.is_dir():
            raise PilotError("browser runtime capability directory is unavailable")
        try:
            browser_capability_relative = browser_capability_dir.relative_to(exp_dir)
        except ValueError as exc:
            raise PilotError(
                "browser runtime capability directory must be inside the experiment"
            ) from exc
        sandbox_browser_capability_dir = (
            Path(SANDBOX_MOUNT_ROOT)
            if isolated_agent
            else sandbox_exp / browser_capability_relative
        )
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
        str(SANDBOX_REPO_ROOT / "gateway"),
        "--dir",
        str(SANDBOX_REPO_ROOT / "skills"),
        "--dir",
        "/home",
        "--dir",
        "/opt",
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
        str(gateway),
        str(SANDBOX_REPO_ROOT / "gateway" / gateway.name),
        "--bind",
        str(job_codex_home),
        str(SANDBOX_CODEX_HOME),
    ]
    if job_publish_tree is not None:
        argv.extend(
            [
                "--ro-bind",
                str(job_publish_tree),
                str(SANDBOX_PUBLISH_TREE),
            ]
        )
    if not isolated_agent:
        argv.extend(("--dir", str(sandbox_exp.parent)))
    if not isolated_agent:
        argv.extend(
            (
                "--ro-bind",
                str(venv),
                str(SANDBOX_REPO_ROOT / ".venv"),
            )
        )
    for path in existing_system_paths():
        argv.extend(["--ro-bind", str(path), str(path)])
    if isolated_agent:
        argv.extend(
            [
                "--ro-bind",
                str(projection_skills),
                str(SANDBOX_REPO_ROOT / "skills"),
                "--dir",
                "/candidate",
                "--bind",
                str(agent_candidate_dir),
                "/candidate",
                "--dir",
                "/agent-surface",
                "--ro-bind",
                str(projected_client),
                "/agent-surface/client.py",
                "--ro-bind",
                str(agent_surface_socket),
                AGENT_SURFACE_SOCKET_TARGET,
            ]
        )
    else:
        argv.extend(
            [
                "--bind",
                str(exp_dir),
                str(sandbox_exp),
            ]
        )
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
                ]
            )
    if browser_capability_dir is not None:
        argv.extend(["--ro-bind", str(browser_capability_dir), str(sandbox_browser_capability_dir)])
        if not isolated_agent:
            argv.extend(["--ro-bind", str(browser_capability_dir), SANDBOX_MOUNT_ROOT])
    argv.extend(
        [
            "--remount-ro",
            "/",
            "--share-net",
            "--die-with-parent",
            "--chdir",
            str(SANDBOX_REPO_ROOT),
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
    sidecar: BrowserRuntimeJob | None = None,
    relay: SignalRelay | None = None,
    agent_candidate_dir: Path | None = None,
    agent_surface_socket: Path | None = None,
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
        sidecar.capability_dir if sidecar is not None else None,
        sidecar.mcp_url if sidecar is not None else None,
        agent_candidate_dir,
        agent_surface_socket,
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
                        isolated_agent=agent_candidate_dir is not None,
                    )
                    workload = subprocess.Popen(
                        bwrap_argv,
                        stdin=None,
                        stdout=None,
                        stderr=None,
                        env=child_env,
                        start_new_session=True,
                    )
                    active_relay.attach(workload)
                    try:
                        state.workload_started = True
                        child_status, tap_failed = wait_workload(
                            workload,
                            tap,
                            sidecar,
                        )
                    finally:
                        active_relay.detach()
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


def _workspace_status_available(exp_dir: Path) -> bool:
    """Ask the public Workspace CLI whether an initialized authority exists."""

    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(WORKSPACE_HELPER),
                "status",
                "--workspace",
                str(exp_dir),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PilotError("cannot inspect Workspace initialization") from exc
    return completed.returncode == 0


def prepare_and_initialize_workspace(exp_dir: Path, input_path: Path) -> Path:
    """Prepare Canonical Reference and initialize a fresh Workspace outside Agent view."""

    if _workspace_status_available(exp_dir):
        return exp_dir / "input" / "reference.ply"
    prepared = exp_dir.parent / f".agent-prepared-{os.getpid()}-{secrets.token_hex(8)}"
    try:
        try:
            validate_trusted_tools(REPO_ROOT)
        except TrustedToolsError as exc:
            raise PilotError("trusted tools are unavailable") from exc
        _ensure_shipped_package(_MESHSCOPE_SRC, "meshscope")
        from meshscope.voxblame import prepare_reference
        _ensure_shipped_package(_MESHSHOT_SRC, "meshshot")
        from meshshot import load_profile

        prepared_input = prepared / "input"
        result = prepare_reference(input_path, prepared_input)
        profile = load_profile()
        (prepared / "setup").mkdir(parents=True, exist_ok=False)
        (prepared / "setup/outer-preparation.json").write_text(
            json.dumps(
                {
                    "schema": "mesh-to-cad.outer-preparation/1",
                    "canonical_reference_sha256": result.manifest[
                        "canonical_reference_sha256"
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        (prepared / "experiment.json").write_text(
            json.dumps(
                {
                    "schema": "mesh-to-cad.experiment/1",
                    "workspace_id": f"pilot-{exp_dir.name}",
                    "coordinate_contract": "trellis2_canonical/1",
                    "canonical_reference_sha256": result.manifest[
                        "canonical_reference_sha256"
                    ],
                    "preview_profile": {
                        "name": profile.profile["name"],
                        "sha256": profile.sha256,
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(WORKSPACE_HELPER),
                "init",
                "--workspace",
                str(exp_dir),
                "--prepared",
                str(prepared),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if completed.returncode != 0:
            raise PilotError("trusted Workspace initialization failed")
        return exp_dir / "input" / "reference.ply"
    except PilotError:
        raise
    except Exception as exc:
        raise PilotError("trusted Canonical Reference preparation failed") from exc
    finally:
        shutil.rmtree(prepared, ignore_errors=True)


def compact_exp_history(exp_dir: Path) -> None:
    """Pack successful pilot commits before cvm-pull preserves the Git authority."""

    try:
        run_git(exp_dir, ["repack", "-Adq"])
    except PilotError as exc:
        print(
            f"pilot-runner: warning: cannot compact experiment Git history: {exc}",
            file=sys.stderr,
        )


def validate_workspace_delivery(exp_dir: Path) -> dict[str, object]:
    """Validate canonical Workspace authority and return its Final Delivery."""

    try:
        workspace_api = _load_workspace_api()
        if workspace_api.workspace_initialized(exp_dir):
            # Terminal validation owns the only complete validator call. The
            # runner's preflight here only distinguishes the public Workspace
            # protocol from legacy/non-Workspace output.
            return {"workspace_initialized": True}
    except Exception:
        pass

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


def _acquire_terminal_publish_lock(exp_dir: Path) -> int:
    """Serialize publication for one experiment on supported POSIX hosts."""

    if fcntl is None:  # Kept explicit for type checkers and patched tests.
        raise PilotError("terminal_publication_unavailable")
    root = exp_dir.parent / ".internal-terminal-validation"
    if root.is_symlink():
        raise PilotError("terminal_handoff_path_conflict")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise PilotError("terminal_handoff_path_conflict")
    lock_path = root / f".{exp_dir.name}.publish.lock"
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise PilotError("terminal_handoff_lock_unavailable") from exc
    deadline = time.monotonic() + TERMINAL_PUBLISH_LOCK_SECONDS
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PilotError("terminal_handoff_lock_conflict")
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise PilotError("terminal_handoff_lock_timeout")
                time.sleep(0.02)
    except Exception:
        os.close(descriptor)
        raise


def _release_terminal_publish_lock(descriptor: int) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _read_terminal_json(path: Path) -> dict[str, object] | None:
    if path.is_symlink():
        raise PilotError("terminal_handoff_path_conflict")
    if not path.exists():
        return None
    if not path.is_file():
        raise PilotError("terminal_handoff_path_conflict")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PilotError("terminal_handoff_invalid") from exc
    if not isinstance(value, dict):
        raise PilotError("terminal_handoff_invalid")
    return value


def _fsync_terminal_parent(path: Path) -> None:
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


def _write_terminal_handoff(
    path: Path,
    payload: Mapping[str, object],
) -> None:
    """Publish a new handoff without replacing a file another publisher owns."""

    if path.exists() or path.is_symlink():
        raise PilotError("terminal_handoff_ownership_conflict")
    encoded = (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(6)}")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    linked = False
    complete = False
    try:
        descriptor = os.open(temporary, flags, 0o600)
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short terminal handoff write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(temporary, path, follow_symlinks=False)
        linked = True
        temporary.unlink()
        _fsync_terminal_parent(path.parent)
        complete = True
    except PilotError:
        raise
    except OSError as exc:
        raise PilotError("terminal_handoff_write_failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        if linked and not complete and path.exists():
            path.unlink()


def _validated_handoff(
    workspace_api: object,
    exp_dir: Path,
    handoff: Mapping[str, object],
) -> tuple[str, Mapping[str, object]]:
    if set(handoff) != {"schema", "terminal_identity_sha256", "bundle"}:
        raise PilotError("terminal_handoff_invalid")
    if handoff.get("schema") != "mesh-to-cad.terminal-validation-handoff/1":
        raise PilotError("terminal_handoff_invalid")
    identity = handoff.get("terminal_identity_sha256")
    bundle = handoff.get("bundle")
    if (
        type(identity) is not str
        or len(identity) != 64
        or any(char not in "0123456789abcdef" for char in identity)
        or not isinstance(bundle, Mapping)
    ):
        raise PilotError("terminal_handoff_invalid")
    try:
        workspace_api.verify_terminal_validation(exp_dir, bundle, identity)  # type: ignore[attr-defined]
    except Exception as exc:
        raise PilotError("terminal_handoff_invalid") from exc
    return identity, bundle


TERMINAL_LOCATOR_SCHEMA = "mesh-to-cad.terminal-validation-locator/2"
TERMINAL_HANDOFF_LAYOUT = "external-sibling-namespace/1"
_TERMINAL_LOCATOR_FIELDS = frozenset({"schema", "handoff_layout"})


def _validate_terminal_marker(locator: Mapping[str, object]) -> None:
    """Ignore locator identity/bundle but reject an incompatible marker."""

    if set(locator) != _TERMINAL_LOCATOR_FIELDS:
        raise PilotError("terminal_locator_conflict")
    if (
        locator.get("schema") != TERMINAL_LOCATOR_SCHEMA
        or locator.get("handoff_layout") != TERMINAL_HANDOFF_LAYOUT
    ):
        raise PilotError("terminal_locator_conflict")


def persist_terminal_validation(exp_dir: Path) -> TerminalValidationLocator | None:
    """Compile and publish the W1/W5 pair after all Agent resources are closed."""

    exp_dir = Path(exp_dir).resolve()
    if os.name != "posix" or fcntl is None or not hasattr(os, "link"):
        raise PilotError("terminal_publication_unavailable")
    try:
        workspace_api = _load_workspace_api()
        if workspace_api.workspace_initialized(exp_dir) is not True:  # type: ignore[attr-defined]
            raise PilotError("workspace_not_initialized")
    except PilotError:
        raise
    except Exception as exc:
        raise PilotError("workspace_not_initialized") from exc

    lock: int | None = None
    handoff_target = exp_dir.parent / ".internal-terminal-validation" / exp_dir.name / "terminal-validation.json"
    locator_target = exp_dir / TERMINAL_LOCATOR_RELATIVE
    created_handoff = False

    def remove_own_handoff() -> None:
        if not created_handoff:
            return
        try:
            handoff_target.unlink(missing_ok=True)
            _fsync_terminal_parent(handoff_target.parent)
        except OSError:
            pass

    try:
        lock = _acquire_terminal_publish_lock(exp_dir)
        handoff_dir = handoff_target.parent
        handoff_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if handoff_dir.is_symlink() or not handoff_dir.is_dir():
            raise PilotError("terminal_handoff_path_conflict")
        handoff = _read_terminal_json(handoff_target)
        reader = getattr(workspace_api, "read_terminal_locator", None)
        locator = reader(exp_dir) if reader is not None else _read_terminal_json(locator_target)
        if handoff is None and locator is not None:
            raise PilotError("terminal_locator_without_handoff")
        locator_payload = {
            "schema": TERMINAL_LOCATOR_SCHEMA,
            "handoff_layout": TERMINAL_HANDOFF_LAYOUT,
        }
        if handoff is not None:
            identity, _bundle = _validated_handoff(workspace_api, exp_dir, handoff)
            if locator is not None:
                _validate_terminal_marker(locator)
                sidecar_path = TERMINAL_LOCATOR_RELATIVE
            else:
                sidecar_path = workspace_api.write_terminal_locator(  # type: ignore[attr-defined]
                    exp_dir, locator_payload
                )
            return TerminalValidationLocator(
                bundle_path=handoff_target,
                expected_identity=identity,
                sidecar_path=sidecar_path,
            )

        compiled = workspace_api.compile_terminal_validation(exp_dir)  # type: ignore[attr-defined]
        if not isinstance(compiled, Mapping):
            raise PilotError("terminal_handoff_invalid")
        bundle = compiled.get("bundle")
        identity = compiled.get("terminal_identity_sha256")
        if (
            not isinstance(bundle, Mapping)
            or type(identity) is not str
            or len(identity) != 64
            or any(char not in "0123456789abcdef" for char in identity)
        ):
            raise PilotError("terminal_handoff_invalid")
        workspace_api.verify_terminal_validation(exp_dir, bundle, identity)  # type: ignore[attr-defined]
        handoff_payload = {
            "schema": "mesh-to-cad.terminal-validation-handoff/1",
            "terminal_identity_sha256": identity,
            "bundle": bundle,
        }
        _write_terminal_handoff(handoff_target, handoff_payload)
        created_handoff = True
        sidecar_path = workspace_api.write_terminal_locator(exp_dir, locator_payload)  # type: ignore[attr-defined]
        return TerminalValidationLocator(
            bundle_path=handoff_target,
            expected_identity=identity,
            sidecar_path=sidecar_path,
        )
    except PilotError:
        remove_own_handoff()
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        remove_own_handoff()
        raise PilotError("cannot persist terminal Workspace validation") from exc
    finally:
        if lock is not None:
            _release_terminal_publish_lock(lock)


def write_agent_bootstrap(candidate_dir: Path, contract: Mapping[str, object]) -> Path:
    """Publish opaque Agent capabilities at the fixed candidate mount."""

    target = Path(candidate_dir) / "bootstrap.json"
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(contract, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise PilotError("cannot publish Agent bootstrap") from exc
    return target


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
                in {
                    "artifact_manifest.json",
                    ".artifact_manifest.json.tmp",
                    TERMINAL_LOCATOR_RELATIVE,
                }
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

    for name in (JOB_CODEX_HOME_REL, JOB_PUBLISH_TREE_REL):
        path = exp_dir / name
        try:
            if path.is_symlink():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        except OSError as exc:
            raise PilotError(f"cannot remove {path}: {exc}") from exc


def finalize_pilot(
    exp_dir: Path,
    workload_status: int,
    environ: Mapping[str, str],
    *,
    require_rollout: bool = True,
    agent_surface: bool = False,
) -> int:
    """Collect the unique rollout, apply cleanup policy, and choose final status."""

    upper = exp_dir / JOB_CODEX_HOME_REL
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
    if workload_status == 0 and not agent_surface:
        try:
            validate_workspace_delivery(exp_dir)
        except PilotError as exc:
            print(f"pilot-runner: {exc}", file=sys.stderr)
            final_status = ARTIFACT_CONTRACT_STATUS
    if final_status == 0:
        compact_exp_history(exp_dir)

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
    elif final_status == 0:
        print(
            f"sandbox preserved at {upper} (exit={final_status})",
            file=sys.stderr,
        )
        print(
            f"clean when done: {Path(__file__)} clean {str(exp_dir)!r}",
            file=sys.stderr,
        )

    if not publish_artifact_manifest(exp_dir, workload_status, final_status):
        if workload_status == 0:
            final_status = ARTIFACT_CONTRACT_STATUS

    if final_status == 0:
        try:
            # The artifact manifest is a later Workspace-root write, so W1
            # must compile only after it is published.  No authority file is
            # written after this handoff succeeds.
            persist_terminal_validation(exp_dir)
        except PilotError as exc:
            if str(exc) == "workspace_not_initialized":
                return final_status
            print(f"pilot-runner: {exc}", file=sys.stderr)
            final_status = ARTIFACT_CONTRACT_STATUS
            publish_artifact_manifest(exp_dir, workload_status, final_status)
    return final_status




def run_pilot(
    exp_dir: Path,
    input_paths: list[Path],
    command: list[str],
    environ: Mapping[str, str],
    agent_candidate_dir: Path | None = None,
    agent_surface: bool = False,
) -> int:
    """Prepare, supervise, and finalize one complete pilot transaction."""

    exp_dir = validate_exp_dir(REPO_ROOT, exp_dir)
    prepare_exp(exp_dir)
    state = LifecycleState()
    workload_status = 1
    sidecar: BrowserRuntimeJob | None = None
    agent_supervisor: WorkspaceSupervisor | None = None
    agent_bridge: AgentSurfaceBridge | None = None
    candidate_runtime: Path | None = None
    candidate_runtime_lease: CandidateRuntimeLease | None = None
    lifetime_confirmed = True
    agent_socket: Path | None = None
    with SignalRelay() as relay:
        try:
            if agent_surface:
                prepare_and_initialize_workspace(exp_dir, input_paths[0])
            sidecar = BrowserRuntimeJob.create(
                exp_dir,
                image_lock_path=HOST_IMAGE_LOCK_PATH,
                viewer_runtime_dir=VIEWER_RUNTIME_DIR,
            )
            sidecar.start()
            tool_registry = publish_tool_registry(sidecar.capability_dir)
            if agent_surface:
                candidate_root = agent_candidate_dir or (
                    exp_dir.parent
                    / f".agent-candidate-{exp_dir.name}-{os.getpid()}-{secrets.token_hex(6)}"
                )
                source_runtime = REPO_ROOT / ".venv"
                runtime_cache = REPO_ROOT / ".cache" / "mesh-to-cad-agent-runtime"
                try:
                    candidate_runtime_lease = materialize_candidate_runtime(
                        source_runtime,
                        runtime_cache,
                        repo_root=REPO_ROOT,
                    )
                    candidate_runtime = candidate_runtime_lease.runtime
                except CandidateRuntimeError as exc:
                    raise PilotError(str(exc)) from exc
                agent_supervisor = WorkspaceSupervisor(
                    exp_dir,
                    bind_reference=True,
                    candidate_root=candidate_root,
                    rebuild_entrypoint=CAD_REBUILD_ENTRYPOINT,
                    geometry_entrypoint=GEOMETRY_ENTRYPOINT,
                    tool_registry=tool_registry,
                    candidate_runtime=candidate_runtime,
                    trusted_tools_root=REPO_ROOT,
                    step_zero_evidence_provider=real_step_zero_evidence_provider,
                    repair_evidence_provider=real_repair_evidence_provider,
                )
                write_agent_bootstrap(
                    agent_supervisor.candidate_root,
                    agent_supervisor.agent_bootstrap_contract(),
                )
                agent_socket = exp_dir.parent / (
                    f".agent-surface-{exp_dir.name}-{os.getpid()}-{secrets.token_hex(6)}.sock"
                )
            if relay.cancelled:
                workload_status = 128 + (relay.signum or signal.SIGTERM)
            else:
                sidecar.preflight()
            if relay.cancelled:
                workload_status = 128 + (relay.signum or signal.SIGTERM)
            else:
                sidecar.preflight_mcp()
            if relay.cancelled:
                workload_status = 128 + (relay.signum or signal.SIGTERM)
            elif agent_supervisor is not None and agent_socket is not None:
                try:
                    agent_bridge = AgentSurfaceBridge(
                        agent_supervisor.agent_surface(), agent_socket
                    )
                    agent_bridge.start()
                except (OSError, RuntimeError) as exc:
                    raise PilotError("cannot start Agent Surface bridge") from exc
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
                    agent_supervisor.candidate_root if agent_supervisor else agent_candidate_dir,
                    agent_socket,
                )
        except (
            OSError,
            PilotError,
            SupervisorError,
            TapError,
            subprocess.SubprocessError,
        ) as exc:
            print(f"pilot-runner: {exc}", file=sys.stderr)
            workload_status = (
                128 + (relay.signum or signal.SIGTERM)
                if relay.cancelled
                else 1
            )
        except BrowserRuntimeError as exc:
            print(f"pilot-runner: browser runtime failed: {exc}", file=sys.stderr)
            workload_status = (
                128 + (relay.signum or signal.SIGTERM)
                if relay.cancelled
                else 1
            )
        finally:
            if agent_bridge is not None:
                try:
                    agent_bridge.stop()
                except (OSError, RuntimeError) as exc:
                    print(
                        f"pilot-runner: Agent Surface cleanup failed: {exc}",
                        file=sys.stderr,
                    )
                    lifetime_confirmed = False
                    if not relay.cancelled:
                        workload_status = workload_status or 1
            if agent_supervisor is not None and lifetime_confirmed:
                try:
                    agent_supervisor.close()
                    if not agent_supervisor.cancellation_confirmed:
                        raise SupervisorError("cancellation_incomplete")
                except SupervisorError as exc:
                    print(
                        "pilot-runner: Agent candidate cleanup failed",
                        file=sys.stderr,
                    )
                    lifetime_confirmed = False
                    if not relay.cancelled:
                        workload_status = workload_status or 1
            if candidate_runtime_lease is not None and lifetime_confirmed:
                try:
                    candidate_runtime_lease.release()
                except (OSError, RuntimeError) as exc:
                    print(
                        f"pilot-runner: candidate runtime lease cleanup failed: {exc}",
                        file=sys.stderr,
                    )
                    lifetime_confirmed = False
                    if not relay.cancelled:
                        workload_status = workload_status or 1
            if sidecar is not None:
                try:
                    sidecar.stop()
                except Exception as exc:
                    print(
                        f"pilot-runner: browser runtime cleanup failed: {exc}",
                        file=sys.stderr,
                    )
                    if not relay.cancelled:
                        workload_status = workload_status or 1
            # A failed Agent bridge/supervisor/runtime lifetime confirmation
            # must prevent terminal publication even when the pilot was
            # interrupted at the same time.
            if not lifetime_confirmed:
                workload_status = workload_status or 1
    return finalize_pilot(
        exp_dir,
        workload_status,
        environ,
        require_rollout=state.workload_started,
        agent_surface=agent_surface,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the run and postmortem-cleanup command surfaces."""

    parser = argparse.ArgumentParser(
        description="Run or clean one mandatory-tap pilot"
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--input", action="append", type=Path, required=True)
    run_parser.add_argument(
        "--agent-candidate-dir",
        type=Path,
        help="bind only this external candidate tree into the Agent sandbox",
    )
    run_parser.add_argument(
        "--agent-surface",
        action="store_true",
        help="host the opaque Agent Surface over the candidate-only bridge",
    )
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
            args.agent_candidate_dir,
            args.agent_surface,
        )
    except PilotError as exc:
        print(f"pilot-runner: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
