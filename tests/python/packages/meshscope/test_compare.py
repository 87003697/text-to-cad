"""Unit tests for meshscope.compare (Trellis2 normalize + Chamfer + Hausdorff)."""

from __future__ import annotations

import unittest
import importlib
from pathlib import Path
from unittest import mock

import trimesh
from scipy.spatial import cKDTree

from tests.python.packages.meshscope.support.mesh_fixtures import (
    GeneratedMeshFixtures,
)
from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")

from meshscope.compare import (  # noqa: E402
    PreparedPair,
    compare,
    normalize,
    prepare,
    vertex_distances,
)
compare_module = importlib.import_module("meshscope.compare")


REPO_ROOT = Path(__file__).resolve().parents[4]
TOYS4K = REPO_ROOT / "models" / "toys4k"

FIXTURES = GeneratedMeshFixtures()
CUBE = str(FIXTURES.cube)
TETRA = str(FIXTURES.tetrahedron)
CUBE_SCALED = str(FIXTURES.cube_scaled)


def tearDownModule() -> None:
    FIXTURES.cleanup()


TOYS4K_CUP = str(TOYS4K / "cup_cup_033.ply")
TOYS4K_CHAIR = str(TOYS4K / "chair_chair_028.ply")
TOYS4K_AIRPLANE = str(TOYS4K / "airplane_airplane_016.ply")
TOYS4K_MUG = str(TOYS4K / "mug_mug_080.ply")


def _toys4k_hydrated(path: str) -> bool:
    """Return True only when a Toys4K PLY is real content (not an LFS pointer)."""
    p = Path(path)
    return p.exists() and p.stat().st_size > 4096


TOYS4K_AVAILABLE = all(
    _toys4k_hydrated(p) for p in (TOYS4K_CUP, TOYS4K_CHAIR, TOYS4K_AIRPLANE, TOYS4K_MUG)
)


class TestNormalize(unittest.TestCase):
    def test_range_fixture(self):
        mesh = trimesh.load(CUBE, force="mesh")
        norm, _, _ = normalize(mesh)
        self.assertGreaterEqual(norm.vertices.min(), -0.5 - 1e-6)
        self.assertLessEqual(norm.vertices.max(), 0.5 + 1e-6)

    def test_scale_invariance(self):
        # 8-vertex cube samples unevenly at 5K; noise floor ~0.017.
        # Threshold reflects that; different shapes are > 0.2 (cube vs tetra),
        # so this range easily separates scale-invariant from scale-drifted.
        mesh = trimesh.load(CUBE, force="mesh")
        norm_base, _, _ = normalize(mesh)
        for s in (0.01, 100.0):
            scaled = mesh.copy()
            scaled.vertices = mesh.vertices * s
            norm_scaled, _, _ = normalize(scaled)
            pa, pb = norm_base.sample(5000), norm_scaled.sample(5000)
            d1, _ = cKDTree(pb).query(pa)
            d2, _ = cKDTree(pa).query(pb)
            self.assertLess((d1.mean() + d2.mean()) / 2, 0.03)

    def test_degenerate_mesh_does_not_crash(self):
        mesh = trimesh.load(CUBE, force="mesh")
        mesh.vertices *= 1e-15
        _, scale, _ = normalize(mesh)
        self.assertEqual(scale, 1.0)

    @unittest.skipUnless(TOYS4K_AVAILABLE, "Toys4K meshes not hydrated (LFS)")
    def test_range_toys4k(self):
        for path in (TOYS4K_CUP, TOYS4K_CHAIR, TOYS4K_AIRPLANE):
            mesh = trimesh.load(path, force="mesh")
            norm, _, _ = normalize(mesh)
            self.assertGreaterEqual(norm.vertices.min(), -0.5 - 1e-6, msg=path)
            self.assertLessEqual(norm.vertices.max(), 0.5 + 1e-6, msg=path)


class TestPrepare(unittest.TestCase):
    def test_returns_prepared_pair(self):
        pair = prepare(CUBE, TETRA)
        self.assertIsInstance(pair, PreparedPair)
        self.assertIsInstance(pair.norm_a, trimesh.Trimesh)
        self.assertIsInstance(pair.norm_b, trimesh.Trimesh)
        self.assertGreater(pair.scale_a, 0)
        self.assertGreater(pair.scale_b, 0)

    @unittest.skipUnless(TOYS4K_AVAILABLE, "Toys4K meshes not hydrated (LFS)")
    def test_toys4k_large_mesh(self):
        pair = prepare(TOYS4K_AIRPLANE, TOYS4K_CHAIR)
        self.assertGreater(len(pair.norm_a.vertices), 1000)


