"""Tests for deterministic sparse Morton surface-error grading."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import random
import tempfile
import unittest

import numpy as np
import trimesh

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")

from meshscope.voxblame import (  # noqa: E402
    CanonicalFrame,
    ChangeCell,
    ErrorCell,
    OctreeError,
    SurfaceTree,
    SurfaceTreeError,
    compare_error_trees,
    grade_surface_trees,
    lattice_bounds,
    read_surface_tree,
    run_step,
    select_next_action,
    tree_from_codes,
    voxelize_mesh,
    world_bounds,
    write_surface_tree,
)
from meshscope.voxblame.codec import (  # noqa: E402
    decode_surface_tree,
    encode_surface_tree,
)
from meshscope.voxblame.reporting import (  # noqa: E402
    next_action_json,
    region_handle_json,
)
from tests.python.packages.meshscope.support.morton_oracle import (  # noqa: E402
    build_surface_codes,
    canonicalize_codes,
    codes_digest,
    grade_codes,
    morton_decode,
    morton_encode,
    prefix_interval,
    prefix_occupied,
    validate_codes,
)

build_surface_tree = voxelize_mesh
grade_trees = grade_surface_trees


def _codes(depth: int, *coordinates: tuple[int, int, int]) -> np.ndarray:
    return canonicalize_codes(
        [morton_encode(*coordinate, depth) for coordinate in coordinates], depth
    )


def _triangle(center: tuple[float, float, float], size: float = 0.08) -> trimesh.Trimesh:
    x, y, z = center
    vertices = np.array(
        [[x - size, y - size, z], [x + size, y - size, z], [x, y + size, z]],
        dtype=np.float64,
    )
    return trimesh.Trimesh(vertices=vertices, faces=[[0, 1, 2]], process=False)


def _combine(*meshes: trimesh.Trimesh) -> trimesh.Trimesh:
    return trimesh.util.concatenate(meshes)


def _sheet_x_zero() -> trimesh.Trimesh:
    vertices = np.array(
        [[0, -0.5, -0.5], [0, 0.5, -0.5], [0, 0.5, 0.5], [0, -0.5, 0.5]],
        dtype=np.float64,
    )
    return trimesh.Trimesh(vertices=vertices, faces=[[0, 1, 2], [0, 2, 3]], process=False)


def _cell_set(cells: list[ErrorCell] | list[ChangeCell], max_depth: int) -> set[int]:
    result: set[int] = set()
    for cell in cells:
        lower, upper = prefix_interval(cell.prefix, cell.depth, max_depth)
        result.update(range(lower, upper))
    return result


def _dense_oracle(
    reference: np.ndarray, candidate: np.ndarray, max_depth: int
) -> list[ErrorCell]:
    ref = {int(value) for value in reference}
    cand = {int(value) for value in candidate}
    result: list[ErrorCell] = []

    def occupied(values: set[int], prefix: int, depth: int) -> bool:
        lower, upper = prefix_interval(prefix, depth, max_depth)
        return any(lower <= value < upper for value in values)

    def visit(prefix: int, depth: int) -> None:
        r = occupied(ref, prefix, depth)
        c = occupied(cand, prefix, depth)
        if r != c:
            result.append(ErrorCell(prefix, depth, "missing" if r else "excess"))
        elif r and depth < max_depth:
            for child in range(8):
                visit((prefix << 3) | child, depth + 1)

    visit(0, 0)
    return result


class TestMortonCodes(unittest.TestCase):
    def test_round_trip_boundary_coordinates(self):
        for depth in (1, 4, 8, 21):
            high = (1 << depth) - 1
            for coordinate in ((0, 0, 0), (high, high, high), (0, high, high), (high, 0, high)):
                code = morton_encode(*coordinate, depth)
                self.assertEqual(coordinate, morton_decode(code, depth))
                self.assertGreaterEqual(code, 0)
                self.assertLess(code, 1 << (3 * depth))

    def test_child_bit_order_is_xyz(self):
        for x in (0, 1):
            for y in (0, 1):
                for z in (0, 1):
                    self.assertEqual((x << 2) | (y << 1) | z, morton_encode(x, y, z, 1))

    def test_codes_are_sorted_unique_little_endian(self):
        result = canonicalize_codes(np.array([7, 1, 7, 0], dtype=np.int64), 2)
        self.assertEqual("<u8", result.dtype.str)
        np.testing.assert_array_equal(result, np.array([0, 1, 7], dtype="<u8"))

    def test_invalid_array_is_rejected(self):
        invalid = (
            np.array([[1]], dtype="<u8"),
            np.array([1], dtype=np.int64),
            np.array([1.0], dtype=np.float64),
            np.array([2, 1], dtype="<u8"),
            np.array([1, 1], dtype="<u8"),
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(OctreeError):
                    validate_codes(value)

    def test_npy_round_trip_preserves_logical_digest(self):
        source = canonicalize_codes([0, 1, 7, 63, 511], 3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "codes.npy"
            np.save(path, source, allow_pickle=False)
            loaded = np.load(path, allow_pickle=False)
        np.testing.assert_array_equal(source, loaded)
        self.assertEqual(codes_digest(source), codes_digest(loaded))

    def test_prefix_interval(self):
        values = _codes(3, (0, 0, 0), (1, 2, 3), (7, 7, 7))
        raw = [int(value) for value in values]
        for depth in range(4):
            for prefix in range(1 << (3 * depth)):
                lower, upper = prefix_interval(prefix, depth, 3)
                self.assertEqual(
                    any(lower <= value < upper for value in raw),
                    prefix_occupied(values, prefix, depth, 3),
                )


class TestSurfaceTreeCodec(unittest.TestCase):
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
    GOLDEN_BRANCH_DEPTH_2 = bytes.fromhex(
        "564253560102000103000000000000000200000000000000"
        "9174058aaecad15a9f1e7d721f0e7e093a2abec7bba0f07"
        "e9f4ef628af30e7cf810180030000000100000001000000"
    )

    def test_empty_tree_has_canonical_encoding(self):
        tree = SurfaceTree.empty(8)
        self.assertEqual(b"\0", tree.masks)
        np.testing.assert_array_equal(np.array([1], dtype="<u4"), tree.spans)
        self.assertEqual(1, tree.node_count)
        self.assertEqual(0, tree.leaf_count)

    def test_empty_iterable_has_canonical_encoding(self):
        tree = tree_from_codes([], 3)
        self.assertEqual(b"\0", tree.masks)
        np.testing.assert_array_equal(np.array([1], dtype="<u4"), tree.spans)

    def test_spans_use_immutable_independent_storage(self):
        source = np.array([1], dtype="<u4")
        tree = SurfaceTree(3, b"\0", source, 0)
        self.assertIsNot(source, tree.spans)
        self.assertFalse(tree.spans.flags.writeable)
        source[0] = 9
        self.assertEqual(1, int(tree.spans[0]))
        with self.assertRaises(ValueError):
            tree.spans.flags.writeable = True

    def test_leaf_count_requires_an_integer(self):
        spans = np.array([1], dtype="<u4")
        for value in (0.0, 0.1, False, np.float64(0), np.bool_(False), "0"):
            with self.subTest(value=value):
                with self.assertRaises(SurfaceTreeError):
                    SurfaceTree(3, b"\0", spans, value)
        self.assertEqual(0, SurfaceTree(3, b"\0", spans, np.int64(0)).leaf_count)

    def test_single_leaf_and_branch_spans(self):
        leaves = _codes(3, (0, 0, 0), (7, 7, 7), (6, 7, 7))
        tree = tree_from_codes(leaves, 3)
        np.testing.assert_array_equal(leaves, tree.leaf_codes())
        self.assertEqual(tree.node_count, int(tree.spans[0]))
        self.assertEqual(len(leaves), tree.leaf_count)

    def test_vbsvo_round_trip_and_exact_size(self):
        tree = tree_from_codes(_codes(4, (0, 0, 0), (8, 9, 10), (15, 15, 15)), 4)
        encoded = encode_surface_tree(tree)
        self.assertEqual(56 + tree.node_count * 5, len(encoded))
        decoded = decode_surface_tree(encoded)
        self.assertEqual(tree.masks, decoded.masks)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tree.vbsvo"
            write_surface_tree(tree, path)
            self.assertEqual(56 + tree.node_count * 5, path.stat().st_size)
            loaded = read_surface_tree(path)
        self.assertEqual(tree.masks, loaded.masks)
        np.testing.assert_array_equal(tree.spans, loaded.spans)
        self.assertEqual(tree.logical_sha256, loaded.logical_sha256)

    def test_vbsvo_golden_bytes(self):
        fixtures = (
            (SurfaceTree.empty(3), self.GOLDEN_EMPTY_DEPTH_3),
            (tree_from_codes([0], 1), self.GOLDEN_SINGLE_DEPTH_1),
            (tree_from_codes([0, 63], 2), self.GOLDEN_BRANCH_DEPTH_2),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (tree, expected) in enumerate(fixtures):
                with self.subTest(index=index):
                    path = Path(directory) / f"golden-{index}.vbsvo"
                    write_surface_tree(tree, path)
                    self.assertEqual(expected, path.read_bytes())
                    loaded = read_surface_tree(path)
                    self.assertEqual(tree.logical_sha256, loaded.logical_sha256)

    def test_corrupt_header_payload_and_index_fail_closed(self):
        tree = tree_from_codes(_codes(3, (0, 0, 0), (7, 7, 7)), 3)
        with tempfile.TemporaryDirectory() as directory:
            clean = Path(directory) / "clean.vbsvo"
            write_surface_tree(tree, clean)
            source = clean.read_bytes()
            non_root_zero = bytearray(source)
            non_root_zero[57] = 0
            forged_non_root_span = bytearray(source)
            forged_non_root_span[-4:] = b"\0\0\0\0"
            variants = {
                "magic": b"BAD!" + source[4:],
                "version": source[:4] + b"\x02" + source[5:],
                "order": source[:6] + b"\x01" + source[7:],
                "flags": source[:7] + b"\x00" + source[8:],
                "zero-node-count": source[:8] + b"\0" * 8 + source[16:],
                "short-node-count": source[:8] + (1).to_bytes(8, "little") + source[16:],
                "leaf-count": source[:16] + b"\0" * 8 + source[24:],
                "truncated": source[:-1],
                "trailing": source + b"\0",
                "digest": source[:24] + bytes([source[24] ^ 1]) + source[25:],
                "non-root-zero-mask": bytes(non_root_zero),
                "forged-non-root-span": bytes(forged_non_root_span),
            }
            for name, data in variants.items():
                path = Path(directory) / f"{name}.vbsvo"
                path.write_bytes(data)
                with self.subTest(name=name):
                    with self.assertRaises(SurfaceTreeError):
                        read_surface_tree(path)

    def test_random_morton_sets_round_trip(self):
        generator = random.Random(8052026)
        for case in range(100):
            depth = generator.randint(1, 5)
            limit = 1 << (3 * depth)
            values = canonicalize_codes(
                generator.sample(range(limit), generator.randint(0, min(64, limit))),
                depth,
            )
            with self.subTest(case=case, depth=depth):
                np.testing.assert_array_equal(values, tree_from_codes(values, depth).leaf_codes())


class TestAdaptiveGrading(unittest.TestCase):
    def setUp(self):
        self.reference = _codes(3, (0, 0, 0))

    def test_identical_has_no_errors(self):
        self.assertEqual([], grade_codes(self.reference, self.reference, 3))

    def test_both_empty_stops_at_root(self):
        empty = np.array([], dtype="<u8")
        stats: dict[str, int] = {}
        self.assertEqual([], grade_codes(empty, empty, 3, visit_counts=stats))
        self.assertEqual({"visited": 1}, stats)

    def test_fine_difference_waits_until_depth_three(self):
        errors = grade_codes(self.reference, _codes(3, (1, 0, 0)), 3)
        self.assertEqual(2, len(errors))
        self.assertEqual({3}, {error.depth for error in errors})
        self.assertEqual({"missing", "excess"}, {error.direction for error in errors})

    def test_coarse_difference_stops_at_depth_one(self):
        errors = grade_codes(self.reference, _codes(3, (4, 0, 0)), 3)
        self.assertEqual(2, len(errors))
        self.assertEqual({1}, {error.depth for error in errors})

    def test_swap_only_flips_direction(self):
        candidate = _codes(3, (1, 0, 0))
        forward = grade_codes(self.reference, candidate, 3)
        reverse = grade_codes(candidate, self.reference, 3)
        self.assertEqual(
            {(item.depth, item.prefix) for item in forward},
            {(item.depth, item.prefix) for item in reverse},
        )
        directions = {"missing": "excess", "excess": "missing"}
        self.assertEqual(
            {(item.depth, item.prefix, directions[item.direction]) for item in forward},
            {(item.depth, item.prefix, item.direction) for item in reverse},
        )

    def test_world_bounds_from_reference_frame(self):
        frame = CanonicalFrame((10, 20, 30), 2)
        lower, upper = world_bounds(morton_encode(0, 0, 0, 1), 1, frame)
        np.testing.assert_allclose(lower, [9, 19, 29])
        np.testing.assert_allclose(upper, [10, 20, 30])

    def test_region_handle_is_typed_and_json_is_stable(self):
        error = ErrorCell(17, 3, "missing")
        self.assertEqual(3, error.region.depth)
        self.assertEqual(17, error.region.octant_prefix)
        self.assertEqual(
            {"depth": 3, "octant_prefix": "17"},
            region_handle_json(error.region),
        )

    def test_random_cases_match_dense_prefix_oracle(self):
        generator = random.Random(7302026)
        for case in range(200):
            depth = generator.randint(1, 4)
            limit = 1 << (3 * depth)
            reference = canonicalize_codes(generator.sample(range(limit), generator.randint(0, min(32, limit))), depth)
            candidate = canonicalize_codes(generator.sample(range(limit), generator.randint(0, min(32, limit))), depth)
            with self.subTest(case=case, depth=depth, reference=reference.tolist(), candidate=candidate.tolist()):
                self.assertEqual(_dense_oracle(reference, candidate, depth), grade_codes(reference, candidate, depth))

    def test_tree_grading_matches_code_oracle(self):
        generator = random.Random(7302026)
        for case in range(200):
            depth = generator.randint(1, 4)
            limit = 1 << (3 * depth)
            reference = canonicalize_codes(generator.sample(range(limit), generator.randint(0, min(32, limit))), depth)
            candidate = canonicalize_codes(generator.sample(range(limit), generator.randint(0, min(32, limit))), depth)
            with self.subTest(case=case, depth=depth):
                self.assertEqual(
                    grade_codes(reference, candidate, depth),
                    grade_trees(tree_from_codes(reference, depth), tree_from_codes(candidate, depth)),
                )


class TestErrorTreeOverlay(unittest.TestCase):
    max_depth = 3

    def _assert_partition(self, previous, current, changes):
        cells = _cell_set(changes, self.max_depth)
        self.assertEqual(len(cells), sum(len(range(*prefix_interval(item.prefix, item.depth, self.max_depth))) for item in changes))
        self.assertEqual(_cell_set(previous, self.max_depth) | _cell_set(current, self.max_depth), cells)

    def test_introduced(self):
        current = [ErrorCell(1, 2, "missing")]
        changes = compare_error_trees([], current, self.max_depth)
        self.assertEqual(["introduced"], [change.change for change in changes])
        self._assert_partition([], current, changes)

    def test_resolved(self):
        previous = [ErrorCell(1, 2, "missing")]
        changes = compare_error_trees(previous, [], self.max_depth)
        self.assertEqual(["resolved"], [change.change for change in changes])
        self.assertIsNone(
            select_next_action(changes, [], CanonicalFrame((0, 0, 0), 1))
        )
        self._assert_partition(previous, [], changes)

    def test_direction_change(self):
        previous = [ErrorCell(1, 2, "missing")]
        current = [ErrorCell(1, 2, "excess")]
        changes = compare_error_trees(previous, current, self.max_depth)
        self.assertEqual(["changed"], [change.change for change in changes])
        self._assert_partition(previous, current, changes)

    def test_coarse_to_fine_is_improved(self):
        previous = [ErrorCell(1, 2, "missing")]
        current = [ErrorCell((1 << 3) | 2, 3, "missing")]
        changes = compare_error_trees(previous, current, self.max_depth)
        self.assertEqual(1, sum(change.change == "improved" for change in changes))
        self.assertEqual(7, sum(change.change == "resolved" for change in changes))
        self._assert_partition(previous, current, changes)

    def test_fine_to_coarse_is_regressed(self):
        previous = [ErrorCell((1 << 3) | 2, 3, "missing")]
        current = [ErrorCell(1, 2, "missing")]
        changes = compare_error_trees(previous, current, self.max_depth)
        self.assertEqual(1, sum(change.change == "regressed" for change in changes))
        self.assertEqual(7, sum(change.change == "introduced" for change in changes))
        self._assert_partition(previous, current, changes)

    def test_same_state_is_remaining_not_change(self):
        errors = [ErrorCell(1, 2, "missing")]
        self.assertEqual([], compare_error_trees(errors, errors, self.max_depth))
        action = select_next_action([], errors, CanonicalFrame((0, 0, 0), 1))
        self.assertEqual("remaining", action.reason)

    def test_priority_is_deterministic(self):
        changes = [
            ChangeCell(2, 2, "introduced", None, ErrorCell(2, 2, "excess")),
            ChangeCell(3, 2, "changed", ErrorCell(3, 2, "missing"), ErrorCell(3, 2, "excess")),
            ChangeCell(4, 2, "regressed", ErrorCell(32, 3, "missing"), ErrorCell(4, 2, "missing")),
        ]
        frame = CanonicalFrame((0, 0, 0), 1)
        encoded = [
            json.dumps(
                next_action_json(
                    select_next_action(
                        changes,
                        [ErrorCell(1, 2, "missing")],
                        frame,
                    )
                ),
                sort_keys=True,
            )
            for _ in range(20)
        ]
        self.assertEqual(1, len(set(encoded)))
        self.assertEqual("regressed", json.loads(encoded[0])["reason"])


class TestSurfaceVoxelization(unittest.TestCase):
    def test_box_self_match(self):
        box = trimesh.creation.box(extents=(1.0, 0.5, 0.25))
        frame = CanonicalFrame.from_reference(box)
        first = build_surface_codes(box, frame, 4)
        second = build_surface_codes(box, frame, 4)
        self.assertEqual(codes_digest(first), codes_digest(second))
        self.assertEqual([], grade_codes(first, second, 4))

    def test_triangle_order_and_winding_invariant(self):
        sheet = _sheet_x_zero()
        changed = trimesh.Trimesh(vertices=sheet.vertices.copy(), faces=sheet.faces[::-1, ::-1], process=False)
        frame = CanonicalFrame.from_reference(sheet)
        np.testing.assert_array_equal(build_surface_codes(sheet, frame, 4), build_surface_codes(changed, frame, 4))

    def test_duplicate_faces_do_not_change_codes(self):
        sheet = _sheet_x_zero()
        duplicate = trimesh.Trimesh(vertices=sheet.vertices.copy(), faces=np.vstack((sheet.faces, sheet.faces)), process=False)
        frame = CanonicalFrame.from_reference(sheet)
        np.testing.assert_array_equal(build_surface_codes(sheet, frame, 4), build_surface_codes(duplicate, frame, 4))

    def test_grid_plane_policy_is_deterministic(self):
        sheet = _sheet_x_zero()
        frame = CanonicalFrame.from_reference(sheet)
        first = build_surface_codes(sheet, frame, 4)
        xs = {morton_decode(int(code), 4)[0] for code in first}
        self.assertIn(7, xs)
        self.assertIn(8, xs)
        self.assertEqual(codes_digest(first), codes_digest(build_surface_codes(sheet, frame, 4)))

    def test_disconnected_missing_detail(self):
        a, b = _triangle((-0.3, 0, 0)), _triangle((0.3, 0, 0))
        reference = _combine(a, b)
        frame = CanonicalFrame.from_reference(reference)
        errors = grade_codes(build_surface_codes(reference, frame, 4), build_surface_codes(a, frame, 4), 4)
        self.assertTrue(errors)
        self.assertEqual({"missing"}, {error.direction for error in errors})
        self.assertTrue(all(lattice_bounds(error.prefix, error.depth)[0][0] >= 0 for error in errors))

    def test_floating_excess_detail(self):
        anchor = _triangle((-0.45, -0.45, 0), 0.04)
        a, b, c = _triangle((-0.2, 0, 0)), _triangle((0.1, 0, 0)), _triangle((0.35, 0, 0))
        reference = _combine(anchor, a, b, _triangle((0.45, 0.45, 0), 0.04))
        candidate = _combine(reference, c)
        frame = CanonicalFrame.from_reference(reference)
        errors = grade_codes(build_surface_codes(reference, frame, 4), build_surface_codes(candidate, frame, 4), 4)
        self.assertTrue(errors)
        self.assertEqual({"excess"}, {error.direction for error in errors})

    def test_candidate_crossing_frame_is_clipped(self):
        reference = trimesh.creation.box(extents=(1, 1, 1))
        candidate = reference.copy()
        candidate.apply_translation([0.01, 0, 0])
        frame = CanonicalFrame.from_reference(reference)
        codes = build_surface_codes(candidate, frame, 3)
        self.assertGreater(len(codes), 0)
        self.assertTrue(
            all(
                0 <= coordinate < 8
                for code in codes
                for coordinate in morton_decode(int(code), 3)
            )
        )

    def test_candidate_fully_outside_frame_has_empty_surface(self):
        reference = trimesh.creation.box(extents=(1, 1, 1))
        candidate = reference.copy()
        candidate.apply_translation([2, 0, 0])
        frame = CanonicalFrame.from_reference(reference)
        codes = build_surface_codes(candidate, frame, 3)
        self.assertEqual("<u8", codes.dtype.str)
        self.assertEqual(0, len(codes))

    def test_zero_area_faces_are_ignored(self):
        sheet = _sheet_x_zero()
        vertices = np.vstack((sheet.vertices, [[0, 0, 0], [0, 0.1, 0], [0, 0.2, 0]]))
        with_degenerate = trimesh.Trimesh(vertices=vertices, faces=np.vstack((sheet.faces, [4, 5, 6])), process=False)
        frame = CanonicalFrame.from_reference(sheet)
        np.testing.assert_array_equal(build_surface_codes(sheet, frame, 4), build_surface_codes(with_degenerate, frame, 4))
        degenerate = trimesh.Trimesh(vertices=vertices[4:], faces=[[0, 1, 2]], process=False)
        with self.assertRaisesRegex(OctreeError, "non-degenerate"):
            build_surface_codes(degenerate, CanonicalFrame((0, 0.1, 0), 1), 4)

    def test_hierarchical_python_matches_flat_sat_oracle(self):
        fixtures = [
            trimesh.creation.box(extents=(1.0, 0.5, 0.25)),
            _sheet_x_zero(),
            _combine(_triangle((-0.3, 0, 0)), _triangle((0.3, 0, 0))),
        ]
        for fixture in fixtures:
            frame = CanonicalFrame.from_reference(fixture)
            expected = build_surface_codes(fixture, frame, 4)
            actual = build_surface_tree(fixture, frame, 4, backend="python")
            np.testing.assert_array_equal(expected, actual.leaf_codes())


class TestNativeParity(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("meshscope.voxblame._native") is not None,
        "native VoxBlame extension is not built in this environment",
    )
    def test_native_matches_python_and_flat_oracle(self):
        fixtures = [
            trimesh.creation.box(extents=(1.0, 0.5, 0.25)),
            _sheet_x_zero(),
            _combine(_triangle((-0.3, 0, 0)), _triangle((0.3, 0, 0))),
        ]
        for fixture in fixtures:
            frame = CanonicalFrame.from_reference(fixture)
            python_tree = build_surface_tree(fixture, frame, 5, backend="python")
            native_tree = build_surface_tree(fixture, frame, 5, backend="native")
            self.assertEqual(python_tree.masks, native_tree.masks)
            np.testing.assert_array_equal(python_tree.spans, native_tree.spans)
            self.assertEqual(python_tree.logical_sha256, native_tree.logical_sha256)
            np.testing.assert_array_equal(
                build_surface_codes(fixture, frame, 5),
                native_tree.leaf_codes(),
            )


class TestRunStep(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / "voxblame"
        self.anchor_left = _triangle((-0.48, -0.48, 0), 0.02)
        self.anchor_right = _triangle((0.48, 0.48, 0), 0.02)
        self.a = _triangle((-0.2, 0, 0), 0.05)
        self.b = _triangle((0.2, 0, 0), 0.05)
        self.reference = _combine(self.anchor_left, self.anchor_right, self.a, self.b)
        self.body = _combine(self.anchor_left, self.anchor_right, self.a)

    def test_state_sequence_retry_and_temp_residue(self):
        summary0 = run_step(self.reference, self.reference, self.state, 0, max_depth=4)
        self.assertEqual(0, summary0["remaining_error_count"])
        self.assertEqual(4, summary0["max_depth"])
        self.assertEqual("voxblame.svo/1", summary0["reference"]["storage_schema"])
        self.assertIn("logical_sha256", summary0["candidate"])
        self.assertFalse(summary0["no_observable_geometry_change"])
        expected = (
            self.state / "session.json",
            self.state / "reference.vbsvo",
            self.state / "steps/000000/candidate.vbsvo",
            self.state / "steps/000000/report.json",
            self.state / ".gitignore",
        )
        self.assertTrue(all(path.exists() for path in expected))
        self.assertEqual(".tmp-*\n", (self.state / ".gitignore").read_text())

        summary1 = run_step(self.reference, self.body, self.state, 1, max_depth=4)
        self.assertEqual(0, summary1["compare_to"])
        self.assertGreater(summary1["remaining_error_count"], 0)
        self.assertGreater(summary1["change_counts"]["introduced"], 0)
        self.assertFalse(summary1["no_observable_geometry_change"])
        self.assertEqual(
            summary1["next_action"]["region_handle"]["depth"],
            summary1["next_action"]["first_error_depth"],
        )

        summary2 = run_step(self.reference, self.reference, self.state, 2, max_depth=4, compare_to=0)
        self.assertEqual(0, summary2["compare_to"])
        self.assertEqual(0, summary2["remaining_error_count"])
        self.assertEqual(
            {"introduced": 0, "regressed": 0, "changed": 0, "improved": 0, "resolved": 0},
            summary2["change_counts"],
        )

        step1 = self.state / "steps/000001"
        before = {path.name: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in step1.iterdir()}
        self.assertEqual(summary1, run_step(self.reference, self.body, self.state, 1, max_depth=4))
        after = {path.name: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in step1.iterdir()}
        self.assertEqual(before, after)

        with self.assertRaisesRegex(OctreeError, "different candidate"):
            run_step(self.reference, self.reference, self.state, 1, max_depth=4)
        self.assertEqual(after, {path.name: (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in step1.iterdir()})

        (self.state / ".tmp-000003-dead").mkdir()
        run_step(self.reference, self.body, self.state, 3, max_depth=4, compare_to=1)
        self.assertTrue((self.state / "steps/000003").is_dir())

    def test_unchanged_candidate_sets_observable_change_signal(self):
        run_step(self.reference, self.body, self.state, 0, max_depth=4)
        summary = run_step(self.reference, self.body, self.state, 1, max_depth=4)
        self.assertTrue(summary["no_observable_geometry_change"])

    def test_report_matches_snapshots(self):
        run_step(self.reference, self.reference, self.state, 0, max_depth=4)
        run_step(self.reference, self.body, self.state, 1, max_depth=4)
        reference = read_surface_tree(self.state / "reference.vbsvo")
        candidate = read_surface_tree(
            self.state / "steps/000001/candidate.vbsvo"
        )
        report = json.loads((self.state / "steps/000001/report.json").read_text())
        self.assertEqual("voxblame.report/2", report["schema"])
        self.assertEqual(candidate.logical_sha256, report["candidate"]["logical_sha256"])
        self.assertEqual(candidate.node_count, report["candidate"]["node_count"])
        expected = grade_trees(reference, candidate)
        observed = [ErrorCell(int(item["morton_prefix"]), item["first_error_depth"], item["direction"]) for item in report["current"]["errors"]]
        self.assertEqual(expected, observed)
        counts = {kind: 0 for kind in ("introduced", "regressed", "changed", "improved", "resolved")}
        for item in report["changes"]:
            counts[item["change"]] += 1
        self.assertEqual(counts, report["overview"]["change_counts"])
        self.assertEqual(report["overview"]["next_action"], run_step(self.reference, self.body, self.state, 1, max_depth=4)["next_action"])

    def test_session_mismatch_fails_closed(self):
        run_step(self.reference, self.reference, self.state, 0, max_depth=4)
        with self.assertRaisesRegex(OctreeError, "max_depth"):
            run_step(self.reference, self.reference, self.state, 1, max_depth=3)
        session_path = self.state / "session.json"
        session = json.loads(session_path.read_text())
        session["frame"]["scale"] *= 2
        session_path.write_text(json.dumps(session))
        with self.assertRaisesRegex(OctreeError, "frame metadata"):
            run_step(self.reference, self.reference, self.state, 1, max_depth=4)
        self.assertFalse((self.state / "steps/000001").exists())

    def test_reference_file_bytes_and_published_report_fail_closed(self):
        reference_path = Path(self.temporary.name) / "reference.obj"
        candidate_path = Path(self.temporary.name) / "candidate.obj"
        self.reference.export(reference_path)
        self.body.export(candidate_path)
        run_step(reference_path, candidate_path, self.state, 0, max_depth=4)

        report_path = self.state / "steps/000000/report.json"
        report = json.loads(report_path.read_text())
        report["overview"]["remaining_error_count"] += 1
        report_path.write_text(json.dumps(report))
        with self.assertRaisesRegex(OctreeError, "modified"):
            run_step(reference_path, candidate_path, self.state, 0, max_depth=4)

        reference_path.write_bytes(reference_path.read_bytes() + b"\n# changed bytes\n")
        with self.assertRaisesRegex(OctreeError, "reference mesh"):
            run_step(reference_path, candidate_path, self.state, 1, max_depth=4)

    def test_session_one_is_rejected_without_mutation(self):
        self.state.mkdir(parents=True)
        (self.state / "session.json").write_text(
            json.dumps({"schema": "voxblame.session/1"}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(OctreeError, "session schema"):
            run_step(self.reference, self.body, self.state, 1, max_depth=4, compare_to=0)
        self.assertFalse((self.state / "steps").exists())

    def test_candidate_fully_outside_publishes_global_missing_error(self):
        reference = trimesh.creation.box(extents=(1, 1, 1))
        candidate = reference.copy()
        candidate.apply_translation([2, 0, 0])
        summary = run_step(reference, candidate, self.state, 0, max_depth=3)
        report = json.loads((self.state / "steps/000000/report.json").read_text())
        self.assertEqual(1, summary["remaining_error_count"])
        self.assertEqual(1, len(report["current"]["errors"]))
        error = report["current"]["errors"][0]
        self.assertEqual("missing", error["direction"])
        self.assertEqual(0, error["first_error_depth"])
        self.assertEqual("0", error["morton_prefix"])
        self.assertEqual(
            {"depth": 0, "octant_prefix": "0"},
            error["region_handle"],
        )


if __name__ == "__main__":
    unittest.main()
