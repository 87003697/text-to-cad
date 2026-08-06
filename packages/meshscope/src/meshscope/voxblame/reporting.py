"""Versioned JSON projection for VoxBlame grading domain objects."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from meshscope.voxblame.codec import STORAGE_SCHEMA
from meshscope.voxblame.frame import CanonicalFrame
from meshscope.voxblame.grading import (
    CHANGE_KINDS,
    ChangeCell,
    ErrorCell,
    NextAction,
    RegionHandle,
    world_bounds,
)
from meshscope.voxblame.tree import SurfaceTree


REPORT_SCHEMA = "voxblame.report/2"
SUMMARY_SCHEMA = "voxblame.summary/1"


def tree_metadata(tree: SurfaceTree) -> dict[str, Any]:
    return {
        "storage_schema": STORAGE_SCHEMA,
        "logical_sha256": tree.logical_sha256,
        "node_count": tree.node_count,
        "cell_count": tree.leaf_count,
    }


def build_report(
    *,
    step: int,
    compare_to: int | None,
    max_depth: int,
    frame: CanonicalFrame,
    reference_metadata: dict[str, Any],
    candidate_tree: SurfaceTree,
    previous_tree: SurfaceTree | None,
    current_errors: Sequence[ErrorCell],
    changes: Sequence[ChangeCell],
    next_action: NextAction | None,
) -> dict[str, Any]:
    counts = {kind: 0 for kind in CHANGE_KINDS}
    for change in changes:
        counts[change.change] += 1
    overview = {
        "remaining_error_count": len(current_errors),
        "coarsest_first_error_depth": min(
            (error.depth for error in current_errors), default=None
        ),
        "no_observable_geometry_change": (
            previous_tree is not None
            and previous_tree.logical_sha256 == candidate_tree.logical_sha256
        ),
        "change_counts": counts,
        "next_action": next_action_json(next_action),
    }
    return {
        "schema": REPORT_SCHEMA,
        "step": step,
        "compare_to": compare_to,
        "max_depth": max_depth,
        "frame": frame.to_json(),
        "reference": reference_metadata,
        "candidate": tree_metadata(candidate_tree),
        "current": {
            "errors": [error_json(error, frame) for error in current_errors]
        },
        "changes": [change_json(change, frame) for change in changes],
        "overview": overview,
    }


def summarize_report(report: dict[str, Any], root: Path) -> dict[str, Any]:
    overview = report["overview"]
    return {
        "schema": SUMMARY_SCHEMA,
        "step": report["step"],
        "compare_to": report["compare_to"],
        "report": f"{root.name}/steps/{int(report['step']):06d}/report.json",
        "max_depth": report["max_depth"],
        "frame": report["frame"],
        "reference": {
            "storage_schema": report["reference"]["storage_schema"],
            "logical_sha256": report["reference"]["logical_sha256"],
        },
        "candidate": {
            "storage_schema": report["candidate"]["storage_schema"],
            "logical_sha256": report["candidate"]["logical_sha256"],
        },
        "no_observable_geometry_change": overview[
            "no_observable_geometry_change"
        ],
        "remaining_error_count": overview["remaining_error_count"],
        "coarsest_first_error_depth": overview["coarsest_first_error_depth"],
        "change_counts": overview["change_counts"],
        "next_action": overview["next_action"],
    }


def region_handle_json(region: RegionHandle) -> dict[str, Any]:
    return {
        "depth": region.depth,
        "octant_prefix": str(region.octant_prefix),
    }


def error_json(error: ErrorCell, frame: CanonicalFrame) -> dict[str, Any]:
    return {
        "direction": error.direction,
        "first_error_depth": error.depth,
        "morton_prefix": str(error.prefix),
        "region_handle": region_handle_json(error.region),
        "bounds_world": bounds_json(world_bounds(error.region, frame)),
    }


def change_json(change: ChangeCell, frame: CanonicalFrame) -> dict[str, Any]:
    return {
        "change": change.change,
        "morton_prefix": str(change.prefix),
        "depth": change.depth,
        "region_handle": region_handle_json(change.region),
        "bounds_world": bounds_json(world_bounds(change.region, frame)),
        "previous_error": error_state_json(change.previous_error),
        "current_error": error_state_json(change.current_error),
    }


def next_action_json(action: NextAction | None) -> dict[str, Any] | None:
    if action is None:
        return None
    lower, upper = action.bounds_world
    return {
        "reason": action.reason,
        "direction": action.direction,
        "first_error_depth": action.first_error_depth,
        "region_handle": region_handle_json(action.region),
        "bounds_world": {
            "min": list(lower),
            "max": list(upper),
        },
    }


def error_state_json(error: ErrorCell | None) -> dict[str, Any] | None:
    if error is None:
        return None
    return {
        "direction": error.direction,
        "first_error_depth": error.depth,
        "region_handle": region_handle_json(error.region),
    }


def bounds_json(
    bounds: tuple[Any, Any],
) -> dict[str, list[float]]:
    lower, upper = bounds
    return {
        "min": [float(value) for value in lower],
        "max": [float(value) for value in upper],
    }
