#!/usr/bin/env python3
"""Read-only canonical Workspace graph audit for pilot-review."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
from typing import Any


DEFAULT_WORKSPACE_HELPER = "mesh-to-cad-workspace"
_VENDORED_AUTHORITY_HELPER = Path(__file__).resolve().with_name(
    "workspace_authority.py"
)
_INSTALLED_AUTHORITY_HELPER = (
    Path(__file__).resolve().parent.parent / "mesh-to-cad-authority"
)
DEFAULT_AUTHORITY_HELPER = (
    str(_VENDORED_AUTHORITY_HELPER)
    if _VENDORED_AUTHORITY_HELPER.is_file()
    else str(_INSTALLED_AUTHORITY_HELPER)
    if _INSTALLED_AUTHORITY_HELPER.is_dir()
    else "workspace-authority"
)
_DEPLOYED_AUTHORITY_PATHS = (
    "scripts/pilot",
    "skills/mesh-to-cad/scripts/mesh-to-cad-workspace",
    "skills/mesh-to-cad/scripts/mesh-to-cad-authority",
    "skills/mesh-compare/scripts/mesh-compare",
    "skills/mesh-compare/scripts/packages/meshscope",
    "skills/mesh-compare/scripts/packages/meshshot",
    "skills/cad/scripts/canonical-build",
    "skills/cad/scripts/packages",
    "skills/implicit-cad/scripts/packages/implicitjs",
    "skills/cad-viewer/scripts/viewer",
    "models/simple/rectangular_clamp_block.py",
    "models/simple/simple_model_library.py",
)
_DEPLOYED_AUTHORITY_EXCLUSIONS = {
    "directory_names": [".git", "__pycache__", "node_modules"],
    "file_suffixes": [".dylib", ".pyc", ".pyd"],
    "native_shared_objects_included": True,
}
_SANDBOX_NAMESPACES = (
    ("user", "--unshare-user"),
    ("network", "--unshare-net"),
    ("pid", "--unshare-pid"),
    ("ipc", "--unshare-ipc"),
    ("uts", "--unshare-uts"),
)
_SANDBOX_SETUP_CAPABILITIES = (
    "CAP_SYS_ADMIN",
    "CAP_SYS_CHROOT",
    "CAP_NET_ADMIN",
    "CAP_SETUID",
    "CAP_SETGID",
    "CAP_SYS_PTRACE",
    "CAP_SETFCAP",
)
_SANDBOX_PROFILE = {
    "schema": "cvm.provider-free-linux-sandbox/11",
    "namespaces": [name for name, _flag in _SANDBOX_NAMESPACES],
    "capabilities": {
        "baseline": "drop-all",
        "retained": list(_SANDBOX_SETUP_CAPABILITIES),
        "scope": "outer-user-namespace",
        "purpose": "nested-bwrap-setup",
    },
    "die_with_parent": True,
    "new_session": True,
    "temporary_filesystem": "/tmp",
    "repository_mount": "read-only",
    "output_mount": "read-write-exact-experiment",
    "browser_cache_mount": "read-only-job-scoped-attested-revision",
    "browser_runtime_staging": {
        "source": "deployment-attested-host-revision",
        "source_filesystem": "same-device-as-deployment-browser",
        "scope": "single-attested-revision",
        "destination": "/tmp/provider-free-playwright",
        "staged_revision": "attested",
        "staged_executable": (
            "/tmp/provider-free-playwright/attested/"
            "chrome-headless-shell-linux64/chrome-headless-shell"
        ),
        "destination_filesystem": "read-only-bind-of-exec-permitted-host-stage",
        "tree_validation": "regular-files-only-no-links-or-special",
        "executable_validation": {
            "sha256": "deployment-runtime-identity",
            "execute_bits": "required",
        },
        "exec_permission_validation": {
            "mechanism": "kernel-execve-repository-owned-immediate-exit-probe",
            "network": "none",
            "timeout_seconds": 5,
            "expected_stdout": "cvm.browser-stage-exec-probe/1",
        },
        "sandbox_exec_diagnostics": {
            "schema": "cvm.provider-free-browser-exec-diagnostic/4",
            "receipt": "run/browser-exec-diagnostic.json",
            "executable": (
                "/tmp/provider-free-playwright/attested/"
                "chrome-headless-shell-linux64/chrome-headless-shell"
            ),
            "argv_suffix": ["--version"],
            "lifecycle": "non-rendering-immediate-exit",
            "environment_names": ["HOME", "LANG", "PATH"],
            "network": "none",
            "timeout_seconds": 5,
            "node_probe": {
                "script": "scripts/pilot/browser_exec_probe.js",
                "runtime": "playwright-bundled-node",
                "spawn": "child-process",
                "failure_kinds": [
                    "spawn-event",
                    "nonzero-exit",
                    "timeout",
                    "output-shape",
                ],
                "modes": [
                    {"name": "attached", "detached": False},
                    {"name": "detached", "detached": True},
                ],
                "result": {
                    "exit_code": "zero-only-on-passed",
                    "stdout": "single-closed-result-token",
                    "stderr": "empty",
                    "child_stdout": "single-chromium-version-line",
                    "child_stdout_max_bytes": 128,
                },
            },
            "result": {
                "exit_code": 0,
                "stdout": "single-chromium-version-line",
                "stdout_max_bytes": 128,
                "stderr": "empty",
            },
            "seams": [
                "outer-python-direct",
                "nested-python-direct",
                "nested-node-attached-direct",
                "nested-node-detached-direct",
                "playwright-launch",
            ],
            "published": "closed-outcomes-only-no-raw-output",
            "cleanup": "no-profile-or-persistent-process-artifacts",
        },
        "nested_mount": "read-only-exact-staged-cache",
        "launch_handoff": {
            "environment": "MESHSHOT_BROWSER_EXECUTABLE",
            "value": (
                "/tmp/provider-free-playwright/attested/"
                "chrome-headless-shell-linux64/chrome-headless-shell"
            ),
            "validation": "absolute-regular-non-symlink-executable",
            "playwright_option": "executable_path",
        },
        "cleanup": "supervisor-context-terminal-all-exit-classes",
        "catchable_signal_cleanup": ["SIGINT", "SIGTERM"],
        "uncatchable_termination": "stale-stage-collision-fail-closed",
    },
    "preview_process": {
        "capabilities": "drop-all",
        "mount_namespace": "inherit-outer",
        "receipt": "run/preview-sandbox-enforcement.json",
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
_SANDBOX_REQUIRED_ENVIRONMENT = {
    "HOME": "/home/provider-free",
    "PATH": "/workspace/repo/.venv/bin:/usr/local/bin:/usr/bin:/bin",
    "PLAYWRIGHT_BROWSERS_PATH": "/tmp/provider-free-playwright",
    "PYTHONDONTWRITEBYTECODE": "1",
}
_SYSTEM_RO_PATHS = (
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


class ReviewError(RuntimeError):
    """The review could not read its declared evidence."""


def _normalized_absolute(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ReviewError(f"{label} is not an absolute path")
    pure = PurePosixPath(value)
    if (
        not pure.is_absolute()
        or any(part in {".", ".."} for part in pure.parts)
        or pure.as_posix() != value
    ):
        raise ReviewError(f"{label} is not a normalized absolute path")
    return value


def _validate_provider_free_sandbox_argv(
    argv: object,
    runtime_identity: dict[str, Any],
    immutable_request: dict[str, Any],
) -> None:
    """Reject any launch vector outside the closed provider-free bwrap contract."""

    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise ReviewError("provider-free sandbox argv is invalid")
    bwrap = runtime_identity["bwrap"]["path"]
    chromium = runtime_identity["chromium"]
    exp_dir = immutable_request.get("exp_dir")
    if not isinstance(exp_dir, str):
        raise ReviewError("provider-free experiment path is invalid")
    fixed_prefix = [
        bwrap,
        *(flag for _name, flag in _SANDBOX_NAMESPACES),
        "--cap-drop",
        "ALL",
    ]
    for capability in _SANDBOX_SETUP_CAPABILITIES:
        fixed_prefix.extend(("--cap-add", capability))
    fixed_prefix.extend([
        "--die-with-parent",
        "--new-session",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/workspace",
        "--ro-bind",
    ])
    mount_start = len(fixed_prefix)
    if argv[:mount_start] != fixed_prefix or len(argv) < mount_start + 27:
        raise ReviewError("provider-free sandbox argv prefix conflicts")
    host_root = _normalized_absolute(argv[mount_start], "deployed repository root")
    if host_root == "/":
        raise ReviewError("deployed repository root is too broad")
    sandbox_exp = f"/workspace/repo/{exp_dir}"
    host_exp = f"{host_root}/{exp_dir}"
    group = immutable_request.get("group")
    exp = immutable_request.get("exp")
    if not isinstance(group, str) or not isinstance(exp, str):
        raise ReviewError("provider-free job identity is invalid")
    host_stage = (
        f"{chromium['host_cache_path']}/.cvm-provider-free-browser-stages/"
        f"{group}.{exp}/attested"
    )
    fixed_mounts = [
        host_root,
        "/workspace/repo",
        "--bind",
        host_exp,
        sandbox_exp,
        "--dir",
        "/home",
        "--dir",
        "/home/provider-free",
        "--dir",
        "/home/provider-free/.cache",
        "--dir",
        "/tmp/provider-free-playwright",
        "--ro-bind",
        host_stage,
        "/tmp/provider-free-playwright/attested",
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
    ]
    if argv[mount_start : mount_start + len(fixed_mounts)] != fixed_mounts:
        raise ReviewError("provider-free sandbox mount contract conflicts")
    index = mount_start + len(fixed_mounts)
    mounted_system_paths: list[str] = []
    while argv[index : index + 1] == ["--ro-bind"]:
        if index + 2 >= len(argv) or argv[index + 1] != argv[index + 2]:
            raise ReviewError("provider-free system runtime mount is invalid")
        mounted_system_paths.append(argv[index + 1])
        index += 3
    expected_system_paths = [
        path for path in _SYSTEM_RO_PATHS if path in mounted_system_paths
    ]
    if "/usr" not in mounted_system_paths or mounted_system_paths != expected_system_paths:
        raise ReviewError("provider-free system runtime mounts are outside allowlist")
    suffix = [
        "--chdir",
        "/workspace/repo",
        "--",
        "/workspace/repo/.venv/bin/python",
        "-m",
        "scripts.pilot.provider_free_scenarios",
        "run",
        immutable_request.get("scenario", {}).get("name"),
        "--workspace",
        sandbox_exp,
    ]
    if argv[index:] != suffix:
        raise ReviewError("provider-free sandbox command contract conflicts")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewError(f"expected JSON object: {path}")
    return value


def _validate(
    workspace: Path,
    helper: str | Path,
) -> tuple[int, dict[str, Any]]:
    helper_text = str(helper)
    helper_path = Path(helper_text).expanduser()
    if helper_path.exists() and (helper_path.is_dir() or helper_path.suffix == ".py"):
        command = [sys.executable, str(helper_path)]
    else:
        command = [helper_text]
    try:
        completed = subprocess.run(
            [
                *command,
                "validate",
                "--workspace",
                str(workspace),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReviewError(f"Workspace validator failed to run: {exc}") from exc
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ReviewError("Workspace validator returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ReviewError("Workspace validator returned a non-object")
    return completed.returncode, payload


def _audit_portable_authority(
    workspace: Path,
    authority_helper: str | Path,
    workspace_helper: str | Path,
    *,
    timeout_seconds: float,
    max_files: int,
    max_bytes: int,
) -> tuple[int, dict[str, Any]]:
    """Audit a retained copy through the portable-authority process seam."""

    helper_text = str(authority_helper)
    helper_path = Path(helper_text).expanduser()
    if helper_path.exists() and (helper_path.is_dir() or helper_path.suffix == ".py"):
        command = [sys.executable, str(helper_path)]
    else:
        command = [helper_text]
    try:
        completed = subprocess.run(
            [
                *command,
                "audit",
                "--source",
                str(workspace),
                "--workspace-helper",
                str(workspace_helper),
                "--timeout-seconds",
                str(timeout_seconds),
                "--max-files",
                str(max_files),
                "--max-bytes",
                str(max_bytes),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 5,
        )
    except subprocess.TimeoutExpired:
        return 2, {
            "ok": False,
            "classification": "not_auditable",
            "authority": {
                "classification": "authority_timeout",
                "detail": "portable authority audit timed out",
                "evidence": ["workspace-authority.json", "workspace-authority.bundle"],
            },
        }
    except OSError as exc:
        raise ReviewError(f"portable authority helper failed to run: {exc}") from exc
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ReviewError("portable authority helper returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ReviewError("portable authority helper returned a non-object")
    return completed.returncode, payload


def _runner_verdict(workspace: Path) -> tuple[str, list[dict[str, str]]]:
    path = workspace / "artifact_manifest.json"
    if not path.is_file():
        return "not_auditable", [
            {
                "classification": "observability-gap",
                "detail": "artifact_manifest.json is missing",
                "evidence": "artifact_manifest.json",
            }
        ]
    try:
        manifest = _read_json(path)
    except ReviewError as exc:
        return "not_auditable", [
            {
                "classification": "observability-gap",
                "detail": str(exc),
                "evidence": "artifact_manifest.json",
            }
        ]
    return ("pass" if manifest.get("final_status") == 0 else "fail"), []


def _runtime_authority_verdict(
    workspace: Path,
    payload: dict[str, Any] | None = None,
) -> tuple[str, dict[str, str], list[dict[str, str]], list[str]]:
    """Audit the optional closed provider-free runtime-authority receipt."""

    receipt_path = workspace / "run/runtime-authority-smoke.json"
    if not receipt_path.is_file():
        return (
            "not_auditable",
            {},
            [],
            [
                "production runtime integration requires shipped snapshot, invoked "
                "installed-skill, bundle, parity, and isolation gate evidence"
            ],
        )
    evidence = "run/runtime-authority-smoke.json"
    try:
        receipt = _read_json(receipt_path)
        proof = _read_json(workspace / "run/provider-free-execution.json")
        deployed_path = workspace / "run/deployed-source-authority.json"
        deployed_bytes = deployed_path.read_bytes()
        deployed = json.loads(deployed_bytes)
        sandbox = _read_json(workspace / "run/sandbox-enforcement.json")
        preview_sandbox = _read_json(
            workspace / "run/preview-sandbox-enforcement.json"
        )
        browser_exec_diagnostic = _read_json(
            workspace / "run/browser-exec-diagnostic.json"
        )
        manifest = _read_json(workspace / "artifact_manifest.json")
        if (
            deployed.get("schema") != "cvm.deployed-source-authority/1"
            or deployed.get("contract_paths") != list(_DEPLOYED_AUTHORITY_PATHS)
            or deployed.get("exclusions") != _DEPLOYED_AUTHORITY_EXCLUSIONS
        ):
            raise ReviewError("complete deployed source authority is missing")
        runtime_identity = deployed.get("runtime_identity")
        if (
            not isinstance(runtime_identity, dict)
            or set(runtime_identity) != {"schema", "bwrap", "chromium", "cadpy"}
            or runtime_identity.get("schema")
            != "cvm.provider-free-runtime-identity/1"
        ):
            raise ReviewError("trusted deployed runtime identity is missing")
        bwrap_identity = runtime_identity.get("bwrap")
        chromium_identity = runtime_identity.get("chromium")
        cadpy_identity = runtime_identity.get("cadpy")

        def valid_sha256(value: object) -> bool:
            return isinstance(value, str) and len(value) == 64 and all(
                character in "0123456789abcdef" for character in value
            )

        if (
            not isinstance(bwrap_identity, dict)
            or set(bwrap_identity) != {"path", "sha256", "version"}
            or bwrap_identity.get("path") != "/usr/bin/bwrap"
            or not str(bwrap_identity.get("version", "")).startswith("bubblewrap ")
            or not isinstance(chromium_identity, dict)
            or set(chromium_identity)
            != {
                "revision",
                "host_cache_path",
                "sandbox_cache_path",
                "executable_path",
                "sha256",
            }
            or not str(chromium_identity.get("revision", "")).isdigit()
            or chromium_identity.get("sandbox_cache_path")
            != "/home/provider-free/.cache/ms-playwright"
            or _normalized_absolute(
                chromium_identity.get("host_cache_path"), "Chromium cache"
            )
            != chromium_identity.get("host_cache_path")
            or chromium_identity.get("executable_path")
            != (
                str(chromium_identity.get("host_cache_path"))
                + "/chromium_headless_shell-"
                + str(chromium_identity.get("revision"))
                + "/chrome-headless-shell-linux64/chrome-headless-shell"
            )
            or not isinstance(cadpy_identity, dict)
            or set(cadpy_identity) != {"path", "sha256"}
            or cadpy_identity.get("path")
            != "skills/cad/scripts/packages/cadpy/src/cadpy/__init__.py"
            or any(
                not valid_sha256(identity.get("sha256"))
                for identity in (bwrap_identity, chromium_identity, cadpy_identity)
            )
        ):
            raise ReviewError("trusted deployed runtime identity is invalid")
        deployed_files = deployed.get("files")
        if not isinstance(deployed_files, list) or not deployed_files:
            raise ReviewError("deployed source authority inventory is empty")
        retained_root = workspace / "run/deployed-source"
        actual_files: list[dict[str, Any]] = []
        for path in sorted(retained_root.rglob("*")):
            relative = path.relative_to(retained_root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ReviewError(f"retained deployed source contains symlink: {relative}")
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ReviewError(f"retained deployed source contains special file: {relative}")
            data = path.read_bytes()
            actual_files.append(
                {
                    "path": relative,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        actual_files.sort(key=lambda item: item["path"])
        if actual_files != deployed_files:
            raise ReviewError(
                "retained deployed source does not match complete inventory: "
                f"expected={len(deployed_files)} actual={len(actual_files)}"
            )
        retained_by_path = {item["path"]: item for item in actual_files}
        if retained_by_path.get(cadpy_identity["path"], {}).get("sha256") != (
            cadpy_identity["sha256"]
        ):
            raise ReviewError("audited cadpy identity lacks retained source authority")
        if (
            deployed.get("file_count") != len(actual_files)
            or deployed.get("total_bytes")
            != sum(item["size_bytes"] for item in actual_files)
            or deployed.get("tree_sha256")
            != hashlib.sha256(
                json.dumps(
                    actual_files, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
        ):
            raise ReviewError("retained deployed source tree identity conflicts")
        if not any(
            item["path"].startswith(
                "skills/mesh-compare/scripts/packages/meshscope/"
            )
            and Path(item["path"]).name.startswith("_native")
            and Path(item["path"]).suffix == ".so"
            for item in actual_files
        ):
            raise ReviewError("retained deployed source lacks native meshscope binary")
        if (
            set(sandbox)
            != {
                "schema",
                "network",
                "argv",
                "environment_names",
                "required_environment",
                "sandbox_profile",
                "runtime_identity",
            }
            or sandbox.get("schema")
            != "cvm.provider-free-sandbox-enforcement/1"
            or sandbox.get("network") != "isolated-loopback"
            or not isinstance(sandbox.get("argv"), list)
            or sandbox.get("sandbox_profile") != _SANDBOX_PROFILE
            or sandbox.get("runtime_identity") != runtime_identity
            or sandbox.get("required_environment")
            != _SANDBOX_REQUIRED_ENVIRONMENT
            or not set(sandbox.get("environment_names", [])).issubset(
                {
                    "HOME",
                    "LANG",
                    "PATH",
                    "PLAYWRIGHT_BROWSERS_PATH",
                    "PYTHONDONTWRITEBYTECODE",
                    "TZ",
                }
            )
            or not {
                "HOME",
                "PATH",
                "PLAYWRIGHT_BROWSERS_PATH",
                "PYTHONDONTWRITEBYTECODE",
            }.issubset(
                sandbox.get("environment_names", [])
            )
        ):
            raise ReviewError("retained sandbox/egress enforcement is incomplete")
        required = {
            "schema",
            "scenario_identity",
            "workspace",
            "viewer_deployment",
            "viewer_fallback",
            "native_depth_eight",
            "cadpy_runtime",
            "shipped_tree",
            "commands",
            "preview_sandbox",
        }
        if set(receipt) != required:
            raise ReviewError("runtime-authority receipt is not a closed object")
        if (
            receipt["schema"] != "issue15.runtime-authority-smoke/1"
            or receipt["scenario_identity"]
            != "issue15.provider-free.runtime-authority/1"
        ):
            raise ReviewError("runtime-authority receipt identity conflicts")
        workspace_receipt = receipt["workspace"]
        if (
            not isinstance(workspace_receipt, dict)
            or workspace_receipt.get("path") != "."
            or workspace_receipt.get("schema") != "mesh-to-cad.workspace/1"
            or not isinstance(workspace_receipt.get("final_delivery"), dict)
        ):
            raise ReviewError("runtime-authority Workspace receipt is incomplete")
        graph = payload.get("graph") if isinstance(payload, dict) else None
        canonical_delivery = graph.get("final_delivery") if isinstance(graph, dict) else None
        claimed_delivery = workspace_receipt.get("final_delivery")
        if not isinstance(canonical_delivery, dict) or any(
            claimed_delivery.get(field) != canonical_delivery.get(field)
            for field in ("selected_step", "accepted", "identity_sha256", "manifest")
        ):
            raise ReviewError("runtime receipt Final Delivery conflicts with canonical Workspace")
        deployment = receipt["viewer_deployment"]
        artifacts = deployment.get("artifacts") if isinstance(deployment, dict) else None
        if (
            not isinstance(deployment, dict)
            or deployment.get("schema") != "cvm.viewer-runtime-deployment/1"
            or not isinstance(artifacts, list)
            or [item.get("role") for item in artifacts if isinstance(item, dict)]
            != ["launcher", "server", "client"]
            or any(
                not isinstance(item, dict)
                or item.get("bundle", {}).get("sha256")
                != item.get("deployed", {}).get("sha256")
                for item in artifacts
            )
        ):
            raise ReviewError("Viewer source/bundle/deployed receipt is incomplete")
        for artifact in artifacts:
            for layer in ("source", "bundle", "deployed"):
                identity = artifact.get(layer)
                relative = identity.get("path") if isinstance(identity, dict) else None
                pure = PurePosixPath(relative) if isinstance(relative, str) else None
                if (
                    pure is None
                    or pure.is_absolute()
                    or any(part in {"", ".", ".."} for part in pure.parts)
                    or retained_by_path.get(relative, {}).get("sha256")
                    != identity.get("sha256")
                ):
                    raise ReviewError(
                        f"Viewer {layer} digest lacks retained deployed file authority"
                    )
        fallback = receipt["viewer_fallback"]
        if (
            not isinstance(fallback, dict)
            or fallback.get("schema") != "issue15.viewer-fallback-smoke/1"
            or fallback.get("rejected_reuse", {}).get("http_status") != 400
            or fallback.get("fallback", {}).get("action") != "start"
        ):
            raise ReviewError("Viewer reuse-rejection fallback receipt is incomplete")
        native = receipt["native_depth_eight"]
        if (
            not isinstance(native, dict)
            or native.get("schema") != "issue15.native-depth-eight-evidence/1"
            or native.get("native_required") is not True
            or native.get("backend", {}).get("id")
            != "meshscope.voxblame.native-sat/1"
            or native.get("depths") != list(range(1, 9))
        ):
            raise ReviewError("native-required depth-8 receipt is incomplete")
        cadpy_runtime = receipt["cadpy_runtime"]
        if (
            not isinstance(cadpy_runtime, dict)
            or cadpy_runtime
            != {
                "schema": "cvm.audited-cadpy-runtime/1",
                "path": cadpy_identity["path"],
                "sha256": cadpy_identity["sha256"],
            }
        ):
            raise ReviewError("executed cadpy identity conflicts with deployed authority")
        shipped = receipt["shipped_tree"]
        files = shipped.get("files") if isinstance(shipped, dict) else None
        if (
            not isinstance(shipped, dict)
            or shipped.get("schema") != "cvm.deployed-runtime-tree-receipt/1"
            or not isinstance(files, list)
            or not files
            or shipped.get("file_count") != len(files)
            or shipped.get("total_bytes")
            != sum(item.get("size_bytes", -1) for item in files if isinstance(item, dict))
        ):
            raise ReviewError("complete shipped-runtime tree receipt is missing")
        tree_bytes = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        if shipped.get("tree_sha256") != hashlib.sha256(tree_bytes).hexdigest():
            raise ReviewError("shipped-runtime tree receipt digest conflicts")
        shipped_root = shipped.get("root")
        if shipped_root != "skills/cad-viewer/scripts/viewer":
            raise ReviewError("shipped-runtime root conflicts")
        actual_shipped = []
        prefix = f"{shipped_root}/"
        for item in actual_files:
            if item["path"].startswith(prefix):
                actual_shipped.append(
                    {**item, "path": item["path"][len(prefix) :]}
                )
        if files != actual_shipped:
            raise ReviewError("shipped-runtime receipt does not match retained tree")
        if (
            set(proof)
            != {
                "schema",
                "job",
                "scenario",
                "execution_profile",
                "request_authority",
                "sandbox",
                "provider_environment",
                "requests",
                "sandbox_enforcement",
            }
            or proof.get("schema") != "cvm.provider-free-execution/1"
            or proof.get("scenario")
            != {
                "name": "issue15-runtime-authority",
                "identity": "issue15.provider-free.runtime-authority/1",
            }
            or proof.get("execution_profile")
            != {
                "schema": "cvm.provider-free-execution-profile/1",
                "id": "issue15.provider-free-bounded/11",
                "provider_access": "forbidden",
                "sandbox_profile": "cvm.provider-free-linux-sandbox/11",
            }
            or proof.get("sandbox")
            != {
                "network": "isolated-loopback",
                "resource_profile": "issue15.provider-free-bounded/11",
            }
            or proof.get("provider_environment", {}).get("credential_values_recorded")
            is not False
            or proof.get("requests") != {"model_gateway": 0, "provider": 0, "tap": 0}
            or proof.get("sandbox_enforcement")
            != {
                "path": "run/sandbox-enforcement.json",
                "sha256": hashlib.sha256(
                    (workspace / "run/sandbox-enforcement.json").read_bytes()
                ).hexdigest(),
            }
            or proof.get("request_authority", {}).get(
                "deployment_tree_sha256"
            )
            != deployed.get("tree_sha256")
        ):
            raise ReviewError("provider-free execution proof is incomplete")
        immutable_request = proof.get("request_authority", {}).get(
            "immutable_request"
        )
        if not isinstance(immutable_request, dict):
            raise ReviewError("provider-free immutable request is missing")
        request_authority = immutable_request.get("request_authority")
        canonical_deployed_digest = hashlib.sha256(
            json.dumps(deployed, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if (
            not isinstance(request_authority, dict)
            or set(request_authority)
            != {
                "schema",
                "deployment_receipt",
                "deployment_receipt_sha256",
                "deployment_receipt_canonical_sha256",
                "deployment_source_head",
                "deployment_tree_sha256",
                "runtime_identity",
            }
            or request_authority.get("schema")
            != "cvm.provider-free-request-authority/1"
            or request_authority.get("deployment_receipt") != ".cvm-deployment.json"
            or request_authority.get("deployment_receipt_sha256")
            != hashlib.sha256(deployed_bytes).hexdigest()
            or request_authority.get("deployment_receipt_canonical_sha256")
            != canonical_deployed_digest
            or request_authority.get("deployment_source_head")
            != deployed.get("source_head")
            or request_authority.get("deployment_tree_sha256")
            != deployed.get("tree_sha256")
            or request_authority.get("runtime_identity") != runtime_identity
        ):
            raise ReviewError("immutable deployed source authority binding conflicts")
        immutable_digest = hashlib.sha256(
            b"cvm.provider-free-request-authority/1\0"
            + json.dumps(
                immutable_request, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        if (
            proof.get("request_authority", {}).get("sha256")
            != immutable_digest
            or immutable_request.get("job_kind") != "provider-free"
            or immutable_request.get("object") != "issue15-runtime-authority"
            or immutable_request.get("group") != workspace.parent.name
            or immutable_request.get("exp") != workspace.name
            or immutable_request.get("exp_dir")
            != f"outputs/{workspace.parent.name}/{workspace.name}"
            or immutable_request.get("scenario") != proof.get("scenario")
            or immutable_request.get("execution_profile")
            != proof.get("execution_profile")
            or immutable_request.get("request_authority") != request_authority
        ):
            raise ReviewError("provider-free immutable request binding conflicts")
        _validate_provider_free_sandbox_argv(
            sandbox.get("argv"), runtime_identity, immutable_request
        )
        expected_job = f"{workspace.parent.name}/{workspace.name}"
        provider_environment = proof.get("provider_environment", {})
        stripped = provider_environment.get("stripped")
        if (
            proof.get("job") != expected_job
            or provider_environment.get("allowlist")
            != ["HOME", "LANG", "PATH", "PYTHONDONTWRITEBYTECODE", "TZ"]
            or not isinstance(stripped, list)
            or stripped != sorted(set(stripped))
            or set(stripped).intersection(provider_environment.get("allowlist", []))
        ):
            raise ReviewError("provider-free job/environment binding conflicts")
        command_path = receipt["commands"]
        if command_path != "run/provider-free-commands.jsonl":
            raise ReviewError("public-command receipt path conflicts")
        preview_path = receipt["preview_sandbox"]
        if preview_path != "run/preview-sandbox-enforcement.json":
            raise ReviewError("preview sandbox receipt path conflicts")
        sandbox_root = "/workspace/repo"
        sandbox_exp = f"{sandbox_root}/outputs/{workspace.parent.name}/{workspace.name}"
        expected_preview_argv = [
            "/usr/bin/bwrap",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--bind",
            "/",
            "/",
            "--ro-bind",
            "/tmp/provider-free-playwright",
            "/tmp/provider-free-playwright",
            "--setenv",
            "PLAYWRIGHT_BROWSERS_PATH",
            "/tmp/provider-free-playwright",
            "--setenv",
            "MESHSHOT_BROWSER_EXECUTABLE",
            (
                "/tmp/provider-free-playwright/attested/"
                "chrome-headless-shell-linux64/chrome-headless-shell"
            ),
            "--chdir",
            sandbox_root,
            "--",
            f"{sandbox_root}/.venv/bin/python",
            f"{sandbox_root}/skills/mesh-compare/scripts/mesh-compare",
            "voxblame-preview",
            f"{sandbox_exp}/work/candidate/built/measurement.glb",
            "--reference",
            f"{sandbox_exp}/input",
            "--output",
            f"{sandbox_exp}/work/preview-0",
            "--experiment",
            f"{sandbox_exp}/experiment.json",
            "--variant",
            "step",
        ]
        if (
            set(preview_sandbox)
            != {"schema", "argv", "capabilities", "mount_namespace"}
            or preview_sandbox.get("schema")
            != "cvm.provider-free-preview-sandbox-enforcement/1"
            or preview_sandbox.get("argv") != expected_preview_argv
            or preview_sandbox.get("capabilities") != "drop-all"
            or preview_sandbox.get("mount_namespace") != "inherit-outer"
        ):
            raise ReviewError("preview sandbox enforcement is incomplete")
        if browser_exec_diagnostic != {
            "schema": "cvm.provider-free-browser-exec-diagnostic/4",
            "executable": (
                "/tmp/provider-free-playwright/attested/"
                "chrome-headless-shell-linux64/chrome-headless-shell"
            ),
            "probe": "chromium-version-immediate-exit",
            "outer": "passed",
            "nested": "passed",
            "node_attached": "passed",
            "node_detached": "passed",
            "node_failure_kind": "not-run",
            "playwright": "passed",
        }:
            raise ReviewError("browser exec diagnostic is incomplete")
        manifest_files = manifest.get("files")
        if manifest.get("final_status") != 0 or not isinstance(manifest_files, list):
            raise ReviewError("terminal artifact manifest is incomplete")
        manifest_by_path = {
            item.get("path"): item
            for item in manifest_files
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        for relative in (
            evidence,
            "run/provider-free-execution.json",
            command_path,
            preview_path,
            "run/browser-exec-diagnostic.json",
            "run/deployed-source-authority.json",
            "run/sandbox-enforcement.json",
        ):
            path = workspace / relative
            data = path.read_bytes()
            entry = manifest_by_path.get(relative)
            if (
                not isinstance(entry, dict)
                or entry.get("size_bytes") != len(data)
                or entry.get("sha256") != hashlib.sha256(data).hexdigest()
            ):
                raise ReviewError(f"terminal manifest does not bind {relative}")
        for item in actual_files:
            relative = f"run/deployed-source/{item['path']}"
            if manifest_by_path.get(relative) != {**item, "path": relative}:
                raise ReviewError(f"terminal manifest does not bind {relative}")
    except (IndexError, OSError, TypeError, ValueError, ReviewError) as exc:
        return (
            "not_auditable",
            {},
            [
                {
                    "classification": "observability-gap",
                    "detail": str(exc),
                    "evidence": evidence,
                }
            ],
            ["provider-free production runtime evidence failed closed audit"],
        )
    return (
        "pass",
        {
            "runtime_authority": evidence,
            "provider_free_execution": "run/provider-free-execution.json",
            "terminal_manifest": "artifact_manifest.json",
        },
        [],
        [],
    )


def _invalid_workspace_review(
    workspace: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    error = payload.get("error")
    if not isinstance(error, dict):
        error = {}
    classification = str(error.get("classification") or "invalid_workspace")
    runner, issues = _runner_verdict(workspace)
    issues.append(
        {
            "classification": "contract-gap",
            "detail": str(error.get("detail") or "Workspace validation failed"),
            "evidence": str(error.get("path") or "$"),
        }
    )
    return {
        "verdicts": {
            "runner_completion": runner,
            "workspace_protocol": classification,
            "reconstruction_quality": "not_auditable",
            "production_runtime_integration": "not_auditable",
        },
        "contract_provenance": {
            "workspace": "workspace.json",
            "runner": "artifact_manifest.json",
        },
        "workspace_validation": {
            "valid": False,
            "classification": classification,
            "path": str(error.get("path") or "$"),
            "detail": str(error.get("detail") or "Workspace validation failed"),
        },
        "graph": {"nodes": [], "edges": []},
        "issues": issues,
        "unresolved": [],
        "evidence_gaps": ["canonical Workspace graph unavailable"],
    }


def _node(
    nodes: list[dict[str, Any]],
    node_id: str,
    node_type: str,
    evidence: str,
    **facts: Any,
) -> None:
    nodes.append({"id": node_id, "type": node_type, "evidence": evidence, **facts})


def _edge(
    edges: list[dict[str, str]],
    source: str,
    target: str,
    edge_type: str,
) -> None:
    edges.append({"from": source, "to": target, "type": edge_type})


def _canonical_graph(
    workspace: Path,
    graph: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    _node(
        nodes,
        "canonical-reference",
        "canonical_reference",
        "input/input.json",
    )
    _node(nodes, "workspace", "workspace", "workspace.json")
    _edge(edges, "canonical-reference", "workspace", "reference_initializes_workspace")

    steps = graph.get("steps") if isinstance(graph.get("steps"), list) else []
    for step in steps:
        number = int(step["step"])
        step_id = f"step:{number}"
        preview_id = f"preview:{number}"
        measurement_id = f"measurement:{number}"
        _node(
            nodes,
            step_id,
            "measured_step",
            f"steps/{number:06d}/step.json",
            accepted=bool(step.get("accepted")),
            parent_step=step.get("parent_step"),
        )
        _node(
            nodes,
            preview_id,
            "formal_preview",
            str(step.get("preview") or f"steps/{number:06d}/preview/preview.json"),
        )
        _node(
            nodes,
            measurement_id,
            "measurement",
            str(step.get("measurement") or f"steps/{number:06d}/measurement.json"),
        )
        parent = step.get("parent_step")
        if number == 0:
            _edge(edges, "workspace", step_id, "workspace_publishes_initial_step")
        else:
            _edge(
                edges,
                f"step:{parent}",
                step_id,
                "measured_step_descends_from",
            )

        cycle_number = step.get("cycle")
        attempt_path = (
            workspace / "steps/000000/attempt.json"
            if number == 0
            else workspace
            / "cycles"
            / f"{int(cycle_number if cycle_number is not None else number):06d}"
            / "attempt.json"
        )
        attempt = _read_json(attempt_path)
        attempt_number = int(attempt["attempt"])
        attempt_id = f"attempt:{attempt_number}"
        _node(
            nodes,
            attempt_id,
            "attempt",
            attempt_path.relative_to(workspace).as_posix(),
            result=attempt.get("result"),
            intended_step=attempt.get("intended_step"),
        )
        _edge(
            edges,
            "workspace" if parent is None else f"step:{parent}",
            attempt_id,
            "attempt_branches_from_step",
        )
        _edge(edges, attempt_id, preview_id, "attempt_produces_preview")
        _edge(edges, preview_id, measurement_id, "preview_has_measurement")
        _edge(edges, measurement_id, step_id, "measurement_publishes_step")

    failed_attempts = (
        graph.get("failed_attempts")
        if isinstance(graph.get("failed_attempts"), list)
        else []
    )
    for attempt in failed_attempts:
        attempt_number = int(attempt["attempt"])
        attempt_id = f"attempt:{attempt_number}"
        if not any(node["id"] == attempt_id for node in nodes):
            _node(
                nodes,
                attempt_id,
                "attempt",
                f"attempts/{attempt_number:06d}/attempt.json",
                result=attempt.get("result"),
                classification=attempt.get("classification"),
            )
        parent = attempt.get("from_step")
        _edge(
            edges,
            "workspace" if parent is None else f"step:{parent}",
            attempt_id,
            "attempt_branches_from_step",
        )

    cycles = graph.get("cycles") if isinstance(graph.get("cycles"), list) else []
    for cycle in cycles:
        number = int(cycle["cycle"])
        root = workspace / "cycles" / f"{number:06d}"
        plan = _read_json(root / "plan.json")
        source_changes = _read_json(root / "source_changes.json")
        region_diff = _read_json(root / "diff.json")
        assessment = _read_json(root / "assessment.json")
        cycle_id = f"cycle:{number}"
        batch_id = f"repair-batch:{number}"
        source_id = f"source-change:{number}"
        diff_id = f"region-diff:{number}"
        assessment_id = f"assessment:{number}"
        _node(
            nodes,
            cycle_id,
            "repair_cycle",
            f"cycles/{number:06d}/cycle.json",
            from_step=cycle.get("from_step"),
            to_step=cycle.get("to_step"),
        )
        _node(
            nodes,
            batch_id,
            "repair_batch",
            f"cycles/{number:06d}/plan.json",
            rationale=plan.get("rationale"),
        )
        _node(
            nodes,
            source_id,
            "source_change",
            f"cycles/{number:06d}/source_changes.json",
            files=source_changes.get("files", []),
        )
        _node(
            nodes,
            diff_id,
            "region_diff",
            f"cycles/{number:06d}/diff.json",
            identity=region_diff.get("identity"),
        )
        _node(
            nodes,
            assessment_id,
            "agent_assessment",
            f"cycles/{number:06d}/assessment.json",
            summary=assessment.get("summary"),
        )
        for target in plan.get("selected_targets", []):
            target_key = str(target.get("target_key"))
            target_id = f"repair-target:{number}:{target_key}"
            _node(
                nodes,
                target_id,
                "repair_target",
                f"cycles/{number:06d}/plan.json",
                target_key=target_key,
                mask_sha256=target.get("mask_sha256"),
            )
            _edge(
                edges,
                f"step:{cycle['from_step']}",
                target_id,
                "step_exposes_target",
            )
            _edge(edges, target_id, batch_id, "target_selected_by_batch")
        edit_ids: list[str] = []
        for edit in plan.get("planned_edits", []):
            edit_key = str(edit.get("edit_key"))
            edit_id = f"planned-edit:{number}:{edit_key}"
            edit_ids.append(edit_id)
            _node(
                nodes,
                edit_id,
                "planned_edit",
                f"cycles/{number:06d}/plan.json",
                edit_key=edit_key,
                target_keys=edit.get("target_keys", []),
                description=edit.get("description"),
            )
            _edge(edges, batch_id, edit_id, "batch_contains_edit")
            _edge(edges, edit_id, source_id, "edit_has_source_change")
        if not edit_ids:
            _edge(edges, batch_id, source_id, "batch_has_source_change")
        _edge(edges, source_id, diff_id, "source_change_measured_by_diff")
        _edge(edges, diff_id, assessment_id, "diff_assessed_by_agent")
        _edge(edges, assessment_id, cycle_id, "assessment_publishes_cycle")
        _edge(edges, cycle_id, f"step:{cycle['to_step']}", "cycle_publishes_step")
        attempt_ids = cycle.get("attempt_ids", [])
        if attempt_ids:
            successful_attempt = attempt_ids[-1]
            if any(node["id"] == f"attempt:{successful_attempt}" for node in nodes):
                _edge(
                    edges,
                    f"attempt:{successful_attempt}",
                    cycle_id,
                    "attempt_contributes_to_cycle",
                )

    delivery = graph.get("final_delivery")
    if isinstance(delivery, dict):
        selection = _read_json(workspace / "final/selection.json")
        manifest_path = str(delivery.get("manifest") or "final/manifest.json")
        manifest = _read_json(workspace / manifest_path)
        _node(
            nodes,
            "selection",
            "selection",
            "final/selection.json",
            selected_step=selection.get("selected_step"),
            considered_steps=selection.get("considered_steps", []),
        )
        _node(
            nodes,
            "rebuild",
            "rebuild",
            "final/rebuild.json",
            identity=manifest.get("rebuild_sha256"),
            execution=manifest.get("rebuild_execution"),
        )
        _node(
            nodes,
            "verification",
            "verification",
            "final/verification.json",
            identity=manifest.get("verification_sha256"),
            verification_identity=manifest.get(
                "verification_identity_sha256"
            ),
        )
        _node(
            nodes,
            "final-delivery",
            "final_delivery",
            manifest_path,
            selected_step=delivery.get("selected_step"),
            accepted=delivery.get("accepted"),
            identity_sha256=delivery.get("identity_sha256"),
        )
        for step in selection.get("considered_steps", []):
            _edge(
                edges,
                f"step:{step}",
                "selection",
                "step_considered_for_selection",
            )
        _edge(edges, "selection", "rebuild", "selection_triggers_rebuild")
        _edge(
            edges,
            "rebuild",
            "verification",
            "rebuild_verified_independently",
        )
        _edge(
            edges,
            "verification",
            "final-delivery",
            "verification_supports_delivery",
        )
    return {"nodes": nodes, "edges": edges}


def _canonical_review(
    workspace: Path,
    payload: dict[str, Any],
    authority: dict[str, Any],
) -> dict[str, Any]:
    graph = payload.get("graph")
    if not isinstance(graph, dict):
        raise ReviewError("valid Workspace response omitted its graph")
    delivery = graph.get("final_delivery")
    runner, issues = _runner_verdict(workspace)
    runtime, runtime_provenance, runtime_issues, runtime_gaps = (
        _runtime_authority_verdict(workspace, payload)
    )
    issues.extend(runtime_issues)
    accepted = bool(delivery.get("accepted")) if isinstance(delivery, dict) else False
    provenance = {
        "workspace": "workspace.json",
        "canonical_reference": "input/input.json",
        "graph_index": "step_index.json",
        "runner": "artifact_manifest.json",
        "telemetry": "run/",
        **runtime_provenance,
    }
    if authority.get("mode") == "materialized":
        provenance["portable_authority"] = "workspace-authority.json"
        provenance["portable_bundle"] = "workspace-authority.bundle"
    return {
        "verdicts": {
            "runner_completion": runner,
            "workspace_protocol": "pass",
            "reconstruction_quality": (
                "accepted" if accepted else "delivered_with_residual"
            ),
            "production_runtime_integration": runtime,
        },
        "contract_provenance": provenance,
        "workspace_validation": {
            "valid": True,
            "classification": "valid",
            "recovery": payload.get("recovery", []),
            "authority_mode": authority.get("mode"),
            "authority_evidence": authority.get("evidence", []),
            **(
                {"authority_head": authority.get("head")}
                if authority.get("head") is not None
                else {}
            ),
        },
        "graph": _canonical_graph(workspace, graph),
        "issues": issues,
        "unresolved": [],
        "evidence_gaps": runtime_gaps,
    }


def _not_auditable_authority_review(
    workspace: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Build the stable no-graph review for unavailable portable authority."""

    authority = payload.get("authority")
    if not isinstance(authority, dict):
        authority = {}
    classification = str(authority.get("classification") or "authority_invalid")
    detail = str(authority.get("detail") or "portable authority is unavailable")
    runner, issues = _runner_verdict(workspace)
    issues.append(
        {
            "classification": "observability-gap",
            "detail": detail,
            "evidence": "workspace-authority.json",
        }
    )
    return {
        "verdicts": {
            "runner_completion": runner,
            "workspace_protocol": "not_auditable",
            "reconstruction_quality": "not_auditable",
            "production_runtime_integration": "not_auditable",
        },
        "contract_provenance": {
            "runner": "artifact_manifest.json",
            "portable_authority": "workspace-authority.json",
            "portable_bundle": "workspace-authority.bundle",
        },
        "workspace_validation": {
            "valid": False,
            "classification": "not_auditable",
            "authority_mode": "unavailable",
            "authority_classification": classification,
            "authority_evidence": authority.get("evidence", []),
            "detail": detail,
        },
        "graph": {"nodes": [], "edges": []},
        "issues": issues,
        "unresolved": [],
        "evidence_gaps": ["canonical Workspace authority unavailable"],
    }


