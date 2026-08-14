"""Tests for the generation-time DXF drawing checks."""

import unittest
from pathlib import Path

import ezdxf

from cadgen import drawing_checks
from cadgen.drawing_checks import (
    DrawingValidationError,
    layer_allows_open_geometry,
    layer_intent,
    raise_on_error_findings,
    validate_drawing_document,
    validate_dxf_file,
)
from tests.python.support.tmp_root import temporary_directory


def _new_document():
    document = ezdxf.new("R2010")
    document.units = ezdxf.units.MM
    return document


def _codes(findings):
    return sorted(finding.code for finding in findings)


class LayerIntentTests(unittest.TestCase):
    def test_whole_token_matching(self) -> None:
        self.assertEqual("bend", layer_intent("BEND"))
        self.assertEqual("bend", layer_intent("bend-lines"))
        self.assertEqual("engrave", layer_intent("ENGRAVE_TEXT"))
        self.assertEqual("reference", layer_intent("REF_GEOMETRY"))
        # Substrings must NOT match: PREFORM contains "ref", KEYNOTES contains "note".
        self.assertEqual("cut", layer_intent("PREFORM"))
        self.assertEqual("cut", layer_intent("KEYNOTES"))
        self.assertEqual("cut", layer_intent("CUT"))
        self.assertFalse(layer_allows_open_geometry("PREFORM"))
        self.assertTrue(layer_allows_open_geometry("BEND"))

    def test_render_kind_matches_validation_intent(self) -> None:
        from cadgen.drawing_render import _semantic_kind_for_layer

        for name in ("BEND", "PREFORM", "ENGRAVE", "NOTES", "CUT"):
            self.assertEqual(layer_intent(name), _semantic_kind_for_layer(name))


class DrawingChecksTests(unittest.TestCase):
    def test_full_circle_arc_is_valid(self) -> None:
        document = _new_document()
        document.modelspace().add_arc((0, 0), 5, 0, 360, dxfattribs={"layer": "CUT"})

        self.assertEqual([], validate_drawing_document(document))

    def test_closed_profiles_and_bend_lines_pass(self) -> None:
        document = _new_document()
        modelspace = document.modelspace()
        modelspace.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True, dxfattribs={"layer": "CUT"})
        modelspace.add_circle((20, 10), 3, dxfattribs={"layer": "CUT"})
        modelspace.add_line((10, 0), (10, 20), dxfattribs={"layer": "BEND"})

        self.assertEqual([], validate_drawing_document(document))

    def test_open_polyline_on_cut_layer_is_error(self) -> None:
        document = _new_document()
        document.modelspace().add_lwpolyline([(0, 0), (40, 0), (40, 20)], dxfattribs={"layer": "CUT"})

        self.assertIn("open_cut_profile", _codes(validate_drawing_document(document)))

    def test_chained_lines_closing_a_loop_pass(self) -> None:
        document = _new_document()
        modelspace = document.modelspace()
        for start, end in [((0, 0), (10, 0)), ((10, 0), (10, 10)), ((10, 10), (0, 0))]:
            modelspace.add_line(start, end, dxfattribs={"layer": "CUT"})

        self.assertEqual([], validate_drawing_document(document))

    def test_dangling_line_on_cut_layer_is_error(self) -> None:
        document = _new_document()
        modelspace = document.modelspace()
        modelspace.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True, dxfattribs={"layer": "CUT"})
        modelspace.add_line((100, 100), (120, 100), dxfattribs={"layer": "CUT"})

        self.assertIn("open_cut_profile", _codes(validate_drawing_document(document)))

    def test_zero_length_and_duplicate_entities_are_errors(self) -> None:
        document = _new_document()
        modelspace = document.modelspace()
        modelspace.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True, dxfattribs={"layer": "CUT"})
        modelspace.add_line((5, 5), (5, 5), dxfattribs={"layer": "BEND"})
        modelspace.add_circle((20, 10), 3, dxfattribs={"layer": "CUT"})
        modelspace.add_circle((20, 10), 3, dxfattribs={"layer": "CUT"})

        codes = _codes(validate_drawing_document(document))
        self.assertIn("zero_length_entity", codes)
        self.assertIn("duplicate_entity", codes)

    def test_empty_drawing_is_error(self) -> None:
        document = _new_document()

        self.assertIn("empty_drawing", _codes(validate_drawing_document(document)))

    def test_raise_on_error_findings(self) -> None:
        document = _new_document()
        findings = validate_drawing_document(document)

        with self.assertRaises(DrawingValidationError):
            raise_on_error_findings(findings, label="empty")

    def test_validate_dxf_file_roundtrip(self) -> None:
        document = _new_document()
        document.modelspace().add_lwpolyline(
            [(0, 0), (40, 0), (40, 20), (0, 20)], close=True, dxfattribs={"layer": "CUT"}
        )
        with temporary_directory(prefix="dxf-checks") as root:
            path = Path(root) / "outline.dxf"
            document.saveas(str(path))

            self.assertEqual([], validate_dxf_file(path))


