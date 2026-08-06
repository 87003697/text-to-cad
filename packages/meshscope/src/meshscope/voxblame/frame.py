"""Reference-owned isotropic world/lattice transform."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import trimesh

from meshscope.voxblame.errors import OctreeError


LATTICE_MIN = -0.5
LATTICE_MAX = 0.5


@dataclass(frozen=True)
class CanonicalFrame:
    """Map the reference bounding cube to the canonical VoxBlame lattice."""

    center: tuple[float, float, float]
    scale: float

    def __post_init__(self) -> None:
        if len(self.center) != 3 or not all(math.isfinite(v) for v in self.center):
            raise OctreeError("frame center must contain three finite values")
        if not math.isfinite(self.scale) or self.scale <= 0:
            raise OctreeError("frame scale must be finite and positive")

    @classmethod
    def from_reference(cls, mesh: trimesh.Trimesh) -> "CanonicalFrame":
        vertices = mesh_vertices(mesh, "reference")
        lower = vertices.min(axis=0)
        upper = vertices.max(axis=0)
        scale = float(np.max(upper - lower))
        if scale <= 1e-15:
            raise OctreeError("reference mesh has a degenerate bounding box")
        return cls(tuple(float(v) for v in (lower + upper) / 2.0), scale)

    def world_to_lattice(self, points: np.ndarray) -> np.ndarray:
        return (
            np.asarray(points, dtype=np.float64) - np.asarray(self.center)
        ) / self.scale

    def lattice_to_world(self, points: np.ndarray) -> np.ndarray:
        return (
            np.asarray(points, dtype=np.float64) * self.scale
            + np.asarray(self.center)
        )

    # Compatibility names retained for callers that predate the module split.
    def to_lattice(self, points: np.ndarray) -> np.ndarray:
        return self.world_to_lattice(points)

    def to_world(self, points: np.ndarray) -> np.ndarray:
        return self.lattice_to_world(points)

    def assert_fits(self, mesh: trimesh.Trimesh, label: str) -> None:
        points = self.world_to_lattice(mesh_vertices(mesh, label))
        tolerance = 1e-10
        if np.any(points < LATTICE_MIN - tolerance) or np.any(
            points > LATTICE_MAX + tolerance
        ):
            raise OctreeError(
                f"{label} exceeds reference frame [-0.5, 0.5]^3 "
                f"(lattice min={points.min(axis=0).tolist()}, "
                f"max={points.max(axis=0).tolist()})"
            )

    def to_json(self) -> dict[str, Any]:
        return {"center": list(self.center), "scale": self.scale}

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "CanonicalFrame":
        try:
            return cls(
                tuple(float(v) for v in value["center"]),
                float(value["scale"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OctreeError("invalid frame metadata") from exc


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
