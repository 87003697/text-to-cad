from __future__ import annotations

import builtins
import importlib.util
import inspect
from pathlib import Path
import random
import tempfile
import unittest
from unittest import mock

import numpy as np
import trimesh

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")

from meshscope.voxblame import (  # noqa: E402
    CanonicalFrame,
    SurfaceTree,
    SurfaceTreeError,
    build_lattice_tree,
    measure_step,
    read_surface_tree,
    tree_from_codes,
    voxelize_mesh,
    write_surface_tree,
)
from meshscope.voxblame.codec import (  # noqa: E402
    decode_surface_tree,
    encode_surface_tree,
)
from tests.python.packages.meshscope.support.morton_oracle import (  # noqa: E402
    build_surface_codes,
    canonicalize_codes,
    morton_encode,
)


FRAME = CanonicalFrame((0.0, 0.0, 0.0), 1.0)


def _codes(depth: int, *coordinates: tuple[int, int, int]) -> np.ndarray:
    return canonicalize_codes(
        [morton_encode(*coordinate, depth) for coordinate in coordinates], depth
    )


def _sheet() -> trimesh.Trimesh:
    vertices = np.array(
        [[0, -0.5, -0.5], [0, 0.5, -0.5], [0, 0.5, 0.5], [0, -0.5, 0.5]],
        dtype=np.float64,
    )
    return trimesh.Trimesh(
        vertices=vertices, faces=[[0, 1, 2], [0, 2, 3]], process=False
    )


def _triangle(center: tuple[float, float, float]) -> trimesh.Trimesh:
    x, y, z = center
    return trimesh.Trimesh(
        vertices=np.array(
            [[x - 0.05, y - 0.05, z], [x + 0.05, y - 0.05, z], [x, y + 0.05, z]],
            dtype=np.float64,
        ),
        faces=[[0, 1, 2]],
        process=False,
    )


class SurfaceTreeCodecTests(unittest.TestCase):
    GOLDEN_EMPTY_DEPTH_3 = bytes.fromhex(
        "564253560103000101000000000000000000000000000000"
        "d8fd84b3b2e186e510f6c2638a35e980bbd01fede90e2522"
        "c9ec3ee007abb4260001000000"
    )
    GOLDEN_SINGLE_DEPTH_1 = bytes.fromhex(
        "564253560101000101000000000000000100000000000000"
        "e80654dd102dd128919bdbed145aef9b5c7c3996892def2b"
        "aa870c8635f6a7320101000000"
    )

    def test_empty_tree_has_canonical_encoding(self) -> None:
        tree = SurfaceTree.empty(8)
        self.assertEqual(b"\0", tree.masks)
        np.testing.assert_array_equal(np.array([1], dtype="<u4"), tree.spans)
        self.assertEqual(0, tree.leaf_count)

    def test_codec_and_file_round_trip_preserve_identity(self) -> None:
        tree = tree_from_codes(
            _codes(4, (0, 0, 0), (8, 9, 10), (15, 15, 15)), 4
        )
        encoded = encode_surface_tree(tree)
        self.assertEqual(56 + tree.node_count * 5, len(encoded))
        decoded = decode_surface_tree(encoded)
        self.assertEqual(tree.logical_sha256, decoded.logical_sha256)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tree.vbsvo"
            write_surface_tree(tree, path)
            loaded = read_surface_tree(path)
        self.assertEqual(tree.logical_sha256, loaded.logical_sha256)
        np.testing.assert_array_equal(tree.spans, loaded.spans)

    def test_golden_bytes_are_stable(self) -> None:
        fixtures = (
            (SurfaceTree.empty(3), self.GOLDEN_EMPTY_DEPTH_3),
            (tree_from_codes([0], 1), self.GOLDEN_SINGLE_DEPTH_1),
        )
        for tree, expected in fixtures:
            with self.subTest(depth=tree.max_depth):
                self.assertEqual(expected, encode_surface_tree(tree))

    def test_corruption_fails_closed(self) -> None:
        source = encode_surface_tree(tree_from_codes([0, 63], 2))
        variants = (source[:-1], source + b"\0", b"BAD!" + source[4:])
        for data in variants:
            with self.subTest(size=len(data)):
                with self.assertRaises(SurfaceTreeError):
                    decode_surface_tree(data)

    def test_random_leaf_sets_round_trip(self) -> None:
        generator = random.Random(8052026)
        for case in range(40):
            depth = generator.randint(1, 5)
            limit = 1 << (3 * depth)
            values = canonicalize_codes(
                generator.sample(range(limit), generator.randint(0, min(32, limit))),
                depth,
            )
            with self.subTest(case=case, depth=depth):
                np.testing.assert_array_equal(
                    values, tree_from_codes(values, depth).leaf_codes()
                )


