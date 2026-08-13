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
import stat
import subprocess
import sys
from typing import Mapping

from scripts.pilot import runner as pilot_runner
from scripts.pilot import deployment_authority
from scripts.pilot import provider_free_output
from scripts.pilot.cvm_job.runtime import (
    AttestedBrowserMount,
    BrowserStageError,
    PROVIDER_FREE_ENV_ALLOWLIST,
    PROVIDER_FREE_EXECUTION_PROFILE,
    PROVIDER_FREE_PROOF,
    PROVIDER_FREE_REQUIRED_ENVIRONMENT,
    PROVIDER_FREE_SANDBOX_PROFILE,
    PROVIDER_FREE_SCENARIOS,
    PROVIDER_FREE_SUPERVISOR_LOCALE,
    provider_free_sandbox_argv,
    staged_attested_browser,
)
from scripts.pilot.cvm_job.protocol import (
    PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH,
    PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_OPERATIONS,
    PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH,
    PROVIDER_FREE_PREVIEW_SANDBOX_PATH,
    PROVIDER_FREE_SCENARIO_FAILURE_PATH,
    PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA,
    PROVIDER_FREE_SCENARIO_FAILURE_STAGES,
    ProtocolError,
    provider_free_browser_exec_diagnostic_allowed,
    provider_free_browser_exec_diagnostic_matches_operation,
    provider_free_preview_public_wrapper_allowed,
    provider_free_preview_public_wrapper_matches_operation,
    provider_free_preview_sandbox_receipt_allowed,
    provider_free_scenario_failure_operation_allowed,
    request_authority_sha256,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
_RESOURCE_LIMITS = PROVIDER_FREE_SANDBOX_PROFILE["resource_limits"]
WALL_TIMEOUT_SECONDS = _RESOURCE_LIMITS["wall_seconds"]
CPU_LIMIT_SECONDS = _RESOURCE_LIMITS["cpu_seconds"]
ADDRESS_SPACE_LIMIT_BYTES = _RESOURCE_LIMITS["address_space_bytes"]
FILE_SIZE_LIMIT_BYTES = _RESOURCE_LIMITS["file_size_bytes"]
OPEN_FILE_LIMIT = _RESOURCE_LIMITS["open_files"]
PROCESS_LIMIT = _RESOURCE_LIMITS["processes"]
_CONTROL_ENVIRONMENT = frozenset(
    {
        *PROVIDER_FREE_ENV_ALLOWLIST,
        *PROVIDER_FREE_SUPERVISOR_LOCALE,
        "LC_CTYPE",
        "__CF_USER_TEXT_ENCODING",
        "CVM_PROVIDER_FREE_PROFILE",
        "CVM_PROVIDER_FREE_STRIPPED_NAMES",
        "CVM_PROVIDER_FREE_JOB",
        "CVM_PROVIDER_FREE_REQUEST_AUTHORITY_SHA256",
        "CVM_PROVIDER_FREE_DEPLOYMENT_TREE_SHA256",
        "CVM_PROVIDER_FREE_REQUEST_JSON",
    }
)


class ProviderFreeError(RuntimeError):
    """The provider-free scenario cannot satisfy its fixed execution contract."""


def _validate_interpreter_environment(environ: Mapping[str, str]) -> None:
    """Validate the minimal process variables CPython may add at startup."""

    lc_ctype = environ.get("LC_CTYPE")
    expected_lc_ctype = {
        "darwin": "UTF-8",
        "linux": "C.UTF-8",
    }.get("linux" if sys.platform.startswith("linux") else sys.platform)
    if lc_ctype is not None and lc_ctype != expected_lc_ctype:
        raise ProviderFreeError("provider-free interpreter LC_CTYPE is invalid")
    cf_encoding = environ.get("__CF_USER_TEXT_ENCODING")
    if cf_encoding is not None:
        expected_user = f"0x{os.getuid():X}"
        components = cf_encoding.split(":")
        valid_components = (
            sys.platform == "darwin"
            and len(components) == 3
            and components[0] == expected_user
            and tuple(components[1:])
            in {("0x0", "0x0"), ("0x19", "0x34")}
        )
        if not valid_components:
            raise ProviderFreeError(
                "provider-free interpreter __CF_USER_TEXT_ENCODING is invalid"
            )


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
    for name, expected in PROVIDER_FREE_SUPERVISOR_LOCALE.items():
        if environ.get(name) != expected:
            raise ProviderFreeError(
                f"provider-free deterministic locale {name} is missing or invalid"
            )
    _validate_interpreter_environment(environ)
    raw = environ.get("CVM_PROVIDER_FREE_STRIPPED_NAMES", "")
    stripped = raw.split(",") if raw else []
    if stripped != sorted(set(stripped)) or any(not name for name in stripped):
        raise ProviderFreeError("provider-free stripped-name receipt is invalid")
    for name in (
        "CVM_PROVIDER_FREE_REQUEST_AUTHORITY_SHA256",
        "CVM_PROVIDER_FREE_DEPLOYMENT_TREE_SHA256",
    ):
        value = environ.get(name, "")
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ProviderFreeError(f"provider-free {name} is missing or invalid")
    try:
        immutable_request = json.loads(
            environ.get("CVM_PROVIDER_FREE_REQUEST_JSON", "")
        )
    except json.JSONDecodeError as exc:
        raise ProviderFreeError("provider-free immutable request is invalid") from exc
    if not isinstance(immutable_request, dict):
        raise ProviderFreeError("provider-free immutable request is invalid")
    if request_authority_sha256(immutable_request) != environ[
        "CVM_PROVIDER_FREE_REQUEST_AUTHORITY_SHA256"
    ]:
        raise ProviderFreeError("provider-free immutable request digest conflicts")
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
        if hard != resource.RLIM_INFINITY and hard < requested:
            raise ProviderFreeError("host resource ceiling is below sandbox profile")
        resource.setrlimit(resource_id, (requested, requested))


def _trusted_runtime(environ: Mapping[str, str]) -> dict[str, object]:
    """Remeasure the immutable trusted runtime before sandbox launch."""

    immutable = json.loads(environ["CVM_PROVIDER_FREE_REQUEST_JSON"])
    request = immutable.get("request_authority")
    identity = request.get("runtime_identity") if isinstance(request, dict) else None
    bwrap = identity.get("bwrap") if isinstance(identity, dict) else None
    resolved = shutil.which("bwrap", path=environ.get("PATH"))
    if not isinstance(bwrap, dict) or resolved != bwrap.get("path"):
        raise ProviderFreeError("PATH bwrap does not match trusted system runtime")
    try:
        deployment_authority.validate_runtime_identity(
            REPO_ROOT,
            identity,
            verify_external=True,
        )
    except deployment_authority.DeploymentAuthorityError as exc:
        raise ProviderFreeError(f"trusted runtime identity is invalid: {exc}") from exc
    return identity


def _sandbox_argv(
    scenario_name: str,
    exp_dir: Path,
    *,
    bwrap: str,
    runtime_identity: Mapping[str, object],
    browser_mount: AttestedBrowserMount | None = None,
) -> list[str]:
    identity = dict(runtime_identity)
    if bwrap != identity.get("bwrap", {}).get("path"):
        raise ProviderFreeError("sandbox bwrap conflicts with runtime identity")
    try:
        return provider_free_sandbox_argv(
            scenario_name,
            exp_dir,
            identity,
            repo_root=REPO_ROOT,
            browser_mount=browser_mount,
        )
    except ProtocolError as exc:
        raise ProviderFreeError(f"unsafe provider-free sandbox output: {exc}") from exc


def _sandbox_environment(environ: Mapping[str, str]) -> dict[str, str]:
    result = {
        name: environ[name]
        for name in PROVIDER_FREE_ENV_ALLOWLIST
        if environ.get(name)
    }
    result.update(PROVIDER_FREE_REQUIRED_ENVIRONMENT)
    return result


def _canonical_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    environ: Mapping[str, str],
) -> None:
    scenario = PROVIDER_FREE_SCENARIOS[scenario_name]
    sandbox_path = exp_dir / "run/sandbox-enforcement.json"
    sandbox_bytes = sandbox_path.read_bytes()
    _canonical_write(
        exp_dir / PROVIDER_FREE_PROOF,
        {
            "schema": "cvm.provider-free-execution/1",
            "job": handle,
            "scenario": {"name": scenario.name, "identity": scenario.identity},
            "execution_profile": dict(PROVIDER_FREE_EXECUTION_PROFILE),
            "request_authority": {
                "sha256": environ[
                    "CVM_PROVIDER_FREE_REQUEST_AUTHORITY_SHA256"
                ],
                "deployment_tree_sha256": environ[
                    "CVM_PROVIDER_FREE_DEPLOYMENT_TREE_SHA256"
                ],
                "immutable_request": json.loads(
                    environ["CVM_PROVIDER_FREE_REQUEST_JSON"]
                ),
            },
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
            "sandbox_enforcement": {
                "path": "run/sandbox-enforcement.json",
                "sha256": hashlib.sha256(sandbox_bytes).hexdigest(),
            },
        },
    )


