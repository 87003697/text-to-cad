"""Repair Target behavior through the public measurement and paging seams."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
import trimesh

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")

from meshscope.voxblame import (  # noqa: E402
    active_repair_depth,
    inspect_repair_frontier,
    measure_step,
    page_repair_targets,
    prepare_reference,
    tree_from_codes,
)
from meshscope.voxblame.targets import partition_repair_targets  # noqa: E402


def _write_double_ply(path: Path, mesh: trimesh.Trimesh) -> None:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    lines = [
        "ply",
        "format ascii 1.0",
        f"element vertex {len(vertices)}",
        "property double x",
        "property double y",
        "property double z",
        f"element face {len(faces)}",
        "property list uchar int vertex_indices",
        "end_header",
    ]
    lines.extend(
        " ".join(format(float(value), ".17g") for value in vertex)
        for vertex in vertices
    )
    lines.extend(
        f"3 {int(face[0])} {int(face[1])} {int(face[2])}" for face in faces
    )
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _thin_triangle() -> trimesh.Trimesh:
    return trimesh.Trimesh(
        vertices=[[-0.5, -0.001, 0.0], [0.5, -0.001, 0.0], [-0.5, 0.001, 0.0]],
        faces=[[0, 1, 2]],
        process=False,
    )


def _narrow_rectangle() -> trimesh.Trimesh:
    return trimesh.Trimesh(
        vertices=[[-0.5, -0.04, 0.0], [0.5, -0.04, 0.0], [0.5, 0.04, 0.0], [-0.5, 0.04, 0.0]],
        faces=[[0, 1, 2], [0, 2, 3]],
        process=False,
    )


def _triangle_row(count: int) -> trimesh.Trimesh:
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    for x in np.linspace(-0.45, 0.45, count):
        start = len(vertices)
        vertices.extend(
            [
                [float(x) - 0.01, -0.01, 0.0],
                [float(x) + 0.01, -0.01, 0.0],
                [float(x) - 0.01, 0.01, 0.0],
            ]
        )
        faces.append([start, start + 1, start + 2])
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


class RepairTargetPublicSeamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def publish(self, source: trimesh.Trimesh, transform=None) -> tuple[dict, Path]:
        raw = self.root / "raw-reference.ply"
        _write_double_ply(raw, source)
        reference = self.root / "reference"
        prepare_reference(raw, reference)
        candidate_mesh = trimesh.load(
            reference / "reference.ply", force="mesh", process=False
        )
        if transform is not None:
            transform(candidate_mesh)
        candidate = self.root / "candidate.ply"
        _write_double_ply(candidate, candidate_mesh)
        workspace = self.root / "voxblame"
        summary = measure_step(
            reference, candidate, workspace, step=0, backend="python"
        ).summary
        return summary, workspace

    def all_pages(self, workspace: Path) -> list[dict]:
        items: list[dict] = []
        offset = 0
        while True:
            page = page_repair_targets(workspace, step=0, offset=offset)
            items.extend(page["items"])
            if page["next_offset"] is None:
                self.assertEqual(page["total"], len(items))
                return items
            offset = page["next_offset"]

    def test_empty_error_publishes_empty_terminal_first_page(self) -> None:
        summary, workspace = self.publish(_thin_triangle())

        expected = {
            "ordering_profile": "repair_target_display/1",
            "total": 0,
            "returned": 0,
            "remaining": 0,
            "offset": 0,
            "next_offset": None,
            "items": [],
        }
        self.assertEqual(expected, summary["repair_targets"])
        self.assertEqual(expected, page_repair_targets(workspace, step=0))

    def test_mixed_direction_error_stays_in_a_shared_target(self) -> None:
        summary, workspace = self.publish(
            _thin_triangle(),
            lambda mesh: mesh.apply_translation([0.0, 2 / 256, 0.0]),
        )

        targets = self.all_pages(workspace)
        self.assertTrue(
            any(
                target["error_profile"]["missing_surface_count"] > 0
                and target["error_profile"]["excess_surface_count"] > 0
                for target in targets
                if target["kind"] == "interior"
            )
        )
        self.assertEqual(summary["repair_targets"]["items"], targets[:8])

    def test_large_exact_error_stays_one_macro_target_at_coarse_frontier(self) -> None:
        summary, workspace = self.publish(
            _narrow_rectangle(),
            lambda mesh: mesh.apply_translation([0.0, 0.0, 0.03]),
        )

        targets = self.all_pages(workspace)
        interior = [
            target
            for target in targets
            if target["kind"] == "interior"
        ]
        self.assertEqual(1, len(interior))
        self.assertEqual("not_split", interior[0]["component"]["split_reason"])
        self.assertEqual(
            summary["errors_by_depth"][-1]["surface_error_count"],
            interior[0]["error_profile"]["surface_error_count"],
        )
        self.assertEqual(summary["repair_targets"]["total"], len(targets))
        self.assertEqual(
            list(range(len(targets))),
            [target["display_rank"] for target in targets],
        )

    def test_thin_error_keeps_a_voxel_thin_exact_bound(self) -> None:
        summary, workspace = self.publish(
            _thin_triangle(),
            lambda mesh: mesh.apply_translation([0.0, 0.0, 1 / 256]),
        )

        target = next(
            target
            for target in self.all_pages(workspace)
            if target["kind"] == "interior"
        )
        widths = [
            upper - lower
            for lower, upper in zip(
                target["bounds_canonical"]["min"],
                target["bounds_canonical"]["max"],
                strict=True,
            )
        ]
        self.assertLessEqual(min(widths), 2 / 256)
        self.assertEqual(summary["repair_targets"]["items"][0], target)

    def test_disconnected_exact_errors_share_a_stable_macro_target(self) -> None:
        def keep_first_patch(mesh: trimesh.Trimesh) -> None:
            mesh.update_faces([0])
            mesh.remove_unreferenced_vertices()

        summary, workspace = self.publish(_triangle_row(10), keep_first_patch)
        targets = self.all_pages(workspace)

        self.assertEqual(1, summary["repair_targets"]["total"])
        self.assertEqual(1, len(targets))
        self.assertGreater(targets[0]["mask"]["region_count"], 1)
        self.assertEqual(summary["repair_targets"]["items"], targets)
        self.assertEqual(targets, self.all_pages(workspace))

class AdaptiveRepairResolutionTests(unittest.TestCase):
    @staticmethod
    def evidence(*counts: int) -> list[dict[str, int]]:
        return [
            {"depth": depth, "surface_error_count": count}
            for depth, count in enumerate(counts, start=1)
        ]

    def test_active_depth_is_the_coarsest_failing_depth(self) -> None:
        self.assertEqual(
            3,
            active_repair_depth(self.evidence(0, 0, 2, 8, 12, 20, 30, 40)),
        )
        self.assertIsNone(active_repair_depth(self.evidence(*(0 for _ in range(8)))))

    def test_legacy_workspace_does_not_claim_an_adaptive_frontier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw.ply"
            _write_double_ply(raw, _thin_triangle())
            reference = root / "reference"
            prepare_reference(raw, reference)
            workspace = root / "voxblame"
            measure_step(
                reference,
                reference / "reference.ply",
                workspace,
                step=0,
                backend="python",
            )
            session_path = workspace / "session.json"
            session = json.loads(session_path.read_text(encoding="utf-8"))
            session["profiles"]["target_partition"] = (
                "repair_target_partition/1"
            )
            session_path.write_text(json.dumps(session), encoding="utf-8")
            empty_page = {
                "ordering_profile": "repair_target_display/1",
                "total": 0,
                "returned": 0,
                "remaining": 0,
                "offset": 0,
                "next_offset": None,
                "items": [],
            }
            with mock.patch(
                "meshscope.voxblame.targets.page_repair_targets",
                return_value=empty_page,
            ):
                view = inspect_repair_frontier(workspace, step=0)

        self.assertEqual({"active_depth": None}, view["repair_frontier"])

    def test_active_depth_groups_disconnected_exact_cells_without_diluting_mask(
        self,
    ) -> None:
        exact_cells = {0, (1 << 21) - 1}
        empty = tree_from_codes([], 8)
        missing = tree_from_codes(exact_cells, 8)

        adaptive = partition_repair_targets(
            missing,
            empty,
            active_depth=1,
            source_step=0,
        )
        exact = partition_repair_targets(
            missing,
            empty,
            active_depth=8,
            source_step=0,
        )

        self.assertEqual(1, adaptive.report["total"])
        self.assertEqual(2, exact.report["total"])
        target = adaptive.report["ordered_targets"][0]
        self.assertEqual(2, target["error_profile"]["surface_error_count"])
        self.assertEqual(2, target["mask"]["region_count"])
        self.assertEqual(
            {"min": [-0.5, -0.5, -0.5], "max": [0.0, 0.0, 0.0]},
            target["bounds_canonical"],
        )

    def test_larger_objective_error_target_is_displayed_first(self) -> None:
        missing = tree_from_codes({0, 1, (1 << 24) - 1}, 8)
        partition = partition_repair_targets(
            missing,
            tree_from_codes([], 8),
            active_depth=8,
            source_step=0,
        )

        self.assertEqual(
            [2, 1],
            [
                target["error_profile"]["surface_error_count"]
                for target in partition.report["ordered_targets"]
            ],
        )


if __name__ == "__main__":
    unittest.main()
