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
    edges_a = geometry[:, 1] - geometry[:, 0]
    edges_b = geometry[:, 2] - geometry[:, 0]
    doubled_areas = np.linalg.norm(np.cross(edges_a, edges_b), axis=1)
    geometry = np.ascontiguousarray(geometry[doubled_areas > 1e-15])
    if not len(geometry):
        raise OctreeError("mesh contains no non-degenerate triangles")

    interior: list[np.ndarray] = []
    exterior: list[np.ndarray] = []
    directions: set[str] = set()
    for triangle in geometry:
        triangle_directions = _outside_directions(triangle)
        if not triangle_directions:
            interior.append(triangle)
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
            ratio = previous_distance / (previous_distance - current_distance)
            intersection = previous + ratio * (current - previous)
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
            doubled_area = np.linalg.norm(
                np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
            )
            # A fragment may be arbitrarily thin when it crosses the fixed
            # boundary just beyond the canonical epsilon.  It is still true
            # exterior surface and therefore cannot use the input-mesh
            # degeneracy tolerance as a deletion threshold.
            if doubled_area > 0.0:
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
    for depth in range(MAX_DEPTH, 0, -1):
        if _candidate_cell_count(triangles, depth) <= (
            EXTERIOR_MAX_DIAGNOSTIC_CANDIDATE_CELLS
        ):
            return depth
    return 1


def _candidate_cell_count(triangles: np.ndarray, depth: int) -> int:
    size = 1.0 / (1 << depth)
    total = 0
    for triangle in triangles:
        lower = np.floor((triangle.min(axis=0) + 0.5) / size).astype(np.int64) - 1
        upper = np.floor((triangle.max(axis=0) + 0.5) / size).astype(np.int64) + 1
        spans = upper - lower + 1
        total += math.prod(int(item) for item in spans)
        if total > EXTERIOR_MAX_DIAGNOSTIC_CANDIDATE_CELLS:
            break
    return total


def _diagnostic_cells(triangles: np.ndarray, depth: int) -> list[list[int]]:
    size = 1.0 / (1 << depth)
    half = size / 2.0
    canonical_span = 1 << depth
    occupied: set[tuple[int, int, int]] = set()
    for triangle in triangles:
        lower = np.floor((triangle.min(axis=0) + 0.5) / size).astype(np.int64) - 1
        upper = np.floor((triangle.max(axis=0) + 0.5) / size).astype(np.int64) + 1
        axes = [
            np.arange(lower[axis], upper[axis] + 1, dtype=np.int64)
            for axis in range(3)
        ]
        indices = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
        exterior_domain = np.any(
            (indices < 0) | (indices >= canonical_span), axis=1
        )
        indices = indices[exterior_domain]
        if not len(indices):
            continue
        centers = -0.5 + (indices.astype(np.float64) + 0.5) * size
        translated = triangle[None, :, :] - centers[:, None, :]
        hits = triangles_intersect_box(translated, np.zeros(3), half)
        occupied.update(tuple(int(item) for item in index) for index in indices[hits])
    return [list(cell) for cell in sorted(occupied)]


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
        "cell_size_canonical": 1.0 / (1 << diagnostic_depth),
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
    areas = np.linalg.norm(
        np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]),
        axis=1,
    )
    centroids = triangles.mean(axis=1)
    centroid = np.average(centroids, axis=0, weights=areas)
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

    origin = np.zeros(3, dtype=np.float64)
    if np.any(triangles_intersect_box(triangles, origin, 0.5)):
        return 0.0
    lower = 0.0
    upper = farthest_overrun
    for _ in range(64):
        middle = (lower + upper) / 2.0
        if np.any(triangles_intersect_box(triangles, origin, 0.5 + middle)):
            upper = middle
        else:
            lower = middle
    return upper


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
]
