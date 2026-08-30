"""Deterministic complete Repair Target publication and paging."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable, TYPE_CHECKING

from meshscope.voxblame.codec import read_surface_tree
from meshscope.voxblame.contracts import (
    MAX_DEPTH,
    validate_measurement_contract,
    validate_session_contract,
)
from meshscope.voxblame.errors import (
    OctreeError,
    UnsupportedOrInvalidVoxBlameState,
)
from meshscope.voxblame.tree import decode_octant_prefix
from meshscope.voxblame.tree import SurfaceTree

if TYPE_CHECKING:
    from meshscope.voxblame.exterior import ExteriorMeasurement


LEGACY_TARGET_PARTITION_PROFILE = "repair_target_partition/1"
ADAPTIVE_TARGET_PARTITION_PROFILE = "repair_target_partition/2"
TARGET_PARTITION_PROFILE = "repair_target_partition/3"
LEGACY_TARGET_ORDERING_PROFILE = "repair_target_display/1"
TARGET_ORDERING_PROFILE = "repair_target_display/2"
INTERIOR_REGION_SET_SCHEMA = "octree_region_set/1"
EXTERIOR_GRID_REGION_SET_SCHEMA = "exterior_grid_region_set/1"
TARGET_PAGE_SIZE = 8
TARGET_SPLIT_MAX_CELLS = 4_096
ADAPTIVE_TARGET_SPLIT_MAX_CELLS = 65_536
TARGET_SPLIT_DEPTH = 4

_REGION_SET_DIGEST_DOMAIN = b"octree_region_set/1\0"
_EXTERIOR_REGION_SET_DIGEST_DOMAIN = b"exterior_grid_region_set/1\0"
_MISSING = 1
_EXCESS = 2
_NEIGHBOR_OFFSETS = tuple(
    (dx, dy, dz)
    for dx in (-1, 0, 1)
    for dy in (-1, 0, 1)
    for dz in (-1, 0, 1)
    if 0 < abs(dx) + abs(dy) + abs(dz) <= 2
)


@dataclass(frozen=True)
class _RepairTargetPartition:
    """Internal frozen report metadata plus canonical mask artifact bytes."""

    report: dict[str, Any]
    mask_bytes: dict[str, bytes]


@dataclass(frozen=True)
class _DirectionalTarget:
    direction: str
    coarse_prefix: int
    bounds: dict[str, list[float]]
    mask: dict[str, Any]
    mask_bytes: bytes
    component_key: str


def partition_repair_targets(
    missing_tree: SurfaceTree,
    excess_tree: SurfaceTree,
    *,
    reference_tree: SurfaceTree,
    candidate_tree: SurfaceTree,
    active_depth: int | None,
    source_step: int,
    step_root: str | None = None,
    exterior: ExteriorMeasurement | None = None,
) -> _RepairTargetPartition:
    """Publish one directional target per net-error cell at the active depth."""

    if (
        not isinstance(source_step, int)
        or isinstance(source_step, bool)
        or source_step < 0
    ):
        raise OctreeError("Repair Target source step must be non-negative")
    if step_root is None:
        step_root = f"voxblame/steps/{source_step:06d}"
    if any(
        tree.max_depth != MAX_DEPTH
        for tree in (missing_tree, excess_tree, reference_tree, candidate_tree)
    ):
        raise OctreeError("Repair Targets require depth-8 error evidence")
    missing = {int(code) for code in missing_tree.iter_leaf_codes()}
    excess = {int(code) for code in excess_tree.iter_leaf_codes()}
    if active_depth is None:
        if missing or excess:
            raise OctreeError("nonempty interior errors require an active repair depth")
    elif (
        not isinstance(active_depth, int)
        or isinstance(active_depth, bool)
        or not 1 <= active_depth <= MAX_DEPTH
    ):
        raise OctreeError("active repair depth must be an integer from 1 through 8")
    candidates = _directional_targets(
        reference_tree,
        candidate_tree,
        missing=missing,
        excess=excess,
        active_depth=active_depth,
    )
    ordered: list[dict[str, Any]] = []
    mask_artifacts: dict[str, bytes] = {}
    for display_rank, candidate in enumerate(candidates):
        missing_count = 1 if candidate.direction == "missing" else 0
        excess_count = 1 if candidate.direction == "excess" else 0
        target_key = _target_identity_key(
            source_step=source_step,
            partition_profile=TARGET_PARTITION_PROFILE,
            mask_sha256=candidate.mask["logical_sha256"],
            missing_count=missing_count,
            excess_count=excess_count,
            component_key=candidate.component_key,
            split_index=0,
            split_count=1,
        )
        file_name = f"target-{candidate.mask['logical_sha256']}.vbregions"
        relative_path = f"{step_root}/targets/{file_name}"
        mask = {"path": relative_path, **candidate.mask}
        ordered.append(
            {
                "target_key": target_key,
                "source_step": source_step,
                "kind": "interior",
                "display_rank": display_rank,
                "bounds_canonical": candidate.bounds,
                "error_profile": {
                    "missing_surface_count": missing_count,
                    "excess_surface_count": excess_count,
                    "surface_error_count": 1,
                },
                "mask": mask,
                "component": {
                    "component_key": candidate.component_key,
                    "split_index": 0,
                    "split_count": 1,
                    "split_reason": "not_split",
                },
                "exterior": None,
            }
        )
        mask_artifacts[file_name] = candidate.mask_bytes

    if exterior is not None and exterior.surface_cell_count:
        exterior_bytes, exterior_digest = _exterior_region_set(exterior)
        file_name = f"exterior-{exterior_digest}.vbregions"
        component_key = f"exterior-component-{exterior_digest[:16]}"
        target_key = _target_identity_key(
            source_step=source_step,
            partition_profile=TARGET_PARTITION_PROFILE,
            mask_sha256=exterior_digest,
            missing_count=0,
            excess_count=exterior.surface_cell_count,
            component_key=component_key,
            split_index=0,
            split_count=1,
        )
        exact = exterior.exact
        resolution = exterior.resolution
        ordered.append(
            {
                "target_key": target_key,
                "source_step": source_step,
                "kind": "exterior",
                "display_rank": len(ordered),
                "bounds_canonical": exact["bounds_canonical"],
                "error_profile": {
                    "missing_surface_count": 0,
                    "excess_surface_count": exterior.surface_cell_count,
                    "surface_error_count": exterior.surface_cell_count,
                },
                "mask": {
                    "storage_schema": EXTERIOR_GRID_REGION_SET_SCHEMA,
                    "path": f"{step_root}/targets/{file_name}",
                    "logical_sha256": exterior_digest,
                    "region_count": exterior.surface_cell_count,
                },
                "component": {
                    "component_key": component_key,
                    "split_index": 0,
                    "split_count": 1,
                    "split_reason": "not_split",
                },
                "exterior": {
                    "centroid_canonical": exact["centroid_canonical"],
                    "surface_cell_count": exterior.surface_cell_count,
                    "nearest_overrun": exact["nearest_overrun"],
                    "farthest_overrun": exact["farthest_overrun"],
                    "outside_directions": exact["outside_directions"],
                    "diagnostic_grid_depth": resolution["diagnostic_grid_depth"],
                    "coarsened": resolution["coarsened"],
                },
            }
        )
        mask_artifacts[file_name] = exterior_bytes

    for display_rank, target in enumerate(ordered):
        target["display_rank"] = display_rank

    _validate_interior_partition(
        [target for target in ordered if target["kind"] == "interior"],
        mask_artifacts,
        missing,
        excess,
        profile=TARGET_PARTITION_PROFILE,
        active_depth=active_depth,
        reference={int(code) for code in reference_tree.iter_leaf_codes()},
        candidate={int(code) for code in candidate_tree.iter_leaf_codes()},
    )
    return _RepairTargetPartition(
        report={
            "ordering_profile": TARGET_ORDERING_PROFILE,
            "total": len(ordered),
            "ordered_targets": ordered,
        },
        mask_bytes=mask_artifacts,
    )


def active_repair_depth(errors_by_depth: list[dict[str, Any]]) -> int | None:
    """Return the coarsest depth with interior error, or ``None`` when clear."""

    if len(errors_by_depth) != MAX_DEPTH:
        raise OctreeError("repair resolution requires depth-1 through depth-8 evidence")
    for expected_depth, evidence in enumerate(errors_by_depth, start=1):
        if evidence.get("depth") != expected_depth:
            raise OctreeError("repair resolution depth evidence is not ordered")
        count = evidence.get("surface_error_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise OctreeError("repair resolution error count is invalid")
        if count:
            return expected_depth
    return None


def _directional_targets(
    reference_tree: SurfaceTree,
    candidate_tree: SurfaceTree,
    *,
    missing: set[int],
    excess: set[int],
    active_depth: int | None,
) -> list[_DirectionalTarget]:
    if active_depth is None:
        return []
    shift = 3 * (MAX_DEPTH - active_depth)
    reference_prefixes = {
        int(code) >> shift for code in reference_tree.iter_leaf_codes()
    }
    candidate_prefixes = {
        int(code) >> shift for code in candidate_tree.iter_leaf_codes()
    }
    candidates: list[_DirectionalTarget] = []
    for direction, prefixes, support in (
        ("missing", reference_prefixes - candidate_prefixes, missing),
        ("excess", candidate_prefixes - reference_prefixes, excess),
    ):
        for prefix in prefixes:
            cells = {code for code in support if code >> shift == prefix}
            if not cells:
                raise OctreeError("active-depth target has no directional support")
            mask_bytes, mask_digest, region_count = _region_set(cells)
            component_key = _directional_component_key(
                active_depth, prefix, direction
            )
            candidates.append(
                _DirectionalTarget(
                    direction=direction,
                    coarse_prefix=prefix,
                    bounds=_prefix_bounds(prefix, active_depth),
                    mask={
                        "storage_schema": INTERIOR_REGION_SET_SCHEMA,
                        "logical_sha256": mask_digest,
                        "region_count": region_count,
                    },
                    mask_bytes=mask_bytes,
                    component_key=component_key,
                )
            )
    candidates.sort(
        key=lambda item: (
            item.coarse_prefix,
            0 if item.direction == "missing" else 1,
        )
    )
    return candidates


def _directional_component_key(depth: int, prefix: int, direction: str) -> str:
    payload = {
        "profile": TARGET_PARTITION_PROFILE,
        "active_depth": depth,
        "prefix": prefix,
        "direction": direction,
    }
    digest = hashlib.sha256(_json_bytes(payload)).hexdigest()
    return f"directional-cell-{digest[:16]}"


def _prefix_bounds(prefix: int, depth: int) -> dict[str, list[float]]:
    coordinates = decode_octant_prefix(prefix, depth)
    resolution = 1 << depth
    return {
        "min": [-0.5 + value / resolution for value in coordinates],
        "max": [-0.5 + (value + 1) / resolution for value in coordinates],
    }


def inspect_repair_frontier(
    workspace: str | Path, *, step: int, offset: int = 0
) -> dict[str, Any]:
    """Return the thin Agent view over one validated Measured Step."""

    page_repair_targets(workspace, step=step, offset=offset)
    root = Path(workspace)
    try:
        measurement = json.loads(
            (root / "steps" / f"{step:06d}" / "measurement.json").read_text(
                encoding="utf-8"
            )
        )
        session = json.loads((root / "session.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OctreeError("Repair Frontier evidence is unavailable") from exc
    validate_measurement_contract(measurement)
    validate_session_contract(session)
    partition_profile = session["profiles"]["target_partition"]
    adaptive = partition_profile in {
        ADAPTIVE_TARGET_PARTITION_PROFILE,
        TARGET_PARTITION_PROFILE,
    }
    measured_active_depth = active_repair_depth(measurement["errors_by_depth"])
    active_depth = measured_active_depth if adaptive else None
    interior = [
        target
        for target in measurement["repair_targets"]["ordered_targets"]
        if target["kind"] == "interior"
    ]
    if offset > len(interior):
        _invalid(
            "$.repair_targets.offset",
            "exceeds the frozen interior target count",
        )
    selected = interior[offset : offset + TARGET_PAGE_SIZE]
    remaining = len(interior) - offset - len(selected)
    exterior = [
        target
        for target in measurement["repair_targets"]["ordered_targets"]
        if target["kind"] == "exterior"
    ]
    return {
        "repair_frontier": {
            "active_depth": active_depth,
        },
        "alerts": [_exterior_alert(target) for target in exterior],
        "repair_targets": {
            "total": len(interior),
            "next_offset": offset + len(selected) if remaining else None,
            "items": [_agent_target_view(target) for target in selected],
        },
    }


def repair_target_page(
    report: dict[str, Any], *, offset: int = 0
) -> dict[str, Any]:
    """Return one compact path-free page from a frozen target report."""

    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise OctreeError("Repair Target offset must be a non-negative integer")
    total = report["total"]
    if offset > total:
        raise OctreeError("Repair Target offset exceeds the frozen target count")
    selected = report["ordered_targets"][offset : offset + TARGET_PAGE_SIZE]
    items = []
    for target in selected:
        compact = dict(target)
        compact["mask"] = {
            key: target["mask"][key]
            for key in ("storage_schema", "logical_sha256", "region_count")
        }
        items.append(compact)
    returned = len(items)
    remaining = total - offset - returned
    return {
        "ordering_profile": report["ordering_profile"],
        "total": total,
        "returned": returned,
        "remaining": remaining,
        "offset": offset,
        "next_offset": offset + returned if remaining else None,
        "items": items,
    }


def page_repair_targets(
    workspace: str | Path, *, step: int, offset: int = 0
) -> dict[str, Any]:
    """Page the frozen Repair Targets of one published Measured Step."""

    try:
        return _page_repair_targets(workspace, step=step, offset=offset)
    except UnsupportedOrInvalidVoxBlameState:
        raise
    except Exception as exc:
        _invalid("$", f"invalid persisted Repair Target state: {exc}")


def _page_repair_targets(
    workspace: str | Path, *, step: int, offset: int
) -> dict[str, Any]:
    """Validated implementation for :func:`page_repair_targets`."""

    if not isinstance(step, int) or isinstance(step, bool) or step < 0:
        raise OctreeError("Repair Target step must be a non-negative integer")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise OctreeError("Repair Target offset must be a non-negative integer")
    root = Path(workspace)
    report_path = root / "steps" / f"{step:06d}" / "measurement.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OctreeError("Measured Step Repair Target report is unavailable") from exc
    validate_measurement_contract(report)
    if report["step"] != step:
        _invalid("$.step", "does not match the requested Measured Step")
    if report["repair_targets"]["ordering_profile"] not in {
        LEGACY_TARGET_ORDERING_PROFILE,
        TARGET_ORDERING_PROFILE,
    }:
        _invalid(
            "$.repair_targets.ordering_profile",
            "unsupported Repair Target display profile",
        )
    _validate_published_partition(root, step, report)
    targets = report["repair_targets"]
    return repair_target_page(targets, offset=offset)


def _validate_published_partition(
    workspace: Path, step: int, measurement: dict[str, Any]
) -> None:
    step_root = workspace / "steps" / f"{step:06d}"
    missing_tree = read_surface_tree(step_root / "missing-depth8.vbsvo")
    excess_tree = read_surface_tree(step_root / "excess-depth8.vbsvo")
    missing = {int(code) for code in missing_tree.iter_leaf_codes()}
    excess = {int(code) for code in excess_tree.iter_leaf_codes()}
    try:
        session = json.loads((workspace / "session.json").read_text(encoding="utf-8"))
        profile = session["profiles"]["target_partition"]
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
    ) as exc:
        raise OctreeError("Repair Target partition profile is unavailable") from exc
    if profile not in {
        LEGACY_TARGET_PARTITION_PROFILE,
        ADAPTIVE_TARGET_PARTITION_PROFILE,
        TARGET_PARTITION_PROFILE,
    }:
        raise OctreeError("Repair Target partition profile is unsupported")
    active_depth = (
        active_repair_depth(measurement["errors_by_depth"])
        if profile in {ADAPTIVE_TARGET_PARTITION_PROFILE, TARGET_PARTITION_PROFILE}
        else MAX_DEPTH
    )
    targets = measurement["repair_targets"]["ordered_targets"]
    interior = [target for target in targets if target.get("kind") == "interior"]
    artifacts: dict[str, bytes] = {}
    expected_parent = (
        PurePosixPath(workspace.name) / "steps" / f"{step:06d}" / "targets"
    )
    for rank, target in enumerate(targets):
        if target.get("display_rank") != rank:
            raise OctreeError("Repair Target display order is invalid")
        if target.get("source_step") != step:
            raise OctreeError("Repair Target source step is invalid")
    for target in interior:
        try:
            relative_path = PurePosixPath(target["mask"]["path"])
        except (KeyError, TypeError) as exc:
            raise OctreeError("Repair Target mask path is invalid") from exc
        if relative_path.parent != expected_parent or relative_path.name in artifacts:
            raise OctreeError("Repair Target mask path is invalid")
        try:
            artifacts[relative_path.name] = (
                workspace.parent / Path(relative_path)
            ).read_bytes()
        except OSError as exc:
            raise OctreeError("Repair Target mask artifact is missing") from exc
    _validate_interior_partition(
        interior,
        artifacts,
        missing,
        excess,
        profile=profile,
        active_depth=active_depth,
        reference={
            int(code)
            for code in read_surface_tree(workspace / "reference.vbsvo").iter_leaf_codes()
        },
        candidate={
            int(code)
            for code in read_surface_tree(step_root / "candidate.vbsvo").iter_leaf_codes()
        },
    )
    exterior_targets = [
        target for target in targets if target.get("kind") == "exterior"
    ]
    if not exterior_targets:
        if measurement.get("exterior_surface", {}).get("surface_cell_count", 0):
            raise OctreeError("exterior Repair Target coverage is invalid")
        return
    try:
        exterior_snapshot = json.loads(
            (step_root / "exterior.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OctreeError("exterior Repair Target evidence is unavailable") from exc
    exterior_cells = exterior_snapshot.get("cells")
    if not isinstance(exterior_cells, list):
        raise OctreeError("exterior Repair Target evidence is invalid")
    if not exterior_cells or len(exterior_targets) > 1:
        raise OctreeError("exterior Repair Target coverage is invalid")
    target = exterior_targets[0]
    try:
        relative_path = PurePosixPath(target["mask"]["path"])
    except (KeyError, TypeError) as exc:
        raise OctreeError("exterior Repair Target mask path is invalid") from exc
    if relative_path.parent != expected_parent:
        raise OctreeError("exterior Repair Target mask path is invalid")
    try:
        data = (workspace.parent / Path(relative_path)).read_bytes()
    except OSError as exc:
        raise OctreeError("exterior Repair Target mask artifact is missing") from exc
    digest = hashlib.sha256(
        _EXTERIOR_REGION_SET_DIGEST_DOMAIN + data
    ).hexdigest()
    if digest != target["mask"].get("logical_sha256"):
        raise OctreeError("exterior mask identity mismatch")
    try:
        mask = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OctreeError("exterior Repair Target mask is invalid") from exc
    resolution = exterior_snapshot.get("resolution", {})
    expected_mask = {
        "schema": EXTERIOR_GRID_REGION_SET_SCHEMA,
        "diagnostic_grid_depth": resolution.get("diagnostic_grid_depth"),
        "cells": exterior_cells,
    }
    if mask != expected_mask or data != _json_bytes(expected_mask):
        raise OctreeError("exterior Repair Target mask conflicts with its snapshot")
    surface_count = len(exterior_cells)
    if (
        target["mask"].get("storage_schema")
        != EXTERIOR_GRID_REGION_SET_SCHEMA
        or target["mask"].get("region_count") != surface_count
        or target["error_profile"]
        != {
            "missing_surface_count": 0,
            "excess_surface_count": surface_count,
            "surface_error_count": surface_count,
        }
        or target.get("exterior")
        != {
            "centroid_canonical": exterior_snapshot["exact"]["centroid_canonical"],
            "surface_cell_count": surface_count,
            "nearest_overrun": exterior_snapshot["exact"]["nearest_overrun"],
            "farthest_overrun": exterior_snapshot["exact"]["farthest_overrun"],
            "outside_directions": exterior_snapshot["exact"]["outside_directions"],
            "diagnostic_grid_depth": resolution["diagnostic_grid_depth"],
            "coarsened": resolution["coarsened"],
        }
        or target.get("bounds_canonical")
        != exterior_snapshot["exact"]["bounds_canonical"]
        or measurement["exterior_surface"]["surface_cell_count"] != surface_count
    ):
        raise OctreeError("exterior Repair Target evidence is inconsistent")
    component = target["component"]
    if component != {
        "component_key": f"exterior-component-{digest[:16]}",
        "split_index": 0,
        "split_count": 1,
        "split_reason": "not_split",
    }:
        raise OctreeError("exterior Repair Target split provenance is invalid")
    expected_key = _target_identity_key(
        source_step=step,
        partition_profile=profile,
        mask_sha256=digest,
        missing_count=0,
        excess_count=surface_count,
        component_key=component["component_key"],
        split_index=component["split_index"],
        split_count=component["split_count"],
    )
    if target["target_key"] != expected_key:
        raise OctreeError("exterior Repair Target identity mismatch")


def _partition_components(
    directions: dict[int, int], *, profile: str, active_depth: int | None
) -> list[set[int]]:
    if not directions:
        return []
    if profile == LEGACY_TARGET_PARTITION_PROFILE:
        return _connected_components(directions, depth=MAX_DEPTH)
    if profile != ADAPTIVE_TARGET_PARTITION_PROFILE or active_depth is None:
        raise OctreeError("unsupported adaptive Repair Target partition state")

    shift = 3 * (MAX_DEPTH - active_depth)
    coarse_directions: dict[int, int] = {}
    for code, direction in directions.items():
        prefix = code >> shift
        coarse_directions[prefix] = coarse_directions.get(prefix, 0) | direction
    coarse_components = _connected_components(coarse_directions, depth=active_depth)
    exact_by_prefix: dict[int, set[int]] = {}
    for code in directions:
        exact_by_prefix.setdefault(code >> shift, set()).add(code)
    return [
        set().union(*(exact_by_prefix[prefix] for prefix in component))
        for component in coarse_components
    ]


def _connected_components(
    directions: dict[int, int], *, depth: int = MAX_DEPTH
) -> list[set[int]]:
    coordinates = {
        decode_octant_prefix(code, depth): code for code in directions
    }
    remaining = set(directions)
    components: list[set[int]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        component = {seed}
        pending = deque([seed])
        while pending:
            code = pending.popleft()
            x, y, z = decode_octant_prefix(code, depth)
            for dx, dy, dz in _NEIGHBOR_OFFSETS:
                neighbor = coordinates.get((x + dx, y + dy, z + dz))
                if neighbor is None or neighbor not in remaining:
                    continue
                remaining.remove(neighbor)
                component.add(neighbor)
                pending.append(neighbor)
        components.append(component)
    return components


def _split_component(
    component: set[int],
    *,
    maximum_cells: int = TARGET_SPLIT_MAX_CELLS,
    start_depth: int = TARGET_SPLIT_DEPTH,
) -> list[set[int]]:
    if len(component) <= maximum_cells:
        return [component]

    def split(cells: set[int], depth: int) -> list[set[int]]:
        if len(cells) <= maximum_cells or depth == MAX_DEPTH:
            return [cells]
        child_depth = max(depth + 1, start_depth)
        shift = 3 * (MAX_DEPTH - child_depth)
        buckets: dict[int, set[int]] = {}
        for code in cells:
            buckets.setdefault(code >> shift, set()).add(code)
        result: list[set[int]] = []
        for prefix in sorted(buckets):
            connected_chunks = _connected_components(
                {code: _MISSING for code in buckets[prefix]}, depth=MAX_DEPTH
            )
            for connected in connected_chunks:
                result.extend(split(connected, child_depth))
        return result

    return split(component, start_depth - 1)


def _component_key(
    component: set[int],
    directions: dict[int, int],
    *,
    profile: str = LEGACY_TARGET_PARTITION_PROFILE,
    active_depth: int | None = None,
) -> str:
    payload = {
        "profile": profile,
        "cells": [[code, directions[code]] for code in sorted(component)],
    }
    if profile == ADAPTIVE_TARGET_PARTITION_PROFILE:
        payload["active_depth"] = active_depth
    digest = hashlib.sha256(_json_bytes(payload)).hexdigest()
    return f"component-{digest[:16]}"


def _target_identity_key(
    *,
    source_step: int,
    partition_profile: str,
    mask_sha256: str,
    missing_count: int,
    excess_count: int,
    component_key: str,
    split_index: int,
    split_count: int,
) -> str:
    identity = {
        "schema": "voxblame.repair-target-identity/1",
        "source_step": source_step,
        "partition_profile": partition_profile,
        "mask_sha256": mask_sha256,
        "missing_surface_count": missing_count,
        "excess_surface_count": excess_count,
        "component_key": component_key,
        "split_index": split_index,
        "split_count": split_count,
    }
    digest = hashlib.sha256(_json_bytes(identity)).hexdigest()
    return f"step-{source_step:06d}:target-{digest[:16]}"


def _region_set(cells: Iterable[int]) -> tuple[bytes, str, int]:
    active = {(MAX_DEPTH, int(code)) for code in cells}
    for depth in range(MAX_DEPTH, 0, -1):
        parents: dict[int, set[int]] = {}
        for region_depth, prefix in active:
            if region_depth == depth:
                parents.setdefault(prefix >> 3, set()).add(prefix & 7)
        for parent, children in parents.items():
            if children == set(range(8)):
                active.difference_update(
                    (depth, (parent << 3) | child) for child in range(8)
                )
                active.add((depth - 1, parent))
    regions = sorted(
        active,
        key=lambda item: (
            item[1] << (3 * (MAX_DEPTH - item[0])),
            item[0],
        ),
    )
    snapshot = {
        "schema": INTERIOR_REGION_SET_SCHEMA,
        "max_depth": MAX_DEPTH,
        "regions": [
            {"depth": depth, "prefix": prefix} for depth, prefix in regions
        ],
    }
    data = _json_bytes(snapshot)
    digest = hashlib.sha256(_REGION_SET_DIGEST_DOMAIN + data).hexdigest()
    return data, digest, len(regions)


def _exterior_region_set(
    exterior: ExteriorMeasurement,
) -> tuple[bytes, str]:
    snapshot = {
        "schema": EXTERIOR_GRID_REGION_SET_SCHEMA,
        "diagnostic_grid_depth": exterior.resolution["diagnostic_grid_depth"],
        "cells": exterior.snapshot["cells"],
    }
    data = _json_bytes(snapshot)
    digest = hashlib.sha256(
        _EXTERIOR_REGION_SET_DIGEST_DOMAIN + data
    ).hexdigest()
    return data, digest


def _expand_region_set(data: bytes) -> set[int]:
    try:
        snapshot = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OctreeError("Repair Target mask is invalid") from exc
    if (
        set(snapshot) != {"schema", "max_depth", "regions"}
        or snapshot.get("schema") != INTERIOR_REGION_SET_SCHEMA
        or snapshot.get("max_depth") != MAX_DEPTH
        or not isinstance(snapshot.get("regions"), list)
    ):
        raise OctreeError("Repair Target mask is unsupported")
    result: set[int] = set()
    expanded_count = 0
    for region in snapshot["regions"]:
        if set(region) != {"depth", "prefix"}:
            raise OctreeError("Repair Target mask region is invalid")
        depth = region["depth"]
        prefix = region["prefix"]
        if (
            not isinstance(depth, int)
            or isinstance(depth, bool)
            or not 0 <= depth <= MAX_DEPTH
            or not isinstance(prefix, int)
            or isinstance(prefix, bool)
            or not 0 <= prefix < (1 << (3 * depth))
        ):
            raise OctreeError("Repair Target mask region is invalid")
        remaining = 3 * (MAX_DEPTH - depth)
        expanded_count += 1 << remaining
        if expanded_count > ADAPTIVE_TARGET_SPLIT_MAX_CELLS:
            raise OctreeError("Repair Target mask exceeds its split profile")
        expanded = range(prefix << remaining, (prefix + 1) << remaining)
        if result.intersection(expanded):
            raise OctreeError("Repair Target mask regions overlap")
        result.update(expanded)
    canonical, _, _ = _region_set(result)
    if canonical != data:
        raise OctreeError("Repair Target mask is not canonical and minimal")
    return result


def _canonical_bounds(cells: set[int]) -> dict[str, list[float]]:
    coordinates = [decode_octant_prefix(code, MAX_DEPTH) for code in cells]
    resolution = 1 << MAX_DEPTH
    minimum = [min(item[axis] for item in coordinates) for axis in range(3)]
    maximum = [max(item[axis] for item in coordinates) + 1 for axis in range(3)]
    return {
        "min": [-0.5 + value / resolution for value in minimum],
        "max": [-0.5 + value / resolution for value in maximum],
    }


def _agent_target_view(target: dict[str, Any]) -> dict[str, Any]:
    """Hide persisted validation machinery behind the Agent interface."""

    profile = target["error_profile"]
    return {
        "bounds_canonical": target["bounds_canonical"],
        "missing_surface_count": profile["missing_surface_count"],
        "excess_surface_count": profile["excess_surface_count"],
        "target_key": target["target_key"],
        "mask_sha256": target["mask"]["logical_sha256"],
    }


def _exterior_alert(target: dict[str, Any]) -> dict[str, Any]:
    """Expose only actionable spatial facts and stable selection identity."""

    exterior = target["exterior"]
    return {
        "kind": "exterior_surface",
        "bounds_canonical": target["bounds_canonical"],
        "outside_directions": exterior["outside_directions"],
        "nearest_overrun": exterior["nearest_overrun"],
        "farthest_overrun": exterior["farthest_overrun"],
        "excess_surface_count": target["error_profile"]["excess_surface_count"],
        "target_key": target["target_key"],
        "mask_sha256": target["mask"]["logical_sha256"],
    }


def _validate_interior_partition(
    targets: list[dict[str, Any]],
    artifacts: dict[str, bytes],
    missing: set[int],
    excess: set[int],
    *,
    profile: str = LEGACY_TARGET_PARTITION_PROFILE,
    active_depth: int | None = None,
    reference: set[int] | None = None,
    candidate: set[int] | None = None,
) -> None:
    if profile == TARGET_PARTITION_PROFILE:
        if reference is None or candidate is None:
            raise OctreeError("directional Repair Target occupancy is unavailable")
        _validate_directional_partition(
            targets,
            artifacts,
            missing=missing,
            excess=excess,
            reference=reference,
            candidate=candidate,
            active_depth=active_depth,
        )
        return
    directions = {code: _MISSING for code in missing}
    for code in excess:
        directions[code] = directions.get(code, 0) | _EXCESS
    expected_splits: dict[frozenset[int], dict[str, Any]] = {}
    for component_cells in _partition_components(
        directions, profile=profile, active_depth=active_depth
    ):
        component_key = _component_key(
            component_cells,
            directions,
            profile=profile,
            active_depth=active_depth,
        )
        maximum_cells = (
            ADAPTIVE_TARGET_SPLIT_MAX_CELLS
            if profile == ADAPTIVE_TARGET_PARTITION_PROFILE
            else TARGET_SPLIT_MAX_CELLS
        )
        splits = _split_component(
            component_cells,
            maximum_cells=maximum_cells,
            start_depth=(
                max(TARGET_SPLIT_DEPTH, active_depth or TARGET_SPLIT_DEPTH)
                if profile == ADAPTIVE_TARGET_PARTITION_PROFILE
                else TARGET_SPLIT_DEPTH
            ),
        )
        for split_index, split_cells in enumerate(splits):
            expected_splits[frozenset(split_cells)] = {
                "component_key": component_key,
                "split_index": split_index,
                "split_count": len(splits),
                "split_reason": (
                    "not_split" if len(splits) == 1 else "coarse_octree_locality"
                ),
            }

    observed: set[int] = set()
    for target in targets:
        path = Path(target["mask"]["path"])
        data = artifacts.get(path.name)
        if data is None:
            raise OctreeError("Repair Target mask artifact is missing")
        digest = hashlib.sha256(_REGION_SET_DIGEST_DOMAIN + data).hexdigest()
        if digest != target["mask"]["logical_sha256"]:
            raise OctreeError("Repair Target mask identity mismatch")
        cells = _expand_region_set(data)
        snapshot = json.loads(data)
        if (
            target["mask"].get("storage_schema")
            != INTERIOR_REGION_SET_SCHEMA
            or target["mask"]["region_count"] != len(snapshot["regions"])
        ):
            raise OctreeError("Repair Target mask region count mismatch")
        if target.get("bounds_canonical") != _canonical_bounds(cells):
            raise OctreeError("Repair Target bounds conflict with its exact mask")
        if observed.intersection(cells):
            raise OctreeError("Repair Target masks overlap")
        observed.update(cells)
        error_profile = target["error_profile"]
        if (
            error_profile["missing_surface_count"] != len(cells & missing)
            or error_profile["excess_surface_count"] != len(cells & excess)
            or error_profile["surface_error_count"] != len(cells)
        ):
            raise OctreeError("Repair Target error profile conflicts with its mask")
        component = target["component"]
        if component != expected_splits.get(frozenset(cells)):
            raise OctreeError(
                "Repair Target connectivity or split provenance is invalid"
            )
        expected_key = _target_identity_key(
            source_step=target["source_step"],
            partition_profile=profile,
            mask_sha256=target["mask"]["logical_sha256"],
            missing_count=error_profile["missing_surface_count"],
            excess_count=error_profile["excess_surface_count"],
            component_key=component["component_key"],
            split_index=component["split_index"],
            split_count=component["split_count"],
        )
        if target["target_key"] != expected_key:
            raise OctreeError("Repair Target target identity mismatch")
    if observed != missing | excess:
        raise OctreeError("Repair Targets do not cover the complete error set")


def _validate_directional_partition(
    targets: list[dict[str, Any]],
    artifacts: dict[str, bytes],
    *,
    missing: set[int],
    excess: set[int],
    reference: set[int],
    candidate: set[int],
    active_depth: int | None,
) -> None:
    if active_depth is None:
        if targets:
            raise OctreeError("clear active-depth frontier published interior targets")
        return
    shift = 3 * (MAX_DEPTH - active_depth)
    reference_prefixes = {code >> shift for code in reference}
    candidate_prefixes = {code >> shift for code in candidate}
    expected = {
        (prefix, "missing")
        for prefix in reference_prefixes - candidate_prefixes
    } | {
        (prefix, "excess")
        for prefix in candidate_prefixes - reference_prefixes
    }
    observed: set[tuple[int, str]] = set()
    for target in targets:
        path = Path(target["mask"]["path"])
        data = artifacts.get(path.name)
        if data is None:
            raise OctreeError("Repair Target mask artifact is missing")
        digest = hashlib.sha256(_REGION_SET_DIGEST_DOMAIN + data).hexdigest()
        if digest != target["mask"]["logical_sha256"]:
            raise OctreeError("Repair Target mask identity mismatch")
        cells = _expand_region_set(data)
        prefixes = {code >> shift for code in cells}
        if len(prefixes) != 1:
            raise OctreeError("directional Repair Target mask crosses active-depth cells")
        prefix = next(iter(prefixes))
        profile = target["error_profile"]
        if profile == {
            "missing_surface_count": 1,
            "excess_surface_count": 0,
            "surface_error_count": 1,
        }:
            direction = "missing"
            support = missing
        elif profile == {
            "missing_surface_count": 0,
            "excess_surface_count": 1,
            "surface_error_count": 1,
        }:
            direction = "excess"
            support = excess
        else:
            raise OctreeError("directional Repair Target error profile is invalid")
        identity = (prefix, direction)
        if identity in observed or identity not in expected:
            raise OctreeError("directional Repair Target identity is invalid")
        observed.add(identity)
        expected_cells = {code for code in support if code >> shift == prefix}
        if cells != expected_cells:
            raise OctreeError("directional Repair Target mask has invalid support")
        snapshot = json.loads(data)
        if (
            target["mask"].get("storage_schema") != INTERIOR_REGION_SET_SCHEMA
            or target["mask"].get("region_count") != len(snapshot["regions"])
            or target.get("bounds_canonical") != _prefix_bounds(prefix, active_depth)
        ):
            raise OctreeError("directional Repair Target spatial evidence is invalid")
        component_key = _directional_component_key(active_depth, prefix, direction)
        component = {
            "component_key": component_key,
            "split_index": 0,
            "split_count": 1,
            "split_reason": "not_split",
        }
        if target.get("component") != component:
            raise OctreeError("directional Repair Target provenance is invalid")
        expected_key = _target_identity_key(
            source_step=target["source_step"],
            partition_profile=TARGET_PARTITION_PROFILE,
            mask_sha256=digest,
            missing_count=profile["missing_surface_count"],
            excess_count=profile["excess_surface_count"],
            component_key=component_key,
            split_index=0,
            split_count=1,
        )
        if target["target_key"] != expected_key:
            raise OctreeError("directional Repair Target target identity mismatch")
    if observed != expected:
        raise OctreeError("directional Repair Targets do not cover the net frontier")


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


def _invalid(path: str, detail: str) -> None:
    raise UnsupportedOrInvalidVoxBlameState(path=path, detail=detail)


__all__ = [
    "TARGET_PARTITION_PROFILE",
    "active_repair_depth",
    "inspect_repair_frontier",
    "page_repair_targets",
]