def _publish_terminal_manifest(
    exp_dir: Path,
    *,
    workload_status: int,
    final_status: int,
) -> None:
    try:
        exp_dir = provider_free_output.revalidate_exp_path(REPO_ROOT, exp_dir)
    except provider_free_output.OutputPathError as exc:
        raise ProviderFreeError(f"unsafe provider-free output path: {exc}") from exc
    files: list[dict[str, object]] = []
    for path in sorted(exp_dir.rglob("*")):
        relative = path.relative_to(exp_dir)
        relative_text = relative.as_posix()
        if relative.parts[0] == ".git":
            continue
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise ProviderFreeError(
                f"terminal manifest cannot inspect: {relative_text}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise ProviderFreeError(
                f"terminal manifest rejects symlink: {relative_text}"
            )
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ProviderFreeError(
                f"terminal manifest rejects special file: {relative_text}"
            )
        if relative_text in {
            "artifact_manifest.json",
            ".artifact_manifest.json.tmp",
        }:
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


def _retain_deployment_authority(exp_dir: Path) -> dict[str, object]:
    """Retain actual deployed execution files so review can rehash them."""

    receipt_path = REPO_ROOT / deployment_authority.RECEIPT_PATH
    try:
        receipt_bytes = receipt_path.read_bytes()
        receipt = json.loads(receipt_bytes)
        deployment_authority.verify_receipt(REPO_ROOT, receipt)
        if receipt.get("contract_paths") != list(
            deployment_authority.EXECUTION_AUTHORITY_PATHS
        ):
            raise deployment_authority.DeploymentAuthorityError(
                "deployed source authority contract is incomplete"
            )
        deployment_authority.materialize_receipt(
            REPO_ROOT,
            receipt,
            exp_dir / "run/deployed-source",
        )
    except (
        OSError,
        json.JSONDecodeError,
        deployment_authority.DeploymentAuthorityError,
    ) as exc:
        raise ProviderFreeError("deployed source authority retention failed") from exc
    (exp_dir / "run/deployed-source-authority.json").write_bytes(receipt_bytes)
    return receipt


