from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
TERMINAL_STATES = frozenset({"succeeded", "failed"})
_TRANSITIONS = {
    "submitted": {"running", "failed"},
    "running": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
}
PILOT_STATES = frozenset(_TRANSITIONS)
_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PROVIDER_FREE_BOOTSTRAP_PHASES = frozenset(
    {"before-experiment", "before-artifact-manifest"}
)
_PROVIDER_FREE_BOOTSTRAP_CLASSIFICATIONS = frozenset(
    {
        "python-import-failed",
        "runner-bwrap-path-rejected",
        "runner-contract-rejected",
        "runner-environment-allowlist-rejected",
        "runner-entrypoint-unavailable",
        "runner-execution-profile-rejected",
        "runner-exited-before-artifact-manifest",
        "runner-output-path-rejected",
        "runner-request-digest-rejected",
        "runner-runtime-identity-rejected",
        "runner-stripped-name-receipt-rejected",
        "runner-terminated-before-artifact-manifest",
        "runner-completed-without-artifact-manifest",
    }
)
PROVIDER_FREE_SCENARIO_FAILURE_PATH = "run/scenario-failure.json"
PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA = "cvm.provider-free-scenario-failure/1"
PROVIDER_FREE_PREVIEW_SANDBOX_PATH = "run/preview-sandbox-enforcement.json"
PROVIDER_FREE_PREVIEW_SANDBOX_SCHEMA = (
    "cvm.provider-free-preview-sandbox-enforcement/1"
)
PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_PATH = (
    "run/browser-exec-diagnostic.json"
)
PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_SCHEMA = (
    "cvm.provider-free-browser-exec-diagnostic/5"
)
PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_PATH = (
    "run/browser-identity-diagnostic.json"
)
PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA = (
    "cvm.provider-free-browser-identity-diagnostic/2"
)
PROVIDER_FREE_BROWSER_IDENTITY_SUBSTAGES = frozenset(
    {
        "private_snapshot_launch_image_identity",
        "live_running_image_identity",
        "loopback_listener_address_ownership",
        "connected_cdp_browser_version_identity",
        "runtime_evidence_cross_binding",
    }
)
PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES = frozenset(
    {
        "source_executable_identity",
        "private_tree_materialization",
        "private_launch_image_identity",
        "playwright_package_revision_identity",
        "private_launch_version_execution",
        "private_launch_version_output_identity",
    }
)
PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_PATH = (
    "run/preview-public-wrapper-diagnostic.json"
)
PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA = (
    "cvm.provider-free-preview-public-wrapper/1"
)
PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_EVIDENCE_PUBLICATION_OPERATION = (
    "preview_public_wrapper_evidence_publication"
)
PROVIDER_FREE_PREVIEW_PUBLIC_FAILURE_OPERATIONS = frozenset(
    {
        "preview_public_sandbox_setup",
        "preview_public_spawn",
        "preview_public_timeout",
        "preview_public_unclassified_exit",
        "preview_public_result_shape",
        "preview_public_command_evidence_publication",
        "preview_public_failure_diagnostic_publication",
        "preview_public_success_diagnostic_publication",
    }
)
PROVIDER_FREE_STAGED_BROWSER_CACHE = "/tmp/provider-free-playwright"
PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE = (
    f"{PROVIDER_FREE_STAGED_BROWSER_CACHE}/attested/"
    "chrome-headless-shell-linux64/chrome-headless-shell"
)
PROVIDER_FREE_BROWSER_RUNTIME_SCHEMA = "meshshot.prelaunched-cdp-runtime/1"
PROVIDER_FREE_BROWSER_ADAPTER_PROFILE = "playwright-1.60-chromium-1223-loopback-cdp/1"
PROVIDER_FREE_BROWSER_ADAPTER_PROFILE_SHA256 = (
    "16ef68d9ee9700f10c9e92b6ca88c0430dc98c6808145258f9a6125f3acd5c04"
)


