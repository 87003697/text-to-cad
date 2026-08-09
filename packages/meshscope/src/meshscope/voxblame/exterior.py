"""Geometric canonical-cube clipping and signed exterior occupancy."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Iterable

import numpy as np

from meshscope.voxblame.contracts import (
    BOUNDARY_EPSILON,
    COORDINATE_CONTRACT,
    MAX_DEPTH,
    MIN_EXTERIOR_DIAGNOSTIC_GRID_DEPTH,
)
from meshscope.voxblame.errors import OctreeError
from meshscope.voxblame.voxelize import triangles_intersect_box


EXTERIOR_SNAPSHOT_SCHEMA = "voxblame.exterior-snapshot/1"
EXTERIOR_GRID_PROFILE = "signed_exterior_grid/1"
EXTERIOR_BOUNDARY_POLICY = "canonical-boundary-interior-closed-cells/1"
EXTERIOR_MAX_DIAGNOSTIC_CANDIDATE_CELLS = 65_536
_DIGEST_DOMAIN = b"voxblame.exterior-snapshot/1\0"
_DIRECTION_ORDER = ("-x", "+x", "-y", "+y", "-z", "+z")


@dataclass(frozen=True)
class ExteriorMeasurement:
    """Interior fragments plus immutable exact and diagnostic exterior evidence."""

    interior_triangles: np.ndarray
    snapshot: dict[str, Any]
    snapshot_bytes: bytes
    logical_sha256: str

    @property
    def exact(self) -> dict[str, Any]:
        return self.snapshot["exact"]

    @property
    def resolution(self) -> dict[str, Any]:
        return self.snapshot["resolution"]

    @property
    def surface_cell_count(self) -> int:
        return len(self.snapshot["cells"])


def measure_exterior_surface(triangles: np.ndarray) -> ExteriorMeasurement:
    """Clip triangles at the canonical cube and measure signed exterior cells."""

    geometry = np.ascontiguousarray(triangles, dtype=np.float64)
    if (
        geometry.ndim != 3
        or geometry.shape[1:] != (3, 3)
        or not len(geometry)
        or not np.all(np.isfinite(geometry))
    ):
        raise OctreeError("candidate triangles must contain finite geometry")
    log_areas = _triangle_log_doubled_areas(geometry)
    geometry = np.ascontiguousarray(
        geometry[log_areas > math.log(1e-15)]
    )
    if not len(geometry):
        raise OctreeError("mesh contains no non-degenerate triangles")

    interior: list[np.ndarray] = []
    exterior: list[np.ndarray] = []
    directions: set[str] = set()
    for triangle in geometry:
        triangle_directions = _outside_directions(triangle)
        if not triangle_directions:
            # The fixed epsilon is a containment policy, not an unmeasured
            # gap.  Snap its tolerance band onto the canonical boundary so a
            # surface wholly within that band remains interior occupancy.
            interior.append(np.clip(triangle, -0.5, 0.5))
            continue
        directions.update(triangle_directions)
        clipped_interior, clipped_exterior = _partition_triangle(triangle)
        interior.extend(clipped_interior)
        exterior.extend(clipped_exterior)

    interior_triangles = _triangle_array(interior)
    exterior_triangles = _triangle_array(exterior)
    if not len(exterior_triangles):
        return _snapshot(interior_triangles, exterior_triangles, directions, MAX_DEPTH)

    diagnostic_depth = _select_diagnostic_depth(exterior_triangles)
    return _snapshot(
        interior_triangles,
        exterior_triangles,
        directions,
        diagnostic_depth,
    )


def validate_exterior_measurement(value: ExteriorMeasurement) -> None:
    """Fail closed when exact, diagnostic, byte, or identity evidence conflicts."""

    try:
        decoded = json.loads(value.snapshot_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OctreeError("exterior snapshot bytes are invalid") from exc
    if decoded != value.snapshot:
        raise OctreeError("exterior snapshot bytes conflict with measured evidence")
    expected_digest = hashlib.sha256(
        _DIGEST_DOMAIN + value.snapshot_bytes
    ).hexdigest()
    if expected_digest != value.logical_sha256:
        raise OctreeError("exterior snapshot identity conflict")
    exact = value.exact
    resolution = value.resolution
    present = exact["surface_present"]
    if present is not bool(value.snapshot["cells"]):
        raise OctreeError("exterior containment conflicts with diagnostic occupancy")
    if present is not bool(exact["outside_directions"]):
        raise OctreeError("exterior containment conflicts with overrun directions")
    depth = resolution["diagnostic_grid_depth"]
    if resolution["coarsened"] is not (depth < MAX_DEPTH):
        raise OctreeError("exterior coarsening metadata conflict")


def _partition_triangle(
    triangle: np.ndarray,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    active = [np.asarray(triangle, dtype=np.float64)]
    exterior_polygons: list[np.ndarray] = []
    for axis, bound, keep_less_equal in (
        (0, -0.5, False),
        (0, 0.5, True),
        (1, -0.5, False),
        (1, 0.5, True),
        (2, -0.5, False),
        (2, 0.5, True),
    ):
        next_active: list[np.ndarray] = []
        for polygon in active:
            inside, outside = _split_polygon(
                polygon,
                axis=axis,
                bound=bound,
                keep_less_equal=keep_less_equal,
            )
            if len(inside) >= 3:
                next_active.append(inside)
            if len(outside) >= 3:
                exterior_polygons.append(outside)
        active = next_active
        if not active:
            break
    return _triangulate_polygons(active), _triangulate_polygons(exterior_polygons)


def _split_polygon(
    polygon: np.ndarray,
    *,
    axis: int,
    bound: float,
    keep_less_equal: bool,
) -> tuple[np.ndarray, np.ndarray]:
    inside: list[np.ndarray] = []
    outside: list[np.ndarray] = []

    def signed(point: np.ndarray) -> float:
        value = float(point[axis] - bound)
        return value if keep_less_equal else -value

    previous = polygon[-1]
    previous_distance = signed(previous)
    previous_inside = previous_distance <= 0.0
    for current in polygon:
        current_distance = signed(current)
        current_inside = current_distance <= 0.0
        if current_inside != previous_inside:
            distance_scale = max(abs(previous_distance), abs(current_distance))
            previous_weight = abs(previous_distance) / distance_scale
            current_weight = abs(current_distance) / distance_scale
            ratio = previous_weight / (previous_weight + current_weight)
            intersection = (1.0 - ratio) * previous + ratio * current
            inside.append(intersection)
            outside.append(intersection)
        if current_inside:
            inside.append(current)
        else:
            outside.append(current)
        previous = current
        previous_distance = current_distance
        previous_inside = current_inside
    return _deduplicate_polygon(inside), _deduplicate_polygon(outside)


def _deduplicate_polygon(vertices: Iterable[np.ndarray]) -> np.ndarray:
    result: list[np.ndarray] = []
    for vertex in vertices:
        value = np.asarray(vertex, dtype=np.float64)
        if not result or not np.array_equal(result[-1], value):
            result.append(value)
    if len(result) > 1 and np.array_equal(result[0], result[-1]):
        result.pop()
    if not result:
        return np.empty((0, 3), dtype=np.float64)
    return np.asarray(result, dtype=np.float64)


def _triangulate_polygons(polygons: Iterable[np.ndarray]) -> list[np.ndarray]:
    triangles: list[np.ndarray] = []
    for polygon in polygons:
        for index in range(1, len(polygon) - 1):
            triangle = np.asarray(
                [polygon[0], polygon[index], polygon[index + 1]],
                dtype=np.float64,
            )
            # A fragment may be arbitrarily thin when it crosses the fixed
            # boundary just beyond the canonical epsilon.  It is still true
            # exterior surface and therefore cannot use the input-mesh
            # degeneracy tolerance as a deletion threshold.
            if math.isfinite(_triangle_log_doubled_areas(triangle[None, :, :])[0]):
                triangles.append(triangle)
    return triangles


def _triangle_array(triangles: Iterable[np.ndarray]) -> np.ndarray:
    values = list(triangles)
    if not values:
        return np.empty((0, 3, 3), dtype=np.float64)
    return np.ascontiguousarray(values, dtype=np.float64)


def _outside_directions(triangle: np.ndarray) -> set[str]:
    directions = set()
    lower = -0.5 - BOUNDARY_EPSILON
    upper = 0.5 + BOUNDARY_EPSILON
    for axis, name in enumerate("xyz"):
        if np.any(triangle[:, axis] < lower):
            directions.add(f"-{name}")
        if np.any(triangle[:, axis] > upper):
            directions.add(f"+{name}")
    return directions


def _select_diagnostic_depth(triangles: np.ndarray) -> int:
    for depth in range(
        MAX_DEPTH,
        MIN_EXTERIOR_DIAGNOSTIC_GRID_DEPTH - 1,
        -1,
    ):
        if _candidate_cell_count(triangles, depth) <= (
            EXTERIOR_MAX_DIAGNOSTIC_CANDIDATE_CELLS
        ):
            return depth
    raise OctreeError("exterior geometry exceeds the diagnostic grid range")


def _candidate_cell_count(triangles: np.ndarray, depth: int) -> int:
    size = math.ldexp(1.0, -depth)
    total = 0
    for triangle in triangles:
        try:
            lower, upper = _diagnostic_index_bounds(triangle, size)
        except OverflowError:
            return EXTERIOR_MAX_DIAGNOSTIC_CANDIDATE_CELLS + 1
        spans = [
            int(upper[axis]) - int(lower[axis]) + 1
            for axis in range(3)
        ]
        total += math.prod(spans)
        if total > EXTERIOR_MAX_DIAGNOSTIC_CANDIDATE_CELLS:
            break
    return total


def _diagnostic_cells(triangles: np.ndarray, depth: int) -> list[list[int]]:
    size = math.ldexp(1.0, -depth)
    half = size / 2.0
    occupied: set[tuple[int, int, int]] = set()
    for triangle in triangles:
        lower, upper = _diagnostic_index_bounds(triangle, size)
        axes = [
            np.arange(lower[axis], upper[axis] + 1, dtype=np.int64)
            for axis in range(3)
        ]
        indices = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
        cell_minimum = -0.5 + indices.astype(np.float64) * size
        cell_maximum = cell_minimum + size
        exterior_domain = np.any(
            (cell_minimum < -0.5) | (cell_maximum > 0.5),
            axis=1,
        )
        indices = indices[exterior_domain]
        if not len(indices):
            continue
        centers = -0.5 + (indices.astype(np.float64) + 0.5) * size
        translated = triangle[None, :, :] - centers[:, None, :]
        sat_scale = max(half, float(np.max(np.abs(translated))))
        hits = triangles_intersect_box(
            translated / sat_scale,
            np.zeros(3),
            half / sat_scale,
            tolerance=0.0,
        )
        occupied.update(tuple(int(item) for item in index) for index in indices[hits])
    return [list(cell) for cell in sorted(occupied)]


def _diagnostic_index_bounds(
    triangle: np.ndarray,
    cell_size: float,
) -> tuple[np.ndarray, np.ndarray]:
    minimum = triangle.min(axis=0)
    maximum = triangle.max(axis=0)
    int64 = np.iinfo(np.int64)
    lower: list[int] = []
    upper: list[int] = []
    for axis in range(3):
        low = math.floor((float(minimum[axis]) + 0.5) / cell_size) - 1
        high = math.floor((float(maximum[axis]) + 0.5) / cell_size) + 1
        if low < int64.min or high > int64.max:
            raise OverflowError("diagnostic grid index exceeds int64")
        lower.append(low)
        upper.append(high)
    return np.asarray(lower, dtype=np.int64), np.asarray(upper, dtype=np.int64)


def _snapshot(
    interior_triangles: np.ndarray,
    exterior_triangles: np.ndarray,
    directions: set[str],
    diagnostic_depth: int,
) -> ExteriorMeasurement:
    present = bool(len(exterior_triangles))
    cells = _diagnostic_cells(exterior_triangles, diagnostic_depth) if present else []
    exact = _exact_facts(exterior_triangles, directions) if present else {
        "surface_present": False,
        "bounds_canonical": None,
        "centroid_canonical": None,
        "nearest_overrun": None,
        "farthest_overrun": None,
        "outside_directions": [],
    }
    resolution = {
        "profile": EXTERIOR_GRID_PROFILE,
        "diagnostic_grid_depth": diagnostic_depth,
        "cell_size_canonical": math.ldexp(1.0, -diagnostic_depth),
        "origin_canonical": [-0.5, -0.5, -0.5],
        "index_to_canonical": "center=origin+(index+0.5)*cell_size",
        "boundary_policy": EXTERIOR_BOUNDARY_POLICY,
        "coarsened": diagnostic_depth < MAX_DEPTH,
    }
    snapshot = {
        "schema": EXTERIOR_SNAPSHOT_SCHEMA,
        "coordinate_contract": COORDINATE_CONTRACT,
        "boundary_epsilon": BOUNDARY_EPSILON,
        "exact": exact,
        "resolution": resolution,
        "cells": cells,
    }
    data = _json_bytes(snapshot)
    return ExteriorMeasurement(
        interior_triangles=interior_triangles,
        snapshot=snapshot,
        snapshot_bytes=data,
        logical_sha256=hashlib.sha256(_DIGEST_DOMAIN + data).hexdigest(),
    )


def _exact_facts(triangles: np.ndarray, directions: set[str]) -> dict[str, Any]:
    vertices = triangles.reshape(-1, 3)
    log_areas = _triangle_log_doubled_areas(triangles)
    weights = np.exp(log_areas - float(log_areas.max()))
    centroid_values = []
    for triangle in triangles:
        coordinate_scale = max(1.0, float(np.max(np.abs(triangle))))
        centroid_values.append(
            (triangle / coordinate_scale).mean(axis=0) * coordinate_scale
        )
    centroids = np.asarray(centroid_values, dtype=np.float64)
    centroid = np.sum(centroids * (weights / weights.sum())[:, None], axis=0)
    overruns = np.max(np.maximum(np.abs(vertices) - 0.5, 0.0), axis=1)
    farthest_overrun = float(overruns.max())
    return {
        "surface_present": True,
        "bounds_canonical": {
            "min": vertices.min(axis=0).tolist(),
            "max": vertices.max(axis=0).tolist(),
        },
        "centroid_canonical": centroid.tolist(),
        "nearest_overrun": _nearest_overrun(triangles, farthest_overrun),
        "farthest_overrun": farthest_overrun,
        "outside_directions": [
            direction for direction in _DIRECTION_ORDER if direction in directions
        ],
    }


def _nearest_overrun(triangles: np.ndarray, farthest_overrun: float) -> float:
    """Return the minimum L-infinity expansion needed to touch the surface."""

    scale = max(1.0, float(np.max(np.abs(triangles))))
    scaled_triangles = triangles / scale
    canonical_half = 0.5 / scale
    origin = np.zeros(3, dtype=np.float64)
    if np.any(
        triangles_intersect_box(
            scaled_triangles,
            origin,
            canonical_half,
            tolerance=0.0,
        )
    ):
        return 0.0
    lower = 0.0
    upper = farthest_overrun / scale
    iterations = min(1100, max(128, math.frexp(scale)[1] + 80))
    for _ in range(iterations):
        middle = (lower + upper) / 2.0
        if np.any(
            triangles_intersect_box(
                scaled_triangles,
                origin,
                canonical_half + middle,
                tolerance=0.0,
            )
        ):
            upper = middle
        else:
            lower = middle
    return upper * scale


def _triangle_log_doubled_areas(triangles: np.ndarray) -> np.ndarray:
    """Compute log doubled areas without overflow or global-scale underflow."""

    result = np.full(len(triangles), -math.inf, dtype=np.float64)
    for index, triangle in enumerate(triangles):
        scale = float(np.max(np.abs(triangle)))
        if not math.isfinite(scale) or scale == 0.0:
            continue
        scaled = triangle / scale
        edge_a = scaled[1] - scaled[0]
        edge_b = scaled[2] - scaled[0]
        normalized_area = float(
            np.linalg.norm(np.cross(edge_a, edge_b))
        )
        if normalized_area > 0.0:
            result[index] = math.log(normalized_area) + 2.0 * math.log(scale)
    return result


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


__all__ = [
    "EXTERIOR_BOUNDARY_POLICY",
    "EXTERIOR_GRID_PROFILE",
    "EXTERIOR_SNAPSHOT_SCHEMA",
    "ExteriorMeasurement",
    "measure_exterior_surface",
    "validate_exterior_measurement",
]
