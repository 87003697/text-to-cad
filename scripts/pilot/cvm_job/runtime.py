from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
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
    PROVIDER_FREE_SCENARIO_FAILURE_OPERATIONS,
    PROVIDER_FREE_SCENARIO_FAILURE_PATH,
    PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA,
    PROVIDER_FREE_SCENARIO_FAILURE_STAGES,
    ProtocolError,
    TERMINAL_STATES,
    default_state_root,
    heartbeat,
    load_state,
    log_path,
    parse_handle,
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
class _ProviderFreeTerminalManifest:
    """One parsed and closed terminal manifest shared by all validators."""

    workload_status: int
    final_status: int
    by_path: dict[str, dict[str, Any]]


PROVIDER_FREE_EXECUTION_PROFILE = {
    "schema": "cvm.provider-free-execution-profile/1",
    "id": "issue15.provider-free-bounded/1",
    "provider_access": "forbidden",
    "sandbox_profile": "cvm.provider-free-linux-sandbox/1",
}
PROVIDER_FREE_SANDBOX_PROFILE = {
    "schema": "cvm.provider-free-linux-sandbox/1",
    "namespaces": ["network", "pid", "ipc", "uts"],
    "capabilities": "drop-all",
    "die_with_parent": True,
    "new_session": True,
    "temporary_filesystem": "/tmp",
    "repository_mount": "read-only",
    "output_mount": "read-write-exact-experiment",
    "browser_cache_mount": "read-only-attested-revision",
    "resource_limits": {
        "wall_seconds": 1800,
        "cpu_seconds": 1800,
        "address_space_bytes": 16 * 1024**3,
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
    "PLAYWRIGHT_BROWSERS_PATH": deployment_authority.SANDBOX_BROWSER_CACHE,
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
) -> list[str]:
    """Build the one exact versioned provider-free bubblewrap launch contract."""

    requested_root = REPO_ROOT if repo_root is None else repo_root
    source_root = Path(requested_root).resolve(strict=True)
    try:
        exp_dir = provider_free_output.revalidate_exp_path(source_root, exp_dir)
    except provider_free_output.OutputPathError as exc:
        raise ProtocolError(f"unsafe provider-free output path: {exc}") from exc
    relative_exp = exp_dir.relative_to(source_root)
    sandbox_exp = PROVIDER_FREE_SANDBOX_REPO_ROOT / relative_exp
    bwrap = runtime_identity["bwrap"]["path"]
    chromium = runtime_identity["chromium"]
    argv = [
        bwrap,
        "--unshare-user",
        "--unshare-net",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "CAP_SYS_ADMIN",
        "--cap-add",
        "CAP_SYS_CHROOT",
        "--cap-add",
        "CAP_NET_ADMIN",
        "--cap-add",
        "CAP_SETUID",
        "--cap-add",
        "CAP_SETGID",
        "--cap-add",
        "CAP_SYS_PTRACE",
        "--cap-add",
        "CAP_SETFCAP",
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
        "--ro-bind",
        chromium["host_cache_path"],
        chromium["sandbox_cache_path"],
    ]
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
        deployment_receipt = json.loads(receipt_bytes)
        deployment_authority.verify_receipt(REPO_ROOT, deployment_receipt)
        deployment_authority.validate_runtime_identity(
            REPO_ROOT,
            deployment_receipt.get("runtime_identity"),
            verify_external=False,
        )
    except (
        OSError,
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
        proof = json.loads(proof_bytes)
    except FileNotFoundError:
        return None, "provider-free execution evidence missing"
    except (OSError, json.JSONDecodeError):
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
        retained_receipt = json.loads(retained_receipt_bytes)
        deployment_authority.verify_materialized(
            exp_dir / "run/deployed-source",
            retained_receipt,
        )
    except (
        OSError,
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
        sandbox = json.loads(
            (exp_dir / "run/sandbox-enforcement.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None, "provider-free sandbox enforcement evidence is invalid"
    argv = sandbox.get("argv") if isinstance(sandbox, dict) else None
    environment_names = (
        sandbox.get("environment_names") if isinstance(sandbox, dict) else None
    )
    expected_argv = provider_free_sandbox_argv(
        record["scenario"]["name"],
        exp_dir,
        record["request_authority"]["runtime_identity"],
    )
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
            {*PROVIDER_FREE_ENV_ALLOWLIST, "PLAYWRIGHT_BROWSERS_PATH"}
        )
        or "PLAYWRIGHT_BROWSERS_PATH" not in environment_names
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
) -> tuple[str | None, dict[str, str] | None, str | None]:
    """Validate the failure-only evidence path without requiring success files."""

    by_path = manifest.by_path
    failure_path = exp_dir / PROVIDER_FREE_SCENARIO_FAILURE_PATH
    try:
        failure_bytes = failure_path.read_bytes()
        failure = json.loads(failure_bytes)
    except (OSError, json.JSONDecodeError):
        return None, None, "provider-free scenario failure evidence is invalid"
    failure_keys = set(failure) if isinstance(failure, dict) else set()
    operation = failure.get("operation") if isinstance(failure, dict) else None
    if (
        not isinstance(failure, dict)
        or failure_keys
        not in (
            {"schema", "scenario_identity", "stage"},
            {"schema", "scenario_identity", "stage", "operation"},
        )
        or failure.get("schema") != PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA
        or failure.get("scenario_identity") != record["scenario"]["identity"]
        or failure.get("stage") not in PROVIDER_FREE_SCENARIO_FAILURE_STAGES
        or (
            operation is not None
            and (
                failure.get("stage") != "candidate_workspace"
                or operation not in PROVIDER_FREE_SCENARIO_FAILURE_OPERATIONS
            )
        )
    ):
        return None, None, "provider-free scenario failure identity conflicts"
    expected_entry = {
        "path": PROVIDER_FREE_SCENARIO_FAILURE_PATH,
        "size_bytes": len(failure_bytes),
        "sha256": hashlib.sha256(failure_bytes).hexdigest(),
    }
    if by_path.get(PROVIDER_FREE_SCENARIO_FAILURE_PATH) != expected_entry:
        return None, None, "provider-free scenario failure evidence is not bound"
    proof_path, error = _provider_free_common_evidence_result(
        exp_dir,
        handle=handle,
        record=record,
        expected_stripped=expected_stripped,
        manifest=manifest,
    )
    if error is not None:
        return None, None, error
    return proof_path, failure, None


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
            live_receipt = json.loads(receipt_bytes)
            deployment_authority.verify_receipt(REPO_ROOT, live_receipt)
            deployment_authority.validate_runtime_identity(
                REPO_ROOT,
                live_receipt.get("runtime_identity"),
                verify_external=False,
            )
        except (
            OSError,
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
                proof_path, scenario_failure, failure_error = (
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
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "artifact manifest missing"
    except (OSError, json.JSONDecodeError):
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
        manifest = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None, "artifact manifest missing"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
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