if __name__ == "__main__":
    unittest.main()


def _drawing_with_dimensions():
    """The reporter's shape: open views, annotation layers with local names, dimensions."""
    document = ezdxf.new("R2010", setup=True)
    document.units = ezdxf.units.MM
    modelspace = document.modelspace()
    for name in ("FRONTEN", "KORPUS", "REFERENZ"):
        document.layers.add(name)
    modelspace.add_lwpolyline([(0, 0), (600, 0), (600, 720)], dxfattribs={"layer": "KORPUS"})
    modelspace.add_line((0, 360), (600, 360), dxfattribs={"layer": "REFERENZ"})
    modelspace.add_linear_dim(base=(0, -40), p1=(0, 0), p2=(600, 0)).render()
    return document


def _cut_layout_with_an_open_profile():
    document = ezdxf.new("R2010", setup=True)
    document.units = ezdxf.units.MM
    document.layers.add("CUT")
    document.modelspace().add_lwpolyline(
        [(0, 0), (100, 0), (100, 60)], dxfattribs={"layer": "CUT"}
    )
    return document


class DrawingDetectionTest(unittest.TestCase):
    """A drawing is recognised from the FILE, not from a field a generator sets.

    Issue #246: a workshop drawing -- plan, elevations, sections, title block, 37 dimensions --
    could not generate, because a layer matching no known intent must hold closed contours. The
    signal is the DXF's own apparatus: dimensions, leaders, paper-space viewports. A laser-cut
    layout is model-space geometry at 1:1 with none of it. Deciding from the file means an
    imported .dxf from AutoCAD validates by the same rule as a generated one.
    """

    def test_dimensions_make_it_a_drawing(self) -> None:
        is_drawing, evidence = drawing_checks.document_is_drawing(_drawing_with_dimensions())
        self.assertTrue(is_drawing)
        self.assertIn("dimension", evidence)

    def test_a_drawing_does_not_have_to_close_its_contours(self) -> None:
        findings = validate_drawing_document(_drawing_with_dimensions())
        self.assertEqual([], [f for f in findings if f.severity == "error"])
        # And it says why, rather than passing silently.
        self.assertEqual(
            ["drawing_document"],
            [f.code for f in findings if f.severity == "info"],
        )

    def test_a_cut_layout_with_an_open_profile_still_fails(self) -> None:
        # The check this feature must not become a bypass for. No dimensions, no viewports:
        # nothing says "drawing", so an unclosed cut path is what it looks like -- an error.
        findings = validate_drawing_document(_cut_layout_with_an_open_profile())
        self.assertIn("open_cut_profile", [f.code for f in findings if f.severity == "error"])

    def test_absence_of_closure_is_never_the_evidence(self) -> None:
        # "Nothing closes, so it must be a drawing" would excuse every broken cut layout.
        is_drawing, evidence = drawing_checks.document_is_drawing(
            _cut_layout_with_an_open_profile()
        )
        self.assertFalse(is_drawing)
        self.assertEqual("", evidence)

    def test_paper_space_geometry_counts_as_apparatus(self) -> None:
        document = ezdxf.new("R2010", setup=True)
        document.units = ezdxf.units.MM
        document.layers.add("CUT")
        document.modelspace().add_lwpolyline(
            [(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "CUT"}
        )
        layout = document.layout("Layout1")
        layout.add_text("TITLE BLOCK", height=5).set_placement((0, 0))
        is_drawing, evidence = drawing_checks.document_is_drawing(document)
        self.assertTrue(is_drawing)
        self.assertIn("paper-space", evidence)


class LayerTableIntentTest(unittest.TestCase):
    """Two standard layer properties say "not a cut path" without naming the layer so."""

    def test_a_non_plotting_layer_is_annotation(self) -> None:
        document = ezdxf.new("R2010", setup=True)
        document.layers.add("KORPUS").dxf.plot = 0
        self.assertEqual("reference", drawing_checks.layer_table_intents(document)["KORPUS"])

    def test_a_hidden_or_centre_linetype_is_annotation(self) -> None:
        document = ezdxf.new("R2010", setup=True)
        document.layers.add("VERDECKT", linetype="HIDDEN")
        document.layers.add("MITTE", linetype="CENTER")
        intents = drawing_checks.layer_table_intents(document)
        self.assertEqual("reference", intents["VERDECKT"])
        self.assertEqual("reference", intents["MITTE"])

    def test_a_dashed_BEND_layer_stays_a_bend(self) -> None:
        # Bend lines are conventionally dashed, and bend is not the same intent as annotation:
        # the bend checks want to know it is a bend.
        document = ezdxf.new("R2010", setup=True)
        document.layers.add("BEND", linetype="DASHED")
        self.assertEqual("bend", drawing_checks.layer_table_intents(document).get("BEND", "bend"))

    def test_a_plain_cut_layer_declares_nothing(self) -> None:
        document = ezdxf.new("R2010", setup=True)
        document.layers.add("CUT")
        self.assertNotIn("CUT", drawing_checks.layer_table_intents(document))
