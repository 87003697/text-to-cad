"""Flat Morton leaf oracle used only by VoxBlame tests.

Production voxelization, persistence, and grading must not import this module.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

import numpy as np
import trimesh

from meshscope.io import load
from meshscope.voxblame.errors import OctreeError
from meshscope.voxblame.frame import (
    CanonicalFrame,
    LATTICE_MAX as _LATTICE_MAX,
    LATTICE_MIN as _LATTICE_MIN,
    mesh_vertices,
)
from meshscope.voxblame.grading import ErrorCell
from meshscope.voxblame.tree import validate_depth as _validate_tree_depth

_UINT64_LE = np.dtype("<u8")

def _validate_depth(depth: int) -> None:
    try:
        _validate_tree_depth(depth)
    except ValueError as exc:
        raise OctreeError(str(exc)) from exc


def _load_mesh(
    source: trimesh.Trimesh | str | Path,
    label: str,
) -> trimesh.Trimesh:
    if isinstance(source, trimesh.Trimesh):
        mesh = source.copy()
    else:
        mesh = load(source)
    mesh_vertices(mesh, label)
    return mesh

def morton_encode(x: int, y: int, z: int, depth: int) -> int:
    """Encode coordinates with child bits ordered as ``x, y, z``."""
    _validate_depth(depth)
    limit = 1 << depth
    coordinates = (int(x), int(y), int(z))
    if any(value < 0 or value >= limit for value in coordinates):
        raise OctreeError(f"Morton coordinates must lie in [0, {limit})")
    code = 0
    for shift in range(depth - 1, -1, -1):
        child = (
            (((coordinates[0] >> shift) & 1) << 2)
            | (((coordinates[1] >> shift) & 1) << 1)
            | ((coordinates[2] >> shift) & 1)
        )
        code = (code << 3) | child
    return code


def morton_decode(code: int, depth: int) -> tuple[int, int, int]:
    if depth == 0:
        if int(code) != 0:
            raise OctreeError("Morton code lies outside its depth")
        return (0, 0, 0)
    _validate_depth(depth)
    value = int(code)
    if value < 0 or value >= (1 << (3 * depth)):
        raise OctreeError("Morton code lies outside its depth")
    coordinates = [0, 0, 0]
    for shift in range(depth - 1, -1, -1):
        child = (value >> (3 * shift)) & 7
        coordinates[0] = (coordinates[0] << 1) | ((child >> 2) & 1)
        coordinates[1] = (coordinates[1] << 1) | ((child >> 1) & 1)
        coordinates[2] = (coordinates[2] << 1) | (child & 1)
    return tuple(coordinates)


def canonicalize_codes(values: Iterable[int] | np.ndarray, max_depth: int | None = None) -> np.ndarray:
    """Return sorted, unique, one-dimensional little-endian uint64 codes."""
    array = np.asarray(values)
    if array.ndim != 1:
        raise OctreeError("Morton codes must be a one-dimensional array")
    if len(array) == 0:
        return np.array([], dtype=_UINT64_LE)
    if array.dtype.kind in {"f", "c", "O", "S", "U", "V"}:
        raise OctreeError("Morton codes must be integers")
    if array.dtype.kind == "i" and len(array) and np.any(array < 0):
        raise OctreeError("Morton codes cannot be negative")
    result = np.unique(array.astype(_UINT64_LE, copy=False)).astype(_UINT64_LE, copy=False)
    if max_depth is not None:
        _validate_depth(max_depth)
        if len(result) and int(result[-1]) >= (1 << (3 * max_depth)):
            raise OctreeError("Morton code exceeds max_depth")
    return result


def validate_codes(codes: np.ndarray, max_depth: int | None = None) -> np.ndarray:
    """Validate the canonical in-memory and on-disk Morton representation."""
    if not isinstance(codes, np.ndarray):
        raise OctreeError("Morton codes must be a NumPy array")
    if codes.ndim != 1:
        raise OctreeError("Morton codes must be one-dimensional")
    if codes.dtype.str != "<u8":
        raise OctreeError("Morton codes dtype must be little-endian uint64 (<u8)")
    if len(codes) > 1 and np.any(codes[1:] <= codes[:-1]):
        raise OctreeError("Morton codes must be strictly increasing and unique")
    if max_depth is not None:
        _validate_depth(max_depth)
        if len(codes) and int(codes[-1]) >= (1 << (3 * max_depth)):
            raise OctreeError("Morton code exceeds max_depth")
    return codes


def codes_digest(codes: np.ndarray) -> str:
    canonical = validate_codes(codes)
    return hashlib.sha256(np.ascontiguousarray(canonical).tobytes()).hexdigest()


def prefix_interval(prefix: int, depth: int, max_depth: int) -> tuple[int, int]:
    _validate_depth(max_depth)
    if depth < 0 or depth > max_depth:
        raise OctreeError("prefix depth lies outside max_depth")
    if prefix < 0 or prefix >= (1 << (3 * depth)):
        raise OctreeError("prefix lies outside its depth")
    remaining = 3 * (max_depth - depth)
    return prefix << remaining, (prefix + 1) << remaining


def prefix_occupied(codes: np.ndarray, prefix: int, depth: int, max_depth: int) -> bool:
    lower, upper = prefix_interval(prefix, depth, max_depth)
    left = int(np.searchsorted(codes, np.uint64(lower), side="left"))
    if left >= len(codes):
        return False
    return int(codes[left]) < upper


def grade_codes(
    reference_codes: np.ndarray,
    candidate_codes: np.ndarray,
    max_depth: int,
    *,
    visit_counts: dict[str, int] | None = None,
) -> list[ErrorCell]:
    """Return flat-oracle first mismatches."""
    reference = validate_codes(reference_codes, max_depth)
    candidate = validate_codes(candidate_codes, max_depth)
    errors: list[ErrorCell] = []

    def visit(prefix: int, depth: int) -> None:
        if visit_counts is not None:
            visit_counts["visited"] = visit_counts.get("visited", 0) + 1
        reference_valid = prefix_occupied(reference, prefix, depth, max_depth)
        candidate_valid = prefix_occupied(candidate, prefix, depth, max_depth)
        if reference_valid != candidate_valid:
            errors.append(
                ErrorCell(
                    prefix,
                    depth,
                    "missing" if reference_valid else "excess",
                )
            )
            return
        if not reference_valid or depth == max_depth:
            return
        for child in range(8):
            visit((prefix << 3) | child, depth + 1)

    visit(0, 0)
    return errors


def build_surface_codes(
    mesh: trimesh.Trimesh | str | Path,
    frame: CanonicalFrame,
    max_depth: int,
) -> np.ndarray:
    """Conservatively voxelize in-frame triangle surfaces into Morton codes.

    The reference owns the canonical lattice. Geometry outside that lattice is
    ignored rather than treated as an invalid mesh; downstream grading reports
    the resulting missing/excess surface error.
    """
    _validate_depth(max_depth)
    resolved = _load_mesh(mesh, "mesh")
    lattice_triangles = frame.to_lattice(np.asarray(resolved.triangles, dtype=np.float64))
    edges_a = lattice_triangles[:, 1] - lattice_triangles[:, 0]
    edges_b = lattice_triangles[:, 2] - lattice_triangles[:, 0]
    doubled_areas = np.linalg.norm(np.cross(edges_a, edges_b), axis=1)
    triangles = lattice_triangles[doubled_areas > 1e-15]
    if len(triangles) == 0:
        raise OctreeError("mesh contains no non-degenerate triangles")

    resolution = 1 << max_depth
    cell_size = 1.0 / resolution
    half = cell_size / 2.0
    emitted: list[np.ndarray] = []
    for triangle in triangles:
        triangle_min = triangle.min(axis=0)
        triangle_max = triangle.max(axis=0)
        if np.any(triangle_max < _LATTICE_MIN) or np.any(
            triangle_min > _LATTICE_MAX
        ):
            continue
        # Clamp only the enumeration bounds, not the triangle itself. The SAT
        # below still tests the original geometry against in-frame cells.
        scaled_min = (
            np.maximum(triangle_min, _LATTICE_MIN) - _LATTICE_MIN
        ) * resolution
        scaled_max = (
            np.minimum(triangle_max, _LATTICE_MAX) - _LATTICE_MIN
        ) * resolution
        starts = np.ceil(scaled_min - 1.0).astype(np.int64)
        stops = np.floor(scaled_max).astype(np.int64)
        starts = np.clip(starts, 0, resolution - 1)
        stops = np.clip(stops, 0, resolution - 1)
        if np.any(stops < starts):
            continue
        candidate_count = int(np.prod(stops - starts + 1, dtype=np.int64))
        if candidate_count <= 0:
            continue
        for offset in range(0, candidate_count, 200_000):
            count = min(200_000, candidate_count - offset)
            linear = np.arange(offset, offset + count, dtype=np.int64)
            spans = stops - starts + 1
            z = starts[2] + linear % spans[2]
            linear //= spans[2]
            y = starts[1] + linear % spans[1]
            x = starts[0] + linear // spans[1]
            coordinates = np.column_stack((x, y, z))
            centers = _LATTICE_MIN + (coordinates.astype(np.float64) + 0.5) * cell_size
            intersects = _triangle_intersects_boxes(triangle, centers, half)
            if np.any(intersects):
                selected = coordinates[intersects]
                emitted.append(_morton_encode_arrays(selected, max_depth))
    if not emitted:
        return canonicalize_codes([], max_depth)
    return canonicalize_codes(np.concatenate(emitted), max_depth)


def _triangle_intersects_boxes(
    triangle: np.ndarray, centers: np.ndarray, half: float
) -> np.ndarray:
    """Vectorized triangle/AABB SAT with inclusive (closed-cell) boundaries."""
    vertices = triangle[None, :, :] - centers[:, None, :]
    tolerance = max(half * 1e-10, 1e-14)
    active = np.all(vertices.min(axis=1) <= half + tolerance, axis=1)
    active &= np.all(vertices.max(axis=1) >= -half - tolerance, axis=1)
    if not np.any(active):
        return active
    edges = (
        triangle[1] - triangle[0],
        triangle[2] - triangle[1],
        triangle[0] - triangle[2],
    )
    normal = np.cross(edges[0], triangle[2] - triangle[0])
    axes = [normal]
    basis = np.eye(3, dtype=np.float64)
    axes.extend(np.cross(edge, axis) for edge in edges for axis in basis)
    for axis in axes:
        if float(np.dot(axis, axis)) <= 1e-30:
            continue
        projections = np.einsum("mvi,i->mv", vertices, axis)
        radius = half * float(np.abs(axis).sum())
        active &= projections.min(axis=1) <= radius + tolerance
        active &= projections.max(axis=1) >= -radius - tolerance
        if not np.any(active):
            break
    return active


def _morton_encode_arrays(coordinates: np.ndarray, depth: int) -> np.ndarray:
    result = np.zeros(len(coordinates), dtype=_UINT64_LE)
    values = coordinates.astype(np.uint64, copy=False)
    for shift in range(depth - 1, -1, -1):
        child = (
            ((values[:, 0] >> np.uint64(shift)) & 1) << 2
            | ((values[:, 1] >> np.uint64(shift)) & 1) << 1
            | ((values[:, 2] >> np.uint64(shift)) & 1)
        )
        result = (result << 3) | child
    return result.astype(_UINT64_LE, copy=False)
