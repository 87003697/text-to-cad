#!/usr/bin/env python3
"""Read-only pilot review compiler.

Review consumes the runner-owned external Terminal Validation handoff and
verifies it once with W1. It never interprets Workspace Authority directly.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any


TERMINAL_LOCATOR_SCHEMA = "mesh-to-cad.terminal-validation-locator/2"
TERMINAL_HANDOFF_LAYOUT = "external-sibling-namespace/1"
TERMINAL_LOCATOR_RELATIVE = "run/terminal-validation-locator.json"
TERMINAL_HANDOFF_SCHEMA = "mesh-to-cad.terminal-validation-handoff/1"
TERMINAL_HANDOFF_NAMESPACE = ".internal-terminal-validation"
TERMINAL_HANDOFF_FILENAME = "terminal-validation.json"
MAX_TERMINAL_INPUT_BYTES = 32 * 1024 * 1024
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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewError(f"expected JSON object: {path}")
    return value


def _reject_symlink_components(path: Path, label: str) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise ReviewError(f"{label} contains a symlink")
        if current.parent == current:
            return
        current = current.parent


def _load_workspace_verifier(helper: str | Path | None) -> Any:
    """Load the W1 facade from the source tree or an explicit helper directory."""

    candidates: list[Path] = []
    if helper is not None:
        candidate = Path(str(helper)).expanduser()
        if candidate.is_dir():
            candidates.append(candidate / "workspace.py")
        elif candidate.name == "workspace.py":
            candidates.append(candidate)
    candidates.append(
        Path(__file__).resolve().parent.parent
        / "mesh-to-cad-workspace"
        / "workspace.py"
    )
    facade_path = next(
        (path.resolve() for path in candidates if path.is_file() and not path.is_symlink()),
        None,
    )
    if facade_path is None:
        raise ReviewError("W1 Workspace facade is unavailable for terminal verification")
    module_name = "_mesh_to_cad_workspace_for_review"
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        return loaded
    helper_root = str(facade_path.parent)
    if helper_root not in sys.path:
        sys.path.insert(0, helper_root)
    spec = importlib.util.spec_from_file_location(module_name, facade_path)
    if spec is None or spec.loader is None:
        raise ReviewError("W1 Workspace facade cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise ReviewError("W1 Workspace facade failed to load") from exc
    return module


def _read_fd_json(descriptor: int, label: str) -> dict[str, Any]:
    """Read a bounded regular file using only the already-open descriptor."""

    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size < 0
            or metadata.st_size > MAX_TERMINAL_INPUT_BYTES
        ):
            raise ReviewError(f"{label} is not a private regular file")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise ReviewError(f"{label} ended before its declared size")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ReviewError(f"{label} grew during read")
    except ReviewError:
        raise
    except OSError as exc:
        raise ReviewError(f"cannot read {label}") from exc
    try:
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ReviewError(f"{label} must be a JSON object")
    return value


def _read_locator_descriptor(workspace: Path) -> dict[str, Any]:
    """Open Workspace/run/locator through directory descriptors without races."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_DIRECTORY", 0)
    )
    root_fd: int | None = None
    run_fd: int | None = None
    try:
        root_fd = os.open(workspace, flags)
        root_metadata = os.fstat(root_fd)
        if not stat.S_ISDIR(root_metadata.st_mode):
            raise ReviewError("Workspace root is not a directory")
        run_fd = os.open("run", flags, dir_fd=root_fd)
        run_metadata = os.fstat(run_fd)
        if not stat.S_ISDIR(run_metadata.st_mode):
            raise ReviewError("Workspace run directory is unavailable")
        locator_fd = os.open(
            "terminal-validation-locator.json",
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=run_fd,
        )
        try:
            return _read_fd_json(locator_fd, "terminal locator")
        finally:
            os.close(locator_fd)
    except ReviewError:
        raise
    except (OSError, TypeError) as exc:
        raise ReviewError("terminal locator is unavailable or unsafe") from exc
    finally:
        if run_fd is not None:
            os.close(run_fd)
        if root_fd is not None:
            os.close(root_fd)


