#!/usr/bin/env python3
"""Read-only canonical Workspace graph audit for pilot-review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_WORKSPACE_HELPER = (
    REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace"
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
    helper: Path,
) -> tuple[int, dict[str, Any]]:
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(helper),
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


def _legacy_review(
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
        _node(
            nodes,
            step_id,
            "measured_step",
            f"steps/{number:06d}/step.json",
            accepted=bool(step.get("accepted")),
            parent_step=step.get("parent_step"),
        )
        if number == 0:
            _edge(edges, "workspace", step_id, "workspace_publishes_initial_step")

    failed_attempts = (
        graph.get("failed_attempts")
        if isinstance(graph.get("failed_attempts"), list)
        else []
    )
    for attempt in failed_attempts:
        attempt_number = int(attempt["attempt"])
        attempt_id = f"attempt:{attempt_number}"
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
        for attempt_number in cycle.get("attempt_ids", []):
            if any(
                node["id"] == f"attempt:{attempt_number}"
                for node in nodes
            ):
                _edge(
                    edges,
                    f"attempt:{attempt_number}",
                    cycle_id,
                    "attempt_contributes_to_cycle",
                )

    delivery = graph.get("final_delivery")
    if isinstance(delivery, dict):
        selection = _read_json(workspace / "final/selection.json")
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
            "final-delivery",
            "final_delivery",
            str(delivery.get("manifest") or "final/manifest.json"),
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
        _edge(
            edges,
            "selection",
            "final-delivery",
            "selection_publishes_delivery",
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
    runtime = "pass" if runner == "pass" and isinstance(delivery, dict) else "fail"
    accepted = bool(delivery.get("accepted")) if isinstance(delivery, dict) else False
    return {
        "verdicts": {
            "runner_completion": runner,
            "workspace_protocol": "pass",
            "reconstruction_quality": (
                "accepted" if accepted else "delivered_with_residual"
            ),
            "production_runtime_integration": runtime,
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
        "evidence_gaps": [],
    }


def review_workspace(workspace: Path, helper: Path) -> tuple[int, dict[str, Any]]:
    """Validate and reconstruct one experiment without changing its authority."""

    workspace = workspace.resolve()
    status, payload = _validate(workspace, helper.resolve())
    if status != 0 or payload.get("ok") is not True:
        review = _legacy_review(workspace, payload)
        classification = review["workspace_validation"]["classification"]
        return (2 if classification == "unsupported_legacy_workspace" else 1), review
    return 0, _canonical_review(workspace, payload)


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
        type=Path,
        default=DEFAULT_WORKSPACE_HELPER,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        status, review = review_workspace(args.workspace, args.workspace_helper)
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