class SurfaceOccupancyTests(unittest.TestCase):
    def test_public_measurement_boundaries_default_to_native_backend(self) -> None:
        for function in (measure_step, voxelize_mesh, build_lattice_tree):
            with self.subTest(function=function.__name__):
                self.assertEqual(
                    "native",
                    inspect.signature(function).parameters["backend"].default,
                )

    def test_default_backend_fails_closed_when_native_extension_is_missing(self) -> None:
        real_import = builtins.__import__

        def import_without_native(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "meshscope.voxblame" and "_native" in fromlist:
                raise ImportError("test hides native extension")
            return real_import(name, globals, locals, fromlist, level)

        triangles = np.array(
            [[[-0.1, -0.1, 0.0], [0.1, -0.1, 0.0], [0.0, 0.1, 0.0]]],
            dtype=np.float64,
        )
        with mock.patch("builtins.__import__", side_effect=import_without_native):
            with self.assertRaisesRegex(
                SurfaceTreeError, "native octree backend is unavailable"
            ):
                build_lattice_tree(triangles, 3)

    def test_triangle_order_winding_and_duplicate_faces_do_not_change_tree(self) -> None:
        sheet = _sheet()
        variants = (
            trimesh.Trimesh(
                vertices=sheet.vertices.copy(), faces=sheet.faces[::-1, ::-1], process=False
            ),
            trimesh.Trimesh(
                vertices=sheet.vertices.copy(), faces=np.vstack((sheet.faces, sheet.faces)), process=False
            ),
        )
        expected = voxelize_mesh(sheet, FRAME, 4, backend="python")
        for variant in variants:
            with self.subTest(face_count=len(variant.faces)):
                self.assertEqual(
                    expected.logical_sha256,
                    voxelize_mesh(variant, FRAME, 4, backend="python").logical_sha256,
                )

    def test_hierarchical_builder_matches_independent_flat_sat_oracle(self) -> None:
        fixtures = (
            trimesh.creation.box(extents=(1.0, 0.5, 0.25)),
            _sheet(),
            trimesh.util.concatenate((_triangle((-0.3, 0, 0)), _triangle((0.3, 0, 0)))),
        )
        for fixture in fixtures:
            with self.subTest(face_count=len(fixture.faces)):
                expected = build_surface_codes(fixture, FRAME, 4)
                actual = voxelize_mesh(fixture, FRAME, 4, backend="python")
                np.testing.assert_array_equal(expected, actual.leaf_codes())


class NativeSurfaceOccupancyParityTests(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("meshscope.voxblame._native") is not None,
        "native VoxBlame extension is not built in this environment",
    )
    def test_native_matches_python_and_flat_oracle(self) -> None:
        fixtures = (
            trimesh.creation.box(extents=(1.0, 0.5, 0.25)),
            _sheet(),
        )
        for fixture in fixtures:
            with self.subTest(face_count=len(fixture.faces)):
                python_tree = voxelize_mesh(fixture, FRAME, 5, backend="python")
                native_tree = voxelize_mesh(fixture, FRAME, 5, backend="native")
                self.assertEqual(python_tree.logical_sha256, native_tree.logical_sha256)
                np.testing.assert_array_equal(
                    build_surface_codes(fixture, FRAME, 5), native_tree.leaf_codes()
                )


if __name__ == "__main__":
    unittest.main()