def provider_free_browser_runtime_allowed(
    receipt: object,
    *,
    expected_browser_sha256: object,
) -> bool:
    """Validate the closed production browser result without lifecycle internals."""

    if not isinstance(receipt, dict) or set(receipt) != {
        "schema",
        "adapter_profile",
        "browser_identity",
        "result",
    }:
        return False
    adapter = receipt.get("adapter_profile")
    browser = receipt.get("browser_identity")
    return (
        receipt.get("schema") == PROVIDER_FREE_BROWSER_RUNTIME_SCHEMA
        and receipt.get("result") == "passed"
        and isinstance(adapter, dict)
        and set(adapter) == {"name", "sha256"}
        and adapter.get("name") == PROVIDER_FREE_BROWSER_ADAPTER_PROFILE
        and adapter.get("sha256") == PROVIDER_FREE_BROWSER_ADAPTER_PROFILE_SHA256
        and isinstance(browser, dict)
        and set(browser)
        == {"playwright", "browser", "revision", "version", "sha256"}
        and browser.get("playwright") == "1.60.0"
        and browser.get("browser") == "chromium-headless-shell"
        and browser.get("revision") == "1223"
        and browser.get("version") == "Google Chrome for Testing 148.0.7778.96"
        and isinstance(expected_browser_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", expected_browser_sha256) is not None
        and browser.get("sha256") == expected_browser_sha256
    )


def provider_free_preview_sandbox_argv(group: str, exp: str) -> list[str]:
    """Return the exact in-sandbox preview launch bound to one experiment."""

    sandbox_root = "/workspace/repo"
    sandbox_exp = f"{sandbox_root}/outputs/{group}/{exp}"
    return [
        "/usr/bin/bwrap",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--bind",
        "/",
        "/",
        "--ro-bind",
        PROVIDER_FREE_STAGED_BROWSER_CACHE,
        PROVIDER_FREE_STAGED_BROWSER_CACHE,
        "--setenv",
        "PLAYWRIGHT_BROWSERS_PATH",
        PROVIDER_FREE_STAGED_BROWSER_CACHE,
        "--setenv",
        "MESHSHOT_BROWSER_EXECUTABLE",
        PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE,
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


def provider_free_preview_public_wrapper_allowed(receipt: object) -> bool:
    """Validate one closed public-wrapper result without raw process data."""

    return (
        isinstance(receipt, dict)
        and set(receipt) == {"schema", "operation"}
        and receipt.get("schema")
        == PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_SCHEMA
        and receipt.get("operation")
        in ({"passed"} | PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_OPERATIONS)
    )


def provider_free_preview_public_wrapper_matches_operation(
    receipt: object, operation: object
) -> bool:
    """Require the wrapper receipt to name the exact terminal operation."""

    return (
        provider_free_preview_public_wrapper_allowed(receipt)
        and operation in PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_OPERATIONS
        and receipt.get("operation") == operation
    )


def provider_free_preview_sandbox_receipt_allowed(
    receipt: object,
    group: str,
    exp: str,
) -> bool:
    """Validate one closed per-run child capability enforcement receipt."""

    return (
        isinstance(receipt, dict)
        and set(receipt)
        == {"schema", "argv", "capabilities", "mount_namespace"}
        and receipt.get("schema") == PROVIDER_FREE_PREVIEW_SANDBOX_SCHEMA
        and receipt.get("argv") == provider_free_preview_sandbox_argv(group, exp)
        and receipt.get("capabilities") == "drop-all"
        and receipt.get("mount_namespace") == "inherit-outer"
    )


def provider_free_browser_exec_diagnostic_allowed(receipt: object) -> bool:
    """Validate direct probes plus the Python-owned CDP runtime outcome."""

    if (
        not isinstance(receipt, dict)
        or set(receipt)
        != {
            "schema",
            "executable",
            "probe",
            "outer",
            "nested",
            "node_attached",
            "node_detached",
            "node_failure_kind",
            "prelaunched_cdp",
        }
        or receipt.get("schema")
        != PROVIDER_FREE_BROWSER_EXEC_DIAGNOSTIC_SCHEMA
        or receipt.get("executable")
        != PROVIDER_FREE_STAGED_BROWSER_EXECUTABLE
        or receipt.get("probe") != "chromium-version-immediate-exit"
    ):
        return False
    outcomes = (
        receipt.get("outer"),
        receipt.get("nested"),
        receipt.get("node_attached"),
        receipt.get("node_detached"),
        receipt.get("prelaunched_cdp"),
    )
    if outcomes not in {
        ("failed", "not-run", "not-run", "not-run", "not-run"),
        ("passed", "failed", "not-run", "not-run", "not-run"),
        ("passed", "passed", "not-run", "not-run", "failed"),
        ("passed", "passed", "not-run", "not-run", "passed"),
    }:
        return False
    return receipt.get("node_failure_kind") == "not-run"


def provider_free_browser_identity_diagnostic_allowed(
    receipt: object,
    *,
    expected_failure_sha256: object,
    expected_substage: object,
    expected_phase: object = None,
) -> bool:
    """Validate one closed first-failing identity substage and failure binding."""

    if not isinstance(receipt, dict):
        return False
    substage = receipt.get("substage")
    expected_keys = {
        "schema",
        "operation",
        "substage",
        "scenario_failure",
    }
    if substage == "private_snapshot_launch_image_identity":
        expected_keys.add("phase")
    if set(receipt) != expected_keys:
        return False
    failure = receipt.get("scenario_failure")
    return (
        receipt.get("schema")
        == PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA
        and receipt.get("operation") == "preview_browser_identity"
        and receipt.get("substage") in PROVIDER_FREE_BROWSER_IDENTITY_SUBSTAGES
        and receipt.get("substage") == expected_substage
        and (
            (
                substage == "private_snapshot_launch_image_identity"
                and receipt.get("phase")
                in PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES
                and receipt.get("phase") == expected_phase
            )
            or (
                substage != "private_snapshot_launch_image_identity"
                and expected_phase is None
            )
        )
        and isinstance(failure, dict)
        and set(failure) == {"path", "sha256"}
        and failure.get("path") == PROVIDER_FREE_SCENARIO_FAILURE_PATH
        and isinstance(expected_failure_sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", expected_failure_sha256) is not None
        and failure.get("sha256") == expected_failure_sha256
    )


def provider_free_browser_exec_diagnostic_matches_operation(
    receipt: object,
    operation: object,
) -> bool:
    """Bind one diagnostic failure operation to its exact closed outcomes."""

    if not provider_free_browser_exec_diagnostic_allowed(receipt):
        return False
    expected_outcomes = {
        "preview_browser_outer_exec_probe": (
            "failed",
            "not-run",
            "not-run",
            "not-run",
            "not-run",
        ),
        "preview_browser_nested_exec_probe": (
            "passed",
            "failed",
            "not-run",
            "not-run",
            "not-run",
        ),
    }.get(operation)
    return expected_outcomes == (
        receipt.get("outer"),
        receipt.get("nested"),
        receipt.get("node_attached"),
        receipt.get("node_detached"),
        receipt.get("prelaunched_cdp"),
    ) and receipt.get("node_failure_kind") == "not-run"


PROVIDER_FREE_SCENARIO_FAILURE_STAGES = frozenset(
    {
        "viewer_deployment",
        "shipped_tree",
        "cadpy_runtime",
        "viewer_fallback",
        "candidate_workspace",
        "native_measurement",
        "finalization",
    }
)
PROVIDER_FREE_SCENARIO_FAILURE_OPERATIONS_BY_STAGE = {
    "candidate_workspace": frozenset(
        {
        "fixture_availability",
        "canonical_build",
        "reference_preparation",
        "workspace_init",
        }
    ),
    "native_measurement": frozenset(
        {
            "attempt_begin",
            "voxblame_measure",
            "native_evidence",
            "voxblame_preview",
            "preview_runtime",
            "preview_browser_runtime_staging",
            "preview_browser_outer_exec_probe",
            "preview_browser_nested_exec_probe",
            "preview_dependency",
            "preview_browser_launch",
            "preview_browser_launch_process_limit",
            "preview_browser_launch_file_limit",
            "preview_browser_launch_address_space",
            "preview_browser_launch_shared_memory",
            "preview_browser_launch_executable",
            "preview_browser_launch_executable_missing",
            "preview_browser_launch_executable_permission",
            "preview_browser_launch_executable_spawn_permission",
            "preview_browser_launch_sandbox_permission",
            "preview_browser_launch_filesystem_permission",
            "preview_browser_launch_executable_dependency",
            "preview_browser_render",
            "preview_browser_result",
            "preview_browser_adapter_profile",
            "preview_browser_identity",
            "preview_browser_profile",
            "preview_browser_prelaunch",
            "preview_browser_readiness",
            "preview_browser_readiness_timeout",
            "preview_browser_connect",
            "preview_browser_cleanup",
            "preview_browser_signal",
            "preview_browser_runtime_evidence",
            *PROVIDER_FREE_PREVIEW_PUBLIC_FAILURE_OPERATIONS,
            PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_EVIDENCE_PUBLICATION_OPERATION,
            "step_publication",
        }
    ),
}
PROVIDER_FREE_PREVIEW_PUBLIC_WRAPPER_OPERATIONS = frozenset(
    {
        *PROVIDER_FREE_PREVIEW_PUBLIC_FAILURE_OPERATIONS,
        "preview_runtime",
        "preview_dependency",
        "preview_browser_launch",
        "preview_browser_launch_process_limit",
        "preview_browser_launch_file_limit",
        "preview_browser_launch_address_space",
        "preview_browser_launch_shared_memory",
        "preview_browser_launch_executable",
        "preview_browser_launch_executable_missing",
        "preview_browser_launch_executable_permission",
        "preview_browser_launch_executable_spawn_permission",
        "preview_browser_launch_sandbox_permission",
        "preview_browser_launch_filesystem_permission",
        "preview_browser_launch_executable_dependency",
        "preview_browser_render",
        "preview_browser_result",
        "preview_browser_adapter_profile",
        "preview_browser_identity",
        "preview_browser_profile",
        "preview_browser_prelaunch",
        "preview_browser_readiness",
        "preview_browser_readiness_timeout",
        "preview_browser_connect",
        "preview_browser_cleanup",
        "preview_browser_signal",
        "preview_browser_runtime_evidence",
    }
)
_RESERVED_UPDATE_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "job",
        "group",
        "exp",
        "state",
        "submitted_at",
        "started_at",
        "updated_at",
        "heartbeat_at",
        "finished_at",
        "job_kind",
        "object",
        "exp_dir",
        "scenario",
        "execution_profile",
        "request_authority",
        "request_authority_sha256",
    }
)
_IMMUTABLE_REQUEST_FIELDS = frozenset(
    {
        "job_kind",
        "object",
        "group",
        "exp",
        "exp_dir",
        "scenario",
        "execution_profile",
        "request_authority",
        "request_authority_sha256",
    }
)


class ProtocolError(ValueError):
    """Invalid job handle, schema, state, or transition."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_component(value: str, label: str = "component") -> str:
    if not isinstance(value, str) or not _COMPONENT.fullmatch(value):
        raise ProtocolError(f"unsafe {label}: {value!r}")
    if value in {".", ".."}:
        raise ProtocolError(f"unsafe {label}: {value!r}")
    return value


def parse_handle(handle: str) -> dict[str, str]:
    if not isinstance(handle, str):
        raise ProtocolError("job handle must be a string")
    parts = handle.split("/")
    if len(parts) == 2 and parts[0] != "batch":
        group = validate_component(parts[0], "group")
        exp = validate_component(parts[1], "exp")
        return {
            "kind": "pilot",
            "group": group,
            "exp": exp,
            "job": f"{group}/{exp}",
        }
    raise ProtocolError(f"invalid job handle: {handle!r}")


def default_state_root(repo_root: Path | None = None) -> Path:
    override = os.environ.get("CVM_JOB_STATE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    return repo_root / ".cvm-jobs"


def state_path(root: Path, handle: str) -> Path:
    parsed = parse_handle(handle)
    return root / "pilots" / parsed["group"] / f"{parsed['exp']}.json"


def log_path(root: Path, handle: str) -> Path:
    parsed = parse_handle(handle)
    return root / "logs" / parsed["group"] / f"{parsed['exp']}.log"


def _validate_common(state: dict[str, Any]) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ProtocolError("unsupported job schema")
    parsed = parse_handle(state.get("job"))
    if parsed["kind"] != state.get("kind"):
        raise ProtocolError("job kind does not match handle")
    state_name = state.get("state")
    if state_name not in PILOT_STATES:
        raise ProtocolError(f"invalid state: {state_name!r}")
    if state.get("group") != parsed["group"]:
        raise ProtocolError("group does not match handle")
    if state.get("exp") != parsed["exp"]:
        raise ProtocolError("exp does not match handle")
    if state.get("job_kind") == "provider-free":
        expected = request_authority_sha256(state)
        if state.get("request_authority_sha256") != expected:
            raise ProtocolError("provider-free request authority digest conflicts")


def request_authority_sha256(state: dict[str, Any]) -> str:
    """Digest the complete immutable provider-free dispatch request."""

    payload = request_authority_payload(state)
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(
        b"cvm.provider-free-request-authority/1\0" + data
    ).hexdigest()


def request_authority_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical immutable request retained by terminal evidence."""

    return {
        field: state.get(field)
        for field in (
            "job_kind",
            "object",
            "group",
            "exp",
            "exp_dir",
            "scenario",
            "execution_profile",
            "request_authority",
        )
    }


def validate_state(state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ProtocolError("job state must be an object")
    _validate_common(state)
    return state


def _validate_updates(updates: dict[str, Any]) -> None:
    reserved = sorted(_RESERVED_UPDATE_FIELDS.intersection(updates))
    if reserved:
        raise ProtocolError(f"reserved update fields: {', '.join(reserved)}")


def _sync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    validate_state(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        _sync_directory(path.parent)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def load_state(root: Path, handle: str) -> dict[str, Any]:
    path = state_path(root, handle)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ProtocolError(f"job not found: {handle}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ProtocolError(f"invalid job record: {handle}") from error
    return validate_state(payload)


def publish_state(root: Path, state: dict[str, Any]) -> None:
    path = state_path(root, state.get("job"))
    if path.exists():
        previous = load_state(root, state["job"])
        changed = sorted(
            field
            for field in _IMMUTABLE_REQUEST_FIELDS
            if state.get(field) != previous.get(field)
        )
        if changed:
            raise ProtocolError(
                "immutable request authority fields: " + ", ".join(changed)
            )
        old = previous["state"]
        new = state["state"]
        if old in TERMINAL_STATES:
            if state != previous:
                raise ProtocolError(f"terminal job record is immutable: {state['job']}")
            return
        if new != old and new not in _TRANSITIONS[old]:
            raise ProtocolError(f"invalid transition: {old} -> {new}")
    atomic_write_json(path, state)


def transition(
    root: Path,
    handle: str,
    state_name: str,
    **updates: Any,
) -> dict[str, Any]:
    _validate_updates(updates)
    state = load_state(root, handle)
    previous = state["state"]
    if previous in TERMINAL_STATES and state_name == previous:
        return state
    if state_name != previous and state_name not in _TRANSITIONS[previous]:
        raise ProtocolError(f"invalid transition: {previous} -> {state_name}")
    now = utc_now()
    state.update(updates)
    state.update({"state": state_name, "updated_at": now})
    if state_name == "running" and not state.get("started_at"):
        state["started_at"] = now
    if state_name in TERMINAL_STATES:
        state["finished_at"] = now
        state["heartbeat_at"] = now
    publish_state(root, state)
    return state


def heartbeat(root: Path, handle: str, **updates: Any) -> dict[str, Any]:
    _validate_updates(updates)
    state = load_state(root, handle)
    if state["state"] in TERMINAL_STATES:
        return state
    now = utc_now()
    state.update(updates)
    state.update({"heartbeat_at": now, "updated_at": now})
    publish_state(root, state)
    return state


def heartbeat_age_seconds(state: dict[str, Any], now: datetime | None = None) -> int | None:
    raw = state.get("heartbeat_at") or state.get("updated_at")
    if not raw:
        return None
    try:
        instant = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0, int((current - instant).total_seconds()))


def public_state(state: dict[str, Any], stale_after: float) -> dict[str, Any]:
    age = heartbeat_age_seconds(state)
    active = state["state"] not in TERMINAL_STATES
    health = "stale" if active and age is not None and age > stale_after else "ok"
    result = {
        "job": state["job"],
        "kind": state.get("job_kind", state["kind"]),
        "state": state["state"],
        "health": health,
        "heartbeat_age_seconds": age,
    }
    if state.get("job_kind") == "provider-free":
        result["scenario"] = state.get("scenario")
    if state["state"] in TERMINAL_STATES:
        result.update(
            {
                "process_exit_code": state.get("process_exit_code"),
                "runner_final_status": state.get("runner_final_status"),
                "artifact_manifest": state.get("artifact_manifest"),
            }
        )
        if state.get("failure_reason"):
            result["failure_reason"] = str(state["failure_reason"])[:160]
        diagnostic = _public_provider_free_bootstrap_diagnostic(
            state.get("bootstrap_diagnostic")
        )
        if state.get("job_kind") == "provider-free" and diagnostic is not None:
            result["bootstrap_diagnostic"] = diagnostic
        scenario_failure = _public_provider_free_scenario_failure(
            state.get("scenario_failure"),
            expected_identity=(state.get("scenario") or {}).get("identity"),
        )
        if state.get("job_kind") == "provider-free" and scenario_failure is not None:
            result["scenario_failure"] = scenario_failure
        browser_identity_diagnostic = (
            _public_provider_free_browser_identity_diagnostic(
                state.get("browser_identity_diagnostic"),
                scenario_failure=state.get("scenario_failure"),
            )
        )
        if (
            state.get("job_kind") == "provider-free"
            and browser_identity_diagnostic is not None
        ):
            result["browser_identity_diagnostic"] = browser_identity_diagnostic
    return result


def _public_provider_free_browser_identity_diagnostic(
    value: object,
    *,
    scenario_failure: object,
) -> dict[str, str] | None:
    """Project no identity detail beyond the fixed versioned substage."""

    if not isinstance(value, dict):
        return None
    substage = value.get("substage")
    phase = value.get("phase")
    expected_keys = {"schema", "substage"}
    if substage == "private_snapshot_launch_image_identity":
        expected_keys.add("phase")
    if (
        set(value) != expected_keys
        or value.get("schema")
        != PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA
        or value.get("substage") not in PROVIDER_FREE_BROWSER_IDENTITY_SUBSTAGES
        or not isinstance(scenario_failure, dict)
        or scenario_failure.get("operation") != "preview_browser_identity"
        or scenario_failure.get("browser_identity_substage")
        != value.get("substage")
        or (
            substage == "private_snapshot_launch_image_identity"
            and (
                phase not in PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES
                or scenario_failure.get("browser_identity_phase") != phase
            )
        )
        or (
            substage != "private_snapshot_launch_image_identity"
            and phase is not None
        )
    ):
        return None
    result = {
        "schema": PROVIDER_FREE_BROWSER_IDENTITY_DIAGNOSTIC_SCHEMA,
        "substage": value["substage"],
    }
    if phase is not None:
        result["phase"] = phase
    return result


def _public_provider_free_bootstrap_diagnostic(
    value: object,
) -> dict[str, object] | None:
    """Return only the closed, bounded provider-free bootstrap vocabulary."""

    if not isinstance(value, dict):
        return None
    phase = value.get("phase")
    classification = value.get("classification")
    process_exit_code = value.get("process_exit_code")
    if (
        value.get("schema") != "cvm.provider-free-bootstrap-diagnostic/1"
        or phase not in _PROVIDER_FREE_BOOTSTRAP_PHASES
        or classification not in _PROVIDER_FREE_BOOTSTRAP_CLASSIFICATIONS
        or isinstance(process_exit_code, bool)
        or not isinstance(process_exit_code, int)
        or not -255 <= process_exit_code <= 255
    ):
        return None
    return {
        "schema": "cvm.provider-free-bootstrap-diagnostic/1",
        "phase": phase,
        "classification": classification,
        "process_exit_code": process_exit_code,
    }


def _public_provider_free_scenario_failure(
    value: object,
    *,
    expected_identity: object,
) -> dict[str, str] | None:
    """Project only the repository-owned closed scenario failure vocabulary."""

    if not isinstance(value, dict):
        return None
    scenario_identity = value.get("scenario_identity")
    stage = value.get("stage")
    operation = value.get("operation")
    browser_identity_substage = value.get("browser_identity_substage")
    browser_identity_phase = value.get("browser_identity_phase")
    if (
        value.get("schema") != PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA
        or not isinstance(expected_identity, str)
        or scenario_identity != expected_identity
        or stage not in PROVIDER_FREE_SCENARIO_FAILURE_STAGES
        or (
            operation is not None
            and not provider_free_scenario_failure_operation_allowed(
                stage, operation
            )
        )
        or (
            operation == "preview_browser_identity"
            and browser_identity_substage
            not in PROVIDER_FREE_BROWSER_IDENTITY_SUBSTAGES
        )
        or (
            browser_identity_substage
            == "private_snapshot_launch_image_identity"
            and browser_identity_phase
            not in PROVIDER_FREE_PRIVATE_SNAPSHOT_IDENTITY_PHASES
        )
        or (
            browser_identity_substage
            != "private_snapshot_launch_image_identity"
            and browser_identity_phase is not None
        )
    ):
        return None
    result = {
        "schema": PROVIDER_FREE_SCENARIO_FAILURE_SCHEMA,
        "scenario_identity": scenario_identity,
        "stage": stage,
    }
    if operation is not None:
        result["operation"] = operation
    return result


def provider_free_scenario_failure_operation_allowed(
    stage: object,
    operation: object,
) -> bool:
    """Return whether one closed operation is valid for its failure stage."""

    return (
        isinstance(stage, str)
        and isinstance(operation, str)
        and operation
        in PROVIDER_FREE_SCENARIO_FAILURE_OPERATIONS_BY_STAGE.get(
            stage, frozenset()
        )
    )
