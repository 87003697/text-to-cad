"""Narrow Workspace facade and provider-free terminal evidence compiler.

``workspace_core`` remains the implementation of the existing Workspace
protocol.  This module is the caller boundary and adds only an in-memory,
closed terminal evidence bundle.  Persistence and crash recovery belong to the
outer runner that owns the returned identity handoff.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import workspace_core as _core


# Preserve the existing public function objects and their stable error behavior
# while moving callers to this module's import boundary.
DEFAULT_COMMAND_SECONDS = _core.DEFAULT_COMMAND_SECONDS
FAILED_ATTEMPT_RESULTS = _core.FAILED_ATTEMPT_RESULTS
WorkspaceError = _core.WorkspaceError
ValidationResult = _core.ValidationResult
begin_attempt = _core.begin_attempt
finalize_workspace = _core.finalize_workspace
initialize_workspace = _core.initialize_workspace
publish_step_zero = _core.publish_step_zero
publish_cycle = _core.publish_cycle
record_attempt = _core.record_attempt
recover_workspace = _core.recover_workspace
rebuild_index = _core.rebuild_index
run_attempt_command = _core.run_attempt_command
run_canonical_build = _core.run_canonical_build
validate_workspace = _core.validate_workspace
workspace_status = _core.workspace_status


TERMINAL_VALIDATION_SCHEMA = "mesh-to-cad.terminal-validation/1"
CONTENT_MANIFEST_SCHEMA = "mesh-to-cad.content-manifest/1"
TERMINAL_BUNDLE_SCHEMA = "mesh-to-cad.terminal-validation-bundle/1"
TERMINAL_IDENTITY_SCHEMA = "mesh-to-cad.terminal-validation-handoff/1"
VALIDATOR_VERSION = "mesh-to-cad.workspace-validator/1"

_TERMINAL_FIELDS = {
    "schema",
    "workspace_id",
    "workspace_identity_sha256",
    "validator_version",
    "graph",
    "recovery",
    "review_facts",
    "evaluation_facts",
    "content_manifest_sha256",
    "identity_sha256",
}
_BUNDLE_FIELDS = {"schema", "result", "manifest"}
_MANIFEST_FIELDS = {
    "schema",
    "workspace_id",
    "workspace_identity_sha256",
    "files",
    "identity_sha256",
}
_MANIFEST_ENTRY_FIELDS = {"path", "sha256", "size_bytes"}
_REVIEW_FACT_FIELDS = {
    "step_count",
    "cycle_count",
    "failed_attempt_count",
    "accepted_steps",
    "heads",
    "budget",
    "final_delivery",
    "step_outcomes",
}
_STEP_OUTCOME_FIELDS = {
    "step",
    "parent_step",
    "cycle",
    "accepted",
    "no_observable_geometry_change",
    "candidate_mesh_sha256",
    "observable_sha256",
}
_EVALUATION_FACT_FIELDS = {
    "accepted_step_count",
    "has_accepted_step",
    "final_delivery_present",
    "final_delivery_accepted",
    "objective_facts",
}
_OBJECTIVE_FACT_FIELDS = {
    "global_depth_8_zero",
    "out_of_frame_clear",
    "no_evidence_conflict",
}
_MANIFEST_EXCLUDED_ROOTS = frozenset({".git", "run", "work"})


def compile_terminal_validation(workspace: Path) -> dict[str, Any]:
    """Compile one terminal evidence bundle without mutating the Workspace."""

    workspace = Path(workspace).resolve()
    before = _content_state(workspace)
    validation = validate_workspace(workspace)
    _require_terminal_state(validation.graph)
    after = _content_state(workspace)
    _require_stable_snapshot(before, after)

    workspace_document = _read_workspace_document(workspace)
    manifest = _build_manifest(workspace_document, after)
    result = _build_result(workspace, workspace_document, validation, manifest)
    _validate_manifest(manifest, workspace)
    _validate_result(result, manifest, workspace)
    _require_stable_snapshot(after, _content_state(workspace))

    bundle = {
        "schema": TERMINAL_BUNDLE_SCHEMA,
        "result": result,
        "manifest": manifest,
    }
    return {
        "bundle": bundle,
        "terminal_identity_sha256": _identity(TERMINAL_IDENTITY_SCHEMA, bundle),
    }


def verify_terminal_validation(
    workspace: Path,
    bundle: Mapping[str, Any],
    expected_identity: str | None = None,
) -> dict[str, Any]:
    """Verify a caller-supplied terminal bundle without full validation or Git."""

    if expected_identity is None:
        _fail(
            "terminal_identity_required",
            "caller must supply the expected Terminal Validation identity",
            "$.expected_terminal_identity",
        )
    _sha256(expected_identity, "$.expected_terminal_identity")
    workspace = Path(workspace).resolve()
    _closed(bundle, _BUNDLE_FIELDS, "$.terminal_bundle")
    _const(bundle["schema"], TERMINAL_BUNDLE_SCHEMA, "$.terminal_bundle.schema")
    actual_identity = _identity(TERMINAL_IDENTITY_SCHEMA, bundle)
    if actual_identity != expected_identity:
        _fail(
            "terminal_identity_mismatch",
            "terminal bundle does not match the expected identity",
            "$.expected_terminal_identity",
        )
    manifest = bundle["manifest"]
    result = bundle["result"]
    _validate_manifest(manifest, workspace)
    _validate_result(result, manifest, workspace)
    return dict(result)


def _build_manifest(
    workspace_document: Mapping[str, Any],
    inventory: tuple[tuple[str, int, str], ...],
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema": CONTENT_MANIFEST_SCHEMA,
        "workspace_id": workspace_document["workspace_id"],
        "workspace_identity_sha256": _workspace_identity(workspace_document),
        "files": [
            {"path": path, "size_bytes": size, "sha256": digest}
            for path, size, digest in inventory
        ],
    }
    manifest["identity_sha256"] = _identity(CONTENT_MANIFEST_SCHEMA, manifest)
    return manifest


def _build_result(
    workspace: Path,
    workspace_document: Mapping[str, Any],
    validation: ValidationResult,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": TERMINAL_VALIDATION_SCHEMA,
        "workspace_id": workspace_document["workspace_id"],
        "workspace_identity_sha256": _workspace_identity(workspace_document),
        "validator_version": VALIDATOR_VERSION,
        "graph": validation.graph,
        "recovery": list(validation.recovery),
        "review_facts": _review_facts(validation.graph),
        "evaluation_facts": _evaluation_facts(workspace, validation.graph),
        "content_manifest_sha256": manifest["identity_sha256"],
    }
    result["identity_sha256"] = _identity(TERMINAL_VALIDATION_SCHEMA, result)
    return result


def _require_terminal_state(graph: Mapping[str, Any]) -> None:
    if graph.get("final_delivery") is None:
        _fail(
            "terminal_state_required",
            "Terminal Validation requires a complete Final Delivery",
            "$.graph.final_delivery",
        )


def _content_state(workspace: Path) -> tuple[tuple[str, int, str], ...]:
    state: list[tuple[str, int, str]] = []
    for path in _content_files(workspace):
        relative = _safe_relative(workspace, path)
        try:
            size = path.stat().st_size
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            _inventory_error(relative)
        state.append((relative, size, digest))
    return tuple(state)


def _require_stable_snapshot(
    before: tuple[tuple[str, int, str], ...],
    after: tuple[tuple[str, int, str], ...],
) -> None:
    if before != after:
        before_paths = {entry[0] for entry in before}
        after_paths = {entry[0] for entry in after}
        changed = next(
            (left[0] for left, right in zip(before, after) if left != right),
            next(
                (item[0] for item in after if item[0] not in before_paths),
                next(
                    (item[0] for item in before if item[0] not in after_paths),
                    "Workspace content",
                ),
            ),
        )
        _fail(
            "workspace_changed_during_validation",
            "Workspace authority changed during Terminal Validation",
            f"$.{changed}",
        )


def _content_files(workspace: Path) -> list[Path]:
    try:
        is_directory = workspace.is_dir()
        is_link = workspace.is_symlink()
    except OSError:
        _inventory_error("$")
    if not is_directory or is_link:
        _fail("invalid_workspace", "Workspace directory does not exist")
    files: list[Path] = []
    try:
        children = sorted(workspace.iterdir(), key=lambda path: path.name)
    except OSError:
        _inventory_error("$")
    for child in children:
        if child.name in _MANIFEST_EXCLUDED_ROOTS:
            continue
        files.extend(_walk_content(workspace, child))
    return sorted(files, key=lambda path: path.relative_to(workspace).as_posix())


def _walk_content(workspace: Path, path: Path) -> list[Path]:
    try:
        is_link = path.is_symlink()
        is_file = path.is_file()
        is_directory = path.is_dir()
    except OSError:
        _inventory_error(_safe_relative(workspace, path))
    if is_link:
        _fail(
            "invalid_workspace_path",
            "Terminal Validation content cannot contain symlinks",
            f"$.{path.name}",
        )
    if is_file:
        return [path]
    if not is_directory:
        _fail("corrupt_workspace", "Terminal Validation content is not a file or directory")
    files: list[Path] = []
    try:
        children = sorted(path.iterdir(), key=lambda item: item.name)
    except OSError:
        _inventory_error(path.name)
    for child in children:
        files.extend(_walk_content(workspace, child))
    return files


def _safe_relative(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return "Workspace content"


def _inventory_error(relative: str) -> None:
    _fail(
        "corrupt_workspace",
        "cannot read Workspace authority content",
        f"$.{relative}" if relative != "$" else "$",
    )


def _validate_manifest(manifest: Mapping[str, Any], workspace: Path) -> None:
    _closed(manifest, _MANIFEST_FIELDS, "$.content_manifest")
    _const(manifest["schema"], CONTENT_MANIFEST_SCHEMA, "$.content_manifest.schema")
    _nonempty_string(manifest["workspace_id"], "$.content_manifest.workspace_id")
    _sha256(manifest["workspace_identity_sha256"], "$.content_manifest.workspace_identity_sha256")
    files = manifest["files"]
    if not isinstance(files, list):
        _fail("invalid_contract", "must be an array", "$.content_manifest.files")
    previous = ""
    seen: set[str] = set()
    for index, item in enumerate(files):
        path = f"$.content_manifest.files[{index}]"
        _closed(item, _MANIFEST_ENTRY_FIELDS, path)
        name = item["path"]
        if not isinstance(name, str):
            _fail("invalid_contract", "path must be a string", f"{path}.path")
        pure = PurePosixPath(name)
        if (
            not pure.parts
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or name != pure.as_posix()
            or pure.parts[0] in _MANIFEST_EXCLUDED_ROOTS
        ):
            _fail("invalid_workspace_path", "manifest path is not canonical content", f"{path}.path")
        if name in seen or name <= previous:
            _fail("invalid_contract", "manifest paths must be unique and sorted", f"{path}.path")
        seen.add(name)
        previous = name
        _sha256(item["sha256"], f"{path}.sha256")
        size = item["size_bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            _fail("invalid_contract", "size_bytes must be a non-negative integer", f"{path}.size_bytes")
    expected_identity = _identity(
        CONTENT_MANIFEST_SCHEMA,
        {key: manifest[key] for key in _MANIFEST_FIELDS if key != "identity_sha256"},
    )
    if manifest["identity_sha256"] != expected_identity:
        _fail("corrupt_workspace", "content manifest identity mismatch", "$.content_manifest.identity_sha256")
    expected_files = {
        path.relative_to(workspace).as_posix(): path for path in _content_files(workspace)
    }
    if seen != set(expected_files):
        _fail("corrupt_workspace", "content manifest file set mismatch", "$.content_manifest.files")
    for item in files:
        path = expected_files[item["path"]]
        relative = item["path"]
        try:
            actual_size = path.stat().st_size
            actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            _inventory_error(relative)
        if actual_size != item["size_bytes"] or actual_digest != item["sha256"]:
            _fail("corrupt_workspace", "content manifest file digest mismatch", "$.content_manifest.files")


def _validate_result(
    result: Mapping[str, Any], manifest: Mapping[str, Any], workspace: Path
) -> None:
    _closed(result, _TERMINAL_FIELDS, "$.terminal_validation")
    _const(result["schema"], TERMINAL_VALIDATION_SCHEMA, "$.terminal_validation.schema")
    _nonempty_string(result["workspace_id"], "$.terminal_validation.workspace_id")
    _sha256(result["workspace_identity_sha256"], "$.terminal_validation.workspace_identity_sha256")
    _const(result["validator_version"], VALIDATOR_VERSION, "$.terminal_validation.validator_version")
    graph = result["graph"]
    if not isinstance(graph, dict) or graph.get("schema") != _core.INDEX_SCHEMA:
        _fail("invalid_contract", "graph schema is unsupported", "$.terminal_validation.graph")
    _validate_graph_shape(graph)
    if not isinstance(result["recovery"], list):
        _fail("invalid_contract", "recovery must be an array", "$.terminal_validation.recovery")
    _validate_review_facts(result["review_facts"])
    _validate_evaluation_facts(result["evaluation_facts"])
    try:
        expected_review = _review_facts(graph)
        expected_evaluation = _evaluation_facts(workspace, graph)
    except (KeyError, TypeError, ValueError):
        _fail("invalid_contract", "graph facts are structurally incomplete", "$.terminal_validation.graph")
    if result["review_facts"] != expected_review:
        _fail("corrupt_workspace", "review facts are not deterministic", "$.terminal_validation.review_facts")
    if result["evaluation_facts"] != expected_evaluation:
        _fail("corrupt_workspace", "evaluation facts are not deterministic", "$.terminal_validation.evaluation_facts")
    _sha256(result["content_manifest_sha256"], "$.terminal_validation.content_manifest_sha256")
    if result["content_manifest_sha256"] != manifest["identity_sha256"]:
        _fail("corrupt_workspace", "result is bound to another content manifest", "$.terminal_validation.content_manifest_sha256")
    expected_identity = _identity(
        TERMINAL_VALIDATION_SCHEMA,
        {key: result[key] for key in _TERMINAL_FIELDS if key != "identity_sha256"},
    )
    if result["identity_sha256"] != expected_identity:
        _fail("corrupt_workspace", "Terminal Validation result identity mismatch", "$.terminal_validation.identity_sha256")
    if (
        result["workspace_id"] != manifest["workspace_id"]
        or result["workspace_identity_sha256"] != manifest["workspace_identity_sha256"]
    ):
        _fail("corrupt_workspace", "result and manifest identities conflict", "$.terminal_validation.workspace_identity_sha256")


def _validate_graph_shape(graph: Mapping[str, Any]) -> None:
    required = {
        "schema",
        "steps",
        "cycles",
        "failed_attempts",
        "accepted_steps",
        "budget",
        "heads",
        "final_delivery",
    }
    missing = required - set(graph)
    if missing:
        _fail("invalid_contract", f"graph is missing {sorted(missing)}", "$.terminal_validation.graph")
    for key in ("steps", "cycles", "failed_attempts", "accepted_steps", "heads"):
        if not isinstance(graph[key], list):
            _fail("invalid_contract", f"graph.{key} must be an array", f"$.terminal_validation.graph.{key}")
    if not isinstance(graph["budget"], dict):
        _fail("invalid_contract", "graph.budget must be an object", "$.terminal_validation.graph.budget")
    if graph["final_delivery"] is not None and not isinstance(graph["final_delivery"], dict):
        _fail("invalid_contract", "graph.final_delivery must be an object or null", "$.terminal_validation.graph.final_delivery")


def _review_facts(graph: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "step_count": len(graph["steps"]),
        "cycle_count": len(graph["cycles"]),
        "failed_attempt_count": len(graph["failed_attempts"]),
        "accepted_steps": list(graph["accepted_steps"]),
        "heads": list(graph["heads"]),
        "budget": dict(graph["budget"]),
        "final_delivery": graph["final_delivery"],
        "step_outcomes": [
            {
                "step": item["step"],
                "parent_step": item["parent_step"],
                "cycle": item["cycle"],
                "accepted": item["accepted"],
                "no_observable_geometry_change": item["no_observable_geometry_change"],
                "candidate_mesh_sha256": item["candidate_mesh_sha256"],
                "observable_sha256": item["observable_sha256"],
            }
            for item in graph["steps"]
        ],
    }


def _evaluation_facts(workspace: Path, graph: Mapping[str, Any]) -> dict[str, Any]:
    delivery = graph["final_delivery"]
    return {
        "accepted_step_count": len(graph["accepted_steps"]),
        "has_accepted_step": bool(graph["accepted_steps"]),
        "final_delivery_present": delivery is not None,
        "final_delivery_accepted": bool(delivery["accepted"]) if delivery is not None else None,
        "objective_facts": [
            {"step": item["step"], "facts": _measurement_objective_facts(workspace, item)}
            for item in graph["steps"]
        ],
    }


def _measurement_objective_facts(workspace: Path, step: Mapping[str, Any]) -> dict[str, bool]:
    path = (workspace / step["measurement"]).resolve()
    try:
        path.relative_to(workspace.resolve())
    except ValueError:
        _fail("invalid_workspace_path", "measurement path escapes the Workspace")
    measurement = _read_authority_json(workspace, path, "$.terminal_validation.measurement")
    facts = measurement.get("objective_facts")
    _closed(facts, _OBJECTIVE_FACT_FIELDS, "$.terminal_validation.objective_facts")
    if any(not isinstance(facts[key], bool) for key in _OBJECTIVE_FACT_FIELDS):
        _fail("invalid_contract", "objective facts must be boolean", "$.terminal_validation.objective_facts")
    return {key: facts[key] for key in sorted(_OBJECTIVE_FACT_FIELDS)}


def _validate_review_facts(value: Any) -> None:
    _closed(value, _REVIEW_FACT_FIELDS, "$.terminal_validation.review_facts")
    for key in ("step_count", "cycle_count", "failed_attempt_count"):
        if not isinstance(value[key], int) or isinstance(value[key], bool) or value[key] < 0:
            _fail("invalid_contract", f"{key} must be a non-negative integer", f"$.terminal_validation.review_facts.{key}")
    for key in ("accepted_steps", "heads"):
        if not isinstance(value[key], list) or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in value[key]):
            _fail("invalid_contract", f"{key} must contain non-negative integers", f"$.terminal_validation.review_facts.{key}")
    if not isinstance(value["budget"], dict) or set(value["budget"]) != {"completed_cycles", "remaining_cycles", "total_attempts", "tool_failures"}:
        _fail("invalid_contract", "budget facts are not closed", "$.terminal_validation.review_facts.budget")
    if not isinstance(value["step_outcomes"], list):
        _fail("invalid_contract", "step_outcomes must be an array", "$.terminal_validation.review_facts.step_outcomes")
    for index, item in enumerate(value["step_outcomes"]):
        path = f"$.terminal_validation.review_facts.step_outcomes[{index}]"
        _closed(item, _STEP_OUTCOME_FIELDS, path)
        for key in ("candidate_mesh_sha256", "observable_sha256"):
            _sha256(item[key], f"{path}.{key}")
        for key in ("accepted", "no_observable_geometry_change"):
            if not isinstance(item[key], bool):
                _fail("invalid_contract", f"{key} must be boolean", f"{path}.{key}")


def _validate_evaluation_facts(value: Any) -> None:
    _closed(value, _EVALUATION_FACT_FIELDS, "$.terminal_validation.evaluation_facts")
    if not isinstance(value["accepted_step_count"], int) or isinstance(value["accepted_step_count"], bool) or value["accepted_step_count"] < 0:
        _fail("invalid_contract", "accepted_step_count must be non-negative", "$.terminal_validation.evaluation_facts.accepted_step_count")
    for key in ("has_accepted_step", "final_delivery_present"):
        if not isinstance(value[key], bool):
            _fail("invalid_contract", f"{key} must be boolean", f"$.terminal_validation.evaluation_facts.{key}")
    if value["final_delivery_accepted"] is not None and not isinstance(value["final_delivery_accepted"], bool):
        _fail("invalid_contract", "final_delivery_accepted must be boolean or null", "$.terminal_validation.evaluation_facts.final_delivery_accepted")
    if not isinstance(value["objective_facts"], list):
        _fail("invalid_contract", "objective_facts must be an array", "$.terminal_validation.evaluation_facts.objective_facts")
    for index, item in enumerate(value["objective_facts"]):
        path = f"$.terminal_validation.evaluation_facts.objective_facts[{index}]"
        _closed(item, {"step", "facts"}, path)
        if not isinstance(item["step"], int) or isinstance(item["step"], bool) or item["step"] < 0:
            _fail("invalid_contract", "objective fact step must be non-negative", f"{path}.step")
        _closed(item["facts"], _OBJECTIVE_FACT_FIELDS, f"{path}.facts")
        if any(not isinstance(item["facts"][key], bool) for key in _OBJECTIVE_FACT_FIELDS):
            _fail("invalid_contract", "objective facts must be boolean", f"{path}.facts")


def _read_workspace_document(workspace: Path) -> dict[str, Any]:
    return _read_authority_json(workspace, workspace / "workspace.json", "$.workspace")


def _read_authority_json(workspace: Path, path: Path, label: str) -> dict[str, Any]:
    relative = _safe_relative(workspace, path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _inventory_error(relative)
    if not isinstance(value, dict):
        _fail("invalid_contract", "JSON artifact must contain an object", label)
    return value


def _workspace_identity(value: Mapping[str, Any]) -> str:
    return _core._identity(_core.WORKSPACE_SCHEMA, value)


_closed = _core._closed_object
_const = _core._const
_nonempty_string = _core._nonempty_string
_sha256 = _core._sha256
_identity = _core._identity


def _fail(classification: str, detail: str, path: str = "$") -> None:
    raise WorkspaceError(classification, detail, path)


__all__ = [
    "CONTENT_MANIFEST_SCHEMA",
    "DEFAULT_COMMAND_SECONDS",
    "FAILED_ATTEMPT_RESULTS",
    "TERMINAL_BUNDLE_SCHEMA",
    "TERMINAL_IDENTITY_SCHEMA",
    "TERMINAL_VALIDATION_SCHEMA",
    "VALIDATOR_VERSION",
    "WorkspaceError",
    "ValidationResult",
    "begin_attempt",
    "compile_terminal_validation",
    "finalize_workspace",
    "initialize_workspace",
    "publish_cycle",
    "publish_step_zero",
    "record_attempt",
    "recover_workspace",
    "rebuild_index",
    "run_attempt_command",
    "run_canonical_build",
    "validate_workspace",
    "verify_terminal_validation",
    "workspace_status",
]