def _read_handoff_descriptor(workspace: Path) -> dict[str, Any]:
    """Open the external runner-owned handoff via descriptor-relative traversal.

    The handoff lives at the fixed sibling namespace
    ``<workspace.parent>/.internal-terminal-validation/<workspace.name>/terminal-validation.json``.
    Every hop uses O_DIRECTORY/O_NOFOLLOW to reject symlink swaps.
    """

    exp_name = workspace.name
    parent = workspace.parent
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_DIRECTORY", 0)
    )
    parent_fd: int | None = None
    namespace_fd: int | None = None
    exp_fd: int | None = None
    try:
        parent_fd = os.open(parent, flags)
        parent_metadata = os.fstat(parent_fd)
        if not stat.S_ISDIR(parent_metadata.st_mode):
            raise ReviewError("Workspace parent is not a directory")
        namespace_fd = os.open(TERMINAL_HANDOFF_NAMESPACE, flags, dir_fd=parent_fd)
        namespace_metadata = os.fstat(namespace_fd)
        if not stat.S_ISDIR(namespace_metadata.st_mode):
            raise ReviewError("terminal handoff namespace is unavailable")
        exp_fd = os.open(exp_name, flags, dir_fd=namespace_fd)
        exp_metadata = os.fstat(exp_fd)
        if not stat.S_ISDIR(exp_metadata.st_mode):
            raise ReviewError("terminal handoff directory is unavailable")
        handoff_fd = os.open(
            TERMINAL_HANDOFF_FILENAME,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=exp_fd,
        )
        try:
            return _read_fd_json(handoff_fd, "terminal handoff")
        finally:
            os.close(handoff_fd)
    except ReviewError:
        raise
    except (OSError, TypeError) as exc:
        raise ReviewError("terminal handoff is unavailable or unsafe") from exc
    finally:
        if exp_fd is not None:
            os.close(exp_fd)
        if namespace_fd is not None:
            os.close(namespace_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def _confirm_locator_marker(workspace: Path) -> None:
    """Consume the Workspace-local marker for discovery only.

    A well-formed marker never authenticates anything; a malformed or
    dual-authority payload (e.g. embedded bundle/identity) fails closed so a
    Workspace author cannot smuggle a self-authenticating pair.
    """

    locator = _read_locator_descriptor(workspace)
    if set(locator) != {"schema", "handoff_layout"}:
        raise ReviewError("terminal locator has an unsupported closed schema")
    if locator.get("schema") != TERMINAL_LOCATOR_SCHEMA:
        raise ReviewError("terminal locator schema is unsupported")
    if locator.get("handoff_layout") != TERMINAL_HANDOFF_LAYOUT:
        raise ReviewError("terminal locator handoff layout is unsupported")


def _terminal_bundle(
    workspace: Path, helper: str | Path | None
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Authenticate the runner-owned external handoff once with W1.

    The Workspace-local locator is consulted only as a marker so operators can
    discover the layout; it never provides the bundle or identity used for
    verification. The bundle and expected identity always originate from the
    external ``.internal-terminal-validation`` sibling namespace.
    """

    _reject_symlink_components(workspace, "Workspace")
    _confirm_locator_marker(workspace)
    handoff = _read_handoff_descriptor(workspace)
    if set(handoff) != {"schema", "terminal_identity_sha256", "bundle"}:
        raise ReviewError("terminal handoff has an unsupported closed schema")
    if handoff.get("schema") != TERMINAL_HANDOFF_SCHEMA:
        raise ReviewError("terminal handoff schema is unsupported")
    identity = handoff.get("terminal_identity_sha256")
    bundle = handoff.get("bundle")
    if (
        not isinstance(identity, str)
        or len(identity) != 64
        or any(char not in "0123456789abcdef" for char in identity)
        or not isinstance(bundle, dict)
    ):
        raise ReviewError("terminal handoff identity or bundle is malformed")

    verifier = _load_workspace_verifier(helper)
    try:
        result = verifier.verify_terminal_validation(workspace, bundle, identity)
    except Exception as exc:
        raise ReviewError("W1 terminal bundle verification failed") from exc
    if not isinstance(result, dict) or result != bundle.get("result"):
        raise ReviewError("W1 terminal verifier returned a conflicting result")
    if not isinstance(result.get("graph"), dict):
        raise ReviewError("verified terminal bundle omitted its closed graph")
    review_graph = result.get("review_graph")
    if not isinstance(review_graph, dict) or review_graph.get("schema") != "mesh-to-cad.review-graph/1":
        raise ReviewError("verified terminal bundle omitted its closed review graph")
    if not isinstance(result.get("review_facts"), dict) or not isinstance(
        result.get("evaluation_facts"), dict
    ):
        raise ReviewError("verified terminal bundle omitted current facts")
    return bundle, result, identity


def _bundle_graph_view(
    graph: dict[str, Any], review_graph: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Project only closed W1 graph facts into the legacy review graph shape."""

    nodes: list[dict[str, Any]] = [
        {"id": "canonical-reference", "type": "canonical_reference", "evidence": "input/input.json"},
        {"id": "workspace", "type": "workspace", "evidence": "workspace.json"},
    ]
    edges: list[dict[str, str]] = [
        {
            "from": "canonical-reference",
            "to": "workspace",
            "type": "reference_initializes_workspace",
        }
    ]
    review_steps = {
        item.get("step"): item
        for item in (review_graph or {}).get("steps", [])
        if isinstance(item, dict) and isinstance(item.get("step"), int)
    }
    attempt_records = {
        item["attempt"].get("attempt"): item
        for item in (review_graph or {}).get("attempts", [])
        if isinstance(item, dict)
        and isinstance(item.get("attempt"), dict)
        and isinstance(item["attempt"].get("attempt"), int)
    }
    cycle_records = {
        item.get("cycle"): item
        for item in (review_graph or {}).get("cycles", [])
        if isinstance(item, dict) and isinstance(item.get("cycle"), int)
    }
    steps = graph.get("steps") if isinstance(graph.get("steps"), list) else []
    for item in steps:
        if not isinstance(item, dict) or not isinstance(item.get("step"), int):
            raise ReviewError("verified terminal graph contains an invalid step")
        number = item["step"]
        step_id = f"step:{number}"
        preview_id = f"preview:{number}"
        measurement_id = f"measurement:{number}"
        nodes.extend(
            [
                {
                    "id": step_id,
                    "type": "measured_step",
                    "evidence": f"steps/{number:06d}/step.json",
                    "accepted": item.get("accepted"),
                    "parent_step": item.get("parent_step"),
                },
                {
                    "id": preview_id,
                    "type": "formal_preview",
                    "evidence": item.get(
                        "preview", f"steps/{number:06d}/preview/preview.json"
                    ),
                },
                {
                    "id": measurement_id,
                    "type": "measurement",
                    "evidence": item.get(
                        "measurement", f"steps/{number:06d}/measurement.json"
                    ),
                },
            ]
        )
        parent = item.get("parent_step")
        edges.append(
            {
                "from": "workspace" if parent is None else f"step:{parent}",
                "to": step_id,
                "type": "workspace_publishes_initial_step"
                if parent is None
                else "measured_step_descends_from",
            }
        )
        edges.extend(
            [
                {"from": preview_id, "to": measurement_id, "type": "preview_has_measurement"},
                {"from": measurement_id, "to": step_id, "type": "measurement_publishes_step"},
            ]
        )
        attempt_ids = review_steps.get(number, {}).get(
            "attempt_ids", item.get("attempt_ids", [])
        )
        if isinstance(attempt_ids, list):
            successful_attempt = next(
                (
                    attempt_number
                    for attempt_number in reversed(attempt_ids)
                    if isinstance(attempt_number, int)
                    and attempt_records.get(attempt_number, {})
                    .get("attempt", {})
                    .get("result")
                    in {"measured_step_published", "repair_cycle_published"}
                ),
                None,
            )
            if successful_attempt is not None:
                record = attempt_records.get(successful_attempt, {})
                document = record.get("attempt", {})
                nodes.append(
                    {
                        "id": f"attempt:{successful_attempt}",
                        "type": "attempt",
                        "evidence": record.get(
                            "path", f"attempts/{successful_attempt:06d}/attempt.json"
                        ),
                        "result": document.get("result"),
                        "intended_step": document.get("intended_step"),
                    }
                )
                edges.append(
                    {
                        "from": "workspace" if parent is None else f"step:{parent}",
                        "to": f"attempt:{successful_attempt}",
                        "type": "attempt_branches_from_step",
                    }
                )
                edges.append(
                    {
                        "from": f"attempt:{successful_attempt}",
                        "to": preview_id,
                        "type": "attempt_produces_preview",
                    }
                )

    failed_attempts = (
        graph.get("failed_attempts")
        if isinstance(graph.get("failed_attempts"), list)
        else []
    )
    for attempt in failed_attempts:
        if not isinstance(attempt, dict) or not isinstance(attempt.get("attempt"), int):
            raise ReviewError("verified terminal graph contains an invalid attempt")
        attempt_id = f"attempt:{attempt['attempt']}"
        if not any(node["id"] == attempt_id for node in nodes):
            nodes.append(
                {
                    "id": attempt_id,
                    "type": "attempt",
                    "evidence": attempt_records.get(attempt["attempt"], {}).get(
                        "path", f"attempts/{attempt['attempt']:06d}/attempt.json"
                    ),
                    "result": attempt_records.get(attempt["attempt"], {})
                    .get("attempt", {})
                    .get("result", attempt.get("result")),
                    "classification": attempt_records.get(attempt["attempt"], {})
                    .get("attempt", {})
                    .get("classification", attempt.get("classification")),
                }
            )
        parent = attempt.get("from_step")
        edges.append(
            {
                "from": "workspace" if parent is None else f"step:{parent}",
                "to": attempt_id,
                "type": "attempt_branches_from_step",
            }
        )

    cycles = graph.get("cycles") if isinstance(graph.get("cycles"), list) else []
    for cycle in cycles:
        if not isinstance(cycle, dict) or not isinstance(cycle.get("cycle"), int):
            raise ReviewError("verified terminal graph contains an invalid cycle")
        number = cycle["cycle"]
        cycle_id = f"cycle:{number}"
        batch_id = f"repair-batch:{number}"
        source_id = f"source-change:{number}"
        diff_id = f"region-diff:{number}"
        assessment_id = f"assessment:{number}"
        cycle_record = cycle_records.get(number, {})
        plan = cycle_record.get("plan", {})
        source_changes = cycle_record.get("source_changes", {})
        region_diff = cycle_record.get("diff_document", {})
        assessment = cycle_record.get("assessment", {})
        nodes.extend(
            [
                {"id": cycle_id, "type": "repair_cycle", "evidence": f"cycles/{number:06d}/cycle.json", "from_step": cycle.get("from_step"), "to_step": cycle.get("to_step")},
                {"id": batch_id, "type": "repair_batch", "evidence": f"cycles/{number:06d}/plan.json", "rationale": plan.get("rationale") if isinstance(plan, dict) else None},
                {"id": source_id, "type": "source_change", "evidence": f"cycles/{number:06d}/source_changes.json", "files": source_changes.get("files", []) if isinstance(source_changes, dict) else []},
                {"id": diff_id, "type": "region_diff", "evidence": cycle.get("diff", f"cycles/{number:06d}/diff.json"), "identity": region_diff.get("identity") if isinstance(region_diff, dict) else None},
                {"id": assessment_id, "type": "agent_assessment", "evidence": f"cycles/{number:06d}/assessment.json", "summary": assessment.get("summary") if isinstance(assessment, dict) else None},
            ]
        )
        if isinstance(plan, dict):
            for target in plan.get("selected_targets", []):
                if not isinstance(target, dict):
                    continue
                target_key = str(target.get("target_key"))
                target_id = f"repair-target:{number}:{target_key}"
                nodes.append(
                    {
                        "id": target_id,
                        "type": "repair_target",
                        "evidence": f"cycles/{number:06d}/plan.json",
                        "target_key": target_key,
                        "mask_sha256": target.get("mask_sha256"),
                    }
                )
                edges.append(
                    {"from": f"step:{cycle.get('from_step')}", "to": target_id, "type": "step_exposes_target"}
                )
                edges.append(
                    {"from": target_id, "to": batch_id, "type": "target_selected_by_batch"}
                )
            edit_ids: list[str] = []
            for edit in plan.get("planned_edits", []):
                if not isinstance(edit, dict):
                    continue
                edit_key = str(edit.get("edit_key"))
                edit_id = f"planned-edit:{number}:{edit_key}"
                edit_ids.append(edit_id)
                nodes.append(
                    {
                        "id": edit_id,
                        "type": "planned_edit",
                        "evidence": f"cycles/{number:06d}/plan.json",
                        "edit_key": edit_key,
                        "target_keys": edit.get("target_keys", []),
                        "description": edit.get("description"),
                    }
                )
                edges.append({"from": batch_id, "to": edit_id, "type": "batch_contains_edit"})
                edges.append({"from": edit_id, "to": source_id, "type": "edit_has_source_change"})
            if not edit_ids:
                edges.append({"from": batch_id, "to": source_id, "type": "batch_has_source_change"})
        edges.extend(
            [
                {"from": source_id, "to": diff_id, "type": "source_change_measured_by_diff"},
                {"from": diff_id, "to": assessment_id, "type": "diff_assessed_by_agent"},
                {"from": assessment_id, "to": cycle_id, "type": "assessment_publishes_cycle"},
                {"from": cycle_id, "to": f"step:{cycle.get('to_step')}", "type": "cycle_publishes_step"},
            ]
        )
        attempt_ids = cycle.get("attempt_ids", [])
        if isinstance(attempt_ids, list) and attempt_ids:
            attempt_id = attempt_ids[-1]
            if isinstance(attempt_id, int):
                node_id = f"attempt:{attempt_id}"
                if any(node["id"] == node_id for node in nodes):
                    edges.append(
                        {
                            "from": node_id,
                            "to": cycle_id,
                            "type": "attempt_contributes_to_cycle",
                        }
                    )

    delivery = graph.get("final_delivery")
    if isinstance(delivery, dict):
        final = (review_graph or {}).get("final")
        final = final if isinstance(final, dict) else {}
        selection = final.get("selection")
        manifest = final.get("manifest")
        rebuild = final.get("rebuild")
        verification = final.get("verification")
        if isinstance(selection, dict):
            nodes.append(
                {
                    "id": "selection",
                    "type": "selection",
                    "evidence": "final/selection.json",
                    "selected_step": selection.get("selected_step"),
                    "considered_steps": selection.get("considered_steps", []),
                }
            )
            for step_number in selection.get("considered_steps", []):
                if isinstance(step_number, int):
                    edges.append(
                        {
                            "from": f"step:{step_number}",
                            "to": "selection",
                            "type": "step_considered_for_selection",
                        }
                    )
        if isinstance(rebuild, dict):
            nodes.append(
                {
                    "id": "rebuild",
                    "type": "rebuild",
                    "evidence": "final/rebuild.json",
                    "identity": manifest.get("rebuild_sha256") if isinstance(manifest, dict) else None,
                    "execution": manifest.get("rebuild_execution") if isinstance(manifest, dict) else None,
                }
            )
        if isinstance(verification, dict):
            nodes.append(
                {
                    "id": "verification",
                    "type": "verification",
                    "evidence": "final/verification.json",
                    "identity": manifest.get("verification_sha256") if isinstance(manifest, dict) else None,
                    "verification_identity": manifest.get("verification_identity_sha256") if isinstance(manifest, dict) else None,
                }
            )
        nodes.append(
            {
                "id": "final-delivery",
                "type": "final_delivery",
                "evidence": str(delivery.get("manifest") or "final/manifest.json"),
                "selected_step": delivery.get("selected_step"),
                "accepted": delivery.get("accepted"),
                "identity_sha256": delivery.get("identity_sha256"),
            }
        )
        if isinstance(selection, dict) and isinstance(rebuild, dict):
            edges.append({"from": "selection", "to": "rebuild", "type": "selection_triggers_rebuild"})
        if isinstance(rebuild, dict) and isinstance(verification, dict):
            edges.append({"from": "rebuild", "to": "verification", "type": "rebuild_verified_independently"})
        if isinstance(verification, dict):
            edges.append({"from": "verification", "to": "final-delivery", "type": "verification_supports_delivery"})
    unique_nodes: dict[str, dict[str, Any]] = {}
    for node in nodes:
        unique_nodes.setdefault(str(node["id"]), node)
    unique_edges: dict[tuple[str, str, str], dict[str, str]] = {}
    for edge in edges:
        key = (edge["from"], edge["to"], edge["type"])
        unique_edges.setdefault(key, edge)
    return {
        "nodes": list(unique_nodes.values()),
        "edges": list(unique_edges.values()),
        "source_graph": graph,
    }


def _bundle_review(workspace: Path, helper: str | Path | None) -> dict[str, Any]:
    _bundle, result, identity = _terminal_bundle(workspace, helper)
    graph = result["graph"]
    delivery = graph.get("final_delivery")
    accepted = bool(delivery.get("accepted")) if isinstance(delivery, dict) else False
    return {
        "verdicts": {
            "workspace_protocol": "pass",
            "reconstruction_quality": "accepted" if accepted else "delivered_with_residual",
            "production_runtime_integration": "not_auditable",
        },
        "contract_provenance": {
            "terminal_locator": TERMINAL_LOCATOR_RELATIVE,
            "terminal_handoff": (
                f"../{TERMINAL_HANDOFF_NAMESPACE}/<exp>/{TERMINAL_HANDOFF_FILENAME}"
            ),
        },
        "workspace_validation": {
            "valid": True,
            "classification": "valid",
            "recovery": result.get("recovery", []),
            "terminal_identity_sha256": identity,
        },
        "graph": _bundle_graph_view(graph, result.get("review_graph")),
        "review_facts": result["review_facts"],
        "evaluation_facts": result["evaluation_facts"],
        "issues": [],
        "unresolved": [],
        "evidence_gaps": [
            "production runtime integration requires shipped snapshot, invoked installed-skill, bundle, parity, and isolation gate evidence"
        ],
    }


def review_workspace(
    workspace: Path,
    helper: str | Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """Review one experiment from its verified Terminal Validation handoff."""

    if Path(workspace).is_symlink():
        raise ReviewError("review workspace must not be a symlink")
    workspace = workspace.resolve()
    return 0, _bundle_review(workspace, helper)


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
    return (
        path.parent
        / TERMINAL_HANDOFF_NAMESPACE
        / path.name
        / TERMINAL_HANDOFF_FILENAME
    ).is_file()


def _discover_target(target: Path) -> tuple[Path | None, list[Path]]:
    if target.is_symlink():
        raise ReviewError(f"review target must not be a symlink: {target}")
    target = target.resolve()
    if _is_experiment(target):
        return None, [target]
    if not target.is_dir():
        raise ReviewError(f"review target is not a directory: {target}")
    namespace = target / TERMINAL_HANDOFF_NAMESPACE
    if namespace.is_symlink() or not namespace.is_dir():
        raise ReviewError(f"group has no terminal handoff namespace: {target}")
    experiments: list[Path] = []
    for handoff in namespace.iterdir():
        child = target / handoff.name
        if handoff.is_symlink() or child.is_symlink():
            raise ReviewError(f"review target contains a symlink: {child}")
        if handoff.is_dir() and child.is_dir():
            experiments.append(child.resolve())
    experiments.sort()
    if not experiments:
        raise ReviewError(f"group contains no reviewable experiments: {target}")
    return target, experiments


def _review_paths(
    group: Path | None,
    experiments: list[Path],
    review_root: Path | None,
    *,
    bundle_mode: bool = False,
) -> tuple[Path, dict[Path, Path]]:
    """Map immutable evidence sources to their writable review destinations."""

    if review_root is None:
        if bundle_mode:
            root = (group if group is not None else experiments[0]) / "run" / "review"
            for component in (root.parent, root):
                if component.is_symlink() or (
                    component.exists() and not component.is_dir()
                ):
                    raise ReviewError(
                        f"default review destination is not a directory: {component}"
                    )
                component.mkdir(parents=True, exist_ok=True)
            if root.is_symlink():
                raise ReviewError("default review destination must not be a symlink")
        else:
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
        if destination.is_symlink():
            raise ReviewError(
                f"review destination must not be a symlink: {destination}"
            )
        destination.mkdir(parents=True, exist_ok=True)
        destinations[workspace] = destination.resolve()
    return root, destinations


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


def _prepare_experiment(
    workspace: Path,
    group: Path | None,
    review_destination: Path,
    helper: str | Path | None,
    bundle_mode: bool,
) -> tuple[int, dict[str, Any]]:
    status, baseline = review_workspace(workspace, helper)
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
        "review_storage": {
            "scope": "workspace-root" if not bundle_mode else "run/review"
        },
        "baseline": baseline,
    })
    _atomic_write_json(review_destination / "review-input.json", evidence)
    return status, evidence


def prepare_target(
    target: Path,
    helper: str | Path | None = None,
    review_root: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    """Compile deterministic review evidence for one experiment or a group."""

    group, experiments = _discover_target(target)
    bundle_mode = review_root is None
    output_root, destinations = _review_paths(
        group, experiments, review_root, bundle_mode=bundle_mode
    )
    results: list[dict[str, Any]] = []
    status = 0
    for workspace in experiments:
        experiment_status, evidence = _prepare_experiment(
            workspace,
            group,
            destinations[workspace],
            helper,
            bundle_mode,
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
        "review_root": str(output_root.resolve()),
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


def _default_bundle_review_mode(
    group: Path | None, experiments: list[Path], review_root: Path | None
) -> bool:
    """Find the storage mode written by prepare without trusting caller paths."""

    if review_root is not None:
        return False
    source = group if group is not None else experiments[0]
    bundle_root = source / "run" / "review"
    if group is not None:
        if (bundle_root / "review-input.json").is_file():
            return True
        if bundle_root.is_dir() and not bundle_root.is_symlink() and any(
            (child / "review-input.json").is_file()
            for child in bundle_root.iterdir()
            if not child.is_symlink() and child.is_dir()
        ):
            return True
    elif (bundle_root / "review-input.json").is_file():
        return True

    legacy_input = source / "review-input.json"
    if legacy_input.is_file():
        evidence_paths = (
            [
                child / "review-input.json"
                for child in experiments
                if (child / "review-input.json").is_file()
            ]
            if group is not None
            else [legacy_input]
        )
        scopes = {
            _read_json(path).get("review_storage", {}).get("scope")
            for path in evidence_paths
        }
        if not evidence_paths or scopes != {"workspace-root"}:
            raise ReviewError(
                "legacy review input is unscoped; rerun prepare to use the "
                "default run/review bundle"
            )
        return False
    return True


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
    result = {
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
    for key in ("review_facts", "evaluation_facts"):
        if key in baseline:
            result[key] = baseline[key]
    return result


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
        "| Experiment | Workspace | Reconstruction | Production runtime |",
        "|---|---|---|---|",
    ]
    for workspace, review in reviews:
        verdicts = review["verdicts"]
        lines.append(
            f"| {workspace.name} | {verdicts['workspace_protocol']} | "
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
    bundle_mode = _default_bundle_review_mode(group, experiments, review_root)
    output_root, destinations = _review_paths(
        group, experiments, review_root, bundle_mode=bundle_mode
    )
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
        default=None,
        help="W1 Workspace facade used to verify the Terminal Validation handoff",
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
    prepare.add_argument(
        "--workspace-helper",
        default=None,
        help="W1 Workspace facade used to verify the Terminal Validation handoff",
    )
    prepare.add_argument(
        "--review-root",
        type=Path,
        help="write review artifacts outside the immutable evidence target",
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
        workspace = args.workspace.resolve()
        status, review = review_workspace(workspace, args.workspace_helper)
        _, destinations = _review_paths(None, [workspace], None, bundle_mode=True)
        destination = destinations[workspace]
        _publish(destination, review)
    except (OSError, ReviewError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "ok": status == 0,
                "status": status,
                "classification": review["workspace_validation"]["classification"],
                "review_json": str(destination / "review.json"),
                "review_markdown": str(destination / "review.md"),
            },
            separators=(",", ":"),
        )
    )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
