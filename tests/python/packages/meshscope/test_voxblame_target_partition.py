"""Synthetic invariants for the frozen Repair Target partition profile."""

from __future__ import annotations

from pathlib import Path
import unittest

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")

from meshscope.voxblame.targets import (  # noqa: E402
    TARGET_PARTITION_PROFILE,
    TARGET_SPLIT_DEPTH,
    TARGET_SPLIT_MAX_CELLS,
    _connected_components,
    _expand_region_set,
    partition_repair_targets,
)
from meshscope.voxblame.tree import tree_from_codes  # noqa: E402
from tests.python.packages.meshscope.support.morton_oracle import (  # noqa: E402
    morton_encode,
)


def _code(x: int, y: int, z: int) -> int:
    return morton_encode(x, y, z, 8)


def _target_cells(partition, target: dict) -> set[int]:
    name = Path(target["mask"]["path"]).name
    return _expand_region_set(partition.mask_bytes[name])


class RepairTargetPartitionInvariantTests(unittest.TestCase):
    def test_mixed_error_masks_are_disjoint_and_cover_the_exact_union(self) -> None:
        missing = {0, 3}
        excess = {1}
        partition = partition_repair_targets(
            tree_from_codes(missing, 8),
            tree_from_codes(excess, 8),
            source_step=2,
        )
        targets = partition.report["ordered_targets"]
        masks = [_target_cells(partition, target) for target in targets]

        self.assertEqual(1, len(targets))
        self.assertEqual(missing | excess, set().union(*masks))
        self.assertEqual(sum(map(len, masks)), len(set().union(*masks)))
        self.assertEqual(
            {
                "missing_surface_count": 2,
                "excess_surface_count": 1,
                "surface_error_count": 3,
            },
            targets[0]["error_profile"],
        )

    def test_corner_touching_cells_are_disconnected_under_eighteen_connectivity(
        self,
    ) -> None:
        partition = partition_repair_targets(
            tree_from_codes([0, 7], 8),
            tree_from_codes([], 8),
            source_step=0,
        )

        self.assertEqual(
            [{0}, {7}],
            [
                _target_cells(partition, target)
                for target in partition.report["ordered_targets"]
            ],
        )

    def test_oversized_split_is_deterministic_bounded_and_connected(self) -> None:
        block = {
            _code(x, y, z)
            for x in range(16)
            for y in range(16)
            for z in range(16)
        }
        codes = block | {_code(16, 0, 0)}
        self.assertEqual(TARGET_SPLIT_MAX_CELLS + 1, len(codes))
        self.assertEqual(4, TARGET_SPLIT_DEPTH)
        self.assertEqual("repair_target_partition/1", TARGET_PARTITION_PROFILE)

        first = partition_repair_targets(
            tree_from_codes(codes, 8),
            tree_from_codes([], 8),
            source_step=4,
        )
        second = partition_repair_targets(
            tree_from_codes(reversed(sorted(codes)), 8),
            tree_from_codes([], 8),
            source_step=4,
        )
        targets = first.report["ordered_targets"]
        masks = [_target_cells(first, target) for target in targets]

        self.assertEqual(first, second)
        self.assertEqual(2, len(targets))
        self.assertEqual(codes, set().union(*masks))
        self.assertTrue(all(len(mask) <= TARGET_SPLIT_MAX_CELLS for mask in masks))
        self.assertTrue(
            all(
                len(_connected_components({code: 1 for code in mask})) == 1
                for mask in masks
            )
        )
        self.assertEqual(
            {"coarse_octree_locality"},
            {target["component"]["split_reason"] for target in targets},
        )

    def test_split_rechecks_connectivity_inside_each_coarse_bucket(self) -> None:
        second_block = {
            _code(x, y, z)
            for x in range(16, 32)
            for y in range(16)
            for z in range(16)
        }
        codes = second_block | {_code(15, 0, 0), _code(15, 15, 15)}

        partition = partition_repair_targets(
            tree_from_codes(codes, 8),
            tree_from_codes([], 8),
            source_step=5,
        )
        masks = [
            _target_cells(partition, target)
            for target in partition.report["ordered_targets"]
        ]

        self.assertEqual([1, 1, TARGET_SPLIT_MAX_CELLS], sorted(map(len, masks)))
        self.assertTrue(
            all(
                len(_connected_components({code: 1 for code in mask})) == 1
                for mask in masks
            )
        )

    def test_thin_component_has_exact_one_cell_wide_bounds(self) -> None:
        codes = {_code(x, 40, 80) for x in range(12, 44)}
        partition = partition_repair_targets(
            tree_from_codes(codes, 8),
            tree_from_codes([], 8),
            source_step=1,
        )
        bounds = partition.report["ordered_targets"][0]["bounds_canonical"]

        self.assertEqual(1 / 256, bounds["max"][1] - bounds["min"][1])
        self.assertEqual(1 / 256, bounds["max"][2] - bounds["min"][2])

    def test_empty_error_has_no_frozen_targets(self) -> None:
        partition = partition_repair_targets(
            tree_from_codes([], 8),
            tree_from_codes([], 8),
            source_step=0,
        )

        self.assertEqual(
            {
                "ordering_profile": "repair_target_display/1",
                "total": 0,
                "ordered_targets": [],
            },
            partition.report,
        )
        self.assertEqual({}, partition.mask_bytes)


if __name__ == "__main__":
    unittest.main()
