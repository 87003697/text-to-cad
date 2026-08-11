"""Hierarchical conservative triangle/AABB surface voxelization."""

from __future__ import annotations

from typing import Literal

import numpy as np
import trimesh

from meshscope.voxblame.errors import OctreeError, SurfaceTreeError
from meshscope.voxblame.frame import CanonicalFrame, mesh_vertices
from meshscope.voxblame.tree import SurfaceTree, validate_depth


Backend = Literal["python", "native"]
BACKEND_IDENTITY_SCHEMA = "meshscope.surface-occupancy-backend/1"
NATIVE_BACKEND_ID = "meshscope.voxblame.native-sat/1"
PYTHON_BACKEND_ID = "meshscope.voxblame.python-sat/1"


def backend_identity(backend: Backend) -> dict[str, str]:
    """Return the explicit versioned identity of one occupancy backend."""
    if backend == "python":
        return {
            "schema": BACKEND_IDENTITY_SCHEMA,
            "id": PYTHON_BACKEND_ID,
            "implementation": "python",
        }
    if backend != "native":
        raise SurfaceTreeError("backend must be python or native")
    try:
        from meshscope.voxblame import _native as native
    except ImportError:
        raise SurfaceTreeError("native octree backend is unavailable") from None
    if getattr(native, "BACKEND_ID", None) != NATIVE_BACKEND_ID:
        raise SurfaceTreeError("native octree backend identity is missing or unsupported")
    return {
        "schema": BACKEND_IDENTITY_SCHEMA,
        "id": NATIVE_BACKEND_ID,
        "implementation": "native",
    }


def voxelize_mesh(
    mesh: trimesh.Trimesh,
    frame: CanonicalFrame,
    max_depth: int,
    *,
    backend: Backend = "native",
) -> SurfaceTree:
    """Convert a validated world-space mesh into canonical surface occupancy."""
    validate_depth(max_depth)
    mesh_vertices(mesh, "mesh")
    lattice_triangles = frame.world_to_lattice(
        np.asarray(mesh.triangles, dtype=np.float64)
    )
    edges_a = lattice_triangles[:, 1] - lattice_triangles[:, 0]
    edges_b = lattice_triangles[:, 2] - lattice_triangles[:, 0]
    doubled_areas = np.linalg.norm(np.cross(edges_a, edges_b), axis=1)
    triangles = np.ascontiguousarray(lattice_triangles[doubled_areas > 1e-15])
    if len(triangles) == 0:
        raise OctreeError("mesh contains no non-degenerate triangles")
    try:
        return build_lattice_tree(triangles, max_depth, backend=backend)
    except SurfaceTreeError as exc:
        raise OctreeError(str(exc)) from exc


def build_lattice_tree(
    triangles: np.ndarray,
    max_depth: int,
    *,
    backend: Backend = "native",
) -> SurfaceTree:
    """Build a tree directly from canonical-lattice float64 triangles."""
    validate_depth(max_depth)
    geometry = np.ascontiguousarray(triangles, dtype=np.float64)
    if geometry.ndim != 3 or geometry.shape[1:] != (3, 3):
        raise SurfaceTreeError("triangles must have shape [F, 3, 3]")
    if not len(geometry) or not np.all(np.isfinite(geometry)):
        raise SurfaceTreeError("triangles must contain finite geometry")
    if backend not in {"python", "native"}:
        raise SurfaceTreeError("backend must be python or native")
    if backend == "python":
        return _build_python(geometry, max_depth)
    backend_identity(backend)
    from meshscope.voxblame import _native as native
    masks, span_bytes, leaf_count = native.build(geometry, max_depth)
    spans = np.frombuffer(span_bytes, dtype=np.dtype("<u4"))
    return SurfaceTree(max_depth, masks, spans, int(leaf_count))


def _build_python(triangles: np.ndarray, max_depth: int) -> SurfaceTree:
    root_hits = np.flatnonzero(
        triangles_intersect_box(triangles, np.zeros(3), 0.5)
    )
    if not len(root_hits):
        return SurfaceTree.empty(max_depth)
    masks = bytearray()

    def visit(
        indices: np.ndarray,
        center: np.ndarray,
        half: float,
        depth: int,
    ) -> None:
        node = len(masks)
        masks.append(0)
        child_half = half / 2.0
        for child in range(8):
            offset = np.array(
                [
                    1.0 if child & 4 else -1.0,
                    1.0 if child & 2 else -1.0,
                    1.0 if child & 1 else -1.0,
                ]
            )
            child_center = center + offset * child_half
            local = triangles_intersect_box(
                triangles[indices], child_center, child_half
            )
            child_indices = indices[local]
            if not len(child_indices):
                continue
            masks[node] |= 1 << child
            if depth + 1 < max_depth:
                visit(child_indices, child_center, child_half, depth + 1)

    visit(root_hits, np.zeros(3), 0.5, 0)
    return SurfaceTree.from_masks(max_depth, bytes(masks))


def triangles_intersect_box(
    triangles: np.ndarray,
    center: np.ndarray,
    half: float,
    *,
    tolerance: float | None = None,
) -> np.ndarray:
    """Triangle/AABB SAT with the inclusive closed-cell occupancy policy."""
    vertices = triangles - center[None, None, :]
    if tolerance is None:
        tolerance = max(half * 1e-10, 1e-14)
    active = np.all(vertices.min(axis=1) <= half + tolerance, axis=1)
    active &= np.all(vertices.max(axis=1) >= -half - tolerance, axis=1)
    if not np.any(active):
        return active
    edges = np.stack(
        (
            triangles[:, 1] - triangles[:, 0],
            triangles[:, 2] - triangles[:, 1],
            triangles[:, 0] - triangles[:, 2],
        ),
        axis=1,
    )
    normals = np.cross(edges[:, 0], triangles[:, 2] - triangles[:, 0])
    axes = [normals]
    basis = np.eye(3, dtype=np.float64)
    axes.extend(
        np.cross(edges[:, edge], basis[axis])
        for edge in range(3)
        for axis in range(3)
    )
    for axis in axes:
        useful = np.einsum("ni,ni->n", axis, axis) > 1e-30
        projections = np.einsum("nvi,ni->nv", vertices, axis)
        radius = half * np.abs(axis).sum(axis=1)
        separated = (projections.min(axis=1) > radius + tolerance) | (
            projections.max(axis=1) < -radius - tolerance
        )
        active &= ~useful | ~separated
        if not np.any(active):
            break
    return active
