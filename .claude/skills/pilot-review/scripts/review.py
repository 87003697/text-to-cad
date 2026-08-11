#!/usr/bin/env python3
"""Read-only canonical Workspace graph audit for pilot-review."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
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


def _runtime_authority_verdict(
    workspace: Path,
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
        manifest = _read_json(workspace / "artifact_manifest.json")
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
        if (
            set(proof)
            != {
                "schema",
                "job",
                "scenario",
                "execution_profile",
                "sandbox",
                "provider_environment",
                "requests",
            }
            or proof.get("schema") != "cvm.provider-free-execution/1"
            or proof.get("scenario")
            != {
                "name": "issue15-runtime-authority",
                "identity": "issue15.provider-free.runtime-authority/1",
            }
            or proof.get("execution_profile", {}).get("schema")
            != "cvm.provider-free-execution-profile/1"
            or proof.get("execution_profile", {}).get("id")
            != "issue15.provider-free-bounded/1"
            or proof.get("execution_profile", {}).get("provider_access") != "forbidden"
            or proof.get("sandbox")
            != {
                "network": "isolated-loopback",
                "resource_profile": "issue15.provider-free-bounded/1",
            }
            or proof.get("provider_environment", {}).get("credential_values_recorded")
            is not False
            or proof.get("requests") != {"model_gateway": 0, "provider": 0, "tap": 0}
        ):
            raise ReviewError("provider-free execution proof is incomplete")
        command_path = receipt["commands"]
        if command_path != "run/provider-free-commands.jsonl":
            raise ReviewError("public-command receipt path conflicts")
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
    except (OSError, TypeError, ReviewError) as exc:
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
        _runtime_authority_verdict(workspace)
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
