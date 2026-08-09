"""Immutable Mesh-to-CAD Workspace state and Git publication.

This module intentionally uses only the Python standard library so the skill is
self-contained when installed outside the repository checkout.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time
from typing import Any, Iterable, Mapping
import uuid


WORKSPACE_SCHEMA = "mesh-to-cad.workspace/1"
EXPERIMENT_SCHEMA = "mesh-to-cad.experiment/1"
STEP_SCHEMA = "mesh-to-cad.measured-step/1"
CYCLE_SCHEMA = "mesh-to-cad.repair-cycle/1"
INDEX_SCHEMA = "mesh-to-cad.step-index/1"
ATTEMPT_SCHEMA = "mesh-to-cad.attempt/1"
COMMAND_SCHEMA = "mesh-to-cad.command/1"
INITIAL_PLAN_SCHEMA = "mesh-to-cad.initial-plan/1"
COORDINATE_CONTRACT = "trellis2_canonical/1"
MAX_DEPTH = 8
MAX_REPAIR_CYCLES = 5
MAX_ATTEMPTS_PER_STEP = 3
MAX_TOOL_FAILURES_PER_STEP = 2
MAX_COMMANDS_PER_ATTEMPT = 8
MAX_LOG_BYTES = 65536
MAX_COMMAND_SECONDS = 900

_EXPERIMENT_FIELDS = {
    "schema",
    "workspace_id",
    "coordinate_contract",
    "canonical_reference_sha256",
    "preview_profile",
    "route",
}
_WORKSPACE_FIELDS = {
    "schema",
    "workspace_id",
    "coordinate_contract",
    "canonical_reference_sha256",
    "preview_profile",
    "route",
    "limits",
}
_STEP_FIELDS = {
    "schema",
    "step",
    "parent_step",
    "cycle",
    "attempt_ids",
    "canonical_reference_sha256",
    "candidate_mesh_sha256",
    "observable_sha256",
    "preview_identity_sha256",
    "preview_profile_sha256",
    "measurement_path",
    "compare_to",
    "accepted",
    "no_observable_geometry_change",
    "files",
    "identity_sha256",
}
_ACTIVE_ATTEMPT_FIELDS = {
    "schema",
    "attempt",
    "intended_cycle",
    "intended_step",
    "from_step",
    "plan_digest",
    "result",
}
_PUBLISHED_ATTEMPT_FIELDS = {
    "schema",
    "attempt",
    "intended_cycle",
    "intended_step",
    "from_step",
    "plan_digest",
    "result",
    "classification",
    "command_ids",
    "files",
    "identity_sha256",
}
_CYCLE_FIELDS = {
    "schema",
    "cycle",
    "from_step",
    "to_step",
    "attempt_ids",
    "plan_digest",
    "region_diff_sha256",
    "assessment_sha256",
    "source_changes_sha256",
    "from_observable_sha256",
    "to_observable_sha256",
    "no_observable_geometry_change",
    "files",
    "identity_sha256",
}
_SECRET_ARGUMENTS = {
    "--api-key",
    "--token",
    "--password",
    "authorization",
}
_LFS_SUFFIXES = {
    ".step",
    ".stp",
    ".glb",
    ".gltf",
    ".ply",
    ".obj",
    ".stl",
    ".vbsvo",
    ".png",
}
_FORBIDDEN_TREE_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".env",
    "credentials.json",
    "secrets.json",
    "id_rsa",
}
_LEGACY_ROOT_NAMES = {
    "compare_metrics.json",
    "iteration_state.json",
    "mesh_stats.json",
}


class WorkspaceError(RuntimeError):
    """Stable public Workspace failure."""

    def __init__(self, classification: str, detail: str, path: str = "$"):
        super().__init__(detail)
        self.classification = classification
        self.detail = detail
        self.path = path


@dataclass(frozen=True)
class ValidationResult:
    graph: dict[str, Any]
    recovery: list[dict[str, Any]]


def initialize_workspace(workspace: Path, prepared: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    prepared = prepared.resolve()
    _require_git_root(workspace)
    _reject_staged_changes(workspace)
    if (workspace / "workspace.json").exists():
        _fail("workspace_conflict", "Workspace is already initialized")
    for name in ("input", "setup", "experiment.json"):
        if not (prepared / name).exists():
            _fail("invalid_setup", f"prepared setup is missing {name}", f"$.{name}")
    _validate_tree_source(prepared / "input", "$.input")
    _validate_tree_source(prepared / "setup", "$.setup")
    experiment = _read_json(prepared / "experiment.json", "$.experiment")
    _validate_experiment(experiment)
    input_manifest = _read_json(prepared / "input/input.json", "$.input.input.json")
    if input_manifest.get("canonical_reference_sha256") != experiment[
        "canonical_reference_sha256"
    ]:
        _fail(
            "identity_conflict",
            "prepared input and experiment Canonical Reference identities differ",
            "$.input.input.json.canonical_reference_sha256",
        )
    _configure_git_contract(workspace)
    workspace_document = {
        "schema": WORKSPACE_SCHEMA,
        "workspace_id": experiment["workspace_id"],
        "coordinate_contract": experiment["coordinate_contract"],
        "canonical_reference_sha256": experiment["canonical_reference_sha256"],
        "preview_profile": experiment["preview_profile"],
        "route": experiment["route"],
        "limits": {
            "repair_cycles": MAX_REPAIR_CYCLES,
            "attempts_per_step": MAX_ATTEMPTS_PER_STEP,
            "tool_failures_per_step": MAX_TOOL_FAILURES_PER_STEP,
        },
    }
    stage = workspace / f".tmp-setup-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        shutil.copytree(prepared / "input", stage / "input")
        shutil.copytree(prepared / "setup", stage / "setup")
        shutil.copy2(prepared / "experiment.json", stage / "experiment.json")
        _write_json(stage / "workspace.json", workspace_document)
        _write_json(
            stage / "transaction.json",
            {
                "schema": "mesh-to-cad.transaction/1",
                "kind": "setup",
                "workspace_identity_sha256": _identity(
                    WORKSPACE_SCHEMA, workspace_document
                ),
            },
        )
        _validate_staged_setup(stage, workspace_document)
        (stage / "input").rename(workspace / "input")
        (stage / "setup").rename(workspace / "setup")
        (stage / "experiment.json").rename(workspace / "experiment.json")
        (stage / "workspace.json").rename(workspace / "workspace.json")
        (stage / "transaction.json").unlink()
        stage.rmdir()
    except Exception:
        # Keep incomplete setup evidence. Validation reports it as recoverable,
        # never as published authority.
        raise
    graph = rebuild_index(workspace, validate=False)
    paths = [
        ".gitignore",
        ".gitattributes",
        "input",
        "setup",
        "experiment.json",
        "workspace.json",
        "step_index.json",
    ]
    _commit_protocol_paths(
        workspace,
        paths,
        "setup: publish canonical Workspace",
        {
            "Workspace-Schema": WORKSPACE_SCHEMA,
            "Workspace-Id": workspace_document["workspace_id"],
            "Canonical-Reference-SHA256": workspace_document[
                "canonical_reference_sha256"
            ],
        },
    )
    return {**workspace_document, "graph": graph}


def begin_attempt(
    workspace: Path,
    plan_path: Path,
    *,
    intended_step: int,
    from_step: int | None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    validate_workspace(workspace)
    if intended_step < 0:
        _fail("invalid_attempt", "intended step must be non-negative")
    if intended_step == 0:
        if from_step is not None:
            _fail("parent_mismatch", "Step 0 cannot declare a parent")
        intended_cycle = None
    else:
        if from_step is None or from_step < 0 or from_step >= intended_step:
            _fail(
                "parent_mismatch",
                "a repair attempt requires an explicit earlier Measured Step",
            )
        if not (workspace / "steps" / f"{from_step:06d}" / "step.json").is_file():
            _fail("parent_mismatch", f"parent Measured Step {from_step} is not published")
        intended_cycle = intended_step
    plan = _read_json(plan_path, "$.plan")
    if intended_step == 0:
        _closed_object(plan, {"schema", "summary"}, "$.plan")
        _const(plan["schema"], INITIAL_PLAN_SCHEMA, "$.plan.schema")
        _nonempty_string(plan["summary"], "$.plan.summary")
    else:
        _validate_repair_plan_boundary(plan, from_step)
    _check_attempt_budget(workspace, intended_step)
    attempt = _next_attempt_id(workspace)
    plan_bytes = _json_bytes(plan)
    plan_digest = hashlib.sha256(
        plan["schema"].encode("utf-8") + b"\0" + plan_bytes
    ).hexdigest()
    document = {
        "schema": ATTEMPT_SCHEMA,
        "attempt": attempt,
        "intended_cycle": intended_cycle,
        "intended_step": intended_step,
        "from_step": from_step,
        "plan_digest": plan_digest,
        "result": "active",
    }
    active_root = workspace / "work/attempts"
    active_root.mkdir(parents=True, exist_ok=True)
    target = active_root / f"{attempt:06d}"
    stage = active_root / f".tmp-{attempt:06d}-{uuid.uuid4().hex}"
    stage.mkdir()
    _write_json(stage / "plan.json", plan)
    _write_json(stage / "attempt.json", document)
    stage.rename(target)
    return document


def publish_step_zero(
    workspace: Path,
    *,
    attempt: int,
    candidate: Path,
    candidate_mesh: str,
    measurement: Path,
    preview: Path,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    validate_workspace(workspace, allowed_voxblame_step=0)
    if (workspace / "steps/000000").exists():
        _fail("workspace_conflict", "Measured Step 0 is already published")
    active_root, active, plan = _load_active_attempt(workspace, attempt)
    if active["intended_step"] != 0 or active["from_step"] is not None:
        _fail("parent_mismatch", "attempt does not belong to Step 0")
    candidate = candidate.resolve()
    preview = preview.resolve()
    measurement = measurement.resolve()
    _validate_tree_source(candidate, "$.candidate")
    _validate_tree_source(preview, "$.preview")
    mesh_path = _relative_member(candidate, candidate_mesh, "$.candidate_mesh")
    measurement_document = _read_json(measurement, "$.measurement")
    preview_document = _read_json(preview / "preview.json", "$.preview")
    experiment = _load_workspace_document(workspace)
    identities = _validate_step_evidence(
        workspace,
        experiment,
        step=0,
        parent_step=None,
        mesh_path=mesh_path,
        measurement_path=measurement,
        measurement=measurement_document,
        preview_root=preview,
        preview=preview_document,
    )
    steps_root = workspace / "steps"
    steps_root.mkdir(exist_ok=True)
    transaction = workspace / "work" / (
        f".tmp-step-zero-{uuid.uuid4().hex}"
    )
    stage = transaction / "step"
    stage.mkdir(parents=True)
    shutil.copytree(candidate, stage / "candidate")
    shutil.copytree(preview, stage / "preview")
    shutil.copy2(measurement, stage / "measurement.json")
    _write_json(stage / "plan.json", plan)
    successful_attempt = {**active, "result": "measured_step_published"}
    _write_json(stage / "attempt.json", successful_attempt)
    files = _inventory(stage)
    document: dict[str, Any] = {
        "schema": STEP_SCHEMA,
        "step": 0,
        "parent_step": None,
        "cycle": None,
        "attempt_ids": [attempt],
        **identities,
        "measurement_path": _workspace_relative(workspace, measurement),
        "compare_to": None,
        "accepted": _accepted(measurement_document),
        "no_observable_geometry_change": measurement_document[
            "no_observable_geometry_change"
        ],
        "files": files,
    }
    document["identity_sha256"] = _identity(STEP_SCHEMA, document)
    _write_json(stage / "step.json", document)
    _write_json(
        transaction / "transaction.json",
        {
            "schema": "mesh-to-cad.transaction/1",
            "kind": "step_zero",
            "attempt": attempt,
            "step_identity_sha256": document["identity_sha256"],
        },
    )
    _validate_step_directory(workspace, stage, expected_step=0)
    stage.rename(workspace / "steps/000000")
    (transaction / "transaction.json").unlink()
    transaction.rmdir()
    graph = rebuild_index(workspace, validate=False)
    voxblame_paths = _step_zero_voxblame_paths(workspace, measurement)
    _commit_protocol_paths(
        workspace,
        ["steps/000000", "step_index.json", *voxblame_paths],
        "step 0: publish initial Measured Step",
        {
            "Workspace-Step": "0",
            "Workspace-Attempt": str(attempt),
            "Candidate-SHA256": document["candidate_mesh_sha256"],
            "Observable-SHA256": document["observable_sha256"],
        },
    )
    shutil.rmtree(active_root)
    return {**document, "graph": graph}


def run_attempt_command(
    workspace: Path,
    *,
    attempt: int,
    phase: str,
    argv: list[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    """Run one bounded argv command without a shell and freeze its audit log."""

    workspace = workspace.resolve()
    validate_workspace(workspace)
    active_root, _active, _plan = _load_active_attempt(workspace, attempt)
    _nonempty_string(phase, "$.phase")
    if not argv or any(not isinstance(value, str) or "\0" in value for value in argv):
        _fail("invalid_command", "command argv must contain non-empty strings")
    if timeout_seconds <= 0 or timeout_seconds > MAX_COMMAND_SECONDS:
        _fail(
            "invalid_command",
            f"timeout must be between 1 and {MAX_COMMAND_SECONDS} seconds",
        )
    commands_root = active_root / "commands"
    commands_root.mkdir(exist_ok=True)
    existing = [
        int(path.name)
        for path in commands_root.iterdir()
        if path.is_dir() and path.name.isdigit()
    ]
    if len(existing) >= MAX_COMMANDS_PER_ATTEMPT:
        _fail("budget_violation", "attempt has exhausted its command allowance")
    command_id = max(existing, default=0) + 1
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout or b""
        stderr = (exc.stderr or b"") + b"\ncommand timed out\n"
        timed_out = True
    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    stored_stdout, stdout_metadata = _bounded_log(stdout)
    stored_stderr, stderr_metadata = _bounded_log(stderr)
    stdout_metadata["path"] = f"commands/{command_id:06d}/stdout.log"
    stderr_metadata["path"] = f"commands/{command_id:06d}/stderr.log"
    document = {
        "schema": COMMAND_SCHEMA,
        "command": command_id,
        "phase": phase,
        "argv": _redact_argv(argv),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "stdout": stdout_metadata,
        "stderr": stderr_metadata,
    }
    stage = commands_root / f".tmp-{command_id:06d}-{uuid.uuid4().hex}"
    stage.mkdir()
    (stage / "stdout.log").write_bytes(stored_stdout)
    (stage / "stderr.log").write_bytes(stored_stderr)
    _write_json(stage / "command.json", document)
    stage.rename(commands_root / f"{command_id:06d}")
    return document


def record_attempt(
    workspace: Path,
    *,
    attempt: int,
    result: str,
    classification: str,
) -> dict[str, Any]:
    """Atomically publish one failed or strategy-changed Attempt."""

    workspace = workspace.resolve()
    validate_workspace(workspace)
    active_root, active, _plan = _load_active_attempt(workspace, attempt)
    if result not in {"tool_failure", "strategy_changed", "no_feasible_strategy"}:
        _fail("invalid_attempt", "unsupported terminal Attempt result")
    _nonempty_string(classification, "$.classification")
    command_documents = _load_command_documents(active_root)
    if result == "tool_failure" and not any(
        command["exit_code"] != 0 for command in command_documents
    ):
        _fail(
            "invalid_attempt",
            "tool_failure requires at least one recorded failing command",
        )
    attempts_root = workspace / "attempts"
    attempts_root.mkdir(exist_ok=True)
    target = attempts_root / f"{attempt:06d}"
    if target.exists():
        _fail("workspace_conflict", f"Attempt {attempt} is already published")
    stage = attempts_root / f".tmp-{attempt:06d}-{uuid.uuid4().hex}"
    stage.mkdir()
    shutil.copy2(active_root / "plan.json", stage / "plan.json")
    if (active_root / "commands").exists():
        shutil.copytree(active_root / "commands", stage / "commands")
    files = _inventory(stage)
    document: dict[str, Any] = {
        **{key: active[key] for key in _ACTIVE_ATTEMPT_FIELDS - {"result"}},
        "result": result,
        "classification": classification,
        "command_ids": [item["command"] for item in command_documents],
        "files": files,
    }
    document["identity_sha256"] = _identity(ATTEMPT_SCHEMA, document)
    _write_json(stage / "attempt.json", document)
    _validate_published_attempt(stage, expected_attempt=attempt)
    stage.rename(target)
    graph = rebuild_index(workspace, validate=False)
    _commit_protocol_paths(
        workspace,
        [f"attempts/{attempt:06d}", "step_index.json"],
        f"attempt {attempt}: {result}",
        {
            "Workspace-Attempt": str(attempt),
            "Intended-Step": str(document["intended_step"]),
            "Attempt-Result": result,
            "Plan-SHA256": document["plan_digest"],
        },
    )
    shutil.rmtree(active_root)
    return {**document, "graph": graph}


def publish_cycle(
    workspace: Path,
    *,
    attempt: int,
    candidate: Path,
    candidate_mesh: str,
    measurement: Path,
    preview: Path,
    region_diff: Path,
    assessment: Path,
    source_changes: Path,
) -> dict[str, Any]:
    """Publish one successful Repair Cycle edge and its Measured Step node."""

    workspace = workspace.resolve()
    active_root, active, plan = _load_active_attempt(workspace, attempt)
    intended_step = active["intended_step"]
    if intended_step <= 0 or active["intended_cycle"] != intended_step:
        _fail("invalid_attempt", "attempt is not a Repair Cycle attempt")
    validate_workspace(workspace, allowed_voxblame_step=intended_step)
    existing_cycles = _published_numbers(workspace / "cycles")
    expected_cycle = len(existing_cycles) + 1
    if intended_step != expected_cycle:
        _fail(
            "budget_violation",
            f"next successful Repair Cycle must be {expected_cycle}",
        )
    if intended_step > MAX_REPAIR_CYCLES:
        _fail("budget_violation", "Workspace has exhausted five Repair Cycles")
    from_step = active["from_step"]
    if from_step is None:
        _fail("parent_mismatch", "Repair Cycle requires a source Measured Step")
    parent_manifest = _read_json(
        workspace / "steps" / f"{from_step:06d}" / "step.json", "$.parent_step"
    )
    candidate = candidate.resolve()
    preview = preview.resolve()
    measurement = measurement.resolve()
    region_diff = region_diff.resolve()
    assessment = assessment.resolve()
    source_changes = source_changes.resolve()
    _validate_tree_source(candidate, "$.candidate")
    _validate_tree_source(preview, "$.preview")
    mesh_path = _relative_member(candidate, candidate_mesh, "$.candidate_mesh")
    measurement_document = _read_json(measurement, "$.measurement")
    preview_document = _read_json(preview / "preview.json", "$.preview")
    experiment = _load_workspace_document(workspace)
    identities = _validate_step_evidence(
        workspace,
        experiment,
        step=intended_step,
        parent_step=from_step,
        mesh_path=mesh_path,
        measurement_path=measurement,
        measurement=measurement_document,
        preview_root=preview,
        preview=preview_document,
    )
    diff_document = _read_json(region_diff, "$.region_diff")
    _validate_region_diff_boundary(
        diff_document,
        plan=plan,
        plan_digest=active["plan_digest"],
        from_step=from_step,
        to_step=intended_step,
        before_observable=parent_manifest["observable_sha256"],
        after_observable=identities["observable_sha256"],
    )
    assessment_document = _read_json(assessment, "$.assessment")
    _validate_assessment(
        assessment_document, from_step=from_step, to_step=intended_step
    )
    source_changes_document = _read_json(source_changes, "$.source_changes")
    _validate_source_changes(
        source_changes_document, from_step=from_step, to_step=intended_step
    )
    attempt_ids = _cycle_attempt_ids(workspace, intended_step, attempt)
    transaction = workspace / "work" / (
        f".tmp-cycle-{intended_step:06d}-{uuid.uuid4().hex}"
    )
    step_stage = transaction / "step"
    cycle_stage = transaction / "cycle"
    step_stage.mkdir(parents=True)
    cycle_stage.mkdir()
    shutil.copytree(candidate, step_stage / "candidate")
    shutil.copytree(preview, step_stage / "preview")
    shutil.copy2(measurement, step_stage / "measurement.json")
    step_files = _inventory(step_stage)
    step_document: dict[str, Any] = {
        "schema": STEP_SCHEMA,
        "step": intended_step,
        "parent_step": from_step,
        "cycle": intended_step,
        "attempt_ids": attempt_ids,
        **identities,
        "measurement_path": _workspace_relative(workspace, measurement),
        "compare_to": from_step,
        "accepted": _accepted(measurement_document),
        "no_observable_geometry_change": measurement_document[
            "no_observable_geometry_change"
        ],
        "files": step_files,
    }
    step_document["identity_sha256"] = _identity(STEP_SCHEMA, step_document)
    _write_json(step_stage / "step.json", step_document)

    _write_json(cycle_stage / "plan.json", plan)
    shutil.copy2(region_diff, cycle_stage / "diff.json")
    shutil.copy2(assessment, cycle_stage / "assessment.json")
    shutil.copy2(source_changes, cycle_stage / "source_changes.json")
    successful_attempt = {**active, "result": "repair_cycle_published"}
    _write_json(cycle_stage / "attempt.json", successful_attempt)
    if (active_root / "commands").exists():
        shutil.copytree(active_root / "commands", cycle_stage / "logs/commands")
    cycle_files = _inventory(cycle_stage)
    cycle_document: dict[str, Any] = {
        "schema": CYCLE_SCHEMA,
        "cycle": intended_step,
        "from_step": from_step,
        "to_step": intended_step,
        "attempt_ids": attempt_ids,
        "plan_digest": active["plan_digest"],
        "region_diff_sha256": diff_document["identity"]["region_diff_sha256"],
        "assessment_sha256": _file_sha256(assessment),
        "source_changes_sha256": _file_sha256(source_changes),
        "from_observable_sha256": parent_manifest["observable_sha256"],
        "to_observable_sha256": identities["observable_sha256"],
        "no_observable_geometry_change": measurement_document[
            "no_observable_geometry_change"
        ],
        "files": cycle_files,
    }
    cycle_document["identity_sha256"] = _identity(CYCLE_SCHEMA, cycle_document)
    _write_json(cycle_stage / "cycle.json", cycle_document)
    _write_json(
        transaction / "transaction.json",
        {
            "schema": "mesh-to-cad.transaction/1",
            "kind": "repair_cycle",
            "cycle": intended_step,
            "step_identity_sha256": step_document["identity_sha256"],
            "cycle_identity_sha256": cycle_document["identity_sha256"],
        },
    )
    _validate_step_directory(workspace, step_stage, expected_step=intended_step)
    _validate_cycle_directory(
        workspace, cycle_stage, expected_cycle=intended_step, step=step_document
    )
    steps_root = workspace / "steps"
    cycles_root = workspace / "cycles"
    steps_root.mkdir(exist_ok=True)
    cycles_root.mkdir(exist_ok=True)
    step_target = steps_root / f"{intended_step:06d}"
    cycle_target = cycles_root / f"{intended_step:06d}"
    if step_target.exists() or cycle_target.exists():
        _fail("workspace_conflict", "Repair Cycle target already exists")
    step_stage.rename(step_target)
    cycle_stage.rename(cycle_target)
    (transaction / "transaction.json").unlink()
    transaction.rmdir()
    graph = rebuild_index(workspace, validate=False)
    voxblame_path = _voxblame_step_path(workspace, measurement, intended_step)
    _commit_protocol_paths(
        workspace,
        [
            f"steps/{intended_step:06d}",
            f"cycles/{intended_step:06d}",
            voxblame_path,
            "step_index.json",
        ],
        f"repair cycle {intended_step}: publish Measured Step {intended_step}",
        {
            "Repair-Cycle": str(intended_step),
            "Workspace-Step": str(intended_step),
            "From-Step": str(from_step),
            "Workspace-Attempt": str(attempt),
            "Plan-SHA256": active["plan_digest"],
            "Candidate-SHA256": step_document["candidate_mesh_sha256"],
            "Observable-SHA256": step_document["observable_sha256"],
        },
    )
    shutil.rmtree(active_root)
    return {**cycle_document, "step": step_document, "graph": graph}


def validate_workspace(
    workspace: Path, *, allowed_voxblame_step: int | None = None
) -> ValidationResult:
    workspace = workspace.resolve()
    if not (workspace / "workspace.json").is_file():
        if any((workspace / name).exists() for name in _LEGACY_ROOT_NAMES):
            _fail(
                "unsupported_legacy_workspace",
                "legacy Mesh-to-CAD layout is not a canonical Workspace",
            )
        incomplete = _find_incomplete_transactions(workspace)
        if incomplete or any(
            (workspace / name).exists()
            for name in ("input", "setup", "experiment.json")
        ):
            _fail(
                "incomplete_transaction",
                "Workspace setup publication is incomplete",
                incomplete[0]["path"] if incomplete else "$",
            )
        _fail("invalid_workspace", "workspace.json is missing")
    for name in _LEGACY_ROOT_NAMES:
        if (workspace / name).exists():
            _fail(
                "unsupported_legacy_workspace",
                f"legacy Workspace path is unsupported: {name}",
            )
    recovery = _find_incomplete_transactions(workspace)
    if allowed_voxblame_step is not None:
        allowed_path = f"voxblame/steps/{allowed_voxblame_step:06d}"
        recovery = [
            item
            for item in recovery
            if not (
                item["classification"] == "orphan_voxblame_step"
                and item["path"] == allowed_path
            )
        ]
    if recovery:
        _fail(
            "incomplete_transaction",
            "Workspace contains staged or incomplete publication state",
            recovery[0]["path"],
        )
    workspace_document = _load_workspace_document(workspace)
    experiment = _read_json(workspace / "experiment.json", "$.experiment")
    _validate_experiment(experiment)
    for key in (
        "workspace_id",
        "coordinate_contract",
        "canonical_reference_sha256",
        "preview_profile",
        "route",
    ):
        if workspace_document[key] != experiment[key]:
            _fail("identity_conflict", f"workspace and experiment {key} differ")
    input_manifest = _read_json(workspace / "input/input.json", "$.input.input.json")
    if input_manifest.get("canonical_reference_sha256") != workspace_document[
        "canonical_reference_sha256"
    ]:
        _fail("identity_conflict", "input Canonical Reference identity conflicts")
    graph = _build_graph(workspace, validate_steps=True)
    index_path = workspace / "step_index.json"
    if not index_path.is_file():
        _fail("derived_index_missing", "step_index.json can be rebuilt")
    index = _read_json(index_path, "$.step_index")
    if index != graph:
        _fail("derived_index_conflict", "step_index.json does not match authority")
    _validate_git_evidence(workspace, graph)
    return ValidationResult(graph=graph, recovery=[])


def rebuild_index(workspace: Path, *, validate: bool = True) -> dict[str, Any]:
    workspace = workspace.resolve()
    _load_workspace_document(workspace)
    if validate and _find_incomplete_transactions(workspace):
        _fail("incomplete_transaction", "cannot index incomplete publication state")
    graph = _build_graph(workspace, validate_steps=validate)
    target = workspace / "step_index.json"
    stage = workspace / f".tmp-step-index-{uuid.uuid4().hex}"
    _write_json(stage, graph)
    os.replace(stage, target)
    return graph


def workspace_status(workspace: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    document = _load_workspace_document(workspace)
    graph = _build_graph(workspace, validate_steps=True)
    recovery = _find_incomplete_transactions(workspace)
    steps = graph["steps"]
    return {
        "schema": "mesh-to-cad.workspace-status/1",
        "workspace_id": document["workspace_id"],
        "base_step": 0 if steps else None,
        "head_steps": _graph_heads(graph),
        "completed_cycles": graph["budget"]["completed_cycles"],
        "remaining_cycles": graph["budget"]["remaining_cycles"],
        "next_intended_step": (
            graph["budget"]["completed_cycles"] + 1 if steps else 0
        ),
        "total_attempts": graph["budget"]["total_attempts"],
        "tool_failures": graph["budget"]["tool_failures"],
        "accepted_steps": graph["accepted_steps"],
        "recovery": recovery,
    }


def recover_workspace(workspace: Path) -> dict[str, Any]:
    """Finish validated marker-last cycle transactions after interruption."""

    workspace = workspace.resolve()
    setup_transactions = sorted(
        path
        for path in workspace.glob(".tmp-setup-*")
        if path.is_dir() and (path / "transaction.json").is_file()
    )
    recovered_setup = False
    for transaction in setup_transactions:
        _recover_setup_transaction(workspace, transaction)
        recovered_setup = True
    _load_workspace_document(workspace)
    _require_git_root(workspace)
    step_transactions = sorted(
        path
        for path in (workspace / "work").glob(".tmp-step-zero-*")
        if path.is_dir() and (path / "transaction.json").is_file()
    ) if (workspace / "work").exists() else []
    transaction_roots = sorted(
        path
        for path in (workspace / "work").glob(".tmp-cycle-*")
        if path.is_dir() and (path / "transaction.json").is_file()
    ) if (workspace / "work").exists() else []
    known = {
        path.resolve()
        for root in [*setup_transactions, *step_transactions, *transaction_roots]
        for path in (root, *root.rglob("*"))
    }
    unknown = [
        item
        for item in _find_incomplete_transactions(workspace)
        if not (workspace / item["path"]).resolve() in known
        and item["classification"] != "orphan_voxblame_step"
    ]
    if unknown:
        _fail(
            "unknown_staged_state",
            "recovery refuses staged state without a known transaction marker",
            unknown[0]["path"],
        )
    recovered: list[int] = []
    recovered_steps: list[int] = []
    for transaction in step_transactions:
        recovered_steps.append(_recover_step_zero_transaction(workspace, transaction))
    for transaction in transaction_roots:
        recovered.append(_recover_cycle_transaction(workspace, transaction))
    if not recovered and not recovered_steps:
        orphans = [
            item
            for item in _find_incomplete_transactions(workspace)
            if item["classification"] == "orphan_voxblame_step"
        ]
        if orphans:
            _fail(
                "orphan_voxblame_step",
                "orphan measurement requires the original publication inputs",
                orphans[0]["path"],
            )
    result = validate_workspace(workspace)
    return {
        "recovered_setup": recovered_setup,
        "recovered_steps": recovered_steps,
        "recovered_cycles": recovered,
        "graph": result.graph,
    }


def _validate_experiment(value: Mapping[str, Any]) -> None:
    root = _closed_object(value, _EXPERIMENT_FIELDS, "$.experiment")
    _const(root["schema"], EXPERIMENT_SCHEMA, "$.experiment.schema")
    _nonempty_string(root["workspace_id"], "$.experiment.workspace_id")
    _const(
        root["coordinate_contract"],
        COORDINATE_CONTRACT,
        "$.experiment.coordinate_contract",
    )
    _sha256(root["canonical_reference_sha256"], "$.experiment.canonical_reference_sha256")
    profile = _closed_object(root["preview_profile"], {"name", "sha256"}, "$.experiment.preview_profile")
    _nonempty_string(profile["name"], "$.experiment.preview_profile.name")
    _sha256(profile["sha256"], "$.experiment.preview_profile.sha256")
    if root["route"] not in {"cad", "implicit"}:
        _fail("invalid_setup", "route must be cad or implicit", "$.experiment.route")


def _load_workspace_document(workspace: Path) -> dict[str, Any]:
    value = _read_json(workspace / "workspace.json", "$.workspace")
    root = _closed_object(value, _WORKSPACE_FIELDS, "$.workspace")
    _const(root["schema"], WORKSPACE_SCHEMA, "$.workspace.schema")
    _const(root["coordinate_contract"], COORDINATE_CONTRACT, "$.workspace.coordinate_contract")
    _sha256(root["canonical_reference_sha256"], "$.workspace.canonical_reference_sha256")
    _closed_object(root["preview_profile"], {"name", "sha256"}, "$.workspace.preview_profile")
    limits = _closed_object(
        root["limits"],
        {"repair_cycles", "attempts_per_step", "tool_failures_per_step"},
        "$.workspace.limits",
    )
    expected = {
        "repair_cycles": MAX_REPAIR_CYCLES,
        "attempts_per_step": MAX_ATTEMPTS_PER_STEP,
        "tool_failures_per_step": MAX_TOOL_FAILURES_PER_STEP,
    }
    if limits != expected:
        _fail("budget_violation", "Workspace budget contract is unsupported")
    return dict(root)


def _validate_staged_setup(stage: Path, expected: dict[str, Any]) -> None:
    actual = _read_json(stage / "workspace.json", "$.workspace")
    if actual != expected:
        _fail("invalid_setup", "staged workspace manifest changed")
    _validate_tree_source(stage / "input", "$.input")
    _validate_tree_source(stage / "setup", "$.setup")


def _validate_step_evidence(
    workspace: Path,
    experiment: Mapping[str, Any],
    *,
    step: int,
    parent_step: int | None,
    mesh_path: Path,
    measurement_path: Path,
    measurement: Mapping[str, Any],
    preview_root: Path,
    preview: Mapping[str, Any],
) -> dict[str, str]:
    _validate_measurement_boundary(measurement, step=step, compare_to=parent_step)
    _validate_preview_boundary(preview, preview_root)
    candidate_sha = _file_sha256(mesh_path)
    reference_sha = experiment["canonical_reference_sha256"]
    profile = experiment["preview_profile"]
    checks = (
        (measurement["canonical_reference"]["canonical_reference_sha256"], reference_sha, "measurement reference"),
        (preview["reference"]["canonical_reference_sha256"], reference_sha, "preview reference"),
        (measurement["measurement"]["candidate_mesh_sha256"], candidate_sha, "measurement candidate"),
        (preview["candidate"]["mesh_sha256"], candidate_sha, "preview candidate"),
        (preview["profile"]["experiment_identity"], profile, "preview profile"),
        (preview["canonical_frame"]["coordinate_contract"], experiment["coordinate_contract"], "preview frame"),
    )
    for actual, expected, label in checks:
        if actual != expected:
            _fail("identity_conflict", f"{label} identity conflicts with the Workspace")
    _workspace_relative(workspace, measurement_path)
    return {
        "canonical_reference_sha256": reference_sha,
        "candidate_mesh_sha256": candidate_sha,
        "observable_sha256": measurement["measurement"]["observable_sha256"],
        "preview_identity_sha256": preview["preview_identity_sha256"],
        "preview_profile_sha256": profile["sha256"],
    }


def _validate_measurement_boundary(
    value: Mapping[str, Any], *, step: int, compare_to: int | None
) -> None:
    required = {
        "schema",
        "coordinate_contract",
        "max_depth",
        "step",
        "compare_to",
        "canonical_reference",
        "measurement",
        "errors_by_depth",
        "exterior_surface",
        "objective_facts",
        "no_observable_geometry_change",
    }
    if not isinstance(value, Mapping) or not required.issubset(value):
        _fail("unsupported_or_invalid_voxblame_state", "measurement summary is incomplete")
    if value["schema"] not in {"voxblame.measurement-summary/1", "voxblame.summary/1"}:
        _fail("unsupported_or_invalid_voxblame_state", "measurement summary schema is unsupported")
    if (
        value["coordinate_contract"] != COORDINATE_CONTRACT
        or value["max_depth"] != MAX_DEPTH
        or value["step"] != step
        or value["compare_to"] != compare_to
    ):
        _fail("parent_mismatch", "measurement ancestry or canonical frame conflicts")
    reference = value["canonical_reference"]
    measurement = value["measurement"]
    if not isinstance(reference, Mapping) or not isinstance(measurement, Mapping):
        _fail("unsupported_or_invalid_voxblame_state", "measurement identities are invalid")
    _sha256(reference.get("canonical_reference_sha256"), "$.measurement.canonical_reference.canonical_reference_sha256")
    for key in (
        "candidate_mesh_sha256",
        "interior_tree_sha256",
        "exterior_snapshot_sha256",
        "observable_sha256",
    ):
        _sha256(measurement.get(key), f"$.measurement.measurement.{key}")
    errors = value["errors_by_depth"]
    if not isinstance(errors, list) or [item.get("depth") for item in errors if isinstance(item, Mapping)] != list(range(1, 9)):
        _fail("unsupported_or_invalid_voxblame_state", "measurement must contain ordered depths 1 through 8")
    facts = value["objective_facts"]
    if not isinstance(facts, Mapping) or set(facts) != {
        "global_depth_8_zero",
        "out_of_frame_clear",
        "no_evidence_conflict",
    } or any(not isinstance(facts[key], bool) for key in facts):
        _fail("unsupported_or_invalid_voxblame_state", "measurement objective facts are invalid")
    if not isinstance(value["no_observable_geometry_change"], bool):
        _fail("unsupported_or_invalid_voxblame_state", "measurement no-op fact is invalid")


def _validate_preview_boundary(value: Mapping[str, Any], root: Path) -> None:
    required = {
        "schema",
        "render_variant",
        "canonical_frame",
        "profile",
        "reference",
        "candidate",
        "image",
        "preview_identity_sha256",
    }
    if not isinstance(value, Mapping) or not required.issubset(value):
        _fail("invalid_preview", "preview metadata is incomplete")
    if value["schema"] != "voxblame.preview/1" or value["render_variant"] != "step":
        _fail("invalid_preview", "Measured Steps require a formal step preview")
    nested = {
        "canonical_frame": {"coordinate_contract"},
        "profile": {"experiment_identity"},
        "reference": {"canonical_reference_sha256"},
        "candidate": {"mesh_sha256"},
    }
    for key, required_fields in nested.items():
        item = value[key]
        if not isinstance(item, Mapping) or not required_fields.issubset(item):
            _fail("invalid_preview", f"preview {key} identity is incomplete")
    identity = value["profile"]["experiment_identity"]
    if not isinstance(identity, Mapping) or set(identity) != {"name", "sha256"}:
        _fail("invalid_preview", "preview profile experiment identity is invalid")
    _sha256(value["preview_identity_sha256"], "$.preview.preview_identity_sha256")
    image = value["image"]
    if not isinstance(image, Mapping) or image.get("path") != "preview.png":
        _fail("invalid_preview", "preview image path is invalid")
    _sha256(image.get("sha256"), "$.preview.image.sha256")
    if _file_sha256(root / "preview.png") != image["sha256"]:
        _fail("corrupt_workspace", "preview PNG digest mismatch")


def _validate_step_directory(workspace: Path, root: Path, *, expected_step: int) -> dict[str, Any]:
    value = _read_json(root / "step.json", f"$.steps[{expected_step}]")
    document = _closed_object(value, _STEP_FIELDS, f"$.steps[{expected_step}]")
    _const(document["schema"], STEP_SCHEMA, f"$.steps[{expected_step}].schema")
    if document["step"] != expected_step:
        _fail("parent_mismatch", "step directory and manifest number differ")
    identity_document = dict(document)
    identity = identity_document.pop("identity_sha256")
    if identity != _identity(STEP_SCHEMA, identity_document):
        _fail("corrupt_workspace", "Measured Step identity digest mismatch")
    files = document["files"]
    if not isinstance(files, list) or not files:
        _fail("corrupt_workspace", "Measured Step file inventory is empty")
    expected_paths: set[str] = set()
    for index, item in enumerate(files):
        entry = _closed_object(item, {"path", "sha256", "size_bytes"}, f"$.steps[{expected_step}].files[{index}]")
        path = _relative_member(root, entry["path"], f"$.steps[{expected_step}].files[{index}].path")
        if entry["path"] in expected_paths:
            _fail("corrupt_workspace", "duplicate Measured Step inventory path")
        expected_paths.add(entry["path"])
        if path.stat().st_size != entry["size_bytes"] or _file_sha256(path) != entry["sha256"]:
            _fail("corrupt_workspace", f"Measured Step artifact digest mismatch: {entry['path']}")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "step.json"
    }
    if expected_paths != actual_paths:
        _fail("corrupt_workspace", "Measured Step inventory does not cover its files")
    measurement = _read_json(root / "measurement.json", f"$.steps[{expected_step}].measurement")
    preview = _read_json(root / "preview/preview.json", f"$.steps[{expected_step}].preview")
    candidate_mesh_rel = next(
        (
            item["path"]
            for item in files
            if Path(item["path"]).suffix.lower() in {".glb", ".gltf"}
            and item["path"].startswith("candidate/")
        ),
        None,
    )
    if candidate_mesh_rel is None:
        _fail("corrupt_workspace", "Measured Step candidate mesh is missing")
    experiment = _load_workspace_document(workspace)
    identities = _validate_step_evidence(
        workspace,
        experiment,
        step=expected_step,
        parent_step=document["parent_step"],
        mesh_path=root / candidate_mesh_rel,
        measurement_path=workspace / document["measurement_path"],
        measurement=measurement,
        preview_root=root / "preview",
        preview=preview,
    )
    for key, expected in identities.items():
        if document[key] != expected:
            _fail("identity_conflict", f"Measured Step {key} conflicts")
    if document["compare_to"] != document["parent_step"]:
        _fail("parent_mismatch", "Measured Step compare_to and parent differ")
    if expected_step == 0:
        if document["parent_step"] is not None or document["cycle"] is not None:
            _fail("parent_mismatch", "Measured Step 0 cannot have a parent or cycle")
    elif (
        not isinstance(document["parent_step"], int)
        or document["parent_step"] < 0
        or document["parent_step"] >= expected_step
        or document["cycle"] != expected_step
    ):
        _fail("parent_mismatch", "repair Measured Step ancestry is invalid")
    if document["accepted"] != _accepted(measurement):
        _fail("identity_conflict", "Measured Step acceptance conflicts with evidence")
    if document["no_observable_geometry_change"] != measurement["no_observable_geometry_change"]:
        _fail("identity_conflict", "Measured Step no-op fact conflicts with evidence")
    source_measurement = workspace / document["measurement_path"]
    if not source_measurement.is_file() or source_measurement.read_bytes() != (root / "measurement.json").read_bytes():
        _fail("corrupt_workspace", "published VoxBlame summary conflicts with Measured Step")
    return dict(document)


def _build_graph(workspace: Path, *, validate_steps: bool) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    steps_root = workspace / "steps"
    if steps_root.exists():
        for child in sorted(steps_root.iterdir()):
            if child.name.startswith(".tmp-"):
                continue
            if not child.is_dir() or len(child.name) != 6 or not child.name.isdigit():
                _fail("corrupt_workspace", f"invalid Measured Step path: {child.name}")
            step_number = int(child.name)
            document = (
                _validate_step_directory(workspace, child, expected_step=step_number)
                if validate_steps
                else _read_json(child / "step.json", f"$.steps[{step_number}]")
            )
            steps.append(
                {
                    "step": step_number,
                    "parent_step": document["parent_step"],
                    "cycle": document["cycle"],
                    "accepted": document["accepted"],
                    "no_observable_geometry_change": document[
                        "no_observable_geometry_change"
                    ],
                    "candidate_mesh_sha256": document["candidate_mesh_sha256"],
                    "observable_sha256": document["observable_sha256"],
                    "measurement": f"steps/{step_number:06d}/measurement.json",
                    "preview": f"steps/{step_number:06d}/preview/preview.json",
                }
            )
    if steps and steps[0]["step"] != 0:
        _fail("parent_mismatch", "Workspace graph is missing Measured Step 0")
    if len(steps) > MAX_REPAIR_CYCLES + 1:
        _fail("budget_violation", "Workspace exceeds the five-cycle budget")
    cycles: list[dict[str, Any]] = []
    cycles_root = workspace / "cycles"
    if cycles_root.exists():
        for child in sorted(cycles_root.iterdir()):
            if child.name.startswith(".tmp-"):
                continue
            if not child.is_dir() or len(child.name) != 6 or not child.name.isdigit():
                _fail("corrupt_workspace", f"invalid Repair Cycle path: {child.name}")
            cycle_number = int(child.name)
            step_document = _read_json(
                workspace / "steps" / f"{cycle_number:06d}" / "step.json",
                "$.cycle.step",
            )
            document = (
                _validate_cycle_directory(
                    workspace,
                    child,
                    expected_cycle=cycle_number,
                    step=step_document,
                )
                if validate_steps
                else _read_json(child / "cycle.json", "$.cycle")
            )
            cycles.append(
                {
                    "cycle": cycle_number,
                    "from_step": document["from_step"],
                    "to_step": document["to_step"],
                    "attempt_ids": document["attempt_ids"],
                    "plan_digest": document["plan_digest"],
                    "no_observable_geometry_change": document[
                        "no_observable_geometry_change"
                    ],
                    "diff": f"cycles/{cycle_number:06d}/diff.json",
                }
            )
    if [item["cycle"] for item in cycles] != list(range(1, len(cycles) + 1)):
        _fail("parent_mismatch", "Repair Cycle numbers must be contiguous")
    nonzero_steps = [item["step"] for item in steps if item["step"] != 0]
    if nonzero_steps != [item["cycle"] for item in cycles]:
        _fail(
            "incomplete_transaction",
            "every nonzero Measured Step requires its matching Repair Cycle",
        )
    if len(cycles) > MAX_REPAIR_CYCLES:
        _fail("budget_violation", "Workspace exceeds five Repair Cycles")
    accepted_steps = [item["step"] for item in steps if item["accepted"]]
    failed_attempts: list[dict[str, Any]] = []
    attempts_root = workspace / "attempts"
    if attempts_root.exists():
        for child in sorted(attempts_root.iterdir()):
            if child.name.startswith(".tmp-"):
                continue
            if not child.is_dir() or len(child.name) != 6 or not child.name.isdigit():
                _fail("corrupt_workspace", f"invalid Attempt path: {child.name}")
            document = (
                _validate_published_attempt(child, expected_attempt=int(child.name))
                if validate_steps
                else _read_json(child / "attempt.json", "$.attempt")
            )
            failed_attempts.append(
                {
                    "attempt": document["attempt"],
                    "intended_step": document["intended_step"],
                    "from_step": document["from_step"],
                    "result": document["result"],
                    "classification": document["classification"],
                    "plan_digest": document["plan_digest"],
                }
            )
    attempt_ids_by_step: dict[int, set[int]] = {}
    tool_failure_counts: dict[int, int] = {}
    for item in failed_attempts:
        step = item["intended_step"]
        attempt_ids_by_step.setdefault(step, set()).add(item["attempt"])
        if item["result"] == "tool_failure":
            tool_failure_counts[step] = tool_failure_counts.get(step, 0) + 1
    for item in steps:
        manifest = _read_json(
            workspace / "steps" / f"{item['step']:06d}" / "step.json", "$.step"
        )
        attempt_ids_by_step.setdefault(item["step"], set()).update(
            manifest["attempt_ids"]
        )
    if any(
        len(value) > MAX_ATTEMPTS_PER_STEP
        for value in attempt_ids_by_step.values()
    ):
        _fail("budget_violation", "an intended step exceeds three attempts")
    if any(
        value > MAX_TOOL_FAILURES_PER_STEP for value in tool_failure_counts.values()
    ):
        _fail("budget_violation", "an intended step exceeds two tool failures")
    all_attempt_ids = set().union(*attempt_ids_by_step.values()) if attempt_ids_by_step else set()
    total_attempts = len(all_attempt_ids)
    tool_failures = sum(tool_failure_counts.values())
    return {
        "schema": INDEX_SCHEMA,
        "steps": steps,
        "cycles": cycles,
        "failed_attempts": failed_attempts,
        "accepted_steps": accepted_steps,
        "budget": {
            "completed_cycles": len(cycles),
            "remaining_cycles": MAX_REPAIR_CYCLES - len(cycles),
            "total_attempts": total_attempts,
            "tool_failures": tool_failures,
        },
        "heads": _heads_from_steps(steps),
    }


def _validate_git_evidence(workspace: Path, graph: Mapping[str, Any]) -> None:
    _require_git_root(workspace)
    setup_messages = _git(
        workspace, "log", "--format=%B", "--", "workspace.json"
    ).stdout
    if f"Workspace-Schema: {WORKSPACE_SCHEMA}" not in setup_messages:
        _fail("missing_git_evidence", "Git metadata is missing Workspace setup identity")
    for step in graph["steps"]:
        trailer = f"Workspace-Step: {step['step']}"
        messages = _git(
            workspace, "log", "--format=%B", "--", f"steps/{step['step']:06d}"
        ).stdout
        if trailer not in messages:
            _fail("missing_git_evidence", f"Git metadata is missing {trailer}")
    for attempt in graph["failed_attempts"]:
        trailer = f"Workspace-Attempt: {attempt['attempt']}"
        messages = _git(
            workspace,
            "log",
            "--format=%B",
            "--",
            f"attempts/{attempt['attempt']:06d}",
        ).stdout
        if trailer not in messages:
            _fail("missing_git_evidence", f"Git metadata is missing {trailer}")
    for cycle in graph["cycles"]:
        trailer = f"Repair-Cycle: {cycle['cycle']}"
        messages = _git(
            workspace,
            "log",
            "--format=%B",
            "--",
            f"cycles/{cycle['cycle']:06d}",
        ).stdout
        if trailer not in messages:
            _fail("missing_git_evidence", f"Git metadata is missing {trailer}")
    tracked = _git(workspace, "ls-files", "-s").stdout
    for line in tracked.splitlines():
        path = line.split("\t", 1)[-1]
        if Path(path).suffix.lower() in _LFS_SUFFIXES:
            attr = _git(workspace, "check-attr", "filter", "--", path).stdout.strip()
            if not attr.endswith(": lfs"):
                _fail("lfs_contract_violation", f"LFS filter is not active for {path}")


def _accepted(measurement: Mapping[str, Any]) -> bool:
    return all(measurement["objective_facts"].values())


def _inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _file_sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _step_zero_voxblame_paths(workspace: Path, measurement: Path) -> list[str]:
    relative = _workspace_relative(workspace, measurement)
    if not relative.startswith("voxblame/steps/000000/"):
        _fail("invalid_workspace_path", "Step 0 measurement must be under voxblame/steps/000000")
    required = ["voxblame/session.json", "voxblame/reference.vbsvo", "voxblame/steps/000000"]
    for path in required:
        if not (workspace / path).exists():
            _fail("unsupported_or_invalid_voxblame_state", f"missing {path}")
    return required


def _voxblame_step_path(workspace: Path, measurement: Path, step: int) -> str:
    relative = _workspace_relative(workspace, measurement)
    expected = f"voxblame/steps/{step:06d}/"
    if not relative.startswith(expected):
        _fail(
            "invalid_workspace_path",
            f"measurement must be under {expected.rstrip('/')}",
        )
    path = expected.rstrip("/")
    if not (workspace / path).is_dir():
        _fail("unsupported_or_invalid_voxblame_state", f"missing {path}")
    return path


def _configure_git_contract(workspace: Path) -> None:
    _git(workspace, "lfs", "version")
    _git(workspace, "lfs", "install", "--local")
    ignore = workspace / ".gitignore"
    _append_contract_lines(ignore, ["/run/", "/work/", ".tmp-*", "**/.tmp-*"])
    attributes = workspace / ".gitattributes"
    _append_contract_lines(
        attributes,
        [f"*{suffix} filter=lfs diff=lfs merge=lfs -text" for suffix in sorted(_LFS_SUFFIXES)],
    )


def _append_contract_lines(path: Path, required: Iterable[str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = list(existing)
    for line in required:
        if line not in lines:
            lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _commit_protocol_paths(
    workspace: Path,
    paths: list[str],
    subject: str,
    trailers: Mapping[str, str],
    *,
    allow_noop: bool = False,
) -> bool:
    _reject_staged_changes(workspace)
    normalized = sorted(set(paths))
    _git(workspace, "add", "--", *normalized)
    staged = set(_git(workspace, "diff", "--cached", "--name-only").stdout.splitlines())
    allowed_files: set[str] = set()
    for declared in normalized:
        path = workspace / declared
        if path.is_dir():
            allowed_files.update(
                child.relative_to(workspace).as_posix()
                for child in path.rglob("*")
                if child.is_file()
            )
        elif path.exists():
            allowed_files.add(declared)
    if not staged and allow_noop:
        return False
    if not staged or not staged.issubset(allowed_files):
        _fail("git_scope_violation", "Git index contains paths outside this protocol publication")
    for path in staged:
        if Path(path).suffix.lower() in _LFS_SUFFIXES:
            attr = _git(workspace, "check-attr", "filter", "--", path).stdout.strip()
            if not attr.endswith(": lfs"):
                _fail("lfs_contract_violation", f"LFS filter is not active for {path}")
    message = subject + "\n\n" + "\n".join(f"{key}: {value}" for key, value in trailers.items())
    _git(workspace, "commit", "-m", message)
    return True


def _reject_staged_changes(workspace: Path) -> None:
    staged = _git(workspace, "diff", "--cached", "--name-only").stdout.strip()
    if staged:
        _fail("git_scope_violation", "Workspace refuses pre-existing staged paths")


def _require_git_root(workspace: Path) -> None:
    if not workspace.is_dir():
        _fail("invalid_workspace", "Workspace directory does not exist")
    result = _git(workspace, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0 or Path(result.stdout.strip()).resolve() != workspace:
        _fail("invalid_git_workspace", "Workspace must be the root of an existing Git repository")


def _git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ("git", *args),
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout).split())
        _fail("git_operation_failed", detail or f"git {' '.join(args)} failed")
    return result


def _load_active_attempt(workspace: Path, attempt: int) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root = workspace / "work/attempts" / f"{attempt:06d}"
    document = _read_json(root / "attempt.json", "$.attempt")
    _closed_object(document, _ACTIVE_ATTEMPT_FIELDS, "$.attempt")
    if document["schema"] != ATTEMPT_SCHEMA or document["attempt"] != attempt or document["result"] != "active":
        _fail("invalid_attempt", "attempt is not active")
    plan = _read_json(root / "plan.json", "$.attempt.plan")
    schema = plan.get("schema")
    if not isinstance(schema, str):
        _fail("corrupt_workspace", "active attempt plan schema is missing")
    digest = hashlib.sha256(
        schema.encode("utf-8") + b"\0" + _json_bytes(plan)
    ).hexdigest()
    if digest != document["plan_digest"]:
        _fail("corrupt_workspace", "active attempt plan digest mismatch")
    return root, document, plan


def _load_command_documents(active_root: Path) -> list[dict[str, Any]]:
    commands_root = active_root / "commands"
    if not commands_root.exists():
        return []
    documents: list[dict[str, Any]] = []
    for child in sorted(commands_root.iterdir()):
        if child.name.startswith(".tmp-"):
            _fail("incomplete_transaction", "Attempt contains a staged command")
        if not child.is_dir() or len(child.name) != 6 or not child.name.isdigit():
            _fail("corrupt_workspace", f"invalid command path: {child.name}")
        value = _read_json(child / "command.json", "$.command")
        if value.get("schema") != COMMAND_SCHEMA or value.get("command") != int(
            child.name
        ):
            _fail("corrupt_workspace", "command manifest identity mismatch")
        for stream in ("stdout", "stderr"):
            metadata = value.get(stream)
            if not isinstance(metadata, Mapping):
                _fail("corrupt_workspace", "command log metadata is invalid")
            log_path = child / f"{stream}.log"
            if (
                not log_path.is_file()
                or log_path.stat().st_size != metadata.get("stored_byte_count")
            ):
                _fail("corrupt_workspace", "command log size mismatch")
        documents.append(value)
    return documents


def _validate_published_attempt(root: Path, *, expected_attempt: int) -> dict[str, Any]:
    value = _read_json(root / "attempt.json", "$.attempt")
    document = _closed_object(value, _PUBLISHED_ATTEMPT_FIELDS, "$.attempt")
    if document["schema"] != ATTEMPT_SCHEMA or document["attempt"] != expected_attempt:
        _fail("corrupt_workspace", "Attempt identity mismatch")
    if document["result"] not in {
        "tool_failure",
        "strategy_changed",
        "no_feasible_strategy",
    }:
        _fail("corrupt_workspace", "Attempt result is unsupported")
    identity_document = dict(document)
    identity = identity_document.pop("identity_sha256")
    if identity != _identity(ATTEMPT_SCHEMA, identity_document):
        _fail("corrupt_workspace", "Attempt identity digest mismatch")
    files = document["files"]
    if not isinstance(files, list) or not files:
        _fail("corrupt_workspace", "Attempt file inventory is empty")
    entries = [
        _closed_object(
            item,
            {"path", "sha256", "size_bytes"},
            f"$.attempt.files[{index}]",
        )
        for index, item in enumerate(files)
    ]
    expected_paths = {item["path"] for item in entries}
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "attempt.json"
    }
    if expected_paths != actual_paths:
        _fail("corrupt_workspace", "Attempt file inventory mismatch")
    for item in entries:
        path = _relative_member(root, item["path"], "$.attempt.files")
        if path.stat().st_size != item["size_bytes"] or _file_sha256(path) != item[
            "sha256"
        ]:
            _fail("corrupt_workspace", "Attempt artifact digest mismatch")
    commands = _load_command_documents(root)
    if document["command_ids"] != [item["command"] for item in commands]:
        _fail("corrupt_workspace", "Attempt command index mismatch")
    if document["result"] == "tool_failure" and not any(
        item["exit_code"] != 0 for item in commands
    ):
        _fail("corrupt_workspace", "tool_failure has no failing command")
    return dict(document)


def _redact_argv(argv: list[str]) -> list[str]:
    redacted: list[str] = []
    hide_next = False
    header_next = False
    for value in argv:
        lowered = value.lower()
        if header_next:
            redacted.append(
                "<redacted>" if lowered.startswith("authorization") else value
            )
            header_next = False
            continue
        if hide_next:
            redacted.append("<redacted>")
            hide_next = False
            continue
        matched_assignment = next(
            (
                secret
                for secret in _SECRET_ARGUMENTS
                if lowered.startswith(secret + "=")
            ),
            None,
        )
        if matched_assignment is not None:
            redacted.append(value.split("=", 1)[0] + "=<redacted>")
            continue
        if lowered.startswith("authorization:"):
            redacted.append("Authorization: <redacted>")
            continue
        redacted.append(value)
        if lowered in _SECRET_ARGUMENTS:
            hide_next = True
        elif lowered in {"--header", "-h"}:
            header_next = True
    return redacted


def _bounded_log(data: bytes) -> tuple[bytes, dict[str, Any]]:
    original = len(data)
    if original <= MAX_LOG_BYTES:
        stored = data
        truncated = False
    else:
        half = MAX_LOG_BYTES // 2
        stored = data[:half] + data[-half:]
        truncated = True
    return stored, {
        "path": "",
        "original_byte_count": original,
        "stored_byte_count": len(stored),
        "truncated": truncated,
        "truncation_policy": "head-tail-65536/1",
    }


def _check_attempt_budget(workspace: Path, intended_step: int) -> None:
    attempts = 0
    tool_failures = 0
    attempts_root = workspace / "attempts"
    if attempts_root.exists():
        for path in attempts_root.glob("[0-9][0-9][0-9][0-9][0-9][0-9]/attempt.json"):
            value = _read_json(path, "$.attempt")
            if value.get("intended_step") == intended_step:
                attempts += 1
                tool_failures += value.get("result") == "tool_failure"
    active_root = workspace / "work/attempts"
    if active_root.exists():
        for path in active_root.glob("[0-9][0-9][0-9][0-9][0-9][0-9]/attempt.json"):
            value = _read_json(path, "$.attempt")
            if value.get("intended_step") == intended_step:
                attempts += 1
    if attempts >= MAX_ATTEMPTS_PER_STEP:
        _fail("budget_violation", "intended step has exhausted its three attempts")
    if tool_failures >= MAX_TOOL_FAILURES_PER_STEP:
        _fail("budget_violation", "intended step has exhausted its two tool failures")


def _next_attempt_id(workspace: Path) -> int:
    ids: list[int] = []
    for base in (workspace / "attempts", workspace / "work/attempts"):
        if base.exists():
            ids.extend(int(path.name) for path in base.iterdir() if path.is_dir() and path.name.isdigit())
    step_glob = (workspace / "steps").glob("*/step.json") if (workspace / "steps").exists() else []
    for step_path in step_glob:
        ids.extend(_read_json(step_path, "$.step").get("attempt_ids", []))
    return max(ids, default=0) + 1


def _validate_repair_plan_boundary(plan: Mapping[str, Any], from_step: int | None) -> None:
    required = {
        "schema",
        "from_step",
        "selected_targets",
        "planned_edits",
        "rationale",
        "preview_observation",
    }
    _closed_object(plan, required, "$.plan")
    _const(plan["schema"], "voxblame.repair-batch/1", "$.plan.schema")
    if plan["from_step"] != from_step:
        _fail("parent_mismatch", "Repair Batch from_step conflicts with attempt")
    if not isinstance(plan["selected_targets"], list) or not plan["selected_targets"]:
        _fail("invalid_attempt", "Repair Batch must select one or more targets")
    if not isinstance(plan["planned_edits"], list) or not plan["planned_edits"]:
        _fail("invalid_attempt", "Repair Batch must contain Planned Edits")
    selected_keys: set[str] = set()
    for index, item in enumerate(plan["selected_targets"]):
        target = _closed_object(
            item,
            {"target_key", "mask_sha256"},
            f"$.plan.selected_targets[{index}]",
        )
        _stable_key(target["target_key"], f"$.plan.selected_targets[{index}].target_key")
        _sha256(target["mask_sha256"], f"$.plan.selected_targets[{index}].mask_sha256")
        if target["target_key"] in selected_keys:
            _fail("invalid_attempt", "Repair Batch target keys must be unique")
        selected_keys.add(target["target_key"])
    edit_keys: set[str] = set()
    mapped_targets: set[str] = set()
    for index, item in enumerate(plan["planned_edits"]):
        edit = _closed_object(
            item,
            {"edit_key", "target_keys", "description"},
            f"$.plan.planned_edits[{index}]",
        )
        _stable_key(edit["edit_key"], f"$.plan.planned_edits[{index}].edit_key")
        if edit["edit_key"] in edit_keys:
            _fail("invalid_attempt", "Planned Edit keys must be unique")
        edit_keys.add(edit["edit_key"])
        if (
            not isinstance(edit["target_keys"], list)
            or not edit["target_keys"]
            or len(set(edit["target_keys"])) != len(edit["target_keys"])
            or not set(edit["target_keys"]).issubset(selected_keys)
        ):
            _fail("invalid_attempt", "Planned Edit target mapping is invalid")
        mapped_targets.update(edit["target_keys"])
        _nonempty_string(
            edit["description"], f"$.plan.planned_edits[{index}].description"
        )
    if mapped_targets != selected_keys:
        _fail("invalid_attempt", "every selected target must map to a Planned Edit")
    _nonempty_string(plan["rationale"], "$.plan.rationale")
    _nonempty_string(plan["preview_observation"], "$.plan.preview_observation")


def _find_incomplete_transactions(workspace: Path) -> list[dict[str, Any]]:
    recovery: list[dict[str, Any]] = []
    for path in workspace.rglob(".tmp-*"):
        if ".git" in path.parts:
            continue
        recovery.append(
            {
                "classification": "staged_transaction",
                "path": path.relative_to(workspace).as_posix(),
            }
        )
    steps_root = workspace / "steps"
    if (workspace / "voxblame/steps").exists():
        published = {path.name for path in steps_root.iterdir() if path.is_dir()} if steps_root.exists() else set()
        for path in (workspace / "voxblame/steps").iterdir():
            if path.is_dir() and path.name.isdigit() and path.name not in published:
                recovery.append(
                    {
                        "classification": "orphan_voxblame_step",
                        "path": path.relative_to(workspace).as_posix(),
                    }
                )
    return sorted(recovery, key=lambda item: item["path"])


def _recover_cycle_transaction(workspace: Path, transaction: Path) -> int:
    marker = _read_json(transaction / "transaction.json", "$.transaction")
    root = _closed_object(
        marker,
        {
            "schema",
            "kind",
            "cycle",
            "step_identity_sha256",
            "cycle_identity_sha256",
        },
        "$.transaction",
    )
    if (
        root["schema"] != "mesh-to-cad.transaction/1"
        or root["kind"] != "repair_cycle"
        or not isinstance(root["cycle"], int)
        or root["cycle"] <= 0
        or root["cycle"] > MAX_REPAIR_CYCLES
    ):
        _fail("unknown_staged_state", "transaction marker is unsupported")
    cycle = root["cycle"]
    step_target = workspace / "steps" / f"{cycle:06d}"
    cycle_target = workspace / "cycles" / f"{cycle:06d}"
    step_stage = transaction / "step"
    cycle_stage = transaction / "cycle"

    if step_target.exists():
        published_step = _read_json(step_target / "step.json", "$.recovery.step")
        if published_step.get("identity_sha256") != root["step_identity_sha256"]:
            _fail("identity_conflict", "published Step conflicts with staged transaction")
        if step_stage.exists():
            staged_step = _read_json(step_stage / "step.json", "$.recovery.step")
            if staged_step.get("identity_sha256") != root["step_identity_sha256"]:
                _fail("identity_conflict", "staged Step identity conflicts")
            shutil.rmtree(step_stage)
    else:
        if not step_stage.is_dir():
            _fail("incomplete_transaction", "transaction cannot recover its Step")
        step_document = _validate_step_directory(
            workspace, step_stage, expected_step=cycle
        )
        if step_document["identity_sha256"] != root["step_identity_sha256"]:
            _fail("identity_conflict", "staged Step identity conflicts")
        step_stage.rename(step_target)

    step_document = _read_json(step_target / "step.json", "$.recovery.step")
    if cycle_target.exists():
        published_cycle = _read_json(
            cycle_target / "cycle.json", "$.recovery.cycle"
        )
        if published_cycle.get("identity_sha256") != root["cycle_identity_sha256"]:
            _fail("identity_conflict", "published Cycle conflicts with staged transaction")
        if cycle_stage.exists():
            staged_cycle = _read_json(
                cycle_stage / "cycle.json", "$.recovery.cycle"
            )
            if staged_cycle.get("identity_sha256") != root["cycle_identity_sha256"]:
                _fail("identity_conflict", "staged Cycle identity conflicts")
            shutil.rmtree(cycle_stage)
    else:
        if not cycle_stage.is_dir():
            _fail("incomplete_transaction", "transaction cannot recover its Cycle")
        cycle_document = _validate_cycle_directory(
            workspace,
            cycle_stage,
            expected_cycle=cycle,
            step=step_document,
        )
        if cycle_document["identity_sha256"] != root["cycle_identity_sha256"]:
            _fail("identity_conflict", "staged Cycle identity conflicts")
        cycle_target.parent.mkdir(exist_ok=True)
        cycle_stage.rename(cycle_target)

    (transaction / "transaction.json").unlink()
    if any(transaction.iterdir()):
        _fail("unknown_staged_state", "transaction contains unknown recovery files")
    transaction.rmdir()
    graph = rebuild_index(workspace, validate=False)
    cycle_document = _read_json(cycle_target / "cycle.json", "$.recovery.cycle")
    step_document = _read_json(step_target / "step.json", "$.recovery.step")
    measurement = workspace / step_document["measurement_path"]
    _commit_protocol_paths(
        workspace,
        [
            f"steps/{cycle:06d}",
            f"cycles/{cycle:06d}",
            _voxblame_step_path(workspace, measurement, cycle),
            "step_index.json",
        ],
        f"repair cycle {cycle}: recover interrupted publication",
        {
            "Repair-Cycle": str(cycle),
            "Workspace-Step": str(cycle),
            "From-Step": str(cycle_document["from_step"]),
            "Workspace-Attempt": str(cycle_document["attempt_ids"][-1]),
            "Plan-SHA256": cycle_document["plan_digest"],
            "Candidate-SHA256": step_document["candidate_mesh_sha256"],
            "Observable-SHA256": step_document["observable_sha256"],
        },
        allow_noop=True,
    )
    active = workspace / "work/attempts" / f"{cycle_document['attempt_ids'][-1]:06d}"
    if active.exists():
        shutil.rmtree(active)
    # Ensure the graph was structurally rebuildable before final deep validation.
    if graph["budget"]["completed_cycles"] < cycle:
        _fail("incomplete_transaction", "recovered cycle is absent from graph")
    return cycle


def _recover_setup_transaction(workspace: Path, transaction: Path) -> None:
    marker = _read_json(transaction / "transaction.json", "$.transaction")
    root = _closed_object(
        marker,
        {"schema", "kind", "workspace_identity_sha256"},
        "$.transaction",
    )
    if (
        root["schema"] != "mesh-to-cad.transaction/1"
        or root["kind"] != "setup"
    ):
        _fail("unknown_staged_state", "setup transaction marker is unsupported")
    for name in ("input", "setup", "experiment.json", "workspace.json"):
        staged = transaction / name
        target = workspace / name
        if target.exists():
            if staged.exists():
                if _path_digest(staged) != _path_digest(target):
                    _fail(
                        "identity_conflict",
                        f"published setup path conflicts with staged {name}",
                    )
                if staged.is_dir():
                    shutil.rmtree(staged)
                else:
                    staged.unlink()
        else:
            if not staged.exists():
                _fail(
                    "incomplete_transaction",
                    f"setup transaction cannot recover {name}",
                )
            staged.rename(target)
    workspace_document = _load_workspace_document(workspace)
    if (
        _identity(WORKSPACE_SCHEMA, workspace_document)
        != root["workspace_identity_sha256"]
    ):
        _fail("identity_conflict", "setup transaction identity conflicts")
    (transaction / "transaction.json").unlink()
    if any(transaction.iterdir()):
        _fail("unknown_staged_state", "setup transaction contains unknown files")
    transaction.rmdir()
    graph = rebuild_index(workspace, validate=False)
    _commit_protocol_paths(
        workspace,
        [
            ".gitignore",
            ".gitattributes",
            "input",
            "setup",
            "experiment.json",
            "workspace.json",
            "step_index.json",
        ],
        "setup: recover canonical Workspace",
        {
            "Workspace-Schema": WORKSPACE_SCHEMA,
            "Workspace-Id": workspace_document["workspace_id"],
            "Canonical-Reference-SHA256": workspace_document[
                "canonical_reference_sha256"
            ],
        },
        allow_noop=True,
    )
    if graph["steps"]:
        _fail("identity_conflict", "setup recovery unexpectedly contains steps")


def _recover_step_zero_transaction(workspace: Path, transaction: Path) -> int:
    marker = _read_json(transaction / "transaction.json", "$.transaction")
    root = _closed_object(
        marker,
        {
            "schema",
            "kind",
            "attempt",
            "step_identity_sha256",
        },
        "$.transaction",
    )
    if (
        root["schema"] != "mesh-to-cad.transaction/1"
        or root["kind"] != "step_zero"
        or not isinstance(root["attempt"], int)
        or root["attempt"] <= 0
    ):
        _fail("unknown_staged_state", "Step 0 transaction marker is unsupported")
    target = workspace / "steps/000000"
    staged = transaction / "step"
    if target.exists():
        published = _read_json(target / "step.json", "$.recovery.step")
        if published.get("identity_sha256") != root["step_identity_sha256"]:
            _fail("identity_conflict", "published Step 0 conflicts")
        if staged.exists():
            staged_document = _read_json(staged / "step.json", "$.recovery.step")
            if staged_document.get("identity_sha256") != root[
                "step_identity_sha256"
            ]:
                _fail("identity_conflict", "staged Step 0 conflicts")
            shutil.rmtree(staged)
    else:
        if not staged.is_dir():
            _fail("incomplete_transaction", "transaction cannot recover Step 0")
        document = _validate_step_directory(workspace, staged, expected_step=0)
        if document["identity_sha256"] != root["step_identity_sha256"]:
            _fail("identity_conflict", "staged Step 0 identity conflicts")
        target.parent.mkdir(exist_ok=True)
        staged.rename(target)
    (transaction / "transaction.json").unlink()
    if any(transaction.iterdir()):
        _fail("unknown_staged_state", "Step 0 transaction contains unknown files")
    transaction.rmdir()
    graph = rebuild_index(workspace, validate=False)
    document = _read_json(target / "step.json", "$.recovery.step")
    measurement = workspace / document["measurement_path"]
    _commit_protocol_paths(
        workspace,
        ["steps/000000", "step_index.json", *_step_zero_voxblame_paths(workspace, measurement)],
        "step 0: recover initial Measured Step",
        {
            "Workspace-Step": "0",
            "Workspace-Attempt": str(root["attempt"]),
            "Candidate-SHA256": document["candidate_mesh_sha256"],
            "Observable-SHA256": document["observable_sha256"],
        },
        allow_noop=True,
    )
    active = workspace / "work/attempts" / f"{root['attempt']:06d}"
    if active.exists():
        shutil.rmtree(active)
    if not graph["steps"] or graph["steps"][0]["step"] != 0:
        _fail("incomplete_transaction", "recovered Step 0 is absent from graph")
    return 0


def _validate_region_diff_boundary(
    value: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    plan_digest: str,
    from_step: int,
    to_step: int,
    before_observable: str,
    after_observable: str,
) -> None:
    required = {
        "schema",
        "coordinate_contract",
        "max_depth",
        "from_step",
        "to_step",
        "repair_batch",
        "measurement_trajectory",
        "identity",
    }
    if not required.issubset(value):
        _fail("unsupported_or_invalid_voxblame_state", "Region Diff is incomplete")
    if (
        value["schema"] != "voxblame.region-diff/1"
        or value["coordinate_contract"] != COORDINATE_CONTRACT
        or value["max_depth"] != MAX_DEPTH
        or value["from_step"] != from_step
        or value["to_step"] != to_step
    ):
        _fail("parent_mismatch", "Region Diff edge conflicts with Repair Cycle")
    batch = value["repair_batch"]
    if not isinstance(batch, Mapping) or (
        batch.get("schema") != "voxblame.repair-batch/1"
        or batch.get("from_step") != from_step
        or batch.get("plan_sha256") != plan_digest
    ):
        _fail("identity_conflict", "Region Diff Repair Batch identity conflicts")
    expected_plan_digest = hashlib.sha256(
        b"voxblame.repair-batch/1\0" + _json_bytes(plan)
    ).hexdigest()
    if plan_digest != expected_plan_digest:
        _fail("identity_conflict", "frozen Repair Batch digest conflicts")
    trajectory = value["measurement_trajectory"]
    observable = trajectory.get("observable_geometry") if isinstance(trajectory, Mapping) else None
    if (
        not isinstance(trajectory, Mapping)
        or trajectory.get("steps") != [from_step, to_step]
        or not isinstance(observable, Mapping)
        or observable.get("before_sha256") != before_observable
        or observable.get("after_sha256") != after_observable
        or observable.get("changed") != (before_observable != after_observable)
    ):
        _fail("identity_conflict", "Region Diff Observable Geometry trajectory conflicts")
    identity = value["identity"]
    if not isinstance(identity, Mapping) or set(identity) != {"region_diff_sha256"}:
        _fail("unsupported_or_invalid_voxblame_state", "Region Diff identity is invalid")
    expected_document = dict(value)
    expected_document.pop("identity")
    expected_identity = hashlib.sha256(
        b"voxblame.region-diff/1\0" + _json_bytes(expected_document)
    ).hexdigest()
    if identity["region_diff_sha256"] != expected_identity:
        _fail("corrupt_workspace", "Region Diff identity digest mismatch")


def _validate_assessment(
    value: Mapping[str, Any], *, from_step: int, to_step: int
) -> None:
    root = _closed_object(
        value,
        {"schema", "from_step", "to_step", "preview_observation", "summary"},
        "$.assessment",
    )
    if (
        root["schema"] != "mesh-to-cad.assessment/1"
        or root["from_step"] != from_step
        or root["to_step"] != to_step
    ):
        _fail("parent_mismatch", "Agent assessment edge conflicts")
    _nonempty_string(root["preview_observation"], "$.assessment.preview_observation")
    _nonempty_string(root["summary"], "$.assessment.summary")


def _validate_source_changes(
    value: Mapping[str, Any], *, from_step: int, to_step: int
) -> None:
    root = _closed_object(
        value,
        {"schema", "from_step", "to_step", "files"},
        "$.source_changes",
    )
    if (
        root["schema"] != "mesh-to-cad.source-changes/1"
        or root["from_step"] != from_step
        or root["to_step"] != to_step
    ):
        _fail("parent_mismatch", "source-change evidence edge conflicts")
    if not isinstance(root["files"], list) or not root["files"]:
        _fail("invalid_contract", "source-change evidence must list files")
    seen: set[str] = set()
    for index, item in enumerate(root["files"]):
        entry = _closed_object(
            item,
            {"path", "before_sha256", "after_sha256"},
            f"$.source_changes.files[{index}]",
        )
        pure = PurePosixPath(entry["path"])
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            _fail("invalid_workspace_path", "source-change path is invalid")
        if entry["path"] in seen:
            _fail("invalid_contract", "duplicate source-change path")
        seen.add(entry["path"])
        before = entry["before_sha256"]
        after = entry["after_sha256"]
        if before is not None:
            _sha256(before, "$.source_changes.before_sha256")
        if after is not None:
            _sha256(after, "$.source_changes.after_sha256")
        if (before is None and after is None) or before == after:
            _fail("invalid_contract", "source-change digests must describe a change")


def _validate_cycle_directory(
    workspace: Path,
    root: Path,
    *,
    expected_cycle: int,
    step: Mapping[str, Any],
) -> dict[str, Any]:
    value = _read_json(root / "cycle.json", f"$.cycles[{expected_cycle}]")
    document = _closed_object(value, _CYCLE_FIELDS, f"$.cycles[{expected_cycle}]")
    if (
        document["schema"] != CYCLE_SCHEMA
        or document["cycle"] != expected_cycle
        or document["to_step"] != expected_cycle
        or step["step"] != expected_cycle
        or step["cycle"] != expected_cycle
        or step["parent_step"] != document["from_step"]
        or step["attempt_ids"] != document["attempt_ids"]
        or step["observable_sha256"] != document["to_observable_sha256"]
        or step["no_observable_geometry_change"]
        != document["no_observable_geometry_change"]
    ):
        _fail("parent_mismatch", "Repair Cycle and Measured Step conflict")
    parent = _read_json(
        workspace / "steps" / f"{document['from_step']:06d}" / "step.json",
        "$.cycle.parent",
    )
    if parent["observable_sha256"] != document["from_observable_sha256"]:
        _fail("identity_conflict", "Repair Cycle parent Observable Geometry conflicts")
    identity_document = dict(document)
    identity = identity_document.pop("identity_sha256")
    if identity != _identity(CYCLE_SCHEMA, identity_document):
        _fail("corrupt_workspace", "Repair Cycle identity digest mismatch")
    files = document["files"]
    expected_paths = {item["path"] for item in files if isinstance(item, Mapping)}
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "cycle.json"
    }
    if expected_paths != actual_paths:
        _fail("corrupt_workspace", "Repair Cycle file inventory mismatch")
    for item in files:
        path = _relative_member(root, item["path"], "$.cycle.files")
        if path.stat().st_size != item["size_bytes"] or _file_sha256(path) != item[
            "sha256"
        ]:
            _fail("corrupt_workspace", "Repair Cycle artifact digest mismatch")
    plan = _read_json(root / "plan.json", "$.cycle.plan")
    diff = _read_json(root / "diff.json", "$.cycle.diff")
    assessment = _read_json(root / "assessment.json", "$.cycle.assessment")
    source_changes = _read_json(
        root / "source_changes.json", "$.cycle.source_changes"
    )
    _validate_region_diff_boundary(
        diff,
        plan=plan,
        plan_digest=document["plan_digest"],
        from_step=document["from_step"],
        to_step=document["to_step"],
        before_observable=document["from_observable_sha256"],
        after_observable=document["to_observable_sha256"],
    )
    _validate_assessment(
        assessment, from_step=document["from_step"], to_step=document["to_step"]
    )
    _validate_source_changes(
        source_changes,
        from_step=document["from_step"],
        to_step=document["to_step"],
    )
    if _file_sha256(root / "assessment.json") != document["assessment_sha256"]:
        _fail("corrupt_workspace", "assessment digest mismatch")
    if (
        _file_sha256(root / "source_changes.json")
        != document["source_changes_sha256"]
    ):
        _fail("corrupt_workspace", "source-change digest mismatch")
    if (
        diff["identity"]["region_diff_sha256"]
        != document["region_diff_sha256"]
    ):
        _fail("identity_conflict", "Repair Cycle Region Diff identity conflicts")
    return dict(document)


def _cycle_attempt_ids(
    workspace: Path, intended_step: int, successful_attempt: int
) -> list[int]:
    ids: list[int] = []
    attempts_root = workspace / "attempts"
    if attempts_root.exists():
        for path in sorted(attempts_root.glob("*/attempt.json")):
            value = _read_json(path, "$.attempt")
            if value.get("intended_step") == intended_step:
                ids.append(value["attempt"])
    ids.append(successful_attempt)
    if len(ids) > MAX_ATTEMPTS_PER_STEP:
        _fail("budget_violation", "Repair Cycle exceeds three attempts")
    return ids


def _published_numbers(root: Path) -> list[int]:
    if not root.exists():
        return []
    return sorted(
        int(path.name)
        for path in root.iterdir()
        if path.is_dir() and path.name.isdigit()
    )


def _path_digest(path: Path) -> str:
    if path.is_file():
        return _file_sha256(path)
    document = [
        {
            "path": child.relative_to(path).as_posix(),
            "sha256": _file_sha256(child),
        }
        for child in sorted(path.rglob("*"))
        if child.is_file()
    ]
    return hashlib.sha256(_json_bytes({"files": document})).hexdigest()


def _graph_heads(graph: Mapping[str, Any]) -> list[int]:
    return list(graph["heads"])


def _heads_from_steps(steps: list[dict[str, Any]]) -> list[int]:
    parents = {
        item["parent_step"] for item in steps if item["parent_step"] is not None
    }
    return [item["step"] for item in steps if item["step"] not in parents]


def _validate_tree_source(root: Path, path_label: str) -> None:
    if not root.is_dir() or root.is_symlink():
        _fail("invalid_workspace_path", "artifact source must be a real directory", path_label)
    for path in root.rglob("*"):
        if path.is_symlink():
            _fail("invalid_workspace_path", "symlinks are forbidden in formal bundles", path_label)
        if path.name in _FORBIDDEN_TREE_NAMES:
            _fail("invalid_workspace_path", f"forbidden bundle path: {path.name}", path_label)


def _relative_member(root: Path, value: str, path_label: str) -> Path:
    if not isinstance(value, str):
        _fail("invalid_workspace_path", "path must be a string", path_label)
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        _fail("invalid_workspace_path", "path must be normalized and relative", path_label)
    target = root.joinpath(*pure.parts)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        _fail("invalid_workspace_path", "path escapes its artifact root", path_label)
    if not target.is_file() or target.is_symlink():
        _fail("invalid_workspace_path", "declared artifact file is missing", path_label)
    return target


def _workspace_relative(workspace: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(workspace.resolve())
    except ValueError:
        _fail("invalid_workspace_path", "formal artifact path is outside the Workspace")
    if any(part in {"work", "run"} for part in relative.parts):
        _fail("invalid_workspace_path", "formal authority cannot reference mutable telemetry or work")
    return relative.as_posix()


def _closed_object(value: Any, fields: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("invalid_contract", "must be an object", path)
    if set(value) != fields:
        missing = sorted(fields - set(value))
        unknown = sorted(set(value) - fields)
        _fail("invalid_contract", f"closed object mismatch; missing={missing}, unknown={unknown}", path)
    return value


def _const(actual: Any, expected: Any, path: str) -> None:
    if actual != expected:
        _fail("invalid_contract", f"must equal {expected!r}", path)


def _nonempty_string(value: Any, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail("invalid_contract", "must be a non-empty string", path)


def _sha256(value: Any, path: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        _fail("invalid_contract", "must be a lowercase SHA-256 digest", path)


def _stable_key(value: Any, path: str) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9._:-]*", value) is None
    ):
        _fail("invalid_contract", "must be a stable lowercase key", path)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("corrupt_workspace", f"cannot read JSON artifact: {path}", label)
    if not isinstance(value, dict):
        _fail("invalid_contract", "JSON artifact must contain an object", label)
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        _fail("corrupt_workspace", f"cannot read artifact: {path}")


def _identity(schema: str, value: Mapping[str, Any]) -> str:
    return hashlib.sha256(schema.encode("utf-8") + b"\0" + _json_bytes(value)).hexdigest()


def _fail(classification: str, detail: str, path: str = "$") -> None:
    raise WorkspaceError(classification, detail, path)


__all__ = [
    "WorkspaceError",
    "begin_attempt",
    "initialize_workspace",
    "publish_step_zero",
    "publish_cycle",
    "record_attempt",
    "recover_workspace",
    "rebuild_index",
    "run_attempt_command",
    "validate_workspace",
    "workspace_status",
]
