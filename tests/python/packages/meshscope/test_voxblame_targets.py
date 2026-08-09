"""Public Repair Target partition and paging behavior."""

from __future__ import annotations

import unittest

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")

from meshscope.voxblame import (  # noqa: E402
    TARGET_PARTITION_PROFILE,
    TARGET_SPLIT_DEPTH,
    TARGET_SPLIT_MAX_CELLS,
    partition_repair_targets,
    repair_target_page,
    tree_from_codes,
)


def _morton_encode(x: int, y: int, z: int, depth: int = 8) -> int:
    code = 0
    for shift in range(depth - 1, -1, -1):
        code = (
            (code << 3)
            | (((x >> shift) & 1) << 2)
            | (((y >> shift) & 1) << 1)
            | ((z >> shift) & 1)
        )
    return code


class RepairTargetPartitionTests(unittest.TestCase):
    def test_eighteen_connected_mixed_error_is_one_complete_exact_target(self) -> None:
        missing = tree_from_codes([0, 3], 8)
        excess = tree_from_codes([1], 8)

        partition = partition_repair_targets(missing, excess, source_step=2)

        self.assertEqual(1, len(partition.targets))
        target = partition.targets[0]
        self.assertEqual(frozenset({0, 3}), target.missing_codes)
        self.assertEqual(frozenset({1}), target.excess_codes)
        self.assertEqual(frozenset({0, 1, 3}), target.mask_codes)
        self.assertEqual("not_split", target.split_reason)
        self.assertEqual(0, target.split_index)
        self.assertEqual(1, target.split_count)
        self.assertEqual(
            frozenset({0, 1, 3}),
            frozenset().union(*(item.mask_codes for item in partition.targets)),
        )

    def test_corner_touching_cells_are_disconnected_under_eighteen_connectivity(
        self,
    ) -> None:
        partition = partition_repair_targets(
            tree_from_codes([0, 7], 8),
            tree_from_codes([], 8),
            source_step=0,
        )

        self.assertEqual(2, len(partition.targets))
        self.assertEqual(
            [frozenset({0}), frozenset({7})],
            [target.mask_codes for target in partition.targets],
        )

    def test_target_identity_binds_missing_and_excess_direction_facts(self) -> None:
        missing = partition_repair_targets(
            tree_from_codes([0], 8),
            tree_from_codes([], 8),
            source_step=6,
        )
        excess = partition_repair_targets(
            tree_from_codes([], 8),
            tree_from_codes([0], 8),
            source_step=6,
        )

        self.assertEqual(
            missing.report["ordered_targets"][0]["mask"]["logical_sha256"],
            excess.report["ordered_targets"][0]["mask"]["logical_sha256"],
        )
        self.assertNotEqual(
            missing.targets[0].target_key,
            excess.targets[0].target_key,
        )

    def test_oversized_component_uses_versioned_coarse_octree_split_profile(
        self,
    ) -> None:
        first_block = {
            _morton_encode(x, y, z)
            for x in range(16)
            for y in range(16)
            for z in range(16)
        }
        connected_next_block_cell = _morton_encode(16, 0, 0)
        codes = first_block | {connected_next_block_cell}
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

        self.assertEqual(first, second)
        self.assertEqual(2, len(first.targets))
        self.assertEqual({2}, {target.split_count for target in first.targets})
        self.assertEqual([0, 1], sorted(target.split_index for target in first.targets))
        self.assertEqual(
            {"coarse_octree_locality"},
            {target.split_reason for target in first.targets},
        )
        self.assertEqual(1, len({target.component_key for target in first.targets}))
        self.assertEqual(codes, set().union(*(target.mask_codes for target in first.targets)))
        self.assertTrue(
            all(target.target_key.startswith("step-000004:target-") for target in first.targets)
        )

    def test_oversized_split_keeps_each_coarse_locality_chunk_connected(self) -> None:
        second_block = {
            _morton_encode(x, y, z)
            for x in range(16, 32)
            for y in range(16)
            for z in range(16)
        }
        separated_first_block_cells = {
            _morton_encode(15, 0, 0),
            _morton_encode(15, 15, 15),
        }
        codes = second_block | separated_first_block_cells

        partition = partition_repair_targets(
            tree_from_codes(codes, 8),
            tree_from_codes([], 8),
            source_step=5,
        )

        self.assertEqual(3, len(partition.targets))
        self.assertEqual(
            [1, 1, TARGET_SPLIT_MAX_CELLS],
            sorted(len(target.mask_codes) for target in partition.targets),
        )
        self.assertEqual(codes, set().union(*(target.mask_codes for target in partition.targets)))

    def test_thin_component_keeps_exact_one_cell_wide_bounds(self) -> None:
        codes = {_morton_encode(x, 40, 80) for x in range(12, 44)}

        partition = partition_repair_targets(
            tree_from_codes(codes, 8),
            tree_from_codes([], 8),
            source_step=1,
        )

        self.assertEqual(1, len(partition.targets))
        target = partition.report["ordered_targets"][0]
        self.assertEqual(32, target["error_profile"]["missing_surface_count"])
        self.assertEqual(
            1 / 256,
            target["bounds_canonical"]["max"][1]
            - target["bounds_canonical"]["min"][1],
        )
        self.assertEqual(
            1 / 256,
            target["bounds_canonical"]["max"][2]
            - target["bounds_canonical"]["min"][2],
        )

    def test_empty_error_has_an_empty_terminal_page(self) -> None:
        partition = partition_repair_targets(
            tree_from_codes([], 8),
            tree_from_codes([], 8),
            source_step=0,
        )

        self.assertEqual((), partition.targets)
        self.assertEqual(
            {
                "ordering_profile": "repair_target_display/1",
                "total": 0,
                "returned": 0,
                "remaining": 0,
                "offset": 0,
                "next_offset": None,
                "items": [],
            },
            repair_target_page(partition.report),
        )

    def test_every_disconnected_target_is_reachable_through_stable_pages(self) -> None:
        codes = {_morton_encode(x, 0, 0) for x in range(0, 30, 3)}
        partition = partition_repair_targets(
            tree_from_codes(codes, 8),
            tree_from_codes([], 8),
            source_step=3,
        )

        first = repair_target_page(partition.report)
        second = repair_target_page(partition.report, offset=first["next_offset"])

        self.assertEqual(8, first["returned"])
        self.assertEqual(2, first["remaining"])
        self.assertEqual(8, first["next_offset"])
        self.assertEqual(2, second["returned"])
        self.assertEqual(0, second["remaining"])
        self.assertIsNone(second["next_offset"])
        self.assertEqual(first, repair_target_page(partition.report))
        self.assertEqual(
            [target.target_key for target in partition.targets],
            [item["target_key"] for item in first["items"] + second["items"]],
        )


if __name__ == "__main__":
    unittest.main()
