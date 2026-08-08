"""Small generated mesh fixtures for meshscope unit tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

import trimesh


class GeneratedMeshFixtures:
    """Create path-based mesh fixtures without requiring repository LFS data."""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="meshscope-mesh-fixtures-"
        )
        root = Path(self._temporary.name)
        self.cube = root / "cube.obj"
        self.cube_scaled = root / "cube_scaled.obj"
        self.tetrahedron = root / "tetrahedron.ply"

        cube = trimesh.Trimesh(
            vertices=[
                [0, 0, 0],
                [1, 0, 0],
                [1, 1, 0],
                [0, 1, 0],
                [0, 0, 1],
                [1, 0, 1],
                [1, 1, 1],
                [0, 1, 1],
            ],
            faces=[
                [0, 1, 2],
                [0, 2, 3],
                [4, 6, 5],
                [4, 7, 6],
                [0, 4, 5],
                [0, 5, 1],
                [1, 5, 6],
                [1, 6, 2],
                [2, 6, 7],
                [2, 7, 3],
                [3, 7, 4],
                [3, 4, 0],
            ],
            process=False,
        )
        cube.export(self.cube)

        cube_scaled = cube.copy()
        cube_scaled.vertices *= 3
        cube_scaled.export(self.cube_scaled)

        tetrahedron = trimesh.Trimesh(
            vertices=[
                [0, 0, 0],
                [1, 0, 0],
                [0.5, 0.866, 0],
                [0.5, 0.289, 0.816],
            ],
            faces=[
                [0, 1, 2],
                [0, 1, 3],
                [1, 2, 3],
                [0, 2, 3],
            ],
            process=False,
        )
        tetrahedron.export(self.tetrahedron)

    def cleanup(self) -> None:
        self._temporary.cleanup()