def review_workspace(
    workspace: Path,
    helper: str | Path,
    *,
    authority_helper: str | Path = DEFAULT_AUTHORITY_HELPER,
    authority_timeout_seconds: float = 120.0,
    authority_max_files: int = 20_000,
    authority_max_bytes: int = 5 * 1024 * 1024 * 1024,
) -> tuple[int, dict[str, Any]]:
    """Validate and reconstruct one experiment without changing its authority."""

    workspace = workspace.resolve()
    authority: dict[str, Any]
    if (workspace / ".git").exists():
        status, payload = _validate(workspace, helper)
        authority = {"mode": "live", "evidence": [".git", "workspace.json"]}
    else:
        status, audit_payload = _audit_portable_authority(
            workspace,
            authority_helper,
            helper,
            timeout_seconds=authority_timeout_seconds,
            max_files=authority_max_files,
            max_bytes=authority_max_bytes,
        )
        if status != 0 or audit_payload.get("ok") is not True:
            return 2, _not_auditable_authority_review(workspace, audit_payload)
        payload = audit_payload.get("workspace_validation")
        authority = audit_payload.get("authority")
        if not isinstance(payload, dict) or not isinstance(authority, dict):
            raise ReviewError("portable authority audit omitted validated evidence")
    if status != 0 or payload.get("ok") is not True:
        review = _invalid_workspace_review(workspace, payload)
        classification = review["workspace_validation"]["classification"]
        return (2 if classification == "unsupported_legacy_workspace" else 1), review
    return 0, _canonical_review(workspace, payload, authority)