class TestCompare(unittest.TestCase):
    def test_self_comparison_near_zero(self):
        # 8-vertex cube at 5K samples floors at ~0.017; @50K it drops to
        # ~0.005 (measured empirically). Keep 5K here for CI speed and set
        # a headroom threshold that stays well below the < 0.25 seen for
        # different shapes below.
        pair = prepare(CUBE, CUBE)
        result = compare(pair, n_samples=5000)
        self.assertLess(result["chamfer"], 0.03)
        self.assertIn("hausdorff", result)
        self.assertIn("stats", result)
        self.assertIn("meta", result)
        self.assertEqual(result["meta"]["normalization"], "trellis2")
        self.assertEqual(result["meta"]["sample_seed"], 0)
        self.assertEqual(result["meta"]["sampling"], "trimesh_surface_seeded")

    def test_sampling_is_deterministic(self):
        pair = prepare(CUBE, TETRA)
        first = compare(pair, n_samples=1000, seed=17)
        second = compare(pair, n_samples=1000, seed=17)
        self.assertEqual(first, second)

    def test_sampling_does_not_require_new_trimesh_method_seed_api(self):
        norm_a, scale_a, center_a = normalize(trimesh.creation.box())
        norm_b, scale_b, center_b = normalize(trimesh.creation.icosphere(subdivisions=1))
        pair = PreparedPair(
            norm_a=norm_a,
            norm_b=norm_b,
            scale_a=scale_a,
            scale_b=scale_b,
            center_a=center_a,
            center_b=center_b,
        )
        with mock.patch.object(
            trimesh.Trimesh,
            "sample",
            side_effect=AssertionError("Trimesh.sample must not be used"),
        ):
            result = compare(pair, n_samples=1000, seed=17)
        self.assertEqual(result["meta"]["sample_seed"], 17)

    def test_rejects_invalid_sampling_parameters(self):
        pair = prepare(CUBE, TETRA)
        with self.assertRaisesRegex(ValueError, "n_samples must be positive"):
            compare(pair, n_samples=0)
        with self.assertRaisesRegex(ValueError, "seed must be non-negative"):
            compare(pair, n_samples=100, seed=-1)

    def test_vertex_distances_uses_compare_target_stream(self):
        pair = prepare(CUBE, TETRA)
        with mock.patch(
            "meshscope.compare._sample_surface",
            wraps=compare_module._sample_surface,
        ) as sample_surface:
            vertex_distances(pair, n_samples=100, seed=17)

        sample_surface.assert_called_once_with(pair.norm_b, 100, 18)

    def test_different_shapes_large_distance(self):
        pair = prepare(CUBE, TETRA)
        result = compare(pair, n_samples=5000)
        self.assertGreater(result["chamfer"], 0.05)

    def test_scale_invariance(self):
        pair = prepare(CUBE, CUBE_SCALED)
        result = compare(pair, n_samples=5000)
        # After Trellis2 normalize, cube vs 3x-scaled cube should collapse
        # to the self-compare noise floor.
        self.assertLess(result["chamfer"], 0.03)

    def test_include_distances(self):
        pair = prepare(CUBE, TETRA)
        result = compare(pair, n_samples=1000, include_distances=True)
        self.assertIn("distances_a2b", result)
        self.assertEqual(len(result["distances_a2b"]), 1000)

    @unittest.skipUnless(TOYS4K_AVAILABLE, "Toys4K meshes not hydrated (LFS)")
    def test_toys4k_self_compare(self):
        # This package-level test intentionally stays at 10K for CI speed.
        # The mesh-to-cad acceptance protocol uses 50K deterministic samples
        # so its 0.01 threshold remains above the self-compare noise floor.
        for path in (TOYS4K_CUP, TOYS4K_CHAIR):
            pair = prepare(path, path)
            result = compare(pair, n_samples=10000)
            self.assertLess(result["chamfer"], 0.02, msg=f"{path} self-compare failed")

    @unittest.skipUnless(TOYS4K_AVAILABLE, "Toys4K meshes not hydrated (LFS)")
    def test_toys4k_similar_less_than_dissimilar(self):
        # Note: TOYS4K_MUG file existence is orthogonal to TOYS4K_AVAILABLE
        # (which checks CUP/CHAIR/AIRPLANE/MUG). If missing, skip.
        if not Path(TOYS4K_MUG).exists():
            self.skipTest("Toys4K MUG not present")
        pair_similar = prepare(TOYS4K_CUP, TOYS4K_MUG)
        pair_dissimilar = prepare(TOYS4K_CUP, TOYS4K_AIRPLANE)
        r_similar = compare(pair_similar, n_samples=10000)
        r_dissimilar = compare(pair_dissimilar, n_samples=10000)
        self.assertLess(r_similar["chamfer"], r_dissimilar["chamfer"])


class TestVertexDistances(unittest.TestCase):
    def test_output_shape(self):
        pair = prepare(CUBE, TETRA)
        dists = vertex_distances(pair, n_samples=5000)
        self.assertEqual(dists.shape, (len(pair.norm_a.vertices),))

    def test_self_comparison_near_zero(self):
        # vertex_distances at 5K samples measures ~0.05 max on the 8-vertex
        # cube due to sparse sampling; 0.1 is a comfortable headroom above
        # the noise floor while still catching bugs that would make it
        # order-of-magnitude larger.
        pair = prepare(CUBE, CUBE)
        dists = vertex_distances(pair, n_samples=5000)
        self.assertLess(float(dists.max()), 0.1)


if __name__ == "__main__":
    unittest.main()
