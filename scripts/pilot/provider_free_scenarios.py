#!/usr/bin/env python3
"""Closed provider-free scenarios dispatched by ``provider_free_runner``."""

from __future__ import annotations

import argparse
import hashlib
import http.server
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Sequence
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

from scripts.pilot import deployment_authority
from scripts.pilot.cvm_job.protocol import (
    PROVIDER_FREE_PREVIEW_SANDBOX_PATH,
    PROVIDER_FREE_PREVIEW_SANDBOX_SCHEMA,
    PROVIDER_FREE_SCENARIO_FAILURE_PATH,
    PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA,
    PROVIDER_FREE_SCENARIO_FAILURE_STAGES,
    provider_free_scenario_failure_operation_allowed,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_HELPER = REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace"
MESH_COMPARE = REPO_ROOT / "skills/mesh-compare/scripts/mesh-compare"
MESH_COMPARE_ENTRYPOINT = MESH_COMPARE / "cli.py"
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


class ScenarioError(RuntimeError):
    """A closed scenario could not publish required auditable evidence."""

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        operation: str | None = None,
        classification: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.operation = operation
        self.classification = classification


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
        ) from exc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, indent=2, sort_keys=True, separators=(",", ": "))
        + "\n"
    ).encode("utf-8")


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
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
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


def _run_public(argv: Sequence[str], *, cwd: Path, command_log: Path) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScenarioError(f"public command could not run: {argv[0]}: {exc}") from exc
    record = {
        "schema": "cvm.provider-free-command/1",
        "argv": list(argv),
        "cwd": os.fspath(cwd),
        "exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    command_log.parent.mkdir(parents=True, exist_ok=True)
    with command_log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    if completed.returncode != 0:
        classification = None
        try:
            failure_payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
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
        detail = " ".join((completed.stderr or completed.stdout).split())[:1000]
        raise ScenarioError(
            f"public command failed ({completed.returncode}): {detail}",
            classification=classification,
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ScenarioError("public command returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ScenarioError("public command did not return an ok result")
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
        deployment_authority.SANDBOX_BROWSER_CACHE,
        deployment_authority.SANDBOX_BROWSER_CACHE,
        "--chdir",
        os.fspath(cwd),
        "--",
        *command,
    ]


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
    input_document = json.loads((prepared / "input/input.json").read_text(encoding="utf-8"))
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
        "preview_browser_render_failed": "preview_browser_render",
        "preview_browser_result_failed": "preview_browser_result",
    }
    try:
        sandbox_argv = _preview_sandbox_argv(argv, cwd=cwd)
        _publish_preview_sandbox_enforcement(command_log, sandbox_argv)
        return _run_public(
            sandbox_argv,
            cwd=cwd,
            command_log=command_log,
        )
    except ScenarioError as exc:
        operation = operations.get(exc.classification)
        if operation is None:
            raise
        raise ScenarioError(
            f"provider-free preview operation failed: {operation}",
            operation=operation,
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
    preview = json.loads((workspace / "steps/000000/preview/preview.json").read_text(encoding="utf-8"))
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
    args = parser.parse_args(argv)
    if args.scenario != "issue15-runtime-authority":
        print(f"provider-free-scenario: unknown scenario: {args.scenario!r}", file=sys.stderr)
        return 2
    workspace = args.workspace.resolve()
    try:
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
            _write_json(
                workspace / PROVIDER_FREE_SCENARIO_FAILURE_PATH,
                failure,
            )
        print(f"provider-free-scenario: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "scenario": receipt}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
