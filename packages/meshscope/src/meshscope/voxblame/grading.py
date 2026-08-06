"""Pure tree grading and iteration-over-iteration change analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np

from meshscope.voxblame.errors import OctreeError
from meshscope.voxblame.frame import CanonicalFrame, LATTICE_MIN
from meshscope.voxblame.tree import SurfaceTree, validate_depth


CHANGE_KINDS = ("introduced", "regressed", "changed", "improved", "resolved")


@dataclass(frozen=True, order=True)
class RegionHandle:
    """Stable logical address of an octree cell."""

    depth: int
    octant_prefix: int

    def __post_init__(self) -> None:
        if not isinstance(self.depth, int) or isinstance(self.depth, bool):
            raise OctreeError("region depth must be an integer")
        if self.depth < 0 or self.depth > 21:
            raise OctreeError("region depth must be in [0, 21]")
        if (
            not isinstance(self.octant_prefix, int)
            or isinstance(self.octant_prefix, bool)
            or self.octant_prefix < 0
            or self.octant_prefix >= (1 << (3 * self.depth))
        ):
            raise OctreeError("region prefix lies outside its depth")


@dataclass(frozen=True, order=True)
class ErrorCell:
    """First reference/candidate occupancy mismatch on one octree branch."""

    prefix: int
    depth: int
    direction: str

    def __post_init__(self) -> None:
        RegionHandle(self.depth, self.prefix)
        if self.direction not in {"missing", "excess"}:
            raise OctreeError("error direction must be missing or excess")

    @property
    def region(self) -> RegionHandle:
        return RegionHandle(self.depth, self.prefix)


@dataclass(frozen=True, order=True)
class ChangeCell:
    """One non-overlapping leaf in the overlay of two adaptive error trees."""

    prefix: int
    depth: int
    change: str
    previous_error: ErrorCell | None
    current_error: ErrorCell | None

    def __post_init__(self) -> None:
        RegionHandle(self.depth, self.prefix)
        if self.change not in CHANGE_KINDS:
            raise OctreeError(f"invalid error-tree change: {self.change}")
        if self.previous_error is None and self.current_error is None:
            raise OctreeError("change cell must reference previous or current error")

    @property
    def region(self) -> RegionHandle:
        return RegionHandle(self.depth, self.prefix)


@dataclass(frozen=True)
class NextAction:
    """One deterministic bounded action selected from the current error state."""

    reason: str
    direction: str
    first_error_depth: int
    region: RegionHandle
    bounds_world: tuple[tuple[float, float, float], tuple[float, float, float]]


def grade_surface_trees(
    reference_tree: SurfaceTree,
    candidate_tree: SurfaceTree,
    *,
    visit_counts: dict[str, int] | None = None,
) -> list[ErrorCell]:
    """Synchronously traverse two trees and stop each branch at first mismatch."""
    if reference_tree.max_depth != candidate_tree.max_depth:
        raise OctreeError("surface trees must share max_depth")
    max_depth = reference_tree.max_depth
    errors: list[ErrorCell] = []

    def visit(
        reference_node: int,
        candidate_node: int,
        prefix: int,
        depth: int,
    ) -> None:
        if visit_counts is not None:
            visit_counts["visited"] = visit_counts.get("visited", 0) + 1
        for child in range(8):
            reference_valid = reference_tree.child_occupied(reference_node, child)
            candidate_valid = candidate_tree.child_occupied(candidate_node, child)
            child_prefix = (prefix << 3) | child
            child_depth = depth + 1
            if reference_valid != candidate_valid:
                errors.append(
                    ErrorCell(
                        child_prefix,
                        child_depth,
                        "missing" if reference_valid else "excess",
                    )
                )
                continue
            if not reference_valid or child_depth == max_depth:
                continue
            reference_child = reference_tree.child_node(
                reference_node, depth, child
            )
            candidate_child = candidate_tree.child_node(
                candidate_node, depth, child
            )
            if reference_child is None or candidate_child is None:
                raise OctreeError("surface-tree internal child is missing")
            visit(reference_child, candidate_child, child_prefix, child_depth)

    reference_valid = bool(reference_tree.masks[0])
    candidate_valid = bool(candidate_tree.masks[0])
    if reference_valid != candidate_valid:
        return [ErrorCell(0, 0, "missing" if reference_valid else "excess")]
    if reference_valid:
        visit(0, 0, 0, 0)
    return errors


def compare_error_trees(
    previous: Iterable[ErrorCell],
    current: Iterable[ErrorCell],
    max_depth: int,
) -> list[ChangeCell]:
    """Overlay two adaptive error trees into disjoint spatial change leaves."""
    validate_depth(max_depth)
    previous_cells = tuple(previous)
    current_cells = tuple(current)
    _validate_error_tree(previous_cells, max_depth, "previous")
    _validate_error_tree(current_cells, max_depth, "current")
    previous_exact, previous_ancestors = _tree_index(previous_cells)
    current_exact, current_ancestors = _tree_index(current_cells)
    changes: list[ChangeCell] = []

    def visit(
        prefix: int,
        depth: int,
        inherited_previous: ErrorCell | None,
        inherited_current: ErrorCell | None,
    ) -> None:
        key = (depth, prefix)
        previous_state = inherited_previous or previous_exact.get(key)
        current_state = inherited_current or current_exact.get(key)
        previous_below = previous_state is None and key in previous_ancestors
        current_below = current_state is None and key in current_ancestors

        if previous_state is not None and current_state is not None:
            if previous_state.direction != current_state.direction:
                changes.append(
                    ChangeCell(prefix, depth, "changed", previous_state, current_state)
                )
                return
            if current_state.depth > previous_state.depth:
                changes.append(
                    ChangeCell(
                        prefix, depth, "improved", previous_state, current_state
                    )
                )
                return
            if current_state.depth < previous_state.depth:
                changes.append(
                    ChangeCell(
                        prefix, depth, "regressed", previous_state, current_state
                    )
                )
                return
            return

        if previous_state is not None and current_state is None and not current_below:
            changes.append(ChangeCell(prefix, depth, "resolved", previous_state, None))
            return
        if current_state is not None and previous_state is None and not previous_below:
            changes.append(ChangeCell(prefix, depth, "introduced", None, current_state))
            return
        if (
            previous_state is None
            and current_state is None
            and not previous_below
            and not current_below
        ):
            return
        if depth >= max_depth:
            raise OctreeError("invalid error tree overlay at max_depth")
        for child in range(8):
            visit(
                (prefix << 3) | child,
                depth + 1,
                previous_state,
                current_state,
            )

    visit(0, 0, None, None)
    return changes


def lattice_bounds(
    region_or_prefix: RegionHandle | int,
    depth: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return canonical bounds for a logical region."""
    region = _coerce_region(region_or_prefix, depth)
    coordinates = np.asarray(
        decode_octant_prefix(region.octant_prefix, region.depth),
        dtype=np.float64,
    )
    edge = 1.0 / (1 << region.depth)
    lower = LATTICE_MIN + coordinates * edge
    return lower, lower + edge


