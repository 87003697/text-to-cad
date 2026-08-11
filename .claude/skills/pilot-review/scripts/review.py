#!/usr/bin/env python3
"""Read-only canonical Workspace graph audit for pilot-review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


DEFAULT_WORKSPACE_HELPER = "mesh-to-cad-workspace"
_VENDORED_AUTHORITY_HELPER = Path(__file__).resolve().with_name(
    "workspace_authority.py"
)
DEFAULT_AUTHORITY_HELPER = (
    str(_VENDORED_AUTHORITY_HELPER)
    if _VENDORED_AUTHORITY_HELPER.is_file()
    else "workspace-authority"
)


class ReviewError(RuntimeError):
    """The review could not read its declared evidence."""


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
    accepted = bool(delivery.get("accepted")) if isinstance(delivery, dict) else False
    provenance = {
        "workspace": "workspace.json",
        "canonical_reference": "input/input.json",
        "graph_index": "step_index.json",
        "runner": "artifact_manifest.json",
        "telemetry": "run/",
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
            "production_runtime_integration": "not_auditable",
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
        "evidence_gaps": [
            "production runtime integration requires shipped snapshot, invoked "
            "installed-skill, bundle, parity, and isolation gate evidence"
        ],
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


def _publish(workspace: Path, review: dict[str, Any]) -> None:
    json_tmp = workspace / ".review.json.tmp"
    markdown_tmp = workspace / ".review.md.tmp"
    json_tmp.write_text(
        json.dumps(review, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_tmp.write_text(_markdown(review), encoding="utf-8")
    json_tmp.replace(workspace / "review.json")
    markdown_tmp.replace(workspace / "review.md")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
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
    try:
        status, review = review_workspace(
            args.workspace,
            args.workspace_helper,
            authority_helper=args.authority_helper,
            authority_timeout_seconds=args.authority_timeout_seconds,
            authority_max_files=args.authority_max_files,
            authority_max_bytes=args.authority_max_bytes,
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
