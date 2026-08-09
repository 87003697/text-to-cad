"""Deterministic complete Repair Target publication and paging."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable, TYPE_CHECKING

from meshscope.voxblame.contracts import MAX_DEPTH
from meshscope.voxblame.codec import read_surface_tree
from meshscope.voxblame.errors import OctreeError
from meshscope.voxblame.grading import decode_octant_prefix
from meshscope.voxblame.tree import SurfaceTree

if TYPE_CHECKING:
    from meshscope.voxblame.exterior import ExteriorMeasurement


TARGET_PARTITION_PROFILE = "repair_target_partition/1"
TARGET_ORDERING_PROFILE = "repair_target_display/1"
INTERIOR_REGION_SET_SCHEMA = "octree_region_set/1"
EXTERIOR_GRID_REGION_SET_SCHEMA = "exterior_grid_region_set/1"
TARGET_PAGE_SIZE = 8
TARGET_SPLIT_MAX_CELLS = 4_096
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
class RepairTarget:
    """One exact source-step-local interior error partition."""

    target_key: str
    source_step: int
    component_key: str
    split_index: int
    split_count: int
    split_reason: str
    missing_codes: frozenset[int]
    excess_codes: frozenset[int]
    mask_codes: frozenset[int]


@dataclass(frozen=True)
class RepairTargetPartition:
    """Frozen report metadata plus canonical mask artifact bytes."""

    targets: tuple[RepairTarget, ...]
    report: dict[str, Any]
    mask_bytes: dict[str, bytes]


@dataclass(frozen=True)
class _InteriorTarget:
    cells: frozenset[int]
    missing_count: int
    excess_count: int
    bounds: dict[str, list[float]]
    mask: dict[str, Any]
    mask_bytes: bytes
    component_key: str
    split_index: int
    split_count: int
    split_reason: str
    coarse_coverage: int


def partition_repair_targets(
    missing_tree: SurfaceTree,
    excess_tree: SurfaceTree,
    *,
    source_step: int | None = None,
    step: int | None = None,
    step_root: str | None = None,
    exterior: ExteriorMeasurement | None = None,
) -> RepairTargetPartition:
    """Partition all depth-8 interior error cells with frozen 18-connectivity."""

    if source_step is None:
        source_step = step
    elif step is not None and step != source_step:
        raise OctreeError("Repair Target source step is inconsistent")
    if (
        not isinstance(source_step, int)
        or isinstance(source_step, bool)
        or source_step < 0
    ):
        raise OctreeError("Repair Target source step must be non-negative")
    if step_root is None:
        step_root = f"voxblame/steps/{source_step:06d}"
    if missing_tree.max_depth != MAX_DEPTH or excess_tree.max_depth != MAX_DEPTH:
        raise OctreeError("Repair Targets require depth-8 error evidence")
    missing = {int(code) for code in missing_tree.iter_leaf_codes()}
    excess = {int(code) for code in excess_tree.iter_leaf_codes()}
    directions = {code: _MISSING for code in missing}
    for code in excess:
        directions[code] = directions.get(code, 0) | _EXCESS

    candidates: list[_InteriorTarget] = []
    for component in _connected_components(directions):
        component_key = _component_key(component, directions)
        splits = _split_component(component)
        split_count = len(splits)
        for split_index, cells in enumerate(splits):
            mask_bytes, mask_digest, region_count = _region_set(cells)
            candidates.append(
                _InteriorTarget(
                    cells=frozenset(cells),
                    missing_count=sum(code in missing for code in cells),
                    excess_count=sum(code in excess for code in cells),
                    bounds=_canonical_bounds(cells),
                    mask={
                        "storage_schema": INTERIOR_REGION_SET_SCHEMA,
                        "logical_sha256": mask_digest,
                        "region_count": region_count,
                    },
                    mask_bytes=mask_bytes,
                    component_key=component_key,
                    split_index=split_index,
                    split_count=split_count,
                    split_reason=(
                        "not_split"
                        if split_count == 1
                        else "coarse_octree_locality"
                    ),
                    coarse_coverage=len(
                        {
                            code >> (3 * (MAX_DEPTH - TARGET_SPLIT_DEPTH))
                            for code in cells
                        }
                    ),
                )
            )

    candidates.sort(key=_display_order)
    ordered: list[dict[str, Any]] = []
    public_targets: list[RepairTarget] = []
    mask_artifacts: dict[str, bytes] = {}
    for display_rank, candidate in enumerate(candidates):
        target_key = _interior_target_key(source_step, candidate)
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
                    "missing_surface_count": candidate.missing_count,
                    "excess_surface_count": candidate.excess_count,
                    "surface_error_count": (
                        candidate.missing_count + candidate.excess_count
                    ),
                },
                "mask": mask,
                "component": {
                    "component_key": candidate.component_key,
                    "split_index": candidate.split_index,
                    "split_count": candidate.split_count,
                    "split_reason": candidate.split_reason,
                },
                "exterior": None,
            }
        )
        mask_artifacts[file_name] = candidate.mask_bytes
        public_targets.append(
            RepairTarget(
                target_key=target_key,
                source_step=source_step,
                component_key=candidate.component_key,
                split_index=candidate.split_index,
                split_count=candidate.split_count,
                split_reason=candidate.split_reason,
                missing_codes=frozenset(candidate.cells & missing),
                excess_codes=frozenset(candidate.cells & excess),
                mask_codes=candidate.cells,
            )
        )

    if exterior is not None and exterior.surface_cell_count:
        exterior_bytes, exterior_digest = _exterior_region_set(exterior)
        file_name = f"exterior-{exterior_digest}.vbregions"
        component_key = f"exterior-component-{exterior_digest[:16]}"
        target_key = _target_identity_key(
            source_step=source_step,
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
        if len(ordered) > TARGET_PAGE_SIZE:
            ordered.insert(TARGET_PAGE_SIZE - 1, ordered.pop())

    for display_rank, target in enumerate(ordered):
        target["display_rank"] = display_rank

    _validate_interior_partition(
        [target for target in ordered if target["kind"] == "interior"],
        mask_artifacts,
        missing,
        excess,
    )
    return RepairTargetPartition(
        targets=tuple(public_targets),
        report={
            "ordering_profile": TARGET_ORDERING_PROFILE,
            "total": len(ordered),
            "ordered_targets": ordered,
        },
        mask_bytes=mask_artifacts,
    )


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

    if not isinstance(step, int) or isinstance(step, bool) or step < 0:
        raise OctreeError("Repair Target step must be a non-negative integer")
    root = Path(workspace)
    report_path = root / "steps" / f"{step:06d}" / "measurement.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OctreeError("Measured Step Repair Target report is unavailable") from exc
    if report.get("schema") != "voxblame.measurement/1" or report.get("step") != step:
        raise OctreeError("Measured Step Repair Target report is invalid")
    targets = report.get("repair_targets")
    if (
        not isinstance(targets, dict)
        or targets.get("ordering_profile") != TARGET_ORDERING_PROFILE
        or targets.get("total") != len(targets.get("ordered_targets", ()))
    ):
        raise OctreeError("Measured Step Repair Target report is invalid")
    _validate_published_partition(root, step, report)
    return repair_target_page(targets, offset=offset)


def _validate_published_partition(
    workspace: Path, step: int, measurement: dict[str, Any]
) -> None:
    step_root = workspace / "steps" / f"{step:06d}"
    missing_tree = read_surface_tree(step_root / "missing-depth8.vbsvo")
    excess_tree = read_surface_tree(step_root / "excess-depth8.vbsvo")
    missing = {int(code) for code in missing_tree.iter_leaf_codes()}
    excess = {int(code) for code in excess_tree.iter_leaf_codes()}
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
    _validate_interior_partition(interior, artifacts, missing, excess)
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
    if mask != expected_mask:
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
        or measurement["exterior_surface"]["surface_cell_count"] != surface_count
    ):
        raise OctreeError("exterior Repair Target evidence is inconsistent")
    component = target["component"]
    expected_key = _target_identity_key(
        source_step=step,
        mask_sha256=digest,
        missing_count=0,
        excess_count=surface_count,
        component_key=component["component_key"],
        split_index=component["split_index"],
        split_count=component["split_count"],
    )
    if target["target_key"] != expected_key:
        raise OctreeError("exterior Repair Target identity mismatch")


def _connected_components(directions: dict[int, int]) -> list[set[int]]:
    coordinates = {
        decode_octant_prefix(code, MAX_DEPTH): code for code in directions
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
            x, y, z = decode_octant_prefix(code, MAX_DEPTH)
            for dx, dy, dz in _NEIGHBOR_OFFSETS:
                neighbor = coordinates.get((x + dx, y + dy, z + dz))
                if neighbor is None or neighbor not in remaining:
                    continue
                remaining.remove(neighbor)
                component.add(neighbor)
                pending.append(neighbor)
        components.append(component)
    return components


def _split_component(component: set[int]) -> list[set[int]]:
    if len(component) <= TARGET_SPLIT_MAX_CELLS:
        return [component]

    def split(cells: set[int], depth: int) -> list[set[int]]:
        if len(cells) <= TARGET_SPLIT_MAX_CELLS or depth == MAX_DEPTH:
            return [cells]
        child_depth = max(depth + 1, TARGET_SPLIT_DEPTH)
        shift = 3 * (MAX_DEPTH - child_depth)
        buckets: dict[int, set[int]] = {}
        for code in cells:
            buckets.setdefault(code >> shift, set()).add(code)
        result: list[set[int]] = []
        for prefix in sorted(buckets):
            connected_chunks = _connected_components(
                {code: _MISSING for code in buckets[prefix]}
            )
            for connected in connected_chunks:
                result.extend(split(connected, child_depth))
        return result

    return split(component, TARGET_SPLIT_DEPTH - 1)


def _component_key(component: set[int], directions: dict[int, int]) -> str:
    payload = {
        "profile": TARGET_PARTITION_PROFILE,
        "cells": [[code, directions[code]] for code in sorted(component)],
    }
    digest = hashlib.sha256(_json_bytes(payload)).hexdigest()
    return f"component-{digest[:16]}"


def _interior_target_key(
    source_step: int, target: _InteriorTarget
) -> str:
    return _target_identity_key(
        source_step=source_step,
        mask_sha256=target.mask["logical_sha256"],
        missing_count=target.missing_count,
        excess_count=target.excess_count,
        component_key=target.component_key,
        split_index=target.split_index,
        split_count=target.split_count,
    )


def _target_identity_key(
    *,
    source_step: int,
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
        "partition_profile": TARGET_PARTITION_PROFILE,
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
        snapshot.get("schema") != INTERIOR_REGION_SET_SCHEMA
        or snapshot.get("max_depth") != MAX_DEPTH
        or not isinstance(snapshot.get("regions"), list)
    ):
        raise OctreeError("Repair Target mask is unsupported")
    result: set[int] = set()
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
        expanded = range(prefix << remaining, (prefix + 1) << remaining)
        if result.intersection(expanded):
            raise OctreeError("Repair Target mask regions overlap")
        result.update(expanded)
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


def _display_order(target: _InteriorTarget) -> tuple[Any, ...]:
    return (
        -target.coarse_coverage,
        -(target.missing_count + target.excess_count),
        *target.bounds["min"],
        target.mask["logical_sha256"],
    )


def _validate_interior_partition(
    targets: list[dict[str, Any]],
    artifacts: dict[str, bytes],
    missing: set[int],
    excess: set[int],
) -> None:
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
        if observed.intersection(cells):
            raise OctreeError("Repair Target masks overlap")
        observed.update(cells)
        profile = target["error_profile"]
        if (
            profile["missing_surface_count"] != len(cells & missing)
            or profile["excess_surface_count"] != len(cells & excess)
            or profile["surface_error_count"] != len(cells)
        ):
            raise OctreeError("Repair Target error profile conflicts with its mask")
        component = target["component"]
        expected_key = _target_identity_key(
            source_step=target["source_step"],
            mask_sha256=target["mask"]["logical_sha256"],
            missing_count=profile["missing_surface_count"],
            excess_count=profile["excess_surface_count"],
            component_key=component["component_key"],
            split_index=component["split_index"],
            split_count=component["split_count"],
        )
        if target["target_key"] != expected_key:
            raise OctreeError("Repair Target target identity mismatch")
    if observed != missing | excess:
        raise OctreeError("Repair Targets do not cover the complete error set")


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


__all__ = [
    "EXTERIOR_GRID_REGION_SET_SCHEMA",
    "INTERIOR_REGION_SET_SCHEMA",
    "RepairTarget",
    "RepairTargetPartition",
    "TARGET_ORDERING_PROFILE",
    "TARGET_PAGE_SIZE",
    "TARGET_PARTITION_PROFILE",
    "TARGET_SPLIT_DEPTH",
    "TARGET_SPLIT_MAX_CELLS",
    "partition_repair_targets",
    "page_repair_targets",
    "repair_target_page",
]
