"""Public ``mesh-compare voxblame-diff`` integration tests."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import trimesh

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")
add_repo_path("skills/mesh-compare/scripts/mesh-compare")

import cli  # noqa: E402
from meshscope.voxblame import measure_step, prepare_reference  # noqa: E402


def _disconnected_triangles(count: int) -> trimesh.Trimesh:
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


class RegionDiffCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

        raw = self.root / "raw-reference.ply"
        _disconnected_triangles(4).export(raw)
        self.reference = self.root / "input"
        prepare_reference(raw, self.reference)

        reference_mesh = trimesh.load(
            self.reference / "reference.ply", force="mesh", process=False
        )
        before_mesh = reference_mesh.copy()
        before_mesh.update_faces([0])
        before_mesh.remove_unreferenced_vertices()
        before = self.root / "before.ply"
        before_mesh.export(before)

        self.workspace = self.root / "voxblame"
        measure_step(self.reference, before, self.workspace, step=0)
        measure_step(
            self.reference,
            self.reference / "reference.ply",
            self.workspace,
            step=1,
            compare_to=0,
        )

    def invoke(self, *arguments: str) -> tuple[int, dict, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(list(arguments))
        return status, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_multi_target_batch_publishes_objective_region_diff(self) -> None:
        measurement = json.loads(
            (self.workspace / "steps/000000/measurement.json").read_text(
                encoding="utf-8"
            )
        )
        targets = measurement["repair_targets"]["ordered_targets"][:2]
        self.assertEqual(2, len(targets))
        selected = [
            {
                "target_key": target["target_key"],
                "mask_sha256": target["mask"]["logical_sha256"],
            }
            for target in targets
        ]
        plan = {
            "schema": "voxblame.repair-batch/1",
            "from_step": 0,
            "selected_targets": selected,
            "planned_edits": [
                {
                    "edit_key": "restore-disconnected-surfaces",
                    "target_keys": [item["target_key"] for item in selected],
                    "description": "Restore the two selected surface patches.",
                },
                {
                    "edit_key": "align-first-patch",
                    "target_keys": [selected[0]["target_key"]],
                    "description": "Keep the first restored patch aligned.",
                },
            ],
            "rationale": "One coherent restoration of disconnected source patches.",
            "preview_observation": "The selected patches are absent in step 0.",
        }
        plan_path = self.root / "repair-batch.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        output = self.root / "region-diff.json"

        status, payload, stderr = self.invoke(
            "voxblame-diff",
            "--workspace",
            str(self.workspace),
            "--from-step",
            "0",
            "--to-step",
            "1",
            "--repair-plan",
            str(plan_path),
            "--output",
            str(output),
        )

        self.assertEqual(0, status, stderr)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["idempotent"])
        diff = payload["region_diff"]
        self.assertEqual("voxblame.region-diff/1", diff["schema"])
        self.assertEqual([0, 1], diff["measurement_trajectory"]["steps"])
        self.assertEqual(
            [item["target_key"] for item in selected],
            [item["target_key"] for item in diff["repair_batch"]["selected_targets"]],
        )
        self.assertEqual(
            {"align-first-patch", "restore-disconnected-surfaces"},
            {
                item["edit_key"]
                for item in diff["repair_batch"]["planned_edits"]
            },
        )
        self.assertEqual(64, len(diff["repair_batch"]["plan_sha256"]))
        self.assertEqual(64, len(diff["identity"]["region_diff_sha256"]))
        self.assertEqual(2, len(diff["selected_regions"]))
        for region in diff["selected_regions"]:
            depth_eight = region["interior"]["errors_by_depth"][-1]
            self.assertGreater(
                depth_eight["before"]["surface_error_depth8_equivalent_count"],
                0,
            )
            self.assertEqual(
                0,
                depth_eight["after"]["surface_error_depth8_equivalent_count"],
            )
            self.assertEqual(8, region["interior"]["halo"]["grid_depth"])
        self.assertEqual(diff, json.loads(output.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
