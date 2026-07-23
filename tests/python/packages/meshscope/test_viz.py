"""Unit tests for meshscope.viz (distance colorization + Pillow composite)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import trimesh

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")

from meshscope.compare import prepare, vertex_distances  # noqa: E402
from meshscope.viz import colorize, side_by_side  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURES = REPO_ROOT / "models" / "mesh-fixtures"
CUBE = str(FIXTURES / "cube.obj")
TETRA = str(FIXTURES / "tetrahedron.ply")


class TestColorize(unittest.TestCase):
    def test_produces_glb(self):
        pair = prepare(CUBE, TETRA)
        dists = vertex_distances(pair, n_samples=5000)
        glb_path = colorize(pair.norm_a, dists)
        self.assertTrue(glb_path.exists())
        self.assertTrue(str(glb_path).endswith(".glb"))
        loaded = trimesh.load(str(glb_path), force="mesh")
        self.assertGreater(len(loaded.vertices), 0)

    def test_vertex_colors_assigned(self):
        pair = prepare(CUBE, TETRA)
        dists = vertex_distances(pair, n_samples=5000)
        glb_path = colorize(pair.norm_a, dists)
        loaded = trimesh.load(str(glb_path), force="mesh")
        self.assertIsNotNone(loaded.visual.vertex_colors)
        self.assertEqual(len(loaded.visual.vertex_colors), len(loaded.vertices))


class TestSideBySide(unittest.TestCase):
    def test_produces_png(self):
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover - environment-dependent
            self.skipTest("Pillow (viz extra) not installed")

        tmp = Path(tempfile.mkdtemp())
        img_a = tmp / "a.png"
        img_b = tmp / "b.png"
        Image.new("RGB", (100, 80), (255, 0, 0)).save(str(img_a))
        Image.new("RGB", (120, 80), (0, 0, 255)).save(str(img_b))

        out = side_by_side([img_a, img_b], labels=["A", "B"])
        self.assertTrue(out.exists())
        result = Image.open(str(out))
        self.assertEqual(result.width, 220)


if __name__ == "__main__":
    unittest.main()
