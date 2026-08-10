"""Reference-owned isotropic world/lattice transform."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import trimesh

from meshscope.voxblame.errors import OctreeError


LATTICE_MIN = -0.5
LATTICE_MAX = 0.5


@dataclass(frozen=True)
class CanonicalFrame:
    """Map already-canonical coordinates to the VoxBlame lattice."""

    center: tuple[float, float, float]
    scale: float

    def __post_init__(self) -> None:
        if len(self.center) != 3 or not all(math.isfinite(v) for v in self.center):
            raise OctreeError("frame center must contain three finite values")
        if not math.isfinite(self.scale) or self.scale <= 0:
            raise OctreeError("frame scale must be finite and positive")

    def world_to_lattice(self, points: np.ndarray) -> np.ndarray:
        return (
            np.asarray(points, dtype=np.float64) - np.asarray(self.center)
        ) / self.scale

    def lattice_to_world(self, points: np.ndarray) -> np.ndarray:
        return (
            np.asarray(points, dtype=np.float64) * self.scale
            + np.asarray(self.center)
        )

def mesh_vertices(mesh: trimesh.Trimesh, label: str) -> np.ndarray:
    """Return validated finite vertices for a triangle mesh."""
    if not isinstance(mesh, trimesh.Trimesh):
        raise OctreeError(f"{label} must be a trimesh.Trimesh")
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    if vertices.ndim != 2 or vertices.shape[1:] != (3,) or not len(vertices):
        raise OctreeError(f"{label} mesh has no vertices")
    if not np.all(np.isfinite(vertices)):
        raise OctreeError(f"{label} mesh contains non-finite vertices")
    if len(mesh.faces) == 0:
        raise OctreeError(f"{label} mesh has no triangle faces")
    return vertices
