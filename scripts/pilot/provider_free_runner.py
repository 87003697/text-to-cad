#!/usr/bin/env python3
"""Run one closed, provider-free CVM scenario in a bounded network namespace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
from typing import Mapping

from scripts.pilot import runner as pilot_runner
from scripts.pilot.cvm_job.runtime import (
    PROVIDER_FREE_ENV_ALLOWLIST,
    PROVIDER_FREE_EXECUTION_PROFILE,
    PROVIDER_FREE_PROOF,
    PROVIDER_FREE_SCENARIOS,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_REPO_ROOT = Path("/workspace/repo")
WALL_TIMEOUT_SECONDS = 1800
CPU_LIMIT_SECONDS = 1800
ADDRESS_SPACE_LIMIT_BYTES = 16 * 1024**3
FILE_SIZE_LIMIT_BYTES = 4 * 1024**3
OPEN_FILE_LIMIT = 512
PROCESS_LIMIT = 256
_CONTROL_ENVIRONMENT = frozenset(
    {
        *PROVIDER_FREE_ENV_ALLOWLIST,
        "CVM_PROVIDER_FREE_PROFILE",
        "CVM_PROVIDER_FREE_STRIPPED_NAMES",
    }
)


class ProviderFreeError(RuntimeError):
    """The provider-free scenario cannot satisfy its fixed execution contract."""


def _safe_group(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-z0-9-]+", value))


def _safe_exp(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)) and value not in {
        ".",
        "..",
    }


def _validate_request(scenario_name: str, group: str, exp: str) -> None:
    if scenario_name not in PROVIDER_FREE_SCENARIOS:
        raise ProviderFreeError(f"unknown provider-free scenario: {scenario_name!r}")
    if not _safe_group(group):
        raise ProviderFreeError(f"unsafe provider-free group: {group!r}")
    if not _safe_exp(exp) or not exp.endswith(f"-{scenario_name}"):
        raise ProviderFreeError(f"unsafe provider-free experiment: {exp!r}")


def _validate_environment(environ: Mapping[str, str]) -> list[str]:
    profile = environ.get("CVM_PROVIDER_FREE_PROFILE")
    if profile != PROVIDER_FREE_EXECUTION_PROFILE["id"]:
        raise ProviderFreeError("provider-free execution profile is missing or stale")
    unexpected = sorted(set(environ).difference(_CONTROL_ENVIRONMENT))
    if unexpected:
        raise ProviderFreeError(
            "provider-free environment contains non-allowlisted names: "
            + ",".join(unexpected)
        )
    raw = environ.get("CVM_PROVIDER_FREE_STRIPPED_NAMES", "")
    stripped = raw.split(",") if raw else []
    if stripped != sorted(set(stripped)) or any(not name for name in stripped):
        raise ProviderFreeError("provider-free stripped-name receipt is invalid")
    return stripped


def _apply_resource_limits() -> None:
    limits = (
        (resource.RLIMIT_CPU, CPU_LIMIT_SECONDS),
        (resource.RLIMIT_AS, ADDRESS_SPACE_LIMIT_BYTES),
        (resource.RLIMIT_FSIZE, FILE_SIZE_LIMIT_BYTES),
        (resource.RLIMIT_NOFILE, OPEN_FILE_LIMIT),
        (resource.RLIMIT_NPROC, PROCESS_LIMIT),
    )
    for resource_id, requested in limits:
        _soft, hard = resource.getrlimit(resource_id)
        effective = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
        resource.setrlimit(resource_id, (effective, effective))


def _sandbox_argv(
    scenario_name: str,
    exp_dir: Path,
    *,
    bwrap: str,
) -> list[str]:
    relative_exp = exp_dir.relative_to(REPO_ROOT)
    sandbox_exp = SANDBOX_REPO_ROOT / relative_exp
    argv = [
        bwrap,
        "--unshare-net",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--cap-drop",
        "ALL",
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
        os.fspath(REPO_ROOT),
        os.fspath(SANDBOX_REPO_ROOT),
        "--bind",
        os.fspath(exp_dir),
        os.fspath(sandbox_exp),
        "--dir",
        "/home",
        "--dir",
        "/home/provider-free",
    ]
    for source, target in (("usr/bin", "/bin"), ("usr/sbin", "/sbin"), ("usr/lib", "/lib"), ("usr/lib64", "/lib64")):
        argv.extend(("--symlink", source, target))
    for path in pilot_runner.existing_system_paths():
        argv.extend(("--ro-bind", os.fspath(path), os.fspath(path)))
    argv.extend(
        (
            "--chdir",
            os.fspath(SANDBOX_REPO_ROOT),
            "--",
            os.fspath(SANDBOX_REPO_ROOT / ".venv/bin/python"),
            os.fspath(SANDBOX_REPO_ROOT / "scripts/pilot/provider_free_scenarios.py"),
            "run",
            scenario_name,
            "--workspace",
            os.fspath(sandbox_exp),
        )
    )
    return argv


def _sandbox_environment(environ: Mapping[str, str]) -> dict[str, str]:
    result = {
        name: environ[name]
        for name in PROVIDER_FREE_ENV_ALLOWLIST
        if environ.get(name)
    }
    result.update(
        {
            "HOME": "/home/provider-free",
            "PATH": f"{SANDBOX_REPO_ROOT}/.venv/bin:/usr/local/bin:/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return result


def _canonical_write(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _publish_no_provider_proof(
    exp_dir: Path,
    *,
    handle: str,
    scenario_name: str,
    stripped: list[str],
) -> None:
    scenario = PROVIDER_FREE_SCENARIOS[scenario_name]
    _canonical_write(
        exp_dir / PROVIDER_FREE_PROOF,
        {
            "schema": "cvm.provider-free-execution/1",
            "job": handle,
            "scenario": {"name": scenario.name, "identity": scenario.identity},
            "execution_profile": dict(PROVIDER_FREE_EXECUTION_PROFILE),
            "sandbox": {
                "network": "isolated-loopback",
                "resource_profile": PROVIDER_FREE_EXECUTION_PROFILE["id"],
            },
            "provider_environment": {
                "allowlist": list(PROVIDER_FREE_ENV_ALLOWLIST),
                "stripped": stripped,
                "credential_values_recorded": False,
            },
            "requests": {"model_gateway": 0, "provider": 0, "tap": 0},
        },
    )


def _publish_terminal_manifest(
    exp_dir: Path,
    *,
    workload_status: int,
    final_status: int,
) -> None:
    files: list[dict[str, object]] = []
    for path in sorted(exp_dir.rglob("*")):
        relative = path.relative_to(exp_dir)
        relative_text = relative.as_posix()
        if (
            not path.is_file()
            or relative.parts[0] == ".git"
            or relative_text == "artifact_manifest.json"
            or relative_text == ".artifact_manifest.json.tmp"
        ):
            continue
        data = path.read_bytes()
        files.append(
            {
                "path": relative_text,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    temporary = exp_dir / ".artifact_manifest.json.tmp"
    temporary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workload_status": workload_status,
                "final_status": final_status,
                "files": files,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(exp_dir / "artifact_manifest.json")


def _validate_scenario_evidence(exp_dir: Path, scenario_name: str) -> None:
    """Require every Issue #37 runtime-authority evidence layer before success."""

    path = exp_dir / "run" / "runtime-authority-smoke.json"
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("runtime-authority scenario receipt is missing or invalid") from exc
    required = {
        "schema",
        "scenario_identity",
        "workspace",
        "viewer_deployment",
        "viewer_fallback",
        "native_depth_eight",
        "shipped_tree",
        "commands",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise ProviderFreeError("runtime-authority scenario receipt is not a closed object")
    scenario = PROVIDER_FREE_SCENARIOS[scenario_name]
    if (
        receipt["schema"] != "issue15.runtime-authority-smoke/1"
        or receipt["scenario_identity"] != scenario.identity
    ):
        raise ProviderFreeError("runtime-authority scenario identity conflicts")
    workspace = receipt["workspace"]
    if (
        not isinstance(workspace, dict)
        or workspace.get("path") != "."
        or workspace.get("schema") != "mesh-to-cad.workspace/1"
        or not isinstance(workspace.get("final_delivery"), dict)
    ):
        raise ProviderFreeError("runtime-authority Workspace evidence is incomplete")
    deployment = receipt["viewer_deployment"]
    artifacts = deployment.get("artifacts") if isinstance(deployment, dict) else None
    if (
        not isinstance(deployment, dict)
        or deployment.get("schema") != "cvm.viewer-runtime-deployment/1"
        or not isinstance(artifacts, list)
        or [item.get("role") for item in artifacts if isinstance(item, dict)]
        != ["launcher", "server", "client"]
        or any(
            item.get("bundle", {}).get("sha256")
            != item.get("deployed", {}).get("sha256")
            for item in artifacts
        )
    ):
        raise ProviderFreeError("runtime-authority Viewer deployment evidence is incomplete")
    fallback = receipt["viewer_fallback"]
    if (
        not isinstance(fallback, dict)
        or fallback.get("schema") != "issue15.viewer-fallback-smoke/1"
        or fallback.get("rejected_reuse", {}).get("http_status") != 400
        or fallback.get("fallback", {}).get("action") != "start"
    ):
        raise ProviderFreeError("runtime-authority Viewer fallback evidence is incomplete")
    native = receipt["native_depth_eight"]
    if (
        not isinstance(native, dict)
        or native.get("native_required") is not True
        or native.get("backend", {}).get("id")
        != "meshscope.voxblame.native-sat/1"
        or native.get("depths") != list(range(1, 9))
    ):
        raise ProviderFreeError("runtime-authority native depth-8 evidence is incomplete")
    shipped = receipt["shipped_tree"]
    files = shipped.get("files") if isinstance(shipped, dict) else None
    if (
        not isinstance(shipped, dict)
        or shipped.get("schema") != "cvm.deployed-runtime-tree-receipt/1"
        or not isinstance(files, list)
        or not files
        or shipped.get("file_count") != len(files)
    ):
        raise ProviderFreeError("runtime-authority shipped-tree evidence is incomplete")
    identity_bytes = json.dumps(files, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    if shipped.get("tree_sha256") != hashlib.sha256(identity_bytes).hexdigest():
        raise ProviderFreeError("runtime-authority shipped-tree digest conflicts")
    commands = exp_dir / str(receipt["commands"])
    if not commands.is_file() or commands.stat().st_size == 0:
        raise ProviderFreeError("runtime-authority public-command evidence is missing")
    for authority_name in ("workspace-authority.json", "workspace-authority.bundle"):
        if not (exp_dir / authority_name).is_file():
            raise ProviderFreeError("runtime-authority portable Workspace authority is missing")


def run_scenario(
    scenario_name: str,
    group: str,
    exp: str,
    *,
    environ: Mapping[str, str],
) -> int:
    _validate_request(scenario_name, group, exp)
    stripped = _validate_environment(environ)
    exp_dir = REPO_ROOT / "outputs" / group / exp
    handle = f"{group}/{exp}"
    pilot_runner.prepare_exp(exp_dir)
    bwrap = shutil.which("bwrap", path=environ.get("PATH"))
    if not bwrap:
        raise ProviderFreeError("bwrap is required for provider-free execution")
    argv = _sandbox_argv(scenario_name, exp_dir, bwrap=bwrap)
    workload_status = 1
    try:
        completed = subprocess.run(
            argv,
            check=False,
            env=_sandbox_environment(environ),
            stdin=subprocess.DEVNULL,
            timeout=WALL_TIMEOUT_SECONDS,
            preexec_fn=_apply_resource_limits,
        )
        workload_status = completed.returncode
    except subprocess.TimeoutExpired:
        workload_status = 124
    final_status = workload_status
    if workload_status == 0:
        try:
            pilot_runner.validate_workspace_delivery(exp_dir)
            pilot_runner.publish_workspace_authority(exp_dir)
            _validate_scenario_evidence(exp_dir, scenario_name)
        except (pilot_runner.PilotError, ProviderFreeError) as exc:
            print(f"provider-free-runner: {exc}", file=sys.stderr)
            final_status = pilot_runner.ARTIFACT_CONTRACT_STATUS
    _publish_no_provider_proof(
        exp_dir,
        handle=handle,
        scenario_name=scenario_name,
        stripped=stripped,
    )
    _publish_terminal_manifest(
        exp_dir,
        workload_status=workload_status,
        final_status=final_status,
    )
    return final_status


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="provider-free-runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("scenario")
    run.add_argument("group")
    run.add_argument("exp")
    args = parser.parse_args(argv)
    try:
        return run_scenario(
            args.scenario,
            args.group,
            args.exp,
            environ=os.environ if environ is None else environ,
        )
    except ProviderFreeError as exc:
        print(f"provider-free-runner: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