def _markdown(review: dict[str, Any]) -> str:
    lines = ["# Pilot review", "", "## Verdicts", ""]
    for name, value in review["verdicts"].items():
        lines.append(f"- {name}: `{value}`")
    validation = review["workspace_validation"]
    lines.extend(
        [
            "",
            "## Workspace validation",
            "",
            f"- classification: `{validation['classification']}`",
            "",
            "## Graph",
            "",
            f"- nodes: {len(review['graph']['nodes'])}",
            f"- edges: {len(review['graph']['edges'])}",
            "",
            "## Issues",
            "",
        ]
    )
    if review["issues"]:
        for issue in review["issues"]:
            lines.append(
                f"- `{issue['classification']}`: {issue['detail']} "
                f"({issue['evidence']})"
            )
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _publish(output: Path, review: dict[str, Any]) -> None:
    """Atomically publish review artifacts into the explicit output root."""

    output.mkdir(parents=True, exist_ok=True)
    json_tmp = output / ".review.json.tmp"
    markdown_tmp = output / ".review.md.tmp"
    json_tmp.write_text(
        json.dumps(review, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_tmp.write_text(_markdown(review), encoding="utf-8")
    json_tmp.replace(output / "review.json")
    markdown_tmp.replace(output / "review.md")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--workspace-helper",
        default=DEFAULT_WORKSPACE_HELPER,
    )
    parser.add_argument("--authority-helper", default=DEFAULT_AUTHORITY_HELPER)
    parser.add_argument("--authority-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--authority-max-files", type=int, default=20_000)
    parser.add_argument(
        "--authority-max-bytes",
        type=int,
        default=5 * 1024 * 1024 * 1024,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = args.workspace.resolve()
    live = (workspace / ".git").exists()
    if not live and args.output is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "portable review requires an explicit separate --output",
                }
            )
        )
        return 1
    output = args.output.resolve() if args.output is not None else workspace
    if not live and (output == workspace or workspace in output.parents):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "portable review output must be outside the retained input",
                }
            )
        )
        return 1
    try:
        status, review = review_workspace(
            workspace,
            args.workspace_helper,
            authority_helper=args.authority_helper,
            authority_timeout_seconds=args.authority_timeout_seconds,
            authority_max_files=args.authority_max_files,
            authority_max_bytes=args.authority_max_bytes,
        )
        _publish(output, review)
    except (OSError, ReviewError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "ok": status == 0,
                "status": status,
                "classification": review["workspace_validation"]["classification"],
                "review_json": str(output / "review.json"),
                "review_markdown": str(output / "review.md"),
            },
            separators=(",", ":"),
        )
    )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