def _publish_sandbox_enforcement(
    exp_dir: Path,
    *,
    argv: list[str],
    child_environment: Mapping[str, str],
    runtime_identity: Mapping[str, object],
) -> None:
    """Retain the exact namespace/resource launch boundary without values."""

    _canonical_write(
        exp_dir / "run/sandbox-enforcement.json",
        {
            "schema": "cvm.provider-free-sandbox-enforcement/1",
            "network": "isolated-loopback",
            "argv": argv,
            "environment_names": sorted(child_environment),
            "required_environment": PROVIDER_FREE_REQUIRED_ENVIRONMENT,
            "sandbox_profile": PROVIDER_FREE_SANDBOX_PROFILE,
            "runtime_identity": runtime_identity,
        },
    )


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
        "cadpy_runtime",
        "shipped_tree",
        "commands",
        "preview_sandbox",
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
    if receipt["preview_sandbox"] != PROVIDER_FREE_PREVIEW_SANDBOX_PATH:
        raise ProviderFreeError("runtime-authority preview sandbox path conflicts")
    try:
        preview_sandbox = json.loads(
            (exp_dir / PROVIDER_FREE_PREVIEW_SANDBOX_PATH).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError(
            "runtime-authority preview sandbox evidence is missing or invalid"
        ) from exc
    if not provider_free_preview_sandbox_receipt_allowed(
        preview_sandbox,
        exp_dir.parent.name,
        exp_dir.name,
    ):
        raise ProviderFreeError(
            "runtime-authority preview sandbox evidence conflicts"
        )
    try:
        browser_exec_diagnostic = json.loads(
            (exp_dir / PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError(
            "runtime-authority browser exec diagnostic is missing or invalid"
        ) from exc
    if not provider_free_browser_exec_diagnostic_allowed(
        browser_exec_diagnostic
    ) or browser_exec_diagnostic["playwright"] != "passed":
        raise ProviderFreeError(
            "runtime-authority browser exec diagnostic conflicts"
        )
    try:
        public_wrapper = json.loads(
            (exp_dir / PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError(
            "runtime-authority preview public wrapper is missing or invalid"
        ) from exc
    if (
        not provider_free_preview_public_wrapper_allowed(public_wrapper)
        or public_wrapper["operation"] != "passed"
    ):
        raise ProviderFreeError(
            "runtime-authority preview public wrapper conflicts"
        )
    for authority_name in ("workspace-authority.json", "workspace-authority.bundle"):
        if not (exp_dir / authority_name).is_file():
            raise ProviderFreeError("runtime-authority portable Workspace authority is missing")


def _validate_scenario_failure_evidence(exp_dir: Path, scenario_name: str) -> None:
    """Require one exact, closed scenario failure receipt on nonzero exit."""

    path = exp_dir / PROVIDER_FREE_SCENARIO_FAILURE_PATH
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError(
            "provider-free scenario failure receipt is missing or invalid"
        ) from exc
    scenario = PROVIDER_FREE_SCENARIOS[scenario_name]
    receipt_keys = set(receipt) if isinstance(receipt, dict) else set()
    operation = receipt.get("operation") if isinstance(receipt, dict) else None
    if (
        not isinstance(receipt, dict)
        or receipt_keys
        not in (
            {"schema", "scenario_identity", "stage"},
            {"schema", "scenario_identity", "stage", "operation"},
        )
        or receipt.get("schema") != PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA
        or receipt.get("scenario_identity") != scenario.identity
        or receipt.get("stage") not in PROVIDER_FREE_SCENARIO_FAILURE_STAGES
        or (
            operation is not None
            and not provider_free_scenario_failure_operation_allowed(
                receipt.get("stage"), operation
            )
        )
    ):
        raise ProviderFreeError(
            "provider-free scenario failure receipt conflicts with request"
        )
    diagnostic_operations = {
        "preview_browser_outer_exec_probe",
        "preview_browser_nested_exec_probe",
        "preview_browser_playwright_launch_after_direct_probes",
    }
    if operation in diagnostic_operations or (
        isinstance(operation, str)
        and operation.startswith("preview_browser_node_")
    ):
        try:
            browser_exec_diagnostic = json.loads(
                (
                    exp_dir / PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH
                ).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderFreeError(
                "provider-free browser exec diagnostic is missing or invalid"
            ) from exc
        if not provider_free_browser_exec_diagnostic_matches_operation(
            browser_exec_diagnostic,
            operation,
        ):
            raise ProviderFreeError(
                "provider-free browser exec diagnostic conflicts"
            )
    if operation in PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_OPERATIONS:
        try:
            public_wrapper = json.loads(
                (
                    exp_dir / PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH
                ).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderFreeError(
                "provider-free preview public wrapper is missing or invalid"
            ) from exc
        if not provider_free_preview_public_wrapper_matches_operation(
            public_wrapper,
            operation,
        ):
            raise ProviderFreeError(
                "provider-free preview public wrapper conflicts"
            )


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
    if environ.get("CVM_PROVIDER_FREE_JOB") != handle:
        raise ProviderFreeError("provider-free job identity conflicts with request")
    try:
        existing_exp, existed = provider_free_output.physical_exp_path(
            REPO_ROOT,
            group,
            exp,
            create_exp=False,
        )
        if existed:
            raise provider_free_output.OutputPathError(
                f"provider-free experiment must be new: {existing_exp}"
            )
    except provider_free_output.OutputPathError as exc:
        raise ProviderFreeError(f"unsafe provider-free output path: {exc}") from exc
    runtime_identity = _trusted_runtime(environ)
    try:
        exp_dir, existed = provider_free_output.physical_exp_path(
            REPO_ROOT,
            group,
            exp,
            create_exp=True,
        )
        if existed:
            raise provider_free_output.OutputPathError(
                f"provider-free experiment creation lost race: {exp_dir}"
            )
        exp_dir = provider_free_output.require_empty_exp_path(REPO_ROOT, exp_dir)
    except provider_free_output.OutputPathError as exc:
        raise ProviderFreeError(f"unsafe provider-free output path: {exc}") from exc
    pilot_runner.prepare_exp(exp_dir)
    try:
        exp_dir = provider_free_output.revalidate_exp_path(REPO_ROOT, exp_dir)
    except provider_free_output.OutputPathError as exc:
        raise ProviderFreeError(f"unsafe provider-free output path: {exc}") from exc
    try:
        _retain_deployment_authority(exp_dir)
    except ProviderFreeError as exc:
        print(f"provider-free-runner: {exc}", file=sys.stderr)
        contract_status = pilot_runner.ARTIFACT_CONTRACT_STATUS
        _publish_terminal_manifest(
            exp_dir,
            workload_status=contract_status,
            final_status=contract_status,
        )
        return contract_status
    child_environment = _sandbox_environment(environ)
    workload_status = 1
    try:
        with staged_attested_browser(
            runtime_identity["chromium"],
            handle,
            repo_root=REPO_ROOT,
        ) as browser_mount:
            bwrap = runtime_identity["bwrap"]["path"]
            argv = _sandbox_argv(
                scenario_name,
                exp_dir,
                bwrap=bwrap,
                runtime_identity=runtime_identity,
                browser_mount=browser_mount,
            )
            completed = subprocess.run(
                argv,
                check=False,
                env=child_environment,
                stdin=subprocess.DEVNULL,
                timeout=WALL_TIMEOUT_SECONDS,
                preexec_fn=_apply_resource_limits,
            )
            workload_status = completed.returncode
    except BrowserStageError as exc:
        raise ProviderFreeError("trusted browser staging failed") from exc
    except subprocess.TimeoutExpired:
        workload_status = 124
    try:
        exp_dir = provider_free_output.revalidate_exp_path(REPO_ROOT, exp_dir)
    except provider_free_output.OutputPathError as exc:
        raise ProviderFreeError(f"unsafe provider-free output path: {exc}") from exc
    _publish_sandbox_enforcement(
        exp_dir,
        argv=argv,
        child_environment=child_environment,
        runtime_identity=runtime_identity,
    )
    final_status = workload_status
    if workload_status == 0:
        try:
            pilot_runner.validate_workspace_delivery(exp_dir)
            pilot_runner.publish_workspace_authority(exp_dir)
            _validate_scenario_evidence(exp_dir, scenario_name)
        except (pilot_runner.PilotError, ProviderFreeError) as exc:
            print(f"provider-free-runner: {exc}", file=sys.stderr)
            final_status = pilot_runner.ARTIFACT_CONTRACT_STATUS
    else:
        try:
            _validate_scenario_failure_evidence(exp_dir, scenario_name)
        except ProviderFreeError as exc:
            print(f"provider-free-runner: {exc}", file=sys.stderr)
            final_status = pilot_runner.ARTIFACT_CONTRACT_STATUS
    _publish_no_provider_proof(
        exp_dir,
        handle=handle,
        scenario_name=scenario_name,
        stripped=stripped,
        environ=environ,
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
