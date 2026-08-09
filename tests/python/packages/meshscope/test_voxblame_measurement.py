"""Public Measured Step behavior for canonical VoxBlame measurement."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import trimesh

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")

from meshscope.voxblame import (  # noqa: E402
    MEASUREMENT_SUMMARY_SCHEMA,
    measure_step,
    prepare_reference,
    read_surface_tree,
)


def _thin_triangle() -> trimesh.Trimesh:
    return trimesh.Trimesh(
        vertices=np.array(
            [[-0.5, -0.001, 0.0], [0.5, -0.001, 0.0], [-0.5, 0.001, 0.0]],
            dtype=np.float64,
        ),
        faces=np.array([[0, 1, 2]], dtype=np.int64),
        process=False,
    )


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
    lines.extend(f"3 {int(face[0])} {int(face[1])} {int(face[2])}" for face in faces)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


class VoxBlameMeasurementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = self.root / "raw-reference.ply"
        _thin_triangle().export(source)
        self.reference = self.root / "input"
        prepare_reference(source, self.reference)
        self.candidate = self.reference / "reference.ply"
        self.state = self.root / "voxblame"

    def test_identical_thin_open_step_zero_publishes_ordered_multiresolution_evidence(
        self,
    ) -> None:
        result = measure_step(
            self.reference,
            self.candidate,
            self.state,
            step=0,
        )

        self.assertFalse(result.idempotent)
        summary = result.summary
        self.assertEqual(MEASUREMENT_SUMMARY_SCHEMA, summary["schema"])
        self.assertEqual("trellis2_canonical/1", summary["coordinate_contract"])
        self.assertEqual(8, summary["max_depth"])
        self.assertEqual(0, summary["step"])
        self.assertIsNone(summary["compare_to"])
        self.assertEqual(
            list(range(1, 9)),
            [item["depth"] for item in summary["errors_by_depth"]],
        )
        for item in summary["errors_by_depth"]:
            self.assertGreater(item["reference_surface_count"], 0)
            self.assertEqual(
                item["reference_surface_count"], item["candidate_surface_count"]
            )
            self.assertEqual(0, item["missing_surface_count"])
            self.assertEqual(0, item["excess_surface_count"])
            self.assertEqual(
                item["reference_surface_count"], item["union_surface_count"]
            )
            self.assertEqual(0, item["surface_error_count"])
            self.assertEqual(0.0, item["surface_error_rate"])

        measurement = summary["measurement"]
        self.assertEqual(
            {
                "candidate_mesh_sha256",
                "interior_tree_sha256",
                "exterior_snapshot_sha256",
                "observable_sha256",
            },
            set(measurement),
        )
        self.assertTrue(all(len(value) == 64 for value in measurement.values()))
        self.assertEqual(
            {
                "global_depth_8_zero": True,
                "out_of_frame_clear": True,
                "no_evidence_conflict": True,
            },
            summary["objective_facts"],
        )
        self.assertFalse(summary["no_observable_geometry_change"])

        step = self.state / "steps/000000"
        self.assertEqual(
            {
                "candidate.vbsvo",
                "excess-depth8.vbsvo",
                "exterior.json",
                "missing-depth8.vbsvo",
                "measurement.json",
                "summary.json",
            },
            {path.name for path in step.iterdir()},
        )
        self.assertEqual(
            summary,
            json.loads((step / "summary.json").read_text(encoding="utf-8")),
        )

    def test_scaled_candidate_remains_unaligned_and_visible_as_error(self) -> None:
        measure_step(self.reference, self.candidate, self.state, step=0)
        scaled = trimesh.load(self.candidate, force="mesh", process=False)
        scaled.apply_scale(0.8)
        scaled_path = self.root / "scaled.ply"
        scaled.export(scaled_path)

        summary = measure_step(
            self.reference,
            scaled_path,
            self.state,
            step=1,
            compare_to=0,
        ).summary

        self.assertGreater(summary["errors_by_depth"][-1]["surface_error_count"], 0)
        self.assertFalse(summary["objective_facts"]["global_depth_8_zero"])

    def test_fully_exterior_candidate_publishes_complete_veto_evidence(self) -> None:
        exterior = trimesh.Trimesh(
            vertices=np.array(
                [
                    [0.6, -0.05, 0.0],
                    [0.7, -0.05, 0.0],
                    [0.6, 0.05, 0.0],
                ],
                dtype=np.float64,
            ),
            faces=[[0, 1, 2]],
            process=False,
        )
        exterior_path = self.root / "fully-exterior.obj"
        exterior.export(exterior_path)

        summary = measure_step(
            self.reference,
            exterior_path,
            self.state,
            step=0,
        ).summary

        depth_eight = summary["errors_by_depth"][-1]
        self.assertEqual(0, depth_eight["candidate_surface_count"])
        self.assertEqual(
            depth_eight["reference_surface_count"],
            depth_eight["missing_surface_count"],
        )
        self.assertEqual(0, depth_eight["excess_surface_count"])
        evidence = summary["exterior_surface"]
        self.assertTrue(evidence["surface_present"])
        self.assertGreater(evidence["surface_cell_count"], 0)
        self.assertEqual(["+x"], evidence["outside_directions"])
        self.assertEqual(
            {"min": [0.6, -0.05, 0.0], "max": [0.7, 0.05, 0.0]},
            evidence["bounds_canonical"],
        )
        self.assertAlmostEqual(0.1, evidence["nearest_overrun"])
        self.assertAlmostEqual(0.2, evidence["farthest_overrun"])
        self.assertEqual(
            {
                "global_depth_8_zero": False,
                "out_of_frame_clear": False,
                "no_evidence_conflict": True,
            },
            summary["objective_facts"],
        )
        snapshot_path = self.state / "steps/000000/exterior.json"
        snapshot_bytes = snapshot_path.read_bytes()
        snapshot = json.loads(snapshot_bytes)
        self.assertEqual("voxblame.exterior-snapshot/1", snapshot["schema"])
        self.assertEqual(
            hashlib.sha256(
                b"voxblame.exterior-snapshot/1\0" + snapshot_bytes
            ).hexdigest(),
            evidence["logical_sha256"],
        )
        self.assertEqual(
            evidence["logical_sha256"],
            summary["measurement"]["exterior_snapshot_sha256"],
        )

    def test_boundary_crossing_triangle_matches_its_clipped_interior_identity(
        self,
    ) -> None:
        crossing = trimesh.Trimesh(
            vertices=np.array(
                [
                    [-0.25, -0.1, 0.0],
                    [0.75, -0.1, 0.0],
                    [-0.25, 0.1, 0.0],
                ],
                dtype=np.float64,
            ),
            faces=[[0, 1, 2]],
            process=False,
        )
        crossing_path = self.root / "crossing.obj"
        crossing.export(crossing_path)
        clipped = trimesh.Trimesh(
            vertices=np.array(
                [
                    [-0.25, -0.1, 0.0],
                    [0.5, -0.1, 0.0],
                    [0.5, -0.05, 0.0],
                    [-0.25, 0.1, 0.0],
                ],
                dtype=np.float64,
            ),
            faces=[[0, 1, 2], [0, 2, 3]],
            process=False,
        )
        clipped_path = self.root / "clipped.obj"
        clipped.export(clipped_path)

        crossing_summary = measure_step(
            self.reference,
            crossing_path,
            self.state,
            step=0,
        ).summary
        clipped_summary = measure_step(
            self.reference,
            clipped_path,
            self.state,
            step=1,
            compare_to=0,
        ).summary

        self.assertEqual(
            crossing_summary["measurement"]["interior_tree_sha256"],
            clipped_summary["measurement"]["interior_tree_sha256"],
        )
        self.assertNotEqual(
            crossing_summary["measurement"]["exterior_snapshot_sha256"],
            clipped_summary["measurement"]["exterior_snapshot_sha256"],
        )
        self.assertNotEqual(
            crossing_summary["measurement"]["observable_sha256"],
            clipped_summary["measurement"]["observable_sha256"],
        )
        crossing_exterior = crossing_summary["exterior_surface"]
        self.assertTrue(crossing_exterior["surface_present"])
        self.assertEqual(["+x"], crossing_exterior["outside_directions"])
        self.assertAlmostEqual(
            0.5,
            crossing_exterior["bounds_canonical"]["min"][0],
        )
        self.assertAlmostEqual(0.0, crossing_exterior["nearest_overrun"])
        self.assertFalse(crossing_summary["objective_facts"]["out_of_frame_clear"])
        self.assertTrue(clipped_summary["objective_facts"]["out_of_frame_clear"])

    def test_boundary_epsilon_distinguishes_clear_from_true_exterior(self) -> None:
        canonical = trimesh.load(self.candidate, force="mesh", process=False)
        boundary_vertex = int(np.argmax(np.asarray(canonical.vertices)[:, 0]))
        within_epsilon = canonical.copy()
        within_epsilon.vertices[boundary_vertex, 0] = 0.5 + 0.5e-9
        within_path = self.root / "within-epsilon.ply"
        _write_double_ply(within_path, within_epsilon)
        beyond_epsilon = canonical.copy()
        beyond_epsilon.vertices[boundary_vertex, 0] = 0.5 + 2.0e-9
        beyond_path = self.root / "beyond-epsilon.ply"
        _write_double_ply(beyond_path, beyond_epsilon)

        within = measure_step(
            self.reference,
            within_path,
            self.state,
            step=0,
        ).summary
        beyond = measure_step(
            self.reference,
            beyond_path,
            self.state,
            step=1,
            compare_to=0,
        ).summary

        self.assertEqual(
            {
                "global_depth_8_zero": True,
                "out_of_frame_clear": True,
                "no_evidence_conflict": True,
            },
            within["objective_facts"],
        )
        self.assertTrue(beyond["objective_facts"]["global_depth_8_zero"])
        self.assertFalse(beyond["objective_facts"]["out_of_frame_clear"])
        self.assertTrue(beyond["exterior_surface"]["surface_present"])
        self.assertEqual(["+x"], beyond["exterior_surface"]["outside_directions"])

    def test_surface_wholly_within_boundary_epsilon_belongs_to_interior(
        self,
    ) -> None:
        tolerance_band = trimesh.Trimesh(
            vertices=np.array(
                [
                    [0.5 + 0.5e-9, -0.1, -0.1],
                    [0.5 + 0.5e-9, 0.1, -0.1],
                    [0.5 + 0.5e-9, -0.1, 0.1],
                ],
                dtype=np.float64,
            ),
            faces=[[0, 1, 2]],
            process=False,
        )
        tolerance_path = self.root / "tolerance-band.ply"
        _write_double_ply(tolerance_path, tolerance_band)
        boundary = tolerance_band.copy()
        boundary.vertices[:, 0] = 0.5
        boundary_path = self.root / "boundary.ply"
        _write_double_ply(boundary_path, boundary)

        tolerance_summary = measure_step(
            self.reference,
            tolerance_path,
            self.state,
            step=0,
        ).summary
        boundary_summary = measure_step(
            self.reference,
            boundary_path,
            self.state,
            step=1,
            compare_to=0,
        ).summary

        self.assertTrue(
            tolerance_summary["objective_facts"]["out_of_frame_clear"]
        )
        self.assertGreater(
            tolerance_summary["errors_by_depth"][-1]["candidate_surface_count"],
            0,
        )
        self.assertEqual(
            boundary_summary["measurement"]["interior_tree_sha256"],
            tolerance_summary["measurement"]["interior_tree_sha256"],
        )
        self.assertEqual(
            boundary_summary["measurement"]["observable_sha256"],
            tolerance_summary["measurement"]["observable_sha256"],
        )

    def test_resource_coarsening_changes_only_diagnostic_exterior_evidence(
        self,
    ) -> None:
        vertices = np.array(
            [
                [0.6, -1.0e20, -1.0e20],
                [0.6, 1.0e20, -1.0e20],
                [0.6, -1.0e20, 1.0e20],
            ],
            dtype=np.float64,
        )
        exterior = trimesh.Trimesh(
            vertices=vertices,
            faces=[[0, 1, 2]],
            process=False,
        )
        exterior_path = self.root / "coarsened-exterior.ply"
        _write_double_ply(exterior_path, exterior)

        summary = measure_step(
            self.reference,
            exterior_path,
            self.state,
            step=0,
        ).summary
        evidence = summary["exterior_surface"]
        self.assertTrue(evidence["surface_present"])
        self.assertEqual(
            {
                "min": [0.6, -1.0e20, -1.0e20],
                "max": [0.6, 1.0e20, 1.0e20],
            },
            evidence["bounds_canonical"],
        )
        self.assertEqual(
            ["+x", "-y", "+y", "-z", "+z"],
            evidence["outside_directions"],
        )
        self.assertAlmostEqual(0.1, evidence["nearest_overrun"])
        self.assertAlmostEqual(1.0e20 - 0.5, evidence["farthest_overrun"])
        self.assertTrue(evidence["coarsened"])
        self.assertLess(evidence["diagnostic_grid_depth"], 1)
        self.assertLessEqual(evidence["surface_cell_count"], 65_536)
        snapshot = json.loads(
            (self.state / "steps/000000/exterior.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            evidence["diagnostic_grid_depth"],
            snapshot["resolution"]["diagnostic_grid_depth"],
        )
        self.assertEqual(
            "canonical-boundary-interior-closed-cells/1",
            snapshot["resolution"]["boundary_policy"],
        )
        expected_observable = {
            "schema": "voxblame.observable/1",
            "interior_tree_sha256": summary["measurement"]["interior_tree_sha256"],
            "exterior_snapshot_sha256": summary["measurement"][
                "exterior_snapshot_sha256"
            ],
            "exterior_profile": "signed_exterior_surface/1",
            "exterior_resolution": snapshot["resolution"],
        }
        expected_digest = hashlib.sha256(
            (
                json.dumps(
                    expected_observable,
                    indent=2,
                    sort_keys=True,
                    separators=(",", ": "),
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            expected_digest,
            summary["measurement"]["observable_sha256"],
        )

    def test_missing_and_excess_candidates_report_separate_directions(self) -> None:
        raw = trimesh.Trimesh(
            vertices=np.array(
                [
                    [-0.5, -0.051, 0.0],
                    [0.5, -0.051, 0.0],
                    [-0.5, -0.049, 0.0],
                    [-0.5, 0.049, 0.0],
                    [0.5, 0.049, 0.0],
                    [-0.5, 0.051, 0.0],
                ],
                dtype=np.float64,
            ),
            faces=np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int64),
            process=False,
        )
        raw_path = self.root / "two-strips.ply"
        raw.export(raw_path)
        reference = self.root / "two-strip-input"
        state = self.root / "two-strip-voxblame"
        prepare_reference(raw_path, reference)
        canonical = trimesh.load(
            reference / "reference.ply", force="mesh", process=False
        )
        canonical_triangles = np.asarray(canonical.triangles, dtype=np.float64)
        missing_mesh = trimesh.Trimesh(
            vertices=canonical_triangles[0], faces=[[0, 1, 2]], process=False
        )
        missing_path = self.root / "missing.ply"
        missing_mesh.export(missing_path)
        extra_triangle = np.array(
            [[-0.25, 0.2, 0.0], [0.25, 0.2, 0.0], [-0.25, 0.202, 0.0]],
            dtype=np.float64,
        )
        excess_mesh = trimesh.util.concatenate(
            [
                canonical,
                trimesh.Trimesh(
                    vertices=extra_triangle, faces=[[0, 1, 2]], process=False
                ),
            ]
        )
        excess_path = self.root / "excess.ply"
        excess_mesh.export(excess_path)

        measure_step(reference, reference / "reference.ply", state, step=0)
        missing = measure_step(
            reference, missing_path, state, step=1, compare_to=0
        ).summary["errors_by_depth"][-1]
        excess = measure_step(
            reference, excess_path, state, step=2, compare_to=0
        ).summary["errors_by_depth"][-1]

        self.assertGreater(missing["missing_surface_count"], 0)
        self.assertEqual(0, missing["excess_surface_count"])
        self.assertEqual(0, excess["missing_surface_count"])
        self.assertGreater(excess["excess_surface_count"], 0)

    def test_bytes_different_geometric_no_op_has_distinct_mesh_identity(self) -> None:
        first = measure_step(self.reference, self.candidate, self.state, step=0).summary
        canonical = trimesh.load(self.candidate, force="mesh", process=False)
        triangle = np.asarray(canonical.triangles[0], dtype=np.float64)
        reordered = trimesh.Trimesh(
            vertices=triangle[[2, 0, 1]], faces=[[0, 2, 1]], process=False
        )
        reordered_path = self.root / "same-geometry.obj"
        reordered.export(reordered_path)

        second = measure_step(
            self.reference,
            reordered_path,
            self.state,
            step=1,
            compare_to=0,
        ).summary

        self.assertNotEqual(
            first["measurement"]["candidate_mesh_sha256"],
            second["measurement"]["candidate_mesh_sha256"],
        )
        self.assertEqual(
            first["measurement"]["interior_tree_sha256"],
            second["measurement"]["interior_tree_sha256"],
        )
        self.assertEqual(
            first["measurement"]["observable_sha256"],
            second["measurement"]["observable_sha256"],
        )
        self.assertTrue(second["no_observable_geometry_change"])

    def test_nonzero_step_requires_explicit_existing_earlier_compare_to(self) -> None:
        measure_step(self.reference, self.candidate, self.state, step=0)

        with self.assertRaisesRegex(ValueError, "explicit earlier compare_to"):
            measure_step(self.reference, self.candidate, self.state, step=1)
        self.assertFalse((self.state / "steps/000001").exists())
        with self.assertRaisesRegex(ValueError, "not published"):
            measure_step(
                self.reference,
                self.candidate,
                self.state,
                step=2,
                compare_to=1,
            )
        self.assertFalse((self.state / "steps/000002").exists())

    def test_reference_identity_drift_is_rejected_before_publication(self) -> None:
        manifest_path = self.reference / "input.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["triangle_set_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            measure_step(self.reference, self.candidate, self.state, step=0)

        self.assertFalse(self.state.exists())

    def test_translated_candidate_persists_exact_depth_eight_error_sets(self) -> None:
        measure_step(self.reference, self.candidate, self.state, step=0)
        translated = trimesh.load(
            self.candidate, force="mesh", process=False
        )
        translated.apply_translation([0.0, 0.05, 0.0])
        translated_path = self.root / "translated.ply"
        translated.export(translated_path)

        result = measure_step(
            self.reference,
            translated_path,
            self.state,
            step=1,
            compare_to=0,
        )

        summary = result.summary
        self.assertEqual(0, summary["compare_to"])
        self.assertFalse(summary["objective_facts"]["global_depth_8_zero"])
        self.assertFalse(summary["no_observable_geometry_change"])
        for item in summary["errors_by_depth"]:
            self.assertEqual(
                item["union_surface_count"],
                item["reference_surface_count"] + item["excess_surface_count"],
            )
            self.assertEqual(
                item["union_surface_count"],
                item["candidate_surface_count"] + item["missing_surface_count"],
            )
            self.assertEqual(
                item["surface_error_count"],
                item["missing_surface_count"] + item["excess_surface_count"],
            )
        depth_eight = summary["errors_by_depth"][-1]
        self.assertGreater(depth_eight["missing_surface_count"], 0)
        self.assertGreater(depth_eight["excess_surface_count"], 0)

        step = self.state / "steps/000001"
        measurement = json.loads(
            (step / "measurement.json").read_text(encoding="utf-8")
        )
        missing = read_surface_tree(step / "missing-depth8.vbsvo")
        excess = read_surface_tree(step / "excess-depth8.vbsvo")
        self.assertEqual(depth_eight["missing_surface_count"], missing.leaf_count)
        self.assertEqual(depth_eight["excess_surface_count"], excess.leaf_count)
        self.assertEqual(
            missing.logical_sha256,
            measurement["depth_8_evidence"]["missing_surface"][
                "logical_sha256"
            ],
        )
        self.assertEqual(
            excess.logical_sha256,
            measurement["depth_8_evidence"]["excess_surface"][
                "logical_sha256"
            ],
        )
        self.assertNotIn("repair_targets", measurement)

    def test_identical_rerun_is_idempotent_and_conflict_cannot_overwrite_step(self) -> None:
        first = measure_step(self.reference, self.candidate, self.state, step=0)
        step = self.state / "steps/000000"
        before = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in step.iterdir()
        }

        repeated = measure_step(self.reference, self.candidate, self.state, step=0)

        self.assertFalse(first.idempotent)
        self.assertTrue(repeated.idempotent)
        self.assertEqual(first.summary, repeated.summary)
        translated = trimesh.load(self.candidate, force="mesh", process=False)
        translated.apply_translation([0.0, 0.05, 0.0])
        conflicting = self.root / "conflicting.ply"
        translated.export(conflicting)
        with self.assertRaisesRegex(ValueError, "different identity"):
            measure_step(self.reference, conflicting, self.state, step=0)
        after = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in step.iterdir()
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
