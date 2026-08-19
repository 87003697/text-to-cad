#!/usr/bin/env python3
"""Read-only canonical Workspace graph audit for pilot-review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from typing import Any


DEFAULT_WORKSPACE_HELPER = "mesh-to-cad-workspace"
DEFAULT_VALIDATION_TIMEOUT_SECONDS = 1800
MAX_VALIDATION_TIMEOUT_SECONDS = 1800
VALID_ROOT_CAUSES = {
    "agent-policy-deviation",
    "contract-gap",
    "contract-ambiguity",
    "tool-interface-failure",
    "runtime-deployment-failure",
    "observability-gap",
    "modeling-limit",
}
SEMANTIC_VERDICTS = {
    "reconstruction_quality": {
        "accepted",
        "delivered_with_residual",
        "failed_before_measurement",
        "not_auditable",
    },
    "production_runtime_integration": {"pass", "fail", "not_auditable"},
}
PROTOCOL_ASSESSMENT_STATUSES = {
    "observed",
    "partial",
    "missing",
    "not_applicable",
    "not_auditable",
}
PROTOCOL_CHECKS = (
    {
        "check_id": "canonical-reference-and-setup",
        "requirement": "Canonical reference and setup authority are present.",
    },
    {
        "check_id": "workspace-initialization",
        "requirement": "The canonical Workspace is initialized.",
    },
    {
        "check_id": "initial-attempt",
        "requirement": "An Attempt branches toward Measured Step 0.",
    },
    {
        "check_id": "formal-preview-and-measurement",
        "requirement": "The initial Attempt produces a formal preview and measurement.",
    },
    {
        "check_id": "measured-step-zero",
        "requirement": "The initial measurement publishes Measured Step 0.",
    },
    {
        "check_id": "repair-cycle-chain",
        "requirement": (
            "Each applicable repair chain records its batch, Attempt, region diff, "
            "Measured Step, and Repair Cycle within budget."
        ),
    },
    {
        "check_id": "final-selection",
        "requirement": "Final selection identifies the chosen Measured Step.",
    },
    {
        "check_id": "isolated-registered-rebuild",
        "requirement": "The selected source is rebuilt through the registered isolated path.",
    },
    {
        "check_id": "provenance-verification-and-preview",
        "requirement": (
            "Provenance validation, non-publishing verification, and final preview "
            "support delivery."
        ),
    },
    {
        "check_id": "atomic-final-delivery",
        "requirement": "Final Delivery is published atomically from verified evidence.",
    },
)


class ReviewError(RuntimeError):
    """The review could not read its declared evidence."""


class ValidatorTimeoutError(ReviewError):
    """The Workspace validator exceeded the configured review budget."""

    def __init__(self, timeout_seconds: int):
        super().__init__(
            f"Workspace validator exceeded {timeout_seconds} seconds"
        )
        self.timeout_seconds = timeout_seconds


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
    timeout_seconds: int,
) -> tuple[int, dict[str, Any]]:
    helper_text = str(helper)
    helper_path = Path(helper_text).expanduser()
    if helper_path.exists() and (helper_path.is_dir() or helper_path.suffix == ".py"):
        command = [sys.executable, str(helper_path)]
    else:
        command = [helper_text]
    argv = [
        *command,
        "validate",
        "--workspace",
        str(workspace),
    ]
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name == "posix",
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.communicate()
            raise ValidatorTimeoutError(timeout_seconds)
    except ValidatorTimeoutError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReviewError(f"Workspace validator failed to run: {exc}") from exc
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        detail = stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise ReviewError(
            f"Workspace validator returned invalid JSON{suffix}"
        ) from exc
    if not isinstance(payload, dict):
        raise ReviewError("Workspace validator returned a non-object")
    return process.returncode, payload


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
) -> dict[str, Any]:
    graph = payload.get("graph")
    if not isinstance(graph, dict):
        raise ReviewError("valid Workspace response omitted its graph")
    delivery = graph.get("final_delivery")
    runner, issues = _runner_verdict(workspace)
    accepted = bool(delivery.get("accepted")) if isinstance(delivery, dict) else False
    return {
        "verdicts": {
            "runner_completion": runner,
            "workspace_protocol": "pass",
            "reconstruction_quality": (
                "accepted" if accepted else "delivered_with_residual"
            ),
            "production_runtime_integration": "not_auditable",
        },
        "contract_provenance": {
            "workspace": "workspace.json",
            "canonical_reference": "input/input.json",
            "graph_index": "step_index.json",
            "runner": "artifact_manifest.json",
            "telemetry": "run/",
        },
        "workspace_validation": {
            "valid": True,
            "classification": "valid",
            "recovery": payload.get("recovery", []),
        },
        "graph": _canonical_graph(workspace, graph),
        "issues": issues,
        "unresolved": [],
        "evidence_gaps": [
            "production runtime integration requires shipped snapshot, invoked "
            "installed-skill, bundle, parity, and isolation gate evidence"
        ],
    }


def review_workspace(
    workspace: Path,
    helper: str | Path,
    validation_timeout_seconds: int = DEFAULT_VALIDATION_TIMEOUT_SECONDS,
) -> tuple[int, dict[str, Any]]:
    """Validate and reconstruct one experiment without changing its authority."""

    workspace = workspace.resolve()
    status, payload = _validate(
        workspace,
        helper,
        validation_timeout_seconds,
    )
    if status != 0 or payload.get("ok") is not True:
        review = _invalid_workspace_review(workspace, payload)
        classification = review["workspace_validation"]["classification"]
        return (2 if classification == "unsupported_legacy_workspace" else 1), review
    return 0, _canonical_review(workspace, payload)


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(value, indent=2, sort_keys=True) + "\n",
    )


def _atomic_write_text(path: Path, value: str) -> None:
    descriptor, temporary_text = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_text)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
        temporary.replace(path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _identity_sha256(value: dict[str, Any]) -> str:
    payload = dict(value)
    payload.pop("compiler_identity_sha256", None)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _seal_compiler_output(value: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(value)
    sealed["compiler_identity_sha256"] = _identity_sha256(sealed)
    return sealed


def _verify_compiler_output(value: dict[str, Any], path: Path) -> None:
    identity = value.get("compiler_identity_sha256")
    if not isinstance(identity, str) or identity != _identity_sha256(value):
        raise ReviewError(f"Evidence Compiler identity mismatch: {path}")


def _verify_experiment_context(
    evidence: dict[str, Any],
    workspace: Path,
    group: Path | None,
) -> None:
    expected_group = group.name if group is not None else None
    if evidence.get("experiment") != workspace.name:
        raise ReviewError(
            "review input experiment does not match its directory: "
            f"{workspace / 'review-input.json'}"
        )
    if evidence.get("group") != expected_group:
        raise ReviewError(
            "review input group does not match its directory context: "
            f"{workspace / 'review-input.json'}"
        )
    source = evidence.get("source")
    expected_source = {
        "workspace": str(workspace.resolve()),
        "group": str(group.resolve()) if group is not None else None,
    }
    if source != expected_source:
        raise ReviewError(
            "review input source does not match the requested evidence target"
        )


def _group_records(
    group_input: dict[str, Any],
    group: Path,
    experiments: list[Path],
) -> dict[str, dict[str, Any]]:
    if group_input.get("schema") != "pilot-review.group-evidence/2":
        raise ReviewError("group review input must use pilot-review.group-evidence/2")
    if group_input.get("group") != group.name:
        raise ReviewError("group review input does not match its directory")
    if group_input.get("source_group") != str(group.resolve()):
        raise ReviewError(
            "group review input source does not match the requested evidence target"
        )
    raw_records = group_input.get("experiments")
    if not isinstance(raw_records, list):
        raise ReviewError("group review input experiments must be a list")
    records: dict[str, dict[str, Any]] = {}
    for item in raw_records:
        if not isinstance(item, dict) or not isinstance(item.get("experiment"), str):
            raise ReviewError("group review input contains an invalid experiment record")
        name = item["experiment"]
        if name in records:
            raise ReviewError(f"duplicate group experiment record: {name}")
        records[name] = item
    discovered = {workspace.name for workspace in experiments}
    if set(records) != discovered:
        raise ReviewError(
            "group review-input coverage does not match discovered experiments"
        )
    return records


def _is_experiment(path: Path) -> bool:
    file_markers = any(
        (path / name).is_file()
        for name in ("workspace.json", "experiment.json", "artifact_manifest.json")
    )
    directory_markers = any(
        (path / name).is_dir()
        for name in ("run", "input", "attempts", "steps", "cycles", "final")
    )
    return file_markers or directory_markers


def _discover_target(target: Path) -> tuple[Path | None, list[Path]]:
    target = target.resolve()
    if _is_experiment(target):
        return None, [target]
    if not target.is_dir():
        raise ReviewError(f"review target is not a directory: {target}")
    # Canonical output groups define every non-snapshot child directory as an
    # experiment. Do not require success markers here: a runner can fail before
    # workspace.json or artifact_manifest.json is published and that failure
    # still needs an explicit review record.
    experiments = sorted(
        child.resolve()
        for child in target.iterdir()
        if child.name != "_snapshot" and child.is_dir()
    )
    if not experiments:
        raise ReviewError(f"group contains no reviewable experiments: {target}")
    return target, experiments


def _review_paths(
    group: Path | None,
    experiments: list[Path],
    review_root: Path | None,
) -> tuple[Path, dict[Path, Path]]:
    """Map immutable evidence sources to their writable review destinations."""

    if review_root is None:
        root = group if group is not None else experiments[0]
    else:
        root = review_root.expanduser().resolve()
        source_root = group if group is not None else experiments[0]
        if root == source_root or root.is_relative_to(
            source_root
        ) or source_root.is_relative_to(root):
            raise ReviewError(
                "external review root must not overlap the evidence target"
            )
        root.mkdir(parents=True, exist_ok=True)
    destinations: dict[Path, Path] = {}
    candidates = {
        workspace: (root / workspace.name if group is not None else root)
        for workspace in experiments
    }
    if review_root is not None:
        for candidate in candidates.values():
            if candidate.is_symlink():
                raise ReviewError(
                    f"review destination must not be a symlink: {candidate}"
                )
            if candidate.exists() and not candidate.is_dir():
                raise ReviewError(
                    f"review destination must be a directory: {candidate}"
                )
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root):
                raise ReviewError(
                    f"review destination escapes the review root: {candidate}"
                )
    for workspace in experiments:
        destination = candidates[workspace]
        destination.mkdir(parents=True, exist_ok=True)
        destinations[workspace] = destination.resolve()
    return root, destinations


def _read_bounded_text(path: Path, limit: int = 4096) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n...[truncated by Evidence Compiler]...\n" + text[-half:]


def _command_records(workspace: Path) -> list[dict[str, Any]]:
    paths: set[Path] = set()
    for pattern in (
        "attempts/*/commands/*/command.json",
        "steps/*/commands/*/command.json",
        "cycles/*/commands/*/command.json",
    ):
        paths.update(workspace.glob(pattern))
    records: list[dict[str, Any]] = []
    for path in sorted(paths):
        command = _read_json(path)
        stderr = command.get("stderr")
        stderr_path: Path | None = None
        if isinstance(stderr, dict) and isinstance(stderr.get("path"), str):
            declared = Path(stderr["path"])
            command_scope = path.parents[2].resolve()
            if declared.is_absolute():
                raise ReviewError(
                    f"command stderr path must be relative: {path}"
                )
            candidate = (command_scope / declared).resolve()
            try:
                candidate.relative_to(command_scope)
            except ValueError as exc:
                raise ReviewError(
                    f"command stderr path escapes its command scope: {path}"
                ) from exc
            if candidate.is_file():
                stderr_path = candidate
        records.append(
            {
                "evidence": path.relative_to(workspace).as_posix(),
                "phase": command.get("phase"),
                "argv": command.get("argv", []),
                "duration_ms": command.get("duration_ms"),
                "exit_code": command.get("exit_code"),
                "timed_out": command.get("timed_out"),
                "stderr": (
                    {
                        "path": stderr_path.relative_to(workspace).as_posix(),
                        "preview": _read_bounded_text(stderr_path),
                    }
                    if stderr_path is not None
                    else None
                ),
            }
        )
    return records


def _artifact_summary(workspace: Path) -> dict[str, Any]:
    path = workspace / "artifact_manifest.json"
    if not path.is_file():
        return {"path": "artifact_manifest.json", "present": False}
    manifest = _read_json(path)
    return {
        "path": "artifact_manifest.json",
        "present": True,
        "final_status": manifest.get("final_status"),
        "workload_status": manifest.get("workload_status"),
    }


def _execution_evidence(
    workspace: Path,
) -> tuple[dict[str, Any], list[str]]:
    """Compile execution evidence while containing malformed sub-records."""

    errors: list[str] = []
    try:
        artifact = _artifact_summary(workspace)
    except ReviewError as exc:
        errors.append(str(exc))
        artifact = {
            "path": "artifact_manifest.json",
            "present": (workspace / "artifact_manifest.json").is_file(),
            "error": str(exc),
        }
    try:
        commands = _command_records(workspace)
    except ReviewError as exc:
        errors.append(str(exc))
        commands = []
    return (
        {
            "artifact_manifest": artifact,
            "files": _presence_index(workspace),
            "commands": commands,
            "rollout": (
                {"path": "run/rollout.jsonl"}
                if (workspace / "run/rollout.jsonl").is_file()
                else None
            ),
        },
        errors,
    )


def _presence_index(workspace: Path) -> dict[str, bool]:
    return {
        path: (workspace / path).is_file()
        for path in (
            "workspace.json",
            "experiment.json",
            "input/input.json",
            "step_index.json",
            "notes.md",
            "final/manifest.json",
            "run/rollout.jsonl",
            "run/stderr.log",
            "run/traces.sqlite3",
        )
    }


def _snapshot_head(group: Path | None, workspace: Path) -> str | None:
    root = group if group is not None else workspace.parent
    path = root / "_snapshot/HEAD.sha"
    if not path.is_file():
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _timeout_review(workspace: Path, timeout_seconds: int) -> dict[str, Any]:
    runner, issues = _runner_verdict(workspace)
    issues.append(
        {
            "classification": "observability-gap",
            "detail": (
                "Workspace authority was not classified because its validator "
                f"exceeded the {timeout_seconds}-second review budget"
            ),
            "evidence": "workspace.json",
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
            "workspace": "workspace.json",
            "runner": "artifact_manifest.json",
        },
        "workspace_validation": {
            "valid": None,
            "classification": "validator_timeout",
            "timeout_seconds": timeout_seconds,
        },
        "graph": {"nodes": [], "edges": []},
        "issues": issues,
        "unresolved": ["Workspace validation did not reach a terminal result"],
        "evidence_gaps": ["canonical Workspace graph unavailable"],
    }


def _compiler_error_review(workspace: Path, detail: str) -> dict[str, Any]:
    runner, issues = _runner_verdict(workspace)
    issues.append(
        {
            "classification": "observability-gap",
            "detail": f"Evidence Compiler could not classify Workspace authority: {detail}",
            "evidence": "workspace.json",
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
            "workspace": "workspace.json",
            "runner": "artifact_manifest.json",
        },
        "workspace_validation": {
            "valid": None,
            "classification": "compiler_failure",
            "detail": detail,
        },
        "graph": {"nodes": [], "edges": []},
        "issues": issues,
        "unresolved": ["Workspace validation did not produce readable evidence"],
        "evidence_gaps": ["canonical Workspace graph unavailable"],
    }


def _prepare_experiment(
    workspace: Path,
    group: Path | None,
    review_destination: Path,
    helper: str | Path,
    timeout_seconds: int,
) -> tuple[int, dict[str, Any]]:
    try:
        status, baseline = review_workspace(
            workspace,
            helper,
            validation_timeout_seconds=timeout_seconds,
        )
    except ValidatorTimeoutError:
        status = 1
        baseline = _timeout_review(workspace, timeout_seconds)
    except ReviewError as exc:
        status = 1
        baseline = _compiler_error_review(workspace, str(exc))
    execution, execution_errors = _execution_evidence(workspace)
    if execution_errors:
        status = 1
        detail = "; ".join(execution_errors)
        baseline = _compiler_error_review(workspace, detail)
        execution["compiler_errors"] = execution_errors
    evidence = _seal_compiler_output({
        "schema": "pilot-review.evidence/2",
        "experiment": workspace.name,
        "group": group.name if group is not None else None,
        "source": {
            "workspace": str(workspace.resolve()),
            "group": str(group.resolve()) if group is not None else None,
        },
        "compiler_status": {
            "status": status,
            "classification": baseline["workspace_validation"]["classification"],
        },
        "snapshot_head": _snapshot_head(group, workspace),
        "protocol_checks": list(PROTOCOL_CHECKS),
        "baseline": baseline,
        "execution": execution,
    })
    _atomic_write_json(review_destination / "review-input.json", evidence)
    return status, evidence


def prepare_target(
    target: Path,
    helper: str | Path,
    timeout_seconds: int,
    review_root: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """Compile deterministic review evidence for one experiment or a group."""

    if timeout_seconds <= 0 or timeout_seconds > MAX_VALIDATION_TIMEOUT_SECONDS:
        raise ReviewError(
            "validation timeout must be between 1 and "
            f"{MAX_VALIDATION_TIMEOUT_SECONDS} seconds"
        )
    group, experiments = _discover_target(target)
    output_root, destinations = _review_paths(group, experiments, review_root)
    results: list[dict[str, Any]] = []
    status = 0
    for workspace in experiments:
        experiment_status, evidence = _prepare_experiment(
            workspace,
            group,
            destinations[workspace],
            helper,
            timeout_seconds,
        )
        status = max(status, experiment_status)
        results.append(
            {
                "experiment": workspace.name,
                "path": workspace.name if group is not None else ".",
                "status": experiment_status,
                "classification": evidence["compiler_status"]["classification"],
                "review_input": (
                    f"{workspace.name}/review-input.json"
                    if group is not None
                    else "review-input.json"
                ),
                "compiler_identity_sha256": evidence[
                    "compiler_identity_sha256"
                ],
            }
        )
    summary = _seal_compiler_output({
        "schema": "pilot-review.group-evidence/2",
        "group": group.name if group is not None else None,
        "source_group": str(group.resolve()) if group is not None else None,
        "snapshot_head": _snapshot_head(group, experiments[0]),
        "experiments": results,
    })
    if group is not None:
        _atomic_write_json(output_root / "review-input.json", summary)
    return status, summary


def _require_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ReviewError(f"{field} must be a list of non-empty strings")
    return value


def _validate_evidence_reference(
    reference: Any,
    workspace: Path,
    group: Path,
) -> dict[str, str]:
    if not isinstance(reference, dict):
        raise ReviewError("evidence entries must be objects")
    scope = reference.get("scope", "experiment")
    if not isinstance(scope, str) or scope not in {"experiment", "group"}:
        raise ReviewError("evidence scope must be experiment or group")
    path_text = reference.get("path")
    if not isinstance(path_text, str) or not path_text:
        raise ReviewError("evidence path must be a non-empty string")
    relative = Path(path_text)
    if relative.is_absolute():
        raise ReviewError(f"evidence path must be relative: {path_text}")
    root = workspace if scope == "experiment" else group
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise ReviewError(f"evidence path is missing or escapes its scope: {path_text}")
    normalized = {"scope": scope, "path": relative.as_posix()}
    selector = reference.get("selector")
    if selector is not None:
        if not isinstance(selector, str) or not selector:
            raise ReviewError("evidence selector must be a non-empty string")
        normalized["selector"] = selector
    return normalized


def _validate_issue(
    value: Any,
    workspace: Path,
    group: Path,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewError("issues must be objects")
    classification = value.get("classification")
    if classification not in VALID_ROOT_CAUSES:
        raise ReviewError(f"invalid root-cause classification: {classification}")
    detail = value.get("detail")
    fix_target = value.get("fix_target")
    if not isinstance(detail, str) or not detail.strip():
        raise ReviewError("issue detail must be a non-empty string")
    if not isinstance(fix_target, str) or not fix_target.strip():
        raise ReviewError("issue fix_target must be a non-empty string")
    evidence = value.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ReviewError("each issue must cite at least one evidence entry")
    result = {
        "classification": classification,
        "detail": detail,
        "fix_target": fix_target,
        "evidence": [
            _validate_evidence_reference(item, workspace, group)
            for item in evidence
        ],
    }
    for field in (
        "last_good_node",
        "first_failing_node",
        "missing_evidence",
        "cheapest_next_experiment",
    ):
        item = value.get(field)
        if item is not None:
            if not isinstance(item, str) or not item.strip():
                raise ReviewError(f"issue {field} must be a non-empty string")
            result[field] = item
    return result


def _validate_experiment_draft(
    draft: dict[str, Any],
    workspace: Path,
    group: Path,
    protocol_checks: Any,
) -> dict[str, Any]:
    if draft.get("schema") != "pilot-review.draft/2":
        raise ReviewError("review draft must use pilot-review.draft/2")
    semantic = draft.get("semantic_verdicts")
    if not isinstance(semantic, dict) or set(semantic) != set(SEMANTIC_VERDICTS):
        raise ReviewError(
            "semantic_verdicts must contain reconstruction_quality and "
            "production_runtime_integration"
        )
    for name, allowed in SEMANTIC_VERDICTS.items():
        if semantic[name] not in allowed:
            raise ReviewError(f"invalid {name} verdict: {semantic[name]}")
    issues = draft.get("issues")
    if not isinstance(issues, list):
        raise ReviewError("issues must be a list")
    if not isinstance(protocol_checks, list) or not protocol_checks:
        raise ReviewError("review input omitted protocol_checks")
    required_check_ids: list[str] = []
    for check in protocol_checks:
        if not isinstance(check, dict):
            raise ReviewError("protocol_checks entries must be objects")
        check_id = check.get("check_id")
        requirement = check.get("requirement")
        if not isinstance(check_id, str) or not check_id.strip():
            raise ReviewError("protocol check_id must be a non-empty string")
        if not isinstance(requirement, str) or not requirement.strip():
            raise ReviewError("protocol requirement must be a non-empty string")
        required_check_ids.append(check_id)
    if len(required_check_ids) != len(set(required_check_ids)):
        raise ReviewError("review input contains duplicate protocol check_ids")
    assessments = draft.get("protocol_assessments")
    if not isinstance(assessments, list):
        raise ReviewError("protocol_assessments must be a list")
    normalized_assessments: list[dict[str, Any]] = []
    seen_check_ids: list[str] = []
    for assessment in assessments:
        if not isinstance(assessment, dict):
            raise ReviewError("protocol assessments must be objects")
        check_id = assessment.get("check_id")
        if not isinstance(check_id, str) or not check_id.strip():
            raise ReviewError("protocol assessment check_id must be non-empty")
        status = assessment.get("status")
        if not isinstance(status, str) or status not in PROTOCOL_ASSESSMENT_STATUSES:
            raise ReviewError(f"invalid protocol assessment status: {status}")
        rationale = assessment.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ReviewError("protocol assessment rationale must be non-empty")
        raw_evidence = assessment.get("evidence")
        if not isinstance(raw_evidence, list):
            raise ReviewError("protocol assessment evidence must be a list")
        if status in {"observed", "partial", "not_applicable"} and not raw_evidence:
            raise ReviewError(f"{status} protocol assessment requires evidence")
        missing_evidence = assessment.get("missing_evidence")
        if status in {"missing", "not_auditable"}:
            if not isinstance(missing_evidence, str) or not missing_evidence.strip():
                raise ReviewError(
                    f"{status} protocol assessment requires missing_evidence"
                )
        elif missing_evidence is not None and (
            not isinstance(missing_evidence, str) or not missing_evidence.strip()
        ):
            raise ReviewError("protocol assessment missing_evidence must be non-empty")
        normalized = {
            "check_id": check_id,
            "status": status,
            "rationale": rationale,
            "evidence": [
                _validate_evidence_reference(item, workspace, group)
                for item in raw_evidence
            ],
        }
        if missing_evidence is not None:
            normalized["missing_evidence"] = missing_evidence
        normalized_assessments.append(normalized)
        seen_check_ids.append(check_id)
    if len(seen_check_ids) != len(set(seen_check_ids)):
        raise ReviewError("protocol_assessments contains duplicate check_ids")
    missing = sorted(set(required_check_ids) - set(seen_check_ids))
    unknown = sorted(set(seen_check_ids) - set(required_check_ids))
    if missing or unknown:
        raise ReviewError(
            "protocol_assessments must exactly cover protocol_checks: "
            f"missing={missing}, unknown={unknown}"
        )
    assessments_by_id = {
        assessment["check_id"]: assessment for assessment in normalized_assessments
    }
    return {
        "semantic_verdicts": semantic,
        "protocol_assessments": [
            assessments_by_id[check_id] for check_id in required_check_ids
        ],
        "issues": [
            _validate_issue(issue, workspace, group) for issue in issues
        ],
        "unresolved": _require_string_list(draft.get("unresolved", []), "unresolved"),
        "evidence_gaps": _require_string_list(
            draft.get("evidence_gaps", []), "evidence_gaps"
        ),
        "fix_playbook": _require_string_list(
            draft.get("fix_playbook", []), "fix_playbook"
        ),
    }


def _final_review(evidence: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    baseline = evidence.get("baseline")
    if not isinstance(baseline, dict):
        raise ReviewError("review-input.json omitted its baseline")
    verdicts = dict(baseline["verdicts"])
    verdicts.update(draft["semantic_verdicts"])
    return {
        "verdicts": verdicts,
        "contract_provenance": baseline["contract_provenance"],
        "workspace_validation": baseline["workspace_validation"],
        "graph": baseline["graph"],
        "protocol_assessments": draft["protocol_assessments"],
        "issues": [*baseline.get("issues", []), *draft["issues"]],
        "unresolved": draft["unresolved"],
        "evidence_gaps": draft["evidence_gaps"],
        "fix_playbook": draft["fix_playbook"],
    }


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
            "## Contract provenance",
            "",
        ]
    )
    for name, value in review["contract_provenance"].items():
        lines.append(f"- {name}: `{value}`")
    assessments = review.get("protocol_assessments", [])
    if assessments:
        lines.extend(["", "## Protocol assessment", ""])
        for assessment in assessments:
            lines.append(
                f"- `{assessment['check_id']}`: `{assessment['status']}` — "
                f"{assessment['rationale']}"
            )
            if assessment.get("missing_evidence"):
                lines.append(
                    f"  - missing evidence: {assessment['missing_evidence']}"
                )
    lines.extend(
        [
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
            evidence = issue.get("evidence")
            if isinstance(evidence, list):
                rendered = ", ".join(
                    f"{item.get('scope', 'experiment')}:{item.get('path')}"
                    + (
                        f"#{item['selector']}"
                        if isinstance(item.get("selector"), str)
                        else ""
                    )
                    for item in evidence
                )
            else:
                rendered = str(evidence)
            lines.append(f"- `{issue['classification']}`: {issue['detail']} ({rendered})")
            if issue.get("fix_target"):
                lines.append(f"  - fix target: `{issue['fix_target']}`")
    else:
        lines.append("- none")
    for heading, key in (
        ("Unresolved", "unresolved"),
        ("Evidence gaps", "evidence_gaps"),
        ("Ordered fix playbook", "fix_playbook"),
    ):
        lines.extend(["", f"## {heading}", ""])
        values = review.get(key, [])
        if values:
            for index, value in enumerate(values, start=1):
                marker = f"{index}." if key == "fix_playbook" else "-"
                lines.append(f"{marker} {value}")
        else:
            lines.append("- none")
    return "\n".join(lines) + "\n"


def _publish(workspace: Path, review: dict[str, Any]) -> None:
    _atomic_write_text(
        workspace / "review.json",
        json.dumps(review, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write_text(workspace / "review.md", _markdown(review))


def _validate_group_draft(
    draft: dict[str, Any],
    group: Path,
) -> dict[str, Any]:
    if draft.get("schema") != "pilot-review.group-draft/1":
        raise ReviewError("group draft must use pilot-review.group-draft/1")
    summary = draft.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ReviewError("group draft summary must be a non-empty string")
    findings = draft.get("cross_experiment_findings", [])
    if not isinstance(findings, list):
        raise ReviewError("cross_experiment_findings must be a list")
    return {
        "summary": summary,
        "cross_experiment_findings": [
            _validate_issue(finding, group, group) for finding in findings
        ],
        "fix_playbook": _require_string_list(
            draft.get("fix_playbook", []), "group fix_playbook"
        ),
    }


def _group_markdown(
    group: Path,
    reviews: list[tuple[Path, dict[str, Any]]],
    draft: dict[str, Any],
) -> str:
    lines = [
        "# Pilot review summary",
        "",
        draft["summary"],
        "",
        "## Experiment verdicts",
        "",
        "| Experiment | Runner | Workspace | Reconstruction | Production runtime |",
        "|---|---|---|---|---|",
    ]
    for workspace, review in reviews:
        verdicts = review["verdicts"]
        lines.append(
            f"| {workspace.name} | {verdicts['runner_completion']} | "
            f"{verdicts['workspace_protocol']} | "
            f"{verdicts['reconstruction_quality']} | "
            f"{verdicts['production_runtime_integration']} |"
        )
    lines.extend(["", "## Cross-experiment findings", ""])
    findings = draft["cross_experiment_findings"]
    if findings:
        for finding in findings:
            evidence = ", ".join(
                f"{item['scope']}:{item['path']}"
                + (f"#{item['selector']}" if item.get("selector") else "")
                for item in finding["evidence"]
            )
            lines.append(
                f"- `{finding['classification']}`: {finding['detail']} ({evidence})"
            )
            lines.append(f"  - fix target: `{finding['fix_target']}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Ordered fix playbook", ""])
    if draft["fix_playbook"]:
        for index, item in enumerate(draft["fix_playbook"], start=1):
            lines.append(f"{index}. {item}")
    else:
        lines.append("- none")
    lines.extend(["", "## Reports", ""])
    for workspace, _review in reviews:
        relative = workspace.relative_to(group).as_posix()
        lines.append(f"- `{relative}/review.md`")
        lines.append(f"- `{relative}/review.json`")
    return "\n".join(lines) + "\n"


def publish_target(
    target: Path,
    review_root: Path | None = None,
) -> dict[str, Any]:
    """Validate Review Agent drafts and publish final review artifacts."""

    group, experiments = _discover_target(target)
    output_root, destinations = _review_paths(group, experiments, review_root)
    group_root = group if group is not None else experiments[0]
    records: dict[str, dict[str, Any]] | None = None
    if group is not None:
        group_input = _read_json(output_root / "review-input.json")
        _verify_compiler_output(group_input, output_root / "review-input.json")
        records = _group_records(group_input, group, experiments)

    pending: list[tuple[Path, Path, dict[str, Any]]] = []
    for workspace in experiments:
        review_workspace = destinations[workspace]
        evidence = _read_json(review_workspace / "review-input.json")
        _verify_compiler_output(
            evidence,
            review_workspace / "review-input.json",
        )
        if evidence.get("schema") != "pilot-review.evidence/2":
            raise ReviewError(
                "unsupported review input schema in "
                f"{review_workspace / 'review-input.json'}"
            )
        _verify_experiment_context(evidence, workspace, group)
        if records is not None:
            record = records[workspace.name]
            expected_path = workspace.relative_to(group).as_posix()
            expected_input = f"{expected_path}/review-input.json"
            if record.get("path") != expected_path:
                raise ReviewError(
                    f"group record path mismatch for experiment {workspace.name}"
                )
            if record.get("review_input") != expected_input:
                raise ReviewError(
                    f"group review input path mismatch for experiment {workspace.name}"
                )
            if record.get("compiler_identity_sha256") != evidence.get(
                "compiler_identity_sha256"
            ):
                raise ReviewError(
                    f"group compiler identity mismatch for experiment {workspace.name}"
                )
        draft = _validate_experiment_draft(
            _read_json(review_workspace / "review-draft.json"),
            workspace,
            group_root,
            evidence.get("protocol_checks"),
        )
        pending.append(
            (workspace, review_workspace, _final_review(evidence, draft))
        )

    group_draft: dict[str, Any] | None = None
    if group is not None:
        group_draft = _validate_group_draft(
            _read_json(output_root / "review-summary-draft.json"),
            group,
        )

    for _workspace, review_workspace, review in pending:
        _publish(review_workspace, review)
    if group is not None and group_draft is not None:
        _atomic_write_text(
            output_root / "review-summary.md",
            _group_markdown(
                group,
                [
                    (workspace, review)
                    for workspace, _review_workspace, review in pending
                ],
                group_draft,
            ),
        )
    return {
        "experiments": [workspace.name for workspace, _output, _review in pending],
        "review_root": str(output_root),
        "group_summary": (
            str(output_root / "review-summary.md")
            if group is not None
            else None
        ),
    }


def _legacy_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument(
        "--workspace-helper",
        default=DEFAULT_WORKSPACE_HELPER,
    )
    parser.add_argument(
        "--validation-timeout-seconds",
        type=int,
        default=DEFAULT_VALIDATION_TIMEOUT_SECONDS,
    )
    return parser


def _workflow_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser(
        "prepare",
        help="compile deterministic evidence for a local Review Agent",
    )
    prepare.add_argument("target", type=Path)
    prepare.add_argument("--workspace-helper", default=DEFAULT_WORKSPACE_HELPER)
    prepare.add_argument(
        "--review-root",
        type=Path,
        help="write review artifacts outside the immutable evidence target",
    )
    prepare.add_argument(
        "--validation-timeout-seconds",
        type=int,
        default=DEFAULT_VALIDATION_TIMEOUT_SECONDS,
    )
    publish = subparsers.add_parser(
        "publish",
        help="validate Review Agent drafts and publish final reports",
    )
    publish.add_argument("target", type=Path)
    publish.add_argument(
        "--review-root",
        type=Path,
        help="read drafts and publish reports outside the evidence target",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in {"prepare", "publish"}:
        args = _workflow_parser().parse_args(raw)
        try:
            if args.command == "prepare":
                status, summary = prepare_target(
                    args.target,
                    args.workspace_helper,
                    args.validation_timeout_seconds,
                    args.review_root,
                )
                print(
                    json.dumps(
                        {
                            "ok": status == 0,
                            "status": status,
                            "review_root": str(
                                (
                                    args.review_root
                                    if args.review_root is not None
                                    else args.target
                                )
                                .expanduser()
                                .resolve()
                            ),
                            **summary,
                        },
                        separators=(",", ":"),
                    )
                )
                return status
            published = publish_target(args.target, args.review_root)
            print(
                json.dumps(
                    {"ok": True, **published},
                    separators=(",", ":"),
                )
            )
            return 0
        except (OSError, ReviewError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1

    args = _legacy_parser().parse_args(raw)
    try:
        if (
            args.validation_timeout_seconds <= 0
            or args.validation_timeout_seconds > MAX_VALIDATION_TIMEOUT_SECONDS
        ):
            raise ReviewError(
                "validation timeout must be between 1 and "
                f"{MAX_VALIDATION_TIMEOUT_SECONDS} seconds"
            )
        status, review = review_workspace(
            args.workspace,
            args.workspace_helper,
            args.validation_timeout_seconds,
        )
        _publish(args.workspace.resolve(), review)
    except (OSError, ReviewError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "ok": status == 0,
                "status": status,
                "classification": review["workspace_validation"]["classification"],
                "review_json": str(args.workspace / "review.json"),
                "review_markdown": str(args.workspace / "review.md"),
            },
            separators=(",", ":"),
        )
    )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