def world_bounds(
    region_or_prefix: RegionHandle | int,
    depth_or_frame: int | CanonicalFrame,
    frame: CanonicalFrame | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return world-space bounds while accepting the legacy prefix/depth form."""
    if isinstance(region_or_prefix, RegionHandle):
        if not isinstance(depth_or_frame, CanonicalFrame) or frame is not None:
            raise OctreeError("world_bounds(region, frame) requires a frame")
        region = region_or_prefix
        resolved_frame = depth_or_frame
    else:
        if not isinstance(depth_or_frame, int) or frame is None:
            raise OctreeError("world_bounds(prefix, depth, frame) is invalid")
        region = RegionHandle(depth_or_frame, region_or_prefix)
        resolved_frame = frame
    lower, upper = lattice_bounds(region)
    return (
        resolved_frame.lattice_to_world(lower),
        resolved_frame.lattice_to_world(upper),
    )


def select_next_action(
    changes: Iterable[ChangeCell],
    current_errors: Iterable[ErrorCell],
    frame: CanonicalFrame,
) -> NextAction | None:
    """Choose one deterministic action without exposing the full report."""
    priority = {"regressed": 0, "introduced": 1, "changed": 2, "remaining": 3}
    candidates: list[tuple[tuple[Any, ...], NextAction]] = []
    for change in changes:
        if change.change not in {"regressed", "introduced", "changed"}:
            continue
        current = change.current_error
        if current is None:
            continue
        bounds = world_bounds(change.region, frame)
        candidates.append(
            (
                _action_sort_key(priority[change.change], current.depth, bounds),
                _next_action(change.change, current, change.region, bounds),
            )
        )
    if not candidates:
        for error in current_errors:
            bounds = world_bounds(error.region, frame)
            candidates.append(
                (
                    _action_sort_key(priority["remaining"], error.depth, bounds),
                    _next_action("remaining", error, error.region, bounds),
                )
            )
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def decode_octant_prefix(code: int, depth: int) -> tuple[int, int, int]:
    """Decode the xyz child bits of a logical region handle."""
    region = RegionHandle(depth, int(code))
    coordinates = [0, 0, 0]
    for shift in range(region.depth - 1, -1, -1):
        child = (region.octant_prefix >> (3 * shift)) & 7
        coordinates[0] = (coordinates[0] << 1) | ((child >> 2) & 1)
        coordinates[1] = (coordinates[1] << 1) | ((child >> 1) & 1)
        coordinates[2] = (coordinates[2] << 1) | (child & 1)
    return tuple(coordinates)


def _coerce_region(
    region_or_prefix: RegionHandle | int,
    depth: int | None,
) -> RegionHandle:
    if isinstance(region_or_prefix, RegionHandle):
        if depth is not None:
            raise OctreeError("region depth must not be supplied twice")
        return region_or_prefix
    if depth is None:
        raise OctreeError("region depth is required")
    return RegionHandle(depth, region_or_prefix)


def _tree_index(
    cells: tuple[ErrorCell, ...],
) -> tuple[dict[tuple[int, int], ErrorCell], set[tuple[int, int]]]:
    exact: dict[tuple[int, int], ErrorCell] = {}
    ancestors: set[tuple[int, int]] = set()
    for cell in cells:
        key = (cell.depth, cell.prefix)
        exact[key] = cell
        for depth in range(cell.depth):
            ancestors.add((depth, cell.prefix >> (3 * (cell.depth - depth))))
    return exact, ancestors


def _validate_error_tree(
    cells: tuple[ErrorCell, ...],
    max_depth: int,
    label: str,
) -> None:
    seen: set[tuple[int, int]] = set()
    for cell in cells:
        if cell.depth > max_depth:
            raise OctreeError(f"{label} error exceeds max_depth")
        key = (cell.depth, cell.prefix)
        if key in seen:
            raise OctreeError(f"{label} error tree contains duplicates")
        for depth in range(cell.depth):
            ancestor = (depth, cell.prefix >> (3 * (cell.depth - depth)))
            if ancestor in seen:
                raise OctreeError(f"{label} error tree contains overlapping nodes")
        if any(
            other_depth > cell.depth
            and other_prefix >> (3 * (other_depth - cell.depth)) == cell.prefix
            for other_depth, other_prefix in seen
        ):
            raise OctreeError(f"{label} error tree contains overlapping nodes")
        seen.add(key)


def _action_sort_key(
    priority: int,
    first_error_depth: int,
    bounds: tuple[np.ndarray, np.ndarray],
) -> tuple[Any, ...]:
    lower, upper = bounds
    volume = float(np.prod(upper - lower))
    return (priority, first_error_depth, -volume, *[float(v) for v in lower])


def _next_action(
    reason: str,
    error: ErrorCell,
    region: RegionHandle,
    bounds: tuple[np.ndarray, np.ndarray],
) -> NextAction:
    lower, upper = bounds
    return NextAction(
        reason=reason,
        direction=error.direction,
        first_error_depth=error.depth,
        region=region,
        bounds_world=(
            tuple(float(value) for value in lower),
            tuple(float(value) for value in upper),
        ),
    )
