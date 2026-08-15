"""An assembly's refs must resolve in the namespace the tools actually hand out.

Issue 0b (tom-cad FEEDBACK.md): `snapshot --mode list` and the CAD Viewer enumerate instance-tree
occurrences -- `#o1.12 shoulder_yaw_yoke` -- while `inspect refs` and `snapshot --focus/--hide`
resolved against the whole-assembly topology sidecar, which is extracted from the COMPOSED
compound and therefore describes any assembly as ONE occurrence. Every ref a user could pick was
rejected by every tool that could measure it, and `--facts` reported `occurrenceCount: 1` for a
160-part assembly.

These cover the merge itself (real package, real descriptor) and the transform arithmetic, which
is the part with a wrong answer that looks plausible.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from build123d import Box, Compound, Pos  # noqa: E402

from cadgen import assembly_lookup, lookup  # noqa: E402
from cadgen._internal import component_package  # noqa: E402
from cadgen.coordination.lock import exclusive  # noqa: E402
from cadgen.coordination.paths import write_lock_path  # noqa: E402


def _demo_compound() -> Compound:
    """Two identical boxes placed apart plus a distinct one: 3 occurrences over 2 components."""
    first = Pos(0, 0, 0) * Box(10, 10, 10)
    first.label = "box_a_1"
    second = Pos(40, 0, 0) * Box(10, 10, 10)
    second.label = "box_a_2"
    third = Pos(0, 40, 0) * Box(4, 6, 8)
    third.label = "box_b"
    assembly = Compound(children=[first, second, third])
    assembly.label = "demo"
    return assembly


class _FakeArtifact:
    """Stands in for StepTopologyArtifact: the merge only reads `kind` and `artifact_path`."""

    def __init__(self, kind: str, artifact_path: Path) -> None:
        self.kind = kind
        self.artifact_path = artifact_path


class AssemblyOccurrenceRefsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="assembly-refs-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.package_dir = self.root / "__cadgen__" / "models" / "demo.step.py"
        with exclusive(write_lock_path(self.package_dir)):
            component_package.build_package_from_compound(
                _demo_compound(), package_dir=self.package_dir, root_name="demo"
            )
        descriptor = component_package.read_package_descriptor(self.package_dir)
        self.assertIsInstance(descriptor, dict, "fixture package has no descriptor")
        self.descriptor = descriptor

    def _flat_index(self) -> lookup.SelectorIndex:
        """A stand-in for the composed-compound sidecar: one occurrence, as the real one has."""
        manifest = {
            "stats": {"occurrenceCount": 1, "shapeCount": 3},
            "tables": {"occurrenceColumns": ["id", "name", "bbox"]},
            "occurrences": [["o1", "demo.step", None]],
        }
        return lookup.build_selector_index(manifest)

    def test_every_ref_the_lister_hands_out_resolves(self) -> None:
        """The bug as reported: ids present in assembly.json, absent from the resolver."""
        index = assembly_lookup.merge_assembly_occurrences(
            self._flat_index(), self.descriptor, self.package_dir
        )
        ids = [str(row["id"]) for row in self.descriptor["occurrences"]]
        self.assertTrue(ids, "fixture assembly declares no occurrences")
        for occurrence_id in ids:
            with self.subTest(occurrence=occurrence_id):
                found = lookup.lookup_selector(f"#{occurrence_id}", index)
                self.assertIsNotNone(found, f"{occurrence_id} did not resolve")
                self.assertEqual("occurrence", found[0])

    def test_the_flat_namespace_still_resolves(self) -> None:
        """The merge is ADDITIVE. `#o1` and bare-ordinal refs are what every existing caller
        and test uses; fixing instance refs must not cost them."""
        flat = self._flat_index()
        merged = assembly_lookup.merge_assembly_occurrences(
            flat, self.descriptor, self.package_dir
        )
        self.assertIsNotNone(lookup.lookup_selector("#o1", merged))
        self.assertEqual(flat.single_occurrence_id, merged.single_occurrence_id)

    def test_the_reported_occurrence_count_stops_saying_one(self) -> None:
        merged = assembly_lookup.merge_assembly_occurrences(
            self._flat_index(), self.descriptor, self.package_dir
        )
        summary = lookup.entry_summary(merged)
        self.assertGreater(
            int(summary["occurrenceCount"]),
            1,
            "`inspect refs --facts` reported occurrenceCount 1 for a whole assembly",
        )

    def test_a_non_assembly_artifact_is_returned_untouched(self) -> None:
        """The guardrail. Part entries are the common path and must not notice this change."""
        flat = self._flat_index()
        part = _FakeArtifact(kind="part", artifact_path=self.package_dir)
        self.assertIs(flat, assembly_lookup.index_with_assembly_occurrences(flat, part))
        self.assertIs(flat, assembly_lookup.index_with_assembly_occurrences(flat, None))

    def test_a_missing_package_directory_is_not_an_error(self) -> None:
        flat = self._flat_index()
        absent = _FakeArtifact(kind="assembly", artifact_path=self.root / "nope")
        self.assertIs(flat, assembly_lookup.index_with_assembly_occurrences(flat, absent))

    def test_occurrences_are_reported_in_world_coordinates(self) -> None:
        rows = assembly_lookup.assembly_occurrence_rows(self.descriptor, self.package_dir)
        placed = [row for row in rows if row.get("bbox")]
        self.assertTrue(placed, "no occurrence carried a bbox")
        # The two identical boxes share a component and differ ONLY by placement, so
        # component-local bboxes would make them indistinguishable. World coordinates are the
        # frame the viewer shows and `snapshot --mode list` reports.
        centres = {
            tuple(round((row["bbox"]["min"][axis] + row["bbox"]["max"][axis]) / 2, 3) for axis in range(3))
            for row in placed
        }
        self.assertEqual(len(centres), len(placed), "occurrences collapsed to one position")


class TransformArithmeticTest(unittest.TestCase):
    """A box transformed by its min/max alone keeps its diagonal and loses its extent. Every
    occurrence in a posed assembly is rotated, so this is the failure mode that matters."""

    def test_a_rotated_box_is_transformed_by_its_corners(self) -> None:
        # 90 degrees about Z: x -> y, y -> -x.
        matrix = [
            0.0, -1.0, 0.0, 0.0,
            1.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]
        box = {"min": [0.0, 0.0, 0.0], "max": [2.0, 6.0, 1.0]}
        placed = assembly_lookup.transform_bbox(matrix, box)
        self.assertIsNotNone(placed)
        for axis, (low, high) in enumerate(zip([-6.0, 0.0, 0.0], [0.0, 2.0, 1.0])):
            self.assertAlmostEqual(low, placed["min"][axis], places=6)
            self.assertAlmostEqual(high, placed["max"][axis], places=6)

    def test_translation_moves_the_box(self) -> None:
        matrix = [
            1.0, 0.0, 0.0, 5.0,
            0.0, 1.0, 0.0, -3.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]
        placed = assembly_lookup.transform_bbox(
            matrix, {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]}
        )
        self.assertEqual([5.0, -3.0, 0.0], placed["min"])
        self.assertEqual([6.0, -2.0, 1.0], placed["max"])

    def test_a_malformed_transform_falls_back_to_identity(self) -> None:
        box = {"min": [0.0, 0.0, 0.0], "max": [1.0, 2.0, 3.0]}
        rows = assembly_lookup.transform_bbox(assembly_lookup._matrix("nonsense"), box)
        self.assertEqual(box["min"], rows["min"])
        self.assertEqual(box["max"], rows["max"])

    def test_a_missing_bbox_is_none_rather_than_a_crash(self) -> None:
        self.assertIsNone(assembly_lookup.transform_bbox(assembly_lookup._matrix(None), None))
        self.assertIsNone(assembly_lookup.transform_bbox(assembly_lookup._matrix(None), {"min": [0, 0, 0]}))


if __name__ == "__main__":
    unittest.main()
