"""Repair Batch and Region Diff behavior through the public Workspace seam."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
import trimesh

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")

from meshscope.voxblame import (  # noqa: E402
    OctreeError,
    measure_step,
    prepare_reference,
    publish_region_diff,
    validate_region_diff_contract,
)


def _thin_triangle() -> trimesh.Trimesh:
    return trimesh.Trimesh(
        vertices=np.array(
            [[-0.5, -0.001, 0.0], [0.5, -0.001, 0.0], [-0.5, 0.001, 0.0]],
            dtype=np.float64,
        ),
        faces=[[0, 1, 2]],
        process=False,
    )


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


def _write_double_ply(path: Path, mesh: trimesh.Trimesh, *, comment: str) -> None:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    lines = [
        "ply",
        "format ascii 1.0",
        f"comment {comment}",
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


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value))
    return set()


def _rehash_region_diff(value: dict) -> None:
    document = dict(value)
    document.pop("identity")
    data = (
        json.dumps(document, indent=2, sort_keys=True, separators=(",", ": "))
        + "\n"
    ).encode("utf-8")
    value["identity"] = {
        "region_diff_sha256": hashlib.sha256(
            b"voxblame.region-diff/1\0" + data
        ).hexdigest()
    }


class RegionDiffWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        raw = self.root / "raw.ply"
        _thin_triangle().export(raw)
        self.reference = self.root / "input"
        prepare_reference(raw, self.reference)
        self.workspace = self.root / "voxblame"

    def _plan(self, targets: list[dict], *, from_step: int = 0) -> dict:
        selected = [
            {
                "target_key": target["target_key"],
                "mask_sha256": target["mask"]["logical_sha256"],
            }
            for target in targets
        ]
        return {
            "schema": "voxblame.repair-batch/1",
            "from_step": from_step,
            "selected_targets": selected,
            "planned_edits": [
                {
                    "edit_key": "edit-selected-regions",
                    "target_keys": [item["target_key"] for item in selected],
                    "description": "Edit the selected fixed regions.",
                }
            ],
            "rationale": "One coherent change for the selected regions.",
            "preview_observation": "The selected regions contain visible residuals.",
        }

    def test_outside_selected_reports_new_collateral_components_objectively(
        self,
    ) -> None:
        raw = self.root / "row-reference.ply"
        _disconnected_triangles(4).export(raw)
        reference = self.root / "row-input"
        prepare_reference(raw, reference)
        reference_mesh = trimesh.load(
            reference / "reference.ply", force="mesh", process=False
        )
        before_mesh = reference_mesh.copy()
        before_mesh.update_faces([0])
        before_mesh.remove_unreferenced_vertices()
        before = self.root / "row-before.ply"
        before_mesh.export(before)
        workspace = self.root / "row-voxblame"
        measured = measure_step(reference, before, workspace, step=0).summary

        collateral = trimesh.Trimesh(
            vertices=[[-0.04, 0.2, 0.0], [0.04, 0.2, 0.0], [-0.04, 0.28, 0.0]],
            faces=[[0, 1, 2]],
            process=False,
        )
        after_mesh = trimesh.util.concatenate([reference_mesh, collateral])
        after = self.root / "row-after.ply"
        after_mesh.export(after)
        measure_step(reference, after, workspace, step=3, compare_to=0)
        selected_target = measured["repair_targets"]["items"][0]

        diff = publish_region_diff(
            workspace,
            from_step=0,
            to_step=3,
            repair_plan=self._plan([selected_target]),
            output=self.root / "collateral-region-diff.json",
        ).region_diff

        outside = diff["outside_selected_regions"]["interior"]
        self.assertEqual([0, 3], diff["measurement_trajectory"]["steps"])
        self.assertGreater(outside["new_excess_surface_count"], 0)
        self.assertEqual(
            outside["new_surface_error_count"],
            outside["new_missing_surface_count"]
            + outside["new_excess_surface_count"],
        )
        self.assertTrue(outside["largest_new_components"])
        component = outside["largest_new_components"][0]
        self.assertEqual(
            {
                "missing_surface_count",
                "excess_surface_count",
                "surface_error_count",
                "bounds_canonical",
            },
            set(component),
        )
        forbidden = {
            "improved",
            "regressed",
            "resolved",
            "introduced",
            "keep",
            "revert",
        }
        self.assertFalse(forbidden & _all_keys(diff))

    def test_batch_keeps_exterior_exact_halo_and_containment_separate(self) -> None:
        exterior = trimesh.Trimesh(
            vertices=np.array(
                [[0.6, -0.05, 0.0], [0.7, -0.05, 0.0], [0.6, 0.05, 0.0]],
                dtype=np.float64,
            ),
            faces=[[0, 1, 2]],
            process=False,
        )
        before = self.root / "exterior.obj"
        exterior.export(before)
        measured = measure_step(
            self.reference, before, self.workspace, step=0
        ).summary
        measure_step(
            self.reference,
            self.reference / "reference.ply",
            self.workspace,
            step=1,
            compare_to=0,
        )
        selected = [
            {
                "target_key": target["target_key"],
                "mask_sha256": target["mask"]["logical_sha256"],
            }
            for target in measured["repair_targets"]["items"]
        ]
        self.assertEqual({"interior", "exterior"}, {
            target["kind"] for target in measured["repair_targets"]["items"]
        })
        plan = {
            "schema": "voxblame.repair-batch/1",
            "from_step": 0,
            "selected_targets": selected,
            "planned_edits": [
                {
                    "edit_key": "restore-canonical-surface",
                    "target_keys": [item["target_key"] for item in selected],
                    "description": "Restore the canonical surface in one edit.",
                }
            ],
            "rationale": (
                "Replace the exterior-only candidate with the canonical surface."
            ),
            "preview_observation": "The candidate is outside the canonical cube.",
        }

        diff = publish_region_diff(
            self.workspace,
            from_step=0,
            to_step=1,
            repair_plan=plan,
            output=self.root / "region-diff.json",
        ).region_diff

        exterior_region = next(
            region for region in diff["selected_regions"]
            if region["kind"] == "exterior"
        )
        evidence = exterior_region["exterior"]
        self.assertIsNone(exterior_region["interior"])
        self.assertGreater(
            evidence["exact_region"]["before"]["excess_surface_count"], 0
        )
        self.assertEqual(0, evidence["exact_region"]["after"]["excess_surface_count"])
        self.assertGreater(evidence["halo"]["cell_count"], 0)
        self.assertEqual(
            exterior_region["exact_mask"]["region_count"],
            evidence["exact_region"]["cell_count"],
        )
        self.assertTrue(evidence["containment"]["before"]["surface_present"])
        self.assertFalse(evidence["containment"]["after"]["surface_present"])
        self.assertEqual(evidence, diff["batch_union"]["exterior"])

    def test_no_op_is_objective_idempotent_and_identity_validated(self) -> None:
        before = self.root / "before.ply"
        shifted = _thin_triangle()
        shifted.apply_translation([0.0, 0.02, 0.0])
        _write_double_ply(before, shifted, comment="before")
        measured = measure_step(
            self.reference, before, self.workspace, step=0
        ).summary

        same_geometry = self.root / "same-geometry.ply"
        _write_double_ply(same_geometry, shifted, comment="after")
        self.assertNotEqual(before.read_bytes(), same_geometry.read_bytes())
        after = measure_step(
            self.reference,
            same_geometry,
            self.workspace,
            step=2,
            compare_to=0,
        ).summary
        self.assertTrue(after["no_observable_geometry_change"])
        plan = self._plan([measured["repair_targets"]["items"][0]])
        output = self.root / "no-op-region-diff.json"

        first = publish_region_diff(
            self.workspace,
            from_step=0,
            to_step=2,
            repair_plan=plan,
            output=output,
        )
        second = publish_region_diff(
            self.workspace,
            from_step=0,
            to_step=2,
            repair_plan=dict(reversed(list(plan.items()))),
            output=output,
        )

        self.assertFalse(first.idempotent)
        self.assertTrue(second.idempotent)
        trajectory = first.region_diff["measurement_trajectory"]
        self.assertFalse(trajectory["observable_geometry"]["changed"])
        for depth in trajectory["errors_by_depth"]:
            self.assertEqual(
                {key: 0 for key in depth["delta"]}, depth["delta"]
            )
        validate_region_diff_contract(first.region_diff)
        tampered = copy.deepcopy(first.region_diff)
        tampered["repair_batch"]["planned_edits"][0]["target_keys"] = []
        with self.assertRaises(ValueError):
            validate_region_diff_contract(tampered)

        nested_unknown = copy.deepcopy(first.region_diff)
        nested_unknown["measurement_trajectory"]["verdict"] = "keep"
        _rehash_region_diff(nested_unknown)
        with self.assertRaises(ValueError):
            validate_region_diff_contract(nested_unknown)

        changed_plan = copy.deepcopy(plan)
        changed_plan["rationale"] = "A different frozen rationale."
        with self.assertRaises(OctreeError):
            publish_region_diff(
                self.workspace,
                from_step=0,
                to_step=2,
                repair_plan=changed_plan,
                output=output,
            )

        race_output = self.root / "race-region-diff.json"
        real_link = os.link

        def concurrent_publication(source: str, destination: str) -> None:
            Path(destination).write_text("concurrent winner\n", encoding="utf-8")
            real_link(source, destination)

        with mock.patch(
            "meshscope.voxblame.region_diff.os.link",
            side_effect=concurrent_publication,
        ), self.assertRaises(OctreeError):
            publish_region_diff(
                self.workspace,
                from_step=0,
                to_step=2,
                repair_plan=plan,
                output=race_output,
            )
        self.assertEqual(
            "concurrent winner\n", race_output.read_text(encoding="utf-8")
        )

    def test_direction_transition_reports_both_objective_ends(self) -> None:
        before_mesh = _thin_triangle()
        before_mesh.apply_translation([0.0, 1 / 256, 0.0])
        before = self.root / "direction-before.ply"
        _write_double_ply(before, before_mesh, comment="direction before")
        measured = measure_step(
            self.reference, before, self.workspace, step=0
        ).summary

        after_mesh = _thin_triangle()
        after_mesh.apply_translation([0.0, -1 / 256, 0.0])
        after = self.root / "direction-after.ply"
        _write_double_ply(after, after_mesh, comment="direction after")
        measure_step(
            self.reference, after, self.workspace, step=1, compare_to=0
        )
        target = max(
            measured["repair_targets"]["items"],
            key=lambda item: item["error_profile"]["missing_surface_count"],
        )

        diff = publish_region_diff(
            self.workspace,
            from_step=0,
            to_step=1,
            repair_plan=self._plan([target]),
            output=self.root / "direction-region-diff.json",
        ).region_diff

        transition = diff["selected_regions"][0]["interior"][
            "direction_transitions"
        ]["missing_to_excess"]
        self.assertGreater(transition["before_missing_not_after_count"], 0)
        self.assertGreater(transition["after_excess_not_before_count"], 0)

    def test_exterior_comparison_rejects_coarser_than_frozen_evidence(self) -> None:
        near = trimesh.Trimesh(
            vertices=[[0.6, -0.05, 0.0], [0.7, -0.05, 0.0], [0.6, 0.05, 0.0]],
            faces=[[0, 1, 2]],
            process=False,
        )
        near_path = self.root / "near-exterior.ply"
        _write_double_ply(near_path, near, comment="near exterior")
        measured = measure_step(
            self.reference, near_path, self.workspace, step=0
        ).summary

        far = trimesh.Trimesh(
            vertices=[
                [1e6, -1e6, 0.0],
                [2e6, -1e6, 0.0],
                [1e6, 1e6, 0.0],
            ],
            faces=[[0, 1, 2]],
            process=False,
        )
        far_path = self.root / "far-exterior.ply"
        _write_double_ply(far_path, far, comment="far exterior")
        later = measure_step(
            self.reference, far_path, self.workspace, step=1, compare_to=0
        ).summary
        exterior_target = next(
            target
            for target in measured["repair_targets"]["items"]
            if target["kind"] == "exterior"
        )
        self.assertLess(
            later["exterior_surface"]["diagnostic_grid_depth"],
            exterior_target["exterior"]["diagnostic_grid_depth"],
        )

        with self.assertRaisesRegex(OctreeError, "coarser.*frozen"):
            publish_region_diff(
                self.workspace,
                from_step=0,
                to_step=1,
                repair_plan=self._plan([exterior_target]),
                output=self.root / "coarse-exterior-region-diff.json",
            )

    def test_repair_batch_rejects_stale_targets_and_unstable_mappings(self) -> None:
        shifted = _thin_triangle()
        shifted.apply_translation([0.0, 0.02, 0.0])
        before = self.root / "invalid-plan-before.ply"
        _write_double_ply(before, shifted, comment="invalid plan before")
        measured = measure_step(
            self.reference, before, self.workspace, step=0
        ).summary
        measure_step(
            self.reference,
            self.reference / "reference.ply",
            self.workspace,
            step=1,
            compare_to=0,
        )
        target = measured["repair_targets"]["items"][0]
        valid = self._plan([target])
        invalid_plans = []

        empty = copy.deepcopy(valid)
        empty["selected_targets"] = []
        invalid_plans.append(empty)

        stale = copy.deepcopy(valid)
        stale["selected_targets"][0]["mask_sha256"] = "0" * 64
        invalid_plans.append(stale)

        unstable = copy.deepcopy(valid)
        unstable["planned_edits"][0]["edit_key"] = "Not Stable"
        invalid_plans.append(unstable)

        unmapped = copy.deepcopy(valid)
        unmapped["planned_edits"][0]["target_keys"] = [
            "step-000000:target-not-selected"
        ]
        invalid_plans.append(unmapped)

        for index, plan in enumerate(invalid_plans):
            with self.subTest(index=index), self.assertRaises(OctreeError):
                publish_region_diff(
                    self.workspace,
                    from_step=0,
                    to_step=1,
                    repair_plan=plan,
                    output=self.root / f"invalid-{index}.json",
                )
            self.assertFalse((self.root / f"invalid-{index}.json").exists())

    def test_new_exterior_surface_stays_in_outside_selected_space(self) -> None:
        shifted = _thin_triangle()
        shifted.apply_translation([0.0, 0.02, 0.0])
        before = self.root / "interior-before.ply"
        _write_double_ply(before, shifted, comment="interior before")
        measured = measure_step(
            self.reference, before, self.workspace, step=0
        ).summary

        exterior = trimesh.Trimesh(
            vertices=[[0.6, -0.05, 0.0], [0.7, -0.05, 0.0], [0.6, 0.05, 0.0]],
            faces=[[0, 1, 2]],
            process=False,
        )
        after_mesh = trimesh.util.concatenate([shifted, exterior])
        after = self.root / "interior-plus-exterior.ply"
        _write_double_ply(after, after_mesh, comment="new exterior")
        measure_step(
            self.reference, after, self.workspace, step=1, compare_to=0
        )

        diff = publish_region_diff(
            self.workspace,
            from_step=0,
            to_step=1,
            repair_plan=self._plan([measured["repair_targets"]["items"][0]]),
            output=self.root / "new-exterior-region-diff.json",
        ).region_diff

        outside = diff["outside_selected_regions"]["exterior"]
        self.assertGreater(outside["new_excess_surface_count"], 0)
        self.assertTrue(outside["largest_new_components"])
        component = outside["largest_new_components"][0]
        self.assertEqual(0, component["missing_surface_count"])
        self.assertEqual(
            component["excess_surface_count"], component["surface_error_count"]
        )
        self.assertEqual({"min", "max"}, set(component["bounds_canonical"]))


if __name__ == "__main__":
    unittest.main()
